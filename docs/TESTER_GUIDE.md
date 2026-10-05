# datapipe: tester guide (one page)

**What it is:** a local app that reads a CSV (or JSON / SQL dump), checks every row against a schema you describe, sets bad rows aside with the reason, and writes a cleaned file plus a metrics report. Nothing leaves your computer.

## 1. Set up (5 minutes, once)
You need Python 3.10 or newer (python.org, or `scoop install python` on Windows, `brew install python` on Mac).

```
git clone https://github.com/Aleks108-heaven/datapipe-prototype.git
cd datapipe-prototype
python -m pip install duckdb
```
Windows: use `python`. Mac/Linux: use `python3` if `python` is not found. Check it works: `python -m datapipe policies` should print three lines.

## 2. Make a fake file (never test with real people's data)
```
python examples/make_synthetic_buyers.py buyers.csv --mb 20
```
(`--mb 120` makes a file about the size of the real target. It runs in about 1.5 minutes and uses about 2.5 GB of RAM.)

## 3. Start the app
```
python -m datapipe app --data-dir .
```
Your browser opens. Choose **Data file** `buyers.csv`, **Schema** `schema_buyers.json`, **Metrics** `analysis_buyers.json`, policy **business**, enter your name, press **Run**. When it finishes you see the counts, the metrics tables and download buttons.

**Using a file from somewhere else** (Downloads, a USB stick): copy it into `work/inbox/` (created when the app starts) and press *Refresh the lists*, or restart with `python -m datapipe app --data-dir "C:\path\to\folder"`. The page lists the folders it reads under *Add your own files*.

Prefer the terminal? `python -m datapipe run buyers.csv --policy business --schema examples/schema_buyers.json --analysis examples/analysis_buyers.json --actor yourname`

## 4. What to look at
- **Counts:** about 0.5% of the fake rows are wrong on purpose. Do the "set aside" numbers look right?
- **quarantine.csv** (bad rows and why): is every reason correct and understandable? Did it set aside anything that is actually fine, or keep anything that is wrong?
- **clean.csv:** are the good rows complete? Names and emails show `<masked>` under *business*; that is intended.
- **Report metrics:** do the totals make sense?
- **Try to break it:** an empty file, a file with a wrong header, a file with a date like `03/04/2026`, a `.txt` renamed to `.csv`, two runs at once. It should refuse or explain, never crash or silently guess.

## 5. Rules
- Fake data only, unless the owner of the real data has agreed.
- Do not edit and re-save `clean.csv` (its checksum is recorded; a changed file is detected).
- Output goes into the `work/` folder; delete it when you are done.

## 6. Report a problem
Send: what you did (the steps), what you expected, what happened (screenshot or the text), your system (Windows/Mac/Linux, Python version from `python --version`), and the file size. Do **not** send real data. The run folder's `result.json` and `report.md` hold no raw values and are safe to share when the data is fake.

## 7. Known limits
Files above 300 MB (150 MB in *regulated*) are refused unless you raise the limit with `--max-file-mb`; a file that needs more than an estimated 6 GB of RAM is refused unless you pass `--max-memory-gb` (only on a computer that has the memory). It was tested on Windows; Mac and Linux are what testers are most useful for.
