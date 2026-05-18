from __future__ import annotations

import base64
import hashlib
import hmac
import json
import time
from dataclasses import replace
from pathlib import Path
from urllib.parse import parse_qs, urlparse

import httpx
from fastapi.testclient import TestClient

from agentmail.api import create_app
from agentmail.db import Database
from agentmail.ingest import IngestService
from agentmail.resend import ResendReceivingClient, sync_resend_received, verify_resend_webhook
from tests.test_ingest import make_settings


def make_mock_resend_client(raw: bytes, *, received_id: str = "resend-1") -> ResendReceivingClient:
    def handler(request: httpx.Request) -> httpx.Response:
        parsed = urlparse(str(request.url))
        if parsed.path == "/emails/receiving":
            assert parse_qs(parsed.query)["limit"] == ["100"]
            return httpx.Response(
                200,
                json={
                    "object": "list",
                    "has_more": False,
                    "data": [
                        {
                            "id": received_id,
                            "to": ["bot@bot.alexpitcher.co.uk"],
                            "from": "client@example.com",
                            "subject": "Website assets for homepage",
                        },
                        {
                            "id": "other-1",
                            "to": ["not-agentmail@bot.alexpitcher.co.uk"],
                            "from": "client@example.com",
                            "subject": "Ignore me",
                        },
                    ],
                },
            )
        if parsed.path == f"/emails/receiving/{received_id}":
            return httpx.Response(
                200,
                json={
                    "id": received_id,
                    "to": ["bot@bot.alexpitcher.co.uk"],
                    "from": "client@example.com",
                    "message_id": "<simple@example.com>",
                    "raw": {"download_url": "https://raw.resend.test/message.eml"},
                },
            )
        if str(request.url) == "https://raw.resend.test/message.eml":
            return httpx.Response(200, content=raw)
        return httpx.Response(404, json={"error": "not found"})

    http_client = httpx.Client(transport=httpx.MockTransport(handler))
    return ResendReceivingClient("test-key", base_url="https://api.resend.test", client=http_client)


def test_resend_sync_imports_target_inbox(tmp_path: Path) -> None:
    settings = replace(
        make_settings(tmp_path),
        resend_api_key="test-key",
        resend_api_url="https://api.resend.test",
        resend_sync_to="bot@bot.alexpitcher.co.uk",
    )
    settings.ensure_storage_dirs()
    db = Database(settings.db_path)
    db.init()
    service = IngestService(settings, db)
    raw = Path("tests/fixtures/simple.eml").read_bytes()
    client = make_mock_resend_client(raw)

    result = sync_resend_received(settings, service, client=client)

    assert result.as_dict() == {
        "scanned": 2,
        "matched": 1,
        "imported": 1,
        "duplicates": 0,
        "skipped": 1,
        "errors": [],
    }
    row = db.list_emails(limit=1)[0]
    assert row["provider"] == "resend-receiving"
    assert row["provider_message_id"] == "resend-1"
    assert row["envelope_to"] == "bot@bot.alexpitcher.co.uk"


def test_resend_sync_dedupes_existing_message_id(tmp_path: Path) -> None:
    settings = replace(
        make_settings(tmp_path),
        resend_api_key="test-key",
        resend_api_url="https://api.resend.test",
        resend_sync_to="bot@bot.alexpitcher.co.uk",
    )
    settings.ensure_storage_dirs()
    db = Database(settings.db_path)
    db.init()
    service = IngestService(settings, db)
    raw = Path("tests/fixtures/simple.eml").read_bytes()
    modified_resend_raw = raw.replace(b"Attached separately are", b"Forwarded copy has")

    created = service.ingest_raw(
        raw,
        provider="cloudflare-email-worker",
        envelope_from="client@example.com",
        envelope_to="bot@bot.alexpitcher.co.uk",
    )
    client = make_mock_resend_client(modified_resend_raw)

    result = sync_resend_received(settings, service, client=client)

    assert created["status"] == "created"
    assert result.imported == 0
    assert result.duplicates == 1
    assert db.list_emails(limit=10)[0]["id"] == created["email_id"]


def signed_resend_headers(body: bytes, secret: str) -> dict[str, str]:
    webhook_id = "msg_test"
    timestamp = str(int(time.time()))
    key = secret.split("_", 1)[1]
    key += "=" * (-len(key) % 4)
    digest = hmac.new(
        base64.b64decode(key),
        webhook_id.encode() + b"." + timestamp.encode() + b"." + body,
        hashlib.sha256,
    ).digest()
    return {
        "svix-id": webhook_id,
        "svix-timestamp": timestamp,
        "svix-signature": "v1," + base64.b64encode(digest).decode("ascii"),
    }


def test_verify_resend_webhook_signature() -> None:
    secret = "whsec_" + base64.b64encode(b"test-secret").decode("ascii").rstrip("=")
    body = b'{"type":"email.received","data":{"email_id":"resend-1"}}'

    payload = verify_resend_webhook(body, signed_resend_headers(body, secret), secret)

    assert payload["data"]["email_id"] == "resend-1"


def test_resend_webhook_endpoint_imports_received_email(tmp_path: Path, monkeypatch) -> None:
    secret = "whsec_" + base64.b64encode(b"test-secret").decode("ascii").rstrip("=")
    settings = replace(
        make_settings(tmp_path),
        resend_api_key="test-key",
        resend_api_url="https://api.resend.test",
        resend_sync_to="bot@bot.alexpitcher.co.uk",
        resend_webhook_secret=secret,
    )
    settings.ensure_storage_dirs()
    client = TestClient(create_app(settings))
    raw = Path("tests/fixtures/simple.eml").read_bytes()
    resend_client = make_mock_resend_client(raw)

    class FakeResendReceivingClient:
        def __init__(self, *args, **kwargs):
            self._client = resend_client

        def get_received(self, email_id: str):
            return self._client.get_received(email_id)

        def download_raw(self, download_url: str):
            return self._client.download_raw(download_url)

        def close(self):
            return None

    monkeypatch.setattr("agentmail.resend.ResendReceivingClient", FakeResendReceivingClient)
    body = json.dumps({"type": "email.received", "data": {"email_id": "resend-1"}}).encode()

    response = client.post("/ingest/resend", content=body, headers=signed_resend_headers(body, secret))

    assert response.status_code == 200
    assert response.json()["status"] == "created"
