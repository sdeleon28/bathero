"""What happened in the game while a stream recording was running, on the video's clock.

    .venv/bin/python .claude/skills/twitch-clips/moments.py --file stream.mp4 [--start EPOCH] [--offset S] [--json]
    .venv/bin/python .claude/skills/twitch-clips/moments.py --start EPOCH --duration S
    .venv/bin/python .claude/skills/twitch-clips/moments.py --layout 1920x1080

--file is the recording the user handed over (a Twitch VOD download, a screen recording, an
export). Its duration comes from ffprobe; when the video started (epoch seconds of its first frame)
is --start, or else guessed and printed: a YYYYmmdd-HHMMSS stamp in the file name (local time),
else the AAC copy of a stream in ~/Movies/bathero/streams whose length matches the video's (a
Twitch VOD is the stream, start = the copy's stamp + 6 s), else the container's creation_time
tag, else the file's mtime minus its duration (a download's mtime is when it was downloaded).
--align measures the start instead of guessing: 90 s of the video's sound against the matching
AAC copy, mono 8 kHz, cross-correlated with numpy (the copy starts at the daemon's stamp; on
2026-09-16 the VOD's first frame came 6.02 s after it, correlation 0.89). Video time of anything
= wall - start - offset (offset: a further correction measured on the picture, 0 by default).

Prints, on the video's clock: every level played (from the run logs in ~/Library/Logs/bathero/runs:
name, bpm, grade, stars, max combo, misses, longest streak of PERFECT/GOOD with its time) and every
take the drummer chose to record (~/Movies/bathero/*/take.json), then suggested clips: every level
with 2 stars or more, from 2 s before its first hit to 4 s after its end (the results screen),
best first. --layout prints the camera picture-in-picture rectangle of the video frame (from
settings.json, capture.pip_rect) and the rects of the 1080x1920 social edition (capture.social_layout).
"""
import argparse
import datetime
import glob
import json
import os
import re
import subprocess
import sys
import time

RUNS_DIR = os.path.expanduser("~/Library/Logs/bathero/runs")
TAKES_DIR = os.path.expanduser("~/Movies/bathero")
SETTINGS = os.path.expanduser("~/.config/bathero/settings.json")


def hms(s):
    s = max(0, int(round(s)))
    return f"{s // 3600}:{s // 60 % 60:02d}:{s % 60:02d}"


def runs_between(t0, t1):
    out = []
    for path in sorted(glob.glob(os.path.join(RUNS_DIR, "*.jsonl"))):
        with open(path) as f:
            try:
                head = json.loads(f.readline())
            except ValueError:
                continue
            if head.get("kind") != "run" or head.get("started", 0) > t1 or head.get("ended", head.get("started", 0)) < t0:
                continue          # keep the runs that overlap [t0, t1]
            events = []
            for line in f:
                try:
                    events.append(json.loads(line))
                except ValueError:
                    pass
        hits = [e for e in events if e.get("kind") == "hit"]
        misses = [e for e in events if e.get("kind") == "miss"]
        streak, best, best_end = 0, 0, None
        for e in sorted(hits + misses, key=lambda e: e["wall"]):
            if e["kind"] == "hit" and e.get("judge") in ("PERFECT", "GOOD"):
                streak += 1
                if streak > best:
                    best, best_end = streak, e["wall"]
            else:
                streak = 0
        st = head.get("stats") or {}
        chart = head["chart"]
        first_hit = min((h["wall"] for h in hits), default=head["started"])
        out.append({
            "path": path, "name": chart["name"], "bpm": chart.get("bpm"), "started": head["started"],
            "ended": head.get("ended", first_hit), "first_hit": first_hit,
            "chart_zero": next((h["wall"] - h["chart_t"] for h in sorted(hits, key=lambda h: h["wall"])
                                if h.get("chart_t") is not None), first_hit),
            "grade": st.get("grade"), "stars": st.get("stars"), "max_combo": st.get("max_combo"),
            "accuracy": st.get("accuracy"), "hits": len(hits), "misses": len(misses), "notes": st.get("notes"),
            "best_streak": best, "best_streak_end": best_end,
        })
    return out


