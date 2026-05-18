from __future__ import annotations

from dataclasses import dataclass


@dataclass
class ParsedAttachment:
    filename: str
    content_type: str | None
    payload: bytes
    is_inline: bool = False
    content_id: str | None = None


@dataclass
class ParsedEmail:
    header_from: str | None
    header_to: str | None
    header_cc: str | None
    header_bcc: str | None
    reply_to: str | None
    subject: str | None
    message_id: str | None
    in_reply_to: str | None
    references_header: str | None
    received_at: str | None
    body_text: str
    body_html: str
    attachments: list[ParsedAttachment]
