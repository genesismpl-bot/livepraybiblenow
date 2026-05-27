#!/usr/bin/env python3
"""
prayer_reel_pipeline.py — reference-matched faith reels (Pillar 1 & 2).

Unlike hook_pivot_pipeline.py (viral hook + still + Ken Burns + VO captions),
this builds the *winning* @pray / @bondandseek style:

  moving cinematic video background  (real motion, not a still+zoom)
  + clean STATIC prayer / verse text  (white sans OR elegant serif)
  + fade-in "Follow @livepraybible" end card
  NO viral hook, NO bottom watermark, NO Gregorian chant, NO gold emphasis.

Background sources (config `background.type`):
  animate_still : Kling i2v animates an existing PNG into real motion
  scene         : Gemini makes a still from a prompt, then Kling animates it
  video         : use an existing MP4 as-is

Music is optional and OFF by default — on Instagram you'll usually add a
trending/worship audio in-app.

Run:
  python prayer_reel_pipeline.py configs/p1_sample.yaml
"""
from __future__ import annotations

import argparse
import os
import sys
from pathlib import Path

import yaml
from dotenv import load_dotenv

ROOT = Path(__file__).resolve().parent
load_dotenv(ROOT / ".env")
sys.path.insert(0, str(ROOT))

from lib.shared import (  # noqa: E402
    fal_upload, fal_submit, fal_poll, fal_fetch_result,
    run_ffmpeg, get_duration, upload_to_r2,
)

FAL_KLING_MODEL = "fal-ai/kling-video/v2/master/image-to-video"
FAL_NEG_PROMPT = (
    "text, watermark, logo, captions, distorted faces, extra limbs, "
    "warped hands, glitch, low quality, oversaturated, camera shake"
)

SANS_FONTS = [
    # macOS
    "/System/Library/Fonts/Supplemental/Arial.ttf",
    "/Library/Fonts/Arial.ttf",
    "/System/Library/Fonts/Helvetica.ttc",
    # Linux (Ubuntu CI runners + most distros)
    "/usr/share/fonts/truetype/liberation/LiberationSans-Regular.ttf",
    "/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf",
]
SERIF_FONTS = [
    # macOS
    "/System/Library/Fonts/Supplemental/Georgia.ttf",
    "/System/Library/Fonts/Supplemental/Times New Roman.ttf",
    "/Library/Fonts/Georgia.ttf",
    "/System/Library/Fonts/Times.ttc",
    # Linux (Ubuntu CI runners + most distros)
    "/usr/share/fonts/truetype/liberation/LiberationSerif-Regular.ttf",
    "/usr/share/fonts/truetype/dejavu/DejaVuSerif.ttf",
]


def pick_font(kind: str) -> str:
    cands = SERIF_FONTS if kind == "serif" else SANS_FONTS
    for c in cands:
        if Path(c).exists():
            return c
    raise FileNotFoundError(f"no {kind} font found; tried {cands}")


# ── Stage 1: background still (Gemini) ────────────────────────────────
def gen_scene_still(prompt: str, out: Path) -> Path:
    from google import genai
    from google.genai import types
    if out.exists():
        print(f"  scene still exists: {out.name}")
        return out
    client = genai.Client(api_key=os.environ["GEMINI_API_KEY"])
    full = (
        "Vertical 9:16 cinematic still, 1080x1920. " + prompt +
        " Photorealistic, natural light, film grain, NO text, NO watermark."
    )
    resp = client.models.generate_content(
        model="gemini-3.1-flash-image-preview",
        contents=[types.Part.from_text(text=full)],
        config=types.GenerateContentConfig(
            response_modalities=["IMAGE", "TEXT"], temperature=0.85),
    )
    for part in resp.candidates[0].content.parts:
        if part.inline_data is not None:
            out.parent.mkdir(parents=True, exist_ok=True)
            out.write_bytes(part.inline_data.data)
            print(f"  scene still saved: {out.name}")
            return out
    raise RuntimeError("Gemini returned no image")


