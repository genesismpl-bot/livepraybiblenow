#!/usr/bin/env python3
"""Post a Reel to Instagram via the Graph API.

Usage:
  python post_to_instagram.py --video-url <PUBLIC_MP4_URL> --caption "<text>"

Env:
  IG_USER_ID                  Instagram Business/Creator account ID
  INSTAGRAM_LONG_LIVED_TOKEN  long-lived access token with scopes
                              instagram_basic + instagram_content_publish

Flow (Reels):
  1. POST /{ig-user-id}/media        → container_id
  2. Poll GET /{container-id}?fields=status_code  until FINISHED
  3. POST /{ig-user-id}/media_publish?creation_id=<container_id>
  4. GET  /{media-id}?fields=permalink  for the public IG URL
"""
from __future__ import annotations

import argparse
import os
import sys
import time

import requests

GRAPH = "https://graph.facebook.com/v21.0"


def post_reel(video_url: str, caption: str = "", share_to_feed: bool = True) -> str:
    ig_user = os.environ["IG_USER_ID"]
    token = os.environ["INSTAGRAM_LONG_LIVED_TOKEN"]

    # 1. Create container
    print(f"[1/4] Creating media container", flush=True)
    print(f"      video_url: {video_url}", flush=True)
    r = requests.post(
        f"{GRAPH}/{ig_user}/media",
        data={
            "media_type": "REELS",
            "video_url": video_url,
            "caption": caption,
            "share_to_feed": "true" if share_to_feed else "false",
            "access_token": token,
        },
        timeout=60,
    )
    if not r.ok:
        sys.exit(f"create-container failed: {r.status_code} {r.text}")
    container_id = r.json()["id"]
    print(f"      container_id: {container_id}", flush=True)

    # 2. Poll status_code until FINISHED
    print(f"[2/4] Polling container status (IG processes the video)…", flush=True)
    deadline = time.time() + 600  # 10 min cap
    last_code = None
    while time.time() < deadline:
        s = requests.get(
            f"{GRAPH}/{container_id}",
            params={"fields": "status_code,status", "access_token": token},
            timeout=30,
        )
        if not s.ok:
            sys.exit(f"poll failed: {s.status_code} {s.text}")
        data = s.json()
        code = data.get("status_code")
        if code != last_code:
            print(f"      status: {code} {data.get('status','')}", flush=True)
            last_code = code
        if code == "FINISHED":
            break
        if code in ("ERROR", "EXPIRED"):
            sys.exit(f"container failed: {data}")
        time.sleep(10)
    else:
        sys.exit("container polling timed out after 10 minutes")

    # 3. Publish
    print("[3/4] Publishing…", flush=True)
    p = requests.post(
        f"{GRAPH}/{ig_user}/media_publish",
        data={"creation_id": container_id, "access_token": token},
        timeout=60,
    )
    if not p.ok:
        sys.exit(f"publish failed: {p.status_code} {p.text}")
    media_id = p.json()["id"]
    print(f"      media_id: {media_id}", flush=True)

    # 4. Fetch permalink
    print("[4/4] Fetching permalink…", flush=True)
    m = requests.get(
        f"{GRAPH}/{media_id}",
        params={"fields": "permalink", "access_token": token},
        timeout=30,
    )
    permalink = ""
    if m.ok:
        permalink = m.json().get("permalink", "")
    print(f"\n✓ POSTED → {permalink or '(permalink not available yet)'}", flush=True)
    if permalink:
        # Machine-readable line for CI to capture
        print(f"PERMALINK={permalink}", flush=True)
    return permalink


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--video-url", required=True)
    ap.add_argument("--caption", default="")
    ap.add_argument("--no-share-to-feed", action="store_true")
    args = ap.parse_args()
    post_reel(args.video_url, args.caption, share_to_feed=not args.no_share_to_feed)
    return 0


if __name__ == "__main__":
    sys.exit(main())
