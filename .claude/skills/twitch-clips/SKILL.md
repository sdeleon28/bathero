---
name: twitch-clips
description: Cut a recording of the user's Twitch stream (a video file whose path they give) into clips for distribution (16:9 and 9:16), choosing the moments from the game's own data about what happened. Use when the user asks for clips of a stream, to edit a VOD or stream recording, or to "editar el stream". Takes the file path plus optional instructions (which parts, how to edit).
---

# Clips from a stream recording

The first argument is the path of the video (a Twitch VOD the user downloaded, a screen recording,
an export). The rest, if any, are instructions. No path: ask for it and stop, nothing can be done.
No browser, no downloading: the file is the input.

The stream is the display as the viewers saw it: the game full frame, the camera as a
picture-in-picture in a corner, the LIVE badge top right, sometimes the chat pane bottom left.
Every stream's audio also stays as an AAC copy in
`~/Movies/bathero/streams/<local start stamp> stream.aac`, and the daemon's log
(`~/Library/Logs/bathero/stream.log`) says how long each stream ran.

## Rules

- **Never start or stop the stream**: no T, no `python -m bathero.twitch --stop`, no touching
  the daemon.
- **Never publish**: the clips land in a folder, the user distributes them. Do not upload, post,
  or share anything.
- Never modify or move the input file.
- Never leave a clip unwatched: every delivered file is probed and frames of it looked at.
- **The style is fixed** (asked for 2026-09-17: "use the same style for future clips"): the
  functions in `style.sh` next to this file, described below. A new look is not an option
  unless the instructions ask for one; a new layout for a situation the style has not got
  (like a stretch with the camera off) goes into `style.sh` as another function, so the next
  run has it.

## 1. The video's clock against the game's

Video time = wall time - start of the video - offset. The helper does that for the run logs and
the takes:

    .venv/bin/python .claude/skills/twitch-clips/moments.py --file <video> [--align] [--start EPOCH] [--offset S] [--json]

**Run it with `--align` first.** A Twitch VOD is the whole stream, so the helper finds the
stream whose length (from the daemon's log) matches the video's, and `--align` cross-correlates
90 s of the video's sound with that stream's AAC copy: the start comes out to the millisecond
(2026-09-16: the VOD's first frame was 6.024 s after the daemon's start, peak 0.93; the badge in
the picture agrees, it reads "LIVE 00:06" on the first frame). Without `--align` it guesses (a
`YYYYmmdd-HHMMSS` stamp in the file name, else the matching stream plus 6 s, else the container's
creation_time, else mtime minus duration, which for a download is just when it was downloaded)
and says which. `--start` overrides everything.

It prints every level played during the video on the video's clock (name, bpm, grade, stars, max
combo, misses, longest PERFECT/GOOD streak and where it ends), every take the drummer chose to
record, and suggested clips (levels with 2 stars or more, 2 s of run-up, 4 s of results screen,
best first). `--json` gives the same with `chart_zero` per level (video time of the chart's beat
0, so a downbeat is at `chart_zero + k * 4 * 60 / bpm`), `started` (the count-in begins 4 beats
before chart_zero) and the run log paths, for cutting on the beat. `--layout WxH` prints the
camera rectangle for the 9:16.

**Check the alignment on the picture before cutting**, always, even after `--align`: extract a
frame at a level's first hit (`ffmpeg -ss T -i video -frames:v 1 frame.png`, read it) and see
the note at the line, and frames around the level's end for the RESULTS card. A montage of
several frames in one image (`xstack`) reads faster than one at a time; a strip of the RESULTS
title region at 5 fps (`fps=5,crop=700:120:610:140,tile=5x6`, with `drawtext='%{pts\:hms}'` for
the stamps) gives the card's exact window. If it is off by more than a second and `--align`
found no match, measure the offset on the picture and pass `--offset`; write it in notes.md.
If no run log falls inside the video, say so and cut only from the instructions (or ask).

