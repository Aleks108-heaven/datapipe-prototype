# Install datapipe on a laptop

You need **Python 3.10 or newer** (python.org; or `scoop install python` / `winget install Python.Python.3.13` on Windows, `brew install python` on Mac, your package manager on Linux). Memory: a CSV or TSV file above 100 MB is read in chunks and needs well under 1 GB of RAM whatever its size (it needs free disk space instead, about 2-3 times the file's size while it runs). Other formats are loaded whole and need about 25 times their size in RAM (a 120 MB file used about 2.5 GB), so a 16 GB laptop is comfortable.

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

## Or: the standalone program (no Python needed)

A single file, about 25 MB, built for Windows, Mac (Apple silicon) and Linux by the *build standalone program* workflow on GitHub
(Actions tab, then *Run workflow*; the files appear as downloads at the bottom of the run, and as a Release when a version tag is pushed).

- **Windows:** `datapipe-windows.exe`. Double-click it. Windows may say *"Windows protected your PC"* because the file is not signed: choose *More info*, then *Run anyway*.
- **Mac:** `datapipe-macos-arm64.tar.gz`. Unpack it (double-click), then in Terminal: `xattr -d com.apple.quarantine datapipe` and `./datapipe` (or right-click, Open). Intel Macs: use the pip install above.
- **Linux:** `datapipe-linux-x86_64.tar.gz`. `tar xzf datapipe-linux-x86_64.tar.gz && ./datapipe`

Started without arguments it opens the app in your browser. A black window stays open showing the link; close it to stop the app. Your files go in `~/datapipe/files`, results in `~/datapipe/work`. It starts in a few seconds (it unpacks itself each time). With arguments it works exactly like the `datapipe` command, for example `datapipe.exe sample test.csv --mb 5`.

**First time, no files?** On the *Run a file* page press **Create a fake sample file to try**: it writes a 2 MB file of invented buyers into your files folder and selects the matching example schema and metrics. The ⚙ **Settings** button holds your name, the default policy, size and memory limits, light/dark appearance, and a check of the audit log.

## What is not available yet

- No signed installer, so Windows and Mac warn about an unknown publisher (code-signing certificates cost money). No Intel-Mac build.
- Tested on Windows; Mac and Linux are covered by the automated tests on GitHub but have not been tried by hand.
- Excel and Numbers files are not read directly: export to CSV first.
