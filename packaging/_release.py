"""What the build scripts share. Nothing here needs a particular operating system, so the tests can run it anywhere."""
import plistlib
import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
BUNDLE_ID = "io.github.aleks108-heaven.datapipe"        # the Mac app's identity (reverse domain); changing it makes macOS treat a new build as a different app
PUBLISHER = "datapipe-prototype"
DESCRIPTION = "datapipe - clean and check data files on this computer"


def version():
    text = (ROOT / "datapipe" / "__init__.py").read_text(encoding="utf-8")
    m = re.search(r'^__version__\s*=\s*"(\d+\.\d+\.\d+)"', text, re.M)
    if not m:
        sys.exit('datapipe/__init__.py has no __version__ = "x.y.z"')
    return m.group(1)


def windows_version_info(ver):
    """The text of a PyInstaller version file: the Company, Description and Version that Explorer's Details tab and Defender show for the .exe."""
    numbers = tuple(int(p) for p in ver.split(".")) + (0,)
    tup = ", ".join(str(n) for n in numbers[:4])
    return f"""VSVersionInfo(
  ffi=FixedFileInfo(filevers=({tup}), prodvers=({tup}), mask=0x3f, flags=0x0, OS=0x40004, fileType=0x1, subtype=0x0, date=(0, 0)),
  kids=[
    StringFileInfo([StringTable('040904B0', [
      StringStruct('CompanyName', '{PUBLISHER}'),
      StringStruct('FileDescription', '{DESCRIPTION}'),
      StringStruct('FileVersion', '{ver}.0'),
      StringStruct('InternalName', 'datapipe'),
      StringStruct('OriginalFilename', 'datapipe.exe'),
      StringStruct('ProductName', 'datapipe'),
      StringStruct('ProductVersion', '{ver}')])]),
    VarFileInfo([VarStruct('Translation', [1033, 1200])])
  ])
"""


def patch_info_plist(path, ver):
    """Give the Mac app its name, version and identity (PyInstaller writes placeholders), in place."""
    with open(path, "rb") as fh:
        info = plistlib.load(fh)
    info.update({
        "CFBundleName": "datapipe", "CFBundleDisplayName": "datapipe", "CFBundleIdentifier": BUNDLE_ID,
        "CFBundleShortVersionString": ver, "CFBundleVersion": ver,
        "NSHumanReadableCopyright": PUBLISHER, "LSApplicationCategoryType": "public.app-category.utilities",
        "NSHighResolutionCapable": True,
    })
    with open(path, "wb") as fh:
        plistlib.dump(info, fh)
    return info


def mac_arch(machine):
    """The word in the .dmg's file name: Apple silicon reports arm64, Intel reports x86_64."""
    machine = machine.lower()
    return "arm64" if machine in ("arm64", "aarch64") else "x86_64" if machine in ("x86_64", "amd64") else machine
