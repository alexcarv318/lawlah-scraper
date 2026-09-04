#!/usr/bin/env bash
set -euo pipefail

ROOT="${1:-/workspace/lawlah}"
shift || true
if [ "$#" -eq 0 ]; then
  echo "usage: runpod.sh <root> <command...>" >&2
  exit 2
fi

LOG="$ROOT/train.log"
DONE="$ROOT/TRAINING_DONE"
FAILED="$ROOT/TRAINING_FAILED"
POD_ID="${RUNPOD_POD_ID:-}"

export PYTHONPATH="$ROOT"
cd "$ROOT"
rm -f "$DONE" "$FAILED"

stop_pod() {
  if [ -z "$POD_ID" ]; then
    echo "skip pod stop: RUNPOD_POD_ID unset"
    return 0
  fi

  echo "stopping pod $POD_ID"
  if runpodctl stop pod "$POD_ID"; then
    return 0
  fi

  runpodctl pod stop "$POD_ID" || true
}

on_exit() {
  status=$?
  if [ "$status" -ne 0 ] && [ ! -f "$DONE" ]; then
    echo "failed status=$status finished=$(date -u +%Y-%m-%dT%H:%M:%SZ)" | tee "$FAILED"
  fi
  stop_pod
}
trap on_exit EXIT

{
  echo "START $(date -u +%Y-%m-%dT%H:%M:%SZ) $*"
  nvidia-smi || true
  PYTHON="${PYTHON:-/usr/local/bin/python}"
  "$PYTHON" -m pip install --break-system-packages \
    datasets protobuf pyarrow scikit-learn sentencepiece transformers
  "$@"
  echo "TRAINING FINISHED $(date -u +%Y-%m-%dT%H:%M:%SZ)"
} 2>&1 | tee "$LOG"

echo "ok finished=$(date -u +%Y-%m-%dT%H:%M:%SZ)" > "$DONE"
