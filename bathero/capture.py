"""Recording: the game's own frames, the sound card's output and an optional camera (the
iPhone), each to its own file, and editions rendered from those raws.

    V in the game starts and stops a take. Every take is its own folder in ~/Movies/bathero/:

        <stamp> <name>/
            take.json                 when, how long, what was played, where everything is
            raw/screen.mp4            the game exactly as it was on screen (the window's size)
            raw/camera.mp4            the iPhone, 1920x1080
            raw/audio.wav             the interface's mix
            computer.mp4              edition: the screen, 16:9, the camera picture-in-picture
            social.mp4                edition: 1080x1920 for Reels / TikTok / Shorts: the screen as a
                                      thumbnail across the top, the camera under it (capture_split
                                      of the height, cropped to fill), the pair centred vertically
            edits/<edition>-<style>/  what Claude cut from an edition (edit.py)

    Both editions are rendered when the take stops (the user manages the disk). They can be
    rendered again from the raws at any time: the Edit screen, or
    `python -m bathero.capture --render "<take folder>" social`.

How it works, and why not a screen recorder:
- Video: the game hands a copy of its surface to a writer thread 30 times a second; the
  thread converts it and pipes raw frames into ffmpeg (h264 by VideoToolbox). No screen
  recording permission, no display to pick, and the main loop only pays for one blit.
- Audio: a sounddevice input stream on the interface (default "X18/XR18"), two of its
  input channels (default 17-18: set the XR18's USB sends 17/18 to Main L/R in X-AIR
  Edit and they carry exactly the mix you hear, the game and Bitwig included). Written
  to a WAV by a thread. `python -m bathero.capture --check` shows which channels have
  signal.
- Camera (optional): ffmpeg's avfoundation records the first video device whose name
  matches the camera setting ("iPhone" by default, so Continuity Camera or Camo) to its
  own file, on the same clock as the game's frames.
- Sync: the picture is sampled by wall clock (frame n = n / FPS s after the start, missed
  slots repeat the previous frame); the wav is rewritten onto the take's clock from the
  arrival time of every audio block (the interface's sample clock is not ours); then
  measure_sync tracks the drum's onsets against the MIDI strokes over the take (re-timing
  the wav if the offset moves) and measures the camera delay from the camera's view of the
  monitor, all into take.json.
- Editions: ffmpeg overlays the camera on the game raw and muxes the audio with those
  corrections (render_edition). The raws stay, so both editions can be made from one take.
"""
import glob
import json
import os
import queue
import re
import shutil
import subprocess
import sys
import tempfile
import threading
import time

import numpy as np

OUT_DIR = os.path.expanduser("~/Movies/bathero")
FPS = 30
DEFAULTS = {"capture_audio_device": "X18/XR18", "capture_audio_channels": [17, 18], "capture_split": 0.5,
            "capture_camera": "iPhone", "capture_pip": 0.28, "capture_corner": "br", "capture_camera_delay_ms": 0}
CORNERS = ["br", "bl", "tr", "tl"]
SPLITS = [0.32, 0.4, 0.5, 0.6]     # social edition: the camera's share of the 1920 px height (0.32 = the whole 16:9 picture)
PIPS = [0.2, 0.28, 0.36, 0.45, 0.55]  # computer edition: the camera picture's height as a share of the frame height
SOCIAL_SIZE = (1080, 1920)
PREVIEW_SIZE = (640, 360)
AUDIO_STALL_S = 1.0          # input stream silent this long: reopen it (Recorder._watch_audio)
# Sync, measured from the raws when a take ends (measure_sync): the interface's audio against
# the MIDI hits in the run logs, the camera against the game picture as the camera sees it on the
# monitor. Both corrections go to take.json and render_edition applies them.
DISPLAY_LAG_MS = 25          # the monitor shows a frame this long after the game drew it (refresh + panel)
SYNC_LAG_SPAN = 30           # frames each way searched for the camera lag (1 s)
SYNC_MIN_HITS = 8            # isolated hits needed to trust the audio measurement
SYNC_MAX_AUDIO_SHIFT = 0.25  # s; a larger correction means the measurement is wrong, keep the estimate
SYNC_MIN_CAMERA_PEAK = 1.5   # the camera lag's correlation peak must beat the runner-up by this factor
SYNC_WINDOW_S = 20           # the audio offset is tracked over the take in windows this long, every SYNC_STEP_S
SYNC_STEP_S = 10
SYNC_SEARCH_S = 0.15         # offset searched each way to lock on (under half a sixteenth at 60 bpm)
SYNC_FOLLOW_S = 0.06         # then each window is searched this far from the previous one (the drift is slow)
SYNC_RANGE_S = 0.6           # the grid the tracker can wander over during a take
SYNC_WINDOW_PEAK = 1.8       # a window counts when its best offset beats the median score by this factor
SYNC_WARP_MIN_MS = 8         # the wav is re-timed when the offset moves more than this over the take
CAMERA_FPS = 30              # what cameras accept (Continuity Camera: 30 or 60)
CAMERA_SIZE = (1920, 1080)   # what the take records from the camera (the social edition shows it 960 px tall)
# Raws: HEVC by VideoToolbox at a high bitrate. Measured 2026-09-09 on the M1 Max on fresh camera
# frames: h264_videotoolbox stops spending bits at ~2 Mbps whatever -b:v or -q:v says (SSIM 0.9984),
# hevc_videotoolbox at a high target reaches libx264 crf 16 (SSIM 0.9992) with no CPU cost while the
# game runs. The editions are the deliverables: libx264 crf 16, H.264 High, offline in the finish thread.
RAW_ENCODE = ["-c:v", "hevc_videotoolbox", "-b:v", "40M", "-pix_fmt", "yuv420p", "-tag:v", "hvc1"]
EDITION_ENCODE = ["-c:v", "libx264", "-preset", "medium", "-crf", "16", "-profile:v", "high", "-pix_fmt", "yuv420p"]
# The interface's USB return is quiet (a take measured 2026-09-11: peaks -18 dBFS, mean -42 dB,
# inaudible on a phone), so the editions are loudness-normalised to what the platforms expect.
EDITION_AUDIO = ["-af", "loudnorm=I=-16:TP=-1.5:LRA=11", "-c:a", "aac", "-b:a", "256k"]
SCALE = "flags=lanczos"


def pip_rect(frame, pip, corner, margin=24):
    """Computer layout: where the camera goes on a landscape frame of size `frame` (w, h), a 16:9
    picture `pip` of the frame height tall in `corner`. (x, y, w, h) in frame pixels, even sizes."""
    w, h = frame
    ph = int(pip * h) // 2 * 2
    pw = int(ph * 16 / 9) // 2 * 2
    x = w - pw - margin if corner in ("br", "tr") else margin
    y = h - ph - margin if corner in ("br", "bl") else margin
    return int(x), int(y), pw, ph


