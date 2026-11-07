"""Profiles: who is playing, so two people's stars stay apart.

A profile is a name and a pad: the pad's icon is the profile's icon, and hitting that pad on
the login screen is the login. Nine icons, one per pad (ICON_PADS, in keyboard order 1..9),
drawn here as flat glyphs in the pad's colour, so no font or emoji is involved.

`~/.config/bathero/profiles.json` holds the list. Each profile has its own progress file:
the first profile ever created is the owner ("main") and inherits the progress.json that was
there before profiles existed, plus the run logs written before runs carried a profile; every
later one gets `progress-<id>.json` and only its own runs. With no profiles at all the game
behaves as before (one shared progress, no login).
"""
import json
import math
import os
import re
import time

import pygame
import pygame.gfxdraw

from . import chart as C
from .kit import PROGRESS_PATH

PROFILES_PATH = os.path.expanduser("~/.config/bathero/profiles.json")
MAIN = "main"                    # the owner's id: the legacy progress.json and the untagged runs are theirs

ICON_PADS = ["kick", "snare", "hihat", "crash", "tom1", "floor", "ride", "crash2", "pedal"]   # keys 1..9
ICONS = {"kick": "flame", "snare": "star", "hihat": "bolt", "crash": "skull", "tom1": "heart",
         "floor": "gem", "ride": "moon", "crash2": "sun", "pedal": "note"}


def load_profiles(path=None):
    try:
        with open(path or PROFILES_PATH) as f:
            data = json.load(f)
    except (OSError, ValueError):
        return []
    out = []
    for p in data.get("profiles", []):
        if isinstance(p, dict) and p.get("id") and p.get("name") and p.get("pad") in ICONS:
            out.append({"id": str(p["id"]), "name": str(p["name"]), "pad": p["pad"], "created": p.get("created", 0)})
    return out


def save_profiles(profiles, path=None):
    path = path or PROFILES_PATH
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w") as f:
        json.dump({"profiles": profiles}, f, indent=1, ensure_ascii=False)


def progress_path(profile):
    """Where a profile's best results live: the owner (and nobody) keep the old progress.json."""
    if profile is None or profile["id"] == MAIN:
        return PROGRESS_PATH
    return os.path.join(os.path.dirname(PROGRESS_PATH), f"progress-{profile['id']}.json")


def new_profile(profiles, name, pad):
    """A profile record with a fresh id: the first one ever is the owner (MAIN)."""
    name = name.strip()
    if not name or pad not in ICONS:
        raise ValueError("a profile needs a name and a pad")
    if any(p["pad"] == pad for p in profiles):
        raise ValueError(f"the {C.LABELS[pad]} is taken")
    taken = {p["id"] for p in profiles}
    if MAIN not in taken:
        pid = MAIN
    else:
        base = re.sub(r"[^a-z0-9]+", "-", name.lower()).strip("-") or "player"
        pid, n = base, 2
        while pid in taken or pid == MAIN:
            pid, n = f"{base}-{n}", n + 1
    return {"id": pid, "name": name, "pad": pad, "created": time.time()}


def by_pad(profiles, pad):
    return next((p for p in profiles if p["pad"] == pad), None)


def runs_match(profile_id, run):
    """Whether a run log header belongs to a profile: the owner also gets the runs written
    before profiles existed; profile_id None means everyone."""
    if profile_id is None:
        return True
    rp = run.get("profile")
    if profile_id == MAIN:
        return rp in (None, MAIN)
    return rp == profile_id


# --- icons ----------------------------------------------------------------------------------
def _poly(surf, pts, color):
    pts = [(int(round(x)), int(round(y))) for x, y in pts]
    pygame.gfxdraw.filled_polygon(surf, pts, color)
    pygame.gfxdraw.aapolygon(surf, pts, color)


def _circle(surf, cx, cy, r, color):
    cx, cy, r = int(round(cx)), int(round(cy)), max(1, int(round(r)))
    pygame.gfxdraw.filled_circle(surf, cx, cy, r, color)
    pygame.gfxdraw.aacircle(surf, cx, cy, r, color)


