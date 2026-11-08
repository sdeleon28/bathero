"""The Launchpad MK2 as the level's navigator, while the Twister is on bank 2.

The Launchpad is GroupCtl's (bitwig-maps) the rest of the time. The Twister's bank buttons send
a CC on channel 4 (CC 0..3, one per bank, 127 for the bank entered, 0 for the one left), which
both programs hear: on bank 2 GroupCtl's `launchpadlending` package stops painting and listening,
on bank 1 it repaints its page. bathero takes over at the same message, paints the whole surface
(twice, in case a last frame of GroupCtl's lands after the first), and on bank 1 just stops
sending: it never blanks the surface to hand it back, GroupCtl repaints it. The last bank seen is
kept in ~/.config/bathero/twister_bank, since a relaunch of the game does not hear the bank.

The controls are GroupCtl's project explorer (baseexplorer, normalexplorer), on the level instead
of the arranger: the 64 pads are the level's bars in reading order from the top left, each bar
coloured by its phrase (the number keys' phrases), a pad merging up to N bars of one phrase. A
song's two bars of count-in (1 . 3 . | 1 2 3 4) come first, one orange pad each at any zoom: a
pad there starts the count from that bar.

    pad              jump there (one bar of run-up, like the number keys)
    SESSION / USER 1 zoom out / in: 1, 2, 4 .. 32 bars a pad; auto-fit (the finest that fits one
                     page) on every new level and every time bank 2 is entered, until zoomed by hand
    LEFT / RIGHT     the previous / next 64 pads
    MIXER            the loop gesture: press it (white), then two pads, the first and the last of
                     the loop; it marks the range and leaves the gesture (red again)
    MUTE (side)      the loop on / off
    STOP (side)      pause / play

The playing pad blinks white, the marked loop is white, the gesture's first pad too. Pads and
side buttons act on release unless held 0.5 s (GroupCtl's long press, unused here); the top
buttons act on press.
"""
import os
import threading
import time

import mido

PORT_HINT = "launchpad"
BANK_FILE = os.path.expanduser("~/.config/bathero/twister_bank")
LENT_BANK = 2

PADS = [r * 10 + c for r in range(8, 0, -1) for c in range(1, 9)]       # 81..88, 71..78 .. 11..18
PAGE_SIZE = 64
TOP = {104: "up", 105: "down", 106: "left", 107: "right", 108: "session", 109: "user1", 110: "user2", 111: "mixer"}
SIDE = {89: "volume", 79: "pan", 69: "send_a", 59: "send_b", 49: "stop", 39: "mute", 29: "solo", 19: "arm"}
TOP_CC = {v: k for k, v in TOP.items()}
SIDE_NOTE = {v: k for k, v in SIDE.items()}
HOLD_S = 0.5
RESOLUTIONS = (1, 2, 4, 8, 16, 32)

# GroupCtl's ExplorerColors (raw MK2 palette indices)
WHITE, RESOLUTION_COLOR, PAGE_COLOR, SELECT_COLOR, STOP_COLOR, LOOP_COLOR = 3, 41, 49, 5, 7, 41
# one per phrase (ten at most), neighbours far apart; GroupCtl colours by the marker instead
PHRASE_COLORS = [45, 21, 57, 53, 37, 13, 29, 49, 17, 25]
# a song's two bars of count-in, ahead of bar 1: orange, which no phrase (and no section) uses
COUNT_COLOR = 9
# an imported song's sections (sections.json), the user's convention; GroupCtl's names for 41 / 45 / 49 / 5
SECTION_COLORS = {"intro": 41, "verse": 21, "pre-chorus": 57, "chorus": 5, "bridge": 45, "outro": 45,
                  "break": 49, "solo": 13, "inst": 53}
#                  cyan        green        pink               red          blue          blue
#                  purple (breakdown) yellow (solo) magenta (other instrumental)
OTHER_SECTION = 53


def read_bank():
    try:
        return int(open(BANK_FILE).read().strip())
    except (OSError, ValueError):
        return 1


