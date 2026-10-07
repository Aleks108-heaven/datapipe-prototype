"""A native "choose a file" window opened by the program itself (not by the browser).

A browser never reveals the real path of a file, and uploading a multi-gigabyte file to ourselves would copy it for
nothing. So the server asks the operating system for the dialog and gets the real path back. Only the person sitting at
this computer can answer it. No tkinter: the Windows dialog is called directly, macOS and Linux use their own tools.
"""
import ctypes
import shutil
import subprocess
import sys
from pathlib import Path

PATTERNS = ("*.csv", "*.tsv", "*.jsonl", "*.json", "*.sql")
TITLE = "Choose a data, schema or metrics file"


class DialogUnavailable(Exception):
    """No file window can be shown here (no desktop, or no dialog tool installed)."""


def available() -> bool:
    if sys.platform == "win32" or sys.platform == "darwin":
        return True
    return bool(shutil.which("zenity") or shutil.which("kdialog"))


def choose_file():
    """Show the window and return the chosen Path, or None if the person cancelled."""
    if sys.platform == "win32":
        return _windows()
    if sys.platform == "darwin":
        return _run(["osascript", "-e", f'POSIX path of (choose file with prompt "{TITLE}")'])
    if shutil.which("zenity"):
        return _run(["zenity", "--file-selection", f"--title={TITLE}", "--file-filter=Data files | " + " ".join(PATTERNS),
                     "--file-filter=All files | *"])
    if shutil.which("kdialog"):
        return _run(["kdialog", "--title", TITLE, "--getopenfilename", str(Path.home()), " ".join(PATTERNS) + "|Data files"])
    raise DialogUnavailable("no file window is available on this computer; paste the full path instead")


def _run(cmd):
    try:
        done = subprocess.run(cmd, capture_output=True, text=True, timeout=3600)
    except (OSError, subprocess.SubprocessError) as exc:
        raise DialogUnavailable(f"the file window could not be opened ({type(exc).__name__}); paste the full path instead")
    text = done.stdout.strip()
    return Path(text) if done.returncode == 0 and text else None


def _windows():
    from ctypes import wintypes

    class OPENFILENAMEW(ctypes.Structure):
        _fields_ = [("lStructSize", wintypes.DWORD), ("hwndOwner", wintypes.HWND), ("hInstance", wintypes.HINSTANCE),
                    ("lpstrFilter", ctypes.c_void_p), ("lpstrCustomFilter", ctypes.c_void_p), ("nMaxCustFilter", wintypes.DWORD),
                    ("nFilterIndex", wintypes.DWORD), ("lpstrFile", ctypes.c_void_p), ("nMaxFile", wintypes.DWORD),
                    ("lpstrFileTitle", ctypes.c_void_p), ("nMaxFileTitle", wintypes.DWORD), ("lpstrInitialDir", ctypes.c_void_p),
                    ("lpstrTitle", ctypes.c_void_p), ("Flags", wintypes.DWORD), ("nFileOffset", wintypes.WORD),
                    ("nFileExtension", wintypes.WORD), ("lpstrDefExt", ctypes.c_void_p), ("lCustData", wintypes.LPARAM),
                    ("lpfnHook", ctypes.c_void_p), ("lpTemplateName", ctypes.c_void_p), ("pvReserved", ctypes.c_void_p),
                    ("dwReserved", wintypes.DWORD), ("FlagsEx", wintypes.DWORD)]

    OFN_NOCHANGEDIR, OFN_PATHMUSTEXIST, OFN_FILEMUSTEXIST, OFN_EXPLORER = 0x8, 0x800, 0x1000, 0x80000
    user32, comdlg32 = ctypes.windll.user32, ctypes.windll.comdlg32
    user32.GetForegroundWindow.restype = wintypes.HWND
    comdlg32.GetOpenFileNameW.argtypes = [ctypes.POINTER(OPENFILENAMEW)]
    comdlg32.GetOpenFileNameW.restype = wintypes.BOOL
    # Strings are kept in local buffers for the whole call. The filter is a list of NUL-separated pairs, ended by two NULs.
    filt = ctypes.create_unicode_buffer("Data files\0" + ";".join(PATTERNS) + "\0All files\0*.*\0\0", 256)
    title = ctypes.create_unicode_buffer(TITLE)
    buf = ctypes.create_unicode_buffer(32768)                          # long paths
    ofn = OPENFILENAMEW()
    ofn.lStructSize = ctypes.sizeof(OPENFILENAMEW)
    ofn.hwndOwner = user32.GetForegroundWindow()                       # the browser, so the window opens in front of it
    ofn.lpstrFilter = ctypes.cast(filt, ctypes.c_void_p)
    ofn.nFilterIndex = 1
    ofn.lpstrFile = ctypes.cast(buf, ctypes.c_void_p)
    ofn.nMaxFile = len(buf)
    ofn.lpstrTitle = ctypes.cast(title, ctypes.c_void_p)
    ofn.Flags = OFN_EXPLORER | OFN_FILEMUSTEXIST | OFN_PATHMUSTEXIST | OFN_NOCHANGEDIR
    if comdlg32.GetOpenFileNameW(ctypes.byref(ofn)):
        return Path(buf.value)
    code = comdlg32.CommDlgExtendedError()
    if code:
        raise DialogUnavailable(f"the file window could not be opened (error {code}); paste the full path instead")
    return None                                                        # cancelled
