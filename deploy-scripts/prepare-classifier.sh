#!/usr/bin/env bash
# First-time setup for the lawlah-classifier GPU EC2.
#
# From your laptop, with the instance running:
#   aws ssm send-command --profile aws_admin --region ap-south-1 \
#     --instance-ids <lawlah-classifier-id> \
#     --document-name AWS-RunShellScript \
#     --parameters "$(jq -n --rawfile cmd deploy-scripts/prepare-classifier.sh '{commands:[$cmd]}')"
#
# Then run deploy-scripts/deploy-classifier.sh.

set -euo pipefail

CLASSIFIER_DIR="${LAWLAH_CLASSIFIER_DIR:-/home/ubuntu/lawlah-classifier}"

if [ "$(id -u)" -eq 0 ]; then
  TARGET_USER="${SUDO_USER:-ubuntu}"
  COPIED="/tmp/lawlah-prepare-classifier.sh"
  cat "$0" > "${COPIED}"
  chown "${TARGET_USER}:${TARGET_USER}" "${COPIED}"
  chmod 755 "${COPIED}"
  exec sudo -iu "${TARGET_USER}" env LAWLAH_CLASSIFIER_DIR="${CLASSIFIER_DIR}" bash "${COPIED}"
fi

export HOME="${HOME:-/home/ubuntu}"
export PATH="${HOME}/.local/bin:/usr/local/bin:${PATH}"

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
    linux-headers-generic \
    gcc \
    make
}

install_aws_cli() {
  if command -v aws >/dev/null 2>&1; then
    return
  fi
  local tmp
  tmp="$(mktemp -d)"
  curl -fsSL "https://awscli.amazonaws.com/awscli-exe-linux-x86_64.zip" -o "${tmp}/awscliv2.zip"
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

install_nvidia() {
  if command -v nvidia-smi >/dev/null 2>&1 && nvidia-smi >/dev/null 2>&1; then
    nvidia-smi
    return
  fi

  sudo DEBIAN_FRONTEND=noninteractive apt-get install -y nvidia-driver-550-server nvidia-utils-550-server
  echo "NVIDIA driver installed. Rebooting so the GPU is usable."
  sudo reboot
}

install_systemd_unit() {
  sudo tee /etc/systemd/system/lawlah-classifier.service >/dev/null <<'UNIT'
[Unit]
Description=Lawlah classifier API
After=network-online.target
Wants=network-online.target

[Service]
Type=simple
User=ubuntu
Group=ubuntu
WorkingDirectory=/home/ubuntu/lawlah-classifier
Environment=PYTHONUNBUFFERED=1
Environment=CLASSIFY_IDLE_SECONDS=300
ExecStart=/home/ubuntu/lawlah-classifier/.venv/bin/uvicorn src.classification.router:app --host 0.0.0.0 --port 8080
Restart=on-failure
RestartSec=5

[Install]
WantedBy=multi-user.target
UNIT
  sudo systemctl daemon-reload
  sudo systemctl enable lawlah-classifier.service
}

install_system_packages
install_aws_cli
install_uv
mkdir -p "${CLASSIFIER_DIR}"
install_systemd_unit
install_nvidia

echo "Classifier host is ready. Run deploy-scripts/deploy-classifier.sh next."
