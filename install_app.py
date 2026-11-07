#!/usr/bin/env python3
"""Build bathero.app so macOS (Dock, Spotlight, rcmd, app switchers) sees bathero
as an installed application with its own name and icon.

    .venv/bin/python install_app.py                 # -> /Applications/bathero.app
    .venv/bin/python install_app.py ~/Applications  # -> somewhere else

How it works: the bundle's executable is a copy of the venv's base Python binary
(so the process belongs to this bundle, not to Python.app), and Contents/pyvenv.cfg
turns the bundle into a minimal venv whose only site-packages entry is a .pth file
that puts this repo and its .venv on sys.path and launches the game at startup.
Nothing is copied but the interpreter: run this again after moving the repo,
recreating .venv, or changing the icon. Logs go to ~/Library/Logs/bathero.log.
"""
import os
import plistlib
import shutil
import subprocess
import sys

REPO = os.path.dirname(os.path.abspath(__file__))
VENV = os.path.join(REPO, ".venv")
NAME = "bathero"
BUNDLE_ID = "com.santi.bathero"
VERSION = "0.1"
LSREGISTER = "/System/Library/Frameworks/CoreServices.framework/Frameworks/LaunchServices.framework/Support/lsregister"

LAUNCHER = '''"""Started by bathero.pth while the bundle's Python initialises: runs the game and exits."""
import os
import sys
import traceback

REPO = {repo!r}


class _Stamped:
    """The log file with a clock on every line (a take's timeline has to be reconstructible)."""

    def __init__(self, f):
        self.f, self.at_bol = f, True

    def write(self, text):
        import time
        out = []
        for piece in text.splitlines(True):
            if self.at_bol and piece.strip():
                out.append(time.strftime("%H:%M:%S ") + piece)
            else:
                out.append(piece)
            self.at_bol = piece.endswith("\\n")
        self.f.write("".join(out))

    def flush(self):
        self.f.flush()

    def fileno(self):
        return self.f.fileno()


def _run():
    log = os.path.expanduser("~/Library/Logs/bathero.log")
    os.makedirs(os.path.dirname(log), exist_ok=True)
    f = open(log, "a", buffering=1)
    sys.stdout = sys.stderr = _Stamped(f)
    if not hasattr(sys, "argv"):
        sys.argv = ["bathero"]
    os.chdir(REPO)
    code = 0
    try:
        from bathero.app import main
        main([])
    except SystemExit as e:
        code = e.code if isinstance(e.code, int) else 0
    except Exception:
        traceback.print_exc()
        code = 1
    finally:
        f.flush()
        os._exit(code)


_run()
'''


def read_pyvenv():
    cfg = {}
    with open(os.path.join(VENV, "pyvenv.cfg")) as f:
        for line in f:
            if "=" in line:
                k, v = line.split("=", 1)
                cfg[k.strip()] = v.strip()
    return cfg


def base_python(cfg):
    home = cfg["home"]
    major_minor = ".".join(cfg["version"].split(".")[:2])
    for name in (f"python{major_minor}", "python3", "python"):
        p = os.path.realpath(os.path.join(home, name))
        if os.path.isfile(p):
            return p, major_minor
    sys.exit(f"no python binary found in {home}")


def make_icon(icns_path, tmp_dir):
    """A dark rounded tile with the four drum colours and the hit line, rendered with pygame."""
    os.environ.setdefault("SDL_VIDEODRIVER", "dummy")
    os.environ.setdefault("SDL_AUDIODRIVER", "dummy")   # drawing only: never open an audio device here
    import pygame
    pygame.display.init()
    size = 1024
    surf = pygame.Surface((size, size), pygame.SRCALPHA)
    pygame.draw.rect(surf, (14, 14, 18), (0, 0, size, size), border_radius=int(size * 0.22))
    colors = [(245, 90, 90), (250, 170, 60), (245, 230, 80), (110, 220, 110)]
    for i, c in enumerate(colors):
        x = int(size * (0.2 + 0.2 * i))
        pygame.draw.rect(surf, (28, 28, 36), (x - 80, 130, 160, 640), border_radius=40)
        pygame.draw.rect(surf, c, (x - 62, 300 - 40 * i, 124, 44), border_radius=22)
        pygame.draw.rect(surf, c, (x - 62, 520 + 30 * i, 124, 44), border_radius=22)
    pygame.draw.line(surf, (235, 235, 235), (110, 780), (size - 110, 780), 14)
    for i, c in enumerate(colors):
        x = int(size * (0.2 + 0.2 * i))
        pygame.draw.circle(surf, c, (x, 780), 42)
    iconset = os.path.join(tmp_dir, "bathero.iconset")
    shutil.rmtree(iconset, ignore_errors=True)
    os.makedirs(iconset)
    png = os.path.join(tmp_dir, "icon_1024.png")
    pygame.image.save(surf, png)
    for px in (16, 32, 64, 128, 256, 512):
        for scale in (1, 2):
            n = px * scale
            out = os.path.join(iconset, f"icon_{px}x{px}{'@2x' if scale == 2 else ''}.png")
            subprocess.run(["sips", "-z", str(n), str(n), png, "--out", out], check=True, capture_output=True)
    subprocess.run(["iconutil", "-c", "icns", iconset, "-o", icns_path], check=True)
    pygame.quit()


