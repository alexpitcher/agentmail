from __future__ import annotations

import re
from email.utils import parseaddr
from pathlib import Path


CONTROL_CHARS = re.compile(r"[\x00-\x1f\x7f]")
SAFE_FALLBACK = "attachment"


def bearer_token(authorization: str | None) -> str | None:
    if not authorization:
        return None
    scheme, _, token = authorization.partition(" ")
    if scheme.lower() != "bearer" or not token:
        return None
    return token.strip()


def sender_address(value: str | None) -> str:
    if not value:
        return ""
    _, address = parseaddr(value)
    return (address or value).strip().lower()


def is_allowed_sender(value: str | None, allowed_senders: list[str]) -> bool:
    if not allowed_senders:
        return True
    sender = sender_address(value)
    return sender in allowed_senders


def split_route(address: str | None) -> tuple[str | None, str | None, str | None]:
    if not address or "@" not in address:
        return None, None, None
    local, domain = address.rsplit("@", 1)
    route_local_part, plus_tag = (local.split("+", 1) + [None])[:2] if "+" in local else (local, None)
    return route_local_part or None, plus_tag or None, domain.lower() or None


def sanitize_filename(filename: str | None, fallback: str = SAFE_FALLBACK, max_len: int = 150) -> str:
    name = Path(filename or fallback).name
    name = name.replace("\\", "/").split("/")[-1]
    name = CONTROL_CHARS.sub("", name).strip().strip(".")
    if not name:
        name = fallback
    if len(name) <= max_len:
        return name
    stem = Path(name).stem
    suffix = Path(name).suffix
    keep = max_len - len(suffix)
    return f"{stem[:keep]}{suffix}" if keep > 1 else name[:max_len]


def dedupe_filename(name: str, used: set[str]) -> str:
    if name not in used:
        used.add(name)
        return name
    path = Path(name)
    stem = path.stem or "attachment"
    suffix = path.suffix
    for index in range(1, 1000):
        candidate = f"{stem}_{index:03d}{suffix}"
        if candidate not in used:
            used.add(candidate)
            return candidate
    raise ValueError(f"could not deduplicate filename: {name}")


def is_blocked_extension(filename: str, blocked_extensions: set[str]) -> bool:
    return Path(filename).suffix.lower() in blocked_extensions
