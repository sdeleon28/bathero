"""Screens (hub, category lists, kit wizard, play) and the main loop.

Navigation is drum-driven: on the hub each drum opens its own colored section;
inside a section the hi-hat moves down, the crash moves up, the snare accepts
and the kick goes back. The keyboard always works too.
"""
import argparse
import bisect
import math
import os
import sys
import threading
import time
import zlib
from collections import deque

import mido
import pygame

from . import chart as C
from .chart import BEATS, COURSES, EXERCISES, build_lanes
from . import game as GM
from .game import Game
from .kit import (default_kit, describe, describe_pads, load_kit, load_progress, load_settings, save_kit,
                  save_progress, save_settings)
from .runlog import RunLog
from .capture import Recorder
from .twitch import Chat, StreamLink
from . import twitch as TW
from . import capture as CP
from . import edit as E
from . import stats as ST
from . import profiles as PR
from .coach import Coach
from . import hints as HI
from . import latency as LA
from . import coach as CO
from .devices import DeviceWatcher, Toasts, TOAST_S
from .render import ACCENT, BG, DIM, JUDGE_COLORS, LANE_BG, TEXT, Fonts, Renderer, draw_hihat_state, draw_stars, lerp
from .game import SONG_COUNT, SONG_COUNT_BEATS, TAIL_S, lead_in_for_chart
from . import ghost as GH
from . import intro as IN
from . import keygen
from . import songs as SG
from .twister import Twister, PORT_HINT as TWISTER_HINT
from . import launchpad as LP
from . import songedit as SE
from . import importer as IM
from .ghost import GhostFilter
from .sounds import (BACKING_GAIN, METRONOME_GAIN, PROGRESSIONS, SoundBank, Track,
                     menu_music_sound, output_devices, render_backing_track, render_count, render_metronome, MENU_CHANNEL)
from . import sounds as SND

TARGET_FPS = 240
CAPTURE_S = 1.5           # wizard: keep collecting note numbers this long after the first hit
NAV_MIN_VELOCITY = 25     # softer hits never navigate (sticks resting on the snare read 4..14)
NAV_SOUND_MIN_VELOCITY = 15  # ...but every hit above this is heard, undebounced, so rolls sound whole
RESULTS_GRACE_S = 1.0     # after a level ends, ignore drum hits this long before they navigate
ARM_GRACE_S = 0.4         # the intro ignores snare hits this long after it appears (a flam after the nav hit)
VOLUME_STEP = 0.05        # { and } move the game's output level by this much
METRO_VOLUME_STEP = 0.1   # = and - move the metronome's own level by this much
METRO_VOLUME_MAX = 1.6    # over that the track's gain would clip at the mixer's ceiling
LOOP_GESTURE_S = 2.0      # after l, the two phrase digits must arrive within this; then it lapses
DEBUG_HITS = 200          # hits the ` pane remembers
STALL_S = 2.0             # a frame this long is logged with the main thread's stack (App._watchdog)
CAM_RETRY_S = 5.0         # the ! layer looks for a missing camera this often
DEBUG_BARS = 48           # of which it draws as bars
NOTE_NAMES = ("C", "C#", "D", "D#", "E", "F", "F#", "G", "G#", "A", "A#", "B")


def note_name(note):
    """MIDI note -> name in the Bitwig / Kontakt convention (60 = C3): 42 -> C#1."""
    return f"{NOTE_NAMES[note % 12]}{note // 12 - 2}"
KEY_LANES = {pygame.K_1: 0,   # 1..9, 0 -> 0..9: the transport's phrases, the wizard's pads
             pygame.K_2: 1, pygame.K_3: 2, pygame.K_4: 3, pygame.K_5: 4,
             pygame.K_6: 5, pygame.K_7: 6, pygame.K_8: 7, pygame.K_9: 8, pygame.K_0: 9}
MODULE_HINTS = ("td-", "td1", "td2", "td5", "alesis", "nitro", "strike", "dtx", "roland", "drum")   # most specific first
MODULE_EXCLUDE = ("piano", "fp-", "keyboard", "twister", "launchpad", "hhmapper")   # Roland makes pianos too (2026-11-08: the game sat on "Roland Digital Piano")


def find_module(names, hints=MODULE_HINTS):
    """The drum module among the MIDI inputs: the first hint that matches anything wins, so
    "TD-17" beats "Roland Digital Piano" whatever order CoreMIDI lists them in."""
    ok = [n for n in names if not any(x in n.lower() for x in MODULE_EXCLUDE)]
    return next((n for h in hints for n in ok if h in n.lower()), None)

# What each drum does inside a list. The hub uses the drums as section buttons instead.
NAV = {"snare": "accept", "kick": "back", "hihat": "next", "crash": "prev", "crash2": "prev",
       "tom1": "lead", "floor": "lead"}      # lead: swap the leading hand (lists of exercises)
HAND_COLORS = {"R": (245, 90, 90), "L": (80, 200, 230)}     # as on the sticking strip
CATEGORIES = [
    ("kick", "Exercises", "one drum at a time, slow"),
    ("snare", "Beats", "full grooves"),
    ("hihat", "Courses", "a genre's grooves and fills, in order"),
    ("tom1", "Songs", "your songs, split and transcribed"),
    ("crash", "Setup", "kit, sounds, quit"),
]
HUB_ROWS = [[0, 1, 2], [3, 4]]      # the hub's five cards: three on top, two wider ones under them


