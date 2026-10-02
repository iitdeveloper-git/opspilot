# Agent Workspace Rules & Execution Guidelines — OpsPilot

This is the canonical, tool-neutral entry point for AI coding agents and engineers working in the **OpsPilot** repository. OpsPilot is an autonomous infrastructure monitoring, ChatOps, and billing renewal management command center.

---

## 1. Architectural Philosophy & Folder Design Pattern

OpsPilot adheres to a **modular monolith** layout patterned after Sendrin (`ett_gns`):

```text
opspilot/
├── deploy/                      # All deployment configurations and manifests
│   ├── docker/                  # Dockerfiles and Docker Compose files
│   ├── scripts/                 # Manual and automated deployment scripts
│   ├── systemd/                 # Systemd unit service descriptors
│   └── caddy/                   # Reverse proxy and SSL configurations
├── docs/                        # Architecture, API specifications, and operational guides
│   ├── architecture/            # High-level and low-level architectural specs
│   ├── api/                     # OpenAPI, REST endpoint specs and payloads
│   └── guides/                  # Deployment, onboarding, and configuration runbooks
├── docs-internal/               # Internal plans, roadmaps, and decision records
│   ├── plans/                   # Milestone roadmaps and task breakdowns
│   └── strategy/                # Architectural Decision Records (ADRs)
├── scripts/                     # Developer and DevOps operational automation
│   ├── backup_db.sh             # SQLite snapshot and backup automation
│   ├── dev.sh                   # Local development runner
│   ├── export_openapi.py        # OpenAPI JSON / YAML exporter
│   └── healthcheck.sh           # Node and probe validation
├── src/opspilot/                # Application source code
│   ├── ai/                      # Copilot, provider abstraction, and RCA engine
│   ├── automation/              # Cron scheduler, auto-prune, and background tasks
│   ├── channels/                # Multi-channel notification dispatchers (Telegram, etc.)
│   ├── chatops/                 # Interactive bot handlers, FSM, and inline keyboards
│   ├── core/                    # Non-blocking executor, security, and audit ledger
│   ├── db/                      # SQLite persistence (incidents, probes, renewals, routes)
│   ├── monitor/                 # System telemetry, Docker status collector, HTTP/SSL probes
│   ├── web/                     # Web Command Center (FastAPI, static UI assets)
│   ├── cli.py                   # Command-line interface
│   ├── config.py                # Pydantic v2 settings management
│   └── main.py                  # Service bootstrap orchestrator
└── tests/                       # Comprehensive pytest suite
```

---

## 2. Core Engineering Standards

1. **Defensive Coding & Type Annotations**:
   - Python 3.12+ type hints are mandatory (`from __future__ import annotations`).
   - Use Pydantic v2 for configuration and payload validation.

2. **Non-blocking Operations**:
   - Never execute blocking I/O (e.g., synchronous `subprocess.run`, socket operations) inside async request loops. Use `asyncio.to_thread` or the non-blocking `CommandExecutor`.

3. **Security & Zero-Secrets**:
   - Secrets must never be logged, hardcoded, or committed.
   - Master admin password must be verified with salted hashing (`hashlib.pbkdf2_hmac` or `secrets.compare_digest`).
   - All state-changing web mutations require HTTP-only CSRF verification.

4. **Testing Gate**:
   - All tests must pass cleanly before any merge or deploy (`make test`).
   - New features must include unit or integration test coverage in `tests/`.

---

## 3. Web Command Center Standards

- **Aesthetics & UX**: OpsPilot Command Center follows a high-end glassmorphic dark theme with sleek gradients, micro-interactions, responsive sidebars, and real-time auto-refresh tickers.
- **Independence**: The Web Command Center must function reliably with or without Telegram bot configuration.
- **Safety**: Destructive actions (container restarts, docker pruning, route deletions) require explicit user confirmations and produce tamper-evident audit records.
