"""
Tests for the three production startup bugs:

Fix #1 — /setchat persists across restarts (alert_chat_id read from DB on startup)
Fix #2 — Deleted YAML items don't come back after restart (soft-delete)
Fix #3 — v0.2.0 ignored_containers.json migrated to container_snooze DB on first boot
"""
import json
from datetime import UTC, datetime, timedelta

import pytest

from opspilot.db.endpoints import list_endpoints, remove_endpoint, seed_endpoints_from_yaml
from opspilot.db.engine import init_db, set_db_path
from opspilot.db.renewals import delete_renewal, list_renewals, seed_renewals_from_yaml
from opspilot.db.snooze import is_snoozed
from opspilot.db.store import get_setting, set_setting


@pytest.fixture(autouse=True)
async def fresh_db(tmp_path):
    set_db_path(tmp_path / "test.db")
    await init_db()
    return tmp_path


# ─── Fix #1: /setchat persists across restarts ────────────────────────────────

async def test_setchat_persisted_and_read_at_startup():
    """
    Simulates: user calls /setchat → saves to DB → process restarts →
    startup reads DB value (not stale YAML/env value).
    """
    config_chat_id = "-100ORIGINAL"
    new_chat_id = "-100UPDATED_VIA_SETCHAT"

    # User calls /setchat → saved to DB
    await set_setting("alert_chat_id", new_chat_id)

    # Startup logic: read from DB, fall back to config value
    startup_chat_id = await get_setting("alert_chat_id", config_chat_id)

    assert startup_chat_id == new_chat_id, (
        f"Expected DB value '{new_chat_id}', got '{startup_chat_id}'. "
        "Startup is ignoring the persisted /setchat value."
    )


async def test_setchat_falls_back_to_config_when_not_set():
    """When no DB override exists, startup must use the config/env value."""
    config_chat_id = "-100FROM_CONFIG"
    startup_chat_id = await get_setting("alert_chat_id", config_chat_id)
    assert startup_chat_id == config_chat_id


async def test_setchat_updates_live_channel():
    """TelegramChannel.update_chat_id() must immediately change where alerts go."""
    from unittest.mock import MagicMock

    from opspilot.channels.telegram import TelegramChannel

    fake_bot = MagicMock()
    channel = TelegramChannel(fake_bot, "-100OLD")
    assert channel._chat_id == "-100OLD"

    channel.update_chat_id("-100NEW")
    assert channel._chat_id == "-100NEW"


# ─── Fix #2: Deleted YAML items don't come back on restart ────────────────────

async def test_removed_endpoint_not_reseeded_after_restart():
    """
    Scenario: endpoint defined in YAML → seeded → user runs /rmprobe →
    process restarts → seed runs again → endpoint must stay disabled.
    """
    yaml_items = [{"name": "GNS Health", "url": "https://gns.test/health"}]

    # First boot: seed from YAML
    seeded = await seed_endpoints_from_yaml(yaml_items)
    assert seeded == 1

    eps = await list_endpoints(enabled_only=False)
    ep_id = next(e["id"] for e in eps if e["name"] == "GNS Health")

    # User removes it (/rmprobe → soft-delete: enabled=0)
    await remove_endpoint(ep_id)

    disabled = await list_endpoints(enabled_only=False)
    ep = next(e for e in disabled if e["name"] == "GNS Health")
    assert ep["enabled"] == 0, "Endpoint must be disabled after /rmprobe"

    # Process restarts: seed runs again
    reseeded = await seed_endpoints_from_yaml(yaml_items)
    assert reseeded == 0, "Seed must not re-insert a soft-deleted endpoint"

    # Endpoint must remain disabled
    after_restart = await list_endpoints(enabled_only=False)
    ep_after = next(e for e in after_restart if e["name"] == "GNS Health")
    assert ep_after["enabled"] == 0, "Endpoint came back enabled after restart — seed stomped user decision"


async def test_removed_endpoint_absent_from_active_list():
    """Disabled endpoint must NOT appear in the enabled-only list the scheduler uses."""
    yaml_items = [{"name": "API Health", "url": "https://api.test/health"}]
    await seed_endpoints_from_yaml(yaml_items)
    eps = await list_endpoints(enabled_only=False)
    ep_id = next(e["id"] for e in eps if e["name"] == "API Health")
    await remove_endpoint(ep_id)

    active = await list_endpoints(enabled_only=True)
    assert not any(e["name"] == "API Health" for e in active)


