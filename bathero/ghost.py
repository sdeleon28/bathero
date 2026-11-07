"""Hi-hat ghost filter, ported from hhmapper's measurements on a Roland TD-17.

Working the pedal shakes the hat and the module sends stick notes that nobody
played. Measured on 2026-09-06 while stomping:
  - ~30 ms BEFORE the chick: note 46 at velocity 7..22 with the pedal still up
  - 3..8 ms AFTER the chick: note 46 at velocity 48..94 with the pedal moving fast
  - up to ~250 ms after: note 42 at velocity 22..36 while the pedal settles
Measured 2026-09-07 hitting the edge with the pedal closed: nearly every hard stroke is
followed by one or two bow notes (42, sometimes 46), 42 ms later at 70..76 % of the
stroke's velocity and/or 73..93 ms later at 35..56 % (73..111 ms at 28..56 % after a 113..127,
measured 2026-09-20 on the kick gallop level); soft strokes get them rarely.
Re-tuned 2026-09-09 on real playing (paradiddles, fast bow/edge alternation, chick and
stroke together, open hats): the softest real tap read 29; real bow taps land as close
as 44 ms after an edge accent at 63..85 % of it; a stick landing with the chick reads
113..126 within 5 ms of it, or 56..127 as a 42 up to 52 ms later; real strokes while
the pedal is opening read 116..127. Every rule below keeps those.
Measured 2026-09-19 over ten days of the MIDI trace (118k notes, two drummers, a friend who
plays every hat stroke hard on the edge): the near crosstalk is a bow note 8..48 ms after an
edge stroke (the mass at 40..48), never louder than 92 whatever the stroke (55..127: the
ratio runs 0.7 at 120 to 1.4 at 55, so a ratio cannot describe it), and in those ten days no
bow note within 50 ms of an edge stroke was ever a chart note (174 of them, every one a stray;
the closest hi-hat figure any chart writes is 178 ms). It is one stroke heard twice, so it
falls now, at the cost of the real tap it overlaps (a double landing edge then bow within
50 ms at 95 or under, which no chart asks for).

The kick (2026-09-19, the Pop punk course): burying the beater on the KD pad bounces it back
onto the head, and the module sends a second kick nobody played: 36..60 ms after the stroke at
12..54 % of its velocity (most of them), a few up to 93 ms at 13..26 %, and a slower one
160..250 ms after (the beater settling on release) at 12..48 %. On an acoustic drum a buried
beater stays on the head and none of these would sound. Measured over every run log (5081 kick
strokes): no real kick ever came within 70 ms of another, the fastest kick figure in a chart is
94 ms apart (sixteenths at 160 bpm), and a real second kick within 250 ms is never under 60 %
of the first (a soft real & at 43 came 300 ms after a 103).

The snare (2026-09-22, "crosstalk in the snare": a hard stroke on the pad is heard a second
time 41..60 ms later at 30..60 % of it, on the same zone). The median delay is 51 ms on every
one of the thirteen days of the MIDI trace, and the median ratio 0.40..0.45: a pad retriggering,
not a hand. It comes with the hard strokes (that day: 2.5..3.3 % of the strokes at 80 and over,
none under 80) and it is growing (0.04 % of the day's snare strokes on 09-20, 1.78 % on 09-21,
2.65 % on 09-22, the highest in the trace), so the module's retrigger-cancel deserves a look
too. The rim (40) does the same to itself, 21..50 ms later at 0.5..0.6; head and rim do not
ring each other (in that day's 1106 snare notes, not one 38/40 pair within 200 ms), so each
zone keeps its own reference. Over the whole trace [(70, 0.6)] drops 367 strays and 81 notes
the judge had accepted, but 78 of those 81 are followed within 200 ms by a louder snare STRAY:
the echo had taken the chart note and the real stroke that followed was counted a stray, so
dropping it gives the note back (seen whole on the six stroke roll at 60 bpm: accent, echo 51 ms
later judged OK, the next real stroke 100 ms after that a STRAY). The fastest snare figure any
chart writes is 89 ms apart (16 - Pop punk anthem, written tempo), so 70 ms does not touch it.
"""
import time
from collections import deque

HIHAT_STICK_NOTES = {22, 26, 42, 46}   # bow and edge, closed and open
CHICK_NOTES = {44}
PEDAL_CC = 4

