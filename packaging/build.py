"""Build the standalone program for THIS operating system (no cross-compiling): python packaging/build.py

Needs:  python -m pip install . pyinstaller     Output: dist/datapipe (dist/datapipe.exe on Windows).
The example schemas and metrics files are bundled so the program has something to start from."""
import os
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent


def main():
    sep = os.pathsep                      # PyInstaller wants SRC<sep>DEST, and the separator differs between Windows and Unix
    cmd = [sys.executable, "-m", "PyInstaller", "--noconfirm", "--clean", "--onefile", "--name", "datapipe",
           "--distpath", str(ROOT / "dist"), "--workpath", str(ROOT / "build" / "pyinstaller"), "--specpath", str(ROOT / "build"),
           "--collect-all", "duckdb"]
    for f in sorted((ROOT / "examples").glob("*.json")):
        cmd += ["--add-data", f"{f}{sep}examples"]
    cmd.append(str(ROOT / "packaging" / "datapipe_entry.py"))
    print(" ".join(cmd))
    return subprocess.call(cmd, cwd=ROOT)


if __name__ == "__main__":
    sys.exit(main())