async def test_deleted_renewal_not_reseeded_after_restart():
    """
    Scenario: renewal defined in YAML → seeded → user deletes it →
    restart → seed runs → renewal must stay cancelled.
    """
    yaml_items = [{"name": "Domain", "category": "domain", "due_date": "2027-01-01"}]

    seeded = await seed_renewals_from_yaml(yaml_items)
    assert seeded == 1

    all_r = await list_renewals()
    r_id = next(r["id"] for r in all_r if r["name"] == "Domain")

    # User deletes the renewal (soft-delete → status='cancelled')
    await delete_renewal(r_id)

    cancelled = await list_renewals()
    r = next(r for r in cancelled if r["name"] == "Domain")
    assert r["status"] == "cancelled"

    # Restart: seed runs again
    reseeded = await seed_renewals_from_yaml(yaml_items)
    assert reseeded == 0, "Seed must not re-insert a cancelled (soft-deleted) renewal"

    after = await list_renewals()
    r_after = next(r for r in after if r["name"] == "Domain")
    assert r_after["status"] == "cancelled", "Renewal came back as 'pending' after restart"


# ─── Fix #3: v0.2.0 ignored_containers.json migrated on first boot ────────────

async def test_migrate_ignored_json_new_format(tmp_path, fresh_db):
    """New JSON format (dict with expires_at) is imported into container_snooze."""
    from opspilot.core.ignored import IgnoredContainersManager
    from opspilot.main import _migrate_ignored_json

    # Create fake audit_logs dir in tmp_path and set cwd-like path
    audit_dir = tmp_path / "audit_logs"
    audit_dir.mkdir()
    json_file = audit_dir / "ignored_containers.json"

    future = (datetime.now(UTC) + timedelta(hours=5)).strftime("%Y-%m-%d %H:%M:%S")
    data = {
        "my_app": {"ignored_at": "2026-09-01T00:00:00+00:00", "expires_at": future, "duration_str": "5h"},
        "redis":  {"ignored_at": "2026-09-01T00:00:00+00:00", "expires_at": None,   "duration_str": "forever"},
    }
    json_file.write_text(json.dumps(data))

    import os
    orig_dir = os.getcwd()
    os.chdir(tmp_path)
    try:
        ignored_manager = IgnoredContainersManager()
        await _migrate_ignored_json(ignored_manager)
    finally:
        os.chdir(orig_dir)

    assert await is_snoozed("my_app"), "my_app should be snoozed after migration"
    assert await is_snoozed("redis"),  "redis (forever) should be snoozed after migration"
    assert not json_file.exists(), "JSON file must be renamed after migration"
    assert (json_file.with_suffix(".json.migrated")).exists(), "Renamed file must exist"


async def test_migrate_ignored_json_old_format(tmp_path, fresh_db):
    """Old JSON format (plain list of names) is imported as indefinite snooze."""
    from opspilot.core.ignored import IgnoredContainersManager
    from opspilot.main import _migrate_ignored_json

    audit_dir = tmp_path / "audit_logs"
    audit_dir.mkdir()
    json_file = audit_dir / "ignored_containers.json"
    json_file.write_text(json.dumps(["celery_worker", "flower"]))

    import os
    orig_dir = os.getcwd()
    os.chdir(tmp_path)
    try:
        await _migrate_ignored_json(IgnoredContainersManager())
    finally:
        os.chdir(orig_dir)

    assert await is_snoozed("celery_worker")
    assert await is_snoozed("flower")
    assert not json_file.exists()


async def test_migrate_ignored_json_skips_expired(tmp_path, fresh_db):
    """Entries that have already expired must NOT be imported."""
    from opspilot.core.ignored import IgnoredContainersManager
    from opspilot.main import _migrate_ignored_json

    audit_dir = tmp_path / "audit_logs"
    audit_dir.mkdir()
    json_file = audit_dir / "ignored_containers.json"

    already_past = (datetime.now(UTC) - timedelta(hours=2)).strftime("%Y-%m-%d %H:%M:%S")
    data = {"expired_svc": {"expires_at": already_past, "duration_str": "1h"}}
    json_file.write_text(json.dumps(data))

    import os
    orig_dir = os.getcwd()
    os.chdir(tmp_path)
    try:
        await _migrate_ignored_json(IgnoredContainersManager())
    finally:
        os.chdir(orig_dir)

    assert not await is_snoozed("expired_svc"), "Expired entry must be skipped"


async def test_migrate_ignored_json_idempotent(tmp_path, fresh_db):
    """Migration must not run twice — renamed file prevents re-import."""
    from opspilot.core.ignored import IgnoredContainersManager
    from opspilot.main import _migrate_ignored_json

    audit_dir = tmp_path / "audit_logs"
    audit_dir.mkdir()
    # Only the .migrated file exists (already done)
    migrated_file = audit_dir / "ignored_containers.json.migrated"
    migrated_file.write_text(json.dumps(["old_container"]))

    import os
    orig_dir = os.getcwd()
    os.chdir(tmp_path)
    try:
        await _migrate_ignored_json(IgnoredContainersManager())
    finally:
        os.chdir(orig_dir)

    # Should not have imported old_container
    assert not await is_snoozed("old_container")
