#!/usr/bin/env bash
# =============================================================================
#  OpsPilot — Manual Direct Deploy Script (Source Sync & VPS Build)
#  Syncs local code directly to the VPS and builds via Docker Compose.
#  Does NOT depend on GitHub Actions or GHCR.
#
#  USAGE:
#    ./deploy_manual.sh
#
#  CONFIG (set in .deploy.env):
#    DEPLOY_HOST   VPS IP or hostname  (required)
#    DEPLOY_USER   SSH user            (default: ubuntu)
#    SSH_KEY_PATH  Path to SSH key      (default: ssh-agent / ~/.ssh/id_rsa)
# =============================================================================
set -euo pipefail

RED='\033[0;31m'; GREEN='\033[0;32m'; YELLOW='\033[1;33m'
BLUE='\033[0;34m'; CYAN='\033[0;36m'; BOLD='\033[1m'; NC='\033[0m'
info()    { echo -e "${CYAN}[INFO]${NC}  $*"; }
success() { echo -e "${GREEN}[OK]${NC}    $*"; }
warn()    { echo -e "${YELLOW}[WARN]${NC}  $*"; }
error()   { echo -e "${RED}[ERROR]${NC} $*" >&2; }
step()    { echo -e "\n${BOLD}${BLUE}══ $* ══${NC}"; }

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"

# Load .deploy.env if present
if [[ -f "${SCRIPT_DIR}/.deploy.env" ]]; then
  info "Loading deployment config from .deploy.env"
  set -a; source "${SCRIPT_DIR}/.deploy.env"; set +a
fi

# ── Config ────────────────────────────────────────────────────────────────────
DEPLOY_HOST="${DEPLOY_HOST:-}"
DEPLOY_USER="${DEPLOY_USER:-ubuntu}"
SSH_KEY_PATH="${SSH_KEY_PATH:-}"
REMOTE_DIR="/opt/opspilot"

# ── Build SSH options ─────────────────────────────────────────────────────────
SSH_OPTS=(-o "BatchMode=yes" -o "ConnectTimeout=15" -o "StrictHostKeyChecking=yes")
[[ -n "${SSH_KEY_PATH}" ]] && SSH_OPTS+=(-i "${SSH_KEY_PATH}")

ssh_run() { ssh "${SSH_OPTS[@]}" "${DEPLOY_USER}@${DEPLOY_HOST}" "$@"; }

# ── Pre-flight ────────────────────────────────────────────────────────────────
step "Pre-flight checks"

if [[ -z "${DEPLOY_HOST}" ]]; then
  error "DEPLOY_HOST is not set."
  error "Create .deploy.env and add:  DEPLOY_HOST=<your-vps-ip>"
  exit 1
fi

info "Target VPS : ${DEPLOY_USER}@${DEPLOY_HOST}"
info "Target Dir : ${REMOTE_DIR}"

if ! ssh_run true 2>/dev/null; then
  error "SSH connection to ${DEPLOY_HOST} failed."
  warn  "If this is your first time connecting, add host key fingerprint:"
  warn  "  ssh-keyscan ${DEPLOY_HOST} >> ~/.ssh/known_hosts"
  exit 1
fi
success "SSH connection verified"

# ── Step 1: Ensure Remote Directories & Permissions ───────────────────────────
step "1/4  Prepare remote directory"
ssh_run bash -s << 'REMOTE'
  set -euo pipefail
  sudo mkdir -p /opt/opspilot/audit_logs
  sudo chown -R "$(id -u):$(id -g)" /opt/opspilot
REMOTE
success "Remote directory /opt/opspilot ready"

# ── Step 2: Sync Codebase to VPS ──────────────────────────────────────────────
step "2/4  Sync source code to VPS"

