"""Drawing: the play field, flashes, HUD, results. Also shared fonts and helpers."""
import bisect
import math
import time

import pygame

from .chart import COUNT_LABELS, HH_GLYPH, PHRASE_KEYS, art_goal
from .game import CONTRAST_TARGET, Game
from .ghost import PEDAL_CLOSED_CC, openness_label

LOOKAHEAD_S = 2.0        # seconds of chart visible above the line at speed 1.0
FLASH_S = 0.18           # duration of the hit flash
GLOW_H = 220             # height of the lane glow above the hit line, in px
GLOW_STRENGTH = 0.16     # how far the lane background moves toward the judgement color
JUDGE_TEXT_S = 0.6       # how long the judgement word stays up
MISS_FADE_S = 0.6        # missed notes fade out below the line for this long
HIT_LINE_FRAC = 0.82     # vertical position of the hit line (fraction of window height)
NOTE_H = 16
MAX_LANE_W = 260         # lanes never get wider than this; few lanes sit centered

BG = (14, 14, 18)
LANE_BG = (22, 22, 28)
LANE_EDGE = (40, 40, 50)
LINE = (235, 235, 235)
TEXT = (220, 220, 220)
DIM = (120, 120, 130)
ACCENT = (80, 200, 230)
JUDGE_COLORS = {
    "PERFECT": (80, 230, 140),
    "GOOD": (240, 210, 70),
    "OK": (240, 140, 60),
    "MISS": (230, 70, 70),
    "STRAY": (170, 90, 200),
}
DYN_COLORS = {"ACCENT": (255, 255, 255), "TAP": (150, 200, 160), "SOFT": (240, 140, 60), "LOUD": (240, 140, 60)}
DYN_LABELS = {"ACCENT": "ACCENT", "TAP": "tap", "SOFT": "no accent!", "LOUD": "too loud!"}
ACCENT_NOTE_SCALE = 1.7   # accented notes are this much taller
HAND_COLORS = {"R": (245, 90, 90), "L": (80, 200, 230)}   # the sticking strip's hands, and the doubles' brackets
DOUBLE_TICK = 14          # the bracket's ticks reach this far over the notes (px at scale 1)


def lerp(a, b, k):
    k = max(0.0, min(1.0, k))
    return tuple(int(a[i] + (b[i] - a[i]) * k) for i in range(3))


# The pedal positions' colours: the pedal widget's label and, since 2026-09-17, the hi-hat
# notes that ask for one. Tight is a crimson pink, not the kick's salmon red (the kick is two
# lanes away and the first version made a bar of tight hats look like a bar of kicks); mid a
# gold clear of the snare's orange; open the green of "pedal up".
OPENNESS_COLORS = {"tight": (240, 70, 140), "mid": (250, 215, 70), "open": (110, 220, 110)}


STAR = (250, 200, 60)
STAR_OFF = (60, 60, 72)


def draw_stars(surf, fonts, stars, right_x, y, S, size="small", pop=None):
    """Five stars ending at right_x, `stars` of them lit. pop: seconds since the stars
    appeared (the lit ones scale in one after another). Returns the width drawn."""
    r = {"tiny": 5, "small": 7}.get(size, 16) * S
    gap = {"tiny": 3, "small": 6}.get(size, 14) * S
    w = 5 * (2 * r) + 4 * gap
    x0 = right_x - w
    for i in range(5):
        cx = x0 + r + i * (2 * r + gap)
        lit = i < stars
        k = 1.0
        if pop is not None and lit:
            k = max(0.0, min(1.0, (pop - 0.25 * i) / 0.25))
            k = 1.0 + 0.6 * math.sin(math.pi * k) if k < 1 else 1.0
            if pop - 0.25 * i < 0:
                lit = False
        pts = []
        for j in range(10):
            ang = -math.pi / 2 + j * math.pi / 5
            rad = r * k if j % 2 == 0 else r * k * 0.45
            pts.append((cx + rad * math.cos(ang), y + r + rad * math.sin(ang)))
        pygame.draw.polygon(surf, STAR if lit else STAR_OFF, pts)
    return w


def draw_hihat_state(surf, fonts, ghosts, cx, cy, S=1.0, color=(245, 230, 80)):
    """Two cymbals whose gap follows CC4, the openness class, the last stroke's zone.
    (cx, cy) is the centre of the widget; it is about 120 x 110 px at S = 1."""
    if ghosts is None:
        return
    cc = ghosts.pedal_cc
    closed = max(0.0, min(1.0, cc / PEDAL_CLOSED_CC))
    label = openness_label(cc)
    ocol = OPENNESS_COLORS[label]
    now = time.perf_counter()
    w, hh = 88 * S, 12 * S
    gap = (2 + 26 * (1 - closed)) * S
    top_y = cy - gap / 2 - hh
    bot_y = cy + gap / 2
    flash = 0.0
    if ghosts.last_stroke and now - ghosts.last_stroke[0] < 0.25:
        flash = 1 - (now - ghosts.last_stroke[0]) / 0.25
    rim = lerp(color, (255, 255, 255), flash)
    pygame.draw.ellipse(surf, lerp(LANE_BG, color, 0.55), (cx - w / 2, top_y, w, hh))
    pygame.draw.ellipse(surf, rim, (cx - w / 2, top_y, w, hh), max(1, int(2 * S)))
    pygame.draw.ellipse(surf, lerp(LANE_BG, color, 0.35), (cx - w / 2, bot_y, w, hh))
    pygame.draw.ellipse(surf, color, (cx - w / 2, bot_y, w, hh), max(1, int(2 * S)))
    pygame.draw.line(surf, DIM, (cx, top_y - 10 * S), (cx, bot_y + hh + 6 * S), max(1, int(2 * S)))
    fonts.center(surf, f"{label.upper()}  {cc}", fonts.small, ocol, bot_y + hh + 20 * S, cx)
    if ghosts.last_stroke and now - ghosts.last_stroke[0] < 1.5:
        _, note, vel, zone, op = ghosts.last_stroke
        fonts.center(surf, f"{op} {zone} {note} v{vel}", fonts.small, lerp(TEXT, BG, (now - ghosts.last_stroke[0]) / 1.5),
                     bot_y + hh + 38 * S, cx)
    elif ghosts.last_ghost and now - ghosts.last_ghost[0] < 1.5:
        _, note, vel, why = ghosts.last_ghost
        fonts.center(surf, f"ignored {note} v{vel}: {why}", fonts.small, lerp(DIM, BG, (now - ghosts.last_ghost[0]) / 1.5),
                     bot_y + hh + 38 * S, cx)


