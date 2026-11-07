"""Offset fit: which latency offset would have scored a run best, and which one scores the
recent runs best together (asked for 2026-09-20: the user kept moving `offset_ms` between 20
and 85 without finding it, because one run's optimum is that run's own timing, not the chain).

Every stroke is re-judged against the chart at the run's offset plus a shift, with the game's
windows (PERFECT / GOOD / OK, nearest pending note within OK_MS), and the grade is the timing
grade (grade_for with no dynamics). `fit_game` gives the results box its line: this run's best
offset and what it would have gained, and the offset that maximises the mean grade over the
last RECENT runs of the profile (pooled), which is the number to set. Run logs carry the fit as
stats["offset_fit"]. `python -m bathero.latency [--runs N] [--profile ID]` prints the sweep
over the recent logs.
"""
import bisect
import glob
import json
import os
import statistics
import sys
from collections import Counter

from .game import GOOD_MS, OK_MS, PERFECT_MS, grade_for
from .profiles import runs_match
from .runlog import RUNS_DIR

SHIFTS = range(-60, 81)      # ms around the run's own offset
RECENT = 15                  # runs pooled for the number to set
MIN_NOTES = 30               # a shorter run says little
_curves = {}                 # run log path -> (mtime, offset, curve)


def grade_at(strokes, notes, shift_ms):
    """Timing grade of the run with shift_ms more offset. strokes: [(judged t, key)] in time
    order; notes: {key: sorted chart times}."""
    d = shift_ms / 1000
    claimed = set()
    c = Counter()
    for t, key in strokes:
        tj = t - d
        ts = notes.get(key)
        best = None
        if ts:
            i = bisect.bisect_left(ts, tj)
            for j in range(max(0, i - 2), min(len(ts), i + 2)):
                err = abs(tj - ts[j])
                if err * 1000 <= OK_MS and (key, j) not in claimed and (best is None or err < best[0]):
                    best = (err, j)
        if best is None:
            c["STRAY"] += 1
            continue
        claimed.add((key, best[1]))
        a = best[0] * 1000
        c["PERFECT" if a <= PERFECT_MS else "GOOD" if a <= GOOD_MS else "OK"] += 1
    total = sum(len(v) for v in notes.values())
    if not total:
        return 0.0
    hit = c["PERFECT"] + c["GOOD"] + c["OK"]
    return grade_for({"accuracy": hit / total, "quality": (c["PERFECT"] + 0.6 * c["GOOD"] + 0.3 * c["OK"]) / total,
                      "stray_rate": c["STRAY"] / total, "dyn_rate": None})


def curve(strokes, notes, offset_ms):
    """{total offset (ms): timing grade} over SHIFTS around offset_ms."""
    return {int(round(offset_ms + s)): grade_at(strokes, notes, s) for s in SHIFTS}


def best_of(cv):
    """(offset, grade) with the highest grade; among equals, the one nearest the middle of the
    plateau (so a flat top does not pull to one edge)."""
    top = max(cv.values())
    offs = sorted(o for o, g in cv.items() if g >= top - 1e-9)
    return offs[len(offs) // 2], top


def pooled(curves):
    """The offset that maximises the mean grade over the curves, on the offsets they all cover."""
    common = set.intersection(*(set(c) for c in curves)) if curves else set()
    if not common:
        return None
    mean = {o: statistics.fmean(c[o] for c in curves) for o in common}
    return best_of(mean)[0]


def fit(strokes, notes, offset_ms, others=()):
    """The results line: this run's own curve and the pooled number.
    offset: the run's offset; best / gain: this run's best offset and the grade it would have
    added; pooled: the best offset over this run and `others` (curves); n: runs pooled."""
    cv = curve(strokes, notes, offset_ms)
    best, top = best_of(cv)
    out = {"offset": offset_ms, "best": best, "gain": top - cv[int(round(offset_ms))], "curve": cv}
    allc = [cv] + list(others)
    out["pooled"] = pooled(allc)
    out["n"] = len(allc)
    return out


def from_game(game):
    """(strokes, notes) of a Game that just finished, skipped notes (transport jumps) left out."""
    notes = {}
    for n in game.notes:
        if n.state != "skip":
            notes.setdefault(n.key, []).append(n.t)
    return list(game.strokes), {k: sorted(v) for k, v in notes.items()}


def from_runlog(path):
    """(strokes, notes, offset) of a run log, or None if it is not a judged run."""
    with open(path) as f:
        head = json.loads(f.readline())
        st = head.get("stats")
        if head.get("kind") != "run" or not st or len(head["chart"]["notes"]) < MIN_NOTES or st["hit"] < 0.5 * st["notes"]:
            return None                                   # an abandoned run says nothing about the offset
        strokes = []
        for line in f:
            if '"kind": "hit"' not in line:
                continue
            r = json.loads(line)
            if r.get("kind") == "hit":
                strokes.append((r["song_t"], r["key"]))
    notes = {}
    for n in head["chart"]["notes"]:
        notes.setdefault(n["key"], []).append(n["t"])
    return sorted(strokes), {k: sorted(v) for k, v in notes.items()}, float(head.get("offset_ms") or 0.0)


def recent_curves(profile=None, n=RECENT, runs_dir=None):
    """Curves of the last n judged run logs of the profile, newest first, cached by mtime."""
    paths = sorted(glob.glob(os.path.join(runs_dir or RUNS_DIR, "*.jsonl")), reverse=True)
    out = []
    for path in paths:
        if len(out) >= n:
            break
        try:
            m = os.path.getmtime(path)
            if path not in _curves or _curves[path][0] != m:
                with open(path) as f:
                    head = json.loads(f.readline())
                if not runs_match(profile, head):
                    continue
                r = from_runlog(path)
                _curves[path] = (m, None if r is None else curve(r[0], r[1], r[2]))
            cv = _curves[path][1]
            if cv is not None:
                out.append((path, cv))
        except (OSError, ValueError, KeyError):
            continue
    return out


def fit_game(game, offset_ms, profile=None):
    """The results box's fit for a Game that just finished, pooled with the profile's recent logs
    (this run's own log is not written yet, so it is not among them)."""
    strokes, notes = from_game(game)
    if len(strokes) < 5 or sum(len(v) for v in notes.values()) < MIN_NOTES:
        return None
    return fit(strokes, notes, offset_ms, [cv for _, cv in recent_curves(profile, RECENT - 1)])


def main(argv=None):
    import argparse
    ap = argparse.ArgumentParser(description="which offset would have scored the recent runs best")
    ap.add_argument("--runs", type=int, default=RECENT)
    ap.add_argument("--profile", default=None, help="profile id (default: every run)")
    args = ap.parse_args(argv)
    rows = recent_curves(args.profile, args.runs)
    if not rows:
        print("no judged runs")
        return
    print(f"{'run':40} {'offset':>6} {'best':>5} {'gain':>5}")
    curves = []
    for path, cv in reversed(rows):
        r = from_runlog(path)
        best, top = best_of(cv)
        print(f"{os.path.basename(path)[:40]:40} {r[2]:6.0f} {best:5.0f} {top - cv[int(round(r[2]))]:+5.1f}")
        curves.append(cv)
    common = sorted(set.intersection(*(set(c) for c in curves)))
    print(f"\npooled over {len(curves)} runs (offsets they all cover, {common[0]}..{common[-1]} ms):")
    for o in common[::5]:
        print(f"  {o:4} ms  mean grade {statistics.fmean(c[o] for c in curves):5.1f}")
    print("best:", pooled(curves), "ms")


if __name__ == "__main__":
    main()
