"""Imported songs as levels (the game side; bathero.ingest makes them).

A song folder holds song.json (the chart on the song's own beat grid), backing.wav (the song
without its drums) and drums.wav (the drum stem). The audio is decoded, and stretched to the
tempo being played, by ffmpeg (atempo: pitch kept), cached per rate next to the stems.
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
        notes = [ChartNote(t=t, key=k, velocity=v) for t, k, v in s["notes"]]
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


def audio(song_dir, stem, rate=1.0):
    """The stem as int16 stereo at SR, stretched to `rate` (cached as a raw file)."""
    rate = round(rate, 3)
    cache = os.path.join(song_dir, "cache", f"{stem}@{rate:.3f}.s16")
    if not os.path.exists(cache):
        os.makedirs(os.path.dirname(cache), exist_ok=True)
        cmd = [_ffmpeg(), "-hide_banner", "-loglevel", "error", "-nostdin", "-y", "-i", os.path.join(song_dir, f"{stem}.wav")]
        if rate != 1.0:
            cmd += ["-af", _atempo(rate)]
        cmd += ["-f", "s16le", "-ac", "2", "-ar", str(SR), cache + ".part"]
        subprocess.run(cmd, check=True)
        os.replace(cache + ".part", cache)
    return np.fromfile(cache, dtype=np.int16).reshape(-1, 2)