class App:
    def __init__(self, args):
        self.args = args
        self.settings = load_settings()
        if args.audio_device:
            self.settings["audio_device"] = args.audio_device
        SND.set_master(self.settings.get("volume", 1.0))
        kit = load_kit(args.kit) if args.kit else load_kit()
        self.first_run = kit is None
        self.set_kit(kit or default_kit())
        self.guide = not args.no_guide
        self.backing_on = not args.no_backing
        self.metronome_mode = "off" if args.no_metronome else "full"    # full / beats / off
        self.menu_music_on = not args.no_menu_music
        self.menu_music = None
        self.track_cache = {}
        self.offset_ms = args.offset if args.offset else float(self.settings.get("offset_ms") or 0.0)
        self.rate = args.speed          # tempo multiplier for levels, 1.0 = as written
        self.profiles = PR.load_profiles()   # who can play; empty = no login, one shared progress
        self.profile = None                  # the profile playing now (LoginScreen), None = nobody / no profiles
        self.results = load_progress(PR.progress_path(None))   # chart key -> best stats so far (stars, grade...), saved per profile
        self.tries = {k: v.get("tries", 0) for k, v in self.results.items()}   # attempts under five stars in a row, per level
        self.midi_name = None
        self.midi_in = None
        self.screen_obj = None
        self.fullscreen = False
        self.drum_queue = deque()  # navigation hits, handed to the screen on the main thread
        # the Midi Fighter Twister's bank 2 (twister.py): song mixer and tempo, handed to the main thread
        self.twister_queue = deque()
        self.twister = Twister(lambda name, v: self.twister_queue.append(("turn", name, v)),
                               lambda name: self.twister_queue.append(("press", name, None)),
                               lambda bank: self.twister_queue.append(("bank", None, bank)))
        # the Launchpad MK2 (launchpad.py): the level's navigator while the Twister is on bank 2
        self.launchpad_queue = deque()
        self.launchpad = LP.Launchpad(lambda kind, name: self.launchpad_queue.append((kind, name)))
        self.lp_explorer = LP.Explorer()
        self.twister_bank = LP.read_bank()
        self.launchpad_checked = 0.0
        self.lp_repaint_at = None
        self.twister_checked = 0.0
        self.tempo_want = None       # (rate, wall time of the last turn): the tempo knob settles before the level restarts
        self.tempo_job = None        # (rate, thread) stretching the stems in the background
        self.refresh_songs()
        self.importer = IM.Importer()         # the song importer's process (bathero.ingest --watch), on / off from the songs screen
        self.importer_seen = None             # (its start, songs imported) last seen: a new song refreshes the list
        self.importer_checked = 0.0
        if self.settings.get("importer_on") and not self.importer.running:
            self.importer.start()
        self.ghosts = GhostFilter()  # drops the hi-hat notes the pedal produces on its own
        self.runlog = RunLog()       # every level is written to ~/Library/Logs/bathero/runs when it ends
        self.recorder = Recorder(self.settings)   # V: take of the game, the interface's mix and the camera
        self.streamer = StreamLink(self.settings, log=print)   # T: the stream, its own process (attaches to a live one)
        self.stream_phase = "off"                 # what the badge showed last frame; transitions become toasts
        self.chat = None                          # the channel's chat while the ! layer is on
        self.editor = E.Editor(self.settings.get("claude_bin"))   # Edit with Claude
        self.coach = Coach(self.settings.get("claude_bin"), self.settings.get("coach_language", "es"),
                           self.settings.get("coach_model"))
        self.session = None          # {"name", "items": [(cat, index, rate, reps, why, lead)], "pos", "rep"} while a playlist runs
        self.lead = "R"              # the hand leading the exercises that have a lead (Chart.lead); toms swap it
        # the transport (play screen): 1..0 jump to a phrase, l35 marks a loop, \ switches it,
        # K gives the numbers back to the lanes (the two are exclusive), P stops saving progress
        self.practice = False        # P: the run writes no progress
        # S: every level opens on its intro (what it teaches, in Spanish) and waits for a snare hit
        self.intro_on = bool(self.settings.get("intro", True))
        self.scroll_fixed = bool(self.settings.get("scroll_fixed", False))   # D: notes fall at one speed whatever the tempo
        self.loop_range = None       # (first phrase, last phrase), kept across a retry (tempo change)
        self.loop_bars = None        # or (first bar, end bar), marked on the Launchpad
        self.loop_on = False
        self.toasts = Toasts()
        self.camera_name = None
        self.watcher = DeviceWatcher(args.port, self.settings.get("audio_device"), self.settings.get("capture_camera", "iPhone"),
                                     midi_hints=MODULE_HINTS)
        self.midi_trace = None       # one line per note-on, for latency measurements (--midi-trace or settings)
        trace = getattr(args, "midi_trace", None) or self.settings.get("midi_trace")
        if trace:
            self.midi_trace = open(os.path.expanduser(trace), "a")
        self.legend_flash = {}     # instrument -> wall time of its last navigation hit
        self.debug_on = False      # the ` key: velocity viewer over any screen
        self.layer_on = False      # the ! key: the streamer's layer over any screen (the camera as the PiP, the chat)
        self.layer_auto = False    # the stream turned it on (so its end turns it off and frees the camera); ! makes it the user's
        self.cam_preview = None    # our own CameraPreview while the recorder does not hold the camera
        self._cam_opening = None   # the thread opening it (open_cam_preview)
        self._cam_tried = -1e9     # perf_counter of the last attempt
        self._cam_wanted = False
        self._cam_cache = (None, None)   # (source frame counter, scaled surface)
        self.debug_hits = deque(maxlen=DEBUG_HITS)   # (wall t, note, velocity, instrument, outcome, is ghost)

        # display and fonts only: pygame.init() would open the mixer here, and opening an audio
        # device can block on macOS's microphone prompt (seen 2026-09-08: the window never came
        # up). The mixer opens in a thread instead and swaps in when ready.
        pygame.display.init()
        pygame.font.init()
        self.sounds = SoundBank(enabled=False)
        self.sounds_ready = threading.Event()
        self._sounds_pending = None
        if not args.no_sound:
            threading.Thread(target=self._open_sounds, daemon=True).start()
        else:
            self.sounds_ready.set()
        w, h = (int(v) for v in args.size.lower().split("x"))
        # RESIZABLE gives the window macOS's green fullscreen button (native Spaces fullscreen).
        self.surface = pygame.display.set_mode((w, h), pygame.RESIZABLE)
        pygame.display.set_caption("bathero")
        self.size = self.surface.get_size()
        self.fonts = Fonts(self.scale)
        fullscreen = self.settings.get("fullscreen", True)
        if args.fullscreen:
            fullscreen = True
        if args.windowed:
            fullscreen = False
        if fullscreen:
            self.toggle_fullscreen()
            self.on_resize()
        self.open_midi(args.port)
        self.watcher.state["midi"] = self.midi_name
        self.watcher.state["audio"] = self.sounds.device or ("system default" if not self.settings.get("audio_device") else None)
        if not (args.port == "__none__"):
            self.watcher.start()

    # --- audio output ------------------------------------------------------------
    def _open_sounds(self):
        """Background: build the mixer and the kit; the main loop adopts it (adopt_sounds)."""
        try:
            bank = SoundBank(enabled=True, device=self.settings.get("audio_device"), drums=self.settings.get("drum_sounds", True))
        except Exception as e:                                  # noqa: BLE001
            print(f"audio failed to open: {e}")
            bank = SoundBank(enabled=False)
        self._sounds_pending = bank

    def adopt_sounds(self):
        """Main thread: swap in the mixer opened in the background, once."""
        bank = self._sounds_pending
        if bank is None:
            return
        self._sounds_pending = None
        self.sounds = bank
        if isinstance(self.screen_obj, PlayScreen):
            self.screen_obj.game.sounds = bank
        self.sounds_ready.set()
        if bank.ok:
            self.toasts.add(f"audio ready: {bank.device or 'system default'}", DIM)
        self.update_menu_music()

    def wait_sounds(self, timeout=15.0):
        """Block until the mixer is open (tests and scripts); the game never waits."""
        t0 = time.perf_counter()
        while not self.sounds_ready.is_set() and time.perf_counter() - t0 < timeout:
            self.adopt_sounds()
            time.sleep(0.01)
        self.adopt_sounds()
        return self.sounds.ok

    def set_audio_device(self, name):
        """Reopen the mixer on another output; every Sound belongs to the old mixer, so rebuild."""
        if self.menu_music is not None:
            self.menu_music.stop()
        if isinstance(self.screen_obj, PlayScreen):
            self.screen_obj.game.stop_tracks()
        self.settings["audio_device"] = name
        save_settings(self.settings)
        self.sounds = SoundBank(enabled=not self.args.no_sound, device=name)
        self.track_cache = {}
        self.menu_music = None
        if isinstance(self.screen_obj, PlayScreen):
            self.screen_obj.game.sounds = self.sounds
        self.update_menu_music()

    def set_drum_sounds(self, on):
        """The kit's own hit, guide and navigation sounds. Off when the module or a DAW
        (Bitwig with GetGood Drums through hhmapper) makes the drum sound; the metronome,
        backing and menu music stay."""
        self.sounds.drums = on
        self.settings["drum_sounds"] = on
        save_settings(self.settings)

    @property
    def volume(self):
        return SND.master()

    def set_volume(self, v):
        """The game's own output level ({ and }), independent of the mixer's fader. Applies to
        the sounds already playing (menu music, a level's tracks) and to every new one; saved."""
        SND.set_master(v)
        self.settings["volume"] = SND.master()
        save_settings(self.settings)
        if self.menu_music is not None:
            self.menu_music.set_volume(SND.MENU_MUSIC_GAIN * self.menu_volume * SND.master())
        if isinstance(self.screen_obj, PlayScreen):
            for track, _ in self.screen_obj.game.tracks.values():
                track.apply_gain()
        self.toasts.add(f"volume {SND.master():.0%}", ACCENT, key="volume")
        self.twister.ring("master", SND.master())

    def nudge_volume(self, d):
        self.set_volume(round(SND.master() + d * VOLUME_STEP, 2))

    @property
    def metro_volume(self):
        return self.settings.get("metronome_volume", 1.0)

    def set_metro_volume(self, v):
        """The metronome's level relative to the rest of the mix (= and -), 0..METRO_VOLUME_MAX
        of METRONOME_GAIN: it gets lost under the backing or the song otherwise. Applies to the
        level being played (the track keeps its gain, which is cached with it); saved."""
        v = round(max(0.0, min(METRO_VOLUME_MAX, float(v))), 2)
        self.settings["metronome_volume"] = v
        save_settings(self.settings)
        if isinstance(self.screen_obj, PlayScreen):
            self.screen_obj.game.metro_volume = v
            track = self.screen_obj.game.tracks.get("metronome")
            if track:
                track[0].gain = METRONOME_GAIN * v
                track[0].apply_gain()
        self.toasts.add(f"metronome {v:.0%}" + (" (off)" if v == 0 else ""), ACCENT, key="metro_volume")
        self.twister.ring("metronome", v / METRO_VOLUME_MAX)

    def nudge_metro_volume(self, d):
        self.set_metro_volume(self.metro_volume + d * METRO_VOLUME_STEP)

    @property
    def dyn_scales(self):
        """Accent sensitivity per instrument (instrument -> scale, every instrument present).
        Settings before 2026-09-17 had one number, `dyn_scale`: it seeds every instrument."""
        sc = self.settings.get("dyn_scales")
        if not isinstance(sc, dict):
            sc = self.settings["dyn_scales"] = {inst: self.settings.get("dyn_scale", 1.0) for inst in C.INSTRUMENTS}
        for inst in C.INSTRUMENTS:
            sc.setdefault(inst, self.settings.get("dyn_scale", 1.0))
        return sc

    def toggle_intro(self):
        """S: the level intro (what the level teaches, then a snare hit starts it) on or off, saved."""
        self.intro_on = not self.intro_on
        self.settings["intro"] = self.intro_on
        save_settings(self.settings)
        self.toasts.add("level intro on: each level explains itself and waits for a snare hit" if self.intro_on
                        else "level intro off: levels start right away", ACCENT, key="intro")

    def toggle_scroll(self):
        """D: the highway's speed. Off, the screen shows two seconds of chart and the notes fall
        slower at a slower tempo (a millisecond shrinks with the rate); on, they fall at the
        same pixels per second at every tempo, so an error is always the same distance. Saved."""
        self.scroll_fixed = not self.scroll_fixed
        self.settings["scroll_fixed"] = self.scroll_fixed
        save_settings(self.settings)
        self.toasts.add("scroll fixed: notes fall at the same speed at every tempo, a millisecond is always the same distance"
                        if self.scroll_fixed else "scroll follows the tempo: two seconds of chart on screen", ACCENT, key="scroll")

    def dyn_scale_label(self):
        """For the Setup row: one number when every body agrees, else the ones that differ."""
        sc = self.dyn_scales
        vals = set(sc.values())
        if len(vals) == 1:
            return f"{vals.pop():.0%}"
        common = max(vals, key=lambda v: list(sc.values()).count(v))
        return f"{common:.0%} · " + " · ".join(f"{i} {v:.0%}" for i, v in sc.items() if v != common)

    def dyn_target(self):
        """The instrument ; and ' move while the velocity viewer is open: the last stroke
        heard that was not a ghost (None before the first)."""
        for _, note, vel, inst, res, ghost in reversed(self.debug_hits):
            if not ghost and inst:
                return inst
        return None

    def set_dyn_scale(self, v, instrument=None):
        """Accent sensitivity (; and '): scales the accent / tap thresholds of one instrument
        (its zones share it) or, with None, of every one to the same value, 100 % = the
        measured ones. Applies to the level being played; saved."""
        v = round(max(GM.DYN_SCALE_MIN, min(GM.DYN_SCALE_MAX, float(v))), 2)
        sc = self.dyn_scales
        for inst in ([instrument] if instrument else list(sc)):
            sc[inst] = v
        save_settings(self.settings)
        if isinstance(self.screen_obj, PlayScreen):
            self.screen_obj.game.dyn_scales = dict(sc)
            if self.runlog is not None:
                self.runlog.add("dyn_scale", thresholds=self.screen_obj.game.dyn_thresholds())
        self.toasts.add(f"{C.LABELS.get(instrument, instrument) + ' ' if instrument else ''}accent sensitivity {v:.0%}",
                        ACCENT, key="dyn_scale")

    def nudge_dyn_scale(self, d):
        """With the velocity viewer open: the last body heard, on its own (asked 2026-09-17,
        the hi-hat reads hotter than the snare and the toms softer). Closed: every body,
        each from where it stands, so their offsets are kept."""
        target = self.dyn_target() if self.debug_on else None
        if target:
            self.set_dyn_scale(self.dyn_scales[target] + d * GM.DYN_SCALE_STEP, target)
            return
        sc = self.dyn_scales
        for inst in list(sc):
            sc[inst] = round(max(GM.DYN_SCALE_MIN, min(GM.DYN_SCALE_MAX, sc[inst] + d * GM.DYN_SCALE_STEP)), 2)
        save_settings(self.settings)
        if isinstance(self.screen_obj, PlayScreen):
            self.screen_obj.game.dyn_scales = dict(sc)
            if self.runlog is not None:
                self.runlog.add("dyn_scale", thresholds=self.screen_obj.game.dyn_thresholds())
        self.toasts.add(f"accent sensitivity {self.dyn_scale_label()}", ACCENT, key="dyn_scale")

    def cycle_audio_device(self):
        names = [None] + output_devices()
        current = self.sounds.device
        i = names.index(current) if current in names else 0
        self.set_audio_device(names[(i + 1) % len(names)])

    # --- window --------------------------------------------------------------------
    @property
    def scale(self):
        return self.size[1] / 720

    def toggle_fullscreen(self):
        """Desktop fullscreen (macOS Spaces, the green button), never the exclusive mode that
        switches the monitor to the window's resolution and swallows trackpad gestures."""
        try:
            win = pygame.Window.from_display_module()
            if self.fullscreen:
                win.set_windowed()
                self.fullscreen = False
            else:
                win.set_fullscreen(desktop=True)
                self.fullscreen = True
        except (pygame.error, AttributeError) as e:
            print(f"fullscreen: {e}")

    def on_resize(self):
        """The window changed size (green button, drag, fullscreen toggle): relayout everything."""
        self.surface = pygame.display.get_surface()
        new = self.surface.get_size()
        if new == self.size and self.fonts.scale == self.scale:
            return
        self.size = new
        self.fonts = Fonts(self.scale)
        if self.screen_obj is not None:
            self.screen_obj.on_resize()

    # --- kit / content -----------------------------------------------------------
    def set_kit(self, kit):
        """kit: {zone key: [note numbers]}. A number in two zones goes to the earlier zone."""
        self.kit = kit
        self.note_to_zone = {}
        for zk in reversed(C.ZONE_KEYS):
            for n in kit.get(zk, []):
                self.note_to_zone[n] = zk

    def zone_for(self, note):
        return self.note_to_zone.get(note)

    def instrument_for(self, note):
        zk = self.note_to_zone.get(note)
        return C.ZONE[zk].instrument if zk else None

    def has_drum(self, inst):
        return bool(self.midi_in) and bool(C.kit_notes(self.kit, inst))

    def categories(self):
        """Every category that holds levels: the two lists and one key per course
        ("course:pop-punk"); "hihat" itself is the course chooser and holds none."""
        return ["kick", "snare"] + [c.key for c in COURSES] + [SG.SONG_COURSE]

    def refresh_songs(self):
        """The imported songs (bathero.songs) as one more course, re-read from disk. It lives in
        C.COURSE (so the lists and the play screen treat it like a course) but not in COURSES
        (the built-in levels, which every intro and uniqueness check walks)."""
        C.COURSE[SG.SONG_COURSE] = SG.course()
        scr = getattr(self, "screen_obj", None)
        if isinstance(scr, PlayScreen) and scr.cat == SG.SONG_COURSE:
            # sorted by difficulty: an edit or a new song can move the one being played
            names = [ch.name for ch in C.COURSE[SG.SONG_COURSE].levels]
            if scr.chart.name in names:
                scr.index = names.index(scr.chart.name)
        return C.COURSE[SG.SONG_COURSE]

    # --- the song importer (importer.py) -------------------------------------------------
    def poll_importer(self):
        """Once a second: a song the importer finished joins the list (and says so)."""
        now = time.perf_counter()
        if now - self.importer_checked < 1.0:
            return
        self.importer_checked = now
        st = self.importer.status()
        if not st.get("pid"):
            return
        seen = (st.get("started"), st.get("imported", 0))
        if self.importer_seen is not None and seen[0] == self.importer_seen[0] and seen[1] > self.importer_seen[1]:
            self.refresh_songs()
            self.toasts.add(f"song ready: {st.get('last')}", JUDGE_COLORS["PERFECT"], key="importer")
        self.importer_seen = seen

    def toggle_importer(self):
        """The importer on or off, remembered: on, it starts with the game until turned off."""
        im = self.importer
        if im.running or im.starting:
            im.stop()
            self.settings["importer_on"] = False
            self.toasts.add("importer stopped", DIM, key="importer")
        elif not im.available():
            self.toasts.add("no importer: .venv-ingest is missing (see CLAUDE.md, Imported songs)", JUDGE_COLORS["MISS"], key="importer")
            return
        else:
            im.start()
            self.settings["importer_on"] = True
            self.toasts.add("importer started: watching ~/Music/bathero/inbox", ACCENT, key="importer")
        save_settings(self.settings)

    # --- song mixer (the Twister) ------------------------------------------------------
    SONG_GAINS = {"backing": ("song_backing", 1.0), "drums": ("song_drums", 0.3)}
    OFFSET_KNOB_MAX = 127        # knob 5: 0..127 ms, one ms per step
    OFFSET_FALLBACK = 45         # the chain's measured latency, when there are no runs to fit
    SONG_BASE_GAIN = 0.8
    COUNT_OVER_DB = 3            # a song's count-in: this much over the backing's loud moments

    def song_gain(self, name):
        key, default = self.SONG_GAINS[name]
        return float(self.settings.get(key, default))

    def play_screen_song(self):
        scr = self.screen_obj
        return scr if isinstance(scr, PlayScreen) and scr.chart.song else None

    def on_twister(self, name, v):
        """A turn of a bank-2 knob, on the main thread (v 0..1)."""
        if name in self.SONG_GAINS:
            self.settings[self.SONG_GAINS[name][0]] = round(v, 3)
            self.settings_dirty = time.perf_counter()
            scr = self.play_screen_song()
            if scr:
                tr = scr.game.tracks.get(name)
                if tr:
                    tr[0].gain = self.SONG_BASE_GAIN * v
                    tr[0].apply_gain()
            self.toasts.add(f"{'backing track' if name == 'backing' else 'drum stem'} {v:.0%}", ACCENT, key="twister_" + name)
        elif name == "metronome":
            self.set_metro_volume(v * METRO_VOLUME_MAX)
        elif name == "offset":
            self.set_offset(round(v * self.OFFSET_KNOB_MAX))
        elif name == "menu":
            self.set_menu_volume(v)
        elif name == "master":
            self.set_volume(v)
        elif name == "tempo":
            scr = self.play_screen_song()
            if not scr:
                self.toasts.add("tempo knob: songs only", DIM, key="twister_tempo")
                return
            lo = SG.min_rate(scr.chart.bpm / scr.chart.rate)
            rate = round(lo + v * (1 - lo), 3)
            self.tempo_want = (rate, time.perf_counter())
            self.toasts.add(f"tempo {scr.chart.bpm / scr.chart.rate * rate:.0f} bpm ({rate:.0%})", ACCENT, key="twister_tempo")

    @property
    def menu_volume(self):
        return float(self.settings.get("menu_music_volume", 1.0))

    def set_menu_volume(self, v):
        """The menu music's own level (knob 15), under the master; saved."""
        self.settings["menu_music_volume"] = round(max(0.0, min(1.0, v)), 3)
        self.settings_dirty = time.perf_counter()
        if self.menu_music is not None:
            self.menu_music.set_volume(SND.MENU_MUSIC_GAIN * self.menu_volume * SND.master())
        self.twister.ring("menu", self.menu_volume)
        self.toasts.add(f"menu music {self.menu_volume:.0%}", ACCENT, key="menu_volume")

    def set_offset(self, ms):
        """The latency offset (, . and knob 5): on the level being played at once, saved."""
        self.offset_ms = float(ms)
        if isinstance(self.screen_obj, PlayScreen):
            self.screen_obj.game.offset_ms = self.offset_ms
        self.settings["offset_ms"] = self.offset_ms
        self.settings_dirty = time.perf_counter()
        self.runlog.add("offset", offset_ms=self.offset_ms)
        self.twister.ring("offset", self.offset_ms / self.OFFSET_KNOB_MAX)
        self.toasts.add(f"offset {self.offset_ms:.0f} ms", ACCENT, key="offset")

    def on_twister_press(self, name):
        """A pushed encoder: its knob back to the neutral value. The mix: backing full, drum stem
        silent (you are the drummer), metronome and everything at their default; the song's own
        tempo; the offset the recent runs fit best (the results box's "last N runs"), worked out
        in a thread since it reads the run logs, the chain's 45 ms when there are none."""
        if name in self.SONG_GAINS:
            v = {"backing": 1.0, "drums": 0.0}[name]
            self.on_twister(name, v)
        elif name == "metronome":
            self.set_metro_volume(1.0)
            v = 1.0 / METRO_VOLUME_MAX
        elif name == "master":
            self.set_volume(1.0)
            v = 1.0
        elif name == "menu":
            self.set_menu_volume(1.0)
            v = 1.0
        elif name == "tempo":
            if not self.play_screen_song():
                return
            self.tempo_want = (1.0, 0.0)
            v = 1.0
        elif name == "offset":
            profile = self.profile_id

            def fit():
                try:
                    best = LA.pooled([cv for _, cv in LA.recent_curves(profile)])
                except Exception:
                    best = None
                self.twister_queue.append(("offset_fit", "offset", best))
            threading.Thread(target=fit, daemon=True).start()
            self.toasts.add("offset: fitting the recent runs...", DIM, key="offset")
            return
        else:
            return
        self.twister.ring(name, v)

    def apply_turn(self, name, v):
        """A turn from the knob itself: applied without sending its ring back (Twister.ring)."""
        self.twister.turning = True
        try:
            self.on_twister(name, v)
        finally:
            self.twister.turning = False

    def poll_twister(self):
        now = time.perf_counter()
        if now - self.twister_checked > 3.0 and self.twister.inp is None:
            self.twister_checked = now
            if self.twister.open():
                self.toasts.add(f"Twister: bank 2 is the song mixer", ACCENT, key="twister")
                self.sync_twister()
                self.twister_resync_at = now + 1.0       # again, in case a replugged Twister was still booting
        if getattr(self, "twister_resync_at", None) and now >= self.twister_resync_at:
            self.twister_resync_at = None
            self.sync_twister()
        last = {}
        while self.twister_queue:
            kind, name, v = self.twister_queue.popleft()
            if kind == "turn":
                last[name] = v                   # a fast turn sends dozens: the last one per frame is enough
                continue
            for n, lv in last.items():           # turns before a push land first
                self.apply_turn(n, lv)
            last = {}
            if kind == "press":
                self.on_twister_press(name)
            elif kind == "bank":
                self.on_twister_bank(v)
            elif kind == "offset_fit":
                ms = v if v is not None else self.OFFSET_FALLBACK
                self.set_offset(max(0, min(self.OFFSET_KNOB_MAX, ms)))
                self.toasts.add(f"offset {ms:.0f} ms" + (" (best over the recent runs)" if v is not None else " (no runs to fit)"),
                                ACCENT, key="offset")
        for name, v in last.items():
            self.apply_turn(name, v)
        if getattr(self, "settings_dirty", None) and now - self.settings_dirty > 1.0:
            self.settings_dirty = None
            save_settings(self.settings)
        # the tempo knob: once it rests 0.4 s, stretch the stems in the background, then restart the song there
        if self.tempo_want and now - self.tempo_want[1] > 0.4 and self.tempo_job is None:
            rate = self.tempo_want[0]
            self.tempo_want = None
            scr = self.play_screen_song()
            if scr and abs(rate - self.rate) > 0.004:
                song = scr.chart.song
                th = threading.Thread(target=lambda: [SG.audio(song, st, rate) for st in ("backing", "drums")], daemon=True)
                th.start()
                self.tempo_job = (rate, th)
                self.toasts.add(f"stretching to {rate:.0%}...", DIM, key="twister_tempo")
        if self.tempo_job and not self.tempo_job[1].is_alive():
            rate, _ = self.tempo_job
            self.tempo_job = None
            scr = self.play_screen_song()
            if scr:
                self.rate = rate
                scr.retry()

    def on_twister_bank(self, bank):
        """The Twister changed bank: on bank 2 the Launchpad is ours (GroupCtl lets go of it at the
        same message), anywhere else it is GroupCtl's and we stop sending."""
        was = self.twister_bank
        self.twister_bank = bank
        LP.write_bank(bank)
        self.launchpad.forget()
        self.launchpad_queue.clear()
        if bank == LP.LENT_BANK:
            self.sync_twister()                     # bank 2's LEDs and rings, also after a replug the watcher missed
            self.lp_explorer.enter()
            self.lp_repaint_at = time.perf_counter() + 0.3     # again, over a late frame of GroupCtl's
            if was != bank:
                self.toasts.add("Launchpad: the level's navigator (Twister bank 2)", ACCENT, key="launchpad")
        elif was == LP.LENT_BANK:
            self.toasts.add("Launchpad back to GroupCtl", DIM, key="launchpad")

    def poll_launchpad(self):
        now = time.perf_counter()
        lp = self.launchpad
        if lp.inp is None and now - self.launchpad_checked > 3.0:
            self.launchpad_checked = now
            lp.open()
        if lp.inp is None or self.twister_bank != LP.LENT_BANK:
            self.launchpad_queue.clear()
            return
        scr = self.screen_obj if isinstance(self.screen_obj, PlayScreen) else None
        while self.launchpad_queue:
            kind, name = self.launchpad_queue.popleft()
            if scr is not None:
                scr.on_launchpad(kind, name)
        if self.lp_repaint_at is not None and now >= self.lp_repaint_at:
            self.lp_repaint_at = None
            lp.forget()
        lp.show(scr.launchpad_frame() if scr is not None else LP.blank())

    def sync_twister(self):
        """Put the rings where the values are."""
        tw = self.twister
        tw.paint()
        for name in self.SONG_GAINS:
            tw.ring(name, self.song_gain(name))
        tw.ring("metronome", self.metro_volume / METRO_VOLUME_MAX)
        tw.ring("master", SND.master())
        tw.ring("offset", self.offset_ms / self.OFFSET_KNOB_MAX)
        tw.ring("menu", self.menu_volume)
        scr = self.play_screen_song()
        if scr:
            lo = SG.min_rate(scr.chart.bpm / scr.chart.rate)
            tw.ring("tempo", (self.rate - lo) / (1 - lo) if lo < 1 else 1)

    def items_for(self, cat):
        if cat in C.COURSE:
            return C.COURSE[cat].levels
        return {"kick": EXERCISES, "snare": BEATS}.get(cat, [])

    def prog_for(self, cat, index):
        """The backing's progression seed for a level: the exercises, the beats and every course
        get their own range, so two levels with the same index do not share a tune."""
        if cat in C.COURSE:
            return 100 * (1 + list(C.COURSE).index(cat)) + index
        return index + (0 if cat == "kick" else 2)

    def course_stars(self, course):
        """(stars got, stars possible) over a course's levels for the profile playing."""
        keys = [ch.key for ch in course.levels] + [ch.mirrored().key for ch in course.levels if ch.lead]   # both sides count
        return sum(self.results.get(k, {}).get("stars", 0) for k in keys), 5 * len(keys)

    def tracks_for(self, chart, prog_index):
        """Pre-rendered backing (built-in levels only, prog_index None = none) and metronome
        for this chart, on its timeline from -lead_in. Cached per chart and metronome mode."""
        if not self.sounds.ok:
            return {}
        lead_in = lead_in_for_chart(chart)
        total = chart.length + TAIL_S + 0.5
        out = {}
        if chart.song:
            # an imported song: its own stems, stretched to the rate, starting where chart 0 falls in them
            t0 = -chart.song_offset / chart.rate
            for name in ("backing", "drums"):
                key = ("song", chart.song, name, round(chart.rate, 3))
                if key not in self.track_cache:
                    self.track_cache[key] = Track(SG.audio(chart.song, name, chart.rate), t0)
                self.track_cache[key].gain = self.SONG_BASE_GAIN * self.song_gain(name)
                out[name] = self.track_cache[key]
            # the count, 1 . 3 . | 1 2 3 4, its own track so the metronome's mode and level never mute it
            # at the song's own level: its loudest hit COUNT_OVER_DB over the backing's loud moments in the
            # first 30 s (at full scale it came in 10..18 dB over the music, "way too loud", 2026-11-08)
            key = ("count", chart.song, round(chart.bpm, 3))
            if key not in self.track_cache:
                pcm = render_count(chart, lead_in, SONG_COUNT_BEATS, SONG_COUNT)
                backing = out["backing"]
                song_db = SND.loud_db(backing.pcm, -backing.t0, 30.0)
                self.track_cache[key] = Track(pcm, -lead_in)
                self.track_cache[key].count_db = (song_db, SND.loud_db(pcm, pct=100))
            song_db, count_db = self.track_cache[key].count_db
            backing_gain = self.SONG_BASE_GAIN * max(0.25, self.song_gain("backing"))
            self.track_cache[key].gain = min(METRONOME_GAIN, 10 ** ((song_db + self.COUNT_OVER_DB - count_db) / 20) * backing_gain)
            out["count"] = self.track_cache[key]
        elif prog_index is not None:
            # triplet and sextuplet levels get the shuffle arrangement: the music must confirm the subdivision;
            # a level with a style of its own (the cumbias) names it
            sub = chart.subdivision_at(0)
            feel = chart.backing or ("sextuplet" if sub % 6 == 0 else "triplet" if sub % 3 == 0 else "straight")
            key = ("backing", chart.name, round(chart.bpm, 3), prog_index, feel)
            if key not in self.track_cache:
                if feel == "keygen":                  # the waiting screen's tune, one per level (seeded by the name)
                    data = keygen.render_level(chart.bpm, zlib.crc32(chart.name.encode()), lead_in, total)
                else:
                    rhythm = chart.kick_rhythm() if feel == "kick" or chart.hammer else None
                    data = render_backing_track(chart.bpm, prog_index, lead_in, total, feel, rhythm=rhythm)
                self.track_cache[key] = Track(data, -lead_in, BACKING_GAIN)
            out["backing"] = self.track_cache[key]
        if self.metronome_mode != "off":
            key = ("metro", chart.name, round(chart.bpm, 3), self.metronome_mode)
            if key not in self.track_cache:
                data = render_metronome(chart, lead_in, total, self.metronome_mode, count=not chart.song)
                self.track_cache[key] = Track(data, -lead_in, METRONOME_GAIN)
            self.track_cache[key].gain = METRONOME_GAIN * self.metro_volume
            out["metronome"] = self.track_cache[key]
        return out

    # --- menu music ------------------------------------------------------------------
    def update_menu_music(self):
        """Ambient loop in every screen except play. Called on each screen change."""
        if not self.sounds.ok:
            return
        want = self.menu_music_on and not isinstance(self.screen_obj, PlayScreen)
        if want and self.menu_music is None:
            self.menu_music = menu_music_sound()
            self.menu_music.set_volume(SND.MENU_MUSIC_GAIN * self.menu_volume * SND.master())
        ch = pygame.mixer.Channel(MENU_CHANNEL)
        if want:
            if not ch.get_busy():
                ch.play(self.menu_music, loops=-1, fade_ms=600)
        elif self.menu_music is not None and ch.get_busy():
            ch.fadeout(300)

    # --- MIDI ------------------------------------------------------------------
    def open_midi(self, wanted):
        names = mido.get_input_names()
        name = None
        if wanted:
            matches = [n for n in names if wanted.lower() in n.lower()]
            if not matches:
                print(f"No MIDI input matching '{wanted}'. Available: {', '.join(names) or 'none'}")
            else:
                name = matches[0]
        else:
            name = find_module(names)
        if name:
            self.midi_in = mido.open_input(name, callback=self.on_midi)
            self.midi_name = name
            print(f"MIDI input: {name}")
        else:
            print("No MIDI input: keyboard only. Inputs: " + (", ".join(names) or "none"))

    def on_midi(self, msg):
        t_cb = time.perf_counter()
        if msg.type == "control_change":
            self.ghosts.control_change(msg.control, msg.value)
            if msg.control == 4:
                self.runlog.add("cc", control=msg.control, value=msg.value)
        elif msg.type == "note_on" and msg.velocity > 0:
            scr = self.screen_obj
            if scr is None:
                return
            why = self.ghosts.reason(msg.note, msg.velocity)
            if why is not None:
                scr.on_ghost(msg.note, msg.velocity, why)
                self.runlog.add("ghost", note=msg.note, velocity=msg.velocity, why=why)
                result = why
            else:
                self.runlog.add("note", note=msg.note, velocity=msg.velocity, cc=self.ghosts.pedal_cc)
                result = scr.on_note(msg.note, msg.velocity)
            self.debug_hits.append((time.perf_counter(), msg.note, msg.velocity, self.instrument_for(msg.note),
                                    result if isinstance(result, str) else None, why is not None))
            if self.midi_trace is not None:
                # wall clock at the callback, note, velocity, outcome, microseconds spent judging
                self.midi_trace.write(f"{time.time():.6f} {msg.note} {msg.velocity} {result or '-'} "
                                      f"{(time.perf_counter() - t_cb) * 1e6:.0f}\n")
                self.midi_trace.flush()

    def nav_hit(self, note, velocity, raw=False):
        """A drum hit used as a button. Every hit is sounded; every hit above the gate is
        one action, immediately (no debounce: the game must feel instant, and MIDI hits
        never bounce), queued for the main thread. raw: the right crash stays crash2 (the
        profile screens tell the pads apart)."""
        inst = self.instrument_for(note)
        if inst is None or velocity < NAV_SOUND_MIN_VELOCITY:
            return
        self.sounds.play(inst, velocity, 0.8)
        if velocity < NAV_MIN_VELOCITY:
            return
        if inst == "crash2" and not raw:
            inst = "crash"                     # either crash is the "up" / Setup button
        self.legend_flash[inst] = time.perf_counter()
        self.drum_queue.append(inst)

    # --- screens ---------------------------------------------------------------
    # --- devices -------------------------------------------------------------------
    def handle_device_events(self):
        while not self.watcher.events.empty():
            kind, name, connected = self.watcher.events.get_nowait()
            self.on_device(kind, name, connected)

    def controller_port(self, name, connected):
        """The Twister or the Launchpad left or came back (a replug, a USB hub's reset): drop the
        old ports and open them again at once, so the Twister's LEDs and rings and the Launchpad's
        pads are painted whole (a replugged controller comes back dark, and until 2026-11-08 the
        game kept its old ports and never repainted). True when the port was one of them."""
        low = name.lower()
        if TWISTER_HINT in low:
            self.twister.close()
            self.twister_checked = 0.0              # poll_twister reopens it and syncs the rings
            if not connected:
                self.toasts.add("Twister disconnected", DIM, key="twister")
            return True
        if LP.PORT_HINT in low:
            self.launchpad.close()
            self.launchpad_checked = 0.0            # poll_launchpad reopens it and paints every pad
            if not connected:
                self.toasts.add("Launchpad disconnected", DIM, key="launchpad")
            return True
        return False

    def on_device(self, kind, name, connected):
        """A device appeared or went away: toast it and react."""
        ok, bad = JUDGE_COLORS["PERFECT"], JUDGE_COLORS["MISS"]
        if kind == "midi":
            if connected:
                if self.midi_in is None or self.midi_name != name:
                    try:
                        if self.midi_in is not None:
                            self.midi_in.close()
                        self.midi_in = mido.open_input(name, callback=self.on_midi)
                        self.midi_name = name
                    except (OSError, IOError) as e:
                        self.toasts.add(f"{name}: could not open ({e})", bad)
                        return
                self.toasts.add(f"{name} connected: drums are live", ok)
            else:
                if self.midi_in is not None:
                    try:
                        self.midi_in.close()
                    except Exception:                       # noqa: BLE001
                        pass
                self.midi_in, self.midi_name = None, None
                self.toasts.add(f"{name} disconnected: keyboard only", bad)
        elif kind in ("midi-other", "midi-gone") and self.controller_port(name, connected):
            pass
        elif kind == "midi-other":
            self.toasts.add(f"MIDI device connected: {name}", DIM)
        elif kind == "audio-list":
            # CoreAudio's device set changed (a phone mic, a display...): SDL can lose its output
            # when that happens, so reopen the mixer on the device we want
            if self.sounds.ok and not self.recorder.active:
                self.reopen_sounds(self.settings.get("audio_device"))
                self.toasts.add(f"audio devices changed ({name}): mixer reopened", DIM)
        elif kind == "audio":
            if connected:
                self.toasts.add(f"audio output {name} connected", ok)
                if self.settings.get("audio_device") and (self.sounds.device or "") != name:
                    self.set_audio_device(self.settings["audio_device"])
            else:
                self.toasts.add(f"audio output {name} disconnected: sound falls back to the system default", bad)
                if self.sounds.device:
                    self.reopen_sounds(None)
        elif kind == "camera":
            self.camera_name = name if connected else None
            if connected and self.layer_on and self.cam_preview is None:
                self._cam_tried = -1e9                     # the layer was waiting for it: look now, not in CAM_RETRY_S
            self.toasts.add(f"camera {name} {'available for takes' if connected else 'gone'}", ok if connected else DIM)

    def reopen_sounds(self, device):
        """Rebuild the mixer on `device` (None = system default) without touching the saved
        setting. A running level keeps going: its tracks restart from the current time on the
        next update (Track keeps its samples and makes a fresh Sound for the new mixer)."""
        if self.menu_music is not None:
            self.menu_music.stop()
        if isinstance(self.screen_obj, PlayScreen):
            self.screen_obj.game.stop_tracks()
        self.sounds = SoundBank(enabled=not self.args.no_sound, device=device, drums=self.settings.get("drum_sounds", True))
        self.track_cache = {}
        self.menu_music = None
        if isinstance(self.screen_obj, PlayScreen):
            self.screen_obj.game.sounds = self.sounds
        self.update_menu_music()

    def draw_toasts(self):
        live = self.toasts.live()
        if not live:
            return
        S = self.scale
        f = self.fonts
        y = 108 * S                                              # under the titles and the hub strip
        for age, text, color in reversed(live):
            k = 1.0 if age < TOAST_S - 0.8 else max(0.0, (TOAST_S - age) / 0.8)
            slide = 0 if age > 0.25 else (1 - age / 0.25) * 30 * S
            ts = f.text(text, f.small, TEXT)
            w, h = ts.get_width() + 44 * S, ts.get_height() + 14 * S
            x = self.size[0] - w - 16 * S + slide
            box = pygame.Surface((int(w), int(h)), pygame.SRCALPHA)
            pygame.draw.rect(box, (*LANE_BG, int(235 * k)), box.get_rect(), border_radius=int(9 * S))
            pygame.draw.rect(box, (*color, int(255 * k)), box.get_rect(), max(1, int(2 * S)), border_radius=int(9 * S))
            pygame.draw.circle(box, (*color, int(255 * k)), (int(16 * S), int(h / 2)), int(5 * S))
            ts.set_alpha(int(255 * k))
            box.blit(ts, (30 * S, 7 * S))
            self.surface.blit(box, (x, y))
            y += h + 8 * S

    def device_line(self):
        """One line for the hub: the three devices with their state."""
        w = self.watcher.state
        midi = f"MIDI {self.midi_name}" if self.midi_in else "TD-17 not connected"
        audio = f"audio {self.sounds.device}" if self.sounds.device else ("audio system default" if self.sounds.ok else "no audio")
        cam = f"camera {w['camera']}" if w.get("camera") else "no camera"
        return midi, audio, cam

    def level_by_name(self, name):
        """(category, index, lead) for a level name or its other side's key ("Paradiddle (L)",
        "5 · Washing the crash (R)")."""
        for cat in self.categories():
            for i, ch in enumerate(self.items_for(cat)):
                if ch.name == name:
                    return cat, i, ch.lead or "R"
                if ch.lead and ch.mirrored().key == name:
                    return cat, i, ch.mirrored().lead
        return None

    def chart_for(self, cat, index):
        """The level to play: the mirror (the left-hand lead, the wash on the other crash) when
        the chosen side is not the one the level was written on."""
        ch = self.items_for(cat)[index]
        return ch.mirrored() if ch.lead and self.lead != ch.lead else ch

    def level_names(self):
        return {ch.name for cat in self.categories() for ch in self.items_for(cat)}

    def stats_summary(self):
        """Cached for a second: the hub draws it every frame."""
        now = time.perf_counter()
        if getattr(self, "_summary_at", 0) < now - 1.0:
            self._summary = ST.summary(profile=self.profile_id)
            self._summary_at = now
        return self._summary

    def coach_report(self):
        levels = {"exercises": self.items_for("kick"), "beats": self.items_for("snare")}
        for c in COURSES:
            levels[f"course: {c.name}"] = c.levels
        return ST.report(levels, profile=self.profile_id)

    # --- profiles ------------------------------------------------------------------
    @property
    def profile_id(self):
        return self.profile["id"] if self.profile else None

    def set_profile(self, profile):
        """Log a profile in (None: out): its best results replace the loaded ones, the stats
        and the coach read its runs from here on."""
        self.profile = profile
        self.results = load_progress(PR.progress_path(profile))
        self.tries = {k: v.get("tries", 0) for k, v in self.results.items()}   # attempts under five stars in a row, per level
        self._summary_at = 0
        # the coach's report is about one player's runs: the owner keeps the old folder
        self.coach.dir = CO.COACH_DIR if profile is None or profile["id"] == PR.MAIN else os.path.join(CO.COACH_DIR, profile["id"])
        self.coach.result = CO.load_coach(os.path.join(self.coach.dir, "coach.json"))
        self.session = None

    def save_results(self):
        save_progress(self.results, PR.progress_path(self.profile))

    def save_profiles(self):
        PR.save_profiles(self.profiles)

    def home(self, sel=0):
        """The screen the game opens on: the login when there are profiles and nobody is in."""
        return LoginScreen(self) if self.profiles and self.profile is None else HubScreen(self, sel)

    def start_session(self, playlist):
        items = []
        for it in playlist.get("items", []):
            found = self.level_by_name(it.get("level"))
            if found:
                items.append((found[0], found[1], float(it.get("rate", 1.0)), max(1, int(it.get("reps", 1))), it.get("why", ""), found[2]))
        if not items:
            return False
        self.session = {"name": playlist.get("name", "session"), "items": items, "pos": 0, "rep": 1}
        self.session_go()
        return True

    def session_go(self):
        cat, index, rate, reps, why, lead = self.session["items"][self.session["pos"]]
        self.rate, self.lead = rate, lead
        self.go(PlayScreen(self, cat, index))

    def session_advance(self):
        """Called when a level of a session ends and the player continues. True if it moved on."""
        ss = self.session
        if ss is None:
            return False
        cat, index, rate, reps, why, lead = ss["items"][ss["pos"]]
        if ss["rep"] < reps:
            ss["rep"] += 1
        elif ss["pos"] + 1 < len(ss["items"]):
            ss["pos"] += 1; ss["rep"] = 1
        else:
            self.session = None
            self.rate = 1.0
            self.go(CoachScreen(self, done=ss["name"]))
            return True
        self.session_go()
        return True

    def toggle_recording(self):
        if self.recorder.active:
            path = self.recorder.stop()
            self.watcher.paused = False
            self.toasts.add(f"take stopped: {os.path.basename(path)}, rendering both editions", DIM)
            print(f"recording stopped, finishing {path}")
        else:
            self.close_cam_monitor()                      # the camera goes to the recorder; ! reopens it after
            name = self.screen_obj.chart.key if isinstance(self.screen_obj, PlayScreen) else "take"
            self.watcher.paused = True                     # the camera is ffmpeg's now
            if self.recorder.start(self.size, name):
                self.toasts.add("recording" + (f" with camera {self.camera_name}" if self.camera_name else ", no camera"), (235, 70, 70))
                print("recording started")
            else:
                self.watcher.paused = False
                self.toasts.add(f"recording could not start: {self.recorder.error}", JUDGE_COLORS["MISS"])
                print(f"recording could not start: {self.recorder.error}")

    def draw_debug(self):
        """The ` pane: the last hits' velocities as bars against the accent / tap thresholds
        in force, the last few as text, the pedal CC. Drawn over any screen."""
        if not self.debug_on:
            return
        S, f = self.scale, self.fonts
        game = self.screen_obj.game if isinstance(self.screen_obj, PlayScreen) else None
        night = game.night if game is not None else GM.is_night()
        scales = self.dyn_scales
        band_of = lambda inst: GM.dyn_band(inst, night, scales.get(inst, 1.0))
        hits = list(self.debug_hits)
        target = self.dyn_target()              # the body ; and ' move: its bars lit, the others dim
        now = time.perf_counter()
        w, h = 470 * S, 232 * S
        x0, y0 = 16 * S, self.size[1] - h - 16 * S
        pane = pygame.Surface((int(w), int(h)), pygame.SRCALPHA)
        pane.fill((*LANE_BG, 225))
        self.surface.blit(pane, (x0, y0))
        pygame.draw.rect(self.surface, DIM, (x0, y0, w, h), 1)
        a_min, t_max = band_of(target)
        head = f"velocity  cc {self.ghosts.pedal_cc}  " + (
            f"; ' {target} {scales.get(target, 1.0):.0%}: {a_min}/{t_max}" if target else f"thresholds {a_min}/{t_max}"
        ) + ("  night" if night else "")
        self.surface.blit(f.text(head, f.small, DIM), (x0 + 12 * S, y0 + 8 * S))
        # bars: the last DEBUG_BARS hits, newest at the right, 0..127 tall; ghosts hollow
        gx, gy, gw, gh = x0 + 12 * S, y0 + 34 * S, w - 130 * S, 96 * S     # room for the big number at the right
        pygame.draw.rect(self.surface, BG, (gx, gy, gw, gh))
        for v, col in ((a_min, JUDGE_COLORS["PERFECT"]), (t_max, ACCENT)):
            yy = gy + gh * (1 - v / 127)
            pygame.draw.line(self.surface, lerp(col, BG, 0.5), (gx, yy), (gx + gw, yy), 1)
        bars = hits[-DEBUG_BARS:]
        bw = gw / DEBUG_BARS
        for i, (t, note, vel, inst, res, ghost) in enumerate(bars):
            bh = gh * vel / 127
            bx = gx + gw - (len(bars) - i) * bw
            band = band_of(inst)
            col = JUDGE_COLORS["MISS"] if ghost else JUDGE_COLORS["PERFECT"] if vel >= band[0] else ACCENT if vel <= band[1] else DIM
            if target and inst != target and not ghost:
                col = lerp(col, BG, 0.6)                # another body: dim, so the target's strokes stand out
            rect = (bx + 1, gy + gh - bh, max(1, bw - 2), bh)
            pygame.draw.rect(self.surface, col, rect, 1 if ghost else 0)
        # the last hit, big, and the last few as text
        if hits:
            t, note, vel, inst, res, ghost = hits[-1]
            ts = f.text(str(vel), f.big, JUDGE_COLORS["MISS"] if ghost else TEXT)
            self.surface.blit(ts, (x0 + w - 12 * S - ts.get_width(), gy + gh / 2 - ts.get_height() / 2 - 10 * S))
            ns = f.text(f"{note} {note_name(note)}", f.small, DIM)   # the note: number and name
            self.surface.blit(ns, (x0 + w - 12 * S - ns.get_width(), gy + gh - ns.get_height() - 2 * S))
        y = y0 + 138 * S
        for t, note, vel, inst, res, ghost in reversed(hits[-4:]):
            age = now - t
            line = f"{age:5.1f}s  {note:3d} {note_name(note):<4} {inst or '?':7} {vel:3d}  {'ghost: ' + res if ghost else (res or '')}"
            self.surface.blit(f.text(line[:56], f.small, DIM if age > 2 else TEXT), (x0 + 12 * S, y))
            y += 22 * S

    # --- the ! layer: the camera as the picture-in-picture, the chat ---------------------
    def set_layer(self, on, auto=False):
        """! : the streamer's layer over any screen: the camera where the computer edition puts
        it (capture_pip of the height in capture_corner, 30 fps) and the channel's chat pane.
        Independent of the stream (it is how the picture is checked before going live), but
        going live turns it on (auto=True): that is how the iPhone gets into the stream, which
        is the display; and the stream's end turns an auto layer off again, so the camera is
        released (Continuity Camera stayed "connected" after a stream, 2026-09-13). A layer the
        user switched with ! is theirs: the stream leaves it alone."""
        if on == self.layer_on:
            if not auto:
                self.layer_auto = False
            return
        self.layer_on = on
        self.layer_auto = auto and on
        if on:
            if self.chat is None:
                self.chat = Chat(self.streamer.settings["twitch_channel"], log=print)
        else:
            self.close_cam_monitor()
            if self.chat is not None:
                self.chat.stop()
                self.chat = None
        print(f"layer {'on' if on else 'off'}")

    def close_cam_monitor(self):
        """Release our camera preview (the recorder or the camera check screen takes the camera)."""
        if self.cam_preview is not None:
            self.cam_preview.stop()
            self.cam_preview = None
        self._cam_wanted = False

    def open_cam_preview(self, fps):
        """Open our camera preview in a thread: finding the camera spawns `ffmpeg -list_devices`
        (0.7 s, 15 s when AVFoundation stalls) and used to run on the main thread, every frame
        while no camera was found (2026-09-12: the game froze at "waiting for frames" during a
        stream). A miss is retried after CAM_RETRY_S."""
        now = time.perf_counter()
        self._cam_wanted = True
        if self._cam_opening is not None or now - self._cam_tried < CAM_RETRY_S:
            return
        self._cam_tried = now

        def work():
            preview = None
            try:
                cam = CP.find_camera(self.recorder.settings["capture_camera"])
                if cam and CP.ffmpeg_path():
                    preview = CP.CameraPreview(cam, fps=fps)
            finally:
                if preview is not None and (not self._cam_wanted or self.recorder.active or self.cam_preview is not None):
                    preview.stop()                # nobody wants it any more (layer closed, a take took the camera)
                elif preview is not None:
                    self.cam_preview = preview
                    self._cam_cache = (None, None)
                self._cam_opening = None

        self._cam_opening = threading.Thread(target=work, daemon=True)
        self._cam_opening.start()

    def pip_rect(self):
        """Where the layer draws the camera: the computer edition's picture-in-picture."""
        return CP.pip_rect(self.size, self.recorder.settings["capture_pip"], self.recorder.settings["capture_corner"],
                           margin=int(16 * self.scale))

    def draw_cam_monitor(self):
        """The layer's camera: what the camera sees, as the computer edition's picture-in-picture,
        over any screen. While a take runs it shows the recorder's own frames (the ones going
        into the take); otherwise it opens a preview of its own. The camera check screen has its
        own picture."""
        if not self.layer_on or isinstance(self.screen_obj, CameraCheckScreen):
            self.close_cam_monitor()
            return
        S, f = self.scale, self.fonts
        x0, y0, pw, ph = self.pip_rect()
        live = self.streamer.active
        if self.recorder.active and self.recorder.feed is not None:
            self.close_cam_monitor()
            src, frame, size, count = self.recorder.feed, self.recorder.feed.frame, CP.CAMERA_SIZE, self.recorder.feed.frames
            error = self.recorder.feed.error
        else:
            if self.cam_preview is None and not self.recorder.active:
                self.open_cam_preview(CP.CAMERA_FPS)
            p = self.cam_preview
            src, frame, size, count = p, (p.frame if p else None), CP.PREVIEW_SIZE, (p.frames if p else 0)
            error = (p.error if p else f"no camera '{self.recorder.settings['capture_camera']}'")
        key = (id(src), count)
        if frame is not None and self._cam_cache[0] != key:
            pic = pygame.image.frombuffer(frame, size, "RGB")
            self._cam_cache = (key, pygame.transform.scale(pic, (int(pw), int(ph))))
        pic = self._cam_cache[1] if self._cam_cache[0] == key else None
        if pic is not None:
            self.surface.blit(pic, (x0, y0))
        else:
            pygame.draw.rect(self.surface, LANE_BG, (x0, y0, pw, ph))
            self.fonts.center(self.surface, error or "waiting for frames...", f.small, DIM, y0 + ph / 2 - 8 * S, x0 + pw / 2)
        pygame.draw.rect(self.surface, (235, 70, 70) if self.recorder.active else (145, 70, 255) if live else DIM, (x0, y0, pw, ph), 1)

    def draw_chat(self):
        """The layer's chat pane: the last messages of the channel that fit, name in the user's
        Twitch colour. Bottom left, the velocity viewer's place (above it when both are on);
        bottom right when the camera has the bottom left corner."""
        chat = self.chat
        if chat is None or not self.layer_on or isinstance(self.screen_obj, CameraCheckScreen):
            return
        S, f = self.scale, self.fonts
        w, h = 470 * S, 232 * S
        x0 = 16 * S if self.recorder.settings["capture_corner"] != "bl" else self.size[0] - w - 16 * S
        y0 = self.size[1] - h - 16 * S - ((232 + 12) * S if self.debug_on and x0 < self.size[0] / 2 else 0)
        pane = pygame.Surface((int(w), int(h)), pygame.SRCALPHA)
        pane.fill((*LANE_BG, 225))
        self.surface.blit(pane, (x0, y0))
        pygame.draw.rect(self.surface, DIM, (x0, y0, w, h), 1)
        head = f"chat  #{chat.channel}" + ("" if chat.connected else f"  ({chat.error or 'connecting'})")
        self.surface.blit(f.text(head, f.small, (145, 70, 255) if chat.connected else DIM), (x0 + 12 * S, y0 + 8 * S))
        with chat._lock:
            msgs = list(chat.messages)
        lines = []                                    # (name, colour, text) per wrapped line, newest last; name only on the first
        max_w = w - 24 * S
        for t, name, text, colour in msgs[-24:]:
            colour = colour or TEXT
            prefix = f"{name}: "
            pw = f.text(prefix, f.small, colour).get_width()
            words = text.split(" ")
            row, avail = "", max_w - pw
            first = True
            for word in words:
                trial = (row + " " + word).strip()
                if row and f.text(trial, f.small, TEXT).get_width() > avail:
                    lines.append((prefix if first else "", colour, row))
                    first, row, avail = False, word, max_w
                else:
                    row = trial
            lines.append((prefix if first else "", colour, row))
        lh = f.text("Ag", f.small, TEXT).get_height() + 2 * S
        y = y0 + h - 10 * S - lh
        top = y0 + 30 * S
        for prefix, colour, row in reversed(lines):
            if y < top:
                break
            x = x0 + 12 * S
            if prefix:
                ps = f.text(prefix, f.small, colour)
                self.surface.blit(ps, (x, y)); x += ps.get_width()
            self.surface.blit(f.text(row, f.small, TEXT), (x, y))
            y -= lh

    # --- the stream: T, the badge, the daemon ---------------------------------------------
    def toggle_stream(self):
        """T (and the badge's x): the stream starts and stops here and nowhere else (never on its own).
        Starting spawns the stream process; the badge says STARTING until ffmpeg reports, then LIVE."""
        st = self.streamer
        if st.phase == "stopping":
            return
        if st.active:
            self.stop_stream()
        elif st.start(self.size):
            self.set_layer(True, auto=True)            # the camera into the stream; ! hides it again
            what = (f"display {st.settings['stream_display']}" if st.source == "screen" else "%dx%d window" % st.out_size)
            self.toasts.add(f"starting the stream to twitch.tv/{st.settings['twitch_channel']}: {what}, {st.settings['stream_kbps']} kbps"
                            + (" (bandwidth test, not public)" if st.settings.get("stream_bandwidth_test") else ""), (145, 70, 255))
        else:
            self.toasts.add(f"stream could not start: {st.error}", JUDGE_COLORS["MISS"])
            print(f"stream could not start: {st.error}")

    def stop_stream(self):
        self.streamer.stop()
        if self.streamer.phase == "stopping":
            self.toasts.add("stopping the stream", DIM)

    def watch_stream(self):
        """Every frame: the stream process's state, and its transitions as toasts (live at last,
        stopped, ended on its own with the reason, found live when the game started)."""
        st = self.streamer
        st.refresh()
        phase = st.phase
        if phase == self.stream_phase:
            return
        was, self.stream_phase = self.stream_phase, phase
        if phase == "live":
            if was == "off" and st.attached:
                st.attached = False
                since = time.strftime("%H:%M", time.localtime(st.started_wall or time.time()))
                self.toasts.add(f"stream live since {since} on twitch.tv/{st.settings['twitch_channel']} (the stream runs on its own; T or the x stop it)", (145, 70, 255))
                self.set_layer(True, auto=True)
            elif was == "starting":
                self.toasts.add(f"live on twitch.tv/{st.settings['twitch_channel']}", (235, 70, 70))
        elif phase == "off":
            if st.ended_reason:
                self.toasts.add(f"stream ended: {st.ended_reason}", JUDGE_COLORS["MISS"])
                st.ended_reason = None
            else:
                self.toasts.add("stream stopped", DIM)
            if self.layer_auto:
                self.set_layer(False)                  # the camera and the chat came with the stream: they go with it

    def badge_height(self):
        """The room the play HUD leaves top right for the stream's LIVE badge (the daemon's own
        floating window, badge.py, sits over that corner while the stream lives), else 0."""
        return int(26 * self.scale) if self.streamer.phase != "off" else 0

    def draw_recording_status(self):
        status = self.recorder.status
        rec = self.recorder.active
        if not status and (self.editor.busy or self.editor.status):
            status = self.editor.status
            if self.editor.busy:
                status += " ." * (int(time.perf_counter()) % 4)
        if not status and self.coach.busy:
            status = self.coach.status + " ." * (int(time.perf_counter()) % 4)
        if not status:
            return
        S = self.scale
        f = self.fonts
        color = (235, 70, 70) if rec else (JUDGE_COLORS["MISS"] if (self.recorder.error or self.editor.error) and not (self.editor.busy or self.coach.busy) else DIM)
        ts = f.text(status, f.small, color)
        x = self.size[0] - ts.get_width() - 16 * S
        y = self.size[1] - ts.get_height() - 8 * S
        if rec and int(time.perf_counter() * 2) % 2 == 0:
            pygame.draw.circle(self.surface, color, (int(x - 12 * S), int(y + ts.get_height() / 2)), int(5 * S))
        self.surface.blit(ts, (x, y))

    def _watchdog(self):
        """A frame that takes longer than STALL_S: the main thread's stack goes to the log, once per
        stall, so a frozen game explains itself (2026-09-12: froze during a stream, no trace)."""
        import traceback
        main_id = threading.main_thread().ident
        reported = None
        while True:
            time.sleep(0.5)
            at = self.frame_at
            stalled = time.perf_counter() - at
            if stalled < STALL_S or reported == at:
                continue
            reported = at
            frame = sys._current_frames().get(main_id)
            stack = "".join(traceback.format_stack(frame)) if frame is not None else "(no frame)"
            print(f"main loop stalled {stalled:.1f} s, main thread at:\n{stack}")

    def go(self, screen):
        self.screen_obj = screen
        self.update_menu_music()

    def run(self):
        self.go(SetupScreen(self, first_run=True) if self.first_run and self.midi_in else self.home())
        clock = pygame.time.Clock()
        running = True
        self.frame_at = time.perf_counter()
        threading.Thread(target=self._watchdog, daemon=True).start()
        while running:
            self.frame_at = time.perf_counter()
            for ev in pygame.event.get():
                if ev.type == pygame.QUIT:
                    running = False
                elif ev.type in (pygame.VIDEORESIZE, pygame.WINDOWSIZECHANGED):
                    self.on_resize()
                elif ev.type in (pygame.MOUSEBUTTONDOWN, pygame.MOUSEBUTTONUP, pygame.MOUSEMOTION, pygame.MOUSEWHEEL):
                    self.screen_obj.on_mouse(ev)
                elif ev.type == pygame.KEYDOWN:
                    if ev.key == pygame.K_F11 or (ev.key == pygame.K_f and ev.mod & (pygame.KMOD_META | pygame.KMOD_CTRL)):
                        self.toggle_fullscreen()
                    elif self.screen_obj.typing:
                        # a name is being typed: every key is text (or the screen's), none of the shortcuts
                        if ev.unicode and ev.unicode.isprintable() and not ev.mod & (pygame.KMOD_META | pygame.KMOD_CTRL):
                            self.screen_obj.on_text(ev.unicode)
                        elif self.screen_obj.on_key(ev.key) is False:
                            running = False
                    elif ev.key == pygame.K_v:
                        self.toggle_recording()
                    elif ev.key == pygame.K_t:
                        self.toggle_stream()
                    elif ev.unicode == "{" or (ev.key == pygame.K_LEFTBRACKET and ev.mod & pygame.KMOD_SHIFT):
                        self.nudge_volume(-1)              # the plain brackets belong to the screens (tempo, corner)
                    elif ev.unicode == "}" or (ev.key == pygame.K_RIGHTBRACKET and ev.mod & pygame.KMOD_SHIFT):
                        self.nudge_volume(+1)
                    elif ev.unicode == "`" or ev.key == pygame.K_BACKQUOTE:
                        self.debug_on = not self.debug_on
                    elif ev.unicode == "!" or (ev.key == pygame.K_1 and ev.mod & pygame.KMOD_SHIFT):
                        self.set_layer(not self.layer_on)
                    elif ev.unicode in ("-", "_") or ev.key in (pygame.K_MINUS, pygame.K_KP_MINUS):
                        self.nudge_metro_volume(-1)        # the metronome's own level, so it is not lost under the music
                    elif ev.unicode in ("=", "+") or ev.key in (pygame.K_EQUALS, pygame.K_PLUS, pygame.K_KP_PLUS):
                        self.nudge_metro_volume(+1)
                    elif ev.unicode == ";" or ev.key == pygame.K_SEMICOLON:
                        self.nudge_dyn_scale(-1)           # softer accents count
                    elif ev.unicode == "'" or ev.key == pygame.K_QUOTE:
                        self.nudge_dyn_scale(+1)
                    else:
                        key = ev.key
                        if ev.unicode == "\\":
                            key = pygame.K_BACKSLASH     # layouts where the backslash needs a modifier
                        if self.screen_obj.on_key(key) is False:
                            running = False
            while self.drum_queue:
                if self.screen_obj.on_drum(self.drum_queue.popleft()) is False:
                    running = False
            self.handle_device_events()
            self.poll_twister()
            self.poll_launchpad()
            self.poll_importer()
            self.adopt_sounds()
            self.screen_obj.update()
            self.screen_obj.draw(self.surface, clock.get_fps())
            self.recorder.push(self.surface)              # a copy 30 times a second while recording
            self.draw_recording_status()
            self.draw_toasts()
            self.draw_debug()
            self.draw_chat()                              # the ! layer, after the recorder's push: never in the take
            self.draw_cam_monitor()
            self.streamer.push(self.surface)              # window source only: the stream is the window, every overlay included
            self.watch_stream()
            pygame.display.flip()
            clock.tick(TARGET_FPS)
        if self.midi_in:
            self.midi_in.close()
        if self.twister_bank == LP.LENT_BANK:
            self.launchpad.show(LP.blank())               # ours until bank 1: dark, not a frozen level
        self.launchpad.close()
        self.watcher.stop()
        self.set_layer(False)
        if self.recorder.active:
            self.recorder.stop()
        self.streamer.close()                             # the stream process stays live; T in the next game stops it
        if self.recorder.composing is not None:
            print("finishing the take...")
            self.recorder.composing[0].join(timeout=300)
        pygame.quit()


