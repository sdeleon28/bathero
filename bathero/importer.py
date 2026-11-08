"""The song importer (bathero.ingest) seen from the game: is it running, what is it doing, on / off.

The importer is its own process in its own venv (.venv-ingest): the game never imports torch. It
writes songs.STATUS_PATH (see bathero.ingest.Status); this reads it, at most twice a second.
`start` spawns `.venv-ingest/bin/python -m bathero.ingest --watch` in a new session, niced, its
output appended to ~/Library/Logs/bathero/ingest.log, so it outlives the game (a deploy relaunch
does not stop an import half way); `stop` sends it SIGTERM, which it answers by killing the step in
hand, deleting its temporary files and marking itself stopped.
"""
import json
import os
import signal
import subprocess
import time

from .songs import STATUS_PATH

REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
PYTHON = os.path.join(REPO, ".venv-ingest", "bin", "python")
LOG = os.path.expanduser("~/Library/Logs/bathero/ingest.log")
STALE_S = 10            # a status not refreshed for this long belongs to a process that died
NICE = 10


def _alive(pid):
    try:
        os.kill(pid, 0)
        return True
    except (OSError, TypeError):
        return False


class Importer:
    def __init__(self):
        self.st = {}
        self.read_at = 0.0
        self.started_at = 0.0          # a start requested: "starting" until the process writes its status

    def status(self):
        """The importer's status; {} when no importer runs."""
        now = time.time()
        if now - self.read_at > 0.5:
            self.read_at = now
            try:
                with open(STATUS_PATH) as f:
                    st = json.load(f)
            except (OSError, ValueError):
                st = {}
            if not st.get("pid") or not _alive(st["pid"]) or now - st.get("updated", 0) > STALE_S:
                st = {"failed": st.get("failed", {}), "imported": st.get("imported", 0), "last": st.get("last")}
            self.st = st
        return self.st

    @property
    def running(self):
        return bool(self.status().get("pid"))

    @property
    def watching(self):
        return bool(self.status().get("watching"))

    @property
    def starting(self):
        return not self.running and time.time() - self.started_at < 15

    def available(self):
        return os.path.exists(PYTHON)

    def start(self):
        if self.running or self.starting or not self.available():
            return False
        os.makedirs(os.path.dirname(LOG), exist_ok=True)
        with open(LOG, "a") as log:
            subprocess.Popen(["nice", "-n", str(NICE), PYTHON, "-m", "bathero.ingest", "--watch"], cwd=REPO,
                             stdin=subprocess.DEVNULL, stdout=log, stderr=subprocess.STDOUT, start_new_session=True)
        self.started_at = time.time()
        return True

    def stop(self):
        pid = self.status().get("pid")
        if pid:
            try:
                os.kill(pid, signal.SIGTERM)
            except OSError:
                pass
        self.started_at = 0.0
        self.read_at = 0.0

    def current(self):
        """(title, label, fraction done 0..1, seconds on this song) of the song in hand, or None."""
        cur = self.status().get("current")
        if not cur:
            return None
        now = time.time()
        steps, expect = cur["steps"], cur["expect"]
        k = cur.get("step")
        if k is None:
            frac = 0.0
        else:
            within = min(0.95, (now - cur["step_started"]) / max(1.0, expect[k]))   # never full until the step ends
            frac = (sum(expect[:k]) + within * expect[k]) / max(1.0, sum(expect))
        label = cur["label"] + (f" · {k + 1}/{len(steps)}" if k is not None and len(steps) > 1 else "")
        return cur["title"], label, frac, now - cur["started"]
