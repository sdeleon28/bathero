"""Synthesized drum sounds. No sample files: everything is generated at startup."""
import math

import numpy as np
import pygame

SR = 44100
MIXER_BUFFER = 256          # small buffer for low latency; raise it if audio crackles
# Mixer channels: the first few are reserved so a burst of hit and guide sounds can never
# leave the menu music or a level's tracks without a channel.
NUM_CHANNELS = 32
MENU_CHANNEL = 0
TRACK_CHANNELS = {"backing": 1, "metronome": 2, "music": 3}
RESERVED = 4

# The game's own output level, 0..1 (the { and } keys). pygame has no master volume, so
# every set_volume in this module multiplies by it; App.set_volume re-applies it to the
# sounds already playing.
_master = 1.0


def master():
    return _master


def set_master(v):
    global _master
    _master = max(0.0, min(1.0, float(v)))


def _t(seconds):
    return np.arange(int(seconds * SR)) / SR


def _noise(n, seed):
    return np.random.default_rng(seed).uniform(-1.0, 1.0, n)


def kick():
    t = _t(0.4)
    freq = 45 + 140 * np.exp(-t * 28)                 # fast pitch drop
    phase = 2 * math.pi * np.cumsum(freq) / SR
    body = np.sin(phase) * np.exp(-t * 7)
    click = _noise(len(t), 1) * np.exp(-t * 400) * 0.5
    return body + click


def snare():
    t = _t(0.3)
    tone = (np.sin(2 * math.pi * 185 * t) + 0.5 * np.sin(2 * math.pi * 330 * t)) * np.exp(-t * 22) * 0.5
    rattle = _noise(len(t), 2) * np.exp(-t * 16)
    rattle = np.diff(rattle, prepend=0.0) * 0.9 + rattle * 0.5   # brighten
    return tone + rattle


def hihat():
    t = _t(0.09)
    x = _noise(len(t), 3)
    for _ in range(2):                                  # crude high-pass
        x = np.diff(x, prepend=0.0)
    return x * np.exp(-t * 55)


def hihat_open(dur=0.55, seed=12):
    t = _t(dur)
    x = _noise(len(t), seed)
    for _ in range(2):
        x = np.diff(x, prepend=0.0)
    ring = np.sin(2 * np.pi * 6100 * t) * 0.15 + np.sin(2 * np.pi * 8700 * t) * 0.1
    return (x * 0.6 + ring) * np.exp(-t * 7) * np.minimum(1.0, t / 0.002)


def hihat_mid():
    return hihat_open(0.22, seed=13)


def chick():
    t = _t(0.08)
    x = _noise(len(t), 14)
    for _ in range(3):
        x = np.diff(x, prepend=0.0)
    return x * np.exp(-t * 90) * 0.7 + np.sin(2 * np.pi * 3000 * t) * np.exp(-t * 200) * 0.3


HH_SOUND = {"tight body": "hihat", "tight edge": "hihat", "mid body": "hihat_mid", "mid edge": "hihat_mid",
            "open body": "hihat_open", "open edge": "hihat_open", "pedal chick": "chick"}


def crash(seed=4, dur=1.6):
    t = _t(dur)
    x = _noise(len(t), seed)
    x = np.diff(x, prepend=0.0)
    shimmer = sum(np.sin(2 * math.pi * f * t) for f in (3150, 4270, 5590, 7330)) * 0.05
    return (x + shimmer) * np.exp(-t * 2.4) * (1 - np.exp(-t * 200))


def tom(f0=170.0, dur=0.45, seed=5):
    t = _t(dur)
    freq = f0 * (1 + 0.6 * np.exp(-t * 30))              # short pitch bend down
    phase = 2 * math.pi * np.cumsum(freq) / SR
    body = np.sin(phase) * np.exp(-t * 7)
    attack = _noise(len(t), seed) * np.exp(-t * 60) * 0.3
    return body + attack


def floor_tom():
    return tom(105.0, 0.6, seed=8)


def ride():
    t = _t(1.2)
    x = np.diff(_noise(len(t), 6), prepend=0.0)          # bright wash
    ping = np.sin(2 * math.pi * 3200 * t) * np.exp(-t * 12) * 0.5 + np.sin(2 * math.pi * 4700 * t) * np.exp(-t * 9) * 0.3
    return x * np.exp(-t * 4) * 0.5 + ping


def click(high=False):
    t = _t(0.03)
    return np.sin(2 * math.pi * (1600 if high else 1000) * t) * np.exp(-t * 120)


def tone(midi_note):
    t = _t(0.18)
    f = 440 * 2 ** ((midi_note - 69) / 12)
    return np.sin(2 * math.pi * f * t) * np.exp(-t * 18)


def _to_sound(x):
    x = x / (np.max(np.abs(x)) or 1.0) * 0.85
    pcm = (x * 32767).astype(np.int16)
    stereo = np.column_stack([pcm, pcm])
    return pygame.sndarray.make_sound(np.ascontiguousarray(stereo))


# ---------------------------------------------------------------------------
# End-of-level jingles, one per star count. Five stars is the victory fanfare.
# ---------------------------------------------------------------------------
def _tone_env(f, dur, kind="brass", sr=SR):
    t = _t(dur)
    if kind == "brass":
        raw = _saw(t * f) + 0.6 * _saw(t * f * 1.004) + 0.3 * _saw(t * f * 0.5)
        sig = np.convolve(np.tanh(1.6 * raw), np.ones(6) / 6, mode="same")
        env = np.minimum(1.0, t / 0.03) * np.minimum(1.0, np.maximum(0.0, (dur - t) / 0.06)) * (0.7 + 0.3 * np.exp(-t * 3))
    elif kind == "bell":
        sig = np.sin(2 * np.pi * f * t) + 0.4 * np.sin(2 * np.pi * f * 2.76 * t) * np.exp(-t * 6)
        env = np.minimum(1.0, t / 0.003) * np.exp(-t * 3)
    elif kind == "pluck":
        sig = np.sin(2 * np.pi * f * t) + 0.3 * np.sin(6 * np.pi * f * t)
        env = np.minimum(1.0, t / 0.002) * np.exp(-t * 9)
    else:  # organ / soft
        sig = sum(a * np.sin(2 * np.pi * f * h * t) for h, a in ((1, 1), (2, 0.5), (3, 0.25)))
        env = np.minimum(1.0, t / 0.02) * np.minimum(1.0, np.maximum(0.0, (dur - t) / 0.1))
    return sig * env


def _midi(n):
    return 440.0 * 2 ** ((n - 69) / 12)


