from __future__ import annotations

import base64
import hashlib
import hmac
import json
import time
from dataclasses import dataclass, field
from typing import Any, Iterable

import httpx

from agentmail.config import Settings
from agentmail.ingest import IngestService


class ResendSyncError(RuntimeError):
    pass


class ResendWebhookError(RuntimeError):
    pass


@dataclass
class ResendSyncResult:
    scanned: int = 0
    matched: int = 0
    imported: int = 0
    duplicates: int = 0
    skipped: int = 0
    errors: list[str] = field(default_factory=list)

    def as_dict(self) -> dict[str, Any]:
        return {
            "scanned": self.scanned,
            "matched": self.matched,
            "imported": self.imported,
            "duplicates": self.duplicates,
            "skipped": self.skipped,
            "errors": self.errors,
        }


class ResendReceivingClient:
    def __init__(self, api_key: str, *, base_url: str = "https://api.resend.com", client: httpx.Client | None = None):
        self._own_client = client is None
        self.client = client or httpx.Client(timeout=30.0)
        self.base_url = base_url.rstrip("/")
        self.headers = {
            "Authorization": f"Bearer {api_key}",
            "Accept": "application/json",
        }

    def close(self) -> None:
        if self._own_client:
            self.client.close()

    def __enter__(self) -> "ResendReceivingClient":
        return self

    def __exit__(self, *exc_info: object) -> None:
        self.close()

    def list_received(self, *, limit: int = 100, after: str | None = None) -> dict[str, Any]:
        params: dict[str, Any] = {"limit": max(1, min(limit, 100))}
        if after:
            params["after"] = after
        response = self.client.get(f"{self.base_url}/emails/receiving", headers=self.headers, params=params)
        try:
            response.raise_for_status()
        except httpx.HTTPStatusError as exc:
            raise ResendSyncError(f"Resend list received emails failed: HTTP {response.status_code}") from exc
        return response.json()

    def get_received(self, email_id: str) -> dict[str, Any]:
        response = self.client.get(f"{self.base_url}/emails/receiving/{email_id}", headers=self.headers)
        try:
            response.raise_for_status()
        except httpx.HTTPStatusError as exc:
            raise ResendSyncError(f"Resend retrieve received email {email_id} failed: HTTP {response.status_code}") from exc
        return response.json()

    def download_raw(self, download_url: str) -> bytes:
        response = self.client.get(download_url)
        try:
            response.raise_for_status()
        except httpx.HTTPStatusError as exc:
            raise ResendSyncError(f"Resend raw email download failed: HTTP {response.status_code}") from exc
        return response.content


def _addresses_match(addresses: Iterable[str], expected: str | None) -> bool:
    if not expected:
        return True
    expected_lower = expected.casefold()
    return any(address.casefold() == expected_lower for address in addresses)


def _best_envelope_to(addresses: list[str], expected: str | None) -> str | None:
    if not addresses:
        return expected
    if expected:
        expected_lower = expected.casefold()
        for address in addresses:
            if address.casefold() == expected_lower:
                return address
    return addresses[0]


def _decode_svix_secret(secret: str) -> bytes:
    if secret.startswith("whsec_"):
        value = secret.split("_", 1)[1]
        value += "=" * (-len(value) % 4)
        return base64.b64decode(value)
    return secret.encode("utf-8")


def verify_resend_webhook(body: bytes, headers: dict[str, str], secret: str, *, tolerance_seconds: int = 300) -> dict[str, Any]:
    normalized = {key.lower(): value for key, value in headers.items()}
    webhook_id = normalized.get("svix-id") or normalized.get("webhook-id")
    timestamp = normalized.get("svix-timestamp") or normalized.get("webhook-timestamp")
    signature = normalized.get("svix-signature") or normalized.get("webhook-signature")
    if not webhook_id or not timestamp or not signature:
        raise ResendWebhookError("Missing Resend webhook signature headers")

    try:
        timestamp_int = int(timestamp)
    except ValueError as exc:
        raise ResendWebhookError("Invalid Resend webhook timestamp") from exc
    if abs(int(time.time()) - timestamp_int) > tolerance_seconds:
        raise ResendWebhookError("Resend webhook timestamp is outside the allowed tolerance")

    signed_content = webhook_id.encode("utf-8") + b"." + timestamp.encode("utf-8") + b"." + body
    expected = base64.b64encode(hmac.new(_decode_svix_secret(secret), signed_content, hashlib.sha256).digest()).decode("ascii")
    signatures = [part.split(",", 1)[1] for part in signature.split() if part.startswith("v1,")]
    if not any(hmac.compare_digest(expected, candidate) for candidate in signatures):
        raise ResendWebhookError("Invalid Resend webhook signature")

    try:
        return json.loads(body)
    except json.JSONDecodeError as exc:
        raise ResendWebhookError("Invalid Resend webhook JSON") from exc


