#!/usr/bin/env python3
"""p1 — one-shot CLI to create a Pillar 1 prayer reel.

End-to-end: extracts an IG reel as background → uploads to R2 →
writes config + caption → commits + merges to main → optionally
renders and publishes to @livepraybible.

Examples
--------
  # Stage only (write & commit, hold off on publishing):
  scripts/p1 https://www.instagram.com/reel/XXX/ p1_hope \\
      --prayer-file prayer.txt --caption-file caption.txt

  # Full loop — render in CI + publish + wait for the permalink:
  scripts/p1 https://www.instagram.com/reel/XXX/ p1_hope \\
      -p prayer.txt -c caption.txt --publish --watch

  # Inline prayer + caption, darken=0 (preserve colors):
  scripts/p1 URL p1_quiet --prayer "Lord, in the quiet, You are here." \\
      --caption "Lord, in the quiet... #prayer" --darken 0 --publish --watch

  # Just stage on local disk — no git, no R2 upload, no PR:
  scripts/p1 URL p1_test -p prayer.txt -c caption.txt --dry-run

Inputs
------
URL              Instagram reel URL
SLUG             config slug (e.g., p1_hope) — also used as filename stem

  -p / --prayer-file PATH     prayer text from file (use - for stdin)
       --prayer TEXT          prayer text inline (\\n for newlines)
  -c / --caption-file PATH    caption text from file (use - for stdin)
       --caption TEXT         caption text inline

Flags
-----
  --bg-filename NAME    R2 filename under "background videos/" (default: <slug>_bg.mp4)
  --darken FLOAT        drawbox darken overlay (default: 0 — preserve colors)
  --duration INT        target reel duration (default: 14)
  --font {serif,sans}   text font (default: serif)
  --size INT            text size (default: 68)
  --no-music            disable music folder rotation
  --dry-run             write files locally; skip R2, git, and PR
  --publish             trigger Render + publish workflow after merge
  --watch               wait for the workflow and print the IG permalink

Environment
-----------
Reads R2 credentials from .env at the repo root:
  R2_ACCESS_KEY_ID  R2_SECRET_ACCESS_KEY  R2_ENDPOINT
  R2_BUCKET         R2_PUBLIC_BASE_URL
Requires `yt-dlp`, `rclone`, `gh`, and `ffprobe` on PATH.
"""
from __future__ import annotations

import argparse
import os
import re
import shutil
import subprocess
import sys
import time
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
TMP_DIR   = Path("/tmp/p1_extract")
BRANCH    = None  # filled in by main()


# ── helpers ──────────────────────────────────────────────────────────
def run(cmd, *, env=None, capture=False, check=True, quiet=False) -> subprocess.CompletedProcess:
    """Run a subprocess from REPO_ROOT, with friendly logging."""
    if isinstance(cmd, str):
        cmd_list = cmd.split()
    else:
        cmd_list = [str(c) for c in cmd]
    if not quiet:
        print(f"$ {' '.join(cmd_list)}", file=sys.stderr)
    return subprocess.run(
        cmd_list, env=env, check=check,
        capture_output=capture, text=True, cwd=REPO_ROOT,
    )


def out(cmd, *, env=None, quiet=True) -> str:
    """Capture stdout from a subprocess."""
    return run(cmd, env=env, capture=True, quiet=quiet).stdout.strip()


def read_text(spec: str | None) -> str | None:
    """Read text from inline value, file path, or `-` (stdin)."""
    if spec is None:
        return None
    if spec == "-":
        return sys.stdin.read()
    p = Path(spec)
    if p.exists() and p.is_file():
        return p.read_text()
    # treat as inline text
    return spec.replace("\\n", "\n")


def load_dotenv(path: Path) -> dict:
    """Tiny .env loader — KEY=VALUE pairs, ignores comments and blanks."""
    env = os.environ.copy()
    if not path.exists():
        return env
    for line in path.read_text().splitlines():
        s = line.strip()
        if not s or s.startswith("#") or "=" not in s:
            continue
        k, v = s.split("=", 1)
        env[k] = v
    return env


