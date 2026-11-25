# Contributing to Nervus

## Setup

```bash
uv sync --extra gui
uv run pre-commit install
```

## Workflow

1. Pick an item from [`plan.md`](plan.md), or open an issue.
2. Write the code **and** its tests **and** its documentation, both in the app and on GitHub:
   - block docstrings (shown in the in-app help and the generated reference), with LaTeX equations,
   - in-app guide pages in `src/nervus/gui/resources/help/`,
   - user docs in `docs/`, with pictures where they help:
     `uv run python scripts/gen_figures.py` (block figures) and
     `QT_QPA_PLATFORM=offscreen uv run python scripts/screenshots.py` (editor screenshots),
   - then `uv run python scripts/gen_block_reference.py`.
3. Run the checks:
   ```bash
   uv run ruff check . && uv run ruff format --check . && uv run mypy && uv run pytest
   ```
4. Tick the item off in `plan.md` and add an entry to `CHANGELOG.md` under *Unreleased*.
5. Commit with a descriptive message.

## Architecture rules

- `nervus.core`, `nervus.sim`, `nervus.solvers`, `nervus.blocks` and `nervus.io` **must never import Qt**.
- The GUI (`nervus.gui`) follows Model–View–Controller:
  - *models* wrap the core diagram and emit Qt signals when it changes,
  - *views* render and forward user input, with no business logic,
  - *controllers* change the model through undoable commands.
- Every block must be verified by tests, against analytical solutions where possible.

## Writing a block

See [docs/developer/custom-blocks.md](docs/developer/custom-blocks.md).

## Credit

Every merged contribution is credited in [CONTRIBUTORS.md](CONTRIBUTORS.md) and in the editor under **Help ▸ About
Nervus ▸ Contributors**. Fill in the *Credit* section of the pull request (the name you want to appear and one line
about the contribution); when the pull request is merged, `.github/workflows/credit.yml` adds the row. Without it,
your GitHub name and the pull request's title are used.

## Continuous integration

Run `scripts/check.sh` before every push: it is the full check. CI (`.github/workflows/ci.yml`) runs the tests on
Python 3.11 and 3.13 when code changes, for pull requests, every Monday and on demand (Actions → CI → *Run
workflow*); a newer push cancels the run of an older one. Put `[skip ci]` in a commit message to skip it.

## Performance

`uv run python scripts/benchmark.py` times the simulation of some example models (best of three); run it before
and after a change to the engine. `--profile MODEL` shows where the time goes.

## Releasing

Releases go to PyPI from GitHub Actions (`.github/workflows/release.yml`), with
[trusted publishing](https://docs.pypi.org/trusted-publishers/): no password or token is stored.

One-time setup:

1. On pypi.org, log in and open *Your account → Publishing → Add a new pending publisher*: PyPI project name
   `nervus`, owner `ikjbi`, repository `nervus`, workflow `release.yml`, environment `pypi`.
2. On GitHub, *Settings → Environments → New environment* `pypi` (optionally with a required reviewer, so every
   release waits for a click).

Each release:

1. Set `__version__` in `src/nervus/__init__.py` (e.g. `0.1.0`), and in `CHANGELOG.md` rename *Unreleased* to the
   version with the date.
2. Commit, then tag and push: `git tag v0.1.0 && git push origin v0.1.0`.
3. The workflow checks that the tag matches the version, runs the tests, builds the wheel and the source package,
   checks them with `twine check`, and publishes them.
4. The installers workflow builds the standalone applications (PyInstaller, `packaging/nervus.spec`) on macOS,
   Windows and Linux and attaches them to the GitHub Release.
5. The docs workflow publishes the documentation of that version (e.g. 0.2) as `latest` on
   https://ikjbi.github.io/nervus/; pushes to `main` update `dev`.

Try a standalone application before releasing: `cd packaging && uv run --with pyinstaller pyinstaller nervus.spec`,
or run the *Installers* workflow by hand (Actions tab). Try a package before releasing: `uv build`, then `pip install "dist/nervus-<version>-py3-none-any.whl[gui]"` in a
fresh environment, and `nervus --copy-examples /tmp/ex`.
