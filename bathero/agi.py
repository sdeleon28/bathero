"""AGI: a waiting screen for the stream, drawn in the terminal.

`cortina` (the repo's `./cortina`, symlinked into ~/bin, or
`.venv/bin/python -m bathero.agi`) takes over the terminal with a
full-screen animation for the moments the user walks away from the camera: the
Twitch-style "be right back" card (LIVE badge, away timer, rotating excuse) over
a rotation of scenes that look like a machine thinking - latent space, attention,
reaction-diffusion, a forward/backward pass, training telemetry and a token
stream. With `stream_source: "screen"` (the default, see CLAUDE.md) ffmpeg
captures the whole display, so a full-screen terminal on display 0 is what the
viewers get.

The picture is a real pixel canvas, not ASCII art: every cell is the half block
U+2580 with the top half as the foreground colour and the bottom half as the
background, so the resolution is cols x (2*rows) in 24-bit colour. A second
layer puts real characters on chosen cells (HUD, labels, bars) so text stays
crisp instead of being drawn as pixels. Only the cells that changed are written
each frame, and the colour escapes are emitted only when they change, which is
what keeps a 212x58 terminal at 30 fps cheap.

Keys: q/Esc quit, space next scene, 1..6 jump to a scene, p pause, h HUD,
b the be-right-back card, f fps counter.

The same screen in English is `curtain` (`./curtain`, `--lang en`): one string
table per language (STRINGS below), one animation.
"""
import argparse
import math
import os
import random
import select
import shutil
import signal
import sys
import termios
import time
import tty

import numpy as np

# Before anything can import pygame (the title font, the music): its banner
# would land on the alt screen and stay there.
os.environ.setdefault("PYGAME_HIDE_SUPPORT_PROMPT", "1")
os.environ.setdefault("SDL_VIDEODRIVER", "dummy")

# ---------------------------------------------------------------- palette
# Linear-light colours: everything is composited additively and tone-mapped at
# the end, so these are intensities, not sRGB values. Small numbers are normal.
BG       = (0.0018, 0.0024, 0.0052)
CYAN     = (0.16, 0.86, 1.00)
VIOLET   = (0.52, 0.34, 1.00)
MAGENTA  = (1.00, 0.24, 0.78)
AMBER    = (1.00, 0.62, 0.20)
LIME     = (0.45, 1.00, 0.55)
WHITE    = (0.88, 0.93, 1.00)
SLATE    = (0.30, 0.36, 0.52)
DIM      = (0.16, 0.20, 0.30)
RED      = (1.00, 0.18, 0.26)

ACCENTS = [CYAN, VIOLET, MAGENTA, AMBER, LIME]

HALF = "▀"   # upper half block


def lerp(a, b, u):
    return a + (b - a) * u


def mix(c1, c2, u):
    return tuple(a + (b - a) * u for a, b in zip(c1, c2))


def scale(c, k):
    return (c[0] * k, c[1] * k, c[2] * k)


def ramp(stops, x):
    """stops: [(pos, (r,g,b))], x: array in 0..1 -> array (..., 3)."""
    ps = np.array([p for p, _ in stops], np.float32)
    cs = np.array([c for _, c in stops], np.float32)
    return np.stack([np.interp(x, ps, cs[:, i]) for i in range(3)], axis=-1)


def blur(a, passes=1):
    """Cheap separable 3-tap box blur, wrapping at the edges."""
    for _ in range(passes):
        a = (a + np.roll(a, 1, 0) + np.roll(a, -1, 0)) * (1.0 / 3.0)
        a = (a + np.roll(a, 1, 1) + np.roll(a, -1, 1)) * (1.0 / 3.0)
    return a


def splat(px, xs, ys, rgb, gain=1.0):
    """Add points at float coordinates with bilinear spread.

    np.bincount over flat indices instead of np.add.at: the same result and
    about 20x faster for the few thousand particles the scenes push each frame.
    """
    h, w, _ = px.shape
    xs = np.asarray(xs, np.float32)
    ys = np.asarray(ys, np.float32)
    if xs.size == 0:
        return
    rgb = np.asarray(rgb, np.float32)
    if rgb.ndim == 1:
        rgb = np.broadcast_to(rgb, (xs.size, 3))
    x0 = np.floor(xs).astype(np.int32)
    y0 = np.floor(ys).astype(np.int32)
    fx = xs - x0
    fy = ys - y0
    flat = px.reshape(-1, 3)
    n = h * w
    for dx in (0, 1):
        for dy in (0, 1):
            xi = x0 + dx
            yi = y0 + dy
            wgt = (fx if dx else 1.0 - fx) * (fy if dy else 1.0 - fy) * gain
            m = (xi >= 0) & (xi < w) & (yi >= 0) & (yi < h) & (wgt > 1e-4)
            if not m.any():
                continue
            idx = yi[m] * w + xi[m]
            ww = wgt[m]
            if idx.size * 8 < n:
                # bincount allocates a whole canvas per channel, so for the small
                # splats (arcs, grid lines, single dots) scatter-add is cheaper
                np.add.at(flat, idx, rgb[m] * ww[:, None])
            else:
                for c in range(3):
                    flat[:, c] += np.bincount(idx, weights=ww * rgb[m, c], minlength=n)


def splat_line(px, x0, y0, x1, y1, rgb, gain=1.0, step=0.7):
    """Draw a segment by splatting points along it."""
    n = max(2, int(math.hypot(x1 - x0, y1 - y0) / step))
    t = np.linspace(0.0, 1.0, n, dtype=np.float32)
    splat(px, x0 + (x1 - x0) * t, y0 + (y1 - y0) * t, rgb, gain)


def tonemap(px):
    """Linear light -> 8-bit sRGB-ish, with the highlights rolled off."""
    c = 1.0 - np.exp(-np.maximum(px, 0.0) * 1.15)
    c **= (1.0 / 2.2)
    return (c * 255.0 + 0.5).astype(np.uint8)


# ---------------------------------------------------------------- canvas
class Canvas:
    """A pixel buffer (cols x 2*rows) plus a sparse layer of real characters."""

    def __init__(self, cols, rows):
        self.cols = cols
        self.rows = rows
        self.w = cols
        self.h = rows * 2
        self.px = np.zeros((self.h, self.w, 3), np.float32)
        self.chars = {}

    def clear(self, color=BG):
        self.px[:] = color
        self.chars.clear()

    def text(self, row, col, s, color=WHITE):
        """Put a string on the character layer. Cells outside are dropped."""
        if not (0 <= row < self.rows):
            return
        for i, ch in enumerate(s):
            c = col + i
            if 0 <= c < self.cols:
                self.chars[(row, c)] = (ch, color)

    def text_center(self, row, s, color=WHITE, x=None):
        col = (self.cols - len(s)) // 2 if x is None else int(x - len(s) / 2)
        self.text(row, col, s, color)


