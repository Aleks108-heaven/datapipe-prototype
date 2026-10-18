"""Entry point of the standalone program (PyInstaller). Started with no arguments (a double-click) it opens the app."""
import os
import subprocess
import sys
import traceback
from pathlib import Path

from datapipe.cli import _in_mac_app, main


def _quiet_streams():
    """Started from Finder or without a console there may be no stdout or stderr at all (None); print() must not fail on that."""
    for name in ("stdout", "stderr"):
        if getattr(sys, name, None) is None:
            setattr(sys, name, open(os.devnull, "w", encoding="utf-8"))


def _tell(message):
    """Say something to a person who double-clicked the Mac app and has no terminal: a dialog."""
    text = message.replace("\\", "\\\\").replace('"', '\\"')
    script = f'display dialog "{text}" with title "datapipe" buttons {{"OK"}} default button "OK" with icon caution'
    try:
        subprocess.run(["osascript", "-e", script], check=False, timeout=600)
    except (OSError, subprocess.SubprocessError):
        pass


def _mac_app_log():
    """The Mac app has no terminal: what it would print on screen (errors, tracebacks) goes to ~/datapipe/work/app.log, rewritten at every start.
    The link, which holds the session secret, is printed to stdout and is NOT written there."""
    path = Path.home() / "datapipe" / "work" / "app.log"
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        sys.stderr = open(path, "w", buffering=1, encoding="utf-8")
        sys.stdout = open(os.devnull, "w", encoding="utf-8")
    except OSError:
        _quiet_streams()
    return path


if __name__ == "__main__":
    _quiet_streams()
    double_click = not [a for a in sys.argv[1:] if not a.startswith("-psn_")]            # Finder may add a process number
    mac_app = _in_mac_app() and double_click
    log = _mac_app_log() if mac_app else None
    try:
        code = main()
    except Exception:                                    # noqa: BLE001 - nobody can read a traceback here: leave it in the log and tell the person
        traceback.print_exc()
        code = 1
    if getattr(sys, "frozen", False) and double_click and code:
        if mac_app:
            _tell(f"datapipe could not start or stopped with an error. The details are in:\n{log}")
        else:
            input("datapipe stopped with an error (see above). Press Enter to close. ")
    sys.exit(code)
