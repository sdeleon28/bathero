"""How long a MIDI message waited before Python got to it (asked for 2026-11-14: "the judging
goes off at times"). rtmidi stamps every message in C++ when CoreMIDI delivers it, with no GIL;
mido throws that stamp away. The stamp comes as the delta since the previous message, so their
sum is the arrival time on rtmidi's clock; perf_counter() minus that sum, at the moment the
callback runs, is a constant plus the wait. The smallest value seen is taken as the constant
(a message that did not wait), so `lag` is the wait in seconds. A stand-alone run:
`python -m bathero.midilag [PATH.jsonl ...]` summarises the lag in run logs."""
import time


class LagMeter:
    def __init__(self):
        self.sum = 0.0
        self.base = None
        self.lag = None          # seconds, of the message being handled now (callback thread)
        self.arrived = None      # perf_counter time the message arrived

    def install(self, port):
        """Put the meter between rtmidi and mido's own wrapper on an open mido rtmidi input."""
        rt = getattr(port, "_rt", None)
        wrap = getattr(port, "_callback_wrapper", None)
        if rt is None or wrap is None:
            return False

        def cb(event, data):
            msg_data, delta = event
            now = time.perf_counter()
            self.sum += delta
            off = now - self.sum
            if self.base is None or off < self.base:
                self.base = off
            self.lag = off - self.base
            self.arrived = now - self.lag
            wrap(event, data)

        with port._callback_lock:
            rt.cancel_callback()
            rt.set_callback(cb)
        return True


def main(argv=None):
    import argparse, glob, json, os
    ap = argparse.ArgumentParser()
    ap.add_argument("paths", nargs="*")
    ap.add_argument("--last", type=int, default=10)
    a = ap.parse_args(argv)
    paths = a.paths or sorted(glob.glob(os.path.expanduser("~/Library/Logs/bathero/runs/**/*.jsonl"), recursive=True),
                              key=os.path.getmtime)[-a.last:]
    for p in paths:
        lags = []
        with open(p) as f:
            for line in f:
                try:
                    e = json.loads(line)
                except ValueError:
                    continue
                if isinstance(e, dict) and "lag" in e:
                    lags.append(e["lag"])
        if not lags:
            continue
        lags.sort()
        q = lambda x: lags[min(len(lags) - 1, int(len(lags) * x))]
        big = sum(1 for v in lags if v >= 5)
        print(f"{os.path.basename(p)}: {len(lags)} notes, lag ms p50 {q(.5):.1f} p90 {q(.9):.1f} "
              f"p99 {q(.99):.1f} max {lags[-1]:.1f}, >=5 ms: {big}")


if __name__ == "__main__":
    main()
