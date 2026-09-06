#!/usr/bin/env bash
# First-time and repeatable setup for the lawlah-scraper EC2.
#
# Does not stop the instance. After this you can run commands by hand,
# or start-backfill.sh / start-update.sh.
#
# From your laptop, with the instance running:
#   aws ssm send-command \
#     --profile aws_admin --region ap-south-1 \
#     --instance-ids "$(terraform -chdir=/path/to/lexrag-infrastructure output -raw lawlah_scraper_instance_id)" \
#     --document-name AWS-RunShellScript \
#     --parameters "$(jq -n --rawfile cmd deploy-scripts/prepare-server.sh '{commands:[$cmd]}')"
#
# Put values in Secrets Manager first:
#   lawlah/scraper              JSON (OPENAI_API_KEY, BOT_TOKEN, proxy, optional GITHUB_REPOSITORY)
#   lawlah/scraper-deploy-key   raw GitHub deploy-key PEM

set -euo pipefail

REPO_DIR="${LAWLAH_SCRAPER_DIR:-/home/ubuntu/lawlah-scraper}"
SCRAPER_SECRET="${LAWLAH_SCRAPER_SECRET:-lawlah/scraper}"
DEPLOY_KEY_SECRET="${LAWLAH_SCRAPER_DEPLOY_KEY_SECRET:-lawlah/scraper-deploy-key}"
DEFAULT_GITHUB_REPOSITORY="${LAWLAH_SCRAPER_GITHUB_REPOSITORY:-alexcarv318/lawlah-scraper}"

if [ "$(id -u)" -eq 0 ]; then
  TARGET_USER="${SUDO_USER:-ubuntu}"
  TARGET_HOME="$(getent passwd "${TARGET_USER}" | cut -d: -f6)"
  COPIED="/tmp/lawlah-prepare-server.sh"
  cat "$0" > "${COPIED}"
  chown "${TARGET_USER}:${TARGET_USER}" "${COPIED}"
  chmod 755 "${COPIED}"
  install -d -m 755 -o "${TARGET_USER}" -g "${TARGET_USER}" "${TARGET_HOME}"
  exec sudo -iu "${TARGET_USER}" env \
    LAWLAH_SCRAPER_DIR="${REPO_DIR}" \
    LAWLAH_SCRAPER_SECRET="${SCRAPER_SECRET}" \
    LAWLAH_SCRAPER_DEPLOY_KEY_SECRET="${DEPLOY_KEY_SECRET}" \
    LAWLAH_SCRAPER_GITHUB_REPOSITORY="${DEFAULT_GITHUB_REPOSITORY}" \
    bash "${COPIED}"
fi

export HOME="${HOME:-/home/ubuntu}"
SSH_KEY_PATH="${HOME}/.ssh/lawlah_scraper_deploy"
export PATH="${HOME}/.local/bin:/usr/local/bin:${PATH}"

imds_token() {
  curl -fsS -X PUT "http://169.254.169.254/latest/api/token" \
    -H "X-aws-ec2-metadata-token-ttl-seconds: 21600"
}

aws_region() {
  if [ -n "${AWS_REGION:-}" ]; then
    printf '%s\n' "${AWS_REGION}"
    return
  fi
  local token
  token="$(imds_token)"
  curl -fsS -H "X-aws-ec2-metadata-token: ${token}" \
    "http://169.254.169.254/latest/meta-data/placement/region"
}

install_system_packages() {
  sudo DEBIAN_FRONTEND=noninteractive apt-get update
  sudo DEBIAN_FRONTEND=noninteractive apt-get install -y \
    ca-certificates \
    curl \
    git \
    jq \
    unzip \
    python3 \
    python3-venv \
    postgresql-client
}

install_aws_cli() {
  if command -v aws >/dev/null 2>&1; then
    return
  fi
  local tmp
  tmp="$(mktemp -d)"
  curl -fsSL "https://awscli.amazonaws.com/awscli-exe-linux-aarch64.zip" -o "${tmp}/awscliv2.zip"
  unzip -q "${tmp}/awscliv2.zip" -d "${tmp}"
  sudo "${tmp}/aws/install"
  rm -rf "${tmp}"
}

install_uv() {
  if command -v uv >/dev/null 2>&1; then
    return
  fi
  curl -LsSf https://astral.sh/uv/install.sh | sh
}

