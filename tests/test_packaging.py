"""The parts of the release build that do not need Windows or a Mac: the version resource, the Mac app's Info.plist, the file names, the PyInstaller
command lines, the icon files. (The installer and the disk image themselves are built and tested by the build workflow on real systems.)"""
import plistlib
import re
import struct
import sys
import zlib
from pathlib import Path

import pytest

import datapipe

PACKAGING = Path(__file__).resolve().parent.parent / "packaging"
sys.path.insert(0, str(PACKAGING))
import _release           # noqa: E402
import build              # noqa: E402


def test_the_version_comes_from_the_package_and_is_three_numbers():
    assert _release.version() == datapipe.__version__ and re.fullmatch(r"\d+\.\d+\.\d+", _release.version())


def test_the_windows_version_resource_carries_name_publisher_and_version():
    text = _release.windows_version_info("1.22.3")
    assert "filevers=(1, 22, 3, 0)" in text and "prodvers=(1, 22, 3, 0)" in text
    for needed in ("'CompanyName', 'datapipe-prototype'", "'ProductName', 'datapipe'", "'ProductVersion', '1.22.3'", "'FileVersion', '1.22.3.0'",
                   "'OriginalFilename', 'datapipe.exe'"):
        assert needed in text, needed
    assert text.count("(") == text.count(")") and text.count("[") == text.count("]")


def test_pyinstaller_itself_can_read_the_version_resource():
    """PyInstaller evaluates the file with its own classes; a typo here would only show when a Windows build is made."""
    versioninfo = pytest.importorskip("PyInstaller.utils.win32.versioninfo")
    scope = {name: getattr(versioninfo, name) for name in ("VSVersionInfo", "FixedFileInfo", "StringFileInfo", "StringTable", "StringStruct", "VarFileInfo", "VarStruct")}
    info = eval(_release.windows_version_info("0.2.0"), {"__builtins__": {}}, scope)         # noqa: S307 - our own text, with no builtins
    assert str(info).count("ProductName") == 1 and "0.2.0" in str(info)


def test_the_mac_apps_info_plist_is_patched_in_place_and_keeps_what_pyinstaller_wrote(tmp_path):
    path = tmp_path / "Info.plist"
    path.write_bytes(plistlib.dumps({"CFBundleExecutable": "datapipe", "CFBundleName": "placeholder", "CFBundleVersion": "0.0.0", "LSBackgroundOnly": False}))
    info = _release.patch_info_plist(path, "2.5.1")
    again = plistlib.loads(path.read_bytes())
    assert again == info
    assert again["CFBundleExecutable"] == "datapipe" and again["LSBackgroundOnly"] is False                  # untouched
    assert again["CFBundleName"] == "datapipe" and again["CFBundleDisplayName"] == "datapipe"
    assert again["CFBundleShortVersionString"] == again["CFBundleVersion"] == "2.5.1"
    assert again["CFBundleIdentifier"] == _release.BUNDLE_ID and re.fullmatch(r"[A-Za-z0-9.-]+", _release.BUNDLE_ID)


@pytest.mark.parametrize("machine,word", [("arm64", "arm64"), ("aarch64", "arm64"), ("ARM64", "arm64"), ("x86_64", "x86_64"), ("AMD64", "x86_64"), ("ppc", "ppc")])
def test_the_disk_images_name_says_which_kind_of_mac_it_is_for(machine, word):
    assert _release.mac_arch(machine) == word


def test_each_layout_gets_its_own_output_and_work_folders_so_builds_never_overwrite_each_other():
    cmds = {mode: build.command(mode) for mode in ("onefile", "onedir", "mac-app")}
    folders = {mode: Path(c[c.index("--distpath") + 1]).as_posix().split("/")[-2:] for mode, c in cmds.items()}
    assert folders["onefile"][-1] == "dist" and folders["onedir"] == ["dist", "onedir"] and folders["mac-app"] == ["dist", "mac"]
    works = [c[c.index("--workpath") + 1] for c in cmds.values()]
    assert len(set(works)) == 3
    assert "--onefile" in cmds["onefile"] and "--onedir" in cmds["onedir"] and "--onedir" in cmds["mac-app"]
    assert "--windowed" in cmds["mac-app"] and "--windowed" not in cmds["onedir"] and "--windowed" not in cmds["onefile"]       # only the Mac app has no console
    assert cmds["mac-app"][cmds["mac-app"].index("--osx-bundle-identifier") + 1] == _release.BUNDLE_ID
    assert cmds["mac-app"][cmds["mac-app"].index("--icon") + 1].endswith("datapipe.icns")
    for c in cmds.values():
        assert "--collect-all" in c and c[-1].endswith("datapipe_entry.py")
        assert sum(1 for a in c if a == "--add-data") == len(list((PACKAGING.parent / "examples").glob("*.json"))) > 0


