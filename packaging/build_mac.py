"""Build the Mac app and its disk image (on a Mac):  python packaging/build_mac.py        Output: dist/datapipe-macos-<arm64 or x86_64>.dmg

An Apple silicon Mac builds an Apple silicon app, an Intel Mac an Intel one (the engine inside is compiled for one or the other).
Needs:  python -m pip install . pyinstaller

The app is signed "ad hoc" (no certificate): macOS requires some signature on Apple silicon, and this one keeps the bundle's parts from
being changed unnoticed, but it does not say WHO made the app. macOS therefore asks once before the first start (see the note inside the
disk image). A signature from an Apple Developer ID plus Apple's notarisation is what removes that question; it costs money and is not set up."""
import os
import platform
import shutil
import subprocess
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from _release import ROOT, mac_arch, patch_info_plist, version      # noqa: E402

NOTE = """How to open datapipe the first time
====================================

1. Drag datapipe into the Applications folder (the arrow in this window).
2. Open it from Applications. macOS says it cannot check the app for malware, because datapipe is not signed by a registered
   developer. That is expected: datapipe is open source, and everything it does stays on this computer.
3. Open  System Settings > Privacy & Security, scroll down to the message about "datapipe" and press  Open Anyway,
   then confirm with your password. macOS asks this once.

If you prefer Terminal, this does the same:   xattr -dr com.apple.quarantine /Applications/datapipe.app

Using it: datapipe opens in your web browser. Your data files go in  ~/datapipe/files  (a folder called datapipe in your home folder).
Results are written to  ~/datapipe/work . Close the browser tab and datapipe stops by itself a few seconds later; or press
Stop datapipe on the Settings page. To check this download: shasum -a 256 datapipe-macos-*.dmg  and compare with SHA256SUMS.txt.
"""


def run(cmd, **kw):
    print("+", " ".join(str(c) for c in cmd), flush=True)
    subprocess.check_call([str(c) for c in cmd], **kw)


def run_again_if_busy(cmd, attempts=6):
    """hdiutil sometimes answers "Resource busy" right after files were written (Spotlight or a virus scan is still looking at them); a few
    seconds later the same command works."""
    for attempt in range(1, attempts + 1):
        try:
            return run(cmd)
        except subprocess.CalledProcessError:
            if attempt == attempts:
                raise
            print(f"hdiutil failed (attempt {attempt} of {attempts}); trying again in {5 * attempt} seconds", flush=True)
            subprocess.call(["sync"])
            time.sleep(5 * attempt)


def main():
    if sys.platform != "darwin":
        sys.exit("the Mac app can only be built on a Mac")
    ver, arch = version(), mac_arch(platform.machine())
    app = ROOT / "dist" / "mac" / "datapipe.app"
    if "--rebuild" in sys.argv or not app.is_dir():
        run([sys.executable, ROOT / "packaging" / "build.py", "--mac-app"], cwd=ROOT)
    patch_info_plist(app / "Contents" / "Info.plist", ver)
    run(["codesign", "--force", "--deep", "--sign", "-", "--timestamp=none", app])
    run(["codesign", "--verify", "--deep", "--strict", "--verbose=2", app])

    staging = ROOT / "dist" / "dmg-staging"
    if staging.exists():
        shutil.rmtree(staging)
    staging.mkdir(parents=True)
    run(["ditto", app, staging / "datapipe.app"])                      # ditto keeps the bundle's links and attributes intact
    os.symlink("/Applications", staging / "Applications")
    (staging / "How to open datapipe the first time.txt").write_text(NOTE, encoding="utf-8")
    dmg = ROOT / "dist" / f"datapipe-macos-{arch}.dmg"
    if dmg.exists():
        dmg.unlink()
    run_again_if_busy(["hdiutil", "create", "-volname", "datapipe", "-srcfolder", staging, "-ov", "-format", "UDZO", "-fs", "HFS+", dmg])
    run_again_if_busy(["hdiutil", "verify", dmg])
    print(f"\nDisk image: {dmg}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
