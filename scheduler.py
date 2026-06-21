#!/usr/bin/env python3
"""Process the @livepraybiblelove Instagram post queue.

Reads `configs/post_queue.yaml`, finds rows whose `status: queued` and
`scheduled_at` is in the past, uploads the video to R2, posts to
Instagram, and writes the queue back with the updated status / media_id
/ error.

Designed to be safe under cron — idempotent, never re-posts a row whose
status isn't `queued`.

Run:
  python scheduler.py                # process all due posts
  python scheduler.py --dry-run      # do everything except media_publish
  python scheduler.py --once         # process at most one and exit
"""
from __future__ import annotations

import argparse
import datetime
import sys
import traceback
from pathlib import Path

import yaml
from dotenv import load_dotenv

ROOT = Path(__file__).resolve().parent
load_dotenv(ROOT / ".env")
sys.path.insert(0, str(ROOT))

from lib.shared import upload_to_r2  # noqa: E402
from lib import instagram             # noqa: E402

QUEUE_PATH = ROOT / "configs" / "post_queue.yaml"


def now_utc() -> datetime.datetime:
    return datetime.datetime.now(datetime.timezone.utc)


def parse_when(s: str) -> datetime.datetime:
    dt = datetime.datetime.fromisoformat(s)
    if dt.tzinfo is None:                              # treat naive as UTC
        dt = dt.replace(tzinfo=datetime.timezone.utc)
    return dt


def load_queue() -> dict:
    if not QUEUE_PATH.exists():
        return {"posts": []}
    return yaml.safe_load(QUEUE_PATH.read_text()) or {"posts": []}


def save_queue(data: dict) -> None:
    QUEUE_PATH.parent.mkdir(parents=True, exist_ok=True)
    QUEUE_PATH.write_text(
        yaml.safe_dump(data, sort_keys=False, allow_unicode=True, width=120)
    )


def process_one(post: dict, dry_run: bool) -> None:
    """Mutate `post` in place with the result of attempting to publish it.

    Prefers `video_url` (pre-uploaded to R2 — works in CI). Falls back to
    `video` (local path; only useful when running locally) — uploads to R2
    on the fly.
    """
    url = post.get("video_url")
    if url:
        print(f"  [1/3] using pre-uploaded R2 URL")
    else:
        vid_path = post.get("video")
        if not vid_path:
            post["status"] = "failed"
            post["error"]  = "neither video_url nor video set"
            return
        vid = ROOT / vid_path
        if not vid.exists():
            post["status"] = "failed"
            post["error"]  = f"video not found: {vid_path} (set video_url to skip local upload)"
            return
        key = f"reels/{(post.get('id') or vid.stem)}-{int(now_utc().timestamp())}.mp4"
        print(f"  [1/3] uploading to R2: {key}")
        url = upload_to_r2(str(vid), key)
        if not url or not url.startswith("http"):
            post["status"] = "failed"
            post["error"]  = f"R2 upload failed (returned: {url!r})"
            return
        post["video_url"] = url

    try:
        print(f"  [2/3] creating IG container")
        result = instagram.post_reel(url, post["caption"], dry_run=dry_run)
        post["container_id"] = result["container_id"]
        post["posted_at"]    = now_utc().isoformat()
        if result.get("published"):
            post["media_id"] = result["media_id"]
            post["status"]   = "posted"
            print(f"  [3/3] PUBLISHED ✓ media_id={post['media_id']}")
        else:
            post["status"] = "skipped"
            print(f"  [3/3] DRY RUN — not published (container {result['container_id']})")
    except Exception as e:
        post["status"] = "failed"
        post["error"]  = f"{type(e).__name__}: {e}"
        traceback.print_exc()


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--dry-run", action="store_true",
                    help="upload + create container but skip media_publish")
    ap.add_argument("--once", action="store_true",
                    help="process at most one due post then exit")
    args = ap.parse_args()

    data  = load_queue()
    posts = data.get("posts") or []
    now   = now_utc()

    due = []
    for p in posts:
        if p.get("status") != "queued":
            continue
        try:
            when = parse_when(p["scheduled_at"])
        except Exception:
            print(f"  skipping {p.get('id','?')}: bad scheduled_at")
            continue
        if when <= now:
            due.append(p)

    if not due:
        print("nothing due. exiting.")
        return

    print(f"processing {len(due)} due post(s) at {now.isoformat()}\n")
    processed = 0
    for p in due:
        print(f"--- {p.get('id', p.get('video'))} ---")
        process_one(p, dry_run=args.dry_run)
        save_queue(data)         # save after each so partial progress persists
        processed += 1
        if args.once:
            break

    print(f"\ndone. processed {processed} post(s).")


if __name__ == "__main__":
    main()
