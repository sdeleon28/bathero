"""The coach: Claude reads the progress report and answers with strengths, weaknesses,
the focus for the next session, an exercise diet and playlists the game can run as
training sessions.

The report (stats.report) is written to ~/.config/bathero/coach/report.json and
`claude -p` is started there with the Write tool only; it writes coach.json, which is
validated (playlist items must name real levels) and kept with a timestamp.
"""
import json
import os
import shutil
import subprocess
import threading
import time

COACH_DIR = os.path.expanduser("~/.config/bathero/coach")
CLAUDE_BIN = shutil.which("claude") or next((c for c in (os.path.expanduser("~/.local/bin/claude"), "/opt/homebrew/bin/claude", "/usr/local/bin/claude") if os.path.exists(c)), None)

PROMPT = """You are a drum teacher. report.json in this directory describes a student's practice in a
drum-training game: the catalogue of levels (exercises: single drums, rudiments and accent control on
a practice pad; beats: a curriculum of grooves that add one idea each; courses: one genre each, its
grooves and fills in order, e.g. pop punk), every level they have
played with best and mean results, the last runs in detail, and their practice habit.

Metrics: grade 0..100 (stars at 30/50/70/85/94), accuracy = notes hit, mean_ms = timing bias
(positive = late), std_ms = timing consistency (lower is tighter; under 15 is excellent, 15..25
good, over 35 loose), strays = hits with no note, accents/taps = dynamics on rudiments (contrast =
accent velocity / tap velocity, 1.4 is the target), rate = tempo multiplier they played at.

Write coach.json here, valid JSON and nothing else in the file, with exactly these keys:
{{
  "strengths": ["3 to 5 short specific sentences, each citing the evidence (levels, numbers)"],
  "weaknesses": ["3 to 5 short specific sentences, each citing the evidence"],
  "focus_next_session": "2 or 3 sentences: the single most valuable thing to work on next time and how to know it improved",
  "diet": "a weekly plan in 4 to 6 lines: what to play, how often, at what tempo, when to move on",
  "playlists": [
    {{"name": "short name", "goal": "one line", "minutes": 15,
      "items": [{{"level": "exact level name from levels_catalogue (or its left_hand_lead name)", "rate": 0.9, "reps": 2, "why": "short"}}]}}
  ]
}}
Make 3 playlists of 4 to 8 items each: a warm-up and fundamentals session, a session that attacks
the main weakness, and a session that stretches toward the next level of the curriculum (include
levels not yet played when they are the natural next step). Rates between 0.6 and 1.3; use slower
rates for levels with low accuracy or loose timing, and a faster one only for levels with 4-5 stars.
Level names must match the catalogue exactly. Write everything in {language}. Finish with the single
word DONE."""

LANGUAGES = {"es": "Spanish (rioplatense, informal)", "en": "English"}


def load_coach(path=None):
    path = path or os.path.join(COACH_DIR, "coach.json")
    try:
        with open(path) as f:
            return json.load(f)
    except (OSError, ValueError):
        return None


class Coach:
    def __init__(self, claude_bin=None, language="es", model=None, log=print, coach_dir=None):
        self.claude_bin = claude_bin or CLAUDE_BIN
        self.language = language
        self.model = model
        self.log = log
        self.dir = coach_dir or COACH_DIR
        self.thread = None
        self.error = None
        self.started_at = None
        self.result = load_coach(os.path.join(self.dir, "coach.json"))

    @property
    def busy(self):
        return self.thread is not None and self.thread.is_alive()

    def start(self, report, level_names):
        if self.busy:
            return False
        if not self.claude_bin or not os.path.exists(self.claude_bin):
            self.error = "claude CLI not found"
            return False
        os.makedirs(self.dir, exist_ok=True)
        with open(os.path.join(self.dir, "report.json"), "w") as f:
            json.dump(report, f, indent=1)
        self.error = None
        self.started_at = time.perf_counter()
        self.thread = threading.Thread(target=self._run, args=(set(level_names),), daemon=True)
        self.thread.start()
        self.log("coach: asking Claude...")
        return True

    def _run(self, level_names):
        out = os.path.join(self.dir, "coach.json")
        if os.path.exists(out):
            os.replace(out, os.path.join(self.dir, "coach-previous.json"))
        prompt = PROMPT.format(language=LANGUAGES.get(self.language, self.language))
        cmd = [self.claude_bin, "-p", prompt, "--output-format", "text", "--max-turns", "12",
               "--permission-mode", "acceptEdits", "--allowedTools", "Read", "Write"]
        if self.model:
            cmd += ["--model", self.model]
        env = {k: v for k, v in os.environ.items() if k != "CLAUDECODE"}
        with open(os.path.join(self.dir, "claude.log"), "w") as logf:
            try:
                r = subprocess.run(cmd, cwd=self.dir, stdout=logf, stderr=subprocess.STDOUT, text=True, env=env, timeout=900)
                code = r.returncode
            except subprocess.TimeoutExpired:
                code = -1
        data = load_coach(out)
        if not data or "playlists" not in data:
            self.error = f"coach failed (exit {code}), see {os.path.join(self.dir, 'claude.log')}"
            self.log(self.error)
            return
        # keep only playlist items that name real levels
        for pl in data.get("playlists", []):
            pl["items"] = [it for it in pl.get("items", []) if it.get("level") in level_names]
        data["playlists"] = [pl for pl in data["playlists"] if pl["items"]]
        data["generated"] = time.time()
        with open(out, "w") as f:
            json.dump(data, f, indent=1, ensure_ascii=False)
        self.result = data
        self.log(f"coach: done, {len(data['playlists'])} playlists")

    @property
    def status(self):
        if self.busy:
            s = int(time.perf_counter() - self.started_at)
            return f"coach thinking {s // 60:02d}:{s % 60:02d}"
        return self.error
