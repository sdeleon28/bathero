"""Charts: the notes to play, the built-in levels and courses, and the lane layout."""
import re
import dataclasses
from dataclasses import dataclass, field


# --- the kit -------------------------------------------------------------------------
# A zone is one strikeable part of the TD-17 (snare head, snare rim, ride bell, ...).
# The wizard asks for every zone in this order, so the saved kit knows the whole kit even
# though lessons only use a few instruments so far. Head/bow zones come before rim/edge
# zones on purpose: a note heard in two steps goes to the later one.
@dataclass(frozen=True)
class Zone:
    key: str
    label: str          # "Snare rim"
    pad: str            # the physical pad: "Snare"
    part: str           # "head" / "rim" / "bow" / "edge" / "bell" / "pedal"
    instrument: str     # chart key these hits count for
    prompt: str         # wizard instruction
    defaults: tuple     # General MIDI / TD-17 factory note numbers


ZONES = [
    Zone("kick", "Kick", "Kick", "", "kick", "Hit the KICK a few times", (36, 35)),
    Zone("snare", "Snare head", "Snare", "head", "snare", "Hit the SNARE head a few times", (38,)),
    Zone("snare_rim", "Snare rim", "Snare", "rim", "snare", "Hit the SNARE RIM: rimshots and cross-stick", (40, 37)),
    Zone("hihat", "Hi-hat bow", "Hi-hat", "bow", "hihat", "Hit the HI-HAT on top (bow), pedal up and down", (42, 46)),
    Zone("hihat_edge", "Hi-hat edge", "Hi-hat", "edge", "hihat", "Hit the HI-HAT EDGE, pedal up and down", (22, 26)),
    Zone("hihat_pedal", "Hi-hat pedal", "Hi-hat", "pedal", "pedal", "Stomp the HI-HAT PEDAL a few times (chick)", (44,)),
    Zone("crash", "Crash L bow", "Crash L", "bow", "crash", "Hit the LEFT CRASH on the bow", (49,)),
    Zone("crash_edge", "Crash L edge", "Crash L", "edge", "crash", "Hit the LEFT CRASH on the edge", (55,)),
    Zone("crash2", "Crash R bow", "Crash R", "bow", "crash2", "Hit the RIGHT CRASH on the bow", (57,)),
    Zone("crash2_edge", "Crash R edge", "Crash R", "edge", "crash2", "Hit the RIGHT CRASH on the edge", (52,)),
    Zone("tom1", "Rack tom head", "Rack tom", "head", "tom1", "Hit the RACK TOM head", (48,)),
    Zone("tom1_rim", "Rack tom rim", "Rack tom", "rim", "tom1", "Hit the RACK TOM RIM", (50,)),
    Zone("floor", "Floor tom head", "Floor tom", "head", "floor", "Hit the FLOOR TOM head", (43, 45)),
    Zone("floor_rim", "Floor tom rim", "Floor tom", "rim", "floor", "Hit the FLOOR TOM RIM", (58, 47)),
    Zone("ride", "Ride bow", "Ride", "bow", "ride", "Hit the RIDE on the bow", (51,)),
    Zone("ride_edge", "Ride edge", "Ride", "edge", "ride", "Hit the RIDE on the edge", (59,)),
    Zone("ride_bell", "Ride bell", "Ride", "bell", "ride", "Hit the RIDE BELL", (53,)),
]
ZONE = {z.key: z for z in ZONES}
ZONE_KEYS = [z.key for z in ZONES]
PADS = []                       # [(pad name, [zone keys]), ...] in wizard order
for _z in ZONES:
    if not PADS or PADS[-1][0] != _z.pad:
        PADS.append((_z.pad, []))
    PADS[-1][1].append(_z.key)

# Instruments: what charts refer to. The first four also drive the menus.
INSTRUMENTS = ["kick", "snare", "hihat", "crash", "tom1", "floor", "ride", "crash2", "pedal"]
LABELS = {"kick": "Kick", "snare": "Snare", "hihat": "Hi-Hat", "crash": "Crash L",
          "tom1": "Rack tom", "floor": "Floor tom", "ride": "Ride", "crash2": "Crash R", "pedal": "HH pedal"}
COLORS = {
    "kick": (245, 90, 90),
    "snare": (250, 170, 60),
    "hihat": (245, 230, 80),
    "crash": (110, 220, 110),
    "tom1": (80, 200, 230),
    "floor": (100, 130, 250),
    "ride": (190, 110, 240),
    "crash2": (60, 190, 150),
    "pedal": (220, 200, 120),
}
# Hi-hat articulations, named exactly as hhmapper labels them (and GetGood Drums plays them):
# openness from the pedal (CC4) x zone, plus the foot.
HH_ARTS = ["tight body", "tight edge", "mid body", "mid edge", "open body", "open edge", "pedal chick"]
HH_GLYPH = {"tight body": "+", "tight edge": ">+", "mid body": "/", "mid edge": ">/", "open body": "o", "open edge": ">o",
            "pedal chick": "^"}


def art_matches(want: str, played: str) -> bool:
    """Whether a played hi-hat articulation satisfies the chart's. The pedal position is what
    is judged (tight / mid / open, or the chick): the zone is the chart's suggestion, a stroke
    on the bow where the edge was written, or the other way round, is still right (asked
    2026-09-17: the ladder writes edge on the beats and bow on the &s, and either is fine)."""
    if not want or not played:
        return False
    return want == played or (want.split()[0] == played.split()[0] and "pedal" not in want)


def art_goal(want: str) -> str:
    """What art_matches actually asks of a chart articulation, for the feedback: 'tight' /
    'mid' / 'open' (either zone) or 'chick'."""
    return "chick" if "pedal" in want else want.split()[0]


# Charts that use one crash accept either crash pad; only charts with both lanes tell them apart.
CRASH_PAIR = {"crash": "crash2", "crash2": "crash"}
INSTRUMENT_ZONES = {inst: [z.key for z in ZONES if z.instrument == inst] for inst in INSTRUMENTS}
# Fallback kit, General MIDI / TD-17 factory numbers, used until the wizard has run.
DEFAULT_KIT = {z.key: list(z.defaults) for z in ZONES}


def kit_notes(kit: dict, instrument: str):
    """Every input note number of an instrument across its zones."""
    out = set()
    for zk in INSTRUMENT_ZONES.get(instrument, []):
        out.update(kit.get(zk, []))
    return sorted(out)


# Notes that come from the same zone depending on pedal position. When the kit wizard
# hears one of them, it assigns the pair, so a hi-hat captured with the pedal up still
# counts when it is closed.
NOTE_FAMILIES = [
    {42, 46},    # hi-hat bow: closed / open
    {22, 26},    # hi-hat edge: closed / open
]


def expand_family(notes):
    out = set(notes)
    for fam in NOTE_FAMILIES:
        if out & fam:
            out |= fam
    return sorted(out)


# Chart notes from MIDI files are folded into instruments when they are the GM drum numbers.
GM_TO_INSTRUMENT = {35: "kick", 36: "kick", 37: "snare", 38: "snare", 40: "snare",
                    22: "hihat", 26: "hihat", 42: "hihat", 44: "pedal", 46: "hihat",
                    49: "crash", 55: "crash", 52: "crash2", 57: "crash2",
                    47: "tom1", 48: "tom1", 50: "tom1", 41: "floor", 43: "floor", 45: "floor", 58: "floor",
                    51: "ride", 53: "ride", 59: "ride"}
GM_DRUM_NAMES = {
    37: "Side Stick", 39: "Clap", 41: "Floor Tom 2", 43: "Floor Tom", 45: "Low Tom", 47: "Mid Tom",
    48: "High Tom 2", 50: "High Tom", 51: "Ride", 52: "China", 53: "Ride Bell", 54: "Tambourine",
    55: "Splash", 56: "Cowbell", 58: "Vibraslap", 59: "Ride 2",
}
EXTRA_PALETTE = [(240, 120, 190), (140, 200, 160), (200, 200, 200), (230, 200, 120)]


@dataclass
class ChartNote:
    t: float                # seconds from chart start
    key: str                # "kick" / "snare" / "hihat" / "crash" or "n<midi number>"
    velocity: int
    lane: int = -1
    state: str = "pending"  # pending / hit / miss
    judge: str = None       # PERFECT / GOOD / OK / MISS
    error_ms: float = None  # hit time - note time (negative = early)
    sounded: bool = False   # guide sound already played
    hand: str = None        # "R" / "L" sticking hint for rudiments, shown on the note
    strip: int = None       # index of this stroke in the chart's sticking strip (lit when played)
    accent: bool = False    # an accented stroke (judged when the chart has dynamics)
    hit_velocity: int = None
    dyn: str = None         # ACCENT / TAP (right) or SOFT / LOUD (wrong), set when hit
    art: str = None         # required hi-hat articulation (HH_ARTS), judged when the chart has expression
    art_ok: bool = None     # set when hit: the stroke's articulation matched
    played: str = None      # the articulation actually played


@dataclass
class Lane:
    index: int
    key: str
    label: str
    color: tuple
    notes: set = field(default_factory=set)   # input note numbers that hit this lane


# Counting syllables per subdivision; the first one is replaced by the beat number.
COUNT_LABELS = {1: ["1"], 2: ["1", "&"], 3: ["1", "&", "a"], 4: ["1", "e", "&", "a"], 6: ["1", "2", "3", "4", "5", "6"]}
GRIDS = [1, 2, 3, 4]            # subdivisions per beat we recognise, coarsest first
GRID_TOLERANCE = 0.12           # of a grid step
GRID_COVERAGE = 0.95            # fraction of onsets that must sit on the grid
PHRASE_BARS = 4                 # subdivision is decided per phrase of this many bars
PHRASE_KEYS = 10                # transport: the number keys 1..9 and 0, one phrase each


