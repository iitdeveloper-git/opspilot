# OpsPilot Web Command Center Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Ship an optional, off-by-default web console (v0.4.0, then live stream + settings in v0.4.1) that manages the same containers, probes, renewals and incidents as the Telegram bot, plus a v0.3.1 hotfix for bugs found while planning.

**Architecture:** A thin FastAPI adapter (`opspilot.web`) over a shared service layer and the existing `db/*` and `SafeOperationExecutor`. The scheduler publishes system, container and probe data into an in-memory `SnapshotCache`; the web layer only reads the cache. Sessions are server-side and in memory; every mutation needs a session, a CSRF token and a matching `Origin`. The server runs as an isolated task inside `run_daemon`, loopback-bound, behind the operator's reverse proxy.

**Tech Stack:** Python 3.11+, FastAPI + uvicorn (optional extra `opspilot[web]`), aiosqlite, pydantic v2, vanilla HTML/CSS/JS (no build step, no CDN), pytest (`asyncio_mode = "auto"`), ruff, mypy.

**Spec:** `docs/superpowers/specs/2026-09-30-opspilot-web-command-center.md` (SPEC-OPSPILOT-WEB-001, revision 2). Read it before starting; this plan implements it and lists two small additions in Task 12.

## Global Constraints

- **Clean start:** begin from a clean committed tree (`git status` empty). Do not build on uncommitted edits from another session.
- Python `>=3.11`; ruff `line-length = 120`. Before every commit: `uv run ruff check src tests` and `uv run ruff format --check src tests` clean. Before Tasks 12/14: `uv run mypy src/` clean (CI runs it).
- Install for development: `uv pip install -e ".[ai,web,dev]"`. Tests that need FastAPI/uvicorn start with `pytest.importorskip(...)`.
- Web console **disabled by default**: `OPSPILOT_WEB_ENABLED=false`. When disabled no listener starts and `opspilot.web.app`/`fastapi`/`uvicorn` are never imported.
- Defaults: `OPSPILOT_WEB_HOST=127.0.0.1`, `OPSPILOT_WEB_PORT=8088`, `OPSPILOT_WEB_TRUSTED_PROXIES=127.0.0.1`, `OPSPILOT_WEB_INSECURE_COOKIES=false`.
- Credentials: `OPSPILOT_ADMIN_PASSWORD_HASH` (preferred) or `OPSPILOT_ADMIN_PASSWORD`; password minimum **12** characters, whitespace-only rejected. Weak or missing credentials: log a fatal error, do **not** start the web server, bot and scheduler keep running.
- Hash format has no `$` (docker compose interpolates `$` in env files): `pbkdf2_sha256:<iterations>:<salt_b64>:<hash_b64>`, PBKDF2-HMAC-SHA256, 600000 iterations by default.
- Session cookie `opspilot_session`: `HttpOnly`, `Secure` (unless `OPSPILOT_WEB_INSECURE_COOKIES=true`), `SameSite=Strict`, `Path=/`; rolling TTL **8 h**, absolute maximum **24 h**; server-side in-memory table; logout revokes.
- Login throttle: exponential backoff 1 s, 2 s, 4 s, 8 s … capped 300 s; after **5** failures within **15 min** return HTTP 429 with `Retry-After`.
- Client IP: `X-Forwarded-For` honoured only when the TCP peer is inside `OPSPILOT_WEB_TRUSTED_PROXIES`; rightmost non-trusted hop wins; malformed header falls back to the peer.
- Mutating methods (`POST`/`PUT`/`PATCH`/`DELETE`) require session + `X-CSRF-Token` + `Origin` (or `Referer`) whose host equals the request `Host`. Errors: 401 `unauthorized`, 403 `bad_origin` / `csrf`. Login also checks `Origin`.
- Every response carries `Content-Security-Policy: default-src 'self'; script-src 'self'; style-src 'self'; img-src 'self' data:; connect-src 'self'; frame-ancestors 'none'; base-uri 'none'; form-action 'self'`, `X-Content-Type-Options: nosniff`, `Referrer-Policy: no-referrer`; `/api/*`, `/`, `/login` also `Cache-Control: no-store`. **No inline scripts, no inline styles, no `style=` attributes, no `on*=` attributes.**
- Frontend never uses `innerHTML`, `outerHTML`, `insertAdjacentHTML`, `document.write` or `eval`; user and container text goes through `textContent`/`createTextNode`.
- Log endpoint: `tail` 1–500, response capped at 256 KB, 10 s timeout. Snooze durations from the fixed set `1h`, `24h`, `7d`, `forever` (the bot also keeps `1d`, `3d`). Renewals cannot be snoozed `forever`.
- API error body: `{"error": "<code>", "message": "<text>"}`. List endpoints: `limit` max 100, `offset` ≥ 0.
- Audit: every login (success/failure), restart, logs view, snooze, probe change, renewal change, and every auth/CSRF rejection is written through `AuditLogger.record_action` with `user_id="web-admin"` and `{"ip": ...}` in details.
- Public repository: fixtures, docs and screenshots use placeholders (`example.com`, "Acme VPS", "Client A"). Never commit real client names, amounts or hostnames.
- Every commit message ends with the trailer `Co-Authored-By: Claude Sonnet 5.5 <noreply@anthropic.com>`. Conventional Commit prefixes.
- **Do not** change `auth_mode` env handling in `config.py` (see Task 2 note). **Do not** push, tag or deploy without the maintainer's explicit OK.

## Review Focus

Failure modes the spec implies but that a straight reading of the tasks would not exercise; each has a test in the named task.

1. **Typo'd snooze duration silently mutes forever** (`/ignore api 2h` today stores an indefinite mute). Must raise and the bot must reply with usage. — Task 3.
2. **Spoofed or malformed `X-Forwarded-For`** must not bypass or poison the login throttle. — Task 7 (unit) and Task 8 (integration).
3. **Port already in use**: uvicorn calls `sys.exit(1)` (a `SystemExit`, not an `Exception`), which would kill the whole daemon. Must be caught and logged. — Task 11.
4. **Double-click on "Mark paid"** must return 409 and never create a second rolled-forward renewal. — Task 9.
5. **Multi-byte log output cut at the 256 KB boundary** must not raise `UnicodeDecodeError`. — Task 9.

## File Structure

```text
src/opspilot/
├── services/                    NEW  logic shared by bot and web
│   ├── __init__.py
│   ├── durations.py             duration name -> expiry datetime (strict)
│   ├── renewals.py              pay_renewal() with NotFound/NotPending errors
│   └── settings.py              set_alert_chat_id() (validate, persist, update live channel)
├── core/snapshot.py             NEW  in-memory SnapshotCache written by scheduler, read by web
├── web/                         NEW  (package __init__ imports nothing heavy)
│   ├── __init__.py
│   ├── passwords.py             PBKDF2 hash/verify, CredentialVerifier, validate_web_credentials (stdlib only)
│   ├── sessions.py              SessionStore, LoginThrottle (stdlib only)
│   ├── netutil.py               parse_trusted, client_ip, origin_matches_host (stdlib only)
│   ├── deps.py                  WebDeps dataclass
│   ├── schemas.py               pydantic request models
│   ├── auth.py                  ApiError, dependencies, /api/auth routes
│   ├── app.py                   create_app(): middleware, error handlers, static, pages
│   ├── runner.py                build_deps(), run_web_console() (isolated task body)
│   ├── routes/{overview,containers,probes,renewals,incidents,settings,live}.py
│   └── static/{index.html,login.html,app.js,login.js,styles.css}
├── core/executor.py             MODIFY  run Docker SDK calls in threads
├── db/{engine,endpoints,incidents}.py  MODIFY  endpoint tombstones, incident state listing
├── chatops/telegram/bot.py      MODIFY  hotfix + use services
├── automation/scheduler.py      MODIFY  publish snapshots
├── config.py, main.py, cli.py   MODIFY
tests/
├── test_bot_executor_contract.py, test_executor_async.py, test_services.py,
├── test_snapshot.py, test_web_passwords.py, test_web_sessions.py, test_web_netutil.py,
├── test_web_runner.py, test_cli_web.py
└── web/{conftest.py,test_security.py,test_api_*.py,test_static.py,test_live.py}
docs/web-console.md              NEW
```

---

## Task 0: v0.3.1 hotfix — the bot calls an executor method that does not exist

The released `bot.py` calls `executor.run_command([...])` in `/logs`, the Logs button, Restart confirm and Clean. `SafeOperationExecutor` has no such method (v0.2.0 used `get_container_logs`, `restart_container`, `prune_docker`), so all four raise `AttributeError`. Also, log text is inserted into a Telegram HTML message unescaped, so a log line containing `<` or `&` makes Telegram reject the message.

**Files:**
- Modify: `src/opspilot/chatops/telegram/bot.py` (`cmd_logs`, `callback_act`, `callback_confirm`, imports)
- Create: `tests/test_bot_executor_contract.py`
- Modify: `CHANGELOG.md`, `pyproject.toml`

**Interfaces:**
- Consumes: `SafeOperationExecutor.get_container_logs(name, tail) -> str`, `.restart_container(name) -> {"success": bool, "message": str}`, `.prune_docker() -> {"success": bool, "reclaimed_mb": float} | {"success": False, "error": str}`.
- Produces: nothing new; restores working behaviour. `tests/test_bot_executor_contract.py::_executor_attrs_used` is reused by later tasks.

- [ ] **Step 1: Write the failing contract test**

Create `tests/test_bot_executor_contract.py`:

```python
"""Guard: code that talks to the executor may only call methods that exist on SafeOperationExecutor."""

import ast
from pathlib import Path

import pytest

from opspilot.core.executor import SafeOperationExecutor

ROOT = Path(__file__).resolve().parents[1] / "src" / "opspilot"


def _executor_attrs_used(path: Path) -> set[str]:
    """Names accessed as `executor.X` or `<anything>.executor.X` in a source file."""
    tree = ast.parse(path.read_text(encoding="utf-8"))
    used: set[str] = set()
    for node in ast.walk(tree):
        if not isinstance(node, ast.Attribute):
            continue
        value = node.value
        if (isinstance(value, ast.Name) and value.id == "executor") or (
            isinstance(value, ast.Attribute) and value.attr == "executor"
        ):
            used.add(node.attr)
    return used


@pytest.mark.parametrize(
    "relative",
    ["chatops/telegram/bot.py", "automation/scheduler.py"],
)
def test_only_existing_executor_methods_are_called(relative):
    used = _executor_attrs_used(ROOT / relative)
    missing = sorted(name for name in used if not hasattr(SafeOperationExecutor, name))
    assert not missing, f"{relative} calls executor methods that do not exist: {missing}"
```

- [ ] **Step 2: Run it and confirm it fails**

Run: `uv run pytest tests/test_bot_executor_contract.py -v`
Expected: the `bot.py` case FAILS with `['run_command']`; the scheduler case passes.

- [ ] **Step 3: Fix `bot.py`**

Add near the other imports: `from html import escape`.

In `cmd_logs`, replace the block that starts at `n = int(args[1]) ...` through the `text = (...)` expression with:

```python
n = int(args[1]) if len(args) > 1 and args[1].isdigit() else 40
n = max(1, min(n, 500))
output = await executor.get_container_logs(container_name, tail=n)
name_html = escape(container_name)
if output.strip():
    text = f"📋 <b>Logs: {name_html}</b> (last {n} lines)\n━━━━━━━━━━━━━━━━━━━━━\n<pre>{escape(output)[-3500:]}</pre>"
else:
    text = f"📋 <b>{name_html}</b>: No logs or container not found."
await message.reply(text, parse_mode="HTML")
```

In `callback_act`, replace the `if action == "logs":` branch body with:

```python
        if action == "logs":
            output = await executor.get_container_logs(container_name, tail=40)
            name_html = escape(container_name)
            text = (
                f"📋 <b>Logs: {name_html}</b>\n<pre>{escape(output)[-3500:]}</pre>"
                if output.strip()
                else f"📋 <b>{name_html}</b>: No logs."
            )
            await query.message.reply(text, parse_mode="HTML")
```

In `callback_confirm`, replace the `restart` and `clean` branches with:

```python
if action == "restart":
    status_msg = await query.message.reply("🔄 <i>Restarting container...</i>", parse_mode="HTML")
    result = await executor.restart_container(target)
    if result["success"]:
        audit.record_action(user_id, "restart", target, "SUCCESS")
        await status_msg.edit_text(f"✅ <b>Container Restarted</b>\n<code>{escape(target)}</code>", parse_mode="HTML")
    else:
        audit.record_action(user_id, "restart", target, "FAILED", {"message": result["message"]})
        await status_msg.edit_text(
            f"❌ <b>Restart Failed</b>\n<code>{escape(result['message'])}</code>",
            parse_mode="HTML",
        )
elif action == "clean":
    status_msg = await query.message.reply("🧹 <i>Pruning Docker cache...</i>", parse_mode="HTML")
    result = await executor.prune_docker()
    if result["success"]:
        audit.record_action(user_id, "clean", "docker", "SUCCESS")
        await status_msg.edit_text(
            f"🧹 <b>Docker Cache Pruned</b>\n✅ Reclaimed <b>{result['reclaimed_mb']} MB</b>.",
            parse_mode="HTML",
        )
    else:
        audit.record_action(user_id, "clean", "docker", "FAILED", {"error": result.get("error", "")})
        await status_msg.edit_text(
            f"❌ Cleanup failed: <code>{escape(str(result.get('error', 'unknown')))}</code>",
            parse_mode="HTML",
        )
```

- [ ] **Step 4: Run tests and lint**

Run: `uv run pytest -q && uv run ruff check src tests && uv run ruff format --check src tests`
Expected: all pass. If `ruff format` complains, run `uv run ruff format src tests` and re-run.

- [ ] **Step 5: Version, changelog, commit**

In `pyproject.toml` set `version = "0.3.1"`. Add to `CHANGELOG.md` above `## [0.3.0]`:

```markdown
## [0.3.1] - 2026-09-30

### Fixed
- `/logs`, the **Logs** button, **Restart** confirmation and **Clean** crashed with `AttributeError` (the bot called an executor method that does not exist). They now use the executor's Docker methods again.
- Log output containing `<` or `&` no longer makes Telegram reject the message.
- Added a test that fails if bot or scheduler code calls a non-existent executor method.
```

Add the link line `[0.3.1]: https://github.com/iitdeveloper-git/opspilot/compare/v0.3.0...v0.3.1` next to the other links.

```bash
git add tests/test_bot_executor_contract.py src/opspilot/chatops/telegram/bot.py CHANGELOG.md pyproject.toml
git commit -m "fix: restore executor calls for /logs, restart and clean (v0.3.1)" -m "Co-Authored-By: Claude Sonnet 5.5 <noreply@anthropic.com>"
```

Stop here and tell the maintainer: v0.3.1 should be tagged and deployed before web work merges. Do not tag or push without their OK.

---

## Task 1: Executor runs Docker calls in worker threads

`SafeOperationExecutor` methods are `async def` but call the synchronous Docker SDK directly, so a restart blocks the event loop (bot, scheduler and web) for up to ~10 s. The web console makes this visible, so fix it first.

**Files:**
- Modify: `src/opspilot/core/executor.py` (full replacement)
- Test: `tests/test_executor_async.py`

**Interfaces:**
- Consumes: nothing.
- Produces: unchanged public API of `SafeOperationExecutor` (same method names, same return shapes) — later tasks and the bot depend on exactly these.

- [ ] **Step 1: Write the failing test**

Create `tests/test_executor_async.py`:

```python
import asyncio
import time

import pytest

pytest.importorskip("docker")

from opspilot.core.executor import SafeOperationExecutor  # noqa: E402


class _FakeContainer:
    def __init__(self, delay: float):
        self._delay = delay

    def restart(self, timeout=10):
        time.sleep(self._delay)  # simulates the blocking Docker SDK

    def logs(self, tail=50, timestamps=True):
        time.sleep(self._delay)
        return b"line one\nline two\n"


class _FakeClient:
    def __init__(self, delay: float):
        self.containers = self
        self._delay = delay

    def get(self, name):
        return _FakeContainer(self._delay)


def _executor(delay: float) -> SafeOperationExecutor:
    executor = SafeOperationExecutor()
    executor.client = _FakeClient(delay)
    return executor


async def _count_ticks(stop: asyncio.Event) -> int:
    ticks = 0
    while not stop.is_set():
        await asyncio.sleep(0.02)
        ticks += 1
    return ticks


async def test_restart_does_not_block_the_event_loop():
    executor = _executor(delay=0.3)
    stop = asyncio.Event()
    ticker = asyncio.create_task(_count_ticks(stop))
    result = await executor.restart_container("api")
    stop.set()
    ticks = await ticker
    assert result["success"] is True
    assert ticks >= 5, "event loop was blocked while the container restarted"


async def test_logs_do_not_block_the_event_loop():
    executor = _executor(delay=0.3)
    stop = asyncio.Event()
    ticker = asyncio.create_task(_count_ticks(stop))
    text = await executor.get_container_logs("api", tail=10)
    stop.set()
    ticks = await ticker
    assert "line two" in text
    assert ticks >= 5
```

- [ ] **Step 2: Run it and confirm it fails**

Run: `uv run pytest tests/test_executor_async.py -v`
Expected: both FAIL on `assert ticks >= 5` (the loop is blocked).

- [ ] **Step 3: Replace `executor.py`**

```python
import asyncio
import logging
from collections.abc import Callable
from typing import Any

try:
    import docker
    from docker.errors import APIError, NotFound
except ImportError:
    docker = None
    NotFound = Exception
    APIError = Exception

logger = logging.getLogger("opspilot.executor")


class SafeOperationExecutor:
    """Deterministic safe executor. NEVER runs arbitrary shell strings.

    The Docker SDK is synchronous, so every call is pushed to a worker thread to keep the
    event loop (bot, scheduler, web console) responsive.
    """

    def __init__(self):
        self.client = None
        if docker is not None:
            try:
                self.client = docker.from_env()
            except Exception as e:
                logger.warning(f"Docker client initialization deferred: {e}")

    def _ensure_docker(self):
        if docker is None:
            raise RuntimeError("docker SDK is not installed")
        if self.client is None:
            self.client = docker.from_env()

    def _container(self, name: str):
        self._ensure_docker()
        return self.client.containers.get(name)

    async def _act(self, name: str, ok_message: str, action: Callable[[Any], Any]) -> dict[str, Any]:
        def work() -> None:
            action(self._container(name))

        try:
            await asyncio.to_thread(work)
            return {"success": True, "message": ok_message}
        except NotFound:
            return {"success": False, "message": f"Container {name} not found."}
        except Exception as e:
            return {"success": False, "message": str(e)}

    async def restart_container(self, container_name: str) -> dict[str, Any]:
        return await self._act(
            container_name,
            f"Container {container_name} restarted successfully.",
            lambda c: c.restart(timeout=10),
        )

    async def stop_container(self, container_name: str) -> dict[str, Any]:
        return await self._act(container_name, f"Container {container_name} stopped.", lambda c: c.stop(timeout=10))

    async def start_container(self, container_name: str) -> dict[str, Any]:
        return await self._act(container_name, f"Container {container_name} started.", lambda c: c.start())

    async def get_container_logs(self, container_name: str, tail: int = 50) -> str:
        def work() -> str:
            container = self._container(container_name)
            return container.logs(tail=tail, timestamps=True).decode("utf-8", errors="replace")

        try:
            return await asyncio.to_thread(work)
        except Exception as e:
            return f"Error fetching logs: {e}"

    async def prune_docker(self) -> dict[str, Any]:
        def work() -> dict[str, Any]:
            self._ensure_docker()
            images = self.client.images.prune()
            containers = self.client.containers.prune()
            reclaimed = images.get("SpaceReclaimed", 0) + containers.get("SpaceReclaimed", 0)
            return {"success": True, "reclaimed_mb": round(reclaimed / (1024 * 1024), 2)}

        try:
            return await asyncio.to_thread(work)
        except Exception as e:
            return {"success": False, "error": str(e)}
```

- [ ] **Step 4: Run tests**

Run: `uv run pytest tests/test_executor_async.py tests/test_executor.py tests/test_bot_executor_contract.py -v`
Expected: all PASS (existing executor tests still pass: unknown containers return `success: False`).

- [ ] **Step 5: Commit**

```bash
uv run ruff check src tests && uv run ruff format --check src tests
git add src/opspilot/core/executor.py tests/test_executor_async.py
git commit -m "fix: run Docker SDK calls in worker threads so restarts do not block the event loop" -m "Co-Authored-By: Claude Sonnet 5.5 <noreply@anthropic.com>"
```

---

## Task 2: Web settings, optional dependencies, CI

**Files:**
- Modify: `src/opspilot/config.py`, `pyproject.toml`, `.github/workflows/ci.yml`, `Dockerfile`, `.env.example`
- Test: `tests/test_config.py` (append)

**Interfaces:**
- Consumes: existing `Settings`.
- Produces: `Settings.web_enabled: bool`, `.web_host: str`, `.web_port: int`, `.admin_password: SecretStr`, `.admin_password_hash: SecretStr`, `.web_trusted_proxies: str`, `.web_insecure_cookies: bool`. Read secrets with `.get_secret_value()`.

> **Note (do not act on it here):** `auth_mode` has no env alias, so `OPSPILOT_AUTH_MODE` in `.env` is ignored and the bot always runs in `production` mode, contrary to `.env.example`. Making the alias effective could silently switch an existing deployment to `development` (allow-all). Leave it; the maintainer decides separately.

- [ ] **Step 1: Write the failing tests**

Append to `tests/test_config.py`:

```python
def test_web_console_is_disabled_by_default(monkeypatch):
    for name in ("OPSPILOT_WEB_ENABLED", "OPSPILOT_WEB_HOST", "OPSPILOT_WEB_PORT"):
        monkeypatch.delenv(name, raising=False)
    from opspilot.config import Settings

    s = Settings(_env_file=None)
    assert s.web_enabled is False
    assert s.web_host == "127.0.0.1"
    assert s.web_port == 8088
    assert s.web_trusted_proxies == "127.0.0.1"
    assert s.web_insecure_cookies is False


def test_web_settings_read_opspilot_prefixed_env(monkeypatch):
    monkeypatch.setenv("OPSPILOT_WEB_ENABLED", "true")
    monkeypatch.setenv("OPSPILOT_WEB_PORT", "9000")
    monkeypatch.setenv("OPSPILOT_ADMIN_PASSWORD", "a-long-enough-secret")
    from opspilot.config import Settings

    s = Settings(_env_file=None)
    assert s.web_enabled is True
    assert s.web_port == 9000
    assert s.admin_password.get_secret_value() == "a-long-enough-secret"


def test_admin_password_is_not_printed():
    from opspilot.config import Settings

    s = Settings(_env_file=None, admin_password="a-long-enough-secret")
    assert "a-long-enough-secret" not in repr(s)
    assert "a-long-enough-secret" not in str(s.model_dump())
```

- [ ] **Step 2: Run and confirm failure**

Run: `uv run pytest tests/test_config.py -v`
Expected: the three new tests FAIL (`AttributeError: ... web_enabled`).

- [ ] **Step 3: Add the settings**

In `src/opspilot/config.py` change the pydantic imports and add fields to `Settings` (after `db_path`):

```python
from pydantic import AliasChoices, BaseModel, Field, SecretStr
```

```python
    # ─── Optional web console (disabled by default) ───────────────────────────
    web_enabled: bool = Field(False, validation_alias=AliasChoices("OPSPILOT_WEB_ENABLED", "web_enabled"))
    web_host: str = Field("127.0.0.1", validation_alias=AliasChoices("OPSPILOT_WEB_HOST", "web_host"))
    web_port: int = Field(8088, validation_alias=AliasChoices("OPSPILOT_WEB_PORT", "web_port"))
    web_trusted_proxies: str = Field(
        "127.0.0.1", validation_alias=AliasChoices("OPSPILOT_WEB_TRUSTED_PROXIES", "web_trusted_proxies")
    )
    web_insecure_cookies: bool = Field(
        False, validation_alias=AliasChoices("OPSPILOT_WEB_INSECURE_COOKIES", "web_insecure_cookies")
    )
    admin_password: SecretStr = Field(
        SecretStr(""), validation_alias=AliasChoices("OPSPILOT_ADMIN_PASSWORD", "admin_password")
    )
    admin_password_hash: SecretStr = Field(
        SecretStr(""), validation_alias=AliasChoices("OPSPILOT_ADMIN_PASSWORD_HASH", "admin_password_hash")
    )
```

- [ ] **Step 4: Optional extra, CI, Docker image, env example**

`pyproject.toml`: under `[project.optional-dependencies]` add

```toml
web = [
    "fastapi>=0.110.0",
    "uvicorn>=0.29.0",
]
```

`.github/workflows/ci.yml`: replace every `uv pip install -e ".[ai,dev]"` and `uv pip install pip-audit -e ".[ai,dev]"` with the same command using `".[ai,web,dev]"` (three places: lint, audit, test jobs).

`Dockerfile`: change `RUN pip install --no-cache-dir -e ".[ai]"` to `RUN pip install --no-cache-dir -e ".[ai,web]"` (the web console stays off unless enabled).

`.env.example`: append

```env
# ─────────────────────────────────────────────────────────────
# WEB CONSOLE (optional, DISABLED by default)
# ─────────────────────────────────────────────────────────────
# OPSPILOT_WEB_ENABLED=true
# OPSPILOT_WEB_HOST=127.0.0.1          # keep loopback; expose only through a TLS reverse proxy
# OPSPILOT_WEB_PORT=8088
# Generate a hash with:  opspilot web hash-password
# OPSPILOT_ADMIN_PASSWORD_HASH=
# Or (discouraged) a plaintext password of at least 12 characters:
# OPSPILOT_ADMIN_PASSWORD=
# OPSPILOT_WEB_TRUSTED_PROXIES=127.0.0.1   # peers allowed to set X-Forwarded-For
# OPSPILOT_WEB_INSECURE_COOKIES=false      # true only for plain-HTTP local development
```

- [ ] **Step 5: Install, run, commit**

```bash
uv pip install -e ".[ai,web,dev]"
uv run pytest -q && uv run ruff check src tests && uv run ruff format --check src tests
git add src/opspilot/config.py pyproject.toml .github/workflows/ci.yml Dockerfile .env.example tests/test_config.py
git commit -m "feat(web): add web console settings and optional [web] extra" -m "Co-Authored-By: Claude Sonnet 5.5 <noreply@anthropic.com>"
```