def build(dest_dir):
    cfg = read_pyvenv()
    py, mm = base_python(cfg)
    app = os.path.join(dest_dir, f"{NAME}.app")
    contents = os.path.join(app, "Contents")
    macos = os.path.join(contents, "MacOS")
    resources = os.path.join(contents, "Resources")
    site = os.path.join(contents, "lib", f"python{mm}", "site-packages")
    venv_site = os.path.join(VENV, "lib", f"python{mm}", "site-packages")
    if not os.path.isdir(venv_site):
        sys.exit(f"missing {venv_site}; create the venv first")

    was_running = subprocess.run(["pgrep", "-f", f"{NAME}.app/Contents/MacOS/{NAME}"], capture_output=True).returncode == 0
    if was_running:
        sys.exit(f"{NAME} is running; quit it first")
    shutil.rmtree(app, ignore_errors=True)
    for d in (macos, resources, site):
        os.makedirs(d)

    exe = os.path.join(macos, NAME)
    shutil.copy2(py, exe)
    os.chmod(exe, 0o755)

    with open(os.path.join(contents, "pyvenv.cfg"), "w") as f:
        f.write(f"home = {cfg['home']}\ninclude-system-site-packages = false\nversion = {cfg['version']}\n")
    with open(os.path.join(site, f"{NAME}.pth"), "w") as f:
        f.write(f"{venv_site}\n{REPO}\nimport {NAME}_launch\n")
    with open(os.path.join(site, f"{NAME}_launch.py"), "w") as f:
        f.write(LAUNCHER.format(repo=REPO))

    make_icon(os.path.join(resources, f"{NAME}.icns"), os.path.join(dest_dir if os.access(dest_dir, os.W_OK) else "/tmp", f".{NAME}-build"))
    shutil.rmtree(os.path.join(dest_dir, f".{NAME}-build"), ignore_errors=True)
    shutil.rmtree(f"/tmp/.{NAME}-build", ignore_errors=True)

    info = {
        "CFBundleName": NAME,
        "CFBundleDisplayName": NAME,
        "CFBundleIdentifier": BUNDLE_ID,
        "CFBundleExecutable": NAME,
        "CFBundleIconFile": f"{NAME}.icns",
        "CFBundlePackageType": "APPL",
        "CFBundleShortVersionString": VERSION,
        "CFBundleVersion": VERSION,
        "CFBundleInfoDictionaryVersion": "6.0",
        # without these macOS denies camera and microphone access silently (no prompt): the
        # take's camera (ffmpeg) and its audio input (sounddevice) run as this app's children
        "NSCameraUsageDescription": "bathero records the camera picture-in-picture in your takes.",
        "NSMicrophoneUsageDescription": "bathero records the audio interface's mix in your takes and meters it in the camera check.",
        "LSMinimumSystemVersion": "12.0",
        "NSHighResolutionCapable": True,
        "LSApplicationCategoryType": "public.app-category.music",
        "NSHumanReadableCopyright": "bathero",
    }
    with open(os.path.join(contents, "Info.plist"), "wb") as f:
        plistlib.dump(info, f)
    subprocess.run(["codesign", "--force", "--sign", "-", "--deep", app], check=False, capture_output=True)
    subprocess.run([LSREGISTER, "-f", app], check=False)
    return app


def main():
    dest = os.path.expanduser(sys.argv[1]) if len(sys.argv) > 1 else "/Applications"
    if not os.access(dest, os.W_OK):
        sys.exit(f"{dest} is not writable; try ~/Applications")
    app = build(dest)
    print(f"built {app}")
    print("open it from Finder / Spotlight / rcmd, or: open -a bathero")


if __name__ == "__main__":
    main()
