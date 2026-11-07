"""Audit a run log: what happened, and what the judging would have said with other
thresholds. Usage:

    python -m bathero.audit                      # the latest run
    python -m bathero.audit PATH.jsonl           # a given run
    python -m bathero.audit --hihat-tap 96 --hihat-accent 112 --tap 84 --accent 88   # re-judge dynamics
    python -m bathero.audit --hits               # list every hit
"""
import argparse
import glob
import os
import statistics as st
from collections import Counter, defaultdict

from .runlog import RUNS_DIR, load


def latest():
    files = sorted(glob.glob(os.path.join(RUNS_DIR, "*.jsonl")))
    return files[-1] if files else None


def dist(vs):
    if not vs:
        return "none"
    vs = sorted(vs)
    q = lambda f: vs[min(len(vs) - 1, int(len(vs) * f))]
    return f"n={len(vs)} min {vs[0]} p25 {q(.25)} median {q(.5)} p75 {q(.75)} max {vs[-1]}"


def rejudge(accent, velocity, accent_min, tap_max):
    if accent:
        return "ACCENT" if velocity >= accent_min else "SOFT" if velocity <= tap_max else "-"
    return "TAP" if velocity <= tap_max else "LOUD" if velocity >= accent_min else "-"


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("path", nargs="?")
    ap.add_argument("--hits", action="store_true", help="list every hit")
    ap.add_argument("--tap", type=int, help="re-judge: tap max for drums")
    ap.add_argument("--accent", type=int, help="re-judge: accent min for drums")
    ap.add_argument("--hihat-tap", type=int, help="re-judge: tap max for the hi-hat")
    ap.add_argument("--hihat-accent", type=int, help="re-judge: accent min for the hi-hat")
    args = ap.parse_args()
    path = args.path or latest()
    if not path:
        raise SystemExit(f"no runs in {RUNS_DIR}")
    header, events = load(path)
    ch = header["chart"]
    print(f"{path}\n{ch['name']}  {ch['bpm']:.0f} bpm  rate {ch['rate']}  offset {header.get('offset_ms')} ms  "
          f"{len(ch['notes'])} notes  dynamics {ch['dynamics']}")
    if "stats" in header:
        s = header["stats"]
        print(f"result: {s['hit']}/{s['notes']} ({s['accuracy'] * 100:.1f}%)  mean {s['mean_ms']:+.1f} ms  std {s['std_ms']:.1f} ms")
    kinds = Counter(e["kind"] for e in events)
    print("events:", dict(kinds))

    hits = [e for e in events if e["kind"] == "hit"]
    ghosts = [e for e in events if e["kind"] == "ghost"]
    if ghosts:
        print("ghosts:", dict(Counter(g["why"] for g in ghosts)))
    by_judge = Counter(h["judge"] for h in hits)
    print("judges:", dict(by_judge))
    errs = [h["error_ms"] for h in hits if h.get("error_ms") is not None]
    if errs:
        print(f"timing: mean {st.fmean(errs):+.1f} ms  std {st.pstdev(errs):.1f}  early {sum(e < 0 for e in errs)}  late {sum(e > 0 for e in errs)}")
    per_key = defaultdict(list)
    for h in hits:
        if h.get("error_ms") is not None:
            per_key[h["key"]].append(h["error_ms"])
    for k, v in per_key.items():
        print(f"  {k:8} mean {st.fmean(v):+.1f} ms  n={len(v)}")

    if ch["dynamics"]:
        print("\ndynamics as judged:", dict(Counter(h.get("dyn") or "-" for h in hits if h["judge"] != "STRAY")))
        for k in sorted({h["key"] for h in hits}):
            acc = [h["velocity"] for h in hits if h["key"] == k and h.get("accent")]
            taps = [h["velocity"] for h in hits if h["key"] == k and h.get("accent") is False]
            print(f"  {k:8} accented notes hit at {dist(acc)}\n           tap notes hit at      {dist(taps)}")
        wrong = [h for h in hits if h.get("dyn") in ("SOFT", "LOUD")]
        if wrong:
            print("  wrong dynamics:")
            for h in wrong[:40]:
                print(f"    {h['chart_t']:8.3f}s {h['key']:6} {h['hand'] or ' '} {'accent' if h['accent'] else 'tap   '} vel {h['velocity']:3d} -> {h['dyn']}")
        th = header.get("dyn_thresholds", {})
        if any(v is not None for v in (args.tap, args.accent, args.hihat_tap, args.hihat_accent)):
            dflt = th.get("default", [88, 84])
            hh = th.get("hihat", [112, 100])
            new = {"default": (args.accent or dflt[0], args.tap or dflt[1]),
                   "hihat": (args.hihat_accent or hh[0], args.hihat_tap or hh[1])}
            c = Counter()
            for h in hits:
                if h["judge"] == "STRAY":
                    continue
                a, t = new["hihat"] if h["key"] == "hihat" else new["default"]
                c[rejudge(h["accent"], h["velocity"], a, t)] += 1
            print(f"\nre-judged with {new}: {dict(c)}")

    if args.hits:
        print("\nhits:")
        for h in hits:
            print(f"  {h['t']:.3f} {h['key']:8} note {h['note']:3d} vel {h['velocity']:3d} {h['judge']:8} "
                  f"{'' if h.get('error_ms') is None else f'{h['error_ms']:+6.1f} ms'} {h.get('dyn') or ''}")


if __name__ == "__main__":
    main()
