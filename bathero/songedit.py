"""Editing an imported song's chart on the play screen: E while the song is paused.

The transcription (ADTOF) misses notes, doubles some and puts others on the wrong drum; this is
where they get fixed by hand, on the highway itself. The game stays paused while editing; the
view is the paused playhead, so the wheel, the number keys and the Launchpad's pads move it.

    click a note            select it (shift: add to / take from the selection)
    drag a note             move the selection: up / down in time (snapped to the grid), sideways to
                            another lane
    drag on empty space     a marquee: selects every note it touches (shift: adds)
    double-click empty      a new note there, on the grid, at the lane's usual velocity
    right-click a note      the articulation menu for the selection (the note alone when it is not
                            selected): hi-hat openness, zone and whether the zone is strict, rim / head,
                            accent / tap, flam, delete. Backspace / Delete delete the selection
    hit a drum              the selection moves to that drum (a lane appears if the song had none) and
                            takes the stroke's articulation: on the hi-hat only the pedal position
                            (tight / mid / open; the note's zone stays, and is not judged unless the
                            menu makes it strict), on snare and toms rim or not
    up / down               nudge the selection a grid step (nothing selected: scroll a beat)
    left / right            the selection one lane over
    wheel                   scroll; with cmd / ctrl, zoom
    G                       the grid: 1/16, 1/8, 1/4, 1/32, eighth triplets, sixteenth triplets, off
    cmd Z / cmd shift Z     undo / redo;  cmd A selects every note
    E / Esc                 done (Esc first drops a selection);  space: done, and play from here

Every change is written to the song's song.json at once (times at rate 1, whatever tempo is being
played); the first edit keeps the transcription as song.transcribed.json beside it. The songs
course is re-read after each save, so a retry or the tempo knob plays the edited chart.
"""
import json
import os
import shutil
import time
from collections import deque

import pygame

from . import songs as SG
from .chart import HH_GLYPH, ChartNote, build_lanes

GRIDS = [(4, "1/16"), (2, "1/8"), (1, "1/4"), (8, "1/32"), (3, "1/8 triplets"), (6, "1/16 triplets"), (0, "off")]
DOUBLE_CLICK_S = 0.35
DRAG_PX = 4                    # a press that moves less than this is a click
DEDUPE_S = 0.010               # two notes of one drum closer than this are one
MIN_HIT_PX = 14                # a note is at least this tall to the mouse
ZOOMS = (0.25, 8.0)
UNDO_DEPTH = 200
BACKUP = "song.transcribed.json"
OPENNESS = ("tight", "mid", "open")
RIM_KEYS = ("snare", "tom1", "tom2", "tom3", "floor")


def hh_parts(n):
    """(openness, zone) of a hi-hat note's articulation, (None, None) without one."""
    if n.key != "hihat" or not n.art or n.art.split()[0] not in OPENNESS:
        return None, None
    a = n.art.split()
    return a[0], (a[1] if len(a) > 1 else "body")


