# Locked requirements

The `.txt` files here pin **every** package CI and the release build install, to an exact version and to the SHA-256 of every file
published for it, so a changed or hijacked upload is refused (`pip install --require-hashes`). The `.in` files are the short lists
a person edits; the `.txt` files are generated from them and cover Windows, macOS and Linux and Python 3.11 and newer in one file.

| file | used by | holds |
|---|---|---|
| `test.txt` | `.github/workflows/ci.yml` | what the test suite needs: duckdb, pytest, playwright, setuptools |
| `build.txt` | `.github/workflows/build.yml` | what the standalone program and installer are built with: duckdb, PyInstaller, setuptools |
| `audit.txt` | the `audit` job in `ci.yml` | `pip-audit`, the tool that looks for known vulnerabilities |

The program itself (`pyproject.toml`) only asks for `duckdb>=1.0,<2`, so a person who installs it with pip is not forced onto these versions.

## Updating

Dependabot opens a pull request when a pinned package has a new release (see `.github/dependabot.yml`); CI runs on that pull request.
To refresh by hand (needs [uv](https://docs.astral.sh/uv/), `pip install uv`):

```
uv pip compile requirements/test.in  --universal --python-version 3.11 --generate-hashes -o requirements/test.txt
uv pip compile requirements/build.in --universal --python-version 3.11 --generate-hashes -o requirements/build.txt
uv pip compile requirements/audit.in --universal --python-version 3.11 --generate-hashes -o requirements/audit.txt
```

To install from a lock the way CI does:

```
python -m pip install --require-hashes -r requirements/test.txt
python -m pip install --no-deps --no-build-isolation -e .
```

`--no-deps` and `--no-build-isolation` matter: without them pip would fetch unpinned dependencies and an unpinned `setuptools` while building.
