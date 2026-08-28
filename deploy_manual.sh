#!/usr/bin/env bash
# =============================================================================
#  OpsPilot — Manual Deployment Script
#  Replicates the GitHub Actions release.yml when Actions billing is unavailable.
#
#  USAGE:
#    ./deploy_manual.sh [IMAGE_TAG]
#
#  CONFIG (set in .deploy.env — never commit this file):
#    DEPLOY_HOST   VPS IP or hostname  (required)
#    DEPLOY_USER   SSH user            (default: ubuntu)
#    GHCR_TOKEN    GitHub PAT with read:packages scope (optional for public images)
#    GITHUB_ACTOR  Your GitHub username (default: iitdeveloper-git)
#    SSH_KEY_PATH  Path to SSH key      (default: ssh-agent)
#    IMAGE_TAG     Tag to deploy        (default: latest, or pass as $1)
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

# Load .deploy.env if present (never committed — local secrets only)
if [[ -f "${SCRIPT_DIR}/.deploy.env" ]]; then
  info "Loading deployment config from .deploy.env"
  set -a; source "${SCRIPT_DIR}/.deploy.env"; set +a
fi

# ── Config ────────────────────────────────────────────────────────────────────
IMAGE_TAG="${1:-${IMAGE_TAG:-latest}}"
DEPLOY_HOST="${DEPLOY_HOST:-}"
DEPLOY_USER="${DEPLOY_USER:-ubuntu}"
GHCR_TOKEN="${GHCR_TOKEN:-}"
GITHUB_ACTOR="${GITHUB_ACTOR:-iitdeveloper-git}"
GITHUB_REPO="iitdeveloper-git/opspilot"
GHCR_IMAGE="ghcr.io/${GITHUB_REPO}"
SSH_KEY_PATH="${SSH_KEY_PATH:-}"
REMOTE_DIR="/opt/opspilot"

# ── Build SSH options ─────────────────────────────────────────────────────────
SSH_OPTS=(-o "BatchMode=yes" -o "ConnectTimeout=15" -o "StrictHostKeyChecking=yes")
[[ -n "${SSH_KEY_PATH}" ]] && SSH_OPTS+=(-i "${SSH_KEY_PATH}")

ssh_run()  { ssh "${SSH_OPTS[@]}" "${DEPLOY_USER}@${DEPLOY_HOST}" "$@"; }
scp_file() { scp "${SSH_OPTS[@]}" "$1" "${DEPLOY_USER}@${DEPLOY_HOST}:$2"; }

# ── Pre-flight ────────────────────────────────────────────────────────────────
step "Pre-flight checks"

if [[ -z "${DEPLOY_HOST}" ]]; then
  error "DEPLOY_HOST is not set."
  error "Create .deploy.env and add:  DEPLOY_HOST=your-server-ip"
  exit 1
fi

info "Target : ${DEPLOY_USER}@${DEPLOY_HOST}"
info "Image  : ${GHCR_IMAGE}:${IMAGE_TAG}"

# Verify SSH (uses known_hosts — run ssh-keyscan once if missing)
if ! ssh_run true 2>/dev/null; then
  error "SSH connection to ${DEPLOY_HOST} failed."
  warn  "If this is your first deploy, add the host fingerprint with:"
  warn  "  ssh-keyscan ${DEPLOY_HOST} >> ~/.ssh/known_hosts"
  exit 1
fi
success "SSH connection verified"

# ── Step 1: Prepare remote directory ─────────────────────────────────────────
step "1/5  Prepare remote directory"
ssh_run bash -s << 'REMOTE'
  set -euo pipefail
  sudo mkdir -p /opt/opspilot/audit_logs
  sudo chown -R "$(id -u):$(id -g)" /opt/opspilot
REMOTE
success "Remote directory ready"

# ── Step 2: Sync Compose and config ──────────────────────────────────────────
step "2/5  Sync docker-compose.prod.yml"

[[ ! -f "${SCRIPT_DIR}/docker-compose.prod.yml" ]] && {
  error "docker-compose.prod.yml not found in project root"; exit 1
}
scp_file "${SCRIPT_DIR}/docker-compose.prod.yml" "${REMOTE_DIR}/docker-compose.prod.yml"
success "docker-compose.prod.yml synced"

