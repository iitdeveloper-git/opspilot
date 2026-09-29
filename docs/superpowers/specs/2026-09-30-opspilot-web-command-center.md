# OpsPilot Web Command Center — Design Spec

**Document ID**: SPEC-OPSPILOT-WEB-001
**Status**: REVISED DRAFT (awaiting maintainer approval — no implementation until approved)
**Target versions**: v0.4.0 (core console) and v0.4.1 (live stream and settings)
**Author**: OpsPilot maintainers
**Date**: 2026-09-30
**Revision**: 2 — incorporates security and architecture review

> This is a public repository. Examples, fixtures and screenshots must use placeholder data (`example.com`, "Acme VPS", "Client A"). Never commit real client names, amounts or hostnames.

---

## 1. Summary and goals

OpsPilot v0.3.0 keeps runtime state in SQLite (`data/opspilot.db`): HTTP probes, renewals and billing reminders, incidents, container snoozes, and settings. Everything is currently managed through Telegram.

The **Web Command Center** is an optional, integrated web console for the same data and actions, for operators who prefer a screen to a chat. It must not duplicate ChatOps logic and must not weaken the security of the host.

**Success criteria**
- A self-hoster can enable it with three settings, put it behind their reverse proxy, log in, and restart a container, add a probe and mark a renewal paid.
- With the feature off (default), nothing listens on any port and no web dependency is imported.
- Every web action is audited exactly like a Telegram action.

**Non-goals (v0.4.x)**: multiple users or roles, editing thresholds or intervals, editing YAML, metrics history and charts, mobile app.

---

## 2. Security and architecture constraints

### 2.1 Exposure model
- **Disabled by default.** `OPSPILOT_WEB_ENABLED=false`. When false, no listener starts and `opspilot.web` is never imported.
- **Loopback bind by default.** `OPSPILOT_WEB_HOST=127.0.0.1`, `OPSPILOT_WEB_PORT=8088`. OpsPilot never binds `0.0.0.0` unless the operator sets it explicitly; doing so logs a prominent warning at startup.
- **TLS terminates at the reverse proxy** (Caddy in the reference setup). HSTS is set by the proxy.
- **Proxy connectivity — must be verified before implementation.** The reference deployment runs OpsPilot with `network_mode: host` and Caddy as a separate container. A container on a Docker bridge network cannot reach the host's `127.0.0.1`. The docs must give one supported recipe, chosen after checking how the reference Caddy container is attached:
  1. Caddy on the host network (`127.0.0.1:8088` works as is), or
  2. Bind `OPSPILOT_WEB_HOST` to the Docker bridge gateway address (e.g. `172.17.0.1`) and proxy to it, or
  3. A Unix socket shared with the proxy.

  Whichever is chosen, the docs must include a copy-paste Caddyfile snippet using placeholder domains.

### 2.2 Authentication
- **Single admin account** in v0.4.x. Roles and multi-user are out of scope.
- **Startup enforcement.** If the web UI is enabled and no credential is configured, or the password is shorter than 12 characters or only whitespace, OpsPilot logs a fatal error and does **not** start the web server. The bot and scheduler continue to run.
- **Credential sources**, in order: `OPSPILOT_ADMIN_PASSWORD_HASH` (a PBKDF2-SHA256 hash produced by a documented `opspilot web hash-password` helper, stdlib only), then `OPSPILOT_ADMIN_PASSWORD` (plaintext, allowed but discouraged in docs). Comparison is constant-time (`hmac.compare_digest` over equal-length digests).
- **Login throttling.** Failed logins are tracked per client IP and slowed with exponential backoff (1s, 2s, 4s … capped at 5 min), with HTTP 429 after 5 failures within 15 minutes. State is in memory.
- **Client IP** is taken from `X-Forwarded-For` **only** when the TCP peer is a configured trusted proxy (`OPSPILOT_WEB_TRUSTED_PROXIES`, default `127.0.0.1`); uvicorn `forwarded_allow_ips` is set to the same value. Otherwise the peer address is used. This prevents both spoofing and a single shared "proxy IP" locking everyone out.