HIHAT_MIN_VELOCITY = 25      # softer hi-hat notes are ghosts (also covers the pre-chick ghosts)
CHICK_SPLASH_MS = 10         # hi-hat notes this soon after a chick are ghosts...
CHICK_SPLASH_VELOCITY_MIN = 100  # ...unless this loud: a stick landing with the chick reads 113..126
PEDAL_MOTION_CC = 20         # hi-hat notes are ghosts if the pedal moved at least this much...
PEDAL_MOTION_MS = 50         # ...within this many milliseconds before the note...
PEDAL_MOTION_VELOCITY_MIN = 50   # ...unless this loud: real strokes while opening read 116..127
PEDAL_SETTLE_MS = 250        # closed-hat notes (42/22) this long after a chick...
PEDAL_SETTLE_VELOCITY_MAX = 40   # ...at or below this velocity are the pedal settling (real: >= 56)
ANY_MIN_VELOCITY = 8         # below this nothing counts, on any pad
# A hard stroke on one zone makes the other zone fire late. Near: 8..48 ms after the stroke,
# never above 92 whatever the stroke's velocity (2026-09-19, 174 cases in ten days of trace,
# none a chart note): a note on the other zone within CROSSTALK_NEAR_MS at or under
# CROSSTALK_NEAR_VELOCITY_MAX is that stroke heard twice. Late: 73..111 ms after at 28..56 %
# (the hardest strokes, 113..127, ring the longest: 95..111 ms, 2026-09-20),
# at up to 0.70 (the run logs, 2026-09-20 evening: 51 escapes between 0.58 and 0.80, hat open or
# tight alike, one of them a chart note, at 0.71). The double the 2026-09-09 measurement kept
# (44..90 ms at 63..85 %) is now partly eaten; the user chose one note over two. Within 30 ms
# the other zone falls at any velocity: 8..25 ms at 0.7..1.5 x, one stroke read on both zones,
# nobody plays two hat strokes 30 ms apart. (window ms, max velocity ratio) tiers, in order.
CROSSTALK_ONE_STROKE_MS = 30    # two zones this close are one stroke read twice, whatever the velocities
CROSSTALK_NEAR_MS = 50
CROSSTALK_NEAR_VELOCITY_MAX = 95
ZONE_CROSSTALK = [(115, 0.70)]
# Beater bounce on the kick: (window ms, max velocity ratio to the last real kick) tiers, the
# reference stays the last real kick so a chain of bounces falls whole. 80 ms is under the
# fastest chart figure (94 ms); 0.4 within 250 ms keeps a soft real double (never under 0.6).
KICK_NOTES = {36}
KICK_BOUNCE = [(80, 0.6), (250, 0.4)]
# The snare pad heard twice: the same zone again 41..60 ms after a hard stroke at 0.30..0.60 of
# it (2026-09-22; the median delay is 51 ms on every day of the trace). Same shape as the kick's,
# (window ms, max velocity ratio to the last real stroke of that zone) tiers, the reference
# staying the last real stroke so a chain falls whole. 70 ms is under the fastest snare figure
# any chart writes (89 ms); 0.6 is above every echo measured and under the softest real stroke
# that follows one that close.
SNARE_NOTES = {38, 40}          # head and rim, each zone its own reference
SNARE_BOUNCE = [(70, 0.6)]

PEDAL_CLOSED_CC = 90         # fully closed on this pedal (0 = fully open)
TIGHT_MIN = 80               # closedness >= this -> tight
OPEN_MAX = 10                # closedness <= this -> open; between -> mid
EDGE_NOTES = {22, 26}
CLOSED_NOTES = {42, 22}      # what the module sends when its own threshold says closed


def openness_label(cc):
    if cc >= TIGHT_MIN:
        return "tight"
    if cc <= OPEN_MAX:
        return "open"
    return "mid"