# ---------------------------------------------------------------------------
class Screen:
    typing = False       # True: the keyboard types into the screen (on_text) and the global shortcuts sleep

    def __init__(self, app: App):
        self.app = app

    # window geometry is read live so a resize or fullscreen toggle relayouts every screen
    @property
    def f(self):
        return self.app.fonts

    @property
    def w(self):
        return self.app.size[0]

    @property
    def h(self):
        return self.app.size[1]

    @property
    def s(self):
        return self.app.scale

    def on_resize(self):
        pass

    def on_note(self, note, velocity):
        """MIDI thread. Default: the hit is a button press."""
        self.app.nav_hit(note, velocity)

    def on_ghost(self, note, velocity, why):
        """MIDI thread. A note the ghost filter dropped; screens may show it."""
        pass

    def on_drum(self, inst):
        """Main thread. Return False to quit."""
        return True

    def on_key(self, key):
        """Return False to quit."""
        return True

    def on_mouse(self, ev):
        """A mouse event (the song editor's)."""
        pass

    def on_text(self, ch):
        """A printable character, while `typing`."""
        pass

    def update(self):
        pass

    def draw(self, surf, fps):
        pass

    # --- shared widgets ----------------------------------------------------------
    def legend(self, surf, pairs, y=None, keys=None):
        """Colored drum chips: [(instrument, action label), ...]. Flash when that drum is hit."""
        S = self.s
        y = self.h - 44 * S if y is None else y
        now = time.perf_counter()
        chips = []
        for inst, label in pairs:
            ts = self.f.text(f"{C.LABELS[inst]}  {label}", self.f.small, TEXT)
            chips.append((inst, ts))
        gap, pad, r = 26 * S, 12 * S, 8 * S
        total = sum(ts.get_width() + 2 * r + pad for _, ts in chips) + gap * (len(chips) - 1)
        x = self.w / 2 - total / 2
        for inst, ts in chips:
            color = C.COLORS[inst]
            k = max(0.0, 1 - (now - self.app.legend_flash.get(inst, 0)) / 0.25)
            w = ts.get_width() + 2 * r + pad
            box = pygame.Rect(int(x - 10 * S), int(y - 14 * S), int(w + 20 * S), int(28 * S))
            pygame.draw.rect(surf, lerp(LANE_BG, color, 0.6 * k), box, border_radius=int(14 * S))
            pygame.draw.rect(surf, color if k > 0 else (50, 50, 60), box, 1, border_radius=int(14 * S))
            pygame.draw.circle(surf, color, (int(x + r), int(y)), int(r + 3 * k * S))
            surf.blit(ts, (x + 2 * r + pad - 4, y - ts.get_height() / 2))
            x += w + gap
        if keys:
            self.f.center(surf, keys, self.f.small, DIM, y + 26 * S)

    def midi_line(self):
        return f"MIDI: {self.app.midi_name}" if self.app.midi_in else "no MIDI input, keyboard only"


