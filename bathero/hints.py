"""Hints: what went wrong in a run, worked out from the judged notes alone (no LLM), for the
results screen. Asked for 2026-09-18: every attempt that does not reach five stars, the game
says in one or two lines what to work on ("you rush the &s", "the kick lands ahead of the
rest of the kit", "soft strokes that are not in the chart"). It was every third attempt for a
day; with R retried mid-run the one run played to the end, the one that needs it, never showed it.

analyse() takes the judged notes and the strays in a plain form that both the live Game
and a run log can produce (`from_game`, `from_runlog`), and returns the hints ordered by
how many grade points the fault costs, so the first is the one to work on. Every rule is a
threshold on a plain statistic; the numbers are the ones that read as "a problem" on the
2026-09-17 runs (a 22 ms bias with a 15 ms spread was the difference between four stars and
five).

    python -m bathero.hints [PATH.jsonl ...]   # the hints for the latest run (or the given ones)
"""
import glob
import os
import re
import statistics as st
from collections import Counter, defaultdict

from .chart import LABELS
from .game import GOOD_MS, PERFECT_MS, grade_for, stars_for
from .runlog import RUNS_DIR, load

BIAS_MS = 15            # a mean this far from zero is a lead / lag worth naming
GAP_MS = 15             # one body, one hand or the offbeats this far from the rest
DRIFT_MS = 15           # second half this far from the first: a tempo drift
SPREAD_MS = 25          # a std this wide is "irregular" even when the mean is fine
MIN_NOTES = 4           # never diagnose a group from fewer notes
STRAY_SOFT = 40         # strays at this velocity or under are touches, not strokes

LABEL = {k: v.lower() for k, v in LABELS.items()}
LABEL_ES = {"kick": "el bombo", "snare": "el tambor", "hihat": "el hi-hat", "crash": "el crash", "crash2": "el crash derecho",
            "tom1": "el tom", "floor": "el tom de piso", "ride": "el ride", "pedal": "el pedal del hi-hat"}
HAND_ES = {"R": "la derecha", "L": "la izquierda"}
HAND_EN = {"R": "the right hand", "L": "the left hand"}


def _body(key, lang):
    return LABEL_ES.get(key, key) if lang == "es" else "the " + LABEL.get(key, key)


def _de(key):
    """'del bombo', 'de la ...' for the Spanish lines."""
    return re.sub(r"^el ", "del ", LABEL_ES.get(key, key))


def _hand(hand, lang):
    return (HAND_ES if lang == "es" else HAND_EN).get(hand, hand)


def _pos(beat_pos):
    """A note's place in its bar as a name: 1 / & of 1 / 2 ..., for the 4/4 grid the charts use;
    None off the eighth grid (a triplet's inner notes, sextuplets), which has no such name."""
    p = round(beat_pos * 2) / 2
    if abs(p - beat_pos) > 0.05:
        return None
    p %= 4
    return f"{int(p) + 1}" if p == int(p) else f"& of {int(p) + 1}"


def _onbeat(beat_pos):
    """True for a note on a beat (any grid)."""
    return abs(beat_pos - round(beat_pos)) < 0.05


def _grid(beat_pos):
    """'on' / 'off' for a note on the eighth grid, None off it."""
    p = _pos(beat_pos)
    return None if p is None else ("off" if p.startswith("&") else "on")


def _pos_es(name):
    return name.replace("& of ", "& de ")


def from_game(game):
    """(notes, strays) from a live Game: every chart note with its judgement, and the strays."""
    notes = [{"t": n.t, "beat": game.chart.beat_pos(n.t), "key": n.key, "judge": n.judge, "err": n.error_ms,
              "accent": n.accent, "hand": n.hand, "vel": n.hit_velocity, "dyn": n.dyn,
              "art": n.art, "art_ok": n.art_ok, "played": n.played}
             for n in game.notes if n.state != "skip"]
    strays = [(game.lanes[lane].key, vel) for t, lane, judge, err, vel, dyn in game.hits if judge == "STRAY"]
    return notes, strays


