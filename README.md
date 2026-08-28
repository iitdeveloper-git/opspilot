# OpsPilot 🚀

> **The Open-Source AI Infrastructure Copilot & Command Center**  
> *Monitor. Understand. Act. Automate.*

[![License: Apache 2.0](https://img.shields.io/badge/License-Apache_2.0-blue.svg)](https://opensource.org/licenses/Apache-2.0)
[![Python 3.11+](https://img.shields.io/badge/python-3.11+-blue.svg)](https://www.python.org/downloads/)
[![ChatOps: Telegram](https://img.shields.io/badge/ChatOps-Telegram-2CA5E0?logo=telegram)](https://telegram.org/)
[![Docker](https://img.shields.io/badge/Docker-Ready-2496ED?logo=docker)](https://www.docker.com/)

**OpsPilot** is an open-source, AI-powered infrastructure command center. It monitors your servers, analyzes incidents using pluggable LLMs (OpenAI, Claude, Gemini, or local Ollama), and allows you to safely operate containers and inspect logs directly from **Telegram**.

---

## ✨ Features (v0.1.0)

- **🔭 OBSERVE**: Real-time CPU, RAM, Disk, Load Average, Docker container health, and SSL certificate expiration checks.
- **🤖 UNDERSTAND (AI Copilot)**: Pluggable AI engine for error log summarization and natural language questions (`/ask why is the API slow?`). Supports OpenAI, Anthropic Claude, Google Gemini, and local Ollama models.
- **⚡ ACT (ChatOps)**: Safe, interactive commands via Telegram (`/status`, `/ps`, `/logs`, `/restart`, `/ask`) with interactive confirmation buttons.
- **🛡️ ZERO-SHELL SECURITY**: Strictly forbids arbitrary `shell=True` commands. All actions pass through **User ID Allowlist → Safe Deterministic Executor → Audit Trail**.
- **🧹 AUTOMATION**: Self-healing loops with opt-in Docker cache pruning when disk space exceeds a configurable threshold.

---

## 🏗️ Architecture

```
                    ┌─────────────────────────┐
                    │        OpsPilot         │
                    │      Command Center     │
                    └────────────┬────────────┘
                                 │
       ┌─────────────┬───────────┼───────────┬─────────────┐
       ▼             ▼           ▼           ▼             ▼
  1. OBSERVE    2. UNDERSTAND  3. ACT    4. AUTOMATE   5. AUDIT
  Telemetry      AI Reasoning   ChatOps   Auto-Prune   JSONL Trail
       │             │           │           │             │
  CPU/RAM/Disk   Log Summary  /status     (opt-in)     Action Log
  Docker Health  "/ask" Q&A   /restart    Disk prune
  SSL Expiry                  /logs
```

---

## 🚀 Quickstart

### 1. Installation

```bash
git clone https://github.com/iitdeveloper-git/opspilot.git
cd opspilot

# Install with UV or pip
pip install -e ".[ai]"
```

### 2. Configuration

Copy `.env.example` and `config.example.yaml`:

```bash
cp .env.example .env
cp config.example.yaml config.yaml
```

Set your **Telegram Bot Token** and **Your Telegram User ID** in `.env`:
```env
TELEGRAM_BOT_TOKEN="123456789:ABCdefGHIjklMNOpqrSTUvwxYZ"
TELEGRAM_ALLOWED_USER_IDS="YOUR_TELEGRAM_USER_ID"
OPSPILOT_AUTH_MODE=production   # Always production in real deployments
```

> **Security note**: `TELEGRAM_ALLOWED_USER_IDS` is required in production. If it is empty and `OPSPILOT_AUTH_MODE` is not set to `development`, OpsPilot will deny all incoming requests (fail-closed). Find your Telegram User ID via [@userinfobot](https://t.me/userinfobot).

### 3. Run OpsPilot

```bash
# Via CLI
opspilot status
opspilot ps

# Run the 24/7 background daemon
opspilot start
```

---

## 🐳 Docker Deployment

**Development** (builds locally):
```bash
docker compose up -d
```

**Production** (uses pre-built GHCR image):
```bash
IMAGE_TAG=v0.1.0 docker compose -f docker-compose.prod.yml up -d
```

---

## 📱 Telegram Command Reference

| Command | Action |
|---|---|
| `/status` | Live CPU, Memory, Disk free/used, and system load. |
| `/ps` | Lists active Docker containers and their health checks. |
| `/logs <container> [N]` | Tails the last *N* log lines in chat. |
| `/restart <container>` | Prompts for confirmation and safely restarts the service. |
| `/ask <query>` | Asks OpsPilot AI to diagnose problems using live telemetry. |

---

## 🗺️ Roadmap

The following features are planned and in active development:

- `/backup` — On-demand and scheduled database backups
- `/deploy` — Trigger deployments from Telegram
- **Root Cause Analysis (RCA)** — Full incident workflow with AI-driven root cause analysis
- **Role-Based Access Control (RBAC)** — Per-user role authorization beyond a simple allowlist
- **Immutable Audit Trail** — Append-only, tamper-evident audit log storage
- **HTTP endpoint monitoring** — Active health checks for configured endpoints

---

## 🔒 Security Model

- **Fail-closed auth**: An empty `TELEGRAM_ALLOWED_USER_IDS` denies all users in production mode.
- **No shell injection**: All operations go through `SafeOperationExecutor`, which never executes arbitrary shell strings.
- **Docker socket trust boundary**: The Docker socket provides host-level access. See [SECURITY.md](SECURITY.md) for the full security model.

---

## 📄 License

OpsPilot is licensed under the [Apache License 2.0](LICENSE).  
Maintained with ❤️ by **[IITDEVELOPER](https://iitdeveloper.com)**.
