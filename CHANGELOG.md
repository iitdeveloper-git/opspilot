# Changelog

All notable changes to OpsPilot are documented here.
The format follows [Keep a Changelog](https://keepachangelog.com/en/1.1.0/) and the project uses [Semantic Versioning](https://semver.org/).

## [0.3.0] - 2026-09-30

### Added
- **SQLite persistence** (`data/opspilot.db`, WAL mode) for runtime state; configurable with `db_path`.
- **Renewal tracker:** `/renewals`, guided `/addrenew`, categories, reminders that repeat daily until marked paid, snooze (24h / 7d), and **monthly / yearly recurrence** that rolls the due date forward on *Mark Paid* (month-end dates clamp, e.g. Jan 31 → Feb 28).
- **HTTP probes managed at runtime:** `/probes`, guided `/addprobe`, `/rmprobe` — no redeploy needed.
- **Incidents:** deduplicated per source and target, auto-resolved with a recovery alert, snoozable, `/incidents` with pagination, resolved incidents pruned after 30 days.
- **`/setchat`** changes the alert destination live and persists across restarts; **`/settings`** shows runtime settings.
- **`NotificationChannel` interface** with a Telegram implementation, so new alert channels can be added without touching the scheduler.
- Rich HTML alert templates and new inline keyboards (renewal, probe, snooze, pagination).
- `initial_renewals` and `db_path` in `config.yaml`.
- Test coverage for the DB layer, scheduler loops, bot conversation flows and startup migration.
- `CHANGELOG.md`, expanded `CONTRIBUTING.md`, issue and pull request templates.

### Changed
- Container snooze moved from a JSON file to a database table.
- SSL expiry alerts now go through incidents: one alert per expiring domain instead of one per monitoring cycle.
- Blocking Docker, system and SSL checks now run in worker threads so they no longer stall the bot.
- Scheduler errors are logged instead of silently discarded.
- YAML `http_endpoints` and `initial_renewals` are seeded into the database on startup; the database is then the source of truth.
- README rewritten.

### Migration notes
- **Add a `./data:/app/data` volume** to your compose file. Without it, state is lost when the container is replaced (the bundled compose files already include it).
- `audit_logs/ignored_containers.json` is imported into the database once on first start and renamed to `ignored_containers.json.migrated`.

## [0.2.0] — 2026-08-29

### Added
- Interactive alert buttons on container alerts: **Logs**, **Restart**, **Ignore**.
- Time-based snooze / ignore for container health alerts (`/ignore <name> [1h|24h|7d|forever]`), stored persistently.
- `deploy_manual.sh` syncs source directly with rsync and builds on the host.

### Fixed
- Placeholder alert chat IDs are ignored instead of causing send errors.
- `ssl_domains` in the example config defaults to an empty list.
- Telegram command arguments parsed with aiogram's `CommandObject`; the AI API key is validated before querying.
- Authorization implemented as a proper aiogram 3 middleware.

## [0.1.0] — 2026-08-29

### Added
- Initial release: CPU, RAM, disk and Docker health monitoring; SSL expiry checks.
- Telegram ChatOps (`/status`, `/ps`, `/logs`, `/restart`, `/ask`) with confirmation buttons.
- Pluggable AI copilot (OpenAI, Anthropic, Gemini, Ollama).
- Zero-shell `SafeOperationExecutor`, user-ID allowlist (fail-closed) and JSONL audit trail.
- Opt-in Docker disk auto-prune.
- CI test workflow and GHCR Docker release workflow.

[0.3.0]: https://github.com/iitdeveloper-git/opspilot/compare/v0.2.0...v0.3.0
[0.2.0]: https://github.com/iitdeveloper-git/opspilot/releases/tag/v0.2.0
