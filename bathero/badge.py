"""The LIVE badge: a small floating window, top right of every screen, over everything.

Owned by the stream daemon (twitch.daemon): it exists exactly while the stream process does,
so seeing it means being live, whether the game is open or not. Cocoa through PyObjC:
a borderless transparent window at the top of the window levels (over a fullscreen Space
too: FullScreenAuxiliary), on every Space (CanJoinAllSpaces) and one per display, stationary
in Mission Control, no Dock icon (accessory activation policy), never takes the focus. A red
pill "LIVE mm:ss" with a blinking dot (amber STARTING until ffmpeg reports, dim STOPPING,
the miss colour with the reason after an unrequested end) and an x that calls `on_close`
(the daemon stops the stream). Under it, when the daemon gives one, the audio row: a level
meter of what goes out (peak, after the gain), a gain fader and its dB (the daemon writes
GAIN_PATH, the feeder applies it within half a second). Also captured by the stream: viewers
see it too.

    python -m bathero.badge --demo     # shows one for 10 s, cycling the states
"""
import sys
import time

import objc
from AppKit import (NSApplication, NSApplicationActivationPolicyAccessory, NSBackingStoreBuffered, NSBezierPath, NSColor,
                    NSControlSizeSmall, NSFont, NSFontWeightBold, NSMakeRect, NSScreen, NSSlider, NSView, NSWindow,
                    NSWindowCollectionBehaviorCanJoinAllSpaces,
                    NSWindowCollectionBehaviorFullScreenAuxiliary, NSWindowCollectionBehaviorIgnoresCycle,
                    NSWindowCollectionBehaviorStationary, NSWindowStyleMaskBorderless, NSFontAttributeName,
                    NSForegroundColorAttributeName, NSAttributedString)
from Foundation import NSObject, NSTimer, NSRunLoop, NSRunLoopCommonModes

H = 26                 # the pill's height in points
ROW2 = 22              # the audio row under it (meter, fader, dB), when there is one
METER_W, DB_W = 64, 58
MIN_W = 300            # the window is at least this wide when it has the audio row (room for the fader)
MAX_LEVEL = 2147483631   # kCGMaximumWindowLevel: over a fullscreen Space's shielding level too
MARGIN = 8             # from the screen's top right corner
TICK_S = 0.5           # the daemon's callback and the redraw run this often
COLORS = {             # state -> (text, fill)
    "live": ((1.0, 1.0, 1.0), (0.78, 0.12, 0.16)),
    "starting": ((0.08, 0.08, 0.1), (0.92, 0.67, 0.16)),
    "stopping": ((0.86, 0.86, 0.86), (0.27, 0.27, 0.31)),
    "ended": ((0.95, 0.35, 0.35), (0.09, 0.09, 0.11)),
}


def _rgb(c, a=1.0):
    return NSColor.colorWithSRGBRed_green_blue_alpha_(c[0], c[1], c[2], a)


