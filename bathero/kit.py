"""Kit configuration: which input note numbers belong to which instrument."""
import json
import os

from .chart import DEFAULT_KIT, INSTRUMENT_ZONES, PADS, ZONE, ZONE_KEYS

KIT_PATH = os.path.expanduser("~/.config/bathero/kit.json")
SETTINGS_PATH = os.path.expanduser("~/.config/bathero/settings.json")
DEFAULT_SETTINGS = {"audio_device": None, "fullscreen": True, "midi_trace": None, "offset_ms": 0.0, "drum_sounds": True,
                    "capture_audio_device": "X18/XR18", "capture_audio_channels": [17, 18], "capture_camera": "iPhone",
                    "capture_pip": 0.28, "capture_corner": "br", "volume": 1.0, "metronome_volume": 1.0, "dyn_scale": 1.0, "dyn_scales": None, "coach_language": "es", "coach_model": None, "claude_bin": None,
                    "twitch_channel": "xantwav", "stream_height": 1080, "stream_kbps": 6000, "stream_url": None, "stream_bandwidth_test": False,
                    "stream_source": "screen", "stream_display": 0, "intro": True}


def load_settings(path=SETTINGS_PATH):
    out = dict(DEFAULT_SETTINGS)
    try:
        with open(path) as f:
            out.update(json.load(f))
    except (OSError, ValueError):
        pass
    return out


def save_settings(settings, path=SETTINGS_PATH):
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w") as f:
        json.dump(settings, f, indent=2)


PROGRESS_PATH = os.path.expanduser("~/.config/bathero/progress.json")


# Levels that were renamed or folded into another one: old key -> current key. Applied on load.
PROGRESS_MIGRATIONS = {
    "Paradiddle left lead": "Paradiddle (L)",                          # now the paradiddle's left-hand lead
    "Six stroke roll left lead": "Six stroke roll in triplets (L)",
    "31 · Plena": "31 · Cumbia",                                        # it was a cumbia villera all along (2026-09-14)
    "32 · Reggae: one drop, fills": "33 · Reggae: one drop, fills",     # 32 became the cumbia repiques
    # Pop punk: ten fill drills went in before the levels that use the fills (2026-09-22), so
    # everything from the eighth-note fills on moved up; two levels were renamed as well, because
    # their new number collided with the beats course's own "Kick doubles" and "Around the kit".
    "7 · Eighth-note fills": "9 · Eighth-note fills",
    "8 · Sixteenth fills": "14 · Sixteenth fills",
    "9 · Crash on the &": "15 · Crash on the &",
    "10 · Stabs": "16 · Stabs",
    "11 · Half-time verse": "17 · Half-time verse",
    "12 · The build": "18 · The build",
    "13 · Kick doubles": "19 · Doubles under the push",
    "14 · Ride bridge": "20 · Ride bridge",
    "15 · Around the kit": "25 · Whole-bar fills",
    "16 · Pop punk anthem": "26 · Pop punk anthem",
}


def load_progress(path=PROGRESS_PATH):
    """chart key -> best stats so far (stars, grade, accuracy, mean_ms, ...). The key is the
    level name, plus " (L)" for the left-hand-lead version of a level (Chart.key)."""
    try:
        with open(path) as f:
            progress = json.load(f)
    except (OSError, ValueError):
        return {}
    moved = False
    for old, new in PROGRESS_MIGRATIONS.items():
        if old in progress:
            entry = progress.pop(old)
            if new not in progress or entry.get("grade", -1) > progress[new].get("grade", -1):
                progress[new] = entry
            moved = True
    if moved:
        save_progress(progress, path)
    return progress


def save_progress(progress, path=PROGRESS_PATH):
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w") as f:
        json.dump(progress, f, indent=1)


def load_kit(path=KIT_PATH):
    """The saved kit {zone key: [note numbers]}, or None if the wizard has never run."""
    try:
        with open(path) as f:
            data = json.load(f)
    except (OSError, ValueError):
        return None
    return migrate(data)


def migrate(data):
    """Kits saved before zones existed had one list per instrument (kick/snare/hihat/crash).
    Spread those notes over the instrument's zones by their factory numbers; a note with no
    known home stays in the first zone (head/bow)."""
    kit = {k: [] for k in ZONE_KEYS}
    legacy = not any(k in data for k in ZONE_KEYS if k not in INSTRUMENT_ZONES)
    for k, notes in data.items():
        notes = [int(n) for n in notes] if isinstance(notes, list) else []
        if k in ZONE and not (legacy and k in INSTRUMENT_ZONES):
            kit[k].extend(notes)
        elif k in INSTRUMENT_ZONES:
            # old kits held both crashes under "crash" and the pedal under "hihat"
            zones = INSTRUMENT_ZONES[k] + (INSTRUMENT_ZONES["crash2"] if k == "crash" else []) + (INSTRUMENT_ZONES["pedal"] if k == "hihat" else [])
            for n in notes:
                home = next((z for z in zones if n in ZONE[z].defaults), zones[0])
                kit[home].append(n)
    return {k: sorted(set(v)) for k, v in kit.items()}


def save_kit(kit, path=KIT_PATH):
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w") as f:
        json.dump({k: sorted(kit.get(k, [])) for k in ZONE_KEYS}, f, indent=2)


def describe(kit):
    """Short: how many zones are set, and the first few missing ones."""
    missing = [ZONE[k].label for k in ZONE_KEYS if not kit.get(k)]
    n = len(ZONE_KEYS) - len(missing)
    if not missing:
        return f"all {n} zones assigned"
    more = f" +{len(missing) - 3}" if len(missing) > 3 else ""
    return f"{n}/{len(ZONE_KEYS)} zones · missing {', '.join(missing[:3])}{more}"


def describe_pads(kit):
    """One line per pad: zone parts with their note numbers."""
    lines = []
    for pad, zones in PADS:
        parts = []
        for zk in zones:
            notes = "/".join(map(str, kit.get(zk, []))) or "-"
            parts.append(f"{ZONE[zk].part or 'pad'} {notes}")
        lines.append(f"{pad}: " + "  ".join(parts))
    return lines


def default_kit():
    return {k: list(v) for k, v in DEFAULT_KIT.items()}