# ---------------------------------------------------------------------------
class HubScreen(Screen):
    """Five colored sections, one per drum (the rack tom opens the songs), three on top and two
    under them. Strike the drum to open its section."""

    def __init__(self, app, sel=0):
        super().__init__(app)
        self.sel = sel
        self.flash = None          # (wall_t, category index)

    def open(self, i):
        cat = CATEGORIES[i][0]
        self.flash = (time.perf_counter(), i)
        if cat == "hihat":
            self.app.go(CoursesScreen(self.app))
        elif cat == "tom1":
            self.app.refresh_songs()
            self.app.go(SongsScreen(self.app))
        else:
            self.app.go(ListScreen(self.app, cat))

    def on_drum(self, inst):
        for i, (cat, _, _) in enumerate(CATEGORIES):
            if cat == inst:
                self.open(i)
        return True

    def on_key(self, key):
        if key in (pygame.K_ESCAPE, pygame.K_q):
            return False
        if pygame.K_1 <= key < pygame.K_1 + len(CATEGORIES):
            self.open(key - pygame.K_1)
        elif key in (pygame.K_LEFT, pygame.K_h):
            self.sel = (self.sel - 1) % len(CATEGORIES)
        elif key in (pygame.K_RIGHT, pygame.K_l):
            self.sel = (self.sel + 1) % len(CATEGORIES)
        elif key in (pygame.K_UP, pygame.K_DOWN, pygame.K_k, pygame.K_j):
            # the other row, under / over the card's middle
            r = 0 if self.sel in HUB_ROWS[0] else 1
            frac = (HUB_ROWS[r].index(self.sel) + 0.5) / len(HUB_ROWS[r])
            other = HUB_ROWS[1 - r]
            self.sel = other[min(len(other) - 1, int(frac * len(other)))]
        elif key in (pygame.K_RETURN, pygame.K_SPACE):
            self.open(self.sel)
        elif key == pygame.K_s:
            self.app.go(StatsScreen(self.app))
        elif key == pygame.K_c:
            self.app.go(CoachScreen(self.app))
        return True

    def draw(self, surf, fps):
        surf.fill(BG)
        S = self.s
        self.f.center(surf, "bathero", self.f.big, TEXT, 52 * S)
        sm = self.app.stats_summary()
        strip = (f"streak {sm['streak']} day{'s' if sm['streak'] != 1 else ''}  ·  today {sm['today_minutes']:.0f} min"
                 f"  ·  {sm['total_minutes']:.0f} min in {sm['days']} days  ·  S progress  ·  C coach")
        self.f.center(surf, strip, self.f.small, ACCENT if sm["streak"] else DIM, 92 * S)
        if self.app.profile:                                  # who is playing, top left
            p = self.app.profile
            PR.draw_icon(surf, p["pad"], 60 * S, 52 * S, 20 * S, bg=BG)
            surf.blit(self.f.text(p["name"], self.f.mid, C.COLORS[p["pad"]]), (90 * S, 36 * S))
            stars = sum(v.get("stars", 0) for v in self.app.results.values())
            surf.blit(self.f.text(f"★ {stars}  ·  Setup ▸ Profile to log out", self.f.small, DIM), (90 * S, 66 * S))
        gap, top, bottom, side = 18 * S, 118 * S, self.h - 70 * S, 60 * S
        ph = (bottom - top - gap) / 2
        now = time.perf_counter()
        for i, (cat, title, sub) in enumerate(CATEGORIES):
            row = 0 if i in HUB_ROWS[0] else 1
            cols = HUB_ROWS[row]
            pw = (self.w - 2 * side - gap * (len(cols) - 1)) / len(cols)
            x, y = side + cols.index(i) * (pw + gap), top + row * (ph + gap)
            color = C.COLORS[cat]
            hot = max(0.0, 1 - (now - self.app.legend_flash.get(cat, 0)) / 0.3)
            fill = lerp(lerp(LANE_BG, color, 0.14), color, 0.5 * hot)
            rect = pygame.Rect(int(x), int(y), int(pw), int(ph))
            pygame.draw.rect(surf, fill, rect, border_radius=int(18 * S))
            pygame.draw.rect(surf, color if (i == self.sel or hot) else lerp(LANE_BG, color, 0.5), rect,
                             3 if i == self.sel else 2, border_radius=int(18 * S))
            # drum chip
            pygame.draw.circle(surf, color, (int(x + 34 * S), int(y + 34 * S)), int(12 * S))
            surf.blit(self.f.text(f"{C.LABELS[cat]}", self.f.mid, color), (x + 56 * S, y + 20 * S))
            surf.blit(self.f.text(f"key {i + 1}", self.f.small, DIM), (x + pw - 70 * S, y + 24 * S))
            room = pw - 28 * S                     # three cards a row: a long title or subtitle must fit
            tf = self.f.big if self.f.text(title, self.f.big, TEXT).get_width() <= room else self.f.mid
            self.f.center(surf, title, tf, TEXT, y + ph / 2 - 6 * S, x + pw / 2)
            for k, line in enumerate(wrap(self.f, sub, self.f.small, room)[:2]):
                self.f.center(surf, line, self.f.small, DIM, y + ph / 2 + (36 + 20 * k) * S, x + pw / 2)
            if cat in ("kick", "snare"):
                items = self.app.items_for(cat)
                keys = [ch.key for ch in items] + [ch.mirrored().key for ch in items if ch.lead]   # both leads count
                got = sum(self.app.results.get(k, {}).get("stars", 0) for k in keys)
                self.f.center(surf, f"{len(items)} levels  ·  ★ {got} / {5 * len(keys)}", self.f.small, color, y + ph - 26 * S, x + pw / 2)
            elif cat == "hihat":
                got, total = (sum(v) for v in zip(*(self.app.course_stars(c) for c in COURSES)))
                self.f.center(surf, f"{len(COURSES)} course{'s' if len(COURSES) != 1 else ''}  ·  ★ {got} / {total}", self.f.small, color, y + ph - 26 * S, x + pw / 2)
            elif cat == "tom1":
                sc = C.COURSE.get(SG.SONG_COURSE)
                if sc and sc.levels:
                    got, total = self.app.course_stars(sc)
                    line = f"{len(sc.levels)} song{'s' if len(sc.levels) != 1 else ''}  ·  ★ {got} / {total}"
                else:
                    line = "none yet: ~/Music/bathero/inbox"
                if self.app.importer.status().get("current"):
                    line += "  ·  importing"
                self.f.center(surf, line, self.f.small, color, y + ph - 26 * S, x + pw / 2)
            else:
                self.f.center(surf, describe(self.app.kit), self.f.small, color, y + ph - 26 * S, x + pw / 2)
            if not self.app.has_drum(cat) and self.app.midi_in:
                self.f.center(surf, "no pad assigned", self.f.small, JUDGE_COLORS["MISS"], y + 60 * S, x + pw / 2)
        midi, audio, cam = self.app.device_line()
        chips = [(midi, bool(self.app.midi_in)), (audio, bool(self.app.sounds.ok)), (cam, bool(self.app.watcher.state.get("camera")))]
        total = sum(self.f.text(t, self.f.small, DIM).get_width() + 34 * S for t, _ in chips)
        x = self.w / 2 - total / 2
        for text, on in chips:
            pygame.draw.circle(surf, JUDGE_COLORS["PERFECT"] if on else (80, 80, 90), (int(x + 6 * S), int(self.h - 44 * S)), int(5 * S))
            ts = self.f.text(text, self.f.small, TEXT if on else DIM)
            surf.blit(ts, (x + 18 * S, self.h - 44 * S - ts.get_height() / 2))
            x += ts.get_width() + 34 * S
        self.f.center(surf, "keys 1-5, arrows or hjkl + Enter   ·   F11 fullscreen   ·   Esc quit", self.f.small, DIM, self.h - 20 * S)