def write_bank(bank):
    try:
        os.makedirs(os.path.dirname(BANK_FILE), exist_ok=True)
        with open(BANK_FILE, "w") as f:
            f.write(f"{bank}\n")
    except OSError:
        pass


class Launchpad:
    """The MK2's ports: hands ("pad", note) / ("side", name) on a short release and ("top", name)
    on a press to `on_event`, from the MIDI thread (the receiver queues them). `show` sends the
    difference between a frame and what is lit."""

    def __init__(self, on_event):
        self.on_event = on_event
        self.inp = self.out = None
        self.name = None
        self.held = {}
        self.shown = {}            # ("note" | "cc", n) -> (colour, blink)
        self.lock = threading.Lock()

    def open(self):
        if self.inp is not None:
            return True
        ins = [n for n in mido.get_input_names() if PORT_HINT in n.lower()]
        outs = [n for n in mido.get_output_names() if PORT_HINT in n.lower()]
        if not ins or not outs:
            return False
        try:
            self.inp = mido.open_input(ins[0], callback=self._cb)
            self.out = mido.open_output(outs[0])
            self.name = ins[0]
            print(f"launchpad: opened '{self.name}'", flush=True)
        except (OSError, IOError):
            self.close()
            return False
        return True

    def _cb(self, msg):
        now = time.perf_counter()
        if msg.type == "control_change" and msg.control in TOP:
            if msg.value > 0:
                self.on_event("top", TOP[msg.control])
            return
        if msg.type not in ("note_on", "note_off"):
            return
        n = msg.note
        if n not in SIDE and n not in PADS:
            return
        if msg.type == "note_on" and msg.velocity > 0:
            self.held[n] = now
            return
        since = self.held.pop(n, None)
        if since is None or now - since > HOLD_S:
            return
        self.on_event(*(("side", SIDE[n]) if n in SIDE else ("pad", n)))

    def forget(self):
        """Repaint everything on the next `show` (the surface was someone else's)."""
        self.shown = {}
        self.held = {}

    def show(self, frame):
        if self.out is None:
            return
        msgs = []
        for key, want in frame.items():
            if self.shown.get(key) == want:
                continue
            self.shown[key] = want
            kind, n = key
            colour, blink = want
            if kind == "cc":
                msgs.append(mido.Message("control_change", channel=0, control=n, value=colour))
            elif blink:
                msgs.append(mido.Message("note_on", channel=0, note=n, velocity=0))
                msgs.append(mido.Message("note_on", channel=1, note=n, velocity=colour))
            else:
                msgs.append(mido.Message("note_on", channel=0, note=n, velocity=colour))
        with self.lock:
            try:
                for m in msgs:
                    self.out.send(m)
            except (OSError, IOError):
                pass

    def close(self):
        for p in (self.inp, self.out):
            if p is not None:
                try:
                    p.close()
                except Exception:
                    pass
        self.inp = self.out = None


def blank():
    """Every pad and button dark."""
    f = {("note", n): (0, False) for n in PADS}
    f.update({("note", n): (0, False) for n in SIDE})
    f.update({("cc", n): (0, False) for n in TOP})
    return f