### 2.3 Sessions
- Server-side, in-memory session table keyed by a random 256-bit token. Cookie carries only the token.
- Cookie flags: `HttpOnly`, `Secure`, `SameSite=Strict`, `Path=/`. Rolling TTL of 8 hours with an absolute maximum of 24 hours.
- **Logout invalidates the session server-side.** Sessions are lost on restart (users log in again); this is acceptable and documented.
- Development override `OPSPILOT_WEB_INSECURE_COOKIES=true` drops `Secure` for plain-HTTP local use; it logs a warning and is documented as dev-only.

### 2.4 CSRF and request hardening
- State-changing methods (`POST`, `PUT`, `PATCH`, `DELETE`) require:
  - a valid session, and
  - the header `X-CSRF-Token` matching a per-session token (issued by `GET /api/auth/session`, never placed in a cookie the page cannot read), and
  - an `Origin` (or `Referer`) header matching the request host.
- Failures return 403 and are audited.
- **Security headers on every response:** `Content-Security-Policy: default-src 'self'; script-src 'self'; style-src 'self'; frame-ancestors 'none'; base-uri 'none'`, `X-Content-Type-Options: nosniff`, `Referrer-Policy: no-referrer`, `Cache-Control: no-store` on API responses. This requires all JS and CSS to be external files with **no inline scripts or styles**.
- **Input validation** on every request body via typed models:
  - probe: `name` 1–80 chars; `url` must be `http`/`https`; `expected_status` 100–599; `timeout_seconds` 1–60.
  - renewal: `name` 1–120; `category` and `recurrence` from fixed enums; `due_date` ISO date; `amount` ≥ 0 or null; `remind_days_before` 0–365.
  - snooze duration from a fixed set: `1h`, `24h`, `7d`, `forever`.

### 2.5 Failure isolation
- Web dependencies live in the optional extra `opspilot[web]` (`fastapi`, `uvicorn`, plus stdlib for hashing and sessions). Core installs stay small.
- The web server runs as an independent `asyncio.Task` inside `run_daemon`. If it fails to start or crashes, an error is logged and the scheduler and Telegram bot keep running. It is never combined with them in a fail-together `gather`.
- Uvicorn signal handling is disabled; the daemon owns shutdown and stops the web task cleanly.
- If the operator enables the web UI but `opspilot[web]` is not installed, OpsPilot logs a clear "install `opspilot[web]`" error and continues without it.

### 2.6 No logic duplication and safe execution
- The web layer is a thin adapter. It calls the same functions the bot uses:
  - `opspilot.db.*` (`endpoints`, `renewals`, `incidents`, `snooze`, `store`)
  - `opspilot.core.executor.SafeOperationExecutor` (`restart_container`, `get_container_logs`)
- **Shared service layer.** Business rules that today live inside `bot.py` handlers (duration parsing, snooze until-date calculation, mark-paid and its result, channel update) move into `opspilot/services/` and are called by both the bot and the web routes. This refactor is part of v0.4.0, with the existing bot tests as the safety net.
- **Container names** received from the client are validated against the live container list before reaching the executor. Log requests cap `tail` at 500 lines and the response at 256 KB, with a timeout.
- **Audit trail.** Every restart, snooze, probe change, renewal change and login (success or failure) is recorded via `AuditLogger.record_action` with `user_id="web-admin"` and the client IP in `details`. CSRF and auth failures are recorded as `BLOCKED`.
- **XSS.** Container output and all user-supplied strings are rendered with `textContent` only; static JS must never use `innerHTML`, `outerHTML` or `insertAdjacentHTML` (enforced by a test).
- **SSRF note.** Probes make outbound requests to operator-supplied URLs, including internal addresses. This is by design for internal monitoring, is documented in the security section, and is limited to the authenticated admin.

---

## 3. Data flow: snapshot cache

The console needs container stats, system metrics and probe results that are not stored today (the scheduler records only failures as incidents).

Introduce `opspilot/core/snapshot.py`: a small in-memory `SnapshotCache` written by the scheduler loops and read by the web layer.

