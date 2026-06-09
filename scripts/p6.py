#!/usr/bin/env python3
"""p6 — one-shot CLI to create a Pillar 6 daily-verse reel.

P6 is the 15-second daily-verse anchor — see configs/p6_sample.yaml for
the format spec. Visual identity is locked (same background, same font,
same layout every day); only the verse + reference + application line
change.

Examples
--------
  # Single verse, stage only (no commit, no publish):
  scripts/p6.py shepherd \\
    --verse "The Lord is my shepherd; I shall not want." \\
    --ref   "Psalm 23:1" \\
    --apply "Walk into today with nothing to fear." \\
    --dry-run

  # Single verse, full loop — render in CI + publish + watch:
  scripts/p6.py shepherd \\
    -v "The Lord is my shepherd; I shall not want." \\
    -r "Psalm 23:1" \\
    -a "Walk into today with nothing to fear." \\
    --go

  # Batch — generate every entry in scripts/p6_verses.yaml (stage only):
  scripts/p6.py --batch

Inputs
------
SLUG               short key — becomes p6_<slug>.yaml
  -v / --verse     verse text (use \\n for line breaks on screen)
  -r / --ref       "Book Chap:Verse"  (em-dash is added automatically)
  -a / --apply     one-sentence application line
       --theme     hashtag-picker theme (default: faith)

Flags
-----
  --background URL   override the locked background video URL
  --duration INT     reel duration (default: 15)
  --darken FLOAT     drawbox darken overlay (default: 0.42)
  --batch            generate every entry in scripts/p6_verses.yaml
  --dry-run          stage files only; skip commit/push/publish
  --publish          trigger Render + publish workflow after merge
  --watch            wait for the workflow and print the IG permalink
  --go               shorthand for --publish --watch

Why P6 looks the way it does
----------------------------
- Save-first CTA ("Save • Follow"): IG weights saves > follows for
  cold-start distribution. P6 is designed to be the highest save-rate
  pillar in the lineup.
- Locked background: consistency is the brand. After 30 reels with the
  same opener frame, a viewer recognises the channel in 0.4s.
- No music block in the config: post via the Graph API and attach
  trending audio in-app — the audio graph is the distribution lever
  custom MP3s cannot give you.
"""
from __future__ import annotations

import argparse
import os
import re
import subprocess
import sys
import time
from pathlib import Path

import yaml

REPO_ROOT  = Path(__file__).resolve().parent.parent
VERSES_YML = REPO_ROOT / "scripts" / "p6_verses.yaml"
# P6 background rotation — 3 calm, branded-together clips. Pipeline picks
# one per reel via hash(slug) % 3, so the same slug always renders against
# the same clip and the 14 starter configs spread evenly across the set.
# Add/remove URLs here and re-run `scripts/p6.py --batch` to rebalance.
LOCKED_BG_ROTATION = [
    "https://pub-009ad35726e64a38930ccc6e3aff0a81.r2.dev/background%20videos/daily_verse_bg.mp4",
    "https://pub-009ad35726e64a38930ccc6e3aff0a81.r2.dev/background%20videos/daily_verse_bg_02.mp4",
    "https://pub-009ad35726e64a38930ccc6e3aff0a81.r2.dev/background%20videos/daily_verse_bg_03.mp4",
    "https://pub-009ad35726e64a38930ccc6e3aff0a81.r2.dev/background%20videos/daily_verse_bg_04.mp4",
]
LOCKED_BG = LOCKED_BG_ROTATION[0]  # back-compat for the single-arg path