def jingle(stars, sr=SR):
    """Mono float32. 0..1 star: a short sag; 2: a plain cadence; 3: a bright cadence; 4: a
    rising fanfare; 5: the full victory fanfare with drums."""
    events = []                                     # (time, signal, gain)

    def note(t0, midi, dur, kind="brass", gain=0.3):
        events.append((t0, _tone_env(_midi(midi), dur, kind), gain))

    def chord(t0, midis, dur, kind="brass", gain=0.22):
        for m in midis:
            note(t0, m, dur, kind, gain)

    if stars <= 1:
        note(0.0, 64, 0.45, "soft", 0.35); note(0.4, 62, 0.45, "soft", 0.35); note(0.8, 58, 1.2, "soft", 0.35)
        events.append((0.8, kick(), 0.5))
        total = 2.2
    elif stars == 2:
        chord(0.0, [60, 64, 67], 0.5, "soft"); chord(0.5, [59, 62, 67], 0.5, "soft"); chord(1.0, [60, 64, 67], 1.2, "soft")
        total = 2.4
    elif stars == 3:
        for i, m in enumerate([67, 72, 76]):
            note(i * 0.14, m, 0.3, "pluck", 0.35)
        chord(0.42, [72, 76, 79], 1.4, "bell", 0.3)
        chord(0.42, [48, 55], 1.4, "soft", 0.25)
        total = 2.0
    elif stars == 4:
        for i, m in enumerate([60, 64, 67, 72]):
            note(i * 0.12, m, 0.25, "brass", 0.3)
        chord(0.5, [72, 76, 79, 84], 1.6, "brass", 0.2)
        chord(0.5, [48, 55], 1.6, "soft", 0.3)
        events.append((0.5, crash(), 0.5)); events.append((0.5, kick(), 0.6))
        total = 2.4
    else:
        # victory fanfare: triplet pickup, held note, ascending run, big chord with tremolo
        for i in range(3):
            note(i * 0.11, 67, 0.12, "brass", 0.32)
            events.append((i * 0.11, snare(), 0.25))
        note(0.33, 67, 0.55, "brass", 0.32); note(0.33, 55, 0.55, "soft", 0.3)
        events.append((0.33, kick(), 0.8)); events.append((0.33, crash(), 0.6))
        for i, m in enumerate([63, 65, 67, 70, 72, 74]):
            note(0.9 + i * 0.09, m, 0.16, "brass", 0.28)
        note(1.45, 75, 0.5, "brass", 0.32); note(1.45, 51, 0.5, "soft", 0.3)
        events.append((1.45, kick(), 0.8)); events.append((1.45, crash(seed=9, dur=1.3), 0.5))
        for i, m in enumerate([67, 70, 72, 75, 79]):
            note(2.0 + i * 0.08, m, 0.14, "brass", 0.28)
        # final chord with tremolo, a bell on top, timpani hits
        for k in range(6):
            chord(2.45 + k * 0.5, [72, 75, 79, 84], 0.55, "brass", 0.16 * (1.0 if k < 5 else 1.3))
            note(2.45 + k * 0.5, 48 if k % 2 == 0 else 55, 0.5, "soft", 0.3)
            events.append((2.45 + k * 0.5, kick(), 0.7))
        chord(2.45, [91, 96], 3.5, "bell", 0.12)
        events.append((2.45, crash(), 0.6)); events.append((4.95, crash(seed=9, dur=1.6), 0.6))
        chord(4.95, [72, 76, 79, 84], 2.4, "brass", 0.18)
        note(4.95, 48, 2.4, "soft", 0.32)
        total = 7.6
    out = _mix_events(events, total)
    return out.astype(np.float32)


def output_devices():
    """Names of the audio output devices SDL can open. SDL only lists them once its audio
    subsystem is up, so open the mixer on the default device first when nothing is open."""
    try:
        from pygame._sdl2 import audio
        if not pygame.mixer.get_init():
            pygame.mixer.init()                  # SDL refuses to list devices before its audio system is up
        return list(audio.get_audio_device_names(False))
    except Exception:
        return []


class SoundBank:
    """Builds the sounds once and plays them by chart key ("kick", "n45", ...).
    device: output device name (substring match), None = system default."""

    def __init__(self, enabled=True, device=None, drums=True):
        self.ok = False
        self.sounds = {}
        self.device = None
        self.drums = drums          # False: the kit is silent (the module or a DAW makes the drum sound)
        if not enabled:
            return
        name = None
        if device:
            names = output_devices()            # needs SDL audio up: list before quitting the mixer
            name = next((n for n in names if device.lower() in n.lower()), None)
            if name is None:
                print(f"audio device '{device}' not found, using the default. Available: {', '.join(names) or 'none'}")
        if pygame.mixer.get_init():
            pygame.mixer.quit()
        try:
            pygame.mixer.pre_init(SR, -16, 2, MIXER_BUFFER)
            if name:
                pygame.mixer.init(devicename=name)
            else:
                pygame.mixer.init()
            pygame.mixer.set_num_channels(NUM_CHANNELS)
            pygame.mixer.set_reserved(RESERVED)
        except pygame.error as e:
            print(f"audio disabled: {e}")
            return
        self.ok = True
        self.device = name
        print(f"audio output: {name or 'system default'}")
        self.sounds = {
            "kick": _to_sound(kick()), "snare": _to_sound(snare()),
            "hihat": _to_sound(hihat()), "crash": _to_sound(crash()),
            "hihat_open": _to_sound(hihat_open()), "hihat_mid": _to_sound(hihat_mid()), "chick": _to_sound(chick()),
            "pedal": _to_sound(chick()),
            "tom1": _to_sound(tom()), "floor": _to_sound(floor_tom()), "ride": _to_sound(ride()),
            "crash2": _to_sound(crash(seed=9, dur=1.3)),
            "click": _to_sound(click()), "click_hi": _to_sound(click(high=True)),
        }

    def play_jingle(self, stars):
        """The end-of-level jingle for a star count; rendered on first use, then cached."""
        if not self.ok:
            return
        key = f"jingle{stars}"
        if key not in self.sounds:
            self.sounds[key] = _to_sound(jingle(stars))
        self.stop_jingle()
        ch = self.sounds[key].play()
        if ch is not None:
            ch.set_volume(0.9 * _master)
            self._jingle = self.sounds[key]

    def stop_jingle(self):
        """Cut the end-of-level jingle at once (retry / next / back must not carry it over)."""
        j = getattr(self, "_jingle", None)
        if j is not None:
            j.stop()
            self._jingle = None

    def _get(self, key):
        s = self.sounds.get(key)
        if s is None and key.startswith("n"):
            s = self.sounds[key] = _to_sound(tone(int(key[1:])))
        return s

    def play(self, key, velocity=100, gain=1.0, art=None):
        """art: a hi-hat articulation (hhmapper's labels) picks the open, mid, tight or chick sample."""
        if not self.ok or (not self.drums and key not in ("click", "click_hi")):
            return
        if art in HH_SOUND and key in ("hihat", "pedal"):
            key = HH_SOUND[art]
        s = self._get(key)
        if s is None:
            return
        ch = s.play()
        if ch is not None:
            ch.set_volume(max(0.05, min(1.0, gain * (0.3 + 0.7 * velocity / 127))) * _master)


