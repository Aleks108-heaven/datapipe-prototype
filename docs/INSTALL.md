# Install datapipe on a laptop

You need **Python 3.11 or newer** (python.org; or `scoop install python` / `winget install Python.Python.3.13` on Windows, `brew install python` on Mac, your package manager on Linux). Memory: a CSV or TSV file above 100 MB is read in chunks and needs well under 1 GB of RAM whatever its size (it needs free disk space instead, about 2-3 times the file's size while it runs). Other formats are loaded whole and need about 25 times their size in RAM (a 120 MB file used about 2.5 GB), so a 16 GB laptop is comfortable.

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

Your browser opens the app. Put your data files in **`~/datapipe/files`** (Windows: `C:\Users\<you>\datapipe\files`; the launcher creates it). Results are written to `~/datapipe/work`. For a file anywhere else, press *Choose a file on this computer…* on the Run page; the page's *Where the lists come from* section lists the folders it always reads.

You also need a **schema** for each kind of file (what every column should look like). In the app: choose the file, then *Draft a schema from the chosen file*, review the draft, and pick it. Examples are in the repository's `examples/` folder.

## Or: the standalone program (no Python needed)

Built for Windows, Mac (Apple silicon and Intel) and Linux by the *build standalone program* workflow on GitHub (Actions tab, then *Run workflow*; the files appear as downloads at the bottom of the run, and as a Release when a version tag is pushed). Each build is tested on the system it is for before it is offered: the program is started, a file is run end to end, and it is stopped again.

- **Windows, with a setup wizard (recommended):** `datapipe-windows-setup.exe`. Double-click it and approve the administrator prompt. It installs **for all users, into `C:\Program Files\datapipe`**, adds *datapipe* to the Start menu (a desktop icon is optional) and, when you start the program, creates `C:\Users\<you>\datapipe\files` for the person who uses it. To remove it: Settings, Apps, *datapipe*, Uninstall. Uninstalling never touches your files or results in `C:\Users\<you>\datapipe`. Installing a newer version over an older one upgrades it in place, and a copy that an earlier version installed for your user only (`%LOCALAPPDATA%\Programs\datapipe`) is removed, so you are not left with two. The setup wizard speaks English or Ukrainian: it follows the language of Windows and asks only when Windows uses neither. Without administrator rights, run `datapipe-windows-setup.exe /CURRENTUSER` in a terminal: it then installs for you only, into `%LOCALAPPDATA%\Programs\datapipe`.
- **Windows, single file:** `datapipe-windows.exe`. Double-click it; nothing is installed. It unpacks itself on every start, so it starts slower than the installed program.
- **Mac:** `datapipe-macos-arm64.dmg` for a Mac with an Apple chip (M1 and later), `datapipe-macos-x86_64.dmg` for an Intel Mac ( > About This Mac says which). Open the disk image and drag *datapipe* into *Applications*. Open it from there: it starts the program, which opens in your browser; close the browser tab and it stops by itself a few seconds later (or press *Stop datapipe* on the Settings page). The first time, macOS asks before it opens an app that is not from a registered developer: open *System Settings, Privacy & Security*, scroll to the message about *datapipe* and press *Open Anyway* (the same instructions are inside the disk image). In Terminal, `xattr -dr com.apple.quarantine /Applications/datapipe.app` does the same. If it does not start, `~/datapipe/work/app.log` holds what it would have printed.
- **Linux:** `datapipe-linux-x86_64.tar.gz`. `tar xzf datapipe-linux-x86_64.tar.gz && ./datapipe`

Started without arguments (a double-click) it opens the app in your browser. On Windows and Linux a black window stays open showing the link; close it to stop the app. Your files go in `~/datapipe/files`, results in `~/datapipe/work`. With arguments it works exactly like the `datapipe` command, for example `datapipe.exe sample test.csv --mb 5`.

### What "protected" means here, and what it does not

- **Program Files protects the installed files from being changed.** Only an administrator can add to or alter what is in `C:\Program Files`, so a program or script running as an ordinary user cannot swap `datapipe.exe` for something else. The build's own test checks this on every release: ordinary users may read and run the installed files and nothing else, and the program writes nothing into its own folder (your results go to your own `datapipe` folder).
- **Where the program is installed does not decide whether Windows Security lets it run.** That depends on the program's *signature*, and nothing here is code-signed (a certificate costs money). Microsoft Defender and SmartScreen therefore show "Unknown publisher" / *Windows protected your PC* (More info, then Run anyway), and **Smart App Control**, where it is switched on, can refuse an unsigned program outright. On many Windows versions Smart App Control cannot be turned back on once it has been turned off, so do not switch it off just for this; on such a computer use `python -m datapipe app` (see above), whose `python.exe` is signed, or have the program signed.
- **Check a download** against `SHA256SUMS.txt` from the same release: Windows `Get-FileHash datapipe-windows-setup.exe`, Mac `shasum -a 256 datapipe-macos-*.dmg`. The numbers must be identical.
- **The Mac app** is signed "ad hoc": macOS requires some signature on Apple silicon and this one keeps the app's parts from being changed unnoticed, but it does not say who made the app, so macOS asks once (see above). Removing that question needs an Apple Developer ID and Apple's notarisation (a yearly fee); the Windows equivalent is a code-signing certificate, or a signing service for open-source projects.

**First time, no files?** On the *Run a file* page press **Create a fake sample file to try**: it writes a 2 MB file of invented buyers into your files folder and selects the matching example schema and metrics. The ⚙ **Settings** button holds your name, the default policy, size and memory limits, light/dark appearance, the interface language (English, Ukrainian — Українська — or the language of your browser), the language-model server for column mapping (pick Ollama or LM Studio, check that it answers, choose a model) with an optional API key (on Windows kept in the Windows Credential Manager, not in a file), and a check of the audit log.

## What is not available yet

- Nothing is code-signed (see above), so Windows and Mac warn about an unknown publisher. The setup wizard exists for Windows only; the Mac app comes as a disk image.
- The app (all its pages and messages) is available in English and Ukrainian. The files a run writes (report, quarantine reasons, audit log), the command line and the reasons the checks give for a single row stay in English, so a report reads the same wherever it is opened. The Ukrainian text was written without a review by a native speaker: if a phrase reads oddly, it is one line in `datapipe/webui/i18n_uk.py`.
- Tested by hand on Windows. The Mac app is built and tested on real macOS machines (Apple silicon and Intel) by GitHub's build: it is started the way a double-click starts it, answers, runs a file and quits when macOS asks it to. Nobody has yet used it on a Mac with a screen: how its icon behaves in the Dock, and the first-start question, are known only from the instructions above.
- Excel and Numbers files are not read directly: export to CSV first (datapipe says so when it is given one, and it names UTF-16 "Unicode Text" files, zip and gzip archives, PDFs and SQLite databases for what they are).