class Menu:
    """The right-click menu: rows of (group, label, value) acting on the selection; stays open
    after a choice so several can be made, closes on a click outside or Esc."""
    ROW, HEAD, W = 30, 26, 340

    def __init__(self, ed, pos):
        self.ed, self.pos, self.hover = ed, pos, None
        self.rows = []
        self.layout()

    def layout(self):
        notes = list(self.ed.sel.values())
        hh = [n for n in notes if n.key == "hihat"]
        rim = [n for n in notes if n.key in RIM_KEYS]
        rows = []

        def group(title, items):
            rows.append(("head", title, None, None))
            rows.extend(items)

        def state(ns, f):
            v = {f(n) for n in ns}
            return v.pop() if len(v) == 1 else "mixed"

        if hh:
            o = state(hh, lambda n: hh_parts(n)[0])
            group("hi-hat openness", [("open", lbl, val, o == val) for lbl, val in
                                      (("any (not judged)", None), ("tight", "tight"), ("mid", "mid"), ("open", "open"))])
            withart = [n for n in hh if n.art]
            if withart:
                z = state(withart, lambda n: hh_parts(n)[1])
                st = state(withart, lambda n: n.strict)
                group("hi-hat zone", [("zone", "body", "body", z == "body"), ("zone", "edge", "edge", z == "edge"),
                                      ("strict", "strict: only this zone counts", None, st is True)])
            group("hi-hat foot", [("chick", "chick (to the pedal lane)", None, False)])
        if rim:
            r = state(rim, lambda n: n.art if n.art in ("rim", "head") else None)
            group("snare / tom zone", [("rim", lbl, val, r == val) for lbl, val in
                           (("any (not judged)", None), ("head only", "head"), ("rim", "rim"))])
        d = state(notes, lambda n: "accent" if n.accent else "tap" if n.tap else None)
        group("dynamics", [("dyn", lbl, val, d == val) for lbl, val in
                           (("free (not judged)", None), ("accent", "accent"), ("tap (soft)", "tap"))])
        fl = state(notes, lambda n: n.flam)
        cr = state(notes, lambda n: n.cresc)
        group(f"{len(notes)} note{'s' if len(notes) > 1 else ''}",
              [("cresc", "crescendo (louder / hat opening)", None, cr is True), ("flam", "flam", None, fl is True),
               ("delete", "delete  (Backspace)", None, False)])
        self.rows = rows

    def rects(self):
        S = self.ed.r.s
        w = int(self.W * S)
        h = sum(int((self.HEAD if r[0] == "head" else self.ROW) * S) for r in self.rows) + int(8 * S)
        sw, sh = self.ed.r.w, self.ed.r.h
        x = min(self.pos[0], sw - w - 4)
        y = max(4, min(self.pos[1], sh - h - 4))
        out, cy = [], y + int(4 * S)
        for r in self.rows:
            rh = int((self.HEAD if r[0] == "head" else self.ROW) * S)
            out.append((r, pygame.Rect(x, cy, w, rh)))
            cy += rh
        return pygame.Rect(x, y, w, h), out

    def at(self, pos):
        frame, rows = self.rects()
        for r, rect in rows:
            if r[0] != "head" and rect.collidepoint(pos):
                return r
        return None

    def draw(self, surf):
        S, f = self.ed.r.s, self.ed.r.f
        frame, rows = self.rects()
        pygame.draw.rect(surf, (22, 22, 30), frame, border_radius=int(8 * S))
        pygame.draw.rect(surf, (80, 200, 230), frame, 1, border_radius=int(8 * S))
        for r, rect in rows:
            if r[0] == "head":
                ts = f.text(r[1].upper(), f.tiny, (120, 120, 140))
                surf.blit(ts, (rect.x + 12 * S, rect.bottom - ts.get_height() - 3 * S))
                continue
            if self.hover == r:
                pygame.draw.rect(surf, (45, 60, 75), rect.inflate(-6 * S, 0), border_radius=int(5 * S))
            mark = "" if r[0] in ("delete", "chick") else ("●" if r[3] else "○")
            col = (230, 90, 90) if r[0] == "delete" else (235, 235, 240) if r[3] else (170, 170, 180)
            ts = f.text(f"{mark}  {r[1]}" if mark else r[1], f.small, col)
            surf.blit(ts, (rect.x + 14 * S, rect.centery - ts.get_height() / 2))

    def choose(self, r):
        ed, kind, val = self.ed, r[0], r[2]
        notes = list(ed.sel.values())
        if kind == "delete":
            ed.menu = None
            ed.delete(notes)
            return
        ed.remember()
        what = r[1]
        if kind == "open":
            for n in notes:
                if n.key == "hihat":
                    z = hh_parts(n)[1] or "body"
                    n.art = f"{val} {z}" if val else None
                    if not val:
                        n.strict = False
            what = f"hi-hat {val or 'openness not judged'}"
        elif kind == "zone":
            for n in notes:
                o = hh_parts(n)[0]
                if o:
                    n.art = f"{o} {val}"
            what = f"hi-hat {val}"
        elif kind == "strict":
            on = not r[3]
            for n in notes:
                if hh_parts(n)[0]:
                    n.strict = on
            what = f"zone strict {'on' if on else 'off'}"
        elif kind == "chick":
            for n in notes:
                if n.key == "hihat":
                    n.key, n.art, n.strict = "pedal", "pedal chick", False
            what = "to the pedal chick"
        elif kind == "rim":
            for n in notes:
                if n.key in RIM_KEYS:
                    n.art = val
            what = f"zone {val or 'not judged'}"
        elif kind == "dyn":
            for n in notes:
                n.accent, n.tap = val == "accent", val == "tap"
            what = val or "dynamics not judged"
        elif kind == "cresc":
            on = not r[3]
            for n in notes:
                n.cresc = on
            what = f"crescendo {'on' if on else 'off'}"
        elif kind == "flam":
            on = not r[3]
            for n in notes:
                n.flam = on
            what = f"flam {'on' if on else 'off'}"
        ed.commit(f"{what} · {len(notes)} note{'s' if len(notes) > 1 else ''}")
        if ed.sel:
            self.layout()
        else:
            ed.menu = None