# ---------------------------------------------------------------------------
# Backing: a small arrangement rendered once for the whole level. Each level
# picks a style (timbres, harmony, section plan, melody generator) so levels
# sound different from each other: synth, chiptune, metal, punk, organ,
# strings, funk.
# ---------------------------------------------------------------------------
# (root semitone relative to C, chord quality) per bar. Qualities: M, m, 5 (power
# chord), M7, m7, sus2, dim.
PROGRESSIONS = {
    "pop": [
        [(9, "m"), (5, "M"), (0, "M"), (7, "M")],      # Am F C G
        [(0, "M"), (7, "M"), (9, "m"), (5, "M")],      # C G Am F
        [(4, "m"), (0, "M"), (7, "M"), (2, "M")],      # Em C G D
        [(7, "M"), (2, "M"), (4, "m"), (0, "M")],      # G D Em C
        [(5, "M"), (7, "M"), (9, "m"), (9, "m")],      # F G Am Am
    ],
    "minor": [
        [(2, "m"), (10, "M"), (5, "M"), (0, "M")],     # Dm Bb F C
        [(9, "m"), (7, "M"), (5, "M"), (7, "M")],      # Am G F G
        [(2, "m"), (7, "M"), (0, "M"), (9, "m")],      # Dm G C Am
        [(0, "m"), (8, "M"), (3, "M"), (10, "M")],     # Cm Ab Eb Bb
        [(9, "m7"), (5, "M7"), (2, "m7"), (4, "m7")],  # Am7 Fmaj7 Dm7 Em7
    ],
    "metal": [
        [(4, "5"), (5, "5"), (4, "5"), (2, "5")],      # E5 F5 E5 D5   phrygian
        [(4, "5"), (4, "5"), (7, "5"), (5, "5")],      # E5 E5 G5 F5
        [(9, "5"), (0, "5"), (7, "5"), (10, "5")],     # A5 C5 G5 Bb5
        [(4, "5"), (10, "5"), (9, "5"), (7, "5")],     # E5 Bb5 A5 G5  tritone
        [(2, "5"), (2, "5"), (5, "5"), (4, "5")],      # D5 D5 F5 E5
    ],
    "punk": [
        [(0, "5"), (5, "5"), (7, "5"), (7, "5")],      # C5 F5 G5 G5
        [(9, "5"), (5, "5"), (0, "5"), (7, "5")],      # A5 F5 C5 G5
        [(2, "5"), (9, "5"), (7, "5"), (2, "5")],      # D5 A5 G5 D5
        [(4, "5"), (0, "5"), (7, "5"), (2, "5")],      # E5 C5 G5 D5
    ],
    "soul": [
        [(2, "m7"), (7, "M7"), (0, "M7"), (9, "m7")],  # Dm7 G7 Cmaj7 Am7
        [(4, "m7"), (9, "m7"), (2, "m7"), (7, "M7")],  # Em7 Am7 Dm7 G7
        [(0, "M7"), (4, "m7"), (5, "M7"), (7, "M7")],  # Cmaj7 Em7 Fmaj7 G7
        [(9, "m7"), (2, "m7"), (9, "m7"), (4, "m7")],  # Am7 Dm7 Am7 Em7
    ],
    "cinematic": [
        [(9, "m"), (9, "m"), (5, "M7"), (7, "sus2")],  # Am Am Fmaj7 Gsus2
        [(2, "m"), (10, "M7"), (2, "m"), (0, "sus2")], # Dm Bbmaj7 Dm Csus2
        [(4, "m"), (0, "M7"), (2, "sus2"), (11, "dim")],  # Em Cmaj7 Dsus2 Bdim
        [(0, "m"), (8, "M7"), (10, "sus2"), (7, "m")], # Cm Abmaj7 Bbsus2 Gm
    ],
    "funk": [
        [(4, "m7"), (4, "m7"), (9, "m7"), (4, "m7")],  # Em7 Em7 Am7 Em7
        [(2, "m7"), (7, "M7"), (2, "m7"), (7, "M7")],  # Dm7 G7 Dm7 G7
        [(9, "m7"), (2, "m7"), (4, "m7"), (2, "m7")],  # Am7 Dm7 Em7 Dm7
    ],
}
BACKING_GAIN = 0.55
SECTION_BARS = 4
BASS_PATTERNS = {                          # (sixteenth index, degree, gain); degree 0 = root, 7 = fifth
    "eighths": [(e * 2, 0, 1.0 if e % 2 == 0 else 0.7) for e in range(8)],
    "root_fifth": [(0, 0, 1.0), (4, 7, 0.8), (8, 0, 1.0), (12, 7, 0.8), (14, 0, 0.6)],
    "sync": [(0, 0, 1.0), (6, 0, 0.9), (8, 7, 0.8), (12, 0, 0.9), (14, 12, 0.6)],
    "sparse": [(0, 0, 1.0), (12, 0, 0.6)],
    "whole": [(0, 0, 1.0)],
    "octave": [(e * 2, 0 if e % 2 == 0 else 12, 1.0 if e % 2 == 0 else 0.75) for e in range(8)],
    "gallop": [(q * 4 + k, 0, 1.0 if k == 0 else 0.7) for q in range(4) for k in (0, 2, 3)],
    "chug": [(0, 0, 1.0), (2, 0, 0.8), (4, 0, 0.9), (6, 0, 0.8), (8, 0, 1.0), (10, 0, 0.8), (11, 0, 0.7), (14, 0, 0.8)],
    "funk": [(0, 0, 1.0), (3, 12, 0.7), (6, 7, 0.9), (8, 0, 0.9), (11, 12, 0.7), (13, 10, 0.6), (14, 0, 0.9)],
    "pump": [(e, 0, 1.0 if e % 4 == 0 else 0.7) for e in range(16)],
    "cumbia": [(0, 0, 1.0), (6, 7, 0.85), (8, 0, 1.0), (14, 7, 0.85)],   # the tumbao: root on 1 and 3, the fifth on the & of 2 and 4
}
# The same for levels in triplets ("triplet" feel): indices are twelfths of a bar, beat q
# at 3q, its third triplet (the shuffle "a") at 3q + 2. Nothing here falls on a straight
# eighth or sixteenth, so the music only ever confirms the subdivision being practised.
BASS_PATTERNS_TRIPLET = {
    "quarters": [(q * 3, 0, 1.0 if q % 2 == 0 else 0.85) for q in range(4)],
    "shuffle": [(q * 3 + k, 0, 1.0 if k == 0 else 0.7) for q in range(4) for k in (0, 2)],
    "shuffle_fifth": [(q * 3 + k, 0 if k == 0 else 7, 1.0 if k == 0 else 0.7) for q in range(4) for k in (0, 2)],
    "triplets": [(q * 3 + k, 0, 1.0 if k == 0 else 0.65) for q in range(4) for k in range(3)],
    "sparse": [(0, 0, 1.0), (9, 0, 0.6)],
    "whole": [(0, 0, 1.0)],
}
# What plays per 4-bar section: (bass pattern, chord part, chord pattern, lead, pad).
# chord part: "pad" (sustained), "arp" (pluck, pattern = order), "chug" (power-chord
# stabs on the bass rhythm), "stab" (short chord hits on a rhythm), "strum" (chord on
# every eighth).
STYLES = {
    "synth": dict(
        progressions=("pop", "minor"), pad="saw", bass="sine", chord="pluck", lead="sine", lead_kind="phrase",
        scale="natural", plan=[
            ("eighths", None, None, False, True), ("eighths", "arp", "up", False, True),
            ("root_fifth", "arp", "up", True, True), ("sync", "arp", "updown", True, True),
            ("sparse", None, None, False, True), ("octave", "arp", "sixteenths", True, True),
            ("root_fifth", "arp", "broken", False, True), ("sync", "arp", "up", True, False)]),
    "chiptune": dict(
        progressions=("pop",), pad=None, bass="square", chord="square", lead="square", lead_kind="riff",
        scale="pentatonic", plan=[
            ("octave", "arp", "sixteenths", False, False), ("octave", "arp", "sixteenths", True, False),
            ("pump", "arp", "up", True, False), ("sync", "arp", "updown", True, False),
            ("eighths", None, None, True, False), ("octave", "arp", "sixteenths", True, False)]),
    "metal": dict(
        progressions=("metal",), pad="dark", bass="dist", chord="power", lead="lead_saw", lead_kind="riff",
        scale="phrygian", plan=[
            ("chug", "chug", None, False, False), ("gallop", "chug", None, False, False),
            ("chug", "chug", None, True, False), ("whole", "pad_only", None, False, True),
            ("gallop", "chug", None, True, False), ("pump", "chug", None, False, False),
            ("chug", "chug", None, True, True)]),
    # Pop punk (the courses, 2026-09-20 rewrite: "make it sound much better and more punk"): a
    # power-chord guitar (`guitar` below: two gain stages, a box cabinet, a pick scratch) playing
    # palm-muted eighths with an open chord ringing on every accent ("drive"), every eighth open in
    # the chorus ("wash") or the accents alone, the band stopping ("figure"); a picked, gritty
    # bass; a lead in octaves on the same guitar; no pad; the bus saturated (`bus_drive`). The
    # accents are the bass pattern's slots, or the level's own kick figure, bar by bar, when the
    # level hammers it (Chart.hammer: bass "kick" everywhere and the last bar of every "drive"
    # section stops on the figure), so the ear has the feet's rhythm in the music.
    "punk": dict(
        progressions=("punk",), pad=None, bass="punk", chord="power", lead="octave", lead_kind="riff",
        scale="pentatonic", guitar=True, bus_drive=1.6, plan=[
            ("eighths", "drive", None, False, False), ("eighths", "drive", None, True, False),
            ("octave", "wash", None, True, False), ("sync", "figure", None, False, False),
            ("eighths", "drive", None, False, False), ("octave", "wash", None, True, False),
            ("eighths", "drive", None, True, False), ("sync", "figure", None, True, False)]),
    "organ": dict(
        progressions=("soul",), pad="organ", bass="sub", chord="bell", lead="triangle", lead_kind="phrase",
        scale="dorian", plan=[
            ("root_fifth", None, None, False, True), ("root_fifth", "stab", None, False, True),
            ("sync", "stab", None, True, True), ("funk", "arp", "broken", True, True),
            ("whole", None, None, True, True), ("sync", "stab", None, True, True)]),
    "strings": dict(
        progressions=("cinematic", "minor"), pad="strings", bass="sub", chord="bell", lead="lead_saw", lead_kind="long",
        scale="natural", plan=[
            ("whole", None, None, False, True), ("whole", "arp", "up", False, True),
            ("sparse", "arp", "up", True, True), ("root_fifth", "arp", "updown", True, True),
            ("whole", None, None, True, True), ("eighths", "arp", "sixteenths", True, True)]),
    "funk": dict(
        progressions=("funk",), pad=None, bass="pick", chord="clav", lead="square", lead_kind="riff",
        scale="pentatonic", plan=[
            ("funk", "stab", None, False, False), ("funk", "stab", None, True, False),
            ("octave", "stab", None, True, True), ("funk", None, None, False, False),
            ("funk", "stab", None, True, False), ("sync", "arp", "broken", True, True)]),
}
# Triplet levels always get this one: a plain shuffle. Bass on the beat and the third
# triplet from the first bar, then an arpeggio that plays all three triplets of every
# beat, so the ear hears the subdivision the hands are learning.
SHUFFLE_STYLE = dict(
    progressions=("soul", "pop"), pad="organ", bass="pick", chord="clav", lead="triangle", lead_kind="phrase",
    scale="pentatonic", plan=[
        ("shuffle", None, None, False, True), ("shuffle", "arp", "triplets", False, True),
        ("shuffle_fifth", "stab", None, False, True), ("shuffle", "arp", "triplets", True, True),
        ("quarters", "stab", None, True, True), ("shuffle_fifth", "arp", "triplets", True, True)])
