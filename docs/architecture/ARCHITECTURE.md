# OpsPilot 2.0 System Architecture

## 1. High-Level Architectural Overview

OpsPilot 2.0 is an autonomous infrastructure monitoring, ChatOps, and billing renewal management command center designed as a **modular monolith**. It bridges proactive hardware/service observability with conversational ChatOps and a web-based command center.

```text
┌────────────────────────────────────────────────────────────────────────┐
│                        OpsPilot 2.0 Core Runtime                       │
│                                                                        │
│  ┌────────────────────────┐         ┌───────────────────────────────┐  │
│  │   FastAPI Web Engine   │         │     aiogram 3.x Telegram      │  │
│  │   Command Center (SPA) │         │     ChatOps & Interactive FSM │  │
│  └───────────┬────────────┘         └───────────────┬───────────────┘  │
│              │                                      │                  │
│              ▼                                      ▼                  │
│  ┌──────────────────────────────────────────────────────────────────┐  │
│  │                     Domain Service Mesh Layer                    │  │
│  │  - Probe Manager          - Snooze & Ignored Engine              │  │
│  │  - Renewals Registry      - Alert Routing Matrix                 │  │
│  │  - Non-blocking Executor  - AI RCA & Copilot Engine              │  │
│  └──────────────────────────────────┬───────────────────────────────┘  │
│                                     │                                  │
│                                     ▼                                  │
│  ┌──────────────────────────────────────────────────────────────────┐  │
│  │                    Persistence & Telemetry                       │  │
│  │  - SQLite (WAL mode)      - Docker Engine API                    │  │
│  │  - JSON Audit Ledger      - Linux procfs / psutil Telemetry      │  │
│  └──────────────────────────────────────────────────────────────────┘  │
└────────────────────────────────────────────────────────────────────────┘
```

## 2. Directory Layout & Standards

Aligned with the Sendrin (`ett_gns`) standard:
- `deploy/`: Centralized deployment descriptors for Docker, Systemd, Caddy, and sync scripts.
- `docs/`: System documentation organized into `architecture/`, `api/`, and `guides/`.
- `docs-internal/`: Internal roadmap and architectural decision records.
- `scripts/`: Operational tools for database maintenance, backups, and OpenAPI exports.
- `src/opspilot/`: Modularized domain packages with clean separation of concerns.

## 3. Subsystem Breakdown

1. **`monitor/`**: Collects system metrics (CPU, RAM, load averages), inspects Docker container states, and performs asynchronous HTTP/SSL probes.
2. **`automation/`**: Background scheduler coordinating probe intervals, certificate expiration checks, and automated Docker cache pruning.
3. **`ai/`**: Vendor-agnostic AI provider layer (supporting Gemini, Groq, OpenAI, DeepSeek, and Ollama) with dedicated Root Cause Analysis (RCA).
4. **`web/`**: Hardened FastAPI web interface featuring session authentication, CSRF tokens, real-time KPI metrics, and an ultra-modern glassmorphic command center.
5. **`chatops/`**: Telegram bot with conversational commands, callback query routers, and interactive confirmation flows.
