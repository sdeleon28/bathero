"""Keygen music: the busy chiptune loop the waiting screen plays.

Not the game's menu music (that one drifts on purpose) and not `make_arrangement`
either: the backing engine is built to sit under a drummer, so it has no drums and
never gets in the way. This is the opposite, a 2000s cracktro tune - four chords in
a minor key, sixteenth arpeggios with a ping-pong delay, a pulse lead with vibrato,
a driving square bass and a drum machine on top, eight sections that add and drop
layers, about a hundred seconds, looped. A different tune every run: everything is
drawn from one seed.

The drums and the oscillator helpers come from `sounds`, so the waiting screen sounds
like the same instrument as the game. Rendering is a few seconds, which is why the
screen does it in a thread and fades the music in when it is ready.

`render()` also returns the two envelopes the animation reacts to (low = kick and
bass, high = hats and arpeggio), one value per 60th of a second, so the card can
pulse on the beat without any analysis at playback time.

Some levels play it as their backing (`Chart.backing == "keygen"`, `render_level`):
the sextuplet rudiments, which at 60 bpm feel like triplets at 120. The tune goes
out at twice the level's tempo on a triplet grid (12 slots a bar instead of 16), so
its arpeggio lands on the level's sextuplets, its beat is the level's eighth and its
backbeat the level's off-beats; the count-in is the tune's intro and the level's bar 0
the drop of the main section. One tune per level (the seed is the level's name).
"""
import math

import numpy as np

from .sounds import (SR, _chord_notes, _lowpass, _midi_hz, _noise, _saw, crash,
                     hihat, hihat_open, kick, snare, tom)

# Four bars, one chord each, all of them minor-key staples of the genre.
PROGS = [
    [(9, "m"), (5, "M"), (0, "M"), (7, "M")],      # Am F C G
    [(9, "m"), (7, "M"), (5, "M"), (7, "M")],      # Am G F G
    [(2, "m"), (10, "M"), (5, "M"), (0, "M")],     # Dm Bb F C
    [(0, "m"), (8, "M"), (3, "M"), (10, "M")],     # Cm Ab Eb Bb
    [(4, "m"), (0, "M"), (7, "M"), (4, "m")],      # Em C G Em
    [(9, "m"), (5, "M"), (7, "M"), (9, "m")],      # Am F G Am
]
NAMES = ["C", "Db", "D", "Eb", "E", "F", "Gb", "G", "Ab", "A", "Bb", "B"]
MINOR = [0, 2, 3, 5, 7, 8, 10]

# (drums, bass, arp, lead, pad) per eight-bar section. 0 is off; the numbers are
# how thick the layer plays. The last section leads back into the first.
PLAN = [
    dict(name="intro",  drums=0, bass=0, arp=1, lead=0, pad=1),
    dict(name="beat",   drums=1, bass=1, arp=1, lead=0, pad=1),
    dict(name="main",   drums=2, bass=1, arp=2, lead=1, pad=1),
    dict(name="lift",   drums=3, bass=2, arp=2, lead=1, pad=1),
    dict(name="break",  drums=0, bass=0, arp=1, lead=2, pad=1),
    dict(name="drop",   drums=3, bass=2, arp=2, lead=1, pad=1),
    dict(name="bridge", drums=2, bass=1, arp=1, lead=0, pad=1),
    dict(name="outro",  drums=3, bass=2, arp=2, lead=1, pad=1),
]
SECTION_BARS = 8

