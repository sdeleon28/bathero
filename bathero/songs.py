"""Imported songs as levels (the game side; bathero.ingest makes them).

A song folder holds song.json (the chart on the song's own beat grid), backing.wav (the song
without its drums) and drums.wav (the drum stem). The audio is decoded, and stretched to the
tempo being played, by ffmpeg (atempo: pitch kept), straight into memory: nothing is written
(until 2026-11-08 every rate tried was kept as raw audio in the song's cache/, 823 MB for one song,
and the disk filled up mid-stretch).
"""
import json
import os
import subprocess

import numpy as np

from .chart import Chart, ChartNote, Course

INBOX = os.path.expanduser("~/Music/bathero/inbox")
SONGS_DIR = os.path.expanduser("~/Music/bathero/songs")
SONG_COURSE = "course:songs"
# what the importer is doing (bathero.ingest writes it, bathero.importer reads it for the songs screen)
STATUS_PATH = os.path.expanduser("~/.config/bathero/ingest.json")
SR = 44100
SLOWER_BPM = 126          # the Twister's tempo knob goes from the song's tempo down to this much slower
MIN_RATE = 0.3            # ... but never under the game's slowest rate


FLAGS = ("flam", "strict", "accent", "tap", "cresc")


def note_from(x):
    """A song.json row [t, key, velocity(, art)] as a ChartNote; art may carry flags after a "+":
    "flam", "strict" (the zone is judged), "accent", "tap" ("rim+flam", "mid edge+strict+accent", "flam")."""
    parts = x[3].split("+") if len(x) > 3 and x[3] else []
    art = "+".join(p for p in parts if p not in FLAGS) or None
    return ChartNote(t=x[0], key=x[1], velocity=x[2], art=art, flam="flam" in parts, strict="strict" in parts,
                     accent="accent" in parts, tap="tap" in parts, cresc="cresc" in parts)


def note_row(n, rate=1.0):
    art = "+".join(p for p in (n.art, "flam" if n.flam else None, "strict" if n.strict and n.art else None,
                               "accent" if n.accent else None, "tap" if n.tap and not n.accent else None,
                               "cresc" if n.cresc else None) if p)
    return [round(n.t * rate, 4), n.key, int(n.velocity)] + ([art] if art else [])


def difficulty(notes):
    """0..10 from the chart in absolute time (the beat tracker halves some tempos, so nothing here
    counts in beats): half the mean and half the busiest 4 s in notes per second, the share of kicks
    under 200 ms apart and of snare / tom strokes under 160 ms apart (x8 each), toms a minute / 10;
    halved. Measured 2026-11-08: I Won't Back Down 3.2, The Middle 6.5, When I Come Around 6.6,
    Basket Case 7.4, Fat Lip 7.7, Sugar, We're Goin Down 8.0, Misery Business 9.0."""
    t = sorted(n.t for n in notes)
    if len(t) < 2:
        return 0.0
    dur = max(1.0, t[-1] - t[0])
    import bisect
    windows = sorted(bisect.bisect_left(t, x + 4.0) - i for i, x in enumerate(t))
    peak = sum(windows[-20:]) / len(windows[-20:]) / 4

    def fast(keys, limit):
        x = sorted(n.t for n in notes if n.key in keys)
        gaps = [b - a for a, b in zip(x, x[1:])]
        return sum(g < limit for g in gaps) / len(gaps) if gaps else 0.0

    toms = sum(1 for n in notes if n.key in ("tom1", "floor")) / dur * 60
    score = 0.5 * len(t) / dur + 0.5 * peak + 8 * fast({"kick"}, 0.20) + 8 * fast({"snare", "tom1", "floor"}, 0.16) + toms / 10
    return round(min(10.0, score / 2), 1)


GRID_SMOOTH_BEATS = 6     # each beat refitted on a straight line through the 13 around it


def smooth_grid(beats, w=GRID_SMOOTH_BEATS):
    """beat_this's beat times sit on its 50 fps frames (20 ms steps: gaps of 0.36, 0.38, 0.40 s at
    158 bpm), so a bar's sixteenths came out uneven by up to 20 ms. Each beat is refitted on a
    least-squares line through its neighbours: even inside the bar, still following the band's
    tempo across the song (a single constant tempo misses Come Out and Play by up to 225 ms)."""
    b = np.asarray(beats, float)
    if len(b) < 3:
        return list(b)
    i = np.arange(len(b))
    out = b.copy()
    for k in range(len(b)):
        lo, hi = max(0, k - w), min(len(b), k + w + 1)
        out[k] = np.polyval(np.polyfit(i[lo:hi], b[lo:hi], 1), k)
    return [float(x) for x in out]