def from_runlog(header, events):
    """(notes, strays) from a run log (runlog.load): the chart's notes matched with the hit /
    miss events by chart time and instrument."""
    chart = header["chart"]
    beat = 60 / chart["bpm"]                    # already the tempo played (at_rate scales bpm)
    notes = [{"t": n["t"], "beat": n["t"] / beat, "key": n["key"], "judge": None, "err": None, "accent": n.get("accent"),
              "hand": n.get("hand"), "vel": None, "dyn": None, "art": n.get("art"), "art_ok": None, "played": None}
             for n in chart["notes"]]
    by = defaultdict(list)
    for n in notes:
        by[(round(n["t"], 3), n["key"])].append(n)
    strays = []
    for e in events:
        if e["kind"] == "hit":
            if e["judge"] == "STRAY":
                strays.append((e["key"], e["velocity"]))
                continue
            k = (round(e["chart_t"], 3), e["key"])
            cands = [n for n in by.get(k, []) if n["judge"] is None]
            if cands:
                n = cands[0]
                n.update(judge=e["judge"], err=e["error_ms"], vel=e["velocity"], dyn=e.get("dyn"),
                         played=e.get("played"), art_ok=e.get("art_ok"))
        elif e["kind"] == "miss":
            k = (round(e["chart_t"], 3), e["key"])
            cands = [n for n in by.get(k, []) if n["judge"] is None]
            if cands:
                cands[0]["judge"] = "MISS"
    # a rehearsal (seek) or a run left early: only the notes that were judged
    return [n for n in notes if n["judge"] is not None], strays


def stats_of(notes, strays, dynamics=False, expression=False):
    """The stats dict grade_for wants, from the plain notes (the Game's own stats() is the
    reference; this one exists for run logs and tests)."""
    c = Counter(n["judge"] for n in notes)
    total = len(notes)
    errs = [n["err"] for n in notes if n["err"] is not None]
    out = {"notes": total, "hit": len(errs), "accuracy": len(errs) / total if total else 0.0,
           "mean_ms": st.fmean(errs) if errs else 0.0, "std_ms": st.pstdev(errs) if len(errs) > 1 else 0.0,
           "quality": (c["PERFECT"] + 0.6 * c["GOOD"] + 0.3 * c["OK"]) / total if total else 0.0,
           "stray_rate": len(strays) / total if total else 0.0, "dyn_rate": None}
    if dynamics:
        judged = [n for n in notes if n["dyn"]]
        out["dyn_rate"] = sum(1 for n in judged if n["dyn"] in ("ACCENT", "TAP")) / len(judged) if judged else None
    if expression:
        judged = [n for n in notes if n["art_ok"] is not None]
        r = sum(1 for n in judged if n["art_ok"]) / len(judged) if judged else None
        if r is not None:
            out["dyn_rate"] = r if out["dyn_rate"] is None else (out["dyn_rate"] + r) / 2
    out["grade"] = grade_for(out)
    out["stars"] = stars_for(out["grade"])
    return out


def _n(k, one, many):
    return f"{k} {one if k == 1 else many}"


