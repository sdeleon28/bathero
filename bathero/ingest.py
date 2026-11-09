"""Song import: a .wav in the inbox becomes a playable song level.

Runs in its own venv (.venv-ingest: torch, demucs, beat_this, adtof), never inside the game:

    .venv-ingest/bin/python -m bathero.ingest            # process what is in the inbox, once
    .venv-ingest/bin/python -m bathero.ingest --watch    # and keep watching it
    .venv-ingest/bin/python -m bathero.ingest FILE.wav   # one file

Per song, into ~/Music/bathero/songs/<slug>/ (each step skipped when its output exists, so a
crash or a new step reruns only what is missing):

    backing.wav, drums.wav   Demucs htdemucs_ft, two stems (the drums and everything else)
    beats.txt                beat_this on the full mix: beat times and their position in the bar
    drums.mid                ADTOF on the drum stem: kick 35, snare 38, tom 47, hi-hat 42, cymbal 49
    song.json                the chart: notes on the song's own beat grid (bathero.songs reads it)
    sections.json            All-In-One (all-in-one-infer, harmonix-all) on the full mix: the song's
                             sections (intro, verse, chorus, bridge, break, solo, inst, outro) per bar,
                             bathero's bars; the bars before the vocals start are the intro and the
                             bars after they end the outro (its own vocals stem says so)

A song made before a step existed gets that step on the next run (the file in the inbox stays).

While it runs it keeps ~/.config/bathero/ingest.json (songs.STATUS_PATH) up to date for the game's
songs screen: its pid and whether it watches, a heartbeat every 2 s, the song and the step in hand
(with the steps it still has to take), the queue, the failures, and a count of songs finished.
SIGTERM (the game's toggle) stops it cleanly: the step's process is killed and its temporary files go.
"""
import argparse
import json
import os
import re
import shutil
import signal
import subprocess
import sys
import threading
import time

import mido

from .songs import INBOX, SONGS_DIR, STATUS_PATH

VENV_BIN = os.path.dirname(sys.executable)
AUDIO_EXT = (".wav", ".aif", ".aiff", ".flac", ".mp3", ".m4a")

# ADTOF's classes -> bathero instruments
ADTOF_KEYS = {35: "kick", 36: "kick", 38: "snare", 40: "snare", 47: "tom1", 42: "hihat", 46: "hihat", 49: "crash", 51: "crash"}
MIN_FREE_MB = 600      # a song takes ~150 MB, its temporary stems ~250 MB more: never fill the disk
VOCAL_SILENT_DB = -50  # a bar of the vocals stem under this has nobody singing
SNAP_MS = 35          # a note this close to a sixteenth (or a triplet eighth) of the song's grid lands on it
DEDUPE_MS = 40        # one instrument heard twice this close is one stroke


STEPS = {"separate": "separating the drums", "beats": "finding the beats", "transcribe": "transcribing the drums",
         "chart": "writing the chart", "sections": "finding the sections"}
EXPECT_S = {"separate": 40, "beats": 15, "transcribe": 30, "chart": 2, "sections": 100}   # for the progress bar


class Status:
    """The status file: written whole (atomically) on every change and every 2 s."""

    def __init__(self, watching):
        self.lock = threading.Lock()
        self.d = {"pid": os.getpid(), "watching": watching, "started": time.time(), "inbox": INBOX,
                  "current": None, "queue": [], "failed": {}, "imported": 0, "last": None}
        self.write()
        threading.Thread(target=self._beat, daemon=True).start()

    def _beat(self):
        while self.d["pid"]:
            time.sleep(2)
            self.write()

    def write(self, **kw):
        with self.lock:
            self.d.update(kw)
            self.d["updated"] = time.time()
            try:
                os.makedirs(os.path.dirname(STATUS_PATH), exist_ok=True)
                with open(STATUS_PATH + ".part", "w") as f:
                    json.dump(self.d, f)
                os.replace(STATUS_PATH + ".part", STATUS_PATH)
            except OSError:
                pass

    def begin(self, title, song, steps):
        self.write(current={"title": title, "song": song, "steps": steps, "expect": [EXPECT_S[s] for s in steps],
                            "step": None, "label": "starting", "step_started": time.time(), "started": time.time()},
                   queue=[q for q in self.d["queue"] if q != title])

    def step(self, key):
        cur = self.d["current"]
        if cur is not None and key in cur["steps"]:
            self.write(current=dict(cur, step=cur["steps"].index(key), label=STEPS[key], step_started=time.time()))

    def done(self, title):
        self.d["failed"].pop(title, None)
        self.write(current=None, imported=self.d["imported"] + 1, last=title)

    def fail(self, title, why):
        self.d["failed"][title] = why[-300:]
        self.write(current=None)

    def close(self):
        self.write(pid=None, watching=False, current=None, queue=[])


