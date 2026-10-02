# OpsPilot Internal Roadmap & Architectural Milestones

## Completed Milestones
- [x] v0.1.0: Core monitoring engine, Telegram ChatOps, Docker collector.
- [x] v0.2.0: Time-based container alert snooze & ignore registry.
- [x] v0.3.0: Dynamic SQLite-backed renewals, HTTP/SSL probes, and incident feed.
- [x] v0.4.0: High-performance Web Command Center with session auth and CSRF protection.
- [x] Sendrin Architecture Alignment: Standardized `deploy/`, `docs/`, `scripts/`, `Makefile`, and `AGENTS.md`.

## Upcoming Initiatives (v0.5.0+)
- [ ] Multi-Host Mesh: Single command center monitoring multiple nodes over secure agent tokens.
- [ ] Push Notifications: Web Push & In-App notification drawer using Sendrin (`gns`) adapter.
- [ ] Live Streaming Terminal: WebSocket-based interactive container log streamer.
- [ ] Automated Remediation Playbooks: Self-healing rules (e.g. restart on consecutive 502s).