class SongEditor:
    def __init__(self, screen):
        self.screen = screen
        self.sel = {}                      # id(note) -> note
        self.drag = None                   # a note being dragged: the selection's origins and the anchor
        self.marquee = None                # [x0, y0, x1, y1] while dragging on empty space
        self.undo, self.redo = [], []
        self.grid = 0
        self.zoom = 1.0
        self.hits = deque()                # instruments heard (MIDI thread) -> update()
        self.keys = {l.key for l in screen.game.lanes}   # lanes stay while editing, even emptied
        self.last_click = (0.0, None)
        self.saved = 0
        self.moved = False                 # the view left the paused playhead: resuming runs up to it
        self.armed = False                 # the Launchpad's RECORD ARM: space records over the loop
        self.recording = False
        self.span = None                   # (t0, t1) being recorded: the loop's
        self.take = deque()                # (chart t, key, velocity, art) heard while recording (MIDI thread)
        self.menu = None                   # the right-click articulation menu

    # --- geometry -------------------------------------------------------------------------
    @property
    def game(self):
        return self.screen.game

    @property
    def chart(self):
        return self.screen.chart

    @property
    def r(self):
        return self.screen.renderer

    def now(self):
        return self.game.song_time()

    def k(self):
        """Pixels per chart second on screen."""
        r = self.r
        return r.pps * (1.0 if r.scroll_fixed else self.game.speed) * self.zoom

    def t_at(self, y):
        return self.now() + (self.r.line_y - y) / self.k()

    def lane_at(self, x, clamp=False):
        r = self.r
        i = int((x - r.lane_x[0]) // r.lane_w)
        if clamp:
            return max(0, min(len(r.lane_x) - 1, i))
        return i if 0 <= i < len(r.lane_x) else None

    def window(self):
        """The chart times on screen, bottom to top."""
        now, k = self.now(), self.k()
        return now - (self.r.h - self.r.line_y) / k - 0.05, now + self.r.line_y / k + 0.05

    def visible(self):
        lo, hi = self.window()
        return [n for n in self.game.notes if lo <= n.t <= hi]

    def box(self, n):
        x, w, nh, _ = self.r.note_box(n)
        y = self.r.y_for(n.t, self.now())
        h = max(nh, MIN_HIT_PX * self.r.s)
        return pygame.Rect(x, int(y - h / 2), w, int(h))

    def note_at(self, pos):
        for n in reversed(self.visible()):
            if self.box(n).collidepoint(pos):
                return n
        return None

    def selected(self, n):
        return id(n) in self.sel

    def snap(self, t):
        sub = GRIDS[self.grid][0]
        if not sub:
            return t
        ch = self.chart
        return ch.beat_time(round(ch.beat_pos(t) * sub) / sub)

    def step(self, t, d):
        """t moved d grid steps (a sixteenth when the grid is off)."""
        sub = GRIDS[self.grid][0] or 4
        ch = self.chart
        return ch.beat_time(round(ch.beat_pos(t) * sub) / sub + d / sub)

    # --- the view ---------------------------------------------------------------------------
    def view(self, t):
        self.moved = True
        self.game.seek(max(0.0, t), 0.0)

    def scroll(self, dy_px):
        self.view(self.now() + dy_px / self.k())

    def view_bar(self, bar):
        self.view(self.chart.beat_time(bar * 4))
        self.screen.app.toasts.add(self.chart.place(self.now()), (80, 200, 230), key="phrase")

    # --- changes ------------------------------------------------------------------------------
    def snapshot(self):
        return [(n.t, n.key, n.velocity, n.art, n.flam, n.strict, n.accent, n.tap, n.cresc) for n in self.game.notes]

    def remember(self):
        self.undo.append(self.snapshot())
        del self.undo[:-UNDO_DEPTH]
        self.redo.clear()

    def restore(self, snap):
        self.game.notes[:] = [ChartNote(t=t, key=k, velocity=v, art=a, flam=fl, strict=st, accent=ac, tap=tp, cresc=cr)
                              for t, k, v, a, fl, st, ac, tp, cr in snap]
        self.sel = {}
        self.menu = None
        self.commit()

    def commit(self, what=None):
        """After any change: sorted, one note per drum per instant, lanes, the song written."""
        notes = self.game.notes
        notes.sort(key=lambda n: n.t)
        out, last = [], {}
        for n in notes:
            p = last.get(n.key)
            if p is not None and n.t - p.t < DEDUPE_S:
                if self.selected(n) and not self.selected(p):
                    out = [m for m in out if m is not p]
                else:
                    self.sel.pop(id(n), None)
                    continue
            out.append(n)
            last[n.key] = n
        notes[:] = out
        self.relane()
        self.chart._phrase_cache = None
        self.chart._cresc_cache = None
        self.screen._lp_colours = None
        self.save()
        if what:
            self.screen.app.toasts.add(what, (80, 200, 230), key="edit")

    def relane(self):
        g, scr = self.game, self.screen
        self.keys |= {n.key for n in g.notes}
        if self.keys != {l.key for l in g.lanes}:
            g.lanes, g.by_note = build_lanes(self.chart, scr.app.kit, extra=self.keys)
            scr.lanes, scr.by_note = g.lanes, g.by_note
            scr.on_resize()                                # the renderer lays the lanes out again
        else:
            index = {l.key: i for i, l in enumerate(g.lanes)}
            for n in g.notes:
                n.lane = index[n.key]
        self.r.double_runs = None
        self.r.note_times = None

    def save(self):
        ch, app = self.chart, self.screen.app
        path = os.path.join(ch.song, "song.json")
        try:
            with open(path) as f:
                s = json.load(f)
            backup = os.path.join(ch.song, BACKUP)
            if not os.path.exists(backup):
                shutil.copyfile(path, backup)
            s["notes"] = [SG.note_row(n, ch.rate) for n in self.game.notes]
            s["edited"] = time.strftime("%Y-%m-%d %H:%M")
            with open(path + ".part", "w") as f:
                json.dump(s, f)
            os.replace(path + ".part", path)
            self.saved += 1
            app.refresh_songs()
        except (OSError, ValueError) as e:
            app.toasts.add(f"could not save the song: {e}", (230, 80, 80), key="edit")

    def delete(self, notes):
        notes = [n for n in notes]
        if not notes:
            return
        if len(notes) >= len(self.game.notes):
            self.screen.app.toasts.add("a song keeps at least one note", (120, 120, 130), key="edit")
            return
        self.remember()
        gone = {id(n) for n in notes}
        self.game.notes[:] = [n for n in self.game.notes if id(n) not in gone]
        for i in gone:
            self.sel.pop(i, None)
        self.commit(f"deleted {len(notes)} note{'s' if len(notes) > 1 else ''}")

    def add(self, lane, t):
        key = self.game.lanes[lane].key
        vels = sorted(n.velocity for n in self.game.notes if n.key == key)
        vel = vels[len(vels) // 2] if vels else 96
        self.remember()
        n = ChartNote(t=max(0.0, self.snap(t)), key=key, velocity=vel)
        self.game.notes.append(n)
        self.sel = {id(n): n}
        self.commit(f"added a {self.game.lanes[lane].label.lower()} note · {self.chart.place(n.t)}")
        if self.game.sounds is not None:
            self.game.sounds.play(key, vel, 0.8)

    def retarget(self, inst, art=None):
        """The selection to the drum hit, with the stroke's articulation: on the hi-hat the pedal
        position only (the note keeps its zone), on snare / toms rim or not; other drums none."""
        notes = list(self.sel.values())
        new = {}
        for n in notes:
            a, strict = n.art, n.strict
            if inst == "hihat":
                o = art.split()[0] if art and art.split()[0] in OPENNESS else None
                if o:
                    z = hh_parts(n)[1] or art.split()[1]
                    a = f"{o} {z}"
                elif n.key != "hihat":
                    a = None
            elif inst in RIM_KEYS:
                a = "rim" if art == "rim" else ("head" if n.art == "head" and n.key == inst else None)
            elif inst == "pedal":
                a = "pedal chick" if n.art == "pedal chick" else None
            else:
                a = None
            if not a:
                strict = False
            if (n.key, n.art, n.strict) != (inst, a, strict):
                new[id(n)] = (n, a, strict)
        if not new:
            return
        self.remember()
        for n, a, strict in new.values():
            n.key, n.art, n.strict = inst, a, strict
        arts = {a for _, a, _ in new.values() if a}
        tag = f" · {arts.pop().split()[0]}" if len(arts) == 1 else ""
        self.commit(f"{len(new)} note{'s' if len(new) > 1 else ''} to the {inst}{tag}")
        if self.menu is not None:
            self.menu.layout()

    def nudge(self, dt_steps=0, dl=0):
        notes = list(self.sel.values())
        if not notes:
            return
        lanes = self.game.lanes
        if dl and not all(0 <= n.lane + dl < len(lanes) for n in notes):
            return
        self.remember()
        for n in notes:
            if dt_steps:
                n.t = max(0.0, self.step(n.t, dt_steps))
            if dl:
                n.lane += dl
                n.key = lanes[n.lane].key
        self.commit()

    # --- input ---------------------------------------------------------------------------------
    def on_hit(self, inst, velocity, art=None):
        """MIDI thread: the drum sounds; with a selection it is where the selection goes."""
        self.hits.append((inst, art))

    def update(self):
        while self.hits:
            inst, art = self.hits.popleft()
            if self.sel:
                self.retarget(inst, art)

    def on_mouse(self, ev):
        if self.recording:
            return
        shift = bool(pygame.key.get_mods() & pygame.KMOD_SHIFT)
        if self.menu is not None and ev.type in (pygame.MOUSEBUTTONDOWN, pygame.MOUSEMOTION, pygame.MOUSEBUTTONUP):
            if ev.type == pygame.MOUSEMOTION:
                self.menu.hover = self.menu.at(ev.pos)
                return
            if ev.type == pygame.MOUSEBUTTONDOWN:
                r = self.menu.at(ev.pos)
                if r is not None and ev.button == 1:
                    self.menu.choose(r)
                    return
                if self.menu.rects()[0].collidepoint(ev.pos):
                    return
                self.menu = None                           # a click outside closes it (and does its own thing)
            else:
                return
        if ev.type == pygame.MOUSEBUTTONDOWN and ev.button == 1:
            self.press_left(ev.pos, shift)
        elif ev.type == pygame.MOUSEBUTTONDOWN and ev.button == 3:
            n = self.note_at(ev.pos)
            if n is not None:
                if not self.selected(n):
                    self.sel = {id(n): n}
                self.menu = Menu(self, ev.pos)
        elif ev.type == pygame.MOUSEMOTION:
            self.motion(ev.pos)
        elif ev.type == pygame.MOUSEBUTTONUP and ev.button == 1:
            self.release(ev.pos, shift)
        elif ev.type == pygame.MOUSEWHEEL:
            dy = getattr(ev, "precise_y", ev.y)
            if pygame.key.get_mods() & (pygame.KMOD_META | pygame.KMOD_CTRL):
                self.zoom = max(ZOOMS[0], min(ZOOMS[1], self.zoom * 1.15 ** dy))
            else:
                self.scroll(dy * 40 * self.r.s)
                if self.drag is not None:
                    self.motion(pygame.mouse.get_pos())

    # --- recording over the loop (RECORD ARM, space) -------------------------------------------
    def quantize(self, t):
        sub, ch = GRIDS[self.grid][0] or 4, self.chart
        return ch.beat_time(round(ch.beat_pos(t) * sub) / sub)

    def take_notes(self):
        """The take quantised to the grid, inside the span, one note per drum per slot (the loudest)."""
        t0, t1 = self.span
        best = {}
        for t, key, vel, art in list(self.take):
            q = self.quantize(t)
            if not (t0 - 1e-4 <= q < t1 - 1e-4):
                continue
            k = (key, round(q, 4))
            if k not in best or vel > best[k][2]:
                best[k] = (q, key, vel, art)
        return [ChartNote(t=q, key=key, velocity=vel, art=art) for q, key, vel, art in best.values()]

    def apply_take(self):
        """Replace the span's notes with the take's."""
        t0, t1 = self.span
        new = self.take_notes()
        self.take.clear()
        self.remember()
        old = [n for n in self.game.notes if t0 - 1e-4 <= n.t < t1 - 1e-4]
        gone = {id(n) for n in old}
        self.game.notes[:] = [n for n in self.game.notes if id(n) not in gone] + new
        if not self.game.notes:
            self.restore(self.undo.pop())
            return
        self.sel = {}
        self.commit(f"recorded {len(new)} notes over {len(old)} · grid "
                    f"{GRIDS[self.grid][1] if GRIDS[self.grid][0] else '1/16'} · ⌘Z undoes")

    def press_left(self, pos, shift):
        n = self.note_at(pos)
        if n is None:
            now = time.perf_counter()
            last_t, last_pos = self.last_click
            lane = self.lane_at(pos[0])
            if (lane is not None and last_pos is not None and now - last_t < DOUBLE_CLICK_S
                    and abs(pos[0] - last_pos[0]) < 8 and abs(pos[1] - last_pos[1]) < 8):
                self.last_click = (0.0, None)
                self.add(lane, self.t_at(pos[1]))
                return
            self.last_click = (now, pos)
            self.marquee = [pos[0], pos[1], pos[0], pos[1]]
            return
        self.last_click = (0.0, None)
        if shift:
            if self.selected(n):
                self.sel.pop(id(n))
                return
            self.sel[id(n)] = n
        elif not self.selected(n):
            self.sel = {id(n): n}
        self.drag = {"anchor": n, "pos": pos, "t0": self.t_at(pos[1]), "lane0": self.lane_at(pos[0], clamp=True),
                     "orig": {id(m): (m.t, m.lane, m.key) for m in self.sel.values()}, "moved": False,
                     "before": self.snapshot()}

    def motion(self, pos):
        d = self.drag
        if d is not None:
            if not d["moved"] and abs(pos[0] - d["pos"][0]) < DRAG_PX and abs(pos[1] - d["pos"][1]) < DRAG_PX:
                return
            d["moved"] = True
            a_t = d["orig"][id(d["anchor"])][0]
            dt = self.snap(a_t + self.t_at(pos[1]) - d["t0"]) - a_t
            lanes = self.game.lanes
            dl = self.lane_at(pos[0], clamp=True) - d["lane0"]
            lo = min(o[1] for o in d["orig"].values())
            hi = max(o[1] for o in d["orig"].values())
            dl = max(-lo, min(len(lanes) - 1 - hi, dl))
            first = min(o[0] for o in d["orig"].values())
            dt = max(dt, -first)
            for m in self.sel.values():
                t, lane, _ = d["orig"][id(m)]
                m.t, m.lane = t + dt, lane + dl
                m.key = lanes[m.lane].key
        elif self.marquee is not None:
            self.marquee[2:] = pos

    def release(self, pos, shift):
        d = self.drag
        if d is not None:
            self.drag = None
            if d["moved"]:
                self.undo.append(d["before"])
                del self.undo[:-UNDO_DEPTH]
                self.redo.clear()
                self.commit()
            elif not shift and len(self.sel) > 1:
                self.sel = {id(d["anchor"]): d["anchor"]}       # a click on one of many: that one alone
            return
        if self.marquee is None:
            return
        x0, y0, x1, y1 = self.marquee
        self.marquee = None
        rect = pygame.Rect(min(x0, x1), min(y0, y1), abs(x1 - x0) + 1, abs(y1 - y0) + 1)
        if not shift:
            self.sel = {}
        if rect.w < DRAG_PX and rect.h < DRAG_PX:
            return
        for n in self.visible():
            if self.box(n).colliderect(rect):
                self.sel[id(n)] = n
        if self.sel:
            self.screen.app.toasts.add(f"{len(self.sel)} selected · hit a drum to move them there", (80, 200, 230), key="edit")

    def on_key(self, key):
        """The editor's keys; returns False for the ones the play screen should handle."""
        mods = pygame.key.get_mods()
        cmd = bool(mods & (pygame.KMOD_META | pygame.KMOD_CTRL))
        shift = bool(mods & pygame.KMOD_SHIFT)
        app = self.screen.app
        if self.menu is not None and key == pygame.K_ESCAPE:
            self.menu = None
            return True
        if key == pygame.K_z and cmd:
            src, dst = (self.redo, self.undo) if shift else (self.undo, self.redo)
            if not src:
                app.toasts.add("nothing to redo" if shift else "nothing to undo", (120, 120, 130), key="edit")
                return True
            dst.append(self.snapshot())
            self.restore(src.pop())
            app.toasts.add("redo" if shift else "undo", (80, 200, 230), key="edit")
        elif key == pygame.K_a and cmd:
            self.sel = {id(n): n for n in self.game.notes}
            app.toasts.add(f"all {len(self.sel)} notes selected", (80, 200, 230), key="edit")
        elif key in (pygame.K_BACKSPACE, pygame.K_DELETE):
            self.menu = None
            self.delete(list(self.sel.values()))
        elif key == pygame.K_f and not cmd:
            if not self.sel:
                app.toasts.add("select notes first, then F for a flam", (120, 120, 130), key="edit")
                return True
            self.remember()
            on = not all(n.flam for n in self.sel.values())
            for n in self.sel.values():
                n.flam = on
            self.commit()
            app.toasts.add(f"flam {'on' if on else 'off'} · {len(self.sel)} notes", (80, 200, 230), key="edit")
        elif key == pygame.K_g:
            self.grid = (self.grid + 1) % len(GRIDS)
            app.toasts.add(f"grid {GRIDS[self.grid][1]}", (80, 200, 230), key="edit")
        elif key in (pygame.K_UP, pygame.K_DOWN):
            d = 1 if key == pygame.K_UP else -1
            if self.sel:
                self.nudge(dt_steps=d)
            else:
                ch = self.chart
                self.view(ch.beat_time(round(ch.beat_pos(self.now())) + d * (4 if shift else 1)))
        elif key in (pygame.K_LEFT, pygame.K_RIGHT):
            self.nudge(dl=1 if key == pygame.K_RIGHT else -1)
        else:
            return False
        return True

    # --- drawing (the renderer calls these instead of the notes and the PAUSED card) -------------
    def draw_grid(self, surf, now):
        r, ch = self.r, self.chart
        lo, hi = self.window()
        sub = GRIDS[self.grid][0] or 1
        x0, x1 = int(r.lane_x[0]), int(r.lane_x[-1] + r.lane_w)
        p = int(ch.beat_pos(max(lo, 0.0)) * sub) - 1
        while True:
            p += 1
            t = ch.beat_time(p / sub)
            if t > hi:
                break
            if t < lo:
                continue
            y = int(r.y_for(t, now))
            if p % (4 * sub) == 0:
                col, wd = (70, 70, 86), 2
            elif p % sub == 0:
                col, wd = (48, 48, 60), 1
            else:
                col, wd = (32, 32, 40), 1
            pygame.draw.line(surf, col, (x0, y), (x1, y), wd)

    def draw_notes(self, surf, now):
        r, S = self.r, self.r.s
        for n in self.visible():
            x, w, nh, accent = r.note_box(n)
            y = r.y_for(n.t, now)
            rect = (x, int(y - nh / 2), w, nh)
            colour = self.game.lanes[n.lane].color
            if n.flam:
                gw, gh = int(w * 0.5), max(3, int(nh * 0.45))
                pygame.draw.rect(surf, colour, (int(x + (w - gw) / 2), int(y + nh / 2 + 2 * S), gw, gh),
                                 border_radius=int(3 * S))
            if self.selected(n):
                pygame.draw.rect(surf, (255, 255, 255), rect, border_radius=int(6 * S))
                pygame.draw.rect(surf, colour, rect, max(2, int(3 * S)), border_radius=int(6 * S))
            else:
                pygame.draw.rect(surf, colour, rect, border_radius=int(6 * S))
                if accent:
                    pygame.draw.rect(surf, (255, 255, 255), rect, 2, border_radius=int(6 * S))
            label = HH_GLYPH.get(n.art, "") if n.art else ""
            if n.strict and label:
                label += "!"
            if n.accent:
                label = ("> " + label).strip() if not label.startswith(">") else label
            elif n.tap:
                label = ("( " + label + " )") if label else "( )"
            if label:
                self.r.f.center(surf, label, self.r.f.small, (20, 20, 24), y, x + w / 2)
        if self.recording:                                 # the take so far, quantised, as outlines
            index = {l.key: i for i, l in enumerate(self.game.lanes)}
            for n in self.take_notes():
                if n.key in index:
                    w = r.lane_w * 0.8
                    x = r.lane_x[index[n.key]] + (r.lane_w - w) / 2
                    y = r.y_for(n.t, now)
                    pygame.draw.rect(surf, (255, 255, 255), (x, int(y - 8 * S), w, int(16 * S)), max(2, int(2 * S)),
                                     border_radius=int(6 * S))
        if self.marquee is not None:
            x0, y0, x1, y1 = self.marquee
            rect = pygame.Rect(min(x0, x1), min(y0, y1), abs(x1 - x0), abs(y1 - y0))
            if rect.w and rect.h:
                fill = pygame.Surface(rect.size, pygame.SRCALPHA)
                fill.fill((80, 200, 230, 40))
                surf.blit(fill, rect.topleft)
                pygame.draw.rect(surf, (80, 200, 230), rect, 1)

    def draw_card(self, surf, now):
        """Only the recording's state: a paused song is the editor, without a card (2026-11-09)."""
        r, f, S = self.r, self.r.f, self.r.s
        if self.menu is not None and not self.recording:
            self.menu.draw(surf)
        if self.recording:
            text, col = f"● RECORDING  ·  {self.chart.place(max(now, 0.0))}  ·  grid " \
                        f"{GRIDS[self.grid][1] if GRIDS[self.grid][0] else '1/16'}  ·  Esc cancels", (230, 60, 60)
        elif self.armed:
            text, col = "ARMED  ·  space records over the loop", (230, 60, 60)
        else:
            return
        f.center(surf, text, f.mid, col, 178 * S)
