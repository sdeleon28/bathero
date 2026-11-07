"""Edit a take with Claude Code: pick a style, no typing, and Claude cuts the video.

The game writes a job folder inside the take's folder (<take>/edits/<edition>-<style>/; for the
flat takes recorded before the folders existed, ~/Movies/bathero/edits/<take>/) with
job.json (the take, its sidecar with timing and the run logs of the levels played during
it, the chosen style and its brief) and starts `claude -p` there, allowed to use ffmpeg,
ffprobe and files only. Claude produces edit-<style>.mp4 in that folder and writes
claude.log. The game polls the thread and shows the state bottom right.
"""
import glob
import json
import os
import shutil
import subprocess
import threading
import time

from .capture import OUT_DIR

EDITS_DIR = os.path.join(OUT_DIR, "edits")
CLAUDE_BIN = shutil.which("claude") or next((c for c in (os.path.expanduser("~/.local/bin/claude"), "/opt/homebrew/bin/claude", "/usr/local/bin/claude") if os.path.exists(c)), None)

# (key, label, one-line blurb for the menu, brief for Claude)
STYLES = [
    ("hype", "Hype video", "fast cuts on the best moments, beat-synced, big title, under 60 s",
     "A hype edit under 60 seconds. Open with a bold title card (level name, date) for about a second. "
     "Cut to the strongest moments: long PERFECT streaks, fills, the ending with the stars. Use the run "
     "log to place cuts on downbeats and to find the streaks. Add speed ramps into the best fills "
     "(slow-mo 0.5x for the fill, snap back). Keep the take's own audio throughout (it is the music the "
     "drummer played to plus the drums); when you slow the picture, keep the audio at normal speed by "
     "cutting the picture instead of stretching audio. End on the results screen with the stars, hold "
     "two seconds, fade to black."),
    ("highlights", "Highlights", "the best 30..90 s, in order, with the results at the end",
     "A highlights reel of 30 to 90 seconds: the best streaks and fills in chronological order, each "
     "clip 4 to 12 seconds, hard cuts on downbeats (use the run log), a small lower-third with the level "
     "name for the first two seconds of each clip, the results screen with the stars at the end."),
    ("full", "Full, polished", "the whole take, title and lower-thirds, no cuts",
     "The whole take untouched in time: add a title card (level name, date) at the start over the first "
     "second of picture, a lower-third with the level name when each level begins (use the run logs), "
     "a fade in and out. Do not cut anything."),
    ("lesson", "Lesson", "full take, fills and accents slowed down and captioned",
     "A practice-review edit: the whole take, but every fill (a bar with toms or sixteenth snares in the "
     "chart) is shown twice: once at normal speed, then again at half speed with a caption saying which "
     "bar and what the fill is. Caption wrong dynamics (SOFT/LOUD in the run log) and misses with short "
     "text overlays at the moment they happen. Keep the take's audio under the normal-speed parts; the "
     "half-speed repeats can use the same audio slowed with atempo=0.5."),
    ("raw", "Raw with a title", "just a title card and an end card",
     "Only add a title card at the start (level name, date) and an end card with the stars and the grade "
     "from the run log. No cuts, no speed changes."),
]

PROMPT = """You are editing a drum-practice video for the drummer who recorded it. Everything you need is in
job.json in the current directory. Work only in this directory. Use ffmpeg and ffprobe (both are
installed; the h264_videotoolbox encoder is available and fast) and write files here. Do not modify or
delete the inputs.

job.json fields:
- take: the recorded mp4 (the game's picture, the mix the drummer heard, and the camera, picture-in-picture
  or under the game in the 9:16 social edition). take_meta: its sidecar: t0 (epoch seconds of the first
  frame), duration, fps, size (of the computer raw; the mp4's own size may differ, check it with ffprobe).
- run_logs: JSON-lines files, one per level played during the take. Line 1 is a header (chart with every
  note: t in chart seconds, key, accent; stats with grade and stars; offset). The other lines are events
  with "wall" = epoch seconds: kind "hit" (judge PERFECT/GOOD/OK/STRAY, error_ms, velocity, dyn
  ACCENT/TAP/SOFT/LOUD, chart_t, combo), "miss", "ghost", "cc". Video time of an event = wall - t0.
  The chart's downbeats fall on chart_t multiples of 4 * 60 / bpm (bpm is in the header); a level's
  chart time 0 is at the wall time of its first hit minus that hit's chart_t.
- style, brief: what to make. Follow the brief.

Deliver exactly one file: {output}. Also write notes.md with the edit decisions (which moments and why)
in a few lines. Use drawtext for titles and captions (a system font like /System/Library/Fonts/Helvetica.ttc
or Menlo); keep text inside the frame, readable, no more than two lines. Keep the output 1920x1080 or the
take's own size, 30 fps, h264_videotoolbox at 12M, AAC 192k, faststart. Check the result with ffprobe
(duration, streams) before finishing, and finish with one line: DONE <path> <duration seconds>."""


def sidecar_for(take):
    """take.json of a take folder, or the legacy <take>.json next to a flat mp4."""
    p = os.path.join(take, "take.json") if os.path.isdir(take) else take[:-4] + ".json"
    try:
        with open(p) as f:
            return json.load(f)
    except (OSError, ValueError):
        return None


