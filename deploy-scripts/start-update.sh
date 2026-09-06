#!/usr/bin/env bash
# Weekly knowledge-base update: new cases, then paragraph classify/embed.
# Stops this EC2 when the job finishes or fails.
#
# The weekly clock is not on this box (it is stopped between runs).
# EventBridge in lexrag-infrastructure starts the instance and SSM-runs this script.
# Enable it with lawlah_scraper_weekly_update_enabled = true after prepare + first backfill.
#
# Manual run on the instance:
#   /home/ubuntu/lawlah-scraper/deploy-scripts/start-update.sh

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
# shellcheck source=deploy-scripts/common.sh
source "${SCRIPT_DIR}/common.sh"

run_update() {
  require_checkpoints
  run_pipeline cases update
  run_pipeline paragraphs
}

run_job_and_stop run_update
