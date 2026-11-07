"""Twitch: the stream and the chat, from the game, no OBS.

    T in the game starts and stops the stream (so does the x next to the LIVE badge, and
    `python -m bathero.twitch --stop`). It never starts on its own.

The stream is its own process (`daemon()`, `python -m bathero.twitch --daemon`, launched
by the game in a new session): the game can quit, crash or be relaunched by deploy.sh and
the stream goes on; a game started under a live stream attaches to it (StreamLink reads
STATE_PATH, the daemon's state file, written twice a second). The LIVE badge is the daemon's
own floating window (badge.py), top right of every screen and Space, over everything, so it
is seen with the game closed too; its x stops the stream. What goes out is the display, so
the game's window, its overlays (the ! layer: the camera as the computer edition's
picture-in-picture, the chat), the badge, the terminal, everything.

The stream itself (Streamer). Two sources, `stream_source` in settings:

"screen" (the default since 2026-09-12): ffmpeg captures display `stream_display` through
avfoundation, and the interface's mix comes from a separate process, `python -m bathero.twitch
--audio-feed FIFO` (audio_feed): a sounddevice input on the interface (the take's channels and
buffer settings), blocks queued and written to a named pipe as float32 stereo, which ffmpeg
reads as its second input. The video gets wall-clock timestamps (-use_wallclock_as_timestamps,
-copyts, setpts minus the launch time t0); the audio's clock is its own sample count, which the
feeder anchors to the same t0 (it pads the head, and any gap, with silence from the callback),
so the two stay in sync whatever each took to open. Never stamp the audio pipe with the wall
clock: that dates each block by the moment ffmpeg read it, and aresample=async then discards
everything that arrived while the loop was busy -- measured 2026-09-14, half the audio gone in
200 ms chunks, metronomic, for as long as anything (the screen capture, the rtmp send) made
ffmpeg hiccup; three networks were tried and the interface measured clean, and the cut was here.
What goes out is the whole display, the terminal included when it is on that screen.
Needs the Screen Recording permission for bathero.app (macOS asks once; then relaunch the app).
Why not avfoundation for the audio too ("Capture screen N:X18/XR18", the first version):
measured 2026-09-12 with the interface's native buffer timestamps, ffmpeg's avfoundation input
drops audio buffers even capturing audio alone (6.1: 113 drops, 1.3 s lost in 15 s; 8.0.1: 58
drops, 0.67 s), because it keeps a single pending audio buffer and sleeps 10 ms when it finds
none; viewers heard those as small pops. And why a separate process rather than the game's:
the sounddevice callback needs the GIL and the game's main loop holds it for milliseconds at a
time; the window source's in-process input logged 0.25 s stalls. The feeder reports its own
count of blocks against the wall clock (lost ms) when the stream stops.
Every stream also keeps an AAC copy of what was sent (ffmpeg's tee muxer) in STREAMS_DIR, to
check the audio afterwards.

"window": the game's own frames, like a take:
- Video: the main loop hands the surface to `push` after everything is drawn; a copy is taken
  once per 1/STREAM_FPS slot of the wall clock (the Recorder's scheme: a missed slot repeats
  the previous frame, so the picture stays on the wall clock and never runs ahead of the
  audio) and piped as raw RGB into ffmpeg, which encodes H.264 on VideoToolbox (Twitch does
  not take the takes' HEVC) at a constant bitrate with a keyframe every 2 s and muxes FLV over
  RTMPS to Twitch's ingest.
- Audio: a sounddevice input on the interface, the same two channels the takes use
  (capture_audio_device / capture_audio_channels: the XR18's USB sends 17/18 = Main L/R,
  everything you hear), opened with the mixer-sized buffer (capture.input_stream_kwargs) so
  the game keeps playing. The blocks go to ffmpeg through a named pipe as float32 stereo.
  The audio timeline is put on the stream's wall clock: silence before the first block for
  the time it took the stream to open, and silence for every gap when the device stalls
  (the XR18 has done that, see the ROADMAP), so both inputs start at 0 = the moment T was
  pressed and stay together.
- Health: ffmpeg's `-progress` output is read every second: the frame count, the bitrate
  and the speed (1.0x = keeping up; under it, the encoder or the network is behind and
  Twitch will see dropped frames). When ffmpeg exits (Twitch closed the connection, no
  network) the stream stops and the HUD shows the last line of its stderr.
- The key: ~/.config/bathero/twitch_key (TWITCH_KEY_PATH), one line, the "Primary Stream
  key" of the Twitch dashboard. Never logged, never in settings.json. The URL is
  STREAM_URL_TWITCH with the key appended; `stream_url` in settings.json replaces the whole
  URL (a local rtmp server for tests: `python -m bathero.twitch --selftest`). With
  `stream_bandwidth_test` on, `?bandwidthtest=true` is appended: Twitch takes the stream
  without going live and shows it at inspector.twitch.tv.

The chat (Chat): Twitch chat is IRC over TLS; reading it needs no account or token
(nick justinfan + digits). The pane in the game shows the last messages of the channel,
name in the user's Twitch colour, wrapped, and reconnects by itself. Connected while the
game's ! layer is on, live or not (or `python -m bathero.twitch --chat` for the terminal).
"""
import json
import os
import queue
import random
import re
import signal
import socket
import ssl
import subprocess
import sys
import tempfile
import threading
import time

import numpy as np

from .capture import AUDIO_SR, audio_device_index, ffmpeg_path, input_stream_kwargs

TWITCH_KEY_PATH = os.path.expanduser("~/.config/bathero/twitch_key")
STREAM_URL_TWITCH = "rtmps://ingest.global-contribute.live-video.net:443/app/"
STREAM_FPS = 30
# Twitch's limits for a non-partner: 6000 kbps, 1080p, keyframes every 2 s, AAC 160 kbps 44.1/48 kHz.
DEFAULTS = {"twitch_channel": "xantwav", "stream_height": 1080, "stream_kbps": 6000, "stream_url": None,
            "stream_bandwidth_test": False, "stream_source": "screen", "stream_display": 0, "stream_video_device": None,
            "capture_audio_device": "X18/XR18", "capture_audio_channels": [17, 18]}
STREAMS_DIR = os.path.expanduser("~/Movies/bathero/streams")   # the AAC copy of every stream's audio
STATE_PATH = os.environ.get("BATHERO_STREAM_STATE") or os.path.expanduser("~/.config/bathero/stream.json")   # the daemon's state, read by the game (tests: their own file)
DAEMON_LOG = os.path.expanduser("~/Library/Logs/bathero/stream.log")   # the daemon's output
GAIN_PATH = os.path.expanduser("~/.config/bathero/stream_gain")         # the stream's audio gain in dB, one number, kept between streams
GAIN_RANGE = (-12.0, 30.0)   # the badge's fader; the first streams measured -42 LUFS with the interface's mix as it came (2026-09-13)
AUDIO_STALL_S = 1.0          # no audio callback this long: the input is reopened (at most 3 times)
AUDIO_LAG_PAD_S = 0.25       # the audio timeline fell this far behind the wall clock: pad it with silence
START_TIMEOUT_S = 15         # ffmpeg reported no progress this long after the start: the stream is declared dead
CHAT_HOST, CHAT_PORT = "irc.chat.twitch.tv", 6697
CHAT_KEEP = 60               # messages kept for the pane
CHAT_RECONNECT_S = (2, 5, 10, 20, 30)


