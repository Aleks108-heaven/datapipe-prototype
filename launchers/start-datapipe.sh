#!/bin/sh
# Start datapipe (Mac, Linux). Your files go in ~/datapipe/files ; results are written to ~/datapipe/work
ROOT="$HOME/datapipe"
mkdir -p "$ROOT/files"
if command -v datapipe >/dev/null 2>&1; then
  exec datapipe --workdir "$ROOT/work" app --data-dir "$ROOT/files" "$@"
fi
for PY in python3 python; do
  if command -v "$PY" >/dev/null 2>&1; then
    exec "$PY" -m datapipe --workdir "$ROOT/work" app --data-dir "$ROOT/files" "$@"
  fi
done
echo "datapipe is not installed, or Python was not found."
echo "Install Python 3.10 or newer, then see docs/INSTALL.md"
printf "Press Enter to close. "; read _
exit 1