def social_layout(screen, split, size=SOCIAL_SIZE):
    """The social edition on a canvas of `size` (1080x1920): the screen (w, h) scaled to the full
    width as a thumbnail, the camera under it `split` of the canvas height tall (cropped to fill),
    the pair centred vertically. Returns the two rects (x, y, w, h), even sizes."""
    W, H = size
    sw, sh = screen
    gh = int(W * sh / sw) // 2 * 2
    ch = min(int(H * split) // 2 * 2, H - gh)
    top = int((H - gh - ch) / 2) // 2 * 2
    return (0, top, W, gh), (0, top + gh, W, ch)


def cover(surface, size):
    """`surface` scaled to fill `size` keeping its aspect, centred and cropped (what the take's
    compose does with the camera in the social layout). Returns a new surface of `size`."""
    import pygame
    iw, ih = surface.get_size()
    w, h = int(size[0]), int(size[1])
    if iw == 0 or ih == 0 or w <= 0 or h <= 0:
        return pygame.Surface((max(1, w), max(1, h)))
    k = max(w / iw, h / ih)
    sw, sh = max(w, int(iw * k + 0.5)), max(h, int(ih * k + 0.5))
    scaled = pygame.transform.smoothscale(surface, (sw, sh))
    out = pygame.Surface((w, h))
    out.blit(scaled, (0, 0), pygame.Rect((sw - w) // 2, (sh - h) // 2, w, h))
    return out
AUDIO_SR = 44100


FFMPEG_DIRS = ["/opt/homebrew/bin", "/opt/homebrew/anaconda3/bin", "/usr/local/bin", os.path.expanduser("~/.local/bin")]


def _tool(name):
    """A tool on PATH or in the usual places: the app bundle launched from Finder or rcmd
    gets a minimal PATH (seen 2026-09-08: 'ffmpeg not found' inside the game)."""
    found = shutil.which(name)
    if found:
        return found
    for d in FFMPEG_DIRS:
        cand = os.path.join(d, name)
        if os.path.exists(cand):
            return cand
    return None



def input_stream_kwargs(samplerate):
    """Buffer settings for any PortAudio input opened on the interface the game also plays
    through. PortAudio sets the CoreAudio device's buffer size to its own latency target
    (1024+ frames) and SDL's output, opened with MIXER_BUFFER frames, goes silent within a
    second and stays silent (measured 2026-09-09 on the X18; reopening the mixer did not
    help, and opening the mixer after such an input killed the input instead). Asking
    PortAudio for exactly the mixer's buffer keeps both streams alive."""
    from .sounds import MIXER_BUFFER
    return {"blocksize": MIXER_BUFFER, "latency": MIXER_BUFFER / float(samplerate)}


def ffmpeg_path():
    return _tool("ffmpeg")


def ffprobe_path():
    return _tool("ffprobe")


def list_video_devices():
    """[(index, name)] of avfoundation video devices, or [] without ffmpeg."""
    ff = ffmpeg_path()
    if not ff:
        return []
    try:
        r = subprocess.run([ff, "-hide_banner", "-f", "avfoundation", "-list_devices", "true", "-i", ""],
                           capture_output=True, text=True, timeout=15)
    except (OSError, subprocess.TimeoutExpired):
        return []
    out, video = [], False
    for line in r.stderr.splitlines():
        if "AVFoundation video devices" in line:
            video = True
        elif "AVFoundation audio devices" in line:
            video = False
        elif video:
            m = re.search(r"\[(\d+)\] (.+)$", line)
            if m:
                out.append((int(m.group(1)), m.group(2).strip()))
    return out


def find_camera(substring):
    if not substring:
        return None
    for idx, name in list_video_devices():
        if substring.lower() in name.lower() or ("camo" in name.lower() and "iphone" in substring.lower()):
            return idx, name
    return None


def audio_device_index(substring):
    import sounddevice as sd
    for i, d in enumerate(sd.query_devices()):
        if d["max_input_channels"] > 0 and substring.lower() in d["name"].lower():
            return i, d
    return None


def run_logs_between(t0, duration):
    """The run logs of the levels played between t0 and t0 + duration (epoch seconds)."""
    from .runlog import RUNS_DIR
    logs = []
    for path in sorted(glob.glob(os.path.join(RUNS_DIR, "*.jsonl"))):
        try:
            with open(path) as f:
                head = json.loads(f.readline())
            if head.get("ended", 0) >= t0 and head.get("started", 0) <= t0 + duration:
                logs.append({"path": path, "chart": head["chart"]["name"], "started": head["started"],
                             "ended": head.get("ended"), "stats": head.get("stats")})
        except (OSError, ValueError, KeyError):
            continue
    return logs


def render_edition(take_dir, edition, settings=None, log=print):
    """Render one edition of a take from its raws, any time after the take:
    "computer": the screen with the camera picture-in-picture (capture_pip / capture_corner);
    "social": 1080x1920, the screen as a thumbnail across the top and the camera under it
    (capture_split of the height, cropped to fill), the pair centred vertically.
    The take's own settings apply unless `settings` overrides them. Writes
    <take_dir>/<edition>.mp4, records it in take.json and returns the path. Paths in take.json
    are relative to the take folder."""
    with open(os.path.join(take_dir, "take.json")) as f:
        meta = json.load(f)
    st = {**DEFAULTS, **meta.get("settings", {}),
          **{k: v for k, v in (settings or {}).items() if k in ("capture_pip", "capture_corner", "capture_split")}}
    raw = meta.get("raws", {}).get("screen")
    if raw is None:
        raise ValueError("this take has no screen raw")
    if edition not in ("computer", "social"):
        raise ValueError(f"unknown edition {edition!r}")
    w, h = raw["size"]
    cmd = [ffmpeg_path(), "-hide_banner", "-loglevel", "error", "-y", "-i", os.path.join(take_dir, raw["file"])]
    cam_i = aud_i = None
    n = 1
    cam, aud = meta.get("camera"), meta.get("audio")
    if cam and os.path.exists(os.path.join(take_dir, cam["file"])):
        cmd += ["-itsoffset", f"{-float(cam.get('delay_ms', 0)) / 1000:.3f}", "-i", os.path.join(take_dir, cam["file"])]
        cam_i, n = n, n + 1
    if aud and os.path.exists(os.path.join(take_dir, aud["file"])):
        cmd += ["-itsoffset", f"{float(aud.get('offset', 0)):.3f}", "-i", os.path.join(take_dir, aud["file"])]
        aud_i, n = n, n + 1
    encode = list(EDITION_ENCODE)
    if edition == "social":
        W, H = SOCIAL_SIZE
        (_, gy, gw, gh), (_, cy, cw, ch) = social_layout((w, h), float(st["capture_split"]))
        if cam_i is not None:
            cmd += ["-filter_complex", f"[0:v]scale={gw}:{gh}:{SCALE},pad={W}:{H}:0:{gy}:black[g];"
                                       f"[{cam_i}:v]scale={cw}:{ch}:force_original_aspect_ratio=increase:{SCALE},crop={cw}:{ch}[cam];"
                                       f"[g][cam]overlay=0:{cy}:eof_action=pass[v]", "-map", "[v]"] + encode
        else:
            cmd += ["-filter_complex", f"[0:v]scale={gw}:{gh}:{SCALE},pad={W}:{H}:0:{(H - gh) // 2 // 2 * 2}:black[v]", "-map", "[v]"] + encode
    elif cam_i is not None:
        x, y, pw, ph = pip_rect((w, h), float(st["capture_pip"]), st.get("capture_corner", "br"))
        cmd += ["-filter_complex", f"[{cam_i}:v]scale={pw}:{ph}:{SCALE}[pip];[0:v][pip]overlay={x}:{y}:eof_action=pass[v]", "-map", "[v]"] + encode
    else:
        cmd += ["-map", "0:v"] + encode          # the raw is HEVC: the edition is always H.264
    if aud_i is not None:
        cmd += ["-map", f"{aud_i}:a"] + EDITION_AUDIO
    out = os.path.join(take_dir, f"{edition}.mp4")
    cmd += ["-t", f"{float(meta['duration']):.3f}", "-movflags", "+faststart", out]
    r = subprocess.run(cmd, capture_output=True, text=True)
    if r.returncode != 0:
        raise RuntimeError(f"ffmpeg: {r.stderr.strip()[-300:]}")
    meta.setdefault("editions", {})[edition] = f"{edition}.mp4"
    with open(os.path.join(take_dir, "take.json"), "w") as f:
        json.dump(meta, f, indent=1)
    log(f"edition saved: {out}")
    return out


def _gray_series(path, w, h):
    """Every frame of a video as a (frames, h*w) float32 array of gray levels, scaled to w x h."""
    r = subprocess.run([ffmpeg_path(), "-hide_banner", "-loglevel", "error", "-i", path, "-vf", f"scale={w}:{h}",
                        "-f", "rawvideo", "-pix_fmt", "gray", "pipe:1"], capture_output=True)
    a = np.frombuffer(r.stdout, dtype=np.uint8)
    n = len(a) // (w * h)
    return a[: n * w * h].reshape(n, w * h).astype(np.float32)


def _xcorr(a, b, span):
    """Normalised cross-correlation of two equal series for lags -span..span; positive lag = b later."""
    a = (a - a.mean()) / (a.std() + 1e-9); b = (b - b.mean()) / (b.std() + 1e-9)
    out = np.empty(2 * span + 1)
    for i, L in enumerate(range(-span, span + 1)):
        out[i] = (a[: len(a) - L] * b[L:]).mean() if L >= 0 else (a[-L:] * b[: len(b) + L]).mean()
    return out


def measure_camera_lag(take_dir, meta):
    """How many frames the camera runs behind the game picture, from the raws: the monitor is in the
    camera's view (the drummer faces it), so the camera regions whose brightness changes follow the
    screen's are found, and the lag that lines them up is measured (frame changes cross-correlated,
    sub-frame by parabolic fit). Returns {"frames", "ms", "peak", "runner_up", "blocks"} or None."""
    cam = meta.get("camera")
    if not cam or not meta.get("raws", {}).get("screen"):
        return None
    scr = _gray_series(os.path.join(take_dir, meta["raws"]["screen"]["file"]), 32, 32).mean(axis=1)
    blocks = _gray_series(os.path.join(take_dir, cam["file"]), 64, 36)
    n = min(len(scr), len(blocks))
    if n < 10 * FPS:
        return None
    ds = np.diff(scr[:n]); db = np.diff(blocks[:n], axis=0)
    ds = (ds - ds.mean()) / (ds.std() + 1e-9)
    db = (db - db.mean(axis=0)) / (db.std(axis=0) + 1e-9)
    # a block follows the screen if some lag 0..span correlates it well with the screen's changes
    best = np.full(db.shape[1], -1.0)
    for L in range(0, SYNC_LAG_SPAN + 1):
        best = np.maximum(best, (ds[: len(ds) - L, None] * db[L:]).mean(axis=0))
    thr = max(0.08, np.percentile(best, 98))
    chosen = np.nonzero(best >= thr)[0]
    if len(chosen) < 4:
        return None
    series = db[:, chosen].mean(axis=1)
    xc = _xcorr(ds, series, SYNC_LAG_SPAN)
    k = int(xc.argmax()); runner = float(np.sort(xc)[-2])
    frac = 0.0
    if 0 < k < len(xc) - 1 and (xc[k - 1] - 2 * xc[k] + xc[k + 1]) < 0:
        frac = float(0.5 * (xc[k - 1] - xc[k + 1]) / (xc[k - 1] - 2 * xc[k] + xc[k + 1]))
    lag = k - SYNC_LAG_SPAN + frac
    return {"frames": round(lag, 2), "ms": round(lag * 1000 / FPS, 1), "peak": round(float(xc[k]), 4),
            "runner_up": round(runner, 4), "blocks": int(len(chosen))}


def measure_audio_offset(take_dir, meta):
    """Where the drum sounds in the interface's wav against when the MIDI said the stroke
    happened (the run logs' hit wall times): median onset minus expected position over isolated
    hits (with the guide on, only hits early enough that the drum sounds before the guide).
    Returns {"hits", "shift_ms", "p10_ms", "p90_ms"} or None. shift > 0 = the drum sounds
    earlier than the MIDI, so the wav must be delayed that much more."""
    aud = meta.get("audio")
    if not aud or not meta.get("run_logs"):
        return None
    import soundfile as sf
    x, sr = sf.read(os.path.join(take_dir, aud["file"]), dtype="float32")
    if x.ndim == 1:
        x = x[:, None]
    env = np.abs(x).mean(axis=1)
    k = max(1, int(sr * 0.002))
    env = np.convolve(env, np.ones(k) / k, mode="same")
    t0, offset = meta["t0"], float(aud.get("offset", 0))
    diffs = []
    for r in meta["run_logs"]:
        try:
            with open(r["path"]) as f:
                head = json.loads(f.readline())
                hits = [json.loads(l) for l in f if '"hit"' in l]
        except (OSError, ValueError):
            continue
        guide = bool(head.get("guide", True))       # the guide sounds the note at its chart time, the drum at the hit
        prev = None
        for h in hits:
            if h.get("kind") != "hit":
                continue
            wall = h["wall"]
            isolated = prev is None or wall - prev > 0.3
            prev = wall
            if not isolated or h.get("judge") == "STRAY" or h.get("velocity", 0) < 40:
                continue
            if guide and (h.get("error_ms") is None or h["error_ms"] > -40):
                continue                                    # the guide's note would sound first; only clearly early hits
            t = wall - t0 - offset
            i0, i1 = int((t - 0.08) * sr), int((t + 0.12) * sr)
            if i0 < 0 or i1 >= len(env):
                continue
            seg = env[i0:i1]
            floor = seg[: int(0.04 * sr)].mean()
            above = np.nonzero(seg > max(4 * floor + 1e-4, 0.3 * seg.max()))[0]
            if len(above):
                diffs.append(t - (i0 + above[0]) / sr)
    if len(diffs) < SYNC_MIN_HITS:
        return None
    d = np.array(diffs)
    return {"hits": int(len(d)), "shift_ms": round(float(np.median(d)) * 1000, 1),
            "p10_ms": round(float(np.percentile(d, 10)) * 1000, 1), "p90_ms": round(float(np.percentile(d, 90)) * 1000, 1)}


def align_audio_to_clock(path, clock, started_pc, latency, log=print):
    """Re-time a take's wav onto the take's clock from the recorder's callback log: the
    interface's sample clock is not ours (measured 2026-09-11 on the X18: 44071 samples per
    second of wall time, wandering; 150 ms early after six minutes), so each block's arrival
    time says where its samples belong. The wav is rewritten so that sample j sits at
    offset + j / sr of take time; the device's own file stays beside it as audio.device.wav.
    Returns {"offset", "rate", "drift_ms", "blocks"} or None."""
    import soundfile as sf
    if len(clock) < 50:
        return None
    x, sr = sf.read(path, dtype="float32")
    if x.ndim == 1:
        x = x[:, None]
    n = len(x)
    pcs = np.array([c[0] for c in clock]) - started_pc - latency      # take time the block's last sample was captured
    S = np.array([c[1] for c in clock], dtype=np.float64)              # samples written after the block
    c = pcs - S / sr                                                    # take time of sample 0 as each block sees it
    # one point per second of samples, the median inside it (callback jitter is a few ms)
    edges = np.arange(0, S[-1] + sr, sr)
    which = np.searchsorted(edges, S, side="right") - 1
    pts_s, pts_c = [], []
    for b in range(len(edges)):
        sel = which == b
        if sel.sum() >= 3:
            pts_s.append(float(np.median(S[sel]))); pts_c.append(float(np.median(c[sel])))
    if len(pts_s) < 2:
        return None
    pts_s, pts_c = np.array(pts_s), np.array(pts_c)
    offset = float(pts_c[0])
    tau = pts_c + pts_s / sr                                            # take time of those samples
    rate = float((pts_s[-1] - pts_s[0]) / (tau[-1] - tau[0]))
    drift_ms = float((pts_c[-1] - pts_c[0]) * 1000)
    if abs(drift_ms) < 1.0 and abs(rate - sr) < 1.0:
        return {"offset": offset, "rate": round(rate, 2), "drift_ms": round(drift_ms, 1), "blocks": int(len(clock)), "rewritten": False}
    device = path[:-4] + ".device.wav"
    shutil.move(path, device)
    out = np.empty_like(x)
    step = sr * 60
    for a in range(0, n, step):
        b = min(n, a + step)
        T = offset + np.arange(a, b) / sr                               # take time of output sample j
        pos = np.clip(np.interp(T, tau, pts_s), 0, n - 1.001)           # source sample at that time
        i = pos.astype(int); f = (pos - i)[:, None]
        out[a:b] = x[i] * (1 - f) + x[i + 1] * f
    sf.write(path, out, sr, subtype="PCM_16")
    log(f"audio aligned to the take's clock: device ran at {rate:.1f} Hz, {drift_ms:+.0f} ms over the take, rewritten")
    return {"offset": offset, "rate": round(rate, 2), "drift_ms": round(drift_ms, 1), "blocks": int(len(clock)), "rewritten": True}


def _level_clocks(meta):
    """Per run log: (wall time of chart time 0, chart note times, guide on) from the hits' wall
    and song_t (the game clock is the wall clock, so one hit pins the level)."""
    out = []
    for r in meta.get("run_logs", []):
        try:
            with open(r["path"]) as f:
                head = json.loads(f.readline())
                hits = [json.loads(l) for l in f if '"hit"' in l]
        except (OSError, ValueError):
            continue
        anchors = [h["wall"] - h["song_t"] for h in hits if h.get("kind") == "hit" and "song_t" in h]
        if anchors:
            out.append((float(np.median(anchors)), [n["t"] for n in head["chart"]["notes"]], bool(head.get("guide", True))))
    return out


def measure_audio_curve(take_dir, meta):
    """How early the drum sounds in the wav, over the take: every stroke in the run logs has a
    wall time, so in SYNC_WINDOW_S windows the offset that best lines the wav's onsets up with
    the strokes is found (tracked from one window to the next). The strokes are the anchor
    because that is what the viewer compares: the hands on the camera against the sound.
    Without strokes the guide notes (at their chart times) are used instead.
    Returns [(t, early_s, peak)] on the wav's timeline; empty when nothing lines up."""
    aud = meta.get("audio")
    if not aud:
        return []
    import soundfile as sf
    x, sr = sf.read(os.path.join(take_dir, aud["file"]), dtype="float32")
    env = np.abs(x).mean(axis=1) if x.ndim > 1 else np.abs(x)
    k = max(1, int(sr * 0.002))
    env = np.convolve(env, np.ones(k) / k, mode="same")
    d = np.clip(np.diff(env, prepend=env[0]), 0, None)
    t0, base = meta["t0"], float(aud.get("offset_estimated", aud["offset"]))
    events = []
    for r in meta.get("run_logs", []):
        try:
            with open(r["path"]) as f:
                events += [json.loads(l)["wall"] - t0 - base for l in f if '"kind": "hit"' in l]
        except (OSError, ValueError, KeyError):
            continue
    if len(events) < 15:
        for w0, notes, guide in _level_clocks(meta):
            if guide:
                events += [w0 + t - t0 - base for t in notes]
    events = np.array(sorted(events))
    if len(events) < 15:
        return []
    taus = np.arange(-SYNC_RANGE_S, SYNC_RANGE_S + 1e-9, 0.001)
    prev, locked, curve = 0.0, False, []
    for a in range(0, int(len(env) / sr) - SYNC_WINDOW_S + 1, SYNC_STEP_S):
        ev = events[(events > a) & (events < a + SYNC_WINDOW_S)]
        if len(ev) < 15:
            continue
        idx = np.clip(((ev[:, None] + taus[None, :]) * sr).astype(int), 0, len(d) - 1)
        score = d[idx].mean(axis=0)
        med = np.median(score) + 1e-12
        near = np.abs(taus - prev) < (SYNC_FOLLOW_S if locked else SYNC_SEARCH_S)
        if score[near].max() < SYNC_WINDOW_PEAK * med:
            continue
        k = int(np.argmax(np.where(near, score, -1)))
        locked = True
        prev = float(taus[k])
        curve.append((a + SYNC_WINDOW_S / 2, round(prev, 4), round(float(score[k] / med), 2)))
    return curve


def warp_audio(src, dst, curve):
    """Write `dst`: `src` re-timed so that a sound the curve says was early by e at time t lands
    at t. Piecewise linear between the curve's points, flat beyond them."""
    import soundfile as sf
    x, sr = sf.read(src, dtype="float32")
    if x.ndim == 1:
        x = x[:, None]
    ts = np.array([c[0] for c in curve]); es = np.array([c[1] for c in curve])
    n = len(x)
    out = np.empty_like(x)
    step = sr * 60
    for a in range(0, n, step):
        b = min(n, a + step)
        t = np.arange(a, b) / sr
        pos = (t + np.interp(t, ts, es)) * sr                  # where the sound for instant t sits in the source
        pos = np.clip(pos, 0, n - 1.001)
        i = pos.astype(int); f = (pos - i)[:, None]
        out[a:b] = x[i] * (1 - f) + x[i + 1] * f
    sf.write(dst, out, sr, subtype="PCM_16")


def measure_sync(take_dir, log=print):
    """Measure the audio offset and the camera lag of a take from its raws and write the corrections
    to take.json (audio.offset, camera.delay_ms; the recorder's estimates stay as *_estimated).
    Run when a take ends and by `--sync`; render the editions afterwards. Returns the report."""
    with open(os.path.join(take_dir, "take.json")) as f:
        meta = json.load(f)
    report = meta.get("sync", {})
    aud = meta.get("audio")
    if aud:                                                 # measure against the recorder's estimate, not a previous correction
        aud["offset"] = aud.get("offset_estimated", aud["offset"])
        if aud.get("file_device"):
            aud["file"] = aud["file_device"]
    curve = []
    try:
        curve = measure_audio_curve(take_dir, meta)
    except Exception as e:                                  # noqa: BLE001
        report["curve_error"] = str(e)
    if curve:
        early = [c[1] for c in curve]
        report["audio_curve"] = curve
        span = (max(early) - min(early)) * 1000
        log(f"sync: game sounds early by {early[0] * 1000:+.0f} ms at the start, {early[-1] * 1000:+.0f} ms at the end ({len(curve)} windows)")
        if span > SYNC_WARP_MIN_MS:
            src = os.path.join(take_dir, aud["file"])
            dst = os.path.join(take_dir, "raw", "audio.synced.wav")
            warp_audio(src, dst, curve)
            aud["file_device"] = aud["file"]
            aud["file"] = "raw/audio.synced.wav"
            log(f"sync: audio re-timed over the take (moved {span:.0f} ms) -> raw/audio.synced.wav")
        else:
            aud["offset"] = round(aud["offset"] + float(np.median(early)), 4)
            log(f"sync: audio offset moved {np.median(early) * 1000:+.0f} ms from the game sounds")
    a = None
    try:
        a = measure_audio_offset(take_dir, meta)
    except Exception as e:                                  # noqa: BLE001
        report["audio_error"] = str(e)
    if a is not None:
        report["audio"] = a
        if curve:                                           # the game clock is the timeline; the drum's own latency is reported
            log(f"sync: drum sounds {-a['shift_ms']:+.0f} ms after the MIDI over {a['hits']} hits (kept: that is what the drummer heard)")
        elif abs(a["shift_ms"]) <= SYNC_MAX_AUDIO_SHIFT * 1000:
            aud.setdefault("offset_estimated", aud["offset"])
            aud["offset"] = round(aud["offset"] + a["shift_ms"] / 1000, 4)
            log(f"sync: audio offset set from {a['hits']} drum hits ({a['shift_ms']:+.0f} ms) -> {aud['offset'] * 1000:.0f} ms")
        else:
            log(f"sync: audio measurement ignored ({a['shift_ms']:+.0f} ms from {a['hits']} hits is too large)")
    elif not curve:
        log("sync: audio not measured (no wav, no run logs, guide off and too few isolated hits)")
    try:
        c = measure_camera_lag(take_dir, meta)
    except Exception as e:                                  # noqa: BLE001
        c, report["camera_error"] = None, str(e)
    if c is not None:
        cam = meta["camera"]
        if c["peak"] > 0 and c["peak"] >= SYNC_MIN_CAMERA_PEAK * max(c["runner_up"], 1e-6):
            cam.setdefault("delay_ms_setting", cam.get("delay_ms", 0))
            cam["delay_ms"] = round(max(0.0, c["ms"] - DISPLAY_LAG_MS), 1)
            log(f"sync: camera runs {c['frames']:.2f} frames ({c['ms']:.0f} ms) behind the screen, delay set to {cam['delay_ms']:.0f} ms")
        else:
            log(f"sync: camera lag unclear (peak {c['peak']:.3f} vs {c['runner_up']:.3f}), delay kept at {cam.get('delay_ms', 0):.0f} ms")
        report["camera"] = c
    elif meta.get("camera"):
        log("sync: camera lag not measured (the monitor is not in the camera's view?)")
    meta["sync"] = report
    with open(os.path.join(take_dir, "take.json"), "w") as f:
        json.dump(meta, f, indent=1)
    return report


def retime_raws(take_dir, inserts, log=print):
    """Repair a take whose video raws lost frames (the recorder before 2026-09-11 skipped the
    slots of a stalled main loop instead of repeating the last frame, so the picture ran ahead of
    the audio by the stalled time). `inserts` = [(frame_index, frames)]: before each raw frame
    index that many copies of the previous frame are inserted, in the screen and the camera raws
    alike (they share one clock). The originals stay beside them as *.unsynced.mp4; take.json
    records the repair. Then render the editions again."""
    with open(os.path.join(take_dir, "take.json")) as f:
        meta = json.load(f)
    inserts = sorted((int(i), int(n)) for i, n in inserts if int(n) > 0)
    files = [meta["raws"]["screen"]["file"]] + ([meta["camera"]["file"]] if meta.get("camera") else [])
    for rel in files:
        path = os.path.join(take_dir, rel)
        keep = path[:-4] + ".unsynced.mp4"
        if not os.path.exists(keep):
            shutil.move(path, keep)
        parts, labels, prev = [], [], 0
        for k, (idx, n) in enumerate(inserts):
            parts.append(f"[0:v]trim=start_frame={prev}:end_frame={idx},setpts=PTS-STARTPTS,"
                         f"tpad=stop_mode=clone:stop_duration={n / FPS:.6f}[s{k}]")
            labels.append(f"[s{k}]"); prev = idx
        parts.append(f"[0:v]trim=start_frame={prev},setpts=PTS-STARTPTS[s{len(inserts)}]"); labels.append(f"[s{len(inserts)}]")
        graph = ";".join(parts) + f";{''.join(labels)}concat=n={len(labels)}:v=1:a=0,fps={FPS}[v]"
        cmd = [ffmpeg_path(), "-hide_banner", "-loglevel", "error", "-y", "-i", keep, "-filter_complex", graph, "-map", "[v]",
               *RAW_ENCODE, path]
        r = subprocess.run(cmd, capture_output=True, text=True)
        if r.returncode != 0:
            raise RuntimeError(f"ffmpeg: {r.stderr.strip()[-300:]}")
        log(f"retimed {rel}: {sum(n for _, n in inserts)} frames inserted at {[i for i, _ in inserts]}")
    meta["retimed"] = inserts
    meta["frames"] = meta.get("frames", 0) + sum(n for _, n in inserts)
    with open(os.path.join(take_dir, "take.json"), "w") as f:
        json.dump(meta, f, indent=1)


class Recorder:
    """One take: the screen, the interface's mix and the camera, each to its own file (the raws)
    in a take folder, then both editions rendered from them; an edition can be rendered again
    any time later (`render`). Start/stop from the main loop; `push` takes the frames."""

    def __init__(self, settings, log=print):
        self.settings = {**DEFAULTS, **{k: v for k, v in settings.items() if k in DEFAULTS}}
        self.log = log
        self.active = False
        self.composing = None          # (thread, label) while the raws are finished or an edition renders
        self.error = None
        self.result = None             # the last file written (a take folder or an edition)
        self.started_at = None
        self.feed = None               # the camera while recording
        self.size = None
        self._last_slot = -1
        self._q = None

    # --- start ---------------------------------------------------------------------
    def start(self, size, name="take"):
        """size: (w, h) of the surfaces the game will push (the window's size)."""
        if self.active or not ffmpeg_path():
            self.error = None if self.active else "ffmpeg not found"
            return False
        self.error = None
        self.size = (int(size[0]) // 2 * 2, int(size[1]) // 2 * 2)   # the encoder wants even sizes
        self.tmp = tempfile.mkdtemp(prefix="bathero-take-")
        self.name = "".join(c if c.isalnum() or c in "-_ " else "_" for c in name).strip() or "take"
        self.t0 = time.time()
        self.started_at = time.perf_counter()
        self._last_slot = -1
        # video: raw frames piped to ffmpeg
        w, h = self.size
        self.ff_video = subprocess.Popen(
            [ffmpeg_path(), "-hide_banner", "-loglevel", "error", "-y",
             "-f", "rawvideo", "-pix_fmt", "rgb24", "-s", f"{w}x{h}", "-r", str(FPS), "-i", "pipe:0",
             *RAW_ENCODE, os.path.join(self.tmp, "screen.mp4")],
            stdin=subprocess.PIPE, stdout=subprocess.DEVNULL, stderr=subprocess.PIPE)
        self._q = queue.Queue(maxsize=8)
        self.frames = 0
        self.dropped = 0
        self.repeated = 0
        self._writer = threading.Thread(target=self._write_frames, daemon=True)
        self._writer.start()
        # audio: sounddevice input -> wav
        self.audio_path = None
        self._audio = None
        self.audio_blocks = self.audio_restarts = self.audio_overflows = 0
        self.audio_t0 = None
        self._audio_started = self.t0
        dev = audio_device_index(self.settings["capture_audio_device"])
        if dev is not None:
            try:
                self._start_audio(dev)
            except Exception as e:                        # noqa: BLE001
                self.log(f"recording: no audio ({e})")
                self._audio = None
        else:
            self.log(f"recording: audio device '{self.settings['capture_audio_device']}' not found, video only")
        # camera: frames come into memory and are written on the same clock as the game's
        self.cam_path = None
        self.feed = None
        self.ff_cam = None
        self.cam_frames = 0
        cam = find_camera(self.settings["capture_camera"])
        if cam is not None:
            try:
                self.feed = CameraFeed(cam)
                self.cam_path = os.path.join(self.tmp, "camera.mp4")
                self.ff_cam = subprocess.Popen(
                    [ffmpeg_path(), "-hide_banner", "-loglevel", "error", "-y",
                     "-f", "rawvideo", "-pix_fmt", "rgb24", "-s", "%dx%d" % CAMERA_SIZE, "-r", str(FPS), "-i", "pipe:0",
                     *RAW_ENCODE, self.cam_path],
                    stdin=subprocess.PIPE, stdout=subprocess.DEVNULL, stderr=subprocess.PIPE)
                self.log(f"recording: camera {cam[1]}")
            except OSError as e:
                self.log(f"recording: camera failed ({e})")
                self.feed = None
        self.active = True
        return True

    def _start_audio(self, dev):
        import sounddevice as sd
        import soundfile as sf
        idx, info = dev
        chans = [int(c) - 1 for c in self.settings["capture_audio_channels"]]
        nin = int(info["max_input_channels"])
        chans = [c for c in chans if 0 <= c < nin] or [0, min(1, nin - 1)]
        sr = int(info["default_samplerate"]) or AUDIO_SR
        self.audio_path = os.path.join(self.tmp, "audio.wav")
        self._aq = queue.Queue()
        self._afile = sf.SoundFile(self.audio_path, "w", samplerate=sr, channels=len(chans), subtype="PCM_16")

        self.audio_overflows = 0
        self.audio_blocks = 0            # callbacks that delivered samples (0 at the end = a silent wav)
        self.audio_restarts = 0
        self.audio_t0 = None             # wall time of the first sample, set by the first callback
        self.audio_samples = 0           # samples written so far (gap padding included)
        self._aclock = []                # (perf_counter at the callback, samples written after it): the device's clock against ours
        self._audio_started = time.time()
        self._audio_last = None          # perf_counter of the last callback
        self._audio_gap_from = None      # perf_counter the stream stalled at: the next block is preceded by that much silence
        self._audio_restarting = False
        self._audio_spec = (idx, nin, sr, chans)
        self._open_audio()
        self._awriter = threading.Thread(target=self._write_audio, daemon=True)
        self._awriter.start()
        self.log(f"recording: audio {info['name']} channels {[c + 1 for c in chans]} at {sr} Hz")

    def _open_audio(self):
        import sounddevice as sd
        idx, nin, sr, chans = self._audio_spec

        def cb(indata, frames, t, status):
            now = time.perf_counter()
            if status.input_overflow:
                self.audio_overflows += 1
            if self.audio_t0 is None:                    # the first sample's wall time: now, minus this buffer and the input latency
                self.audio_t0 = time.time() - frames / sr - float(self._audio.latency or 0)
            if self._audio_gap_from is not None:          # the stream was reopened: keep the wav aligned with silence for the gap
                gap = int((now - self._audio_gap_from) * sr) - frames
                if gap > 0:
                    self._aq.put(np.zeros((gap, len(chans)), dtype="float32"))
                    self.audio_samples += gap
                self._audio_gap_from = None
            self.audio_blocks += 1
            self._audio_last = now
            self.audio_samples += frames
            self._aclock.append((now, self.audio_samples))
            self._aq.put(indata[:, chans].copy())

        self._audio = sd.InputStream(device=idx, channels=nin, samplerate=sr, dtype="float32", callback=cb,
                                     **input_stream_kwargs(sr))
        self._audio.start()
        self._audio_started_pc = time.perf_counter()
        self._audio_latency = float(self._audio.latency or 0)

    def _watch_audio(self):
        """Main thread, every frame: an input stream that stops delivering (seen 2026-09-09: the first
        take of a freshly launched app opened its stream without error and got no callback at all,
        44-byte wav; the ROADMAP also records the mixer reopening killing an input, PaMacCore -50)
        is reopened, at most 3 times, and the wav gets silence for the gap."""
        if self._audio is None or self._audio_restarting or self.audio_restarts >= 3:
            return
        last = self._audio_last or self._audio_started_pc
        if time.perf_counter() - last < AUDIO_STALL_S:
            return
        self._audio_restarting = True
        self.audio_restarts += 1
        stalled_at = last

        def restart():
            try:
                try:
                    self._audio.abort(); self._audio.close()
                except Exception:                           # noqa: BLE001
                    pass
                self._audio_gap_from = stalled_at if self.audio_blocks else None
                self._open_audio()
                if self.audio_blocks:
                    self.log(f"recording: audio stream stalled after {self.audio_blocks} blocks, reopened (restart {self.audio_restarts}, gap padded)")
                else:
                    self._audio_started = time.time()
                    self.log(f"recording: audio stream delivered nothing in {AUDIO_STALL_S:.1f} s, reopened (restart {self.audio_restarts})")
            except Exception as e:                          # noqa: BLE001
                self.log(f"recording: audio reopen failed ({e})")
                self._audio = None
            finally:
                self._audio_restarting = False

        threading.Thread(target=restart, daemon=True).start()

    # --- frames from the main loop ----------------------------------------------------
    def push(self, surface):
        """Call every frame; takes a copy once per 1/FPS slot of the wall clock. The frame's index
        in the raw is its slot, so a slot the main loop missed (a stall, a full queue) is filled by
        the writer with the previous frame and the picture never runs ahead of the audio (before
        2026-09-11 missed slots were skipped: 1.2 s ahead after a 6 minute take)."""
        if not self.active:
            return
        now = time.perf_counter()
        slot = int((now - self.started_at) * FPS + 0.5)
        if slot <= self._last_slot:
            return
        if self._audio is not None:
            self._watch_audio()
        self._last_slot = slot
        cam = self.feed.frame if self.feed is not None else None
        try:
            self._q.put_nowait((slot, surface.copy(), cam))
        except queue.Full:
            self.dropped += 1

    def _write_frames(self):
        import pygame
        black = None
        last_rgb = last_cam = None
        while True:
            item = self._q.get()
            if item is None:
                break
            slot, surf, cam = item
            if surf.get_size() != self.size:
                surf = pygame.transform.smoothscale(surf, self.size)
            rgb = pygame.image.tobytes(surf, "RGB")
            if self.ff_cam is not None and cam is None:          # camera not streaming yet: black frame keeps sync
                black = black or bytes(CAMERA_SIZE[0] * CAMERA_SIZE[1] * 3)
                cam = black
            repeats = slot - self.frames                         # slots nobody pushed: repeat the previous frame
            if repeats > 0:
                self.repeated += repeats
            frames = [(last_rgb or rgb, last_cam or cam)] * max(0, repeats) + [(rgb, cam)]
            for r, c in frames:
                try:
                    self.ff_video.stdin.write(r)
                    self.frames += 1
                except (BrokenPipeError, ValueError, OSError):
                    return
                if self.ff_cam is not None:
                    try:
                        self.ff_cam.stdin.write(c)
                        self.cam_frames += 1
                    except (BrokenPipeError, ValueError, OSError):
                        self.ff_cam = None
            last_rgb, last_cam = rgb, cam

    def _write_audio(self):
        while True:
            block = self._aq.get()
            if block is None:
                break
            self._afile.write(block)

    # --- stop, the take folder, the editions -------------------------------------------
    def stop(self):
        """Stop and finish in the background: the raws move to ~/Movies/bathero/<stamp> <name>/raw/
        with take.json beside them, then every edition is rendered. Returns the take folder."""
        if not self.active:
            return None
        self.active = False
        duration = time.perf_counter() - self.started_at
        self._q.put(None)
        self._writer.join(timeout=10)
        try:
            self.ff_video.stdin.close()
        except OSError:
            pass
        if self.audio_path:
            if self._audio is not None:
                try:
                    self._audio.stop(); self._audio.close()
                except Exception:                           # noqa: BLE001
                    pass
            self._aq.put(None); self._awriter.join(timeout=10); self._afile.close()
        if self.feed is not None:
            self.feed.stop()
        if self.ff_cam is not None:
            try:
                self.ff_cam.stdin.close()
            except OSError:
                pass
        stamp = time.strftime("%Y%m%d-%H%M%S", time.localtime(self.t0))
        os.makedirs(OUT_DIR, exist_ok=True)
        take_dir = os.path.join(OUT_DIR, f"{stamp} {self.name}")
        t = threading.Thread(target=self._finish, args=(take_dir, duration), daemon=True)
        self.composing = (t, os.path.basename(take_dir))
        t.start()
        return take_dir

    def _finish(self, take_dir, duration):
        try:
            self.ff_video.wait(timeout=60)
        except subprocess.TimeoutExpired:
            self.ff_video.kill()
        if self.ff_cam is not None:
            try:
                self.ff_cam.wait(timeout=10)
            except subprocess.TimeoutExpired:
                self.ff_cam.kill()
        raw_dir = os.path.join(take_dir, "raw")
        os.makedirs(raw_dir, exist_ok=True)
        raws = {}
        src = os.path.join(self.tmp, "screen.mp4")
        if os.path.exists(src) and os.path.getsize(src) > 1000:
            shutil.move(src, os.path.join(raw_dir, "screen.mp4"))
            raws["screen"] = {"file": "raw/screen.mp4", "size": list(self.size)}
        camera = None
        if self.cam_path and os.path.exists(self.cam_path) and os.path.getsize(self.cam_path) > 1000 and self.cam_frames > 0:
            shutil.move(self.cam_path, os.path.join(raw_dir, "camera.mp4"))
            camera = {"file": "raw/camera.mp4", "frames": self.cam_frames, "size": list(CAMERA_SIZE),
                      "delay_ms": float(self.settings.get("capture_camera_delay_ms", 0))}
        audio = None
        if self.audio_path and os.path.exists(self.audio_path):
            wav = os.path.join(raw_dir, "audio.wav")
            shutil.move(self.audio_path, wav)
            audio = {"file": "raw/audio.wav", "offset": (self.audio_t0 or self._audio_started) - self.t0,
                     "overflows": self.audio_overflows, "blocks": self.audio_blocks, "restarts": self.audio_restarts}
            try:
                clock = align_audio_to_clock(wav, self._aclock, self.started_at, getattr(self, "_audio_latency", 0.0), log=self.log)
            except Exception as e:                          # noqa: BLE001
                clock = None
                self.log(f"audio clock alignment failed ({e}); the device's timing stays")
            if clock is not None:
                audio["offset"] = clock["offset"]
                audio["clock"] = clock
        meta = {"take": take_dir, "t0": self.t0, "duration": duration, "fps": FPS, "size": list(self.size),
                "frames": self.frames, "dropped": self.dropped, "repeated": self.repeated, "raws": raws, "camera": camera, "audio": audio,
                "settings": {k: self.settings[k] for k in ("capture_pip", "capture_corner", "capture_split")},
                "run_logs": run_logs_between(self.t0, duration), "editions": {}}
        with open(os.path.join(take_dir, "take.json"), "w") as f:
            json.dump(meta, f, indent=1)
        shutil.rmtree(self.tmp, ignore_errors=True)
        self.log(f"take saved: {take_dir} ({self.frames} frames, {self.repeated} repeated, {self.dropped} dropped, camera {'yes' if camera else 'no'}, "
                 f"audio {'yes' if audio else 'no'}" + (f": {audio['blocks']} blocks, {audio['overflows']} overflows, "
                                                        f"{audio['restarts']} restarts, offset {audio['offset'] * 1000:.0f} ms" if audio else "") + ")")
        self.result = take_dir
        if raws:
            self.composing = (threading.current_thread(), f"sync of {os.path.basename(take_dir)}")
            try:
                measure_sync(take_dir, log=self.log)
            except Exception as e:                              # noqa: BLE001
                self.log(f"sync measurement failed ({e}); the estimates stay")
        for edition in (("computer", "social") if raws else ()):   # both editions, every time; the user manages the disk
            self.composing = (threading.current_thread(), f"{edition} edition of {os.path.basename(take_dir)}")
            try:
                self.result = render_edition(take_dir, edition, log=self.log)
            except (OSError, ValueError, RuntimeError) as e:
                self.error = f"{edition} edition failed: {e}"
                self.log(self.error)
        self.composing = None

    def render(self, take_dir, edition):
        """Render an edition of an earlier take in the background (the Edit screen)."""
        if self.active or self.composing is not None:
            return False
        self.error = None

        def run():
            try:
                self.result = render_edition(take_dir, edition, settings=self.settings, log=self.log)
            except (OSError, ValueError, RuntimeError) as e:
                self.error = f"{edition} edition failed: {e}"
                self.log(self.error)
            self.composing = None

        t = threading.Thread(target=run, daemon=True)
        self.composing = (t, f"{edition} edition of {os.path.basename(take_dir)}")
        t.start()
        return True

    @property
    def status(self):
        """Short text for the HUD: recording time, or what is being rendered, or the last result."""
        if self.active:
            s = int(time.perf_counter() - self.started_at)
            return f"REC {s // 60:02d}:{s % 60:02d}"
        if self.composing is not None:
            return f"rendering {self.composing[1]}..."
        if self.error:
            return self.error
        if self.result:
            return f"saved {os.path.relpath(self.result, OUT_DIR)}"
        return ""


class CameraFeed:
    """Raw camera frames from ffmpeg into memory; the recorder samples the latest one on its
    own 30 Hz clock, the same clock that samples the game's picture, so the two streams are
    aligned by construction (no file offsets to guess)."""

    def __init__(self, camera, size=CAMERA_SIZE):
        self.size = size
        self.frame = None
        self.frames = 0
        self.error = None
        w, h = size
        self.proc = subprocess.Popen(
            [ffmpeg_path(), "-hide_banner", "-loglevel", "error", "-f", "avfoundation", "-framerate", str(CAMERA_FPS),
             "-pixel_format", "uyvy422", "-video_size", "%dx%d" % size, "-i", f"{camera[0]}:none",
             # one output frame per camera frame: with the default cfr sync ffmpeg fills the camera's
             # timestamp gaps with duplicates (2x at 720p, at 1080p one frozen frame 450 times a second)
             "-fps_mode", "passthrough", "-vf", f"scale={w}:{h}", "-f", "rawvideo", "-pix_fmt", "rgb24", "pipe:1"],
            stdout=subprocess.PIPE, stderr=subprocess.PIPE, stdin=subprocess.DEVNULL)
        self.thread = threading.Thread(target=self._read, daemon=True)
        self.thread.start()

    def _read(self):
        n = self.size[0] * self.size[1] * 3
        while True:
            buf = self.proc.stdout.read(n)
            if len(buf) < n:
                break
            self.frame = buf
            self.frames += 1
        if not self.frames:
            err = self.proc.stderr.read().decode(errors="replace")
            lines = [l for l in err.splitlines() if l.strip() and "deprecated" not in l and "NSKVO" not in l]
            self.error = lines[-1][-160:] if lines else "the camera sent no frames"

    def stop(self):
        if self.proc.poll() is None:
            self.proc.kill()


class CameraPreview:
    """Live frames from the camera through ffmpeg (rawvideo on a pipe), latest frame kept."""

    def __init__(self, camera, size=PREVIEW_SIZE, fps=15):
        self.size = size
        self.frame = None            # bytes of the latest rgb24 frame
        self.error = None
        self.frames = 0
        w, h = size
        try:
            # cameras advertise 30/60 fps modes (the iPhone refuses 15): capture at 30, keep every other frame
            self.proc = subprocess.Popen(
                [ffmpeg_path(), "-hide_banner", "-loglevel", "error", "-f", "avfoundation", "-framerate", str(CAMERA_FPS),
                 "-pixel_format", "uyvy422", "-video_size", "1280x720", "-i", f"{camera[0]}:none",
                 "-vf", f"fps={fps},scale={w}:{h}", "-f", "rawvideo", "-pix_fmt", "rgb24", "pipe:1"],
                stdout=subprocess.PIPE, stderr=subprocess.PIPE, stdin=subprocess.DEVNULL)
        except OSError as e:
            self.proc = None
            self.error = str(e)
            return
        self._err = []
        threading.Thread(target=self._drain_stderr, daemon=True).start()
        self.thread = threading.Thread(target=self._read, daemon=True)
        self.thread.start()

    def _drain_stderr(self):
        """ffmpeg's stderr, read as it comes: left unread, 64 KB of AVFoundation warnings fill the
        pipe and ffmpeg blocks on it, sending no more frames ("waiting for frames..." for ever)."""
        for line in iter(self.proc.stderr.readline, b""):
            self._err.append(line.decode(errors="replace"))
            del self._err[:-50]

    def _read(self):
        n = self.size[0] * self.size[1] * 3
        while True:
            buf = self.proc.stdout.read(n)
            if len(buf) < n:
                break
            self.frame = buf
            self.frames += 1
        self.proc.wait()
        err = "".join(self._err)
        if not self.frames:
            lines = [l for l in err.splitlines() if l.strip() and "deprecated" not in l and "NSKVO" not in l and "Please use" not in l]
            self.error = (lines[-1][-160:] if lines else "the camera sent no frames")

    def surface(self):
        if self.frame is None:
            return None
        import pygame
        return pygame.image.frombuffer(self.frame, self.size, "RGB")

    def stop(self):
        if self.proc is not None and self.proc.poll() is None:
            self.proc.kill()


class AudioMeter:
    """RMS level per selected channel of the capture device, updated by the input stream."""

    def __init__(self, settings):
        import sounddevice as sd
        self.settings = {**DEFAULTS, **{k: v for k, v in settings.items() if k in DEFAULTS}}
        self.levels = None           # dB per selected channel
        self.error = None
        dev = audio_device_index(self.settings["capture_audio_device"])
        if dev is None:
            self.error = f"audio device '{self.settings['capture_audio_device']}' not found"
            self.stream = None
            return
        idx, info = dev
        nin = int(info["max_input_channels"])
        self.chans = [c - 1 for c in self.settings["capture_audio_channels"] if 0 <= c - 1 < nin]
        sr = int(info["default_samplerate"])
        self.stream = sd.InputStream(device=idx, channels=nin, samplerate=sr, dtype="float32", callback=self._cb,
                                     **input_stream_kwargs(sr))
        self.stream.start()

    def _cb(self, indata, frames, t, status):
        rms = np.sqrt(np.mean(indata[:, self.chans] ** 2, axis=0)) if self.chans else np.zeros(0)
        self.levels = [float(20 * np.log10(v + 1e-9)) for v in rms]

    def stop(self):
        if self.stream is not None:
            self.stream.stop(); self.stream.close()


def check(settings=None, seconds=2.0):
    """Record a moment from the audio device and report each channel's level, so the
    routing (XR18 USB sends) can be verified. Also lists cameras."""
    import sounddevice as sd
    settings = {**DEFAULTS, **(settings or {})}
    dev = audio_device_index(settings["capture_audio_device"])
    if dev is None:
        print(f"audio device '{settings['capture_audio_device']}' not found. Inputs:")
        for i, d in enumerate(sd.query_devices()):
            if d["max_input_channels"] > 0:
                print(f"  {d['name']} ({d['max_input_channels']} in)")
    else:
        idx, info = dev
        n = int(info["max_input_channels"]); sr = int(info["default_samplerate"])
        print(f"{info['name']}: {n} inputs at {sr} Hz; recording {seconds:.0f} s, play something...")
        rec = sd.rec(int(seconds * sr), samplerate=sr, channels=n, device=idx, dtype="float32"); sd.wait()
        rms = np.sqrt(np.mean(rec ** 2, axis=0))
        want = settings["capture_audio_channels"]
        for c in range(n):
            db = 20 * np.log10(rms[c] + 1e-9)
            mark = " <- selected" if c + 1 in want else ""
            bar = "#" * int(max(0, (db + 60) / 2))
            print(f"  in {c + 1:2d}: {db:6.1f} dBFS {bar}{mark}")
        print("Signal on the selected channels means the mix is coming back over USB. If they are silent,"
              " set USB sends 17/18 to Main L/R in X-AIR Edit, or pick other channels in settings.")
    cams = list_video_devices()
    print("cameras:", ", ".join(f"[{i}] {nm}" for i, nm in cams) or "none (ffmpeg missing or no devices)")
    cam = find_camera(settings["capture_camera"])
    print(f"camera for the take: {cam[1] if cam else 'none found matching ' + repr(settings['capture_camera'])}")


if __name__ == "__main__":
    import argparse
    ap = argparse.ArgumentParser(description="bathero takes: check the devices, or render an edition of a take")
    ap.add_argument("--check", action="store_true", help="record a moment and report the channel levels and cameras (default)")
    ap.add_argument("--render", nargs=2, metavar=("TAKE_DIR", "EDITION"), help="render computer or social from a take folder's raws")
    ap.add_argument("--retime", nargs="+", metavar="ARG", help="TAKE_DIR FRAME:COUNT ...: insert COUNT frozen frames before raw frame FRAME (repair a take whose video ran ahead), measure the sync, render both editions")
    ap.add_argument("--sync", metavar="TAKE_DIR", help="measure the audio offset and camera lag of a take from its raws, then render both editions")
    a = ap.parse_args()
    if a.render:
        print(render_edition(*a.render))
    elif a.retime or a.sync:
        if a.retime:
            take_dir, *specs = a.retime
            retime_raws(take_dir, [tuple(int(v) for v in spec.split(":")) for spec in specs])
        else:
            take_dir = a.sync
        print(measure_sync(take_dir))
        for ed in ("computer", "social"):
            print(render_edition(take_dir, ed))
    else:
        check()