# Sync config.yaml only if it exists locally
if [[ -f "${SCRIPT_DIR}/config.yaml" ]]; then
  scp_file "${SCRIPT_DIR}/config.yaml" "${REMOTE_DIR}/config.yaml"
  success "config.yaml synced"
else
  warn "No local config.yaml — remote config unchanged"
fi

# Sync .env only if it exists locally
if [[ -f "${SCRIPT_DIR}/.env" ]]; then
  scp_file "${SCRIPT_DIR}/.env" "${REMOTE_DIR}/.env"
  success ".env synced to VPS"
else
  warn "No local .env — remote .env unchanged (bot will use existing remote .env)"
fi

# ── Step 3: Pull Docker image on VPS ─────────────────────────────────────────
step "3/5  Pull Docker image"
ssh_run bash -s -- "${GHCR_IMAGE}" "${IMAGE_TAG}" "${GHCR_TOKEN}" "${GITHUB_ACTOR}" << 'REMOTE'
  set -euo pipefail
  IMAGE="$1"; TAG="$2"; TOKEN="$3"; ACTOR="$4"
  if [[ -n "${TOKEN}" ]]; then
    echo "${TOKEN}" | sudo docker login ghcr.io -u "${ACTOR}" --password-stdin
    echo "Logged in to GHCR as ${ACTOR}"
  fi
  echo "Pulling ${IMAGE}:${TAG} ..."
  sudo docker pull "${IMAGE}:${TAG}"
REMOTE
success "Image pulled on VPS"

# ── Step 4: Deploy with Docker Compose ───────────────────────────────────────
step "4/5  Docker Compose up"
ssh_run bash -s -- "${REMOTE_DIR}" "${IMAGE_TAG}" << 'REMOTE'
  set -euo pipefail
  DIR="$1"; TAG="$2"
  cd "${DIR}"
  [[ ! -f docker-compose.prod.yml ]] && { echo "ERROR: docker-compose.prod.yml missing"; exit 1; }
  IMAGE_TAG="${TAG}" sudo --preserve-env=IMAGE_TAG \
    docker compose -f docker-compose.prod.yml up -d --remove-orphans
  sudo docker image prune -f >/dev/null 2>&1 || true
REMOTE
success "Docker Compose deployed"

# ── Step 5: Health check ──────────────────────────────────────────────────────
step "5/5  Health check"
sleep 4
STATUS=$(ssh_run sudo docker inspect opspilot-agent --format '{{.State.Status}}' 2>/dev/null || echo "not_found")

if [[ "${STATUS}" == "running" ]]; then
  success "opspilot-agent is RUNNING ✅"
  echo ""
  info "Last 20 log lines:"
  ssh_run sudo docker logs opspilot-agent --tail 20 2>&1 || true
else
  error "Container status: ${STATUS}"
  ssh_run sudo docker logs opspilot-agent --tail 30 2>&1 || true
  exit 1
fi

# ── Summary ───────────────────────────────────────────────────────────────────
echo ""
echo -e "${GREEN}${BOLD}╔══════════════════════════════════════════════════════╗"
echo -e "║  ✅  OpsPilot ${IMAGE_TAG} deployed successfully!       "
echo -e "╚══════════════════════════════════════════════════════╝${NC}"
echo ""
echo -e "  Host    : ${DEPLOY_USER}@${DEPLOY_HOST}"
echo -e "  Image   : ${GHCR_IMAGE}:${IMAGE_TAG}"
echo -e "  Logs    : ssh ${DEPLOY_USER}@${DEPLOY_HOST} 'sudo docker logs -f opspilot-agent'"
echo -e "  Restart : ssh ${DEPLOY_USER}@${DEPLOY_HOST} 'cd /opt/opspilot && sudo IMAGE_TAG=${IMAGE_TAG} docker compose -f docker-compose.prod.yml restart'"
echo -e "  Stop    : ssh ${DEPLOY_USER}@${DEPLOY_HOST} 'cd /opt/opspilot && sudo docker compose -f docker-compose.prod.yml down'"
echo ""