# Sextuplet levels (subdivision 6, "sextuplet" feel): the shuffle's third triplet lands on the
# fifth stroke of the sextuplet, a soft double, and its arpeggio and stabs fight the hands
# (2026-09-13: "Six stroke roll" was confusing). So only the beat: bass on the quarters, a pad,
# no chords, no lead.
SEXTUPLET_STYLE = dict(SHUFFLE_STYLE, plan=[("quarters", None, None, False, True)] * 6)
# Cumbia villera (Chart.backing == "cumbia", 2026-09-13, called plena until 2026-09-14: the generic
# arrangement did not sound like one and did not help hear the groove): the cumbia tumbao on a round bass, the keyboard
# chords on every & ("a contratiempo", a reed timbre), a güiro on every beat (a long scrape on
# the beat, two short ticks on the e and the &... "chi-ki-chi-ki"), a flute-like tune now and
# then, no pad: dry and rhythmic. The güiro is not a drum voice, so it never doubles what the
# drummer plays on the bodies.
CUMBIA_STYLE = dict(
    progressions=("minor", "pop"), pad=None, bass="sine", chord="reed", lead="triangle", lead_kind="phrase",
    scale="natural", stab_rhythm=[2, 6, 10, 14], guiro=True, plan=[
        ("cumbia", "stab", None, False, False), ("cumbia", "stab", None, True, False),
        ("cumbia", "stab", None, False, False), ("cumbia", "stab", None, True, False)])
# A double-kick level's own music (Chart.backing == "kick", asked for 2026-09-20: the gallop under
# a synth pop backing drawn by index "had nothing to do with it"): metal, and the guitar and the
# bass hammer the level's own kick figure, bar after bar, so the ear has the feet's rhythm in the
# music before the feet find it. The rhythm comes from the chart (Chart.kick_rhythm, passed to
# make_arrangement as `rhythm`); the timbres and the progressions are the metal style's; the
# plan only brings the pad and the lead in and out, the riff never leaves.
KICK_STYLE = dict(STYLES["metal"], plan=[
    ("kick", "chug", None, False, False), ("kick", "chug", None, False, True),
    ("kick", "chug", None, True, False), ("kick", "chug", None, True, True)])
STYLE_ORDER = ["synth", "metal", "chiptune", "punk", "organ", "strings", "funk"]
STAB_RHYTHMS = [[0, 6, 8, 14], [2, 6, 10, 14], [0, 3, 6, 10, 12], [4, 12], [0, 7, 10]]   # sixteenth indices
STAB_RHYTHMS_TRIPLET = [[3, 9], [0, 2, 6, 8], [3, 5, 9, 11], [2, 5, 8, 11]]              # twelfth indices
SCALES = {"natural": {"M": [0, 2, 4, 5, 7, 9, 11], "m": [0, 2, 3, 5, 7, 8, 10]},
          "pentatonic": {"M": [0, 2, 4, 7, 9], "m": [0, 3, 5, 7, 10]},
          "dorian": {"M": [0, 2, 4, 5, 7, 9, 11], "m": [0, 2, 3, 5, 7, 9, 10]},
          "phrygian": {"M": [0, 2, 4, 5, 7, 9, 11], "m": [0, 1, 3, 5, 7, 8, 10]}}


def style_for(prog_index, feel="straight"):
    if feel in ("cumbia", "kick"):
        return feel
    if feel in STYLES:                  # a course names its style (Chart.backing == "punk")
        return feel
    return "shuffle" if feel in ("triplet", "sextuplet") else STYLE_ORDER[prog_index % len(STYLE_ORDER)]


def _midi_hz(n):
    return 440.0 * 2 ** ((n - 69) / 12)


def _saw(phase):
    return 2 * (phase % 1.0) - 1


def _square(phase):
    return np.where((phase % 1.0) < 0.5, 1.0, -1.0)


def _chord_notes(root, quality, octave_base=57, inversion=0):
    """Chord tones as MIDI numbers around octave_base (A3 = 57)."""
    r = octave_base + ((root - octave_base) % 12)
    iv = {"M": [0, 4, 7], "m": [0, 3, 7], "5": [0, 7, 12], "M7": [0, 4, 7, 11], "m7": [0, 3, 7, 10],
          "sus2": [0, 2, 7], "dim": [0, 3, 6]}[quality]
    tones = [r + i for i in iv]
    for _ in range(inversion):
        tones = tones[1:] + [tones[0] + 12]
    return tones


def _minor(quality):
    return quality in ("m", "m7", "dim") or quality == "5"


def _arp_order(pattern, chord):
    top = [m + 12 for m in chord[:3]]
    if pattern == "broken":
        return [top[0], top[2], top[1], top[2]]
    if pattern == "sixteenths":
        return top + [top[2] + 12]
    if pattern == "triplets":
        return top                                    # one chord tone per triplet, a triad per beat
    return top + [top[1]]


def _lowpass(sig, n):
    """Moving average over n samples, centred, zeros outside (what np.convolve(sig, ones(n) / n,
    "same") gives), in O(len) through a cumulative sum."""
    if n <= 1:
        return sig
    cs = np.concatenate([np.zeros(1), np.cumsum(sig, dtype=np.float64)])
    left, right = n // 2, n - n // 2                      # window [i - left, i + right)
    idx = np.arange(len(sig))
    lo = np.clip(idx - left, 0, len(sig))
    hi = np.clip(idx + right, 0, len(sig))
    return (cs[hi] - cs[lo]) / n


def _eq(sig, points):
    """sig through a gain curve, [(hz, dB), ...] interpolated over log frequency (FFT, zero phase):
    the guitar's cabinet, the bass amp. Cheap enough per stroke."""
    n = len(sig)
    f = np.fft.rfftfreq(n, 1 / SR)
    gain = 10 ** (np.interp(np.log(np.maximum(f, 1.0)), np.log([p[0] for p in points]), [p[1] for p in points]) / 20)
    return np.fft.irfft(np.fft.rfft(sig) * gain, n)


