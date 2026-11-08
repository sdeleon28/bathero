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
SR = 44100
SLOWER_BPM = 126          # the Twister's tempo knob goes from the song's tempo down to this much slower
MIN_RATE = 0.3            # ... but never under the game's slowest rate


def load():
    """Every song folder with a song.json, as Charts (backing "song", the folder in Chart.song)."""
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
        out.append(Chart(name=f"♪ {s['title']}", notes=notes, bpm=s["bpm"], beats=s["beats"], backing="song",
                         song=os.path.join(SONGS_DIR, d), song_offset=s["offset"],
                         desc=f"{s['bpm']:.0f} bpm · {len(notes)} notes · imported {s.get('made', '')}"))
    return out


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