| Snapshot | Written by | Contents |
|---|---|---|
| `system` | health loop | CPU %, RAM %, disk %, load, uptime, timestamp |
| `containers` | health loop | name, image, status, health, ports, timestamp |
| `probes` | probe loop | per endpoint: last status code, latency ms, last checked, last error |

- Web GETs read the cache; they never trigger Docker or network calls themselves. This keeps the console cheap and prevents one open tab per operator from multiplying `docker` calls.
- Per-container CPU and memory figures require `docker stats`, which is expensive across dozens of containers. **v0.4.0 shows status and health only.** CPU/memory per container is deferred until measured.
- Cache values carry a timestamp; the UI shows "updated N s ago" and greys out stale data.
- v0.4.1's WebSocket simply pushes the cache every 5 seconds.

---

## 4. Scope and phases

### Phase 1 — v0.4.0 core console
1. **Login and session gate** (§2.2–2.4), logout.
2. **Overview:** CPU, RAM, disk, uptime, open-incident count, containers healthy/total.
3. **Containers:** table of containers with status and health; **Restart** (with confirmation dialog), **Logs** modal (read-only, `textContent`), **Snooze**.
4. **Probes:** status, code, latency, last checked; add, enable/disable, remove.
5. **Renewals:** list (pending, overdue, paid), add, **Mark paid**, **Snooze**.
6. **Incidents:** open and recent resolved, paginated.
7. **Shared service layer** refactor (§2.6) and **snapshot cache** (§3).
8. **Removal semantics unified** (§5).

### Phase 2 — v0.4.1 live stream and settings
1. `/ws/live`: authenticated by session cookie, `Origin` validated, pushes cached snapshots every 5 s, closes on logout or session expiry.
2. **Settings screen limited to the alert chat ID.** It calls the same service as `/setchat` (persist to DB and update the live channel object). Thresholds, intervals and YAML-owned settings are explicitly out of scope, to avoid reopening the config-versus-DB precedence question.

---

## 5. Removal semantics (Telegram and web must match)

Today `/rmprobe` hard-deletes a row, and YAML seeding re-inserts it on the next restart. To fix this and give the web UI one consistent behaviour:

- Add a `deleted` flag to `endpoints` (migration via `ALTER TABLE`, default 0). "Remove" sets `deleted=1`; the row is kept as a tombstone so YAML seeding (`INSERT OR IGNORE` on the unique name) can never resurrect it.
- `list_endpoints` excludes deleted rows everywhere. The web `DELETE /api/probes/{id}` and Telegram `/rmprobe` both call the same `remove_endpoint` service.
- Re-adding a probe with the same name un-deletes and updates the row.
- The same tombstone approach applies to renewals from `yaml_seed` if a delete is later exposed; the web API does not expose renewal deletion in v0.4.0.

---

## 6. API (v0.4.0)

All `/api/*` routes require a session. Mutations also require CSRF (§2.4). Errors use `{ "error": "<code>", "message": "<text>" }`. List endpoints accept `limit` (max 100) and `offset`.

| Endpoint | Method | Notes |
|---|---|---|
| `/login` | GET | Static login page |
| `/api/auth/login` | POST | Throttled; sets session cookie |
| `/api/auth/logout` | POST | Invalidates session |
| `/api/auth/session` | GET | Returns `{ csrf_token, server_name, expires_at }` |
| `/api/overview` | GET | Reads `system` snapshot plus counts |
| `/api/containers` | GET | Reads `containers` snapshot |
| `/api/containers/{name}/restart` | POST | Validated name; executor; audited |
| `/api/containers/{name}/logs` | GET | `tail` ≤ 500; size and time capped |
| `/api/containers/{name}/snooze` | POST | Body `{duration}` from fixed set |
| `/api/probes` | GET | Endpoint rows merged with `probes` snapshot |
| `/api/probes` | POST | Validated body; add or un-delete |
| `/api/probes/{id}/toggle` | POST | Enable or disable |
| `/api/probes/{id}` | DELETE | Tombstone (§5) |
| `/api/renewals` | GET | Filter by `status` |
| `/api/renewals` | POST | Validated body |
| `/api/renewals/{id}/pay` | POST | 409 if not `pending` (prevents double-click effects); returns next renewal if recurring |
| `/api/renewals/{id}/snooze` | POST | Body `{duration}` from fixed set |
| `/api/incidents` | GET | `state=open|resolved|all`, paginated |
| `/healthz` | GET | Unauthenticated, returns `{ "status": "ok" }` only, for proxy health checks |

