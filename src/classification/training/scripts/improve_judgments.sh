#!/usr/bin/env bash
set -euo pipefail

ROOT="${1:-/workspace/lawlah}"
PYTHON="${PYTHON:-/usr/local/bin/python}"
export PYTHONPATH="$ROOT"
cd "$ROOT"

"$PYTHON" -m src.classification.training.train --source judgments --task topics --decode-only --batch-size 16
"$PYTHON" -m src.classification.training.train --source judgments --task concepts --decode-only --batch-size 16
"$PYTHON" -m src.classification.training.train --source judgments --task role --encoder microsoft/deberta-v3-base --batch-size 8
