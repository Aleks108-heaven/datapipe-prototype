"""Build the standalone program for THIS operating system (no cross-compiling).

    python packaging/build.py             one file: dist/datapipe  (dist/datapipe.exe on Windows). It unpacks itself on every start.
    python packaging/build.py --onedir    a folder: dist/onedir/datapipe/  Starts faster and unpacks nothing: this is what the Windows installer installs.
    python packaging/build.py --mac-app   macOS only: dist/mac/datapipe.app  (python packaging/build_mac.py also signs it and makes the .dmg)

Needs:  python -m pip install . pyinstaller       The example schemas and metrics files are bundled so the program has something to start from."""
import os
import subprocess
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from _release import BUNDLE_ID, ROOT, version, windows_version_info      # noqa: E402

ICONS = ROOT / "packaging" / "icons"


def command(mode):
    """The PyInstaller command line for one layout: 'onefile', 'onedir' or 'mac-app'."""
    sep = os.pathsep                      # PyInstaller wants SRC<sep>DEST, and the separator differs between Windows and Unix
    folder = {"onefile": "dist", "onedir": "dist/onedir", "mac-app": "dist/mac"}[mode]
    cmd = [sys.executable, "-m", "PyInstaller", "--noconfirm", "--clean", "--name", "datapipe",
           "--distpath", str(ROOT / folder), "--workpath", str(ROOT / "build" / f"pyinstaller-{mode}"), "--specpath", str(ROOT / "build" / mode),
           "--collect-all", "duckdb"]
    cmd += ["--onefile"] if mode == "onefile" else ["--onedir"]
    if mode == "mac-app":
        # tkinter is loaded by name (datapipe.cli._serve_in_mac_app), so PyInstaller cannot see it: it is named here, and only for the Mac app
        # (Tk is what answers macOS: Quit, and "opened again"); the Windows and Linux programs do not carry it.
        cmd += ["--windowed", "--osx-bundle-identifier", BUNDLE_ID, "--icon", str(ICONS / "datapipe.icns"), "--hidden-import", "tkinter"]
    elif sys.platform == "win32":
        info = ROOT / "build" / f"version-info-{mode}.txt"
        info.parent.mkdir(parents=True, exist_ok=True)
        info.write_text(windows_version_info(version()), encoding="utf-8")
        cmd += ["--icon", str(ICONS / "datapipe.ico"), "--version-file", str(info)]
    for f in sorted((ROOT / "examples").glob("*.json")):
        cmd += ["--add-data", f"{f}{sep}examples"]
    cmd.append(str(ROOT / "packaging" / "datapipe_entry.py"))
    return cmd


def main(argv=None):
    argv = sys.argv[1:] if argv is None else argv
    mode = "mac-app" if "--mac-app" in argv else "onedir" if "--onedir" in argv else "onefile"
    if mode == "mac-app" and sys.platform != "darwin":
        sys.exit("the Mac app can only be built on a Mac")
    cmd = command(mode)
    print(" ".join(cmd))
    return subprocess.call(cmd, cwd=ROOT)


if __name__ == "__main__":
    sys.exit(main())
