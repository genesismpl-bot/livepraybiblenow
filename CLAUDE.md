# Claude context — livepraybiblenow

This file travels with the repo so any Claude session on any machine
boots with the same project context. Update it when key decisions change.

## What this project is

Automated short-form video pipeline for the Instagram account
**@livepraybiblelove** (was `@livepraybible` until 2026-06-12) — bible /
prayer content. Instagram is the first
target; TikTok, YouTube Shorts, X follow later behind a platform-agnostic
publishing abstraction.

The user's automation goal is **end-to-end**: generation, rendering,
scheduling, and posting all autonomous. This isn't a manual-workflow
helper repo — it's intended to run on its own.

## Video format (Segment 1 + Segment 2)

Every video is two clips concatenated:

- **Segment 1 — Hook.** External viral clip (~10–15s), trimmed verbatim
  from a source MP4 (e.g. boomwhackers, marble drop, Bruce Lee
  motivation reel). Re-encoded to 1080×1920 @ 30fps so the codec params
  match Segment 2 for stream-copy concat.
- **Segment 2 — Payload.** Original bible content:
  - ElevenLabs voiceover from `script:` (with `*emphasis*` markers
    stripped before TTS).
  - Background still (`assets/stills/*.png`) animated with slow
    Ken-Burns zoom OR locked.
  - Burnt-in ASS captions, white `#FFFFFF` with gold `#FFD700`
    emphasis on `*marked*` words.
  - End-card CTA drawtext overlay fading in over the last ~1.5s
    pointing at `@livepraybiblelove`.

The pipeline lives in `hook_pivot_pipeline.py` and runs 7 stages:
`trim hook → choose still → ElevenLabs VO + word timestamps →
animate still → captions → burn payload → concat`.

## Code architecture

```
hook_pivot_pipeline.py   # main entrypoint (CLI: takes a YAML config)
lib/
  shared.py              # ffmpeg helpers, ElevenLabs VO, phone-mic filter
  explainer_captions.py  # ASS caption generation (sentence + source-match)
configs/
  behind.yaml            # active config; one config per video
scripts/
  segment2_shortlist_v1.md  # human-readable script library
assets/
  stills/                # background images for Segment 2 payload
```

Note: `lib/shared.py` declares `PERSONA_DIR`/`PERSONA_REFS` constants
pointing at `assets/persona/...` that aren't in this repo. The
hook_pivot flow doesn't touch them, so imports work — but other helpers
in `shared.py` that reference persona assets will fail if used. Add the
persona assets only if you start using flows that need them.

## Lineage — MMO framework

This repo is a derivative of the user's earlier project at
`/Users/tom-new/My Drive/MMO new framework/` (on tom-new's machine,
Google Drive — not a git repo). That framework's Segment 2 content
was money / motivation; the only meaningful change here is bible /
prayer content.

Code was **imported** (copied + adapted) — not forked, not used as a
library dependency. The MMO framework is the canonical reference for
the format. Useful files there:
- `hook_pivot_pipeline.py` — the pipeline we vendored.
- `configs/stop_doomscrolling_real_cost.yaml` — the config template.
- `output/hook_pivot_stop_doomscrolling_real_cost/` — reference renders
  (`boomwhackers12s_intro_plus_final.mp4`, `marble_drop_intro_plus_final.mp4`,
  `nokia_intro_plus_final.mp4`, `soft_cube_intro_plus_final.mp4`).
- Sibling pipelines for other formats: `carousel_pipeline.py`,
  `explainer_pipeline.py`, `lifestyle_pipeline.py`, `listicle.py`,
  `quote_reel_pipeline.py`, `scene_pipeline.py`, `silent_hook.py`.

## Decisions locked in

| Decision | Choice |
| --- | --- |
| First platform | Instagram |
| CTA handle | `@livepraybiblelove` (switched 2026-06-12 from `@livepraybible`; NOT `@livepraybiblenow` — repo name differs) |
| Code relationship to MMO framework | Import / vendor — don't fork, don't depend on Drive path |
| Segment 2 content source | **Scripture API** (specific provider TBD) |
| Brand opener for Segment 2 | Locked. Every script starts with `Stop *doomscrolling*.` + one of three pivots (random distribution): A `Have you *prayed* today?` / B `When's the last time you *prayed*?` / C `Talk to *God* instead.` See `scripts/segment2_shortlist_v1.md`. |

## Open items

- [ ] Confirm `@livepraybible` Instagram handle availability (automated
      check was blocked by IG; needs manual verification).
- [ ] Choose scripture API: `bible-api.com` (free, public-domain only),
      ESV API (free w/ key, rate-limited), or `api.bible` (broader).
- [ ] Pick Segment 1 hook clip source — reuse from MMO outputs (cross-
      machine paths break) or commit a curated `assets/hooks/` set
      (may need Git LFS).
- [ ] First end-to-end bible render.
- [ ] Instagram Graph API posting integration (needs Business/Creator
      account linked to a Facebook Page).
- [ ] Multi-platform publish abstraction.

## Working conventions

- The pipeline reads `*emphasis*` markers in `script:` as caption
  highlight cues. Asterisks are stripped before being sent to TTS, so
  pronunciation isn't affected.
- Each video gets its own YAML config under `configs/` with a `slug`
  that matches the filename stem. `output_dir` defaults to
  `output/<slug>/` (gitignored).
- The `output/` directory is gitignored — generated media doesn't cross
  machines. Each machine renders locally.
- Secrets live in `.env` (gitignored). Required keys are listed in
  `.env.example`.
- System dependency: `ffmpeg` + `ffprobe` on PATH (`brew install ffmpeg`
  on macOS). The pipeline shells out for every stage.

## Cross-machine setup

```bash
git clone https://github.com/genesismpl-bot/livepraybiblenow.git
cd livepraybiblenow
python -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt
cp .env.example .env  # then fill in API keys
brew install ffmpeg   # (or platform equivalent)
```

`hook.source` in any committed config may point to a local absolute
path that doesn't exist on a fresh machine. Either commit hook clips
into the repo (with LFS if needed) or override per-machine.