def takes_between(t0, t1):
    out = []
    for path in glob.glob(os.path.join(TAKES_DIR, "*", "take.json")) + glob.glob(os.path.join(TAKES_DIR, "* take.json")):
        try:
            with open(path) as f:
                meta = json.load(f)
        except (OSError, ValueError):
            continue
        if meta.get("t0", 0) <= t1 and meta.get("t0", 0) + meta.get("duration", 0) >= t0:
            out.append({"path": os.path.dirname(path) if path.endswith("/take.json") else path,
                        "t0": meta["t0"], "duration": meta.get("duration", 0)})
    return sorted(out, key=lambda t: t["t0"])


def probe(path):
    """(duration s, creation_time epoch or None) of a video file, from ffprobe."""
    r = subprocess.run(["ffprobe", "-v", "error", "-show_entries", "format=duration:format_tags=creation_time",
                        "-of", "json", path], capture_output=True, text=True, check=True)
    fmt = json.loads(r.stdout)["format"]
    created = None
    tag = fmt.get("tags", {}).get("creation_time")
    if tag:
        try:
            created = datetime.datetime.fromisoformat(tag.replace("Z", "+00:00")).timestamp()
        except ValueError:
            pass
    return float(fmt["duration"]), created


STREAMS_DIR = os.path.expanduser("~/Movies/bathero/streams")
VOD_DELAY = 6.0     # s from the daemon's start (the AAC copy's stamp) to a Twitch VOD's first frame, measured 2026-09-16


STREAM_LOG = os.path.expanduser("~/Library/Logs/bathero/stream.log")


def stream_lengths():
    """{AAC copy path: seconds the stream ran}, from the daemon's log ("audio copy X" then
    "stopped after mm:ss"). ffprobe's duration of a raw ADTS file is a guess from the bitrate
    (it said 48 min for a 23 min stream), so the log is the source."""
    out, current = {}, None
    try:
        with open(STREAM_LOG) as f:
            for line in f:
                m = re.search(r"audio copy (.+\.aac)\s*$", line)
                if m:
                    current = m.group(1).strip()
                    continue
                m = re.search(r"stopped after (\d+):(\d\d)(?::(\d\d))?", line)
                if m and current:
                    h, mi, se = (m.group(1), m.group(2), m.group(3)) if m.group(3) else ("0", m.group(1), m.group(2))
                    out[current] = int(h) * 3600 + int(mi) * 60 + int(se)
                    current = None
    except OSError:
        pass
    return out


def stream_copies():
    """[(epoch of the stamp, duration s, path)] of the AAC copies of the streams, newest first."""
    lengths = stream_lengths()
    out = []
    for path in glob.glob(os.path.join(STREAMS_DIR, "*.aac")):
        m = re.search(r"(20\d{6})-(\d{6})", os.path.basename(path))
        if not m:
            continue
        stamp = time.mktime(time.strptime(m.group(1) + m.group(2), "%Y%m%d%H%M%S"))
        dur = lengths.get(path)
        if dur is None:
            r = subprocess.run(["ffprobe", "-v", "error", "-show_entries", "format=duration", "-of", "csv=p=0", path],
                               capture_output=True, text=True)
            try:
                dur = float(r.stdout.strip())
            except ValueError:
                continue
        out.append((stamp, dur, path))
    return sorted(out, reverse=True)


def matching_copy(duration, tolerance=20.0):
    """The AAC copy whose length is the video's (a VOD is the whole stream), or None."""
    near = [c for c in stream_copies() if abs(c[1] - duration) <= tolerance]
    return min(near, key=lambda c: abs(c[1] - duration)) if near else None