@pytest.mark.skipif(sys.platform != "win32", reason="the Windows version resource and icon only apply on Windows")
def test_a_windows_build_carries_the_icon_and_the_version_file():
    c = build.command("onedir")
    assert c[c.index("--icon") + 1].endswith("datapipe.ico")
    version_file = Path(c[c.index("--version-file") + 1])
    assert f"'ProductVersion', '{datapipe.__version__}'" in version_file.read_text(encoding="utf-8")


# ---------------------------------------------------------------- the icon files
def png_size(blob):
    assert blob[:8] == b"\x89PNG\r\n\x1a\n"
    length, kind = struct.unpack(">I4s", blob[8:16])
    assert kind == b"IHDR"
    width, height = struct.unpack(">II", blob[16:24])
    pos = 8
    while pos < len(blob):                                           # every chunk's checksum is right
        n, kind = struct.unpack(">I4s", blob[pos:pos + 8])
        body = blob[pos + 8:pos + 8 + n]
        assert struct.unpack(">I", blob[pos + 8 + n:pos + 12 + n])[0] == zlib.crc32(kind + body) & 0xFFFFFFFF
        pos += 12 + n
    return width, height


def test_the_windows_icon_holds_every_size_windows_asks_for_in_a_valid_container():
    data = (PACKAGING / "icons" / "datapipe.ico").read_bytes()
    reserved, kind, count = struct.unpack("<HHH", data[:6])
    assert (reserved, kind) == (0, 1) and count >= 7
    sizes = []
    for i in range(count):
        w, h, _c, _r, _planes, bpp, length, offset = struct.unpack("<BBBBHHII", data[6 + 16 * i:22 + 16 * i])
        w = w or 256
        sizes.append(w)
        blob = data[offset:offset + length]
        assert len(blob) == length and bpp == 32
        if w >= 128:
            assert png_size(blob) == (w, w)
        else:
            assert struct.unpack("<IiiHH", blob[:16]) == (40, w, w * 2, 1, 32)           # the bitmap header: height counts the picture and its mask
            assert len(blob) == 40 + w * w * 4 + ((w + 31) // 32) * 4 * w
    assert sorted(sizes) == [16, 24, 32, 48, 64, 128, 256]


def test_the_mac_icon_holds_the_sizes_macos_asks_for_in_a_valid_container():
    data = (PACKAGING / "icons" / "datapipe.icns").read_bytes()
    assert data[:4] == b"icns" and struct.unpack(">I", data[4:8])[0] == len(data)
    pos, seen = 8, {}
    while pos < len(data):
        kind, length = struct.unpack(">4sI", data[pos:pos + 8])
        seen[kind] = png_size(data[pos + 8:pos + length])[0]
        pos += length
    assert pos == len(data)
    assert seen[b"ic07"] == 128 and seen[b"ic08"] == 256 and seen[b"ic09"] == 512 and seen[b"ic10"] == 1024 and seen[b"icp4"] == 16
    assert {b"ic11", b"ic12", b"ic13", b"ic14"} <= set(seen)


def test_the_icon_is_not_blank_and_has_rounded_transparent_corners():
    blob = (PACKAGING / "icons" / "datapipe.png").read_bytes()
    assert png_size(blob) == (512, 512)
    raw = zlib.decompress(b"".join(blob[i + 8:i + 8 + n] for i, n, k in _chunks(blob) if k == b"IDAT"))
    rows = [raw[y * (512 * 4 + 1) + 1:(y + 1) * (512 * 4 + 1)] for y in range(512)]
    assert rows[0][3] == 0 and rows[511][-1] == 0                                   # the corner pixel is transparent
    centre = rows[256][256 * 4:256 * 4 + 4]
    assert centre[3] == 255
    assert len({bytes(r[x * 4:x * 4 + 3]) for r in rows[::64] for x in range(0, 512, 32)}) > 3      # more than one colour in it


def _chunks(blob):
    pos = 8
    while pos < len(blob):
        n, kind = struct.unpack(">I4s", blob[pos:pos + 8])
        yield pos, n, kind
        pos += 12 + n