def takes():
    """Recorded takes, newest first: take folders (raws and editions, see capture.py) and the
    flat mp4 files of takes recorded before the folders existed."""
    out = [p for p in glob.glob(os.path.join(OUT_DIR, "*")) if os.path.isdir(p) and os.path.exists(os.path.join(p, "take.json"))]
    out += glob.glob(os.path.join(OUT_DIR, "*.mp4"))
    return sorted(out, key=os.path.getmtime, reverse=True)


def label(take):
    return os.path.basename(take) if os.path.isdir(take) else os.path.basename(take)[:-4]


def editions(take):
    """{edition: mp4 path} rendered so far; a legacy flat take is its own computer edition."""
    if not os.path.isdir(take):
        return {"computer": take}
    meta = sidecar_for(take) or {}
    return {k: os.path.join(take, v) for k, v in meta.get("editions", {}).items() if os.path.exists(os.path.join(take, v))}


def has_raws(take):
    """True for a take folder whose screen raw is still there (editions can be rendered again)."""
    meta = sidecar_for(take) or {}
    raw = meta.get("raws", {}).get("screen")
    return bool(os.path.isdir(take) and raw and os.path.exists(os.path.join(take, raw["file"])))


def video_for(take, prefer="computer"):
    """The edition to hand to Claude: the preferred one, else any rendered one, else None."""
    eds = editions(take)
    return eds.get(prefer) or next(iter(eds.values()), None)


class Editor:
    """One edit job at a time."""

    def __init__(self, claude_bin=None, log=print):
        self.claude_bin = claude_bin or CLAUDE_BIN
        self.log = log
        self.thread = None
        self.job_dir = None
        self.style = None
        self.result = None       # path of the finished edit
        self.error = None
        self.started_at = None

    @property
    def busy(self):
        return self.thread is not None and self.thread.is_alive()

    def start(self, take, style_key, prefer="computer"):
        """take: a take folder or a legacy mp4; the edition `prefer` (else any rendered one) is edited."""
        if self.busy or not take:
            return False
        if not self.claude_bin or not os.path.exists(self.claude_bin):
            self.error = "claude CLI not found"
            return False
        video = video_for(take, prefer)
        if video is None:
            self.error = "no edition rendered yet: render one first (computer or social)"
            return False
        style = next(s for s in STYLES if s[0] == style_key)
        base = label(take)
        if os.path.isdir(take):                  # the edit lives in the take's folder: edits/<edition>-<style>/
            self.job_dir = os.path.join(take, "edits", f"{os.path.basename(video)[:-4]}-{style_key}")
        else:                                    # a take recorded before the folders: ~/Movies/bathero/edits/<take>/
            self.job_dir = os.path.join(EDITS_DIR, base)
        os.makedirs(self.job_dir, exist_ok=True)
        meta = sidecar_for(take) or {}
        output = os.path.join(self.job_dir, f"edit-{style_key}.mp4")
        job = {"take": os.path.abspath(video), "take_meta": meta, "run_logs": meta.get("run_logs", []),
               "style": style_key, "brief": style[3], "output": output, "created": time.time()}
        with open(os.path.join(self.job_dir, "job.json"), "w") as f:
            json.dump(job, f, indent=1)
        self.style, self.result, self.error, self.output = style, None, None, output
        self.started_at = time.perf_counter()
        self.thread = threading.Thread(target=self._run, args=(job,), daemon=True)
        self.thread.start()
        self.log(f"edit started: {style[1]} on {base}")
        return True

    def _run(self, job):
        prompt = PROMPT.format(output=job["output"])
        cmd = [self.claude_bin, "-p", prompt, "--output-format", "text", "--max-turns", "80",
               "--permission-mode", "acceptEdits",
               "--allowedTools", "Read", "Write", "Edit", "Glob", "Grep", "Bash(ffmpeg:*)", "Bash(ffprobe:*)",
               "Bash(ls:*)", "Bash(cat:*)", "Bash(mkdir:*)", "Bash(cp:*)", "Bash(python3:*)",
               "--add-dir", os.path.dirname(job["take"])]
        env = {k: v for k, v in os.environ.items() if k != "CLAUDECODE"}   # allow nesting from a session
        with open(os.path.join(self.job_dir, "claude.log"), "w") as logf:
            try:
                r = subprocess.run(cmd, cwd=self.job_dir, stdout=logf, stderr=subprocess.STDOUT, text=True, env=env, timeout=3600)
                code = r.returncode
            except subprocess.TimeoutExpired:
                code = -1
        if os.path.exists(job["output"]) and os.path.getsize(job["output"]) > 1000:
            self.result = job["output"]
            self.log(f"edit saved: {self.result}")
        else:
            self.error = f"edit failed (exit {code}), see {os.path.join(self.job_dir, 'claude.log')}"
            self.log(self.error)

    @property
    def status(self):
        if self.busy:
            s = int(time.perf_counter() - self.started_at)
            return f"Claude editing ({self.style[1].lower()}) {s // 60:02d}:{s % 60:02d}"
        if self.result:
            return f"edit saved: {os.path.basename(self.result)}"
        return self.error