STATUS = None          # the running importer's Status


def report(key):
    if STATUS is not None:
        STATUS.step(key)


def slug(name):
    s = re.sub(r"[^\w\s-]", "", name, flags=re.UNICODE).strip().lower()
    return re.sub(r"[\s_-]+", "-", s)[:80] or "song"


def run(cmd):
    print("  $", " ".join(os.path.basename(c) if i == 0 else c for i, c in enumerate(cmd)), flush=True)
    subprocess.run(cmd, check=True, stdout=subprocess.DEVNULL, stderr=subprocess.PIPE)


def separate(src, out):
    if os.path.exists(os.path.join(out, "drums.wav")) and os.path.exists(os.path.join(out, "backing.wav")):
        return
    report("separate")
    tmp = os.path.join(out, "_demucs")
    run([os.path.join(VENV_BIN, "python"), "-m", "demucs", "-n", "htdemucs_ft", "--two-stems", "drums",
         "-d", "mps", "-o", tmp, "--filename", "{stem}.{ext}", src])
    d = os.path.join(tmp, "htdemucs_ft")
    os.replace(os.path.join(d, "drums.wav"), os.path.join(out, "drums.wav"))
    os.replace(os.path.join(d, "no_drums.wav"), os.path.join(out, "backing.wav"))
    for root, dirs, files in os.walk(tmp, topdown=False):
        for f in files:
            os.remove(os.path.join(root, f))
        os.rmdir(root)


def track_beats(src, out):
    path = os.path.join(out, "beats.txt")
    if not os.path.exists(path):
        report("beats")
        run([os.path.join(VENV_BIN, "beat_this"), src, "-o", path])
    beats = []
    for line in open(path):
        parts = line.split()
        if len(parts) >= 2:
            beats.append((float(parts[0]), int(float(parts[1]))))
    return beats


def transcribe(out):
    path = os.path.join(out, "drums.mid")
    if not os.path.exists(path):
        report("transcribe")
        run([os.path.join(VENV_BIN, "adtof"), "--audio", os.path.join(out, "drums.wav"), "--out", path, "--device", "cpu"])
    t, notes = 0.0, []
    for msg in mido.MidiFile(path):
        t += msg.time
        if msg.type == "note_on" and msg.velocity > 0 and msg.note in ADTOF_KEYS:
            notes.append((t, ADTOF_KEYS[msg.note], msg.velocity))
    return notes


