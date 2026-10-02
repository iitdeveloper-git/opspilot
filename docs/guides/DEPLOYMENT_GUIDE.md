# OpsPilot 2.0 Deployment Guide

## 1. Quick Start via Docker Compose (Recommended)

OpsPilot is packaged as a lightweight Docker container configured to monitor the host system and Docker daemon.

```bash
# Clone the repository
git clone https://github.com/iitdeveloper-git/opspilot.git
cd opspilot

# Configure environment
cp sample.env .env
# Edit .env with your ADMIN_PASSWORD and optional TELEGRAM tokens

# Build and start via Makefile or Docker Compose
make docker-up
```

OpsPilot Command Center is now accessible at `http://localhost:8080`.

## 2. Direct VPS Manual Deployment

To sync and build directly on a remote VPS without CI dependencies:

1. Configure `.deploy.env`:
   ```bash
   DEPLOY_HOST=149.56.101.2
   DEPLOY_USER=ubuntu
   SSH_KEY_PATH=~/.ssh/id_rsa
   ```
2. Run deployment:
   ```bash
   make deploy
   # or ./deploy_manual.sh
   ```

## 3. Systemd Unit Installation

To run OpsPilot natively as a host system daemon:

```bash
sudo cp deploy/systemd/opspilot.service /etc/systemd/system/
sudo systemctl daemon-reload
sudo systemctl enable --now opspilot.service
sudo systemctl status opspilot.service
```

## 4. Production Caddy Reverse Proxy

Deploy `deploy/caddy/Caddyfile.example` to automate Let's Encrypt TLS certificates:

```bash
caddy reload --config /etc/caddy/Caddyfile
```