class Explorer:
    """GroupCtl's explorer state over a level (pure: no MIDI). Lives in the App, so a retry
    (the tempo knob, R) keeps the zoom, the page and the gesture's mode."""

    def __init__(self):
        self.bpp = 1
        self.overridden = False
        self.page = 0
        self.selecting = False
        self.anchor = None         # (first bar, end bar) of the gesture's first pad
        self.level = None
        self.slots = []

    def enter(self):
        """Bank 2 entered: a fresh view, as GroupCtl's explorer page on entry."""
        self.overridden = False
        self.level = None

    def fit(self, bars):
        for bpp in RESOLUTIONS:
            if -(-bars // bpp) <= PAGE_SIZE:
                return bpp
        return RESOLUTIONS[-1]

    def zoom(self, out):
        """SESSION zooms out, USER 1 in; returns the new bars per pad, or None at the limit."""
        if out and self.bpp < RESOLUTIONS[-1]:
            self.bpp *= 2
        elif not out and self.bpp > RESOLUTIONS[0]:
            self.bpp //= 2
        else:
            return None
        self.overridden = True
        return self.bpp

    def toggle_selecting(self):
        self.anchor = None
        self.selecting = not self.selecting

    def pad(self, note):
        """A pad released: ("jump", bar), ("anchor", bars), ("loop", (first bar, end bar)) or None."""
        if note not in PADS:
            return None
        i = PADS.index(note)
        if i >= len(self.slots) or self.slots[i] is None:
            return None
        a, b = self.slots[i]["bars"]
        if not self.selecting:
            return ("jump", a)
        if self.anchor is None:
            self.anchor = (a, b)
            return ("anchor", (a, b))
        lo, hi = min(self.anchor[0], a), max(self.anchor[1], b)
        self.anchor = None
        self.selecting = False
        return ("loop", (lo, hi))

    def grid(self, colours, loop_bars, playing_bar, level, lead=0, groups=None):
        """colours: one per bar of the level; loop_bars: (first, end) or None; playing_bar: the bar
        under the playhead while playing, else None; lead: bars of count-in ahead of bar 0 (a song's
        two), one pad each whatever the zoom, bars -lead..-1; groups: what a pad may merge (one per
        bar, a song's section index; default the colour). Returns the page's 64 slots (None = empty)."""
        groups = colours if groups is None else groups
        bars = len(colours)
        if level != self.level:
            self.level = level
            if not self.overridden:
                self.bpp = self.fit(bars + lead)
            self.page = 0

        def inside(span, a, b):
            return span is not None and a < span[1] and b > span[0]

        blocks = [{"bars": (i, i + 1), "colour": COUNT_COLOR,
                   "selected": inside(self.anchor, i, i + 1),
                   "playing": playing_bar == i} for i in range(-lead, 0)]
        i = 0
        while i < bars:
            j = i + 1
            while j < bars and j - i < self.bpp and groups[j] == groups[i]:
                j += 1
            blocks.append({"bars": (i, j), "colour": colours[i],
                           "selected": (not self.selecting and inside(loop_bars, i, j)) or inside(self.anchor, i, j),
                           "playing": playing_bar is not None and i <= playing_bar < j})
            i = j
        self.pages = max(1, -(-len(blocks) // PAGE_SIZE))
        self.page = max(0, min(self.page, self.pages - 1))
        page = blocks[self.page * PAGE_SIZE:(self.page + 1) * PAGE_SIZE]
        self.slots = page + [None] * (PAGE_SIZE - len(page))
        return self.slots

    def flip(self, delta):
        p = self.page + delta
        if 0 <= p < getattr(self, "pages", 1):
            self.page = p
            return True
        return False

    def frame(self, playing, loop_on):
        """The whole surface for the slots last computed."""
        f = blank()
        for note, s in zip(PADS, self.slots):
            if s is None:
                continue
            if s["playing"]:
                f[("note", note)] = (WHITE, True)
            elif s["selected"]:
                f[("note", note)] = (WHITE, False)
            else:
                f[("note", note)] = (s["colour"], False)
        pages = getattr(self, "pages", 1)
        f[("cc", TOP_CC["session"])] = (RESOLUTION_COLOR if self.bpp < RESOLUTIONS[-1] else 0, False)
        f[("cc", TOP_CC["user1"])] = (RESOLUTION_COLOR if self.bpp > RESOLUTIONS[0] else 0, False)
        f[("cc", TOP_CC["left"])] = (PAGE_COLOR if self.page > 0 else 0, False)
        f[("cc", TOP_CC["right"])] = (PAGE_COLOR if self.page < pages - 1 else 0, False)
        f[("cc", TOP_CC["mixer"])] = (WHITE if self.selecting else SELECT_COLOR, False)
        f[("note", SIDE_NOTE["stop"])] = (STOP_COLOR if playing else 0, False)
        f[("note", SIDE_NOTE["mute"])] = (LOOP_COLOR if loop_on else 0, False)
        return f