def detect_branch() -> str:
    return out(["git", "branch", "--show-current"])


# ── steps ────────────────────────────────────────────────────────────
def step_extract(url: str) -> Path:
    """yt-dlp the reel into TMP_DIR and return the .mp4 path."""
    TMP_DIR.mkdir(parents=True, exist_ok=True)
    for f in TMP_DIR.glob("*"):
        f.unlink()
    print("[1/6] Extracting reel via yt-dlp")
    run([
        "yt-dlp", "--no-warnings", "--no-playlist",
        "--merge-output-format", "mp4",
        "-o", str(TMP_DIR / "igreel.%(ext)s"),
        "--write-info-json", url,
    ])
    mp4 = TMP_DIR / "igreel.mp4"
    if not mp4.exists():
        # yt-dlp may produce .webm if merge failed
        candidates = [
            p for p in TMP_DIR.glob("igreel.*")
            if p.suffix.lower() in (".mp4", ".webm", ".mkv")
        ]
        if not candidates:
            sys.exit("✗ yt-dlp produced no video file")
        mp4 = candidates[0]
    dur = out([
        "ffprobe", "-v", "error",
        "-show_entries", "format=duration",
        "-of", "default=noprint_wrappers=1:nokey=1",
        str(mp4),
    ])
    print(f"      duration: {float(dur):.2f}s   file: {mp4.name}")
    return mp4


def step_upload_to_r2(src: Path, bg_name: str, env: dict) -> str:
    """Stage clip locally and upload to R2 'background videos/<bg_name>'."""
    print("[2/6] Staging + uploading background to R2")
    bg_local = REPO_ROOT / "assets" / "backgrounds" / bg_name
    bg_local.parent.mkdir(parents=True, exist_ok=True)
    shutil.copy(src, bg_local)
    print(f"      local: {bg_local.relative_to(REPO_ROOT)}")

    rclone_env = dict(env,
        RCLONE_CONFIG_R2UP_TYPE="s3",
        RCLONE_CONFIG_R2UP_PROVIDER="Cloudflare",
        RCLONE_CONFIG_R2UP_ACCESS_KEY_ID=env["R2_ACCESS_KEY_ID"],
        RCLONE_CONFIG_R2UP_SECRET_ACCESS_KEY=env["R2_SECRET_ACCESS_KEY"],
        RCLONE_CONFIG_R2UP_ENDPOINT=env["R2_ENDPOINT"],
    )
    bucket = env["R2_BUCKET"]
    run([
        "rclone", "copyto",
        "--s3-no-check-bucket", "--no-update-modtime", "--s3-no-head",
        str(bg_local), f"r2up:{bucket}/background videos/{bg_name}",
    ], env=rclone_env)
    public_url = f"{env['R2_PUBLIC_BASE_URL'].rstrip('/')}/background%20videos/{bg_name}"
    print(f"      R2 URL: {public_url}")
    return public_url


def step_write_files(args, r2_url: str, prayer: str, caption: str) -> tuple[Path, Path]:
    """Write configs/<slug>.yaml and scripts/captions/<slug>.txt."""
    print("[3/6] Writing config + caption")
    cfg_path = REPO_ROOT / "configs" / f"{args.slug}.yaml"
    cap_path = REPO_ROOT / "scripts" / "captions" / f"{args.slug}.txt"
    cap_path.parent.mkdir(parents=True, exist_ok=True)

    prayer_indented = "\n".join(
        "    " + line for line in prayer.rstrip("\n").splitlines()
    )

    music_block = (
        "" if args.no_music else
        '\nmusic:\n'
        '  folder: "/Users/tom-new/livepraybiblenow/.claude/worktrees/'
        'unruffled-swanson-24a9fd/background music"\n'
        '  volume: 0.18\n'
    )

    cfg_path.write_text(f"""# Pillar 1 reel — generated by scripts/p1.py
# Source: {args.url}
slug: {args.slug}

background:
  type: video
  video: {r2_url}

duration: {args.duration}
darken: {args.darken}

text:
  font: {args.font}
  size: {args.size}
  align: center
  position: center
  color: white
  lines: |
{prayer_indented}

cta:
  text: "Follow @livepraybible"
  duration: 1.6
  fade: 0.4
{music_block}
output_dir: output/{args.slug}
""")
    cap_path.write_text(caption.rstrip() + "\n")
    print(f"      {cfg_path.relative_to(REPO_ROOT)}")
    print(f"      {cap_path.relative_to(REPO_ROOT)}")
    return cfg_path, cap_path


