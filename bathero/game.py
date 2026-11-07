"""Game state: the clock, judging hits, misses, guide track and stats. No drawing."""
import csv
import math
import statistics
import threading
import time
from collections import deque
from dataclasses import dataclass

from .chart import art_matches

PERFECT_MS = 30          # |error| <= this -> PERFECT (25 until 2026-09-20: five stars asked for 9 in 10 notes
                         # within 25 ms, a 20 ms spread; the user's best gallop runs sat at 24 ms and 94 was
                         # reached by 1 run in 132; at 30 by 18, the ones with every note and no stray)
GOOD_MS = 60             # |error| <= this -> GOOD
OK_MS = 100              # |error| <= this -> OK; beyond -> the hit is stray / the note is missed
SCORE = {"PERFECT": 100, "GOOD": 50, "OK": 20}
# Dynamics, measured 2026-09-07 on the user's paradiddles (snare, 184 strokes): accents
# 88..124 (median 112), taps 29..84 (median 66), accent / taps ratio 1.35..2.2 (median 1.7).
ACCENT_MIN = 88          # an accented note hit at least this hard counts as an accent
TAP_MAX = 84             # an unaccented note hit at most this hard counts as a tap; between: neither
# The hi-hat pad reads much hotter than the snare (2026-09-07, 196 judged hi-hat hits in
# rudiments: taps 64..96, accents 120..127, median 106), so it gets its own band.
DYN_THRESHOLDS = {"hihat": (116, 104)}     # instrument -> (accent min, tap max); others use the defaults
# At night the whole band slides down by NIGHT_DYN_SCALE so accents can be played softer
# (default 70 / 67, hi-hat 93 / 83); daytime thresholds above stay as they are. Decided when a
# level starts, from the local clock.
NIGHT_START, NIGHT_END = 22, 8     # night is from 22:00 to 08:00 local time
NIGHT_DYN_SCALE = 0.8
# On top of that the user scales the band with ; and ' (accent sensitivity, saved in the
# settings): 1.0 = the measured thresholds, lower = softer accents count. One scale per
# instrument since 2026-09-17 (`dyn_scales`, instrument -> scale): with the velocity viewer
# open the keys move the instrument of the last stroke heard, closed they move all of them.
# The zones of a pad share the instrument's scale (the hi-hat's bow and edge, a snare's head
# and rim): a threshold is per body, never per zone.
DYN_SCALE_STEP = 0.05
DYN_SCALE_MIN, DYN_SCALE_MAX = 0.5, 1.3
CONTRAST_TARGET = 1.4    # median accent velocity / median tap velocity to aim for
DYN_BONUS = 30           # score for the right dynamic on a hit note
ART_BONUS = 30           # score for the right hi-hat articulation on a hit note
# Stars: a 0..100 grade from accuracy (half), hit quality (PERFECT 1, GOOD 0.6, OK 0.3) and
# dynamics (or quality again when the chart has none), minus strays, cut at these grades.
STAR_GRADES = [30, 50, 70, 85, 94]


def grade_for(st):
    """0..100 from a stats dict (accuracy, quality, dyn_rate, stray_rate)."""
    dyn = st.get("dyn_rate")
    g = 100 * (0.5 * st["accuracy"] + 0.3 * st["quality"] + 0.2 * (dyn if dyn is not None else st["quality"]))
    g -= 200 * st["stray_rate"]                      # 5 strays per 100 notes cost 10 points
    return max(0.0, min(100.0, g))


def stars_for(grade):
    return sum(1 for g in STAR_GRADES if grade >= g)
TAIL_S = 2.0             # seconds after the last note before the results


@dataclass
class Flash:
    wall_t: float
    lane: int
    judge: str
    error_ms: float
    velocity: int
    dyn: str = None      # ACCENT / TAP / SOFT / LOUD when the chart judges dynamics
    art: tuple = None    # (required articulation, matched, played) when the chart judges hi-hat expression


def is_night(now: float = None) -> bool:
    hour = time.localtime(now).tm_hour
    return hour >= NIGHT_START or hour < NIGHT_END


