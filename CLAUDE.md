# bathero

Guitar Hero style drum trainer (pygame + mido). Run with `.venv/bin/python -m bathero`.
Sibling of `../hhmapper`; same conventions: own `.venv`, reply ends with the user's next action.

## Git: commit and push when a task is done (required)

Commits are allowed without asking. When a task is finished and verified (and deployed, see
below), commit it and `git push origin master` without being asked. `origin` is github
`sdeleon28/bathero`; the branch is `master`.

## Deploy after every change (required)

The user launches the game through an rcmd shortcut bound to `/Applications/bathero.app`,
and expects that shortcut to always open the latest code. After any change to this repo
(code, assets, dependencies), run:

```
./deploy.sh
```

It rebuilds the bundle with `install_app.py` and, if the app was running, quits and
relaunches it. Do this before reporting the change as done. The bundle does not copy
the code (it points at this repo), but a running instance keeps the old code loaded,
so the relaunch is what matters.

## Twitch stream (T), the badge, and the ! layer (camera + chat)

`bathero/twitch.py`. `T` starts and stops the stream (also the Setup row "Stream (T)" and the
x next to the LIVE badge). **The stream never starts on its own, never in a test, never from a
script: only the user presses T, and Claude does not press it for them.** For pipeline checks use
`python -m bathero.twitch --selftest [--source screen]` (a local rtmp listener, no Twitch;
prints the received audio's level, so silence is caught) and
`python -m bathero.twitch --chat xantwav` (reads the chat, no account needed).

- **The stream is its own process** (since 2026-09-13, so deploy.sh relaunching the game does not
  kill a live stream): T spawns `.venv/bin/python -m bathero.twitch --daemon --settings JSON` in
  a new session (`twitch.daemon`), which runs the `Streamer` on the screen source and writes
  `~/.config/bathero/stream.json` twice a second (pid, when it started, ffmpeg's progress); its
  output goes to `~/Library/Logs/bathero/stream.log`. The game holds a `StreamLink` (`app.streamer`):
  `phase` is off / starting / live / stopping from the state file and the pid; a game started under a
  live stream attaches to it (toast "stream live since hh:mm", the layer comes on); quitting the game
  leaves the daemon alone; `stop()` sends SIGTERM and never blocks (the daemon stops ffmpeg cleanly,
  a few seconds; SIGKILL plus a `pkill -f bathero-stream-` after 10 s if it ignores it). A daemon
  that dies on its own (or is killed) is reported "stream ended: ..." from the state file's error,
  and its leftover capture is killed. `python -m bathero.twitch --status` / `--stop` from a
  terminal. The "window" source is still the in-process Streamer and dies with the game (it is the
  game's frames); a daemon refuses to start while another is live.
- **The badge** (`bathero/badge.py`) is the daemon's own window, so it is on screen exactly while
  the stream process lives, game or no game: Cocoa through PyObjC (`pyobjc-framework-Cocoa` in the
  venv), a borderless transparent window at the maximum window level, on every Space
  (CanJoinAllSpaces, FullScreenAuxiliary: it shows over the game's fullscreen Space and follows a
  Mission Control switch), one per display, top right under the menu bar (at the very top when a
  fullscreen Space hides it; re-placed every half second), no Dock icon, never takes the focus. A
  red pill "LIVE mm:ss" with a blinking dot; amber STARTING until ffmpeg reports, dim STOPPING;
  after an unrequested end "STREAM ENDED · reason" stays until its x is clicked or another stream
  goes live. Under the pill, the audio row (meter, gain fader, dB), see below. Speed under 0.95x and dropped frames appear in amber inside the pill. The x next to it
  stops the stream like T (the daemon's SIGTERM path). The game draws no badge of its own; the
  play HUD's right column starts under the corner while the stream lives (`Renderer.top_inset`
  from `App.badge_height`). Viewers see the badge too (the stream is the display).
  `python -m bathero.badge --demo` shows one for 10 s. Without PyObjC the daemon still streams,
  badge-less. Tests give the daemon its own state file with `BATHERO_STREAM_STATE`.
- **The ! layer** (`App.set_layer`, `layer_on`): the streamer's overlay over any screen, independent
  of the stream: the camera as the computer edition's picture-in-picture (`capture_pip` of the
  height in `capture_corner`, 30 fps preview, border purple while live, red while a take records
  and it shows the recorder's own frames) and the chat pane (bottom left, the velocity viewer's
  place, above it when both are on; bottom right when the camera has the bottom-left corner). The
  chat connects when the layer turns on and closes when it turns off. Going live (or attaching to a
  live stream) turns the layer on, because the stream is the display and this is how the iPhone
  gets into it; ! hides it again during the stream if wanted. A layer the stream turned on goes
  off when the stream ends (`layer_auto`), so the camera preview's ffmpeg is released and
  Continuity Camera disconnects; a layer the user switched with ! stays. The camera check screen hides the
  layer (it has its own picture). This replaced the old camera monitor (a small 15 fps picture)
  and the chat-tied-to-the-stream of 2026-09-12.
- Source (`stream_source`, default "screen", since the first live tests 2026-09-12): ffmpeg
  captures display `stream_display` (0) through avfoundation, so the terminal or anything else
  on that display goes out too; the interface's mix comes from a separate feeder process
  (`python -m bathero.twitch --audio-feed`, a sounddevice input with the take's channels and
  buffer settings) through a named pipe; the video on the wall clock (`-copyts`, setpts
  minus the launch time t0), the audio on its own sample count, which the feeder anchors to
  the same t0 (`--t0`: the callback pads the head, and any gap over 0.25 s, with silence),
  `aresample=async` for drift. Needs the Screen Recording permission
  for bathero.app (macOS asks once, then relaunch the app). Every stream keeps an AAC copy of
  its audio in `~/Movies/bathero/streams/` to check afterwards.
  **Never stamp the audio pipe with the wall clock**
  (`-use_wallclock_as_timestamps` on the f32le input, until 2026-09-14): that dates each block
  by the moment ffmpeg got round to reading the pipe, so any hiccup in its loop (the screen
  capture, the rtmp send) bunches a clump of blocks onto one timestamp and `aresample=async`
  throws away everything that arrived late, filling the hole with silence. Measured that night:
  half the audio gone, metronomic, 200 ms of sound and 200 ms of silence, on every stream of
  the evening, while the interface read clean and the feeder reported `lost 1 ms`. Three
  networks were tried; it was never the network. The audio's clock is the interface's, and the
  feeder is what places it against t0. Reproduced and fixed by freezing ffmpeg 150 ms at a time
  (SIGSTOP/SIGCONT): the old chain 31.6 % silence, the new one 0.
  **Never capture the interface's audio with ffmpeg's avfoundation**: measured 2026-09-12 with
  the native buffer timestamps, it drops buffers even capturing audio alone (6.1: 1.3 s lost
  in 15 s; 8.0.1: 0.67 s) because it keeps one pending buffer and sleeps 10 ms between reads;
  viewers heard those as pops. The feeder loses 0..2 ms in 15 s. And never a sounddevice
  input inside the game process for the stream: the callback waits for the GIL and the
  window source logged 0.25 s stalls; the headphone return also chopped that night.
  "window" stays as the fallback (`stream_source: "window"`).
- **Gain and level** (2026-09-13: the first streams went out at -42 LUFS, peaks -20 dB, the
  interface's mix as it came): the badge's audio row has a peak meter of what goes out, a gain
  fader (-12..+30 dB, whole dB) and its value. The fader writes `~/.config/bathero/stream_gain`
  (one number, kept between streams, `twitch.read_gain`); the feeder re-reads it twice a second
  and multiplies its blocks (the window source's in-process input too), and prints
  "level <peak dB> <rms dB>" on stdout four times a second, which the Streamer keeps as
  `audio_level`. ffmpeg's audio chain ends in `alimiter=limit=0.97`, so a hot fader clips softly.
  The gain applies from the next stream a running daemon does not have the fader.
- What goes out is the display as the user sees it, every overlay included (the layer, the
  toasts, the velocity viewer, the badge): not an edition, no social layout.
- Video: 30 fps, H.264 on VideoToolbox, `stream_height` (1080) and `stream_kbps` (6000) from
  settings, keyframe every 2 s, FLV over RTMPS to Twitch's ingest. Audio: the same interface
  channels as the take (X18/XR18, USB 17/18 = Main L/R) through the feeder, float32 stereo,
  padded with silence onto the stream's clock.
- The key lives in `~/.config/bathero/twitch_key` (one line). Never log it, never copy it
  into settings.json or the repo; `twitch.redact` strips it from ffmpeg's messages.
  `stream_url` in settings replaces the whole URL (tests); `stream_bandwidth_test` appends
  `?bandwidthtest=true` (Twitch takes the stream without going live, inspector.twitch.tv).
- Chat: anonymous IRC over TLS, `justinfan` nick, reconnects by itself. Writing to the chat would
  need a token; not built.
- Network measured 2026-09-12 on Wi-Fi (en1): uplink 14..24 Mbps, responsiveness low
  (2.6..3.7 s under load). The user has two USB Ethernet adapters; a wired link is the fix
  if the stream drops frames. Ingest TCP round trip 47..51 ms.

## Skills (`.claude/skills/`)

- **`/twitch-clips <video path> [instructions]`**: cuts a recording of the stream the user hands
  over (a downloaded VOD, a screen recording; no browser, no downloading) into clips (16:9 and
  9:16) in `~/Movies/bathero/clips/<date> <label>/`. The moments come from the run logs and the
  takes on the video's clock (`moments.py` in the skill folder: video time = wall - the video's
  start, guessed from the file name's stamp or creation_time unless given). Instructions can name
  the parts or the style. It never starts or stops the stream and never posts anything.

## Waiting screen (`cortina`)

`bathero/agi.py`, a separate terminal program, for the moments the user leaves
the camera. It runs as `cortina` from any terminal: `~/bin/cortina` is a symlink
to `./cortina` in this repo, which resolves the symlink before cd-ing here (so
`$0` is not `~/bin`) and execs `.venv/bin/python -m bathero.agi`. The user named
it, and that is the name to use for it:
a Twitch-style be-right-back card (EN VIVO badge, channel, AFK timer, big title,
a rotating line of excuses) over six scenes that cycle every 26 s. The stream
captures the display, so a full-screen terminal on display 0 is what goes out;
nothing of this touches the game or the stream code. Keys: q, space, 1..6, p, b,
h, f. `--title`, `--note`, `--scene`, `--seconds`, `--fps`, `--no-card`.

- Each cell is the half block U+2580 with the top half as the foreground colour
  and the bottom as the background, so the canvas is cols x 2*rows pixels in
  24-bit colour; a sparse layer of real characters on top keeps the HUD and the
  labels crisp. Everything is composited additively in linear light and tone
  mapped at the end, which is why the palette constants look so dark.
- Two things keep it cheap enough for 30 fps at 212x58 (measured: 4..14 ms per
  frame, 0.1..6.8 MB/s to the terminal): only the cells whose colour moved by
  more than 3/255 are rewritten (and what is remembered is what was written, so
  a slow drift still arrives), and a cell whose halves match is a space with one
  colour instead of a half block with two. `splat()` scatters points with
  np.bincount, or np.add.at when there are few enough that allocating a canvas
  per channel would cost more.
- The big title is rasterised by pygame (already a dependency) into a mask:
  `font.render(..., True, ...)` puts the shape in the ALPHA channel, so it is
  `surfarray.array_alpha`, not `array3d`. Set PYGAME_HIDE_SUPPORT_PROMPT before
  importing pygame or the banner lands on the alt screen.
- The music is `bathero/keygen.py`, its own renderer (not `make_arrangement`,
  which has no drums because it plays under a drummer): four minor chords,
  sixteenth arps with a 3/16 ping-pong delay, pulse lead, square bass and a drum
  machine, eight sections of eight bars, ~100 s, looped, all from one seed, using
  the oscillators and the drum hits of `sounds`. `python -m bathero.keygen
  [--wav f] [--seed N]` renders or plays one on its own. Rendering is 0.3 s
  because every distinct note is cached: four chords is a handful of waveforms.
  Balance measured by band energy after the mix (2026-09-13); the first version
  buried the melody (3 % between 400 and 4000 Hz) because the bass doubled itself
  an octave below its root at 27 Hz.
- **The tune is also a level backing** (asked for 2026-09-16): `Chart.backing == "keygen"`
  (the four all-sextuplet rudiments: Six stroke roll, Six stroke roll R L R R L L, Double
  paradiddle + six stroke, Six stroke + paradiddle-diddle; the `backing=` argument of
  `_rudiment` / `_rudiment_mix` adds more). `App.tracks_for` calls `keygen.render_level`:
  the tune at twice the level's tempo on a triplet grid (`render(grid=12)`, its own
  pattern tables in `GRIDS`), so its arpeggio falls on the level's sextuplets and its
  backbeat on the off-beat eighths; the count-in is the intro section, bar 0 is the drop
  of "main", the sections go round from there. One tune per level, seeded by the name
  (both hand leads, every rate). The cortina's own render is unchanged (grid 16, looped).
- `render()` also returns `low` and `high` envelopes, one value per 60th of a
  second, so the animation follows the music by indexing (position = wall clock
  since play started, a Sound on a channel has no cursor). `Scene.low` / `.high`
  are set every frame: the card breathes with the kick, Gray-Scott drops a cell on
  it, the forward pass rides it, the arcs and the particles brighten with the hats.
- Leaving is an outro, not a stop: q (Esc, ctrl-c) fades the picture to black
  while `Sound.fadeout` takes the music down, 1.4 s, 0.45 s for ctrl-c, then one
  black frame. The fade factor is squared in linear light, which the tone map's
  gamma turns into an even ramp for the eye; it multiplies the character colours
  too, or the HUD would stay lit over a black screen.
- **English: `curtain`** (asked for 2026-09-19). `./curtain` is `./cortina` plus
  `--lang en`, its own `~/bin/curtain` symlink; `--lang` works on either command.
  Every string a viewer reads lives in `STRINGS` in `agi.py` (`T` is the picked
  language, `set_lang` in `main` before the parser, so the help and the `--title`
  default follow it): scene titles and subs (a `Scene` reads them from the table
  by its `name`), the latent clusters, the attention sentence, the layer names,
  the telemetry labels and unlocks, the thoughts and their candidate tokens, the
  card (title, badge, AFK, the line, excuses, marquee) and the key legend. Scene
  `name`s stay Spanish where they are (`red`, `entrenamiento`): they are
  identifiers for `--scene` and the snapshot files, not text. A new language is
  one more dict; a new string goes into every dict.
- `--snap DIR` renders a frame of every scene to PNG (plus the character layer
  as .txt) with SDL_VIDEODRIVER=dummy: that is how the look was checked without
  a terminal, and how to check it after touching a scene.

## The level intro: a snare hit starts every level (S)

`bathero/intro.py`, asked for 2026-09-19: with `intro` on in settings (default, `App.intro_on`,
**S** toggles it in the lists, on the play screen and from the Setup row "Level intro (S)"; the
hub's S stays the stats) every `PlayScreen` opens **armed**: the game is paused at the top of its
count-in (`PlayScreen.arm`, nothing plays, no note can be missed) under a card that says, in
Spanish, what the level teaches and what to watch (`Renderer.intro_card`), and **a snare hit
starts it** (velocity >= `NAV_MIN_VELOCITY`, after `ARM_GRACE_S` so the stroke that chose the
level on the results screen cannot start the next one; every other pad only sounds). The MIDI
thread stamps the hit, `update` starts the count-in dated at it (`PlayScreen.start`) and resets
the run log's `started`, so the minutes in the stats do not count the reading. Enter and space
start it too; S while the card is up turns the feature off and starts the level; a retry or a
tempo change arms again. The texts live in `INTROS` in `intro.py`, one entry per level name (a
paragraph "qué vamos a aprender" and a few "para tener en cuenta"; "derecha" / "izquierda"
swapped for the left-hand-lead version like the description's R / L), plus `facts()` (tempo,
compases, notas, subdivisión, cuerpos) and `judged()` (dinámicas, articulación, mano guía,
pista de fondo) read from the chart itself. **A new level needs an entry**: `python -m
bathero.intro [NAME ...]` prints the cards and names the levels without one (`missing()`).
With `coach_language` "en" the card shows the English description instead.

## Hints on the results screen (every attempt short of five stars)

`bathero/hints.py`, asked for 2026-09-18: deterministic, no LLM. `App.tries` counts, per level
key, the finished runs in a row under five stars (a rehearsal with the transport is not an
attempt, a practice run with P is; five stars reset it; kept as `tries` in the level's progress
entry). On every attempt short of five stars `PlayScreen.attempt` runs `hints.analyse` on the
judged notes (`from_game`) and the results box shows the two costliest faults, in
`coach_language`, under "try N · work on this" (it was every third attempt for a day, 2026-09-18:
with R retried mid-run, the one run played to the end never showed it). The rules are thresholds on plain
statistics, each hint ordered by the grade points it costs: misses (which body, which beat
position), strays (soft ones are pedal or stick touches), then one timing hint, the largest of:
one body against the rest of the kit **where they land together** (a chord, >= 15 ms: a flam;
since 2026-09-20 evening, when comparing the bodies' means read the gallop's early kick
sixteenths as "the kick ahead of the kit" while every chord was tight), the left hand against
the right, the notes between the beats against the notes on them per body or for the kit
(naming the group that is off the click: "el bombo: el tiempo llega 27 ms tarde, las notas
entre los tiempos están en su lugar"; "los &" when they are all eighths), a drift between the
halves of the run, the whole kit's bias (>= 15 ms), or the spread alone (std >= 25 ms) when
nothing explains it; then soft accents
or loud taps (with the hand when it is one hand), and the hi-hat openness confused most.
`python -m bathero.hints [PATH.jsonl ...] [--all] [--lang en]` prints them for a run log
(`from_runlog`; the run logs carry `expression` and every hit's `art` / `played` / `art_ok`
since this date), which is how to check a new rule against the past runs before shipping it.

## Profiles (who is playing)

`bathero/profiles.py`, asked for 2026-09-17 so a friend's stars stay apart from the user's. A
profile is a name and a pad: the pad's icon (nine flat glyphs drawn in `draw_icon`, one per pad
in `ICON_PADS` order, keys 1..9: flame, star, bolt, skull, heart, gem, moon, sun, note; no font,
no emoji) is the profile's icon, and **hitting that pad on the login screen is the login**. The
list lives in `~/.config/bathero/profiles.json`.

- **Progress per profile**: `App.results` is loaded by `App.set_profile` from
  `PR.progress_path(profile)`, saved by `App.save_results`. The first profile ever created is
  the owner (id `main`): it keeps the old `progress.json` and, in the stats, the run logs written
  before runs carried a `profile` field (`runs_match`); every later profile gets
  `progress-<id>.json` and only its own runs. The run log header carries `profile`;
  `stats.summary` / `report` / `per_level` take `profile=`; the coach's folder is
  `coach/<id>/` for everyone but the owner. **With no profiles the game is as before**: no
  login, one shared progress.
- **Screens**: `LoginScreen` (`App.home()`, the screen the game opens on whenever profiles
  exist and nobody is in; cards with the icon, "hit the Snare", the key; a pad nobody owns
  toasts; N new profile; Esc quits). `ProfilesScreen` (Setup's first row "Profile: Santi"):
  Log out, New profile, then one row per profile (select switches to it; the tom, or
  Backspace, deletes it after a second tom within 3 s, its progress file with it, except the
  owner's `progress.json`). `NewProfileScreen`: the name from the keyboard (`Screen.typing`:
  while it is True the main loop routes every key to `on_text` / `on_key` and the global
  shortcuts V T { } ` ! - = ; ' sleep, so a name can contain them), then **a pad picks its
  icon and the same pad again creates the profile** and logs it in (Enter also creates; a pad
  another profile owns is dimmed with the owner's name and refused). The keyboard is only for
  the name, as asked.
- The login and new-profile screens take the pads raw (`App.nav_hit(..., raw=True)`: the right
  crash stays crash2, it is its own icon), every other screen still folds both crashes into
  "up". Login is not remembered between launches: the game always asks who is playing.
- The hub shows the icon, the name and the star total top left. A rehearsal (transport or P)
  still saves nothing, whoever is in.

## Courses (the hi-hat pad, since 2026-09-17)

The old Songs section went on 2026-09-17 (copyright); songs came back on 2026-11-07 as an
import pipeline over songs the user sources (see "Imported songs" below). Alongside
them, **courses by genre**: `chart.Course` (key `course:<slug>`, name,
desc, levels), `COURSES` / `COURSE` in `chart.py`, each level a `_groove(..., backing=<style>)`
with the genre's arrangement style from `sounds.STYLES` (`style_for` returns a feel that names
a style). The hub's hi-hat card opens `CoursesScreen` (one row per course with its stars),
which opens a `ListScreen` on the course's key; `App.categories()` is `kick`, `snare` and the
course keys, `App.items_for(key)` its levels, `App.prog_for` the backing seed (each course its
own hundred). Level names are unique across the game (asserted at import): progress, the run
logs and the coach's playlists are keyed by them. The first course is **Pop punk**
(`POP_PUNK`, 26 levels, 140..168 bpm written, the style at 160..190 with `]`), each level
keeping what the previous taught: eighths, four on the floor, the push, tight/open hats, the
crash wash, the skank, fills from eighths to the whole kit, the & crash, stabs, half time, the
build, kick doubles, the ride bridge, the whole-bar fills, a 32-bar anthem. Adding a genre: a
list of levels plus one `Course(...)` in `COURSES`; nothing else.

**A fill is drilled before it is used** (asked for 2026-09-22, "aprender el vocabulario antes
de aplicarlo a través de la repetición", one level per fill): every fill the course introduces
gets a `_drill(...)` level of its own ahead of the level that plays it — two bars looping
sixteen bars long, the groove bar the fill lives in and then the fill, eight repetitions, the
two bars copied from the level that uses them so the hands learn them note for note. The
level's eight transport phrases are one cell each, so `l11` loops a single groove + fill.
Levels 7..8 drill the two eighth-note fills of 9, levels 10..13 the four sixteenth fills of 14,
levels 21..24 the four whole-bar fills of 25 over the kick-doubles groove (24's groove bar
carries no crash: its fill's crashes land on the 4 and must ring over the 1). Everything from
the old level 7 on moved up ten places; the old names map to the new ones in
`kit.PROGRESS_MIGRATIONS` (`stats.per_level` applies the same map, so the run logs written
under an old name stay with the level), and the two whose new number collided with the beats
course were renamed: "13 · Kick doubles" is "19 · Doubles under the push" and
"15 · Around the kit" is "25 · Whole-bar fills". A new fill in any course wants the same pair.

## Imported songs (`bathero/ingest.py`, `bathero/songs.py`, `bathero/twister.py`)

Asked for 2026-11-07 (branch `song-import`). The user sources the songs; a `.wav` in
`~/Music/bathero/inbox` becomes a level. **The importer runs in its own venv, `.venv-ingest`**
(torch, demucs, beat-this, adtof-pytorch pinned at 85c192e, watchdog; never in the game's venv):
`.venv-ingest/bin/python -m bathero.ingest [--watch | FILE]`, log in
`~/Library/Logs/bathero/ingest.log`. Per song, into `~/Music/bathero/songs/<slug>/`, each step
skipped when its file exists: Demucs `htdemucs_ft` two stems on MPS (`drums.wav`, `backing.wav`),
beat_this on the full mix (`beats.txt`: time, position in the bar), ADTOF on the drum stem
(`drums.mid`, CPU only: its CLI refuses mps), then `song.json`: chart time 0 is the first
downbeat (`offset` = where that is in the audio), the beat grid shifted with it (`Chart.beats`,
so bars and phrases follow the song's own tempo), notes snapped to a sixteenth or triplet eighth
within 35 ms, one instrument within 40 ms deduped. ADTOF knows kick, snare, tom, hi-hat, cymbal
only: no open hats, no ride, no second crash. The first song (I Won't Back Down, 3 min): 67 s.

In the game the songs have their own card on the hub, opened by the **rack tom** (key 4):
since 2026-11-08 the hub is five cards, Exercises / Beats / Courses on top and Songs / Setup
under them, wider (`HUB_ROWS`; a title too wide for a narrow card drops a size, a subtitle
wraps; up / down go to the card under / over the middle). Behind it the songs are a course
(`App.refresh_songs` puts it in `C.COURSE` but not in `COURSES`, which the intro and
name-uniqueness checks walk; re-read from disk whenever the card opens; back returns to the hub).

**The drum module is found by the most specific hint** (`find_module`, also the device
watcher's): until 2026-11-08 the first port matching any of `MODULE_HINTS` won, and with the
piano plugged in the game sat on "Roland Digital Piano" ("roland") while the TD-17 played to
nobody. Pianos, the Twister, the Launchpad and hhmapper's port are never taken. A song chart
has `Chart.song` (its folder) and `song_offset`; `App.tracks_for` plays its two stems as the
tracks "backing" and "drums" from `-song_offset / rate`. A rate other than 1 is ffmpeg `atempo`
(pitch kept, chained under 0.5), cached as raw s16 in the song's `cache/` (0.4 s for both stems).

**The Midi Fighter Twister, bank 2** (bank 1 is GroupCtl's, in `../../js-projects/bitwig-maps/GroupCtl`):
GroupCtl's numbering (1 bottom left, left to right, a row up every four, 16 top right; CC =
16 + (3 - row) * 4 + col on channel 1, absolute 0..127). 1 backing track, 2 drum stem (settings
`song_backing`, `song_drums`), 3 metronome (`metronome_volume`), 4 tempo (songs only: from the
song's tempo down to 126 bpm slower, never under rate 0.3; once the knob rests 0.4 s the stems
are stretched in a thread and the song restarts there), 5 offset (0..127 ms, one per step,
`App.set_offset`, which `,` `.` use too), 15 menu music (`menu_music_volume`, under the master),
16 everything (`volume`). **Pushing an encoder resets it**
(channel 2, 127 down): backing 100 %, drum stem 0 (you are the drummer), metronome 100 % (its
default), tempo the song's own, menu music and everything 100 %, offset the pooled fit of the recent runs
(`latency.pooled` over `recent_curves`, in a thread; 45 ms when there are none). **LEDs** as
GroupCtl paints them (`TwisterOutput.paint`, `Colors.BITWIG_TO_TWISTER`): hue on channel 2,
brightness 47 on channel 3; backing magenta 87, drums green 44, metronome yellow 66, tempo cyan
19, offset blue 123, menu music pink 88, everything red 85. The game looks for the port every 3 s; when it finds it
it paints the seven knobs and sets their rings (the keyboard's `{ }`, `- =`, `, .` move the rings
too), and applies the last turn per frame.

**The Launchpad MK2 on Twister bank 2** (`bathero/launchpad.py`, asked for 2026-11-08): the bank
buttons send CC 0..3 on channel 4 (bank 1..4; 127 for the bank entered, 0 for the one left).
GroupCtl's `launchpadlending` package hears it too: on bank 2 its `LaunchpadOutput` /
`LaunchpadInput` go quiet (`LaunchpadLent`), on bank 1 the `Pager` clears and re-announces its page
(`LaunchpadReturned`). bathero paints the whole surface on bank 2 (again 0.3 s later, over a late
frame of GroupCtl's) and on bank 1 stops sending without blanking. The last bank is kept in
`~/.config/bathero/twister_bank` (a relaunch does not hear it). On the play screen it is GroupCtl's
project explorer over the level: 64 pads = the level's bars from the top left, coloured by phrase,
up to N bars of one phrase per pad; a pad jumps there (a bar of run-up, the screen says "bar 18");
SESSION / USER 1 zoom out / in (1..32 bars a pad, auto-fit on a new level and on entering bank 2
until zoomed by hand); LEFT / RIGHT page; MIXER then two pads marks a loop of bars
(`App.loop_bars`, beside the phrase loop `l35` in `loop_range`; `PlayScreen.loop_span` gives
either as times, which the renderer draws); side MUTE toggles the loop, side STOP pauses / plays
(starts the level from the intro). The playing pad blinks white, the loop is white. Pads and side
buttons act on release unless held 0.5 s; other screens show it dark.

**Editing a song** (`bathero/songedit.py`, asked for 2026-11-08): **E** on the play screen of an
imported song (paused, or it pauses; from the intro card too) opens the editor over the highway;
the game stays paused and the view is the paused playhead (`Game.seek(t, 0)`), so the wheel, the
number keys and the Launchpad's pads (`bar_jump` goes to `SongEditor.view_bar` while editing, no
run-up; the view's bar blinks) move it. Notes show above and below the line on the grid. Click
selects (shift adds), dragging moves the selection in time (snapped: G cycles 1/16, 1/8, 1/4,
1/32, triplets, off) and across lanes, a drag on empty space is a marquee, a double-click adds a
note (the lane's median velocity), right-click or Backspace deletes, **a drum hit moves the
selection to that drum** (a lane appears; lanes stay while editing, `build_lanes(extra=)`), arrows
nudge, cmd Z / cmd shift Z undo / redo, cmd A all, cmd wheel zooms. Every change writes song.json
at once (times divided back to rate 1; the first keeps `song.transcribed.json`), one note per drum
per 10 ms, and re-reads the songs course so a retry plays the edit. E / Esc done (the playhead
parks a bar before the view), space or the Launchpad's STOP done and play. A run that was edited
is a rehearsal (it seeked).

## The double-kick levels' backing (`Chart.backing == "kick"`)

Asked for 2026-09-20 (the gallop under a synth pop backing drawn by index "had nothing to do
with it"): `sounds.KICK_STYLE`, the metal timbres and progressions, with the bass and the power
chords hammering the level's own kick figure bar after bar, so the ear has the feet's rhythm in
the music before the feet find it. `Chart.kick_rhythm()` reads the kick notes of every bar into
(grid 16 or 12, [[(slot, gain)], ...]) and `App.tracks_for` passes it to `render_backing_track` /
`make_arrangement` as `rhythm`; the plan only brings the pad and the lead in and out. The gallop
and its two preparatory levels name it (`_kick_ostinato(..., backing="kick")`: "Double kick
pairs, left first" and "Double kick gallop tail", added 2026-09-20 because the gallop's closing
right-foot sixteenth came 30 ms early over 53 runs and knowing it did not fix it); any other
double-kick level is the same argument.

## The music hammers the kick figure (`Chart.hammer`)

Asked for 2026-09-20 for "4 · Tight and open" ("que el backing ayude a memorizar el patrón de
bombos"): `_groove(..., hammer=True)` makes the style's bass play the level's kick figure in every
section and, in the punk style, the guitar's open chords accent it and the last bar of every
"drive" section stops on it (also on "5 · Washing the crash" since 2026-09-21: the level keeps the
same push figure and without the hammer the aid vanished on the level after the one that taught
it; 56 kicks in the chart, a bass onset within 12 ms of every one, none elsewhere. The rest of the
pop punk course does not hammer). `Chart.kick_rhythm()` returns the figure **per bar** (grid 16 or 12,
one slot list per bar of the level, a bar without kicks repeating the last one), so the push on
the & of 4 every second bar is played as written; `make_arrangement` takes it as `rhythm` and
indexes it by bar (the double-kick levels' `backing="kick"` is the same mechanism on the metal
style). Verified by onset detection on the bass alone: 28 kicks in the chart, an onset within
30 ms of every one, none elsewhere. Any other level is the one argument.

## The punk style (`sounds.STYLES["punk"]`, rewritten 2026-09-20)

"Make it sound much better and more punk": `guitar()` in `make_arrangement` is a power chord
(root, fifth, octave, two detuned saws each, a pick scratch) through two tanh stages with the
lows cut between them and a cabinet EQ (`_eq`, an FFT gain curve: `CAB_OPEN` with a presence
bump at 3 kHz, `CAB_MUTED` darker). Three parts: "drive" (palm-muted eighths, an open chord
ringing until the next stroke on every accent), "wash" (every eighth open, the chorus), "figure"
(the accents alone, the band stopping). The accents are the bass pattern's slots, or the kick
figure when the level hammers it. Bass kind "punk" (saw and square through a pedal, `BASS_AMP`,
held to the next note), a lead in octaves on the same guitar (`tone` kind "octave", a legato riff
on the eighths), the count-in one open chord ringing on its 1, and the bus through
`tanh(bus_drive)`. Balance measured on level 4 at mix level (RMS over the level): guitar -14,
bass -17, lead -17 while it plays; energy 2..6 kHz went from 1 % to 10 % (the box-filter cabinet
had closed at 2 kHz). `_lowpass` is now a cumulative-sum box filter (O(n), identical output).
`make_arrangement(..., normalise=False)` returns the raw sum, for measuring parts against each
other; a style with `bass=None` has no bass.

## Scroll speed (D): fixed or following the tempo

`scroll_fixed` in settings (default off), `App.scroll_fixed`, `App.toggle_scroll`; **D** in the
lists and on the play screen, the Setup row "Scroll speed (D)", saved. Off, the highway shows
`LOOKAHEAD_S` (2 s) of chart and the notes fall at `pps` times the rate, so at rate 0.6 a
millisecond is 60 % of the distance it is at 1x and the eye leads slow notes (asked for
2026-09-20: "the timing feels completely different when the tempo changes"; the judging does
not move with the rate, verified over 619 runs, the picture does). On, the notes fall at `pps`
whatever the tempo, an error is always the same distance, and a slow level shows more seconds
of chart. The HUD's tempo line says "scroll fixed" while it is on. The kit's own sounds moved
from D to **K** on the play screen that day.

## The offset fit on the results screen (`bathero/latency.py`)

Asked for 2026-09-20, after a day of moving `offset_ms` between 20 and 85 without settling: at
the end of every level the results box says "offset 55 ms · this run best at 62 (+2.4) · last
15 runs: 58". Every stroke (`Game.strokes`, judged time and key) is re-judged against the chart
at the run's offset plus a shift of -60..+80 ms with the game's own windows, the grade being the
timing grade (no dynamics); "best" is where this run peaks (the middle of a flat top), "gain"
what it would have added, and "last N runs" the offset that maximises the mean grade over this
run and the profile's recent run logs pooled (`recent_curves`, cached by mtime). **The pooled
number is the one to set**: a single run's optimum is that run's own timing (measured 2026-09-20,
44 gallop runs: per-run optima from 22 to 86 ms, pooled 55). The fit goes into the run log as
`stats["offset_fit"]` (without the curve). `python -m bathero.latency [--runs N] [--profile ID]`
prints the sweep over the recent logs. `.` and `,` still move the offset 5 ms during play.

## Open items

`ROADMAP.md` lists the loose ends with their full context (hi-hat filter eating fast
notes, XR18 USB sends for take audio, XR18 network). Read it before touching the ghost
filter or `capture.py`; update it when an item is closed.

## Hand lead in Exercises

A rudiment whose sticking uses both hands on one instrument (`Chart.lead == "R"`, set by
`_rudiment` / `_rudiment_mix` when `lanes` is None) exists in two versions: right and left
hand lead. There is one level, `Chart.mirrored()` swaps every R and L (notes, strip, the R / L
tokens of the description); never add a "left lead" level by hand. `App.lead` ("R" / "L",
toms or the arrow keys in the list swap it) picks the version played. Progress is keyed by
`Chart.key`: the name, plus `" (L)"` for the left lead; the list shows one star row per hand,
the hub counts both. Old level names map to current keys in `kit.PROGRESS_MIGRATIONS`,
applied when progress loads. Levels with hands on different drums, feet patterns and hi-hat
lessons have no lead (one version).

**The same toggle picks the crash on a two-crash level** (asked for 2026-09-21 for "5 · Washing
the crash"): `_groove(..., mirror="crash")` sets `Chart.mirror = "crash"` and `Chart.home` to the
side written (the crash with more strokes: L for the wash on the left crash), `Chart.lead` being
the side of the version in hand; `mirrored()` swaps crash and crash2 in the notes and "left" /
"right" in the description. `Chart.key` is the bare name for the written side and `" (L)"` /
`" (R)"` for the other one (so the crash-L wash keeps its progress under "5 · Washing the crash"
and the crash-R version is "5 · Washing the crash (R)"); `App.chart_for` mirrors whenever
`App.lead` differs from the level's own `lead` (`App.lead` is one setting for both meanings:
R = right hand leads = the wash on the right crash). The list shows the star row per side, the
description of the side chosen, "wash on the right crash" top right and "crash R/L" in the
legend (`Chart.side_text`; `has_leads` is any level with a lead in the list, so the toms and the
arrows work in a course); the card says "el wash en el crash derecho" and its Spanish swaps
"izquierdo" / "derecho" like the hands. **The exit from the wash** (that day's question): after
the crash on the 1 the hand rejoins the hats on the 2 with the snare (`_H8_2`), not on the & of 1:
from the far crash at 180 the & is a stretch, and the crash should ring until the backbeat. Bars
1 and 9 of the level; the other course levels keep `_H8C` (their crash sits over the hats).

**Doubles are bracketed on the highway** (asked for 2026-09-16): `Chart.doubles()` finds runs
of two or three consecutive strokes of one hand (or foot) on one instrument within a beat of
each other (`ChartNote.hand`; notes without a hand are skipped over, a longer run is a one-hand
exercise and gets nothing), and `Renderer.doubles` draws a bracket hugging the notes on the
side of the hand that plays them, `]` to the right of an R R, `[` to the left of an L L, in
the hand's colour from the sticking strip (`HAND_COLORS`: R red, L cyan). It follows the taps'
narrower boxes, goes the moment the first stroke of the double is played or missed (left
over the second note it distracted), is not drawn over a skipped note after a jump, and
comes down again with the loop's head at the wrap.

## The transport: phrases, loop, practice (play screen)

Navigating a level like a DAW, asked for 2026-09-15. `Chart.phrase_bounds()` cuts the level
into at most ten phrases (`PHRASE_KEYS`) of whole bars, one per number key from 1, and
returns their starts plus the end, so phrase i is `[out[i], out[i+1])`. **Phrases are
equal whenever the level allows it**: up to ten bars, one bar per key (8 bars, keys 1..8);
longer, the largest count in 10..5 dividing the level exactly (16 bars are 8 of 2, 12 are
6 of 2, 120 are 10 of 12); only a length nothing divides (83 bars) gets ten of nearly equal
size. Three rules were tried on 2026-09-15/16 and this is where they landed: "fixed 8-bar
phrases" gave an 8-bar exercise a single phrase and wasted the ruler; "ten tenths in every
level" (a bar, half a bar or a beat as the grid) made the ruler's cells visibly unequal,
which read as arbitrary. The first version's confusion (pressing 3 and seeing 2) was not
the phrase rule but the run-up display, fixed separately (below). A key past the last phrase
toasts "no phrase 9: this level has 8 phrases, one bar each" (`PlayScreen.no_phrase`);
never clamp. `Chart.phrase_at` is None past the end; `Chart.place(t)` says "bar 3" /
"bar 3 beat 3" for the toasts and the ruler.

- **`Game.seek(t, count_in)`** is the whole mechanism: notes before t become state `"skip"`
  (never judged, never sounded by the guide, never drawn), notes from t on are re-armed to
  pending, `wall_start` is moved so `song_time()` is `t - count_in`, and the tracks are
  stopped so `_drive_tracks` restarts them at the new position (`Track.start_at` already
  played from any point, for pause/resume). It sets `seeked`, which is what makes the run a
  rehearsal.
- **`1`..`9`, `0` jump** to phrases 1..10 with one bar of run-up (`count_in_end`): the chart
  is silent through it, the metronome and the backing keep playing, so the first notes of
  the phrase come down the screen instead of landing on the line. **Through the run-up the
  screen shows the landing, not the bar being crossed** (2026-09-16: pressing 3 lit cell 2
  for a bar and the huge count digits read as "the number I pressed"): the ruler lights the
  key's cell with the playhead parked at its start, the centre says "phrase 3" over a
  smaller "in 4 .. in 1", and phrase markers before the landing are not drawn.
- **`l` + two digits** marks and starts a loop (`l35` = phrases 3 to 5, inclusive; `l33` one
  phrase; reversed digits are sorted). The pending gesture lapses after `LOOP_GESTURE_S`.
  `\` switches the marked loop off and on. The loop lives in the App (`loop_range`,
  `loop_on`) so it survives the restart `[` and `]` cause, and is cleared when the level is
  left. **The wrap has no count-in** (it would break the pulse): `Game.update` seeks back the
  moment `t >= loop[1]`, and the renderer draws the loop's first bar a lap early, above the
  line (`Renderer.note(..., coming=True)`), so the scroll is continuous. A level never
  finishes while a loop runs.
- **What the screen shows** (`Renderer.transport_bar`, `markers`, `marker_names`,
  `loop_badge`): the ruler is ten slots of one width, one per key, the level's phrases in
  the first ones and the rest drawn empty (the keys the level has not got stay visible, in
  their place); the playhead sweeps the level's part, piecewise per slot (white with a dark
  edge, so it reads on the lit slot and on the loop band), the current slot is lit, the
  digits typed after l light their slots, `bar n [beat m] of total` beside it, the loop a framed band there and
  named under the metronome's beat squares ("LOOP 3-5" bright, "loop 3-5 off" dim, on a
  backing since it sits over the highway). On the lanes every phrase start scrolls down as a line with its key's
  number (drawn under the notes, the labels after them so a single lane does not hide them);
  the loop's start and end are lines in the loop's colour, and while the loop runs the start
  is drawn again at the end ("loop 3 again"), because that is where the notes come back to.
- The number keys are the transport only: playing the lanes from the keyboard (and the `K`
  toggle that swapped them) was removed on 2026-09-16, the game is played on the module.
  `KEY_LANES` stays as the digit map (the transport, the kit wizard's stand-in for a pad).
  **`P`** is practice: no progress written.
- **A rehearsal saves nothing**: `PlayScreen.record` returns early when `app.practice` or
  `game.seeked`, because notes played out of order or several times would make the stars a
  lie. The results screen says "practice run · progress not saved".

## Accent sensitivity: one threshold per body (; and ')

`dyn_scales` in settings (instrument -> scale, since 2026-09-17; the old single `dyn_scale`
seeds it): the accent / tap band of every instrument is `game.dyn_band(inst)`, its measured
thresholds times its own scale, the night factor on top. **With the ` velocity viewer open,
`;` and `'` move only the body of the last stroke heard** (`App.dyn_target`, ghosts do not
count; the pane names it, draws its thresholds and dims the other bodies' bars); closed, they
move every body a step from where it stands. A pad's zones share the body's scale (hi-hat bow
and edge, snare head and rim): never a threshold per zone. The Setup row's select sets every
body to the same value.

## Judging windows

PERFECT within 30 ms, GOOD within 60, OK within 100 (`game.PERFECT_MS` etc.); five stars at
grade 94, where the grade is half accuracy and half quality (PERFECT 1, GOOD 0.6, OK 0.3)
minus 2 points per stray per 100 notes. PERFECT was 25 ms until 2026-09-20: that asked for a
20 ms timing spread with no bias, and over 132 gallop runs the user's best (24 ms spread, every
note, no stray) reached 94 once; at 30 ms 18 of them do and the 31 ms runs stay at four stars.
Saved stars are not recomputed.

## Judging: the miss deadline follows the latency offset

`Game.hit_lane` dates a stroke `song_time - offset` (the +45 ms of the user's chain), but
until 2026-09-16 `Game.update` declared a note missed at raw `t + OK_MS`, which is +55 ms
compensated: a stroke 55..100 ms late, an OK, found its note gone and became a STRAY on top
of the MISS. 112 of that day's 403 strays were this ("strays between the accents that I
never played"). The deadline is now `t - offset - OK_MS`; keep the two in step.

## Testing

Headless tests use `SDL_VIDEODRIVER=dummy` and `SDL_AUDIODRIVER=dummy`; screens can be
rendered to PNG with `pygame.image.save` for visual checks.

## The hi-hat gesture (Roland TD-17 with VH-10, measured 2026-09-06/07)

This is the ground truth for both repos. hhmapper turns it into One Kit Wonder
articulations for Bitwig; bathero turns it into game hits. Both must agree.

**What the module sends.**

| gesture | note | notes |
|---|---|---|
| stick on the bow (body), pedal closed | 42 | |
| stick on the bow, pedal open | 46 | |
| stick on the edge, pedal closed | 22 | Roland-specific |
| stick on the edge, pedal open | 26 | Roland-specific |
| pedal chick (foot close) | 44 | velocity from the stomp |
| pedal position | CC4 | 0 = fully open, 90 = fully closed on this pedal, nonlinear: "half by feel" reads 8..28 |

The module picks 42/46 or 22/26 by its own closed/open threshold; the CC value
at the moment of the stroke is the real openness. Zone = edge (22/26) or bow
(42/46); openness = from the last CC4 value, not from the note number.

**Openness classes** (closedness = CC4 value): tight >= 80, open <= 10, mid in
between. hhmapper maps zone x openness to: Tip/Edge Tight (41/42), Tip/Edge
Closed (43/44), Open 2/3 (46/47), Pedal (48) in One Kit Wonder, Kontakt C3 = 60.

**Ghost notes the pedal produces** (nobody hit the hat):

| when | note | velocity | how to catch it |
|---|---|---|---|
| ~30 ms before the chick, pedal still at CC 0 | 46 | 7..22 | velocity floor |
| 3..8 ms after the chick, pedal moving fast | 46 | 48..94 | window after chick |
| up to ~250 ms after the chick, pedal settling | 42 | 22..36 | settle window, soft closed note |
| 8..25 ms after a stroke, the other zone, one stroke read twice | 42 or 46 (or 22/26) | 0.7..1.5 x the stroke, up to 122 | within 30 ms: drop, any velocity |
| 8..48 ms after an edge stroke (the mass at 40..48), any strength | 42 or 46 | up to 92, whatever the stroke: 70 % of a 120, 140 % of a 55 | near crosstalk: 50 ms window, velocity cap |
| 73..93 ms after an edge stroke, one or two of them, nearly every hard stroke; 95..111 ms after a 113..127 | 42 or 46 | 28..70 % of the stroke (a hat left open under double kick swings and reads higher) | zone crosstalk |
| ~55 ms after a hard bow stroke, rare | 22 | ~40 % of the stroke | zone crosstalk |
| chick double trigger ~110 ms after a chick | 44 | 16..20 | chick velocity floor |

**Real strokes that look like ghosts** (measured 2026-09-09 on paradiddles, fast bow/edge
alternation, chick + stroke together, open hats; the rules must keep all of these):

| gesture | what arrives |
|---|---|
| softest real tap | 29 (taps in fast doubles 29..48; a missed tap can read 7..18, lost) |
| bow tap right after an edge accent (fast alternation) | 42 at 44..90 ms, 63..85 % of the accent |
| stick landing together with the chick | 44 then, 3..5 ms later, 46 at 113..126 |
| stick landing just after the chick | 44, the 46 ghost at 3..8 ms, then 42 at 11..52 ms, velocity 56..127 |
| stroke while the pedal is still opening (CC moving 20+ in 50 ms) | 46 at 116..127 |

Resting sticks on a pad: 4..14.

**Filter rules, identical in both repos** (hhmapper.py constants, bathero/ghost.py):

- hi-hat stick note with velocity < 25: drop (both repos; hhmapper no longer holds
  strokes 40 ms for a following chick, that hold cost 40 ms of latency on every stroke)
- hi-hat stick note within 10 ms after a chick (44) with velocity < 100: drop (chick splash)
- closed-hat note (42/22) within 250 ms after a chick with velocity <= 40: drop (pedal settling)
- hi-hat stick note while CC4 moved >= 20 within the last 50 ms, velocity < 50: drop
- hi-hat stick note on the other zone than the previous stroke, within 30 ms at any velocity:
  drop (one stroke read on both zones, 2026-09-20 evening: 8..25 ms apart at 0.7..1.5 x, up to
  122; nobody plays two hat strokes 30 ms apart; in the run logs 7 such, 6 strays, one GOOD
  whose edge twin had the note anyway).
- hi-hat stick note on the other zone than the previous stroke, within 50 ms at velocity
  <= 95: drop (near crosstalk, since 2026-09-19: one stroke heard twice, in Bitwig as two
  notes. Measured over ten days of bathero's MIDI trace, 118k notes: 174 bow notes within
  50 ms of an edge stroke, none over 92, none a chart note, the charts' closest hi-hat
  figure being 178 ms. It costs the real double that lands edge then bow within 50 ms at 95
  or under; until that date it was let through for that double's sake, and a friend who
  plays every hat stroke hard on the edge got a stray per stroke.)
- hi-hat stick note on the other zone than the previous stroke, within 115 ms at
  <= 70 % of its velocity: drop (late zone crosstalk; the reference stays the last real
  stroke, so chained ghosts fall too; 95 ms until 2026-09-20, when the hardest strokes,
  113..127, were seen ringing the other zone at 95..111 ms and 28..50 %, six strays a
  run on the kick gallop; 58 % until that evening, when the run logs showed 51 escapes at
  58..80 %, hat open or tight alike, 50 of them strays and one a chart note at 71 %: the
  double-kick levels leave the hat open and swinging, and a chained 46 at 60 % took the
  reference with it). This eats the soft real double the 2026-09-09 measurement kept
  (44..90 ms at 63..85 %): the user chose one note over two. Above 70 % nothing is dropped
  past 50 ms.
- kick note within 80 ms after the last real kick at <= 60 % of its velocity, or within
  250 ms at <= 40 %: drop (beater bounce, since 2026-09-19: burying the beater on the KD pad
  throws it back, 36..60 ms later at 12..54 %, a slower settle 160..250 ms later; on an
  acoustic drum a buried beater sounds once. Over 5081 logged kick strokes no real kick came
  within 70 ms of another or under 60 % of the previous within 250 ms; the fastest chart
  figure is 94 ms apart. The reference stays the last real kick.)
- snare note within 70 ms after the last real stroke **on the same zone** (head 38, rim 40) at
  <= 60 % of its velocity: drop (pad rebound, since 2026-09-22: a hard stroke comes back 41..60 ms
  later at 30..60 %, the median delay 51 ms on every one of the thirteen days of the MIDI trace and
  the median ratio 0.40..0.45, which is a pad retriggering, not a hand. Only strokes at 80 and over
  do it, and it is growing: 0.04 % of the day's snare strokes on 09-20, 1.78 % on 09-21, 2.65 % on
  09-22, the highest logged, so the module's retrigger-cancel is worth a look as well. Head and rim
  do not ring each other (1106 snare notes that day, not one 38/40 pair within 200 ms), hence a
  reference per zone; the reference stays the last real stroke, so a chain falls whole. Over the
  whole trace the rule drops 367 strays and 81 notes the judge had accepted, but 78 of those 81 are
  followed within 200 ms by a louder snare stray: the echo had taken the chart note and the real
  stroke behind it was counted a stray, so dropping it gives the note back. The fastest snare
  figure any chart writes is 89 ms apart.)
- chick with velocity <= 20: drop (hhmapper)
- any note with velocity < 8: drop (bathero)

A change to a threshold goes to both repos and to this section. Validated 2026-09-09 by
replaying two recorded takes (306 real strokes) through both filters: no real stroke
dropped, every ghost above still caught. Record a take with `mido` (timestamps in ms)
and replay it through `State` / `GhostFilter` before touching a number. Since 2026-09-19 the
MIDI trace (`~/Library/Logs/bathero-midi.log`: wall time, note, velocity, the filter's
reason or the judge, CC4) is the larger check: replay it and count what falls that was
judged PERFECT / GOOD / OK.

**Kit wizard rule (bathero).** The wizard has one step per zone of the TD-17
(17 zones, `chart.ZONES`): the hi-hat has three, bow, edge and pedal. In the
bow step hearing 42 or 46 assigns both; in the edge step 22 or 26 assigns
both; 44 is only assigned in the pedal step. A number heard in two steps goes
to the later zone. Charts still refer to instruments (kick/snare/hihat/crash,
plus tom1/floor/ride), and an instrument accepts every note of all its zones.
Menu navigation ignores hits under velocity 25; every other hit is one action, immediately,
no debounce (the user wants the game to feel instant); every hit above 15 is heard.