# ── Stage 2: animate still → real motion (Kling i2v via fal) ──────────
def animate_still(still: Path, motion: str, kling_dur: int, out: Path) -> Path:
    if out.exists():
        print(f"  animated clip exists: {out.name}")
        return out
    print(f"  uploading {still.name} to fal.ai")
    img_url = fal_upload(str(still))
    payload = {
        "prompt": motion,
        "image_url": img_url,
        "duration": str(kling_dur),
        "aspect_ratio": "9:16",
        "negative_prompt": FAL_NEG_PROMPT,
        "cfg_scale": 0.5,
    }
    print(f"  submitting Kling i2v ({kling_dur}s)…")
    sub = fal_submit(FAL_KLING_MODEL, payload)
    fal_poll(sub["status_url"], timeout=900, interval=15)
    result = fal_fetch_result(sub["response_url"])
    video_url = result.get("video", {}).get("url") or result.get("video_url")
    if not video_url:
        raise RuntimeError(f"no video url in fal result: {result}")
    import requests
    print("  downloading animated clip…")
    r = requests.get(video_url, stream=True, timeout=300)
    r.raise_for_status()
    out.parent.mkdir(parents=True, exist_ok=True)  # Drive may prune empty dirs
    with open(out, "wb") as f:
        for chunk in r.iter_content(32768):
            f.write(chunk)
    print(f"  saved: {out.name}")
    return out


def resolve_background(cfg: dict, work: Path) -> Path:
    bg = cfg["background"]
    btype = bg["type"]
    if btype == "video":
        v = bg["video"]
        # Support http(s) URLs — download once into the work dir and cache.
        # This lets CI render configs whose source clips live on R2 instead
        # of being committed to the repo (size + licensing).
        if isinstance(v, str) and v.startswith(("http://", "https://")):
            cache = work / "background_source.mp4"
            if not cache.exists():
                import urllib.request
                cache.parent.mkdir(parents=True, exist_ok=True)
                print(f"  downloading background: {v}")
                urllib.request.urlretrieve(v, cache)
            return cache
        src = Path(v).expanduser()
        if not src.is_absolute():
            src = ROOT / src
        if not src.exists():
            raise FileNotFoundError(f"background.video not found: {src}")
        return src
    if btype == "scene":
        still = gen_scene_still(bg["scene"], work / "scene_still.png")
    elif btype == "animate_still":
        still = Path(bg["still"]).expanduser()
        if not still.exists():
            raise FileNotFoundError(f"background.still not found: {still}")
    else:
        raise ValueError(f"unknown background.type: {btype}")
    return animate_still(
        still, bg["motion"], int(bg.get("kling_duration", 5)),
        work / "raw_animated.mp4")