class Fonts:
    """Font set for a given window scale (1.0 = 720 px tall)."""

    def __init__(self, scale=1.0):
        pygame.font.init()
        name = pygame.font.match_font("menlo,monaco,dejavusansmono,consolas,couriernew") or None
        self.scale = scale
        px = lambda n: max(8, round(n * scale))
        self.tiny = pygame.font.Font(name, px(12))
        self.small = pygame.font.Font(name, px(16))
        self.mid = pygame.font.Font(name, px(24))
        self.large = pygame.font.Font(name, px(36))
        self.big = pygame.font.Font(name, px(56))
        self.huge = pygame.font.Font(name, px(88))
        self.cache = {}

    def text(self, s, font, color):
        key = (s, id(font), color)
        surf = self.cache.get(key)
        if surf is None:
            surf = font.render(s, True, color)
            if len(self.cache) > 1024:
                self.cache.clear()
            self.cache[key] = surf
        return surf

    def center(self, surf, s, font, color, y, x=None):
        ts = self.text(s, font, color)
        x = surf.get_width() / 2 if x is None else x
        surf.blit(ts, (x - ts.get_width() / 2, y - ts.get_height() / 2))
        return ts.get_height()


STRIP_ROW = 24                  # strokes per row of the sticking strip


def wrap(font, text, width):
    """The words of `text` in rows no wider than `width` pixels."""
    rows, row = [], ""
    for word in text.split():
        cand = f"{row} {word}".strip()
        if row and font.size(cand)[0] > width:
            rows.append(row)
            row = word
        else:
            row = cand
    if row:
        rows.append(row)
    return rows