def ingest_resend_received_email(
    settings: Settings,
    ingest_service: IngestService,
    email_id: str,
    *,
    client: ResendReceivingClient | None = None,
) -> dict[str, str]:
    if not settings.resend_api_key and client is None:
        raise ResendSyncError("AGENTMAIL_RESEND_API_KEY is not configured")

    existing = ingest_service.db.find_email_by_provider_message_id("resend-receiving", email_id)
    if existing:
        return {"status": "duplicate", "email_id": existing["id"], "raw_sha256": existing["raw_sha256"]}

    active_client = client or ResendReceivingClient(settings.resend_api_key or "", base_url=settings.resend_api_url)
    should_close = client is None
    try:
        detail = active_client.get_received(email_id)
        raw = detail.get("raw") or {}
        download_url = raw.get("download_url")
        if not download_url:
            raise ResendSyncError(f"{email_id}: raw download URL is unavailable")
        raw_bytes = active_client.download_raw(download_url)
        return ingest_service.ingest_raw(
            raw_bytes,
            provider="resend-receiving",
            envelope_from=detail.get("from"),
            envelope_to=_best_envelope_to(list(detail.get("to") or []), settings.resend_sync_to),
            provider_message_id=email_id,
            actor="resend-webhook",
            dedupe_message_id=True,
        )
    finally:
        if should_close:
            active_client.close()


def sync_resend_received(
    settings: Settings,
    ingest_service: IngestService,
    *,
    client: ResendReceivingClient | None = None,
    target_to: str | None = None,
    page_limit: int | None = None,
    max_pages: int | None = None,
) -> ResendSyncResult:
    if not settings.resend_api_key and client is None:
        raise ResendSyncError("AGENTMAIL_RESEND_API_KEY is not configured")

    effective_target = target_to if target_to is not None else settings.resend_sync_to
    effective_limit = page_limit if page_limit is not None else settings.resend_sync_page_limit
    effective_pages = max_pages if max_pages is not None else settings.resend_sync_max_pages
    result = ResendSyncResult()

    active_client = client or ResendReceivingClient(settings.resend_api_key or "", base_url=settings.resend_api_url)
    should_close = client is None
    try:
        after: str | None = None
        for _ in range(max(1, effective_pages)):
            page = active_client.list_received(limit=effective_limit, after=after)
            items = list(page.get("data") or [])
            if not items:
                break

            for item in items:
                result.scanned += 1
                email_id = item.get("id")
                to_addresses = list(item.get("to") or [])
                if not email_id:
                    result.skipped += 1
                    result.errors.append("Received email item is missing an id")
                    continue
                if not _addresses_match(to_addresses, effective_target):
                    result.skipped += 1
                    continue

                result.matched += 1
                existing = ingest_service.db.find_email_by_provider_message_id("resend-receiving", email_id)
                if existing:
                    result.duplicates += 1
                    continue

                try:
                    ingest = ingest_resend_received_email(settings, ingest_service, email_id, client=active_client)
                except Exception as exc:
                    result.errors.append(f"{email_id}: {exc}")
                    continue

                if ingest["status"] == "created":
                    result.imported += 1
                elif ingest["status"] == "duplicate":
                    result.duplicates += 1

            if not page.get("has_more"):
                break
            after = items[-1].get("id")
            if not after:
                break
    finally:
        if should_close:
            active_client.close()

    return result