---

## 7. UI and UX

### 7.1 Visual language
Dark theme with glass-style surfaces, defined as CSS custom properties: background `#0a0d14`, surface `#121722`, card `rgba(22, 28, 45, 0.7)`, border `1px solid rgba(255,255,255,0.08)`, blur `12px`. Accents: success `#10b981`, warning `#f59e0b`, critical `#ef4444`, primary `#6366f1`. System sans-serif stack; tabular numerals for latency and timestamps. No external fonts or CDN assets (required by the CSP and for offline installs).

### 7.2 Layout
Header (server name badge, data freshness indicator, open-alert count, logout), pill navigation (Overview, Containers, Probes, Renewals, Incidents), responsive down to phone width.

### 7.3 States and accessibility
- Every screen defines **loading** (skeleton), **empty** (with the primary action), **error** (toast plus inline retry) and **stale-data** states.
- Destructive actions (Restart, Remove probe) use a confirmation dialog.
- Modals trap focus, close on Escape, and return focus to the trigger.
- Keyboard-operable controls, visible focus rings, WCAG AA contrast, `prefers-reduced-motion` respected.
- Status is never conveyed by colour alone (icon and text label included).

---

## 8. Configuration summary

| Setting (env) | Default | Purpose |
|---|---|---|
| `OPSPILOT_WEB_ENABLED` | `false` | Master switch |
| `OPSPILOT_WEB_HOST` | `127.0.0.1` | Bind address |
| `OPSPILOT_WEB_PORT` | `8088` | Port |
| `OPSPILOT_ADMIN_PASSWORD_HASH` | unset | Preferred credential |
| `OPSPILOT_ADMIN_PASSWORD` | unset | Plaintext alternative, min 12 chars |
| `OPSPILOT_WEB_TRUSTED_PROXIES` | `127.0.0.1` | Peers allowed to set `X-Forwarded-For` |
| `OPSPILOT_WEB_INSECURE_COOKIES` | `false` | Dev only; drops `Secure` |

`.env.example` and the README get a "Web console" section with the Caddy recipe (§2.1), the hash helper, and the security notes.

---

## 9. Testing and quality gates

1. **Security** (`tests/test_web_security.py`): disabled by default (no listener, no import); refuses to start on missing or weak password while bot and scheduler keep running; throttling and 429; trusted-proxy IP handling (spoofed header from untrusted peer ignored); cookie flags; CSRF and Origin rejection on every mutating route; security headers present; logout invalidates the session; `/healthz` leaks nothing.
2. **API** (`tests/test_web_api.py`): each route against a temporary SQLite DB and a fake executor; validation errors; 409 on double pay; container name not in list rejected; log caps; audit entries with `web-admin` and IP.
3. **Shared services:** existing bot tests still pass after the refactor; new service tests cover snooze, pay and channel update.
4. **Static checks:** a test that fails if any shipped JS uses `innerHTML`, `outerHTML`, `insertAdjacentHTML` or inline `<script>`.
5. **Removal semantics:** removing a YAML-seeded probe and re-running the seed does not resurrect it.
6. **Tooling:** `ruff check .` and `ruff format --check .` clean; full suite green before merge.
7. **Reviews before merge:** run the architecture review and the UI/UX review on the finished branch.

---

## 10. Risks and open items

| Item | Status |
|---|---|
| Caddy-to-OpsPilot connectivity on the reference VPS | **Verify before planning** (§2.1) |
| Per-container CPU/memory | Deferred until cost is measured |
| Memory footprint of FastAPI + uvicorn | Measure; make no numeric claim in docs until measured |
| Multi-user and roles | Out of scope; revisit after v0.4.1 |
| Sessions lost on restart | Accepted and documented |
| Probe SSRF to internal addresses | Documented; admin-only |