def guess_start(path, duration, created):
    """(epoch of the first frame, how it was guessed)."""
    m = re.search(r"(20\d{6})-(\d{6})", os.path.basename(path)) or re.search(r"(20\d{6})-(\d{6})", os.path.basename(os.path.dirname(os.path.abspath(path))))
    if m:
        return time.mktime(time.strptime(m.group(1) + m.group(2), "%Y%m%d%H%M%S")), "the stamp in the file's (or its folder's) name, local time"
    copy = matching_copy(duration)
    if copy:
        return copy[0] + VOD_DELAY, (f"the stream whose AAC copy is as long ({os.path.basename(copy[2])}, {hms(copy[1])}) plus "
                                     f"{VOD_DELAY:.0f} s (a VOD's usual delay; --align measures it, or check it on a frame)")
    if created:
        return created, "the container's creation_time tag"
    return os.path.getmtime(path) - duration, "the file's mtime minus its duration (weak: a download's mtime is the download; check it on a frame)"


def align_start(path, duration):
    """The video's start measured against the matching AAC copy: (epoch, how), or None.

    90 s of each, mono 8 kHz, from the same point (a third of the way in, so both have sound),
    cross-correlated in numpy. video time = aac time - lag, so start = stamp + lag."""
    import numpy as np
    copy = matching_copy(duration)
    if not copy:
        return None
    stamp, cdur, cpath = copy
    at, span = max(0.0, min(duration, cdur) / 3 - 45), 90
    def pcm(src, ss):
        r = subprocess.run(["ffmpeg", "-v", "error", "-ss", f"{ss:.3f}", "-t", str(span), "-i", src, "-vn", "-ac", "1", "-ar", "8000", "-f", "f32le", "-"],
                           capture_output=True)
        return np.frombuffer(r.stdout, np.float32)
    a, b = pcm(path, at), pcm(cpath, at)
    n = min(len(a), len(b))
    if n < 8000 * 10:
        return None
    a, b = a[:n] - a[:n].mean(), b[:n] - b[:n].mean()
    size = 1 << int(np.ceil(np.log2(2 * n)))
    c = np.fft.irfft(np.fft.rfft(a, size) * np.conj(np.fft.rfft(b, size)), size)
    lag = int(np.argmax(c))
    if lag > size // 2:
        lag -= size
    peak = float(c.max() / np.sqrt((a ** 2).sum() * (b ** 2).sum()))
    lag_s = -lag / 8000.0      # video = aac + lag_s ... video runs lag_s later than the copy
    return stamp + lag_s, f"the sound cross-correlated with {os.path.basename(cpath)}: video = copy {lag_s:+.3f} s (peak {peak:.2f}; under 0.5 is no match)"