# Theme → hashtag pack. Each pack is distinct so we don't ship the
# same 9 tags on every post (the spam-pattern signal that's been
# poisoning distribution).
THEME_TAGS = {
    "peace":       "#peace #stillness #godspeace #prayer #faith #scripture #dailyverse",
    "anxiety":     "#anxiety #calm #prayfear #christianreels #faith #scripture #dailyverse",
    "strength":    "#strength #endurance #godisstrong #faith #prayer #scripture #dailyverse",
    "hope":        "#hope #faithoverfear #godsplan #prayer #christianreels #scripture #dailyverse",
    "love":        "#godslove #youareloved #identity #faith #prayer #scripture #dailyverse",
    "faith":       "#faith #trustgod #believe #prayer #christianreels #scripture #dailyverse",
    "trust":       "#trustgod #leadme #faith #prayer #christianreels #scripture #dailyverse",
    "grace":       "#grace #newmercies #freshstart #faith #prayer #scripture #dailyverse",
    "rest":        "#rest #weary #cometojesus #faith #prayer #christianreels #dailyverse",
    "courage":     "#courage #purpose #godmadeyou #faith #prayer #scripture #dailyverse",
    "guidance":    "#guidance #leadme #godsplan #faith #prayer #christianreels #dailyverse",
    "praise":      "#praise #worship #thankyoulord #faith #prayer #scripture #dailyverse",
    "joy":         "#joy #godsjoy #fullness #faith #prayer #christianreels #dailyverse",
    "forgiveness": "#forgiveness #grace #mercy #faith #prayer #scripture #dailyverse",
}


def run(cmd, *, env=None, capture=False, check=True, quiet=False) -> subprocess.CompletedProcess:
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
    return run(cmd, env=env, capture=True, quiet=quiet).stdout.strip()


def detect_branch() -> str:
    return out(["git", "branch", "--show-current"])


# ── content builders ─────────────────────────────────────────────────
def build_lines(verse: str, ref: str, apply_line: str) -> str:
    """Build the four-block text body: tag / verse / reference / application."""
    verse  = verse.strip()
    ref    = ref.strip()
    apply_ = apply_line.strip()
    if not ref.startswith("—"):
        ref = f"— {ref}"
    return f"TODAY'S VERSE\n\n{verse}\n\n{ref}\n\n{apply_}\n"


def build_caption(verse: str, ref: str, apply_line: str, theme: str) -> str:
    """Auto-derive a P6 IG caption.

    Intentionally varies structure per theme so the captions don't all
    look the same to IG's spam classifier. Hook is the verse rendered
    in first-person; body is the reference + application; CTA leads
    with SAVE (per the saves > follows algorithmic argument).
    """
    verse_flat = " ".join(verse.split())
    ref_clean  = ref.lstrip("—").strip()
    tags = THEME_TAGS.get(theme.lower(), THEME_TAGS["faith"])
    # Vary the CTA per theme so the same closer doesn't ship twice in a row.
    cta_map = {
        "peace":   "Save this for the next time your mind won't quiet down.",
        "anxiety": "Save this for the next time it hits.",
        "strength":"Save this for the day the strength runs out.",
        "hope":    "Save this for the day hope feels thin.",
        "love":    "Save this for the day you forget who you are.",
        "faith":   "Save this for the day faith feels far.",
        "trust":   "Save this for the next step you can't see.",
        "grace":   "Save this for tomorrow morning.",
        "rest":    "Save this for the night you can't sleep.",
        "courage": "Save this for the moment you have to choose.",
    }
    save_cta = cta_map.get(theme.lower(), "Save this for when you need it.")
    return (
        f"\"{verse_flat}\" — {ref_clean}\n\n"
        f"{apply_line.strip()}\n\n"
        f"🔖 {save_cta}\n"
        f"🙏 Follow @livepraybible for today's verse, every morning.\n\n"
        f"{tags}\n"
    )


def write_config(slug: str, verse: str, ref: str, apply_line: str,
                 background, duration: int, darken: float,
                 rotation_index: int | None = None) -> Path:
    cfg_path = REPO_ROOT / "configs" / f"p6_{slug}.yaml"
    lines = build_lines(verse, ref, apply_line)
    indented = "\n".join("    " + ln for ln in lines.rstrip("\n").split("\n"))
    # background can be a single URL string OR a list (rotation set).
    # When list: pipeline picks via rotation_index if set, else hash(slug).
    if isinstance(background, list):
        bg_yaml = "\n".join(f"    - {url}" for url in background)
        bg_block = f"  video:\n{bg_yaml}\n"
        if rotation_index is not None:
            bg_block += f"  rotation_index: {rotation_index}\n"
    else:
        bg_block = f"  video: {background}\n"
    cfg_path.write_text(
        f"# Pillar 6 — \"Today's verse\" daily anchor — generated by scripts/p6.py\n"
        f"slug: p6_{slug}\n"
        f"\n"
        f"background:\n"
        f"  type: video\n"
        f"{bg_block}"
        f"\n"
        f"duration: {duration}\n"
        f"darken: {darken}\n"
        f"\n"
        f"text:\n"
        f"  font: serif\n"
        f"  size: 58\n"
        f"  align: center\n"
        f"  position: center\n"
        f"  color: white\n"
        f"  lines: |\n"
        f"{indented}\n"
        f"\n"
        f"cta:\n"
        f"  text: \"Save • Follow @livepraybible\"\n"
        f"  duration: 1.8\n"
        f"  fade: 0.5\n"
        f"\n"
        f"output_dir: output/p6_{slug}\n"
    )
    return cfg_path