# The pattern tables per grid: 16 slots a bar (sixteenths, the cracktro proper) or 12
# (triplets, the level backing). Slots are the bar's grid positions; the bass carries
# (slot, semitones over the root); riff slots span two bars; hats are the closed
# pattern of the thin sections (the thick ones play every slot).
GRIDS = {
    16: dict(kicks={1: [0, 8], 2: [0, 6, 8, 14], 3: [0, 4, 8, 12]}, pickups=[2, 10],
             snares={1: [], 2: [4, 12], 3: [4, 12]}, ghosts={3: [7, 15, 11]},
             bass=[[(0, 0), (3, 0), (6, 12), (8, 0), (11, 0), (14, 12)],
                   [(0, 0), (2, 12), (3, 0), (6, 0), (8, 0), (10, 12), (11, 0), (14, 0)],
                   [(0, 0), (4, 7), (6, 0), (8, 0), (12, 7), (14, 0)],
                   [(2, 0), (6, 0), (10, 0), (14, 0)]],
             riff_slots=list(range(0, 32, 2)), hats=list(range(0, 16, 2)), delay=3),
    12: dict(kicks={1: [0, 6], 2: [0, 4, 6, 10], 3: [0, 3, 6, 9]}, pickups=[2, 8],
             snares={1: [], 2: [3, 9], 3: [3, 9]}, ghosts={3: [5, 11, 8]},
             bass=[[(0, 0), (2, 0), (5, 12), (6, 0), (8, 0), (11, 12)],
                   [(0, 0), (2, 12), (3, 0), (5, 0), (6, 0), (8, 12), (9, 0), (11, 0)],
                   [(0, 0), (3, 7), (5, 0), (6, 0), (9, 7), (11, 0)],
                   [(2, 0), (5, 0), (8, 0), (11, 0)]],
             riff_slots=[s for s in range(24) if s % 3 != 1], hats=[0, 2, 3, 5, 6, 8, 9, 11], delay=2),
}


def _pulse(phase, duty):
    return np.where((phase % 1.0) < duty, 1.0, -1.0)


class Voices:
    """Every note the tune needs, cached: four chords over a few octaves is a
    handful of distinct waveforms played a couple of thousand times."""

    def __init__(self, sr=SR):
        self.sr = sr
        self.cache = {}

    def _env(self, n, attack, decay):
        t = np.arange(n) / self.sr
        return np.minimum(1.0, t / attack) * np.exp(-t * decay)

    def get(self, kind, f, dur, duty=0.5):
        key = (kind, round(f, 2), round(dur, 4), duty)
        sig = self.cache.get(key)
        if sig is not None:
            return sig
        n = int(dur * self.sr)
        t = np.arange(n) / self.sr
        if kind == "arp":
            sig = _pulse(t * f, duty) * self._env(n, 0.0015, 13.0)
        elif kind == "lead":
            vib = 1 + 0.006 * np.sin(2 * np.pi * 5.6 * t) * np.minimum(1.0, t / 0.18)
            sig = (_pulse(t * f * vib, duty) + 0.55 * _pulse(t * f * 1.006 * vib, 0.35)) \
                * np.minimum(1.0, t / 0.004) * np.exp(-t * 1.6) \
                * np.minimum(1.0, np.maximum(0.0, (dur - t) / 0.04))
        elif kind == "bass":
            sig = (_lowpass(_pulse(t * f, 0.5) * 0.8 + _saw(t * f) * 0.4, 7)
                   + np.sin(2 * np.pi * f * t) * 0.8) * self._env(n, 0.002, 9.0)
        else:  # pad: detuned saws, slow in, dull
            sig = sum(_saw(t * f * d) for d in (0.994, 1.0, 1.006)) / 3
            sig = _lowpass(sig, 40) * np.minimum(1.0, t / 0.25) \
                * np.minimum(1.0, np.maximum(0.0, (dur - t) / 0.3))
        sig = sig.astype(np.float32)
        self.cache[key] = sig
        return sig


def _delay(buf, samples, feedback=0.34, taps=5):
    """A feedback delay written as a sum of taps: same result, no sample loop."""
    out = buf.copy()
    g = feedback
    for k in range(1, taps + 1):
        d = samples * k
        if d >= len(buf):
            break
        out[d:] += buf[:-d] * g
        g *= feedback
    return out


