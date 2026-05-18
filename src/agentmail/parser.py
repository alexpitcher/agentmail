from __future__ import annotations

import html2text
from bs4 import BeautifulSoup
from email import policy
from email.message import EmailMessage, Message
from email.parser import BytesParser
from email.utils import parsedate_to_datetime

from agentmail.models import ParsedAttachment, ParsedEmail


def _header(message: Message, key: str) -> str | None:
    value = message.get(key)
    return str(value) if value is not None else None


def _date_to_iso(value: str | None) -> str | None:
    if not value:
        return None
    try:
        dt = parsedate_to_datetime(value)
        if dt.tzinfo is None:
            return dt.isoformat()
        return dt.astimezone().isoformat()
    except Exception:
        return None


def _part_text(part: EmailMessage) -> str:
    try:
        content = part.get_content()
    except Exception:
        payload = part.get_payload(decode=True) or b""
        charset = part.get_content_charset() or "utf-8"
        return payload.decode(charset, errors="replace")
    return content if isinstance(content, str) else str(content)


def html_to_text(html: str) -> str:
    try:
        converter = html2text.HTML2Text()
        converter.body_width = 0
        converter.ignore_images = False
        converter.ignore_links = False
        return converter.handle(html).strip()
    except Exception:
        return BeautifulSoup(html, "html.parser").get_text("\n").strip()


def _extract_body_from_part(msg: Message) -> tuple[str, str]:
    """Return (body_text, body_html) from a message or message part."""
    text_body = msg.get_body(preferencelist=("plain",))
    html_body = msg.get_body(preferencelist=("html",))
    body_text = _part_text(text_body).strip() if text_body is not None else ""
    body_html = _part_text(html_body).strip() if html_body is not None else ""
    if not body_text and body_html:
        body_text = html_to_text(body_html)
    return body_text, body_html


def parse_email(raw_bytes: bytes) -> ParsedEmail:
    message = BytesParser(policy=policy.default).parsebytes(raw_bytes)

    body_text, body_html = _extract_body_from_part(message)

    # Fallback: some forwarded emails (e.g. Gmail forwarding HTML marketing emails)
    # embed the original as a message/rfc822 part. get_body() does not descend into
    # those, so try each one until we find content.
    if not body_text and not body_html:
        for part in message.walk():
            if part.get_content_type() != "message/rfc822":
                continue
            payload = part.get_payload()
            inner = payload[0] if isinstance(payload, list) else payload
            if not hasattr(inner, "get_body"):
                continue
            body_text, body_html = _extract_body_from_part(inner)
            if body_text or body_html:
                break

    attachments: list[ParsedAttachment] = []
    for part in message.walk():
        if part.is_multipart():
            continue
        disposition = (part.get_content_disposition() or "").lower()
        filename = part.get_filename()
        content_id = _header(part, "Content-ID")
        is_body_part = part is text_body or part is html_body
        is_attachment = disposition == "attachment" or bool(filename)
        is_inline_attachment = disposition == "inline" and (bool(filename) or bool(content_id))
        if is_body_part or not (is_attachment or is_inline_attachment):
            continue
        payload = part.get_payload(decode=True)
        if payload is None:
            content = part.get_content()
            payload = content.encode("utf-8", errors="replace") if isinstance(content, str) else b""
        attachments.append(
            ParsedAttachment(
                filename=filename or f"inline-{len(attachments) + 1}",
                content_type=part.get_content_type(),
                payload=payload,
                is_inline=disposition == "inline",
                content_id=content_id,
            )
        )

    return ParsedEmail(
        header_from=_header(message, "From"),
        header_to=_header(message, "To"),
        header_cc=_header(message, "Cc"),
        header_bcc=_header(message, "Bcc"),
        reply_to=_header(message, "Reply-To"),
        subject=_header(message, "Subject"),
        message_id=_header(message, "Message-ID"),
        in_reply_to=_header(message, "In-Reply-To"),
        references_header=_header(message, "References"),
        received_at=_date_to_iso(_header(message, "Date")),
        body_text=body_text,
        body_html=body_html,
        attachments=attachments,
    )