def read_gain(path=GAIN_PATH):
    """The stream's audio gain in dB (0 when unset)."""
    try:
        with open(path) as f:
            return min(GAIN_RANGE[1], max(GAIN_RANGE[0], float(f.read().strip() or 0)))
    except (OSError, ValueError):
        return 0.0


def write_gain(db, path=GAIN_PATH):
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w") as f:
        f.write(f"{db:.1f}\n")


def read_key(path=TWITCH_KEY_PATH):
    """The stream key, or None (missing file, empty)."""
    try:
        with open(path) as f:
            key = f.read().strip()
    except OSError:
        return None
    return key or None


def stream_url(settings, key):
    if settings.get("stream_url"):
        return settings["stream_url"]
    if not key:
        return None
    url = STREAM_URL_TWITCH + key
    if settings.get("stream_bandwidth_test"):
        url += "?bandwidthtest=true"
    return url


def redact(text, key):
    return text.replace(key, "<key>") if key else text


def encode_args(size, fps, kbps, container=True):
    w, h = size
    return ["-c:v", "h264_videotoolbox", "-realtime", "true", "-profile:v", "high", "-pix_fmt", "yuv420p",
            "-b:v", f"{kbps}k", "-maxrate", f"{kbps}k", "-bufsize", f"{2 * kbps}k", "-g", str(2 * fps), "-r", str(fps),
            "-c:a", "aac", "-b:a", "160k", "-ar", str(AUDIO_SR), "-ac", "2"] + (
            ["-f", "flv", "-flvflags", "no_duration_filesize"] if container else [])


def tee_output(url, audio_copy):
    """ffmpeg's tee muxer: the FLV to `url` (a failure there ends the stream) and an AAC copy of
    the audio to `audio_copy` (a failure there is ignored)."""
    esc = lambda p: p.replace("\\", "\\\\").replace("|", "\\|").replace(":", "\\:").replace("[", "\\[").replace("]", "\\]")
    return ["-f", "tee", f"[f=flv:flvflags=no_duration_filesize]{esc(url)}|[select=a:f=adts:onfail=ignore]{esc(audio_copy)}"]