def write_caption(slug: str, verse: str, ref: str, apply_line: str, theme: str) -> Path:
    cap_path = REPO_ROOT / "scripts" / "captions" / f"p6_{slug}.txt"
    cap_path.parent.mkdir(parents=True, exist_ok=True)
    cap_path.write_text(build_caption(verse, ref, apply_line, theme))
    return cap_path


# ── batch ───────────────────────────────────────────────────────────
def do_batch(background: str, duration: int, darken: float) -> int:
    if not VERSES_YML.exists():
        sys.exit(f"✗ {VERSES_YML.relative_to(REPO_ROOT)} not found")
    data = yaml.safe_load(VERSES_YML.read_text()) or {}
    entries = data.get("verses") or []
    if not entries:
        sys.exit(f"✗ no `verses:` entries in {VERSES_YML.name}")
    # Round-robin assignment per verses.yaml position. Guarantees
    # no two consecutive posts share a background, and distributes
    # 14 reels across N backgrounds as evenly as possible.
    is_list = isinstance(background, list)
    n_bg = len(background) if is_list else 1
    print(f"=== p6 batch: {len(entries)} configs ===\n")
    for i, e in enumerate(entries, 1):
        slug  = e["slug"]
        verse = e["verse"]
        ref   = e["ref"]
        apply_= e["apply"]
        theme = e.get("theme", "faith")
        rot   = (i - 1) % n_bg if is_list else None
        cfg   = write_config(slug, verse, ref, apply_, background, duration, darken,
                             rotation_index=rot)
        cap   = write_caption(slug, verse, ref, apply_, theme)
        bg_tag = f"  → bg {rot + 1}/{n_bg}" if rot is not None else ""
        print(f"  [{i:02d}/{len(entries)}] p6_{slug}{bg_tag}")
        print(f"        {cfg.relative_to(REPO_ROOT)}")
        print(f"        {cap.relative_to(REPO_ROOT)}")
    print(f"\n✓ wrote {len(entries)} configs + {len(entries)} captions")
    print("  Review them, then commit:")
    print("    git add configs/p6_*.yaml scripts/captions/p6_*.txt")
    print(f"    git commit -m 'Add P6 daily-verse starter set ({len(entries)} reels)'")
    return 0


# ── single-config publish loop ──────────────────────────────────────
def step_commit_merge(cfg_path: Path, cap_path: Path, slug: str, branch: str) -> None:
    print("[2/4] Commit + push + PR + merge")
    run(["git", "add", str(cfg_path), str(cap_path)])
    run(["git", "commit", "-m", f"Add p6_{slug} (scripts/p6.py)"])
    for i in range(1, 4):
        try:
            run(["git", "push"])
            local  = out(["git", "rev-parse", "HEAD"])
            remote = (out(["git", "ls-remote", "origin", branch]).split("\t") or [""])[0]
            if local == remote:
                break
        except subprocess.CalledProcessError:
            pass
        if i < 3:
            print(f"      push attempt {i} failed — retrying in 2s")
            time.sleep(2)
    else:
        sys.exit("✗ git push failed after 3 attempts")
    try:
        run([
            "gh", "pr", "create", "--base", "main", "--head", branch,
            "--title", f"Add p6_{slug}",
            "--body", f"Auto-staged by scripts/p6.py (slug=p6_{slug}).",
        ])
    except subprocess.CalledProcessError:
        pass
    pr_num = out([
        "gh", "pr", "list",
        "--head", branch, "--base", "main", "--state", "open",
        "--json", "number", "--jq", ".[0].number",
    ])
    if not pr_num:
        sys.exit("✗ couldn't find an open PR to main")
    print(f"      PR #{pr_num}")
    run(["gh", "pr", "merge", pr_num, "--merge", "--admin"])