def analyse(notes, strays, lang="es", dynamics=False, expression=False):
    """[(cost, text)] ordered by cost (grade points the fault is costing), the text one line
    in `lang` ("es" / "en"). Empty when nothing stands out."""
    es = lang == "es"
    total = len(notes)
    if not total:
        return []
    hits = [n for n in notes if n["err"] is not None]
    misses = [n for n in notes if n["judge"] == "MISS"]
    wq = 50 if not (dynamics or expression) else 30     # what hit quality weighs in the grade
    out = []

    # --- misses: half the grade is accuracy --------------------------------------
    if misses:
        cost = (50 + wq) * len(misses) / total
        by_key = Counter(n["key"] for n in misses)
        key, k = by_key.most_common(1)[0]
        by_pos = Counter(_pos(n["beat"]) for n in misses if n["key"] == key and _pos(n["beat"]))
        pos, p = by_pos.most_common(1)[0] if by_pos else (None, 0)
        where = ""
        if pos and p >= 2 and p >= 0.5 * k:
            where = f", sobre todo en el {_pos_es(pos)}" if es else f", mostly on the {pos}"
        if k >= 0.6 * len(misses) and len(misses) >= 2:
            text = (f"se te escapan notas {_de(key)}: {k} de {len(misses)} misses{where}" if es else
                    f"you are dropping {_body(key, lang)}: {k} of {len(misses)} misses{where}")
        else:
            text = (f"{_n(len(misses), 'nota sin tocar', 'notas sin tocar')}: primero asegurá que suene todo, después el timing" if es else
                    f"{_n(len(misses), 'note not played', 'notes not played')}: first play everything, then work on the timing")
        out.append((cost, text))

    # --- strays: 2 points each per 100 notes -------------------------------------
    if strays:
        cost = 200 * len(strays) / total
        soft = [s for s in strays if s[1] <= STRAY_SOFT]
        by_key = Counter(s[0] for s in strays)
        key, k = by_key.most_common(1)[0]
        if len(soft) >= 0.6 * len(strays):
            text = (f"{_n(len(strays), 'golpe de más, suave', 'golpes de más, suaves')} ({', '.join(sorted({LABEL_ES.get(s[0], s[0]).split()[-1] for s in soft}))}): "
                    f"roces del pie o de la baqueta apoyada, cada uno resta" if es else
                    f"{_n(len(strays), 'extra stroke, soft', 'extra strokes, all soft')} ({', '.join(sorted({LABEL.get(s[0], s[0]) for s in soft}))}): "
                    f"foot or stick touching the pad, each one costs")
        elif k >= 0.6 * len(strays):
            text = (f"{_n(len(strays), 'golpe de más', 'golpes de más')} en {_body(key, lang)}: tocá solo lo escrito, cada uno resta" if es else
                    f"{_n(len(strays), 'extra stroke', 'extra strokes')} on {_body(key, lang)}: play only what is written, each one costs")
        else:
            text = (f"{len(strays)} golpes de más: tocá solo lo escrito, cada uno resta" if es else
                    f"{len(strays)} extra strokes: play only what is written, each one costs")
        out.append((cost, text))

    # --- timing: quality is the other half ---------------------------------------
    if len(hits) >= MIN_NOTES:
        errs = [n["err"] for n in hits]
        mean, sd = st.fmean(errs), st.pstdev(errs)
        c = Counter(n["judge"] for n in hits)
        cost = wq * (len(hits) - (c["PERFECT"] + 0.6 * c["GOOD"] + 0.3 * c["OK"])) / total
        timing = []
        # one body against the rest, only where they land together (a chord): comparing the
        # bodies' means read the gallop's early kick sixteenths as "the kick ahead of the kit"
        # while every chord was tight (2026-09-20, "I do not feel any flam")
        keys = {n["key"] for n in hits}
        chords = defaultdict(list)
        for n in hits:
            chords[round(n["t"], 3)].append(n)
        chords = [c for c in chords.values() if len({n["key"] for n in c}) > 1]
        if len(keys) > 1 and len(chords) >= MIN_NOTES:
            for key in sorted(keys):
                gaps = []
                for c in chords:
                    mine = [n["err"] for n in c if n["key"] == key]
                    rest = [n["err"] for n in c if n["key"] != key]
                    if mine and rest:
                        gaps.append(st.fmean(mine) - st.fmean(rest))
                if len(gaps) >= MIN_NOTES:
                    gap = st.fmean(gaps)
                    if abs(gap) >= GAP_MS:
                        timing.append((abs(gap) + 10, (
                            f"cuando van juntos, {_body(key, lang)} cae {abs(gap):.0f} ms {'antes' if gap < 0 else 'después'} que el resto: un flam" if es else
                            f"when they land together, {_body(key, lang)} is {abs(gap):.0f} ms {'ahead of' if gap < 0 else 'behind'} the rest: a flam").capitalize()))
        # one hand against the other
        hands = {n["hand"] for n in hits if n["hand"] in ("R", "L")}
        if len(hands) == 2:
            r = [n["err"] for n in hits if n["hand"] == "R"]
            l = [n["err"] for n in hits if n["hand"] == "L"]
            if len(r) >= MIN_NOTES and len(l) >= MIN_NOTES:
                gap = st.fmean(l) - st.fmean(r)
                if abs(gap) >= GAP_MS:
                    timing.append((abs(gap) + 10, (
                        f"{_hand('L', lang)} queda {'atrasada' if gap > 0 else 'adelantada'} {abs(gap):.0f} ms respecto a la derecha" if es else
                        f"{_hand('L', lang)} lands {abs(gap):.0f} ms {'behind' if gap > 0 else 'ahead of'} the right").capitalize()))
        # the notes between the beats against the notes on them, per body and for the kit: the
        # one group that is off the click is named (the gallop: the beat's kick 30 ms late with
        # the hands, its sixteenths in place; or the sixteenths early with the beat in place)
        groups = [(None, hits)] + [(k, [n for n in hits if n["key"] == k]) for k in sorted(keys)]
        found = []
        for key, ns in groups:
            on = [n["err"] for n in ns if _onbeat(n["beat"])]
            off = [n for n in ns if not _onbeat(n["beat"])]
            if len(on) >= MIN_NOTES and len(off) >= MIN_NOTES:
                m_on, m_off = st.fmean(on), st.fmean(n["err"] for n in off)
                if abs(m_off - m_on) >= GAP_MS:
                    eighths = all(_grid(n["beat"]) == "off" for n in off)
                    found.append((abs(m_off - m_on), key, m_on, m_off, eighths))
        if found:
            found.sort(key=lambda f: (f[1] is None, -f[0]))       # a body's own figure before the whole kit
            gap, key, m_on, m_off, eighths = found[0]
            who = _body(key, lang) if key else ("el kit" if es else "the kit")
            subs = ("los &" if eighths else "las notas entre los tiempos") if es else ("the &s" if eighths else "the notes between the beats")
            if abs(m_on) > abs(m_off) + 5:                                  # the beat is what is off
                text = (f"{who}: {'el tiempo llega' if key else 'los tiempos llegan'} {abs(m_on):.0f} ms {'antes' if m_on < 0 else 'tarde'}, {subs} están en su lugar" if es else
                        f"{who}: the beat lands {abs(m_on):.0f} ms {'early' if m_on < 0 else 'late'}, {subs} are in place")
            elif abs(m_off) > abs(m_on) + 5:                                # the subdivisions are
                text = (f"{who}: {subs} {'se adelantan' if m_off < 0 else 'se atrasan'} {abs(m_off):.0f} ms, el tiempo está en su lugar" if es else
                        f"{who}: {subs} land {abs(m_off):.0f} ms {'early' if m_off < 0 else 'late'}, the beat is in place")
            else:
                text = (f"{who}: {subs} llegan {gap:.0f} ms {'antes' if m_off < m_on else 'después'} que los tiempos" if es else
                        f"{who}: {subs} land {gap:.0f} ms {'ahead of' if m_off < m_on else 'behind'} the beats")
            timing.append((gap + 5, text.capitalize()))
        # drift: first half against second
        half = len(hits) // 2
        if half >= MIN_NOTES:
            drift = st.fmean(errs[half:]) - st.fmean(errs[:half])
            if abs(drift) >= DRIFT_MS:
                timing.append((abs(drift), (
                    f"vas {'acelerando' if drift < 0 else 'frenando'} con el correr del nivel: la segunda mitad queda {abs(drift):.0f} ms {'antes' if drift < 0 else 'después'} que la primera" if es else
                    f"you {'speed up' if drift < 0 else 'slow down'} as the level goes: the second half lands {abs(drift):.0f} ms {'earlier' if drift < 0 else 'later'} than the first").capitalize()))
        # the bias of everything
        if abs(mean) >= BIAS_MS:
            timing.append((abs(mean) + 8, (
                f"{'te adelantás' if mean < 0 else 'vas atrasado'} {abs(mean):.0f} ms en todo el kit: {'esperá el click, tocá más tarde de lo que sentís' if mean < 0 else 'anticipá, el golpe tiene que caer con el click'}" if es else
                f"you play {abs(mean):.0f} ms {'early' if mean < 0 else 'late'} across the kit: {'wait for the click, play later than it feels' if mean < 0 else 'anticipate, the stroke has to land on the click'}").capitalize()))
        # the spread, when nothing else explains it
        if sd >= SPREAD_MS and not timing:
            by_key = {k: st.pstdev([n["err"] for n in hits if n["key"] == k]) for k in keys
                      if sum(1 for n in hits if n["key"] == k) >= MIN_NOTES}
            worst = max(by_key, key=by_key.get) if by_key else None
            where = f" ({_body(worst, lang)} sobre todo)" if es and worst and len(by_key) > 1 else \
                    f" ({_body(worst, lang)} most)" if worst and len(by_key) > 1 else ""
            timing.append((sd, (
                f"timing irregular, ±{sd:.0f} ms{where}: bajá el tempo con [ hasta que salga parejo" if es else
                f"uneven timing, ±{sd:.0f} ms{where}: slow down with [ until it is steady").capitalize()))
        if timing and cost > 0:
            timing.sort(key=lambda h: -h[0])
            out.append((cost, timing[0][1]))
            if len(timing) > 1 and timing[1][1] != timing[0][1]:
                out.append((cost * 0.5, timing[1][1]))

    # --- dynamics (rudiments with accents) ---------------------------------------
    if dynamics:
        judged = [n for n in hits if n["dyn"]]
        wrong = [n for n in judged if n["dyn"] in ("SOFT", "LOUD")]
        if judged and wrong:
            cost = 20 * len(wrong) / len(judged)
            soft = [n for n in wrong if n["dyn"] == "SOFT"]
            loud = [n for n in wrong if n["dyn"] == "LOUD"]
            if len(soft) >= len(loud):
                hand = Counter(n["hand"] for n in soft if n["hand"]).most_common(1)
                who = f" ({_hand(hand[0][0], lang)})" if hand and hand[0][1] >= 0.75 * len(soft) and len(soft) >= 3 else ""
                text = (f"{len(soft)} acentos flojos{who}: subí el acento, no bajes el tap" if es else
                        f"{len(soft)} accents too soft{who}: raise the accent, do not lower the tap")
            else:
                hand = Counter(n["hand"] for n in loud if n["hand"]).most_common(1)
                who = f" ({_hand(hand[0][0], lang)})" if hand and hand[0][1] >= 0.75 * len(loud) and len(loud) >= 3 else ""
                text = (f"{len(loud)} taps demasiado fuertes{who}: los taps a media altura, el acento desde arriba" if es else
                        f"{len(loud)} taps too loud{who}: taps from half height, the accent from the top")
            out.append((cost, text))

    # --- hi-hat expression ---------------------------------------------------------
    if expression:
        judged = [n for n in hits if n["art_ok"] is not None]
        wrong = [n for n in judged if not n["art_ok"]]
        if judged and wrong:
            cost = 20 * len(wrong) / len(judged)
            pair = Counter((n["art"].split()[0], (n["played"] or "?").split()[0]) for n in wrong).most_common(1)[0]
            (want, got), k = pair
            text = (f"{len(wrong)} hats con la apertura equivocada: {k} {want} salieron {got}, es el pedal lo que se juzga" if es else
                    f"{len(wrong)} hats at the wrong openness: {k} {want} came out {got}, the pedal is what is judged")
            out.append((cost, text))

    out.sort(key=lambda h: -h[0])
    return out