class Streamer:
    """The live stream: `start(size)`, `push(surface)` every frame, `stop()`. `status` for the HUD."""

    def __init__(self, settings, log=print):
        self.settings = {**DEFAULTS, **{k: v for k, v in settings.items() if k in DEFAULTS}}
        self.log = log
        self.active = False
        self.error = None
        self.started_at = None
        self.frames = self.dropped = self.repeated = 0
        self.progress = {}             # ffmpeg's last progress block: frame, fps, bitrate, speed, drop_frames
        self.audio_blocks = self.audio_restarts = self.audio_overflows = self.audio_pads = 0
        self.key = None
        self._q = None

    # --- start ---------------------------------------------------------------------
    def start(self, size):
        """size: (w, h) of the surfaces the game will push. The stream is that size scaled to
        stream_height tall (even sizes, never upscaled)."""
        if self.active:
            return False
        self.error = None
        if not ffmpeg_path():
            self.error = "ffmpeg not found"
            return False
        self.key = read_key()
        url = stream_url(self.settings, self.key)
        if not url:
            self.error = f"no stream key in {TWITCH_KEY_PATH}"
            return False
        self.source = "screen" if self.settings.get("stream_source", "screen") != "window" else "window"
        self.fps = STREAM_FPS
        self.started_at = time.perf_counter()
        self._last_slot = -1
        self.frames = self.dropped = self.repeated = 0
        self.progress = {}
        self.audio_blocks = self.audio_restarts = self.audio_overflows = self.audio_pads = 0
        self.stopped_reason = None
        self.tmp = self.fifo = None
        self.feeder = None
        self._audio = None
        self._q = self._writer = self._awriter = None
        if self.source == "screen":
            return self._start_screen(size, url)
        w, h = int(size[0]), int(size[1])
        th = min(h, int(self.settings["stream_height"]))
        tw = int(w * th / h)
        self.in_size = (w // 2 * 2, h // 2 * 2)
        self.out_size = (tw // 2 * 2, th // 2 * 2)
        self.tmp = tempfile.mkdtemp(prefix="bathero-stream-")
        self.fifo = os.path.join(self.tmp, "audio.pipe")
        os.mkfifo(self.fifo)
        vf = [] if self.out_size == self.in_size else ["-vf", "scale=%d:%d:flags=bilinear" % self.out_size]
        sr = self._audio_rate()
        cmd = [ffmpeg_path(), "-hide_banner", "-loglevel", "error", "-nostdin", "-y",
               "-thread_queue_size", "1024", "-f", "rawvideo", "-pix_fmt", "rgb24", "-s", "%dx%d" % self.in_size,
               "-r", str(self.fps), "-i", "pipe:0",
               "-thread_queue_size", "1024", "-f", "f32le", "-ar", str(sr), "-ac", "2", "-i", self.fifo,
               "-map", "0:v", "-map", "1:a", *vf, *encode_args(self.out_size, self.fps, int(self.settings["stream_kbps"])),
               "-progress", "pipe:1", url]
        self.ff = subprocess.Popen(cmd, stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.PIPE)
        self._q = queue.Queue(maxsize=8)
        self._writer = threading.Thread(target=self._write_frames, daemon=True)
        self._writer.start()
        self._progress_thread = threading.Thread(target=self._read_progress, daemon=True)
        self._progress_thread.start()
        # audio: the fifo is opened by a thread (open blocks until ffmpeg opens its end)
        self._aq = queue.Queue()
        self._audio = None
        self._audio_sr = sr
        self._awriter = threading.Thread(target=self._write_audio, daemon=True)
        self._awriter.start()
        dev = audio_device_index(self.settings["capture_audio_device"])
        if dev is not None:
            try:
                self._start_audio(dev, sr)
            except Exception as e:                        # noqa: BLE001
                self.log(f"stream: no audio ({e}), silence")
                self._audio = None
        else:
            self.log(f"stream: audio device '{self.settings['capture_audio_device']}' not found, silence")
        if self._audio is None:                          # keep the audio timeline alive with silence
            self._silence_thread = threading.Thread(target=self._feed_silence, daemon=True)
            self._silence_thread.start()
        self.active = True
        self.log(f"stream: window {self.out_size[0]}x{self.out_size[1]} at {self.fps} fps, {self.settings['stream_kbps']} kbps, to {redact(url, self.key)}")
        return True

    def _start_screen(self, size, url):
        """ffmpeg: the display through avfoundation + the interface's mix from the audio feeder
        process through a named pipe, both on the wall clock; encode; send (and keep the AAC)."""
        # Leftovers of an earlier stream (the game killed under them, 2026-09-12) keep the display
        # captured, and a second screen capture then waits for ever inside avformat_open_input;
        # such an ffmpeg ignores SIGTERM, so SIGKILL. Ours carry the pipe's prefix on their command line.
        # Never while a stream daemon is live, and never from a test (stream_url set): a selftest run
        # during a stream killed it (2026-09-13).
        st = read_state()
        if not self.settings.get("stream_url") and not (st.get("active") and pid_alive(st.get("pid")) and st["pid"] != os.getpid()):
            subprocess.run(["pkill", "-9", "-f", "bathero-stream-"], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        display = int(self.settings.get("stream_display", 0))
        video_dev = self.settings.get("stream_video_device") or f"Capture screen {display}"
        dev = self.settings["capture_audio_device"]
        chans = [int(c) for c in self.settings["capture_audio_channels"]][:2]
        th = int(self.settings["stream_height"])
        self.in_size = self.out_size = (0, th)          # the display's size is ffmpeg's business; only the height is known
        sr = self._audio_rate()
        self.tmp = tempfile.mkdtemp(prefix="bathero-stream-")
        self.fifo = os.path.join(self.tmp, "audio.pipe")
        os.mkfifo(self.fifo)
        if self.settings.get("stream_url"):               # a test: the copy stays with the pipe
            self.audio_copy = os.path.join(self.tmp, "audio.aac")
        else:
            os.makedirs(STREAMS_DIR, exist_ok=True)
            self.audio_copy = os.path.join(STREAMS_DIR, time.strftime("%Y%m%d-%H%M%S") + " stream.aac")
        # not sys.executable: inside the app bundle that is a Python whose startup .pth launches the game itself
        venv_py = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), ".venv", "bin", "python")
        # the stream's clock: the video's first frame and the feeder's first sample are both placed against it
        t0 = time.time()
        self.feeder = subprocess.Popen([venv_py if os.path.exists(venv_py) else sys.executable, "-m", "bathero.twitch", "--audio-feed", self.fifo, "--device", dev,
                                        "--channels", ",".join(str(c) for c in chans), "--rate", str(sr), "--t0", "%.3f" % t0],
                                       stdin=subprocess.DEVNULL, stdout=subprocess.PIPE, stderr=subprocess.PIPE)
        self.audio_level = (-99.0, -99.0)            # (peak dB, rms dB) of what the feeder sent lately, after the gain
        threading.Thread(target=self._read_level, daemon=True).start()
        cmd = [ffmpeg_path(), "-hide_banner", "-loglevel", "error", "-nostdin", "-y",
               "-f", "avfoundation", "-framerate", str(self.fps), "-capture_cursor", "1", "-use_wallclock_as_timestamps", "1",
               "-thread_queue_size", "1024", "-i", f"{video_dev}:none",
               # never -use_wallclock_as_timestamps on this one: it would stamp the audio with the moment
               # ffmpeg got round to reading the pipe, and aresample would then throw away every block that
               # arrived while the loop was busy (2026-09-14: half the audio, in 200 ms chunks). The feeder
               # anchors the sample count to t0 instead, so the clock is the interface's.
               "-f", "f32le", "-ar", str(sr), "-ac", "2",
               "-thread_queue_size", "1024", "-i", self.fifo,
               "-copyts", "-map", "0:v", "-map", "1:a",
               "-vf", f"setpts=PTS-{t0:.3f}/TB,scale=-2:'min({th},ih)':flags=bilinear", "-fps_mode", "cfr",
               "-af", "aresample=async=1,alimiter=limit=0.97:level=0",
               *encode_args((0, th), self.fps, int(self.settings["stream_kbps"]), container=False),
               "-progress", "pipe:1", *tee_output(url, self.audio_copy)]
        self.ff = subprocess.Popen(cmd, stdin=subprocess.DEVNULL, stdout=subprocess.PIPE, stderr=subprocess.PIPE)
        self._progress_thread = threading.Thread(target=self._read_progress, daemon=True)
        self._progress_thread.start()
        self.active = True
        self.log(f"stream: {video_dev} + {dev} channels {chans} (feeder pid {self.feeder.pid}) at {self.fps} fps, up to {th}p, "
                 f"{self.settings['stream_kbps']} kbps, to {redact(url, self.key)}, audio copy {self.audio_copy}")
        return True

    def _audio_rate(self):
        dev = audio_device_index(self.settings["capture_audio_device"])
        if dev is None:
            return AUDIO_SR
        return int(dev[1]["default_samplerate"]) or AUDIO_SR

    # --- audio ---------------------------------------------------------------------
    def _start_audio(self, dev, sr):
        import sounddevice as sd
        idx, info = dev
        chans = [int(c) - 1 for c in self.settings["capture_audio_channels"]]
        nin = int(info["max_input_channels"])
        chans = [c for c in chans if 0 <= c < nin] or [0, min(1, nin - 1)]
        if len(chans) == 1:
            chans = chans * 2
        self._audio_spec = (idx, nin, sr, chans[:2])
        self._gain = 10 ** (read_gain() / 20)
        self.audio_samples = 0           # samples sent so far, on the stream's clock (pads included)
        self._audio_last = None
        self._audio_restarting = False
        self._open_audio()
        self.log(f"stream: audio {info['name']} channels {[c + 1 for c in chans[:2]]} at {sr} Hz")

    def _open_audio(self):
        import sounddevice as sd
        idx, nin, sr, chans = self._audio_spec

        def cb(indata, frames, t, status):
            now = time.perf_counter()
            if status.input_overflow:
                self.audio_overflows += 1
            # where this block belongs on the stream's clock: it was captured `frames` ago, plus the input latency
            expected = int((now - self.started_at - frames / sr - float(self._audio.latency or 0)) * sr)
            lag = expected - self.audio_samples
            if lag > AUDIO_LAG_PAD_S * sr or (self.audio_samples == 0 and lag > 0):
                self._aq.put(np.zeros((lag, 2), dtype="float32"))
                self.audio_samples += lag
                self.audio_pads += 1
            self.audio_blocks += 1
            self._audio_last = now
            self.audio_samples += frames
            self._aq.put(np.ascontiguousarray(indata[:, chans], dtype="float32") * self._gain)

        self._audio = sd.InputStream(device=idx, channels=nin, samplerate=sr, dtype="float32", callback=cb,
                                     **input_stream_kwargs(sr))
        self._audio.start()
        self._audio_started_pc = time.perf_counter()

    def _watch_audio(self):
        """Main thread, every frame: an input that stops delivering is reopened, at most 3 times
        (the callback pads the gap with silence when blocks come back)."""
        if self._audio is None or self._audio_restarting or self.audio_restarts >= 3:
            return
        last = self._audio_last or self._audio_started_pc
        if time.perf_counter() - last < AUDIO_STALL_S:
            return
        self._audio_restarting = True
        self.audio_restarts += 1

        def restart():
            try:
                try:
                    self._audio.abort(); self._audio.close()
                except Exception:                           # noqa: BLE001
                    pass
                self._open_audio()
                self.log(f"stream: audio stream stalled after {self.audio_blocks} blocks, reopened (restart {self.audio_restarts})")
            except Exception as e:                          # noqa: BLE001
                self.log(f"stream: audio reopen failed ({e})")
                self._audio = None
            finally:
                self._audio_restarting = False

        threading.Thread(target=restart, daemon=True).start()

    def _feed_silence(self):
        """No audio device: silence on the wall clock, so ffmpeg still has an audio track."""
        sr = self._audio_sr
        sent = 0
        while self.active or sent == 0:
            due = int((time.perf_counter() - self.started_at) * sr)
            if due > sent:
                self._aq.put(np.zeros((due - sent, 2), dtype="float32"))
                sent = due
            time.sleep(0.05)
            if not self.active:
                break

    def _write_audio(self):
        try:
            fd = open(self.fifo, "wb", buffering=0)
        except OSError:
            return
        with fd:
            while True:
                block = self._aq.get()
                if block is None:
                    break
                try:
                    fd.write(block.tobytes())
                except (BrokenPipeError, ValueError, OSError):
                    break

    # --- health -------------------------------------------------------------------------
    def poll(self):
        """ffmpeg gone (Twitch closed the connection, an error) or silent since the start: the
        stream ends and `error` says why. Returns `active`."""
        if not self.active:
            return False
        if self.ff.poll() is not None:
            self._ended()
            return False
        if not self.progress and time.perf_counter() - self.started_at > START_TIMEOUT_S:
            self.log(f"stream: no progress from ffmpeg in {START_TIMEOUT_S} s, giving up")
            self.stop()
            self.error = f"ffmpeg produced nothing in {START_TIMEOUT_S} s (screen capture stuck? permission?)"
            return False
        return True

    # --- frames from the main loop ----------------------------------------------------
    def push(self, surface):
        """Call every frame after everything is drawn; takes a copy once per 1/fps slot."""
        if not self.poll() or self.source == "screen":
            return
        now = time.perf_counter()
        slot = int((now - self.started_at) * self.fps + 0.5)
        if slot <= self._last_slot:
            return
        if self._audio is not None:
            self._watch_audio()
        self._last_slot = slot
        try:
            self._q.put_nowait((slot, surface.copy()))
        except queue.Full:
            self.dropped += 1

    def _write_frames(self):
        import pygame
        last_rgb = None
        while True:
            item = self._q.get()
            if item is None:
                break
            slot, surf = item
            if surf.get_size() != self.in_size:
                surf = pygame.transform.smoothscale(surf, self.in_size)
            rgb = pygame.image.tobytes(surf, "RGB")
            repeats = slot - self.frames
            if repeats > 0:
                self.repeated += repeats
            for r in [last_rgb or rgb] * max(0, repeats) + [rgb]:
                try:
                    self.ff.stdin.write(r)
                    self.frames += 1
                except (BrokenPipeError, ValueError, OSError):
                    return
            last_rgb = rgb

    def _read_level(self):
        """The feeder prints "level <peak dB> <rms dB>" four times a second on stdout."""
        for line in iter(self.feeder.stdout.readline, b""):
            parts = line.decode(errors="replace").split()
            if len(parts) == 3 and parts[0] == "level":
                try:
                    self.audio_level = (float(parts[1]), float(parts[2]))
                except ValueError:
                    pass

    def _read_progress(self):
        block = {}
        for line in iter(self.ff.stdout.readline, b""):
            k, _, v = line.decode(errors="replace").strip().partition("=")
            block[k] = v.strip()
            if k == "progress":
                self.progress = block
                block = {}

    # --- stop ----------------------------------------------------------------------------
    def _ended(self):
        err = ""
        try:
            err = self.ff.stderr.read().decode(errors="replace").strip().splitlines()
            err = err[-1] if err else ""
        except (OSError, ValueError):
            pass
        self.stopped_reason = redact(err, self.key) or f"ffmpeg exited ({self.ff.returncode})"
        self.log(f"stream: ended, {self.stopped_reason}")
        self.stop()
        self.error = self.stopped_reason

    def stop(self):
        if not self.active:
            return
        self.active = False
        if self.source == "screen":
            if self.ff.poll() is None:
                self.ff.terminate()                   # ffmpeg flushes and closes the rtmp session on SIGTERM
            try:
                self.ff.wait(timeout=5)
            except subprocess.TimeoutExpired:
                self.ff.kill()
            feed = ""
            if self.feeder is not None:               # the feeder ends on the broken pipe and reports its tally
                try:
                    os.close(os.open(self.fifo, os.O_RDONLY | os.O_NONBLOCK))   # releases a feeder still blocked opening the pipe
                except OSError:
                    pass
                try:
                    _, err = self.feeder.communicate(timeout=5)
                    feed = err.decode(errors="replace").strip().splitlines()[-1:] or [""]
                    feed = feed[0]
                except subprocess.TimeoutExpired:
                    self.feeder.kill()
                    feed = "feeder killed"
            try:
                os.unlink(self.fifo)
                if self.audio_copy.startswith(self.tmp):
                    os.unlink(self.audio_copy)
                os.rmdir(self.tmp)
            except OSError:
                pass
            s = int(time.perf_counter() - self.started_at)
            p = self.progress
            self.log(f"stream: stopped after {s // 60:02d}:{s % 60:02d}, ffmpeg frame {p.get('frame', '?')}, dropped {p.get('drop_frames', '?')}, "
                     f"last speed {p.get('speed', '?')}; {feed}")
            return
        self._q.put(None)
        self._writer.join(timeout=5)
        if self._audio is not None:
            try:
                self._audio.stop(); self._audio.close()
            except Exception:                           # noqa: BLE001
                pass
            self._audio = None
        self._aq.put(None)
        try:                                              # a writer still blocked opening the fifo (ffmpeg never read it) is released
            os.close(os.open(self.fifo, os.O_RDONLY | os.O_NONBLOCK))
        except OSError:
            pass
        try:
            self.ff.stdin.close()
        except OSError:
            pass
        try:
            self.ff.wait(timeout=5)
        except subprocess.TimeoutExpired:
            self.ff.kill()
        self._awriter.join(timeout=2)
        try:
            os.unlink(self.fifo); os.rmdir(self.tmp)
        except OSError:
            pass
        s = int(time.perf_counter() - self.started_at)
        self.log(f"stream: stopped after {s // 60:02d}:{s % 60:02d}, {self.frames} frames ({self.repeated} repeated, {self.dropped} dropped), "
                 f"audio {self.audio_blocks} blocks, {self.audio_pads} pads, {self.audio_restarts} restarts")

    @property
    def status(self):
        """Short text for the HUD: LIVE mm:ss with the bitrate and speed, or the last error."""
        if self.active:
            s = int(time.perf_counter() - self.started_at)
            p = self.progress
            extra = ""
            if p.get("bitrate", "N/A") not in ("N/A", ""):
                extra += f" · {p['bitrate'].replace('kbits/s', 'kbps')}"
            if p.get("speed", "N/A") not in ("N/A", ""):
                extra += f" · {p['speed']}"
            if p.get("drop_frames", "0") not in ("0", ""):
                extra += f" · dropped {p['drop_frames']}"
            return f"LIVE {s // 60:02d}:{s % 60:02d}{extra}"
        return self.error or ""


# ---------------------------------------------------------------------------
def _stamped_print(text):
    print(time.strftime("%H:%M:%S ") + text, flush=True)


def read_state(path=STATE_PATH):
    """The daemon's state file, or {}."""
    try:
        with open(path) as f:
            return json.load(f)
    except (OSError, ValueError):
        return {}


def write_state(state, path=STATE_PATH):
    os.makedirs(os.path.dirname(path), exist_ok=True)
    tmp = path + ".tmp"
    with open(tmp, "w") as f:
        json.dump(state, f)
    os.replace(tmp, path)


def pid_alive(pid):
    if not pid:
        return False
    try:
        os.kill(int(pid), 0)
    except ProcessLookupError:
        return False
    except PermissionError:
        return True
    return True


def venv_python():
    """The repo's venv python: not sys.executable, which inside the app bundle is a Python whose
    startup .pth launches the game itself."""
    py = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), ".venv", "bin", "python")
    return py if os.path.exists(py) else sys.executable