# ---------------------------------------------------------------------------
class ListScreen(Screen):
    """A section's items. Hi-hat down, crash up, snare accept, kick back."""

    def __init__(self, app, cat, sel=0):
        super().__init__(app)
        self.cat = cat
        self.course = C.COURSE.get(cat)
        if self.course:
            got, total = app.course_stars(self.course)
            self.title, self.sub = self.course.name, f"{len(self.course.levels)} levels  ·  ★ {got} / {total}"
            self.color = C.COLORS["tom1" if cat == SG.SONG_COURSE else "hihat"]
        else:
            self.title, self.sub = next((t, s) for c, t, s in CATEGORIES if c == cat)
            self.color = C.COLORS[cat]
        self.sel = sel

    def items(self):
        if self.cat == "crash":
            p = self.app.profile
            return [((f"Profile: {p['name']}" if p else "Profiles: none"),
                     ("log out, switch, new profile · each profile logs in by hitting its pad" if self.app.profiles
                      else "one shared progress until a profile exists · new profile: a name, and the pad whose icon you want; "
                           "from then on each player logs in by hitting their pad")),
                    ("Set up kit", describe(self.app.kit)),
                    ("Soundcheck", "hit every pad, see where it lands and hear it"),
                    (f"Drum sounds: {'on' if self.app.sounds.drums else 'off'}", "off: the kit is silent here, the module or Bitwig makes the sound"),
                    (f"Guide sounds: {'on' if self.app.guide else 'off'}", "hear the chart as it crosses the line"),
                    (f"Level intro (S): {'on' if self.app.intro_on else 'off'}", "before each level, what it teaches (in Spanish) until a snare hit starts it; "
                                                                             "S toggles it in the lists and in play, saved"),
                    (f"Scroll speed (D): {'fixed' if self.app.scroll_fixed else 'follows tempo'}", "fixed: the notes fall at the same pixels per second at every tempo, "
                                                                                                   "so an error is always the same distance; D toggles it in the lists and in play, saved"),
                    (f"Backing loop: {'on' if self.app.backing_on else 'off'}", "bass, chords and arpeggio under the built-in levels"),
                    (f"Metronome: {self.app.metronome_mode} · {self.app.metro_volume:.0%}", "congas: full follows the subdivision, beats only marks the beats; "
                                                                                            "= and - raise / lower its level anywhere, saved"),
                    (f"Menu music: {'on' if self.app.menu_music_on else 'off'}", "ambient texture outside the game"),
                    (f"Audio output: {self.app.sounds.device or 'system default'}", "select cycles through the outputs, saved"),
                    (f"Volume: {self.app.volume:.0%}", "{ and } lower / raise the game's own level anywhere, saved; select raises, wraps to 5 %"),
                    (f"Accent sensitivity: {self.app.dyn_scale_label()}", "; and ' lower / raise the accent and tap thresholds anywhere, saved; "
                                                                           f"with the ` viewer open only the last body played; "
                                                                           f"night (22:00-08:00) lowers them another 20 %; select sets every body, wraps"),
                    (f"Start fullscreen: {'on' if self.app.settings.get('fullscreen', True) else 'off'}", "F11 or Cmd+F toggles any time, saved"),
                    ("Recording (V)", f"audio {self.app.recorder.settings['capture_audio_device']} ch {self.app.recorder.settings['capture_audio_channels']}"
                                      f" · camera '{self.app.recorder.settings['capture_camera']}' · ~/Movies/bathero"),
                    ("Stream (T)", f"twitch.tv/{self.app.streamer.settings['twitch_channel']} · {self.app.streamer.settings['stream_height']}p "
                                   f"{self.app.streamer.settings['stream_kbps']} kbps · "
                                   + (f"display {self.app.streamer.settings['stream_display']} and the interface, both by ffmpeg"
                                      if self.app.streamer.settings.get('stream_source', 'screen') != 'window' else "the window as you see it, audio as the takes")
                                   + " · its own process with the LIVE badge, survives the game · ! camera and chat layer · key in ~/.config/bathero/twitch_key"),
                    ("Camera & take check", "the iPhone next to the game picture, both editions' layout, the take's audio meter, a test take"),
                    ("Takes: editions, Claude edits", "every take renders a computer (16:9) and a social (9:16) edition; render one again, or have Claude cut it"),
                    ("Progress (S)", "streak, minutes, trends, records"),
                    ("Coach (C)", "Claude reads your stats: strengths, weaknesses, focus, playlists"),
                    ("Quit", "")]
        # the description of the side chosen (the left-hand lead's R / L, the other crash's left / right)
        return [(ch.name, f"{ch.bpm:.0f} bpm · {len(ch.notes):3d} notes · {self.app.chart_for(self.cat, i).desc}")
                for i, ch in enumerate(self.app.items_for(self.cat))]

    def fit(self, text, max_w):
        """text clipped with an ellipsis to max_w pixels in the small font."""
        if self.f.text(text, self.f.small, DIM).get_width() <= max_w:
            return text
        lo, hi = 0, len(text)
        while lo < hi:
            mid = (lo + hi + 1) // 2
            if self.f.text(text[:mid] + "…", self.f.small, DIM).get_width() <= max_w:
                lo = mid
            else:
                hi = mid - 1
        return text[:lo] + "…"

    def move(self, d):
        n = len(self.items())
        if n:
            self.sel = (self.sel + d) % n

    def accept(self):
        if self.cat == "crash":
            if self.sel == 0:
                self.app.go(ProfilesScreen(self.app))
            elif self.sel == 1:
                self.app.go(SetupScreen(self.app))
            elif self.sel == 2:
                self.app.go(SoundcheckScreen(self.app))
            elif self.sel == 3:
                self.app.set_drum_sounds(not self.app.sounds.drums)
            elif self.sel == 4:
                self.app.guide = not self.app.guide
            elif self.sel == 5:
                self.app.toggle_intro()
            elif self.sel == 6:
                self.app.toggle_scroll()
            elif self.sel == 7:
                self.app.backing_on = not self.app.backing_on
            elif self.sel == 8:
                modes = ["full", "beats", "off"]
                self.app.metronome_mode = modes[(modes.index(self.app.metronome_mode) + 1) % 3]
            elif self.sel == 9:
                self.app.menu_music_on = not self.app.menu_music_on
                self.app.update_menu_music()
            elif self.sel == 10:
                self.app.cycle_audio_device()
            elif self.sel == 11:
                self.app.set_volume(VOLUME_STEP if self.app.volume >= 1.0 else self.app.volume + VOLUME_STEP)
            elif self.sel == 12:
                top = max(self.app.dyn_scales.values())
                self.app.set_dyn_scale(GM.DYN_SCALE_MIN if top >= GM.DYN_SCALE_MAX else top + GM.DYN_SCALE_STEP)
            elif self.sel == 13:
                self.app.settings["fullscreen"] = not self.app.settings.get("fullscreen", True)
                save_settings(self.app.settings)
            elif self.sel == 14:
                self.app.toggle_recording()
            elif self.sel == 15:
                self.app.toggle_stream()
            elif self.sel == 16:
                self.app.go(CameraCheckScreen(self.app, self.app.surface))
            elif self.sel == 17:
                self.app.go(EditScreen(self.app))
            elif self.sel == 18:
                self.app.go(StatsScreen(self.app))
            elif self.sel == 19:
                self.app.go(CoachScreen(self.app))
            else:
                return False
        elif self.items():
            self.app.go(PlayScreen(self.app, self.cat, self.sel))
        return True

    def back(self):
        if self.cat == SG.SONG_COURSE:
            self.app.go(HubScreen(self.app, next(i for i, c in enumerate(CATEGORIES) if c[0] == "tom1")))
        elif self.course:
            self.app.go(CoursesScreen(self.app, COURSES.index(self.course)))
        else:
            self.app.go(HubScreen(self.app, CATEGORIES.index(next(c for c in CATEGORIES if c[0] == self.cat))))

    def on_drum(self, inst):
        action = NAV.get(inst)
        if action == "next":
            self.move(1)
        elif action == "prev":
            self.move(-1)
        elif action == "accept":
            return self.accept()
        elif action == "back":
            self.back()
        elif action == "lead":
            self.swap_lead()
        return True

    def has_leads(self):
        return any(ch.lead for ch in self.app.items_for(self.cat))

    def swap_lead(self):
        if self.has_leads():
            self.app.lead = "L" if self.app.lead == "R" else "R"

    def on_key(self, key):
        if key in (pygame.K_DOWN, pygame.K_j):
            self.move(1)
        elif key in (pygame.K_UP, pygame.K_k):
            self.move(-1)
        elif key in (pygame.K_RETURN, pygame.K_SPACE, pygame.K_l):
            return self.accept()
        elif key in (pygame.K_ESCAPE, pygame.K_h):
            self.back()
        elif key in (pygame.K_LEFT, pygame.K_RIGHT, pygame.K_TAB):
            self.swap_lead()
        elif key == pygame.K_s:
            self.app.toggle_intro()
        elif key == pygame.K_d:
            self.app.toggle_scroll()
        return True

    def draw_lead_rows(self, surf, ch, right, y, selected):
        """A two-sided level's best results, one line per side: R on top, L below, the chosen
        side lit on the selected row. Returns the x where the sub text must stop."""
        S, f = self.s, self.f
        stop = right
        m = ch.mirrored()
        for k, (lead, key) in enumerate(sorted([(ch.lead, ch.key), (m.lead, m.key)], key=lambda r: r[0] != "R")):
            best = self.app.results.get(key)
            yy = y - 3 * S + k * 16 * S
            x = right
            hot = selected and lead == self.app.lead
            if best:                                             # a fixed column: the stars stay aligned
                ts = f.text(f"{best['accuracy'] * 100:.0f}%", f.tiny, TEXT if hot else DIM)
                surf.blit(ts, (x - ts.get_width(), yy + 1 * S))
            x -= f.text("100%", f.tiny, DIM).get_width() + 8 * S
            x -= draw_stars(surf, f, best.get("stars", 0) if best else 0, x, yy + 2 * S, S, size="tiny") + 8 * S
            hs = f.text(lead, f.tiny, HAND_COLORS[lead] if hot or best else DIM)
            surf.blit(hs, (x - hs.get_width(), yy + 1 * S)); x -= hs.get_width()
            if hot:
                pygame.draw.rect(surf, HAND_COLORS[lead], (x - 6 * S, yy + 2 * S, 2 * S, 11 * S))
            stop = min(stop, x - 10 * S)
        return stop

    def draw(self, surf, fps):
        surf.fill(BG)
        S = self.s
        pygame.draw.rect(surf, lerp(LANE_BG, self.color, 0.14), (0, 0, self.w, 96 * S))
        pygame.draw.circle(surf, self.color, (int(self.w * 0.12 - 30 * S), int(48 * S)), int(12 * S))
        surf.blit(self.f.text(self.title, self.f.big, TEXT), (self.w * 0.12, 20 * S))
        ts = self.f.text(self.sub, self.f.small, DIM)
        surf.blit(ts, (self.w * 0.88 - ts.get_width(), 60 * S))
        items = self.items()
        y = 140 * S
        row_h = 44 * S
        max_rows = int((self.h - 250 * S) / row_h)          # leaves two lines for the selected description
        first = max(0, min(self.sel - max_rows // 2, len(items) - max_rows))
        for i in range(first, min(len(items), first + max_rows)):
            name, sub = items[i]
            selected = i == self.sel
            x = self.w * 0.12
            if selected:
                pygame.draw.rect(surf, lerp(LANE_BG, self.color, 0.18), (x - 20 * S, y - 8 * S, self.w * 0.76 + 40 * S, row_h - 4 * S), border_radius=int(10 * S))
                pygame.draw.rect(surf, self.color, (x - 20 * S, y - 8 * S, 6 * S, row_h - 4 * S), border_radius=int(3 * S))
            shown = name if len(name) <= 34 else name[:33] + "…"
            surf.blit(self.f.text(shown, self.f.mid, self.color if selected else TEXT), (x, y))
            best = self.app.results.get(name)
            right = self.w * 0.88
            charts = self.app.items_for(self.cat)
            ch = charts[i] if i < len(charts) else None
            if ch is not None and ch.lead:
                right = self.draw_lead_rows(surf, ch, right, y, selected)
            elif best:
                ts = self.f.text(f"{best['accuracy'] * 100:.0f}%", self.f.small, DIM)
                surf.blit(ts, (right - ts.get_width(), y + 4 * S))
                right -= ts.get_width() + 12 * S
                right -= draw_stars(surf, self.f, best.get("stars", 0), right, y + 2 * S, S, size="small") + 16 * S
            note = self.row_note(name)
            if note is not None:
                self.progress_row(surf, x + 500 * S, y, right - (x + 500 * S), *note)
            else:
                surf.blit(self.f.text(self.fit(sub, right - (x + 500 * S)), self.f.small, DIM), (x + 500 * S, y + 4 * S))
            y += row_h
        self.draw_more(surf, y, row_h, first + max_rows - len(items))
        if items:                                             # the selected one in full, two lines if it needs them
            lines = wrap(self.f, items[self.sel][1], self.f.small, self.w - 80 * S)[:2]
            for k, line in enumerate(lines):
                self.f.center(surf, line, self.f.small, TEXT, self.h - (84 + 22 * (len(lines) - 1 - k)) * S)
        if self.has_leads():
            lead = self.app.lead
            charts = self.app.items_for(self.cat)
            sel = charts[self.sel] if self.sel < len(charts) and charts[self.sel].lead else next(ch for ch in charts if ch.lead)
            ls = self.f.text(sel.side_text(lead), self.f.small, HAND_COLORS[lead])
            surf.blit(ls, (self.w * 0.88 - ls.get_width(), 28 * S))
            what = "lead" if sel.mirror == "hands" else "crash"
            self.legend(surf, [("hihat", "down"), ("crash", "up"), ("snare", "select"), ("tom1", f"{what} R/L"), ("kick", "back")],
                        keys=f"arrows or j k · Enter or l · ← → {what} · Esc or h")
        else:
            pairs, keys = self.legend_spec()
            self.legend(surf, pairs, keys=keys)

    # hooks for the songs screen (the importer's rows) -------------------------------------
    def row_note(self, name):
        """(label, fraction or None, colour) to show instead of a row's description, or None."""
        return None

    def draw_more(self, surf, y, row_h, rows_left):
        """Rows after the items (the songs being imported)."""

    def legend_spec(self):
        return [("hihat", "down"), ("crash", "up"), ("snare", "select"), ("kick", "back")], "arrows or j k · Enter or l · Esc or h"

    def progress_row(self, surf, x, y, w, label, frac, colour):
        """A status label, over a progress bar when there is a fraction."""
        S = self.s
        if frac is not None:
            bar = pygame.Rect(int(x), int(y + 22 * S), int(max(10 * S, w)), int(6 * S))
            pygame.draw.rect(surf, LANE_BG, bar, border_radius=int(3 * S))
            pygame.draw.rect(surf, colour, (bar.x, bar.y, int(bar.w * max(0.0, min(1.0, frac))), bar.h), border_radius=int(3 * S))
        surf.blit(self.f.text(self.fit(label, w), self.f.small, colour), (x, y + (0 if frac is not None else 4) * S))


def list_screen(app, cat, sel=0):
    return SongsScreen(app, sel) if cat == SG.SONG_COURSE else ListScreen(app, cat, sel)


class SongsScreen(ListScreen):
    """The imported songs, and the importer: whether it runs (the floor tom or I turns it on and
    off), the song in hand greyed with its step and a progress bar, the queue, the failures."""

    def __init__(self, app, sel=0):
        super().__init__(app, SG.SONG_COURSE, sel)
        self.seen = None
        self.update()

    def update(self):
        if self.app.importer_seen != self.seen or self.seen is None:     # a song finished: the list grew
            self.seen = self.app.importer_seen
            old = self.course.levels[self.sel].name if self.course and self.sel < len(self.course.levels) else None
            self.course = C.COURSE.get(SG.SONG_COURSE)
            names = [ch.name for ch in self.course.levels]
            if old in names:                               # sorted by difficulty: keep the selection on its song
                self.sel = names.index(old)
            got, total = self.app.course_stars(self.course)
            n = len(self.course.levels)
            self.sub = f"{n} song{'s' if n != 1 else ''}  ·  ★ {got} / {total}"

    def items(self):
        return [(ch.name, f"{ch.desc.split(' · ')[0]} · {ch.bpm:.0f} bpm · {len(ch.notes)} notes · "
                          + " · ".join(ch.desc.split(" · ")[1:])) for ch in self.app.items_for(self.cat)]

    def pending(self):
        """[(title, label, fraction or None, colour)] for the songs the importer has not finished."""
        im = self.app.importer
        st = im.status()
        listed = {ch.name for ch in self.app.items_for(self.cat)}
        out = []
        cur = im.current()
        if cur is not None and f"♪ {cur[0]}" not in listed:
            t = int(cur[3])
            out.append((cur[0], f"{cur[1]} · {t // 60}:{t % 60:02d}", cur[2], ACCENT))
        failed = st.get("failed", {})
        shown = {cur[0]} if cur is not None else set()
        for title in st.get("queue", []) + im.waiting():       # the queue first, then the rest of the inbox
            if title in shown or title in failed or f"♪ {title}" in listed:
                continue
            shown.add(title)
            if im.running:
                out.append((title, "queued", None, DIM))
            else:
                out.append((title, "waiting", None, JUDGE_COLORS["OK"]))
        for title, why in failed.items():
            if f"♪ {title}" not in listed:
                out.append((title, f"failed: {why}", None, JUDGE_COLORS["MISS"]))
        return out

    def row_note(self, name):
        cur = self.app.importer.current()
        if cur is not None and name == f"♪ {cur[0]}":
            t = int(cur[3])
            return f"{cur[1]} · {t // 60}:{t % 60:02d}", cur[2], ACCENT
        return None

    def draw_more(self, surf, y, row_h, rows_left):
        S = self.s
        x = self.w * 0.12
        for title, label, frac, colour in self.pending()[:max(0, rows_left)]:
            shown = f"♪ {title}"
            while len(shown) > 8 and self.f.text(shown, self.f.mid, DIM).get_width() > 480 * S:
                shown = shown[:-2] + "…"
            surf.blit(self.f.text(shown, self.f.mid, DIM), (x, y))
            self.progress_row(surf, x + 500 * S, y, self.w * 0.88 - (x + 500 * S), label, frac, colour)
            y += row_h

    def draw(self, surf, fps):
        super().draw(surf, fps)
        S, im = self.s, self.app.importer
        st = im.status()
        waiting = len(im.waiting())
        if im.running and st.get("current"):
            n = 1 + len([q for q in st.get("queue", []) if q != st["current"]["title"]])
            text, colour = f"IMPORTER ON  ·  importing {n} song{'s' if n > 1 else ''}", ACCENT
        elif im.running:
            text, colour = "IMPORTER ON  ·  watching the inbox" if im.watching else "IMPORTER ON", JUDGE_COLORS["PERFECT"]
        elif im.starting:
            text, colour = "IMPORTER STARTING", ACCENT
        else:
            text = "IMPORTER OFF" + (f"  ·  {waiting} song{'s' if waiting != 1 else ''} waiting" if waiting else "")
            colour = JUDGE_COLORS["OK"] if waiting else DIM
        on = im.running or im.starting
        ts = self.f.text(text, self.f.small, colour)
        pill = pygame.Rect(0, 0, int(ts.get_width() + 44 * S), int(ts.get_height() + 10 * S))
        pill.topright = (int(self.w * 0.88), int(22 * S))
        pygame.draw.rect(surf, lerp(LANE_BG, colour, 0.25 if on else 0.08), pill, border_radius=pill.h // 2)
        pygame.draw.rect(surf, colour, pill, 1, border_radius=pill.h // 2)
        pygame.draw.circle(surf, colour, (pill.x + int(16 * S), pill.centery), int(5 * S), 0 if on else 1)
        surf.blit(ts, (pill.x + 30 * S, pill.centery - ts.get_height() / 2))
        if not self.items() and not self.pending():
            self.f.center(surf, "no songs yet: drop a .wav into ~/Music/bathero/inbox" +
                          ("" if on else ", then turn the importer on (floor tom or I)"), self.f.mid, DIM, 200 * S)

    def legend_spec(self):
        on = self.app.importer.running or self.app.importer.starting
        return ([("hihat", "down"), ("crash", "up"), ("snare", "select"), ("floor", "stop importer" if on else "start importer"), ("kick", "back")],
                "arrows or j k · Enter or l · I start / stop the importer · Esc or h")

    def on_drum(self, inst):
        if inst == "floor":
            self.app.toggle_importer()
            return True
        return super().on_drum(inst)

    def on_key(self, key):
        if key == pygame.K_i:
            self.app.toggle_importer()
            return True
        return super().on_key(key)


class CoursesScreen(ListScreen):
    """The courses, one row each with its stars; select opens the course's levels."""

    def __init__(self, app, sel=0):
        super().__init__(app, "hihat", sel)
        self.courses = COURSES

    def items(self):
        out = []
        for c in self.courses:
            got, total = self.app.course_stars(c)
            out.append((c.name, f"{len(c.levels)} levels · ★ {got} / {total} · {c.desc}"))
        return out

    def accept(self):
        self.app.go(ListScreen(self.app, self.courses[self.sel].key))
        return True


def wrap(fonts, text, font, max_w):
    """Word-wrap text into lines that fit max_w pixels."""
    lines = []
    for para in str(text).split("\n"):
        words = para.split()
        cur = ""
        for w in words:
            trial = (cur + " " + w).strip()
            if fonts.text(trial, font, TEXT).get_width() <= max_w or not cur:
                cur = trial
            else:
                lines.append(cur); cur = w
        lines.append(cur)
    return lines


def sparkline(surf, values, x, y, w, h, color, lo=None, hi=None, S=1.0):
    if len(values) < 2:
        return
    lo = min(values) if lo is None else lo
    hi = max(values) if hi is None else hi
    span = (hi - lo) or 1.0
    pts = [(x + i * w / (len(values) - 1), y + h - (v - lo) / span * h) for i, v in enumerate(values)]
    pygame.draw.lines(surf, color, False, pts, max(1, int(2 * S)))
    pygame.draw.circle(surf, color, (int(pts[-1][0]), int(pts[-1][1])), int(4 * S))


# ---------------------------------------------------------------------------
class LoginScreen(Screen):
    """Who's playing: one card per profile, hit its pad (or its number) to log in."""

    def __init__(self, app):
        super().__init__(app)
        self.flash = None       # (wall t, pad) the last hit on a pad nobody owns

    def on_note(self, note, velocity):
        self.app.nav_hit(note, velocity, raw=True)

    def on_drum(self, inst):
        self.login(inst)
        return True

    def login(self, pad):
        p = PR.by_pad(self.app.profiles, pad)
        if p is None:
            self.flash = (time.perf_counter(), pad)
            self.app.toasts.add(f"nobody on the {C.LABELS[pad]} · N makes a profile", DIM, key="login")
            return
        self.app.set_profile(p)
        self.app.toasts.add(f"playing as {p['name']}", C.COLORS[p["pad"]], key="login")
        self.app.go(HubScreen(self.app))

    def on_key(self, key):
        if key in (pygame.K_ESCAPE, pygame.K_q):
            return False
        if key in KEY_LANES and KEY_LANES[key] < len(PR.ICON_PADS):
            self.login(PR.ICON_PADS[KEY_LANES[key]])
        elif key == pygame.K_n:
            self.app.go(NewProfileScreen(self.app, back=lambda: LoginScreen(self.app)))
        return True

    def update(self):
        if not self.app.profiles:
            self.app.go(HubScreen(self.app))

    def draw(self, surf, fps):
        surf.fill(BG)
        S = self.s
        self.f.center(surf, "Who's playing?", self.f.large, TEXT, 60 * S)
        self.f.center(surf, "hit your pad", self.f.mid, DIM, 104 * S)
        ps = self.app.profiles
        n = max(1, len(ps))
        cw = min(300 * S, (self.w - 120 * S) / n)
        ch = min(330 * S, self.h - 280 * S)
        x0 = self.w / 2 - cw * n / 2
        cy = 150 * S + ch / 2
        now = time.perf_counter()
        for i, p in enumerate(ps):
            pad = p["pad"]
            color = C.COLORS[pad]
            hot = max(0.0, 1 - (now - self.app.legend_flash.get(pad, 0)) / 0.3)
            cx = x0 + (i + 0.5) * cw
            rect = pygame.Rect(int(cx - cw / 2 + 10 * S), int(cy - ch / 2), int(cw - 20 * S), int(ch))
            fill = lerp(lerp(LANE_BG, color, 0.12), color, 0.5 * hot)
            pygame.draw.rect(surf, fill, rect, border_radius=int(18 * S))
            pygame.draw.rect(surf, color if hot else lerp(LANE_BG, color, 0.5), rect, 2, border_radius=int(18 * S))
            PR.draw_icon(surf, pad, cx, cy - 60 * S, min(64 * S, ch * 0.22), bg=fill)
            self.f.center(surf, p["name"], self.f.big if len(p["name"]) <= 8 else self.f.mid, TEXT, cy + 40 * S, cx)
            self.f.center(surf, f"hit the {C.LABELS[pad]}", self.f.small, color, cy + 90 * S, cx)
            self.f.center(surf, f"key {PR.ICON_PADS.index(pad) + 1}", self.f.small, DIM, cy + 112 * S, cx)
            if self.app.midi_in and not self.app.has_drum(pad):
                self.f.center(surf, "pad not in the kit", self.f.small, JUDGE_COLORS["MISS"], cy + ch / 2 - 22 * S, cx)
        if self.flash and now - self.flash[0] < 1.0:
            pad = self.flash[1]
            self.f.center(surf, f"nobody on the {C.LABELS[pad]}", self.f.mid, lerp(BG, C.COLORS[pad], 1 - (now - self.flash[0])),
                          cy + ch / 2 + 34 * S)
        self.f.center(surf, "N new profile   ·   Esc quit", self.f.small, DIM, self.h - 20 * S)


# ---------------------------------------------------------------------------
class ProfilesScreen(Screen):
    """Setup ▸ Profile: log out, make a profile, switch to another, delete one."""

    DELETE_S = 3.0      # the second tom hit must come within this

    def __init__(self, app, sel=0):
        super().__init__(app)
        self.sel = sel
        self.arm = None     # (wall t, profile id) a delete waiting for its confirmation
        self.stars = {p["id"]: sum(v.get("stars", 0) for v in load_progress(PR.progress_path(p)).values())
                      for p in app.profiles}

    def rows(self):
        out = []
        if self.app.profile:
            out.append(("logout", "Log out", "back to the login screen: the next player hits their pad"))
        out.append(("new", "New profile", "type a name, hit the pad whose icon you want, hit it again"))
        for p in self.app.profiles:
            now = "playing now · " if self.app.profile is p else ""
            out.append((p, p["name"], f"{now}{C.LABELS[p['pad']]} · ★ {self.stars.get(p['id'], 0)} · select switches to it, tom deletes it"))
        return out

    def move(self, d):
        self.sel = (self.sel + d) % len(self.rows())

    def accept(self):
        what = self.rows()[self.sel][0]
        if what == "logout":
            self.app.set_profile(None)
            self.app.toasts.add("logged out", DIM, key="login")
            self.app.go(LoginScreen(self.app))
        elif what == "new":
            self.app.go(NewProfileScreen(self.app, back=lambda: ProfilesScreen(self.app, self.sel)))
        else:
            self.app.set_profile(what)
            self.app.toasts.add(f"playing as {what['name']}", C.COLORS[what["pad"]], key="login")
            self.app.go(HubScreen(self.app))

    def delete(self):
        what = self.rows()[self.sel][0]
        if not isinstance(what, dict):
            return
        now = time.perf_counter()
        if self.arm and self.arm[1] == what["id"] and now - self.arm[0] < self.DELETE_S:
            self.arm = None
            self.app.profiles.remove(what)
            self.app.save_profiles()
            path = PR.progress_path(what)
            if what["id"] != PR.MAIN:                       # the owner's file is the old shared progress.json: kept
                try:
                    os.remove(path)
                except OSError:
                    pass
            if self.app.profile is what:
                self.app.set_profile(None)
            self.app.toasts.add(f"{what['name']} deleted", DIM, key="login")
            self.sel = min(self.sel, len(self.rows()) - 1)
            if not self.app.profiles:
                self.app.go(ListScreen(self.app, "crash", 0))
        else:
            self.arm = (now, what["id"])

    def on_drum(self, inst):
        action = NAV.get(inst)
        if action == "next":
            self.move(1)
        elif action == "prev":
            self.move(-1)
        elif action == "accept":
            self.accept()
        elif action == "back":
            self.app.go(ListScreen(self.app, "crash", 0))
        elif action == "lead":
            self.delete()
        return True

    def on_key(self, key):
        if key in (pygame.K_DOWN, pygame.K_j):
            self.move(1)
        elif key in (pygame.K_UP, pygame.K_k):
            self.move(-1)
        elif key in (pygame.K_RETURN, pygame.K_SPACE, pygame.K_l):
            self.accept()
        elif key in (pygame.K_ESCAPE, pygame.K_h):
            self.app.go(ListScreen(self.app, "crash", 0))
        elif key in (pygame.K_BACKSPACE, pygame.K_DELETE, pygame.K_x):
            self.delete()
        return True

    def draw(self, surf, fps):
        surf.fill(BG)
        S = self.s
        color = C.COLORS["crash"]
        pygame.draw.rect(surf, lerp(LANE_BG, color, 0.14), (0, 0, self.w, 96 * S))
        pygame.draw.circle(surf, color, (int(self.w * 0.12 - 30 * S), int(48 * S)), int(12 * S))
        surf.blit(self.f.text("Profiles", self.f.big, TEXT), (self.w * 0.12, 20 * S))
        ts = self.f.text("each profile has its own stars, streak and coach", self.f.small, DIM)
        surf.blit(ts, (self.w * 0.88 - ts.get_width(), 60 * S))
        rows = self.rows()
        y, row_h = 140 * S, 52 * S
        now = time.perf_counter()
        for i, (what, name, sub) in enumerate(rows):
            selected = i == self.sel
            x = self.w * 0.12
            if selected:
                pygame.draw.rect(surf, lerp(LANE_BG, color, 0.18), (x - 20 * S, y - 8 * S, self.w * 0.76 + 40 * S, row_h - 4 * S), border_radius=int(10 * S))
                pygame.draw.rect(surf, color, (x - 20 * S, y - 8 * S, 6 * S, row_h - 4 * S), border_radius=int(3 * S))
            if isinstance(what, dict):
                PR.draw_icon(surf, what["pad"], x + 16 * S, y + 16 * S, 14 * S, bg=lerp(LANE_BG, color, 0.18) if selected else BG)
                tx = x + 44 * S
                col = C.COLORS[what["pad"]] if selected or self.app.profile is what else TEXT
            else:
                tx, col = x, (color if selected else TEXT)
            surf.blit(self.f.text(name, self.f.mid, col), (tx, y))
            armed = isinstance(what, dict) and self.arm and self.arm[1] == what["id"] and now - self.arm[0] < self.DELETE_S
            if armed:
                sub = f"hit the tom again to delete {what['name']} and their stars"
            surf.blit(self.f.text(sub, self.f.small, JUDGE_COLORS["MISS"] if armed else DIM), (tx + 260 * S, y + 6 * S))
            y += row_h
        self.legend(surf, [("hihat", "down"), ("crash", "up"), ("snare", "select"), ("tom1", "delete"), ("kick", "back")],
                    keys="arrows or j k · Enter · Backspace delete · Esc")


# ---------------------------------------------------------------------------
class NewProfileScreen(Screen):
    """A name from the keyboard, an icon from the pads: hit a pad to choose its icon, hit it
    again to create the profile and log in as it."""

    typing = True

    def __init__(self, app, back=None):
        super().__init__(app)
        self.name = ""
        self.pad = None
        self.back = back or (lambda: ListScreen(app, "crash", 0))
        self.flash = None       # (wall t, pad, reason) a pad that could not be chosen

    def on_note(self, note, velocity):
        self.app.nav_hit(note, velocity, raw=True)

    def on_drum(self, inst):
        self.choose(inst)
        return True

    def choose(self, pad):
        owner = PR.by_pad(self.app.profiles, pad)
        if owner is not None:
            self.flash = (time.perf_counter(), pad, f"the {C.LABELS[pad]} is {owner['name']}'s")
            return
        if self.pad == pad:
            self.create()
        else:
            self.pad = pad

    def create(self):
        if not self.name.strip():
            self.flash = (time.perf_counter(), self.pad, "type a name first")
            return
        if self.pad is None:
            self.flash = (time.perf_counter(), None, "hit a pad to choose an icon")
            return
        try:
            p = PR.new_profile(self.app.profiles, self.name, self.pad)
        except ValueError as e:
            self.flash = (time.perf_counter(), self.pad, str(e))
            return
        self.app.profiles.append(p)
        self.app.save_profiles()
        self.app.set_profile(p)
        self.app.toasts.add(f"welcome, {p['name']} · you log in with the {C.LABELS[p['pad']]}", C.COLORS[p["pad"]], key="login")
        self.app.go(HubScreen(self.app))

    def on_text(self, ch):
        if len(self.name) < 16:
            self.name += ch

    def on_key(self, key):
        if key == pygame.K_ESCAPE:
            self.app.go(self.back())
        elif key == pygame.K_BACKSPACE:
            self.name = self.name[:-1]
        elif key in (pygame.K_RETURN, pygame.K_KP_ENTER):
            self.create()
        elif key in KEY_LANES and KEY_LANES[key] < len(PR.ICON_PADS) and not self.name:
            self.choose(PR.ICON_PADS[KEY_LANES[key]])       # digits are text once a name has begun
        return True

    def draw(self, surf, fps):
        surf.fill(BG)
        S = self.s
        now = time.perf_counter()
        self.f.center(surf, "New profile", self.f.large, TEXT, 50 * S)
        # the name
        bw, bh = 420 * S, 56 * S
        box = pygame.Rect(int(self.w / 2 - bw / 2), int(96 * S), int(bw), int(bh))
        pygame.draw.rect(surf, LANE_BG, box, border_radius=int(12 * S))
        pygame.draw.rect(surf, ACCENT if not self.name else DIM, box, 2, border_radius=int(12 * S))
        caret = "|" if int(now * 2) % 2 == 0 else " "
        if self.name:
            self.f.center(surf, self.name + caret, self.f.mid, TEXT, box.centery)
        else:
            self.f.center(surf, caret + " type a name", self.f.mid, DIM, box.centery)
        # the icons
        n = len(PR.ICON_PADS)
        cw = min(130 * S, (self.w - 80 * S) / n)
        x0 = self.w / 2 - cw * n / 2
        iy = 300 * S
        self.f.center(surf, "hit a pad to choose your icon" if self.pad is None else
                      f"hit the {C.LABELS[self.pad]} again to create the profile", self.f.mid,
                      TEXT if self.pad is None else C.COLORS[self.pad], 196 * S)
        for i, pad in enumerate(PR.ICON_PADS):
            cx = x0 + (i + 0.5) * cw
            owner = PR.by_pad(self.app.profiles, pad)
            color = C.COLORS[pad]
            hot = max(0.0, 1 - (now - self.app.legend_flash.get(pad, 0)) / 0.3)
            chosen = pad == self.pad
            if chosen:
                pygame.draw.circle(surf, lerp(LANE_BG, color, 0.25 + 0.4 * hot), (int(cx), int(iy)), int(cw * 0.46))
                pygame.draw.circle(surf, color, (int(cx), int(iy)), int(cw * 0.46), max(2, int(3 * S)))
            r = cw * (0.3 if chosen else 0.22) * (1 + 0.15 * hot)
            PR.draw_icon(surf, pad, cx, iy, r, color=lerp(color, BG, 0.65) if owner else color,
                         bg=lerp(LANE_BG, color, 0.25 + 0.4 * hot) if chosen else BG)
            self.f.center(surf, C.LABELS[pad], self.f.small, DIM if owner else TEXT, iy + cw * 0.56, cx)
            self.f.center(surf, owner["name"] if owner else f"key {i + 1}", self.f.small,
                          lerp(color, DIM, 0.4) if owner else DIM, iy + cw * 0.56 + 20 * S, cx)
        if self.flash and now - self.flash[0] < 1.6:
            k = 1 - (now - self.flash[0]) / 1.6
            self.f.center(surf, self.flash[2], self.f.mid, lerp(BG, JUDGE_COLORS["MISS"], k), iy + cw * 0.56 + 62 * S)
        self.f.center(surf, "type the name · a pad picks the icon, the same pad again creates it · Enter also creates · Esc cancel",
                      self.f.small, DIM, self.h - 20 * S)


# ---------------------------------------------------------------------------
class StatsScreen(Screen):
    """Progress at a glance: streak, minutes, this week, trends, records, last runs."""

    def on_drum(self, inst):
        if NAV.get(inst) == "back":
            self.app.go(HubScreen(self.app))
        elif NAV.get(inst) == "accept":
            self.app.go(CoachScreen(self.app))
        return True

    def on_key(self, key):
        if key in (pygame.K_ESCAPE, pygame.K_h, pygame.K_s):
            self.app.go(HubScreen(self.app))
        elif key in (pygame.K_c, pygame.K_RETURN, pygame.K_l):
            self.app.go(CoachScreen(self.app))
        return True

    def draw(self, surf, fps):
        surf.fill(BG)
        S = self.s; f = self.f
        sm = self.app.stats_summary()
        self.f.center(surf, "Progress", f.large, TEXT, 44 * S)
        if not sm["runs"]:
            f.center(surf, "Nothing played yet. Every level you finish lands here.", f.mid, DIM, self.h * 0.42)
            self.legend(surf, [("snare", "coach"), ("kick", "back")], keys="C coach · Esc back")
            return
        # headline numbers
        cols = [(f"{sm['streak']}", "day streak" + (f" (best {sm['best_streak']})" if sm["best_streak"] > sm["streak"] else "")),
                (f"{sm['today_minutes']:.0f}", "minutes today"),
                (f"{sm['total_minutes']:.0f}", f"minutes in {sm['days']} days"),
                (f"{sm['total_notes']:,}", "notes hit"),
                (f"{sum(self.app.results.get(n, {}).get('stars', 0) for n in self.app.results)}", "stars")]
        cw = (self.w - 120 * S) / len(cols)
        for i, (big, label) in enumerate(cols):
            cx = 60 * S + (i + 0.5) * cw
            f.center(surf, big, f.big, ACCENT, 112 * S, cx)
            f.center(surf, label, f.small, DIM, 152 * S, cx)
        # this week
        x0, y0, bw, bh = 60 * S, 200 * S, (self.w * 0.42 - 60 * S), 130 * S
        surf.blit(f.text("this week", f.small, TEXT), (x0, y0 - 24 * S))
        top = max([m for _, m in sm["week"]] + [10.0])
        for i, (d, m) in enumerate(sm["week"]):
            bx = x0 + i * bw / 7
            hgt = m / top * bh
            col = ACCENT if d == sm["today"] else lerp(ACCENT, LANE_BG, 0.45)
            pygame.draw.rect(surf, LANE_BG, (bx + 6 * S, y0, bw / 7 - 12 * S, bh), border_radius=int(5 * S))
            if hgt > 0:
                pygame.draw.rect(surf, col, (bx + 6 * S, y0 + bh - hgt, bw / 7 - 12 * S, hgt), border_radius=int(5 * S))
            f.center(surf, time.strftime("%a", time.strptime(d, "%Y-%m-%d"))[:2], f.small, DIM, y0 + bh + 14 * S, bx + bw / 14)
            if m >= 1:
                f.center(surf, f"{m:.0f}", f.small, TEXT, y0 + bh - hgt - 12 * S, bx + bw / 14)
        gw, gp = sm["grade_week"], sm["grade_prev_week"]
        if gw is not None:
            delta = f"  ({gw - gp:+.0f} vs last week)" if gp is not None else ""
            surf.blit(f.text(f"mean grade this week {gw:.0f}{delta}", f.small, DIM), (x0, y0 + bh + 34 * S))
        # trends
        tx, tw = self.w * 0.5, self.w * 0.5 - 60 * S
        for j, (label, vals, col, lo, hi, fmt) in enumerate([
                ("accuracy, last runs", [v * 100 for v in sm["trend_accuracy"]], JUDGE_COLORS["PERFECT"], 0, 100, "{:.0f}%"),
                ("timing std ms, last runs (lower is tighter)", sm["trend_std_ms"], JUDGE_COLORS["GOOD"], 0, None, "{:.0f} ms"),
                ("grade, last runs", sm["trend_grade"], ACCENT, 0, 100, "{:.0f}")]):
            ty = y0 - 24 * S + j * 70 * S
            surf.blit(f.text(label, f.small, TEXT), (tx, ty))
            if vals:
                sparkline(surf, vals, tx, ty + 22 * S, tw - 70 * S, 34 * S, col, lo, hi, S)
                surf.blit(f.text(fmt.format(vals[-1]), f.small, col), (tx + tw - 60 * S, ty + 30 * S))
        # records and last runs
        ry = 410 * S
        recs = []
        if sm["best_run"]:
            b = sm["best_run"]; recs.append(f"best run  {b['chart']['name']}  grade {b['stats']['grade']:.0f}, {b['stats']['stars']} stars")
        if sm["tightest"]:
            t = sm["tightest"]; recs.append(f"tightest timing  {t['chart']['name']}  std {t['stats']['std_ms']:.1f} ms")
        if sm["longest_combo"] and sm["longest_combo"]["stats"].get("max_combo"):
            c = sm["longest_combo"]; recs.append(f"longest combo  {c['stats']['max_combo']} on {c['chart']['name']}")
        surf.blit(f.text("records", f.small, TEXT), (x0, ry))
        for i, r in enumerate(recs):
            surf.blit(f.text(r, f.small, DIM), (x0, ry + (22 + i * 20) * S))
        surf.blit(f.text("last runs", f.small, TEXT), (tx, ry))
        for i, r in enumerate(sm["last"][:6]):
            when = time.strftime("%a %H:%M", time.localtime(r["started"]))
            line = f"{when}  {r['chart']['name'][:22]:22}  {r['stats']['accuracy'] * 100:3.0f}%  {r['stats'].get('std_ms', 0):4.0f} ms"
            surf.blit(f.text(line, f.small, DIM), (tx, ry + (22 + i * 20) * S))
            draw_stars(surf, f, r["stats"].get("stars", 0), self.w - 60 * S, ry + (20 + i * 20) * S, S, size="small")
        self.legend(surf, [("snare", "coach"), ("kick", "back")], keys="C coach · Esc back")


# ---------------------------------------------------------------------------
class CoachScreen(Screen):
    """What Claude says about your playing, and the playlists it wrote as sessions."""

    def __init__(self, app, done=None):
        super().__init__(app)
        self.sel = 0
        self.done = done

    def playlists(self):
        r = self.app.coach.result
        return r.get("playlists", []) if r else []

    def on_drum(self, inst):
        action = NAV.get(inst)
        if action == "back":
            self.app.go(HubScreen(self.app))
        elif action == "next":
            self.sel = (self.sel + 1) % max(1, len(self.playlists()))
        elif action == "prev":
            self.sel = (self.sel - 1) % max(1, len(self.playlists()))
        elif action == "accept":
            self.accept()
        return True

    def on_key(self, key):
        if key in (pygame.K_ESCAPE, pygame.K_h):
            self.app.go(HubScreen(self.app))
        elif key in (pygame.K_DOWN, pygame.K_j):
            self.sel = (self.sel + 1) % max(1, len(self.playlists()))
        elif key in (pygame.K_UP, pygame.K_k):
            self.sel = (self.sel - 1) % max(1, len(self.playlists()))
        elif key in (pygame.K_RETURN, pygame.K_l):
            self.accept()
        elif key == pygame.K_a:
            self.ask()
        elif key == pygame.K_s:
            self.app.go(StatsScreen(self.app))
        return True

    def ask(self):
        if not self.app.coach.busy:
            self.app.coach.start(self.app.coach_report(), self.app.level_names())

    def accept(self):
        pls = self.playlists()
        if pls:
            self.app.start_session(pls[self.sel])
        else:
            self.ask()

    def draw(self, surf, fps):
        surf.fill(BG)
        S = self.s; f = self.f
        f.center(surf, "Coach", f.large, TEXT, 44 * S)
        r = self.app.coach.result
        if self.done:
            f.center(surf, f"session '{self.done}' done", f.mid, JUDGE_COLORS["PERFECT"], 84 * S)
        if self.app.coach.busy:
            f.center(surf, self.app.coach.status, f.mid, JUDGE_COLORS["GOOD"], self.h * 0.42)
            f.center(surf, "Claude is reading your run logs; this takes a minute or two", f.small, DIM, self.h * 0.42 + 36 * S)
        elif not r:
            f.center(surf, "No analysis yet.", f.mid, TEXT, self.h * 0.40)
            f.center(surf, "Snare or Enter: ask Claude for strengths, weaknesses, the focus for next session and playlists.",
                     f.small, DIM, self.h * 0.40 + 36 * S)
            if self.app.coach.error:
                f.center(surf, self.app.coach.error, f.small, JUDGE_COLORS["MISS"], self.h * 0.40 + 64 * S)
        else:
            x0, colw = 60 * S, self.w * 0.5 - 80 * S
            y = 84 * S if not self.done else 108 * S
            when = time.strftime("%Y-%m-%d %H:%M", time.localtime(r.get("generated", 0)))
            surf.blit(f.text(f"from {when}  ·  A ask again", f.small, DIM), (x0, y)); y += 26 * S
            surf.blit(f.text("focus next session", f.mid, ACCENT), (x0, y)); y += 30 * S
            for line in wrap(f, r.get("focus_next_session", ""), f.small, colw)[:5]:
                surf.blit(f.text(line, f.small, TEXT), (x0, y)); y += 20 * S
            y += 10 * S
            for title, key, col in (("strengths", "strengths", JUDGE_COLORS["PERFECT"]), ("weaknesses", "weaknesses", JUDGE_COLORS["OK"])):
                surf.blit(f.text(title, f.mid, col), (x0, y)); y += 30 * S
                for item in r.get(key, [])[:5]:
                    lines = wrap(f, "· " + item, f.small, colw)[:2]
                    for line in lines:
                        surf.blit(f.text(line, f.small, TEXT), (x0, y)); y += 20 * S
                y += 8 * S
            # right: diet and playlists
            rx = self.w * 0.5 + 20 * S
            y = 84 * S if not self.done else 108 * S
            surf.blit(f.text("diet", f.mid, ACCENT), (rx, y)); y += 30 * S
            for line in wrap(f, r.get("diet", ""), f.small, colw)[:7]:
                surf.blit(f.text(line, f.small, TEXT), (rx, y)); y += 20 * S
            y += 12 * S
            surf.blit(f.text("sessions  ·  snare or Enter to start", f.mid, ACCENT), (rx, y)); y += 32 * S
            for i, pl in enumerate(self.playlists()):
                selected = i == self.sel
                if selected:
                    pygame.draw.rect(surf, lerp(LANE_BG, ACCENT, 0.18), (rx - 12 * S, y - 6 * S, colw + 24 * S, 46 * S), border_radius=int(8 * S))
                n = len(pl.get("items", []))
                surf.blit(f.text(f"{pl.get('name', 'session')}  ·  {n} levels · {pl.get('minutes', '?')} min", f.small, ACCENT if selected else TEXT), (rx, y))
                surf.blit(f.text(wrap(f, pl.get("goal", ""), f.small, colw)[0] if pl.get("goal") else "", f.small, DIM), (rx, y + 20 * S))
                y += 50 * S
            if self.playlists():
                pl = self.playlists()[self.sel]
                items = ", ".join(f"{it['level']} @{it.get('rate', 1.0):.3g}x" + (f" x{it['reps']}" if it.get('reps', 1) > 1 else "") for it in pl["items"])
                for line in wrap(f, items, f.small, colw)[:3]:
                    surf.blit(f.text(line, f.small, DIM), (rx, y)); y += 20 * S
        self.legend(surf, [("hihat", "down"), ("crash", "up"), ("snare", "start session" if r else "ask Claude"), ("kick", "back")],
                    keys="A ask Claude · S progress · Esc back")


# ---------------------------------------------------------------------------
class CameraCheckScreen(Screen):
    """Soundcheck for takes: the game picture next to the camera, the composed preview with
    the picture-in-picture, the take's audio channels metered, and a 3-second test take."""

    def __init__(self, app, sample=None):
        super().__init__(app)
        self.sample = sample.copy() if sample is not None else None   # what the take captures
        self.settings = {**CP.DEFAULTS, **{k: v for k, v in app.settings.items() if k in CP.DEFAULTS}}
        app.close_cam_monitor()                                  # one ffmpeg on the camera at a time
        self.camera = CP.find_camera(self.settings["capture_camera"])
        self.preview = CP.CameraPreview(self.camera) if self.camera and CP.ffmpeg_path() else None
        try:
            self.meter = CP.AudioMeter(app.settings)
        except Exception as e:                                   # noqa: BLE001
            self.meter = None
            self.meter_error = str(e)
        else:
            self.meter_error = self.meter.error
        self.test_at = None
        self.test_result = None

    def close(self):
        if self.preview:
            self.preview.stop()
        if self.meter:
            self.meter.stop()
        self.preview = self.meter = None

    def leave(self):
        self.close()
        self.app.go(ListScreen(self.app, "crash", 15))

    def on_drum(self, inst):
        action = NAV.get(inst)
        if action == "back":
            self.leave()
        elif action == "next":
            self.cycle_pip(1)
        elif action == "prev":
            self.cycle_pip(-1)
        elif action == "accept":
            self.test_take()
        return True

    def on_key(self, key):
        if key in (pygame.K_ESCAPE, pygame.K_h):
            self.leave()
        elif key in (pygame.K_DOWN, pygame.K_j):
            self.cycle_pip(1)
        elif key in (pygame.K_UP, pygame.K_k):
            self.cycle_pip(-1)
        elif key in (pygame.K_LEFTBRACKET, pygame.K_RIGHTBRACKET, pygame.K_LEFT, pygame.K_RIGHT):
            corners = CP.CORNERS
            i = corners.index(self.settings["capture_corner"]) if self.settings["capture_corner"] in corners else 0
            self.settings["capture_corner"] = corners[(i + (1 if key in (pygame.K_RIGHTBRACKET, pygame.K_RIGHT) else -1)) % 4]
            self.save()
        elif key in (pygame.K_RETURN, pygame.K_l):
            self.test_take()
        elif key == pygame.K_s:
            self.cycle_split()
        elif key == pygame.K_p:
            self.cycle_pip(1)
        elif key == pygame.K_r:
            self.close()
            self.__init__(self.app, self.sample)
        return True

    def cycle_pip(self, d=1):
        """The computer edition: the camera picture's height as a share of the frame (P, up/down, hats/crash)."""
        sizes = CP.PIPS
        cur = min(range(len(sizes)), key=lambda i: abs(sizes[i] - self.settings["capture_pip"]))
        self.settings["capture_pip"] = sizes[(cur + d) % len(sizes)]
        self.save()

    def cycle_split(self):
        """The social edition: the camera's share of the 9:16 height (0.32 = the whole camera picture)."""
        cur = min(range(len(CP.SPLITS)), key=lambda i: abs(CP.SPLITS[i] - self.settings["capture_split"]))
        self.settings["capture_split"] = CP.SPLITS[(cur + 1) % len(CP.SPLITS)]
        self.save()

    def save(self):
        self.app.settings.update({k: self.settings[k] for k in ("capture_pip", "capture_corner", "capture_split")})
        save_settings(self.app.settings)
        self.app.recorder.settings.update(self.settings)

    def test_take(self):
        if self.app.recorder.active or self.app.recorder.composing:
            return
        self.close()                                             # the camera and the device go to the recorder
        self.app.recorder.settings.update(self.settings)
        if self.app.recorder.start(self.app.size, "camera check"):
            self.test_at = time.perf_counter()
            self.test_result = None
        else:
            self.test_result = f"could not start: {self.app.recorder.error}"

    def update(self):
        # no camera yet: look again every few seconds (Continuity Camera comes and goes with the phone)
        if self.camera is None and self.test_at is None and time.perf_counter() - getattr(self, "_scan_at", 0) > 5.0:
            self._scan_at = time.perf_counter()
            cam = CP.find_camera(self.settings["capture_camera"])
            if cam:
                self.camera = cam
                self.preview = CP.CameraPreview(cam) if CP.ffmpeg_path() else None
                print(f"camera check: {cam[1]} appeared")
        if self.preview is not None and self.preview.error and not getattr(self, "_logged_error", False):
            self._logged_error = True
            print(f"camera check: preview error: {self.preview.error}")
        if self.test_at is not None and self.app.recorder.active and time.perf_counter() - self.test_at > 3.0:
            path = self.app.recorder.stop()
            self.test_result = f"test take: {os.path.basename(path)}"
        if self.test_at is not None and not self.app.recorder.active and self.app.recorder.composing is None and self.test_result and self.preview is None:
            self.test_result = (f"saved {self.test_result[11:]}" if not self.app.recorder.error else self.app.recorder.error)
            self.test_at = None
            self.preview = CP.CameraPreview(self.camera) if self.camera and CP.ffmpeg_path() else None   # back to live
            try:
                self.meter = CP.AudioMeter(self.app.settings)
            except Exception:                                  # noqa: BLE001
                self.meter = None

    def draw(self, surf, fps):
        surf.fill(BG)
        S = self.s; f = self.f
        f.center(surf, "Camera & take check", f.large, TEXT, 44 * S)
        avail = self.h - 210 * S                                   # between the title and the legend
        ph = int(avail * 0.42); pw = int(ph * 16 / 9)
        gap = int(40 * S)
        lx = int(self.w / 2 - pw - gap / 2); rx = int(self.w / 2 + gap / 2); y = int(96 * S)
        # game picture
        surf.blit(f.text("game picture (what the take records)", f.small, TEXT), (lx, y - 22 * S))
        if self.sample is not None:
            surf.blit(pygame.transform.smoothscale(self.sample, (pw, ph)), (lx, y))
        pygame.draw.rect(surf, (60, 60, 70), (lx, y, pw, ph), 1)
        # camera
        surf.blit(f.text(f"camera: {self.camera[1] if self.camera else 'none found'}", f.small, TEXT if self.camera else JUDGE_COLORS["MISS"]), (rx, y - 22 * S))
        cam = self.preview.surface() if self.preview else None
        if cam is not None:
            surf.blit(pygame.transform.smoothscale(cam, (pw, ph)), (rx, y))
        else:
            pygame.draw.rect(surf, LANE_BG, (rx, y, pw, ph))
            msg = (self.preview.error or "waiting for frames... (allow camera access if macOS asks)") if self.preview else \
                  (f"no video device matching '{self.settings['capture_camera']}'. Looking every 5 s. iPhone: same Apple ID, "
                   f"Wi-Fi and Bluetooth on, near the Mac, locked and still in landscape (or open Camo).")
            for i, line in enumerate(wrap(f, msg, f.small, pw - 20 * S)[:4]):
                f.center(surf, line, f.small, DIM, y + ph / 2 - 20 * S + i * 20 * S, rx + pw / 2)
        pygame.draw.rect(surf, (60, 60, 70), (rx, y, pw, ph), 1)
        # the two editions: computer (16:9, the camera picture-in-picture) and social (9:16, the
        # screen as a thumbnail on top, the camera under it)
        cy = int(y + ph + 46 * S)
        f.center(surf, f"computer edition: camera {self.settings['capture_pip']:.0%} of the height (P), corner {self.settings['capture_corner']} ([ ])"
                       f"      social edition: camera {self.settings['capture_split']:.0%} of the height (S)",
                 f.small, TEXT, cy - 18 * S)
        ch = int(avail * 0.44); cw = int(ch * 16 / 9)
        cx = int(self.w / 2 - cw / 2 - 120 * S)
        if self.sample is not None:
            surf.blit(pygame.transform.smoothscale(self.sample, (cw, ch)), (cx, cy))
        else:
            pygame.draw.rect(surf, LANE_BG, (cx, cy, cw, ch))
        pip_h = int(ch * self.settings["capture_pip"]); pip_w = int(pip_h * 16 / 9); m = int(24 * cw / 1920)
        corner = self.settings["capture_corner"]
        px = cx + (cw - pip_w - m if corner in ("br", "tr") else m)
        py = cy + (ch - pip_h - m if corner in ("br", "bl") else m)
        if cam is not None:
            surf.blit(pygame.transform.smoothscale(cam, (pip_w, pip_h)), (px, py))
        else:
            pygame.draw.rect(surf, lerp(LANE_BG, ACCENT, 0.3), (px, py, pip_w, pip_h))
        pygame.draw.rect(surf, ACCENT, (px, py, pip_w, pip_h), 2)
        pygame.draw.rect(surf, (60, 60, 70), (cx, cy, cw, ch), 1)
        sx, sh = cx + cw + int(24 * S), ch
        sw = int(sh * 9 / 16)
        pygame.draw.rect(surf, (0, 0, 0), (sx, cy, sw, sh))
        (gx, gy, gw, gh), (bx, by, bw, bh) = CP.social_layout(self.sample.get_size() if self.sample is not None else (16, 9),
                                                              self.settings["capture_split"], size=(sw, sh))
        if self.sample is not None and gw > 0 and gh > 0:
            surf.blit(pygame.transform.smoothscale(self.sample, (gw, gh)), (sx + gx, cy + gy))
        if cam is not None and bw > 0 and bh > 0:
            surf.blit(CP.cover(cam, (bw, bh)), (sx + bx, cy + by))
        else:
            pygame.draw.rect(surf, lerp(LANE_BG, ACCENT, 0.3), (sx + bx, cy + by, bw, bh))
        pygame.draw.rect(surf, ACCENT, (sx + bx, cy + by, bw, bh), 2)
        pygame.draw.rect(surf, (60, 60, 70), (sx, cy, sw, sh), 1)
        # audio meter, to the right of the previews
        mx = sx + sw + 36 * S
        surf.blit(f.text(f"take audio: {self.settings['capture_audio_device']} ch {self.settings['capture_audio_channels']}", f.small, TEXT), (mx, cy))
        levels = self.meter.levels if self.meter else None
        if levels:
            for i, db in enumerate(levels):
                bar_w = 200 * S
                k = max(0.0, min(1.0, (db + 60) / 60))
                pygame.draw.rect(surf, LANE_BG, (mx, cy + (30 + i * 26) * S, bar_w, 14 * S), border_radius=int(4 * S))
                pygame.draw.rect(surf, JUDGE_COLORS["PERFECT"] if db > -40 else JUDGE_COLORS["OK"], (mx, cy + (30 + i * 26) * S, bar_w * k, 14 * S), border_radius=int(4 * S))
                surf.blit(f.text(f"{db:5.0f} dB", f.small, DIM), (mx + bar_w + 10 * S, cy + (27 + i * 26) * S))
            if max(levels) < -70:
                for i, line in enumerate(wrap(f, "silent: on the XR18 set USB sends 17/18 to Main L/R (X-AIR Edit, Setup, Audio/MIDI)", f.small, 300 * S)[:3]):
                    surf.blit(f.text(line, f.small, JUDGE_COLORS["OK"]), (mx, cy + (90 + i * 18) * S))
        else:
            surf.blit(f.text(self.meter_error or "no signal yet", f.small, JUDGE_COLORS["MISS"] if self.meter_error else DIM), (mx, cy + 30 * S))
        # test take state
        if self.app.recorder.active and self.test_at is not None:
            f.center(surf, f"test take recording {3 - int(time.perf_counter() - self.test_at)}...", f.mid, (235, 70, 70), self.h - 92 * S)
        elif self.app.recorder.composing is not None and self.test_at is not None:
            f.center(surf, "rendering the test take's editions...", f.mid, JUDGE_COLORS["GOOD"], self.h - 92 * S)
        elif self.test_result:
            f.center(surf, self.test_result, f.mid, JUDGE_COLORS["PERFECT"] if "saved" in self.test_result else JUDGE_COLORS["MISS"], self.h - 92 * S)
        self.legend(surf, [("hihat", "size"), ("crash", "size"), ("snare", "test take 3 s"), ("kick", "back")],
                    keys="P computer camera share · [ ] corner · S social camera share · R rescan · Enter test take · Esc back · takes in ~/Movies/bathero")


# ---------------------------------------------------------------------------
EDITIONS = [("computer", "Computer edition (16:9)", "render again: the screen, the camera picture-in-picture"),
            ("social", "Social edition (9:16)", "render again: the screen on top, the iPhone under it")]


class EditScreen(Screen):
    """Pick a take (newest first) and what to make of it: an edition rendered again from its raws
    (both are made when a take stops), or a style for Claude Code to cut. Hi-hat / crash move
    through the options, snare starts, kick goes back; [ and ] change the take."""

    def __init__(self, app):
        super().__init__(app)
        self.takes = E.takes()
        self.take_i = 0
        self.sel = 0
        self.message = None

    def options(self):
        """[(key, label, blurb, kind)], kind "edition" or "claude"."""
        return [(k, l, b, "edition") for k, l, b in EDITIONS] + [(k, l, b, "claude") for k, l, b, _ in E.STYLES]

    def on_drum(self, inst):
        action = NAV.get(inst)
        if action == "next":
            self.sel = (self.sel + 1) % len(self.options())
        elif action == "prev":
            self.sel = (self.sel - 1) % len(self.options())
        elif action == "accept":
            self.accept()
        elif action == "back":
            self.app.go(ListScreen(self.app, "crash", 16))
        return True

    def on_key(self, key):
        if key in (pygame.K_ESCAPE, pygame.K_h):
            self.app.go(ListScreen(self.app, "crash", 16))
        elif key in (pygame.K_DOWN, pygame.K_j):
            self.sel = (self.sel + 1) % len(self.options())
        elif key in (pygame.K_UP, pygame.K_k):
            self.sel = (self.sel - 1) % len(self.options())
        elif key in (pygame.K_LEFTBRACKET, pygame.K_LEFT) and self.takes:
            self.take_i = (self.take_i + 1) % len(self.takes)
        elif key in (pygame.K_RIGHTBRACKET, pygame.K_RIGHT) and self.takes:
            self.take_i = (self.take_i - 1) % len(self.takes)
        elif key in (pygame.K_RETURN, pygame.K_l):
            self.accept()
        return True

    def accept(self):
        if not self.takes:
            return
        take = self.takes[self.take_i]
        key, label, blurb, kind = self.options()[self.sel]
        self.message = None
        if kind == "edition":
            if not E.has_raws(take):
                self.message = "this take has no raws (recorded before take folders): nothing to render from"
            elif not self.app.recorder.render(take, key):
                self.message = "the recorder is busy, try again in a moment"
        elif not self.app.editor.busy:
            if self.app.editor.start(take, key):
                self.app.go(HubScreen(self.app, 3))

    def draw(self, surf, fps):
        surf.fill(BG)
        S = self.s
        self.f.center(surf, "Takes: editions and Claude edits", self.f.large, TEXT, 60 * S)
        if not self.takes:
            self.f.center(surf, "No takes yet. Press V during play to record one.", self.f.mid, DIM, self.h * 0.42)
        else:
            take = self.takes[self.take_i]
            meta = E.sidecar_for(take) or {}
            levels = ", ".join(l["chart"] for l in meta.get("run_logs", [])) or "no levels logged"
            eds = ", ".join(sorted(E.editions(take))) or "no edition yet"
            self.f.center(surf, E.label(take), self.f.mid, ACCENT, 110 * S)
            self.f.center(surf, f"{meta.get('duration', 0):.0f} s · {levels}" + ("  · camera" if meta.get("camera") else ""),
                          self.f.small, DIM, 138 * S)
            self.f.center(surf, f"editions: {eds}  ·  take {self.take_i + 1} of {len(self.takes)}  ·  [ ] or ← → to change", self.f.small, DIM, 160 * S)
        opts = self.options()
        step = 60 * S if len(opts) * 60 * S <= self.h - 330 * S else (self.h - 330 * S) / len(opts)
        y = 210 * S
        for i, (key, label, blurb, kind) in enumerate(opts):
            selected = i == self.sel
            x = self.w * 0.18
            if selected:
                pygame.draw.rect(surf, lerp(LANE_BG, ACCENT, 0.18), (x - 20 * S, y - 8 * S, self.w * 0.64 + 40 * S, step - 8 * S), border_radius=int(10 * S))
                pygame.draw.rect(surf, ACCENT, (x - 20 * S, y - 8 * S, 6 * S, step - 8 * S), border_radius=int(3 * S))
            if i == len(EDITIONS):
                pygame.draw.line(surf, (50, 50, 60), (x, y - 12 * S), (self.w * 0.82, y - 12 * S))
            surf.blit(self.f.text(label, self.f.mid, ACCENT if selected else TEXT), (x, y))
            surf.blit(self.f.text(blurb, self.f.small, DIM), (x + 340 * S, y + 6 * S))
            y += step
        note = self.message or (self.app.editor.status if self.app.editor.busy else self.app.editor.error)
        if note:
            self.f.center(surf, note, self.f.small, JUDGE_COLORS["GOOD"] if self.app.editor.busy and not self.message else JUDGE_COLORS["MISS"],
                          self.h - 84 * S)
        self.legend(surf, [("hihat", "down"), ("crash", "up"), ("snare", "go"), ("kick", "back")],
                    keys="arrows or j k · Enter or l · Esc or h · everything lands in the take's folder in ~/Movies/bathero")


# ---------------------------------------------------------------------------
class SetupScreen(Screen):
    """Onboarding wizard: one step per zone of the kit (snare head, snare rim, ride bell...).
    Every distinct note number heard during a step is assigned to that zone."""

    def __init__(self, app, first_run=False):
        super().__init__(app)
        self.first_run = first_run
        self.lock = threading.Lock()
        self.step = 0
        self.captured = {k: [] for k in C.ZONE_KEYS}
        self.capture_start = None
        self.flash = None              # (wall_t, note, velocity)
        self.done_at = None

    @property
    def key(self):
        return C.ZONE_KEYS[self.step] if self.step < len(C.ZONE_KEYS) else None

    def on_note(self, note, velocity):       # drums are being captured here, never navigation
        with self.lock:
            key = self.key
            if key is None:
                return
            now = time.perf_counter()
            if self.capture_start is None:
                self.capture_start = now
            if note not in self.captured[key]:
                self.captured[key].append(note)
            self.flash = (now, note, velocity)
        self.app.sounds.play(C.ZONE[key].instrument, velocity)

    def advance(self):
        self.capture_start = None
        self.step += 1
        if self.step >= len(C.ZONE_KEYS):
            self.finish()

    def finish(self):
        kit = {}
        taken = set()
        for k in reversed(C.ZONE_KEYS):        # a number heard for two zones goes to the later one
            kit[k] = [n for n in C.expand_family(self.captured[k]) if n not in taken]
            taken.update(kit[k])
        self.app.set_kit(kit)
        save_kit(kit, self.app.args.kit) if self.app.args.kit else save_kit(kit)
        self.done_at = time.perf_counter()

    def on_key(self, key):
        if key == pygame.K_ESCAPE:
            self.app.go(HubScreen(self.app, 3))
        elif key in (pygame.K_RETURN, pygame.K_s):
            with self.lock:
                if self.done_at is None:
                    self.advance()
        elif key == pygame.K_BACKSPACE:
            with self.lock:
                if self.done_at is None and self.step > 0:
                    self.step -= 1
                    self.captured[self.key] = []
                    self.capture_start = None
        elif key in KEY_LANES and self.key is not None:
            self.on_note(C.DEFAULT_KIT[self.key][0], 100)   # keyboard stands in for a pad
        return True

    def update(self):
        with self.lock:
            if self.done_at is None and self.capture_start is not None and \
                    time.perf_counter() - self.capture_start >= CAPTURE_S:
                self.advance()
        if self.done_at is not None and time.perf_counter() - self.done_at > 1.2:
            self.app.go(SoundcheckScreen(self.app, first_run=self.first_run))

    def draw(self, surf, fps):
        surf.fill(BG)
        with self.lock:
            step, key, captured = self.step, self.key, {k: list(v) for k, v in self.captured.items()}
            capture_start, flash, done_at = self.capture_start, self.flash, self.done_at
        now = time.perf_counter()
        S = self.s
        self.f.center(surf, "Set up your kit", self.f.large, TEXT, 44 * S)
        if not self.app.midi_in:
            self.f.center(surf, "No MIDI input connected. Start with --port NAME, or press Esc and play with keys 1-4.",
                          self.f.small, JUDGE_COLORS["MISS"], self.h - 76 * S)
            self.f.center(surf, "Any number key here stands in for the pad and assigns its factory note number.",
                          self.f.small, DIM, self.h - 54 * S)

        # left: every zone with its state
        x0, y0, row = 40 * S, 92 * S, 31 * S
        last_pad = None
        y = y0
        for i, zk in enumerate(C.ZONE_KEYS):
            z = C.ZONE[zk]
            if z.pad != last_pad:
                if last_pad is not None:
                    y += 6 * S
                last_pad = z.pad
            color = C.COLORS[z.instrument]
            got = captured[zk]
            if done_at is not None or i < step:
                dot, tcol = (color if got else DIM), (TEXT if got else DIM)
                note_txt = "/".join(map(str, got)) if got else "skipped"
            elif i == step:
                dot, tcol = ACCENT, ACCENT
                note_txt = "/".join(map(str, got)) if got else "..."
            else:
                dot, tcol, note_txt = DIM, DIM, ""
            cy = y + row / 2
            if i == step and done_at is None:
                pygame.draw.rect(surf, LANE_BG, (x0 - 14 * S, y, 360 * S, row), border_radius=int(8 * S))
            pygame.draw.circle(surf, dot, (int(x0), int(cy)), int(6 * S), 0 if (got or i == step) else 2)
            surf.blit(self.f.text(z.label, self.f.small, tcol), (x0 + 16 * S, cy - 10 * S))
            surf.blit(self.f.text(note_txt, self.f.small, DIM if i != step else ACCENT), (x0 + 200 * S, cy - 10 * S))
            y += row

        if done_at is not None:
            self.f.center(surf, "Kit saved", self.f.big, JUDGE_COLORS["PERFECT"], self.h * 0.42, self.w * 0.65)
            self.f.center(surf, describe(self.app.kit), self.f.small, DIM, self.h * 0.42 + 60 * S, self.w * 0.65)
            return

        z = C.ZONE[key]
        color = C.COLORS[z.instrument]
        cx, cy = self.w * 0.65, self.h * 0.47
        r = 96 * S
        k = 0.0
        if flash and now - flash[0] < 0.25:
            k = 1 - (now - flash[0]) / 0.25
            pygame.draw.circle(surf, lerp(LANE_BG, color, k), (int(cx), int(cy)), int(r + 40 * S * (1 - k)))
        pygame.draw.circle(surf, color, (int(cx), int(cy)), int(r), max(2, int(4 * S)))
        self.f.center(surf, z.pad.upper(), self.f.large, lerp(color, BG, k), cy - 16 * S, cx)   # dark on the flash
        self.f.center(surf, z.part.upper() if z.part else "", self.f.mid, lerp(TEXT, BG, k), cy + 24 * S, cx)
        self.f.center(surf, f"step {step + 1} of {len(C.ZONE_KEYS)}", self.f.small, DIM, cy - r - 62 * S, cx)
        self.f.center(surf, z.prompt, self.f.mid, TEXT, cy - r - 34 * S, cx)

        if captured[key]:
            fam = [n for n in C.expand_family(captured[key]) if n not in captured[key]]
            got = "got note " + ", ".join(map(str, captured[key])) + (f"  (+ {', '.join(map(str, fam))} same zone)" if fam else "")
            self.f.center(surf, got, self.f.mid, JUDGE_COLORS["PERFECT"], cy + r + 36 * S, cx)
            if flash:
                self.f.center(surf, f"last: note {flash[1]} · velocity {flash[2]}", self.f.small, DIM, cy + r + 66 * S, cx)
            if capture_start is not None:
                frac = max(0.0, 1 - (now - capture_start) / CAPTURE_S)
                bw = 300 * S
                pygame.draw.rect(surf, LANE_BG, (cx - bw / 2, cy + r + 91 * S, bw, 6 * S))
                pygame.draw.rect(surf, color, (cx - bw / 2, cy + r + 91 * S, bw * frac, 6 * S))
                self.f.center(surf, "keep hitting, moving on...", self.f.small, DIM, cy + r + 114 * S, cx)
        else:
            self.f.center(surf, "waiting...", self.f.mid, DIM, cy + r + 36 * S, cx)
        self.f.center(surf, "Enter next · S skip a zone you don't have · Backspace redo previous · Esc cancel",
                      self.f.small, DIM, self.h - 28 * S)


# ---------------------------------------------------------------------------
class SoundcheckScreen(Screen):
    """Hit every zone: its row lights up, plays the pad's sound and shows the note number
    and velocity. Once every assigned zone has been heard, the snare continues and the
    kick redoes the wizard."""

    def __init__(self, app, first_run=False):
        super().__init__(app)
        self.first_run = first_run
        self.lock = threading.Lock()
        self.heard = {k: None for k in C.ZONE_KEYS}       # zone -> (wall_t, note, velocity)
        self.unknown = None                                # (wall_t, note, velocity) for unassigned pads
        self.ghost = None                                  # (wall_t, note, velocity, why) for dropped notes

    def on_ghost(self, note, velocity, why):
        with self.lock:
            self.ghost = (time.perf_counter(), note, velocity, why)

    def needed(self):
        return [k for k in C.ZONE_KEYS if self.app.kit.get(k)]

    def all_heard(self):
        return all(self.heard[k] for k in self.needed())

    def on_note(self, note, velocity):
        zk = self.app.zone_for(note)
        now = time.perf_counter()
        with self.lock:
            if zk is None:
                self.unknown = (now, note, velocity)
                return
            inst = C.ZONE[zk].instrument
            navigate = self.all_heard() and NAV.get(inst) in ("accept", "back")
            self.heard[zk] = (now, note, velocity)
        if navigate:
            self.app.nav_hit(note, velocity)
        else:
            self.app.sounds.play(inst, velocity)

    def on_drum(self, inst):
        action = NAV.get(inst)
        if action == "accept":
            self.done()
        elif action == "back":
            self.app.go(SetupScreen(self.app, first_run=self.first_run))
        return True

    def on_key(self, key):
        if key in (pygame.K_RETURN, pygame.K_ESCAPE):
            self.done()
        elif key == pygame.K_BACKSPACE:
            self.app.go(SetupScreen(self.app, first_run=self.first_run))
        elif key in KEY_LANES and KEY_LANES[key] < len(C.INSTRUMENTS):
            inst = C.INSTRUMENTS[KEY_LANES[key]]
            notes = C.kit_notes(self.app.kit, inst) or C.DEFAULT_KIT[C.INSTRUMENT_ZONES[inst][0]]
            self.on_note(notes[0], 100)
        return True

    def done(self):
        self.app.go(self.app.home(0) if self.first_run else HubScreen(self.app, 3))

    def draw(self, surf, fps):
        surf.fill(BG)
        with self.lock:
            heard = dict(self.heard)
            unknown = self.unknown
            ghost = self.ghost
        now = time.perf_counter()
        S = self.s
        kit = self.app.kit
        ready = self.all_heard()
        self.f.center(surf, "Soundcheck", self.f.large, TEXT, 44 * S)
        self.f.center(surf, "hit every zone: it should light up and sound like it", self.f.small, DIM, 82 * S)

        cols, side, gap = 4, 40 * S, 14 * S
        cw = (self.w - 2 * side - (cols - 1) * gap) / cols
        row, head = 24 * S, 34 * S
        top = 104 * S
        card_h = head + 3 * row + 10 * S
        for i, (pad, zones) in enumerate(C.PADS):
            col, r_ = i % cols, i // cols
            x, y = side + col * (cw + gap), top + r_ * (card_h + gap)
            color = C.COLORS[C.ZONE[zones[0]].instrument]
            assigned = any(kit.get(zk) for zk in zones)
            hot = max((max(0.0, 1 - (now - heard[zk][0]) / 0.3) for zk in zones if heard[zk]), default=0.0)
            rect = pygame.Rect(int(x), int(y), int(cw), int(card_h))
            pygame.draw.rect(surf, lerp(lerp(LANE_BG, color, 0.10 if assigned else 0.0), color, 0.35 * hot), rect, border_radius=int(12 * S))
            pygame.draw.rect(surf, color if assigned else (60, 60, 70), rect, max(1, int(2 * S)), border_radius=int(12 * S))
            pygame.draw.circle(surf, color if assigned else (60, 60, 70), (int(x + 18 * S), int(y + head / 2)), int(7 * S))
            surf.blit(self.f.text(pad, self.f.mid, TEXT if assigned else DIM), (x + 32 * S, y + head / 2 - 13 * S))
            if all(heard[zk] for zk in zones if kit.get(zk)) and assigned:
                surf.blit(self.f.text("✓", self.f.mid, JUDGE_COLORS["PERFECT"]), (x + cw - 30 * S, y + head / 2 - 13 * S))
            for j, zk in enumerate(zones):
                z = C.ZONE[zk]
                ry = y + head + j * row
                notes = kit.get(zk, [])
                h = heard[zk]
                k = max(0.0, 1 - (now - h[0]) / 0.3) if h else 0.0
                if k > 0:
                    pygame.draw.rect(surf, lerp(LANE_BG, color, 0.6 * k), (x + 6 * S, ry, cw - 12 * S, row), border_radius=int(6 * S))
                label = f"{z.part or 'pad':<6}{'/'.join(map(str, notes)) if notes else '-'}"
                surf.blit(self.f.text(label, self.f.small, TEXT if notes else DIM), (x + 14 * S, ry + 3 * S))
                if h:
                    right = f"✓ {h[1]} v{h[2]}" if now - h[0] < 2.0 else "✓"
                    ts = self.f.text(right, self.f.small, color)
                else:
                    ts = self.f.text("waiting" if notes else "", self.f.small, DIM)
                surf.blit(ts, (x + cw - 14 * S - ts.get_width(), ry + 3 * S))

        bottom = top + 2 * card_h + gap
        if C.kit_notes(kit, "hihat"):
            draw_hihat_state(surf, self.f, self.app.ghosts, self.w / 2, bottom + 52 * S, S)

        my = self.h * 0.84
        if unknown and now - unknown[0] < 2.5:
            self.f.center(surf, f"note {unknown[1]} is not assigned to any zone (vel {unknown[2]})",
                          self.f.mid, JUDGE_COLORS["MISS"], my - 30 * S)
            self.f.center(surf, "if that pad should count, redo the setup and hit it during its zone", self.f.small, DIM, my)
        elif ghost and now - ghost[0] < 1.5:
            self.f.center(surf, f"ignored note {ghost[1]} vel {ghost[2]}: {ghost[3]}", self.f.small, DIM, my)
        elif ready:
            self.f.center(surf, "All zones heard.", self.f.mid, JUDGE_COLORS["PERFECT"], my - 30 * S)

        if ready:
            self.legend(surf, [("snare", "continue"), ("kick", "redo setup")], keys="Enter continue · Backspace redo setup")
        else:
            self.f.center(surf, "Enter skip · Backspace redo setup · keys 1-7 stand in for the pads", self.f.small, DIM, self.h - 28 * S)


# ---------------------------------------------------------------------------
class PlayScreen(Screen):
    def __init__(self, app, cat, index):
        super().__init__(app)
        self.cat, self.index = cat, index
        self.chart = app.chart_for(cat, index).at_rate(app.rate)
        self.lanes, self.by_note = build_lanes(self.chart, app.kit)
        # scroll speed follows the tempo so a beat is always the same distance on screen
        self.game = Game(self.chart, self.lanes, self.by_note, offset_ms=app.offset_ms, speed=app.rate,
                         sounds=app.sounds, guide=app.guide,
                         log=app.runlog, dyn_scales=app.dyn_scales)
        self.game.metronome_mode = app.metronome_mode
        self.game.metro_volume = app.metro_volume
        app.runlog.start(self.chart, self.lanes, app.kit, app.settings, {
            "profile": app.profile_id, "offset_ms": app.offset_ms, "guide": self.game.guide, "metronome": app.metronome_mode,
            "backing": app.backing_on, "drum_sounds": app.sounds.drums, "dyn_thresholds": self.game.dyn_thresholds(),
            "ghost_filter": {"hihat_min_velocity": GH.HIHAT_MIN_VELOCITY, "chick_splash_ms": GH.CHICK_SPLASH_MS,
                             "chick_splash_velocity_min": GH.CHICK_SPLASH_VELOCITY_MIN,
                             "pedal_motion_cc": GH.PEDAL_MOTION_CC, "pedal_motion_ms": GH.PEDAL_MOTION_MS,
                             "pedal_motion_velocity_min": GH.PEDAL_MOTION_VELOCITY_MIN,
                             "pedal_settle_ms": GH.PEDAL_SETTLE_MS, "pedal_settle_velocity_max": GH.PEDAL_SETTLE_VELOCITY_MAX,
                             "crosstalk_one_stroke_ms": GH.CROSSTALK_ONE_STROKE_MS, "crosstalk_near_ms": GH.CROSSTALK_NEAR_MS, "crosstalk_near_velocity_max": GH.CROSSTALK_NEAR_VELOCITY_MAX,
                             "zone_crosstalk": GH.ZONE_CROSSTALK, "kick_bounce": GH.KICK_BOUNCE,
                             "snare_bounce": GH.SNARE_BOUNCE, "any_min_velocity": GH.ANY_MIN_VELOCITY},
        })
        for name, track in app.tracks_for(self.chart, app.prog_for(cat, index)).items():
            self.game.set_track(name, track, enabled=(app.backing_on if name == "backing" else True))
        self.renderer = Renderer(self.game, app.size, app.fonts, app.ghosts)
        self.recorded = False
        self.finished_at = None
        self.hints = []               # what went wrong, for the results box (attempt())
        self.offset_fit = None        # latency.fit_game: the offset line of the results box
        self.loop_digits = None       # the phrases typed after l, or None when no gesture is pending
        self.loop_at = 0.0
        self.armed = False            # the intro is up: the game waits, paused at its count-in, for a snare hit
        self.armed_at = 0.0
        self.start_at = None          # wall time of the snare hit that starts it (MIDI thread -> update)
        self.editor = None            # songedit.SongEditor while the song is being edited (E, paused)
        self.game.reset()
        self.apply_loop()             # a loop marked before a tempo change keeps running
        if app.intro_on:
            self.arm()

    def on_resize(self):
        self.renderer = Renderer(self.game, self.app.size, self.app.fonts, self.app.ghosts)
        self.renderer.finished_at = self.finished_at
        self.renderer.intro = self.intro() if self.armed else None
        self.renderer.edit = self.editor

    # --- the song editor (E while paused, imported songs only): bathero.songedit -------------
    def start_editing(self):
        g = self.game
        if not self.chart.song:
            self.app.toasts.add("only imported songs can be edited", DIM, key="edit")
            return
        if g.finished:
            self.app.toasts.add("the song is over: R plays it again, pause it there to edit", DIM, key="edit")
            return
        if self.armed:
            self.armed, self.start_at = False, None
            self.renderer.intro = None
        if not g.paused:
            g.toggle_pause()
        self.loop_digits = None
        self.editor = SE.SongEditor(self)
        self.editor.view(g.song_time())
        self.renderer.edit = self.editor
        self.app.runlog.add("edit", on=True)
        self.app.toasts.add("editing the song · E when done", ACCENT, key="edit")

    def stop_editing(self, play=False):
        """Done: the playhead parks a bar before the view (the jumps' run-up), paused unless play."""
        ed, g, ch = self.editor, self.game, self.chart
        self.editor = self.renderer.edit = None
        t = max(0.0, g.song_time())
        g.seek(t, t - ch.beat_time(ch.beat_pos(t) - 4))
        self.app.runlog.add("edit", on=False, saves=ed.saved)
        if ed.saved:
            self.app.toasts.add(f"song saved · {len(g.notes)} notes" + (" · playing" if play else " · space plays"), ACCENT, key="edit")
        if play:
            g.toggle_pause()

    def on_mouse(self, ev):
        if self.editor is not None:
            self.editor.on_mouse(ev)

    # --- the intro: the level explains itself, a snare hit starts it (S) ---------------------
    def intro(self):
        """What the renderer's card shows: the level, where it sits, what it teaches."""
        lang = self.app.settings.get("coach_language", "es")
        learn, notes, fx, jd = IN.intro_for(self.chart, lang)
        items = self.app.items_for(self.cat)
        course = C.COURSE.get(self.cat)
        where = course.name if course else next(t for c, t, _ in CATEGORIES if c == self.cat)
        return {"title": self.chart.title, "where": f"{where}  ·  {self.index + 1} / {len(items)}",
                "facts": fx, "learn": learn, "notes": notes, "judged": jd, "lang": lang,
                "practice": self.app.practice}

    def arm(self):
        """Hold the level at the top of its count-in until the snare is hit: paused from the
        start, so nothing plays and no note can be missed while the intro is read."""
        g = self.game
        with g.lock:
            if g.paused_at is None:
                g.paused_at = time.perf_counter()
        self.armed, self.armed_at, self.start_at = True, time.perf_counter(), None
        self.renderer.intro = self.intro()

    def start(self, wall=None, by="snare"):
        """The snare was hit (or Enter): the count-in starts now, dated at the hit."""
        g = self.game
        wall = time.perf_counter() if wall is None else wall
        with g.lock:
            if g.paused_at is not None:
                g.paused_total += wall - g.paused_at
                g.paused_at = None
        self.armed, self.start_at = False, None
        self.renderer.intro = None
        if self.app.runlog.header is not None:
            self.app.runlog.header["started"] = time.time()   # the minutes count from here, not from the intro
        self.app.runlog.add("start", by=by)

    def nav_ready(self):
        return self.finished_at is not None and time.perf_counter() - self.finished_at > RESULTS_GRACE_S

    def on_note(self, note, velocity):        # MIDI thread: judge immediately while playing
        ed = self.editor
        if ed is not None:
            # editing: every pad sounds, and is where the selection goes
            inst = self.app.instrument_for(note)
            if inst is None or velocity < NAV_SOUND_MIN_VELOCITY:
                return "edit"
            self.app.sounds.play(inst, velocity, 0.8)
            if velocity >= NAV_MIN_VELOCITY:
                ed.on_hit(inst, velocity)
            return "edit"
        if self.armed:
            # the intro: every pad sounds, only the snare starts the level (the main thread does it)
            inst = self.app.instrument_for(note)
            if inst is None or velocity < NAV_SOUND_MIN_VELOCITY:
                return "armed"
            self.app.sounds.play(inst, velocity, 0.8)
            now = time.perf_counter()
            if inst == "snare" and velocity >= NAV_MIN_VELOCITY and now - self.armed_at > ARM_GRACE_S and self.start_at is None:
                self.start_at = now
                self.app.legend_flash["snare"] = now
            return "armed"
        if self.nav_ready():
            self.app.nav_hit(note, velocity)
            return "nav"
        j = self.game.hit(note, velocity, art=self.app.ghosts.articulation_for(note))
        return f"{j}" if j else "unmapped"

    def on_drum(self, inst):
        if not self.nav_ready():
            return True
        action = NAV.get(inst)
        if action == "accept":
            self.next_level()
        elif action == "next":
            self.retry()
        elif action == "back":
            self.to_list()
        return True

    def on_key(self, key):
        g = self.game
        ed = self.editor
        if ed is not None:
            if ed.on_key(key):
                pass
            elif key == pygame.K_e:
                self.stop_editing()
            elif key == pygame.K_ESCAPE:
                if ed.sel:
                    ed.sel = {}
                else:
                    self.stop_editing()
            elif key == pygame.K_SPACE:
                self.stop_editing(play=True)
            elif key in KEY_LANES:
                bounds = self.phrases()
                i = KEY_LANES[key]
                if i < len(bounds) - 1:
                    ed.view(bounds[i])
                    self.app.toasts.add(f"phrase {(i + 1) % 10}  ·  {self.chart.place(bounds[i])}", ACCENT, key="phrase")
                else:
                    self.app.toasts.add(self.no_phrase(i, len(bounds) - 1), DIM, key="phrase")
            return True
        if key == pygame.K_e:
            self.start_editing()
        elif key == pygame.K_ESCAPE:
            self.to_list()
        elif key == pygame.K_s:
            # the intro on or off, saved; off while it is up, the level starts right away
            self.app.toggle_intro()
            if self.armed and not self.app.intro_on:
                self.start(by="key")
        elif self.armed and key in (pygame.K_SPACE, pygame.K_RETURN):
            self.start(by="key")
        elif key == pygame.K_SPACE:
            if not g.finished:
                g.toggle_pause()
                self.app.runlog.add("pause", paused=g.paused)
        elif key == pygame.K_r:
            self.retry()
        elif key == pygame.K_RETURN and g.finished:
            self.next_level()
        elif key in (pygame.K_LEFTBRACKET, pygame.K_RIGHTBRACKET):
            # tempo: everything (notes, metronome, backing, the record) slows down or speeds up;
            # the level restarts at the new tempo
            step = 0.05 if key == pygame.K_RIGHTBRACKET else -0.05
            self.app.rate = round(min(2.0, max(0.3, self.app.rate + step)), 2)
            self.retry()
        elif key in (pygame.K_COMMA, pygame.K_PERIOD):
            self.app.set_offset(g.offset_ms + (5 if key == pygame.K_PERIOD else -5))   # remembered across runs
        elif key == pygame.K_g:
            g.guide = self.app.guide = not g.guide
        elif key == pygame.K_d:
            self.app.toggle_scroll()
        elif key == pygame.K_k:
            self.app.set_drum_sounds(not self.app.sounds.drums)
        elif key == pygame.K_b:
            self.app.backing_on = not self.app.backing_on
            g.enable_track("backing", self.app.backing_on)
        elif key == pygame.K_m:
            modes = ["full", "beats", "off"]
            self.app.metronome_mode = modes[(modes.index(self.app.metronome_mode) + 1) % 3]
            g.metronome_mode = self.app.metronome_mode
            if self.app.metronome_mode == "off":
                g.enable_track("metronome", False)
            else:
                g.set_track("metronome", self.app.tracks_for(self.chart, self.app.prog_for(self.cat, self.index))["metronome"], True)
        elif key == pygame.K_p:
            self.app.practice = not self.app.practice
            self.app.toasts.add("practice: this run saves no progress" if self.app.practice else
                                ("practice off · this run already jumped, R starts a clean one" if g.seeked
                                 else "practice off: this run counts"), ACCENT, key="practice")
        elif g.finished:
            pass                                          # the results screen is no place for the transport
        elif key == pygame.K_l:
            self.loop_digits, self.loop_at = [], time.perf_counter()
        elif key == pygame.K_BACKSLASH:
            self.toggle_loop()
        elif key in KEY_LANES:
            i = KEY_LANES[key]                            # 1..9, 0 -> phrases 0..9
            if self.loop_digits is None:
                self.phrase_jump(i)
            else:
                self.loop_digits.append(i)
                self.loop_at = time.perf_counter()
                if len(self.loop_digits) == 2:
                    a, b = self.loop_digits
                    self.loop_digits = None
                    self.set_loop(a, b)
        return True

    # --- transport ---------------------------------------------------------------
    def phrases(self):
        return self.chart.phrase_bounds()

    def phrase_jump(self, i, count_in=True):
        """Jump to phrase i (0-based), preceded by one silent bar of count-in so the notes
        have time to come down. The chart is silent during it; the metronome keeps counting.
        A key past the level's last phrase (a short level has fewer than ten) does nothing but say so."""
        bounds = self.phrases()
        n = len(bounds) - 1
        if i >= n:
            self.app.toasts.add(self.no_phrase(i, n), DIM, key="phrase")
            return None
        t = bounds[i]
        pre = t - self.chart.beat_time(self.chart.beat_pos(t) - 4) if count_in else 0.0
        self.game.seek(t, pre)
        self.app.toasts.add(f"phrase {(i + 1) % 10}  ·  {self.chart.place(t)}", ACCENT, key="phrase")
        self.app.runlog.add("seek", phrase=i + 1, chart_t=round(t, 4))
        return i

    def no_phrase(self, i, n):
        bars = self.chart.bars
        each = "one bar each" if n == bars else (f"{bars // n} bars each" if bars % n == 0 else f"{bars} bars")
        return f"no phrase {(i + 1) % 10}: this level has {n} phrases, {each}"

    def loop_span(self):
        """The marked loop as (start, end, label) in chart seconds, or None: phrases typed with l
        (clamped to the phrases this chart has) or bars marked on the Launchpad."""
        ch = self.chart
        if self.app.loop_bars is not None:
            a, b = self.app.loop_bars
            b = min(b, ch.bars)
            if a >= b:
                return None
            return ch.beat_time(a * 4), ch.beat_time(b * 4), f"bars {a + 1}-{b}"
        rng = self.app.loop_range
        if rng is None:
            return None
        bounds = self.phrases()
        n = len(bounds) - 1
        a, b = min(rng[0], n - 1), min(rng[1], n - 1)
        return bounds[a], bounds[b + 1], f"{(a + 1) % 10}-{(b + 1) % 10}"

    def apply_loop(self):
        """Hand the marked range to the game while the loop is on."""
        span = self.loop_span()
        self.game.set_loop(span[:2] if span is not None and self.app.loop_on else None)

    def jump_to_loop(self):
        if self.app.loop_bars is not None:
            self.bar_jump(self.app.loop_bars[0])
        else:
            self.phrase_jump(self.app.loop_range[0])

    def bar_jump(self, bar):
        """Jump to the start of bar `bar` (0-based) with the phrase keys' bar of run-up; a
        song's count bars (-2, -1) are their own run-up: the count from there."""
        if self.editor is not None:
            self.editor.view_bar(max(0, bar))
            return
        ch = self.chart
        t = ch.beat_time(bar * 4)
        if bar < 0:
            self.game.seek(t, 0.0)
            self.app.toasts.add("the count", ACCENT, key="phrase")
            self.app.runlog.add("seek", bar=bar + 1, chart_t=round(t, 4))
            return
        self.game.seek(t, t - ch.beat_time(ch.beat_pos(t) - 4))
        self.app.toasts.add(ch.place(t), ACCENT, key="phrase")
        self.app.runlog.add("seek", bar=bar + 1, chart_t=round(t, 4))

    def set_loop(self, a, b):
        n = len(self.phrases()) - 1
        a, b = sorted((a, b))
        if a >= n:
            self.app.toasts.add(self.no_phrase(a, n), DIM, key="loop")
            return
        b = min(b, n - 1)                                 # a loop past the end stops at the last phrase
        self.app.loop_range, self.app.loop_bars, self.app.loop_on = (a, b), None, True
        self.apply_loop()
        self.phrase_jump(a)
        self.app.toasts.add(f"loop {(a + 1) % 10}-{(b + 1) % 10} on", ACCENT, key="loop")
        self.app.runlog.add("loop", on=True, phrases=[a + 1, b + 1])

    def toggle_loop(self):
        span = self.loop_span()
        if span is None:
            self.app.toasts.add("no loop marked: l35 loops phrases 3 to 5", DIM, key="loop")
            return
        self.app.loop_on = not self.app.loop_on
        self.apply_loop()
        if self.app.loop_on and not (span[0] <= self.game.song_time() < span[1]):
            self.jump_to_loop()                           # outside the range: go there, or it never wraps
        self.app.toasts.add(f"loop {span[2]} {'on' if self.app.loop_on else 'off'}", ACCENT, key="loop")
        self.log_loop()

    def log_loop(self):
        if self.app.loop_bars is not None:
            self.app.runlog.add("loop", on=self.app.loop_on, bars=[self.app.loop_bars[0] + 1, self.app.loop_bars[1]])
        elif self.app.loop_range is not None:
            a, b = self.app.loop_range
            self.app.runlog.add("loop", on=self.app.loop_on, phrases=[a + 1, b + 1])

    def clear_loop(self):
        self.app.loop_range, self.app.loop_bars, self.app.loop_on = None, None, False

    # --- the Launchpad (Twister bank 2): GroupCtl's project explorer over the level -------
    def set_bar_loop(self, a, b):
        """The Launchpad's loop gesture: marks bars a..b-1; on or off stays as it was (MUTE)."""
        self.app.loop_bars, self.app.loop_range = (a, b), None
        self.apply_loop()
        span = self.loop_span()
        if self.app.loop_on and span is not None and not (span[0] <= self.game.song_time() < span[1]):
            self.jump_to_loop()
        self.app.toasts.add(f"loop {span[2]} marked" + (" · on" if self.app.loop_on else " · MUTE starts it"),
                            ACCENT, key="loop")
        self.log_loop()

    def on_launchpad(self, kind, name):
        ex, g = self.app.lp_explorer, self.game
        if kind == "top":
            if name in ("session", "user1"):
                bpp = ex.zoom(name == "session")
                if bpp is not None:
                    self.app.toasts.add(f"launchpad: {bpp} bar{'s' if bpp > 1 else ''} a pad", ACCENT, key="lp_zoom")
            elif name in ("left", "right"):
                ex.flip(-1 if name == "left" else 1)
            elif name == "mixer":
                ex.toggle_selecting()
                if ex.selecting:
                    self.app.toasts.add("loop: the first pad, then the last", ACCENT, key="loop")
            return
        if kind == "side":
            if name == "stop":
                if self.editor is not None:
                    self.stop_editing(play=True)
                elif self.armed:
                    self.start(by="launchpad")
                elif not g.finished:
                    g.toggle_pause()
                    self.app.runlog.add("pause", paused=g.paused)
            elif name == "mute" and not g.finished:
                self.toggle_loop()
            return
        if kind == "pad" and not g.finished:
            r = ex.pad(name)
            if r is None:
                return
            if r[0] == "jump":
                if self.armed and self.editor is None:
                    self.start(by="launchpad")
                self.bar_jump(r[1])
            elif r[0] == "loop" and r[1][1] > 0:
                self.set_bar_loop(max(0, r[1][0]), r[1][1])           # a loop never takes the count

    def launchpad_frame(self):
        ch, g, ex = self.chart, self.game, self.app.lp_explorer
        lead = SONG_COUNT_BEATS // 4 if ch.song else 0
        cached = getattr(self, "_lp_colours", None)
        if cached is None:
            if ch.sections:                                # a song's sections, in the user's colours
                starts = [b for b, _ in ch.sections]
                idx = [max(0, bisect.bisect_right(starts, i) - 1) for i in range(ch.bars)]
                colours = [LP.SECTION_COLORS.get(ch.sections[k][1], LP.OTHER_SECTION) for k in idx]
                cached = self._lp_colours = (colours, idx)
            else:
                cached = self._lp_colours = ([LP.PHRASE_COLORS[(ch.phrase_at(ch.beat_time(i * 4)) or 0) % len(LP.PHRASE_COLORS)]
                                              for i in range(ch.bars)], None)
        colours, groups = cached
        playing = not (self.armed or g.paused or g.finished)
        bar = None
        if playing or self.editor is not None:            # editing: the view's bar blinks
            t = g.song_time()
            if g.count_in_end is not None and t < g.count_in_end:
                t = g.count_in_end                        # the run-up of a jump: where it lands, as the screen shows
            bar = int(ch.beat_pos(max(t, 0.0)) // 4 + 1e-6)
            if ch.song and t < 0:                          # the count's own pads
                bar = max(-lead, int(math.floor(ch.beat_pos(t) / 4 + 1e-6)))
        span = self.loop_span()
        loop_bars = None
        if span is not None:
            loop_bars = (int(round(ch.beat_pos(span[0]) / 4)), int(round(ch.beat_pos(span[1]) / 4)))
        ex.grid(colours, loop_bars, bar, ch.key, lead, groups)
        return ex.frame(playing, self.app.loop_on and span is not None)

    def to_list(self):
        self.leave()
        self.clear_loop()
        if self.app.session is not None:
            self.app.session = None
            self.app.rate = 1.0
            self.app.go(CoachScreen(self.app))
            return
        self.app.go(list_screen(self.app, self.cat, self.index))

    def retry(self):
        self.leave()
        self.app.go(PlayScreen(self.app, self.cat, self.index))

    def next_level(self):
        self.leave()
        self.clear_loop()
        if self.app.session_advance():
            return
        nxt = self.index + 1
        if nxt < len(self.app.items_for(self.cat)):
            self.app.go(PlayScreen(self.app, self.cat, nxt))
        else:
            self.app.go(list_screen(self.app, self.cat, self.index))

    def leave(self):
        if self.editor is not None:
            self.stop_editing()
        self.game.stop_tracks()
        self.app.sounds.stop_jingle()                     # the jingle must not bleed into the next screen
        self.record()
        if self.app.args.log:
            self.game.write_csv(self.app.args.log)
        if self.game.hits:
            st = self.game.stats()
            if self.offset_fit:
                st["offset_fit"] = {k: v for k, v in self.offset_fit.items() if k != "curve"}
            self.app.runlog.write(st)                    # once, off the hot path
        else:
            self.app.runlog.header = None

    def record(self):
        # a run that used the transport, or that asked for practice with P, is a rehearsal:
        # its notes were played out of order or several times, so its stars would be a lie
        if self.recorded or not self.game.hits or self.app.practice or self.game.seeked:
            return
        st = self.game.stats()
        best = self.app.results.get(self.chart.key)
        if best is None or st["grade"] > best.get("grade", -1):
            keep = {k: st[k] for k in ("stars", "grade", "accuracy", "mean_ms", "std_ms", "hit", "notes")}
            keep["when"] = time.time()
            self.app.results[self.chart.key] = keep
        self.app.results[self.chart.key]["tries"] = self.app.tries.get(self.chart.key, 0)
        self.app.save_results()
        self.recorded = True

    def attempt(self):
        """The level just ended: count it as an attempt at five stars (a rehearsal is not one), and
        when it falls short work out what went wrong (hints.analyse) for the results box. Five
        stars reset the count."""
        st = self.game.stats()
        if self.game.seeked:
            return
        key = self.chart.key
        if st["stars"] >= 5:
            self.app.tries[key] = 0
            return
        self.app.tries[key] = self.app.tries.get(key, 0) + 1
        notes, strays = HI.from_game(self.game)
        self.hints = HI.hint_for(notes, strays, self.app.settings.get("coach_language", "es"),
                                 self.chart.dynamics, self.chart.expression)
        self.renderer.hints = (self.app.tries[key], self.hints)

    def fit_offset(self):
        """The results box's offset line: the offset that would have scored this run best and the
        one that scores the recent runs best together (latency.fit_game). Never costs the screen."""
        try:
            self.offset_fit = LA.fit_game(self.game, self.app.offset_ms, self.app.profile_id)
        except Exception as e:                            # a malformed old log must not hide the results
            print("offset fit failed:", e)
            self.offset_fit = None
        self.renderer.offset_fit = self.offset_fit

    def update(self):
        if self.editor is not None:
            self.editor.update()
            return
        if self.armed:
            if self.start_at is not None:
                self.start(self.start_at)
            else:
                return                                    # nothing moves until the snare
        if self.loop_digits is not None and time.perf_counter() - self.loop_at > LOOP_GESTURE_S:
            self.loop_digits = None
        self.game.update()
        if self.game.finished:
            if self.finished_at is None:
                self.attempt()
                self.fit_offset()
            self.record()
            if self.finished_at is None:
                self.finished_at = time.perf_counter()
                self.renderer.finished_at = self.finished_at
                self.game.stop_tracks()                       # the jingle takes over from the backing
                self.app.sounds.play_jingle(self.game.stats()["stars"])

    def draw(self, surf, fps):
        self.renderer.top_inset = self.app.badge_height()
        self.renderer.scroll_fixed = self.app.scroll_fixed
        if self.renderer.intro is not None:
            self.renderer.intro["practice"] = self.app.practice        # P while the intro is up
        span = self.loop_span()
        self.renderer.transport = {"practice": self.app.practice,
                                   "span": None if span is None else span[:2],
                                   "label": None if span is None else span[2],
                                   "pending": None if self.loop_digits is None else
                                   "".join(str((i + 1) % 10) for i in self.loop_digits)}
        self.renderer.draw(surf, fps)
        ss = self.app.session
        if ss is not None:
            cat, index, rate, reps, why = ss["items"][ss["pos"]]
            S = self.s
            label = f"session {ss['name']}  ·  {ss['pos'] + 1}/{len(ss['items'])}" + (f"  ·  rep {ss['rep']}/{reps}" if reps > 1 else "")
            if why and not self.game.finished:
                label += f"  ·  {why}"
            self.f.center(surf, label, self.f.small, ACCENT, self.h - (64 if self.game.finished else 14) * S)
        if self.game.finished:
            self.legend(surf, [("snare", "next"), ("hihat", "retry"), ("kick", "back")], y=self.h - 30 * self.s,
                        keys=None)


# ---------------------------------------------------------------------------
def main(argv=None):
    ap = argparse.ArgumentParser(prog="bathero", description="Guitar Hero style drum trainer driven by MIDI.")
    ap.add_argument("--port", help="MIDI input port (substring). Default: first port that looks like a drum module")
    ap.add_argument("--kit", help="kit file to load/save instead of ~/.config/bathero/kit.json")
    ap.add_argument("--offset", type=float, default=0.0,
                    help="latency compensation in ms: your mean error when uncalibrated (positive = hits are treated as earlier); 0 = the saved value")
    ap.add_argument("--speed", type=float, default=1.0, help="tempo multiplier for every level (0.5 = half speed); [ and ] change it in play")
    ap.add_argument("--size", default="1280x720", help="window size WxH")
    ap.add_argument("--fullscreen", action="store_true", help="start in fullscreen (the saved default is on)")
    ap.add_argument("--windowed", action="store_true", help="start in a window, ignoring the saved setting")
    ap.add_argument("--audio-device", help="audio output name (substring), e.g. XR18; saved setting otherwise")
    ap.add_argument("--no-sound", action="store_true", help="disable all audio")
    ap.add_argument("--no-guide", action="store_true", help="start with the guide track off")
    ap.add_argument("--no-backing", action="store_true", help="start with the backing loop off")
    ap.add_argument("--no-metronome", action="store_true", help="start with the metronome off")
    ap.add_argument("--no-menu-music", action="store_true", help="no ambient music in the menus")
    ap.add_argument("--log", help="write every judged hit of the last run to this CSV")
    ap.add_argument("--midi-trace", metavar="FILE",
                    help="append one line per note-on (wall time, note, velocity, outcome, judge µs) for latency measurements")
    args = ap.parse_args(argv)
    App(args).run()


if __name__ == "__main__":
    main()
