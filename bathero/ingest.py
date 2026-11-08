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
"""
import argparse
import json
import os
import re
import subprocess
import sys
import time

import mido

from .songs import INBOX, SONGS_DIR

VENV_BIN = os.path.dirname(sys.executable)
AUDIO_EXT = (".wav", ".aif", ".aiff", ".flac", ".mp3", ".m4a")

# ADTOF's classes -> bathero instruments
ADTOF_KEYS = {35: "kick", 36: "kick", 38: "snare", 40: "snare", 47: "tom1", 42: "hihat", 46: "hihat", 49: "crash", 51: "crash"}
SNAP_MS = 35          # a note this close to a sixteenth (or a triplet eighth) of the song's grid lands on it
DEDUPE_MS = 40        # one instrument heard twice this close is one stroke


def slug(name):
    s = re.sub(r"[^\w\s-]", "", name, flags=re.UNICODE).strip().lower()
    return re.sub(r"[\s_-]+", "-", s)[:80] or "song"


def run(cmd):
    print("  $", " ".join(os.path.basename(c) if i == 0 else c for i, c in enumerate(cmd)), flush=True)
    subprocess.run(cmd, check=True, stdout=subprocess.DEVNULL, stderr=subprocess.PIPE)


def separate(src, out):
    if os.path.exists(os.path.join(out, "drums.wav")) and os.path.exists(os.path.join(out, "backing.wav")):
        return
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
    grid = [g - t0 for g in grid]
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
            "notes": notes, "made": time.strftime("%Y-%m-%d %H:%M")}


def process(src):
    title = os.path.splitext(os.path.basename(src))[0]
    out = os.path.join(SONGS_DIR, slug(title))
    if os.path.exists(os.path.join(out, "song.json")):
        return out
    os.makedirs(out, exist_ok=True)
    print(f"[ingest] {title}", flush=True)
    t = time.time()
    separate(src, out)
    beats = track_beats(src, out)
    notes = transcribe(out)
    song = build_chart(title, beats, notes)
    song["source"] = os.path.basename(src)
    with open(os.path.join(out, "song.json"), "w") as f:
        json.dump(song, f, ensure_ascii=False)
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


def scan():
    for name in sorted(os.listdir(INBOX)):
        p = os.path.join(INBOX, name)
        if name.lower().endswith(AUDIO_EXT) and not name.startswith(".") and stable(p, 0.5):
            try:
                process(p)
            except subprocess.CalledProcessError as e:
                print(f"[ingest] FAILED {name}: {e.stderr.decode(errors='replace')[-800:]}", flush=True)


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
    if a.files:
        for f in a.files:
            process(os.path.abspath(f))
    elif a.watch:
        watch()
    else:
        scan()


if __name__ == "__main__":
    main()