def step_commit_merge(cfg_path: Path, cap_path: Path, slug: str) -> None:
    """git add + commit + push (with retry) + PR + merge to main."""
    global BRANCH
    print("[4/6] Commit + push + PR + merge")
    run(["git", "add", str(cfg_path), str(cap_path)])
    run(["git", "commit", "-m", f"Add {slug} (scripts/p1.py)"])

    # push with retry — the connection occasionally resets
    for i in range(1, 4):
        try:
            run(["git", "push"])
            local = out(["git", "rev-parse", "HEAD"])
            remote_line = out(["git", "ls-remote", "origin", BRANCH])
            remote = remote_line.split("\t")[0] if remote_line else ""
            if local == remote:
                if i > 1:
                    print(f"      push synced on attempt {i}")
                break
        except subprocess.CalledProcessError:
            pass
        if i < 3:
            print(f"      push attempt {i} failed — retrying in 2s")
            time.sleep(2)
    else:
        sys.exit("✗ git push failed after 3 attempts")

    # gh pr create — may fail if PR already exists; that's ok
    try:
        run([
            "gh", "pr", "create", "--base", "main", "--head", BRANCH,
            "--title", f"Add {slug}",
            "--body", f"Auto-staged by scripts/p1.py (slug={slug}).",
        ])
    except subprocess.CalledProcessError:
        pass  # likely already open

    pr_num = out([
        "gh", "pr", "list",
        "--head", BRANCH, "--base", "main", "--state", "open",
        "--json", "number", "--jq", ".[0].number",
    ])
    if not pr_num:
        sys.exit("✗ couldn't find an open PR to main")
    print(f"      PR #{pr_num}")
    run(["gh", "pr", "merge", pr_num, "--merge", "--admin"])
    # quick sanity check that the new file is on main
    cfg_rel = cfg_path.relative_to(REPO_ROOT)
    try:
        out([
            "gh", "api",
            f"repos/genesismpl-bot/livepraybiblenow/contents/{cfg_rel}?ref=main",
            "-q", ".name",
        ])
        print(f"      ✓ {cfg_rel} confirmed on main")
    except subprocess.CalledProcessError:
        sys.exit(f"✗ {cfg_rel} not visible on main after merge")


def step_publish(slug: str) -> str:
    """Trigger Render + publish workflow. Returns run id."""
    print("[5/6] Triggering Render + publish workflow")
    run([
        "gh", "workflow", "run", "render_and_publish.yml", "--ref", "main",
        "-f", "pipeline=prayer_reel_pipeline.py",
        "-f", f"config=configs/{slug}.yaml",
        "-f", f"caption_file=scripts/captions/{slug}.txt",
    ])
    time.sleep(4)
    run_id = out([
        "gh", "run", "list",
        "--workflow=render_and_publish.yml", "--limit", "1",
        "--json", "databaseId", "-q", ".[0].databaseId",
    ])
    print(f"      run id: {run_id}")
    print(f"      url:    https://github.com/genesismpl-bot/livepraybiblenow/actions/runs/{run_id}")
    return run_id