def layout(size):
    sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))))
    from bathero.capture import pip_rect, social_layout
    with open(SETTINGS) as f:
        st = json.load(f)
    w, h = size
    x, y, pw, ph = pip_rect((w, h), float(st.get("capture_pip", 0.28)), st.get("capture_corner", "br"))
    (gx, gy, gw, gh), (cx, cy, cw, ch) = social_layout((w, h), float(st.get("capture_split", 0.32)))
    print(f"video frame {w}x{h}: camera picture-in-picture at x={x} y={y} {pw}x{ph} (corner {st.get('capture_corner', 'br')})")
    print(f"social 1080x1920: game scaled to {gw}x{gh} at y={gy}; camera {cw}x{ch} at y={cy} (crop the PiP, scale to fill)")
    print(f"ffmpeg: [0:v]scale={gw}:{gh}:flags=lanczos,pad=1080:1920:0:{gy}:black[g];"
          f"[0:v]crop={pw}:{ph}:{x}:{y},scale={cw}:{ch}:force_original_aspect_ratio=increase:flags=lanczos,crop={cw}:{ch}[cam];"
          f"[g][cam]overlay=0:{cy}[v]")


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--file", help="the recording (mp4/mkv/mov): duration from ffprobe, start guessed unless --start")
    ap.add_argument("--start", type=float, help="epoch seconds of the video's first frame")
    ap.add_argument("--align", action="store_true", help="measure the start against the stream's AAC copy (sound cross-correlation)")
    ap.add_argument("--duration", type=float, help="video length in seconds (without --file)")
    ap.add_argument("--offset", type=float, default=0.0, help="seconds to subtract from wall - start (measured alignment)")
    ap.add_argument("--layout", help="print the crop rects for a VOD frame of this size, e.g. 1920x1080")
    ap.add_argument("--json", action="store_true")
    a = ap.parse_args()
    if a.layout:
        layout(tuple(int(v) for v in a.layout.lower().split("x")))
        return
    if a.file:
        duration, created = probe(a.file)
        if a.start:
            start, how = a.start, "--start"
        else:
            start, how = guess_start(a.file, duration, created)
            if a.align:
                measured = align_start(a.file, duration)
                if measured:
                    start, how = measured
                else:
                    how += " (--align: no AAC copy of a stream as long as this video)"
        print(f"{os.path.basename(a.file)}: {hms(duration)}, first frame at "
              f"{time.strftime('%Y-%m-%d %H:%M:%S', time.localtime(start))} local, from {how}")
        if a.json:
            print(f"start {start:.3f}")
    elif a.start and a.duration:
        start, duration = a.start, a.duration
    else:
        ap.error("--file, or --start and --duration")
    t = lambda wall: wall - start - a.offset
    runs = runs_between(start, start + duration)
    takes = takes_between(start, start + duration)
    for r in runs:
        r.update(vod_start=t(r["first_hit"]), vod_end=t(r["ended"]), vod_chart_zero=t(r["chart_zero"]),
                 vod_best_streak_end=t(r["best_streak_end"]) if r["best_streak_end"] else None)
    for k in takes:
        k.update(vod_start=t(k["t0"]), vod_end=t(k["t0"] + k["duration"]))
    clips = sorted((r for r in runs if (r["stars"] or 0) >= 2), key=lambda r: (-(r["stars"] or 0), -(r["grade"] or 0)))
    if a.json:
        json.dump({"start": start, "duration": duration, "offset": a.offset, "runs": runs, "takes": takes,
                   "clips": [{"name": c["name"], "in": max(0, c["vod_start"] - 2), "out": min(duration, c["vod_end"] + 4),
                              "stars": c["stars"], "grade": c["grade"]} for c in clips]}, sys.stdout, indent=1)
        return
    print(f"\n{len(runs)} levels played during the video (video time in..out, name, bpm, grade, stars, combo, misses, best streak):")
    for r in runs:
        streak = f"{r['best_streak']} ending {hms(r['vod_best_streak_end'])}" if r["best_streak_end"] else "-"
        print(f"  {hms(r['vod_start'])}..{hms(r['vod_end'])}  {r['name']}  {r['bpm']:.0f} bpm  grade {r['grade'] or 0:.0f}  "
              f"{r['stars'] or 0}*  combo {r['max_combo'] or 0}/{r['notes'] or 0}  misses {r['misses']}  streak {streak}")
    print(f"\n{len(takes)} takes recorded during the video:")
    for k in takes:
        print(f"  {hms(k['vod_start'])}..{hms(k['vod_end'])}  {os.path.basename(k['path'])}")
    print("\nsuggested clips (2 stars or more, best first; 2 s of run-up, 4 s of results):")
    for c in clips:
        print(f"  {hms(max(0, c['vod_start'] - 2))}..{hms(min(duration, c['vod_end'] + 4))}  {c['name']}  {c['stars']}*  grade {c['grade']:.0f}")
    if not clips:
        print("  none: pick from the levels and takes above, or from the instructions")


if __name__ == "__main__":
    main()