def dyn_band(instrument: str = None, night: bool = False, scale: float = 1.0):
    """(accent min, tap max) for an instrument, scaled by the user's sensitivity and,
    at night, by NIGHT_DYN_SCALE on top. Never below 1 so a velocity 0 hit is not a tap."""
    accent_min, tap_max = DYN_THRESHOLDS.get(instrument, (ACCENT_MIN, TAP_MAX))
    k = scale * (NIGHT_DYN_SCALE if night else 1.0)
    if k == 1.0:
        return accent_min, tap_max
    return max(1, round(accent_min * k)), max(1, round(tap_max * k))


def dynamic_for(accent: bool, velocity: int, instrument: str = None, night: bool = False, scale: float = 1.0):
    """ACCENT or TAP when the stroke matches the note, SOFT (missed accent) or LOUD
    (tap too hard) when it does not, None in the band between the thresholds."""
    accent_min, tap_max = dyn_band(instrument, night, scale)
    if accent:
        return "ACCENT" if velocity >= accent_min else "SOFT" if velocity <= tap_max else None
    return "TAP" if velocity <= tap_max else "LOUD" if velocity >= accent_min else None


def lead_in_for(bpm: float) -> float:
    """Count-in: one bar of four beats, two bars if that is under two seconds."""
    beat = 60 / bpm
    return 4 * beat if 4 * beat >= 2.0 else 8 * beat


