#!/usr/bin/env python3
"""Download emails from an AgentMail instance.

Usage:
    python get_email.py --api-key TOKEN [--url URL] [--latest N] [--search QUERY] [--id EMAIL_ID]

Arguments:
    --api-key   Required. AgentMail API token (AGENTMAIL_API_TOKEN).
    --url       Base URL of the AgentMail API. Default: http://localhost:8787
    --latest    Return the N most recent emails with full bodies (default action, N=2).
    --search    Full-text search query; returns matching email summaries.
    --id        Return full detail for a specific email ID.
    --limit     Number of results for --latest or list mode (default: 2).
    --json      Print raw JSON output (default: pretty-printed summary).
"""
from __future__ import annotations

import argparse
import json
import sys
import urllib.error
import urllib.parse
import urllib.request


def _req(url: str, api_key: str) -> dict:
    req = urllib.request.Request(url, headers={"Authorization": f"Bearer {api_key}"})
    try:
        with urllib.request.urlopen(req, timeout=15) as resp:
            return json.loads(resp.read())
    except urllib.error.HTTPError as exc:
        body = exc.read().decode(errors="replace")
        print(f"HTTP {exc.code}: {body}", file=sys.stderr)
        sys.exit(1)
    except urllib.error.URLError as exc:
        print(f"Connection error: {exc.reason}", file=sys.stderr)
        sys.exit(1)


def _print_email(item: dict, raw: bool) -> None:
    if raw:
        print(json.dumps(item, indent=2))
        return
    print(f"ID:      {item.get('id')}")
    print(f"Subject: {item.get('subject')}")
    print(f"From:    {item.get('from')}")
    print(f"To:      {item.get('to')}")
    print(f"Date:    {item.get('received_at')}")
    if "body_text" in item:
        body = (item.get("body_text") or "").strip()
        preview = body[:500] + ("…" if len(body) > 500 else "")
        print(f"Body:\n{preview}")
    elif "snippet" in item:
        print(f"Snippet: {item.get('snippet')}")
    print()


def main() -> None:
    parser = argparse.ArgumentParser(description="Retrieve emails from AgentMail.")
    parser.add_argument("--api-key", required=True, help="AgentMail API token")
    parser.add_argument("--url", default="http://localhost:8787", help="AgentMail base URL")
    parser.add_argument("--latest", type=int, metavar="N", help="Fetch N latest emails with full bodies")
    parser.add_argument("--search", metavar="QUERY", help="Full-text search")
    parser.add_argument("--id", metavar="EMAIL_ID", help="Fetch a specific email by ID")
    parser.add_argument("--limit", type=int, default=2, help="Result limit for list/latest mode")
    parser.add_argument("--json", dest="raw_json", action="store_true", help="Print raw JSON")
    args = parser.parse_args()

    base = args.url.rstrip("/")
    key = args.api_key

    if args.id:
        data = _req(f"{base}/emails/{args.id}", key)
        _print_email(data, args.raw_json)

    elif args.search:
        data = _req(f"{base}/emails/search?q={urllib.parse.quote(args.search)}&limit={args.limit}", key)
        items = data.get("items", [])
        if not items:
            print("No results.")
            return
        for item in items:
            _print_email(item, args.raw_json)

    else:
        n = args.latest if args.latest is not None else args.limit
        data = _req(f"{base}/email-bundle/latest?latest={n}", key)
        items = data.get("items", [])
        if not items:
            print("No emails found.")
            return
        for item in items:
            _print_email(item, args.raw_json)


if __name__ == "__main__":
    main()