def draw_icon(surf, pad, cx, cy, r, color=None, bg=(14, 14, 18)):
    """The pad's glyph, filled, fitting a circle of radius r at (cx, cy). Cut-outs (the
    skull's eyes, the moon) are painted in bg."""
    color = color or C.COLORS[pad]
    P = lambda x, y: (cx + x * r, cy + y * r)
    icon = ICONS[pad]
    if icon == "flame":
        pts = [(0, -1), (0.3, -0.55), (0.5, -0.8), (0.82, -0.2), (0.9, 0.3), (0.7, 0.8), (0.3, 1), (-0.3, 1),
               (-0.7, 0.8), (-0.9, 0.3), (-0.8, -0.25), (-0.45, -0.55), (-0.25, -0.2)]
        _poly(surf, [P(x, y) for x, y in pts], color)
        inner = [(0, 0.05), (0.28, 0.4), (0.3, 0.75), (0.1, 1), (-0.1, 1), (-0.3, 0.75), (-0.28, 0.4)]
        _poly(surf, [P(x, y) for x, y in inner], bg)
    elif icon == "star":
        pts = []
        for i in range(10):
            a = -math.pi / 2 + i * math.pi / 5
            rr = 1.0 if i % 2 == 0 else 0.45
            pts.append(P(math.cos(a) * rr, math.sin(a) * rr))
        _poly(surf, pts, color)
    elif icon == "bolt":
        pts = [(-0.05, -1), (0.6, -1), (0.15, -0.2), (0.6, -0.2), (-0.4, 1), (-0.15, 0.15), (-0.6, 0.15)]
        _poly(surf, [P(x, y) for x, y in pts], color)
    elif icon == "skull":
        _circle(surf, cx, cy - 0.22 * r, 0.7 * r, color)
        jaw = pygame.Rect(0, 0, int(0.9 * r), int(0.62 * r))
        jaw.center = (int(cx), int(cy + 0.55 * r))
        pygame.draw.rect(surf, color, jaw, border_radius=max(1, int(0.18 * r)))
        for sx in (-0.3, 0.3):
            _circle(surf, cx + sx * r, cy - 0.28 * r, 0.22 * r, bg)
        _poly(surf, [P(0, 0.05), P(-0.1, 0.25), P(0.1, 0.25)], bg)
        for sx in (-0.15, 0.0, 0.15):
            pygame.draw.line(surf, bg, P(sx, 0.5), P(sx, 0.86), max(1, int(0.07 * r)))
    elif icon == "heart":
        for sx in (-0.42, 0.42):
            _circle(surf, cx + sx * r, cy - 0.3 * r, 0.47 * r, color)
        _poly(surf, [P(-0.88, -0.12), P(0, 0.95), P(0.88, -0.12)], color)
    elif icon == "gem":
        pts = [(-0.55, -0.75), (0.55, -0.75), (1, -0.2), (0, 1), (-1, -0.2)]
        _poly(surf, [P(x, y) for x, y in pts], color)
        w = max(1, int(0.06 * r))
        pygame.draw.line(surf, bg, P(-1, -0.2), P(1, -0.2), w)
        pygame.draw.line(surf, bg, P(-0.55, -0.75), P(-0.3, -0.2), w)
        pygame.draw.line(surf, bg, P(0.55, -0.75), P(0.3, -0.2), w)
        pygame.draw.line(surf, bg, P(-0.3, -0.2), P(0, 1), w)
        pygame.draw.line(surf, bg, P(0.3, -0.2), P(0, 1), w)
    elif icon == "moon":
        _circle(surf, cx, cy, r, color)
        _circle(surf, cx + 0.45 * r, cy - 0.25 * r, 0.82 * r, bg)
    elif icon == "sun":
        _circle(surf, cx, cy, 0.5 * r, color)
        for i in range(8):
            a = i * math.pi / 4
            ca, sa = math.cos(a), math.sin(a)
            _poly(surf, [P(ca * 0.65 - sa * 0.1, sa * 0.65 + ca * 0.1), P(ca * 0.65 + sa * 0.1, sa * 0.65 - ca * 0.1),
                         P(ca * 1.0, sa * 1.0)], color)
    elif icon == "note":
        _circle(surf, cx - 0.35 * r, cy + 0.62 * r, 0.36 * r, color)
        stem = max(1, int(0.16 * r))
        pygame.draw.line(surf, color, P(-0.03, -0.95), P(-0.03, 0.62), stem)
        _poly(surf, [P(-0.1, -0.98), P(0.35, -0.7), P(0.7, -0.35), P(0.72, 0.15), P(0.5, -0.2), P(0.15, -0.45),
                     P(-0.1, -0.55)], color)
