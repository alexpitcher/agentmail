from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import httpx
import typer
from rich.console import Console
from rich.table import Table

from agentmail.config import Settings, get_settings


app = typer.Typer(help="AgentMail email asset retrieval CLI.")
console = Console()


def _client() -> tuple[Settings, httpx.Client]:
    settings = get_settings()
    headers = {}
    if not settings.auth_disabled and settings.api_token:
        headers["Authorization"] = f"Bearer {settings.api_token}"
    return settings, httpx.Client(base_url=settings.api_url.rstrip("/"), headers=headers, timeout=30.0)


def _print_json(value: Any) -> None:
    console.print_json(json.dumps(value, sort_keys=True))


def _raise_for_status(response: httpx.Response) -> None:
    try:
        response.raise_for_status()
    except httpx.HTTPStatusError as exc:
        detail = exc.response.text
        raise typer.BadParameter(f"API request failed: {exc.response.status_code} {detail}") from exc


def _email_line(item: dict[str, Any]) -> str:
    stamp = (item.get("received_at") or "")[:16].replace("T", " ")
    attachments = f"{item.get('attachment_count', 0)} attachments" if item.get("has_attachments") else "no attachments"
    tag = item.get("plus_tag") or "-"
    return f"{item['id']} | {stamp} | {tag} | {item.get('from') or ''} | {item.get('subject') or ''} | {attachments}"


@app.command()
def health(json_output: bool = typer.Option(False, "--json", help="Print machine-readable JSON.")) -> None:
    """Check the AgentMail API."""
    settings, client = _client()
    with client:
        response = client.get("/health")
        _raise_for_status(response)
        data = response.json()
    if json_output:
        _print_json(data)
    else:
        console.print(f"AgentMail API OK: {settings.api_url}")


@app.command("list")
def list_emails(
    latest: int | None = typer.Option(None, "--latest", min=1),
    limit: int = typer.Option(20, "--limit", min=1),
    has_attachments: bool | None = typer.Option(None, "--has-attachments"),
    tag: str | None = typer.Option(None, "--tag"),
    quarantined: bool = typer.Option(False, "--quarantined"),
    json_output: bool = typer.Option(False, "--json"),
) -> None:
    """List ingested emails."""
    _, client = _client()
    params: dict[str, Any] = {"limit": limit, "quarantined": quarantined}
    if latest:
        params["latest"] = latest
    if has_attachments is not None:
        params["has_attachments"] = has_attachments
    if tag:
        params["tag"] = tag
    with client:
        response = client.get("/emails", params=params)
        _raise_for_status(response)
        data = response.json()
    if json_output:
        _print_json(data)
        return
    for item in data["items"]:
        console.print(_email_line(item))


@app.command()
def latest(
    has_attachments: bool | None = typer.Option(None, "--has-attachments"),
    json_output: bool = typer.Option(False, "--json"),
) -> None:
    """Show the newest non-quarantined email."""
    _, client = _client()
    params: dict[str, Any] = {"latest": 1}
    if has_attachments is not None:
        params["has_attachments"] = has_attachments
    with client:
        response = client.get("/emails", params=params)
        _raise_for_status(response)
        data = response.json()
    item = data["items"][0] if data["items"] else None
    if json_output:
        _print_json(item or {})
    elif item:
        console.print(_email_line(item))


@app.command()
def search(
    query: str = typer.Argument(...),
    latest: int = typer.Option(20, "--latest", min=1),
    has_attachments: bool | None = typer.Option(None, "--has-attachments"),
    tag: str | None = typer.Option(None, "--tag"),
    json_output: bool = typer.Option(False, "--json"),
) -> None:
    """Search emails using SQLite FTS."""
    _, client = _client()
    params: dict[str, Any] = {"q": query, "limit": latest}
    if has_attachments is not None:
        params["has_attachments"] = has_attachments
    if tag:
        params["tag"] = tag
    with client:
        response = client.get("/emails/search", params=params)
        _raise_for_status(response)
        data = response.json()
    if json_output:
        _print_json(data)
        return
    for item in data["items"]:
        console.print(_email_line(item))
        if item.get("snippet"):
            console.print(f"  {item['snippet']}")


