"""Progress statistics from the run logs, for the progress screen and the coach.

Reads only the header line of each run log (cached by file mtime), so it stays cheap as
the logs pile up. summary() feeds the screen; report() is the compact JSON the coach
sends to Claude.
"""
import glob
import json
import os
import statistics as st
import time
from collections import defaultdict

from .runlog import RUNS_DIR
from .profiles import runs_match
from .kit import PROGRESS_MIGRATIONS

_cache = {}     # path -> (mtime, header)


def load_runs(runs_dir=None, profile=None):
    """Headers of every run, oldest first: dicts with started, ended, chart, stats...
    profile: only that profile's runs (the owner's include the runs from before profiles)."""
    runs_dir = runs_dir or RUNS_DIR
    out = []
    for path in glob.glob(os.path.join(runs_dir, "*.jsonl")):
        try:
            m = os.path.getmtime(path)
            if path in _cache and _cache[path][0] == m:
                head = _cache[path][1]
            else:
                with open(path) as f:
                    head = json.loads(f.readline())
                _cache[path] = (m, head)
            if head.get("kind") == "run" and head.get("stats") and runs_match(profile, head):
                head["path"] = path
                out.append(head)
        except (OSError, ValueError):
            continue
    out.sort(key=lambda h: h.get("started", 0))
    return out


def _day(ts):
    return time.strftime("%Y-%m-%d", time.localtime(ts))