class Game:
    metronome_mode = "full"     # set by PlayScreen from the app (full / beats / off)
    metro_volume = 1.0          # the metronome's level relative to the mix (= and -)

    def __init__(self, chart, lanes, by_note, offset_ms=0.0, speed=1.0, sounds=None, guide=True, log=None,
                 night=None, dyn_scales=None):
        self.log = log                      # RunLog or None; append-only, never blocks
        self.night = is_night() if night is None else night     # softer dynamics band after NIGHT_START
        self.dyn_scales = dict(dyn_scales or {})   # accent sensitivity per instrument (; and '); may change mid-level
        self.tracks = {}                    # name -> (Track, enabled); pre-rendered audio on the chart timeline
        self.chart = chart
        self.notes = chart.notes
        self.lanes = lanes
        self.by_note = by_note
        self.offset_ms = offset_ms
        self.speed = speed
        self.sounds = sounds
        self.guide = guide
        self.beat = 60 / chart.bpm
        self.lead_in = lead_in_for(chart.bpm)
        self.lock = threading.Lock()
        self.reset()

    # --- clock -------------------------------------------------------------
    def reset(self):
        self.stop_tracks()
        with self.lock:
            for n in self.notes:
                n.state, n.judge, n.error_ms, n.sounded = "pending", None, None, False
                n.hit_velocity, n.dyn = None, None
            self.wall_start = time.perf_counter()
            self.paused_at = None
            self.paused_total = 0.0
            self.flashes = deque(maxlen=64)
            self.hits = []                      # (chart_t, lane, judge, error_ms) for every judged input
            self.strokes = []                   # (judged t, key) of every stroke, for the offset fit (latency.py)
            self.combo = self.max_combo = self.score = 0
            self.counts = {k: 0 for k in ("PERFECT", "GOOD", "OK", "MISS", "STRAY")}
            self.dyn_counts = {k: 0 for k in ("ACCENT", "TAP", "SOFT", "LOUD")}
            self.dyn_seen = {"accent": 0, "tap": 0}   # chart notes resolved so far (hit or missed): the HUD's denominators
            self.art_counts = {"ok": 0, "wrong": 0}
            self.cursor = 0                     # first note that may still be pending
            self.guide_cursor = 0
            self.last_click_beat = None
            self.finished = False
            self.loop = None                    # (start, end) in chart seconds while a loop runs
            self.count_in_end = None            # chart time the run-up before a jump ends
            self.seeked = False                 # the transport was used: the run is a rehearsal

    def song_time(self, wall_t=None):
        if wall_t is None:
            wall_t = time.perf_counter()
        if self.paused_at is not None:
            wall_t = self.paused_at
        return wall_t - self.wall_start - self.paused_total - self.lead_in

    @property
    def paused(self):
        return self.paused_at is not None

    def dyn_scale(self, instrument):
        return self.dyn_scales.get(instrument, 1.0)

    def dyn_band(self, instrument):
        """(accent min, tap max) in force for an instrument: its own scale, the night on top."""
        return dyn_band(instrument, self.night, self.dyn_scale(instrument))

    def dyn_thresholds(self):
        insts = sorted(set(DYN_THRESHOLDS) | set(self.dyn_scales) | {l.key for l in self.lanes})
        return {"default": list(dyn_band(None, self.night, 1.0)), "night": self.night, "scales": dict(self.dyn_scales),
                **{k: list(self.dyn_band(k)) for k in insts}}

    # --- transport ---------------------------------------------------------
    def seek(self, t, count_in=0.0):
        with self.lock:
            self._seek(t, count_in)

    def _seek(self, t, count_in=0.0):
        """Jump to chart time t; the lock must be held. Notes before t are skipped (never
        judged, never sounded by the guide, never drawn), notes from t on are re-armed, so
        a phrase can be played again. count_in: seconds of run-up before t, chart silent,
        for the notes to come down the screen."""
        for n in self.notes:
            n.judge = n.error_ms = n.hit_velocity = n.dyn = n.art_ok = n.played = None
            if n.t < t:
                n.state, n.sounded = "skip", True
            else:
                n.state, n.sounded = "pending", False
        self.cursor = self.guide_cursor = next((i for i, n in enumerate(self.notes) if n.t >= t), len(self.notes))
        now = time.perf_counter()
        self.paused_total = 0.0                          # folded into wall_start below
        self.wall_start = now - self.lead_in - (t - count_in)
        if self.paused_at is not None:
            self.paused_at = now
        self.count_in_end = t if count_in > 0 else None
        self.finished = False
        self.seeked = True
        self.combo = 0
        self.flashes.clear()
        self.stop_tracks()                               # _drive_tracks restarts them at the new position

    def set_loop(self, bounds):
        """bounds: (start, end) in chart seconds, or None to stop looping."""
        with self.lock:
            self.loop = bounds

    def toggle_pause(self):
        with self.lock:
            if self.paused_at is None:
                self.paused_at = time.perf_counter()
            else:
                self.paused_total += time.perf_counter() - self.paused_at
                self.paused_at = None
        self.stop_tracks()                  # on resume they restart from the exact position

    # --- pre-rendered tracks (backing, metronome) --------------------------------
    def set_track(self, name, track, enabled=True):
        from .sounds import TRACK_CHANNELS
        track.channel = TRACK_CHANNELS.get(name)
        old = self.tracks.get(name)
        if old:
            old[0].stop()
        self.tracks[name] = (track, enabled)

    def enable_track(self, name, enabled):
        if name in self.tracks:
            track, _ = self.tracks[name]
            self.tracks[name] = (track, enabled)
            if not enabled:
                track.stop()

    def track_enabled(self, name):
        return name in self.tracks and self.tracks[name][1]

    def stop_tracks(self):
        for track, _ in self.tracks.values():
            track.stop()

    def _drive_tracks(self, t):
        """Start each enabled track at its own t0 (the count-in), restart it after a pause
        from the current time, and stop everything when the level is over."""
        for track, enabled in self.tracks.values():
            want = enabled and not self.finished and t < track.end
            if want and not track.playing and t >= track.t0 - 0.004:
                track.start_at(t)
            elif not want and track.playing:
                track.stop()

    # --- input -------------------------------------------------------------
    def hit(self, note: int, velocity: int = 100, wall_t: float = None, art: str = None):
        """Judge an incoming MIDI note. Called from the MIDI thread; must be quick.
        art: the hi-hat articulation played (hhmapper's labels), known from the pedal and the zone."""
        lane = self.by_note.get(note)
        if lane is None:
            if self.log is not None:
                self.log.add("unmapped", note=note, velocity=velocity)
            return None
        return self.hit_lane(lane, velocity, wall_t, note, art)

    def hit_lane(self, lane: int, velocity: int = 100, wall_t: float = None, note: int = None, art: str = None):
        if wall_t is None:
            wall_t = time.perf_counter()
        if self.sounds:
            self.sounds.play(self.lanes[lane].key, velocity, art=art)
        with self.lock:
            if self.paused or self.finished:
                return None
            t = self.song_time(wall_t) - self.offset_ms / 1000   # offset = your chain's latency: hits are treated as earlier
            best, best_err = None, None
            for n in self.notes[self.cursor:]:
                if n.t - t > OK_MS / 1000:
                    break
                if n.lane != lane or n.state != "pending":
                    continue
                err = t - n.t
                if abs(err) <= OK_MS / 1000 and (best is None or abs(err) < abs(best_err)):
                    best, best_err = n, err
            dyn = None
            if best is None:
                judge, err_ms = "STRAY", None
            else:
                err_ms = best_err * 1000
                a = abs(err_ms)
                judge = "PERFECT" if a <= PERFECT_MS else "GOOD" if a <= GOOD_MS else "OK"
                best.state, best.judge, best.error_ms = "hit", judge, err_ms
                best.hit_velocity = velocity
                if self.chart.dynamics:
                    dyn = best.dyn = dynamic_for(best.accent, velocity, best.key, self.night, self.dyn_scale(best.key))
                if self.chart.expression and best.art:
                    best.played = art
                    best.art_ok = art_matches(best.art, art)      # the pedal position, not the zone
                    self.art_counts["ok" if best.art_ok else "wrong"] += 1
                    if best.art_ok:
                        self.score += ART_BONUS
            self._register(judge, lane, err_ms, velocity, wall_t, best, dyn)
            self.strokes.append((t, self.lanes[lane].key))
            if self.log is not None:
                self.log.add("hit", note=note, velocity=velocity, key=self.lanes[lane].key, lane=lane, judge=judge,
                             song_t=round(t, 4), chart_t=round(best.t, 4) if best else None,
                             error_ms=round(err_ms, 2) if err_ms is not None else None,
                             accent=best.accent if best else None, hand=best.hand if best else None, dyn=dyn,
                             art=best.art if best else None, played=art, art_ok=best.art_ok if best else None,
                             combo=self.combo, score=self.score)
            return judge

    def _register(self, judge, lane, err_ms, velocity, wall_t, note, dyn=None):
        self.counts[judge] += 1
        if judge in SCORE:
            self.combo += 1
            self.max_combo = max(self.max_combo, self.combo)
            self.score += SCORE[judge] * (1 + self.combo // 10)
        else:
            self.combo = 0
        if note is not None and self.chart.dynamics:
            self.dyn_seen["accent" if note.accent else "tap"] += 1
        if dyn:
            self.dyn_counts[dyn] += 1
            if dyn in ("ACCENT", "TAP"):
                self.score += DYN_BONUS
        self.flashes.append(Flash(wall_t, lane, judge, err_ms, velocity, dyn,
                                  None if note is None or note.art is None or note.art_ok is None
                                  else (note.art, note.art_ok, note.played)))
        self.hits.append((note.t if note else None, lane, judge, err_ms, velocity, dyn))

    # --- per-frame housekeeping ----------------------------------------------
    def update(self):
        """Misses, guide sounds, count-in clicks. Call once per frame."""
        with self.lock:
            if self.paused:
                return
            t = self.song_time()
            if self.loop is not None and t >= self.loop[1]:
                self._seek(self.loop[0])        # straight back, no run-up: the loop keeps the pulse
                t = self.song_time()
            # a note is missed once a hit could no longer reach it: the hits are dated
            # song_time - offset (hit_lane), so the deadline moves by the offset too. Until
            # 2026-09-16 it did not: with +45 ms the note died at +100 raw = +55 compensated,
            # and a stroke 55..100 ms late (an OK) found it gone and became a STRAY, twice
            # penalised. 112 of the day's 403 strays were that.
            for i in range(self.cursor, len(self.notes)):
                n = self.notes[i]
                if n.t > t - self.offset_ms / 1000 - OK_MS / 1000:
                    break
                if n.state == "pending":
                    n.state, n.judge = "miss", "MISS"
                    self._register("MISS", n.lane, None, 0, time.perf_counter(), n)
                    if self.log is not None:
                        self.log.add("miss", key=n.key, chart_t=round(n.t, 4), accent=n.accent, hand=n.hand)
            while self.cursor < len(self.notes) and self.notes[self.cursor].state != "pending":
                self.cursor += 1
            if not self.finished and self.loop is None and t > self.notes[-1].t + TAIL_S:
                self.finished = True
            self._drive_tracks(t)

            if self.sounds:
                # guide track: the chart's own notes as they cross the line
                while self.guide_cursor < len(self.notes) and self.notes[self.guide_cursor].t <= t:
                    n = self.notes[self.guide_cursor]
                    if self.guide and not n.sounded:
                        self.sounds.play(n.key, n.velocity, 0.45, art=n.art)
                    n.sounded = True
                    self.guide_cursor += 1

    # --- stats ---------------------------------------------------------------
    def stats(self):
        errs = [h[3] for h in self.hits if h[3] is not None]
        total = len(self.notes)
        hit = sum(self.counts[k] for k in SCORE)
        out = {
            "notes": total,
            "hit": hit,
            "accuracy": hit / total if total else 0.0,
            "mean_ms": statistics.fmean(errs) if errs else 0.0,
            "std_ms": statistics.pstdev(errs) if len(errs) > 1 else 0.0,
            "early": sum(1 for e in errs if e < 0),
            "late": sum(1 for e in errs if e > 0),
        }
        out.update(self.dynamics(len(self.notes)))
        c = self.counts
        out["quality"] = (c["PERFECT"] + 0.6 * c["GOOD"] + 0.3 * c["OK"]) / total if total else 0.0
        out["stray_rate"] = c["STRAY"] / total if total else 0.0
        judged = out.get("accents_ok", 0) + out.get("taps_ok", 0) + out.get("soft", 0) + out.get("loud", 0)
        out["dyn_rate"] = (out["accents_ok"] + out["taps_ok"]) / judged if self.chart.dynamics and judged else None
        a = self.art_counts
        out["art_ok"], out["art_wrong"] = a["ok"], a["wrong"]
        out["art_total"] = sum(1 for n in self.notes if n.art)
        out["art_rate"] = a["ok"] / (a["ok"] + a["wrong"]) if self.chart.expression and (a["ok"] + a["wrong"]) else None
        if out["art_rate"] is not None:
            out["dyn_rate"] = out["art_rate"] if out["dyn_rate"] is None else (out["dyn_rate"] + out["art_rate"]) / 2
        out["grade"] = grade_for(out)
        out["stars"] = stars_for(out["grade"])
        out["max_combo"] = self.max_combo
        out["score"] = self.score
        return out

    def dynamics(self, last_n=None):
        """Accent / tap tallies and the velocity contrast over the last `last_n` hit notes
        (None = all): contrast = median accent velocity / median tap velocity, or None."""
        if not self.chart.dynamics:
            return {}
        hits = [n for n in self.notes if n.state == "hit" and n.hit_velocity is not None]
        if last_n is not None:
            hits = hits[-last_n:]
        acc = [n.hit_velocity for n in hits if n.accent]
        taps = [n.hit_velocity for n in hits if not n.accent]
        contrast = statistics.median(acc) / statistics.median(taps) if acc and taps and statistics.median(taps) > 0 else None
        return {
            "accents": sum(1 for n in self.notes if n.accent), "accents_ok": sum(1 for n in hits if n.dyn == "ACCENT"),
            "taps": sum(1 for n in self.notes if not n.accent), "taps_ok": sum(1 for n in hits if n.dyn == "TAP"),
            "soft": sum(1 for n in hits if n.dyn == "SOFT"), "loud": sum(1 for n in hits if n.dyn == "LOUD"),
            "contrast": contrast,
        }

    def write_csv(self, path):
        with open(path, "w", newline="") as f:
            w = csv.writer(f)
            w.writerow(["chart_time_s", "lane", "label", "judge", "error_ms", "velocity", "dynamic"])
            for t, lane, judge, err, vel, dyn in self.hits:
                w.writerow([f"{t:.4f}" if t is not None else "", lane, self.lanes[lane].label, judge,
                            f"{err:.1f}" if err is not None else "", vel, dyn or ""])