@app.command()
def show(email_id: str, json_output: bool = typer.Option(False, "--json")) -> None:
    """Show one email."""
    _, client = _client()
    with client:
        response = client.get(f"/emails/{email_id}")
        _raise_for_status(response)
        data = response.json()
    if json_output:
        _print_json(data)
        return
    console.print(_email_line(data))
    if data.get("body_text"):
        console.print("\n" + data["body_text"][:2000])
    if data.get("attachments"):
        table = Table("ID", "Filename", "Type", "Size", "Blocked")
        for item in data["attachments"]:
            table.add_row(item["id"], item["filename"], item.get("content_type") or "", str(item["size_bytes"]), str(item["blocked"]))
        console.print(table)


@app.command()
def attachments(email_id: str, json_output: bool = typer.Option(False, "--json")) -> None:
    """List attachments for one email."""
    _, client = _client()
    with client:
        response = client.get(f"/emails/{email_id}/attachments")
        _raise_for_status(response)
        data = response.json()
    if json_output:
        _print_json(data)
        return
    table = Table("ID", "Filename", "Safe filename", "Type", "Size", "Blocked")
    for item in data["attachments"]:
        table.add_row(item["id"], item["filename"], item["safe_filename"], item.get("content_type") or "", str(item["size_bytes"]), str(item["blocked"]))
    console.print(table)


@app.command()
def pull(
    email_id: str,
    to: Path | None = typer.Option(None, "--to", file_okay=False, dir_okay=True),
    include_blocked: bool = typer.Option(False, "--include-blocked"),
    json_output: bool = typer.Option(False, "--json"),
) -> None:
    """Pull an email body and attachments into the current workspace."""
    destination = to or Path(".agentmail") / email_id
    _, client = _client()
    with client:
        response = client.post(
            f"/emails/{email_id}/pull",
            json={
                "destination": str(destination),
                "include_body": True,
                "include_attachments": True,
                "include_blocked": include_blocked,
            },
        )
        _raise_for_status(response)
        data = response.json()
    if json_output:
        _print_json(data)
        return
    console.print(f"Pulled {email_id} to {data['saved_to']}")
    for warning in data.get("warnings", []):
        console.print(f"Warning: {warning}", style="yellow")


@app.command()
def context(email_id: str) -> None:
    """Print compact agent context for one email."""
    _, client = _client()
    with client:
        response = client.get(f"/emails/{email_id}/context")
        _raise_for_status(response)
        console.print(response.text)


@app.command()
def open(email_id: str) -> None:
    """Print the server-side storage path for one email."""
    settings, client = _client()
    with client:
        response = client.get(f"/emails/{email_id}")
        _raise_for_status(response)
        data = response.json()
    console.print(str(settings.storage_root / "emails" / data["id"]))


@app.command()
def sync() -> None:
    """No-op for compatibility; Cloudflare pushes mail into AgentMail."""
    health(json_output=False)
    console.print("Sync complete: AgentMail uses push ingestion, so there is no mailbox to poll.")


@app.command()
def ingest_file(
    path: Path = typer.Argument(..., exists=True, dir_okay=False, readable=True),
    envelope_from: str | None = typer.Option(None, "--from"),
    envelope_to: str | None = typer.Option(None, "--to"),
    json_output: bool = typer.Option(False, "--json"),
) -> None:
    """Developer helper: post a local .eml file to the Cloudflare ingest endpoint."""
    settings = get_settings()
    headers = {
        "Authorization": f"Bearer {settings.ingest_token}",
        "Content-Type": "message/rfc822",
        "X-AgentMail-Provider": "local-file",
    }
    if envelope_from:
        headers["X-AgentMail-Envelope-From"] = envelope_from
    if envelope_to:
        headers["X-AgentMail-Envelope-To"] = envelope_to
    response = httpx.post(
        f"{settings.api_url.rstrip('/')}/ingest/cloudflare",
        headers=headers,
        content=path.read_bytes(),
        timeout=30.0,
    )
    _raise_for_status(response)
    data = response.json()
    if json_output:
        _print_json(data)
    else:
        console.print(f"{data['status']}: {data['email_id']}")


if __name__ == "__main__":
    app()
