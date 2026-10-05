"""Entry point of the standalone program (PyInstaller). Started with no arguments (a double-click) it opens the app."""
import sys

from datapipe.cli import main

if __name__ == "__main__":
    code = main()
    if getattr(sys, "frozen", False) and len(sys.argv) == 1 and code:
        input("datapipe stopped with an error (see above). Press Enter to close. ")
    sys.exit(code)