Also run `uv lock` and include `uv.lock` in this commit if it changes.

---

## Task 3: Shared services (durations, renewals, alert chat) and bot refactor

**Files:**
- Create: `src/opspilot/services/__init__.py` (empty), `services/durations.py`, `services/renewals.py`, `services/settings.py`
- Modify: `src/opspilot/chatops/telegram/bot.py`
- Test: `tests/test_services.py`

**Interfaces:**
- Consumes: `db.renewals.get_renewal/mark_paid`, `db.store.set_setting/get_setting`, `channel.update_chat_id(str)` (Telegram channel).
- Produces:
  - `resolve_duration(name: str, now: datetime | None = None) -> datetime | None` (raises `ValueError` on unknown names; `None` means forever), `format_until(dt: datetime | None) -> str`, `DURATIONS: dict[str, timedelta | None]`.
  - `pay_renewal(renewal_id: int) -> tuple[dict, dict | None]` raising `RenewalNotFound`, `RenewalNotPending`.
  - `set_alert_chat_id(chat_id: str, channel) -> str` raising `InvalidChatId`; `get_alert_chat_id(default: str) -> str`.

- [ ] **Step 1: Write the failing tests**

Create `tests/test_services.py`:

```python
from datetime import UTC, datetime, timedelta

import pytest

from opspilot.db.engine import init_db, set_db_path
from opspilot.db.renewals import add_renewal, get_renewal, list_renewals
from opspilot.services.durations import DURATIONS, format_until, resolve_duration
from opspilot.services.renewals import RenewalNotFound, RenewalNotPending, pay_renewal
from opspilot.services.settings import InvalidChatId, get_alert_chat_id, set_alert_chat_id


@pytest.fixture(autouse=True)
async def fresh_db(tmp_path):
    set_db_path(tmp_path / "svc.db")
    await init_db()


# ─── durations ────────────────────────────────────────────────────────────────


def test_resolve_duration_known_values():
    now = datetime(2026, 1, 1, 12, 0, tzinfo=UTC)
    assert resolve_duration("1h", now) == now + timedelta(hours=1)
    assert resolve_duration("24h", now) == now + timedelta(hours=24)
    assert resolve_duration("7d", now) == now + timedelta(days=7)
    assert resolve_duration("forever", now) is None
    assert resolve_duration(" 1H ", now) == now + timedelta(hours=1)


@pytest.mark.parametrize("bad", ["2h", "", "soon", "1 hour", "-1h", "0"])
def test_unknown_duration_raises_instead_of_muting_forever(bad):
    with pytest.raises(ValueError):
        resolve_duration(bad)


def test_format_until():
    assert format_until(None) == "indefinitely"
    assert format_until(datetime(2026, 1, 2, 3, 4, tzinfo=UTC)) == "02 Jan 2026 03:04 UTC"


def test_every_keyboard_snooze_duration_is_valid():
    from opspilot.chatops.telegram import keyboards as kb

    markups = [
        kb.get_ignore_duration_keyboard("api"),
        kb.get_renewal_alert_keyboard(1),
        kb.get_probe_alert_keyboard(1),
    ]
    seen = 0
    for markup in markups:
        for row in markup.inline_keyboard:
            for button in row:
                if (
                    button.callback_data
                    and ":snooze:" in button.callback_data
                    or (button.callback_data and button.callback_data.startswith("snooze:"))
                ):
                    duration = button.callback_data.split(":")[-1]
                    assert duration in DURATIONS, f"keyboard emits unknown duration {duration!r}"
                    seen += 1
    assert seen > 0


# ─── renewals ─────────────────────────────────────────────────────────────────


async def test_pay_renewal_marks_paid_and_rolls_recurring():
    rid = await add_renewal("Acme VPS", "vps", "2026-10-01", 1200.0, recurrence="monthly")
    renewal, next_r = await pay_renewal(rid)
    assert renewal["id"] == rid
    assert next_r is not None and next_r["due_date"] == "2026-11-01"
    assert (await get_renewal(rid))["status"] == "paid"


async def test_pay_renewal_twice_raises_and_does_not_create_a_second_next():
    rid = await add_renewal("Acme VPS", "vps", "2026-10-01", 1200.0, recurrence="monthly")
    await pay_renewal(rid)
    with pytest.raises(RenewalNotPending):
        await pay_renewal(rid)
    assert len(await list_renewals()) == 2  # the paid one and exactly one rolled-forward renewal


async def test_pay_missing_renewal_raises():
    with pytest.raises(RenewalNotFound):
        await pay_renewal(9999)


# ─── alert chat id ────────────────────────────────────────────────────────────


class _Channel:
    def __init__(self):
        self.chat_id = ""

    def update_chat_id(self, chat_id: str) -> None:
        self.chat_id = chat_id


async def test_set_alert_chat_id_persists_and_updates_channel():
    channel = _Channel()
    assert await set_alert_chat_id(" -1001234567890 ", channel) == "-1001234567890"
    assert channel.chat_id == "-1001234567890"
    assert await get_alert_chat_id("default") == "-1001234567890"


async def test_set_alert_chat_id_accepts_channel_username_and_no_channel():
    assert await set_alert_chat_id("@opspilot_alerts", None) == "@opspilot_alerts"


@pytest.mark.parametrize("bad", ["", "abc", "12", "-", "@a", "1234 5678", "drop table"])
async def test_set_alert_chat_id_rejects_garbage(bad):
    channel = _Channel()
    with pytest.raises(InvalidChatId):
        await set_alert_chat_id(bad, channel)
    assert channel.chat_id == ""
    assert await get_alert_chat_id("default") == "default"
```

- [ ] **Step 2: Run and confirm failure**

Run: `uv run pytest tests/test_services.py -v`
Expected: collection error `ModuleNotFoundError: opspilot.services`.

- [ ] **Step 3: Implement the services**

`src/opspilot/services/__init__.py`: empty file.

`src/opspilot/services/durations.py`:

```python
"""Snooze duration names shared by the Telegram bot and the web console."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

DURATIONS: dict[str, timedelta | None] = {
    "1h": timedelta(hours=1),
    "24h": timedelta(hours=24),
    "1d": timedelta(days=1),
    "3d": timedelta(days=3),
    "7d": timedelta(days=7),
    "forever": None,
    "indefinite": None,
}


def resolve_duration(name: str, now: datetime | None = None) -> datetime | None:
    """Return the UTC expiry for a duration name, or None for 'forever'.

    Raises ValueError for anything unknown, so a typo can never silently become an indefinite mute.
    """
    key = name.strip().lower()
    if key not in DURATIONS:
        allowed = ", ".join(sorted(k for k in DURATIONS if k != "indefinite"))
        raise ValueError(f"Unknown duration '{name.strip()}'. Use one of: {allowed}.")
    delta = DURATIONS[key]
    if delta is None:
        return None
    return (now or datetime.now(UTC)) + delta


def format_until(dt: datetime | None) -> str:
    return "indefinitely" if dt is None else dt.strftime("%d %b %Y %H:%M UTC")
```

`src/opspilot/services/renewals.py`:

```python
"""Renewal actions shared by the Telegram bot and the web console."""

from __future__ import annotations

from opspilot.db import renewals as ren_db


class RenewalNotFound(Exception):
    pass


class RenewalNotPending(Exception):
    pass


async def pay_renewal(renewal_id: int) -> tuple[dict, dict | None]:
    """Mark a pending renewal paid. Returns (renewal_before_payment, next_renewal_or_None)."""
    renewal = await ren_db.get_renewal(renewal_id)
    if renewal is None:
        raise RenewalNotFound(renewal_id)
    if renewal["status"] != "pending":
        raise RenewalNotPending(renewal_id)
    next_renewal = await ren_db.mark_paid(renewal_id)
    return renewal, next_renewal
```

`src/opspilot/services/settings.py`:

```python
"""Runtime settings shared by the Telegram bot and the web console."""

from __future__ import annotations

import re

from opspilot.db.store import get_setting, set_setting

_CHAT_ID = re.compile(r"^(-?\d{5,20}|@[A-Za-z][A-Za-z0-9_]{4,31})$")


class InvalidChatId(ValueError):
    pass


async def set_alert_chat_id(chat_id: str, channel) -> str:
    """Validate, persist and apply a new alert chat ID. Returns the cleaned value."""
    cleaned = chat_id.strip()
    if not _CHAT_ID.match(cleaned):
        raise InvalidChatId("Chat ID must be a number such as -1001234567890, or a channel like @my_channel.")
    await set_setting("alert_chat_id", cleaned)
    if channel is not None and hasattr(channel, "update_chat_id"):
        channel.update_chat_id(cleaned)
    return cleaned


async def get_alert_chat_id(default: str) -> str:
    return await get_setting("alert_chat_id", default)
```

- [ ] **Step 4: Run service tests**

Run: `uv run pytest tests/test_services.py -v`
Expected: PASS. If `test_every_keyboard_snooze_duration_is_valid` reports an unknown duration or `seen == 0`, open `src/opspilot/chatops/telegram/keyboards.py`, read how the snooze callback strings are built, and adjust only the detection condition in the test (the keyboards themselves already work).

- [ ] **Step 5: Refactor the bot to use the services**

In `src/opspilot/chatops/telegram/bot.py`:

1. Imports: remove `from opspilot.db.store import get_setting, set_setting` and add
```python
from opspilot.services.durations import format_until, resolve_duration
from opspilot.services.renewals import RenewalNotPending, pay_renewal
from opspilot.services.settings import InvalidChatId, get_alert_chat_id, set_alert_chat_id
```
   (`timedelta`/`UTC` imports may become unused — let `ruff` tell you and remove them.)

2. Replace the two helpers `_parse_duration_to_dt` and `_snooze_until_str` with:
```python
def _parse_duration_to_dt(duration_str: str) -> datetime | None:
    """Strict: raises ValueError for unknown durations (see services.durations)."""
    return resolve_duration(duration_str)


def _snooze_until_str(duration_str: str) -> str:
    return format_until(resolve_duration(duration_str))
```

3. `/ignore`: replace `exp_dt = _parse_duration_to_dt(duration)` with
```python
        try:
            exp_dt = resolve_duration(duration)
        except ValueError as exc:
            await message.reply(f"❌ {escape(str(exc))}", parse_mode="HTML")
            return
```
   and replace the `until = ... strftime(...)` line with `until = format_until(exp_dt)`. Do the same `until = format_until(exp_dt)` replacement in `callback_snooze`.

4. `callback_renewal`, `action == "paid"` branch: replace `next_r = await ren_db.mark_paid(renewal_id)` with
```python
            try:
                _, next_r = await pay_renewal(renewal_id)
            except RenewalNotPending:
                await query.answer("Already marked as paid.", show_alert=True)
                return
```

5. `/setchat` (in `cmd_setchat`): replace `current = await get_setting("alert_chat_id", settings.telegram_alert_chat_id)` with `current = await get_alert_chat_id(settings.telegram_alert_chat_id)`, and replace the `await set_setting(...)` line plus the `if channel is not None ...update_chat_id(chat_id)` block with
```python
        try:
            chat_id = await set_alert_chat_id(chat_id, channel)
        except InvalidChatId as exc:
            await message.reply(f"❌ {escape(str(exc))}", parse_mode="HTML")
            return
```

- [ ] **Step 6: Add a handler-level regression test**

Append to `tests/test_services.py`:

```python
def test_bot_ignore_handler_rejects_unknown_duration_source():
    """The /ignore handler must go through the strict resolver (no silent forever-mute)."""
    from pathlib import Path

    src = (Path(__file__).resolve().parents[1] / "src/opspilot/chatops/telegram/bot.py").read_text()
    ignore_block = src.split('@dp.message(Command("ignore"))', 1)[1].split("@dp.message", 1)[0]
    assert "resolve_duration(duration)" in ignore_block
    assert "except ValueError" in ignore_block
```

- [ ] **Step 7: Full run and commit**

```bash
uv run pytest -q && uv run ruff check src tests && uv run ruff format --check src tests
git add src/opspilot/services tests/test_services.py src/opspilot/chatops/telegram/bot.py
git commit -m "refactor: extract shared services; reject unknown snooze durations" -m "Co-Authored-By: Claude Sonnet 5.5 <noreply@anthropic.com>"
```

---

## Task 4: Endpoint tombstones and incident listing

Removing a probe becomes a soft delete so YAML seeding can never resurrect it, and the web console can list incidents by state.

**Files:**
- Modify: `src/opspilot/db/engine.py`, `src/opspilot/db/endpoints.py` (full replacement), `src/opspilot/db/incidents.py`, `src/opspilot/chatops/telegram/bot.py`
- Test: `tests/test_db_endpoints.py`, `tests/test_db_incidents.py` (append)

**Interfaces:**
- Consumes: existing `db_conn`, `init_db`.
- Produces:
  - `db.endpoints`: `list_endpoints(enabled_only: bool = True) -> list[dict]` (never returns deleted), `get_endpoint(endpoint_id: int) -> dict | None`, `add_endpoint(name, url, expected_status=200, timeout_seconds=5) -> int` (raises `DuplicateEndpoint`; revives a tombstone), `remove_endpoint(endpoint_id) -> bool` (soft delete, False if missing), `toggle_endpoint(endpoint_id, enabled) -> bool`, `class DuplicateEndpoint(Exception)`, `seed_endpoints_from_yaml` unchanged.
  - `db.incidents`: `list_by_state(state: str, limit: int, offset: int) -> list[dict]`, `count_by_state(state: str) -> int` with `state in {"open", "resolved", "all"}`.

- [ ] **Step 1: Write the failing tests**

Create `tests/test_db_endpoints.py`:

```python
import pytest

from opspilot.db.endpoints import (
    DuplicateEndpoint,
    add_endpoint,
    get_endpoint,
    list_endpoints,
    remove_endpoint,
    seed_endpoints_from_yaml,
    toggle_endpoint,
)
from opspilot.db.engine import init_db, set_db_path


@pytest.fixture(autouse=True)
async def fresh_db(tmp_path):
    set_db_path(tmp_path / "ep.db")
    await init_db()


async def test_remove_is_soft_and_hidden_everywhere():
    ep_id = await add_endpoint("API", "https://api.example.com/health")
    assert await remove_endpoint(ep_id) is True
    assert await list_endpoints(enabled_only=False) == []
    assert await get_endpoint(ep_id) is None
    assert await remove_endpoint(ep_id) is False


async def test_yaml_seed_does_not_resurrect_a_removed_probe():
    item = {"name": "API", "url": "https://api.example.com/health"}
    await seed_endpoints_from_yaml([item])
    (row,) = await list_endpoints()
    await remove_endpoint(row["id"])
    await seed_endpoints_from_yaml([item])  # simulates a restart
    assert await list_endpoints(enabled_only=False) == []


async def test_re_adding_a_removed_name_revives_and_updates_it():
    ep_id = await add_endpoint("API", "https://old.example.com", expected_status=200)
    await remove_endpoint(ep_id)
    new_id = await add_endpoint("API", "https://new.example.com", expected_status=204, timeout_seconds=9)
    assert new_id == ep_id
    (row,) = await list_endpoints()
    assert row["url"] == "https://new.example.com"
    assert row["expected_status"] == 204
    assert row["timeout_seconds"] == 9
    assert row["enabled"] == 1


async def test_duplicate_active_name_raises():
    await add_endpoint("API", "https://api.example.com")
    with pytest.raises(DuplicateEndpoint):
        await add_endpoint("API", "https://other.example.com")


async def test_toggle_and_missing():
    ep_id = await add_endpoint("API", "https://api.example.com")
    assert await toggle_endpoint(ep_id, False) is True
    assert await list_endpoints(enabled_only=True) == []
    assert len(await list_endpoints(enabled_only=False)) == 1
    assert await toggle_endpoint(9999, True) is False
    await remove_endpoint(ep_id)
    assert await toggle_endpoint(ep_id, True) is False
```

Append to `tests/test_db_incidents.py`:

```python
async def test_list_and_count_by_state():
    from opspilot.db.incidents import (
        count_by_state,
        list_by_state,
        open_incident,
        resolve_incident,
    )

    await open_incident("docker", "api", "critical", "api down")
    await open_incident("docker", "db", "critical", "db down")
    await resolve_incident("docker", "db")

    assert await count_by_state("open") == 1
    assert await count_by_state("resolved") == 1
    assert await count_by_state("all") == 2
    assert [i["target"] for i in await list_by_state("open", 10, 0)] == ["api"]
    assert [i["target"] for i in await list_by_state("resolved", 10, 0)] == ["db"]
    assert len(await list_by_state("all", 1, 0)) == 1
    assert len(await list_by_state("all", 10, 1)) == 1
```

(If `tests/test_db_incidents.py` has no autouse DB fixture, reuse the one already defined at the top of that file — it already initialises a temp DB for the other tests there.)

- [ ] **Step 2: Run and confirm failure**

Run: `uv run pytest tests/test_db_endpoints.py tests/test_db_incidents.py -v`
Expected: import errors for `DuplicateEndpoint`, `get_endpoint`, `count_by_state`, `list_by_state`.

- [ ] **Step 3: Engine migration**

In `src/opspilot/db/engine.py`, in the `endpoints` table definition add a column after `enabled`:

```sql
                deleted         INTEGER NOT NULL DEFAULT 0,
```

Replace the existing single `try: await db.execute("ALTER TABLE renewals ADD COLUMN snoozed_until TEXT") ... except Exception: pass` migration block with:

```python
        migrations = (
            ("ALTER TABLE renewals ADD COLUMN snoozed_until TEXT", "added snoozed_until to renewals"),
            ("ALTER TABLE endpoints ADD COLUMN deleted INTEGER NOT NULL DEFAULT 0", "added deleted to endpoints"),
        )
        for ddl, note in migrations:
            try:
                await db.execute(ddl)
                await db.commit()
                logger.info(f"Migration: {note}.")
            except Exception:
                pass  # Column already exists — normal on fresh installs
```

- [ ] **Step 4: Replace `db/endpoints.py`**

```python
"""CRUD for DB-managed HTTP endpoints — hot-configurable without redeploy.

Removal is a soft delete (tombstone): the row stays with deleted=1 so that seeding from config.yaml
(INSERT OR IGNORE on the unique name) can never bring a removed probe back.
"""

from __future__ import annotations

from opspilot.db.engine import db_conn


class DuplicateEndpoint(Exception):
    pass


async def seed_endpoints_from_yaml(items: list[dict]) -> int:
    """Seed from YAML config. INSERT OR IGNORE prevents duplicates on restart."""
    if not items:
        return 0
    inserted = 0
    async with db_conn() as db:
        for item in items:
            cursor = await db.execute(
                """INSERT OR IGNORE INTO endpoints (name, url, expected_status, timeout_seconds)
                   VALUES (?, ?, ?, ?)""",
                (
                    item["name"],
                    item["url"],
                    item.get("expected_status", 200),
                    item.get("timeout_seconds", 5),
                ),
            )
            if cursor.rowcount == 1:
                inserted += 1
        await db.commit()
    return inserted


async def list_endpoints(enabled_only: bool = True) -> list[dict]:
    query = "SELECT * FROM endpoints WHERE deleted = 0"
    if enabled_only:
        query += " AND enabled = 1"
    async with db_conn() as db:
        cursor = await db.execute(query + " ORDER BY name ASC")
        return [dict(r) for r in await cursor.fetchall()]


async def get_endpoint(endpoint_id: int) -> dict | None:
    async with db_conn() as db:
        cursor = await db.execute("SELECT * FROM endpoints WHERE id = ? AND deleted = 0", (endpoint_id,))
        row = await cursor.fetchone()
        return dict(row) if row else None


async def add_endpoint(
    name: str,
    url: str,
    expected_status: int = 200,
    timeout_seconds: int = 5,
) -> int:
    async with db_conn() as db:
        cursor = await db.execute("SELECT id, deleted FROM endpoints WHERE name = ?", (name,))
        existing = await cursor.fetchone()
        if existing and not existing["deleted"]:
            raise DuplicateEndpoint(name)
        if existing:  # revive a tombstone with the new settings
            await db.execute(
                """UPDATE endpoints
                   SET url = ?, expected_status = ?, timeout_seconds = ?, enabled = 1, deleted = 0
                   WHERE id = ?""",
                (url, expected_status, timeout_seconds, existing["id"]),
            )
            await db.commit()
            return existing["id"]
        cursor = await db.execute(
            """INSERT INTO endpoints (name, url, expected_status, timeout_seconds)
               VALUES (?, ?, ?, ?)""",
            (name, url, expected_status, timeout_seconds),
        )
        await db.commit()
        return cursor.lastrowid  # type: ignore[return-value]


async def remove_endpoint(endpoint_id: int) -> bool:
    """Soft delete. Returns False if the endpoint does not exist or is already removed."""
    async with db_conn() as db:
        cursor = await db.execute(
            "UPDATE endpoints SET deleted = 1, enabled = 0 WHERE id = ? AND deleted = 0",
            (endpoint_id,),
        )
        await db.commit()
        return cursor.rowcount > 0


async def toggle_endpoint(endpoint_id: int, enabled: bool) -> bool:
    async with db_conn() as db:
        cursor = await db.execute(
            "UPDATE endpoints SET enabled = ? WHERE id = ? AND deleted = 0",
            (1 if enabled else 0, endpoint_id),
        )
        await db.commit()
        return cursor.rowcount > 0
```

- [ ] **Step 5: Incident state listing**

Append to `src/opspilot/db/incidents.py`:

```python
_STATE_FILTERS = {
    "open": "resolved_at IS NULL",
    "resolved": "resolved_at IS NOT NULL",
    "all": "1 = 1",
}


async def list_by_state(state: str, limit: int, offset: int) -> list[dict]:
    where = _STATE_FILTERS[state]  # KeyError for unknown state; callers validate first
    async with db_conn() as db:
        cursor = await db.execute(
            f"SELECT * FROM incidents WHERE {where} ORDER BY updated_at DESC, id DESC LIMIT ? OFFSET ?",
            (limit, offset),
        )
        return [dict(r) for r in await cursor.fetchall()]


async def count_by_state(state: str) -> int:
    where = _STATE_FILTERS[state]
    async with db_conn() as db:
        cursor = await db.execute(f"SELECT COUNT(*) AS c FROM incidents WHERE {where}")
        row = await cursor.fetchone()
        return row["c"] if row else 0
```

- [ ] **Step 6: Bot: handle duplicates and missing IDs**

In `bot.py` `addprobe_status`, wrap the add:

```python
        try:
            ep_id = await ep_db.add_endpoint(data["name"], data["url"], status_code)
        except ep_db.DuplicateEndpoint:
            await message.reply(
                f"❌ A probe named <b>{escape(data['name'])}</b> already exists. Remove it first with /rmprobe.",
                parse_mode="HTML",
            )
            return
```

In `cmd_rmprobe`, replace `await ep_db.remove_endpoint(ep_id)` and the reply with:

```python
        removed = await ep_db.remove_endpoint(ep_id)
        if not removed:
            await message.reply(f"❌ No probe with ID #{ep_id}. Find IDs with /probes.", parse_mode="HTML")
            return
        await message.reply(f"🗑 <b>Endpoint #{ep_id} removed.</b>", parse_mode="HTML")
```

Also check the `/probes` handler and the scheduler: both use `ep_db.list_endpoints(...)`, which now excludes deleted rows — no change needed. Search for any other `list_endpoints` caller with `grep -rn "list_endpoints" src` and confirm.

- [ ] **Step 7: Run and commit**

```bash
uv run pytest -q && uv run ruff check src tests && uv run ruff format --check src tests
git add src/opspilot/db tests/test_db_endpoints.py tests/test_db_incidents.py src/opspilot/chatops/telegram/bot.py
git commit -m "feat(db): soft-delete probes so YAML seed cannot resurrect them; list incidents by state" -m "Co-Authored-By: Claude Sonnet 5.5 <noreply@anthropic.com>"
```

---

## Task 5: SnapshotCache and scheduler publishing

**Files:**
- Create: `src/opspilot/core/snapshot.py`
- Modify: `src/opspilot/automation/scheduler.py`
- Test: `tests/test_snapshot.py`

**Interfaces:**
- Consumes: `BackgroundScheduler` loops; `SystemMetrics`, `ContainerStatus`, `ProbeResult` models.
- Produces:
  - `SYSTEM = "system"`, `CONTAINERS = "containers"`, `PROBES = "probes"` constants.
  - `SnapshotCache(clock: Callable[[], float] = time.time)` with `set(key, value)`, `snapshot(key) -> {"data": Any | None, "updated_at": float | None, "age_seconds": float | None}`.
  - `BackgroundScheduler(settings, channel=None, notify_callback=None, snapshots: SnapshotCache | None = None)`.
  - Snapshot shapes: `SYSTEM` → `SystemMetrics.model_dump()`; `CONTAINERS` → `list[ContainerStatus.model_dump()]`; `PROBES` → `{endpoint_name: {"status_code": int|None, "latency_ms": float, "is_healthy": bool, "error": str|None, "checked_at": float}}`.

- [ ] **Step 1: Write the failing tests**

Create `tests/test_snapshot.py`:

```python
from unittest.mock import AsyncMock, patch

import pytest

from opspilot.automation.scheduler import BackgroundScheduler
from opspilot.config import MonitoringConfig, Settings
from opspilot.core.snapshot import CONTAINERS, PROBES, SYSTEM, SnapshotCache
from opspilot.db import endpoints as ep_db
from opspilot.db.engine import init_db, set_db_path
from opspilot.monitor.docker import ContainerStatus
from opspilot.monitor.probes import ProbeResult
from opspilot.monitor.system import SystemMetrics


class _Clock:
    def __init__(self):
        self.now = 1000.0

    def __call__(self):
        return self.now


def test_snapshot_reports_age_and_defaults():
    clock = _Clock()
    cache = SnapshotCache(clock=clock)
    assert cache.snapshot("nope") == {"data": None, "updated_at": None, "age_seconds": None}
    cache.set("k", {"a": 1})
    clock.now += 12.5
    snap = cache.snapshot("k")
    assert snap["data"] == {"a": 1}
    assert snap["updated_at"] == 1000.0
    assert snap["age_seconds"] == 12.5


@pytest.fixture
async def db(tmp_path):
    set_db_path(tmp_path / "snap.db")
    await init_db()


def _settings() -> Settings:
    return Settings(
        _env_file=None,
        server_name="test-node",
        monitoring=MonitoringConfig(interval_seconds=1, ssl_domains=[]),
    )


def _metrics() -> SystemMetrics:
    return SystemMetrics(
        cpu_percent=10.0,
        cpu_count=2,
        ram_total_gb=8.0,
        ram_used_gb=2.0,
        ram_percent=25.0,
        disk_total_gb=100.0,
        disk_used_gb=20.0,
        disk_free_gb=80.0,
        disk_percent=20.0,
        uptime_human="1d",
        load_avg=[0.1, 0.1, 0.1],
    )


async def test_health_loop_publishes_system_and_containers(db):
    cache = SnapshotCache()
    scheduler = BackgroundScheduler(_settings(), snapshots=cache)
    container = ContainerStatus(id="1", name="api", image="img", status="running", health="healthy", created="x")
    with (
        patch("opspilot.automation.scheduler.collect_system_metrics", return_value=_metrics()),
        patch("opspilot.automation.scheduler.collect_docker_statuses", return_value=[container]),
    ):
        await scheduler._run_health_loop()
    assert cache.snapshot(SYSTEM)["data"]["cpu_percent"] == 10.0
    assert cache.snapshot(CONTAINERS)["data"][0]["name"] == "api"


async def test_probe_loop_publishes_results_by_name(db):
    await ep_db.add_endpoint("API", "https://api.example.com/health")
    cache = SnapshotCache()
    scheduler = BackgroundScheduler(_settings(), snapshots=cache)
    result = ProbeResult(
        name="API", url="https://api.example.com/health", status_code=200, is_healthy=True, latency_ms=42.0
    )
    with patch("opspilot.automation.scheduler.probe_http_endpoint", new=AsyncMock(return_value=result)):
        await scheduler._run_probe_loop()
    probe = cache.snapshot(PROBES)["data"]["API"]
    assert probe["status_code"] == 200
    assert probe["latency_ms"] == 42.0
    assert probe["is_healthy"] is True
    assert probe["error"] is None
    assert isinstance(probe["checked_at"], float)


async def test_scheduler_without_cache_still_works(db):
    scheduler = BackgroundScheduler(_settings())
    with (
        patch("opspilot.automation.scheduler.collect_system_metrics", return_value=_metrics()),
        patch("opspilot.automation.scheduler.collect_docker_statuses", return_value=[]),
    ):
        await scheduler._run_health_loop()
```

- [ ] **Step 2: Run and confirm failure**

Run: `uv run pytest tests/test_snapshot.py -v`
Expected: `ModuleNotFoundError: opspilot.core.snapshot`.

- [ ] **Step 3: Implement the cache**

`src/opspilot/core/snapshot.py`:

```python
"""In-memory snapshots written by the scheduler and read by the web console.

Web requests never call Docker or probe endpoints themselves; they read the last snapshot, so any number
of open browser tabs costs nothing extra.
"""

from __future__ import annotations

import time
from collections.abc import Callable
from typing import Any

SYSTEM = "system"
CONTAINERS = "containers"
PROBES = "probes"


class SnapshotCache:
    def __init__(self, clock: Callable[[], float] = time.time) -> None:
        self._clock = clock
        self._data: dict[str, tuple[Any, float]] = {}

    def set(self, key: str, value: Any) -> None:
        self._data[key] = (value, self._clock())

    def snapshot(self, key: str) -> dict[str, Any]:
        entry = self._data.get(key)
        if entry is None:
            return {"data": None, "updated_at": None, "age_seconds": None}
        value, updated_at = entry
        return {"data": value, "updated_at": updated_at, "age_seconds": round(self._clock() - updated_at, 1)}
```

- [ ] **Step 4: Wire the scheduler**

In `src/opspilot/automation/scheduler.py`:

1. Add imports: `import time` (if absent) and `from opspilot.core.snapshot import CONTAINERS, PROBES, SYSTEM, SnapshotCache`.
2. In `BackgroundScheduler.__init__`, add the parameter `snapshots: SnapshotCache | None = None,` after `notify_callback=None,` and the line `self.snapshots = snapshots` in the body.
3. Add the method:

```python
    def _publish(self, key: str, value) -> None:
        if self.snapshots is not None:
            self.snapshots.set(key, value)
```

4. In `_run_health_loop`, directly after `metrics = await asyncio.to_thread(collect_system_metrics)` add `self._publish(SYSTEM, metrics.model_dump())`, and directly after `containers = await asyncio.to_thread(collect_docker_statuses)` add `self._publish(CONTAINERS, [c.model_dump() for c in containers])`.
5. In `_run_probe_loop`, right after `results = await asyncio.gather(*tasks, return_exceptions=True)` and before the existing `for ep, result in zip(...)` loop, add:

```python
        checked: dict[str, dict] = {}
        now = time.time()
        for ep, result in zip(endpoints, results, strict=True):
            if isinstance(result, BaseException):
                continue
            checked[ep["name"]] = {
                "status_code": result.status_code,
                "latency_ms": result.latency_ms,
                "is_healthy": result.is_healthy,
                "error": result.error,
                "checked_at": now,
            }
        self._publish(PROBES, checked)
```

- [ ] **Step 5: Run and commit**

```bash
uv run pytest -q && uv run ruff check src tests && uv run ruff format --check src tests
git add src/opspilot/core/snapshot.py src/opspilot/automation/scheduler.py tests/test_snapshot.py
git commit -m "feat: publish system, container and probe snapshots for the web console" -m "Co-Authored-By: Claude Sonnet 5.5 <noreply@anthropic.com>"
```

---

## Task 6: Password hashing, credential checks and `opspilot web hash-password`

**Files:**
- Create: `src/opspilot/web/__init__.py` (empty — must not import fastapi), `src/opspilot/web/passwords.py`
- Modify: `src/opspilot/cli.py`
- Test: `tests/test_web_passwords.py`, `tests/test_cli_web.py`

**Interfaces:**
- Consumes: nothing.
- Produces (stdlib only):
  - `MIN_PASSWORD_LENGTH = 12`
  - `hash_password(password: str, iterations: int = 600_000) -> str` → `pbkdf2_sha256:<iters>:<salt_b64>:<hash_b64>`
  - `is_valid_hash(stored: str) -> bool`, `verify_hash(password: str, stored: str) -> bool`
  - `CredentialVerifier(password: str = "", password_hash: str = "")` with `.verify(candidate: str) -> bool`
  - `validate_web_credentials(password: str, password_hash: str) -> list[str]` (empty list = OK)

- [ ] **Step 1: Write the failing tests**

Create `tests/test_web_passwords.py`:

```python
import pytest

from opspilot.web.passwords import (
    MIN_PASSWORD_LENGTH,
    CredentialVerifier,
    hash_password,
    is_valid_hash,
    validate_web_credentials,
    verify_hash,
)

GOOD = "correct horse battery"


def test_hash_roundtrip_and_format():
    stored = hash_password(GOOD, iterations=1000)
    assert stored.startswith("pbkdf2_sha256:1000:")
    assert "$" not in stored, "docker compose interpolates $ in env files"
    assert is_valid_hash(stored)
    assert verify_hash(GOOD, stored) is True
    assert verify_hash("wrong password!!", stored) is False


def test_hashes_are_salted():
    assert hash_password(GOOD, iterations=1000) != hash_password(GOOD, iterations=1000)


@pytest.mark.parametrize(
    "bad",
    [
        "",
        "plain",
        "pbkdf2_sha256:1000:abc",
        "md5:1000:AAAA:AAAA",
        "pbkdf2_sha256:x:AAAA:AAAA",
        "pbkdf2_sha256:0:AAAA:AAAA",
        "pbkdf2_sha256:1000:!!!!:AAAA",
    ],
)
def test_malformed_hashes_are_invalid_and_never_verify(bad):
    assert is_valid_hash(bad) is False
    assert verify_hash(GOOD, bad) is False


def test_verifier_plaintext():
    v = CredentialVerifier(password=GOOD)
    assert v.verify(GOOD) is True
    assert v.verify(GOOD + "x") is False
    assert v.verify("") is False


def test_verifier_prefers_hash_over_plaintext():
    v = CredentialVerifier(password="some other password", password_hash=hash_password(GOOD, iterations=1000))
    assert v.verify(GOOD) is True
    assert v.verify("some other password") is False


def test_verifier_without_credentials_never_verifies():
    v = CredentialVerifier()
    assert v.verify("") is False
    assert v.verify("anything at all") is False


def test_validate_credentials():
    assert validate_web_credentials(GOOD, "") == []
    assert validate_web_credentials("", hash_password(GOOD, iterations=1000)) == []
    assert validate_web_credentials("", "") != []
    assert validate_web_credentials("   ", "") != []
    assert validate_web_credentials("x" * (MIN_PASSWORD_LENGTH - 1), "") != []
    assert validate_web_credentials(" " * 20, "") != []
    assert validate_web_credentials("", "not-a-hash") != []
```

Create `tests/test_cli_web.py`:

```python
from typer.testing import CliRunner

from opspilot.cli import app
from opspilot.web.passwords import verify_hash

runner = CliRunner()


def test_hash_password_command_prints_a_usable_hash():
    result = runner.invoke(app, ["web", "hash-password"], input="a-very-long-password\na-very-long-password\n")
    assert result.exit_code == 0
    line = next(x for x in result.output.splitlines() if x.startswith("OPSPILOT_ADMIN_PASSWORD_HASH="))
    assert verify_hash("a-very-long-password", line.split("=", 1)[1])


def test_hash_password_command_rejects_short_passwords():
    result = runner.invoke(app, ["web", "hash-password"], input="short\nshort\n")
    assert result.exit_code == 1
    assert "OPSPILOT_ADMIN_PASSWORD_HASH=" not in result.output
```

- [ ] **Step 2: Run and confirm failure**

Run: `uv run pytest tests/test_web_passwords.py tests/test_cli_web.py -v`
Expected: `ModuleNotFoundError: opspilot.web`.

- [ ] **Step 3: Implement**

`src/opspilot/web/__init__.py`: empty file.

`src/opspilot/web/passwords.py`:

```python
"""Password hashing and credential checks for the web console. Standard library only."""

from __future__ import annotations

import base64
import hashlib
import hmac
import secrets

_SCHEME = "pbkdf2_sha256"
DEFAULT_ITERATIONS = 600_000
MAX_ITERATIONS = 10_000_000
MIN_PASSWORD_LENGTH = 12


def _b64(data: bytes) -> str:
    return base64.b64encode(data).decode("ascii")


def hash_password(password: str, iterations: int = DEFAULT_ITERATIONS) -> str:
    """Return `pbkdf2_sha256:<iterations>:<salt_b64>:<hash_b64>` (no `$`, safe in docker env files)."""
    salt = secrets.token_bytes(16)
    digest = hashlib.pbkdf2_hmac("sha256", password.encode("utf-8"), salt, iterations)
    return f"{_SCHEME}:{iterations}:{_b64(salt)}:{_b64(digest)}"


def _parse(stored: str) -> tuple[int, bytes, bytes] | None:
    parts = stored.strip().split(":")
    if len(parts) != 4 or parts[0] != _SCHEME:
        return None
    try:
        iterations = int(parts[1])
        salt = base64.b64decode(parts[2], validate=True)
        digest = base64.b64decode(parts[3], validate=True)
    except ValueError:
        return None
    if not 0 < iterations <= MAX_ITERATIONS or not salt or not digest:
        return None
    return iterations, salt, digest


def is_valid_hash(stored: str) -> bool:
    return _parse(stored) is not None


def verify_hash(password: str, stored: str) -> bool:
    parsed = _parse(stored)
    if parsed is None:
        return False
    iterations, salt, expected = parsed
    actual = hashlib.pbkdf2_hmac("sha256", password.encode("utf-8"), salt, iterations)
    return hmac.compare_digest(actual, expected)


class CredentialVerifier:
    """Checks a login attempt against the configured credential in constant time."""

    def __init__(self, password: str = "", password_hash: str = "") -> None:
        self._hash = password_hash.strip()
        self._plain_digest: bytes | None = None
        if not self._hash and password.strip():
            self._plain_digest = hashlib.sha256(password.encode("utf-8")).digest()

    def verify(self, candidate: str) -> bool:
        if self._hash:
            return verify_hash(candidate, self._hash)
        if self._plain_digest is None:
            return False
        return hmac.compare_digest(hashlib.sha256(candidate.encode("utf-8")).digest(), self._plain_digest)


def validate_web_credentials(password: str, password_hash: str) -> list[str]:
    """Return a list of problems; an empty list means the web console may start."""
    if password_hash.strip():
        if is_valid_hash(password_hash):
            return []
        return [
            "OPSPILOT_ADMIN_PASSWORD_HASH is not a valid pbkdf2_sha256 hash (create one with: opspilot web hash-password)"
        ]
    if not password.strip():
        return ["no admin credential set: provide OPSPILOT_ADMIN_PASSWORD_HASH (preferred) or OPSPILOT_ADMIN_PASSWORD"]
    if len(password) < MIN_PASSWORD_LENGTH:
        return [f"OPSPILOT_ADMIN_PASSWORD must be at least {MIN_PASSWORD_LENGTH} characters"]
    return []
```

In `src/opspilot/cli.py`, after `console = Console()` add:

```python
web_app = typer.Typer(help="Web console helpers")
app.add_typer(web_app, name="web")


@web_app.command("hash-password")
def hash_password_command():
    """Create a value for OPSPILOT_ADMIN_PASSWORD_HASH."""
    from opspilot.web.passwords import MIN_PASSWORD_LENGTH, hash_password

    password = typer.prompt("Admin password", hide_input=True, confirmation_prompt=True)
    if not password.strip() or len(password) < MIN_PASSWORD_LENGTH:
        console.print(f"[red]Password must be at least {MIN_PASSWORD_LENGTH} characters.[/red]")
        raise typer.Exit(code=1)
    typer.echo("Add this line to your .env:")
    typer.echo(f"OPSPILOT_ADMIN_PASSWORD_HASH={hash_password(password)}")
```

- [ ] **Step 4: Run and commit**

```bash
uv run pytest tests/test_web_passwords.py tests/test_cli_web.py -v
uv run ruff check src tests && uv run ruff format --check src tests
git add src/opspilot/web/__init__.py src/opspilot/web/passwords.py src/opspilot/cli.py tests/test_web_passwords.py tests/test_cli_web.py
git commit -m "feat(web): PBKDF2 credentials and 'opspilot web hash-password' helper" -m "Co-Authored-By: Claude Sonnet 5.5 <noreply@anthropic.com>"
```

---

## Task 7: Sessions, login throttle and network helpers

**Files:**
- Create: `src/opspilot/web/sessions.py`, `src/opspilot/web/netutil.py`
- Test: `tests/test_web_sessions.py`, `tests/test_web_netutil.py`

**Interfaces:**
- Consumes: nothing (stdlib only).
- Produces:
  - `Session` dataclass: `token, csrf_token, created_at, last_seen, ip`.
  - `SessionStore(ttl: float = 28800, absolute_ttl: float = 86400, clock=time.monotonic)` with `.ttl`, `.create(ip: str) -> Session`, `.get(token: str | None) -> Session | None` (rolling, touches `last_seen`), `.peek(token) -> Session | None` (validates without touching), `.revoke(token: str | None) -> None`.
  - `LoginThrottle(max_failures=5, window=900.0, max_backoff=300.0, clock=time.monotonic)` with `.retry_after(ip) -> float`, `.record_failure(ip)`, `.reset(ip)`.
  - `parse_trusted(raw: str) -> tuple[IPv4Network | IPv6Network, ...]` (raises `ValueError` on junk), `client_ip(peer: str | None, xff: str | None, trusted) -> str`, `origin_matches_host(origin: str | None, referer: str | None, host: str | None) -> bool`.

- [ ] **Step 1: Write the failing tests**

Create `tests/test_web_sessions.py`:

```python
from opspilot.web.sessions import LoginThrottle, SessionStore


class Clock:
    def __init__(self):
        self.now = 1000.0

    def __call__(self):
        return self.now

    def advance(self, seconds: float):
        self.now += seconds


HOUR = 3600.0


def test_session_create_get_revoke():
    clock = Clock()
    store = SessionStore(clock=clock)
    s = store.create("1.2.3.4")
    assert len(s.token) >= 40 and s.token != s.csrf_token
    assert store.get(s.token) is s
    store.revoke(s.token)
    assert store.get(s.token) is None
    assert store.get(None) is None
    assert store.get("garbage") is None


def test_ttl_is_rolling_but_absolute_limit_applies():
    clock = Clock()
    store = SessionStore(ttl=8 * HOUR, absolute_ttl=24 * HOUR, clock=clock)
    s = store.create("1.2.3.4")
    for _ in range(3):  # touch every 7h: stays alive through 21h
        clock.advance(7 * HOUR)
        assert store.get(s.token) is not None
    clock.advance(7 * HOUR)  # 28h since creation > 24h absolute limit
    assert store.get(s.token) is None


def test_idle_session_expires():
    clock = Clock()
    store = SessionStore(ttl=8 * HOUR, clock=clock)
    s = store.create("1.2.3.4")
    clock.advance(8 * HOUR + 1)
    assert store.get(s.token) is None


def test_peek_does_not_extend_the_session():
    clock = Clock()
    store = SessionStore(ttl=8 * HOUR, clock=clock)
    s = store.create("1.2.3.4")
    clock.advance(7 * HOUR)
    assert store.peek(s.token) is not None
    clock.advance(2 * HOUR)  # 9h since last real use
    assert store.get(s.token) is None


def test_throttle_backoff_and_lockout():
    clock = Clock()
    t = LoginThrottle(clock=clock)
    assert t.retry_after("ip") == 0.0
    t.record_failure("ip")
    assert 0 < t.retry_after("ip") <= 1.0  # 1s after the first failure
    clock.advance(1.1)
    assert t.retry_after("ip") == 0.0
    t.record_failure("ip")
    assert 1.0 < t.retry_after("ip") <= 2.0  # 2s after the second
    for _ in range(3):
        clock.advance(10)
        t.record_failure("ip")
    assert t.retry_after("ip") > 600  # 5 failures inside the 15 minute window: locked
    clock.advance(900)
    assert t.retry_after("ip") == 0.0


def test_throttle_tracks_addresses_independently_and_resets():
    clock = Clock()
    t = LoginThrottle(clock=clock)
    t.record_failure("a")
    assert t.retry_after("b") == 0.0
    t.reset("a")
    assert t.retry_after("a") == 0.0
```

Create `tests/test_web_netutil.py`:

```python
import pytest

from opspilot.web.netutil import client_ip, origin_matches_host, parse_trusted

LOCAL = parse_trusted("127.0.0.1")


def test_untrusted_peer_cannot_spoof_forwarded_for():
    assert client_ip("203.0.113.9", "198.51.100.1", LOCAL) == "203.0.113.9"


def test_trusted_peer_uses_forwarded_client():
    assert client_ip("127.0.0.1", "198.51.100.1", LOCAL) == "198.51.100.1"


def test_rightmost_untrusted_hop_wins_over_client_supplied_prefix():
    # client sent "6.6.6.6"; our proxy appended the real address
    assert client_ip("127.0.0.1", "6.6.6.6, 198.51.100.1", LOCAL) == "198.51.100.1"


def test_chain_of_trusted_proxies():
    trusted = parse_trusted("127.0.0.1, 10.0.0.0/8")
    assert client_ip("127.0.0.1", "198.51.100.1, 10.1.2.3", trusted) == "198.51.100.1"


@pytest.mark.parametrize("header", ["", "not-an-ip", "198.51.100.1, garbage", ",,,", "999.1.1.1"])
def test_malformed_forwarded_for_falls_back_to_peer(header):
    assert client_ip("127.0.0.1", header, LOCAL) == "127.0.0.1"


def test_missing_peer_and_header():
    assert client_ip(None, None, LOCAL) == "unknown"
    assert client_ip("127.0.0.1", None, LOCAL) == "127.0.0.1"
    assert client_ip("testclient", "1.2.3.4", LOCAL) == "testclient"  # non-IP peers are never trusted


def test_parse_trusted_rejects_junk_and_allows_empty():
    with pytest.raises(ValueError):
        parse_trusted("localhost")
    assert parse_trusted("") == ()


@pytest.mark.parametrize(
    "origin,referer,host,expected",
    [
        ("https://ops.example.com", None, "ops.example.com", True),
        ("https://OPS.example.com", None, "ops.example.com", True),
        ("https://evil.example.net", None, "ops.example.com", False),
        (None, "https://ops.example.com/page", "ops.example.com", True),
        (None, "https://evil.example.net/x", "ops.example.com", False),
        (None, None, "ops.example.com", False),
        ("null", None, "ops.example.com", False),
        ("https://ops.example.com", None, None, False),
        ("https://ops.example.com:8443", None, "ops.example.com", False),
    ],
)
def test_origin_matches_host(origin, referer, host, expected):
    assert origin_matches_host(origin, referer, host) is expected
```

- [ ] **Step 2: Run and confirm failure**

Run: `uv run pytest tests/test_web_sessions.py tests/test_web_netutil.py -v`
Expected: `ModuleNotFoundError` for `opspilot.web.sessions` / `netutil`.

- [ ] **Step 3: Implement `sessions.py`**

```python
"""In-memory sessions and login throttling. Standard library only."""

from __future__ import annotations

import secrets
import time
from collections.abc import Callable
from dataclasses import dataclass


@dataclass
class Session:
    token: str
    csrf_token: str
    created_at: float
    last_seen: float
    ip: str


class SessionStore:
    def __init__(
        self,
        ttl: float = 8 * 3600,
        absolute_ttl: float = 24 * 3600,
        clock: Callable[[], float] = time.monotonic,
    ) -> None:
        self.ttl = ttl
        self._absolute_ttl = absolute_ttl
        self._clock = clock
        self._sessions: dict[str, Session] = {}

    def create(self, ip: str) -> Session:
        now = self._clock()
        session = Session(
            token=secrets.token_urlsafe(32),
            csrf_token=secrets.token_urlsafe(32),
            created_at=now,
            last_seen=now,
            ip=ip,
        )
        self._sessions[session.token] = session
        return session

    def _valid(self, session: Session) -> bool:
        now = self._clock()
        return now - session.last_seen <= self.ttl and now - session.created_at <= self._absolute_ttl

    def peek(self, token: str | None) -> Session | None:
        """Validate without extending the session (used by long-lived connections)."""
        if not token:
            return None
        session = self._sessions.get(token)
        if session is None:
            return None
        if not self._valid(session):
            self._sessions.pop(token, None)
            return None
        return session

    def get(self, token: str | None) -> Session | None:
        session = self.peek(token)
        if session is not None:
            session.last_seen = self._clock()
        return session

    def revoke(self, token: str | None) -> None:
        if token:
            self._sessions.pop(token, None)


class LoginThrottle:
    """Per-address exponential backoff, then a hard lockout after `max_failures` inside `window`."""

    def __init__(
        self,
        max_failures: int = 5,
        window: float = 900.0,
        max_backoff: float = 300.0,
        clock: Callable[[], float] = time.monotonic,
    ) -> None:
        self._max = max_failures
        self._window = window
        self._max_backoff = max_backoff
        self._clock = clock
        self._failures: dict[str, list[float]] = {}

    def _recent(self, ip: str) -> list[float]:
        now = self._clock()
        recent = [t for t in self._failures.get(ip, []) if now - t < self._window]
        if recent:
            self._failures[ip] = recent
        else:
            self._failures.pop(ip, None)
        return recent

    def retry_after(self, ip: str) -> float:
        """Seconds the caller must wait; 0.0 means an attempt is allowed."""
        recent = self._recent(ip)
        if not recent:
            return 0.0
        now = self._clock()
        if len(recent) >= self._max:
            return max(0.0, self._window - (now - recent[0]))
        delay = min(2.0 ** (len(recent) - 1), self._max_backoff)
        return max(0.0, delay - (now - recent[-1]))

    def record_failure(self, ip: str) -> None:
        if len(self._failures) > 10_000:  # bound memory under a spray of addresses
            for key in list(self._failures):
                self._recent(key)
        self._failures.setdefault(ip, []).append(self._clock())

    def reset(self, ip: str) -> None:
        self._failures.pop(ip, None)
```

- [ ] **Step 4: Implement `netutil.py`**

```python
"""Client address and origin helpers. Standard library only."""

from __future__ import annotations

import ipaddress
from urllib.parse import urlparse

Network = ipaddress.IPv4Network | ipaddress.IPv6Network


def parse_trusted(raw: str) -> tuple[Network, ...]:
    """Parse a comma-separated list of IPs/CIDRs. Raises ValueError on anything else."""
    networks: list[Network] = []
    for part in raw.split(","):
        part = part.strip()
        if part:
            networks.append(ipaddress.ip_network(part, strict=False))
    return tuple(networks)


def _parse_ip(value: str) -> ipaddress.IPv4Address | ipaddress.IPv6Address | None:
    try:
        return ipaddress.ip_address(value)
    except ValueError:
        return None


def _is_trusted(value: str, trusted: tuple[Network, ...]) -> bool:
    address = _parse_ip(value)
    return address is not None and any(address in network for network in trusted)


def client_ip(peer: str | None, xff: str | None, trusted: tuple[Network, ...]) -> str:
    """Real client address. X-Forwarded-For is honoured only when the TCP peer is a trusted proxy."""
    peer = peer or "unknown"
    if not xff or not _is_trusted(peer, trusted):
        return peer
    hops = [h.strip() for h in xff.split(",") if h.strip()]
    if not hops:
        return peer
    for hop in reversed(hops):
        if _parse_ip(hop) is None:
            return peer  # malformed header: do not trust any of it
        if not _is_trusted(hop, trusted):
            return hop
    return hops[0]


def origin_matches_host(origin: str | None, referer: str | None, host: str | None) -> bool:
    """True when the Origin (or, failing that, Referer) host equals the request Host header."""
    source = origin or referer
    if not source or not host:
        return False
    return urlparse(source).netloc.lower() == host.lower()
```

- [ ] **Step 5: Run and commit**

