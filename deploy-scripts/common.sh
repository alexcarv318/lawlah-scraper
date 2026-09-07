#!/usr/bin/env bash
# Shared helpers for lawlah-scraper deploy scripts. Source this file; do not execute it.

set -euo pipefail

REPO_DIR="${LAWLAH_SCRAPER_DIR:-/home/ubuntu/lawlah-scraper}"
SCRAPER_SECRET="${LAWLAH_SCRAPER_SECRET:-lawlah/scraper}"
DEPLOY_KEY_SECRET="${LAWLAH_SCRAPER_DEPLOY_KEY_SECRET:-lawlah/scraper-deploy-key}"
KB_IDENTIFIER="${LAWLAH_KB_IDENTIFIER:-lawlah-kb}"
DEFAULT_GITHUB_REPOSITORY="${LAWLAH_SCRAPER_GITHUB_REPOSITORY:-alexcarv318/lawlah-scraper}"
SSH_KEY_PATH="${HOME}/.ssh/lawlah_scraper_deploy"

export PATH="${HOME}/.local/bin:/usr/local/bin:${PATH}"

imds_token() {
  curl -fsS -X PUT "http://169.254.169.254/latest/api/token" \
    -H "X-aws-ec2-metadata-token-ttl-seconds: 21600"
}

imds() {
  local token
  token="$(imds_token)"
  curl -fsS -H "X-aws-ec2-metadata-token: ${token}" "http://169.254.169.254/latest/meta-data/${1}"
}

aws_region() {
  if [ -n "${AWS_REGION:-}" ]; then
    printf '%s\n' "${AWS_REGION}"
    return
  fi
  imds "placement/region"
}

secret_string() {
  aws secretsmanager get-secret-value \
    --region "$(aws_region)" \
    --secret-id "$1" \
    --query SecretString \
    --output text
}

install_deploy_key() {
  mkdir -p "${HOME}/.ssh"
  chmod 700 "${HOME}/.ssh"
  secret_string "${DEPLOY_KEY_SECRET}" > "${SSH_KEY_PATH}"
  chmod 600 "${SSH_KEY_PATH}"
  ssh-keyscan -t ed25519,rsa github.com >> "${HOME}/.ssh/known_hosts" 2>/dev/null
  chmod 644 "${HOME}/.ssh/known_hosts"
}

github_repository() {
  local app_secret repository
  app_secret="$(secret_string "${SCRAPER_SECRET}")"
  repository="$(python3 -c 'import json,sys; print(json.loads(sys.argv[1]).get("GITHUB_REPOSITORY") or "")' "${app_secret}")"
  if [ -n "${repository}" ]; then
    printf '%s\n' "${repository}"
    return
  fi
  printf '%s\n' "${DEFAULT_GITHUB_REPOSITORY}"
}

clone_or_update_repo() {
  local repository
  install_deploy_key
  repository="$(github_repository)"
  export GIT_SSH_COMMAND="ssh -i ${SSH_KEY_PATH} -o IdentitiesOnly=yes"

  if [ ! -d "${REPO_DIR}/.git" ]; then
    git clone "git@github.com:${repository}.git" "${REPO_DIR}"
  else
    git -C "${REPO_DIR}" fetch origin
    git -C "${REPO_DIR}" pull --ff-only origin "$(git -C "${REPO_DIR}" rev-parse --abbrev-ref HEAD)"
  fi
}

write_env_file() {
  local region app_secret rds_json rds_secret
  region="$(aws_region)"
  app_secret="$(secret_string "${SCRAPER_SECRET}")"
  rds_json="$(aws rds describe-db-instances \
    --region "${region}" \
    --db-instance-identifier "${KB_IDENTIFIER}" \
    --output json)"
  rds_secret="$(secret_string "$(python3 -c '
import json, sys
instance = json.loads(sys.argv[1])["DBInstances"][0]
print(instance["MasterUserSecret"]["SecretArn"])
' "${rds_json}")")"

  python3 - "${REPO_DIR}/.env" "${app_secret}" "${rds_secret}" "${rds_json}" <<'PY'
from __future__ import annotations

import json
import sys
from pathlib import Path

env_path = Path(sys.argv[1])
app = json.loads(sys.argv[2])
rds = json.loads(sys.argv[3])
instance = json.loads(sys.argv[4])["DBInstances"][0]

if not isinstance(app, dict):
    raise SystemExit(f"{env_path} source secret must be a JSON object")

lines = {
    "POSTGRES_USER": rds["username"],
    "POSTGRES_PASSWORD": rds["password"],
    "POSTGRES_HOST": str(app.get("POSTGRES_HOST") or instance["Endpoint"]["Address"]),
    "POSTGRES_PORT": str(app.get("POSTGRES_PORT") or instance["Endpoint"]["Port"]),
    "RAW_SOURCE_DATABASE": str(app.get("RAW_SOURCE_DATABASE") or "raw_source"),
    "KNOWLEDGE_BASE_DATABASE": str(app.get("KNOWLEDGE_BASE_DATABASE") or "knowledge_base"),
}

optional_keys = (
    "OPENAI_API_KEY",
    "BOT_TOKEN",
    "EMBEDDING_MODEL",
    "PROXY_DNS",
    "PROXY_PORT",
    "PROXY_USERNAME",
    "PROXY_PASSWORD",
)
for key in optional_keys:
    value = app.get(key)
    if value is None:
        continue
    text = str(value).strip()
    if text:
        lines[key] = text

rendered: list[str] = []
for key, value in lines.items():
    rendered.append(f"{key}={json.dumps(value)}")

env_path.write_text("\n".join(rendered) + "\n")
print(f"Wrote {env_path}")
PY
}

require_checkpoints() {
  local missing=0
  local path
  for path in \
    "${REPO_DIR}/src/classification/checkpoints/judgments/role/best.pt" \
    "${REPO_DIR}/src/classification/checkpoints/judgments/topics/best.pt" \
    "${REPO_DIR}/src/classification/checkpoints/judgments/concepts/best.pt" \
    "${REPO_DIR}/src/classification/checkpoints/legislation/role/best.pt" \
    "${REPO_DIR}/src/classification/checkpoints/legislation/topics/best.pt" \
    "${REPO_DIR}/src/classification/checkpoints/legislation/concepts/best.pt"
  do
    if [ ! -f "${path}" ]; then
      echo "Missing checkpoint: ${path}" >&2
      missing=1
    fi
  done
  if [ "${missing}" -ne 0 ]; then
    echo "Copy src/classification/checkpoints from your laptop onto this instance, then retry." >&2
    exit 1
  fi
}

run_pipeline() {
  cd "${REPO_DIR}"
  PYTHONUNBUFFERED=1 uv run python -m src.pipeline "$@"
}

stop_this_instance() {
  local instance_id region
  instance_id="$(imds instance-id)"
  region="$(aws_region)"
  echo "Stopping ${instance_id} in ${region}"
  aws ec2 stop-instances --region "${region}" --instance-ids "${instance_id}" >/dev/null
}

run_job_and_stop() {
  trap stop_this_instance EXIT
  clone_or_update_repo
  write_env_file
  cd "${REPO_DIR}"
  uv sync --frozen
  "$@"
}