# ── Stage 3: composite text + CTA over (slowed) background ────────────
def build_reel(cfg: dict, raw: Path, work: Path) -> Path:
    out = work / "final.mp4"
    dur = float(cfg.get("duration", 14))
    darken = float(cfg.get("darken", 0.22))
    txt = cfg["text"]
    cta = cfg.get("cta", {})

    work.mkdir(parents=True, exist_ok=True)  # Drive may prune empty dirs

    font = pick_font(txt.get("font", "sans"))
    size = int(txt.get("size", 64))
    color = txt.get("color", "white")
    align = txt.get("align", "center")
    pos = txt.get("position", "center")
    x_expr = "(w-text_w)/2" if align == "center" else "120"

    # slow the raw clip so it fills `dur` smoothly (calm slow-mo), then trim
    raw_dur = get_duration(str(raw))
    factor = max(1.0, dur / raw_dur) if raw_dur else 1.0

    vf = (
        f"scale=1080:1920:force_original_aspect_ratio=increase,"
        f"crop=1080:1920,setpts={factor:.3f}*PTS,fps=30,"
        f"drawbox=x=0:y=0:w=iw:h=ih:color=black@{darken}:t=fill"
    )

    # Render EACH line as its own drawtext (this ffmpeg build renders a
    # literal \n in a textfile as a tofu glyph, so we position lines by hand).
    lines = txt["lines"].rstrip("\n").split("\n")
    line_h = size * 1.45
    block_h = len(lines) * line_h
    start_y = (1920 - block_h) / 2 if pos == "center" else 1920 * 0.48
    for i, line in enumerate(lines):
        s = line.strip()
        if not s:
            continue  # blank line → spacing only
        # one single-line file per line: no tofu, and no need to escape
        # colons / quotes / commas inside drawtext options.
        lf = work / f"line_{i:02d}.txt"
        lf.write_text(s)
        y = int(start_y + i * line_h)
        vf += (
            f",drawtext=textfile='{lf}':fontfile='{font}':fontcolor={color}:"
            f"fontsize={size}:x={x_expr}:y={y}:"
            f"shadowcolor=black@0.6:shadowx=2:shadowy=2"
        )

    if cta.get("text"):
        cta_dur = float(cta.get("duration", 1.6))
        cta_fade = float(cta.get("fade", 0.4))
        start = dur - cta_dur
        alpha = (f"if(lt(t,{start:.2f}),0,"
                 f"min(1,(t-{start:.2f})/{cta_fade:.2f}))")
        vf += (
            f",drawtext=text='{cta['text']}':fontfile='{font}':"
            f"fontcolor=white:fontsize=52:x=(w-text_w)/2:y=h*0.82:"
            f"shadowcolor=black@0.6:shadowx=2:shadowy=2:"
            f"alpha='{alpha}':enable='gte(t,{start:.2f})'"
        )

    music_cfg = cfg.get("music") or {}
    music = music_cfg.get("path")
    # Folder rotation: if no explicit path, pick a deterministic track from
    # `music.folder` keyed on hash(slug). Same slug → same track always
    # (reproducible); different slugs spread evenly across the folder.
    if not music and music_cfg.get("folder"):
        import hashlib
        folder = Path(music_cfg["folder"]).expanduser()
        if folder.exists():
            tracks = sorted(
                p for p in folder.iterdir()
                if p.is_file() and p.suffix.lower() in (".mp3", ".wav", ".m4a")
            )
            if tracks:
                idx = int(hashlib.md5(cfg["slug"].encode()).hexdigest(), 16) % len(tracks)
                music = str(tracks[idx])
                print(f"  music (rotation {idx + 1}/{len(tracks)}): {tracks[idx].name}")
            else:
                print(f"  music folder has no audio files: {folder}")
        else:
            print(f"  music folder not found: {folder}")
    args = ["-i", str(raw)]
    if music and Path(music).exists():
        mv       = float(music_cfg.get("volume", 0.18))
        start_at = float(music_cfg.get("start_at", 0))
        fade_in  = float(music_cfg.get("fade_in", 0))
        # If no explicit start, land on the chorus: look up the track in
        # assets/music/chorus_offsets.yaml; else fall back to a heuristic
        # (~30% into the song, clamped to [40, 75]s).
        if start_at == 0:
            offsets_path = ROOT / "assets" / "music" / "chorus_offsets.yaml"
            if offsets_path.exists():
                try:
                    offsets = yaml.safe_load(offsets_path.read_text()) or {}
                    name = Path(music).name
                    if name in offsets:
                        start_at = float(offsets[name])
                        print(f"  chorus start (table): {start_at:.0f}s")
                except Exception as e:
                    print(f"  chorus offsets read error: {e}")
            if start_at == 0:
                try:
                    track_dur = get_duration(music)
                    start_at = max(40.0, min(track_dur * 0.30, 75.0))
                    print(f"  chorus start (heuristic): {start_at:.0f}s")
                except Exception:
                    pass
        # Fade in 1s when seeking mid-song so it doesn't pop in.
        if start_at > 0 and fade_in == 0:
            fade_in = 1.0
        music_in: list[str] = []
        if start_at > 0:
            # input-side seek — fast and accurate enough for music
            music_in += ["-ss", f"{start_at:.3f}"]
        music_in += ["-i", str(music)]
        afilter = f"[1:a]volume={mv}"
        if fade_in > 0:
            afilter += f",afade=t=in:st=0:d={fade_in:.2f}"
        afilter += f",afade=t=out:st={dur-1.5:.2f}:d=1.5[a]"
        args += music_in + ["-filter_complex",
                 f"[0:v]{vf}[v];{afilter}",
                 "-map", "[v]", "-map", "[a]", "-shortest"]
    else:
        args += ["-vf", vf, "-an"]
    args += ["-t", f"{dur:.2f}", "-c:v", "libx264", "-pix_fmt", "yuv420p",
             "-r", "30", str(out)]
    run_ffmpeg(args)
    print(f"  built: {out}")
    return out


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("config")
    ap.add_argument("--upload-r2", action="store_true",
                    help="upload final.mp4 to Cloudflare R2 and print the public URL")
    args = ap.parse_args()
    cfg = yaml.safe_load(Path(args.config).read_text())
    for f in ("slug", "background", "text"):
        if f not in cfg:
            raise ValueError(f"config missing required field: {f}")
    work = ROOT / cfg.get("output_dir", f"output/{cfg['slug']}")
    work.mkdir(parents=True, exist_ok=True)
    print(f"=== prayer_reel → {work} ===\n")
    print("[1/2] Resolve background (generate/animate as needed)")
    raw = resolve_background(cfg, work)
    print("\n[2/2] Composite text + CTA")
    final = build_reel(cfg, raw, work)
    print(f"\n✓ DONE → {final}")

    if args.upload_r2 or cfg.get("upload_r2"):
        print("\n[+] Upload to R2")
        upload_to_r2(str(final), f"{cfg['slug']}.mp4")


if __name__ == "__main__":
    main()
