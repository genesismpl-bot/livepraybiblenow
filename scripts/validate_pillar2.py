#!/usr/bin/env python3
"""Pre-publish gate for Pillar 2 (person-praying) reels.

Enforces four user-locked constraints before a Pillar 2 reel is posted:

  1. The scene must depict a LADY (woman/mother/girl/etc.).
  2. She must be CRYING AND PRAYING (added 2026-05-28).
  3. The scene_slot tag must be UNIQUE across all committed
     configs/p2_*.yaml files (and the scene prompt must not match any
     other p2 config verbatim).
  4. Music must ROTATE — the music.path must not equal the most recent
     posted Pillar 2 entry's music.path in configs/post_queue.yaml.

Usage:
  python scripts/validate_pillar2.py configs/p2_lady_kitchen_morning.yaml

Exit codes:
  0 — all checks pass; safe to publish
  1 — one or more checks failed (full error list printed)
  2 — bad input (missing file, malformed yaml, missing required fields)

Reference:
  Memory: feedback_pillar2_constraints.md
"""
from __future__ import annotations

import argparse
import re
import sys
from pathlib import Path

import yaml

ROOT = Path(__file__).resolve().parent.parent
CONFIGS_DIR = ROOT / "configs"
QUEUE_PATH = CONFIGS_DIR / "post_queue.yaml"

# Words that indicate the scene depicts a lady. Conservative list.
LADY_WORDS = [
    "lady", "woman", "women", "mother", "mom", "girl", "wife",
    "female", "she", "her", "daughter", "sister", "auntie", "aunt",
    "grandmother", "grandma",
]
LADY_PATTERN = re.compile(
    r"\b(" + "|".join(LADY_WORDS) + r")\b", re.IGNORECASE
)

# Words that indicate the scene depicts crying / tears.
CRY_WORDS = [
    "crying", "cries", "cried", "tear", "tears", "teardrop", "teardrops",
    "tearful", "weeping", "weeps", "wept", "sobbing", "sobs", "sobbed",
    "teary", "wet cheeks", "eyes wet", "tear-streaked", "tearstained",
]
CRY_PATTERN = re.compile(
    r"\b(" + "|".join(w.replace(" ", r"\s+") for w in CRY_WORDS) + r")\b",
    re.IGNORECASE,
)

# Words that indicate the scene depicts praying / prayer.
PRAY_WORDS = [
    "praying", "prays", "prayed", "prayer", "head bowed", "hands folded",
    "hands clasped", "hands cradling", "eyes closed", "in supplication",
    "kneeling in prayer",
]
PRAY_PATTERN = re.compile(
    r"\b(" + "|".join(w.replace(" ", r"\s+") for w in PRAY_WORDS) + r")\b",
    re.IGNORECASE,
)


def normalise_scene(text: str) -> str:
    """Collapse whitespace so two prompts that differ only by formatting
    are treated as the same."""
    return re.sub(r"\s+", " ", text).strip().lower()


def load_yaml(path: Path) -> dict:
    return yaml.safe_load(path.read_text()) or {}


def check_lady(cfg: dict) -> list[str]:
    scene = (cfg.get("background") or {}).get("scene", "")
    if not scene:
        return ["background.scene is empty — Pillar 2 needs a scene prompt"]
    if not LADY_PATTERN.search(scene):
        return [
            "scene does not mention a lady — Pillar 2 must depict a woman. "
            f"Tried: {LADY_WORDS}"
        ]
    return []


def check_crying_and_praying(cfg: dict) -> list[str]:
    """Pillar 2 lady must be both crying AND praying (locked 2026-05-28).
    Faces should be obscured — that's a soft recommendation, not enforced."""
    scene = (cfg.get("background") or {}).get("scene", "")
    errs: list[str] = []
    if not CRY_PATTERN.search(scene):
        errs.append(
            "scene does not depict crying — Pillar 2 lady must be crying "
            "AND praying. Add one of: " + ", ".join(CRY_WORDS[:8]) + " …"
        )
    if not PRAY_PATTERN.search(scene):
        errs.append(
            "scene does not depict praying — Pillar 2 lady must be crying "
            "AND praying. Add one of: " + ", ".join(PRAY_WORDS[:6]) + " …"
        )
    return errs