class Renderer:
    def __init__(self, game: Game, size, fonts: Fonts, ghosts=None):
        self.ghosts = ghosts
        self.game = game
        self.note_times = None                # chart note times, built on first use by the sticking strip
        self.double_runs = None               # chart.doubles(), built on first use
        self.w, self.h = size
        self.f = fonts
        self.judge_surfs = {k: fonts.big.render(k, True, c) for k, c in JUDGE_COLORS.items()}
        self.s = self.h / 720                            # pixel scale relative to the 720p design
        n = len(game.lanes)
        margin = 40 * self.s
        self.top_inset = 0           # the app's stream badge sits top right: the HUD's right column starts under it
        self.lane_w = min((self.w - 2 * margin) / n, MAX_LANE_W * self.s)
        left = (self.w - self.lane_w * n) / 2
        self.lane_x = [left + i * self.lane_w for i in range(n)]
        self.line_y = int(self.h * HIT_LINE_FRAC)
        self.note_h = int(NOTE_H * self.s)
        self.glow_h = int(GLOW_H * self.s)
        self.pps = (self.line_y - 60 * self.s) / LOOKAHEAD_S     # pixels per second at speed 1.0
        self.finished_at = None                                  # set by the play screen for the star animation
        self.marker_labels = []
        self.transport = {}      # set by the play screen: practice, the loop range, the pending l gesture
        self.hints = None        # (attempts, [lines]) from the play screen: what to work on, on every try short of five stars
        self.scroll_fixed = False   # D: the notes fall at pps whatever the tempo (else pps times the rate)
        self.offset_fit = None   # latency.fit_game's dict: the offset line under the timing
        self.intro = None        # the level's intro card while the play screen waits for the snare (intro_card)
        self.edit = None         # songedit.SongEditor while a song is being edited (E): it draws the notes

    def y_for(self, note_t, now):
        zoom = self.edit.zoom if self.edit is not None else 1.0
        return self.line_y - (note_t - now) * self.pps * (1.0 if self.scroll_fixed else self.game.speed) * zoom

    def note_box(self, n):
        """A note's rectangle: (x, width, height, accent). Accents are taller; in a dynamics
        chart the taps are narrower."""
        g, S = self.game, self.s
        x = int(self.lane_x[n.lane] + 6 * S)
        w = int(self.lane_w - 14 * S)
        accent = n.accent or (not g.chart.dynamics and n.velocity >= 100)
        nh = int(self.note_h * ACCENT_NOTE_SCALE) if accent else self.note_h
        if g.chart.dynamics and not accent:
            x, w = x + int(w * 0.15), int(w * 0.7)
        return x, w, nh, accent

    def doubles(self, surf, now, top, shift=0.0, within=None):
        """A bracket beside every double (Chart.doubles) on the side of the hand that plays
        it, in that hand's colour from the sticking strip: ] to the right of an R R, [ to
        the left of an L L, so which hand doubles is read at a glance. The bracket hugs the
        notes and goes with the first stroke of the double (a bracket left over the second
        note distracted, 2026-09-16); a jump that lands inside a double leaves no bracket over
        the skipped note. within / shift: the loop's head, drawn a lap
        early with the coming notes."""
        g, S = self.game, self.s
        if self.double_runs is None:
            self.double_runs = g.chart.doubles()
        tick, wd = DOUBLE_TICK * S, max(2, int(3 * S))
        for i, j in self.double_runs:
            notes = [n for n in g.notes[i:j + 1] if n.state != "skip"]
            if len(notes) < 2:
                continue
            first, last = notes[0], notes[-1]
            if within is not None and not (within[0] <= first.t and last.t <= within[1]):
                continue
            t0, t1 = first.t + shift, last.t + shift
            if t0 > top or t1 < now - MISS_FADE_S:
                continue
            colour = HAND_COLORS.get(last.hand)
            if colour is None:
                continue
            if within is None and any(n.state != "pending" for n in notes):
                continue                       # the first stroke played (or missed): the bracket has done its job
            boxes = [self.note_box(n) for n in notes]
            x = min(b[0] for b in boxes)
            right = max(b[0] + b[1] for b in boxes)
            y0 = int(self.y_for(t1, now) - boxes[-1][2] / 2 - 3 * S)      # the later note is higher up
            y1 = int(self.y_for(t0, now) + boxes[0][2] / 2 + 3 * S)
            if last.hand == "R":
                xb, reach = int(right + 2 * S), -tick
            else:
                xb, reach = int(x - 2 * S - wd), tick
            pygame.draw.rect(surf, colour, (xb, y0, wd, y1 - y0 + wd))
            xt = int(min(xb, xb + reach))
            pygame.draw.rect(surf, colour, (xt, y0, int(abs(reach) + wd), wd))
            pygame.draw.rect(surf, colour, (xt, y1, int(abs(reach) + wd), wd))

    def note(self, surf, n, t_draw, now, coming=False):
        """One chart note at chart time t_draw (the loop's head is drawn a lap early, and
        then always as pending). Skipped notes (before a jump) are not drawn at all."""
        g, f, S = self.game, self.f, self.s
        if n.state == "skip" or (n.state == "hit" and not coming):
            return
        y = self.y_for(t_draw, now)
        if y > self.h + self.note_h:
            return
        x, w, nh, accent = self.note_box(n)
        color = g.lanes[n.lane].color
        missed = n.state == "miss" and not coming
        if missed:
            age = (now - t_draw) / MISS_FADE_S
            if age > 1:
                return
            color = lerp(JUDGE_COLORS["MISS"], BG, age)
        elif g.chart.dynamics and not accent:
            color = lerp(color, LANE_BG, 0.35)            # taps: dimmer
        elif n.art and n.art.split()[0] in OPENNESS_COLORS:
            # A hi-hat note asking for a pedal position wears the position's colour (the same
            # the pedal widget shows: tight crimson, mid gold, open green), so a run of tight
            # hats and the bars where they open read as groups from across the room.
            color = OPENNESS_COLORS[n.art.split()[0]]
        rect = (x, int(y - nh / 2), w, nh)
        pygame.draw.rect(surf, color, rect, border_radius=int(6 * S))
        if accent:
            pygame.draw.rect(surf, (255, 255, 255), rect, 2, border_radius=int(6 * S))
        if not missed:
            if n.art:
                label = HH_GLYPH.get(n.art, "")                 # + tight, / mid, o open, > edge, ^ chick
            else:
                label = (">" + n.hand if accent and n.hand else (">" if accent else n.hand))
            if label:
                f.center(surf, label, f.mid if (accent or n.art) else f.small, (20, 20, 24), y, x + w / 2)

    def draw(self, surf, fps=0.0):
        g = self.game
        f = self.f
        S = self.s
        wall = time.perf_counter()
        with g.lock:
            now = g.song_time(wall)
            flashes = list(g.flashes)
            lo = g.cursor
            paused, finished = g.paused, g.finished
            loop, count_in_end = g.loop, g.count_in_end
            score, combo, counts, offset, speed = g.score, g.combo, dict(g.counts), g.offset_ms, g.speed

        surf.fill(BG)
        for i, lane in enumerate(g.lanes):
            x = int(self.lane_x[i])
            pygame.draw.rect(surf, LANE_BG, (x, 0, int(self.lane_w) - 2, self.h))
            pygame.draw.line(surf, LANE_EDGE, (x, 0), (x, self.h))
            cx = x + self.lane_w / 2
            f.center(surf, lane.label, f.small, lane.color, self.line_y + 34 * S, cx)
            sub = "/".join(map(str, sorted(lane.notes))) if lane.notes else "no pad assigned"
            f.center(surf, sub, f.small, DIM if lane.notes else JUDGE_COLORS["MISS"], self.line_y + 52 * S, cx)

        # flashes: lane glow first so notes draw on top
        for fl in flashes:
            age = (wall - fl.wall_t) / FLASH_S
            if age <= 1:
                glow = lerp(LANE_BG, JUDGE_COLORS[fl.judge], GLOW_STRENGTH * (1 - age))
                pygame.draw.rect(surf, glow, (int(self.lane_x[fl.lane]), self.line_y - self.glow_h, int(self.lane_w) - 2, self.glow_h))

        pygame.draw.line(surf, LINE, (self.lane_x[0], self.line_y), (self.lane_x[-1] + self.lane_w, self.line_y), max(2, int(3 * S)))
        for i, lane in enumerate(g.lanes):
            cx = int(self.lane_x[i] + self.lane_w / 2)
            pygame.draw.circle(surf, lane.color, (cx, self.line_y), int(9 * S), 2)

        # notes: from a bit before the cursor so missed ones can fade out
        top = now + LOOKAHEAD_S / (1.0 if self.scroll_fixed else speed) + 0.2
        if self.edit is not None:
            # the song editor: every note on screen, above and below the line, on its grid
            top = self.edit.window()[1]
            self.edit.draw_grid(surf, now)
            self.markers(surf, now, top, loop)
            self.edit.draw_notes(surf, now)
            notes = []
        else:
            self.markers(surf, now, top, loop)
            notes = g.notes[max(0, lo - 64):]
        for n in notes:
            if n.t > top:
                break
            self.note(surf, n, n.t, now)
        if self.edit is None:
            self.doubles(surf, now, top)
        if loop is not None and self.edit is None:
            # the loop's head, drawn above the line during the tail of the lap, so the notes
            # of the first bar come down instead of appearing on the line at the wrap
            a, b = loop
            ahead = top - b
            if ahead > 0:
                for n in g.notes:
                    if n.t < a:
                        continue
                    if n.t > a + ahead:
                        break
                    self.note(surf, n, n.t + (b - a), now, coming=True)
                self.doubles(surf, now, top, shift=b - a, within=(a, a + ahead))
        self.marker_names(surf)

        # flashes: ring at the line + error number, drawn the frame after the hit arrives
        latest = None
        newest_in_lane = {}                      # the dynamic tag is shown for the newest flash per lane only
        for fl in flashes:
            newest_in_lane[fl.lane] = fl
        for fl in flashes:
            age = (wall - fl.wall_t) / FLASH_S
            cx = int(self.lane_x[fl.lane] + self.lane_w / 2)
            color = JUDGE_COLORS[fl.judge]
            if age <= 1:
                k = 1 - age
                pygame.draw.circle(surf, lerp(BG, color, k), (cx, self.line_y), int((14 + 50 * age) * S), max(1, int(6 * k * S)))
                pygame.draw.circle(surf, color, (cx, self.line_y), int(9 * S))
            jt = (wall - fl.wall_t) / JUDGE_TEXT_S
            if jt <= 1:
                latest = fl
                if fl.error_ms is not None:
                    label = f"{'+' if fl.error_ms >= 0 else '-'}{abs(fl.error_ms):.0f}"
                    ts = f.text(label, f.mid, lerp(color, BG, jt))
                    surf.blit(ts, (cx - ts.get_width() / 2, self.line_y - (70 + 40 * jt) * S))
                if fl.dyn and newest_in_lane[fl.lane] is fl:
                    ds = f.text(DYN_LABELS[fl.dyn], f.mid if fl.dyn in ("SOFT", "LOUD") else f.small, lerp(DYN_COLORS[fl.dyn], BG, jt))
                    surf.blit(ds, (cx - ds.get_width() / 2, self.line_y - (44 + 40 * jt) * S))
                if fl.art and newest_in_lane[fl.lane] is fl:
                    # matched: what was heard ("tight edge"); wrong: the openness wanted, never the
                    # zone, since the zone is not judged (edge strokes read "tight body" as a
                    # correction until 2026-09-19: the pop punk course writes t, the user plays the edge)
                    want, ok, played = fl.art
                    col = DYN_COLORS["ACCENT"] if ok else DYN_COLORS["SOFT"]
                    ds = f.text((played or want) if ok else f"want {art_goal(want)}", f.small if ok else f.mid, lerp(col, BG, jt))
                    surf.blit(ds, (cx - ds.get_width() / 2, self.line_y - (44 + 40 * jt) * S))

        if latest is not None:
            jt = (wall - latest.wall_t) / JUDGE_TEXT_S
            js = self.judge_surfs[latest.judge]
            js.set_alpha(int(255 * (1 - jt ** 2)))
            surf.blit(js, (self.w / 2 - js.get_width() / 2, self.h * 0.30 - 10 * jt * S))
            js.set_alpha(255)
            if latest.error_ms is not None:
                e = latest.error_ms
                label = "on time" if abs(e) < 0.5 else f"{abs(e):.0f} ms {'early' if e < 0 else 'late'}"
                es = f.text(label, f.mid, JUDGE_COLORS[latest.judge])
                es.set_alpha(int(255 * (1 - jt)))
                surf.blit(es, (self.w / 2 - es.get_width() / 2, self.h * 0.30 + 60 * S))
                es.set_alpha(255)
            if latest.dyn in ("SOFT", "LOUD"):
                ds = f.text("ACCENT MISSING" if latest.dyn == "SOFT" else "TAP TOO LOUD", f.mid, DYN_COLORS[latest.dyn])
                ds.set_alpha(int(255 * (1 - jt)))
                surf.blit(ds, (self.w / 2 - ds.get_width() / 2, self.h * 0.30 + 90 * S))
                ds.set_alpha(255)
            elif latest.art and not latest.art[1]:
                ds = f.text(f"HAT: {art_goal(latest.art[0]).upper()}", f.mid, DYN_COLORS["SOFT"])
                ds.set_alpha(int(255 * (1 - jt)))
                surf.blit(ds, (self.w / 2 - ds.get_width() / 2, self.h * 0.30 + 90 * S))
                ds.set_alpha(255)


        # HUD with a backing so it stays readable over notes
        dyn = g.dynamics(32) if g.chart.dynamics else None
        expr = g.chart.expression
        backing = pygame.Surface((int(360 * S), int((150 if dyn is not None else (140 if expr else 100)) * S)))
        backing.fill(BG)
        backing.set_alpha(235)
        surf.blit(backing, (0, 0))
        if dyn is not None:
            self.dynamics_meter(surf, dyn, 12 * S, 96 * S)
        elif expr:
            a = g.art_counts
            surf.blit(f.text(f"hat articulations {a['ok']}/{a['ok'] + a['wrong']}",
                             f.small, TEXT if a["wrong"] == 0 else JUDGE_COLORS["OK"]), (12 * S, 96 * S))
            x = 12 * S
            for word, col in (("+ tight", OPENNESS_COLORS["tight"]), ("/ mid", OPENNESS_COLORS["mid"]),
                              ("o open", OPENNESS_COLORS["open"]), ("> edge", DIM), ("^ foot", DIM)):
                ts = f.text(word, f.small, col)
                surf.blit(ts, (x, 116 * S))
                x += ts.get_width() + 24 * S
        surf.blit(f.text(g.chart.title, f.mid, ACCENT), (12 * S, 8 * S))
        surf.blit(f.text(f"score {score}   combo {combo}", f.mid, TEXT), (12 * S, 38 * S))
        line = f"P {counts['PERFECT']}  G {counts['GOOD']}  O {counts['OK']}  M {counts['MISS']}  S {counts['STRAY']}"
        if self.ghosts is not None and self.ghosts.filtered:
            line += f"  ghosts {self.ghosts.filtered}"
        surf.blit(f.text(line, f.small, DIM), (12 * S, 70 * S))
        right = [f"{fps:5.0f} fps", f"offset {offset:+.0f} ms",
                 (f"tempo {g.chart.rate:.2f}x" if g.chart.rate != 1.0 else "tempo 1x") + ("  ·  scroll fixed" if self.scroll_fixed else ""),
                 f"{g.chart.bpm:.0f} bpm" + (f" (of {g.chart.bpm / g.chart.rate:.0f})" if g.chart.rate != 1.0 else ""),
                 f"guide {'on' if g.guide else 'off'}" + ("" if g.sounds is None or g.sounds.drums else "  ·  drums off"),
                 f"backing {'on' if g.track_enabled('backing') else 'off'}" if 'backing' in g.tracks else "no backing",
                 f"metronome {g.metronome_mode}" + ("" if g.metronome_mode == "off" or g.metro_volume == 1.0 else f" {g.metro_volume:.0%}")]
        if g.chart.dynamics:
            for inst in dict.fromkeys(n.key for n in g.chart.notes):        # one line per body the level plays
                a, tp = g.dyn_band(inst)
                right.append(f"{inst}: accent >= {a}  tap <= {tp}  ({g.dyn_scale(inst):.0%}{', night' if g.night else ''})")
        tr = self.transport
        if tr.get("practice"):
            right.append("practice (P): nothing saved")
        elif g.seeked:
            right.append("rehearsal: jumped, not saved")
        for i, s in enumerate(right):
            ts = f.text(s, f.small, DIM)
            surf.blit(ts, (self.w - ts.get_width() - 12 * S, (10 + i * 20) * S + self.top_inset))

        self.metronome(surf, now)
        if any(l.key == "hihat" for l in g.lanes):     # under the right column, whatever it lists
            draw_hihat_state(surf, f, self.ghosts, self.w - 110 * S, (len(right) * 20 + 80) * S + self.top_inset, S)

        self.transport_bar(surf, now, loop, count_in_end)
        self.loop_badge(surf, loop)
        if self.edit is not None:
            self.edit.draw_card(surf, now)
        elif paused and self.intro:
            self.intro_card(surf)
        elif paused:
            f.center(surf, "PAUSED", f.huge, TEXT, self.h * 0.45)
            f.center(surf, "space resume · R restart · Esc menu" + (" · E edit the song" if g.chart.song else ""),
                     f.small, DIM, self.h * 0.45 + 70 * S)
        elif now < 0:
            beats_left = math.ceil(-now / g.beat)
            f.center(surf, str((beats_left - 1) % 4 + 1), f.huge, TEXT, self.h * 0.45)
            f.center(surf, g.chart.desc, f.mid, DIM, self.h * 0.45 + 80 * S)
        elif count_in_end is not None and now < count_in_end:
            # the run-up to a jump: the phrase it lands in, named, over a smaller count (the
            # huge digits read as "the number I pressed" and they are not)
            beats_left = math.ceil((count_in_end - now) / g.beat)
            i = g.chart.phrase_at(count_in_end)
            if i is not None:
                at_phrase = abs(g.chart.phrase_bounds()[i] - count_in_end) < 1e-4
                label = f"phrase {(i + 1) % 10}" if at_phrase else g.chart.place(count_in_end)   # a Launchpad pad lands on any bar
                f.center(surf, label, f.large, ACCENT, self.h * 0.45 - 44 * S)
            f.center(surf, f"in {(beats_left - 1) % 4 + 1}", f.big, TEXT, self.h * 0.45 + 16 * S)
        if finished:
            self.results(surf)

    def loop_colour(self, loop):
        return ACCENT if loop is not None else lerp(ACCENT, BG, 0.5)

    def markers(self, surf, now, top, loop):
        """Phrase starts scrolling down the lanes with their key's number, like a DAW's
        section markers, so what 1..0 address is on the highway itself. The loop's start and
        end in the loop's colour; while it runs the start is drawn again a lap early, at the
        end, since that is where the notes go back to."""
        g, f, S = self.game, self.f, self.s
        bounds = g.chart.phrase_bounds()
        n = len(bounds) - 1
        span = self.transport.get("span")                 # the marked loop (chart seconds), phrases or bars
        marks = []                                        # (chart time, label, colour, width)
        head = None
        for i in range(n):
            key = str((i + 1) % 10)
            if span is not None and abs(bounds[i] - span[0]) < 1e-4:
                head = bounds[i]
                marks.append((bounds[i], f"loop {key}", self.loop_colour(loop), 2))
            else:
                marks.append((bounds[i], key, DIM, 1))
        if span is not None:
            if head is None:                              # a loop of bars that starts inside a phrase
                head = span[0]
                marks.append((span[0], "loop", self.loop_colour(loop), 2))
            if loop is None:
                marks.append((span[1], "loop end", self.loop_colour(loop), 2))
            else:
                # the wrap: the loop's head again where its tail ends, over the coming notes
                shift = span[1] - span[0]
                for t, label, col, wd in list(marks):
                    if span[0] - 1e-4 <= t < span[1] - 1e-4 and t + shift <= top:
                        marks.append((t + shift, (label + " again") if t == head else label, col, wd))
        x0, x1 = int(self.lane_x[0]), int(self.lane_x[-1] + self.lane_w)
        self.marker_labels = []
        landing = self.game.count_in_end if self.game.count_in_end is not None and now < self.game.count_in_end else None
        for t, label, col, wd in marks:
            if t > top or t < now - 0.5 or (landing is not None and t < landing and wd == 1):
                continue
            y = self.y_for(t, now)
            if y < -4 or y > self.line_y + 2:
                continue
            pygame.draw.line(surf, col, (x0, int(y)), (x1, int(y)), max(1, int(wd * S)))
            self.marker_labels.append((label, TEXT if wd == 1 else col, y))

    def marker_names(self, surf):
        """The markers' labels, after the notes so a note never hides them."""
        f, S = self.f, self.s
        x0 = int(self.lane_x[0])
        for label, col, y in self.marker_labels:
            ts = f.text(label, f.small, col)
            back = pygame.Surface((ts.get_width() + 8 * S, ts.get_height()))
            back.fill(BG)
            back.set_alpha(200)
            surf.blit(back, (x0 + 2 * S, y - ts.get_height() - 1 * S))
            surf.blit(ts, (x0 + 6 * S, y - ts.get_height() - 1 * S))

    def loop_badge(self, surf, loop):
        """Top centre: the loop that runs, or the one marked and off, or the gesture typed."""
        f, S = self.f, self.s
        tr = self.transport
        pending, span, label = tr.get("pending"), tr.get("span"), tr.get("label")
        y = 126 * S                                   # under the metronome's beat squares
        if pending is not None or span is not None:
            back = pygame.Surface((int(300 * S), int(48 * S)))
            back.fill(BG)
            back.set_alpha(215)
            surf.blit(back, (surf.get_width() / 2 - 150 * S, y - 14 * S))
        if pending is not None:
            what = "type the last phrase" if pending else "type the first and last phrase"
            f.center(surf, f"loop  l{pending}_", f.mid, ACCENT, y)
            f.center(surf, what, f.small, ACCENT, y + 24 * S)
        elif span is not None:
            if loop is not None:
                f.center(surf, f"LOOP {label}", f.mid, ACCENT, y)
                f.center(surf, "\\ stops it", f.small, DIM, y + 24 * S)
            else:
                f.center(surf, f"loop {label} off", f.mid, DIM, y)
                f.center(surf, "\\ starts it again", f.small, DIM, y + 24 * S)

    def transport_bar(self, surf, now, loop, count_in_end=None):
        """The ruler under the lanes: ten slots of one width, one per number key, the
        level's phrases in the first ones and the rest empty; the playhead sweeps the
        level's part, the phrase being played is lit, the marked loop is a framed band, the
        digits typed after l light their slots, and a line says what 1..0 do. During the
        run-up to a jump the ruler already shows where it lands (the key's slot lit, the
        playhead parked at its start): otherwise pressing 3 lights slot 2 for a bar."""
        g, f, S = self.game, self.f, self.s
        tr = self.transport
        bounds = g.chart.phrase_bounds()
        n = len(bounds) - 1
        landing = count_in_end is not None and now < count_in_end
        pos = count_in_end if landing else now
        cur = g.chart.phrase_at(max(pos, 0.0))
        span = tr.get("span")
        pending = tr.get("pending")
        typed = set() if pending is None else {(int(d) - 1) % 10 for d in pending}
        W = min(self.w - 240 * S, 720 * S)
        slot = W / PHRASE_KEYS
        x0, H = self.w / 2 - W / 2, 26 * S
        y = self.line_y + 60 * S      # under the lane labels, over the hint and the session line

        def X(t):                                   # slot i is phrase i, whatever its length
            t = max(bounds[0], min(t, bounds[-1]))
            i = g.chart.phrase_at(t)
            if i is None:
                return x0 + n * slot
            return x0 + (i + (t - bounds[i]) / max(1e-6, bounds[i + 1] - bounds[i])) * slot

        for i in range(PHRASE_KEYS):
            xa, xb = x0 + i * slot, x0 + (i + 1) * slot
            box = pygame.Rect(int(xa), int(y), int(xb) - int(xa), int(H))
            if i >= n:                              # past the level: the key does nothing here
                pygame.draw.rect(surf, lerp(LANE_BG, DIM, 0.25), box, 1)
                f.center(surf, str((i + 1) % 10), f.small, lerp(BG, DIM, 0.4), y + H / 2, (xa + xb) / 2)
                continue
            inside = span is not None and bounds[i] < span[1] - 1e-4 and bounds[i + 1] > span[0] + 1e-4
            fill = LANE_BG
            if inside:
                fill = lerp(LANE_BG, ACCENT, 0.45 if loop is not None else 0.18)
            if i in typed:
                fill = lerp(fill, ACCENT, 0.5)
            if i == cur:
                fill = lerp(fill, (255, 255, 255), 0.6)
            pygame.draw.rect(surf, fill, box)
            if i > 0:
                pygame.draw.line(surf, LANE_EDGE if i != cur else TEXT, (int(xa), int(y)), (int(xa), int(y + H)))
            color = (20, 20, 24) if i == cur else TEXT
            f.center(surf, str((i + 1) % 10), f.small, color, y + H / 2, (xa + xb) / 2)
        pygame.draw.rect(surf, DIM, pygame.Rect(int(x0), int(y), int(n * slot), int(H)), 1)
        if span is not None:                          # the loop's frame around its band
            frame = pygame.Rect(int(X(span[0]) - 2 * S), int(y - 3 * S),
                                int(X(span[1]) - X(span[0]) + 4 * S), int(H + 6 * S))
            pygame.draw.rect(surf, self.loop_colour(loop), frame, max(1, int(2 * S)), border_radius=int(4 * S))
        # the playhead, over everything, dark-edged so it reads on any cell
        px = int(X(max(bounds[0], min(pos, bounds[-1]))))
        pygame.draw.line(surf, BG, (px, int(y - 6 * S)), (px, int(y + H + 6 * S)), max(3, int(6 * S)))
        pygame.draw.line(surf, TEXT, (px, int(y - 5 * S)), (px, int(y + H + 5 * S)), max(1, int(2 * S)))
        f.center(surf, "phrase", f.tiny, DIM, y + H / 2, x0 - 24 * S)
        if landing or now >= 0:
            ts = f.text(f"{g.chart.place(pos)} of {g.chart.bars}", f.tiny, ACCENT if landing else DIM)
            surf.blit(ts, (x0 + W + 8 * S, y + H / 2 - ts.get_height() / 2))
        if pending is not None:
            what = "type the last phrase" if pending else "type the first and last phrase"
            hint, col = f"loop: l{pending}_  ·  {what}", ACCENT
        else:
            hint, col = "1-0 jump to a phrase  ·  l35 loop 3-5  ·  \\ loop on/off", DIM
        f.center(surf, hint, f.small, col, y + H + 14 * S)

    def dynamics_meter(self, surf, dyn, x, y):
        """Accent and tap tallies plus a contrast bar (median accent / median tap velocity
        over the last strokes) with the target marked."""
        f, S = self.f, self.s
        # x/y: notes played as the right dynamic / chart notes resolved so far (hit or missed), so the
        # totals end at the chart's own and match the results (2026-09-12: the HUD said 65/75, the
        # results 65/80; strokes between the two thresholds and missed notes counted nowhere)
        dc, seen = self.game.dyn_counts, self.game.dyn_seen
        line = f"accents {dc['ACCENT']}/{seen['accent']}  taps {dc['TAP']}/{seen['tap']}"
        surf.blit(f.text(line, f.small, TEXT), (x, y))
        c = dyn.get("contrast")
        bw, bh = 200 * S, 10 * S
        by = y + 26 * S
        pygame.draw.rect(surf, LANE_BG, (x, by, bw, bh), border_radius=int(5 * S))
        top = 2.5                                   # bar spans ratios 1.0 .. 2.5
        tx = x + bw * (CONTRAST_TARGET - 1) / (top - 1)
        if c is not None:
            k = max(0.0, min(1.0, (c - 1) / (top - 1)))
            col = JUDGE_COLORS["PERFECT"] if c >= CONTRAST_TARGET else JUDGE_COLORS["OK"]
            pygame.draw.rect(surf, col, (x, by, bw * k, bh), border_radius=int(5 * S))
        pygame.draw.line(surf, TEXT, (tx, by - 3 * S), (tx, by + bh + 3 * S), max(1, int(2 * S)))
        label = f"contrast {c:.2f}x" if c is not None else "contrast -"
        surf.blit(f.text(label, f.small, DIM), (x + bw + 12 * S, by - 4 * S))

    def metronome(self, surf, t):
        """Four beat squares, each split into the current subdivision, lit in time."""
        g, f, S = self.game, self.f, self.s
        pos = g.chart.beat_pos(t)                # in beats, negative during the count-in
        bar_beat = math.floor(pos / 4) * 4
        # subdivision per beat of the current bar: mixed rudiments switch inside the bar
        subs = [g.chart.subdivision_at(g.chart.beat_time(bar_beat + b)) for b in range(4)]
        size, gap = 74 * S, 10 * S
        total = 4 * size + 3 * gap
        x0, y0 = self.w / 2 - total / 2, 10 * S
        strip_rows = math.ceil(len(g.chart.sticking) / STRIP_ROW) if g.chart.sticking else 0
        strip_h = (30 + 32 * (strip_rows - 1) + (10 if g.chart.accents else 0)) * S if strip_rows else 0
        strip_w = min(len(g.chart.sticking), STRIP_ROW) * 26 * S if strip_rows else 0
        panel_w = max(total, strip_w) + 40 * S
        panel = pygame.Surface((int(panel_w), int(size + 44 * S + strip_h)))
        panel.fill(BG)
        panel.set_alpha(215)
        surf.blit(panel, (int(self.w / 2 - panel_w / 2), 0))
        beat_i = int(math.floor(pos)) % 4
        frac = pos - math.floor(pos)
        sub = subs[beat_i]
        sub_i = min(sub - 1, int(frac * sub))
        prog = frac * sub - sub_i                # 0..1 inside the current cell
        for b in range(4):
            sub = subs[b]
            labels = COUNT_LABELS.get(sub, [str(i + 1) for i in range(sub)])
            x = x0 + b * (size + gap)
            rect = pygame.Rect(int(x), int(y0), int(size), int(size))
            pygame.draw.rect(surf, LANE_BG, rect, border_radius=int(8 * S))
            cw = size / sub
            for k in range(sub):
                cell = pygame.Rect(int(x + k * cw), int(y0), int(cw) + 1, int(size))
                if b == beat_i and k == sub_i:
                    base = (255, 255, 255) if k == 0 else ACCENT
                    fill = lerp(base, LANE_BG, 0.25 + 0.6 * prog)
                    pygame.draw.rect(surf, fill, cell.inflate(-2, -2), border_radius=int(6 * S))
                label = str(b + 1) if k == 0 else labels[k]
                color = (20, 20, 24) if (b == beat_i and k == sub_i and prog < 0.5) else (TEXT if k == 0 else DIM)
                f.center(surf, label, f.small if sub > 2 else f.mid, color, y0 + size / 2, x + (k + 0.5) * cw)
            pygame.draw.rect(surf, ACCENT if b == beat_i else (50, 50, 60), rect, 3 if b == beat_i else 1, border_radius=int(8 * S))
        sub = subs[beat_i]
        name = {1: "quarter notes", 2: "eighth notes", 3: "triplets", 4: "sixteenth notes", 6: "sextuplets"}.get(sub, f"{sub} per beat")
        f.center(surf, name, f.small, DIM, y0 + size + 12 * S)
        if g.chart.sticking:
            self.sticking_strip(surf, t, y0 + size + (44 if g.chart.accents else 34) * S)

    def sticking_strip(self, surf, t, y):
        """The rudiment's hand pattern, the stroke being played lit up. The lit stroke is
        the last chart note at or before t, so mixed subdivisions need no arithmetic."""
        f, S = self.f, self.s
        pattern = self.game.chart.sticking
        n = len(pattern)
        if self.note_times is None:
            marked = [nt for nt in self.game.notes if nt.strip is not None]
            self.note_times = [nt.t for nt in marked]
            self.note_strips = [nt.strip for nt in marked]
        k = bisect.bisect_right(self.note_times, t) - 1
        idx = self.note_strips[k] if k >= 0 else -1
        groups = self.game.chart.sticking_groups or []
        cw = 26 * S
        accents = self.game.chart.accents or set()
        # long patterns wrap: rows of at most STRIP_ROW strokes, split on a cell boundary
        # when there is one (a two-bar phrase then shows one bar per row)
        rows, start = [], 0
        while n - start > STRIP_ROW:
            target = start + (n - start) / math.ceil((n - start) / STRIP_ROW)     # balanced rows
            fits = [gi for gi in groups if start < gi <= start + STRIP_ROW]
            cut = min(fits, key=lambda gi: abs(gi - target)) if fits else start + STRIP_ROW
            rows.append(range(start, cut))
            start = cut
        rows.append(range(start, n))
        for r, idxs in enumerate(rows):
            x0 = self.w / 2 - len(idxs) * cw / 2
            yr = y + r * 32 * S
            for i in idxs:
                hand = pattern[i]
                cx = x0 + (i - idxs[0] + 0.5) * cw
                if hand == ".":                                   # a rest in a foot pattern
                    pygame.draw.circle(surf, (70, 70, 80), (int(cx), int(yr + 2 * S)), int(2.5 * S))
                    continue
                hot = i == idx
                color = (255, 255, 255) if hot else HAND_COLORS[hand]
                if hot:
                    pygame.draw.circle(surf, lerp(LANE_BG, color, 0.5), (int(cx), int(yr)), int(12 * S))
                if i in accents:
                    f.center(surf, ">", f.small, (255, 255, 255) if hot else TEXT, yr - 17 * S, cx)
                    f.center(surf, hand, f.large, color, yr + 4 * S, cx)
                else:
                    f.center(surf, hand, f.small, lerp(color, LANE_BG, 0.35), yr + 2 * S, cx)
                sep = (i in groups) if groups else (i % (n // max(1, n // 4) if n >= 4 else n) == 0)
                if sep and i != idxs[0]:
                    pygame.draw.line(surf, (60, 60, 70), (int(cx - cw / 2), int(yr - 12 * S)), (int(cx - cw / 2), int(yr + 12 * S)))

    def intro_card(self, surf):
        """The level before it starts: what it teaches and what to watch (bathero.intro), over
        the dimmed highway with the first notes waiting above the line; the snare starts it."""
        it, f, S = self.intro, self.f, self.s
        es = it.get("lang", "es") == "es"
        snare = next((l.color for l in self.game.lanes if l.key == "snare"), ACCENT)
        bw = min(int(880 * S), self.w - int(48 * S))
        inner = bw - int(56 * S)
        rows = []                                          # [text, font, colour, gap after (px at S=1), centred]

        def add(text, font, colour, gap=2, centre=False):
            rows.append([text, font, colour, gap, centre])

        def space(gap):
            rows[-1][3] = gap

        add(it["where"], f.small, DIM, 4)
        for r in wrap(f.large, it["title"], inner):
            add(r, f.large, TEXT)
        for r in wrap(f.small, it["facts"], inner):
            add(r, f.small, DIM, 1)
        space(18)
        add("QUÉ VAMOS A APRENDER" if es else "WHAT THIS LEVEL TEACHES", f.small, ACCENT, 6)
        for r in wrap(f.mid, it["learn"], inner):
            add(r, f.mid, TEXT)
        if it["notes"]:
            space(16)
            add("PARA TENER EN CUENTA" if es else "WATCH FOR", f.small, ACCENT, 6)
            for note in it["notes"]:
                for i, r in enumerate(wrap(f.small, note, inner - int(18 * S))):
                    add(("·  " if i == 0 else "   ") + r, f.small, TEXT, 1)
                space(7)
        if it["judged"]:
            space(12)
            for j in it["judged"]:
                for r in wrap(f.small, j, inner):
                    add(r, f.small, DIM, 1)
        space(22)
        go = "Golpeá el redoblante para empezar" if es else "Hit the snare to start"
        if it.get("practice"):
            go += "  ·  practice"
        add(go, f.mid, snare, 8, centre=True)
        add("S  no mostrar esta pantalla   ·   Enter también empieza   ·   Esc  volver" if es else
            "S  skip this screen   ·   Enter also starts   ·   Esc  back", f.small, DIM, 0, centre=True)
        bh = min(sum(font.get_height() + gap * S for _, font, _, gap, _ in rows) + 56 * S, self.h - 24 * S)
        shade = pygame.Surface((self.w, self.h), pygame.SRCALPHA)
        shade.fill((0, 0, 0, 150))
        surf.blit(shade, (0, 0))
        x0, y0 = self.w / 2 - bw / 2, self.h / 2 - bh / 2
        box = pygame.Rect(int(x0), int(y0), int(bw), int(bh))
        pygame.draw.rect(surf, (10, 10, 14), box, border_radius=int(16 * S))
        pygame.draw.rect(surf, lerp(snare, BG, 0.4), box, 2, border_radius=int(16 * S))
        pulse = 0.55 + 0.45 * (0.5 + 0.5 * math.sin(time.perf_counter() * 4.0))
        y = y0 + 28 * S
        for text, font, colour, gap, centre in rows:
            if colour is snare:
                colour = lerp(BG, snare, pulse)
            ts = f.text(text, font, colour)
            surf.blit(ts, (self.w / 2 - ts.get_width() / 2 if centre else x0 + 28 * S, y))
            y += font.get_height() + gap * S
            if y > y0 + bh - 20 * S:
                break

    def results(self, surf):
        g = self.game
        st = g.stats()
        S = self.s
        lines = [
            ("RESULTS", self.f.big, TEXT),
            (None, None, None),                                       # the stars go here
            (f"{st['hit']}/{st['notes']} notes  ·  {st['accuracy'] * 100:.1f}%  ·  grade {st['grade']:.0f}", self.f.mid, TEXT),
            (f"max combo {g.max_combo}   score {g.score}", self.f.mid, TEXT),
            (f"timing: mean {st['mean_ms']:+.1f} ms, std {st['std_ms']:.1f} ms", self.f.mid, TEXT),
            (f"{st['early']} early · {st['late']} late · {g.counts['STRAY']} stray", self.f.small, DIM),
        ]
        if self.offset_fit:
            fo = self.offset_fit
            s = f"offset {fo['offset']:.0f} ms · this run best at {fo['best']:.0f} ({fo['gain']:+.1f})"
            if fo.get("pooled") is not None and fo["n"] > 1:
                s += f" · last {fo['n']} runs: {fo['pooled']:.0f}"
            lines.append((s, self.f.small, DIM))
        if g.chart.dynamics:
            c = st.get("contrast")
            ok = c is not None and c >= CONTRAST_TARGET
            lines.append((f"accents {st['accents_ok']}/{st['accents']} · taps {st['taps_ok']}/{st['taps']} · "
                          f"contrast {c:.2f}x" if c is not None else
                          f"accents {st['accents_ok']}/{st['accents']} · taps {st['taps_ok']}/{st['taps']}",
                          self.f.mid, JUDGE_COLORS["PERFECT"] if ok else JUDGE_COLORS["OK"]))
        if g.chart.expression and st.get("art_ok", 0) + st.get("art_wrong", 0):
            r = st["art_rate"]
            lines.append((f"hat articulations {st['art_ok']}/{st['art_ok'] + st['art_wrong']}  ({r * 100:.0f}%)", self.f.mid,
                          JUDGE_COLORS["PERFECT"] if r >= 0.85 else JUDGE_COLORS["OK"]))
        rehearsal = self.transport.get("practice") or g.seeked
        if rehearsal:
            lines.append(("practice run · progress not saved", self.f.small, JUDGE_COLORS["OK"]))
        hint_rows = 0
        bw = 600
        if self.hints and self.hints[1]:
            tries, texts = self.hints
            bw = 760
            lines.append(((f"try {tries} · " if tries > 1 else "") + "work on this", self.f.small, DIM))
            for text in texts:
                for row in wrap(self.f.small, text, (bw - 40) * S):
                    lines.append((row, self.f.small, JUDGE_COLORS["OK"]))
                    hint_rows += 1
            hint_rows += 1
        lines.append(("Enter next · R retry · Esc back", self.f.small, DIM))
        bh = 370 + (40 if g.chart.dynamics else 0) + (40 if g.chart.expression else 0) + (28 if rehearsal else 0) + (28 if self.offset_fit else 0) + 28 * hint_rows
        box = pygame.Surface((int(bw * S), int(bh * S)))
        box.fill((10, 10, 14))
        box.set_alpha(250)
        cy = self.h * 0.42
        surf.blit(box, (self.w / 2 - bw / 2 * S, cy - bh / 2 * S))
        y = cy - (bh / 2 - 35) * S
        pop = None if self.finished_at is None else time.perf_counter() - self.finished_at
        for s, font, color in lines:
            if s is None:
                w = 5 * 32 * S + 4 * 14 * S
                draw_stars(surf, self.f, st["stars"], self.w / 2 + w / 2, y - 6 * S, S, size="big", pop=pop)
                y += 52 * S
                continue
            y += self.f.center(surf, s, font, color, y) + 12 * S
