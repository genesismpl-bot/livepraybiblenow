# livepraybiblenow

Automated short-form video pipeline for `@livepraybiblelove` — bible / prayer
content on Instagram first, multi-platform later.

## Structure

Every video is two segments concatenated:

- **Segment 1 — Hook.** An external viral clip (e.g. boomwhackers, marble
  drop), trimmed verbatim. Used to stop the scroll.
- **Segment 2 — Payload.** Original bible content: ElevenLabs voiceover
  over a Ken-Burns still, with white captions and gold emphasis on
  marked `*words*`. Ends with a CTA card pointing at `@livepraybiblelove`.

This pipeline is adapted from the parent **MMO new framework** at
`/Users/tom-new/My Drive/MMO new framework`. Segment 2 content is the
only meaningful difference: bible/prayer here vs. money/motivation there.

## Layout

```
hook_pivot_pipeline.py   # main pipeline (7-stage ffmpeg flow)
lib/
  shared.py              # ffmpeg helpers, ElevenLabs VO, phone-mic filter
  explainer_captions.py  # ASS caption generation (sentence + source-match)
configs/
  behind.yaml            # first bible config (script #9 "Behind")
assets/
  stills/                # background images for Segment 2
    sunrise_ridge.png
    marble_gold.png
    candle_dark.png
```

## Setup

```bash
python -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt
cp .env.example .env  # then fill in API keys
brew install ffmpeg
```

## Running

```bash
python hook_pivot_pipeline.py configs/behind.yaml
```

Before the first run, edit `configs/behind.yaml` and set `hook.source`
to a path to an external viral clip (TODO marker in the file).

The pipeline writes intermediates and `final.mp4` into `output/<slug>/`.

## Status

- [x] Repo created, framework imported.
- [ ] Pick a Segment 1 hook clip library (reuse from MMO, or fresh).
- [ ] Scripture API decision (bible-api.com / ESV API / api.bible).
- [ ] Confirm `@livepraybiblelove` Instagram handle is available.
- [ ] First end-to-end render.
- [ ] Instagram posting automation (Graph API).
- [ ] Multi-platform publish abstraction.
