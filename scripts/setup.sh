#!/usr/bin/env bash
set -u

SCRIPT_DIR="$(CDPATH= cd -- "$(dirname -- "$0")" && pwd)"
SKILL_DIR="$(dirname -- "$SCRIPT_DIR")"

echo "bibliometric-fusion dependency check"
python3 --version || exit 1
python3 -c 'import matplotlib; print("[OK] matplotlib", matplotlib.__version__)' || {
  echo '[MISSING] matplotlib: install with python3 -m pip install matplotlib'
  exit 1
}
echo '[OK] deterministic built-in co-occurrence layout (no graph package required)'
python3 "$SKILL_DIR/scripts/fusion_run.py" info