def step_watch(run_id: str) -> None:
    """Block until the workflow finishes; print the IG permalink."""
    print("[6/6] Watching workflow")
    run(["gh", "run", "watch", run_id, "--exit-status", "--interval", "15"])
    log = out(["gh", "run", "view", run_id, "--log"])
    perma = next(
        (m.group(1) for m in re.finditer(r"PERMALINK=(https?://[^\s]+)", log)),
        None,
    )
    if perma:
        print(f"\n🎉 LIVE on Instagram: {perma}\n")
    else:
        print("\n(workflow finished — no PERMALINK captured; check the run log)\n")


# ── main ─────────────────────────────────────────────────────────────
def main() -> int:
    global BRANCH
    ap = argparse.ArgumentParser(
        prog="p1",
        description="Create a Pillar 1 prayer reel from an Instagram URL.",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=__doc__,
    )
    ap.add_argument("url", help="Instagram reel URL")
    ap.add_argument("slug", help="config slug (e.g., p1_hope)")
    ap.add_argument("--prayer", help="prayer text inline (\\n for newlines)")
    ap.add_argument("-p", "--prayer-file", dest="prayer_file",
                    help="prayer from file (or - for stdin)")
    ap.add_argument("--caption", help="caption text inline")
    ap.add_argument("-c", "--caption-file", dest="caption_file",
                    help="caption from file (or - for stdin)")
    ap.add_argument("--bg-filename", help="R2 filename (default: <slug>_bg.mp4)")
    ap.add_argument("--darken", type=float, default=0.0,
                    help="darken overlay (default 0 — preserve colors)")
    ap.add_argument("--duration", type=int, default=14)
    ap.add_argument("--font", choices=("serif", "sans"), default="serif")
    ap.add_argument("--size", type=int, default=68)
    ap.add_argument("--no-music", action="store_true")
    ap.add_argument("--dry-run", action="store_true",
                    help="write files locally; skip R2/git/PR")
    ap.add_argument("--publish", action="store_true",
                    help="trigger Render + publish workflow after merge")
    ap.add_argument("--watch", action="store_true",
                    help="wait for the workflow and print the IG permalink")
    args = ap.parse_args()

    prayer  = read_text(args.prayer_file) or args.prayer
    caption = read_text(args.caption_file) or args.caption
    if not prayer or not caption:
        sys.exit("✗ --prayer/--prayer-file AND --caption/--caption-file are required")

    if not args.slug.startswith("p1_"):
        print(f"⚠ slug doesn't start with 'p1_': {args.slug}", file=sys.stderr)

    bg_name = args.bg_filename or f"{args.slug.removeprefix('p1_')}_bg.mp4"
    if not bg_name.endswith(".mp4"):
        bg_name += ".mp4"

    env = load_dotenv(REPO_ROOT / ".env")
    BRANCH = detect_branch()
    print(f"  repo:   {REPO_ROOT}")
    print(f"  branch: {BRANCH}")
    print(f"  slug:   {args.slug}")
    print(f"  bg:     background videos/{bg_name}")
    print()

    src = step_extract(args.url)

    if args.dry_run:
        # Just write files locally; skip everything else
        print("[2/6] --dry-run: skipping R2 upload (using local path)")
        r2_url = f"file://{(REPO_ROOT / 'assets' / 'backgrounds' / bg_name).resolve()}"
    else:
        r2_url = step_upload_to_r2(src, bg_name, env)

    step_write_files(args, r2_url, prayer, caption)

    if args.dry_run:
        print("\n--dry-run: stopping after write. Files are staged but not committed.\n")
        return 0

    step_commit_merge(
        REPO_ROOT / "configs" / f"{args.slug}.yaml",
        REPO_ROOT / "scripts" / "captions" / f"{args.slug}.txt",
        args.slug,
    )

    if not args.publish:
        print("\n✓ Staged and merged to main. Re-run with --publish to post to IG.\n")
        return 0

    run_id = step_publish(args.slug)

    if args.watch:
        step_watch(run_id)

    return 0


if __name__ == "__main__":
    try:
        sys.exit(main())
    except KeyboardInterrupt:
        print("\n✗ interrupted", file=sys.stderr)
        sys.exit(130)