# The guitar cabinet: lows cut, a presence bump around 3 kHz, the fizz rolled off; the muted
# stroke darker; the bass amp round with a bump in the low mids for the pick to read.
CAB_OPEN = [(40, -30), (90, -10), (150, 0), (700, -2), (1500, 0), (3000, 6), (4500, 4), (6500, -4), (9000, -14), (15000, -30)]
CAB_MUTED = [(40, -30), (90, -10), (150, 0), (700, -1), (1500, -1), (3000, 2), (4500, -2), (6500, -12), (9000, -24), (15000, -40)]
BASS_AMP = [(30, -6), (60, 0), (150, 0), (700, 3), (1500, -2), (3000, -10), (6000, -30)]


def make_arrangement(bpm, prog_index=0, bars=8, intro_bars=0, sr=SR, seed=None, feel="straight", rhythm=None, normalise=True):
    """Mono float32 of (intro_bars + bars) bars at bpm: intro (thin) then the arrangement,
    bar 0 of the level at intro_bars * bar seconds. Deterministic per prog_index.
    feel: "straight" (sixteenth grid, style by prog_index) or "triplet" (twelfth grid,
    the shuffle style) for levels whose subdivision is 3; "cumbia" for the cumbia levels;
    a STYLES name ("punk") for the courses, which choose their music; "kick" for the
    double-kick levels, whose riff is `rhythm` = (grid 16 or 12, [[(slot, gain)], ...]), the
    level's own kick figure bar by bar (Chart.kick_rhythm); with any other style, `rhythm`
    makes the bass play the figure in every section (Chart.hammer)."""
    rng = np.random.default_rng(prog_index if seed is None else seed)
    triplet = feel in ("triplet", "sextuplet") or (rhythm is not None and rhythm[0] == 12)
    style_name = style_for(prog_index, feel)
    st = CUMBIA_STYLE if feel == "cumbia" else KICK_STYLE if feel == "kick" else SEXTUPLET_STYLE if feel == "sextuplet" else SHUFFLE_STYLE if triplet else STYLES[style_name]
    bass_patterns = dict(BASS_PATTERNS_TRIPLET if triplet else BASS_PATTERNS)
    figures = None                                          # the kick figure per bar, when the music hammers it
    if rhythm is not None:
        figures = rhythm[1] if rhythm[1] and isinstance(rhythm[1][0], list) else [rhythm[1]]
        figures = [f or [(0, 1.0)] for f in figures]
    stab_rhythms = STAB_RHYTHMS_TRIPLET if triplet else STAB_RHYTHMS
    family = st["progressions"][int(rng.integers(0, len(st["progressions"])))]
    prog = PROGRESSIONS[family][int(rng.integers(0, len(PROGRESSIONS[family])))]
    transpose = int(rng.integers(-4, 4))
    stab_rhythm = st.get("stab_rhythm") or stab_rhythms[int(rng.integers(0, len(stab_rhythms)))]
    riff = None                                             # built on first use, then transposed
    beat = 60 / bpm
    bar = 4 * beat
    step = bar / (12 if triplet else 16)                    # one grid slot of the pattern tables
    total_bars = intro_bars + bars
    n = int(round(total_bars * bar * sr))
    out = np.zeros(n + int(1.5 * sr))

    def add(start_s, sig, gain=1.0):
        i = int(round(start_s * sr))
        if i >= len(out) or i < 0:
            return
        j = min(i + len(sig), len(out))
        out[i:j] += sig[: j - i] * gain

    def env_ad(t, attack, decay, hold=None):
        e = np.minimum(1.0, t / max(attack, 1e-4)) * np.exp(-t * decay)
        if hold is not None:
            e *= np.minimum(1.0, np.maximum(0.0, (hold - t) / 0.03))
        return e

    # --- pads ----------------------------------------------------------------------
    def pad(t0, chord, kind):
        tp = np.arange(int(bar * sr) + int(0.1 * sr)) / sr
        sig = np.zeros_like(tp)
        for m in chord:
            f = _midi_hz(m)
            if kind == "saw":
                sig += _saw(tp * f * 1.003) + _saw(tp * f * 0.997)
            elif kind == "strings":
                sig += _saw(tp * f * 1.004) + _saw(tp * f * 0.996) + 0.5 * _saw(tp * f * 2.002)
            elif kind == "organ":
                vib = 1 + 0.003 * np.sin(2 * np.pi * 6 * tp)
                sig += sum(a * np.sin(2 * np.pi * f * h * tp * vib) for h, a in ((1, 1), (2, 0.6), (3, 0.4), (4, 0.3), (6, 0.15)))
            elif kind == "dark":
                sig += np.tanh(1.5 * (_saw(tp * f * 0.5 * 1.002) + _saw(tp * f * 0.5 * 0.998)))
        attack = {"saw": 0.06, "strings": 0.35, "organ": 0.01, "dark": 0.2}[kind]
        env = np.minimum(1.0, tp / attack) * np.minimum(1.0, np.maximum(0.0, (bar + 0.1 - tp) / 0.12))
        sig = _lowpass(sig * env, {"saw": 48, "strings": 64, "organ": 8, "dark": 90}[kind])
        add(t0, sig, {"saw": 0.16, "strings": 0.14, "organ": 0.12, "dark": 0.12}[kind] / max(1, len(chord) / 3))

    # --- bass ----------------------------------------------------------------------
    def bass(t0, root_note, pattern, kind):
        slots = bass_patterns[pattern]
        for k, (e, degree, gain) in enumerate(slots):
            f = _midi_hz(root_note + degree)
            if pattern == "kick":                            # the figure: every note held to the next
                dur = ((slots[k + 1][0] if k + 1 < len(slots) else (12 if triplet else 16)) - e) * step
            else:
                dur = {"whole": bar, "sparse": beat}.get(pattern, 2 * beat / 3 if triplet else beat / 2)
            tb = np.arange(int(dur * sr)) / sr
            if kind == "sine":
                sig = (np.sin(2 * np.pi * f * tb) * 0.8 + np.sin(4 * np.pi * f * tb) * 0.25 + _saw(tb * f) * 0.15) * env_ad(tb, 0.004, 6)
            elif kind == "sub":
                sig = (np.sin(2 * np.pi * f * tb) + 0.15 * np.sin(4 * np.pi * f * tb)) * env_ad(tb, 0.01, 1.5 if pattern == "whole" else 3, dur)
            elif kind == "square":
                sig = _lowpass(_square(tb * f) * 0.6 + 0.3 * _square(tb * f * 0.5), 6) * env_ad(tb, 0.003, 8)
            elif kind == "pick":
                pick = _noise(len(tb), 21) * np.exp(-tb * 400) * 0.5
                sig = (_lowpass(_saw(tb * f) + 0.4 * _square(tb * f * 0.5), 10) + pick) * env_ad(tb, 0.002, 7)
            elif kind == "punk":                              # picked and gritty: saw and square at the root through a pedal, held to the next note
                pick = _noise(len(tb), 21) * np.exp(-tb * 350) * 0.6
                raw = 0.8 * _saw(tb * f) + 0.5 * _square(tb * f * 1.001) + pick
                sig = _eq(np.tanh(2.2 * raw), BASS_AMP) * env_ad(tb, 0.002, 3.5, dur)
            else:  # dist: chugging power root, palm-muted
                raw = _saw(tb * f) + _saw(tb * f * 1.5) * 0.6 + _saw(tb * f * 2) * 0.4
                sig = _lowpass(np.tanh(3.0 * raw), 14) * env_ad(tb, 0.002, 18 if pattern in ("chug", "gallop", "pump", "kick") else 6)
            add(t0 + e * step, sig, (0.6 if kind == "punk" else 0.55) * gain)

    # --- chord parts -----------------------------------------------------------------
    def pluck_tone(f, ta, kind):
        if kind == "pluck":
            return (np.sin(2 * np.pi * f * ta) + 0.3 * np.sin(6 * np.pi * f * ta)) * env_ad(ta, 0.002, 14)
        if kind == "square":
            return _lowpass(_square(ta * f), 4) * env_ad(ta, 0.001, 10)
        if kind == "bell":
            return (np.sin(2 * np.pi * f * ta) + 0.5 * np.sin(2 * np.pi * f * 2.76 * ta) * np.exp(-ta * 8)) * env_ad(ta, 0.002, 4)
        if kind == "clav":
            return _lowpass(_saw(ta * f) * _square(ta * f * 0.501), 3) * env_ad(ta, 0.001, 22)
        if kind == "reed":                                # accordion / cumbia keyboard: two detuned saws, a little square, held
            return _lowpass(_saw(ta * f * 1.004) + _saw(ta * f * 0.996) + 0.35 * _square(ta * f * 0.5), 7) * env_ad(ta, 0.006, 6, 0.2)
        return _saw(ta * f) * env_ad(ta, 0.002, 12)

    def arp(t0, chord, pattern, kind):
        order = _arp_order(pattern, chord)
        steps = {"sixteenths": 16, "triplets": 12}.get(pattern, 8)
        per_beat = steps // 4
        seq = order + order[-2:0:-1] if pattern == "updown" else order
        for e in range(steps):
            f = _midi_hz(seq[e % len(seq)])
            ta = np.arange(int(0.25 * sr)) / sr
            add(t0 + e * bar / steps, pluck_tone(f, ta, kind), (0.22 if steps == 8 else 0.17) * (1.0 if e % per_beat == 0 else 0.8))

    def stab(t0, chord, kind, rhythm):
        for e in rhythm:
            ta = np.arange(int(0.22 * sr)) / sr
            sig = sum(pluck_tone(_midi_hz(m + 12), ta, kind) for m in chord)
            add(t0 + e * step, sig, (0.11 if kind == "reed" else 0.16) / max(1, len(chord) / 3))

    # --- güiro (cumbia) --------------------------------------------------------------
    def guiro(t0, bar_index):
        """Per beat: a long scrape on the beat (noise through a rising resonance, 110 ms) and
        two short ticks on the e and the & ("chi-ki-chi-ki"); the & tick a touch louder."""
        for q in range(4):
            tb = np.arange(int(0.11 * sr)) / sr
            scrape = _noise(len(tb), 300 + bar_index * 4 + q)
            scrape = np.diff(scrape, prepend=0.0) * (1 + 3 * tb / 0.11)      # brighter as the stick travels
            scrape *= np.minimum(1.0, tb / 0.004) * np.exp(-tb * 18) * np.minimum(1.0, np.maximum(0.0, (0.11 - tb) / 0.01))
            add(t0 + q * beat, scrape, 0.07)
            for k, gain in ((2, 0.55), (3, 0.7)):
                tt = np.arange(int(0.03 * sr)) / sr
                tick = np.diff(_noise(len(tt), 700 + bar_index * 8 + q * 2 + k), prepend=0.0) * np.exp(-tt * 140)
                add(t0 + q * beat + k * step, tick, 0.07 * gain)

    def power(t0, root_note, pattern):
        """Distorted power chord: root, fifth, octave through tanh, on the bass rhythm (chug)."""
        for e, _, gain in bass_patterns[pattern]:
            dur = step * 1.6
            ta = np.arange(int(max(dur, 0.12) * sr)) / sr
            raw = np.zeros_like(ta)
            for m in (root_note, root_note + 7, root_note + 12):
                f = _midi_hz(m)
                raw += _saw(ta * f * 1.002) + _saw(ta * f * 0.998)
            sig = _lowpass(np.tanh(2.2 * raw), 6) * env_ad(ta, 0.003, 16, dur)
            add(t0 + e * step, sig, 0.10 * gain)

    def guitar(t0, root_note, mode, accents, seed):
        """The punk guitar: a power chord (root, fifth, octave, two detuned saws each) with a
        pick scratch, through two gain stages with the lows cut between them and a box cabinet.
        "drive": palm-muted eighths, an open chord ringing until the next stroke on every accent;
        "wash": every eighth open (the chorus); "figure": the accents alone, the band stopping
        with the kick. accents: sixteenth slots (the kick figure when the level hammers it)."""
        acc = sorted(set(accents))
        slots = acc if mode == "figure" else sorted(set(range(0, 16, 2)) | set(acc))
        for k, e in enumerate(slots):
            open_ = mode == "wash" or e in acc
            gap = ((slots[k + 1] if k + 1 < len(slots) else 16) - e) * step
            dur = gap if open_ else min(0.11, gap)
            ta = np.arange(int((dur + 0.02) * sr)) / sr
            raw = np.zeros_like(ta)
            for m in (root_note, root_note + 7, root_note + 12):
                f = _midi_hz(m)
                raw += _saw(ta * f * 1.0025) + _saw(ta * f * 0.9975)
            x = raw / 6 + _noise(len(ta), seed + k) * np.exp(-ta * 500) * 0.8
            x = np.tanh(3.5 * x)                             # the pedal
            x = x - _lowpass(x, 300)                         # nothing under ~150 Hz into the amp
            x = np.tanh(2.5 * x)                             # the amp
            x = _eq(x, CAB_OPEN if open_ else CAB_MUTED)     # the cabinet, a muted stroke darker
            env = np.minimum(1.0, ta / (0.003 if open_ else 0.002)) * np.exp(-ta * (0.9 if open_ else 22)) \
                * np.minimum(1.0, np.maximum(0.0, (dur - ta) / 0.02))
            add(t0 + e * step, x * env, 0.42 if open_ else 0.25)

    # --- lead ------------------------------------------------------------------------
    def tone(f, tl, kind):
        ph = 2 * np.pi * f * tl
        vib = 1 + 0.004 * np.sin(2 * np.pi * 5.5 * tl) * np.minimum(1.0, tl / 0.3)
        if kind == "sine":
            sig = np.sin(ph) + 0.2 * np.sin(2 * ph)
        elif kind == "triangle":
            sig = 2 / np.pi * np.arcsin(np.sin(ph))
        elif kind == "square":
            sig = _lowpass(_square(tl * f), 3) * 0.7
        elif kind == "octave":                             # the pop punk lead: octaves on the distorted guitar
            sig = _eq(np.tanh(2.5 * (_saw(tl * f * 1.003) + _saw(tl * f * 0.997) + 0.8 * _saw(tl * f * 2))), CAB_OPEN) * 0.8
        else:  # lead_saw
            sig = _lowpass(_saw(tl * f) + 0.5 * _saw(tl * f * 1.005), 5) * 0.8
        return np.interp(tl * vib, tl, sig) if len(tl) > 1 else sig

    def scale_notes(root, quality):
        degs = SCALES[st["scale"]]["m" if _minor(quality) else "M"]
        return [root + 60 + d for d in degs] + [root + 72]

    def lead(t0, root, quality, chord, bar_in_section, kind):
        nonlocal riff
        scale = scale_notes(root, quality)
        tones = {m % 12 for m in chord}
        if kind == "riff":
            if riff is None:                                  # (slot, scale degree index, length in sixteenths)
                if st["lead"] == "octave":                    # on the eighths, legato: each note held to the next
                    slots = sorted(2 * int(x) for x in rng.choice(8, size=int(rng.integers(3, 6)), replace=False))
                    riff = [(sl, int(rng.integers(0, len(scale))), (slots[k + 1] if k + 1 < len(slots) else 16) - sl)
                            for k, sl in enumerate(slots)]
                else:
                    slots = sorted(rng.choice(16, size=int(rng.integers(4, 7)), replace=False))
                    riff = [(int(sl), int(rng.integers(0, len(scale))), 2) for sl in slots]
            events = [(sl * 0.25, d, ln * 0.25) for sl, d, ln in riff]
        elif kind == "long":
            events = [(0, None, 2.0), (2, None, 2.0)] if bar_in_section % 2 == 0 else [(0, None, 4.0)]
        elif triplet:  # phrase, swung: nothing lands on a straight eighth
            rhythm = [(0, 1.0), (1 + 2 / 3, 1 / 3), (2, 1.0), (3, 2 / 3), (3 + 2 / 3, 1 / 3)] if bar_in_section % 2 == 0 \
                else [(2 / 3, 1 / 3), (1, 1.0), (2, 2.0)]
            events = [(start, None, length) for start, length in rhythm]
        else:  # phrase
            rhythm = [(0, 1.0), (1.5, 0.5), (2, 1.0), (3, 0.5), (3.5, 0.5)] if bar_in_section % 2 == 0 else [(0.5, 1.5), (2, 2.0)]
            events = [(start, None, length) for start, length in rhythm]
        pos = int(rng.integers(2, min(6, len(scale))))
        for k, (start, deg, length) in enumerate(events):
            if deg is None:
                pos = max(0, min(len(scale) - 1, pos + int(rng.integers(-2, 3))))
                if k == 0 or start in (0, 2):                  # strong beats land on a chord tone
                    cands = [i for i, m in enumerate(scale) if m % 12 in tones] or [0]
                    pos = min(cands, key=lambda i: abs(i - pos))
            else:
                pos = deg
            f = _midi_hz(scale[pos])
            dur = length * beat
            tl = np.arange(int(dur * sr)) / sr
            decay = 0.6 if kind == "long" else 1.0 if st["lead"] == "octave" else 2.5
            env = np.minimum(1.0, tl / 0.02) * np.exp(-tl * decay) * np.minimum(1.0, np.maximum(0.0, (dur - tl) / 0.05))
            add(t0 + start * beat, tone(f, tl, st["lead"]) * env, 0.3 if st["lead"] == "octave" else 0.13 if kind != "long" else 0.11)

    plan = st["plan"]
    if figures is not None and feel != "kick":               # the level hammers its kick figure: the bass plays it in every section
        plan = [("kick",) + tuple(p[1:]) for p in plan]
    for i in range(total_bars):
        b = i - intro_bars                                    # level bar, negative in the intro
        t0 = i * bar
        root, quality = prog[b % len(prog)]
        root = (root + transpose) % 12
        if figures is not None:
            bass_patterns["kick"] = [(e, 0, g) for e, g in figures[b % len(figures)]]
        if b >= 0:
            bass_pat, part, part_pat, lead_on, pad_on = plan[(b // SECTION_BARS) % len(plan)]
            if figures is not None and part == "drive" and b % SECTION_BARS == SECTION_BARS - 1:
                part = "figure"                               # the band stops on the figure before every section
        elif st.get("guitar"):                                # the count-in: one open chord ringing on its 1
            bass_pat, part, part_pat, lead_on, pad_on = ("whole", "intro", None, False, False)
        else:
            bass_pat, part, part_pat, lead_on, pad_on = ("sparse", None, None, False, st["pad"] is not None)
        inversion = (b // len(prog)) % 3 if b >= 0 and quality != "5" else 0
        chord = _chord_notes(root, quality, inversion=inversion)
        bass_root = _chord_notes(root, quality)[0] - 24
        if pad_on and st["pad"]:
            pad(t0, chord, st["pad"])
        if part != "pad_only" and st["bass"]:
            bass(t0, bass_root, bass_pat, st["bass"])
        if part == "arp":
            arp(t0, chord, part_pat, st["chord"] if st["chord"] != "power" else "pluck")
        elif part == "stab":
            stab(t0, chord, st["chord"] if st["chord"] != "power" else "pluck", stab_rhythm)
        elif part == "chug":
            power(t0, bass_root + 12, bass_pat)
        elif part in ("drive", "wash", "figure"):
            accents = [e for e, _, g in bass_patterns[bass_pat] if bass_pat == "kick" or e % 4 == 0 or g >= 0.9]
            guitar(t0, bass_root + 12, part, accents, seed=1000 + i * 16)
        elif part == "intro":
            guitar(t0, bass_root + 12, "figure", [0], seed=1000 + i * 16)
        if lead_on and st["lead"]:
            lead(t0, root, quality, chord, b % SECTION_BARS, st["lead_kind"])
        if st.get("guiro") and b >= 0:
            guiro(t0, i)

    out = out[:n]
    if not normalise:                                         # the raw sum, for measuring the parts against each other
        return out.astype(np.float32)
    out = out / (np.max(np.abs(out)) or 1.0)
    if st.get("bus_drive"):                                   # the bus saturated: denser, the way the genre is mixed
        out = np.tanh(st["bus_drive"] * out) / np.tanh(st["bus_drive"])
    return (out * 0.8).astype(np.float32)


def make_backing(bpm, prog_index=0, bars=4, sr=SR):
    """Backwards-compatible: (float32 mono array of `bars` bars, length in seconds)."""
    data = make_arrangement(bpm, prog_index, bars, 0, sr)
    return data, len(data) / sr


def backing_sound(bpm, prog_index=0, bars=4, lead_in_s=0.0):
    """pygame Sound of the loop plus its length in seconds. Needs the mixer initialised.
    lead_in_s: playback starts that long before chart time 0; the loop is rotated so
    the first chord still lands on chart time 0."""
    data, length = make_backing(bpm, prog_index, bars)
    data = np.roll(data, int(round(lead_in_s * SR)))
    pcm = (data * 32767).astype(np.int16)
    stereo = np.ascontiguousarray(np.column_stack([pcm, pcm]))
    snd = pygame.sndarray.make_sound(stereo)
    snd.set_volume(BACKING_GAIN * _master)
    return snd, length


# ---------------------------------------------------------------------------
# Metronome: round, conga-like hits, pre-rendered for the whole level so they
# are sample-accurate and sit inside the mix instead of on top of it.
# Level (2026-09-19, "too quiet even at 160 %"): the track's gain tops out at pygame's
# set_volume ceiling (METRONOME_GAIN * METRO_VOLUME_MAX = 0.96 on a track at full scale),
# so the only headroom left is in the samples: the hits are driven hard (RMS up at the same
# peak), get a slap on top (the sine body alone was A-weighted 10 dB under its RMS) and the
# track goes through a soft limiter (METRO_DRIVE) instead of a peak normalise. Measured on a
# 90 bpm beat and a 152 bpm pop punk level: +7 dB A-weighted at 100 %, +11 dB at 160 %.
# ---------------------------------------------------------------------------
METRONOME_GAIN = 0.6
METRO_LEVELS = {"low": 1.0, "mid": 0.72, "tap": 0.34}
METRO_DRIVE = 2.5               # tanh drive of the rendered track's limiter (1.0 is nearly clean)


def conga(f=180.0, dur=0.35, drop=1.35, decay=9.0, noise=0.12, seed=7, slap=0.5, drive=3.5):
    """A tuned hand-drum: sine with a quick pitch drop, hard saturation (drive), a breath of noise
    and the hand's slap (slap: a 1.5..5 kHz burst with a bright partial, so the hit reads through
    the cymbals and the backing)."""
    t = _t(dur)
    freq = f * (1 + (drop - 1) * np.exp(-t * 90))
    phase = 2 * math.pi * np.cumsum(freq) / SR
    body = np.sin(phase) * np.exp(-t * decay)
    attack = _noise(len(t), seed) * np.exp(-t * 350) * noise
    burst = _lowpass(np.diff(_noise(len(t), seed + 100), prepend=0.0), 5) * np.exp(-t * 220)
    burst /= np.max(np.abs(burst)) or 1.0
    ping = np.sin(2 * math.pi * f * 9.7 * t) * np.exp(-t * 120)
    x = np.tanh(drive * (body + attack)) / np.tanh(drive) + slap * (0.7 * burst + 0.4 * ping)
    x *= np.minimum(1.0, t / 0.001)
    return x / (np.max(np.abs(x)) or 1.0)


METRO_HITS = {
    "low": lambda: conga(150.0, 0.40, 1.4, 8.0, 0.10, 11),   # downbeat: low conga
    "mid": lambda: conga(215.0, 0.30, 1.3, 11.0, 0.12, 12),  # beats 2, 3, 4
    "tap": lambda: conga(330.0, 0.12, 1.2, 32.0, 0.25, 13),  # subdivisions: muted tap
}


def _mix_events(events, total_s, drive=None):
    """events: [(time_s, mono array, gain)] -> mono float32 of total_s seconds. Without drive
    the mix is brought down to peak 0.9 if it goes over; with one it is normalised to full scale
    and soft-limited (tanh, peak 0.98), which lifts every hit's RMS at the same peak."""
    out = np.zeros(int(total_s * SR) + SR)
    for t0, sig, gain in events:
        i = int(round(t0 * SR))
        if i < 0 or i >= len(out):
            continue
        j = min(i + len(sig), len(out))
        out[i:j] += sig[: j - i] * gain
    peak = np.max(np.abs(out))
    if drive:
        out = np.tanh(drive * out / (peak or 1.0)) / np.tanh(drive) * 0.98
    elif peak > 0.9:
        out *= 0.9 / peak
    return out.astype(np.float32)


def render_metronome(chart, lead_in_s, total_s, mode="full"):
    """Congas for every bar from the count-in to total_s (chart time), on the chart's own
    beat grid (constant tempo or a song's tracked beats). mode: full / beats."""
    hits = {k: f() for k, f in METRO_HITS.items()}
    events = []
    first_beat = int(round(chart.beat_pos(-lead_in_s)))
    last_beat = int(chart.beat_pos(total_s)) + 1
    for i in range(first_beat, last_beat):
        t0 = chart.beat_time(i)
        b = i % 4
        sub = chart.subdivision_at(max(0.0, t0))
        events.append((t0 + lead_in_s, hits["low" if b == 0 else "mid"], METRO_LEVELS["low" if b == 0 else "mid"]))
        if mode == "full":
            for k in range(1, sub):
                events.append((chart.beat_time(i + k / sub) + lead_in_s, hits["tap"], METRO_LEVELS["tap"]))
    return _mix_events(events, lead_in_s + total_s, METRO_DRIVE)


def render_backing_track(bpm, prog_index, lead_in_s, total_s, feel="straight", rhythm=None):
    """The arrangement from the count-in to total_s, bar 0 landing on chart time 0.
    feel: see make_arrangement; "triplet" for levels whose subdivision is 3; rhythm for "kick"."""
    bar = 240 / bpm
    intro_bars = int(round(lead_in_s / bar))
    bars = int(np.ceil(total_s / bar)) + 1
    data = make_arrangement(bpm, prog_index, bars, intro_bars, feel=feel, rhythm=rhythm)
    n = int((lead_in_s + total_s) * SR) + SR
    if len(data) < n:
        data = np.concatenate([data, np.zeros(n - len(data), dtype=np.float32)])
    return data[:n]


class Track:
    """A pre-rendered track on the chart timeline, starting at t0 (usually -lead_in).
    Playback can start from any point, which makes pause/resume and late joins exact.
    data: mono float32 in -1..1, or stereo int16 (n, 2) straight from a decoded file."""

    def __init__(self, data, t0, gain=1.0, channel=None):
        self.channel = channel        # reserved mixer channel index, or None for any free one
        if data.dtype == np.int16 and data.ndim == 2:
            self.pcm = np.ascontiguousarray(data)
        else:
            pcm = (np.clip(data, -1, 1) * 32767).astype(np.int16)
            self.pcm = np.ascontiguousarray(np.column_stack([pcm, pcm]))
        self.t0 = t0
        self.gain = gain
        self.sound = None
        self.playing = False

    @property
    def end(self):
        return self.t0 + len(self.pcm) / SR

    def start_at(self, t):
        self.stop()
        i = int(max(0.0, t - self.t0) * SR)
        if i >= len(self.pcm):
            return
        self.sound = pygame.sndarray.make_sound(np.ascontiguousarray(self.pcm[i:]))
        self.apply_gain()
        if self.channel is not None:
            pygame.mixer.Channel(self.channel).play(self.sound)
        else:
            self.sound.play()
        self.playing = True

    def apply_gain(self):
        """Track gain times the master level, on the sound that is playing (if any)."""
        if self.sound is not None:
            self.sound.set_volume(self.gain * _master)

    def stop(self):
        if self.sound is not None:
            self.sound.stop()
        self.sound = None
        self.playing = False


# ---------------------------------------------------------------------------
# Menu music: a slow ambient texture for the hub and lists. Soft chords that
# drift, a sparse pentatonic pluck with a delay tail, a low hum underneath.
# ---------------------------------------------------------------------------
MENU_MUSIC_GAIN = 0.32
MENU_CHORDS = [[57, 60, 64, 67], [55, 59, 62, 66], [53, 57, 60, 64], [52, 55, 59, 62]]   # Am7 Gmaj7 Fmaj7 Em7
MENU_PLUCKS = [69, 72, 74, 76, 79, 81, 84]                                             # A minor pentatonic


def make_menu_music(bar_s=4.0, bars=8, sr=SR):
    """Loop of `bars` bars of `bar_s` seconds. Chords change every two bars."""
    total = bars * bar_s
    n = int(total * sr)
    tail = int(2.0 * sr)
    out = np.zeros(n + tail)

    def add(start_s, sig, gain):
        i = int(round(start_s * sr))
        j = min(i + len(sig), len(out))
        if i < j:
            out[i:j] += sig[: j - i] * gain

    rng = np.random.default_rng(21)
    for b in range(bars):
        chord = MENU_CHORDS[(b // 2) % len(MENU_CHORDS)]
        t0 = b * bar_s
        # pad, with a slow swell so chords breathe
        tp = np.arange(int(bar_s * sr) + int(1.0 * sr)) / sr
        pad = np.zeros_like(tp)
        for m in chord:
            f = _midi_hz(m)
            pad += _saw(tp * f * 1.004) + _saw(tp * f * 0.996) + 0.6 * np.sin(2 * np.pi * f / 2 * tp)
        env = np.minimum(1.0, tp / 0.9) * np.minimum(1.0, np.maximum(0.0, (bar_s + 1.0 - tp) / 1.0))
        env *= 0.85 + 0.15 * np.sin(2 * np.pi * tp / bar_s * 0.5)
        pad = np.convolve(pad * env, np.ones(160) / 160, mode="same")
        add(t0, pad, 0.10)
        # sparse plucks: a few per bar, mostly on the eighths, with two soft echoes
        for e in range(8):
            if rng.random() > 0.4:
                continue
            note = MENU_PLUCKS[rng.integers(len(MENU_PLUCKS))] + (12 if rng.random() < 0.2 else 0)
            f = _midi_hz(note)
            ta = np.arange(int(1.2 * sr)) / sr
            sig = (np.sin(2 * np.pi * f * ta) + 0.25 * np.sin(4 * np.pi * f * ta)) * np.exp(-ta * 3.0) * np.minimum(1.0, ta / 0.003)
            when = t0 + e * bar_s / 8
            add(when, sig, 0.16)
            add(when + 0.375, sig, 0.08)
            add(when + 0.75, sig, 0.04)
    # low hum on the chord root
    t_all = np.arange(n + tail) / sr
    for b in range(0, bars, 2):
        f = _midi_hz(MENU_CHORDS[(b // 2) % len(MENU_CHORDS)][0] - 24)
        seg = slice(int(b * bar_s * sr), int((b + 2) * bar_s * sr))
        tt = t_all[seg] - b * bar_s
        env = np.minimum(1.0, tt / 0.5) * np.minimum(1.0, np.maximum(0.0, (2 * bar_s - tt) / 0.5))
        out[seg] += np.sin(2 * np.pi * f * tt) * env * 0.12
    out[:tail] += out[n:n + tail]
    out = out[:n]
    out = out / (np.max(np.abs(out)) or 1.0) * 0.8
    return out.astype(np.float32)


def menu_music_sound():
    data = make_menu_music()
    pcm = (data * 32767).astype(np.int16)
    snd = pygame.sndarray.make_sound(np.ascontiguousarray(np.column_stack([pcm, pcm])))
    snd.set_volume(MENU_MUSIC_GAIN * _master)
    return snd


