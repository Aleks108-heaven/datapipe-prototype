"""Build the Windows installer: python packaging/build_installer.py        Output: dist/datapipe-setup-<version>.exe

Needs Inno Setup 6 (winget install JRSoftware.InnoSetup) and the standalone program, which this builds first when
dist/datapipe.exe is missing (or when --rebuild is given): python -m pip install . pyinstaller"""
import os
import re
import shutil
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent


def version():
    text = (ROOT / "datapipe" / "__init__.py").read_text(encoding="utf-8")
    m = re.search(r'^__version__\s*=\s*"(\d+\.\d+\.\d+)"', text, re.M)
    if not m:
        sys.exit('datapipe/__init__.py has no __version__ = "x.y.z"')
    return m.group(1)


def find_iscc():
    found = shutil.which("ISCC") or shutil.which("iscc")
    if found:
        return found
    roots = [os.environ.get("ProgramFiles(x86)"), os.environ.get("ProgramFiles"), os.environ.get("LOCALAPPDATA")]
    for root in filter(None, roots):
        for sub in ("Inno Setup 6", str(Path("Programs") / "Inno Setup 6")):
            candidate = Path(root) / sub / "ISCC.exe"
            if candidate.is_file():
                return str(candidate)
    return None


def main():
    if sys.platform != "win32":
        sys.exit("the installer is for Windows; build it on a Windows computer")
    iscc = find_iscc()
    if not iscc:
        sys.exit("Inno Setup 6 was not found. Install it:  winget install JRSoftware.InnoSetup")
    exe = ROOT / "dist" / "datapipe.exe"
    if "--rebuild" in sys.argv or not exe.is_file():
        code = subprocess.call([sys.executable, str(ROOT / "packaging" / "build.py")], cwd=ROOT)
        if code:
            return code
    ver = version()
    code = subprocess.call([iscc, f"/DAppVersion={ver}", str(ROOT / "packaging" / "datapipe.iss")], cwd=ROOT)
    if code == 0:
        print(f"\nInstaller: {ROOT / 'dist' / f'datapipe-setup-{ver}.exe'}")
    return code


if __name__ == "__main__":
    sys.exit(main())