# ---------------------------------------------------------------- renderer
class Screen:
    """Turns a Canvas into escape sequences, writing only what changed."""

    def __init__(self, cols, rows):
        self.cols = cols
        self.rows = rows
        self.prev_fg = None
        self.prev_bg = None
        self.prev_ch = None
        self.chtab = [HALF, " "]
        self.chidx = {HALF: 0, " ": 1}
        self.prev_top = None
        self.prev_bot = None
        self._fgc = {}
        self._bgc = {}
        self.bytes_out = 0

    def _fg(self, p):
        s = self._fgc.get(p)
        if s is None:
            if len(self._fgc) > 120000:
                self._fgc.clear()
            s = "\x1b[38;2;%d;%d;%dm" % (p >> 16, (p >> 8) & 255, p & 255)
            self._fgc[p] = s
        return s

    def _bg(self, p):
        s = self._bgc.get(p)
        if s is None:
            if len(self._bgc) > 120000:
                self._bgc.clear()
            s = "\x1b[48;2;%d;%d;%dm" % (p >> 16, (p >> 8) & 255, p & 255)
            self._bgc[p] = s
        return s

    def frame(self, canvas):
        px = tonemap(canvas.px).astype(np.int32)
        top = px[0::2][: self.rows]
        bot = px[1::2][: self.rows]
        flat = (np.abs(top - bot).max(axis=2) <= 2)   # both halves equal: a space costs one colour
        fgp = (top[:, :, 0] << 16) | (top[:, :, 1] << 8) | top[:, :, 2]
        bgp = (bot[:, :, 0] << 16) | (bot[:, :, 1] << 8) | bot[:, :, 2]
        chp = np.where(flat, 1, 0).astype(np.int32)
        top = np.where(flat[:, :, None], bot, top)

        # Character cells: the glyph takes the whole cell, so the background is
        # the two pixels averaged and dimmed, which keeps the graphics visible
        # behind the text without fighting it.
        for (r, c), (ch, color) in canvas.chars.items():
            if not (0 <= r < self.rows and 0 <= c < self.cols):
                continue
            i = self.chidx.get(ch)
            if i is None:
                i = len(self.chtab)
                self.chtab.append(ch)
                self.chidx[ch] = i
            chp[r, c] = i
            t = top[r, c]
            b = bot[r, c]
            m = ((int(t[0]) + int(b[0])) // 5, (int(t[1]) + int(b[1])) // 5, (int(t[2]) + int(b[2])) // 5)
            bgp[r, c] = (m[0] << 16) | (m[1] << 8) | m[2]
            fgp[r, c] = (int(color[0] * 255) << 16) | (int(color[1] * 255) << 8) | int(color[2] * 255)

        out = []
        if self.prev_fg is None or self.prev_fg.shape != fgp.shape:
            out.append("\x1b[2J")
            changed = np.ones((self.rows, self.cols), bool)
            self.prev_fg = np.full_like(fgp, -1)
            self.prev_bg = np.full_like(bgp, -1)
            self.prev_ch = np.full_like(chp, -1)
        else:
            # Deadband: a cell is rewritten only when a channel moved by more
            # than 3/255, and what is remembered is what was actually written,
            # so a slow drift still reaches the screen.
            changed = ((np.abs((fgp >> 16) - (self.prev_fg >> 16)) > 3)
                       | (np.abs(((fgp >> 8) & 255) - ((self.prev_fg >> 8) & 255)) > 3)
                       | (np.abs((fgp & 255) - (self.prev_fg & 255)) > 3)
                       | (np.abs((bgp >> 16) - (self.prev_bg >> 16)) > 3)
                       | (np.abs(((bgp >> 8) & 255) - ((self.prev_bg >> 8) & 255)) > 3)
                       | (np.abs((bgp & 255) - (self.prev_bg & 255)) > 3)
                       | (chp != self.prev_ch))
        rows_hit = np.flatnonzero(changed.any(axis=1))
        cur_f = cur_b = -1
        for r in rows_hit.tolist():
            cc = np.flatnonzero(changed[r])
            # Merge runs separated by a gap of 3 cells or less: re-emitting a
            # few unchanged cells is cheaper than another cursor jump.
            splits = np.flatnonzero(np.diff(cc) > 3) + 1
            fgr = fgp[r].tolist()
            bgr = bgp[r].tolist()
            chr_ = chp[r].tolist()
            for run in np.split(cc, splits):
                c0 = int(run[0])
                c1 = int(run[-1])
                out.append("\x1b[%d;%dH" % (r + 1, c0 + 1))
                for c in range(c0, c1 + 1):
                    i = chr_[c]
                    if i != 1:
                        f = fgr[c]
                        if f != cur_f:
                            out.append(self._fg(f))
                            cur_f = f
                    b = bgr[c]
                    if b != cur_b:
                        out.append(self._bg(b))
                        cur_b = b
                    out.append(self.chtab[i])
        self.prev_fg = np.where(changed, fgp, self.prev_fg)
        self.prev_bg = np.where(changed, bgp, self.prev_bg)
        self.prev_ch = np.where(changed, chp, self.prev_ch)
        s = "".join(out)
        self.bytes_out = len(s)
        return s


class Term:
    """Alt screen, no cursor, no wrap, raw keys - and put it all back."""

    def __init__(self):
        self.fd = sys.stdin.fileno()
        self.saved = None

    def __enter__(self):
        try:
            self.saved = termios.tcgetattr(self.fd)
            tty.setcbreak(self.fd)
        except termios.error:
            self.saved = None
        sys.stdout.write("\x1b[?1049h\x1b[?25l\x1b[?7l\x1b[2J")
        sys.stdout.flush()
        return self

    def __exit__(self, *exc):
        sys.stdout.write("\x1b[0m\x1b[?7h\x1b[?25h\x1b[?1049l")
        sys.stdout.flush()
        if self.saved is not None:
            termios.tcsetattr(self.fd, termios.TCSADRAIN, self.saved)

    def keys(self):
        out = []
        while select.select([self.fd], [], [], 0)[0]:
            ch = os.read(self.fd, 64)
            if not ch:
                break
            out.append(ch.decode("utf-8", "ignore"))
        return "".join(out)


# ---------------------------------------------------------------- text mask
_FONT_CACHE = {}


def _font(size, bold=True):
    """A real font rasterised by pygame (already a dependency of the game)."""
    key = (size, bold)
    f = _FONT_CACHE.get(key)
    if f is None:
        os.environ.setdefault("PYGAME_HIDE_SUPPORT_PROMPT", "1")
        os.environ.setdefault("SDL_VIDEODRIVER", "dummy")
        import pygame
        if not pygame.font.get_init():
            pygame.font.init()
        path = pygame.font.match_font(
            "avenirnextcondensed,helveticaneue,helvetica,arial,dejavusans", bold=bold)
        f = pygame.font.Font(path, size) if path else pygame.font.Font(None, size)
        f.set_bold(bold and not path)
        _FONT_CACHE[key] = f
    return f


def text_mask(s, w, h, height_px, bold=True, tracking=0, cy=None, fit=0.92):
    """Rasterise `s` into a (h, w) float mask, centred on `cy`, scaled to fit `w`."""
    os.environ.setdefault("PYGAME_HIDE_SUPPORT_PROMPT", "1")
    os.environ.setdefault("SDL_VIDEODRIVER", "dummy")
    import pygame
    if not s:
        return np.zeros((h, w), np.float32)
    size = max(6, int(height_px))
    for _ in range(40):
        font = _font(size, bold)
        surf = font.render(s, True, (255, 255, 255))
        tw = surf.get_width() + tracking * (len(s) - 1)
        if tw <= w * fit or size <= 7:
            break
        size = int(size * 0.92)
    arr = pygame.surfarray.array_alpha(surf).T.astype(np.float32) / 255.0
    if tracking:
        # Letter-spacing by re-blitting glyph by glyph; the condensed face plus
        # wide tracking is what makes the card read as a broadcast title.
        parts = []
        for ch in s:
            g = font.render(ch, True, (255, 255, 255))
            parts.append(pygame.surfarray.array_alpha(g).T.astype(np.float32) / 255.0)
        gh = max(p.shape[0] for p in parts)
        gw = sum(p.shape[1] for p in parts) + tracking * (len(parts) - 1)
        arr = np.zeros((gh, gw), np.float32)
        x = 0
        for p in parts:
            arr[: p.shape[0], x : x + p.shape[1]] = np.maximum(
                arr[: p.shape[0], x : x + p.shape[1]], p)
            x += p.shape[1] + tracking
    ah, aw = arr.shape
    if aw > w:
        arr = arr[:, :w]
        aw = w
    if ah > h:
        arr = arr[:h]
        ah = h
    out = np.zeros((h, w), np.float32)
    y0 = (h - ah) // 2 if cy is None else int(cy - ah / 2)
    y0 = max(0, min(h - ah, y0))
    x0 = (w - aw) // 2
    out[y0 : y0 + ah, x0 : x0 + aw] = arr
    return out


# ---------------------------------------------------------------- language
# Everything a viewer reads, once per language. The screen is the same in both:
# `cortina` is the Spanish one, `curtain` the English one (--lang en). Scene
# `name`s are identifiers (--scene, the snapshot file names) and stay as they
# are; only what is drawn is translated. Add a language by adding a dict here.
STRINGS = {
    "es": {
        "scene": {
            "latent":        ("LATENT SPACE", "12 288 d  ·  proyeccion 2d en vivo"),
            "attention":     ("SELF-ATTENTION", "softmax(QKᵀ/√d) · capa 42"),
            "emergence":     ("EMERGENCIA", "reaccion-difusion · sin supervision"),
            "red":           ("PASADA HACIA ADELANTE", "6 capas · 1.2 B parametros"),
            "entrenamiento": ("ENTRENAMIENTO", "checkpoint vivo · lr coseno"),
            "tokens":        ("GENERANDO", "temp 0.83 · top-p 0.95"),
        },
        "clusters": ["groove", "fill", "ghost", "tempo", "silencio"],
        "vocab": ["el", "pulso", "no", "es", "el", "metronomo", ",", "es", "lo",
                  "que", "el", "cuerpo", "ya", "sabe", "antes", "de"],
        "mass": "masa  %.3f",
        "layers": ["entrada", "attn", "mlp", "attn", "mlp", "logits"],
        "backward": "backward  ←  ∇ propagando",
        "forward": "forward   →  activaciones",
        "step": "paso      %s",
        "weights": "pesos · bloque 12",
        "unlock": "✦  capacidad emergente: ",
        "unlocks": [
            "paradiddle a 180 bpm",
            "independencia del pie izquierdo",
            "swing sin pensarlo",
            "ghost notes por debajo de 30 de velocity",
            "contar el 1 despues de un fill de 7",
            "no acelerar en el estribillo",
            "hi-hat abierto justo antes del bombo",
            "cumbia villera en los cuerpos",
            "escuchar al bajo",
            "tocar mas bajo cuando entra la voz",
            "el silencio como golpe",
        ],
        "thoughts": [
            "el groove es un problema de prediccion: el cuerpo ya sabe el proximo golpe",
            "hipotesis: el ghost note es el dropout del baterista",
            "si escucho diez mil horas de funk, aprendo el pocket o solo la media",
            "el metronomo mide el tiempo; el bombo lo decide",
            "cada fill es una rama del arbol que se poda al caer en el uno",
            "atencion completa sobre el hi-hat: todo lo demas es contexto",
            "el error mas caro no es fallar el golpe, es dudar despues",
            "aprendi a tocar fuerte antes que a tocar bajo, ese fue el sesgo",
            "el silencio tiene la misma probabilidad que cualquier otro token",
            "quien vuelva a la camara va a tener que justificar este tempo",
        ],
        "cands": ["golpe", "tiempo", "pulso", "silencio", "bombo", "redoblante", "aire",
                  "uno", "cuerpo", "ritmo", "error", "swing", "nota", "manos"],
        "card_title": "YA VUELVO",
        "live": "EN VIVO",
        "afk": "AFK  %d:%02d",
        "stay": "el stream sigue en vivo, no cierres nada",
        "excuses": [
            "fui a mear, vuelvo en un toque",
            "fui a buscar los lentes",
            "fui por agua, ya vuelvo",
            "se me cayo una baqueta atras del tom",
            "estoy afinando el bombo",
            "discutiendo con el hi-hat",
            "el AGI quedo practicando solo",
            "pausa tecnica, no toquen nada",
        ],
        "marquee": [
            "el stream no se corta",
            "deja tu tempo favorito en el chat",
            "seguimos en un minuto",
            "mientras tanto el modelo sigue entrenando",
            "pedidos de temas por chat",
        ],
        "keys": "q salir   espacio escena   p pausa   m musica   -/+ volumen   b tarjeta   h hud",
        "no_audio": "sin audio",
        "no_music": "sin musica",
    },
    "en": {
        "scene": {
            "latent":        ("LATENT SPACE", "12 288 d  ·  live 2d projection"),
            "attention":     ("SELF-ATTENTION", "softmax(QKᵀ/√d) · layer 42"),
            "emergence":     ("EMERGENCE", "reaction-diffusion · unsupervised"),
            "red":           ("FORWARD PASS", "6 layers · 1.2 B parameters"),
            "entrenamiento": ("TRAINING", "live checkpoint · cosine lr"),
            "tokens":        ("GENERATING", "temp 0.83 · top-p 0.95"),
        },
        "clusters": ["groove", "fill", "ghost", "tempo", "silence"],
        "vocab": ["the", "pulse", "is", "not", "the", "metronome", ",", "it", "is",
                  "what", "the", "body", "already", "knows", "before", "the"],
        "mass": "mass  %.3f",
        "layers": ["input", "attn", "mlp", "attn", "mlp", "logits"],
        "backward": "backward  ←  ∇ propagating",
        "forward": "forward   →  activations",
        "step": "step      %s",
        "weights": "weights · block 12",
        "unlock": "✦  emergent capability: ",
        "unlocks": [
            "paradiddle at 180 bpm",
            "left foot independence",
            "swing without thinking about it",
            "ghost notes under 30 velocity",
            "finding the 1 after a fill in 7",
            "not rushing the chorus",
            "open hi-hat right before the kick",
            "cumbia villera in the body",
            "listening to the bass",
            "playing quieter when the vocal comes in",
            "silence as a stroke",
        ],
        "thoughts": [
            "the groove is a prediction problem: the body already knows the next stroke",
            "hypothesis: the ghost note is the drummer's dropout",
            "ten thousand hours of funk: do I learn the pocket or only the mean",
            "the metronome measures the time; the kick decides it",
            "every fill is a branch of the tree, pruned when it lands on the one",
            "full attention on the hi-hat: everything else is context",
            "the costliest error is not missing the stroke, it is hesitating after",
            "I learned to play loud before I learned to play soft, that was the bias",
            "silence has the same probability as any other token",
            "whoever comes back to the camera will have to justify this tempo",
        ],
        "cands": ["stroke", "time", "pulse", "silence", "kick", "snare", "air",
                  "one", "body", "groove", "error", "swing", "note", "hands"],
        "card_title": "BE RIGHT BACK",
        "live": "LIVE",
        "afk": "AFK  %d:%02d",
        "stay": "the stream is still live, do not close anything",
        "excuses": [
            "gone for a piss, back in a second",
            "gone to find my glasses",
            "gone for water, back in a minute",
            "dropped a stick behind the tom",
            "tuning the kick drum",
            "arguing with the hi-hat",
            "left the AGI practising on its own",
            "technical pause, do not touch anything",
        ],
        "marquee": [
            "the stream is not going anywhere",
            "leave your favourite tempo in the chat",
            "back in a minute",
            "meanwhile the model keeps training",
            "song requests in the chat",
        ],
        "keys": "q quit   space scene   p pause   m music   -/+ volume   b card   h hud",
        "no_audio": "no audio",
        "no_music": "no music",
    },
}

T = STRINGS["es"]


def set_lang(lang):
    """Pick the language every string is read from; called once, from main()."""
    global T
    T = STRINGS[lang]


# ---------------------------------------------------------------- scenes
class Scene:
    name = "scene"
    low = 0.0          # kick and bass, 0..1.4, set by App from the music every frame
    high = 0.0         # hats and arpeggio

    @property
    def title(self):
        return T["scene"].get(self.name, ("", ""))[0]

    @property
    def sub(self):
        return T["scene"].get(self.name, ("", ""))[1]

    def enter(self, w, h, cols, rows):
        self.w, self.h, self.cols, self.rows = w, h, cols, rows

    def update(self, t, dt):
        pass

    def draw(self, cv):
        pass


class Latent(Scene):
    """Particles in a divergence-free flow field, breathing in and out of clusters."""

    name = "latent"

    def enter(self, w, h, cols, rows):
        Scene.enter(self, w, h, cols, rows)
        n = int(np.clip(w * h * 0.11, 900, 3200))
        self.pos = np.empty((n, 2), np.float32)
        self.pos[:, 0] = np.random.uniform(0, w, n)
        self.pos[:, 1] = np.random.uniform(0, h, n)
        self.cl = np.random.randint(0, len(ACCENTS), n)
        self.col = np.array(ACCENTS, np.float32)[self.cl]
        self.trail = np.zeros((h, w, 3), np.float32)
        # Four sine components make the stream function; its curl is the field,
        # so the flow never piles particles up in a sink.
        self.waves = [(np.random.uniform(0.7, 1.3),
                       np.random.uniform(-0.10, 0.10),
                       np.random.uniform(-0.10, 0.10),
                       np.random.uniform(-0.5, 0.5)) for _ in range(4)]
        # Each cluster keeps its own slow orbit. Pulling towards the centroid of
        # the current members instead collapsed the five of them into one blob.
        self.orbit = [(np.random.uniform(0, 6.28), np.random.uniform(0, 6.28),
                       np.random.uniform(0.05, 0.11), np.random.uniform(0.04, 0.09))
                      for _ in range(len(ACCENTS))]
        self.next_shuffle = 6.0

    def update(self, t, dt):
        x = self.pos[:, 0]
        y = self.pos[:, 1]
        vx = np.zeros_like(x)
        vy = np.zeros_like(y)
        for a, kx, ky, om in self.waves:
            c = np.cos(kx * x + ky * y + om * t) * a
            vx += ky * c
            vy -= kx * c
        vx *= 62.0
        vy *= 62.0
        # Clusters pull themselves together, then let go: cos over ~19 s.
        pull = 0.14 + 0.72 * (0.5 - 0.5 * math.cos(t * 0.33)) ** 2
        ax = np.array([self.w * 0.5 + self.w * 0.33 * math.cos(a + t * wa)
                       for a, b, wa, wb in self.orbit], np.float32)
        ay = np.array([self.h * 0.5 + self.h * 0.30 * math.sin(b + t * wb)
                       for a, b, wa, wb in self.orbit], np.float32)
        self.anchors = (ax, ay)
        if pull > 0.02:
            vx += (ax[self.cl] - x) * pull
            vy += (ay[self.cl] - y) * pull
        jit = 0.8 * math.sqrt(dt)
        self.pos[:, 0] = (x + vx * dt + np.random.normal(0, jit, len(x))) % self.w
        self.pos[:, 1] = (y + vy * dt + np.random.normal(0, jit, len(y))) % self.h
        if t > self.next_shuffle:
            # a few points get re-embedded into the nearest cluster
            self.next_shuffle = t + np.random.uniform(4, 8)
            m = np.random.random(len(self.cl)) < 0.06
            if m.any():
                d = (x[m, None] - ax[None, :]) ** 2 + (y[m, None] - ay[None, :]) ** 2
                self.cl[m] = np.argmin(d, axis=1).astype(self.cl.dtype)
        self.col += (np.array(ACCENTS, np.float32)[self.cl] - self.col) * min(1.0, dt * 3)

    def draw(self, cv):
        self.trail *= 0.935
        splat(self.trail, self.pos[:, 0], self.pos[:, 1], self.col, 0.040 * (1 + 0.9 * self.high))
        cv.px += self.trail
        ax, ay = getattr(self, "anchors", (np.zeros(1), np.zeros(1)))
        for i in range(len(ACCENTS)):
            splat(cv.px, [ax[i]], [ay[i]], scale(ACCENTS[i], 0.8), 1.2)
        cnt = np.bincount(self.cl, minlength=len(ACCENTS))
        for i, name in enumerate(T["clusters"]):
            row = 3 + i
            n = int(cnt[i])
            bar = "█" * max(1, int(10 * n / max(1, len(self.cl)) * len(ACCENTS)))
            cv.text(row, 3, "%-9s" % name, scale(SLATE, 1.6))
            cv.text(row, 13, bar[:12], ACCENTS[i])


class Attention(Scene):
    """One head of causal self-attention: the query sweeps, the arcs light up."""

    name = "attention"

    def enter(self, w, h, cols, rows):
        Scene.enter(self, w, h, cols, rows)
        self.n = len(T["vocab"])
        self.head = 0
        self.new_head()
        self.q = 0.0
        self.base = h * 0.80

    def new_head(self):
        n = self.n
        d = np.abs(np.subtract.outer(np.arange(n), np.arange(n))).astype(np.float32)
        sc = -d * np.random.uniform(0.15, 0.6)
        for _ in range(3):   # a couple of strong long-range links per head
            i, j = np.random.randint(0, n, 2)
            sc[i, j] += np.random.uniform(1.5, 3.5)
        sc += np.random.normal(0, 0.4, (n, n))
        sc[np.triu_indices(n, 1)] = -1e9        # causal
        e = np.exp(sc - sc.max(axis=1, keepdims=True))
        self.att = e / e.sum(axis=1, keepdims=True)
        self.head = np.random.randint(1, 16)

    def update(self, t, dt):
        self.q += dt * 2.2
        if self.q >= self.n:
            self.q = 0.0
            self.new_head()

    def draw(self, cv):
        w, h = self.w, self.h
        n = self.n
        span = w * 0.74
        x0 = w * 0.13
        xs = x0 + (np.arange(n) + 0.5) * span / n
        qi = int(self.q)
        frac = self.q - qi
        # the token rail
        for i in range(n):
            splat_line(cv.px, xs[i] - span / n * 0.36, self.base,
                       xs[i] + span / n * 0.36, self.base, scale(SLATE, 0.5), 0.5)
        wgt = self.att[qi]
        for j in range(qi + 1):
            a = float(wgt[j])
            if a < 0.012:
                continue
            xq, xj = float(xs[qi]), float(xs[j])
            apex = self.base - min(h * 0.62, 8 + abs(xq - xj) * 0.85)
            t = np.linspace(0, 1, 48, dtype=np.float32)
            mt = 1 - t
            bx = mt * mt * xj + 2 * mt * t * ((xq + xj) / 2) + t * t * xq
            by = mt * mt * self.base + 2 * mt * t * apex + t * t * self.base
            col = mix(VIOLET, CYAN, min(1.0, a * 3.2))
            splat(cv.px, bx, by, col, 0.9 * (0.25 + a * 2.4) * (1 + 0.5 * self.high))
            splat(cv.px, [xj], [self.base], mix(col, WHITE, 0.4), 2.5 * (0.3 + a))
        # the query head, sliding between tokens
        xq = float(np.interp(self.q, np.arange(n), xs))
        for dy in (-1, 0, 1):
            splat_line(cv.px, xq, self.base - 3 + dy, xq, self.base + 3 + dy,
                       AMBER, 0.35 if dy else 0.9)
        # token strip on the character layer
        row = int(self.base / 2) + 2
        for i, tok in enumerate(T["vocab"]):
            col = int(xs[i] - len(tok) / 2)
            c = WHITE if i == qi else (AMBER if i == qi + 1 else scale(SLATE, 1.5 if i < qi else 0.9))
            cv.text(row, col, tok, c)
        cv.text(row + 2, int(x0), "head %02d" % self.head, scale(SLATE, 1.4))
        top = np.argsort(-wgt)[:3]
        s = "  ".join("%s %.2f" % (T["vocab"][j], wgt[j]) for j in top if wgt[j] > 0.01)
        cv.text(row + 2, int(x0) + 10, s, mix(CYAN, WHITE, 0.3))
        # the whole matrix, small, on the left
        mw = max(1, int(w * 0.09) // n * n)
        cell = max(1, mw // n)
        img = np.repeat(np.repeat(self.att, cell, 0), cell, 1)
        img = ramp([(0.0, (0.02, 0.02, 0.05)), (0.25, VIOLET), (0.7, CYAN), (1.0, WHITE)],
                   np.clip(img * 3.0, 0, 1)) * 1.1
        y0 = int(h * 0.12)
        x0i = int(w * 0.03)
        cv.px[y0 : y0 + img.shape[0], x0i : x0i + img.shape[1]] += img.astype(np.float32)


class Diffusion(Scene):
    """Gray-Scott reaction-diffusion: structure out of two numbers."""

    name = "emergence"

    def enter(self, w, h, cols, rows):
        Scene.enter(self, w, h, cols, rows)
        self.U = np.ones((h, w), np.float32)
        self.V = np.zeros((h, w), np.float32)
        for _ in range(9):
            self.drop()
        self.F = 0.030
        self.k = 0.060

    def drop(self, r=None):
        h, w = self.h, self.w
        cy = np.random.randint(0, h)
        cx = np.random.randint(0, w)
        r = r or np.random.randint(3, 7)
        y0, y1 = max(0, cy - r), min(h, cy + r)
        x0, x1 = max(0, cx - r), min(w, cx + r)
        self.V[y0:y1, x0:x1] = 0.9
        self.U[y0:y1, x0:x1] = 0.2

    def update(self, t, dt):
        self.F = 0.030 + 0.0085 * math.sin(t * 0.061)
        self.k = 0.0595 + 0.0035 * math.cos(t * 0.043)
        U, V = self.U, self.V
        for _ in range(7):
            lu = (np.roll(U, 1, 0) + np.roll(U, -1, 0) + np.roll(U, 1, 1) + np.roll(U, -1, 1)) - 4 * U
            lv = (np.roll(V, 1, 0) + np.roll(V, -1, 0) + np.roll(V, 1, 1) + np.roll(V, -1, 1)) - 4 * V
            uvv = U * V * V
            U += 0.16 * lu - uvv + self.F * (1 - U)
            V += 0.08 * lv + uvv - (self.F + self.k) * V
        np.clip(U, 0, 1, out=U)
        np.clip(V, 0, 1, out=V)
        if np.random.random() < dt * 0.25 or self.low > 1.0:
            self.drop(3 if self.low > 1.0 else None)      # a new cell on every kick

    def draw(self, cv):
        v = np.clip(self.V * 2.15, 0, 1)
        img = ramp([(0.00, (0.0, 0.0, 0.0)),
                    (0.18, scale(VIOLET, 0.10)),
                    (0.45, scale(CYAN, 0.35)),
                    (0.75, scale(MAGENTA, 0.55)),
                    (1.00, mix(WHITE, CYAN, 0.35))], v)
        cv.px += img.astype(np.float32) * 0.46
        cv.text(3, 3, "F %.4f   k %.4f" % (self.F, self.k), scale(SLATE, 1.6))
        cv.text(4, 3, T["mass"] % float(self.V.mean()), mix(CYAN, WHITE, 0.2))


class Network(Scene):
    """A stack of layers with the forward pass, then the gradient coming back."""

    name = "red"
    SIZES = [6, 11, 15, 11, 8, 4]

    def enter(self, w, h, cols, rows):
        Scene.enter(self, w, h, cols, rows)
        L = len(self.SIZES)
        self.nx, self.ny = [], []
        for li, n in enumerate(self.SIZES):
            x = w * 0.13 + (w * 0.74) * li / (L - 1)
            gap = min(h * 0.52 / max(self.SIZES), 5.0)
            ys = (np.arange(n) - (n - 1) / 2) * gap + h * 0.50
            self.nx.append((np.full(n, x) + np.random.uniform(-1.5, 1.5, n)).astype(np.float32))
            self.ny.append((ys + np.random.uniform(-1.2, 1.2, n)).astype(np.float32))
        ex0, ey0, ex1, ey1, el = [], [], [], [], []
        for li in range(L - 1):
            for i in range(self.SIZES[li]):
                for j in np.random.choice(self.SIZES[li + 1],
                                          min(self.SIZES[li + 1], 5), replace=False):
                    ex0.append(self.nx[li][i]); ey0.append(self.ny[li][i])
                    ex1.append(self.nx[li + 1][j]); ey1.append(self.ny[li + 1][j])
                    el.append(li + 0.5)
        self.ex0 = np.array(ex0, np.float32); self.ey0 = np.array(ey0, np.float32)
        self.ex1 = np.array(ex1, np.float32); self.ey1 = np.array(ey1, np.float32)
        self.el = np.array(el, np.float32)
        ts = np.linspace(0, 1, 22, dtype=np.float32)[None, :]
        self.lx = self.ex0[:, None] + (self.ex1 - self.ex0)[:, None] * ts
        self.ly = self.ey0[:, None] + (self.ey1 - self.ey0)[:, None] * ts
        self.act = [np.random.random(n).astype(np.float32) * 0.3 for n in self.SIZES]
        nx = np.concatenate(self.nx)
        ny = np.concatenate(self.ny)
        offs = [(0, 0, 1.0), (1, 0, .45), (-1, 0, .45), (0, 1, .45), (0, -1, .45),
                (1, 1, .2), (-1, -1, .2), (1, -1, .2), (-1, 1, .2)]
        self.gx = np.concatenate([nx + dx for dx, dy, g in offs]).astype(np.float32)
        self.gy = np.concatenate([ny + dy for dx, dy, g in offs]).astype(np.float32)
        self.gg = np.concatenate([np.full(len(nx), g, np.float32) for dx, dy, g in offs])
        self.gi = np.tile(np.arange(len(nx)), len(offs))
        self.p = -0.6
        self.back = False
        self.loss = 3.2

    def update(self, t, dt):
        L = len(self.SIZES)
        self.p += dt * 1.9 * (1 + 0.7 * self.low) * (-1 if self.back else 1)
        if not self.back and self.p > L - 0.2:
            self.back = True
            self.loss = max(0.32, self.loss * np.random.uniform(0.90, 0.995))
        elif self.back and self.p < -0.6:
            self.back = False
            self.act = [np.random.random(n).astype(np.float32) for n in self.SIZES]
        for li in range(L):
            near = math.exp(-((li - self.p) ** 2) / 0.5)
            if near > 0.2:
                self.act[li] = np.clip(self.act[li] * (1 - dt * 2) +
                                       np.random.random(len(self.act[li])) * near * dt * 6, 0, 1)

    def draw(self, cv):
        col = MAGENTA if self.back else CYAN
        b = np.exp(-((self.el - self.p) ** 2) / 0.30) + 0.18
        live = b > 0.02
        if live.any():
            n_s = self.lx.shape[1]
            rgb = np.repeat((np.array(col, np.float32)[None, :] * b[live, None]), n_s, axis=0)
            splat(cv.px, self.lx[live].ravel(), self.ly[live].ravel(), rgb, 0.075)
            # the signal itself: a bright dot riding each live edge
            u = np.clip(self.p - self.el[live] + 0.5, 0, 1)
            if self.back:
                u = 1 - u
            splat(cv.px, self.ex0[live] + (self.ex1 - self.ex0)[live] * u,
                  self.ey0[live] + (self.ey1 - self.ey0)[live] * u,
                  np.array(mix(col, WHITE, 0.55), np.float32)[None, :] * b[live, None], 1.1)
        a = np.concatenate(self.act)[self.gi]
        c = (np.array(mix(col, WHITE, 0.3), np.float32)[None, :]
             * ((0.25 + a) * self.gg)[:, None])
        splat(cv.px, self.gx, self.gy, c, 1.0)
        gap = min(self.h * 0.52 / max(self.SIZES), 5.0)
        row = int((self.h * 0.50 + gap * (max(self.SIZES) - 1) / 2) / 2) + 2
        for li, n in enumerate(self.SIZES):
            name = T["layers"][li]
            cv.text(row, int(self.nx[li][0] - len(name) / 2), name, scale(SLATE, 1.5))
        cv.text(3, 3, T["backward"] if self.back else T["forward"],
                mix(col, WHITE, 0.35))
        cv.text(4, 3, "loss  %.4f" % self.loss, scale(SLATE, 1.7))


class Telemetry(Scene):
    """The training run: loss going down, weights cooking, capabilities popping."""

    name = "entrenamiento"

    def enter(self, w, h, cols, rows):
        Scene.enter(self, w, h, cols, rows)
        x = np.arange(w, dtype=np.float32)
        self.hist = 2.35 + 2.4 * np.exp(-x / (w * 0.55)) + np.random.normal(0, 0.035, w).astype(np.float32)
        for _ in range(4):                      # the spikes a real run has
            i = np.random.randint(0, w - 12)
            self.hist[i : i + 10] += np.linspace(np.random.uniform(0.2, 0.6), 0, 10)
        self.loss = float(self.hist[-1])
        self.lo = self.hi = None
        self.step = np.random.randint(840, 1250) * 1000
        self.heat = np.random.random((14, 34)).astype(np.float32)
        self.ticker = []
        self.next_unlock = 3.0
        self.acc = 0.61

    def update(self, t, dt):
        self.loss = max(0.24, self.loss - dt * 0.02 * self.loss + np.random.normal(0, 0.012))
        if np.random.random() < dt * 0.12:
            self.loss += np.random.uniform(0.05, 0.35)     # a spike, like a real run
        self.hist[:-1] = self.hist[1:]
        self.hist[-1] = self.loss
        self.step += int(dt * 620)
        self.acc = min(0.999, self.acc + dt * 0.004)
        self.heat += (np.random.random(self.heat.shape).astype(np.float32) - 0.5) * dt * (2.2 + 9 * self.high)
        np.clip(self.heat, 0, 1, out=self.heat)
        if t > self.next_unlock:
            self.next_unlock = t + np.random.uniform(3.5, 7.0)
            self.ticker.append([np.random.choice(T["unlocks"]), t])
            self.ticker = self.ticker[-5:]

    def draw(self, cv):
        w, h = self.w, self.h
        y0, y1 = h * 0.22, h * 0.80
        v = np.log(np.clip(self.hist, 0.05, 20))
        tlo, thi = float(np.nanmin(v)), float(np.nanmax(v))
        pad = max(0.08, (thi - tlo) * 0.18)
        tlo, thi = tlo - pad, thi + pad
        if self.lo is None:
            self.lo, self.hi = tlo, thi
        else:                       # ease the window so the curve never jumps
            self.lo += (tlo - self.lo) * 0.06
            self.hi += (thi - self.hi) * 0.06
        ys = np.interp(v, [self.lo, self.hi], [y1, y0])
        ok = ~np.isnan(self.hist)
        xs = np.arange(w, dtype=np.float32)
        # area under the curve, then the curve, then its glow
        rowsi = np.arange(h, dtype=np.float32)[:, None]
        area = (rowsi > ys[None, :]) & ok[None, :] & (rowsi < y1 + 1)
        cv.px += area[:, :, None] * np.array(scale(VIOLET, 0.020), np.float32)
        splat(cv.px, xs[ok], ys[ok], CYAN, 0.85)
        splat(cv.px, xs[ok], ys[ok] - 1, scale(CYAN, 0.4), 0.5)
        splat(cv.px, xs[ok], ys[ok] + 1, scale(CYAN, 0.4), 0.5)
        splat(cv.px, [w - 1.0], [ys[-1]], WHITE, 3.0)
        for yy in (y0, (y0 + y1) / 2, y1):
            splat(cv.px, np.arange(0, w, 3, dtype=np.float32),
                  np.full(len(range(0, w, 3)), yy, np.float32), scale(SLATE, 0.35), 0.5)
        img = np.repeat(np.repeat(blur(self.heat, 1), 2, 0), 2, 1)
        img = (img - img.min()) / max(1e-6, float(img.max() - img.min()))
        img = ramp([(0.0, (0.01, 0.01, 0.03)), (0.5, scale(VIOLET, 0.5)),
                    (0.8, scale(MAGENTA, 0.8)), (1.0, AMBER)], img) * 0.20
        cv.px[3 : 3 + img.shape[0], w - img.shape[1] - 3 : w - 3] += img.astype(np.float32)
        cv.text(3, 3, "loss      %.4f" % self.loss, mix(CYAN, WHITE, 0.3))
        cv.text(4, 3, T["step"] % ("{:,}".format(self.step).replace(",", " ")), scale(SLATE, 1.7))
        cv.text(5, 3, "accuracy  %.1f %%" % (self.acc * 100), scale(SLATE, 1.7))
        cv.text(2, self.cols - 22, T["weights"], scale(SLATE, 1.4))
        base = self.rows - 4 - len(self.ticker)
        for i, (msg, _) in enumerate(self.ticker):
            fade = 0.35 + 0.65 * (i + 1) / len(self.ticker)
            cv.text(base + i, 3, T["unlock"] + msg, scale(mix(AMBER, WHITE, 0.2), fade))


class TokenStream(Scene):
    """A thought being generated, token by token, with the logits showing."""

    name = "tokens"

    def enter(self, w, h, cols, rows):
        Scene.enter(self, w, h, cols, rows)
        self.lines = []
        self.cur = []
        self.pending = list(np.random.choice(T["thoughts"], 1))[0].split(" ")
        self.i = 0
        self.acc = 0.0
        self.cands = self.roll()
        self.phase = 0.0

    def roll(self):
        p = np.random.random(5) ** 2 + 0.02
        p = np.sort(p / p.sum())[::-1]
        return list(zip(np.random.choice(T["cands"], 5, replace=False), p))

    def update(self, t, dt):
        self.phase = t
        self.acc += dt
        step = 0.16 if self.i < len(self.pending) else 1.4
        if self.acc > step:
            self.acc = 0.0
            if self.i < len(self.pending):
                self.cur.append(self.pending[self.i])
                self.i += 1
                self.cands = self.roll()
            else:
                self.lines.append(" ".join(self.cur))
                self.lines = self.lines[-6:]
                self.cur = []
                self.i = 0
                self.pending = T["thoughts"][np.random.randint(len(T["thoughts"]))].split(" ")

    def draw(self, cv):
        w, h = self.w, self.h
        # a faint standing wave behind the text, so the panel is not flat black
        yy = np.arange(h, dtype=np.float32)[:, None]
        xx = np.arange(w, dtype=np.float32)[None, :]
        f = (np.sin(xx * 0.07 + self.phase * 0.8) * np.sin(yy * 0.11 - self.phase * 0.5) + 1) * 0.5
        cv.px += (f ** 3)[:, :, None] * np.array(scale(VIOLET, 0.014), np.float32)
        left = 6
        width = self.cols - 26
        row = self.rows - 6 - len(self.lines)
        for ln in self.lines:
            cv.text(row, left, ln[:width], scale(SLATE, 1.5))
            row += 1
        s = " ".join(self.cur)
        cv.text(row, left, s[:width], WHITE)
        if self.low > 0.35 or int(self.phase * 2.4) % 2 == 0:
            cv.text(row, left + min(len(s), width) + 1, "█",
                    mix(AMBER, WHITE, min(1.0, self.low)))
        cv.text(3, self.cols - 24, "logits", scale(SLATE, 1.4))
        for i, (tok, p) in enumerate(self.cands):
            bar = "█" * int(p * 14 + 0.5)
            c = mix(CYAN, WHITE, 0.4) if i == 0 else scale(VIOLET, 1.0 - i * 0.12)
            cv.text(5 + i, self.cols - 24, "%-11s" % tok[:11], c)
            cv.text(5 + i, self.cols - 13, bar, c)


# ---------------------------------------------------------------- the card
class Card:
    """The be-right-back card: what a viewer needs to know at a glance."""

    def __init__(self, title=None, note=None, channel="twitch.tv/xantwav"):
        self.title = title or T["card_title"]
        self.note = note
        self.channel = channel
        self.mask = None
        self.glow = None
        self.tint = None

    def enter(self, w, h, cols, rows):
        self.w, self.h, self.cols, self.rows = w, h, cols, rows
        self.y0 = int(h * 0.25)
        self.y1 = int(h * 0.79)
        self.x0 = int(w * 0.09)
        self.x1 = int(w * 0.91)
        f = 7.0
        fy = np.clip(np.minimum(np.arange(h) - self.y0, self.y1 - np.arange(h)) / f, 0, 1)
        fx = np.clip(np.minimum(np.arange(w) - self.x0, self.x1 - np.arange(w)) / f, 0, 1)
        m = (fy[:, None] * fx[None, :]).astype(np.float32)
        m = m * m * (3 - 2 * m)
        self.panel = (1.0 - 0.82 * m)[:, :, None]
        cy = (self.y0 + self.y1) * 0.5 - h * 0.045
        self.mask = text_mask(self.title, w, h, max(9, int(h * 0.175)),
                              tracking=max(1, int(w * 0.006)), cy=cy, fit=0.62)
        self.glow = blur(self.mask.copy(), 3)
        # the title runs cyan -> violet across the screen, the glow is magenta
        gx = np.linspace(0, 1, w, dtype=np.float32)[None, :, None]
        c = np.array(CYAN, np.float32)[None, None, :] * (1 - gx) + np.array(VIOLET, np.float32)[None, None, :] * gx
        self.tint = (self.mask[:, :, None] * (c * 0.55 + np.array(WHITE, np.float32) * 0.55)).astype(np.float32)

    def draw(self, cv, t, elapsed, low=0.0, high=0.0):
        px = cv.px
        y0, y1, x0, x1 = self.y0, self.y1, self.x0, self.x1
        px *= self.panel                  # the panel: everything behind it, quieter
        px[y0:y1, x0:x1] += np.array(scale(VIOLET, 0.004), np.float32)
        rule = np.array(scale(SLATE, 0.5), np.float32)
        px[y0, x0:x1] += rule
        px[y1 - 1, x0:x1] += rule
        # viewfinder brackets, the one thing that says "broadcast"
        b = int((x1 - x0) * 0.035) + 2
        acc = np.array(scale(CYAN, 0.9), np.float32)
        for xa, xb in ((x0, x0 + b), (x1 - b, x1)):
            px[y0, xa:xb] += acc
            px[y1 - 1, xa:xb] += acc
        for ya, yb in ((y0, y0 + b // 2 + 1), (y1 - b // 2 - 1, y1)):
            px[ya:yb, x0] += acc
            px[ya:yb, x1 - 1] += acc
        pulse = (0.70 + 0.30 * math.sin(t * 2.2)) + 0.85 * low
        px += self.glow[:, :, None] * np.array(scale(MAGENTA, 0.30), np.float32) * pulse
        px += self.tint

        r0 = y0 // 2
        r1 = (y1 - 1) // 2
        lit = 0.45 + 0.55 * (0.5 + 0.5 * math.sin(t * 3.0)) + 0.5 * low
        cv.text(r0 + 2, x0 // 1 + 3, "●", scale(RED, lit + 0.4))
        cv.text(r0 + 2, x0 + 5, T["live"], mix(WHITE, RED, 0.25))
        cv.text(r0 + 2, x0 + 7 + len(T["live"]), "·  " + self.channel, scale(SLATE, 1.6))
        afk = T["afk"] % (int(elapsed) // 60, int(elapsed) % 60)
        cv.text(r0 + 2, x1 - 3 - len(afk), afk, mix(AMBER, WHITE, 0.2))

        mid = (self.mask.any(axis=1).nonzero()[0])
        tr = (int(mid[-1]) // 2 + 2) if len(mid) else (r0 + r1) // 2
        cv.text_center(tr + 1, T["stay"], scale(SLATE, 1.9))
        exc = T["excuses"]
        note = self.note or exc[int(t / 7.0) % len(exc)]
        if self.note is None:
            # the excuse types itself in, so a viewer notices it changed
            k = (t % 7.0) / 0.9
            note = note[: max(0, int(len(note) * min(1.0, k)))]
            if k < 1.0 and int(t * 6) % 2:
                note += "█"
        cv.text_center(tr + 3, note, mix(AMBER, WHITE, 0.35))

        m = "   ·   ".join(T["marquee"]) + "   ·   "
        off = int(t * 7) % len(m)
        wide = min(self.cols - 6, 120)
        s = (m[off:] + m[:off])[:wide]
        cv.text(r1 - 1, (self.cols - wide) // 2, s, scale(SLATE, 1.1))


def hud(cv, scene, t, total, fps, show_fps, music=None, lvl=(0.0, 0.0)):
    cols, rows = cv.cols, cv.rows
    cv.px[1, 2 : cols - 2] += np.array(scale(SLATE, 0.22), np.float32)
    cv.px[cv.h - 3, 2 : cols - 2] += np.array(scale(SLATE, 0.22), np.float32)
    cv.text(0, 2, "AGI", mix(CYAN, WHITE, 0.4))
    cv.text(0, 6, "∞  bathero", scale(SLATE, 1.5))
    cv.text_center(0, scene.title, mix(VIOLET, WHITE, 0.45))
    if scene.sub:
        s = scene.sub
        cv.text(0, (cols + len(scene.title)) // 2 + 3, s, scale(SLATE, 1.3))
    clock = time.strftime("%H:%M:%S")
    cv.text(0, cols - 2 - len(clock), clock, scale(SLATE, 1.6))
    if music is not None:
        s = "♪ " + music.label()
        cv.text(0, cols - 5 - len(clock) - len(s), s,
                mix(SLATE, CYAN, 0.35) if music.track else scale(SLATE, 1.2))
        lo, hi = lvl
        k, j, nn = int(lo * 14), int(hi * 14), 14
        bar = "".join("█" if i < k else ("▄" if i < j else "·") for i in range(nn))
        cv.text(rows - 1, cols - 2 - nn - (10 if show_fps else 0), bar,
                mix(CYAN, MAGENTA, min(1.0, hi)))
    keys = T["keys"]
    cv.text(rows - 1, 2, keys, scale(SLATE, 1.0))
    if show_fps:
        s = "%4.1f fps" % fps
        cv.text(rows - 1, cols - 2 - len(s), s, scale(SLATE, 1.4))
    # scene progress, a hairline that fills across the bottom
    n = int((cols - 4) * min(1.0, t / total))
    if n > 0:
        cv.px[cv.h - 1, 2 : 2 + n] += np.array(scale(CYAN, 0.30), np.float32)


def wipe(new, old, u, rng):
    """Transition: the new scene arrives behind a bright edge, with a glitch."""
    h = new.px.shape[0]
    edge = u * (h + 24) - 12
    y = np.arange(h, dtype=np.float32)[:, None, None]
    px = np.where(y < edge, new.px, old.px)
    lo = max(0, int(edge - 5))
    hi = min(h, int(edge + 2))
    if hi > lo:
        px[lo:hi] = np.roll(px[lo:hi], int(rng.integers(-14, 14)), axis=1)
        px[lo:hi] += np.array(scale(CYAN, 0.35), np.float32)
    new.px = px
    chars = {}
    for (r, c), v in old.chars.items():
        if r * 2 >= edge:
            chars[(r, c)] = v
    for (r, c), v in new.chars.items():
        if r * 2 < edge:
            chars[(r, c)] = v
    new.chars = chars


# ---------------------------------------------------------------- music
class Music:
    """The keygen loop, and where the animation is inside it.

    `low` (kick and bass) and `high` (hats and arpeggio) come pre-computed with
    the track, one value per 60th of a second, so following the music at playback
    time is an index, not an analysis. The position is the wall clock since the
    loop started: a Sound on a channel has no cursor to ask.
    """

    def __init__(self, enabled=True, volume=0.55, seed=None, bpm=None):
        self.enabled = enabled
        self.volume = volume
        self.seed = seed
        self.bpm = bpm
        self.track = None
        self.snd = None
        self.t0 = None
        self.error = None

    def start(self):
        if not self.enabled:
            return
        try:
            import pygame
            from . import keygen
            pygame.mixer.init(frequency=44100, size=-16, channels=2, buffer=1024)
            self.track = keygen.render(bpm=self.bpm, seed=self.seed)
            self.snd = pygame.sndarray.make_sound(self.track["pcm"])
            self.snd.set_volume(self.volume)
            self.snd.play(loops=-1, fade_ms=1800)
            self.t0 = time.time()
        except Exception as e:                     # no audio device, no music, no drama
            self.error = str(e).split("\n")[0][:40]
            self.track = None

    def level(self):
        if self.track is None or self.t0 is None:
            return 0.0, 0.0
        tr = self.track
        i = int(((time.time() - self.t0) % tr["length"]) * tr["fps"])
        i = min(i, len(tr["low"]) - 1)
        return float(tr["low"][i]), float(tr["high"][i])

    def toggle(self):
        if self.snd is None:
            self.enabled = not self.enabled
            if self.enabled:
                self.start()
            return
        if self.t0 is None:
            self.snd.play(loops=-1, fade_ms=600)
            self.t0 = time.time()
        else:
            self.snd.fadeout(400)
            self.t0 = None

    def nudge(self, d):
        self.volume = max(0.0, min(1.0, self.volume + d))
        if self.snd is not None:
            self.snd.set_volume(self.volume)

    def label(self):
        if self.error:
            return T["no_audio"]
        if self.track is None:
            return T["no_music"]
        return "%s %.0f" % (self.track["key"], self.track["bpm"])

    def fade(self, ms):
        if self.snd is not None and self.t0 is not None:
            self.snd.fadeout(int(ms))

    def stop(self):
        if self.snd is not None:
            self.snd.stop()


# ---------------------------------------------------------------- the loop
SCENES = [Latent, Attention, Diffusion, Network, Telemetry, TokenStream]
TRANSITION = 0.9
FADE_OUT = 1.4        # q closes the curtain: picture and music go down together
FADE_INT = 0.45       # ctrl-c is in more of a hurry


class App:
    def __init__(self, args):
        self.args = args
        self.rng = np.random.default_rng()
        order = [c for c in SCENES if args.scene in (None, c.name)] or SCENES
        self.order = order
        self.idx = 0
        self.cur = order[0]()
        self.old = None
        self.trans = 0.0
        self.t = 0.0
        self.scene_t = 0.0
        self.old_t = 0.0
        self.started = time.time()
        self.show_card = not args.no_card
        self.show_hud = not args.no_hud
        self.show_fps = False
        self.paused = False
        self.fps = float(args.fps)
        self.card = Card(args.title, args.note, args.channel)
        self.music = Music(not args.no_music, args.volume, args.music_seed, args.bpm)
        self.lvl = (0.0, 0.0)
        self.cols = self.rows = 0
        self.resized = True

    def layout(self):
        cols, rows = shutil.get_terminal_size((100, 30))
        cols = max(40, cols)
        rows = max(14, rows)
        self.cols, self.rows = cols, rows
        self.cv = Canvas(cols, rows)
        self.cv2 = Canvas(cols, rows)
        self.screen = Screen(cols, rows)
        self.cur.enter(self.cv.w, self.cv.h, cols, rows)
        if self.old is not None:
            self.old.enter(self.cv.w, self.cv.h, cols, rows)
        self.card.enter(self.cv.w, self.cv.h, cols, rows)
        self.resized = False

    def next_scene(self, step=1, jump=None):
        self.old = self.cur
        self.old_t = self.scene_t
        self.idx = jump if jump is not None else (self.idx + step) % len(self.order)
        self.idx %= len(self.order)
        self.cur = self.order[self.idx]()
        self.cur.enter(self.cv.w, self.cv.h, self.cols, self.rows)
        self.scene_t = 0.0
        self.trans = TRANSITION

    def keys(self, s):
        for ch in s:
            if ch in ("q", "Q", "\x1b", "\x03"):
                return False
            if ch == " ":
                self.next_scene()
            elif ch in ("p", "P"):
                self.paused = not self.paused
            elif ch in ("h", "H"):
                self.show_hud = not self.show_hud
            elif ch in ("b", "B"):
                self.show_card = not self.show_card
            elif ch in ("f", "F"):
                self.show_fps = not self.show_fps
            elif ch in ("m", "M"):
                self.music.toggle()
            elif ch in ("-", "_"):
                self.music.nudge(-0.05)
            elif ch in ("+", "="):
                self.music.nudge(0.05)
            elif ch.isdigit() and ch != "0":
                i = int(ch) - 1
                if i < len(self.order) and i != self.idx:
                    self.next_scene(jump=i)
        return True

    def frame(self, dt, fade=1.0):
        if self.resized:
            self.layout()
        if not self.paused:
            self.t += dt
            self.scene_t += dt
            self.old_t += dt
        cv = self.cv
        cv.clear()
        self.lvl = low, high = self.music.level()
        self.cur.low, self.cur.high = low, high
        self.cur.update(self.scene_t, dt if not self.paused else 0.0)
        self.cur.draw(cv)
        if self.trans > 0 and self.old is not None:
            self.cv2.clear()
            self.old.low, self.old.high = low, high
            self.old.update(self.old_t, dt if not self.paused else 0.0)
            self.old.draw(self.cv2)
            u = 1.0 - self.trans / TRANSITION
            wipe(cv, self.cv2, u, self.rng)
            self.trans = max(0.0, self.trans - dt)
            if self.trans <= 0:
                self.old = None
        if self.show_hud:
            hud(cv, self.cur, self.scene_t, self.args.seconds, self.fps, self.show_fps,
                self.music, self.lvl)
        if self.show_card:
            self.card.draw(cv, self.t, time.time() - self.started, *self.lvl)
        if fade < 1.0:
            cv.px *= fade
            cv.chars = {k: (ch, (c[0] * fade, c[1] * fade, c[2] * fade))
                        for k, (ch, c) in cv.chars.items()}
        return self.screen.frame(cv)

    def outro(self, out, seconds):
        """Close the curtain: the picture dims to black while the music fades,
        then one black frame so nothing is left burning on the alt screen."""
        self.music.fade(seconds * 1000)
        t0 = last = time.time()
        try:
            while True:
                now = time.time()
                u = (now - t0) / seconds
                if u >= 1.0:
                    break
                dt = min(0.1, now - last)
                last = now
                # squared in linear light is a straight line once the tone map
                # has applied its gamma: the eye sees the picture go down evenly
                out.write(self.frame(dt, fade=(1.0 - u) ** 2.2))
                out.flush()
                time.sleep(max(0.0, 1 / 30.0 - (time.time() - now)))
        except KeyboardInterrupt:
            pass                  # a second ctrl-c: skip the rest of the fade
        self.cv.clear((0.0, 0.0, 0.0))
        out.write(self.screen.frame(self.cv))
        out.flush()

    def run(self):
        target = 1.0 / max(5.0, float(self.args.fps))
        self.music.start()
        with Term() as term:
            try:
                signal.signal(signal.SIGWINCH, lambda *_: setattr(self, "resized", True))
                last = time.time()
                acc = 0.0
                n = 0
                out = sys.stdout
                while True:
                    now = time.time()
                    dt = min(0.1, now - last)
                    last = now
                    if not self.keys(term.keys()):
                        break
                    if not self.paused and self.trans <= 0 and self.scene_t > self.args.seconds:
                        self.next_scene()
                    out.write(self.frame(dt))
                    out.flush()
                    acc += dt
                    n += 1
                    if acc > 0.75:
                        self.fps = n / acc
                        acc = 0.0
                        n = 0
                    rest = target - (time.time() - now)
                    if rest > 0:
                        time.sleep(rest)
                self.outro(out, FADE_OUT)
            except KeyboardInterrupt:
                self.outro(out, FADE_INT)   # ctrl-c closes it as well as q does
        self.music.stop()


def snapshot(args):
    """Render a few frames of every scene to PNGs, to check the look headless."""
    os.environ.setdefault("PYGAME_HIDE_SUPPORT_PROMPT", "1")
    os.environ.setdefault("SDL_VIDEODRIVER", "dummy")
    import pygame
    pygame.init()
    cols, rows = args.snap_size.split("x")
    os.environ["COLUMNS"], os.environ["LINES"] = cols, rows
    app = App(args)
    os.makedirs(args.snap, exist_ok=True)
    for i, cls in enumerate(app.order):
        app.idx = i
        app.cur = cls()
        app.old = None
        app.trans = 0.0
        app.scene_t = 0.0
        app.resized = True
        app.frame(0.0)
        for _ in range(int(args.snap_seconds * 30)):
            app.frame(1 / 30.0)
        rgb = tonemap(app.cv.px)
        big = np.repeat(np.repeat(rgb, 3, 0), 3, 1)
        surf = pygame.image.frombuffer(np.ascontiguousarray(big).tobytes(),
                                       (big.shape[1], big.shape[0]), "RGB")
        path = os.path.join(args.snap, "%d-%s.png" % (i + 1, cls.name))
        pygame.image.save(surf, path)
        print(path, "%dx%d cells" % (app.cols, app.rows))
        # the character layer does not reach the PNG; dump it so it can be read
        grid = [[" "] * app.cols for _ in range(app.rows)]
        for (r, c), (ch, _) in app.cv.chars.items():
            if 0 <= r < app.rows and 0 <= c < app.cols:
                grid[r][c] = ch
        with open(path[:-4] + ".txt", "w") as f:
            f.write("\n".join("".join(row).rstrip() for row in grid))


def main(argv=None):
    # --lang is read before anything else: the help text, the defaults and every
    # string the screen draws come from the table it picks. `cortina` is the
    # Spanish command, `curtain` the English one (it passes --lang en).
    pre = argparse.ArgumentParser(add_help=False)
    pre.add_argument("--lang", default="es", choices=sorted(STRINGS))
    lang = pre.parse_known_args(argv)[0].lang
    set_lang(lang)
    ap = argparse.ArgumentParser(
        prog="curtain" if lang == "en" else "cortina", description=__doc__.split("\n")[0],
        formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--lang", default=lang, choices=sorted(STRINGS),
                    help="language of everything on screen (default: %s)" % lang)
    ap.add_argument("--title", default=None,
                    help="big text on the card (default: %s)" % T["card_title"])
    ap.add_argument("--note", default=None, help="fixed line under the title (default: it rotates)")
    ap.add_argument("--channel", default="twitch.tv/xantwav")
    ap.add_argument("--scene", default=None, help="lock to one scene: " + ", ".join(c.name for c in SCENES))
    ap.add_argument("--seconds", type=float, default=26.0, help="seconds per scene")
    ap.add_argument("--fps", type=float, default=30.0)
    ap.add_argument("--no-card", action="store_true", help="animation only, no be-right-back card")
    ap.add_argument("--no-hud", action="store_true")
    ap.add_argument("--no-music", action="store_true", help="no keygen music")
    ap.add_argument("--volume", type=float, default=0.55)
    ap.add_argument("--music-seed", type=int, default=None, help="play a tune you liked again")
    ap.add_argument("--bpm", type=float, default=None)
    ap.add_argument("--snap", default=None, help="render every scene to PNGs in this directory and exit")
    ap.add_argument("--snap-seconds", type=float, default=6.0)
    ap.add_argument("--snap-size", default="212x58")
    args = ap.parse_args(argv)
    if args.snap:
        snapshot(args)
        return 0
    if not sys.stdout.isatty():
        print("agi needs a terminal", file=sys.stderr)
        return 1
    App(args).run()
    return 0


if __name__ == "__main__":
    sys.exit(main())
