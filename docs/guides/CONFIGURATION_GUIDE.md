# OpsPilot 2.0 Configuration Guide

OpsPilot supports configuration through environment variables (or `.env` files) as well as the optional `config.yaml` file.

## Core Variables

| Variable | Default | Purpose |
|---|---|---|
| `SERVER_NAME` | `node-01` | Human-readable identifier displayed in alerts and Web UI |
| `OPSPILOT_ADMIN_PASSWORD` | Required | Master password for Web Command Center login |
| `OPSPILOT_WEB_PORT` | `8080` | HTTP listening port for Web Command Center |
| `MONITORING_INTERVAL_SECONDS` | `60` | Polling frequency for probes and container status |
| `CPU_THRESHOLD_PERCENT` | `85.0` | CPU alarm threshold |
| `RAM_THRESHOLD_PERCENT` | `90.0` | Memory usage alarm threshold |
| `DISK_THRESHOLD_PERCENT` | `90.0` | Disk usage alarm threshold |
| `AUTO_PRUNE_ENABLED` | `true` | Automated Docker cache cleaning |

## Telegram Bot Integration (Optional)

| Variable | Description |
|---|---|
| `TELEGRAM_BOT_TOKEN` | BotFather API Token |
| `TELEGRAM_ALERT_CHAT_ID` | Telegram Group or Channel ID for automated notifications |
| `TELEGRAM_ALLOWED_USERS` | Comma-delimited list of Telegram user IDs authorized for bot commands |

## AI Diagnostic Copilot (Optional)

| Variable | Description |
|---|---|
| `AI_PROVIDER` | `gemini`, `groq`, `openai`, `deepseek`, or `ollama` |
| `AI_API_KEY` | Provider API authentication key |
| `AI_MODEL_NAME` | Target model (e.g. `gemini-1.5-flash`, `gpt-4o-mini`, `llama3`) |
| `AI_ENDPOINT` | Custom base URL (e.g. for self-hosted Ollama) |