def step_publish(slug: str) -> str:
    print("[3/4] Triggering Render + publish workflow")
    run([
        "gh", "workflow", "run", "render_and_publish.yml", "--ref", "main",
        "-f", "pipeline=prayer_reel_pipeline.py",
        "-f", f"config=configs/p6_{slug}.yaml",
        "-f", f"caption_file=scripts/captions/p6_{slug}.txt",
    ])
    time.sleep(4)
    run_id = out([
        "gh", "run", "list",
        "--workflow=render_and_publish.yml", "--limit", "1",
        "--json", "databaseId", "-q", ".[0].databaseId",
    ])
    print(f"      run id: {run_id}")
    return run_id


def step_watch(run_id: str) -> None:
    print("[4/4] Watching workflow")
    run(["gh", "run", "watch", run_id, "--exit-status", "--interval", "15"])
    log = out(["gh", "run", "view", run_id, "--log"])
    perma = next(
        (m.group(1) for m in re.finditer(r"PERMALINK=(https?://[^\s]+)", log)),
        None,
    )
    if perma:
        print(f"\n🎉 LIVE on Instagram: {perma}\n")
    else:
        print("\n(workflow finished — check the run log)\n")


# ── main ────────────────────────────────────────────────────────────
def main() -> int:
    ap = argparse.ArgumentParser(
        prog="p6",
        description="Create a Pillar 6 daily-verse reel.",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=__doc__,
    )
    ap.add_argument("slug", nargs="?",
                    help="config slug (e.g. shepherd → p6_shepherd.yaml)")
    ap.add_argument("-v", "--verse",   help="verse text on screen")
    ap.add_argument("-r", "--ref",     help="\"Book Chap:Verse\"")
    ap.add_argument("-a", "--apply",   dest="apply_line",
                    help="one-sentence application line")
    ap.add_argument("--theme", default="faith",
                    help=f"hashtag pack: {' / '.join(THEME_TAGS)}")
    ap.add_argument("--background", default=None,
                    help="override the locked P6 background (single URL — "
                         "skips the rotation set)")
    ap.add_argument("--duration", type=int, default=15)
    ap.add_argument("--darken",   type=float, default=0.42)
    ap.add_argument("--batch",    action="store_true",
                    help="generate every entry in scripts/p6_verses.yaml")
    ap.add_argument("--dry-run",  action="store_true",
                    help="stage files only; skip commit/publish")
    ap.add_argument("--publish",  action="store_true",
                    help="trigger Render + publish workflow after merge")
    ap.add_argument("--watch",    action="store_true",
                    help="wait for the workflow and print the IG permalink")
    ap.add_argument("--go",       action="store_true",
                    help="shorthand for --publish --watch")
    args = ap.parse_args()

    # Default to the locked rotation set unless --background was passed.
    bg = args.background if args.background else LOCKED_BG_ROTATION

    if args.batch:
        return do_batch(bg, args.duration, args.darken)

    if not args.slug:
        ap.error("slug is required (or pass --batch)")
    for field in ("verse", "ref", "apply_line"):
        if not getattr(args, field):
            ap.error(f"--{field.replace('_line','')} is required")

    if args.go:
        args.publish = True
        args.watch   = True

    slug = args.slug.removeprefix("p6_")
    print(f"[1/4] Writing config + caption for p6_{slug}")
    cfg = write_config(
        slug, args.verse, args.ref, args.apply_line,
        bg, args.duration, args.darken,
    )
    cap = write_caption(slug, args.verse, args.ref, args.apply_line, args.theme)
    print(f"      {cfg.relative_to(REPO_ROOT)}")
    print(f"      {cap.relative_to(REPO_ROOT)}")

    if args.dry_run:
        print("\n--dry-run: stopping after write.\n")
        return 0

    branch = detect_branch()
    step_commit_merge(cfg, cap, slug, branch)

    if not args.publish:
        print(f"\n✓ Staged + merged to main. Re-run with --publish to post.\n")
        return 0

    run_id = step_publish(slug)
    if args.watch:
        step_watch(run_id)
    return 0


if __name__ == "__main__":
    try:
        sys.exit(main())
    except KeyboardInterrupt:
        print("\n✗ interrupted", file=sys.stderr)
        sys.exit(130)
