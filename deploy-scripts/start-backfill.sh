#!/usr/bin/env bash
# One-time knowledge-base backfill. Stops this EC2 when the job finishes or fails.
#
# On the instance:
#   /home/ubuntu/lawlah-scraper/deploy-scripts/start-backfill.sh
#
# From your laptop (instance must already be running):
#   aws ssm send-command --profile aws_admin --region ap-south-1 \
#     --instance-ids <lawlah-scraper-id> \
#     --document-name AWS-RunShellScript \
#     --parameters commands='["sudo -iu ubuntu bash /home/ubuntu/lawlah-scraper/deploy-scripts/start-backfill.sh"]'

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
# shellcheck source=deploy-scripts/common.sh
source "${SCRIPT_DIR}/common.sh"

run_backfill() {
  require_checkpoints
  # run_pipeline cases backfill
  # run_pipeline paragraphs
  run_pipeline acts backfill
}

run_job_and_stop run_backfill
