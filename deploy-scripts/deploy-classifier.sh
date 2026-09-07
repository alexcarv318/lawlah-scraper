#!/usr/bin/env bash
# Copy only the classification API onto the GPU box and restart the service.
#
# From your laptop, with the instance running and prepared:
#   aws ssm send-command --profile aws_admin --region ap-south-1 \
#     --instance-ids <lawlah-classifier-id> \
#     --document-name AWS-RunShellScript \
#     --parameters "$(jq -n --rawfile cmd deploy-scripts/deploy-classifier.sh '{commands:[$cmd]}')"

set -euo pipefail

export HOME="${HOME:-/home/ubuntu}"
CLASSIFIER_DIR="${LAWLAH_CLASSIFIER_DIR:-/home/ubuntu/lawlah-classifier}"
REPO_CACHE="${LAWLAH_SCRAPER_CACHE:-/tmp/lawlah-scraper}"
DEPLOY_KEY_SECRET="${LAWLAH_SCRAPER_DEPLOY_KEY_SECRET:-lawlah/scraper-deploy-key}"
DEFAULT_GITHUB_REPOSITORY="${LAWLAH_SCRAPER_GITHUB_REPOSITORY:-alexcarv318/lawlah-scraper}"
CHECKPOINT_BUCKET="${LAWLAH_CLASSIFIER_CHECKPOINT_BUCKET:-lawlah-scraper-classificators}"
SSH_KEY_PATH="${HOME}/.ssh/lawlah_scraper_deploy"

if [ "$(id -u)" -eq 0 ]; then
  TARGET_USER="${SUDO_USER:-ubuntu}"
  COPIED="/tmp/lawlah-deploy-classifier.sh"
  cat "$0" > "${COPIED}"
  chown "${TARGET_USER}:${TARGET_USER}" "${COPIED}"
  chmod 755 "${COPIED}"
  exec sudo -iu "${TARGET_USER}" env \
    LAWLAH_CLASSIFIER_DIR="${CLASSIFIER_DIR}" \
    LAWLAH_SCRAPER_CACHE="${REPO_CACHE}" \
    LAWLAH_SCRAPER_DEPLOY_KEY_SECRET="${DEPLOY_KEY_SECRET}" \
    LAWLAH_SCRAPER_GITHUB_REPOSITORY="${DEFAULT_GITHUB_REPOSITORY}" \
    LAWLAH_CLASSIFIER_CHECKPOINT_BUCKET="${CHECKPOINT_BUCKET}" \
    bash "${COPIED}"
fi

export HOME="${HOME:-/home/ubuntu}"
export PATH="${HOME}/.local/bin:/usr/local/bin:${PATH}"

imds_token() {
  curl -fsS -X PUT "http://169.254.169.254/latest/api/token" \
    -H "X-aws-ec2-metadata-token-ttl-seconds: 21600"
}

aws_region() {
  local token
  token="$(imds_token)"
  curl -fsS -H "X-aws-ec2-metadata-token: ${token}" \
    "http://169.254.169.254/latest/meta-data/placement/region"
}

clone_source_repo() {
  mkdir -p "${HOME}/.ssh"
  chmod 700 "${HOME}/.ssh"
  aws secretsmanager get-secret-value \
    --region "$(aws_region)" \
    --secret-id "${DEPLOY_KEY_SECRET}" \
    --query SecretString \
    --output text > "${SSH_KEY_PATH}"
  chmod 600 "${SSH_KEY_PATH}"
  ssh-keyscan -t ed25519,rsa github.com >> "${HOME}/.ssh/known_hosts" 2>/dev/null
  export GIT_SSH_COMMAND="ssh -i ${SSH_KEY_PATH} -o IdentitiesOnly=yes"

  if [ ! -d "${REPO_CACHE}/.git" ]; then
    git clone "git@github.com:${DEFAULT_GITHUB_REPOSITORY}.git" "${REPO_CACHE}"
  else
    git -C "${REPO_CACHE}" fetch origin
    git -C "${REPO_CACHE}" pull --ff-only origin "$(git -C "${REPO_CACHE}" rev-parse --abbrev-ref HEAD)"
  fi
}

copy_scoped_package() {
  rm -rf "${CLASSIFIER_DIR}/src"
  mkdir -p \
    "${CLASSIFIER_DIR}/src/classification/training" \
    "${CLASSIFIER_DIR}/src/classification/checkpoints"

  cp "${REPO_CACHE}/src/__init__.py" "${CLASSIFIER_DIR}/src/__init__.py"
  cp "${REPO_CACHE}/src/logger.py" "${CLASSIFIER_DIR}/src/logger.py"
  cp "${REPO_CACHE}/src/classification/__init__.py" "${CLASSIFIER_DIR}/src/classification/__init__.py"
  cp "${REPO_CACHE}/src/classification/schema.py" "${CLASSIFIER_DIR}/src/classification/schema.py"
  cp "${REPO_CACHE}/src/classification/repository.py" "${CLASSIFIER_DIR}/src/classification/repository.py"
  cp "${REPO_CACHE}/src/classification/router.py" "${CLASSIFIER_DIR}/src/classification/router.py"
  cp "${REPO_CACHE}/src/classification/training/__init__.py" "${CLASSIFIER_DIR}/src/classification/training/__init__.py"
  cp "${REPO_CACHE}/src/classification/training/concept_topics.py" "${CLASSIFIER_DIR}/src/classification/training/concept_topics.py"
  cp "${REPO_CACHE}/src/classification/training/labels.py" "${CLASSIFIER_DIR}/src/classification/training/labels.py"
  cp "${REPO_CACHE}/src/classification/training/metrics.py" "${CLASSIFIER_DIR}/src/classification/training/metrics.py"
  cp "${REPO_CACHE}/src/classification/training/model.py" "${CLASSIFIER_DIR}/src/classification/training/model.py"
  cp "${REPO_CACHE}/deploy-scripts/classifier/pyproject.toml" "${CLASSIFIER_DIR}/pyproject.toml"
  if [ -f "${REPO_CACHE}/deploy-scripts/classifier/uv.lock" ]; then
    cp "${REPO_CACHE}/deploy-scripts/classifier/uv.lock" "${CLASSIFIER_DIR}/uv.lock"
  fi
}

sync_checkpoints() {
  aws s3 sync "s3://${CHECKPOINT_BUCKET}/" "${CLASSIFIER_DIR}/src/classification/checkpoints/" \
    --region "$(aws_region)"
}

install_and_restart() {
  cd "${CLASSIFIER_DIR}"
  if [ -f uv.lock ]; then
    uv sync --frozen
  else
    uv sync
  fi
  sudo systemctl restart lawlah-classifier.service
  sleep 3
  sudo systemctl --no-pager --full status lawlah-classifier.service || true
}

clone_source_repo
copy_scoped_package
sync_checkpoints
install_and_restart

echo "Classifier package deployed to ${CLASSIFIER_DIR}"
