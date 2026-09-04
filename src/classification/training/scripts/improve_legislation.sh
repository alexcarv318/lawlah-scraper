#!/usr/bin/env bash
set -euo pipefail

ROOT="${1:-/workspace/lawlah}"
PYTHON="${PYTHON:-/usr/local/bin/python}"
export PYTHONPATH="$ROOT"
cd "$ROOT"

TOPICS_BEST="$ROOT/src/classification/checkpoints/legislation/topics/best.pt"
if [ -f "$TOPICS_BEST" ]; then
  "$PYTHON" -m src.classification.training.train --source legislation --task topics --decode-only --batch-size 16
else
  "$PYTHON" -m src.classification.training.train --source legislation --task topics --batch-size 16
fi
"$PYTHON" -m src.classification.training.train --source legislation --task concepts --batch-size 16
"$PYTHON" -m src.classification.training.train --source legislation --task role --encoder microsoft/deberta-v3-base --batch-size 8