def daemon(settings):
    """The stream process: a Streamer on the screen source, on its own so the game can quit and
    relaunch under it, and the LIVE badge (badge.py: a floating window top right of every screen,
    over everything, on every Space) that exists exactly as long as this process. Writes
    STATE_PATH twice a second (pid, when it started, ffmpeg's progress); SIGTERM (the game's T,
    `--stop`) or the badge's x stops the stream cleanly and exits. After an unrequested end the
    badge stays, saying why, until its x is clicked or another stream goes live."""
    import signal
    state = read_state()
    if state.get("active") and pid_alive(state.get("pid")) and state["pid"] != os.getpid():
        sys.exit(f"stream: already live (pid {state['pid']})")
    stop = threading.Event()
    signal.signal(signal.SIGTERM, lambda *_: stop.set())
    signal.signal(signal.SIGINT, lambda *_: stop.set())
    st = Streamer({**settings, "stream_source": "screen"}, log=_stamped_print)
    base = {"pid": os.getpid(), "started": time.time(), "channel": st.settings["twitch_channel"],
            "display": st.settings["stream_display"], "height": st.settings["stream_height"], "kbps": st.settings["stream_kbps"],
            "bandwidth_test": bool(st.settings.get("stream_bandwidth_test")), "log": DAEMON_LOG}
    if not st.start((0, 0)):
        write_state({**base, "active": False, "ended": time.time(), "error": st.error})
        _stamped_print(f"stream: could not start: {st.error}")
        sys.exit(1)
    write_state({**base, "active": True, "updated": time.time(), "progress": {}})
    done = {"at": None, "requested": False}          # set once the stream is over

    def tick():
        """Every half second, from whichever loop runs: the health check, the state file, and
        what the badge shows. Returns None when the process should exit."""
        if done["at"] is None:
            if stop.is_set() or not st.poll():
                done["requested"] = stop.is_set()
                if st.active:
                    st.stop()
                done["at"] = time.time()
                write_state({**base, "active": False, "ended": done["at"], "progress": st.progress,
                             "error": None if done["requested"] else (st.error or "stream process ended")})
                _stamped_print("stream: daemon " + ("stopped" if done["requested"] else f"ended on its own ({st.error})"))
            else:
                write_state({**base, "active": True, "updated": time.time(), "progress": st.progress})
        if done["at"] is not None:
            if done["requested"] or stop.is_set():
                return None
            other = read_state()                      # another stream went live: this badge is stale
            if other.get("pid") != os.getpid() and other.get("active") and pid_alive(other.get("pid")):
                return None
            why = st.error or "ended"
            return "ended", "STREAM ENDED · " + (why if len(why) <= 40 else why[:39] + "…"), ""
        s = int(time.time() - base["started"])
        clock = f"{s // 60:02d}:{s % 60:02d}"
        if stop.is_set():
            return "stopping", f"STOPPING {clock}", ""
        p = st.progress
        if not p:
            return "starting", f"STARTING {clock}", ""
        warn = ""
        try:
            if p.get("speed", "").endswith("x") and float(p["speed"][:-1]) < 0.95:
                warn += f"  {p['speed']}"
        except ValueError:
            pass
        if p.get("drop_frames", "0") not in ("0", ""):
            warn += f"  dropped {p['drop_frames']}"
        return "live", f"LIVE {clock}", warn.strip()

    def on_gain(db):
        write_gain(db)
        _stamped_print(f"stream: gain {db:+.1f} dB")

    try:
        from .badge import Badge, TICK_S
    except ImportError as e:                          # no PyObjC: the stream still runs, without the badge
        _stamped_print(f"stream: no badge ({e})")
        while tick() is not None:
            stop.wait(0.5)
    else:
        Badge(tick, on_close=stop.set, gain=(read_gain(), GAIN_RANGE, on_gain), level=lambda: st.audio_level).run()
    _stamped_print("stream: daemon exit")


