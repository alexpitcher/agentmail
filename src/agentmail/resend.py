from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Iterable

import httpx

from agentmail.config import Settings
from agentmail.ingest import IngestService


class ResendSyncError(RuntimeError):
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
                    detail = active_client.get_received(email_id)
                    raw = detail.get("raw") or {}
                    download_url = raw.get("download_url")
                    if not download_url:
                        result.skipped += 1
                        result.errors.append(f"{email_id}: raw download URL is unavailable")
                        continue
                    raw_bytes = active_client.download_raw(download_url)
                    ingest = ingest_service.ingest_raw(
                        raw_bytes,
                        provider="resend-receiving",
                        envelope_from=detail.get("from") or item.get("from"),
                        envelope_to=_best_envelope_to(list(detail.get("to") or to_addresses), effective_target),
                        provider_message_id=email_id,
                        actor="resend-sync",
                        dedupe_message_id=True,
                    )
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