Things the run log does not say, learned 2026-09-16:
- `ended` is when the results screen was left, not when it appeared: the RESULTS card comes up
  about 2 s after the last note (the level's tail) and stays until Enter or R. Cut 3..4 s into
  the card, and check what the display shows after it: the user may switch to the browser or the
  terminal while the card is up (the stream is the whole display), and the next level's count-in
  starts the moment they press Enter.
- The camera may be off for a stretch (the ! layer off, no PiP, no chat pane): the 9:16 must
  not crop the PiP rectangle then (it would be the highway). `gameonly()` is for those.
- A run with more hits than notes ran a loop (`l` + digits); a level abandoned after a few
  seconds and restarted with R sits right before the real run ("falso arranque"): 8 s of it
  before the count-in is a good opening.
- The velocity viewer (bottom left, over the chat pane's place) and the Claude Code terminal
  can be on screen; they go out as they are, they are the stream.

## 2. Which moments

With instructions, follow them: parts as video times ("1:02:30-1:03:10"), as descriptions ("the
four stars", "the last level", "when the plena finally passed"), or as a style ("hype under 60 s",
"one long highlight", "only the 9:16"). Map descriptions onto the helper's list.

By default, 3 to 6 clips, from these, in this order of preference:
1. The best runs (most stars, then grade): the whole level from the count-in (or the false start
   before it) to the stars on the results screen.
2. The same run short ("el remate"): its last two or three bars and the card, 15..20 s, for
   Shorts / TikTok. The title counts the strokes ("LOS ULTIMOS 54 GOLPES": hits with
   `chart_t` from that bar on, from the run log).
3. A level passed after failing it, or the first full pass of the night if a later run beat it
   (a story across the night).
4. The longest PERFECT/GOOD streak of the night, if it is inside a run that did not make the cut.
5. Takes the drummer recorded during the stream: they chose those moments.
Clip lengths: 15..60 s for the 9:16, up to 90 s for the 16:9. Nothing shorter than 10 s, nothing
over 3 minutes unless asked. Skip a level under 10 s (an abandoned start) unless it opens the
real run. Runs played mostly off screen (the terminal in front) and runs full of misses are out.

## 3. Cut and edit

Output folder: `~/Movies/bathero/clips/<YYYYMMDD> <label>/` (the video's local date, the label
from the main level or the instructions, like `20260913 31 Plena 100` and
`20260916 2 paradiddles 5 estrellas`). In it: `clips.sh` (sets `SRC`, `OUT`, `CAM`, sources
`style.sh`, lists the cuts with their times and titles: it regenerates everything, and the user
edits the times or the titles there), two files per clip numbered in video order,
`NN <what happens> social.mp4` (1080x1920) and `NN <what happens> computer.mp4` (1920x1080),
and `notes.md` in Spanish (the user's LEEME): a table of clip, VOD in..out, what happens, files;
the alignment (start, how measured, offset); what was left out and why; anything to tell the
user about the stream. Clip names and titles are Spanish ("falso arranque y 144 de 144",
"el remate", "la primera vuelta completa").

**The style** (`style.sh`; from `~/Downloads/bathero-clips-20260913`, the reference the user
pointed at). Menlo, titles in Spanish caps with the middle dot as the separator
(`'31 · PLENA · 96 BPM'`, `'2 PARADIDDLES · SIX STROKE · 1 MORE'`: commas and colons break
drawtext), a smaller second line under the title (`'60 BPM · MANO IZQUIERDA · 5 ESTRELLAS'`),
`twitch.tv/xantwav` under the picture, no fades longer than half a second, sound at -14 LUFS
(the platforms' level) with a limiter after loudnorm (its single pass let an accent through at
+1.3 dBFS once the AAC encoder overshot). The Homebrew ffmpeg (`/opt/homebrew/bin/ffmpeg`): the
anaconda one on PATH has no drawtext.
- `game()`, 9:16: the camera blurred and darkened as the ground, the screen as a strip across
  the top, the camera big under it (cropped out of the PiP and upscaled, a touch of unsharp),
  the title over the top band, the handle under the camera.
- `game_chat()`: the same with the chat pane's last lines under the camera, when the chat has
  lines (the 2026-09-13 layout). An empty pane is not shown.
- `gameonly()`: the screen strip centred over a blurred, purple-tinted copy of its highway, for a
  stretch streamed with the camera off (the game alone is nearly black).
- `computer()`, 16:9: the frame as seen, the title for 4 s in the empty area right of the
  highway (x 1180, y 340; the highway is x 770..1150, the velocity panel and the chat pane fill
  the bottom left, the camera is bottom right from y 668, the description line crosses at y 600).
- `CAM` comes from `moments.py --layout 1920x1080` (the PiP rectangle from the current
  settings): check on a frame that it is the camera, the corner may have been different.
- **Cuts on the beat**: a clip starts on the count-in (`started`, 4 beats before chart_zero) or
  on a downbeat (`chart_zero + k * bar`), and ends 3..4 s into the RESULTS card. Never stretch
  the audio; a slow-motion repeat is picture only, and only when asked.
- Keep the LIVE badge, the velocity viewer and the chat pane as they are (the stream as seen)
  unless the instructions say to crop them off.

## 4. Check and report

For every delivered file: `ffprobe -v error -show_entries format=duration:stream=width,height,r_frame_rate,codec_name -of compact`
(the right size, 30 fps, H.264, an audio stream, the expected length), `ffmpeg -af ebur128=peak=true`
(-14 LUFS ± 1, peak under -1 dBFS), and frames read as images: one at 1 s (the title inside the
frame, not over the HUD), one from the middle, one from the end (the stars): the camera crop is
the camera, nothing letterboxed by mistake, the title over nothing. Fix and re-render anything
wrong before reporting (a full render of six files is about a minute).

Report in a short table: clip number, video in..out, what happens, the two files. Say which video
it was (name, date, length), the start and how it was measured, and what was left out. Do not
post anywhere. End with the user's next action: open the folder, watch the 9:16 ones on the phone,
and say which to distribute or what to change (`/twitch-clips <path> <the wish>`).