def _riser(n, sr=SR):
    t = np.arange(n) / sr
    up = (t / max(t[-1], 1e-6)) ** 2
    hiss = np.diff(_noise(n, 77), prepend=0.0) * up
    swoop = np.sin(2 * np.pi * np.cumsum(180 + 2400 * up) / sr) * up * 0.25
    return ((hiss + swoop) * np.minimum(1.0, (1 - up) * 6 + 0.15)).astype(np.float32)


def render(bpm=None, seed=None, sections=None, sr=SR, grid=16, plan=None, loop=True):
    """The whole loop. Returns a dict with the stereo int16 samples and the
    envelopes the animation follows.
    grid: slots a bar, 16 (sixteenths) or 12 (triplets, see GRIDS). plan: the sections
    to play instead of PLAN, each with an optional "bars" (SECTION_BARS otherwise).
    loop: fold what rings past the end back onto the top (the waiting screen loops it)."""
    rng = np.random.default_rng(seed)
    bpm = float(bpm or rng.integers(146, 163))
    prog = PROGS[int(rng.integers(0, len(PROGS)))]
    if plan is None:
        plan = PLAN if sections is None else PLAN[:sections]
    G = GRIDS[grid]
    beat = 60.0 / bpm
    bar = 4 * beat
    step = bar / grid                                # one slot of the grid
    q = grid // 4                                    # slots a beat
    # every bar: (section index, bar within the section, the section's length)
    layout = [(si, b, sec.get("bars", SECTION_BARS)) for si, sec in enumerate(plan)
              for b in range(sec.get("bars", SECTION_BARS))]
    bars = len(layout)
    n = int(bars * bar * sr) + int(2.0 * sr)
    mid = np.zeros(n, np.float32)                    # kick, snare, bass: centred
    wide = np.zeros((2, n), np.float32)              # arp, lead, pad, hats
    v = Voices(sr)

    def add(buf, t0, sig, gain=1.0):
        i = int(t0 * sr)
        if i < 0 or i >= len(buf):
            return
        j = min(i + len(sig), len(buf))
        buf[i:j] += sig[: j - i] * gain

    # drum kit, rendered once
    kck = (kick() * 1.15).astype(np.float32)
    snr = snare().astype(np.float32)
    hat = hihat().astype(np.float32)
    hato = hihat_open(0.30, seed=31).astype(np.float32)
    cym = crash(dur=2.2).astype(np.float32)
    toms = [tom(f0, 0.30, seed=40 + i).astype(np.float32)
            for i, f0 in enumerate((300.0, 240.0, 190.0, 150.0))]

    key_root = prog[0][0]
    scale = [key_root + 60 + d for d in MINOR] + [key_root + 72, key_root + 74, key_root + 75]
    # One riff for the whole tune, in sixteenths: a shape the ear can hold on to.
    slots = sorted(rng.choice(np.array(G["riff_slots"]), size=int(rng.integers(7, 11)), replace=False))
    riff = []
    pos = int(rng.integers(2, 6))
    for sl in slots:
        pos = int(np.clip(pos + rng.integers(-3, 4), 0, len(scale) - 1))
        riff.append((int(sl), pos))
    arp_dir = ["up", "updown", "up2"][int(rng.integers(0, 3))]

    for bi, (si, b_in_sec, sec_bars) in enumerate(layout):
        sec = plan[si]
        root, quality = prog[bi % len(prog)]
        chord = _chord_notes(root, quality)
        t0 = bi * bar
        last = b_in_sec == sec_bars - 1

        # --- pad -------------------------------------------------------------
        if sec["pad"]:
            for j, m in enumerate(chord):
                sig = v.get("pad", _midi_hz(m), bar * 1.05)
                p = 0.5 + 0.42 * (j - 1)              # spread the voices across the stereo
                add(wide[0], t0, sig, 0.085 * (1 - p))
                add(wide[1], t0, sig, 0.085 * p)

        # --- bass ------------------------------------------------------------
        if sec["bass"]:
            pat = G["bass"][(si + sec["bass"]) % len(G["bass"])]
            for e, deg in pat:
                f = _midi_hz(chord[0] - 24 + deg)
                add(mid, t0 + e * step, v.get("bass", f, step * 1.9), 0.52)

        # --- arpeggio --------------------------------------------------------
        if sec["arp"]:
            tones = [m + 12 for m in chord]
            up = tones + [tones[0] + 12]
            order = {"up": up, "up2": up + [m + 12 for m in up],
                     "updown": up + up[-2:0:-1]}[arp_dir]
            duty = 0.25 if sec["arp"] > 1 else 0.5
            for e in range(grid):
                f = _midi_hz(order[e % len(order)])
                g = 0.23 * (1.0 if e % q == 0 else 0.78)
                add(wide[e % 2], t0 + e * step, v.get("arp", f, step * 1.6, duty), g)

        # --- lead ------------------------------------------------------------
        if sec["lead"] and b_in_sec % 2 == 0:
            tones = {m % 12 for m in chord}
            for sl, deg in riff:
                p = deg
                if sl % (2 * q) == 0:                # land the strong slots on a chord tone
                    cands = [i for i, m in enumerate(scale) if m % 12 in tones] or [p]
                    p = min(cands, key=lambda i: abs(i - p))
                f = _midi_hz(scale[p] + (24 if sec["lead"] > 1 else 12))
                sig = v.get("lead", f, step * 3.4, 0.5)
                add(wide[0], t0 + sl * step, sig, 0.20)
                add(wide[1], t0 + sl * step, sig, 0.20)

        # --- drums -----------------------------------------------------------
        d = sec["drums"]
        if d:
            for e in G["kicks"][d] + (G["pickups"] if d == 3 and b_in_sec % 4 == 2 else []):
                add(mid, t0 + e * step, kck, 0.80)
            for e in G["snares"][d]:
                add(mid, t0 + e * step, snr, 0.62)
            for e in G["ghosts"].get(d, []) if b_in_sec % 2 else []:
                add(mid, t0 + e * step, snr, 0.12)
            for e in range(grid) if d >= 2 else G["hats"]:
                openhat = d >= 2 and e % q == 2 and (b_in_sec % 2 == 1)
                sig = hato if openhat else hat
                g = (0.10 if openhat else 0.16 * (1.0 if e % 4 == 0 else 0.62))
                add(wide[e % 2], t0 + e * step, sig, g * 0.85)
                add(wide[(e + 1) % 2], t0 + e * step, sig, g * 0.35)
        if b_in_sec == 0 and sec["drums"]:
            add(wide[0], t0, cym, 0.30)
            add(wide[1], t0, cym, 0.26)
        if last and plan[(si + 1) % len(plan)]["drums"]:
            for i, e in enumerate(range(grid // 2, grid, 2)):   # tom fill into the next section
                add(mid, t0 + e * step, toms[i % len(toms)], 0.55)
                add(mid, t0 + (e + 1) * step, snr, 0.22)
            add(mid, t0 + bar - 2.0, _riser(int(2.0 * sr), sr), 0.22)

    # --- mix ---------------------------------------------------------------
    dly = int(round(step * G["delay"] * sr))          # 3/16, the classic ping-pong (2/12 in triplets)
    wide[0] = _delay(wide[0], dly, 0.30)
    wide[1] = _delay(wide[1], dly + int(0.004 * sr), 0.34)
    length = bars * bar
    cut = int(length * sr)
    if loop:
        tail = 1.2                                    # what rings past the loop comes back at the top
        ring = int(tail * sr)
        for buf in (mid, wide[0], wide[1]):
            buf[:ring] += buf[cut : cut + ring]
    left = (mid + wide[0])[:cut]
    right = (mid + wide[1])[:cut]
    peak = max(np.max(np.abs(left)), np.max(np.abs(right))) or 1.0
    left = np.tanh(left / peak * 1.6) * 0.82
    right = np.tanh(right / peak * 1.6) * 0.82

    # --- what the animation dances to --------------------------------------
    mono = (left + right) * 0.5
    fps = 60
    hop = sr // fps
    frames = cut // hop
    m = mono[: frames * hop].reshape(frames, hop)
    low = np.sqrt((_lowpass(mono, 96)[: frames * hop].reshape(frames, hop) ** 2).mean(axis=1))
    hi = m - _lowpass(mono, 16)[: frames * hop].reshape(frames, hop)
    high = np.sqrt((hi ** 2).mean(axis=1))
    low = np.clip(low / (np.percentile(low, 97) or 1.0), 0, 1.4).astype(np.float32)
    high = np.clip(high / (np.percentile(high, 97) or 1.0), 0, 1.4).astype(np.float32)

    pcm = np.ascontiguousarray(
        np.column_stack([(left * 32767).astype(np.int16), (right * 32767).astype(np.int16)]))
    return dict(pcm=pcm, sr=sr, bpm=bpm, bars=bars, length=length, fps=fps,
                low=low, high=high, beat=60.0 / bpm,
                key=NAMES[key_root % 12] + ("m" if prog[0][1].startswith("m") else ""))


LEVEL_SECTIONS = PLAN[2:]                            # main, lift, break, drop, bridge, outro, and round again


def render_level(bpm, seed, lead_in_s, total_s, sr=SR):
    """The tune as a level's backing: stereo int16 from -lead_in_s to past total_s, the
    level's bar 0 at lead_in_s. Twice the level's tempo on the triplet grid (a 60 bpm
    sextuplet level is the tune at 120 in triplets); the count-in is the intro section,
    the level starts on "main" and the sections go round from there."""
    kbpm = 2.0 * bpm
    kbar = 240.0 / kbpm
    intro = int(round(lead_in_s / kbar))
    need = int(np.ceil((lead_in_s + total_s) / kbar)) + 1 - intro
    plan = [dict(PLAN[0], bars=intro)] if intro else []
    i = 0
    while need > 0:
        sec = LEVEL_SECTIONS[i % len(LEVEL_SECTIONS)]
        plan.append(dict(sec, bars=min(SECTION_BARS, need)))
        need -= SECTION_BARS
        i += 1
    tr = render(bpm=kbpm, seed=seed, sr=sr, grid=12, plan=plan, loop=False)
    head = int(round(intro * kbar * sr)) - int(round(lead_in_s * sr))   # 0 unless the count-in is not whole bars
    pcm = tr["pcm"]
    if head > 0:
        pcm = pcm[head:]
    elif head < 0:
        pcm = np.concatenate([np.zeros((-head, 2), np.int16), pcm])
    return pcm


def main(argv=None):
    """`python -m bathero.keygen [--wav out.wav]`: render one tune and play it."""
    import argparse
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("--seed", type=int, default=None)
    ap.add_argument("--bpm", type=float, default=None)
    ap.add_argument("--wav", default=None)
    ap.add_argument("--seconds", type=float, default=None, help="stop after this long")
    args = ap.parse_args(argv)
    import time
    t0 = time.time()
    tr = render(bpm=args.bpm, seed=args.seed)
    print("%s %.0f bpm, %d bars, %.1f s, rendered in %.1f s"
          % (tr["key"], tr["bpm"], tr["bars"], tr["length"], time.time() - t0))
    if args.wav:
        import wave
        with wave.open(args.wav, "wb") as w:
            w.setnchannels(2)
            w.setsampwidth(2)
            w.setframerate(tr["sr"])
            w.writeframes(tr["pcm"].tobytes())
        print(args.wav)
        return 0
    import os
    os.environ.setdefault("PYGAME_HIDE_SUPPORT_PROMPT", "1")
    import pygame
    pygame.mixer.init(frequency=tr["sr"], size=-16, channels=2, buffer=1024)
    snd = pygame.sndarray.make_sound(tr["pcm"])
    snd.play(loops=-1)
    time.sleep(args.seconds or tr["length"])
    return 0


if __name__ == "__main__":
    import sys
    sys.exit(main())