class StreamLink:
    """The game's handle on the stream: `start(size)`, `stop()`, `refresh()` (every frame,
    cheap), `push(surface)`; `phase` is "off", "starting", "live" or "stopping", `status` the
    text for the badge. On the screen source the stream is the daemon (`daemon()` above) and
    outlives the game: a link created while it runs attaches to it. On the window source the
    stream is an in-process Streamer, and dies with the game (it is the game's frames)."""

    POLL_S = 0.5           # the state file is read this often
    STALE_S = 10.0         # a daemon that has not written this long is reported as silent
    KILL_S = 10.0          # a daemon that ignores SIGTERM this long gets SIGKILL

    def __init__(self, settings, log=print):
        self.settings = {**DEFAULTS, **{k: v for k, v in settings.items() if k in DEFAULTS}}
        self.log = log
        self.local = None              # the in-process Streamer (window source)
        self.state = {}
        self.error = None              # why the last stream ended on its own, or could not start
        self.ended_reason = None       # set once when the stream ends without stop(); the app clears it
        self.attached = False          # the stream was live before this link existed
        self._stop_sent = None
        self._read_at = -1e9
        self._proc = None
        self.refresh(force=True)
        if self.phase in ("live", "starting"):
            self.attached = True
            self.log(f"stream: attached to the live stream (pid {self.state.get('pid')}, since {time.strftime('%H:%M:%S', time.localtime(self.state.get('started', 0)))})")

    @property
    def source(self):
        return "screen" if self.settings.get("stream_source", "screen") != "window" else "window"

    @property
    def out_size(self):
        return self.local.out_size if self.local is not None else (0, int(self.settings["stream_height"]))

    @property
    def active(self):
        return self.phase in ("live", "starting")

    @property
    def phase(self):
        if self.local is not None:
            return "live" if self.local.active else "off"
        st = self.state
        if not (st.get("active") and pid_alive(st.get("pid"))):
            return "off"
        if self._stop_sent is not None:
            return "stopping"
        return "live" if st.get("progress") else "starting"

    @property
    def started_wall(self):
        return self.state.get("started")

    @property
    def progress(self):
        return self.local.progress if self.local is not None else (self.state.get("progress") or {})

    def start(self, size):
        if self.active:
            return False
        self.error = self.ended_reason = None
        if self.source == "window":
            self.local = Streamer(self.settings, log=self.log)
            if self.local.start(size):
                return True
            self.error = self.local.error
            self.local = None
            return False
        self.refresh(force=True)
        if self.active:                          # started elsewhere in the meantime: that one is ours now
            return True
        if not ffmpeg_path():
            self.error = "ffmpeg not found"
            return False
        if not read_key() and not self.settings.get("stream_url"):
            self.error = f"no stream key in {TWITCH_KEY_PATH}"
            return False
        os.makedirs(os.path.dirname(DAEMON_LOG), exist_ok=True)
        log = open(DAEMON_LOG, "ab")
        repo = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
        self._proc = subprocess.Popen([venv_python(), "-m", "bathero.twitch", "--daemon", "--settings", json.dumps(self.settings)],
                                      cwd=repo, stdin=subprocess.DEVNULL, stdout=log, stderr=subprocess.STDOUT, start_new_session=True)
        log.close()
        self.state = {"pid": self._proc.pid, "active": True, "started": time.time(), "progress": {}, "channel": self.settings["twitch_channel"]}
        self._launched_at = time.perf_counter()
        self._stop_sent = None
        self._read_at = time.perf_counter()      # the daemon's first write comes after its imports; ours stands until then
        self.log(f"stream: daemon pid {self._proc.pid}, log {DAEMON_LOG}")
        return True

    def stop(self):
        """Asks the stream to stop; `phase` says "stopping" until the daemon is gone (a few seconds:
        ffmpeg flushes and closes the rtmp session). Never blocks the caller."""
        if self.local is not None:
            self.local.stop()
            self.local = None
            return
        pid = self.state.get("pid")
        if self.phase in ("live", "starting") and pid:
            try:
                os.kill(int(pid), signal.SIGTERM)
            except OSError:
                pass
            self._stop_sent = time.perf_counter()
            self.log(f"stream: stop sent to pid {pid}")

    def refresh(self, force=False):
        """Reads the state file (throttled). Notices the daemon dying on its own."""
        if self.local is not None:
            return
        now = time.perf_counter()
        if not force and now - self._read_at < self.POLL_S:
            return
        self._read_at = now
        was = self.phase
        st = read_state()
        if self._proc is not None and self._proc.poll() is None and st.get("pid") != self._proc.pid:
            st = self.state                      # our daemon has not written yet (still importing): keep the provisional state
        if self._proc is not None and self._proc.poll() is not None:
            self._proc = None
        self.state = st
        phase = self.phase
        if was in ("live", "starting") and phase == "off":
            if self._stop_sent is None and not (st.get("ended") and not st.get("error")):   # not a stop asked elsewhere (another game, --stop)
                self.ended_reason = self.error = st.get("error") or "stream process died (see the stream log)"
                self.log(f"stream: ended on its own: {self.error}")
                if not st.get("ended"):              # killed: its capture and feeder may still hold the display
                    subprocess.run(["pkill", "-9", "-f", "bathero-stream-"], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
            else:
                self.log("stream: stopped")
            self._stop_sent = None
        elif was == "stopping" and phase == "off":
            self._stop_sent = None
            self.log("stream: stopped")
        elif phase == "stopping" and now - self._stop_sent > self.KILL_S:
            self.log("stream: daemon ignored SIGTERM, killing it and its capture")
            try:
                os.kill(int(st["pid"]), signal.SIGKILL)
            except OSError:
                pass
            subprocess.run(["pkill", "-9", "-f", "bathero-stream-"], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)

    def push(self, surface):
        if self.local is not None:
            self.local.push(surface)
        else:
            self.refresh()

    def close(self):
        """The game is quitting: the window stream goes with it; the daemon stays live."""
        if self.local is not None:
            self.local.stop()
            self.local = None

    @property
    def silent_s(self):
        """Seconds since the daemon last wrote its state, when that is worrying, else 0."""
        if self.local is not None or self.phase != "live":
            return 0
        age = time.time() - float(self.state.get("updated") or time.time())
        return age if age > self.STALE_S else 0

    @property
    def status(self):
        """Short text for the badge: LIVE mm:ss with the bitrate and speed, or the last error."""
        if self.local is not None:
            return self.local.status
        phase = self.phase
        if phase == "off":
            return self.error or ""
        s = int(time.time() - float(self.state.get("started") or time.time()))
        p = self.progress
        extra = ""
        if p.get("bitrate", "N/A") not in ("N/A", ""):
            extra += f" · {p['bitrate'].replace('kbits/s', 'kbps')}"
        if p.get("speed", "N/A") not in ("N/A", ""):
            extra += f" · {p['speed']}"
        if p.get("drop_frames", "0") not in ("0", ""):
            extra += f" · dropped {p['drop_frames']}"
        if self.silent_s:
            extra += f" · no news for {self.silent_s:.0f} s"
        return f"{phase.upper()} {s // 60:02d}:{s % 60:02d}{extra}"


# ---------------------------------------------------------------------------
def audio_feed(fifo, device, channels, rate, t0=None):
    """The audio feeder process: the interface's two channels to `fifo` as float32 stereo, from
    the moment ffmpeg opens the pipe (so no backlog: the first block written is live). Blocks
    are queued by the callback and written by the main thread; the pipe closing (ffmpeg gone)
    ends it. The last stderr line is the tally: blocks, overflows, and the audio the device
    delivered against the wall clock (lost ms = buffers PortAudio never gave us).

    What goes into the pipe IS the stream's audio clock (ffmpeg counts samples, it does not
    stamp what it reads), so the callback places every block against `t0`, the stream's start:
    the head, and any gap over AUDIO_LAG_PAD_S, is filled with silence here, where the block's
    capture time is known. The same thing the window source does in `_open_audio`."""
    import sounddevice as sd
    dev = audio_device_index(device)
    if dev is None:
        sys.exit(f"audio feed: device '{device}' not found")
    idx, info = dev
    nin = int(info["max_input_channels"])
    chans = [c - 1 for c in channels if 0 < c <= nin] or [0, min(1, nin - 1)]
    if len(chans) == 1:
        chans = chans * 2
    q = queue.Queue()
    stats = {"blocks": 0, "overflows": 0, "frames": 0, "gain": 10 ** (read_gain() / 20), "peak": 0.0, "sq": 0.0, "n": 0,
             "samples": 0, "pads": 0, "padded": 0}
    latency = [float(input_stream_kwargs(rate).get("latency") or 0)]

    def cb(indata, frames, t, status):
        if status.input_overflow:
            stats["overflows"] += 1
        stats["blocks"] += 1
        stats["frames"] += frames
        if t0 is not None:
            # where this block belongs on the stream's clock: captured `frames` ago, plus the input latency
            expected = int((time.time() - t0 - frames / rate - latency[0]) * rate)
            lag = expected - stats["samples"]
            if lag > AUDIO_LAG_PAD_S * rate or (stats["samples"] == 0 and lag > 0):
                q.put(np.zeros((lag, 2), dtype="float32").tobytes())
                stats["samples"] += lag
                stats["pads"] += 1
                stats["padded"] += lag
        stats["samples"] += frames
        block = np.ascontiguousarray(indata[:, chans], dtype="float32") * stats["gain"]
        stats["peak"] = max(stats["peak"], float(np.abs(block).max()) if frames else 0.0)
        stats["sq"] += float((block * block).sum()); stats["n"] += block.size
        q.put(block.tobytes())

    out = open(fifo, "wb", buffering=0)                  # blocks until ffmpeg opens its end
    stream = sd.InputStream(device=idx, channels=nin, samplerate=rate, dtype="float32", callback=cb, **input_stream_kwargs(rate))
    latency[0] = float(stream.latency or latency[0])
    stream.start()
    started_pc = time.perf_counter()                     # not t0: that one is the stream's epoch clock, the callback's
    gain_db, checked, reported = read_gain(), started_pc, started_pc
    try:
        while True:
            block = q.get()
            try:
                out.write(block)
            except (BrokenPipeError, OSError):
                break
            now = time.perf_counter()
            if now - checked > 0.5:                      # the badge's fader writes GAIN_PATH
                checked = now
                g = read_gain()
                if g != gain_db:
                    gain_db = g
                    stats["gain"] = 10 ** (g / 20)
            if now - reported > 0.25:
                reported = now
                peak, sq, n = stats["peak"], stats["sq"], stats["n"]
                stats["peak"], stats["sq"], stats["n"] = 0.0, 0.0, 0
                rms = (sq / n) ** 0.5 if n else 0.0
                print(f"level {20 * np.log10(max(peak, 1e-5)):.1f} {20 * np.log10(max(rms, 1e-5)):.1f}", flush=True)
    finally:
        elapsed = time.perf_counter() - started_pc
        stream.stop(); stream.close()
        lost = max(0.0, elapsed - stats["frames"] / rate) * 1000
        print(f"audio feed: {stats['blocks']} blocks, {stats['overflows']} overflows, {elapsed:.1f} s, lost {lost:.0f} ms, "
              f"{stats['pads']} pads ({stats['padded'] / rate * 1000:.0f} ms), gain {gain_db:+.1f} dB", file=sys.stderr)


# ---------------------------------------------------------------------------
_TAG = re.compile(r"^@([^ ]*) ")
_PRIVMSG = re.compile(r"^:(\w+)!\S+ PRIVMSG #\S+ :(.*)$")


def parse_line(line):
    """An IRC line -> ("msg", name, text, colour) for a chat message, ("ping", payload) for a PING,
    else None."""
    if line.startswith("PING"):
        return ("ping", line.partition(" ")[2])
    tags = {}
    m = _TAG.match(line)
    if m:
        for kv in m.group(1).split(";"):
            k, _, v = kv.partition("=")
            tags[k] = v
        line = line[m.end():]
    m = _PRIVMSG.match(line)
    if not m:
        return None
    name = tags.get("display-name") or m.group(1)
    text = m.group(2)
    if text.startswith("\x01ACTION ") and text.endswith("\x01"):
        text = text[8:-1]
    colour = None
    c = tags.get("color") or ""
    if re.fullmatch(r"#[0-9A-Fa-f]{6}", c):
        colour = tuple(int(c[i:i + 2], 16) for i in (1, 3, 5))
    return ("msg", name, text, colour)


class Chat:
    """The channel's chat, read anonymously. `messages`: the last CHAT_KEEP (t, name, text, colour).
    `connected`, `error`. Reconnects until `stop()`."""

    def __init__(self, channel, log=print, on_message=None):
        self.channel = channel.lstrip("#").lower()
        self.log = log
        self.on_message = on_message
        self.messages = []
        self.connected = False
        self.error = None
        self._stop = threading.Event()
        self._lock = threading.Lock()
        self._sock = None
        self._thread = threading.Thread(target=self._run, daemon=True)
        self._thread.start()

    def stop(self):
        self._stop.set()
        s = self._sock
        if s is not None:
            try:
                s.shutdown(socket.SHUT_RDWR)
            except OSError:
                pass

    def add(self, name, text, colour=None):
        with self._lock:
            self.messages.append((time.perf_counter(), name, text, colour))
            del self.messages[:-CHAT_KEEP]
        if self.on_message:
            self.on_message(name, text)

    def _run(self):
        attempt = 0
        while not self._stop.is_set():
            try:
                self._session()
                attempt = 0
            except (OSError, ssl.SSLError) as e:
                self.error = str(e) or type(e).__name__
                self.connected = False
                if self._stop.is_set():
                    break
                wait = CHAT_RECONNECT_S[min(attempt, len(CHAT_RECONNECT_S) - 1)]
                self.log(f"chat: {self.error}, reconnecting in {wait} s")
                attempt += 1
                self._stop.wait(wait)
        self.connected = False

    def _session(self):
        ctx = ssl.create_default_context()
        raw = socket.create_connection((CHAT_HOST, CHAT_PORT), timeout=10)
        s = ctx.wrap_socket(raw, server_hostname=CHAT_HOST)
        self._sock = s
        nick = f"justinfan{random.randint(10000, 99999)}"
        s.sendall(f"CAP REQ :twitch.tv/tags twitch.tv/commands\r\nNICK {nick}\r\nJOIN #{self.channel}\r\n".encode())
        s.settimeout(5.0)          # recv, not select: TLS keeps decrypted records in its own buffer, select never sees them
        buf = b""
        last_seen = time.monotonic()
        while not self._stop.is_set():
            try:
                data = s.recv(4096)
            except TimeoutError:
                if time.monotonic() - last_seen > 360:           # Twitch pings every ~5 min; nothing for 6: the link is dead
                    raise OSError("no traffic for 6 minutes")
                continue
            if not data:
                raise OSError("connection closed")
            last_seen = time.monotonic()
            buf += data
            while b"\r\n" in buf:
                line, _, buf = buf.partition(b"\r\n")
                line = line.decode("utf-8", errors="replace")
                if not self.connected and (" 366 " in line or " JOIN " in line):
                    self.connected = True
                    self.error = None
                    self.log(f"chat: joined #{self.channel}")
                p = parse_line(line)
                if p is None:
                    continue
                if p[0] == "ping":
                    s.sendall(f"PONG {p[1]}\r\n".encode())
                else:
                    self.add(p[1], p[2], p[3])


# ---------------------------------------------------------------------------
def selftest(seconds=8, source="window", video_device=None, audio_device=None):
    """The stream pipeline against a local rtmp server (ffmpeg listening), no Twitch: synthetic
    frames (window) or the display (screen), the interface's audio, the received FLV probed
    at the end, with the audio's level so silence is caught."""
    import pygame
    out = os.path.join(tempfile.mkdtemp(prefix="bathero-selftest-"), "received.flv")
    port = 19350
    server = subprocess.Popen([ffmpeg_path(), "-hide_banner", "-loglevel", "error", "-y", "-listen", "1",
                               "-i", f"rtmp://127.0.0.1:{port}/live/test", "-c", "copy", out],
                              stdin=subprocess.DEVNULL)
    time.sleep(1.0)
    pygame.display.init()
    pygame.font.init()
    size = (1280, 720)
    st = Streamer({"stream_url": f"rtmp://127.0.0.1:{port}/live/test", "stream_height": 720, "stream_kbps": 3000, "stream_source": source,
                   "stream_video_device": video_device,
                   **({"capture_audio_device": audio_device, "capture_audio_channels": [1, 2]} if audio_device else {})})
    surf = pygame.Surface(size)
    font = pygame.font.SysFont(None, 80)
    if not st.start(size):
        server.kill()
        sys.exit(f"could not start: {st.error}")
    t0 = time.perf_counter()
    n = 0
    while time.perf_counter() - t0 < seconds and st.active:
        n += 1
        surf.fill((20 + (n % 40), 10, 60))
        surf.blit(font.render(f"bathero selftest {n}", True, (240, 240, 240)), (60, 300))
        st.push(surf)
        time.sleep(1 / 120)
    print("status:", st.status)
    st.stop()
    try:
        server.wait(timeout=10)
    except subprocess.TimeoutExpired:
        server.kill()
    from .capture import ffprobe_path
    r = subprocess.run([ffprobe_path(), "-v", "error", "-show_entries", "stream=codec_name,width,height,r_frame_rate,sample_rate,channels:format=duration",
                        "-of", "default=nw=1", out], capture_output=True, text=True)
    print(r.stdout.strip() or r.stderr.strip())
    r = subprocess.run([ffmpeg_path(), "-hide_banner", "-i", out, "-af", "volumedetect", "-vn", "-f", "null", "-"], capture_output=True, text=True)
    print("\n".join(l.split("] ", 1)[-1] for l in r.stderr.splitlines() if "mean_volume" in l or "max_volume" in l))
    print("received:", out, os.path.getsize(out) if os.path.exists(out) else "missing")


def main(argv=None):
    import argparse
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("--chat", metavar="CHANNEL", help="print the channel's chat until Ctrl-C")
    ap.add_argument("--selftest", action="store_true", help="stream synthetic frames to a local rtmp server and probe the result")
    ap.add_argument("--seconds", type=float, default=8)
    ap.add_argument("--source", choices=["window", "screen"], default="window")
    ap.add_argument("--video-device", help="screen source selftest: an avfoundation video device instead of the display")
    ap.add_argument("--audio-device", help="selftest: another input device (channels 1-2) instead of the interface")
    ap.add_argument("--audio-feed", metavar="FIFO", help="(internal) the stream's audio feeder process")
    ap.add_argument("--device", default="X18/XR18")
    ap.add_argument("--channels", default="17,18")
    ap.add_argument("--rate", type=int, default=AUDIO_SR)
    ap.add_argument("--t0", type=float, help="(internal) the stream's start (epoch): the feeder anchors its samples to it")
    ap.add_argument("--daemon", action="store_true", help="(internal, the game's T) the stream process: runs until SIGTERM")
    ap.add_argument("--settings", metavar="JSON", help="--daemon: the stream settings as JSON (the game's settings.json otherwise)")
    ap.add_argument("--status", action="store_true", help="print the stream daemon's state")
    ap.add_argument("--stop", action="store_true", help="stop a live stream daemon (what the game's T does)")
    args = ap.parse_args(argv)
    if args.audio_feed:
        audio_feed(args.audio_feed, args.device, [int(c) for c in args.channels.split(",")], args.rate, args.t0)
    elif args.daemon:
        if args.settings:
            settings = json.loads(args.settings)
        else:
            from .kit import load_settings
            settings = load_settings()
        daemon(settings)
    elif args.status:
        st = read_state()
        alive = st.get("active") and pid_alive(st.get("pid"))
        if not st:
            print("no stream yet")
        elif alive:
            p = st.get("progress") or {}
            print(f"live: pid {st['pid']}, since {time.strftime('%H:%M:%S', time.localtime(st['started']))}, twitch.tv/{st.get('channel')}, "
                  f"frame {p.get('frame', '?')}, {p.get('bitrate', '?')}, speed {p.get('speed', '?')}, dropped {p.get('drop_frames', '?')}")
        else:
            print(f"off (last one ended {time.strftime('%H:%M:%S', time.localtime(st.get('ended') or st.get('started') or 0))}"
                  + (f", {st['error']}" if st.get("error") else "") + ")")
    elif args.stop:
        st = read_state()
        if st.get("active") and pid_alive(st.get("pid")):
            os.kill(int(st["pid"]), signal.SIGTERM)
            print(f"stop sent to pid {st['pid']}")
        else:
            print("no live stream")
    elif args.chat:
        c = Chat(args.chat, on_message=lambda name, text: print(f"{name}: {text}"))
        try:
            while True:
                time.sleep(1)
        except KeyboardInterrupt:
            c.stop()
    elif args.selftest:
        selftest(args.seconds, args.source, args.video_device, args.audio_device)
    else:
        ap.print_help()


if __name__ == "__main__":
    main()