def check_unique_scene(cfg: dict, this_path: Path) -> list[str]:
    errs: list[str] = []
    this_slot = cfg.get("scene_slot")
    if not this_slot:
        errs.append(
            "missing required field `scene_slot:` — Pillar 2 configs must "
            "declare a short unique tag (e.g. `lady_kitchen_morning`)"
        )
    this_scene = normalise_scene((cfg.get("background") or {}).get("scene", ""))

    for sibling in sorted(CONFIGS_DIR.glob("p2_*.yaml")):
        if sibling.resolve() == this_path.resolve():
            continue
        try:
            other = load_yaml(sibling)
        except Exception as e:                                      # pragma: no cover
            errs.append(f"could not parse sibling {sibling.name}: {e}")
            continue
        other_slot = other.get("scene_slot")
        if this_slot and other_slot and other_slot == this_slot:
            errs.append(
                f"scene_slot `{this_slot}` already used by {sibling.name}"
            )
        other_scene = normalise_scene((other.get("background") or {}).get("scene", ""))
        if this_scene and other_scene and this_scene == other_scene:
            errs.append(
                f"background.scene text matches {sibling.name} verbatim — "
                "reword for visual variety"
            )
    return errs


def check_music_rotation(cfg: dict) -> list[str]:
    this_music = (cfg.get("music") or {}).get("path")
    if not this_music:
        # No music set is fine — the constraint is only "don't repeat".
        return []
    if not QUEUE_PATH.exists():
        return []
    queue = load_yaml(QUEUE_PATH)
    posts = (queue or {}).get("posts") or []
    # Find the most recently *posted* p2 entry by posted_at (fallback: list order).
    p2_posted = [
        p for p in posts
        if p.get("status") == "posted"
        and (p.get("id", "").startswith("p2_") or
             str(p.get("video", "")).startswith("output/p2_"))
    ]
    if not p2_posted:
        return []
    p2_posted.sort(key=lambda p: p.get("posted_at") or "", reverse=True)
    last = p2_posted[0]
    last_caption_or_id = last.get("id", "<unknown>")
    last_music = None
    # If the queue row recorded the source config we can read its music back.
    last_video = last.get("video", "")
    if last_video.startswith("output/"):
        slug = Path(last_video).parts[1]
        last_cfg = CONFIGS_DIR / f"{slug}.yaml"
        if last_cfg.exists():
            last_music = (load_yaml(last_cfg).get("music") or {}).get("path")
    if last_music and last_music == this_music:
        return [
            f"music.path `{this_music}` matches the most recent posted "
            f"Pillar 2 reel ({last_caption_or_id}). Rotate to a different "
            f"track."
        ]
    return []


def validate(path: Path) -> int:
    if not path.exists():
        print(f"ERROR: config not found: {path}", file=sys.stderr)
        return 2
    try:
        cfg = load_yaml(path)
    except Exception as e:
        print(f"ERROR: could not parse {path}: {e}", file=sys.stderr)
        return 2
    if not cfg:
        print(f"ERROR: empty config: {path}", file=sys.stderr)
        return 2

    errors: list[str] = []
    errors += check_lady(cfg)
    errors += check_crying_and_praying(cfg)
    errors += check_unique_scene(cfg, path)
    errors += check_music_rotation(cfg)

    print(f"validate_pillar2: {path.name}")
    if not errors:
        print("  ✓ subject is a lady")
        print("  ✓ lady is crying AND praying")
        print("  ✓ scene_slot is unique")
        print("  ✓ music rotates")
        return 0

    print(f"  ✗ {len(errors)} error(s):")
    for e in errors:
        print(f"    - {e}")
    return 1


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("config", type=Path, help="path to a configs/p2_*.yaml file")
    args = ap.parse_args()
    return validate(args.config)


if __name__ == "__main__":
    sys.exit(main())
