#!/usr/bin/env python3
"""Add a Reel to `configs/post_queue.yaml` — uploads to R2 first.

Workflow: render an MP4 locally → run this script → git push → wait for cron.

Usage:
  python scripts/queue_post.py output/p1_sample/final.mp4 \\
      --id p1_surrender_2026-05-27 \\
      --at 2026-05-27T07:00:00-04:00 \\
      --caption-file scripts/captions/p1_surrender.txt

  # or a single-line caption:
  python scripts/queue_post.py output/p2_kitchen_crying/final.mp4 \\
      --id p2_overwhelmed_2026-05-28 \\
      --at 2026-05-28T08:00:00-04:00 \\
      --caption "Come unto me, all ye that labour... #prayer #faith"
"""
from __future__ import annotations

import argparse
import datetime
import sys
from pathlib import Path

import yaml
from dotenv import load_dotenv

ROOT = Path(__file__).resolve().parent.parent
load_dotenv(ROOT / ".env")
sys.path.insert(0, str(ROOT))

from lib.shared import upload_to_r2  # noqa: E402

QUEUE_PATH = ROOT / "configs" / "post_queue.yaml"


def main() -> None:
    ap = argparse.ArgumentParser(formatter_class=argparse.RawDescriptionHelpFormatter,
                                 description=__doc__)
    ap.add_argument("video", help="path to local MP4 (e.g. output/p1_sample/final.mp4)")
    ap.add_argument("--id",  required=True, help="short stable id (used in R2 key)")
    ap.add_argument("--at",  required=True,
                    help="ISO 8601 schedule time WITH timezone, "
                         "e.g. 2026-05-27T07:00:00-04:00")
    grp = ap.add_mutually_exclusive_group(required=True)
    grp.add_argument("--caption",      help="caption text (single line)")
    grp.add_argument("--caption-file", type=Path,
                     help="path to a caption text file (preferred for multi-line)")
    ap.add_argument("--dry-run", action="store_true",
                    help="don't upload or write — just print what would happen")
    args = ap.parse_args()

    video = ROOT / args.video
    if not video.exists():
        sys.exit(f"video not found: {video}")

    caption = args.caption if args.caption else args.caption_file.read_text().rstrip()
    if len(caption) > 2200:
        sys.exit(f"caption too long: {len(caption)} chars (IG max 2200)")

    when = datetime.datetime.fromisoformat(args.at)
    if when.tzinfo is None:
        sys.exit("--at must include a timezone offset (e.g. -04:00)")
    print(f"scheduling at {when.isoformat()}")

    data = yaml.safe_load(QUEUE_PATH.read_text()) if QUEUE_PATH.exists() else {"posts": []}
    if data.get("posts") is None:
        data["posts"] = []
    if any(p.get("id") == args.id for p in data["posts"]):
        sys.exit(f"id '{args.id}' already in queue")

    if args.dry_run:
        print("DRY RUN — would upload + queue:")
        print(f"  id: {args.id}")
        print(f"  video: {args.video}")
        print(f"  scheduled_at: {args.at}")
        print(f"  caption ({len(caption)} chars): {caption[:120]!r}…")
        return

    key = f"reels/{args.id}.mp4"
    print(f"\nuploading {args.video} → R2 ({key})")
    url = upload_to_r2(str(video), key)
    if not url or not url.startswith("http"):
        sys.exit(f"R2 upload failed (returned: {url!r})")

    data["posts"].append({
        "id":           args.id,
        "video":        args.video,        # informational (local source path)
        "video_url":    url,                # what the scheduler actually uses
        "scheduled_at": args.at,
        "caption":      caption,
        "status":       "queued",
    })
    QUEUE_PATH.write_text(
        yaml.safe_dump(data, sort_keys=False, allow_unicode=True, width=120)
    )
    print(f"\nappended to {QUEUE_PATH}")
    print("\nnext: git add configs/post_queue.yaml && \\")
    print("      git commit -m 'schedule post' && git push")


if __name__ == "__main__":
    main()