```bash
uv run pytest tests/test_web_sessions.py tests/test_web_netutil.py -v
uv run ruff check src tests && uv run ruff format --check src tests
git add src/opspilot/web/sessions.py src/opspilot/web/netutil.py tests/test_web_sessions.py tests/test_web_netutil.py
git commit -m "feat(web): sessions, login throttle, trusted-proxy client IP and origin checks" -m "Co-Authored-By: Claude Sonnet 5.5 <noreply@anthropic.com>"
```

---

## Task 8: Web app core — auth routes, CSRF, security headers

**Files:**
- Create: `src/opspilot/web/deps.py`, `web/schemas.py`, `web/auth.py`, `web/app.py`, `web/routes/__init__.py` (empty), placeholder `web/static/index.html` and `web/static/login.html`
- Test: `tests/web/web_helpers.py`, `tests/web/conftest.py`, `tests/web/test_security.py`

**Interfaces:**
- Consumes: `Settings`, `SessionStore`, `LoginThrottle`, `CredentialVerifier`, `client_ip`, `origin_matches_host`, `AuditLogger`, `SnapshotCache`, `ContainerStatus`.
- Produces:
  - `WebDeps` dataclass (fields below) stored on `app.state.deps`.
  - `ApiError(status: int, code: str, message: str, headers: dict | None = None)`.
  - `auth.get_deps(request) -> WebDeps`, `auth.request_ip(request) -> str`, `auth.audit_event(request, action, target, status, details=None) -> None`, `auth.require_session(request) -> Session`, `auth.require_csrf(request, session) -> Session` (FastAPI dependencies).
  - `create_app(deps: WebDeps) -> FastAPI`, `COOKIE_NAME = "opspilot_session"`, `STATIC_DIR: Path`.
  - Routes: `GET /login`, `GET /`, `GET /healthz`, `POST /api/auth/login`, `POST /api/auth/logout`, `GET /api/auth/session`.

- [ ] **Step 1: Test helpers and fixtures**

Create `tests/web/web_helpers.py`:

```python
"""Shared helpers for the web tests (imported as `web_helpers`)."""

import asyncio
from types import SimpleNamespace

import pytest

pytest.importorskip("fastapi")
pytest.importorskip("httpx")

from fastapi.testclient import TestClient  # noqa: E402

from opspilot.config import Settings  # noqa: E402
from opspilot.core.audit import AuditLogger  # noqa: E402
from opspilot.core.snapshot import SnapshotCache  # noqa: E402
from opspilot.db.engine import init_db, set_db_path  # noqa: E402
from opspilot.monitor.docker import ContainerStatus  # noqa: E402
from opspilot.web.app import create_app  # noqa: E402
from opspilot.web.deps import WebDeps  # noqa: E402
from opspilot.web.netutil import parse_trusted  # noqa: E402
from opspilot.web.passwords import CredentialVerifier  # noqa: E402
from opspilot.web.sessions import LoginThrottle, SessionStore  # noqa: E402

PASSWORD = "correct horse battery"


class FakeClock:
    def __init__(self):
        self.now = 1000.0

    def __call__(self) -> float:
        return self.now

    def advance(self, seconds: float) -> None:
        self.now += seconds


class FakeExecutor:
    def __init__(self):
        self.restarted: list[str] = []
        self.logs_text = "line one\nline two\n"
        self.tail_seen: int | None = None
        self.restart_ok = True

    async def restart_container(self, name: str) -> dict:
        if not self.restart_ok:
            return {"success": False, "message": "boom"}
        self.restarted.append(name)
        return {"success": True, "message": f"Container {name} restarted successfully."}

    async def get_container_logs(self, name: str, tail: int = 50) -> str:
        self.tail_seen = tail
        return self.logs_text


def run(coro):
    return asyncio.run(coro)


def make_env(tmp_path, *, secure: bool = True, containers: list[ContainerStatus] | None = None) -> SimpleNamespace:
    set_db_path(tmp_path / "web.db")
    run(init_db())
    clock = FakeClock()
    executor = FakeExecutor()
    audit_dir = tmp_path / "audit"
    audit = AuditLogger(str(audit_dir))
    snapshots = SnapshotCache(clock=clock)
    items = containers or [
        ContainerStatus(id="1", name="api", image="example/api:1", status="running", health="healthy", created="x"),
        ContainerStatus(id="2", name="worker", image="example/worker:1", status="exited", health="none", created="x"),
    ]

    async def list_containers():
        return items

    settings = Settings(_env_file=None, server_name="test-node", web_enabled=True, admin_password=PASSWORD)
    deps = WebDeps(
        settings=settings,
        executor=executor,
        audit=audit,
        snapshots=snapshots,
        list_containers=list_containers,
        verifier=CredentialVerifier(PASSWORD),
        sessions=SessionStore(clock=clock),
        throttle=LoginThrottle(clock=clock),
        trusted_proxies=parse_trusted("127.0.0.1"),
        secure_cookies=secure,
        live_interval=0.05,
    )
    scheme = "https" if secure else "http"
    client = TestClient(create_app(deps), base_url=f"{scheme}://testserver")
    return SimpleNamespace(
        client=client,
        deps=deps,
        clock=clock,
        executor=executor,
        snapshots=snapshots,
        origin=f"{scheme}://testserver",
        audit_file=audit_dir / "audit_trail.jsonl",
    )


def login(env, password: str = PASSWORD, headers: dict | None = None):
    merged = {"Origin": env.origin}
    merged.update(headers or {})
    return env.client.post("/api/auth/login", json={"password": password}, headers=merged)


def authed_headers(env) -> dict:
    """Log in (if needed) and return the headers a browser would send on a mutating request."""
    if login(env).status_code != 200:
        raise AssertionError("login failed in test setup")
    token = env.client.get("/api/auth/session").json()["csrf_token"]
    return {"X-CSRF-Token": token, "Origin": env.origin}


def audit_entries(env) -> list[dict]:
    import json

    if not env.audit_file.exists():
        return []
    return [json.loads(line) for line in env.audit_file.read_text().splitlines() if line.strip()]
```

Create `tests/web/conftest.py`:

```python
import pytest
from web_helpers import make_env


@pytest.fixture
def env(tmp_path):
    return make_env(tmp_path)
```

- [ ] **Step 2: Write the failing security tests**

Create `tests/web/test_security.py`:

```python
import pytest
from web_helpers import PASSWORD, audit_entries, authed_headers, login, make_env


def test_api_requires_a_session(env):
    r = env.client.get("/api/auth/session")
    assert r.status_code == 401
    assert r.json() == {"error": "unauthorized", "message": "Login required."}


def test_login_success_sets_a_hardened_cookie(env):
    r = login(env)
    assert r.status_code == 200 and r.json() == {"ok": True}
    cookie = r.headers["set-cookie"].lower()
    assert "opspilot_session=" in cookie
    assert "httponly" in cookie
    assert "secure" in cookie
    assert "samesite=strict" in cookie
    assert "path=/" in cookie
    session = env.client.get("/api/auth/session").json()
    assert session["server_name"] == "test-node"
    assert len(session["csrf_token"]) >= 40


def test_insecure_cookie_override_drops_secure_flag(tmp_path):
    env = make_env(tmp_path, secure=False)
    r = login(env)
    assert r.status_code == 200
    assert "secure" not in r.headers["set-cookie"].lower()


def test_login_wrong_password_is_401_and_audited(env):
    r = login(env, password="wrong password here")
    assert r.status_code == 401
    assert r.json()["error"] == "invalid_credentials"
    entry = audit_entries(env)[-1]
    assert entry["user_id"] == "web-admin"
    assert entry["action"] == "login" and entry["status"] == "BLOCKED"
    assert "ip" in entry["details"]


def test_login_success_is_audited(env):
    login(env)
    entry = audit_entries(env)[-1]
    assert entry["action"] == "login" and entry["status"] == "SUCCESS"


@pytest.mark.parametrize("headers", [{"Origin": "https://evil.example.net"}, {}])
def test_login_rejects_foreign_or_missing_origin(env, headers):
    r = env.client.post("/api/auth/login", json={"password": PASSWORD}, headers=headers)
    assert r.status_code == 403
    assert r.json()["error"] == "bad_origin"


def test_second_immediate_failure_is_backed_off(env):
    assert login(env, "wrong password 1").status_code == 401
    r = login(env, "wrong password 2")
    assert r.status_code == 429
    assert int(r.headers["retry-after"]) >= 1


def test_five_failures_lock_the_address_out_even_with_the_right_password(env):
    for _ in range(5):
        assert login(env, "wrong password!").status_code == 401
        env.clock.advance(10)
    r = login(env)  # correct password, but locked
    assert r.status_code == 429
    assert int(r.headers["retry-after"]) > 60
    env.clock.advance(900)
    assert login(env).status_code == 200


def test_spoofed_forwarded_for_does_not_bypass_the_lockout(env):
    for i in range(5):
        r = login(env, "wrong password!", headers={"X-Forwarded-For": f"198.51.100.{i}"})
        assert r.status_code == 401
        env.clock.advance(10)
    r = login(env, headers={"X-Forwarded-For": "203.0.113.77"})
    assert r.status_code == 429  # the TCP peer is untrusted, so the header is ignored


def test_mutation_needs_csrf_token(env):
    login(env)
    r = env.client.post("/api/auth/logout", headers={"Origin": env.origin})
    assert r.status_code == 403 and r.json()["error"] == "csrf"
    r = env.client.post("/api/auth/logout", headers={"Origin": env.origin, "X-CSRF-Token": "wrong"})
    assert r.status_code == 403
    assert any(e["action"] == "csrf_blocked" and e["status"] == "BLOCKED" for e in audit_entries(env))


def test_mutation_rejects_foreign_origin_even_with_valid_token(env):
    headers = authed_headers(env)
    headers["Origin"] = "https://evil.example.net"
    r = env.client.post("/api/auth/logout", headers=headers)
    assert r.status_code == 403 and r.json()["error"] == "bad_origin"


def test_logout_revokes_the_session_server_side(env):
    headers = authed_headers(env)
    stolen_cookie = env.client.cookies.get("opspilot_session")
    assert env.client.post("/api/auth/logout", headers=headers).status_code == 200
    env.client.cookies.set("opspilot_session", stolen_cookie)  # replay the old cookie
    assert env.client.get("/api/auth/session").status_code == 401


def test_expired_session_on_a_mutation_is_401_not_403(env):
    headers = authed_headers(env)
    env.clock.advance(9 * 3600)
    r = env.client.post("/api/auth/logout", headers=headers)
    assert r.status_code == 401  # so the UI redirects to login instead of showing a CSRF error


def test_security_headers_on_every_kind_of_response(env):
    for path in ("/login", "/healthz", "/api/auth/session", "/static/login.html"):
        r = env.client.get(path)
        assert "default-src 'self'" in r.headers["content-security-policy"], path
        assert "frame-ancestors 'none'" in r.headers["content-security-policy"], path
        assert "script-src 'self'" in r.headers["content-security-policy"], path
        assert r.headers["x-content-type-options"] == "nosniff", path
        assert r.headers["referrer-policy"] == "no-referrer", path
    assert env.client.get("/api/auth/session").headers["cache-control"] == "no-store"


def test_healthz_reveals_nothing(env):
    r = env.client.get("/healthz")
    assert r.status_code == 200 and r.json() == {"status": "ok"}


def test_index_redirects_to_login_without_a_session(env):
    r = env.client.get("/", follow_redirects=False)
    assert r.status_code == 303 and r.headers["location"] == "/login"
    login(env)
    assert env.client.get("/", follow_redirects=False).status_code == 200


def test_interactive_docs_are_not_exposed(env):
    for path in ("/docs", "/redoc", "/openapi.json"):
        assert env.client.get(path).status_code == 404
```

- [ ] **Step 3: Run and confirm failure**

Run: `uv run pytest tests/web/test_security.py -v`
Expected: collection error `ModuleNotFoundError: opspilot.web.app` (install extras first if `fastapi` is missing: `uv pip install -e ".[ai,web,dev]"`).

- [ ] **Step 4: Implement `deps.py` and `schemas.py`**

`src/opspilot/web/deps.py`:

```python
"""Dependencies shared by the web routes, stored on `app.state.deps`."""

from __future__ import annotations

from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field
from typing import Any

from opspilot.config import Settings
from opspilot.core.audit import AuditLogger
from opspilot.core.snapshot import SnapshotCache
from opspilot.monitor.docker import ContainerStatus
from opspilot.web.netutil import Network
from opspilot.web.passwords import CredentialVerifier
from opspilot.web.sessions import LoginThrottle, SessionStore


@dataclass
class WebDeps:
    settings: Settings
    executor: Any  # SafeOperationExecutor (or a test fake)
    audit: AuditLogger
    snapshots: SnapshotCache
    list_containers: Callable[[], Awaitable[list[ContainerStatus]]]
    verifier: CredentialVerifier
    channel: Any = None  # NotificationChannel | None
    sessions: SessionStore = field(default_factory=SessionStore)
    throttle: LoginThrottle = field(default_factory=LoginThrottle)
    trusted_proxies: tuple[Network, ...] = ()
    secure_cookies: bool = True
    live_interval: float = 5.0
```

`src/opspilot/web/schemas.py`:

```python
"""Request bodies for the web API."""

from __future__ import annotations

from pydantic import BaseModel, Field


class LoginBody(BaseModel):
    password: str = Field(min_length=1, max_length=256)
```

- [ ] **Step 5: Implement `auth.py`**

```python
"""Authentication, CSRF protection and the /api/auth routes."""

from __future__ import annotations

import hmac
from typing import Any

from fastapi import APIRouter, Depends, Request, Response

from opspilot.web.deps import WebDeps
from opspilot.web.netutil import client_ip, origin_matches_host
from opspilot.web.schemas import LoginBody
from opspilot.web.sessions import Session

COOKIE_NAME = "opspilot_session"
ACTOR = "web-admin"


class ApiError(Exception):
    def __init__(self, status: int, code: str, message: str, headers: dict[str, str] | None = None) -> None:
        super().__init__(message)
        self.status = status
        self.code = code
        self.message = message
        self.headers = headers


def get_deps(request: Request) -> WebDeps:
    return request.app.state.deps


def request_ip(request: Request) -> str:
    deps = get_deps(request)
    peer = request.client.host if request.client else None
    return client_ip(peer, request.headers.get("x-forwarded-for"), deps.trusted_proxies)


def audit_event(request: Request, action: str, target: str, status: str, details: dict[str, Any] | None = None) -> None:
    info: dict[str, Any] = {"ip": request_ip(request)}
    info.update(details or {})
    get_deps(request).audit.record_action(ACTOR, action, target, status, info)


def _origin_ok(request: Request) -> bool:
    return origin_matches_host(
        request.headers.get("origin"), request.headers.get("referer"), request.headers.get("host")
    )


def require_session(request: Request) -> Session:
    session = get_deps(request).sessions.get(request.cookies.get(COOKIE_NAME))
    if session is None:
        raise ApiError(401, "unauthorized", "Login required.")
    return session


def require_csrf(request: Request, session: Session = Depends(require_session)) -> Session:
    if not _origin_ok(request):
        audit_event(request, "csrf_blocked", request.url.path, "BLOCKED", {"reason": "origin"})
        raise ApiError(403, "bad_origin", "Cross-origin request rejected.")
    supplied = request.headers.get("x-csrf-token", "")
    if not hmac.compare_digest(supplied, session.csrf_token):
        audit_event(request, "csrf_blocked", request.url.path, "BLOCKED", {"reason": "token"})
        raise ApiError(403, "csrf", "Missing or invalid CSRF token.")
    return session


router = APIRouter(prefix="/api/auth")


@router.post("/login")
async def login(body: LoginBody, request: Request, response: Response) -> dict[str, bool]:
    deps = get_deps(request)
    ip = request_ip(request)
    if not _origin_ok(request):
        audit_event(request, "login", "web", "BLOCKED", {"reason": "origin"})
        raise ApiError(403, "bad_origin", "Cross-origin login rejected.")
    wait = deps.throttle.retry_after(ip)
    if wait > 0:
        seconds = int(wait) + 1
        audit_event(request, "login", "web", "BLOCKED", {"reason": "rate_limited"})
        raise ApiError(
            429, "rate_limited", f"Too many attempts. Try again in {seconds}s.", headers={"Retry-After": str(seconds)}
        )
    if not deps.verifier.verify(body.password):
        deps.throttle.record_failure(ip)
        audit_event(request, "login", "web", "BLOCKED", {"reason": "bad_password"})
        raise ApiError(401, "invalid_credentials", "Invalid password.")
    deps.throttle.reset(ip)
    session = deps.sessions.create(ip)
    response.set_cookie(
        COOKIE_NAME,
        session.token,
        max_age=int(deps.sessions.ttl),
        httponly=True,
        secure=deps.secure_cookies,
        samesite="strict",
        path="/",
    )
    audit_event(request, "login", "web", "SUCCESS")
    return {"ok": True}


@router.post("/logout")
async def logout(request: Request, response: Response, session: Session = Depends(require_csrf)) -> dict[str, bool]:
    get_deps(request).sessions.revoke(session.token)
    response.delete_cookie(COOKIE_NAME, path="/")
    audit_event(request, "logout", "web", "SUCCESS")
    return {"ok": True}


@router.get("/session")
async def session_info(request: Request, session: Session = Depends(require_session)) -> dict[str, Any]:
    deps = get_deps(request)
    return {
        "csrf_token": session.csrf_token,
        "server_name": deps.settings.server_name,
        "ttl_seconds": int(deps.sessions.ttl),
    }
```

- [ ] **Step 6: Implement `app.py` and placeholder pages**

`src/opspilot/web/app.py`:

```python
"""FastAPI application factory for the OpsPilot web console."""

from __future__ import annotations

from pathlib import Path

from fastapi import FastAPI, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import FileResponse, JSONResponse, RedirectResponse
from fastapi.staticfiles import StaticFiles
from starlette.middleware.base import BaseHTTPMiddleware

from opspilot.web import auth
from opspilot.web.auth import COOKIE_NAME, ApiError
from opspilot.web.deps import WebDeps

STATIC_DIR = Path(__file__).parent / "static"

CSP = (
    "default-src 'self'; script-src 'self'; style-src 'self'; img-src 'self' data:; "
    "connect-src 'self'; frame-ancestors 'none'; base-uri 'none'; form-action 'self'"
)


class SecurityHeadersMiddleware(BaseHTTPMiddleware):
    async def dispatch(self, request, call_next):
        response = await call_next(request)
        response.headers["Content-Security-Policy"] = CSP
        response.headers["X-Content-Type-Options"] = "nosniff"
        response.headers["Referrer-Policy"] = "no-referrer"
        path = request.url.path
        if path.startswith("/api/") or path in ("/", "/login"):
            response.headers["Cache-Control"] = "no-store"
        return response


def create_app(deps: WebDeps) -> FastAPI:
    app = FastAPI(title="OpsPilot", docs_url=None, redoc_url=None, openapi_url=None)
    app.state.deps = deps
    app.add_middleware(SecurityHeadersMiddleware)

    @app.exception_handler(ApiError)
    async def _api_error(request: Request, exc: ApiError) -> JSONResponse:
        return JSONResponse({"error": exc.code, "message": exc.message}, status_code=exc.status, headers=exc.headers)

    @app.exception_handler(RequestValidationError)
    async def _validation_error(request: Request, exc: RequestValidationError) -> JSONResponse:
        first = exc.errors()[0]
        location = ".".join(str(part) for part in first["loc"][1:])
        message = f"{location}: {first['msg']}" if location else str(first["msg"])
        return JSONResponse({"error": "validation_error", "message": message}, status_code=422)

    app.include_router(auth.router)

    @app.get("/healthz", include_in_schema=False)
    async def healthz() -> dict[str, str]:
        return {"status": "ok"}

    @app.get("/login", include_in_schema=False)
    async def login_page() -> FileResponse:
        return FileResponse(STATIC_DIR / "login.html")

    @app.get("/", include_in_schema=False)
    async def index(request: Request):
        if deps.sessions.get(request.cookies.get(COOKIE_NAME)) is None:
            return RedirectResponse("/login", status_code=303)
        return FileResponse(STATIC_DIR / "index.html")

    app.mount("/static", StaticFiles(directory=str(STATIC_DIR)), name="static")
    return app
```

Create `src/opspilot/web/routes/__init__.py` (empty). Create minimal placeholders (Task 10 replaces them):

`src/opspilot/web/static/index.html`:
```html
<!doctype html>
<html lang="en"><head><meta charset="utf-8"><title>OpsPilot</title></head><body><p>OpsPilot</p></body></html>
```
`src/opspilot/web/static/login.html`:
```html
<!doctype html>
<html lang="en"><head><meta charset="utf-8"><title>OpsPilot login</title></head><body><p>Login</p></body></html>
```

- [ ] **Step 7: Run and commit**

```bash
uv run pytest tests/web/test_security.py -v
uv run pytest -q && uv run ruff check src tests && uv run ruff format --check src tests
git add src/opspilot/web tests/web
git commit -m "feat(web): app factory with sessions, CSRF, throttling and security headers" -m "Co-Authored-By: Claude Sonnet 5.5 <noreply@anthropic.com>"
```

---

## Task 9: API routes — overview, containers, probes, renewals, incidents

**Files:**
- Modify: `src/opspilot/web/schemas.py` (append), `src/opspilot/web/app.py` (register routers)
- Create: `src/opspilot/web/routes/overview.py`, `containers.py`, `probes.py`, `renewals.py`, `incidents.py`
- Test: `tests/web/test_api_overview.py`, `test_api_containers.py`, `test_api_probes.py`, `test_api_renewals.py`, `test_api_incidents.py`

**Interfaces:**
- Consumes (Tasks 3–8): `auth.require_session`, `auth.require_csrf`, `auth.ApiError`, `auth.audit_event`, `auth.get_deps`; `services.durations.resolve_duration`; `services.renewals.pay_renewal`, `RenewalNotFound`, `RenewalNotPending`; `db.endpoints` (`list_endpoints`, `get_endpoint`, `add_endpoint`, `remove_endpoint`, `toggle_endpoint`, `DuplicateEndpoint`); `db.renewals` (`list_renewals`, `add_renewal`, `get_renewal`, `snooze_renewal`); `db.incidents` (`list_by_state`, `count_by_state`, `count_open_incidents`); `db.snooze` (`snooze_container`, `unsnooze_container`, `list_snoozed`); snapshot keys `SYSTEM`, `CONTAINERS`, `PROBES`; `WebDeps.executor.restart_container/get_container_logs`, `WebDeps.list_containers()`.
- Produces (JSON contract the frontend in Task 10 relies on):
  - `GET /api/overview` → `{"server_name", "system": snapshot, "containers": {"total", "healthy", "updated_at", "age_seconds"}, "open_incidents"}` where `snapshot = {"data", "updated_at", "age_seconds"}`. Also `overview.build_overview(deps) -> dict` (reused by Task 13).
  - `GET /api/containers` → `{"updated_at", "age_seconds", "items": [{id, name, image, status, health, created, muted, muted_remaining}]}`.
  - `POST /api/containers/{name}/restart` → `{"ok": true, "message"}`; `GET /api/containers/{name}/logs?tail=` → `{"name", "tail", "truncated", "logs"}`; `POST /api/containers/{name}/snooze` body `{"duration"}` → `{"ok": true, "until": iso|null}`; `DELETE /api/containers/{name}/snooze` → `{"ok": true}`.
  - `GET /api/probes` → `{"updated_at", "age_seconds", "items": [{id, name, url, expected_status, timeout_seconds, enabled, last}]}` where `last` is the probe snapshot entry or `null`; `POST /api/probes` → 201 `{"id"}`; `POST /api/probes/{id}/toggle` body `{"enabled"}`; `DELETE /api/probes/{id}`.
  - `GET /api/renewals?status=&limit=&offset=` → `{"total", "items": [{id, name, category, due_date, amount, currency, notes, status, recurrence, remind_days_before, paid_at, snoozed_until, days_until_due}]}`; `POST /api/renewals` → 201 `{"id"}`; `POST /api/renewals/{id}/pay` → `{"ok": true, "next": renewal|null}`; `POST /api/renewals/{id}/snooze` body `{"duration"}`.
  - `GET /api/incidents?state=&limit=&offset=` → `{"total", "items": [{id, source, target, severity, title, detail, alert_count, resolved_at, snoozed_until, created_at, updated_at}]}`.

- [ ] **Step 1: Write the failing tests**

Create `tests/web/test_api_overview.py`:

```python
from web_helpers import authed_headers, login

from opspilot.core.snapshot import CONTAINERS, SYSTEM
from opspilot.db.incidents import open_incident
from web_helpers import run


def test_overview_requires_session(env):
    assert env.client.get("/api/overview").status_code == 401


def test_overview_before_first_monitoring_cycle(env):
    login(env)
    data = env.client.get("/api/overview").json()
    assert data["server_name"] == "test-node"
    assert data["system"]["data"] is None
    assert data["containers"]["total"] == 0
    assert data["open_incidents"] == 0


def test_overview_reads_snapshots_and_counts(env):
    env.snapshots.set(SYSTEM, {"cpu_percent": 12.0, "ram_percent": 40.0, "disk_percent": 55.0, "uptime_human": "2d"})
    env.snapshots.set(
        CONTAINERS,
        [
            {"name": "api", "status": "running", "health": "healthy"},
            {"name": "db", "status": "running", "health": "none"},
            {"name": "worker", "status": "exited", "health": "none"},
            {"name": "web", "status": "running", "health": "unhealthy"},
        ],
    )
    run(open_incident("docker", "worker", "critical", "worker down"))
    login(env)
    env.clock.advance(5)
    data = env.client.get("/api/overview").json()
    assert data["system"]["data"]["cpu_percent"] == 12.0
    assert data["system"]["age_seconds"] == 5.0
    assert data["containers"] == {
        "total": 4,
        "healthy": 2,
        "updated_at": data["containers"]["updated_at"],
        "age_seconds": 5.0,
    }
    assert data["open_incidents"] == 1
```

Create `tests/web/test_api_containers.py`:

```python
from web_helpers import audit_entries, authed_headers, login, run

from opspilot.core.snapshot import CONTAINERS
from opspilot.db import snooze as snooze_db


def test_list_containers_marks_muted_ones(env):
    env.snapshots.set(
        CONTAINERS,
        [
            {"id": "1", "name": "api", "image": "i", "status": "running", "health": "healthy", "created": "x"},
            {"id": "2", "name": "db", "image": "i", "status": "running", "health": "none", "created": "x"},
        ],
    )
    run(snooze_db.snooze_container("db", None))
    login(env)
    items = {c["name"]: c for c in env.client.get("/api/containers").json()["items"]}
    assert items["api"]["muted"] is False
    assert items["db"]["muted"] is True and items["db"]["muted_remaining"] == "Indefinite"


def test_restart_calls_executor_and_is_audited(env):
    headers = authed_headers(env)
    r = env.client.post("/api/containers/api/restart", headers=headers)
    assert r.status_code == 200 and r.json()["ok"] is True
    assert env.executor.restarted == ["api"]
    entry = audit_entries(env)[-1]
    assert (entry["user_id"], entry["action"], entry["target"], entry["status"]) == (
        "web-admin",
        "restart",
        "api",
        "SUCCESS",
    )
    assert "ip" in entry["details"]


def test_restart_unknown_container_is_404_and_never_reaches_the_executor(env):
    headers = authed_headers(env)
    r = env.client.post("/api/containers/not-a-container/restart", headers=headers)
    assert r.status_code == 404 and r.json()["error"] == "container_not_found"
    assert env.executor.restarted == []


def test_restart_failure_is_502_and_audited(env):
    env.executor.restart_ok = False
    headers = authed_headers(env)
    r = env.client.post("/api/containers/api/restart", headers=headers)
    assert r.status_code == 502 and r.json()["error"] == "restart_failed"
    assert audit_entries(env)[-1]["status"] == "FAILED"


def test_restart_requires_csrf(env):
    login(env)
    assert env.client.post("/api/containers/api/restart", headers={"Origin": env.origin}).status_code == 403
    assert env.executor.restarted == []


def test_logs_returns_text_and_respects_tail(env):
    login(env)
    r = env.client.get("/api/containers/api/logs?tail=25")
    assert r.status_code == 200
    body = r.json()
    assert body["logs"] == "line one\nline two\n" and body["truncated"] is False and body["tail"] == 25
    assert env.executor.tail_seen == 25
    assert audit_entries(env)[-1]["action"] == "logs"


def test_logs_tail_is_bounded(env):
    login(env)
    assert env.client.get("/api/containers/api/logs?tail=501").status_code == 422
    assert env.client.get("/api/containers/api/logs?tail=0").status_code == 422


def test_logs_are_capped_at_256kb_without_splitting_a_multibyte_character(env):
    env.executor.logs_text = "é" * 200_000  # 400_000 bytes; a cut at an odd byte would split a character
    login(env)
    r = env.client.get("/api/containers/api/logs?tail=500")
    assert r.status_code == 200
    body = r.json()
    assert body["truncated"] is True
    assert len(body["logs"].encode("utf-8")) <= 256 * 1024
    assert set(body["logs"]) == {"é"}


def test_logs_unknown_container_404(env):
    login(env)
    assert env.client.get("/api/containers/ghost/logs").status_code == 404


def test_snooze_and_unsnooze(env):
    headers = authed_headers(env)
    r = env.client.post("/api/containers/api/snooze", json={"duration": "1h"}, headers=headers)
    assert r.status_code == 200 and r.json()["until"] is not None
    assert run(snooze_db.is_snoozed("api")) is True
    r = env.client.post("/api/containers/api/snooze", json={"duration": "forever"}, headers=headers)
    assert r.json()["until"] is None
    assert env.client.delete("/api/containers/api/snooze", headers=headers).status_code == 200
    assert run(snooze_db.is_snoozed("api")) is False


def test_snooze_rejects_unknown_duration(env):
    headers = authed_headers(env)
    r = env.client.post("/api/containers/api/snooze", json={"duration": "2h"}, headers=headers)
    assert r.status_code == 422 and r.json()["error"] == "validation_error"
    assert run(snooze_db.is_snoozed("api")) is False
```

Create `tests/web/test_api_probes.py`:

```python
from web_helpers import audit_entries, authed_headers, login, run

from opspilot.core.snapshot import PROBES
from opspilot.db import endpoints as ep_db

NEW = {"name": "Acme API", "url": "https://api.example.com/health", "expected_status": 200}


def test_add_list_toggle_remove_probe(env):
    headers = authed_headers(env)
    r = env.client.post("/api/probes", json=NEW, headers=headers)
    assert r.status_code == 201
    probe_id = r.json()["id"]

    env.snapshots.set(
        PROBES,
        {"Acme API": {"status_code": 200, "latency_ms": 42.0, "is_healthy": True, "error": None, "checked_at": 1.0}},
    )
    items = env.client.get("/api/probes").json()["items"]
    assert len(items) == 1
    assert items[0]["name"] == "Acme API" and items[0]["enabled"] is True
    assert items[0]["last"]["latency_ms"] == 42.0

    r = env.client.post(f"/api/probes/{probe_id}/toggle", json={"enabled": False}, headers=headers)
    assert r.status_code == 200
    assert env.client.get("/api/probes").json()["items"][0]["enabled"] is False

    assert env.client.delete(f"/api/probes/{probe_id}", headers=headers).status_code == 200
    assert env.client.get("/api/probes").json()["items"] == []
    assert env.client.delete(f"/api/probes/{probe_id}", headers=headers).status_code == 404
    actions = [e["action"] for e in audit_entries(env)]
    assert {"probe_add", "probe_toggle", "probe_remove"} <= set(actions)


def test_probe_without_snapshot_has_null_last(env):
    run(ep_db.add_endpoint("Acme API", "https://api.example.com"))
    login(env)
    assert env.client.get("/api/probes").json()["items"][0]["last"] is None


def test_duplicate_probe_is_409(env):
    headers = authed_headers(env)
    assert env.client.post("/api/probes", json=NEW, headers=headers).status_code == 201
    r = env.client.post("/api/probes", json=NEW, headers=headers)
    assert r.status_code == 409 and r.json()["error"] == "duplicate_probe"


def test_removed_probe_name_can_be_added_again(env):
    headers = authed_headers(env)
    pid = env.client.post("/api/probes", json=NEW, headers=headers).json()["id"]
    env.client.delete(f"/api/probes/{pid}", headers=headers)
    assert env.client.post("/api/probes", json=NEW, headers=headers).status_code == 201


def test_probe_validation(env):
    headers = authed_headers(env)
    bad = [
        {**NEW, "url": "ftp://example.com"},
        {**NEW, "url": "javascript:alert(1)"},
        {**NEW, "url": "https://"},
        {**NEW, "name": ""},
        {**NEW, "name": "x" * 81},
        {**NEW, "expected_status": 99},
        {**NEW, "expected_status": 600},
        {**NEW, "timeout_seconds": 0},
        {**NEW, "timeout_seconds": 61},
    ]
    for payload in bad:
        r = env.client.post("/api/probes", json=payload, headers=headers)
        assert r.status_code == 422, payload
    assert env.client.get("/api/probes").json()["items"] == []


def test_toggle_missing_probe_404(env):
    headers = authed_headers(env)
    r = env.client.post("/api/probes/999/toggle", json={"enabled": True}, headers=headers)
    assert r.status_code == 404
```

Create `tests/web/test_api_renewals.py`:

```python
from datetime import date, timedelta

from web_helpers import authed_headers, login, run

from opspilot.db import renewals as ren_db

NEW = {
    "name": "Acme VPS",
    "category": "vps",
    "due_date": (date.today() + timedelta(days=10)).isoformat(),
    "amount": 1200,
    "currency": "INR",
    "recurrence": "monthly",
    "remind_days_before": 7,
}


def test_add_and_list_renewal(env):
    headers = authed_headers(env)
    r = env.client.post("/api/renewals", json=NEW, headers=headers)
    assert r.status_code == 201
    body = env.client.get("/api/renewals").json()
    assert body["total"] == 1
    item = body["items"][0]
    assert item["name"] == "Acme VPS" and item["status"] == "pending" and item["days_until_due"] == 10


def test_duplicate_renewal_is_409(env):
    headers = authed_headers(env)
    assert env.client.post("/api/renewals", json=NEW, headers=headers).status_code == 201
    r = env.client.post("/api/renewals", json=NEW, headers=headers)
    assert r.status_code == 409 and r.json()["error"] == "duplicate_renewal"


def test_pay_rolls_recurring_and_double_click_is_409_without_a_second_next(env):
    headers = authed_headers(env)
    rid = env.client.post("/api/renewals", json=NEW, headers=headers).json()["id"]
    r = env.client.post(f"/api/renewals/{rid}/pay", headers=headers)
    assert r.status_code == 200 and r.json()["next"] is not None
    again = env.client.post(f"/api/renewals/{rid}/pay", headers=headers)
    assert again.status_code == 409 and again.json()["error"] == "not_pending"
    assert len(run(ren_db.list_renewals())) == 2  # paid one + exactly one rolled-forward


def test_pay_missing_renewal_404(env):
    headers = authed_headers(env)
    assert env.client.post("/api/renewals/999/pay", headers=headers).status_code == 404


def test_filter_by_status_and_pagination(env):
    headers = authed_headers(env)
    for i in range(3):
        payload = {**NEW, "name": f"Item {i}", "recurrence": "none"}
        env.client.post("/api/renewals", json=payload, headers=headers)
    first = env.client.get("/api/renewals").json()["items"][0]["id"]
    env.client.post(f"/api/renewals/{first}/pay", headers=headers)
    assert env.client.get("/api/renewals?status=paid").json()["total"] == 1
    assert env.client.get("/api/renewals?status=pending").json()["total"] == 2
    assert env.client.get("/api/renewals?status=all").json()["total"] == 3
    page = env.client.get("/api/renewals?status=all&limit=2&offset=2").json()
    assert page["total"] == 3 and len(page["items"]) == 1
    assert env.client.get("/api/renewals?limit=101").status_code == 422
    assert env.client.get("/api/renewals?offset=-1").status_code == 422


def test_snooze_renewal(env):
    headers = authed_headers(env)
    rid = env.client.post("/api/renewals", json=NEW, headers=headers).json()["id"]
    r = env.client.post(f"/api/renewals/{rid}/snooze", json={"duration": "24h"}, headers=headers)
    assert r.status_code == 200
    assert run(ren_db.get_renewal(rid))["snoozed_until"] is not None
    r = env.client.post(f"/api/renewals/{rid}/snooze", json={"duration": "forever"}, headers=headers)
    assert r.status_code == 422 and r.json()["error"] == "invalid_duration"
    r = env.client.post("/api/renewals/999/snooze", json={"duration": "1h"}, headers=headers)
    assert r.status_code == 404


def test_renewal_validation(env):
    headers = authed_headers(env)
    bad = [
        {**NEW, "category": "gold"},
        {**NEW, "recurrence": "weekly"},
        {**NEW, "due_date": "not-a-date"},
        {**NEW, "amount": -5},
        {**NEW, "name": ""},
        {**NEW, "currency": "RUPEES"},
        {**NEW, "remind_days_before": 366},
        {**NEW, "remind_days_before": -1},
    ]
    for payload in bad:
        assert env.client.post("/api/renewals", json=payload, headers=headers).status_code == 422, payload
    assert env.client.get("/api/renewals?status=all").json()["total"] == 0


def test_html_in_names_is_stored_verbatim_for_the_client_to_render_as_text(env):
    headers = authed_headers(env)
    payload = {**NEW, "name": "<img src=x onerror=alert(1)>"}
    assert env.client.post("/api/renewals", json=payload, headers=headers).status_code == 201
    assert env.client.get("/api/renewals").json()["items"][0]["name"] == "<img src=x onerror=alert(1)>"
```

Create `tests/web/test_api_incidents.py`:

```python
from web_helpers import login, run

from opspilot.db.incidents import open_incident, resolve_incident


def test_incident_states_and_pagination(env):
    run(open_incident("docker", "api", "critical", "api down", "exited"))
    run(open_incident("http_probe", "https://example.com", "critical", "site down"))
    run(resolve_incident("http_probe", "https://example.com"))
    login(env)
    assert env.client.get("/api/incidents").json()["total"] == 1  # default: open
    resolved = env.client.get("/api/incidents?state=resolved").json()
    assert resolved["total"] == 1 and resolved["items"][0]["resolved_at"] is not None
    everything = env.client.get("/api/incidents?state=all&limit=1").json()
    assert everything["total"] == 2 and len(everything["items"]) == 1
    assert env.client.get("/api/incidents?state=bogus").status_code == 422
    assert env.client.get("/api/incidents?limit=1000").status_code == 422


def test_incidents_require_session(env):
    assert env.client.get("/api/incidents").status_code == 401
```

- [ ] **Step 2: Run and confirm failure**

Run: `uv run pytest tests/web -v`
Expected: the new API tests FAIL with 404 (routes not registered).

- [ ] **Step 3: Append the request models**

Append to `src/opspilot/web/schemas.py`:

```python
from datetime import date
from typing import Literal
from urllib.parse import urlparse

from pydantic import field_validator

Category = Literal["vps", "domain", "ssl", "software", "other"]
Recurrence = Literal["none", "monthly", "yearly"]
Duration = Literal["1h", "24h", "7d", "forever"]


class SnoozeBody(BaseModel):
    duration: Duration


class ProbeCreate(BaseModel):
    name: str = Field(min_length=1, max_length=80)
    url: str = Field(max_length=2048)
    expected_status: int = Field(default=200, ge=100, le=599)
    timeout_seconds: int = Field(default=5, ge=1, le=60)

    @field_validator("name")
    @classmethod
    def _strip_name(cls, value: str) -> str:
        value = value.strip()
        if not value:
            raise ValueError("name must not be blank")
        return value

    @field_validator("url")
    @classmethod
    def _http_url(cls, value: str) -> str:
        value = value.strip()
        parsed = urlparse(value)
        if parsed.scheme not in ("http", "https") or not parsed.netloc:
            raise ValueError("url must be an http:// or https:// address")
        return value


class ProbeToggle(BaseModel):
    enabled: bool


class RenewalCreate(BaseModel):
    name: str = Field(min_length=1, max_length=120)
    category: Category = "other"
    due_date: date
    amount: float | None = Field(default=None, ge=0)
    currency: str = Field(default="INR", min_length=3, max_length=3)
    notes: str = Field(default="", max_length=500)
    recurrence: Recurrence = "none"
    remind_days_before: int = Field(default=7, ge=0, le=365)

    @field_validator("name")
    @classmethod
    def _strip_name(cls, value: str) -> str:
        value = value.strip()
        if not value:
            raise ValueError("name must not be blank")
        return value

    @field_validator("currency")
    @classmethod
    def _upper_currency(cls, value: str) -> str:
        return value.upper()
```