class BadgeView(NSView):
    """The pill and the x, drawn by hand; a click on the x calls the badge's on_close."""

    def initWithFrame_(self, frame):
        self = objc.super(BadgeView, self).initWithFrame_(frame)
        if self is None:
            return None
        self.state = "starting"
        self.label = ""
        self.warn = ""
        self.hot = False
        self.badge = None
        self.row2 = 0              # ROW2 when the audio row is shown
        self.level = (-99.0, -99.0)
        self.gain_db = 0.0
        return self

    def isFlipped(self):
        return False

    def text_width(self, s, font):
        return NSAttributedString.alloc().initWithString_attributes_(s, {NSFontAttributeName: font}).size().width if s else 0

    def font(self):
        return NSFont.monospacedSystemFontOfSize_weight_(13, NSFontWeightBold)

    def layout_width(self):
        """The window's width for the current text: the pill, a gap, the x square."""
        f = self.font()
        w = 24 + self.text_width(self.label, f) + (self.text_width(self.warn, f) + 6 if self.warn else 0) + 10 + 6 + H
        return max(w, MIN_W) if self.row2 else w

    def drawRect_(self, rect):
        f = self.font()
        text_c, fill_c = COLORS[self.state]
        w = self.layout_width()
        pill_w = w - 6 - H
        y0 = self.row2
        if self.row2:
            self.draw_audio_row(w)
        pill = NSBezierPath.bezierPathWithRoundedRect_xRadius_yRadius_(NSMakeRect(0, y0, pill_w, H), H / 2, H / 2)
        _rgb(fill_c).setFill()
        pill.fill()
        if self.state == "ended":
            _rgb(text_c).setStroke()
            pill.setLineWidth_(1)
            pill.stroke()
        blink = self.state != "live" or int(time.time() * 2) % 2 == 0
        _rgb(text_c if blink else fill_c).setFill()
        NSBezierPath.bezierPathWithOvalInRect_(NSMakeRect(8, y0 + H / 2 - 5, 10, 10)).fill()
        attrs = {NSFontAttributeName: f, NSForegroundColorAttributeName: _rgb(text_c)}
        x = 24
        NSAttributedString.alloc().initWithString_attributes_(self.label, attrs).drawAtPoint_((x, y0 + (H - 16) / 2))
        if self.warn:
            x += self.text_width(self.label, f) + 6
            wattrs = {NSFontAttributeName: f, NSForegroundColorAttributeName: _rgb((1.0, 0.88, 0.47))}
            NSAttributedString.alloc().initWithString_attributes_(self.warn, wattrs).drawAtPoint_((x, y0 + (H - 16) / 2))
        # the x
        bx = NSMakeRect(pill_w + 6, y0, H, H)
        box = NSBezierPath.bezierPathWithRoundedRect_xRadius_yRadius_(bx, 6, 6)
        _rgb((0.78, 0.12, 0.16) if self.hot else (0.09, 0.09, 0.11)).setFill()
        box.fill()
        _rgb((1, 1, 1) if self.hot else (0.47, 0.47, 0.51)).setStroke()
        box.setLineWidth_(1)
        box.stroke()
        xattrs = {NSFontAttributeName: NSFont.systemFontOfSize_(18), NSForegroundColorAttributeName: _rgb((1, 1, 1) if self.hot else (0.86, 0.86, 0.86))}
        xs = NSAttributedString.alloc().initWithString_attributes_("×", xattrs)
        sz = xs.size()
        xs.drawAtPoint_((bx.origin.x + (H - sz.width) / 2, y0 + (H - sz.height) / 2 + 1))

    def draw_audio_row(self, w):
        """Left: the peak meter (-60..0 dB, green, amber over -12, red over -3). Right: the gain in
        dB. The fader between them is an NSSlider, the badge places it."""
        h = ROW2 - 6
        back = NSBezierPath.bezierPathWithRoundedRect_xRadius_yRadius_(NSMakeRect(0, 0, w, ROW2), 6, 6)
        _rgb((0.09, 0.09, 0.11), 0.92).setFill()
        back.fill()
        peak = max(-60.0, min(0.0, self.level[0]))
        frac = (peak + 60) / 60
        _rgb((0.16, 0.16, 0.2)).setFill()
        NSBezierPath.bezierPathWithRoundedRect_xRadius_yRadius_(NSMakeRect(6, 3, METER_W, h), 3, 3).fill()
        if frac > 0:
            colour = (0.85, 0.2, 0.2) if peak > -3 else (0.92, 0.67, 0.16) if peak > -12 else (0.3, 0.75, 0.35)
            _rgb(colour).setFill()
            NSBezierPath.bezierPathWithRoundedRect_xRadius_yRadius_(NSMakeRect(6, 3, METER_W * frac, h), 3, 3).fill()
        f = NSFont.monospacedSystemFontOfSize_weight_(11, NSFontWeightBold)
        attrs = {NSFontAttributeName: f, NSForegroundColorAttributeName: _rgb((0.86, 0.86, 0.86))}
        text = f"{self.gain_db:+.0f} dB"
        ts = NSAttributedString.alloc().initWithString_attributes_(text, attrs)
        ts.drawAtPoint_((w - 6 - ts.size().width, (ROW2 - ts.size().height) / 2 + 1))

    def x_rect(self):
        return NSMakeRect(self.layout_width() - H, self.row2, H, H)

    def mouseDown_(self, event):
        p = self.convertPoint_fromView_(event.locationInWindow(), None)
        r = self.x_rect()
        if r.origin.x <= p.x <= r.origin.x + r.size.width and r.origin.y <= p.y <= r.origin.y + H and self.badge is not None:
            self.badge.on_close()

    def mouseMoved_(self, event):
        p = self.convertPoint_fromView_(event.locationInWindow(), None)
        r = self.x_rect()
        hot = r.origin.x <= p.x <= r.origin.x + r.size.width and r.origin.y <= p.y <= r.origin.y + H
        if hot != self.hot:
            self.hot = hot
            self.setNeedsDisplay_(True)

    def mouseExited_(self, event):
        if self.hot:
            self.hot = False
            self.setNeedsDisplay_(True)

    def acceptsFirstMouse_(self, event):
        return True