def build_chart(title, beats, raw):
    """song.json: chart time 0 is the first downbeat; the audio starts at -offset."""
    downs = [i for i, (_, n) in enumerate(beats) if n == 1]
    first = downs[0] if downs else 0
    grid = [t for t, _ in beats[first:]]
    t0 = grid[0]
    from .songs import smooth_grid
    grid = smooth_grid([g - t0 for g in grid])        # beat_this's 20 ms frames out of the grid
    t0 += grid[0]
    grid = [g - grid[0] for g in grid]
    ibis = sorted(b - a for a, b in zip(grid, grid[1:]))
    bpm = 60 / ibis[len(ibis) // 2]

    def snap(t):
        # the nearest sixteenth or triplet eighth of the local beat, if close enough
        import bisect
        k = max(0, min(len(grid) - 2, bisect.bisect_right(grid, t) - 1))
        a, b = grid[k], grid[k + 1]
        best = t
        for div in (4, 3):
            step = (b - a) / div
            g = a + round((t - a) / step) * step
            if abs(g - t) * 1000 <= SNAP_MS and abs(g - t) < abs(best - t) + 1e-9:
                best = g
        return best

    notes, last = [], {}
    for t, key, vel in sorted(raw):
        t = t - t0
        if t < -0.05:
            continue                                    # a pickup before the first downbeat
        t = max(0.0, snap(t))
        if key in last and (t - last[key]) * 1000 < DEDUPE_MS:
            continue
        last[key] = t
        notes.append([round(t, 4), key, int(vel)])
    return {"title": title, "bpm": round(bpm, 2), "offset": round(t0, 4), "beats": [round(g, 4) for g in grid],
            "notes": notes, "made": time.strftime("%Y-%m-%d %H:%M"), "grid": "smooth"}


def bar_starts(song):
    """bathero's bars of a song.json in audio seconds: every fourth beat from the first downbeat."""
    return [song["offset"] + b for b in song["beats"][::4]]


def per_bar(segments, bars):
    """[(start, end, label)] in audio seconds -> the label covering most of each bar (None for
    All-In-One's start / end, the silence around the music)."""
    out = []
    for i, b0 in enumerate(bars):
        b1 = bars[i + 1] if i + 1 < len(bars) else b0 + (bars[-1] - bars[-2] if len(bars) > 1 else 2.0)
        best = max(segments, key=lambda s: max(0.0, min(b1, s[1]) - max(b0, s[0])))
        out.append(None if best[2] in ("start", "end") else best[2])
    for i in range(len(out)):                       # the silence takes its neighbour's section
        if out[i] is None:
            out[i] = next((x for x in out[i::-1] if x), None) or next((x for x in out[i:] if x), "intro")
    return out


def vocal_levels(path, bars):
    import numpy as np
    import soundfile as sf
    x, sr = sf.read(path, always_2d=True)
    x = x.mean(axis=1)
    out = []
    for i, b0 in enumerate(bars):
        b1 = bars[i + 1] if i + 1 < len(bars) else b0 + 2.0
        seg = x[int(b0 * sr):int(b1 * sr)]
        out.append(round(float(20 * np.log10(np.sqrt((seg ** 2).mean()) + 1e-9)) if len(seg) else -120.0, 1))
    return out


def find_sections(src, out):
    """sections.json: [[first bar, label], ...] on bathero's bars (0 = the first downbeat)."""
    path = os.path.join(out, "sections.json")
    if os.path.exists(path):
        return
    report("sections")
    song = json.load(open(os.path.join(out, "song.json")))
    bars = bar_starts(song)
    tmp = os.path.join(out, "_allin1")
    try:
        # the four stems All-In-One was trained on (htdemucs), by the demucs that runs on mps: the
        # package's own demucs-infer fails there ("mps:0 and cpu") and still exits 0
        run([os.path.join(VENV_BIN, "python"), "-m", "demucs", "-n", "htdemucs", "-d", "mps",
             "-o", os.path.join(tmp, "sep"), "--filename", "{stem}.{ext}", src])
        stems = os.path.join(tmp, "sep", "htdemucs")
        run([os.path.join(VENV_BIN, "all-in-one-infer"), "-d", "mps", "--no-multiprocess", "--no-demucs",
             "--stems-from-dir", stems, "--stems-id", "song",
             "-o", os.path.join(tmp, "struct"), "--spec-dir", os.path.join(tmp, "spec")])
        result = os.path.join(tmp, "struct", "song.json")
        if not os.path.exists(result):
            raise subprocess.CalledProcessError(0, "all-in-one-infer", stderr=b"no analysis written")
        res = json.load(open(result))
        segments = [(s["start"], s["end"], s["label"]) for s in res["segments"]]
        labels = per_bar(segments, bars)
        vocals = vocal_levels(os.path.join(stems, "vocals.wav"), bars)
    finally:
        shutil.rmtree(tmp, ignore_errors=True)
    sung = [i for i, db in enumerate(vocals) if db > VOCAL_SILENT_DB]
    if sung:
        for i in range(sung[0]):
            labels[i] = "intro"
        for i in range(sung[-1] + 1, len(labels)):
            labels[i] = "outro"
    sections = [[i, lab] for i, lab in enumerate(labels) if i == 0 or labels[i - 1] != lab]
    with open(path, "w") as f:
        json.dump({"sections": sections, "model": "all-in-one-infer harmonix-all",
                   "segments": [[round(a, 3), round(b, 3), lab] for a, b, lab in segments],
                   "vocal_db": vocals, "made": time.strftime("%Y-%m-%d %H:%M")}, f)
    print("[ingest] sections: " + "  ".join(f"{lab} {i + 1}" for i, lab in sections), flush=True)


def todo(src):
    """The steps a file in the inbox still needs (none: the song is complete)."""
    out = os.path.join(SONGS_DIR, slug(os.path.splitext(os.path.basename(src))[0]))
    have = lambda f: os.path.exists(os.path.join(out, f))
    if have("song.json"):
        return [] if have("sections.json") else ["sections"]
    steps = [] if have("drums.wav") and have("backing.wav") else ["separate"]
    steps += [] if have("beats.txt") else ["beats"]
    steps += [] if have("drums.mid") else ["transcribe"]
    return steps + ["chart", "sections"]


def process(src):
    title = os.path.splitext(os.path.basename(src))[0]
    out = os.path.join(SONGS_DIR, slug(title))
    steps = todo(src)
    if not steps:
        return out
    free = shutil.disk_usage(SONGS_DIR).free // 2 ** 20
    if free < MIN_FREE_MB:
        raise RuntimeError(f"only {free} MB free on the disk (needs {MIN_FREE_MB})")
    if STATUS is not None:
        STATUS.begin(title, slug(title), steps)
    if os.path.exists(os.path.join(out, "song.json")):
        print(f"[ingest] {title}: sections", flush=True)
        find_sections(src, out)
        if STATUS is not None:
            STATUS.done(title)
        return out
    os.makedirs(out, exist_ok=True)
    print(f"[ingest] {title}", flush=True)
    t = time.time()
    separate(src, out)
    beats = track_beats(src, out)
    notes = transcribe(out)
    report("chart")
    song = build_chart(title, beats, notes)
    song["source"] = os.path.basename(src)
    with open(os.path.join(out, "song.json"), "w") as f:
        json.dump(song, f, ensure_ascii=False)
    find_sections(src, out)
    if STATUS is not None:
        STATUS.done(title)
    counts = {}
    for _, k, _ in song["notes"]:
        counts[k] = counts.get(k, 0) + 1
    print(f"[ingest] done in {time.time() - t:.0f} s: {song['bpm']} bpm, {len(song['beats'])} beats, {counts}", flush=True)
    return out


def stable(path, wait=2.0):
    """A file still being copied keeps growing; wait until its size holds."""
    a = os.path.getsize(path)
    time.sleep(wait)
    return os.path.exists(path) and os.path.getsize(path) == a


FAILED = {}           # path -> mtime of a file that failed in this run: not retried until it changes


def scan():
    files = [os.path.join(INBOX, n) for n in sorted(os.listdir(INBOX))
             if n.lower().endswith(AUDIO_EXT) and not n.startswith(".")]
    files = [p for p in files if todo(p) and FAILED.get(p) != os.path.getmtime(p)]
    if STATUS is not None:
        STATUS.write(queue=[os.path.splitext(os.path.basename(p))[0] for p in files])
    for p in files:
        name = os.path.basename(p)
        if not stable(p, 0.5):
            continue
        try:
            process(p)
        except Exception as e:
            why = e.stderr.decode(errors="replace") if isinstance(e, subprocess.CalledProcessError) and e.stderr else repr(e)
            print(f"[ingest] FAILED {name}: {why[-800:]}", flush=True)
            FAILED[p] = os.path.getmtime(p)
            if STATUS is not None:
                STATUS.fail(os.path.splitext(name)[0], why.strip().splitlines()[-1] if why.strip() else "failed")


def watch():
    from watchdog.events import FileSystemEventHandler
    from watchdog.observers import Observer
    pending = set()

    class H(FileSystemEventHandler):
        def on_any_event(self, ev):
            if not ev.is_directory:
                pending.add(1)

    obs = Observer()
    obs.schedule(H(), INBOX)
    obs.start()
    print(f"[ingest] watching {INBOX}", flush=True)
    scan()
    try:
        while True:
            time.sleep(1)
            if pending:
                pending.clear()
                scan()
    finally:
        obs.stop()


def main(argv=None):
    ap = argparse.ArgumentParser(prog="bathero.ingest")
    ap.add_argument("files", nargs="*")
    ap.add_argument("--watch", action="store_true")
    a = ap.parse_args(argv)
    os.makedirs(INBOX, exist_ok=True)
    os.makedirs(SONGS_DIR, exist_ok=True)
    global STATUS
    STATUS = Status(watching=a.watch)
    signal.signal(signal.SIGTERM, lambda *_: sys.exit(0))      # finally blocks run, the step's process dies
    try:
        if a.files:
            for f in a.files:
                process(os.path.abspath(f))
        elif a.watch:
            watch()
        else:
            scan()
    finally:
        STATUS.close()
        print("[ingest] stopped", flush=True)


if __name__ == "__main__":
    main()