def _streaks(days, today):
    """(current streak ending today or yesterday, best streak) over a sorted set of day strings."""
    if not days:
        return 0, 0
    ordinal = sorted({time.mktime(time.strptime(d, "%Y-%m-%d")) // 86400 for d in days})
    best = cur = 1
    for a, b in zip(ordinal, ordinal[1:]):
        cur = cur + 1 if b - a == 1 else 1
        best = max(best, cur)
    today_o = time.mktime(time.strptime(today, "%Y-%m-%d")) // 86400
    current = cur if today_o - ordinal[-1] <= 1 else 0
    return current, best


def summary(runs=None, now=None, profile=None):
    runs = load_runs(profile=profile) if runs is None else runs
    now = time.time() if now is None else now
    today = _day(now)
    by_day = defaultdict(lambda: {"seconds": 0.0, "runs": 0, "notes": 0, "grades": []})
    for r in runs:
        d = _day(r["started"])
        dur = max(0.0, (r.get("ended") or r["started"]) - r["started"])
        by_day[d]["seconds"] += dur
        by_day[d]["runs"] += 1
        by_day[d]["notes"] += r["stats"].get("hit", 0)
        by_day[d]["grades"].append(r["stats"].get("grade", 0))
    days = sorted(by_day)
    current, best = _streaks(days, today)
    week = []
    for i in range(6, -1, -1):
        d = _day(now - i * 86400)
        week.append((d, by_day[d]["seconds"] / 60 if d in by_day else 0.0))
    last_runs = runs[-30:]
    trend_acc = [r["stats"]["accuracy"] for r in last_runs]
    trend_std = [r["stats"].get("std_ms", 0) for r in last_runs]
    trend_grade = [r["stats"].get("grade", 0) for r in last_runs]
    recent = [r for r in runs if r["started"] >= now - 7 * 86400]
    previous = [r for r in runs if now - 14 * 86400 <= r["started"] < now - 7 * 86400]
    mean_grade = lambda rs: st.fmean(r["stats"].get("grade", 0) for r in rs) if rs else None
    best_run = max(runs, key=lambda r: r["stats"].get("grade", 0)) if runs else None
    tightest = min((r for r in runs if r["stats"].get("hit", 0) >= 16), key=lambda r: r["stats"].get("std_ms", 1e9), default=None)
    longest = max(runs, key=lambda r: r["stats"].get("max_combo", 0)) if runs else None
    return {
        "today": today,
        "runs": len(runs),
        "days": len(days),
        "streak": current, "best_streak": best,
        "total_minutes": sum(v["seconds"] for v in by_day.values()) / 60,
        "total_notes": sum(v["notes"] for v in by_day.values()),
        "today_minutes": by_day[today]["seconds"] / 60 if today in by_day else 0.0,
        "today_runs": by_day[today]["runs"] if today in by_day else 0,
        "week": week,                                    # [(day, minutes)] oldest first
        "trend_accuracy": trend_acc, "trend_std_ms": trend_std, "trend_grade": trend_grade,
        "grade_week": mean_grade(recent), "grade_prev_week": mean_grade(previous),
        "best_run": best_run, "tightest": tightest, "longest_combo": longest,
        "last": list(reversed(runs[-8:])),
    }


def per_level(runs=None, profile=None):
    """chart name -> attempts, best stats, last played, mean timing per instrument (from stats)."""
    runs = load_runs(profile=profile) if runs is None else runs
    out = {}
    for r in runs:
        name = r["chart"]["name"]
        name = PROGRESS_MIGRATIONS.get(name, name)     # a renamed level keeps the runs played under its old name
        s = r["stats"]
        e = out.setdefault(name, {"attempts": 0, "best_grade": 0, "best_stars": 0, "last": 0, "grades": [],
                                  "accuracy": [], "mean_ms": [], "std_ms": [], "dyn_rate": [], "stray_rate": [],
                                  "bpm": r["chart"]["bpm"], "rate": r["chart"].get("rate", 1.0), "dynamics": r["chart"].get("dynamics")})
        e["attempts"] += 1
        e["grades"].append(s.get("grade", 0)); e["accuracy"].append(s.get("accuracy", 0))
        e["mean_ms"].append(s.get("mean_ms", 0)); e["std_ms"].append(s.get("std_ms", 0)); e["stray_rate"].append(s.get("stray_rate", 0))
        if s.get("dyn_rate") is not None:
            e["dyn_rate"].append(s["dyn_rate"])
        if s.get("grade", 0) > e["best_grade"]:
            e["best_grade"], e["best_stars"] = s.get("grade", 0), s.get("stars", 0)
        e["last"] = max(e["last"], r["started"])
    for e in out.values():
        for k in ("grades", "accuracy", "mean_ms", "std_ms", "dyn_rate", "stray_rate"):
            v = e.pop(k)
            e[k[:-1] if k.endswith("s") and k != "grades" else k] = round(st.fmean(v), 3) if v else None
        e["last"] = time.strftime("%Y-%m-%d", time.localtime(e["last"]))
    return out


def report(levels, runs=None, now=None, profile=None):
    """Compact JSON-able dict for the coach: catalogue of levels, what was played and how,
    the trends, the streak. `levels`: {category: [Chart]}; profile: whose runs."""
    runs = load_runs(profile=profile) if runs is None else runs
    s = summary(runs, now)
    catalogue = []
    for cat, charts in levels.items():
        for i, ch in enumerate(charts):
            catalogue.append({"category": cat, "index": i + 1, "name": ch.name, "bpm": ch.bpm, "bars": ch.bars,
                              "desc": ch.desc, "instruments": sorted({n.key for n in ch.notes}),
                              "dynamics": ch.dynamics, "sticking": "".join(ch.sticking) if ch.sticking else None,
                              "left_hand_lead": ch.mirrored().key if ch.lead else None})
    lv = per_level(runs)
    played = []
    for name, e in lv.items():
        e = dict(e); e["name"] = name; played.append(e)
    played.sort(key=lambda e: e["last"], reverse=True)
    recent = [{"when": time.strftime("%Y-%m-%d %H:%M", time.localtime(r["started"])), "level": r["chart"]["name"],
               "rate": r["chart"].get("rate", 1.0), "grade": round(r["stats"].get("grade", 0), 1), "stars": r["stats"].get("stars", 0),
               "accuracy": round(r["stats"].get("accuracy", 0), 3), "mean_ms": round(r["stats"].get("mean_ms", 0), 1),
               "std_ms": round(r["stats"].get("std_ms", 0), 1), "early": r["stats"].get("early"), "late": r["stats"].get("late"),
               "strays": round(r["stats"].get("stray_rate", 0) * r["stats"].get("notes", 0)),
               "accents_ok": r["stats"].get("accents_ok"), "accents": r["stats"].get("accents"),
               "taps_ok": r["stats"].get("taps_ok"), "taps": r["stats"].get("taps"), "contrast": r["stats"].get("contrast")}
              for r in runs[-40:]]
    return {
        "generated": time.strftime("%Y-%m-%d %H:%M"),
        "practice": {"days": s["days"], "runs": s["runs"], "streak_days": s["streak"], "best_streak": s["best_streak"],
                     "total_minutes": round(s["total_minutes"]), "week_minutes": [round(m) for _, m in s["week"]],
                     "grade_this_week": s["grade_week"], "grade_previous_week": s["grade_prev_week"]},
        "levels_catalogue": catalogue,
        "levels_played": played,
        "recent_runs": recent,
    }
