# Install datapipe on a laptop

You need **Python 3.10 or newer** (python.org; or `scoop install python` / `winget install Python.Python.3.13` on Windows, `brew install python` on Mac, your package manager on Linux). Memory: a file needs about 25 times its size in RAM (a 120 MB file used about 2.5 GB), so a 16 GB laptop is comfortable.

## 1. Install
```
python -m pip install git+https://github.com/Aleks108-heaven/datapipe-prototype.git
```
Mac/Linux: use `python3` if `python` is not found. This installs the `datapipe` program and its one dependency (DuckDB). To update later, repeat the command with `--upgrade`. To remove: `python -m pip uninstall datapipe`.

Check it: `python -m datapipe policies` prints three lines.

## 2. Start it
**One click:** download the [launchers](../launchers) folder from the repository (or use the cloned copy) and double-click:
- Windows: `Start-datapipe.cmd`
- Mac: `Start-datapipe.command` (the first time: right-click, then Open, because the file is not signed; if it will not run, in Terminal: `chmod +x Start-datapipe.command start-datapipe.sh`)
- Linux: `sh start-datapipe.sh`

**Or from a terminal:** `python -m datapipe app`

Your browser opens the app. Put your data files in **`~/datapipe/files`** (Windows: `C:\Users\<you>\datapipe\files`; the launcher creates it). Results are written to `~/datapipe/work`. The page's *Add your own files* section lists exactly which folders it reads.

You also need a **schema** for each kind of file (what every column should look like). In the app: choose the file, then *Draft a schema from the chosen file*, review the draft, and pick it. Examples are in the repository's `examples/` folder.

## What is not available yet
- No standalone `.exe` / `.app` and no signed installer: Python must be installed. (Planned once a few testers have tried it.)
- Tested on Windows; Mac and Linux are covered by the automated tests on GitHub but have not been tried by hand.
- Excel and Numbers files are not read directly: export to CSV first.
