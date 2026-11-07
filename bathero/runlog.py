"""Run log: everything a level saw, written once when the level ends, so a run can be
audited afterwards (bathero.audit) and the code tuned against it.

Recording is a list append per event (MIDI thread or main thread, no locks, no I/O), so
it costs nothing the player can feel. The file is JSON lines under
~/Library/Logs/bathero/runs/: a "run" header with the chart, the kit, the settings and
the judging thresholds, then one line per MIDI message, ghost, hit, miss and change.
"""
import json
import os
import time

RUNS_DIR = os.path.expanduser("~/Library/Logs/bathero/runs")


class RunLog:
    def __init__(self):
        self.events = []        # (perf_counter, wall time, kind, fields)
        self.header = None

    # --- recording (hot paths) ---------------------------------------------------
    def add(self, kind, **fields):
        self.events.append((time.perf_counter(), time.time(), kind, fields))

    # --- level boundaries ----------------------------------------------------------
    def start(self, chart, lanes, kit, settings, extra):
        """Begin a level: drop what came before, remember the context to write."""
        self.events = []
        self.header = {
            "kind": "run",
            "started": time.time(),
            "chart": {
                "name": chart.key, "lead": chart.lead, "bpm": chart.bpm, "rate": chart.rate, "desc": chart.desc,
                "dynamics": chart.dynamics, "expression": chart.expression, "sticking": chart.sticking,
                "accents": sorted(chart.accents) if chart.accents else None,
                "notes": [{"t": round(n.t, 5), "key": n.key, "vel": n.velocity, "accent": n.accent, "hand": n.hand, "art": n.art}
                          for n in chart.notes],
            },
            "lanes": [{"index": l.index, "key": l.key, "label": l.label, "notes": sorted(l.notes)} for l in lanes],
            "kit": kit,
            "settings": settings,
            **extra,
        }

    def write(self, stats=None, path=None):
        """Write the level's log. Returns the path, or None if nothing was recorded."""
        if self.header is None:
            return None
        os.makedirs(RUNS_DIR, exist_ok=True)
        if path is None:
            stamp = time.strftime("%Y%m%d-%H%M%S", time.localtime(self.header["started"]))
            safe = "".join(c if c.isalnum() or c in "-_" else "_" for c in self.header["chart"]["name"])
            path = os.path.join(RUNS_DIR, f"{stamp} {safe}.jsonl")
        header = dict(self.header)
        header["ended"] = time.time()
        if stats is not None:
            header["stats"] = stats
        events = self.events
        with open(path, "w") as f:
            f.write(json.dumps(header) + "\n")
            for pc, wall, kind, fields in events:
                rec = {"t": round(pc, 6), "wall": round(wall, 6), "kind": kind}
                rec.update(fields)
                f.write(json.dumps(rec) + "\n")
        self.header = None
        self.events = []
        return path


def load(path):
    """(header, events) of a run log file."""
    with open(path) as f:
        lines = [json.loads(l) for l in f if l.strip()]
    return lines[0], lines[1:]