class GhostFilter:
    """Feed every MIDI message; ask reason(note, velocity) for note-ons. Thread-safe enough
    for a single MIDI callback thread."""

    def __init__(self):
        self.cc_trail = deque()          # (t, value) samples within PEDAL_MOTION_MS
        self.last_chick_t = None
        self.filtered = 0
        self.last_reason = None
        self.pedal_cc = PEDAL_CLOSED_CC  # assume closed until the pedal speaks
        self.last_stroke = None          # (t, note, velocity, zone, openness) of the last real hi-hat stroke
        self.last_ghost = None           # (t, note, velocity, why)
        self.last_kick = None            # (t, velocity) of the last real kick
        self.last_snare = {}             # note -> (t, velocity) of the last real stroke on that zone

    def control_change(self, control, value, t=None):
        if control != PEDAL_CC:
            return
        t = time.perf_counter() if t is None else t
        self.pedal_cc = value
        self.cc_trail.append((t, value))

    def pedal_motion(self, t):
        cutoff = t - PEDAL_MOTION_MS / 1000
        while self.cc_trail and self.cc_trail[0][0] < cutoff:
            self.cc_trail.popleft()
        if len(self.cc_trail) < 2:
            return 0
        values = [v for _, v in self.cc_trail]
        return max(values) - min(values)

    def reason(self, note, velocity, t=None):
        """Why this note-on should be ignored, or None if it looks like a real stroke."""
        t = time.perf_counter() if t is None else t
        if note in CHICK_NOTES:
            self.last_chick_t = t
            return None
        if velocity < ANY_MIN_VELOCITY:
            return self._flag("too soft", t, note, velocity)
        if note in KICK_NOTES:
            lk = self.last_kick
            if lk:
                dt = (t - lk[0]) * 1000
                for window_ms, ratio in KICK_BOUNCE:
                    if dt <= window_ms:
                        if velocity <= ratio * lk[1]:
                            return self._flag("beater bounce", t, note, velocity)
                        break
            self.last_kick = (t, velocity)
            return None
        if note in SNARE_NOTES:
            ls = self.last_snare.get(note)
            if ls:
                dt = (t - ls[0]) * 1000
                for window_ms, ratio in SNARE_BOUNCE:
                    if dt <= window_ms:
                        if velocity <= ratio * ls[1]:
                            return self._flag("snare rebound", t, note, velocity)
                        break
            self.last_snare[note] = (t, velocity)
            return None
        if note not in HIHAT_STICK_NOTES:
            return None
        if velocity < HIHAT_MIN_VELOCITY:
            return self._flag("soft hi-hat", t, note, velocity)
        if self.last_chick_t is not None:
            since_chick = (t - self.last_chick_t) * 1000
            if since_chick <= CHICK_SPLASH_MS and velocity < CHICK_SPLASH_VELOCITY_MIN:
                return self._flag("chick splash", t, note, velocity)
            if (since_chick <= PEDAL_SETTLE_MS and velocity <= PEDAL_SETTLE_VELOCITY_MAX
                    and note in CLOSED_NOTES):
                return self._flag("pedal settling", t, note, velocity)
        if self.pedal_motion(t) >= PEDAL_MOTION_CC and velocity < PEDAL_MOTION_VELOCITY_MIN:
            return self._flag("pedal moving", t, note, velocity)
        zone = "edge" if note in EDGE_NOTES else "bow"
        ls = self.last_stroke
        if ls and ls[3] != zone:
            dt = (t - ls[0]) * 1000
            if dt <= CROSSTALK_ONE_STROKE_MS or (dt <= CROSSTALK_NEAR_MS and velocity <= CROSSTALK_NEAR_VELOCITY_MAX):
                return self._flag("zone crosstalk", t, note, velocity)
            for window_ms, ratio in ZONE_CROSSTALK:
                if dt <= window_ms:
                    if velocity <= ratio * ls[2]:
                        return self._flag("zone crosstalk", t, note, velocity)
                    break
        self.last_stroke = (t, note, velocity, zone, openness_label(self.pedal_cc))
        return None

    def articulation_for(self, note):
        """hhmapper's label for the stroke just accepted: 'tight edge', 'open body', 'pedal chick'...
        None for other pads."""
        if note in CHICK_NOTES:
            return "pedal chick"
        ls = self.last_stroke
        if ls and ls[1] == note:
            return f"{ls[4]} {'edge' if ls[3] == 'edge' else 'body'}"
        return None

    def _flag(self, why, t=None, note=None, velocity=None):
        self.filtered += 1
        self.last_reason = why
        self.last_ghost = (time.perf_counter() if t is None else t, note, velocity, why)
        return why
