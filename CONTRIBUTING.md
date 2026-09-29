# Contributing to OpsPilot

Thanks for helping make OpsPilot better! Bug reports, docs fixes, new notification channels and feature ideas are all welcome.

## Ground rules

- **Security first.** OpsPilot can restart containers. Never add code that runs arbitrary shell strings; go through `SafeOperationExecutor`.
- **Keep it small.** OpsPilot is a single lightweight container. Prefer a small, well-tested change over a big framework.
- **Be kind.** Assume good intent and keep discussion constructive.

## Development setup

```bash
git clone https://github.com/iitdeveloper-git/opspilot.git
cd opspilot

python3 -m venv .venv
source .venv/bin/activate
pip install -e ".[ai,dev]"

pytest            # run the tests
ruff check src tests
```

Tests use a temporary SQLite database, so they never touch `data/opspilot.db` and don't need Docker or a Telegram token.

## Project layout

```text
src/opspilot/
├── monitor/      # Docker, system, SSL and HTTP probe collectors
├── automation/   # background scheduler (health, probes, renewals, maintenance)
├── chatops/      # Telegram bot, keyboards and message templates
├── channels/     # NotificationChannel interface + implementations
├── db/           # SQLite access (raw SQL via aiosqlite): renewals, incidents, endpoints, snooze, settings
├── core/         # security, audit trail, safe executor
└── ai/           # pluggable LLM provider and copilot
```

## Adding a notification channel

Alerts leave OpsPilot through a tiny interface in `src/opspilot/channels/base.py`. To add Slack, email, Discord or a webhook:

1. Create `src/opspilot/channels/<name>.py` with a class that implements `NotificationChannel` (see `channels/telegram.py` as the reference).
2. Keep secrets in environment variables and document them in `.env.example`.
3. Never let a send failure crash the scheduler: log and continue.
4. Add tests that use a fake transport (no real network calls).
5. Mention the channel in the README and `CHANGELOG.md`.

## Database changes

- Schema lives in `db/engine.py` (`init_db`). It must stay idempotent.
- Adding a column to an existing table needs a small migration (`ALTER TABLE`), because `CREATE TABLE IF NOT EXISTS` won't change existing databases.
- Store timestamps as UTC in `YYYY-MM-DD HH:MM:SS` so SQL comparisons with `datetime('now')` are correct.
- Add a test for every new query.

## Pull request checklist

1. Fork the repo and branch from `main`.
2. `pytest` passes and `ruff check` is clean.
3. New behaviour has tests; bug fixes include a test that fails without the fix.
4. Docs updated: `README.md` for user-facing changes, `CHANGELOG.md` under **Unreleased**.
5. Commit messages follow [Conventional Commits](https://www.conventionalcommits.org/) (`feat:`, `fix:`, `docs:`, `chore:`).
6. Open the PR with a clear description of the problem and how you tested it.

## Reporting bugs and vulnerabilities

- Bugs and feature requests: open an issue using the templates.
- **Security vulnerabilities:** do not open a public issue. Follow [SECURITY.md](SECURITY.md).
