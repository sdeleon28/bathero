"""The Midi Fighter Twister's second bank as the song mixer.

Numbering as in GroupCtl (bitwig-maps): encoder 1 is bottom left, the numbers run left to
right and up a row every four, 16 is top right. GroupCtl owns bank 1 (CC 0..15 on channel 1);
bathero listens to bank 2, CC 16..31, so the two never clash. Encoders send absolute
values 0..127; a CC sent back on the same number sets the ring (the Twister keeps each
bank's rings, so they are right when the bank is switched to).

    1 backing track   2 drum stem   3 metronome   4 tempo   16 everything (master)
"""
import threading

import mido

PORT_HINT = "twister"
BANK2 = 16
KNOBS = {1: "backing", 2: "drums", 3: "metronome", 4: "tempo", 16: "master"}


def cc_for(n):
    """GroupCtl's encoderToCC, on bank 2: human position 1..16 -> CC."""
    n0 = n - 1
    return BANK2 + (3 - n0 // 4) * 4 + n0 % 4


POSITION = {cc_for(n): n for n in range(1, 17)}


class Twister:
    """Opens the Twister's ports (if it is plugged in) and hands (name, 0..1) to `on_turn`,
    from the MIDI thread: the receiver queues it for the main thread."""

    def __init__(self, on_turn):
        self.on_turn = on_turn
        self.inp = self.out = None
        self.name = None
        self.lock = threading.Lock()

    def open(self):
        if self.inp is not None:
            return True
        ins = [n for n in mido.get_input_names() if PORT_HINT in n.lower()]
        if not ins:
            return False
        try:
            self.inp = mido.open_input(ins[0], callback=self._cb)
            self.name = ins[0]
            outs = [n for n in mido.get_output_names() if PORT_HINT in n.lower()]
            if outs:
                self.out = mido.open_output(outs[0])
        except (OSError, IOError):
            self.close()
            return False
        return True

    def _cb(self, msg):
        if msg.type != "control_change" or msg.channel != 0:
            return
        n = POSITION.get(msg.control)
        if n in KNOBS:
            self.on_turn(KNOBS[n], msg.value / 127)

    def ring(self, name, v):
        """Set a knob's ring to v (0..1)."""
        if self.out is None:
            return
        n = next((k for k, x in KNOBS.items() if x == name), None)
        if n is not None:
            with self.lock:
                try:
                    self.out.send(mido.Message("control_change", channel=0, control=cc_for(n), value=int(round(max(0, min(1, v)) * 127))))
                except (OSError, IOError):
                    pass

    def close(self):
        for p in (self.inp, self.out):
            if p is not None:
                try:
                    p.close()
                except Exception:
                    pass
        self.inp = self.out = None