def regrid(song):
    """song.json onto the smoothed grid, once: every note keeps its place in the beat (beat index
    and fraction on the old grid) and is re-timed on the new one. True when it changed."""
    if song.get("grid") == "smooth" or not song.get("beats") or len(song["beats"]) < 3:
        return False
    old = np.asarray(song["beats"], float)
    new = np.asarray(smooth_grid(old), float)
    idx = np.arange(len(old))
    shift = new[0]                                       # chart time 0 stays the first downbeat
    new = new - shift
    for n in song["notes"]:
        pos = np.interp(n[0], old, idx, left=None, right=None)
        if n[0] < old[0]:
            pos = (n[0] - old[0]) / (old[1] - old[0])
        elif n[0] > old[-1]:
            pos = len(old) - 1 + (n[0] - old[-1]) / (old[-1] - old[-2])
        t = (new[0] + pos * (new[1] - new[0]) if pos < 0 else
             new[-1] + (pos - len(new) + 1) * (new[-1] - new[-2]) if pos > len(new) - 1 else
             float(np.interp(pos, idx, new)))
        n[0] = round(max(0.0, t), 4)
    song["notes"].sort(key=lambda n: n[0])
    song["beats"] = [round(float(x), 4) for x in new]
    song["offset"] = round(song["offset"] + shift, 4)
    song["grid"] = "smooth"
    return True


def load():
    """Every song folder with a song.json, as Charts (backing "song", the folder in Chart.song),
    easiest first (difficulty)."""
    out = []
    if not os.path.isdir(SONGS_DIR):
        return out
    for d in sorted(os.listdir(SONGS_DIR)):
        p = os.path.join(SONGS_DIR, d, "song.json")
        if not os.path.exists(p):
            continue
        try:
            s = json.load(open(p))
        except (OSError, ValueError):
            continue
        if regrid(s):
            import shutil
            bak = os.path.join(SONGS_DIR, d, "song.before-grid.json")
            if not os.path.exists(bak):
                shutil.copyfile(p, bak)
            with open(p + ".part", "w") as f:
                json.dump(s, f)
            os.replace(p + ".part", p)
        notes = [note_from(x) for x in s["notes"]]
        if not notes:
            continue
        sections = None
        try:
            sections = [tuple(x) for x in json.load(open(os.path.join(SONGS_DIR, d, "sections.json")))["sections"]]
        except (OSError, ValueError, KeyError):
            pass
        diff = difficulty(notes)
        out.append((diff, Chart(name=f"♪ {s['title']}", notes=notes, bpm=s["bpm"], beats=s["beats"], backing="song",
                                song=os.path.join(SONGS_DIR, d), song_offset=s["offset"], sections=sections,
                                segments=[(0, s["subdivision"])] if s.get("subdivision") else None, expression=any(n.art or n.flam for n in notes),
                                desc=f"difficulty {diff:.1f} · imported {s.get('made', '')}"
                                     + (f" · edited {s['edited']}" if s.get("edited") else ""))))
    out.sort(key=lambda x: (x[0], x[1].name.lower()))
    return [ch for _, ch in out]


def course():
    return Course(SONG_COURSE, "Songs", f"songs from {INBOX.replace(os.path.expanduser('~'), '~')}, split and transcribed", load())


def min_rate(bpm):
    return max(MIN_RATE, (bpm - SLOWER_BPM) / bpm)


def _ffmpeg():
    from .capture import ffmpeg_path
    return ffmpeg_path()


def _atempo(rate):
    """atempo takes 0.5..2 per stage: chain stages for anything slower."""
    parts = []
    while rate < 0.5:
        parts.append(0.5)
        rate /= 0.5
    parts.append(rate)
    return ",".join(f"atempo={r:.5f}" for r in parts)


ATEMPO_LEAD_S = 0.015


def audio(song_dir, stem, rate=1.0):
    """The stem as int16 stereo at SR, stretched to `rate`, decoded into memory (0.5..1.2 s for a
    3-minute stem). Raises RuntimeError with ffmpeg's message when it fails."""
    rate = round(rate, 3)
    cmd = [_ffmpeg(), "-hide_banner", "-loglevel", "error", "-nostdin", "-i", os.path.join(song_dir, f"{stem}.wav")]
    if rate != 1.0:
        cmd += ["-af", _atempo(rate)]
    cmd += ["-f", "s16le", "-ac", "2", "-ar", str(SR), "-"]
    r = subprocess.run(cmd, capture_output=True)
    if r.returncode != 0:
        msg = r.stderr.decode(errors="replace").strip().splitlines()
        raise RuntimeError(f"{stem}: {msg[-1] if msg else f'ffmpeg exited {r.returncode}'}")
    pcm = np.frombuffer(r.stdout, dtype=np.int16).reshape(-1, 2)
    if rate != 1.0:
        # atempo's output runs ATEMPO_LEAD_S of source time ahead (measured 2026-11-09 by onset
        # correlation against the unstretched stem: 14..17 ms x rate at 0.4..0.9); you play with the
        # music, so a slowed song came out 20..40 ms "early" at the judge. Pad the head to re-align.
        pad = int(round(ATEMPO_LEAD_S / rate * SR))
        pcm = np.concatenate([np.zeros((pad, 2), np.int16), pcm])
    return pcm


def stems(song_dir, rate=1.0):
    """{"backing": pcm, "drums": pcm} at `rate`, both decoded at once."""
    from concurrent.futures import ThreadPoolExecutor
    with ThreadPoolExecutor(2) as ex:
        futures = {st: ex.submit(audio, song_dir, st, rate) for st in ("backing", "drums")}
        return {st: f.result() for st, f in futures.items()}


def clear_disk_cache():
    """Delete the stretched copies older versions kept in every song's cache/ folder."""
    import shutil
    try:
        names = os.listdir(SONGS_DIR)
    except OSError:
        return
    for n in names:
        shutil.rmtree(os.path.join(SONGS_DIR, n, "cache"), ignore_errors=True)