secret_string() {
  aws secretsmanager get-secret-value \
    --region "$(aws_region)" \
    --secret-id "$1" \
    --query SecretString \
    --output text
}

clone_repository() {
  local app_secret repository
  mkdir -p "${HOME}/.ssh"
  chmod 700 "${HOME}/.ssh"
  secret_string "${DEPLOY_KEY_SECRET}" > "${SSH_KEY_PATH}"
  chmod 600 "${SSH_KEY_PATH}"
  ssh-keyscan -t ed25519,rsa github.com >> "${HOME}/.ssh/known_hosts" 2>/dev/null
  chmod 644 "${HOME}/.ssh/known_hosts"

  app_secret="$(secret_string "${SCRAPER_SECRET}")"
  repository="$(python3 -c 'import json,sys; print(json.loads(sys.argv[1]).get("GITHUB_REPOSITORY") or "")' "${app_secret}")"
  if [ -z "${repository}" ]; then
    repository="${DEFAULT_GITHUB_REPOSITORY}"
  fi

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
    --db-instance-identifier "${LAWLAH_KB_IDENTIFIER:-lawlah-kb}" \
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
    raise SystemExit("lawlah/scraper must be a JSON object")

lines = {
    "POSTGRES_USER": rds["username"],
    "POSTGRES_PASSWORD": rds["password"],
    "POSTGRES_HOST": str(app.get("POSTGRES_HOST") or instance["Endpoint"]["Address"]),
    "POSTGRES_PORT": str(app.get("POSTGRES_PORT") or instance["Endpoint"]["Port"]),
    "RAW_SOURCE_DATABASE": str(app.get("RAW_SOURCE_DATABASE") or "raw_source"),
    "KNOWLEDGE_BASE_DATABASE": str(app.get("KNOWLEDGE_BASE_DATABASE") or "knowledge_base"),
}

for key in (
    "OPENAI_API_KEY",
    "BOT_TOKEN",
    "EMBEDDING_MODEL",
    "PROXY_DNS",
    "PROXY_PORT",
    "PROXY_USERNAME",
    "PROXY_PASSWORD",
):
    value = app.get(key)
    if value is None:
        continue
    text = str(value).strip()
    if text:
        lines[key] = text

rendered = [f"{key}={json.dumps(value)}" for key, value in lines.items()]
env_path.write_text("\n".join(rendered) + "\n")
print(f"Wrote {env_path}")
PY
}

prepare_from_repo() {
  write_env_file

  cd "${REPO_DIR}"
  uv sync --frozen
  uv run playwright install chromium
  uv run playwright install-deps chromium

  python3 - "${REPO_DIR}/.env" <<'PY'
from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path

values: dict[str, str] = {}
for line in Path(sys.argv[1]).read_text().splitlines():
    key, separator, raw = line.partition("=")
    if separator:
        values[key] = json.loads(raw)

env = os.environ.copy()
env["PGPASSWORD"] = values["POSTGRES_PASSWORD"]
base = [
    "psql",
    "-h", values["POSTGRES_HOST"],
    "-p", values["POSTGRES_PORT"],
    "-U", values["POSTGRES_USER"],
    "-v", "ON_ERROR_STOP=1",
]
exists = subprocess.check_output(
    [*base, "-d", "postgres", "-tAc", "SELECT 1 FROM pg_database WHERE datname = 'raw_source'"],
    env=env,
    text=True,
).strip()
if exists != "1":
    subprocess.run([*base, "-d", "postgres", "-c", "CREATE DATABASE raw_source"], env=env, check=True)
subprocess.run(
    [*base, "-d", "knowledge_base", "-c", "CREATE EXTENSION IF NOT EXISTS vector;"],
    env=env,
    check=True,
)
PY

  uv run alembic upgrade head
  uv run alembic -n raw upgrade head

  echo
  echo "Server is ready at ${REPO_DIR}"
  echo "Checkpoints are gitignored. Copy src/classification/checkpoints if you will classify."
  echo "Seed taxonomy in knowledge_base before paragraphs or acts."
  echo "Then run deploy-scripts/start-backfill.sh or any uv run python -m src.pipeline ... command."
}

install_system_packages
install_aws_cli
install_uv
clone_repository
prepare_from_repo