class Ticker(NSObject):
    def initWithBadge_(self, badge):
        self = objc.super(Ticker, self).init()
        self.badge = badge
        return self

    def tick_(self, timer):
        self.badge._tick()

    def gain_(self, slider):
        self.badge._gain_moved(slider.doubleValue())


class Badge:
    """`Badge(on_tick, on_close).run()` takes over the main thread: `on_tick()` runs every
    TICK_S and returns (state, label, warn) or None to quit; `on_close()` runs when the x is
    clicked. `set(state, label, warn)` from on_tick is what the windows show."""

    def __init__(self, on_tick, on_close, gain=None, level=None):
        """gain: (initial dB, (min, max), on_change(db)) adds the audio row; level: () -> (peak dB,
        rms dB) feeds its meter."""
        self.on_tick = on_tick
        self.on_close = on_close
        self.gain = gain
        self.level = level
        self.gain_db = gain[0] if gain else 0.0
        self.ticker = Ticker.alloc().initWithBadge_(self)
        self.app = NSApplication.sharedApplication()
        self.app.setActivationPolicy_(NSApplicationActivationPolicyAccessory)
        self.windows = {}          # screen frame (as a tuple) -> (window, view, slider or None)
        self.state, self.label, self.warn = "starting", "STARTING 00:00", ""
        self._screens_at = 0

    def _window(self, screen):
        frame = screen.visibleFrame()
        w = NSWindow.alloc().initWithContentRect_styleMask_backing_defer_(NSMakeRect(0, 0, 200, H), NSWindowStyleMaskBorderless,
                                                                          NSBackingStoreBuffered, False)
        w.setLevel_(MAX_LEVEL)
        w.setOpaque_(False)
        w.setBackgroundColor_(NSColor.clearColor())
        w.setHasShadow_(False)
        w.setIgnoresMouseEvents_(False)
        w.setCollectionBehavior_(NSWindowCollectionBehaviorCanJoinAllSpaces | NSWindowCollectionBehaviorFullScreenAuxiliary
                                 | NSWindowCollectionBehaviorStationary | NSWindowCollectionBehaviorIgnoresCycle)
        w.setAcceptsMouseMovedEvents_(True)
        view = BadgeView.alloc().initWithFrame_(NSMakeRect(0, 0, 200, self.height))
        view.badge = self
        view.row2 = ROW2 if self.gain else 0
        view.gain_db = self.gain_db
        w.setContentView_(view)
        slider = None
        if self.gain:
            slider = NSSlider.alloc().initWithFrame_(NSMakeRect(METER_W + 12, 1, 100, ROW2 - 2))
            slider.setMinValue_(self.gain[1][0]); slider.setMaxValue_(self.gain[1][1])
            slider.setDoubleValue_(self.gain_db)
            slider.setControlSize_(NSControlSizeSmall)
            slider.setContinuous_(True)
            slider.setTarget_(self.ticker); slider.setAction_("gain:")
            view.addSubview_(slider)
        w.orderFrontRegardless()
        return w, view, slider

    @property
    def height(self):
        return H + (ROW2 if self.gain else 0)

    def _place(self):
        """One window per screen, top right of its visible area (under the menu bar when it is
        there, at the very top when a fullscreen Space hides it); screens come and go."""
        screens = list(NSScreen.screens())
        keys = []
        for s in screens:
            vf = s.visibleFrame()
            key = (vf.origin.x, vf.origin.y, vf.size.width, vf.size.height)
            keys.append(key)
            if key not in self.windows:
                self.windows[key] = self._window(s)
            w, view, slider = self.windows[key]
            width, height = view.layout_width(), self.height
            w.setFrame_display_(NSMakeRect(vf.origin.x + vf.size.width - width - MARGIN, vf.origin.y + vf.size.height - height - MARGIN, width, height), True)
            view.setFrame_(NSMakeRect(0, 0, width, height))
            if slider is not None:
                slider.setFrame_(NSMakeRect(METER_W + 12, 1, width - METER_W - DB_W - 18, ROW2 - 2))
        for key in list(self.windows):
            if key not in keys:
                self.windows.pop(key)[0].orderOut_(None)

    def set(self, state, label, warn=""):
        self.state, self.label, self.warn = state, label, warn
        level = self.level() if self.level else (-99.0, -99.0)
        for w, view, slider in self.windows.values():
            view.state, view.label, view.warn = state, label, warn
            view.level, view.gain_db = level, self.gain_db
            view.setNeedsDisplay_(True)

    def _gain_moved(self, db):
        db = round(db)                      # whole dB: enough, and the file does not churn while dragging
        if db == self.gain_db:
            return
        self.gain_db = db
        for w, view, slider in self.windows.values():
            if slider is not None and slider.doubleValue() != db:
                slider.setDoubleValue_(db)
            view.gain_db = db
            view.setNeedsDisplay_(True)
        self.gain[2](db)

    def _tick(self):
        r = self.on_tick()
        if r is None:
            self.app.terminate_(None)
            return
        self.set(*r)
        self._place()

    def run(self):
        self._place()
        timer = NSTimer.timerWithTimeInterval_target_selector_userInfo_repeats_(TICK_S, self.ticker, "tick:", None, True)
        NSRunLoop.mainRunLoop().addTimer_forMode_(timer, NSRunLoopCommonModes)
        self.app.run()


def demo(seconds=10):
    t0 = time.time()
    states = [("starting", "STARTING", ""), ("live", "LIVE", ""), ("live", "LIVE", "0.82x  dropped 17"), ("stopping", "STOPPING", ""),
              ("ended", "STREAM ENDED · ffmpeg exited (1)", "")]

    def tick():
        s = time.time() - t0
        if s > seconds:
            return None
        st, label, warn = states[int(s / 2) % len(states)]
        return st, (label if st == "ended" else f"{label} {int(s) // 60:02d}:{int(s) % 60:02d}"), warn

    import math
    Badge(tick, lambda: print("x clicked"), gain=(0.0, (-12.0, 30.0), lambda db: print(f"gain {db:+.0f} dB")),
          level=lambda: (-30 + 28 * abs(math.sin(time.time() * 1.3)), -40.0)).run()


if __name__ == "__main__":
    if "--demo" in sys.argv:
        demo(float(sys.argv[sys.argv.index("--demo") + 1]) if sys.argv[-1] != "--demo" else 10)
    else:
        print(__doc__)
