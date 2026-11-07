"""Device watcher and toasts: the TD-17 (MIDI), the sound card and the camera (the
iPhone) are polled in a thread; each appearance or disappearance becomes an event the
main loop turns into a toast and a reaction (reopen the MIDI input, reopen the mixer,
mark the camera available for takes).
"""
import queue
import threading
import time

import mido

MIDI_POLL_S = 1.5        # MIDI and audio device lists are cheap to read
CAMERA_POLL_S = 12.0     # the camera list spawns ffmpeg, so less often
TOAST_S = 4.5


class DeviceWatcher:
    """Polls in a thread; poll results land in `events` as (kind, name, connected)."""

    def __init__(self, midi_hint, audio_hint, camera_hint, midi_hints=(), camera_list=None, audio_list=None):
        self.midi_hint = midi_hint
        self.audio_hint = audio_hint
        self.camera_hint = camera_hint
        self.midi_hints = tuple(midi_hints)
        self._camera_list = camera_list
        self._audio_list = audio_list
        self.events = queue.Queue()
        self.state = {"midi": None, "audio": None, "camera": None}   # kind -> name when present
        self.seen_midi = set()
        self._stop = threading.Event()
        self.paused = False           # while a take records: leave the camera alone
        self.thread = threading.Thread(target=self._run, daemon=True)

    def start(self):
        self.thread.start()

    def stop(self):
        self._stop.set()

    # --- one poll of everything (also usable synchronously in tests) ----------------
    def poll(self, cameras=True):
        self._check("midi", self._find_midi())
        self._check("audio", self._find_audio())
        if cameras and not self.paused:
            self._check("camera", self._find_camera())

    def _check(self, kind, name):
        was = self.state[kind]
        if bool(name) != bool(was) or (name and was and name != was):
            if was and name and name != was:
                self.events.put((kind, was, False))
            self.state[kind] = name
            self.events.put((kind, name or was, bool(name)))

    def _find_midi(self):
        try:
            names = mido.get_input_names()
        except Exception:                                   # noqa: BLE001
            return self.state["midi"]
        for n in names:
            if n not in self.seen_midi and self.seen_midi:
                self.events.put(("midi-other", n, True))    # any new MIDI device is worth a word
        self.seen_midi = set(names)
        if self.midi_hint:
            return next((n for n in names if self.midi_hint.lower() in n.lower()), None)
        return next((n for n in names if any(h in n.lower() for h in self.midi_hints)), None)

    def _find_audio(self):
        try:
            names = self._audio_list() if self._audio_list else _output_devices()
        except Exception:                                   # noqa: BLE001
            return self.state["audio"]
        current = set(names)
        if getattr(self, "_audio_set", None) is not None and current != self._audio_set:
            self.events.put(("audio-list", ", ".join(sorted(current ^ self._audio_set)), True))
        self._audio_set = current
        if not self.audio_hint:
            return "system default"
        return next((n for n in names if self.audio_hint.lower() in n.lower()), None)

    def _find_camera(self):
        try:
            from .capture import find_camera
            found = (self._camera_list or find_camera)(self.camera_hint)
        except Exception:                                   # noqa: BLE001
            return self.state["camera"]
        return found[1] if found else None

    def _run(self):
        last_cam = 0.0
        while not self._stop.is_set():
            do_cam = time.perf_counter() - last_cam >= CAMERA_POLL_S
            self.poll(cameras=do_cam)
            if do_cam:
                last_cam = time.perf_counter()
            self._stop.wait(MIDI_POLL_S)


def _output_devices():
    import sounddevice as sd
    return [d["name"] for d in sd.query_devices() if d["max_output_channels"] > 0]


class Toasts:
    """Short messages stacked in a corner, fading out."""

    def __init__(self):
        self.items = []          # (t0, text, color, key)

    def add(self, text, color, key=None):
        """key: a toast with a key replaces the live one with the same key (a value that
        changes on every key press, like the volume, shows one toast, not a stack)."""
        if key is not None:
            self.items = [it for it in self.items if it[3] != key]
        self.items.append((time.perf_counter(), text, color, key))
        self.items = self.items[-5:]

    def live(self):
        now = time.perf_counter()
        self.items = [it for it in self.items if now - it[0] < TOAST_S]
        return [(now - t0, tx, c) for t0, tx, c, _ in self.items]
