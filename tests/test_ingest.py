from __future__ import annotations

from pathlib import Path

from agentmail.config import Settings
from agentmail.db import Database
from agentmail.ingest import IngestService


def make_settings(tmp_path: Path) -> Settings:
    return Settings(
        storage_root=tmp_path,
        db_path=tmp_path / "agentmail.db",
        bind_host="127.0.0.1",
        bind_port=8787,
        ingest_token="ingest-token",
        api_token="api-token",
        auth_disabled=False,
        allowed_senders=[],
        max_email_bytes=26_214_400,
        max_attachment_bytes=26_214_400,
        startup_mail_lookback_days=10,
        blocked_extensions={".sh", ".exe", ".ps1"},
        api_url="http://testserver",
        forward_copy_to=None,
    )


def test_ingest_simple_email_and_duplicate(tmp_path: Path) -> None:
    settings = make_settings(tmp_path)
    settings.ensure_storage_dirs()
    db = Database(settings.db_path)
    db.init()
    service = IngestService(settings, db)
    raw = Path("tests/fixtures/simple.eml").read_bytes()

    first = service.ingest_raw(raw, envelope_from="client@example.com", envelope_to="bot+clientsite@alexpitcher.co.uk")
    second = service.ingest_raw(raw, envelope_from="client@example.com", envelope_to="bot+clientsite@alexpitcher.co.uk")

    assert first["status"] == "created"
    assert second["status"] == "duplicate"
    assert first["email_id"] == second["email_id"]
    row = db.get_email(first["email_id"])
    assert row is not None
    assert row["plus_tag"] == "clientsite"
    assert row["route_local_part"] == "bot"
    assert db.search("clientsite")[0]["id"] == first["email_id"]


def test_ingest_attachment_sanitizes_filename_and_extracts_text(tmp_path: Path) -> None:
    settings = make_settings(tmp_path)
    settings.ensure_storage_dirs()
    db = Database(settings.db_path)
    db.init()
    service = IngestService(settings, db)
    raw = Path("tests/fixtures/with_attachments.eml").read_bytes()

    result = service.ingest_raw(raw, envelope_from="client@example.com", envelope_to="bot+clientsite@alexpitcher.co.uk")
    attachments = db.list_attachments(result["email_id"])

    assert len(attachments) == 1
    assert attachments[0]["safe_filename"] == "brief.txt"
    assert attachments[0]["blocked"] == 0
    storage_path = settings.storage_root / attachments[0]["storage_path"]
    assert storage_path.exists()
    extracted_path = settings.storage_root / attachments[0]["extracted_text_path"]
    assert "Homepage copy" in extracted_path.read_text(encoding="utf-8")


def test_blocked_attachment_is_recorded_but_not_saved(tmp_path: Path) -> None:
    settings = make_settings(tmp_path)
    settings.ensure_storage_dirs()
    db = Database(settings.db_path)
    db.init()
    service = IngestService(settings, db)
    raw = Path("tests/fixtures/malicious_attachment.eml").read_bytes()

    result = service.ingest_raw(raw, envelope_from="client@example.com", envelope_to="bot@alexpitcher.co.uk")
    attachment = db.list_attachments(result["email_id"])[0]

    assert attachment["blocked"] == 1
    assert attachment["block_reason"] == "blocked_extension"
    assert "blocked_attachments" in attachment["storage_path"]
    assert (settings.storage_root / attachment["storage_path"]).exists()
    assert not (settings.storage_root / "emails" / result["email_id"] / "attachments" / "run-me.sh").exists()