# Use rsync if available, fallback to tar over ssh
if command -v rsync >/dev/null 2>&1; then
  RSYNC_SSH="ssh"
  [[ -n "${SSH_KEY_PATH}" ]] && RSYNC_SSH="ssh -i ${SSH_KEY_PATH}"
  
  rsync -avz --delete \
    -e "${RSYNC_SSH} -o StrictHostKeyChecking=yes" \
    --exclude '.git' \
    --exclude '__pycache__' \
    --exclude '*.pyc' \
    --exclude '.pytest_cache' \
    --exclude '.ruff_cache' \
    --exclude '.mypy_cache' \
    --exclude '.venv' \
    --exclude '.deploy.env' \
    --exclude 'audit_logs/*' \
    "${SCRIPT_DIR}/" "${DEPLOY_USER}@${DEPLOY_HOST}:${REMOTE_DIR}/"
else
  info "rsync not found locally, streaming tar archive over SSH..."
  tar --exclude='.git' \
      --exclude='__pycache__' \
      --exclude='*.pyc' \
      --exclude='.pytest_cache' \
      --exclude='.ruff_cache' \
      --exclude='.mypy_cache' \
      --exclude='.venv' \
      --exclude='.deploy.env' \
      -czf - -C "${SCRIPT_DIR}" . | ssh_run "tar -xzf - -C ${REMOTE_DIR}"
fi
success "Source code synced"

# ── Step 3: Build & Start Container on VPS ───────────────────────────────────
step "3/4  Build and start container on VPS"
ssh_run bash -s -- "${REMOTE_DIR}" << 'REMOTE'
  set -euo pipefail
  cd "$1"

  # Ensure config.yaml exists (create from config.example.yaml if missing)
  if [ ! -f config.yaml ] && [ -f config.example.yaml ]; then
    echo "Creating initial config.yaml from config.example.yaml..."
    cp config.example.yaml config.yaml
  fi

  # Ensure .env exists (warn if missing)
  if [ ! -f .env ]; then
    echo "WARNING: .env file missing in /opt/opspilot!"
  fi

  echo "Building Docker container..."
  sudo docker compose -f docker-compose.yml build

  echo "Starting OpsPilot container..."
  sudo docker compose -f docker-compose.yml up -d --remove-orphans

  # Clean unused images
  sudo docker image prune -f >/dev/null 2>&1 || true
REMOTE
success "Build completed and container started"

# ── Step 4: Health Check ──────────────────────────────────────────────────────
step "4/4  Health check"
sleep 4
STATUS=$(ssh_run sudo docker inspect opspilot-agent --format '{{.State.Status}}' 2>/dev/null || echo "not_found")

if [[ "${STATUS}" == "running" ]]; then
  success "opspilot-agent is RUNNING ✅"
  echo ""
  info "Recent logs from OpsPilot:"
  ssh_run sudo docker logs opspilot-agent --tail 25 2>&1 || true
else
  error "Container failed to run. Status: ${STATUS}"
  ssh_run sudo docker logs opspilot-agent --tail 35 2>&1 || true
  exit 1
fi

# ── Summary ───────────────────────────────────────────────────────────────────
echo ""
echo -e "${GREEN}${BOLD}╔══════════════════════════════════════════════════════╗"
echo -e "║  ✅  OpsPilot built and deployed successfully!        "
echo -e "╚══════════════════════════════════════════════════════╝${NC}"
echo ""
echo -e "  Host    : ${DEPLOY_USER}@${DEPLOY_HOST}"
echo -e "  Path    : ${REMOTE_DIR}"
echo -e "  Logs    : ssh ${DEPLOY_USER}@${DEPLOY_HOST} 'sudo docker logs -f opspilot-agent'"
echo -e "  Restart : ssh ${DEPLOY_USER}@${DEPLOY_HOST} 'cd /opt/opspilot && sudo docker compose restart'"
echo -e "  Stop    : ssh ${DEPLOY_USER}@${DEPLOY_HOST} 'cd /opt/opspilot && sudo docker compose down'"
echo ""