def hint_for(notes, strays, lang="es", dynamics=False, expression=False, max_lines=2):
    """The lines for the results screen: the costliest fault first, at most max_lines."""
    return [text for cost, text in analyse(notes, strays, lang, dynamics, expression)[:max_lines]]


def main():
    import argparse
    ap = argparse.ArgumentParser(description="hints for a run log")
    ap.add_argument("paths", nargs="*")
    ap.add_argument("--lang", default="es")
    ap.add_argument("--all", action="store_true", help="every hint with its cost, not only the top two")
    args = ap.parse_args()
    paths = args.paths or sorted(glob.glob(os.path.join(RUNS_DIR, "*.jsonl")))[-1:]
    for path in paths:
        header, events = load(path)
        notes, strays = from_runlog(header, events)
        chart = header["chart"]
        dyn, expr = bool(chart.get("dynamics")), bool(chart.get("expression"))
        s = stats_of(notes, strays, dyn, expr)
        print(f"{os.path.basename(path)}: {s['stars']} stars, grade {s['grade']:.0f}, mean {s['mean_ms']:+.0f} ms, std {s['std_ms']:.0f} ms, {len(strays)} strays")
        hints = analyse(notes, strays, args.lang, dyn, expr)
        for cost, text in (hints if args.all else hints[:2]):
            print(f"  {cost:5.1f}  {text}")
        if not hints:
            print("  (nothing stands out)")


if __name__ == "__main__":
    main()
