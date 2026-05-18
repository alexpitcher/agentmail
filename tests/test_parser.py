from agentmail.parser import parse_email


def test_parse_email_normalizes_date_to_utc() -> None:
    raw = (
        b"From: a@example.com\n"
        b"To: b@example.com\n"
        b"Subject: Timezone\n"
        b"Date: Mon, 18 May 2026 14:55:33 +0100\n"
        b"Content-Type: text/plain; charset=utf-8\n"
        b"\n"
        b"hello\n"
    )

    parsed = parse_email(raw)

    assert parsed.received_at == "2026-05-18T13:55:33Z"
