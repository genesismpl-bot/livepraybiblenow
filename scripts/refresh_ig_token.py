#!/usr/bin/env python3
"""Refresh the Instagram long-lived token (extends another 60 days).

Run periodically (e.g. monthly via cron) to keep the token fresh. Writes
the new token back to `.env` in place.

Note: the token must be at least 24 hours old before it can be refreshed
— Meta returns a clear error if you try too soon.

Usage:
  python scripts/refresh_ig_token.py
"""
from __future__ import annotations

import sys
from pathlib import Path

from dotenv import load_dotenv

ROOT = Path(__file__).resolve().parent.parent
load_dotenv(ROOT / ".env")
sys.path.insert(0, str(ROOT))

from lib.instagram import refresh_long_lived_token  # noqa: E402


def main() -> None:
    print("refreshing IG long-lived token…")
    result      = refresh_long_lived_token()
    new_token   = result["access_token"]
    expires_in  = int(result.get("expires_in", 0))
    expires_d   = expires_in // 86400
    print(f"got new token ({len(new_token)} chars, expires in ~{expires_d} days)")

    env_path = ROOT / ".env"
    lines    = env_path.read_text().splitlines(keepends=True)
    lines    = [l for l in lines if not l.startswith("INSTAGRAM_LONG_LIVED_TOKEN=")]
    lines.append(f"INSTAGRAM_LONG_LIVED_TOKEN={new_token}\n")
    env_path.write_text("".join(lines))
    print(f"updated {env_path}")


if __name__ == "__main__":
    main()
