#!/usr/bin/env bash
# Run every check that must pass before a commit. Usage: scripts/check.sh
set -euo pipefail
cd "$(dirname "$0")/.."
uv run ruff check .
uv run ruff format --check .
uv run mypy
uv run python scripts/gen_block_reference.py > /dev/null
git diff --quiet -- docs/blocks docs/app-help || { echo "generated docs changed; review and stage them"; }
QT_QPA_PLATFORM=offscreen uv run pytest -q
NO_MKDOCS_2_WARNING=1 uv run mkdocs build --strict --quiet  # the website: any warning (a broken link) fails
