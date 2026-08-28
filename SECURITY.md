# Security Policy

## Reporting a Vulnerability

If you discover a potential security vulnerability in OpsPilot, please do **NOT** create a public issue.

Email security disclosures to: **security@iitdeveloper.com** or open a private [GitHub Security Advisory](https://github.com/iitdeveloper-git/opspilot/security/advisories/new).

---

## Security Model

### Authentication (Telegram)

OpsPilot uses a **User ID allowlist** to control who can interact with the bot:

- **Production mode** (default): If `TELEGRAM_ALLOWED_USER_IDS` is not configured, OpsPilot **denies all incoming requests** (fail-closed). This is the safe default.
- **Development mode**: Setting `OPSPILOT_AUTH_MODE=development` allows all Telegram users to control the bot. **Never use this in production.**

### Safe Operation Executor

All bot actions (restart, logs, prune) go through `SafeOperationExecutor`, which:

- Never executes arbitrary shell strings (`shell=True` is forbidden)
- Uses the Docker SDK's typed API exclusively
- Returns structured results; failures surface explicitly

### Audit Trail

Every action (authorized or blocked) is recorded to a local JSONL audit log at `audit_logs/audit.jsonl`. This log is **locally writable** and not currently tamper-evident. For high-compliance environments, ship audit logs to an external append-only store.

---

## Docker Socket Trust Boundary

OpsPilot mounts the Docker socket:

```yaml
volumes:
  - /var/run/docker.sock:/var/run/docker.sock:ro
```

**Important**: The `:ro` (read-only) flag on the bind mount applies to the file itself, not to the Docker Engine operations it enables. OpsPilot necessarily requires mutating Docker API access to restart containers and prune resources.

> [!CAUTION]
> **Control of the Docker socket is effectively equivalent to root access on the Docker host.** Docker itself documents this risk. A compromised OpsPilot instance with direct Docker socket access should be treated as a potential compromise of the entire Docker host.

**Mitigations in use:**
- All Docker operations go through the typed SDK (no shell exec)
- The allowlist restricts which Telegram users can trigger operations
- Every action is audit-logged

**Recommended hardening for high-security environments:**
- Replace direct socket access with a restricted Docker API proxy (e.g., [Tecnativa/docker-socket-proxy](https://github.com/Tecnativa/docker-socket-proxy)) that whitelists only the specific endpoints OpsPilot needs
- Run OpsPilot as a non-root user inside the container
- Use Docker's user namespace remapping

---

## Supported Versions

| Version | Supported |
|---------|-----------|
| `main` branch | ✅ Security fixes applied |
| Older releases | ❌ Please upgrade |
