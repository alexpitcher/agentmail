from __future__ import annotations

import json
import sqlite3
from pathlib import Path
from typing import Any


EMAIL_COLUMNS = [
    "id",
    "provider",
    "provider_message_id",
    "raw_sha256",
    "envelope_from",
    "envelope_to",
    "header_from",
    "header_to",
    "header_cc",
    "header_bcc",
    "reply_to",
    "subject",
    "message_id",
    "in_reply_to",
    "references_header",
    "received_at",
    "ingested_at",
    "plus_tag",
    "route_local_part",
    "route_domain",
    "body_text_path",
    "body_html_path",
    "raw_path",
    "manifest_path",
    "has_attachments",
    "attachment_count",
    "trusted_sender",
    "quarantined",
    "quarantine_reason",
    "notes",
]

ATTACHMENT_COLUMNS = [
    "id",
    "email_id",
    "filename",
    "safe_filename",
    "content_type",
    "detected_type",
    "size_bytes",
    "sha256",
    "storage_path",
    "extracted_text_path",
    "is_inline",
    "content_id",
    "blocked",
    "block_reason",
    "created_at",
]


class Database:
    def __init__(self, path: Path):
        self.path = path
        self.path.parent.mkdir(parents=True, exist_ok=True)

    def connect(self) -> sqlite3.Connection:
        connection = sqlite3.connect(self.path)
        connection.row_factory = sqlite3.Row
        connection.execute("PRAGMA foreign_keys = ON")
        return connection

    def init(self) -> None:
        with self.connect() as db:
            db.executescript(
                """
                CREATE TABLE IF NOT EXISTS emails (
                    id TEXT PRIMARY KEY,
                    provider TEXT NOT NULL,
                    provider_message_id TEXT,
                    raw_sha256 TEXT NOT NULL UNIQUE,
                    envelope_from TEXT,
                    envelope_to TEXT,
                    header_from TEXT,
                    header_to TEXT,
                    header_cc TEXT,
                    header_bcc TEXT,
                    reply_to TEXT,
                    subject TEXT,
                    message_id TEXT,
                    in_reply_to TEXT,
                    references_header TEXT,
                    received_at TEXT,
                    ingested_at TEXT NOT NULL,
                    plus_tag TEXT,
                    route_local_part TEXT,
                    route_domain TEXT,
                    body_text_path TEXT,
                    body_html_path TEXT,
                    raw_path TEXT,
                    manifest_path TEXT,
                    has_attachments INTEGER NOT NULL DEFAULT 0,
                    attachment_count INTEGER NOT NULL DEFAULT 0,
                    trusted_sender INTEGER NOT NULL DEFAULT 0,
                    quarantined INTEGER NOT NULL DEFAULT 0,
                    quarantine_reason TEXT,
                    notes TEXT
                );

                CREATE TABLE IF NOT EXISTS attachments (
                    id TEXT PRIMARY KEY,
                    email_id TEXT NOT NULL,
                    filename TEXT NOT NULL,
                    safe_filename TEXT NOT NULL,
                    content_type TEXT,
                    detected_type TEXT,
                    size_bytes INTEGER NOT NULL,
                    sha256 TEXT NOT NULL,
                    storage_path TEXT NOT NULL,
                    extracted_text_path TEXT,
                    is_inline INTEGER NOT NULL DEFAULT 0,
                    content_id TEXT,
                    blocked INTEGER NOT NULL DEFAULT 0,
                    block_reason TEXT,
                    created_at TEXT NOT NULL,
                    FOREIGN KEY(email_id) REFERENCES emails(id)
                );

                CREATE VIRTUAL TABLE IF NOT EXISTS email_fts USING fts5(
                    email_id,
                    subject,
                    header_from,
                    header_to,
                    body_text,
                    attachment_filenames,
                    extracted_attachment_text
                );

                CREATE TABLE IF NOT EXISTS audit_log (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    timestamp TEXT NOT NULL,
                    event_type TEXT NOT NULL,
                    email_id TEXT,
                    attachment_id TEXT,
                    actor TEXT,
                    detail_json TEXT
                );
                """
            )

    def find_email_by_sha(self, raw_sha256: str) -> sqlite3.Row | None:
        with self.connect() as db:
            return db.execute("SELECT * FROM emails WHERE raw_sha256 = ?", (raw_sha256,)).fetchone()

    def get_email(self, email_id: str) -> sqlite3.Row | None:
        with self.connect() as db:
            return db.execute("SELECT * FROM emails WHERE id = ?", (email_id,)).fetchone()

    def insert_email(self, values: dict[str, Any]) -> None:
        columns = [column for column in EMAIL_COLUMNS if column in values]
        placeholders = ", ".join("?" for _ in columns)
        sql = f"INSERT INTO emails ({', '.join(columns)}) VALUES ({placeholders})"
        with self.connect() as db:
            db.execute(sql, [values[column] for column in columns])

    def insert_attachment(self, values: dict[str, Any]) -> None:
        columns = [column for column in ATTACHMENT_COLUMNS if column in values]
        placeholders = ", ".join("?" for _ in columns)
        sql = f"INSERT INTO attachments ({', '.join(columns)}) VALUES ({placeholders})"
        with self.connect() as db:
            db.execute(sql, [values[column] for column in columns])

    def list_attachments(self, email_id: str) -> list[sqlite3.Row]:
        with self.connect() as db:
            return list(db.execute("SELECT * FROM attachments WHERE email_id = ? ORDER BY id", (email_id,)))

    def list_emails(
        self,
        *,
        limit: int = 20,
        offset: int = 0,
        has_attachments: bool | None = None,
        sender: str | None = None,
        to: str | None = None,
        tag: str | None = None,
        quarantined: bool = False,
        since: str | None = None,
        until: str | None = None,
    ) -> list[sqlite3.Row]:
        where = ["quarantined = ?"]
        params: list[Any] = [1 if quarantined else 0]
        if has_attachments is not None:
            where.append("has_attachments = ?")
            params.append(1 if has_attachments else 0)
        if sender:
            where.append("(envelope_from LIKE ? OR header_from LIKE ?)")
            params.extend([f"%{sender}%", f"%{sender}%"])
        if to:
            where.append("(envelope_to LIKE ? OR header_to LIKE ?)")
            params.extend([f"%{to}%", f"%{to}%"])
        if tag:
            where.append("plus_tag = ?")
            params.append(tag)
        if since:
            where.append("received_at >= ?")
            params.append(since)
        if until:
            where.append("received_at <= ?")
            params.append(until)
        params.extend([limit, offset])
        sql = f"""
            SELECT * FROM emails
            WHERE {' AND '.join(where)}
            ORDER BY COALESCE(received_at, ingested_at) DESC
            LIMIT ? OFFSET ?
        """
        with self.connect() as db:
            return list(db.execute(sql, params))

    def latest_email(self) -> sqlite3.Row | None:
        with self.connect() as db:
            return db.execute(
                """
                SELECT * FROM emails
                ORDER BY COALESCE(received_at, ingested_at) DESC
                LIMIT 1
                """
            ).fetchone()

    def count_emails_since(self, since: str) -> int:
        with self.connect() as db:
            row = db.execute(
                """
                SELECT COUNT(*) AS count
                FROM emails
                WHERE COALESCE(received_at, ingested_at) >= ?
                """,
                (since,),
            ).fetchone()
        return int(row["count"] if row else 0)

    def upsert_fts(self, values: dict[str, str]) -> None:
        with self.connect() as db:
            db.execute("DELETE FROM email_fts WHERE email_id = ?", (values["email_id"],))
            db.execute(
                """
                INSERT INTO email_fts (
                    email_id, subject, header_from, header_to, body_text,
                    attachment_filenames, extracted_attachment_text
                )
                VALUES (?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    values.get("email_id", ""),
                    values.get("subject", ""),
                    values.get("header_from", ""),
                    values.get("header_to", ""),
                    values.get("body_text", ""),
                    values.get("attachment_filenames", ""),
                    values.get("extracted_attachment_text", ""),
                ),
            )

    def search(
        self,
        fts_query: str,
        *,
        limit: int = 20,
        has_attachments: bool | None = None,
        tag: str | None = None,
    ) -> list[sqlite3.Row]:
        where = ["email_fts MATCH ?", "emails.quarantined = 0"]
        params: list[Any] = [fts_query]
        if has_attachments is not None:
            where.append("emails.has_attachments = ?")
            params.append(1 if has_attachments else 0)
        if tag:
            where.append("emails.plus_tag = ?")
            params.append(tag)
        params.append(limit)
        sql = f"""
            SELECT emails.*, bm25(email_fts) AS score,
                   snippet(email_fts, 4, '[', ']', '...', 24) AS snippet
            FROM email_fts
            JOIN emails ON emails.id = email_fts.email_id
            WHERE {' AND '.join(where)}
            ORDER BY score ASC, COALESCE(emails.received_at, emails.ingested_at) DESC
            LIMIT ?
        """
        with self.connect() as db:
            return list(db.execute(sql, params))

    def audit(
        self,
        timestamp: str,
        event_type: str,
        *,
        email_id: str | None = None,
        attachment_id: str | None = None,
        actor: str | None = None,
        detail: dict[str, Any] | None = None,
    ) -> None:
        with self.connect() as db:
            db.execute(
                """
                INSERT INTO audit_log (timestamp, event_type, email_id, attachment_id, actor, detail_json)
                VALUES (?, ?, ?, ?, ?, ?)
                """,
                (timestamp, event_type, email_id, attachment_id, actor, json.dumps(detail or {})),
            )
