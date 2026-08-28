# OpsPilot 🚀

> **The Open-Source AI Infrastructure Copilot & Command Center**  
> *Monitor. Understand. Act. Automate.*

[![License: Apache 2.0](https://img.shields.io/badge/License-Apache_2.0-blue.svg)](https://opensource.org/licenses/Apache-2.0)
[![Python 3.11+](https://img.shields.io/badge/python-3.11+-blue.svg)](https://www.python.org/downloads/)
[![ChatOps: Telegram](https://img.shields.io/badge/ChatOps-Telegram-2CA5E0?logo=telegram)](https://telegram.org/)
[![Docker](https://img.shields.io/badge/Docker-Ready-2496ED?logo=docker)](https://www.docker.com/)

**OpsPilot** is an open-source, AI-powered infrastructure command center. It monitors your servers, analyzes incidents using pluggable LLMs (OpenAI, Claude, Gemini, or local Ollama), and allows you to safely operate containers, inspect logs, run backups, and deploy directly from **Telegram** or your preferred ChatOps channel.

---

## ✨ Features

- **🔭 OBSERVE**: Real-time CPU, RAM, Disk, Load Average, Docker container health, and SSL certificate expiration checks.
- **🤖 UNDERSTAND (AI Copilot)**: Pluggable AI engine for **Root Cause Analysis (RCA)**, error log summarization, and natural language questions (`/ask why is the API failing?`). Supports OpenAI, Anthropic Claude, Google Gemini, and private **local Ollama models**.
- **⚡ ACT (ChatOps)**: Safe, interactive commands via Telegram (`/status`, `/ps`, `/logs`, `/restart`, `/backup`, `/deploy`) with **interactive confirmation buttons**.
- **🛡️ ZERO-SHELL SECURITY**: Strictly forbids arbitrary `shell=True` commands. All actions pass through strict **User ID Allowlist -> Role Authorization -> Safe Deterministic Executor -> Audit Trail**.
- **🧹 AUTONOMOUS AUTOMATION**: Self-healing loops with automated Docker cache pruning when disk space exceeds threshold (e.g. 85%).

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
  Telemetry      AI Reasoning   ChatOps   Autonomous   JSONL Trail
       │             │           │           │             │
  CPU/RAM/Disk   Root Cause   /status     Auto Prune   Immutable
  Docker Health  Log Summary  /restart    Backups      Action Log
  SSL Expiry     "/ask" Q&A   /logs       Recovery
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
AI_PROVIDER="openai" # or "ollama" for 100% private local LLM
```

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

```bash
docker compose up -d
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

## 📄 License

OpsPilot is licensed under the [Apache License 2.0](LICENSE).  
Maintained with ❤️ by **[IITDEVELOPER](https://iitdeveloper.com)**.