(Move the new `from ... import` lines to the top of the file so ruff's import-order rule passes.)

- [ ] **Step 4: Implement the routes**

`src/opspilot/web/routes/overview.py`:

```python
from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Depends, Request

from opspilot.core.snapshot import CONTAINERS, SYSTEM
from opspilot.db import incidents as inc_db
from opspilot.web.auth import get_deps, require_session
from opspilot.web.deps import WebDeps

router = APIRouter(prefix="/api")


async def build_overview(deps: WebDeps) -> dict[str, Any]:
    system = deps.snapshots.snapshot(SYSTEM)
    containers = deps.snapshots.snapshot(CONTAINERS)
    items = containers["data"] or []
    healthy = sum(1 for c in items if c["status"] == "running" and c["health"] in ("healthy", "none"))
    return {
        "server_name": deps.settings.server_name,
        "system": system,
        "containers": {
            "total": len(items),
            "healthy": healthy,
            "updated_at": containers["updated_at"],
            "age_seconds": containers["age_seconds"],
        },
        "open_incidents": await inc_db.count_open_incidents(),
    }


@router.get("/overview")
async def overview(request: Request, _: object = Depends(require_session)) -> dict[str, Any]:
    return await build_overview(get_deps(request))
```

`src/opspilot/web/routes/containers.py`:

```python
from __future__ import annotations

import asyncio
from typing import Any

from fastapi import APIRouter, Depends, Query, Request

from opspilot.core.snapshot import CONTAINERS
from opspilot.db import snooze as snooze_db
from opspilot.services.durations import resolve_duration
from opspilot.web.auth import ApiError, audit_event, get_deps, require_csrf, require_session
from opspilot.web.deps import WebDeps
from opspilot.web.schemas import SnoozeBody

router = APIRouter(prefix="/api/containers")

MAX_TAIL = 500
MAX_LOG_BYTES = 256 * 1024
LOG_TIMEOUT_SECONDS = 10.0


async def _require_container(deps: WebDeps, name: str) -> None:
    """Only names that exist right now may reach the executor."""
    names = {c.name for c in await deps.list_containers()}
    if name not in names:
        raise ApiError(404, "container_not_found", f"No container named '{name}'.")


@router.get("")
async def list_containers(request: Request, _: object = Depends(require_session)) -> dict[str, Any]:
    deps = get_deps(request)
    snap = deps.snapshots.snapshot(CONTAINERS)
    muted = {entry["name"]: entry for entry in await snooze_db.list_snoozed()}
    items = [
        {
            **c,
            "muted": c["name"] in muted,
            "muted_remaining": muted[c["name"]]["remaining"] if c["name"] in muted else None,
        }
        for c in (snap["data"] or [])
    ]
    return {"updated_at": snap["updated_at"], "age_seconds": snap["age_seconds"], "items": items}


@router.post("/{name}/restart")
async def restart(name: str, request: Request, _: object = Depends(require_csrf)) -> dict[str, Any]:
    deps = get_deps(request)
    await _require_container(deps, name)
    result = await deps.executor.restart_container(name)
    if not result["success"]:
        audit_event(request, "restart", name, "FAILED", {"message": result["message"]})
        raise ApiError(502, "restart_failed", result["message"])
    audit_event(request, "restart", name, "SUCCESS")
    return {"ok": True, "message": result["message"]}


@router.get("/{name}/logs")
async def logs(
    name: str,
    request: Request,
    tail: int = Query(100, ge=1, le=MAX_TAIL),
    _: object = Depends(require_session),
) -> dict[str, Any]:
    deps = get_deps(request)
    await _require_container(deps, name)
    try:
        text = await asyncio.wait_for(deps.executor.get_container_logs(name, tail=tail), LOG_TIMEOUT_SECONDS)
    except TimeoutError:
        raise ApiError(504, "logs_timeout", "Timed out reading container logs.") from None
    raw = text.encode("utf-8")
    truncated = len(raw) > MAX_LOG_BYTES
    if truncated:
        text = raw[-MAX_LOG_BYTES:].decode("utf-8", errors="ignore")
    audit_event(request, "logs", name, "SUCCESS", {"tail": tail})
    return {"name": name, "tail": tail, "truncated": truncated, "logs": text}


@router.post("/{name}/snooze")
async def snooze(name: str, body: SnoozeBody, request: Request, _: object = Depends(require_csrf)) -> dict[str, Any]:
    deps = get_deps(request)
    await _require_container(deps, name)
    until = resolve_duration(body.duration)
    await snooze_db.snooze_container(name, until)
    audit_event(request, "snooze", name, "SUCCESS", {"duration": body.duration})
    return {"ok": True, "until": until.isoformat() if until else None}


@router.delete("/{name}/snooze")
async def unsnooze(name: str, request: Request, _: object = Depends(require_csrf)) -> dict[str, bool]:
    await snooze_db.unsnooze_container(name)
    audit_event(request, "unsnooze", name, "SUCCESS")
    return {"ok": True}
```

`src/opspilot/web/routes/probes.py`:

```python
from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Depends, Request

from opspilot.core.snapshot import PROBES
from opspilot.db import endpoints as ep_db
from opspilot.web.auth import ApiError, audit_event, get_deps, require_csrf, require_session
from opspilot.web.schemas import ProbeCreate, ProbeToggle

router = APIRouter(prefix="/api/probes")


def _public(row: dict, last: dict | None) -> dict[str, Any]:
    return {
        "id": row["id"],
        "name": row["name"],
        "url": row["url"],
        "expected_status": row["expected_status"],
        "timeout_seconds": row["timeout_seconds"],
        "enabled": bool(row["enabled"]),
        "last": last,
    }


@router.get("")
async def list_probes(request: Request, _: object = Depends(require_session)) -> dict[str, Any]:
    deps = get_deps(request)
    snap = deps.snapshots.snapshot(PROBES)
    last = snap["data"] or {}
    rows = await ep_db.list_endpoints(enabled_only=False)
    return {
        "updated_at": snap["updated_at"],
        "age_seconds": snap["age_seconds"],
        "items": [_public(r, last.get(r["name"])) for r in rows],
    }


@router.post("", status_code=201)
async def add_probe(body: ProbeCreate, request: Request, _: object = Depends(require_csrf)) -> dict[str, int]:
    try:
        probe_id = await ep_db.add_endpoint(body.name, body.url, body.expected_status, body.timeout_seconds)
    except ep_db.DuplicateEndpoint:
        raise ApiError(409, "duplicate_probe", f"A probe named '{body.name}' already exists.") from None
    audit_event(request, "probe_add", body.name, "SUCCESS", {"url": body.url})
    return {"id": probe_id}


@router.post("/{probe_id}/toggle")
async def toggle_probe(
    probe_id: int, body: ProbeToggle, request: Request, _: object = Depends(require_csrf)
) -> dict[str, bool]:
    if not await ep_db.toggle_endpoint(probe_id, body.enabled):
        raise ApiError(404, "probe_not_found", f"No probe with ID {probe_id}.")
    audit_event(request, "probe_toggle", str(probe_id), "SUCCESS", {"enabled": body.enabled})
    return {"ok": True}


@router.delete("/{probe_id}")
async def remove_probe(probe_id: int, request: Request, _: object = Depends(require_csrf)) -> dict[str, bool]:
    if not await ep_db.remove_endpoint(probe_id):
        raise ApiError(404, "probe_not_found", f"No probe with ID {probe_id}.")
    audit_event(request, "probe_remove", str(probe_id), "SUCCESS")
    return {"ok": True}
```

`src/opspilot/web/routes/renewals.py`:

```python
from __future__ import annotations

import sqlite3
from datetime import date
from typing import Any, Literal

from fastapi import APIRouter, Depends, Query, Request

from opspilot.db import renewals as ren_db
from opspilot.services.durations import resolve_duration
from opspilot.services.renewals import RenewalNotFound, RenewalNotPending, pay_renewal
from opspilot.web.auth import ApiError, audit_event, require_csrf, require_session
from opspilot.web.schemas import RenewalCreate, SnoozeBody

router = APIRouter(prefix="/api/renewals")


def _public(row: dict) -> dict[str, Any]:
    days = (date.fromisoformat(row["due_date"]) - date.today()).days
    return {
        "id": row["id"],
        "name": row["name"],
        "category": row["category"],
        "due_date": row["due_date"],
        "amount": row["amount"],
        "currency": row["currency"],
        "notes": row["notes"],
        "status": row["status"],
        "recurrence": row["recurrence"],
        "remind_days_before": row["remind_days_before"],
        "paid_at": row["paid_at"],
        "snoozed_until": row.get("snoozed_until"),
        "days_until_due": days,
    }


@router.get("")
async def list_renewals(
    status: Literal["pending", "paid", "cancelled", "all"] = "pending",
    limit: int = Query(50, ge=1, le=100),
    offset: int = Query(0, ge=0),
    _: object = Depends(require_session),
) -> dict[str, Any]:
    rows = await ren_db.list_renewals(None if status == "all" else status)
    return {"total": len(rows), "items": [_public(r) for r in rows[offset : offset + limit]]}


@router.post("", status_code=201)
async def add_renewal(body: RenewalCreate, request: Request, _: object = Depends(require_csrf)) -> dict[str, int]:
    try:
        renewal_id = await ren_db.add_renewal(
            body.name,
            body.category,
            body.due_date.isoformat(),
            body.amount,
            body.currency,
            body.notes,
            body.recurrence,
            body.remind_days_before,
        )
    except sqlite3.IntegrityError:
        raise ApiError(
            409, "duplicate_renewal", "A renewal with this name, category and due date already exists."
        ) from None
    audit_event(request, "renewal_add", body.name, "SUCCESS")
    return {"id": renewal_id}


@router.post("/{renewal_id}/pay")
async def pay(renewal_id: int, request: Request, _: object = Depends(require_csrf)) -> dict[str, Any]:
    try:
        renewal, next_renewal = await pay_renewal(renewal_id)
    except RenewalNotFound:
        raise ApiError(404, "renewal_not_found", f"No renewal with ID {renewal_id}.") from None
    except RenewalNotPending:
        raise ApiError(409, "not_pending", "This renewal is not pending (already paid?).") from None
    audit_event(request, "renewal_pay", renewal["name"], "SUCCESS", {"id": renewal_id})
    return {"ok": True, "next": _public(next_renewal) if next_renewal else None}


@router.post("/{renewal_id}/snooze")
async def snooze(
    renewal_id: int, body: SnoozeBody, request: Request, _: object = Depends(require_csrf)
) -> dict[str, Any]:
    until = resolve_duration(body.duration)
    if until is None:
        raise ApiError(422, "invalid_duration", "Renewals cannot be snoozed forever; choose 1h, 24h or 7d.")
    renewal = await ren_db.get_renewal(renewal_id)
    if renewal is None:
        raise ApiError(404, "renewal_not_found", f"No renewal with ID {renewal_id}.")
    await ren_db.snooze_renewal(renewal_id, until)
    audit_event(request, "renewal_snooze", renewal["name"], "SUCCESS", {"duration": body.duration})
    return {"ok": True, "until": until.isoformat()}
```

`src/opspilot/web/routes/incidents.py`:

```python
from __future__ import annotations

from typing import Any, Literal

from fastapi import APIRouter, Depends, Query

from opspilot.db import incidents as inc_db
from opspilot.web.auth import require_session

router = APIRouter(prefix="/api/incidents")

_FIELDS = (
    "id",
    "source",
    "target",
    "severity",
    "title",
    "detail",
    "alert_count",
    "resolved_at",
    "snoozed_until",
    "created_at",
    "updated_at",
)


@router.get("")
async def list_incidents(
    state: Literal["open", "resolved", "all"] = "open",
    limit: int = Query(25, ge=1, le=100),
    offset: int = Query(0, ge=0),
    _: object = Depends(require_session),
) -> dict[str, Any]:
    rows = await inc_db.list_by_state(state, limit, offset)
    return {
        "total": await inc_db.count_by_state(state),
        "items": [{k: row.get(k) for k in _FIELDS} for row in rows],
    }
```

Register them in `create_app` (`src/opspilot/web/app.py`), replacing `app.include_router(auth.router)` with:

```python
    from opspilot.web.routes import containers, incidents, overview, probes, renewals

    for router in (auth.router, overview.router, containers.router, probes.router, renewals.router, incidents.router):
        app.include_router(router)
```

(Move that import to the top of the module if ruff complains.)

- [ ] **Step 5: Add the executor-contract check for web code**

Append to `tests/test_bot_executor_contract.py`:

```python
def test_web_routes_only_call_existing_executor_methods():
    from opspilot.core.executor import SafeOperationExecutor

    for path in (ROOT / "web" / "routes").glob("*.py"):
        used = _executor_attrs_used(path)
        missing = sorted(n for n in used if not hasattr(SafeOperationExecutor, n))
        assert not missing, f"{path.name} calls non-existent executor methods: {missing}"
```

- [ ] **Step 6: Run everything and commit**

```bash
uv run pytest -q && uv run ruff check src tests && uv run ruff format --check src tests
```

Expected: all PASS. If `ruff format` rewrites the long tuples in `incidents.py`, accept its output.

```bash
git add src/opspilot/web tests
git commit -m "feat(web): overview, containers, probes, renewals and incidents API" -m "Co-Authored-By: Claude Sonnet 5.5 <noreply@anthropic.com>"
```

---

## Task 10: Frontend — static pages, script and styles

Vanilla HTML/CSS/JS, no build step, no CDN. Everything must satisfy the CSP: no inline `<script>`, no inline `<style>`, no `style=` attributes, no `on*=` attributes, no `innerHTML`.

**Files:**
- Modify (replace placeholders): `src/opspilot/web/static/index.html`, `login.html`
- Create: `src/opspilot/web/static/app.js`, `login.js`, `styles.css`
- Test: `tests/web/test_static.py`

**Interfaces:**
- Consumes: the JSON contract from Task 9 and `GET /api/auth/session` (Task 8).
- Produces: `/static/app.js`, `/static/login.js`, `/static/styles.css`. Later tasks add a Settings tab and a WebSocket to `app.js` (Tasks 13–14) — keep the `refreshActive()` and `renderOverview(d)` function names.

- [ ] **Step 1: Write the failing static-asset tests**

Create `tests/web/test_static.py`:

```python
import re
from importlib import resources
from pathlib import Path

import pytest

import opspilot.web as web_pkg

STATIC = Path(web_pkg.__file__).parent / "static"
FORBIDDEN_JS = ["innerHTML", "outerHTML", "insertAdjacentHTML", "document.write", "eval(", "new Function"]


def _files(*suffixes):
    return [p for p in STATIC.iterdir() if p.suffix in suffixes]


def test_static_assets_exist_and_are_packaged():
    for name in ("index.html", "login.html", "app.js", "login.js", "styles.css"):
        assert (STATIC / name).is_file(), name
    assert resources.files("opspilot.web").joinpath("static/index.html").is_file()


@pytest.mark.parametrize("path", _files(".js"), ids=lambda p: p.name)
def test_javascript_never_writes_html(path):
    text = path.read_text(encoding="utf-8")
    for token in FORBIDDEN_JS:
        assert token not in text, f"{path.name} uses {token}"


@pytest.mark.parametrize("path", _files(".html"), ids=lambda p: p.name)
def test_html_is_csp_clean(path):
    text = path.read_text(encoding="utf-8")
    assert not re.search(r"<script(?![^>]*\bsrc=)[^>]*>", text, re.I), "inline <script>"
    assert "<style" not in text.lower(), "inline <style>"
    assert not re.search(r"\sstyle\s*=", text, re.I), "style attribute"
    assert not re.search(r"\son[a-z]+\s*=", text, re.I), "inline event handler attribute"
    assert "http://" not in text and "https://" not in text, "external resource"


@pytest.mark.parametrize("path", _files(".js", ".css"), ids=lambda p: p.name)
def test_no_external_urls_or_inline_style_writes(path):
    text = path.read_text(encoding="utf-8")
    assert "@import" not in text
    assert not re.search(r"https?://", text), "external URL in asset"
    assert 'setAttribute("style"' not in text and "setAttribute('style'" not in text


def test_app_js_uses_text_rendering_helpers():
    text = (STATIC / "app.js").read_text(encoding="utf-8")
    assert "textContent" in text or "createTextNode" in text


def test_index_has_the_landmarks_and_dialogs_the_script_needs():
    html = (STATIC / "index.html").read_text(encoding="utf-8")
    for element_id in ("main", "toasts", "dlg-logs", "dlg-probe", "dlg-renewal", "dlg-confirm", "dlg-duration"):
        assert f'id="{element_id}"' in html, element_id
    assert 'lang="en"' in html and 'name="viewport"' in html


def test_pages_are_served_with_correct_types(env):
    from web_helpers import login

    assert "text/html" in env.client.get("/login").headers["content-type"]
    assert "javascript" in env.client.get("/static/app.js").headers["content-type"]
    assert "text/css" in env.client.get("/static/styles.css").headers["content-type"]
    login(env)
    assert "OpsPilot" in env.client.get("/").text
```

- [ ] **Step 2: Run and confirm failure**

Run: `uv run pytest tests/web/test_static.py -v`
Expected: FAIL (`app.js` etc. do not exist; placeholder HTML lacks the dialogs).

- [ ] **Step 3: Write `login.html` and `login.js`**

`src/opspilot/web/static/login.html`:

```html
<!doctype html>
<html lang="en">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width, initial-scale=1">
  <title>OpsPilot login</title>
  <link rel="stylesheet" href="/static/styles.css">
  <script src="/static/login.js" defer></script>
</head>
<body class="login-body">
  <main class="login-card">
    <h1>OpsPilot</h1>
    <p class="muted">Sign in to the command center.</p>
    <form id="login-form" autocomplete="on">
      <label for="password">Password</label>
      <input id="password" name="password" type="password" autocomplete="current-password" required>
      <p id="login-error" class="form-error" role="alert" hidden></p>
      <button class="btn primary" type="submit">Sign in</button>
    </form>
  </main>
</body>
</html>
```

`src/opspilot/web/static/login.js`:

```js
"use strict";
(function () {
  const form = document.getElementById("login-form");
  const errorBox = document.getElementById("login-error");
  const button = form.querySelector("button");

  function showError(message) {
    errorBox.textContent = message;
    errorBox.hidden = false;
  }

  form.addEventListener("submit", async (event) => {
    event.preventDefault();
    errorBox.hidden = true;
    button.disabled = true;
    try {
      const response = await fetch("/api/auth/login", {
        method: "POST",
        credentials: "same-origin",
        headers: { "Content-Type": "application/json", Accept: "application/json" },
        body: JSON.stringify({ password: form.elements.password.value }),
      });
      const data = await response.json().catch(() => ({}));
      if (response.ok) {
        location.assign("/");
        return;
      }
      showError(data.message || "Sign in failed.");
    } catch (err) {
      showError("Could not reach the server.");
    } finally {
      button.disabled = false;
    }
  });
})();
```

- [ ] **Step 4: Write `index.html`**

```html
<!doctype html>
<html lang="en">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width, initial-scale=1">
  <title>OpsPilot</title>
  <link rel="stylesheet" href="/static/styles.css">
  <script src="/static/app.js" defer></script>
</head>
<body>
  <a class="skip-link" href="#main">Skip to content</a>
  <header class="topbar">
    <div class="brand"><span class="pulse" id="live-dot" aria-hidden="true"></span> OpsPilot
      <span class="badge muted" id="server-name"></span></div>
    <div class="topbar-right">
      <span class="muted" id="freshness" aria-live="polite"></span>
      <span class="badge" id="alert-count"></span>
      <button class="btn ghost" id="logout" type="button">Log out</button>
    </div>
  </header>

  <nav class="tabs" aria-label="Sections">
    <button type="button" class="tab" data-tab="overview" aria-current="page">Overview</button>
    <button type="button" class="tab" data-tab="containers">Containers</button>
    <button type="button" class="tab" data-tab="probes">Probes</button>
    <button type="button" class="tab" data-tab="renewals">Renewals</button>
    <button type="button" class="tab" data-tab="incidents">Incidents</button>
  </nav>

  <main id="main">
    <section id="tab-overview" class="panel" aria-labelledby="h-overview">
      <h2 id="h-overview">Overview</h2>
      <div id="overview-grid" class="grid"></div>
    </section>

    <section id="tab-containers" class="panel" aria-labelledby="h-containers" hidden>
      <h2 id="h-containers">Containers</h2>
      <div id="containers-body"></div>
    </section>

    <section id="tab-probes" class="panel" aria-labelledby="h-probes" hidden>
      <div class="panel-head">
        <h2 id="h-probes">HTTP probes</h2>
        <button type="button" class="btn primary" id="add-probe">+ Add probe</button>
      </div>
      <div id="probes-body"></div>
    </section>

    <section id="tab-renewals" class="panel" aria-labelledby="h-renewals" hidden>
      <div class="panel-head">
        <h2 id="h-renewals">Renewals</h2>
        <div class="panel-tools">
          <label class="sr-only" for="renewal-status">Status</label>
          <select id="renewal-status">
            <option value="pending">Pending</option>
            <option value="paid">Paid</option>
            <option value="all">All</option>
          </select>
          <button type="button" class="btn primary" id="add-renewal">+ Add renewal</button>
        </div>
      </div>
      <div id="renewals-body"></div>
    </section>

    <section id="tab-incidents" class="panel" aria-labelledby="h-incidents" hidden>
      <div class="panel-head">
        <h2 id="h-incidents">Incidents</h2>
        <div class="panel-tools">
          <label class="sr-only" for="incident-state">State</label>
          <select id="incident-state">
            <option value="open">Open</option>
            <option value="resolved">Resolved</option>
            <option value="all">All</option>
          </select>
        </div>
      </div>
      <div id="incidents-body"></div>
    </section>
  </main>

  <dialog id="dlg-logs" aria-labelledby="logs-title">
    <form method="dialog" class="dialog-head">
      <h3 id="logs-title">Logs</h3>
      <button class="btn ghost" value="close">Close</button>
    </form>
    <p class="muted" id="logs-note"></p>
    <pre id="logs-output" class="terminal" tabindex="0"></pre>
  </dialog>

  <dialog id="dlg-probe" aria-labelledby="probe-title">
    <form id="probe-form" class="stack">
      <h3 id="probe-title">Add probe</h3>
      <label for="probe-name">Name</label>
      <input id="probe-name" name="name" maxlength="80" required>
      <label for="probe-url">URL</label>
      <input id="probe-url" name="url" type="url" placeholder="https://example.com/health" required>
      <label for="probe-status">Expected HTTP status</label>
      <input id="probe-status" name="expected_status" type="number" min="100" max="599" value="200" required>
      <p class="form-error" id="probe-error" role="alert" hidden></p>
      <div class="dialog-actions">
        <button type="button" class="btn ghost" data-close>Cancel</button>
        <button type="submit" class="btn primary">Add probe</button>
      </div>
    </form>
  </dialog>

  <dialog id="dlg-renewal" aria-labelledby="renewal-title">
    <form id="renewal-form" class="stack">
      <h3 id="renewal-title">Add renewal</h3>
      <label for="renewal-name">Name</label>
      <input id="renewal-name" name="name" maxlength="120" required>
      <label for="renewal-category">Category</label>
      <select id="renewal-category" name="category">
        <option value="vps">VPS / server</option>
        <option value="domain">Domain</option>
        <option value="ssl">SSL certificate</option>
        <option value="software">Software / subscription</option>
        <option value="other" selected>Other</option>
      </select>
      <label for="renewal-due">Due date</label>
      <input id="renewal-due" name="due_date" type="date" required>
      <label for="renewal-amount">Amount (optional)</label>
      <input id="renewal-amount" name="amount" type="number" min="0" step="0.01">
      <label for="renewal-currency">Currency</label>
      <input id="renewal-currency" name="currency" value="INR" minlength="3" maxlength="3" required>
      <label for="renewal-recurrence">Repeats</label>
      <select id="renewal-recurrence" name="recurrence">
        <option value="none">Never</option>
        <option value="monthly">Monthly</option>
        <option value="yearly">Yearly</option>
      </select>
      <label for="renewal-remind">Remind days before</label>
      <input id="renewal-remind" name="remind_days_before" type="number" min="0" max="365" value="7" required>
      <p class="form-error" id="renewal-error" role="alert" hidden></p>
      <div class="dialog-actions">
        <button type="button" class="btn ghost" data-close>Cancel</button>
        <button type="submit" class="btn primary">Add renewal</button>
      </div>
    </form>
  </dialog>

  <dialog id="dlg-confirm" aria-labelledby="confirm-title">
    <form method="dialog" class="stack">
      <h3 id="confirm-title">Confirm</h3>
      <p id="confirm-message"></p>
      <div class="dialog-actions">
        <button class="btn ghost" value="cancel">Cancel</button>
        <button class="btn danger" value="ok">Confirm</button>
      </div>
    </form>
  </dialog>

  <dialog id="dlg-duration" aria-labelledby="duration-title">
    <form method="dialog" class="stack">
      <h3 id="duration-title">Snooze for how long?</h3>
      <div class="dialog-actions wrap">
        <button class="btn" value="1h">1 hour</button>
        <button class="btn" value="24h">24 hours</button>
        <button class="btn" value="7d">7 days</button>
        <button class="btn" value="forever" id="duration-forever">Forever</button>
        <button class="btn ghost" value="cancel">Cancel</button>
      </div>
    </form>
  </dialog>

  <div id="toasts" role="status" aria-live="polite"></div>
</body>
</html>
```

- [ ] **Step 5: Write `styles.css`**

```css
:root {
  --bg: #0a0d14;
  --surface: #121722;
  --card: rgba(22, 28, 45, 0.7);
  --border: rgba(255, 255, 255, 0.12);
  --text: #e6e9f2;
  --muted: #9aa3b8;
  --ok: #10b981;
  --warn: #f59e0b;
  --bad: #ef4444;
  --primary: #6366f1;
  --primary-text: #ffffff;
  --radius: 12px;
  --font: Inter, system-ui, -apple-system, "Segoe UI", Roboto, sans-serif;
}

* { box-sizing: border-box; }
html { color-scheme: dark; }
body {
  margin: 0;
  background: var(--bg);
  color: var(--text);
  font-family: var(--font);
  line-height: 1.5;
}
.sr-only { position: absolute; width: 1px; height: 1px; overflow: hidden; clip: rect(0 0 0 0); white-space: nowrap; }
.skip-link { position: absolute; left: -999px; top: 0; background: var(--primary); color: var(--primary-text); padding: 8px 12px; }
.skip-link:focus { left: 8px; top: 8px; z-index: 10; }
:focus-visible { outline: 2px solid var(--primary); outline-offset: 2px; }
.muted { color: var(--muted); }
.big { font-size: 1.8rem; font-weight: 600; font-variant-numeric: tabular-nums; }

.topbar { display: flex; flex-wrap: wrap; gap: 12px; align-items: center; justify-content: space-between; padding: 12px 16px; background: var(--surface); border-bottom: 1px solid var(--border); }
.brand { font-weight: 700; display: flex; align-items: center; gap: 8px; }
.topbar-right { display: flex; align-items: center; gap: 12px; flex-wrap: wrap; }
.pulse { width: 10px; height: 10px; border-radius: 50%; background: var(--ok); animation: pulse 2s infinite; }
@keyframes pulse { 0% { opacity: 1; } 50% { opacity: 0.35; } 100% { opacity: 1; } }
@media (prefers-reduced-motion: reduce) { .pulse { animation: none; } * { scroll-behavior: auto !important; } }

.tabs { display: flex; gap: 8px; padding: 12px 16px; overflow-x: auto; }
.tab { background: transparent; color: var(--muted); border: 1px solid var(--border); border-radius: 999px; padding: 6px 14px; font: inherit; cursor: pointer; }
.tab[aria-current="page"] { background: var(--primary); color: var(--primary-text); border-color: var(--primary); }

main { padding: 0 16px 48px; max-width: 1100px; margin: 0 auto; }
.panel h2 { margin: 8px 0 12px; font-size: 1.15rem; }
.panel-head { display: flex; justify-content: space-between; align-items: center; flex-wrap: wrap; gap: 8px; }
.panel-tools { display: flex; gap: 8px; align-items: center; }
.grid { display: grid; grid-template-columns: repeat(auto-fit, minmax(200px, 1fr)); gap: 12px; }
.card { background: var(--card); border: 1px solid var(--border); border-radius: var(--radius); padding: 16px; backdrop-filter: blur(12px); }
.card meter { width: 100%; height: 10px; margin-top: 8px; }

.table-wrap { overflow-x: auto; border: 1px solid var(--border); border-radius: var(--radius); }
table { width: 100%; border-collapse: collapse; font-size: 0.95rem; }
th, td { text-align: left; padding: 10px 12px; border-bottom: 1px solid var(--border); vertical-align: middle; }
th { color: var(--muted); font-weight: 600; white-space: nowrap; }
td.num { font-variant-numeric: tabular-nums; }
td.actions { display: flex; gap: 6px; flex-wrap: wrap; }
tr:last-child td { border-bottom: 0; }

.badge { display: inline-block; padding: 2px 10px; border-radius: 999px; font-size: 0.8rem; border: 1px solid var(--border); background: var(--surface); }
.badge.ok { color: var(--ok); border-color: var(--ok); }
.badge.warn { color: var(--warn); border-color: var(--warn); }
.badge.bad { color: var(--bad); border-color: var(--bad); }
.badge.muted { color: var(--muted); }

.btn { font: inherit; background: var(--surface); color: var(--text); border: 1px solid var(--border); border-radius: 8px; padding: 6px 12px; cursor: pointer; }
.btn:hover { border-color: var(--primary); }
.btn.primary { background: var(--primary); color: var(--primary-text); border-color: var(--primary); }
.btn.danger { background: var(--bad); color: #fff; border-color: var(--bad); }
.btn.ghost { background: transparent; }
.btn:disabled { opacity: 0.55; cursor: not-allowed; }

.empty { padding: 24px; text-align: center; color: var(--muted); border: 1px dashed var(--border); border-radius: var(--radius); }
.skeleton { height: 44px; margin-bottom: 8px; border-radius: 8px; background: linear-gradient(90deg, var(--surface), #1a2233, var(--surface)); background-size: 200% 100%; animation: shimmer 1.4s infinite; }
@keyframes shimmer { 0% { background-position: 200% 0; } 100% { background-position: -200% 0; } }
@media (prefers-reduced-motion: reduce) { .skeleton { animation: none; } }
.stale { opacity: 0.6; }

dialog { background: var(--surface); color: var(--text); border: 1px solid var(--border); border-radius: var(--radius); padding: 20px; width: min(560px, 92vw); }
dialog::backdrop { background: rgba(0, 0, 0, 0.6); }
.stack { display: flex; flex-direction: column; gap: 8px; }
.dialog-head { display: flex; justify-content: space-between; align-items: center; }
.dialog-actions { display: flex; gap: 8px; justify-content: flex-end; margin-top: 8px; }
.dialog-actions.wrap { flex-wrap: wrap; justify-content: flex-start; }
label { font-size: 0.9rem; color: var(--muted); }
input, select { font: inherit; background: var(--bg); color: var(--text); border: 1px solid var(--border); border-radius: 8px; padding: 8px 10px; width: 100%; }
.panel-tools select { width: auto; }
.form-error { color: var(--bad); margin: 4px 0; }
.terminal { background: #05070b; color: #d1d5db; font-family: ui-monospace, Menlo, Consolas, monospace; font-size: 0.8rem; padding: 12px; border-radius: 8px; max-height: 55vh; overflow: auto; white-space: pre-wrap; word-break: break-word; }

#toasts { position: fixed; right: 16px; bottom: 16px; display: flex; flex-direction: column; gap: 8px; z-index: 20; }
.toast { background: var(--surface); border: 1px solid var(--border); border-left: 4px solid var(--primary); border-radius: 8px; padding: 10px 14px; max-width: 320px; }
.toast.error { border-left-color: var(--bad); }
.toast.success { border-left-color: var(--ok); }

.login-body { display: grid; place-items: center; min-height: 100vh; padding: 16px; }
.login-card { background: var(--card); border: 1px solid var(--border); border-radius: var(--radius); padding: 28px; width: min(380px, 100%); backdrop-filter: blur(12px); }
.login-card h1 { margin-top: 0; }
.login-card .btn { width: 100%; margin-top: 12px; }

@media (max-width: 600px) {
  th:nth-child(3), td:nth-child(3) { display: none; }
}
```

- [ ] **Step 6: Write `app.js`**

```js
"use strict";
(function () {
  const state = { csrf: "", tab: "overview", timer: null };
  const $ = (selector, root = document) => root.querySelector(selector);

  // ─── small DOM helpers (text only, never HTML) ────────────────────────────
  function h(tag, props, ...children) {
    const el = document.createElement(tag);
    for (const [key, value] of Object.entries(props || {})) {
      if (key === "class") el.className = value;
      else if (key.startsWith("on") && typeof value === "function") el.addEventListener(key.slice(2), value);
      else if (value !== false && value != null) el.setAttribute(key, value === true ? "" : String(value));
    }
    for (const child of children.flat()) {
      if (child == null || child === false) continue;
      el.append(child instanceof Node ? child : document.createTextNode(String(child)));
    }
    return el;
  }

  function toast(message, kind = "info") {
    const item = h("div", { class: `toast ${kind}` }, message);
    $("#toasts").append(item);
    setTimeout(() => item.remove(), 5000);
  }

  async function api(method, path, body) {
    const headers = { Accept: "application/json" };
    if (method !== "GET") {
      headers["Content-Type"] = "application/json";
      headers["X-CSRF-Token"] = state.csrf;
    }
    const response = await fetch(path, {
      method,
      headers,
      credentials: "same-origin",
      body: body === undefined ? undefined : JSON.stringify(body),
    });
    if (response.status === 401) {
      location.assign("/login");
      throw new Error("Session expired");
    }
    const data = await response.json().catch(() => ({}));
    if (!response.ok) throw new Error(data.message || `Request failed (${response.status})`);
    return data;
  }

  async function guarded(action) {
    try {
      return await action();
    } catch (err) {
      toast(err.message, "error");
      return null;
    }
  }

  const ICONS = { ok: "●", warn: "▲", bad: "✕", muted: "○" };
  function badge(text, kind) {
    return h("span", { class: `badge ${kind}` }, `${ICONS[kind] || "●"} ${text}`);
  }
  function btn(label, onclick, extra = "") {
    return h("button", { type: "button", class: `btn ${extra}`.trim(), onclick }, label);
  }
  function empty(message) {
    return h("p", { class: "empty" }, message);
  }
  function loading(target) {
    target.replaceChildren(h("div", { class: "skeleton" }), h("div", { class: "skeleton" }));
  }
  function table(headers, rows) {
    const head = h("tr", {}, headers.map((label) => h("th", { scope: "col" }, label)));
    return h("div", { class: "table-wrap" }, h("table", {}, h("thead", {}, head), h("tbody", {}, rows)));
  }
  function ago(seconds) {
    if (seconds == null) return "no data yet";
    if (seconds < 5) return "just now";
    if (seconds < 60) return `${Math.round(seconds)}s ago`;
    return `${Math.round(seconds / 60)}m ago`;
  }
  function money(amount, currency) {
    if (amount == null) return "—";
    try {
      return new Intl.NumberFormat(undefined, { style: "currency", currency }).format(amount);
    } catch (err) {
      return `${amount} ${currency}`;
    }
  }
  function dueText(days) {
    if (days < 0) return `${-days}d overdue`;
    if (days === 0) return "due today";
    return `in ${days}d`;
  }

  // ─── dialogs ──────────────────────────────────────────────────────────────
  function confirmAction(title, message) {
    return new Promise((resolve) => {
      const dialog = $("#dlg-confirm");
      $("#confirm-title").textContent = title;
      $("#confirm-message").textContent = message;
      dialog.returnValue = "cancel";
      dialog.addEventListener("close", () => resolve(dialog.returnValue === "ok"), { once: true });
      dialog.showModal();
    });
  }

  function chooseDuration(allowForever) {
    return new Promise((resolve) => {
      const dialog = $("#dlg-duration");
      $("#duration-forever").hidden = !allowForever;
      dialog.returnValue = "cancel";
      dialog.addEventListener(
        "close",
        () => resolve(dialog.returnValue === "cancel" || dialog.returnValue === "" ? null : dialog.returnValue),
        { once: true },
      );
      dialog.showModal();
    });
  }

  document.addEventListener("click", (event) => {
    const closer = event.target.closest("[data-close]");
    if (closer) closer.closest("dialog").close();
  });

  function anyDialogOpen() {
    return Array.from(document.querySelectorAll("dialog")).some((d) => d.open);
  }

  // ─── overview ─────────────────────────────────────────────────────────────
  function gauge(label, percent) {
    const meter = h("meter", { min: 0, max: 100, low: 70, high: 90, optimum: 30, value: percent, "aria-label": `${label} usage` });
    return h("div", { class: "card" }, h("div", { class: "muted" }, label), h("div", { class: "big" }, `${Math.round(percent)}%`), meter);
  }
  function stat(label, value) {
    return h("div", { class: "card" }, h("div", { class: "muted" }, label), h("div", { class: "big" }, value));
  }

  function renderOverview(data) {
    $("#server-name").textContent = data.server_name;
    $("#alert-count").textContent = `${data.open_incidents} open incident${data.open_incidents === 1 ? "" : "s"}`;
    $("#alert-count").className = `badge ${data.open_incidents ? "bad" : "ok"}`;
    const grid = $("#overview-grid");
    const sys = data.system.data;
    grid.replaceChildren();
    if (!sys) {
      grid.append(empty("Waiting for the first monitoring cycle…"));
    } else {
      grid.append(gauge("CPU", sys.cpu_percent), gauge("Memory", sys.ram_percent), gauge("Disk", sys.disk_percent));
      grid.append(stat("Uptime", sys.uptime_human));
    }
    grid.append(stat("Containers", `${data.containers.healthy}/${data.containers.total} healthy`));
    $("#freshness").textContent = `Updated ${ago(data.system.age_seconds)}`;
    $("#freshness").classList.toggle("stale", (data.system.age_seconds || 0) > 180);
  }

  async function loadOverview() {
    const data = await api("GET", "/api/overview");
    renderOverview(data);
  }

  // ─── containers ───────────────────────────────────────────────────────────
  function containerKind(c) {
    if (c.status !== "running") return "bad";
    return c.health === "unhealthy" ? "bad" : c.health === "starting" ? "warn" : "ok";
  }

  async function loadContainers() {
    const body = $("#containers-body");
    const data = await api("GET", "/api/containers");
    if (!data.items.length) {
      body.replaceChildren(empty("No containers reported yet."));
      return;
    }
    const rows = data.items.map((c) =>
      h(
        "tr",
        {},
        h("td", {}, c.name),
        h("td", {}, badge(c.health && c.health !== "none" ? `${c.status} · ${c.health}` : c.status, containerKind(c)), c.muted ? " " : null, c.muted ? badge("muted", "muted") : null),
        h("td", { class: "muted" }, c.image),
        h(
          "td",
          { class: "actions" },
          btn("Logs", () => showLogs(c.name)),
          btn("Restart", () => restartContainer(c.name), "danger"),
          c.muted ? btn("Unmute", () => unmute(c.name)) : btn("Snooze", () => snoozeContainer(c.name)),
        ),
      ),
    );
    body.replaceChildren(table(["Name", "Status", "Image", "Actions"], rows));
  }

  async function showLogs(name) {
    const data = await guarded(() => api("GET", `/api/containers/${encodeURIComponent(name)}/logs?tail=200`));
    if (!data) return;
    $("#logs-title").textContent = `Logs — ${name}`;
    $("#logs-note").textContent = data.truncated ? "Output was truncated to the last 256 KB." : `Last ${data.tail} lines`;
    const output = $("#logs-output");
    output.textContent = data.logs || "(no output)";
    $("#dlg-logs").showModal();
    output.scrollTop = output.scrollHeight;
  }

  async function restartContainer(name) {
    if (!(await confirmAction("Restart container", `Restart "${name}"? It will be briefly unavailable.`))) return;
    const result = await guarded(() => api("POST", `/api/containers/${encodeURIComponent(name)}/restart`));
    if (result) {
      toast(result.message, "success");
      setTimeout(refreshActive, 2000);
    }
  }

  async function snoozeContainer(name) {
    const duration = await chooseDuration(true);
    if (!duration) return;
    if (await guarded(() => api("POST", `/api/containers/${encodeURIComponent(name)}/snooze`, { duration }))) {
      toast(`Alerts for ${name} muted (${duration}).`, "success");
      loadContainers();
    }
  }

  async function unmute(name) {
    if (await guarded(() => api("DELETE", `/api/containers/${encodeURIComponent(name)}/snooze`))) {
      toast(`Alerts for ${name} resumed.`, "success");
      loadContainers();
    }
  }

  // ─── probes ───────────────────────────────────────────────────────────────
  async function loadProbes() {
    const body = $("#probes-body");
    const data = await api("GET", "/api/probes");
    if (!data.items.length) {
      body.replaceChildren(empty("No probes yet. Add one to start monitoring an endpoint."));
      return;
    }
    const rows = data.items.map((p) => {
      const last = p.last;
      let status = badge("no data", "muted");
      if (!p.enabled) status = badge("disabled", "muted");
      else if (last) status = last.is_healthy ? badge(`HTTP ${last.status_code}`, "ok") : badge(last.error ? "unreachable" : `HTTP ${last.status_code}`, "bad");
      return h(
        "tr",
        {},
        h("td", {}, p.name),
        h("td", {}, status),
        h("td", { class: "muted" }, p.url),
        h("td", { class: "num" }, last ? `${Math.round(last.latency_ms)} ms` : "—"),
        h(
          "td",
          { class: "actions" },
          btn(p.enabled ? "Disable" : "Enable", () => toggleProbe(p)),
          btn("Remove", () => removeProbe(p), "danger"),
        ),
      );
    });
    body.replaceChildren(table(["Name", "Status", "URL", "Latency", "Actions"], rows));
  }

  async function toggleProbe(probe) {
    if (await guarded(() => api("POST", `/api/probes/${probe.id}/toggle`, { enabled: !probe.enabled }))) loadProbes();
  }

  async function removeProbe(probe) {
    if (!(await confirmAction("Remove probe", `Stop monitoring "${probe.name}"?`))) return;
    if (await guarded(() => api("DELETE", `/api/probes/${probe.id}`))) {
      toast("Probe removed.", "success");
      loadProbes();
    }
  }

  function bindForm(formId, errorId, dialogId, build, endpoint, reload, success) {
    const form = $(formId);
    form.addEventListener("submit", async (event) => {
      event.preventDefault();
      const errorBox = $(errorId);
      errorBox.hidden = true;
      try {
        await api("POST", endpoint, build(new FormData(form)));
        $(dialogId).close();
        form.reset();
        toast(success, "success");
        reload();
      } catch (err) {
        errorBox.textContent = err.message;
        errorBox.hidden = false;
      }
    });
  }

  // ─── renewals ─────────────────────────────────────────────────────────────
  async function loadRenewals() {
    const body = $("#renewals-body");
    const status = $("#renewal-status").value;
    const data = await api("GET", `/api/renewals?status=${encodeURIComponent(status)}&limit=100`);
    if (!data.items.length) {
      body.replaceChildren(empty(status === "pending" ? "Nothing pending. You are all caught up." : "No renewals to show."));
      return;
    }
    const rows = data.items.map((r) => {
      const kind = r.status !== "pending" ? "muted" : r.days_until_due < 0 ? "bad" : r.days_until_due <= r.remind_days_before ? "warn" : "ok";
      const label = r.status !== "pending" ? r.status : dueText(r.days_until_due);
      return h(
        "tr",
        {},
        h("td", {}, r.name),
        h("td", { class: "muted" }, r.category),
        h("td", { class: "num" }, r.due_date),
        h("td", {}, badge(label, kind)),
        h("td", { class: "num" }, money(r.amount, r.currency)),
        h(
          "td",
          { class: "actions" },
          r.status === "pending" ? btn("Mark paid", () => payRenewal(r), "primary") : null,
          r.status === "pending" ? btn("Snooze", () => snoozeRenewal(r)) : null,
        ),
      );
    });
    body.replaceChildren(table(["Name", "Category", "Due", "Status", "Amount", "Actions"], rows));
  }

  async function payRenewal(renewal) {
    if (!(await confirmAction("Mark as paid", `Mark "${renewal.name}" as paid?`))) return;
    const result = await guarded(() => api("POST", `/api/renewals/${renewal.id}/pay`));
    if (result) {
      toast(result.next ? `Paid. Next due ${result.next.due_date}.` : "Marked as paid.", "success");
      loadRenewals();
    }
  }

  async function snoozeRenewal(renewal) {
    const duration = await chooseDuration(false);
    if (!duration) return;
    if (await guarded(() => api("POST", `/api/renewals/${renewal.id}/snooze`, { duration }))) {
      toast(`Reminders for ${renewal.name} snoozed.`, "success");
      loadRenewals();
    }
  }

  // ─── incidents ────────────────────────────────────────────────────────────
  async function loadIncidents() {
    const body = $("#incidents-body");
    const stateName = $("#incident-state").value;
    const data = await api("GET", `/api/incidents?state=${encodeURIComponent(stateName)}&limit=50`);
    if (!data.items.length) {
      body.replaceChildren(empty(stateName === "open" ? "No open incidents. All services operating normally." : "No incidents to show."));
      return;
    }
    const rows = data.items.map((i) =>
      h(
        "tr",
        {},
        h("td", {}, badge(i.resolved_at ? "resolved" : i.severity, i.resolved_at ? "ok" : i.severity === "critical" ? "bad" : "warn")),
        h("td", {}, i.title),
        h("td", { class: "muted" }, `${i.source} · ${i.target}`),
        h("td", { class: "num" }, String(i.alert_count)),
        h("td", { class: "num" }, i.updated_at),
      ),
    );
    body.replaceChildren(table(["State", "Title", "Source", "Alerts", "Updated"], rows));
  }

  // ─── tabs and refresh ─────────────────────────────────────────────────────
  const loaders = { overview: loadOverview, containers: loadContainers, probes: loadProbes, renewals: loadRenewals, incidents: loadIncidents };

  function showTab(name) {
    state.tab = name;
    for (const tab of document.querySelectorAll(".tab")) {
      if (tab.dataset.tab === name) tab.setAttribute("aria-current", "page");
      else tab.removeAttribute("aria-current");
    }
    for (const panel of document.querySelectorAll(".panel")) panel.hidden = panel.id !== `tab-${name}`;
    refreshActive(true);
  }

  async function refreshActive(showSkeleton) {
    if (anyDialogOpen()) return;
    const target = { overview: "#overview-grid", containers: "#containers-body", probes: "#probes-body", renewals: "#renewals-body", incidents: "#incidents-body" }[state.tab];
    if (showSkeleton === true) loading($(target));
    await guarded(loaders[state.tab]);
  }

  async function init() {
    const session = await api("GET", "/api/auth/session");
    state.csrf = session.csrf_token;
    $("#server-name").textContent = session.server_name;
    for (const tab of document.querySelectorAll(".tab")) tab.addEventListener("click", () => showTab(tab.dataset.tab));
    $("#logout").addEventListener("click", async () => {
      await guarded(() => api("POST", "/api/auth/logout"));
      location.assign("/login");
    });
    $("#add-probe").addEventListener("click", () => $("#dlg-probe").showModal());
    $("#add-renewal").addEventListener("click", () => $("#dlg-renewal").showModal());
    $("#renewal-status").addEventListener("change", () => refreshActive(true));
    $("#incident-state").addEventListener("change", () => refreshActive(true));
    bindForm("#probe-form", "#probe-error", "#dlg-probe", (f) => ({ name: f.get("name"), url: f.get("url"), expected_status: Number(f.get("expected_status")) }), "/api/probes", loadProbes, "Probe added.");
    bindForm(
      "#renewal-form",
      "#renewal-error",
      "#dlg-renewal",
      (f) => ({
        name: f.get("name"),
        category: f.get("category"),
        due_date: f.get("due_date"),
        amount: f.get("amount") === "" ? null : Number(f.get("amount")),
        currency: f.get("currency"),
        recurrence: f.get("recurrence"),
        remind_days_before: Number(f.get("remind_days_before")),
      }),
      "/api/renewals",
      loadRenewals,
      "Renewal added.",
    );
    showTab("overview");
    state.timer = setInterval(refreshActive, 10000);
  }

  init().catch(() => {});
})();
```

- [ ] **Step 7: Run tests, do a manual check, commit**

Run: `uv run pytest tests/web -v` — Expected: PASS.

Manual smoke test (required, tests do not cover rendering):

```bash
export OPSPILOT_WEB_ENABLED=true OPSPILOT_WEB_INSECURE_COOKIES=true OPSPILOT_ADMIN_PASSWORD="local dev password 1"
```
This needs Task 11 to run the daemon; if it is not done yet, skip and perform this check at the end of Task 11. Look for: login works; every tab renders; empty states show; Restart asks for confirmation; Escape closes dialogs; keyboard Tab order is sane; page works at 375 px width; browser console shows no CSP violations.

```bash
uv run ruff check src tests && uv run ruff format --check src tests
git add src/opspilot/web/static tests/web/test_static.py
git commit -m "feat(web): dashboard frontend (vanilla JS, CSP-clean, accessible dialogs)" -m "Co-Authored-By: Claude Sonnet 5.5 <noreply@anthropic.com>"
```

---

## Task 11: Daemon integration and failure isolation

**Files:**
- Create: `src/opspilot/web/runner.py`
- Modify: `src/opspilot/main.py`
- Test: `tests/test_web_runner.py`

**Interfaces:**
- Consumes: `Settings` web fields (Task 2), `validate_web_credentials`, `CredentialVerifier`, `parse_trusted`, `WebDeps`, `SnapshotCache`, `BackgroundScheduler(..., snapshots=)`, `collect_docker_statuses`.
- Produces:
  - `runner.build_deps(settings, snapshots, channel) -> WebDeps`.
  - `runner.run_web_console(settings, snapshots, channel) -> None` — never raises (except `CancelledError`); logs and returns on any problem.
  - `main._maybe_start_web(settings, snapshots, channel) -> asyncio.Task | None`.

- [ ] **Step 1: Write the failing tests**

Create `tests/test_web_runner.py`:

```python
import asyncio
import logging
import socket

import pytest

pytest.importorskip("fastapi")
uvicorn = pytest.importorskip("uvicorn")

from opspilot.config import Settings  # noqa: E402
from opspilot.core.snapshot import SnapshotCache  # noqa: E402
from opspilot.db.engine import init_db, set_db_path  # noqa: E402
from opspilot.main import _maybe_start_web  # noqa: E402
from opspilot.web import runner  # noqa: E402

GOOD = "correct horse battery"


def _settings(**overrides) -> Settings:
    values = {"_env_file": None, "web_enabled": True, "admin_password": GOOD, "web_port": 0}
    values.update(overrides)
    return Settings(**values)


def _free_port() -> int:
    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        return sock.getsockname()[1]


async def test_disabled_by_default_starts_nothing():
    assert _maybe_start_web(Settings(_env_file=None), SnapshotCache(), None) is None


async def test_enabled_returns_a_named_task():
    task = _maybe_start_web(_settings(web_port=_free_port()), SnapshotCache(), None)
    assert task is not None and task.get_name() == "web-console"
    await asyncio.sleep(0.3)
    task.cancel()
    await asyncio.gather(task, return_exceptions=True)


@pytest.mark.parametrize("password", ["", "   ", "short"])
async def test_weak_or_missing_password_refuses_to_start_without_raising(password, caplog):
    caplog.set_level(logging.ERROR)
    await asyncio.wait_for(runner.run_web_console(_settings(admin_password=password), SnapshotCache(), None), 5)
    assert any("web console" in r.message.lower() and "not start" in r.message.lower() for r in caplog.records)


async def test_invalid_hash_refuses_to_start(caplog):
    caplog.set_level(logging.ERROR)
    settings = _settings(admin_password="", admin_password_hash="nonsense")
    await asyncio.wait_for(runner.run_web_console(settings, SnapshotCache(), None), 5)
    assert any("not start" in r.message.lower() for r in caplog.records)


async def test_invalid_trusted_proxy_setting_refuses_to_start(caplog):
    caplog.set_level(logging.ERROR)
    await asyncio.wait_for(runner.run_web_console(_settings(web_trusted_proxies="localhost"), SnapshotCache(), None), 5)
    assert any("not start" in r.message.lower() for r in caplog.records)


async def test_port_already_in_use_does_not_kill_the_daemon(caplog, tmp_path):
    """uvicorn calls sys.exit(1) when it cannot bind; that SystemExit must not escape."""
    caplog.set_level(logging.ERROR)
    set_db_path(tmp_path / "r.db")
    await init_db()
    with socket.socket() as blocker:
        blocker.bind(("127.0.0.1", 0))
        blocker.listen(1)
        port = blocker.getsockname()[1]
        await asyncio.wait_for(runner.run_web_console(_settings(web_port=port), SnapshotCache(), None), 10)


async def test_cancellation_stops_the_server_cleanly(tmp_path):
    set_db_path(tmp_path / "r.db")
    await init_db()
    task = asyncio.create_task(runner.run_web_console(_settings(web_port=_free_port()), SnapshotCache(), None))
    await asyncio.sleep(0.5)
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await asyncio.wait_for(task, 5)


async def test_missing_web_extra_is_reported_not_raised(monkeypatch, caplog):
    import builtins

    real_import = builtins.__import__

    def fake_import(name, *args, **kwargs):
        if name == "uvicorn":
            raise ImportError("no uvicorn")
        return real_import(name, *args, **kwargs)

    monkeypatch.setattr(builtins, "__import__", fake_import)
    caplog.set_level(logging.ERROR)
    await asyncio.wait_for(runner.run_web_console(_settings(), SnapshotCache(), None), 5)
    assert any("opspilot[web]" in r.message for r in caplog.records)


async def test_non_loopback_bind_logs_a_warning(caplog, tmp_path):
    set_db_path(tmp_path / "r.db")
    await init_db()
    caplog.set_level(logging.WARNING)
    task = asyncio.create_task(
        runner.run_web_console(_settings(web_host="0.0.0.0", web_port=_free_port()), SnapshotCache(), None)
    )
    await asyncio.sleep(0.5)
    task.cancel()
    await asyncio.gather(task, return_exceptions=True)
    assert any("0.0.0.0" in r.message and "reverse proxy" in r.message for r in caplog.records)
```

- [ ] **Step 2: Run and confirm failure**

Run: `uv run pytest tests/test_web_runner.py -v`
Expected: `ImportError: cannot import name '_maybe_start_web'`.

- [ ] **Step 3: Implement `runner.py`**

```python
"""Runs the web console as an isolated task inside the daemon."""

from __future__ import annotations

import asyncio
import logging

from opspilot.config import Settings
from opspilot.core.audit import AuditLogger
from opspilot.core.executor import SafeOperationExecutor
from opspilot.core.snapshot import SnapshotCache
from opspilot.monitor.docker import ContainerStatus, collect_docker_statuses
from opspilot.web.deps import WebDeps
from opspilot.web.netutil import parse_trusted
from opspilot.web.passwords import CredentialVerifier, validate_web_credentials
from opspilot.web.sessions import LoginThrottle, SessionStore

logger = logging.getLogger("opspilot.web")

LOOPBACK_HOSTS = {"127.0.0.1", "::1", "localhost"}


def build_deps(settings: Settings, snapshots: SnapshotCache, channel) -> WebDeps:
    async def list_containers() -> list[ContainerStatus]:
        return await asyncio.to_thread(collect_docker_statuses)

    return WebDeps(
        settings=settings,
        executor=SafeOperationExecutor(),
        audit=AuditLogger(),
        snapshots=snapshots,
        list_containers=list_containers,
        verifier=CredentialVerifier(
            settings.admin_password.get_secret_value(), settings.admin_password_hash.get_secret_value()
        ),
        channel=channel,
        sessions=SessionStore(),
        throttle=LoginThrottle(),
        trusted_proxies=parse_trusted(settings.web_trusted_proxies),
        secure_cookies=not settings.web_insecure_cookies,
    )


async def run_web_console(settings: Settings, snapshots: SnapshotCache, channel) -> None:
    """Serve the console until cancelled. Any failure is logged; the bot and scheduler keep running."""
    server = None
    try:
        try:
            import uvicorn

            from opspilot.web.app import create_app
        except ImportError:
            logger.error(
                "Web console is enabled but the web extra is not installed. "
                "Install it with: pip install 'opspilot[web]'. The web console will not start."
            )
            return

        problems = validate_web_credentials(
            settings.admin_password.get_secret_value(), settings.admin_password_hash.get_secret_value()
        )
        try:
            parse_trusted(settings.web_trusted_proxies)
        except ValueError:
            problems.append("OPSPILOT_WEB_TRUSTED_PROXIES must be a comma-separated list of IPs or CIDR ranges")
        if problems:
            for problem in problems:
                logger.error(f"Web console: {problem}.")
            logger.error("Web console will not start. The Telegram bot and scheduler keep running.")
            return

        if settings.web_host not in LOOPBACK_HOSTS:
            logger.warning(
                f"Web console is binding to {settings.web_host}, not loopback. "
                "Expose it only through a TLS reverse proxy and firewall the port."
            )
        if settings.web_insecure_cookies:
            logger.warning("OPSPILOT_WEB_INSECURE_COOKIES is on: session cookies lack the Secure flag. Dev only.")

        app = create_app(build_deps(settings, snapshots, channel))
        config = uvicorn.Config(
            app,
            host=settings.web_host,
            port=settings.web_port,
            log_level="info",
            proxy_headers=False,  # X-Forwarded-For is handled (and trust-checked) by opspilot.web.netutil
            server_header=False,
        )
        server = uvicorn.Server(config)
        server.install_signal_handlers = lambda: None  # the daemon owns shutdown
        logger.info(f"Web console listening on http://{settings.web_host}:{settings.web_port}")
        await server.serve()
    except asyncio.CancelledError:
        if server is not None:
            server.should_exit = True
        raise
    except (Exception, SystemExit) as exc:  # uvicorn raises SystemExit(1) when the port is unavailable
        logger.error(f"Web console stopped: {exc!r}. The Telegram bot and scheduler keep running.")
```

- [ ] **Step 4: Wire `main.py`**

Add near the top of `src/opspilot/main.py`:

```python
from opspilot.core.snapshot import SnapshotCache
```

Add this function above `run_daemon`:

```python
def _maybe_start_web(settings, snapshots: SnapshotCache, channel) -> "asyncio.Task | None":
    """Start the optional web console as its own task. Nothing web-related is imported when disabled."""
    if not settings.web_enabled:
        return None
    from opspilot.web.runner import run_web_console

    return asyncio.create_task(run_web_console(settings, snapshots, channel), name="web-console")
```

Replace everything in `run_daemon` from the line `if not settings.telegram_bot_token:` to the end of the function with:

```python
    snapshots = SnapshotCache()
    bot_client = None
    channel = None
    if settings.telegram_bot_token:
        from aiogram import Bot

        bot_client = Bot(token=settings.telegram_bot_token)
        # Construct the channel with the DB-persisted (or config) chat_id
        channel = TelegramChannel(bot_client, alert_chat_id)
    else:
        logger.warning("No TELEGRAM_BOT_TOKEN set. Running in headless monitoring mode.")

    scheduler = BackgroundScheduler(settings, channel=channel, snapshots=snapshots)
    scheduler_task = asyncio.create_task(scheduler.start())
    web_task = _maybe_start_web(settings, snapshots, channel)

    try:
        if bot_client is None:
            await scheduler_task
        else:
            bot, dp = create_bot_app(settings, ignored_manager=ignored_manager, channel=channel)
            logger.info("Telegram Bot ready. Polling for commands...")
            await dp.start_polling(bot)
    finally:
        await scheduler.stop()
        scheduler_task.cancel()
        if web_task is not None:
            web_task.cancel()
            await asyncio.gather(web_task, return_exceptions=True)
        if bot_client is not None:
            await bot_client.session.close()
```

- [ ] **Step 5: Run everything**

```bash
uv run pytest -q && uv run ruff check src tests && uv run ruff format --check src tests && uv run mypy src/
```
Expected: all PASS. `tests/test_startup_fixes.py` exercises `run_daemon` pieces — if a test there patched the old code path and now fails, adapt the patch target to the new structure (the behaviour, not the assertion, is what changed).

- [ ] **Step 6: Manual end-to-end check (also finishes Task 10's smoke test)**

```bash
export OPSPILOT_WEB_ENABLED=true OPSPILOT_WEB_INSECURE_COOKIES=true \
       OPSPILOT_ADMIN_PASSWORD="local dev password 1"
uv run python -m opspilot.main
```
Open `http://127.0.0.1:8088`, log in, walk every tab, add and remove a probe, add and pay a renewal, open logs for a container, restart a disposable container. Then stop with Ctrl-C and confirm the process exits cleanly. Repeat once with `OPSPILOT_ADMIN_PASSWORD=short` and confirm: the fatal message is logged, nothing listens on 8088, the bot still starts.

- [ ] **Step 7: Commit**

```bash
git add src/opspilot/web/runner.py src/opspilot/main.py tests/test_web_runner.py
git commit -m "feat(web): start the console as an isolated task inside the daemon" -m "Co-Authored-By: Claude Sonnet 5.5 <noreply@anthropic.com>"
```

---

## Task 12: Documentation, spec amendments and the v0.4.0 release gate

**Files:**
- Create: `docs/web-console.md`
- Modify: `README.md`, `CHANGELOG.md`, `SECURITY.md`, `pyproject.toml`, `docs/superpowers/specs/2026-09-30-opspilot-web-command-center.md`, `uv.lock`

**Interfaces:**
- Consumes: everything from Tasks 0–11.
- Produces: user documentation and a releasable v0.4.0 candidate. No code changes.

- [ ] **Step 1: Settle the reverse-proxy recipe (maintainer action)**

On the reference VPS run: `docker inspect core_caddy --format '{{.HostConfig.NetworkMode}}'`.
- `host` → use Option A below.
- anything else (a bridge network) → use Option B.

Record the result in the spec (Step 4).

- [ ] **Step 2: Write `docs/web-console.md`**

````markdown
# Web console (optional)

The web console is an optional dashboard for the same data the Telegram bot manages: containers, HTTP probes, renewals and incidents. It is **off by default**, listens on **loopback only** by default, and must be exposed through a TLS reverse proxy.

## Enable it

1. Install the extra (the official Docker image already includes it): `pip install 'opspilot[web]'`.
2. Create a password hash and put it in `.env`:

   ```bash
   opspilot web hash-password
   ```

   ```env
   OPSPILOT_WEB_ENABLED=true
   OPSPILOT_ADMIN_PASSWORD_HASH=pbkdf2_sha256:600000:...:...
   ```

   A plaintext `OPSPILOT_ADMIN_PASSWORD` (at least 12 characters) also works but is discouraged.
3. Restart OpsPilot. The log shows `Web console listening on http://127.0.0.1:8088`.

If the credential is missing or weak, or the web extra is not installed, OpsPilot logs the reason, does **not** start the console, and keeps running the bot and monitors.

## Put it behind a reverse proxy

Never publish port 8088 directly: the console can restart containers.

### Option A — Caddy runs on the host network

```caddyfile
ops.example.com {
    reverse_proxy 127.0.0.1:8088
    header Strict-Transport-Security "max-age=31536000; includeSubDomains"
}
```

Defaults (`OPSPILOT_WEB_HOST=127.0.0.1`, `OPSPILOT_WEB_TRUSTED_PROXIES=127.0.0.1`) work as they are.

### Option B — Caddy runs in a Docker bridge network, OpsPilot on the host network

A bridge container cannot reach the host's `127.0.0.1`. Bind the console to the Docker bridge gateway address instead and trust the bridge subnet:

```env
OPSPILOT_WEB_HOST=172.17.0.1                 # docker0 gateway; check with: ip -4 addr show docker0
OPSPILOT_WEB_TRUSTED_PROXIES=172.17.0.0/16   # the subnet your Caddy container is on
```

```caddyfile
ops.example.com {
    reverse_proxy 172.17.0.1:8088
}
```

Block the port from the internet as well (for example `ufw deny 8088`). The docker0 address is not reachable from outside the host, but a firewall rule costs nothing.

Unix-socket support is not implemented yet.

### Other proxies

The proxy must preserve the original `Host` header (Caddy does by default; for nginx add `proxy_set_header Host $host;`) and send `X-Forwarded-For`. The console compares the browser's `Origin` with `Host` to block cross-site requests.

## Security model

- Single admin account; sessions live in memory (a restart logs everyone out). Idle timeout 8 hours, absolute limit 24 hours.
- Cookie: `HttpOnly`, `Secure`, `SameSite=Strict`. Every state-changing request also needs a CSRF token and a same-host `Origin`.
- Failed logins back off exponentially and lock an address out after 5 failures in 15 minutes. Behind a proxy the real address is read from `X-Forwarded-For`, but only when the connection comes from `OPSPILOT_WEB_TRUSTED_PROXIES`.
- Restarts, snoozes, probe and renewal changes, log views and every login attempt are written to `audit_logs/audit_trail.jsonl` as `web-admin` with the client IP.
- Strict Content-Security-Policy; the page loads no external scripts, fonts or styles.
- Probes make outbound requests to any URL an admin enters, including internal addresses. Only give the password to people you would trust with shell access to the host network.
- The Docker socket is still the trust boundary: an admin can restart any container.

## Troubleshooting

| Symptom | Likely cause |
|---|---|
| Login works but you are sent back to the login page | The cookie is `Secure` and you are using plain `http://`. Use HTTPS through the proxy, or set `OPSPILOT_WEB_INSECURE_COOKIES=true` for local development only. |
| Every action fails with "Cross-origin request rejected" | The proxy is not passing the original `Host` header. |
| Everyone is locked out after one person mistypes | `OPSPILOT_WEB_TRUSTED_PROXIES` does not include the proxy, so all requests appear to come from the proxy address. |
| "Web console will not start" in the log | Missing/weak credential or `opspilot[web]` not installed; the log line says which. |
| "Address already in use" | Another process holds `OPSPILOT_WEB_PORT`; change the port. |
````

- [ ] **Step 3: README, SECURITY, CHANGELOG, version**

`README.md`: add to the Features table a row `| **🖥️ Web console** | Optional dashboard for containers, probes, renewals and incidents. Off by default; see [docs/web-console.md](docs/web-console.md). |`, add a section before "Roadmap":

```markdown
## 🖥️ Web console (optional)

Prefer a screen to a chat? Enable the built-in dashboard:

```env
OPSPILOT_WEB_ENABLED=true
OPSPILOT_ADMIN_PASSWORD_HASH=...        # create with: opspilot web hash-password
```

It listens on `127.0.0.1:8088` only and is meant to sit behind a TLS reverse proxy. Setup, Caddy recipes and the security model: [docs/web-console.md](docs/web-console.md).
```

Update the Roadmap: tick "Optional web admin UI" as shipped in 0.4.0. Add the new env vars to the `.env` overview if the README lists them. Do **not** state memory or CPU figures.

`SECURITY.md`: add a short "Web console" section that links to `docs/web-console.md#security-model` and repeats: off by default, loopback-only by default, reverse proxy required, probe requests can reach internal addresses.

`CHANGELOG.md`: add above `[0.3.1]`:

```markdown
## [0.4.0] - 2026-09-30

### Added
- **Optional web console** (`opspilot[web]`), disabled by default: overview, containers (logs, restart, snooze), HTTP probes, renewals and incidents.
- Security: server-side sessions, hardened cookie, CSRF tokens plus Origin check, login throttling with trusted-proxy aware client IPs, strict CSP, audit entries for every web action.
- `opspilot web hash-password` to create `OPSPILOT_ADMIN_PASSWORD_HASH`.
- Shared service layer used by both the Telegram bot and the web console.
- In-memory snapshot cache so web requests never trigger Docker or network calls.

### Changed
- Removing a probe is now a soft delete: probes defined in `config.yaml` no longer reappear after a restart.
- Docker calls run in worker threads, so a container restart no longer freezes the bot and scheduler.
- `/ignore` and snooze buttons reject unknown durations instead of silently muting forever.
- `/setchat` validates the chat ID.

### Migration notes
- Existing databases migrate automatically (`endpoints.deleted` column).
- To use the console, follow [docs/web-console.md](docs/web-console.md). Nothing changes if you do not enable it.
```

Add link line `[0.4.0]: https://github.com/iitdeveloper-git/opspilot/compare/v0.3.1...v0.4.0`. In `pyproject.toml` set `version = "0.4.0"`.

- [ ] **Step 4: Amend the spec to match what was built**

In `docs/superpowers/specs/2026-09-30-opspilot-web-command-center.md`:
1. §2.1: replace the three connectivity options with the two supported options (host-network Caddy; bridge gateway bind + trusted subnet) and mark Unix sockets "not supported yet". Record the maintainer's answer from Step 1.
2. §2.2: change the hash description to `pbkdf2_sha256:<iterations>:<salt>:<hash>` (no `$`), and replace "uvicorn `forwarded_allow_ips` is set to the same value" with "uvicorn `proxy_headers` is disabled; `X-Forwarded-For` is handled and trust-checked by `opspilot.web.netutil`".
3. §6: add the row `/api/containers/{name}/snooze | DELETE | Unmute a container`.
4. Set the header status to `IMPLEMENTED in v0.4.0/v0.4.1` once the release ships.

- [ ] **Step 5: Lock file, full gates**

```bash
uv lock
uv run pytest -v --cov=src/opspilot --cov-report=term-missing
uv run ruff check src/ tests/
uv run ruff format --check src/ tests/
uv run mypy src/
```
Expected: all green. Fix anything mypy reports in the new modules (add precise types; do not add blanket `# type: ignore`).

- [ ] **Step 6: Manual QA checklist (browser, real Docker)**

Run with a hashed password behind either a local proxy or with `OPSPILOT_WEB_INSECURE_COOKIES=true`, and check each item:
- Login: wrong password → error message; 5 wrong passwords → lockout message; correct password after the window → works.
- Overview gauges and container count update within ~10 s of a container stopping.
- Containers: Logs modal scrolls, shows `<script>` text literally if a container prints it; Restart asks first; Snooze then Unmute.
- Probes: add, see latency after one cycle, disable, remove; re-add the same name.
- Renewals: add, mark paid (monthly one shows next due date), snooze, filter by status.
- Incidents: open and resolved lists.
- Keyboard only: Tab through nav, tables, dialogs; Escape closes dialogs; focus returns to the trigger.
- 375 px wide viewport: no horizontal page scroll (tables scroll inside their wrapper).
- Browser console: zero CSP violations.
- Stop OpsPilot with Ctrl-C: exits promptly.

- [ ] **Step 7: Required reviews before merge**

Invoke the `architecture-review` skill (security-sensitive new subsystem) and the `ui-ux-review` skill (new screens) on the branch. Fix every Critical/High finding, re-run Step 5, note the rest in the PR description.

- [ ] **Step 8: Commit (do not tag or push)**

```bash
git add docs README.md CHANGELOG.md SECURITY.md pyproject.toml uv.lock
git commit -m "docs: web console guide, changelog and release prep for v0.4.0" -m "Co-Authored-By: Claude Sonnet 5.5 <noreply@anthropic.com>"
```

Report to the maintainer that v0.4.0 is ready; tagging, pushing and deploying need their explicit OK.

---

## Task 13: v0.4.1 — live updates over WebSocket

**Files:**
- Create: `src/opspilot/web/routes/live.py`
- Modify: `src/opspilot/web/app.py` (register the router), `src/opspilot/web/static/app.js`, `src/opspilot/web/static/styles.css`
- Test: `tests/web/test_live.py`

**Interfaces:**
- Consumes: `auth.COOKIE_NAME`, `auth.get_deps`, `netutil.origin_matches_host`, `SessionStore.get/peek`, `overview.build_overview(deps)`, snapshot keys, `WebDeps.live_interval`.
- Produces: `WS /ws/live` sending `{"type": "snapshot", "overview": <build_overview>, "containers": <snapshot>, "probes": <snapshot>}` every `live_interval` seconds (default 5).

- [ ] **Step 1: Write the failing tests**

Create `tests/web/test_live.py`:

```python
import pytest
from starlette.websockets import WebSocketDisconnect
from web_helpers import login

from opspilot.core.snapshot import SYSTEM


def _headers(env, origin=None):
    token = env.client.cookies.get("opspilot_session")
    headers = {"Origin": origin or env.origin}
    if token:
        headers["Cookie"] = f"opspilot_session={token}"
    return headers


def test_rejects_connections_without_a_session(env):
    with pytest.raises(WebSocketDisconnect):
        with env.client.websocket_connect("/ws/live", headers={"Origin": env.origin}):
            pass


def test_rejects_foreign_origin_even_with_a_valid_session(env):
    login(env)
    with pytest.raises(WebSocketDisconnect):
        with env.client.websocket_connect("/ws/live", headers=_headers(env, "https://evil.example.net")):
            pass


def test_rejects_missing_origin(env):
    login(env)
    headers = _headers(env)
    del headers["Origin"]
    with pytest.raises(WebSocketDisconnect):
        with env.client.websocket_connect("/ws/live", headers=headers):
            pass


def test_streams_snapshots(env):
    env.snapshots.set(SYSTEM, {"cpu_percent": 7.0, "ram_percent": 1.0, "disk_percent": 2.0, "uptime_human": "1d"})
    login(env)
    with env.client.websocket_connect("/ws/live", headers=_headers(env)) as ws:
        first = ws.receive_json()
        assert first["type"] == "snapshot"
        assert first["overview"]["system"]["data"]["cpu_percent"] == 7.0
        assert {"containers", "probes"} <= set(first)
        env.snapshots.set(SYSTEM, {"cpu_percent": 9.0, "ram_percent": 1.0, "disk_percent": 2.0, "uptime_human": "1d"})
        second = ws.receive_json()
        assert second["overview"]["system"]["data"]["cpu_percent"] == 9.0


def test_closes_when_the_session_is_revoked(env):
    login(env)
    token = env.client.cookies.get("opspilot_session")
    with env.client.websocket_connect("/ws/live", headers=_headers(env)) as ws:
        ws.receive_json()
        env.deps.sessions.revoke(token)
        with pytest.raises(WebSocketDisconnect):
            for _ in range(20):
                ws.receive_json()


def test_open_socket_does_not_keep_an_idle_session_alive(env):
    login(env)
    with env.client.websocket_connect("/ws/live", headers=_headers(env)) as ws:
        ws.receive_json()
        env.clock.advance(9 * 3600)  # past the 8 h idle timeout with no real activity
        with pytest.raises(WebSocketDisconnect):
            for _ in range(20):
                ws.receive_json()
```

- [ ] **Step 2: Run and confirm failure**

Run: `uv run pytest tests/web/test_live.py -v`
Expected: FAIL (`/ws/live` is not routed; the first test may pass by accident — the others will not).

- [ ] **Step 3: Implement the route**

`src/opspilot/web/routes/live.py`:

```python
"""Authenticated WebSocket that pushes cached snapshots."""

from __future__ import annotations

import asyncio

from fastapi import APIRouter, WebSocket, WebSocketDisconnect

from opspilot.core.snapshot import CONTAINERS, PROBES
from opspilot.web.auth import COOKIE_NAME
from opspilot.web.netutil import origin_matches_host
from opspilot.web.routes.overview import build_overview

router = APIRouter()

POLICY_VIOLATION = 1008


@router.websocket("/ws/live")
async def live(ws: WebSocket) -> None:
    deps = ws.app.state.deps
    token = ws.cookies.get(COOKIE_NAME)
    same_origin = origin_matches_host(ws.headers.get("origin"), None, ws.headers.get("host"))
    if not same_origin or deps.sessions.get(token) is None:
        await ws.close(code=POLICY_VIOLATION)
        return
    await ws.accept()
    try:
        while True:
            if deps.sessions.peek(token) is None:  # peek: an open socket must not extend the session
                await ws.close(code=POLICY_VIOLATION)
                return
            await ws.send_json(
                {
                    "type": "snapshot",
                    "overview": await build_overview(deps),
                    "containers": deps.snapshots.snapshot(CONTAINERS),
                    "probes": deps.snapshots.snapshot(PROBES),
                }
            )
            await asyncio.sleep(deps.live_interval)
    except WebSocketDisconnect:
        return
```

Register it in `create_app`: import `live` in the `from opspilot.web.routes import ...` line and add `live.router` to the router tuple.

- [ ] **Step 4: Use it in the frontend**

In `app.js`:

1. Add `live: false` to `state`.
2. Add before `init`:

```js
  function connectLive() {
    const scheme = location.protocol === "https:" ? "wss" : "ws";
    const dot = $("#live-dot");
    const socket = new WebSocket(`${scheme}://${location.host}/ws/live`);
    socket.addEventListener("open", () => {
      state.live = true;
      dot.classList.remove("offline");
    });
    socket.addEventListener("message", (event) => {
      let message;
      try {
        message = JSON.parse(event.data);
      } catch (err) {
        return;
      }
      if (message.type !== "snapshot") return;
      if (state.tab === "overview") renderOverview(message.overview);
      else if (state.tab === "containers" || state.tab === "probes") refreshActive();
    });
    socket.addEventListener("close", () => {
      state.live = false;
      dot.classList.add("offline");
      // if the session ended the next API call redirects to /login; otherwise try again shortly
      guarded(() => api("GET", "/api/auth/session")).then((session) => {
        if (session) setTimeout(connectLive, 5000);
      });
    });
  }
```

3. In `init`, replace `state.timer = setInterval(refreshActive, 10000);` with:

```js
    state.timer = setInterval(() => {
      const pushed = state.live && ["overview", "containers", "probes"].includes(state.tab);
      if (!pushed) refreshActive();
    }, 10000);
    connectLive();
```

In `styles.css` add: `.pulse.offline { background: var(--warn); animation: none; }`.

- [ ] **Step 5: Run and commit**

```bash
uv run pytest -q && uv run ruff check src tests && uv run ruff format --check src tests && uv run mypy src/
```

Manual check: open the dashboard, watch the pulse dot; stop OpsPilot's web task (or the network) and confirm the dot turns amber and the page keeps working via polling; log out in another tab and confirm this tab is redirected.

```bash
git add src/opspilot/web tests/web/test_live.py
git commit -m "feat(web): authenticated WebSocket live updates" -m "Co-Authored-By: Claude Sonnet 5.5 <noreply@anthropic.com>"
```

---

## Task 14: v0.4.1 — settings screen (alert chat ID) and release gate

Scope is deliberately limited to the alert chat ID (spec §4, Phase 2). Thresholds, intervals and YAML-owned settings stay out.

**Files:**
- Create: `src/opspilot/web/routes/settings.py`
- Modify: `src/opspilot/web/schemas.py`, `src/opspilot/web/app.py`, `src/opspilot/web/static/index.html`, `static/app.js`, `README.md`, `docs/web-console.md`, `CHANGELOG.md`, `pyproject.toml`, spec status
- Test: `tests/web/test_api_settings.py`, `tests/web/test_static.py` (extend)

**Interfaces:**
- Consumes: `services.settings.set_alert_chat_id/get_alert_chat_id/InvalidChatId`, `WebDeps.channel`, `auth.require_session/require_csrf/audit_event/ApiError`.
- Produces: `GET /api/settings` → `{"alert_chat_id": str, "server_name": str, "channel_available": bool}`; `POST /api/settings/alert-chat` body `{"chat_id": str}` → `{"ok": true, "chat_id": str, "applied_live": bool}`.

- [ ] **Step 1: Write the failing tests**

Create `tests/web/test_api_settings.py`:

```python
from web_helpers import audit_entries, authed_headers, login, run

from opspilot.services.settings import get_alert_chat_id


class FakeChannel:
    def __init__(self):
        self.chat_id = "-100000000"

    def update_chat_id(self, chat_id: str) -> None:
        self.chat_id = chat_id


def test_settings_require_session(env):
    assert env.client.get("/api/settings").status_code == 401


def test_get_settings_never_exposes_secrets(env):
    env.deps.settings.telegram_bot_token = "123456:SECRET-TOKEN-VALUE"
    login(env)
    r = env.client.get("/api/settings")
    assert r.status_code == 200
    assert "SECRET-TOKEN-VALUE" not in r.text
    assert set(r.json()) == {"alert_chat_id", "server_name", "channel_available"}


def test_set_alert_chat_persists_and_updates_the_live_channel(env):
    env.deps.channel = FakeChannel()
    headers = authed_headers(env)
    r = env.client.post("/api/settings/alert-chat", json={"chat_id": " -1001234567890 "}, headers=headers)
    assert r.status_code == 200
    assert r.json() == {"ok": True, "chat_id": "-1001234567890", "applied_live": True}
    assert env.deps.channel.chat_id == "-1001234567890"
    assert run(get_alert_chat_id("default")) == "-1001234567890"
    assert env.client.get("/api/settings").json()["alert_chat_id"] == "-1001234567890"
    entry = audit_entries(env)[-1]
    assert entry["action"] == "setchat" and entry["user_id"] == "web-admin"


def test_set_alert_chat_without_a_channel_still_persists(env):
    headers = authed_headers(env)
    r = env.client.post("/api/settings/alert-chat", json={"chat_id": "@ops_alerts"}, headers=headers)
    assert r.status_code == 200 and r.json()["applied_live"] is False
    assert env.client.get("/api/settings").json()["channel_available"] is False


def test_invalid_chat_ids_are_rejected_and_nothing_changes(env):
    env.deps.channel = FakeChannel()
    headers = authed_headers(env)
    for bad in ("", "abc", "12", "1234 5678", "x" * 65):
        r = env.client.post("/api/settings/alert-chat", json={"chat_id": bad}, headers=headers)
        assert r.status_code == 422, bad
    assert env.deps.channel.chat_id == "-100000000"
    assert run(get_alert_chat_id("default")) == "default"


def test_set_alert_chat_requires_csrf(env):
    login(env)
    r = env.client.post("/api/settings/alert-chat", json={"chat_id": "-1001234567890"}, headers={"Origin": env.origin})
    assert r.status_code == 403
```

Append to `tests/web/test_static.py`:

```python
def test_index_has_a_settings_tab_and_panel():
    html = (STATIC / "index.html").read_text(encoding="utf-8")
    assert 'data-tab="settings"' in html
    assert 'id="tab-settings"' in html
    assert 'id="settings-body"' in html
```

- [ ] **Step 2: Run and confirm failure**

Run: `uv run pytest tests/web/test_api_settings.py tests/web/test_static.py -v`
Expected: FAIL (routes and markup missing).

- [ ] **Step 3: Implement the API**

Append to `src/opspilot/web/schemas.py`:

```python
class AlertChatBody(BaseModel):
    chat_id: str = Field(min_length=1, max_length=64)
```

`src/opspilot/web/routes/settings.py`:

```python
from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Depends, Request

from opspilot.services.settings import InvalidChatId, get_alert_chat_id, set_alert_chat_id
from opspilot.web.auth import ApiError, audit_event, get_deps, require_csrf, require_session
from opspilot.web.schemas import AlertChatBody

router = APIRouter(prefix="/api/settings")


@router.get("")
async def read_settings(request: Request, _: object = Depends(require_session)) -> dict[str, Any]:
    deps = get_deps(request)
    return {
        "alert_chat_id": await get_alert_chat_id(deps.settings.telegram_alert_chat_id),
        "server_name": deps.settings.server_name,
        "channel_available": deps.channel is not None,
    }


@router.post("/alert-chat")
async def update_alert_chat(body: AlertChatBody, request: Request, _: object = Depends(require_csrf)) -> dict[str, Any]:
    deps = get_deps(request)
    try:
        chat_id = await set_alert_chat_id(body.chat_id, deps.channel)
    except InvalidChatId as exc:
        raise ApiError(422, "invalid_chat_id", str(exc)) from None
    audit_event(request, "setchat", "alert_chat_id", "SUCCESS", {"chat_id": chat_id})
    return {"ok": True, "chat_id": chat_id, "applied_live": deps.channel is not None}
```

Register `settings.router` in `create_app` (same pattern as the other routers).

- [ ] **Step 4: Add the Settings tab**

`index.html`: in `<nav class="tabs">` add `<button type="button" class="tab" data-tab="settings">Settings</button>`, and after the incidents section add:

```html
    <section id="tab-settings" class="panel" aria-labelledby="h-settings" hidden>
      <h2 id="h-settings">Settings</h2>
      <div id="settings-body">
        <form id="settings-form" class="stack card">
          <label for="alert-chat-id">Alert chat ID</label>
          <input id="alert-chat-id" name="chat_id" maxlength="64" required>
          <p class="muted" id="settings-note">Where OpsPilot sends alerts. A number such as -1001234567890, or @channel_name.</p>
          <p class="form-error" id="settings-error" role="alert" hidden></p>
          <div class="dialog-actions"><button type="submit" class="btn primary">Save</button></div>
        </form>
      </div>
    </section>
```

`app.js`: add the loader and wiring:

```js
  async function loadSettings() {
    const data = await api("GET", "/api/settings");
    $("#alert-chat-id").value = data.alert_chat_id || "";
    $("#settings-note").textContent = data.channel_available
      ? "Where OpsPilot sends alerts. Changes apply immediately."
      : "Telegram is not connected in this process; the value is saved and used after a restart.";
  }
```

Add `settings: loadSettings` to `loaders`, `settings: "#settings-body"` to the `target` map in `refreshActive`, and in `init` (before `showTab("overview")`):

```js
    $("#settings-form").addEventListener("submit", async (event) => {
      event.preventDefault();
      const errorBox = $("#settings-error");
      errorBox.hidden = true;
      try {
        const result = await api("POST", "/api/settings/alert-chat", { chat_id: $("#alert-chat-id").value });
        toast(result.applied_live ? "Alert chat updated." : "Saved. It will apply after a restart.", "success");
      } catch (err) {
        errorBox.textContent = err.message;
        errorBox.hidden = false;
      }
    });
```

Note: `refreshActive(true)` calls `loading($(target))`, which would replace the form with skeletons. For the settings tab only, skip the skeleton: in `refreshActive`, change the condition to `if (showSkeleton === true && state.tab !== "settings") loading($(target));`, and do not auto-refresh it (add `state.tab !== "settings"` to the interval guard) so typing is never overwritten.

- [ ] **Step 5: Docs, changelog, version, spec status**

- `docs/web-console.md`: mention live updates (WebSocket, falls back to polling) and the Settings screen (alert chat ID only), and that the WebSocket needs the proxy to allow upgrades (Caddy does by default; nginx needs `proxy_set_header Upgrade $http_upgrade; proxy_set_header Connection "upgrade";`). Add a troubleshooting row: pulse dot stays amber → the proxy blocks WebSocket upgrades.
- `README.md`: mention live updates and settings in the web console section.
- `CHANGELOG.md`: add `## [0.4.1] - <release date>` with Added: live updates over an authenticated WebSocket (session and `Origin` checked, closes on logout/expiry); Settings screen for the alert chat ID. Add the compare link.
- `pyproject.toml`: `version = "0.4.1"`.
- Spec header status → `IMPLEMENTED in v0.4.0/v0.4.1`.

- [ ] **Step 6: Full gates, manual check, reviews, commit**

```bash
uv run pytest -v --cov=src/opspilot --cov-report=term-missing
uv run ruff check src/ tests/ && uv run ruff format --check src/ tests/ && uv run mypy src/
```

Manual: change the alert chat ID in the browser, confirm the next Telegram alert goes to the new chat and that `/setchat` in Telegram shows the same value; restart and confirm it persisted; confirm an invalid ID shows the inline error.

Re-run the `ui-ux-review` skill on the Settings screen and WebSocket status indicator if the earlier review flagged anything there.

```bash
git add src docs README.md CHANGELOG.md pyproject.toml tests
git commit -m "feat(web): settings screen for the alert chat ID (v0.4.1)" -m "Co-Authored-By: Claude Sonnet 5.5 <noreply@anthropic.com>"
```

Report to the maintainer that v0.4.1 is ready; tagging, pushing and deploying need their explicit OK.

---

## Deployment checklist (maintainer, after v0.4.x is tagged)

1. On the VPS: `opspilot web hash-password` (or in a throwaway container) and add `OPSPILOT_WEB_ENABLED=true` plus `OPSPILOT_ADMIN_PASSWORD_HASH=...` to `/opt/opspilot/.env`.
2. Apply the proxy recipe chosen in Task 12 (host-network Caddy, or bridge gateway bind plus trusted subnet); add the Caddy site block; reload Caddy.
3. Firewall: confirm port 8088 is not reachable from the internet (`curl http://<public-ip>:8088` must fail).
4. Deploy, then check `docker logs opspilot-agent` for `Web console listening on ...` and no `will not start` lines.
5. Log in over HTTPS, run the Task 12 manual QA list against the real fleet, and trigger one deliberate failure (stop a disposable container) to see it appear in Incidents.
6. Confirm `audit_logs/audit_trail.jsonl` contains `web-admin` entries with the correct client IP (proves the trusted-proxy setting is right).

## Spec coverage

| Spec section | Task(s) |
|---|---|
| §2.1 exposure (off by default, loopback, proxy) | 2, 11, 12 |
| §2.2 authentication (validation, hashing, throttle, client IP) | 6, 7, 8, 11 |
| §2.3 sessions | 7, 8 |
| §2.4 CSRF, headers, validation | 8, 9 |
| §2.5 failure isolation, optional extra, lazy import | 2, 11 |
| §2.6 shared services, executor use, audit, XSS, SSRF note | 3, 9, 10, 12 |
| §3 snapshot cache | 5, 9 |
| §4 Phase 1 screens | 9, 10 |
| §4 Phase 2 WebSocket, settings | 13, 14 |
| §5 removal semantics | 4, 9 |
| §6 API | 8, 9, 13, 14 |
| §7 UI states and accessibility | 10, 12 |
| §8 configuration | 2, 12 |
| §9 tests and gates | every task; 12, 14 |
| Findings outside the spec (hotfix, blocking executor, duration typo) | 0, 1, 3 |