@dataclass
class Chart:
    name: str
    notes: list
    bpm: float = 120.0
    desc: str = ""
    segments: list = None       # [(start_bar, subdivision), ...] sorted; inferred when None.
                                # start_bar may be fractional (0.5 = beat 3) for mixed rudiments
    beats: list = None          # beat times in chart seconds when the tempo is not constant; None = bpm
    sticking: list = None       # ["R", "L", ...] pattern shown as a strip (rudiments)
    accents: set = None         # indices within the sticking pattern that are accented
    sticking_groups: list = None  # indices where a new cell of the pattern starts (strip separators)
    dynamics: bool = False      # judge accents vs taps by velocity
    expression: bool = False    # judge hi-hat articulations (openness, zone, chick)
    rate: float = 1.0           # tempo multiplier this chart was scaled by (see at_rate)
    lead: str = None            # "R" / "L": the side this version is played on (the hand that leads, or the
                                # crash that is washed, see mirror); None when the level has one version only
                                # (one instrument per hand, feet, hi-hat lessons). Set by the builders.
    mirror: str = "hands"       # what the other version swaps: "hands" (every R and L: notes, strip, the
                                # description's R / L tokens) or "crash" (the two crashes, "left" / "right")
    home: str = "R"             # the side the level was written on: that version's key is the bare name,
                                # the other's carries " (L)" / " (R)"
    backing: str = None         # a backing of its own ("cumbia": sounds.CUMBIA_STYLE, "keygen": the waiting screen's tune, "kick": sounds.KICK_STYLE on kick_rhythm()); None = by subdivision
    hammer: bool = False        # the music's bass and chords play the level's kick figure, bar by bar (kick_rhythm()); "kick" backing implies it

    @property
    def key(self):
        """The progress / run-log name: the level name as written, plus " (L)" / " (R)" for the
        version on the other side (the left-hand lead, the wash on the right crash)."""
        return self.name + (f" ({self.lead})" if self.lead and self.lead != self.home else "")

    @property
    def title(self):
        if not self.lead or self.lead == self.home:
            return self.name
        return self.name + "  ·  " + ("left hand lead" if self.mirror == "hands" else self.side_text(sep=" "))

    def side_text(self, lead=None, sep="  "):
        """What a side means on this level, for the list and the card: "lead hand  right" or
        "wash on the  left crash"."""
        side = "right" if (lead or self.lead) == "R" else "left"
        return f"lead hand{sep}{side}" if self.mirror == "hands" else f"wash on the{sep}{side} crash"

    def mirrored(self):
        """The same level on the other side. mirror "hands": led by the other hand, every R becomes
        L and vice versa, in the notes, the sticking strip and the description's R / L tokens.
        mirror "crash": the two crashes swap, in the notes and the description's "left" / "right".
        Same name, other key."""
        if not self.lead:
            return self
        swap = {"R": "L", "L": "R"}
        if self.mirror == "crash":
            crashes = {"crash": "crash2", "crash2": "crash"}
            notes = [dataclasses.replace(n, key=crashes.get(n.key, n.key)) for n in self.notes]
            desc = re.sub(r"\b(left|right)\b", lambda m: {"left": "right", "right": "left"}[m.group(0)], self.desc)
            return dataclasses.replace(self, notes=notes, desc=desc, lead=swap[self.lead])
        notes = [dataclasses.replace(n, hand=swap.get(n.hand, n.hand)) for n in self.notes]
        desc = re.sub(r"\b[RL]\b", lambda m: swap[m.group(0)], self.desc)
        return dataclasses.replace(self, notes=notes, desc=desc, lead=swap[self.lead],
                                   sticking=[swap.get(h, h) for h in self.sticking] if self.sticking else None)

    def at_rate(self, rate: float):
        """A copy of this chart played at `rate` times the tempo: note times, beat grid and
        audio offset stretched, bpm scaled. rate 0.5 = half speed."""
        rate = float(rate)
        if rate == 1.0:
            return self
        notes = [dataclasses.replace(n, t=n.t / rate) for n in self.notes]
        ch = dataclasses.replace(self, notes=notes, bpm=self.bpm * rate, rate=rate,
                                 beats=[b / rate for b in self.beats] if self.beats else None)
        return ch

    @property
    def length(self):
        return self.notes[-1].t if self.notes else 0.0

    def doubles(self):
        """The doubles: runs of two or three consecutive strokes of one hand (or foot) on one
        instrument, each within a beat of the last, as (first index, last index) into notes.
        The highway brackets them on the hand's side. Notes without a hand are not strokes and
        are skipped over; a longer run is a one-hand exercise, not a double, and gets nothing."""
        out, run = [], []
        beat = 60 / self.bpm

        def flush():
            if 2 <= len(run) <= 3:
                out.append((run[0], run[-1]))

        for i, n in enumerate(self.notes):
            if not n.hand:
                continue
            if run:
                p = self.notes[run[-1]]
                if p.hand != n.hand or p.key != n.key or n.t - p.t > beat * 1.01:
                    flush()
                    run = []
            run.append(i)
        flush()
        return out

    @property
    def beat(self):
        return 60 / self.bpm

    @property
    def bars(self):
        return int(self.beat_pos(self.length) // 4) + 1

    # --- beat grid: constant tempo, or the song's own beat times ------------------------
    def beat_pos(self, t: float) -> float:
        """Chart time -> fractional beat index (0 = first downbeat). Extrapolates outside."""
        b = self.beats
        if not b or len(b) < 2:
            return t / self.beat
        if t <= b[0]:
            return (t - b[0]) / (b[1] - b[0])
        if t >= b[-1]:
            return (len(b) - 1) + (t - b[-1]) / (b[-1] - b[-2])
        lo, hi = 0, len(b) - 1
        while hi - lo > 1:
            mid = (lo + hi) // 2
            if b[mid] <= t:
                lo = mid
            else:
                hi = mid
        return lo + (t - b[lo]) / (b[hi] - b[lo])

    def beat_time(self, i: float) -> float:
        """Fractional beat index -> chart time. Inverse of beat_pos."""
        b = self.beats
        if not b or len(b) < 2:
            return i * self.beat
        if i <= 0:
            return b[0] + i * (b[1] - b[0])
        if i >= len(b) - 1:
            return b[-1] + (i - (len(b) - 1)) * (b[-1] - b[-2])
        k = int(i)
        return b[k] + (i - k) * (b[k + 1] - b[k])

    def phrase_bounds(self, count=PHRASE_KEYS):
        """The transport's markers: the level cut into at most `count` phrases of whole bars,
        plus the end, so phrase i (0-based) is [out[i], out[i + 1]) and there are
        len(out) - 1 of them, one per number key from 1. Phrases are equal whenever the
        level allows it: a level of up to ten bars has one bar per key (8 bars, keys 1..8),
        a longer one takes the largest count in 10..5 that divides it exactly (16 bars are 8
        of 2, 12 are 6 of 2, 120 are 10 of 12), and only a length nothing divides (83 bars)
        gets ten of nearly equal size. Keys past the last phrase do nothing."""
        cached = getattr(self, "_phrase_cache", None)
        if cached is not None and cached[0] == count:
            return cached[1]
        bars = max(1, self.bars)
        if bars <= count:
            n = bars
        else:
            n = next((k for k in range(count, 4, -1) if bars % k == 0), count)
        marks = sorted({round(i * bars / n) for i in range(n)})
        out = [self.beat_time(b * 4) for b in marks] + [self.beat_time(bars * 4)]
        self._phrase_cache = (count, out)
        return out

    def phrase_at(self, t: float, count=PHRASE_KEYS):
        """Index of the phrase holding chart time t (the first one during the count-in);
        None past the end of the level."""
        bounds = self.phrase_bounds(count)
        if t >= bounds[-1]:
            return None
        for i in range(len(bounds) - 1, 0, -1):
            if t >= bounds[i - 1]:
                return i - 1
        return 0

    def place(self, t: float) -> str:
        """'bar 3' or 'bar 3 beat 3': where chart time t falls, 1-based, for the transport."""
        pos = self.beat_pos(max(t, 0.0)) + 1e-6          # beat_time and beat_pos round-trip a hair short
        bar, beat = int(pos // 4) + 1, int(pos % 4) + 1
        return f"bar {bar}" if beat == 1 else f"bar {bar} beat {beat}"

    def segment_list(self):
        if self.segments is None:
            self.segments = infer_segments(self.notes, self.bpm, beat_pos=self.beat_pos)
        return self.segments

    def kick_rhythm(self):
        """The kick figure for the music to hammer (backing "kick", or `hammer`): (grid, bars),
        grid 16 (sixteenths) or 12 (twelfths, for a triplet level), bars a list with one entry per
        bar of the level, [(slot, gain)] with the beats at 1.0 and the rest at 0.8, so a figure
        that changes from bar to bar (the pop punk push every second bar) is played as written.
        A bar without kicks repeats the last bar that had them. sounds.make_arrangement takes it
        as `rhythm`."""
        sub = self.subdivision_at(0)
        grid = 12 if sub % 3 == 0 else 16
        per = grid // 4
        n_bars = max(1, int(round(self.length / (4 * self.beat))))
        by_bar = [set() for _ in range(n_bars)]
        for n in self.notes:
            if n.key != "kick":
                continue
            slot = int(round(n.t / self.beat * per))
            b, s = divmod(slot, grid)
            if 0 <= b < n_bars:
                by_bar[b].add(s)
        bars, last = [], None
        for slots in by_bar:
            if not slots and last is not None:
                slots = last
            bars.append([(s, 1.0 if s % per == 0 else 0.8) for s in sorted(slots)])
            last = slots or last
        return grid, bars

    def subdivision_at(self, t: float) -> int:
        """Subdivisions per beat for chart time t (negative t = count-in uses the first).
        Segments may start on a fraction of a bar, so this can change from beat to beat."""
        pos = self.beat_pos(t) / 4 + 1e-6       # bars; the epsilon absorbs beat_time rounding
        sub = self.segment_list()[0][1]
        for start, n in self.segment_list():
            if start <= pos:
                sub = n
            else:
                break
        return sub


def _grid_fits(positions, n):
    """positions: onsets in beats within a phrase. True if nearly all sit on the 1/n grid."""
    if not positions:
        return True
    ok = 0
    for x in positions:
        k = round(x * n)
        if abs(x * n - k) <= GRID_TOLERANCE:
            ok += 1
    return ok / len(positions) >= GRID_COVERAGE


def infer_segments(notes, bpm, phrase_bars=PHRASE_BARS, beat_pos=None):
    """Per phrase, the coarsest grid that covers the onsets. Phrases without notes keep
    the previous subdivision. Consecutive equal phrases merge into one segment.
    beat_pos: chart time -> beat index; default assumes a constant bpm."""
    if beat_pos is None:
        beat_pos = lambda t: t / (60 / bpm)
    if not notes:
        return [(0, 1)]
    phrase_beats = phrase_bars * 4
    positions = [beat_pos(n.t) for n in notes]
    phrases = int(max(positions) / phrase_beats) + 1
    buckets = [[] for _ in range(phrases)]
    for pos in positions:
        i = min(phrases - 1, max(0, int(pos / phrase_beats)))
        buckets[i].append(pos)
    segments, current = [], None
    for i, pos in enumerate(buckets):
        if pos:
            sub = next((g for g in GRIDS if _grid_fits(pos, g)), GRIDS[-1])
        else:
            sub = current if current is not None else 1
        if sub != current:
            segments.append((i * phrase_bars, sub))
            current = sub
    return segments


# ---------------------------------------------------------------------------
# Built-in levels. A pattern is a function bar -> [(beat, key, velocity), ...]
# with beat counted from 0 within a 4/4 bar.
# ---------------------------------------------------------------------------
def _build(name, desc, bpm, bars, pattern, subdivision=None) -> Chart:
    """subdivision: force the metronome grid (per beat); None = infer from the notes."""
    beat = 60 / bpm
    notes = []
    for bar in range(bars):
        for b, key, vel in pattern(bar):
            notes.append(ChartNote((bar * 4 + b) * beat, key, vel))
    notes.sort(key=lambda n: (n.t, n.key))
    return Chart(name, notes, bpm, desc, [(0, subdivision)] if subdivision else None)


def _kicks_on_beats(bar):
    return [(b, "kick", 110) for b in range(4)]


def _snare_2_4(bar):
    return [(1, "snare", 115), (3, "snare", 115)]


def _kick_snare(bar):
    return [(0, "kick", 110), (2, "kick", 110), (1, "snare", 115), (3, "snare", 115)]


def _hats_quarters(bar):
    return [(b, "hihat", 85) for b in range(4)]


def _basic_beat(bar):
    return _kick_snare(bar) + _hats_quarters(bar)


def _eighth_beat(bar):
    return _kick_snare(bar) + [(e / 2, "hihat", 85 if e % 2 == 0 else 70) for e in range(8)]


def _crash_beat(bar):
    out = _eighth_beat(bar)
    if bar % 4 == 0:
        out.append((0, "crash", 120))
    return out


def _rock_beat(bar):
    out = _crash_beat(bar)
    if bar % 4 == 3:
        out.append((2.5, "kick", 100))
    if bar % 8 == 7:
        out.append((3.5, "snare", 90))
    return out


def _snares_on_beats(bar):
    return [(b, "snare", 110) for b in range(4)]


def _kick_snare_alternating(bar):
    return [(0, "kick", 110), (1, "snare", 110), (2, "kick", 110), (3, "snare", 110)]


def _hats_eighths(bar):
    return [(e / 2, "hihat", 85 if e % 2 == 0 else 70) for e in range(8)]


def _crash_quarters(bar):
    return [(b, "crash", 110) for b in range(4)]


def _rudiment(name, desc, bpm, bars, sticking, sub, accents=(0,), lanes=None, backing=None):
    """A practice-pad rudiment: `sticking` repeats over the bar at `sub` notes per beat.
    accents: indices within the sticking pattern that are accented; the chart then judges
    dynamics (accent vs tap velocity). lanes: optional map hand -> instrument key
    (default: everything on the snare). backing: see Chart.backing."""
    beat = 60 / bpm
    lanes = lanes or {"R": "snare", "L": "snare"}
    notes = []
    per_bar = 4 * sub
    for bar in range(bars):
        for i in range(per_bar):
            hand = sticking[i % len(sticking)]
            accent = (i % len(sticking)) in accents
            notes.append(ChartNote((bar * 4 + i / sub) * beat, lanes[hand], ACCENT_VELOCITY if accent else TAP_VELOCITY,
                                   hand=hand, accent=accent, strip=i % len(sticking)))
    ch = Chart(name, notes, bpm, desc, [(0, sub)])
    ch.sticking = list(sticking)
    ch.accents = set(accents)
    ch.dynamics = bool(accents)
    ch.lead = _lead_of(sticking, lanes)
    ch.backing = backing
    return ch


def _lead_of(sticking, lanes):
    """"R" when the level can be played led by either hand: both hands appear and land on the
    same instrument. A hands-on-different-drums level has one version only."""
    return "R" if {"R", "L"} <= set(sticking) and len(set(lanes.values())) == 1 else None


def _swap(sticking):
    return sticking.translate(str.maketrans("RL", "LR"))


def _reversed(cells):
    return [(_swap(s), sub, acc) for s, sub, acc in cells]


def _rudiment_mix(name, desc, bpm, bars, phrase, lanes=None, backing=None):
    """A rudiment whose beats can differ in subdivision. phrase: cells (sticking, sub,
    accents), each lasting len(sticking) / sub beats, repeated to `bars`. The chart's
    segments carry the subdivision per cell (fractional bar starts), so the count panel,
    the congas and the sticking strip follow every switch. backing: see Chart.backing."""
    beat = 60 / bpm
    lanes = lanes or {"R": "snare", "L": "snare"}
    phrase_beats = sum(len(s) / sub for s, sub, _ in phrase)
    assert abs(phrase_beats - round(phrase_beats)) < 1e-9 and (4 * bars) % round(phrase_beats) == 0, (name, phrase_beats)
    notes, segments, sticking, accents, groups = [], [], [], set(), []
    pos = 0.0                                   # in beats
    for rep_i in range(int(4 * bars // round(phrase_beats))):
        for j, (s, sub, acc) in enumerate(phrase):
            if rep_i == 0:
                groups.append(len(sticking))
                accents |= {len(sticking) + a for a in acc}
                sticking += list(s)
            base = groups[j]                        # this cell's first index in the strip
            if not segments or segments[-1][1] != sub:
                segments.append((pos / 4, sub))
            for i, hand in enumerate(s):
                accent = i in acc
                notes.append(ChartNote((pos + i / sub) * beat, lanes[hand], ACCENT_VELOCITY if accent else TAP_VELOCITY,
                                       hand=hand, accent=accent, strip=base + i))
            pos += len(s) / sub
    ch = Chart(name, notes, bpm, desc, segments)
    ch.sticking, ch.accents, ch.dynamics = sticking, accents, True
    ch.sticking_groups = groups[1:]
    ch.lead = _lead_of(sticking, lanes)
    ch.backing = backing
    return ch


# Chart velocities for accented and unaccented strokes; also what the guide plays.
ACCENT_VELOCITY = 115
TAP_VELOCITY = 70


# cells for the mixed rudiments: (sticking, subdivision, accented indices)
_SINGLES = ("RLRL", 4, (0,))
_PD, _PD_L = ("RLRR", 4, (0,)), ("LRLL", 4, (0,))
_SSR, _SSR_L = ("RLLRRL", 6, (0, 5)), ("LRRLLR", 6, (0, 5))
_DP, _DP_L = ("RLRLRR", 6, (0,)), ("LRLRLL", 6, (0,))
_PDD = ("RLRRLL", 6, (0,))
_PPSP = [_PD, _PD_L, _SSR, _PD]           # paradiddle, paradiddle, six stroke roll, paradiddle

RUDIMENTS = [
    _rudiment("Single strokes 8ths", "Alternate hands on the eighths.", 80, 8, "RL", 2),
    _rudiment("Single strokes 16ths", "Alternate hands on the sixteenths, accent on the beat.", 70, 8, "RLRL", 4),
    _rudiment("Paradiddle", "R L R R  L R L L, accent on the first of each group.", 70, 8, "RLRRLRLL", 4, accents=(0, 4)),
    _rudiment("Paradiddle hat / snare", "Right hand on the hi-hat, left on the snare.", 75, 8, "RLRRLRLL", 4, accents=(0, 4),
              lanes={"R": "hihat", "L": "snare"}),
    _rudiment("Triplets", "Eighth-note triplets, alternating, accent on the beat.", 70, 8, "RLRLRL", 3, accents=(0, 3)),
    _rudiment("Triplets R L L", "R L L on every beat: the shuffle hand pattern.", 70, 8, "RLL", 3),
    _rudiment("Double paradiddle", "R L R L R R  L R L R L L in triplets.", 70, 8, "RLRLRRLRLRLL", 3, accents=(0, 6)),
    _rudiment("Paradiddle-diddle", "R L R R L L in triplets, accent on the first.", 75, 8, "RLRRLL", 3, accents=(0,)),
    # six stroke roll: R L L R R L, the two singles accented, the doubles soft
    _rudiment("Six stroke roll in triplets", "R L L R R L over two beats of triplets: accent the singles, keep the doubles soft.",
              70, 8, "RLLRRL", 3, accents=(0, 5)),
    _rudiment("Six stroke roll", "The same six strokes inside one beat: a sextuplet, accents on the first and the last.",
              60, 8, "RLLRRL", 6, accents=(0, 5), backing="keygen"),
    _rudiment("Six stroke roll R L R R L L", "Singles first: the two accents land together, then the two doubles.",
              60, 8, "RLRRLL", 6, accents=(0, 1), backing="keygen"),
    # combinations: the six stroke roll inside sixteenth flow or next to other sextuplet rudiments.
    # Tempo is one number each; the game's rate control is the speed ladder.
    _rudiment_mix("Sixteenths + six stroke roll", "Single strokes on the sixteenths, a six stroke roll as a sextuplet on beat 4.",
                  60, 8, [_SINGLES, _SINGLES, _SINGLES, _SSR]),
    _rudiment_mix("Paradiddle + six stroke roll", "Paradiddle on the sixteenths, six stroke roll as a sextuplet, alternating beats and hands: the subdivision switches on every beat.",
                  60, 8, [_PD, _SSR_L, _PD_L, _SSR]),
    _rudiment_mix("2 paradiddles, six stroke, 1 more", "Two paradiddles, a six stroke roll as a sextuplet, one more paradiddle: the bar ends on the right, so the next one starts on the left and the whole thing plays reversed.",
                  60, 8, _PPSP + _reversed(_PPSP)),
    _rudiment_mix("Double paradiddle + six stroke", "All sextuplets: a double paradiddle, then a six stroke roll, alternating beats and hands.",
                  60, 8, [_DP, _SSR_L, _DP_L, _SSR], backing="keygen"),
    _rudiment_mix("Six stroke + paradiddle-diddle", "All sextuplets: the same six strokes with the doubles in two different places, one beat each.",
                  60, 8, [_SSR, _PDD, _SSR, _PDD], backing="keygen"),
    _rudiment("Doubles 16ths", "R R L L on the sixteenths.", 70, 8, "RRLL", 4, accents=(0,)),
    # accent control: same hands, the accent walks through the sixteenth
    _rudiment("Accent on 1", "Sixteenths, accent on the beat, taps in between.", 70, 8, "RLRL", 4, accents=(0,)),
    _rudiment("Accent on e", "Sixteenths, accent on the e.", 70, 8, "RLRL", 4, accents=(1,)),
    _rudiment("Accent on &", "Sixteenths, accent on the &.", 70, 8, "RLRL", 4, accents=(2,)),
    _rudiment("Accent on a", "Sixteenths, accent on the a.", 70, 8, "RLRL", 4, accents=(3,)),
    _rudiment("Moving accent", "The accent walks: 1, then e, then &, then a, one per beat.", 70, 8,
              "RLRLRLRLRLRLRLRL", 4, accents=(0, 5, 10, 15)),
]


# Exercises: one or two drums, slow. Beats: full grooves.
EXERCISES = [
    _build("Kick on the beat", "Kick on every beat. Get a feel for the line.", 70, 8, _kicks_on_beats),
    _build("Snare on the beat", "Snare on every beat.", 70, 8, _snares_on_beats),
    _build("Snare on 2 and 4", "Only the backbeat.", 70, 8, _snare_2_4),
    _build("Hi-hat on the beat", "Hi-hat on every beat.", 80, 8, _hats_quarters),
    _build("Crash on the beat", "Crash on every beat, let it ring.", 70, 4, _crash_quarters),
    _build("Kick and snare", "Kick on 1 and 3, snare on 2 and 4.", 80, 8, _kick_snare),
    _build("Alternating", "Kick, snare, kick, snare.", 85, 8, _kick_snare_alternating),
    _build("Hi-hat eighths", "Hi-hat on every eighth note.", 90, 8, _hats_eighths),
]
# --- grooves: a small notation --------------------------------------------------------
# One string per instrument per bar, sixteen slots (sixteenths): "x" a stroke, "X" an
# accent, "o" a ghost, "." nothing. Bars are dicts; a level is a list of bars, its
# phrase, repeated to the level length.
GROOVE_KEYS = {"hh": "hihat", "kk": "kick", "sn": "snare", "t1": "tom1", "ft": "floor",
               "rd": "ride", "cl": "crash", "cr": "crash2", "pd": "pedal"}
GROOVE_VEL = {"X": 120, "x": 96, "o": 62}      # only X draws as an accent
# In the "hh" string these letters ask for an articulation (bow taps soft, edge strokes hard);
# "x"/"X"/"o" stay "any hi-hat". In "pd", "x" is a chick.
HH_LETTERS = {"t": "tight body", "T": "tight edge", "m": "mid body", "M": "mid edge", "a": "open body", "A": "open edge"}


def _groove(name, desc, bpm, phrase, bars=8, backing=None, hammer=False, mirror=None):
    """A groove level from `phrase` (list of bar dicts, see GROOVE_KEYS) repeated to `bars`.
    backing: a style of its own for the music (sounds.make_arrangement's feel), else generic.
    hammer: the music's bass and chords play the level's kick figure (Chart.hammer).
    mirror "crash": the level exists on both crashes (Chart.mirrored swaps them); the side written
    is the crash with more strokes, the lead toggle picks the other."""
    beat = 60 / bpm
    notes = []
    for bar in range(bars):
        for k, pat in phrase[bar % len(phrase)].items():
            key = GROOVE_KEYS[k]
            assert len(pat) == 16, (name, k, pat)
            for i, c in enumerate(pat):
                t = (bar * 4 + i / 4) * beat
                if k == "pd" and c in GROOVE_VEL:
                    notes.append(ChartNote(t, "pedal", GROOVE_VEL[c], art="pedal chick"))
                elif k == "hh" and c in HH_LETTERS:
                    art = HH_LETTERS[c]
                    notes.append(ChartNote(t, "hihat", 110 if "edge" in art else 88, accent="edge" in art, art=art))
                elif c in GROOVE_VEL:
                    notes.append(ChartNote(t, key, GROOVE_VEL[c], accent=(c == "X")))
    notes.sort(key=lambda n: (n.t, INSTRUMENTS.index(n.key)))
    ch = Chart(name, notes, bpm, desc)
    ch.expression = any(n.art for n in notes)
    ch.backing = backing
    ch.hammer = hammer
    if mirror == "crash":
        crashes = [n.key for n in notes if n.key in ("crash", "crash2")]
        ch.mirror = "crash"
        ch.home = ch.lead = "R" if crashes.count("crash2") > crashes.count("crash") else "L"
    return ch


_H8 = "x.x.x.x.x.x.x.x."          # hats on the eighths
_H4 = "x...x...x...x..."          # hats on the quarters
_H16 = "xxxxxxxxxxxxxxxx"         # hats on the sixteenths
_S24 = "....X.......X..."         # backbeat

# The curriculum: every level keeps what the one before taught and adds one idea.
BEATS = [
    _groove("1 · Money beat", "Kick on 1 and 3, snare on 2 and 4, hats on the quarters.", 85, [
        {"hh": _H4, "kk": "x.......x.......", "sn": _S24},
    ]),
    _groove("2 · Eighth-note hats", "The same beat with the hats on every eighth: the right hand keeps time.", 90, [
        {"hh": _H8, "kk": "x.......x.......", "sn": _S24},
    ]),
    _groove("3 · Kick on the &", "A second kick on the & of 3, then on the & of 1 in the answering bar.", 90, [
        {"hh": _H8, "kk": "x.......x.x.....", "sn": _S24},
        {"hh": _H8, "kk": "x.x.....x.......", "sn": _S24},
    ]),
    _groove("4 · Four-bar phrase", "Three bars of groove, then a bar with a snare pickup on the & of 4: the phrase has a shape.", 92, [
        {"hh": _H8, "kk": "x.......x.x.....", "sn": _S24},
        {"hh": _H8, "kk": "x.x.....x.......", "sn": _S24},
        {"hh": _H8, "kk": "x.......x.x.....", "sn": _S24},
        {"hh": _H8, "kk": "x.x.....x.......", "sn": "....X.......X.x."},
    ]),
    _groove("5 · Crash on the one", "The left crash replaces the hat on the 1 that starts each phrase.", 92, [
        {"cl": "x...............", "hh": "..x.x.x.x.x.x.x.", "kk": "x.......x.x.....", "sn": _S24},
        {"hh": _H8, "kk": "x.x.....x.......", "sn": _S24},
        {"hh": _H8, "kk": "x.......x.x.....", "sn": _S24},
        {"hh": _H8, "kk": "x.x.....x.......", "sn": "....X.......X.x."},
    ]),
    _groove("6 · First fill", "Bar 4 ends with snare sixteenths on beat 4, straight into the crash.", 92, [
        {"cl": "x...............", "hh": "..x.x.x.x.x.x.x.", "kk": "x.......x.x.....", "sn": _S24},
        {"hh": _H8, "kk": "x.x.....x.......", "sn": _S24},
        {"hh": _H8, "kk": "x.......x.x.....", "sn": _S24},
        {"hh": "x.x.x.x.x.x.....", "kk": "x.x.....x.......", "sn": "....X.......xxxx"},
    ]),
    _groove("7 · Rack tom enters", "The fill moves: two snares, two rack toms. The tom lane is new.", 92, [
        {"cl": "x...............", "hh": "..x.x.x.x.x.x.x.", "kk": "x.......x.x.....", "sn": _S24},
        {"hh": _H8, "kk": "x.x.....x.......", "sn": _S24},
        {"hh": _H8, "kk": "x.......x.x.....", "sn": _S24},
        {"hh": "x.x.x.x.x.x.....", "kk": "x.x.....x.......", "sn": "....X.......xx..", "t1": "..............xx"},
    ]),
    _groove("8 · Floor tom enters", "The fill walks down snare, rack tom, floor tom over beats 3 and 4.", 92, [
        {"cl": "x...............", "hh": "..x.x.x.x.x.x.x.", "kk": "x.......x.x.....", "sn": _S24},
        {"hh": _H8, "kk": "x.x.....x.......", "sn": _S24},
        {"hh": _H8, "kk": "x.......x.x.....", "sn": _S24},
        {"hh": "x.x.x.x.........", "kk": "x.x.....x.......", "sn": "....X...xxxx....", "t1": "............xx..", "ft": "..............xx"},
    ]),
    _groove("9 · Toms in the groove", "The floor tom takes over the hat's job on beats 1 and 3, the rack tom answers on the & of 4.", 88, [
        {"cl": "x...............", "ft": "..x.....x.x.....", "hh": "....x.x.....x.x.", "kk": "x.......x.......", "sn": _S24},
        {"ft": "x.x.....x.x.....", "hh": "....x.x.....x.x.", "kk": "x.......x.......", "sn": _S24, "t1": "..............x."},
        {"ft": "x.x.....x.x.....", "hh": "....x.x.....x.x.", "kk": "x.......x.......", "sn": _S24},
        {"ft": "x.x.....x.x.....", "hh": "....x.x.........", "kk": "x.......x.......", "sn": "....X.......x.x.", "t1": "............x.x."},
    ]),
    _groove("10 · Ride", "The right hand moves to the ride for the second half of the phrase, crash on the way in.", 92, [
        {"cl": "x...............", "hh": "..x.x.x.x.x.x.x.", "kk": "x.......x.x.....", "sn": _S24},
        {"hh": _H8, "kk": "x.x.....x.......", "sn": _S24},
        {"cl": "x...............", "rd": "..x.x.x.x.x.x.x.", "kk": "x.......x.x.....", "sn": _S24},
        {"rd": "x.x.x.x.x.x.....", "kk": "x.x.....x.......", "sn": "....X.......xx..", "t1": "..............xx"},
    ]),
    _groove("11 · Two crashes", "Left crash opens the phrase, right crash answers on beat 3 of bar 4 and closes it.", 92, [
        {"cl": "x...............", "rd": "..x.x.x.x.x.x.x.", "kk": "x.......x.x.....", "sn": _S24},
        {"rd": _H8, "kk": "x.x.....x.......", "sn": _S24},
        {"rd": _H8, "kk": "x.......x.x.....", "sn": _S24},
        {"cr": "........x.......", "rd": "x.x.x.x.........", "kk": "x.x.....x.......", "sn": "....X...xx......", "t1": "..........xx....", "ft": "............xx.."},
    ], bars=8),
    _groove("12 · Half time", "Snare on 3 only, kick on 1 and the & of 2: twice the space, same phrase shape.", 80, [
        {"cl": "x...............", "hh": "..x.x.x.x.x.x.x.", "kk": "x.....x.........", "sn": "........X......."},
        {"hh": _H8, "kk": "x.....x...x.....", "sn": "........X......."},
        {"hh": _H8, "kk": "x.....x.........", "sn": "........X......."},
        {"hh": "x.x.x.x.........", "kk": "x.....x.........", "sn": "........X...xx..", "t1": "..............xx", "ft": "................"},
    ]),
    _groove("13 · Sixteenth kicks", "Kicks land on the e and the a: the funk pocket under the hats.", 88, [
        {"cl": "x...............", "hh": "..x.x.x.x.x.x.x.", "kk": "x..x......x.x...", "sn": _S24},
        {"hh": _H8, "kk": "x.........x..x..", "sn": _S24},
        {"hh": _H8, "kk": "x..x......x.x...", "sn": _S24},
        {"hh": _H8, "kk": "x.........x..x..", "sn": "....X.......X.ox"},
    ]),
    _groove("14 · Ghost notes", "Soft snares on the e of 2 and the a of 3 between the backbeats.", 88, [
        {"cl": "x...............", "hh": "..x.x.x.x.x.x.x.", "kk": "x..x......x.x...", "sn": "....Xo.....oX..."},
        {"hh": _H8, "kk": "x.........x..x..", "sn": "....Xo.....oX..."},
        {"hh": _H8, "kk": "x..x......x.x...", "sn": "....Xo.....oX..."},
        {"hh": _H8, "kk": "x.........x..x..", "sn": "....Xo.....oX.ox"},
    ]),
    _groove("15 · Sixteenth hats", "One hand plays every sixteenth on the hats while the funk pocket stays put.", 76, [
        {"cl": "x...............", "hh": ".xxxxxxxxxxxxxxx", "kk": "x..x......x.x...", "sn": "....Xo.....oX..."},
        {"hh": _H16, "kk": "x.........x..x..", "sn": "....Xo.....oX..."},
        {"hh": _H16, "kk": "x..x......x.x...", "sn": "....Xo.....oX..."},
        {"hh": "xxxxxxxxxxxx....", "kk": "x.........x..x..", "sn": "....Xo.....oX...", "t1": "............xx..", "ft": "..............xx"},
    ]),
    _groove("16 · Linear", "Nothing lands together: hats, kick and snare take turns, toms and ride fill the gaps.", 86, [
        {"cl": "x...............", "hh": ".x.x..x..x.x..x.", "kk": "x...x......x....", "sn": "....X.......X..."},
        {"hh": ".x.x..x..x.x..x.", "kk": "x...x......x....", "sn": "....X.......X...", "t1": "..............x."},
        {"rd": ".x.x..x..x.x..x.", "kk": "x...x......x....", "sn": "....X.......X..."},
        {"rd": ".x.x..x.........", "kk": "x...x......x....", "sn": "....X...x.x.....", "t1": ".........x.x....", "ft": "..........x.x.x.", "cr": "...............x"},
    ]),
    _groove("17 · Song form", "A A B A: hats verses, a ride bridge with tom hits, two crashes, ghost notes, the works.", 92, [
        {"cl": "x...............", "hh": "..x.x.x.x.x.x.x.", "kk": "x..x......x.x...", "sn": "....Xo.....oX..."},
        {"hh": _H8, "kk": "x.........x..x..", "sn": "....Xo.....oX..."},
        {"hh": _H8, "kk": "x..x......x.x...", "sn": "....Xo.....oX..."},
        {"hh": "x.x.x.x.x.x.....", "kk": "x.........x..x..", "sn": "....Xo.....oX.xx"},
        {"cl": "x...............", "hh": "..x.x.x.x.x.x.x.", "kk": "x..x......x.x...", "sn": "....Xo.....oX..."},
        {"hh": _H8, "kk": "x.........x..x..", "sn": "....Xo.....oX..."},
        {"hh": _H8, "kk": "x..x......x.x...", "sn": "....Xo.....oX..."},
        {"hh": "x.x.x.x.........", "kk": "x.........x..x..", "sn": "....X...xx......", "t1": "..........xx....", "ft": "............xx.."},
        {"cr": "x...............", "rd": "..x.x.x.x.x.x.x.", "kk": "x.....x.........", "sn": "........X.......", "ft": "..........x....."},
        {"rd": _H8, "kk": "x.....x...x.....", "sn": "........X.......", "t1": "..............x."},
        {"rd": _H8, "kk": "x.....x.........", "sn": "........X.......", "ft": "..........x....."},
        {"rd": "x.x.x.x.........", "kk": "x.....x.........", "sn": "........X...x...", "t1": ".............x..", "ft": "..............xx"},
        {"cl": "x...............", "hh": "..x.x.x.x.x.x.x.", "kk": "x..x......x.x...", "sn": "....Xo.....oX..."},
        {"hh": _H8, "kk": "x.........x..x..", "sn": "....Xo.....oX..."},
        {"hh": _H8, "kk": "x..x......x.x...", "sn": "....Xo.....oX..."},
        {"cr": "..............x.", "hh": "x.x.x.x.x.x.....", "kk": "x.........x..x..", "sn": "....X.......xxx.", "cl": "...............x"},
    ], bars=16),
    # --- rock, metal, punk: beats and fills ---
    _groove("18 · Fill vocabulary", "A different fill every two bars: snare sixteenths, snare-tom pairs, the walk down, then all four drums.", 92, [
        {"cl": "x...............", "hh": "..x.x.x.x.x.x.x.", "kk": "x..x......x.x...", "sn": _S24},
        {"hh": "x.x.x.x.x.x.....", "kk": "x.........x.....", "sn": "....X.......xxxx"},
        {"cl": "x...............", "hh": "..x.x.x.x.x.x.x.", "kk": "x..x......x.x...", "sn": _S24},
        {"hh": "x.x.x.x.x.x.....", "kk": "x.........x.....", "sn": "....X.......xx..", "t1": "..............xx"},
        {"cl": "x...............", "hh": "..x.x.x.x.x.x.x.", "kk": "x..x......x.x...", "sn": _S24},
        {"hh": "x.x.x.x.........", "kk": "x.........x.....", "sn": "....X...xxxx....", "t1": "............xx..", "ft": "..............xx"},
        {"cl": "x...............", "hh": "..x.x.x.x.x.x.x.", "kk": "x..x......x.x...", "sn": _S24},
        {"hh": "x.x.............", "kk": "x...........x.x.", "sn": "....xxxx........", "t1": "........xxxx....", "ft": "............x.x."},
    ]),
    _groove("19 · Kick doubles", "Two kicks in a row on the sixteenths, single pedal: the & a of 1 and the a of 3.", 90, [
        {"cl": "x...............", "hh": "..x.x.x.x.x.x.x.", "kk": "x.xx......x.x...", "sn": _S24},
        {"hh": _H8, "kk": "x.xx......x.xx..", "sn": _S24},
        {"hh": _H8, "kk": "x.xx......x.x...", "sn": _S24},
        {"hh": "x.x.x.x.x.x.....", "kk": "x.xx......x.....", "sn": "....X.......xx..", "t1": "..............xx"},
    ]),
    _groove("20 · Punk", "Snare on every &, kick on every beat, hats along: the upbeat snare drives it.", 96, [
        {"cl": "x...............", "hh": "..x.x.x.x.x.x.x.", "kk": "x...x...x...x...", "sn": "..X...X...X...X."},
        {"hh": _H8, "kk": "x...x...x...x...", "sn": "..X...X...X...X."},
        {"hh": _H8, "kk": "x...x...x...x...", "sn": "..X...X...X...X."},
        {"hh": "x.x.x.x.x.x.....", "kk": "x...x...x...x...", "sn": "..X...X...X.xxxx"},
    ]),
    _groove("21 · Gallop", "The metal gallop in the kick: eighth plus two sixteenths under straight hats.", 88, [
        {"cl": "x...............", "hh": "..x.x.x.x.x.x.x.", "kk": "x.xxx.xxx.xxx.xx", "sn": _S24},
        {"hh": _H8, "kk": "x.xxx.xxx.xxx.xx", "sn": _S24},
        {"hh": _H8, "kk": "x.xxx.xxx.xxx.xx", "sn": _S24},
        {"hh": "x.x.x.x.x.x.....", "kk": "x.xxx.xxx.xx....", "sn": "....X.......xx..", "ft": "..............xx"},
    ]),
    _groove("22 · Riding the crash", "Second half of the phrase the right hand rides the left crash on the eighths; the right crash marks the return.", 92, [
        {"cr": "x...............", "hh": "..x.x.x.x.x.x.x.", "kk": "x.xx......x.x...", "sn": _S24},
        {"hh": _H8, "kk": "x.xx......x.xx..", "sn": _S24},
        {"cl": "x.x.x.x.x.x.x.x.", "kk": "x.xx......x.x...", "sn": _S24},
        {"cl": "x.x.x.x.x.x.....", "kk": "x.xx......x.....", "sn": "....X.......xx..", "t1": "..............xx"},
    ]),
    _groove("23 · Crash on the &", "The right crash lands on the & of 4 with a kick under it, pushing into the next bar.", 92, [
        {"cl": "x...............", "hh": "..x.x.x.x.x.x.x.", "kk": "x.xx......x.x...", "sn": _S24},
        {"hh": "x.x.x.x.x.x.x...", "cr": "..............x.", "kk": "x.xx......x...x.", "sn": _S24},
        {"hh": _H8, "kk": "x.xx......x.x...", "sn": _S24},
        {"hh": "x.x.x.x.x.x.....", "cr": "..............x.", "kk": "x.xx......x...x.", "sn": "....X.......xx.."},
    ]),
    _groove("24 · Kick runs", "Sixteenth-note kick runs in bar 4, first half a bar, then a whole bar under the snare.", 84, [
        {"cl": "x...............", "hh": "..x.x.x.x.x.x.x.", "kk": "x.xx......x.x...", "sn": _S24},
        {"hh": _H8, "kk": "x.xx......x.xx..", "sn": _S24},
        {"hh": _H8, "kk": "x.xx......x.x...", "sn": _S24},
        {"hh": "x.x.x.x.........", "kk": "x.xx....xxxxxxxx", "sn": "....X.......X..."},
        {"cl": "x...............", "hh": "..x.x.x.x.x.x.x.", "kk": "x.xx......x.x...", "sn": _S24},
        {"hh": _H8, "kk": "x.xx......x.xx..", "sn": _S24},
        {"hh": _H8, "kk": "x.xx......x.x...", "sn": _S24},
        {"cr": "x...............", "kk": "xxxxxxxxxxxxxxxx", "sn": "....X.......X..."},
    ]),
    _groove("25 · Around the kit", "Whole-bar fills every other bar: snare to rack to floor, then back up, then in pairs, then with kicks.", 90, [
        {"cl": "x...............", "hh": "..x.x.x.x.x.x.x.", "kk": "x.xx......x.x...", "sn": _S24},
        {"kk": "x...........x...", "sn": "xxxx............", "t1": "....xxxx........", "ft": "........xxxx....", "cr": "............x..."},
        {"cl": "x...............", "hh": "..x.x.x.x.x.x.x.", "kk": "x.xx......x.x...", "sn": _S24},
        {"kk": "x...........x...", "ft": "xxxx............", "t1": "....xxxx........", "sn": "........xxxxxxxx"},
        {"cl": "x...............", "hh": "..x.x.x.x.x.x.x.", "kk": "x.xx......x.x...", "sn": _S24},
        {"kk": "x...x...x...x...", "sn": "xx..xx..........", "t1": "..xx......xx....", "ft": "......xx....xxxx"},
        {"cl": "x...............", "hh": "..x.x.x.x.x.x.x.", "kk": "x.xx......x.x...", "sn": _S24},
        {"kk": "..xx..xx..xx..xx", "sn": "xx..............", "t1": "....xx..........", "ft": "........xx..xx.."},
    ]),
    _groove("26 · Breakdown", "Half time, right crash on every beat, snare on 3, the kick chops the sixteenths underneath.", 80, [
        {"cl": "x...............", "cr": "....x...x...x...", "kk": "x..x..x.....x.x.", "sn": "........X......."},
        {"cr": "x...x...x...x...", "kk": "x..x..x...x...xx", "sn": "........X......."},
        {"cr": "x...x...x...x...", "kk": "x..x..x.....x.x.", "sn": "........X......."},
        {"cr": "x...x...x.......", "kk": "x..x..x.........", "sn": "........X...xx..", "t1": "............x...", "ft": "..............xx"},
    ]),
    _groove("27 · Three over four", "The kick walks in groups of three sixteenths across the bar while the hats and the backbeat stay straight.", 86, [
        {"cl": "x...............", "hh": "..x.x.x.x.x.x.x.", "kk": "x..x..x..x..x..x", "sn": _S24},
        {"hh": _H8, "kk": "..x..x..x..x..x.", "sn": _S24},
        {"hh": _H8, "kk": "x..x..x..x..x..x", "sn": _S24},
        {"hh": "x.x.x.x.x.x.....", "kk": "..x..x..x..x....", "sn": "....X.......xx..", "ft": "..............xx"},
    ]),
    _groove("28 · Blast, lite", "Kick and snare alternate on the eighths under a ride on the eighths. Speed it up as it settles.", 84, [
        {"cl": "x...............", "rd": "..x.x.x.x.x.x.x.", "kk": "x.x.x.x.x.x.x.x.", "sn": "..X...X...X...X."},
        {"rd": _H8, "kk": "x.x.x.x.x.x.x.x.", "sn": "..X...X...X...X."},
        {"rd": _H8, "kk": "x.x.x.x.x.x.x.x.", "sn": "..X...X...X...X."},
        {"rd": "x.x.x.x.x.x.....", "kk": "x.x.x.x.x.x.....", "sn": "..X...X...X.xxxx"},
    ]),
    _groove("29 · Crash landing", "Fills that end on a crash with a kick, then the right crash answers on the & before the next phrase.", 92, [
        {"cl": "x...............", "hh": "..x.x.x.x.x.x.x.", "kk": "x.xx......x.x...", "sn": _S24},
        {"hh": _H8, "kk": "x.xx......x.xx..", "sn": _S24},
        {"hh": "x.x.x.x.x.x.x.x.", "kk": "x.xx......x.x...", "sn": _S24},
        {"hh": "x.x.x...........", "kk": "x.......x...x.x.", "sn": "....X...xx......", "t1": "..........xx....", "ft": "............x...", "cl": "............x...", "cr": "..............x."},
    ]),
    _groove("30 · Rock anthem", "Sixteen bars: verse on the hats, a build with the snare growing from ghosts to accents, chorus riding the crash, big ending.", 92, [
        {"cl": "x...............", "hh": "..x.x.x.x.x.x.x.", "kk": "x.xx......x.x...", "sn": _S24},
        {"hh": _H8, "kk": "x.xx......x.xx..", "sn": _S24},
        {"hh": _H8, "kk": "x.xx......x.x...", "sn": _S24},
        {"hh": "x.x.x.x.x.x.....", "kk": "x.xx......x.....", "sn": "....X.......xx..", "t1": "..............xx"},
        {"cl": "x...............", "hh": "..x.x.x.x.x.x.x.", "kk": "x..x..x..x..x..x", "sn": _S24},
        {"hh": _H8, "kk": "..x..x..x..x..x.", "sn": _S24},
        {"hh": "x.x.x.x.x.x.x.x.", "kk": "x...x...x...x...", "sn": "oooooooooooooooo"},
        {"hh": "x.x.x.x.x.x.x.x.", "kk": "x...x...x...x...", "sn": "xxxxxxxxXXXXXXXX", "cr": "...............x"},
        {"cl": "x.x.x.x.x.x.x.x.", "kk": "x.xx......x.x...", "sn": _S24},
        {"cl": "x.x.x.x.x.x.x.x.", "kk": "x.xx......x.xx..", "sn": _S24},
        {"cl": "x.x.x.x.x.x.x.x.", "kk": "x.xx......x.x...", "sn": _S24},
        {"cl": "x.x.x.x.x.x.....", "cr": "..............x.", "kk": "x.xx......x...x.", "sn": "....X.......xx.."},
        {"cl": "x.x.x.x.x.x.x.x.", "kk": "x.xx......x.x...", "sn": _S24},
        {"cl": "x.x.x.x.x.x.x.x.", "kk": "x.xx......x.xx..", "sn": _S24},
        {"cl": "x...............", "hh": "..x.x.x.x.x.x.x.", "kk": "x.xx......x.x...", "sn": _S24},
        {"kk": "x...x...x...x.x.", "sn": "xxxx........xx..", "t1": "....xxxx........", "ft": "........xxxx....", "cl": "..............x.", "cr": "..............x."},
    ], bars=16),
    # Cumbia villera on the three drum bodies (asked 2026-09-12, named plena until 2026-09-14): the
    # cumbia sway, floor tom low on 1 and 3, rack tom on every &, snare on 2 and 4 (the redoblante),
    # then the pickups: the tom's double into the beat, the floor's "4 a" into the 1. Hands free: no
    # kick, no hats.
    _groove("31 · Cumbia", "Cumbia villera on the drum bodies: floor on 1 and 3, rack tom on every &, snare on 2 and 4; pickups on the tom and the floor.", 96, [
        {"ft": "x.......x.......", "t1": "..x...x...x...x.", "sn": _S24},
        {"ft": "x.......x.......", "t1": "..x...x...x...x.", "sn": _S24},
        {"ft": "x.......x.......", "t1": "..x...x...x...xx", "sn": _S24},
        {"ft": "x.......x.....x.", "t1": "..x...x...x....x", "sn": "....X.......X..."},
    ], bars=16, backing="cumbia"),
    # The cumbia with the redoblante's repiques (asked 2026-09-14): the same sway and the two
    # pickups of level 31, plus the snare's picado figures, staccato sixteenths: the drag (a
    # ghost on the "a" into the backbeat), the run out of the 4 into the 1 (4 e & a), the run
    # into the 4 (& a of 3), and a whole two-beat repique closing the phrase, accented on the
    # beats. The rack tom yields its & where a repique needs the snare alone.
    _groove("32 · Cumbia: repiques", "The cumbia of level 31 with the redoblante's repiques: ghost drags into the backbeat, staccato sixteenth runs out of the 4 and into it, and a two-beat repique closing the phrase.", 96, [
        {"ft": "x.......x.......", "t1": "..x...x...x...x.", "sn": _S24},
        {"ft": "x.......x.......", "t1": "..x...x...x...x.", "sn": "...oX......oX..."},
        {"ft": "x.......x.......", "t1": "..x...x...x...xx", "sn": "...oX......oX..."},
        {"ft": "x.......x.......", "t1": "..x...x...x.....", "sn": "....X.......Xxxx"},
        {"ft": "x.......x.......", "t1": "..x...x...x...x.", "sn": "...oX......oX..."},
        {"ft": "x.......x.......", "t1": "..x...x.......x.", "sn": "....X.....xxX..."},
        {"ft": "x.......x.....x.", "t1": "..x...x...x....x", "sn": "...oX......oX..."},
        {"ft": "x.......x.......", "t1": "..x...x.........", "sn": "....X...XxxxXxxx"},
    ], bars=16, backing="cumbia"),
    # Reggae one drop with the classic fills (asked 2026-09-13): kick and cross-stick together on
    # the 3, hats on the eighths; every fourth bar a fill, the crash on the 1 after it.
    # Fills: the four sixteenths on beat 4 into the drop; the walk down snare, rack, floor over
    # beats 3 and 4 with the kick pickup on the "a" of 4; snare doubles then rack and floor on
    # beat 4; the sixteenth roll over beats 3 and 4 accented on every third stroke.
    _groove("33 · Reggae: one drop, fills", "One drop (kick and cross-stick on the 3, hats on the eighths); every fourth bar a classic fill: the four on beat 4, the walk down the toms, snare doubles to the floor, the roll over 3 and 4.", 76, [
        {"hh": _H8, "kk": "........x.......", "sn": "........x......."},
        {"hh": _H8, "kk": "........x.......", "sn": "........x......."},
        {"hh": _H8, "kk": "........x.......", "sn": "........x......."},
        {"hh": "x.x.x.x.x.x.....", "kk": "........x.......", "sn": "........x...xxxX"},
        {"cl": "x...............", "hh": "..x.x.x.x.x.x.x.", "kk": "........x.......", "sn": "........x......."},
        {"hh": _H8, "kk": "........x.......", "sn": "........x......."},
        {"hh": _H8, "kk": "........x.......", "sn": "........x......."},
        {"hh": "x.x.x.x.x.......", "kk": "........x......x", "sn": "........xx......", "t1": "..........xx....", "ft": "............xx.."},
        {"cl": "x...............", "hh": "..x.x.x.x.x.x.x.", "kk": "........x.......", "sn": "........x......."},
        {"hh": _H8, "kk": "........x.......", "sn": "........x......."},
        {"hh": _H8, "kk": "........x.......", "sn": "........x......."},
        {"hh": "x.x.x.x.x.x.....", "kk": "........x.......", "sn": "........x...xx..", "t1": "..............x.", "ft": "...............x"},
        {"cl": "x...............", "hh": "..x.x.x.x.x.x.x.", "kk": "........x.......", "sn": "........x......."},
        {"hh": _H8, "kk": "........x.......", "sn": "........x......."},
        {"hh": _H8, "kk": "........x.......", "sn": "........x......."},
        {"hh": "x.x.x.x.x.......", "kk": "........x.......", "sn": "........XxxXxxXx"},
    ], bars=16),
]
# --- hi-hat control: what the pedal, the zone and the foot can say ----------------------
# Notation: t/T tight bow/edge, m/M mid, a/A open, pd x = chick. The names of the articulations
# are hhmapper's, so what the lesson asks for is what GetGood Drums plays.
_KS = {"kk": "x.......x.......", "sn": _S24}
HIHAT_LESSONS = [
    _groove("Tight and open", "Eighths on the bow: a bar with the pedal down tight, a bar with it up open. Feel the pedal travel.", 84, [
        {"hh": "t.t.t.t.t.t.t.t.", **_KS},
        {"hh": "a.a.a.a.a.a.a.a.", **_KS},
    ]),
    _groove("Half open", "The same eighths at half pedal: the sloshy mid hat. Find the spot and hold it.", 84, [
        {"hh": "m.m.m.m.m.m.m.m.", **_KS},
        {"hh": "m.m.m.m.m.m.m.m.", "kk": "x.....x.x.......", "sn": _S24},
    ]),
    _groove("Openness ladder", "Two bars tight, two bars mid, two bars open, then back down to tight, edge on the beats and bow on the &s. Every step is a pedal position.", 84, [
        {"hh": "T.t.T.t.T.t.T.t.", **_KS}, {"hh": "T.t.T.t.T.t.T.t.", **_KS},
        {"hh": "M.m.M.m.M.m.M.m.", **_KS}, {"hh": "M.m.M.m.M.m.M.m.", **_KS},
        {"hh": "A.a.A.a.A.a.A.a.", **_KS}, {"hh": "A.a.A.a.A.a.A.a.", **_KS},
        {"hh": "M.m.M.m.M.m.M.m.", **_KS}, {"hh": "T.t.T.t.T.t.T.t.", **_KS},
    ]),
    _groove("Open on the &", "Tight eighths, the hat opens on the & of 4 and the foot closes it on the next 1: the disco hat.", 88, [
        {"hh": "t.t.t.t.t.t.t.a.", "pd": "x...............", **_KS},
        {"hh": "t.t.t.t.t.t.t.a.", "pd": "x...............", "kk": "x.......x.x.....", "sn": _S24},
    ]),
    _groove("Bark", "An open hat choked right away by the foot: open on the & of 2, chick on 3, tight around it.", 88, [
        {"hh": "t.t.t.a...t.t.t.", "pd": "........x.......", **_KS},
        {"hh": "t.t.t.a...t.t.a.", "pd": "........x.......", "kk": "x.......x.x.....", "sn": _S24},
    ]),
    _groove("Foot on 2 and 4", "No sticks on the hat: the foot chicks on 2 and 4 under kick and snare, then on every beat.", 84, [
        {"pd": "....x.......x...", "kk": "x.......x.......", "sn": _S24},
        {"pd": "....x.......x...", "kk": "x.......x.......", "sn": _S24},
        {"pd": "x...x...x...x...", "kk": "x.......x.......", "sn": _S24},
        {"pd": "x...x...x...x...", "kk": "x.......x.x.....", "sn": _S24},
    ]),
    _groove("Bow and edge", "Tight eighths alternating bow taps and edge accents: the shoulder of the stick speaks on the beat.", 84, [
        {"hh": "T.t.T.t.T.t.T.t.", **_KS},
        {"hh": "T.t.t.t.T.t.t.t.", **_KS},
    ]),
    _groove("Edge on the open", "Open hats on the edge: the & of 2 and the & of 4 open on the edge, chicks close them on 3 and 1.", 88, [
        {"hh": "t.t.t.A.t.t.t.A.", "pd": "x.......x.......", **_KS},
        {"hh": "t.t.t.A.t.t.t.A.", "pd": "x.......x.......", "kk": "x..x......x.x...", "sn": _S24},
    ]),
    _groove("Sixteenths, mid and tight", "Sixteenths on the bow, tight on the beats and mid in between: the pedal breathes with every beat.", 76, [
        {"hh": "tmmmtmmmtmmmtmmm", **_KS},
        {"hh": "tmmmtmmmtmmmtmmm", "kk": "x..x......x.x...", "sn": _S24},
    ]),
    _groove("Hi-hat song", "Sixteen bars that use everything: tight groove, open &s, edge accents, a mid section, barks, and the foot on 2 and 4.", 88, [
        {"hh": "t.t.t.t.t.t.t.t.", "kk": "x.......x.x.....", "sn": _S24},
        {"hh": "t.t.t.t.t.t.t.a.", "pd": "x...............", "kk": "x.......x.x.....", "sn": _S24},
        {"hh": "t.t.t.t.t.t.t.t.", "kk": "x.......x.x.....", "sn": _S24},
        {"hh": "t.t.t.t.t.t.t.a.", "pd": "x...............", "kk": "x.......x.x.....", "sn": "....X.......X.x."},
        {"hh": "T.t.T.t.T.t.T.t.", "kk": "x.......x.x.....", "sn": _S24},
        {"hh": "T.t.T.t.T.t.T.a.", "pd": "x...............", "kk": "x.......x.x.....", "sn": _S24},
        {"hh": "T.t.T.t.T.t.T.t.", "kk": "x.......x.x.....", "sn": _S24},
        {"hh": "T.t.T.t.T.t.t.A.", "pd": "x...............", "kk": "x.......x.x.....", "sn": "....X.......xx.."},
        {"hh": "m.m.m.m.m.m.m.m.", "kk": "x.....x.x.......", "sn": _S24},
        {"hh": "m.m.m.m.m.m.m.m.", "kk": "x.....x.x.......", "sn": _S24},
        {"hh": "m.m.m.m.m.m.m.m.", "kk": "x.....x.x.......", "sn": _S24},
        {"hh": "m.m.m.m.m.m.m.A.", "pd": "x...............", "kk": "x.....x.x.......", "sn": "....X.......X.x."},
        {"hh": "t.t.t.a...t.t.t.", "pd": "........x.......", "kk": "x.......x.x.....", "sn": _S24},
        {"hh": "t.t.t.a...t.t.a.", "pd": "x.......x.......", "kk": "x.......x.x.....", "sn": _S24},
        {"pd": "....x.......x...", "kk": "x.......x.......", "sn": _S24},
        {"hh": "t.t.t.t.t.t.t.A.", "pd": "....x.......x...", "kk": "x.......x.x.....", "sn": "....X.......xxxx"},
    ], bars=16),
]
# --- double kick: a foot ostinato under simple hands ---------------------------------
def _kick_ostinato(name, desc, bpm, bars, feet, sub, hands, backing=None):
    """A double-kick level. `feet`: the kick pattern for one bar on a grid of `sub` per
    beat, R / L the foot, "." a rest; it is the sticking strip (rests shown as dots).
    `hands`: GROOVE_KEYS key -> pattern on the same grid (x / X / .). Feet are judged on
    time only, no dynamics. backing: see Chart.backing ("kick": the music hammers `feet`)."""
    beat = 60 / bpm
    slots = 4 * sub
    assert len(feet) == slots and all(len(p) == slots for p in hands.values()), name
    notes = []
    for bar in range(bars):
        for i, c in enumerate(feet):
            if c in "RL":
                notes.append(ChartNote((bar * 4 + i / sub) * beat, "kick", GROOVE_VEL["x"], hand=c, strip=i))
        for k, pat in hands.items():
            for i, c in enumerate(pat):
                if c in GROOVE_VEL:
                    notes.append(ChartNote((bar * 4 + i / sub) * beat, GROOVE_KEYS[k], GROOVE_VEL[c], accent=(c == "X")))
    notes.sort(key=lambda n: (n.t, INSTRUMENTS.index(n.key)))
    ch = Chart(name, notes, bpm, desc, [(0, sub)])
    ch.sticking = list(feet)
    ch.accents = set()
    ch.sticking_groups = [sub * b for b in range(1, 4)]
    ch.backing = backing
    return ch


# hands for the double-kick levels: the pulse on the hats, the backbeat on the snare
_DK_HANDS_16 = {"hh": "x...x...x...x...", "sn": "....X.......X..."}
_DK_HANDS_8 = {"hh": "x.x.x.x.", "sn": "..X...X."}
_DK_HANDS_12 = {"hh": "x..x..x..x..", "sn": "...X.....X.."}

# The curriculum: the feet learn to alternate, then to burst, then to run; the hands
# keep a plain beat on top so the feet become an ostinato, not a fill.
KICK_OSTINATOS = [
    _kick_ostinato("Double kick 8ths", "Right, left on the eighths; hats on the beat, snare on 2 and 4.", 80, 8,
                   "RLRLRLRL", 2, _DK_HANDS_8),
    _kick_ostinato("Double kick bursts of two", "Two sixteenths on every beat, right then left, then rest.", 80, 8,
                   "RL..RL..RL..RL..", 4, _DK_HANDS_16),
    # The two steps into the gallop (asked for 2026-09-20: over 53 gallop runs the right foot's
    # closing sixteenth came 30 ms early after the left, the figure squeezed towards the beat,
    # and knowing it did not fix it). First the pair on its own with the left on the beat, then
    # the pair as the gallop has it, on the & and the a, with nothing on the beat but the hats.
    _kick_ostinato("Double kick pairs, left first", "Two sixteenths on every beat, left then right: the right foot waits its whole sixteenth.", 80, 8,
                   "LR..LR..LR..LR..", 4, _DK_HANDS_16, backing="kick"),
    _kick_ostinato("Double kick gallop tail", "The gallop's two sixteenths alone: left on the &, right on the a, the beat left to the hats.", 80, 8,
                   "..LR..LR..LR..LR", 4, _DK_HANDS_16, backing="kick"),
    _kick_ostinato("Double kick gallop, feet only", "The gallop on the feet alone, nothing in the hands: right, left, right on every beat.", 80, 8,
                   "R.LRR.LRR.LRR.LR", 4, {}, backing="kick"),
    _kick_ostinato("Double kick gallop", "Eighth, sixteenth, sixteenth on every beat: right, left, right.", 80, 8,
                   "R.LRR.LRR.LRR.LR", 4, _DK_HANDS_16, backing="kick"),
    _kick_ostinato("Double kick 16ths", "Sixteenths on the feet, right foot on the beat, under the plain beat.", 80, 8,
                   "RLRLRLRLRLRLRLRL", 4, _DK_HANDS_16),
    _kick_ostinato("Double kick 16ths, left lead", "The same run leading with the left foot: the weak foot lands on the beat.", 80, 8,
                   "LRLRLRLRLRLRLRLR", 4, _DK_HANDS_16),
    _kick_ostinato("Double kick 16ths, hats 8ths", "Sixteenths on the feet with the hats on the eighths: hands and feet at different speeds.", 80, 8,
                   "RLRLRLRLRLRLRLRL", 4, {"hh": "x.x.x.x.x.x.x.x.", "sn": "....X.......X..."}),
    _kick_ostinato("Double kick triplets", "Eighth-note triplets on the feet, alternating, so the lead foot swaps every beat.", 80, 8,
                   "RLRLRLRLRLRL", 3, _DK_HANDS_12),
    _kick_ostinato("Double kick, bursts of four", "Four sixteenths on beats 2 and 4, rest on 1 and 3: start and stop cleanly.", 80, 8,
                   "....RLRL....RLRL", 4, {"hh": "x...x...x...x...", "sn": "X.......X......."}),
]


# ---------------------------------------------------------------------------
# Courses: one genre at a time, its idiomatic grooves and fills in levels that build on each
# other (asked 2026-09-17, replacing the Songs section: the copyright made songs a dead end).
# A course is a list of _groove levels with a backing style of its own (sounds.STYLES).
# ---------------------------------------------------------------------------
@dataclass
class Course:
    key: str        # the app's category key; progress stays keyed by the level names
    name: str
    desc: str
    levels: list


# --- Pop punk: fast eighths, the push on the & of 2 and 4, the crash wash, snare on the &, the
# stabs, the build, half-time verses, kick doubles. Written tempos climb from 140 to 168 (the
# style lives at 160..190: the rate keys take it there once the hands are in).
_PP_V = "x.....x.x......."            # kick on 1, the & of 2 and 3
_PP_P = "x.....x.x.....x."            # the same with the push on the & of 4
_H8C = "..x.x.x.x.x.x.x."             # hats on the eighths under a crash on the 1
_H8_2 = "....x.x.x.x.x.x."            # hats from the 2: the crash on the 1 rings until the backbeat, the hand comes home with the snare
_CR1 = "x..............."             # a crash on the 1
_CR8 = "x.x.x.x.x.x.x.x."             # the crash wash: right hand on the crash, every eighth
_SK = "..X...X...X...X."              # the skank: snare on every &
_K4 = "x...x...x...x..."              # kick on every beat
_KD = "x.xx....x.xx...."              # kick doubles: 1 & a, 3 & a
_KDP = "x.xx....x.xx..x."             # the doubles with the push
_HT_K = "x.....x........."            # half time: kick on 1 and the & of 2
_HT_S = "........X......."            # half time: snare on 3
# the fills, on the last bar of a phrase (hats stop where the fill starts)
_F_SN8 = {"hh": "x.x.x.x.x.x.....", "sn": "....X.......x.x."}                                      # two snares on the eighths of 4
_F_SN16 = {"hh": "x.x.x.x.x.x.....", "sn": "....X.......xxxx"}                                     # four snares on 4
_F_SNT = {"hh": "x.x.x.x.x.x.....", "sn": "....X.......xx..", "t1": "..............xx"}            # snare, rack
_F_WALK = {"hh": "x.x.x.x.........", "sn": "....X...xxxx....", "t1": "............xx..", "ft": "..............xx"}   # the walk down
_F_KIT = {"hh": "x.x.............", "sn": "....xxxx........", "t1": "........xxxx....", "ft": "............xxxx"}    # the whole kit from the 2
_F_TOMS8 = {"hh": "x.x.x.x.........", "sn": "....X...x.x.....", "t1": "............x...", "ft": "..............x."}  # eighths down the kit
_F_RUN = {"sn": "............xxxx"}                                                                # the run out of a stab bar
_STAB = {"cl": "x.....x.........", "cr": "x.....x.........", "kk": "x.....x........."}             # hits on 1 and the & of 2


def _pp(bar, **over):
    """A pop punk bar: the dict `bar` with `over` on top (a fill, a crash, another kick)."""
    return {**bar, **over}


_PPV = {"hh": _H8, "kk": _PP_V, "sn": _S24}                                # the verse bar
_PPP = {"hh": _H8, "kk": _PP_P, "sn": _S24}                                # the verse bar with the push
_PPV1 = {"cl": _CR1, "hh": _H8C, "kk": _PP_V, "sn": _S24}                   # the verse bar opening on the crash
_PPC = {"cl": _CR8, "kk": _PP_V, "sn": _S24}                               # the chorus bar: the wash
_PPCP = {"cl": _CR8, "kk": _PP_P, "sn": _S24}                              # the wash with the push
_PPC_AND = {"cl": "x.x.x.x.x.x.x...", "cr": "..............x.", "kk": _PP_P, "sn": _S24}   # the wash, right crash on the & of 4
_PPV_AND = {"hh": "x.x.x.x.x.x.x...", "cr": "..............x.", "kk": _PP_P, "sn": _S24}   # hats, right crash on the & of 4
_SKANK = {"hh": _H8, "kk": _K4, "sn": _SK}
_SKANK1 = {"cl": _CR1, "hh": _H8C, "kk": _K4, "sn": _SK}
_HT = {"hh": _H8, "kk": _HT_K, "sn": _HT_S}
_HT1 = {"cl": _CR1, "hh": _H8C, "kk": _HT_K, "sn": _HT_S}
_BUILD = [{"kk": _K4, "sn": "o.o.o.o.o.o.o.o."}, {"kk": _K4, "sn": "x.x.x.x.x.x.x.x."},
          {"kk": _K4, "sn": "X.x.X.x.X.x.X.x."}, {"kk": _K4, "sn": "xxxxxxxxXXXXXXXX"}]
_KDV = {"hh": _H8, "kk": _KD, "sn": _S24}
_KDV1 = {"cl": _CR1, "hh": _H8C, "kk": _KD, "sn": _S24}
_KDP_B = {"hh": _H8, "kk": _KDP, "sn": _S24}
_RIDE = {"rd": _H8, "kk": _KD, "sn": _S24}
_RIDE_T = {"rd": "x.x.x.x.x.x.x...", "kk": _KD, "sn": _S24, "t1": "..............x."}
_RIDE1 = {"cr": _CR1, "rd": _H8C, "kk": _KD, "sn": _S24}

def _drill(name, desc, bpm, groove, fill, bars=16):
    """A fill drill: two bars looping, the groove bar and then the fill, eight times over, so one
    fill is learnt on its own before the level that puts four of them in a song (asked for
    2026-09-22: the vocabulary by repetition, one fill per level, "y despues incorporarlos al
    nivel"). Both bars are the ones the level that uses the fill writes, so what the hands learn
    here transfers note for note."""
    return _groove(name, desc, bpm, [groove, fill], bars=bars, backing="punk")


POP_PUNK = [
    _groove("1 · Driving eighths", "The pop punk pulse: hats on every eighth, kick on 1 and 3, snare on 2 and 4, a crash opening every four bars. Fast and even.", 140, [
        {"cl": _CR1, "hh": _H8C, "kk": "x.......x.......", "sn": _S24},
        {"hh": _H8, "kk": "x.......x.......", "sn": _S24},
        {"hh": _H8, "kk": "x.......x.......", "sn": _S24},
        {"hh": _H8, "kk": "x.......x.......", "sn": _S24},
    ], backing="punk"),
    _groove("2 · Four on the floor", "The kick moves to every beat under the same hats and backbeat: the punk stomp.", 144, [
        {"cl": _CR1, "hh": _H8C, "kk": _K4, "sn": _S24},
        {"hh": _H8, "kk": _K4, "sn": _S24},
        {"hh": _H8, "kk": _K4, "sn": _S24},
        {"hh": _H8, "kk": _K4, "sn": _S24},
    ], backing="punk"),
    _groove("3 · The push", "Kick on 1, the & of 2 and 3; every second bar adds the & of 4, pushing into the next bar. The pop punk kick.", 148, [
        _PPV1, _PPP, _PPV, _PPP,
    ], backing="punk"),
    _groove("4 · Tight and open", "Four bars of verse with the hats tight, four of chorus with them open, the foot off the pedal; a crash on the change, the pushes stay.", 148, [
        {"cl": _CR1, "hh": "..t.t.t.t.t.t.t.", "kk": _PP_V, "sn": _S24},
        {"hh": "t.t.t.t.t.t.t.t.", "kk": _PP_P, "sn": _S24},
        {"hh": "t.t.t.t.t.t.t.t.", "kk": _PP_V, "sn": _S24},
        {"hh": "t.t.t.t.t.t.t.t.", "kk": _PP_P, "sn": _S24},
        {"cl": _CR1, "hh": "..a.a.a.a.a.a.a.", "kk": _PP_V, "sn": _S24},
        {"hh": "a.a.a.a.a.a.a.a.", "kk": _PP_P, "sn": _S24},
        {"hh": "a.a.a.a.a.a.a.a.", "kk": _PP_V, "sn": _S24},
        {"hh": "a.a.a.a.a.a.a.a.", "kk": _PP_P, "sn": _S24},
    ], backing="punk", hammer=True),
    # The way back to the verse: after the crash on the 1 the hand rejoins the hats on the 2, with
    # the snare, not on the & of 1 (2026-09-21: from the far crash at 180 the & is a stretch, and
    # the crash should ring until the backbeat). Both crashes, the lead toggle picks the side.
    _groove("5 · Washing the crash", "The chorus rides the left crash on every eighth instead of the hats; the right crash marks the way back to the verse, the hats rejoining on the 2.", 152, [
        {"cl": _CR1, "hh": _H8_2, "kk": _PP_V, "sn": _S24}, _PPP, _PPV, _PPP,
        _PPC, _PPCP, _PPC, _PPCP,
        {"cr": _CR1, "hh": _H8_2, "kk": _PP_V, "sn": _S24}, _PPP, _PPV, _PPP,
        _PPC, _PPCP, _PPC, _PPCP,
    ], bars=16, backing="punk", hammer=True, mirror="crash"),
    _groove("6 · Snare on the &", "The skank: kick on every beat, snare on every &, hats along. The verse skanks, the chorus washes the crash with the pushes.", 152, [
        _SKANK1, _SKANK, _SKANK, _SKANK,
        _PPC, _PPCP, _PPC, _PPCP,
        _SKANK1, _SKANK, _SKANK, _SKANK,
        _PPC, _PPCP, _PPC, _PPCP,
    ], bars=16, backing="punk"),
    # The two eighth-note fills, one level each, before the level that plays both (2026-09-22).
    _drill("7 · Drill: two snares on 4", "The first fill alone, every second bar: the hats stop after the & of 3, two snares fall on the eighths of 4, the crash lands on the 1 after it.",
           152, _PPV1, _pp(_PPP, **_F_SN8)),
    _drill("8 · Drill: down the kit", "The same two bars with the fill walking down the kit: snare on 3 and its &, rack on 4, floor on the & of 4, the crash on the 1.",
           152, _PPV1, _pp(_PPP, **_F_TOMS8)),
    _groove("9 · Eighth-note fills", "Every fourth bar ends in a fill on the eighths: two snares on 4; snare, rack and floor down the kit over 3 and 4; the crash lands on the 1 after it.", 152, [
        _PPV1, _PPP, _PPV, _pp(_PPP, **_F_SN8),
        _PPV1, _PPP, _PPV, _pp(_PPP, **_F_TOMS8),
        _PPV1, _PPP, _PPV, _pp(_PPP, **_F_SN8),
        _PPV1, _PPP, _PPV, _pp(_PPP, **_F_TOMS8),
    ], bars=16, backing="punk"),
    # The four sixteenth fills, one level each, before the level that plays all four.
    _drill("10 · Drill: four snares on 4", "The sixteenths alone: four snares on the 4, the hats stopping after the & of 3, the crash on the 1 after them. Every second bar.",
           148, _PPV1, _pp(_PPP, **_F_SN16)),
    _drill("11 · Drill: snare and rack pairs", "The same four sixteenths split over two drums: two snares on the 4, two racks on its &, every second bar.",
           148, _PPV1, _pp(_PPP, **_F_SNT)),
    _drill("12 · Drill: the walk down", "Eight sixteenths in a row over 3 and 4: four snares, two racks, two floors, every second bar.",
           148, _PPV1, _pp(_PPP, **_F_WALK)),
    _drill("13 · Drill: the whole kit", "Three beats of fill: snare on 2, rack on 3, floor on 4, four sixteenths each, the hats stopping on the & of 1.",
           148, _PPV1, _pp(_PPP, **_F_KIT)),
    _groove("14 · Sixteenth fills", "The fills go to sixteenths: four snares on 4; snare and rack in pairs; the walk down snare, rack, floor over 3 and 4; the whole kit from the 2.", 148, [
        _PPV1, _PPP, _PPV, _pp(_PPP, **_F_SN16),
        _PPV1, _PPP, _PPV, _pp(_PPP, **_F_SNT),
        _PPV1, _PPP, _PPV, _pp(_PPP, **_F_WALK),
        _PPV1, _PPP, _PPV, _pp(_PPP, **_F_KIT),
    ], bars=16, backing="punk"),
    _groove("15 · Crash on the &", "The right crash lands with the kick on the & of 4 of bars 2 and 4, ahead of the bar line; the left crash answers on the 1. Fills stay.", 156, [
        _PPV1, _PPV_AND, _PPV1, _pp(_PPV_AND, **_F_SNT),
        _PPV1, _PPV_AND, _PPV1, _pp(_PPV_AND, **_F_WALK),
    ], backing="punk"),
    _groove("16 · Stabs", "Stop time: the band hits on 1 and the & of 2 and the drums hit with it, kick and both crashes together, nothing between; a snare run on 4 brings the groove back.", 156, [
        _PPV1, _PPP, _PPV, {**_STAB, **_F_RUN},
        _PPV1, _PPV_AND, _PPV1, {**_STAB, "sn": "..........xxxxxx"},
        _PPC, _PPCP, _PPC, {**_STAB, **_F_RUN},
        _PPC, _PPC_AND, _PPC, {**_STAB, "sn": "..........xxxxxx"},
    ], bars=16, backing="punk"),
    _groove("17 · Half-time verse", "The verse sits in half time, snare on 3 and kick on 1 and the & of 2; the chorus doubles back to the backbeat on the crash, the right crash on the & of 4.", 160, [
        _HT1, _HT, _HT, _pp(_HT, **{"hh": "x.x.x.x.x.x.....", "sn": "........X...xxxx"}),
        _PPC, _PPC_AND, _PPC, _pp(_PPC_AND, **{"cl": "x.x.x.x.x.x.x...", "sn": "....X.......xx.."}),
        _HT1, _HT, _HT, _pp(_HT, **{"hh": "x.x.x.x.x.x.....", "sn": "........X...xxxx"}),
        _PPC, _PPC_AND, _PPC, _pp(_PPC_AND, **{"cl": "x.x.x.x.x.x.x...", "sn": "....X.......xx.."}),
    ], bars=16, backing="punk"),
    _groove("18 · The build", "Half-time verse, then the pre-chorus: snare eighths growing from ghosts to accents over the kick on the beats, sixteenths in the last bar; the chorus lands on the crash.", 160, [
        _HT1, _HT, _HT, _pp(_HT, **{"hh": "x.x.x.x.x.x.....", "sn": "........X...xxxx"}),
        *_BUILD,
        _PPC, _PPCP, _PPC, _PPC_AND,
        _PPC, _PPCP, _PPC, _pp(_PPCP, **{"cl": "x.x.x.x.x.x.....", "sn": "....X.......xxxx"}),
    ], bars=16, backing="punk"),
    # "Kick doubles" and "Around the kit" are the beats course's 19 and 25: the names here say what
    # the punk level does with them (level names are unique across the game).
    _groove("19 · Doubles under the push", "Two kicks in a row on the sixteenths, the & a of 1 and of 3, single pedal, under the hats; the pushes and the sixteenth fills return.", 150, [
        _KDV1, _KDP_B, _KDV, _pp(_KDP_B, **_F_SN16),
        _KDV1, _KDP_B, _KDV, _pp(_KDP_B, **_F_WALK),
        _KDV1, _KDP_B, _KDV, _pp(_KDP_B, **_F_SNT),
        _KDV1, _KDP_B, _KDV, _pp(_KDP_B, **_F_KIT),
    ], bars=16, backing="punk"),
    _groove("20 · Ride bridge", "The bridge moves the right hand to the ride over the kick doubles, a rack tom on the & of 4 every other bar; the chorus comes back on the crash.", 160, [
        _RIDE1, _RIDE_T, _RIDE, _RIDE_T,
        _RIDE1, _RIDE_T, _RIDE, _pp(_RIDE, **{"rd": "x.x.x.x.........", "sn": "....X...xxxx....", "t1": "............xx..", "ft": "..............xx"}),
        _PPC, _PPCP, _PPC, _PPC_AND,
        _PPC, _PPCP, _PPC, _pp(_PPCP, **{"cl": "x.x.............", "sn": "....xxxx........", "t1": "........xxxx....", "ft": "............xxxx"}),
    ], bars=16, backing="punk"),
    # The four whole-bar fills, one level each, over the kick doubles groove they live in.
    _drill("21 · Drill: around the kit", "A whole bar of fill every second bar: sixteenth singles, snare on 1, rack on 2, floor on 3 and 4, over the kick doubles.",
           150, _KDV1, {"kk": "x...............", "sn": "xxxx............", "t1": "....xxxx........", "ft": "........xxxxxxxx"}),
    _drill("22 · Drill: toms on the eighths", "The whole-bar fill on the eighths: a tom with the kick on every beat, rack then floor, two snares between each pair.",
           150, _KDV1, {"kk": "x...x...x...x...", "sn": ".xx..xx..xx..xx.", "t1": "x...x...........", "ft": "........x...x..."}),
    _drill("23 · Drill: kick and snare pairs", "Feet and hands trading sixteenth pairs through the whole bar: two kicks, two snares, four times over.",
           150, _KDV1, {"kk": "xx..xx..xx..xx..", "sn": "..xx..xx..xx..xx"}),
    # The closing fill keeps the 1 empty, so the groove bar of this one carries no crash.
    _drill("24 · Drill: crashes to close", "The fill that ends a song: snare, rack and floor over 1, 2 and 3, both crashes with the kick on the 4, and the 1 after it left empty.",
           150, _KDV, {"kk": "x...........x...", "sn": "xxxx............", "t1": "....xxxx........", "ft": "........xxxx....", "cl": "............x...", "cr": "............x..."}),
    _groove("25 · Whole-bar fills", "Whole-bar fills every fourth bar: sixteenth singles snare, rack, floor, floor; toms on the eighths with snare pairs between; kick and snare in pairs; both crashes on the 4 to close.", 150, [
        _KDV1, _KDP_B, _KDV, {"kk": "x...............", "sn": "xxxx............", "t1": "....xxxx........", "ft": "........xxxxxxxx"},
        _KDV1, _KDP_B, _KDV, {"kk": "x...x...x...x...", "sn": ".xx..xx..xx..xx.", "t1": "x...x...........", "ft": "........x...x..."},
        _KDV1, _KDP_B, _KDV, {"kk": "xx..xx..xx..xx..", "sn": "..xx..xx..xx..xx"},
        _KDV1, _KDP_B, _KDV, {"kk": "x...........x...", "sn": "xxxx............", "t1": "....xxxx........", "ft": "........xxxx....", "cl": "............x...", "cr": "............x..."},
    ], bars=16, backing="punk"),
    _groove("26 · Pop punk anthem", "Thirty-two bars with everything: a skank intro with stabs, a verse on the pushes with fills, the build, a chorus washing the crash with the & crashes, a half-time bridge on the ride, the last chorus, both crashes to close.", 168, [
        _SKANK1, _SKANK, _SKANK, {**_STAB, **_F_RUN},                                         # intro
        _PPV1, _PPP, _PPV, _pp(_PPP, **_F_SN16),                                             # verse
        _PPV1, _PPP, _PPV, _pp(_PPP, **_F_WALK),
        *_BUILD,                                                                             # pre-chorus
        _PPC, _PPCP, _PPC, _PPC_AND,                                                         # chorus
        _PPC, _PPCP, _PPC, _pp(_PPCP, **{"cl": "x.x.x.x.x.x.....", "sn": "....X.......xxxx"}),
        {"cr": _CR1, "rd": _H8C, "kk": _HT_K, "sn": _HT_S}, {"rd": _H8, "kk": _HT_K, "sn": _HT_S},   # bridge, half time on the ride
        {"rd": _H8, "kk": _HT_K, "sn": _HT_S}, {"rd": "x.x.x.x.........", "kk": _HT_K, "sn": "........X...xxxx", "t1": "............xx..", "ft": "..............xx"},
        _PPC, _PPC_AND, _PPC, {"kk": "x...........x...", "sn": "xxxx............", "t1": "....xxxx........", "ft": "........xxxx....", "cl": "............x...", "cr": "............x..."},
    ], bars=32, backing="punk"),
]

COURSES = [
    Course("course:pop-punk", "Pop punk", "the driving eighths, the push, the crash wash, the skank, stabs, the build, half time, kick doubles; fills from eighths to the whole kit, each one drilled on its own before the level that plays it",
           POP_PUNK),
]
COURSE = {c.key: c for c in COURSES}

EXERCISES = EXERCISES + RUDIMENTS + KICK_OSTINATOS + HIHAT_LESSONS
LEVELS = EXERCISES + BEATS + [ch for c in COURSES for ch in c.levels]
assert len({ch.name for ch in LEVELS}) == len(LEVELS), "level names must be unique: progress and the coach's playlists are keyed by them"


# ---------------------------------------------------------------------------
# Lanes
# ---------------------------------------------------------------------------
def build_lanes(chart: Chart, kit: dict):
    """One lane per distinct key in the chart: instruments first (wizard order), then
    other MIDI numbers ascending. Returns (lanes, input note -> lane index)."""
    keys = {n.key for n in chart.notes}
    ordered = [k for k in INSTRUMENTS if k in keys]
    ordered += sorted((k for k in keys if k not in INSTRUMENTS), key=lambda k: int(k[1:]))
    lanes, by_note, extra = [], {}, 0
    for i, key in enumerate(ordered):
        if key in INSTRUMENTS:
            notes = set(kit_notes(kit, key))
            if key in CRASH_PAIR and CRASH_PAIR[key] not in keys:
                notes |= set(kit_notes(kit, CRASH_PAIR[key]))      # one crash lane: either pad counts
            lane = Lane(i, key, LABELS[key], COLORS[key], notes)
        else:
            num = int(key[1:])
            lane = Lane(i, key, GM_DRUM_NAMES.get(num, f"note {num}"), EXTRA_PALETTE[extra % len(EXTRA_PALETTE)], {num})
            extra += 1
        lanes.append(lane)
        for num in lane.notes:
            by_note.setdefault(num, i)
    index = {k: i for i, k in enumerate(ordered)}
    for n in chart.notes:
        n.lane = index[n.key]
    return lanes, by_note
