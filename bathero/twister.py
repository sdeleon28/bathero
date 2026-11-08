"""The Midi Fighter Twister's second bank as the song mixer.

Numbering as in GroupCtl (bitwig-maps): encoder 1 is bottom left, the numbers run left to
right and up a row every four, 16 is top right. GroupCtl owns bank 1 (CC 0..15 on channel 1);
bathero listens to bank 2, CC 16..31, so the two never clash. Encoders send absolute
values 0..127 on channel 1; a CC sent back on the same number sets the ring (the Twister
keeps each bank's rings, so they are right when the bank is switched to). Pushing an encoder
sends the same CC on channel 2, 127 down and 0 up: a push resets the knob to its neutral value.

    1 backing track   2 drum stem   3 metronome   4 tempo   5 offset (0..127 ms)
    15 menu music   16 everything (master)

The bank buttons send CC 0..3 on channel 4 (bank 1..4), 127 for the bank entered and 0 for the
one left: `on_bank` gets the bank entered (bathero.launchpad takes the Launchpad on bank 2).
"""
import threading

import mido

PORT_HINT = "twister"
BANK2 = 16
KNOBS = {1: "backing", 2: "drums", 3: "metronome", 4: "tempo", 5: "offset", 15: "menu", 16: "master"}
ENCODER_CH, BUTTON_CH, SYSTEM_CH = 0, 1, 3
BANKS = 4
# The LEDs as GroupCtl paints them (TwisterOutput.paint, Colors.BITWIG_TO_TWISTER): the colour's
# hue on channel 2 (the button channel, the other direction), the brightness on channel 3.
COLOR_CH, BRIGHTNESS_CH, FULL_BRIGHTNESS = 1, 2, 47
COLORS = {"backing": 87, "drums": 44, "metronome": 66, "tempo": 19, "offset": 123, "menu": 88, "master": 85}
#          magenta       green        yellow           cyan          blue           pink        red


def cc_for(n):
    """GroupCtl's encoderToCC, on bank 2: human position 1..16 -> CC."""
    n0 = n - 1
    return BANK2 + (3 - n0 // 4) * 4 + n0 % 4


POSITION = {cc_for(n): n for n in range(1, 17)}


class Twister:
    """Opens the Twister's ports (if it is plugged in) and hands (name, 0..1) to `on_turn` and
    a knob's name to `on_press`, from the MIDI thread: the receiver queues them for the main thread."""

    def __init__(self, on_turn, on_press, on_bank=None):
        self.on_turn = on_turn
        self.on_press = on_press
        self.on_bank = on_bank
        self.inp = self.out = None
        self.name = None
        self.lock = threading.Lock()
        self.seen = set()          # (channel, control) already logged: one line per knob, to bathero.log

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
            print(f"twister: opened '{self.name}' (out: {outs[0] if outs else 'none'})", flush=True)
        except (OSError, IOError):
            self.close()
            return False
        return True

    def _cb(self, msg):
        if msg.type != "control_change":
            return
        if msg.channel == SYSTEM_CH and msg.control < BANKS:
            if msg.value > 0 and self.on_bank is not None:
                self.on_bank(msg.control + 1)
            return
        name = KNOBS.get(POSITION.get(msg.control))
        if (msg.channel, msg.control) not in self.seen:
            self.seen.add((msg.channel, msg.control))
            print(f"twister: channel {msg.channel + 1} CC {msg.control} value {msg.value} -> "
                  f"{name or 'not ours'} (knob {POSITION.get(msg.control, '-')})", flush=True)
        if name is None:
            return
        if msg.channel == ENCODER_CH:
            self.on_turn(name, msg.value / 127)
        elif msg.channel == BUTTON_CH and msg.value > 0:
            self.on_press(name)

    def _send(self, channel, n, value):
        with self.lock:
            try:
                self.out.send(mido.Message("control_change", channel=channel, control=cc_for(n), value=value))
            except (OSError, IOError):
                pass

    def paint(self):
        """Colour every knob bathero uses (bank 2 only: the rest stay as they are)."""
        if self.out is None:
            return
        for n, name in KNOBS.items():
            self._send(COLOR_CH, n, COLORS[name])
            self._send(BRIGHTNESS_CH, n, FULL_BRIGHTNESS)

    def ring(self, name, v):
        """Set a knob's ring to v (0..1)."""
        if self.out is None:
            return
        n = next((k for k, x in KNOBS.items() if x == name), None)
        if n is not None:
            self._send(ENCODER_CH, n, int(round(max(0, min(1, v)) * 127)))

    def close(self):
        for p in (self.inp, self.out):
            if p is not None:
                try:
                    p.close()
                except Exception:
                    pass
        self.inp = self.out = None
