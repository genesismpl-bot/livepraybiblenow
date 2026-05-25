#!/usr/bin/env python3
"""
Hook+pivot reel pipeline — splice a recycled viral clip onto a fresh
motivational/quote payload for TikTok/Reels/Shorts.

Shape (per analysed reference reel):
    [Segment 1: viral hook, trimmed verbatim from a source MP4]
    HARD CUT
    [Segment 2: held still + ElevenLabs VO + word-synced captions]
    [End-card CTA over the last ~1.5s of Segment 2]

Captions across the splice use the SrcMatch ASS style (white sans, black
stroke, no pill, lower-third) so the viewer reads both halves as one video.

Usage:
    python hook_pivot_pipeline.py configs/stop_doomscrolling.yaml
    python hook_pivot_pipeline.py configs/stop_doomscrolling.yaml --variant 1
    python hook_pivot_pipeline.py configs/stop_doomscrolling.yaml --skip-image
    python hook_pivot_pipeline.py configs/stop_doomscrolling.yaml --skip-voice
"""
from __future__ import annotations

import argparse
import os
import shutil
import sys
import time
from pathlib import Path
from typing import Any

import yaml
from dotenv import load_dotenv

ROOT = Path(__file__).resolve().parent
load_dotenv(ROOT / ".env")
sys.path.insert(0, str(ROOT))

from lib.shared import (  # noqa: E402
    apply_phone_mic_filter,
    generate_voice_with_timestamps,
    get_duration,
    run_ffmpeg,
    upload_to_r2,
)
from lib.explainer_captions import (  # noqa: E402
    generate_source_match_captions,
    generate_sentence_match_captions,
)


def hex_to_ass_colour(value: str) -> str:
    """Accept either ASS hex (&H00BBGGRR) or CSS hex (#RRGGBB) → ASS hex."""
    if value.startswith("&H"):
        return value
    h = value.lstrip("#")
    if len(h) != 6:
        raise ValueError(f"caption colour must be #RRGGBB or &H00BBGGRR, got {value!r}")
    r, g, b = int(h[0:2], 16), int(h[2:4], 16), int(h[4:6], 16)
    return f"&H00{b:02X}{g:02X}{r:02X}"

VIDEO_W, VIDEO_H = 1080, 1920
FPS = 30


# ──────────────────────────────────────────────────────────────────────
# Config
# ──────────────────────────────────────────────────────────────────────

def load_config(path: Path, no_hook: bool = False) -> dict[str, Any]:
    cfg = yaml.safe_load(path.read_text())
    required = ["slug", "script"]
    if not no_hook:
        required.append("hook")
    if "still_path" not in cfg and "background_video" not in cfg:
        required.append("scene")
    for field in required:
        if field not in cfg:
            raise ValueError(f"config missing required field: {field}")

    cfg.setdefault("variants", 3)
    cfg.setdefault("output_dir", f"output/hook_pivot_{cfg['slug']}")

    motion = cfg.setdefault("motion", {})
    motion.setdefault("type", "ken_burns_slow_zoom")
    motion.setdefault("zoom_to", 1.08)
    motion.setdefault("duration", 43.0)

    voice = cfg.setdefault("voice", {})
    voice.setdefault("voice_id", "bbGtsRRKUfYO634UxSjz")
    voice.setdefault("stability", 0.45)
    voice.setdefault("similarity", 0.80)
    voice.setdefault("style", 0.30)
    voice.setdefault("apply_phone_mic_filter", True)

    captions = cfg.setdefault("captions", {})
    captions.setdefault("style", "source_match")
    captions.setdefault("mode", "sentence")          # sentence | word
    captions.setdefault("font_size", 84)
    captions.setdefault("margin_v", 0)
    captions.setdefault("alignment", 5)              # ASS numpad: 5=mid-center, 2=bot-center, 8=top-center
    captions.setdefault("colour", "#FFFFFF")
    captions.setdefault("emphasis_colour", "#FFD700")  # *word* highlight
    captions.setdefault("chunk_size", 4)             # only used in word mode

    cta = cfg.setdefault("cta", {})
    cta.setdefault("text", "")
    cta.setdefault("duration", 1.5)
    cta.setdefault("fade_in", 0.4)
    cta.setdefault("y_frac", 0.45)  # vertical centre as fraction of frame height
    cta.setdefault("font_size", 64)

    music = cfg.setdefault("music", {})
    music.setdefault("path", None)        # set to a file to enable music bed
    music.setdefault("volume", 0.15)      # 0.0 = silent, 1.0 = full
    music.setdefault("fade_in", 1.0)      # seconds
    music.setdefault("fade_out", 1.5)     # seconds before end of video

    return cfg


# ──────────────────────────────────────────────────────────────────────
# Stage 1: trim Segment 1 from source MP4 (re-encoded to canonical recipe)
# ──────────────────────────────────────────────────────────────────────

def trim_hook(cfg: dict, work: Path) -> Path:
    out = work / "hook.mp4"
    if out.exists():
        print(f"  hook.mp4 exists ({get_duration(str(out)):.2f}s), skipping")
        return out

    src = Path(cfg["hook"]["source"]).expanduser()
    if not src.exists():
        raise FileNotFoundError(f"hook source not found: {src}")

    start = float(cfg["hook"]["start"])
    end = float(cfg["hook"]["end"])
    duration = end - start
    if duration <= 0:
        raise ValueError(f"hook end ({end}) must be > start ({start})")

    # Re-encode (not stream-copy) so codec params match Segment 2 exactly.
    # Pad-and-fit to 1080x1920 in case source isn't exactly 9:16.
    vf = (
        f"scale={VIDEO_W}:{VIDEO_H}:force_original_aspect_ratio=decrease,"
        f"pad={VIDEO_W}:{VIDEO_H}:(ow-iw)/2:(oh-ih)/2:black,"
        f"setsar=1"
    )
    run_ffmpeg([
        "-ss", f"{start:.3f}",
        "-i", str(src),
        "-t", f"{duration:.3f}",
        "-vf", vf,
        "-r", str(FPS),
        "-c:v", "libx264", "-preset", "medium", "-crf", "18",
        "-pix_fmt", "yuv420p",
        "-c:a", "aac", "-b:a", "192k", "-ar", "44100", "-ac", "2",
        str(out),
    ])
    print(f"  hook trimmed: {start}s → {end}s = {duration:.2f}s")
    return out


# ──────────────────────────────────────────────────────────────────────
# Stage 2: Gemini still variants (landscape — no persona refs)
# ──────────────────────────────────────────────────────────────────────

def generate_variants(cfg: dict, work: Path) -> list[Path]:
    from google import genai
    from google.genai import types

    variants_dir = work / "variants"
    variants_dir.mkdir(parents=True, exist_ok=True)

    existing = sorted(variants_dir.glob("variant_*.png"))
    if existing:
        print(f"  {len(existing)} variants already exist, skipping Gemini")
        return existing

    client = genai.Client(api_key=os.environ["GEMINI_API_KEY"])
    prompt = (
        "Generate a vertical 9:16 (1080x1920) cinematic still based on the "
        "following scene. Phone-camera aesthetic, NOT studio. NO people, "
        "NO phones, NO text overlays.\n\n"
        f"SCENE:\n{cfg['scene']}"
    )

    saved: list[Path] = []
    for i in range(cfg["variants"]):
        out = variants_dir / f"variant_{i:02d}.png"
        for attempt in range(1, 4):
            try:
                resp = client.models.generate_content(
                    model="gemini-3.1-flash-image-preview",
                    contents=[types.Part.from_text(text=prompt)],
                    config=types.GenerateContentConfig(
                        response_modalities=["IMAGE", "TEXT"],
                        temperature=0.85,
                    ),
                )
                got = False
                for part in resp.candidates[0].content.parts:
                    if part.inline_data is not None:
                        out.write_bytes(part.inline_data.data)
                        print(f"  variant_{i:02d}: {out.name}")
                        saved.append(out)
                        got = True
                        break
                if got:
                    break
                raise RuntimeError("no image in response")
            except Exception as e:
                print(f"  variant_{i:02d} attempt {attempt}/3: {e}")
                if attempt < 3:
                    time.sleep(8 * attempt)
        time.sleep(1.5)

    if not saved:
        raise RuntimeError("Gemini produced no variants")
    return saved


def pick_variant(variants: list[Path], explicit: int | None) -> Path:
    if explicit is not None:
        if explicit < 0 or explicit >= len(variants):
            raise IndexError(f"--variant {explicit} out of range (0..{len(variants)-1})")
        return variants[explicit]
    print(f"  no --variant set, using {variants[0].name}")
    print(f"  (preview {variants[0].parent} and re-run with --variant N to swap)")
    return variants[0]


# ──────────────────────────────────────────────────────────────────────
# Stage 3: ElevenLabs VO + phone-mic filter
# ──────────────────────────────────────────────────────────────────────

def generate_voice(cfg: dict, work: Path) -> tuple[Path, list[dict]]:
    raw_audio = work / "voice_raw.mp3"
    filtered_audio = work / "voice.mp3"
    timestamps = work / "word_timestamps.json"

    if filtered_audio.exists() and timestamps.exists():
        import json
        words = json.loads(timestamps.read_text())
        print(f"  voice.mp3 + word_timestamps.json exist, skipping ElevenLabs")
        return filtered_audio, words

    voice_cfg = cfg["voice"]
    settings = {
        "stability":        voice_cfg["stability"],
        "similarity_boost": voice_cfg["similarity"],
        "style":            voice_cfg["style"],
        "use_speaker_boost": True,
    }
    # Strip *emphasis* markers — those are caption-only, not for the TTS engine.
    spoken = cfg["script"].replace("*", "").strip()
    _, words = generate_voice_with_timestamps(
        text=spoken,
        output_path=raw_audio,
        voice_id=voice_cfg["voice_id"],
        voice_settings=settings,
    )

    if voice_cfg["apply_phone_mic_filter"]:
        apply_phone_mic_filter(str(raw_audio), str(filtered_audio))
    else:
        shutil.copy(raw_audio, filtered_audio)

    import json
    timestamps.write_text(json.dumps(words, indent=2))
    return filtered_audio, words


# ──────────────────────────────────────────────────────────────────────
# Stage 4: animate held still (locked OR slow ken-burns) sized to VO
# ──────────────────────────────────────────────────────────────────────

def animate_still(still: Path, audio: Path, cfg: dict, work: Path) -> Path:
    out = work / "still_motion.mp4"
    if out.exists():
        return out

    audio_dur = get_duration(str(audio))
    cta_dur = float(cfg["cta"]["duration"])
    target_dur = audio_dur + cta_dur

    # ── Moving video background (Route A: stock/AI clip) ──────────────
    # If background_video is set, loop + scale + centre-crop a real clip
    # to fill 1080x1920 for the payload duration, instead of animating a
    # still. The clip's own motion replaces the Ken-Burns zoom.
    bg_video = cfg.get("background_video")
    if bg_video:
        src = Path(bg_video).expanduser()
        if not src.is_absolute():
            src = ROOT / src
        if not src.exists():
            raise FileNotFoundError(f"background_video not found: {src}")
        run_ffmpeg([
            "-stream_loop", "-1", "-i", str(src),
            "-t", f"{target_dur:.3f}",
            "-vf", (
                f"scale={VIDEO_W}:{VIDEO_H}:force_original_aspect_ratio=increase,"
                f"crop={VIDEO_W}:{VIDEO_H},fps={FPS}"
            ),
            "-r", str(FPS),
            "-c:v", "libx264", "-preset", "medium", "-crf", "18",
            "-pix_fmt", "yuv420p",
            "-an",
            str(out),
        ])
        print(f"  background video fitted to {VIDEO_W}x{VIDEO_H} "
              f"({target_dur:.2f}s, looped): {src.name}")
        return out

    motion = cfg["motion"]
    mtype = motion["type"]

    if mtype == "ken_burns_slow_zoom":
        zoom_to = float(motion["zoom_to"])
        # zoompan increment per output frame so we hit zoom_to at end of clip
        total_frames = int(target_dur * FPS)
        # solve: 1.0 + inc * total_frames = zoom_to
        inc = (zoom_to - 1.0) / max(total_frames, 1)
        vf = (
            f"scale={VIDEO_W * 4}:{VIDEO_H * 4}:force_original_aspect_ratio=increase,"
            f"crop={VIDEO_W * 4}:{VIDEO_H * 4},"
            f"zoompan=z='min(zoom+{inc:.6f},{zoom_to})'"
            f":x='iw/2-(iw/zoom/2)':y='ih/2-(ih/zoom/2)'"
            f":d={total_frames}:s={VIDEO_W}x{VIDEO_H}:fps={FPS}"
        )
    elif mtype == "locked":
        vf = (
            f"scale={VIDEO_W}:{VIDEO_H}:force_original_aspect_ratio=increase,"
            f"crop={VIDEO_W}:{VIDEO_H}"
        )
    else:
        raise ValueError(f"unknown motion.type: {mtype}")

    run_ffmpeg([
        "-loop", "1", "-i", str(still),
        "-t", f"{target_dur:.3f}",
        "-vf", vf,
        "-r", str(FPS),
        "-c:v", "libx264", "-preset", "medium", "-crf", "18",
        "-pix_fmt", "yuv420p",
        "-an",
        str(out),
    ])
    print(f"  still animated ({mtype}, {target_dur:.2f}s)")
    return out


# ──────────────────────────────────────────────────────────────────────
# Stage 5: captions (.ass with SrcMatch style)
# ──────────────────────────────────────────────────────────────────────

def make_captions(words: list[dict], cfg: dict, work: Path) -> Path:
    out = work / "captions.ass"
    cap = cfg["captions"]
    colour = hex_to_ass_colour(cap["colour"])
    if cap["mode"] == "sentence":
        emphasis = hex_to_ass_colour(cap["emphasis_colour"])
        generate_sentence_match_captions(
            words,
            cfg["script"],
            str(out),
            font_size=cap["font_size"],
            margin_v=cap["margin_v"],
            alignment=cap["alignment"],
            primary_colour=colour,
            emphasis_colour=emphasis,
        )
    else:
        generate_source_match_captions(
            words,
            str(out),
            font_size=cap["font_size"],
            margin_v=cap["margin_v"],
            chunk_size=cap["chunk_size"],
        )
    return out


# ──────────────────────────────────────────────────────────────────────
# Stage 6: build payload (still_motion + captions + voice + CTA drawtext)
# ──────────────────────────────────────────────────────────────────────

def build_payload(
    still_motion: Path,
    captions: Path,
    audio: Path,
    cfg: dict,
    work: Path,
) -> Path:
    out = work / "payload.mp4"
    if out.exists():
        return out

    audio_dur = get_duration(str(audio))
    cta = cfg["cta"]
    cta_dur = float(cta["duration"])
    cta_fade = float(cta["fade_in"])
    cta_start = audio_dur  # CTA fades in right after VO ends
    total_dur = audio_dur + cta_dur

    # ASS captions burn-in path needs ffmpeg-friendly escaping.
    cap_path_esc = str(captions).replace("\\", "/").replace(":", r"\:")

    # CTA splits at the first " for " to keep the handle on its own line —
    # avoids text bleeding off frame edges at fontsize that's readable on a phone.
    drawtext = ""
    if cta["text"]:
        text = cta["text"]
        if " for " in text:
            line1, line2 = text.split(" for ", 1)
            line2 = "for " + line2
        else:
            line1, line2 = text, ""

        def esc(s: str) -> str:
            return s.replace("\\", r"\\").replace("'", r"\'").replace(":", r"\:")

        alpha = (
            f":enable='gte(t,{cta_start:.3f})'"
            f":alpha='if(lt(t,{cta_start:.3f}),0,"
            f"if(lt(t,{cta_start + cta_fade:.3f}),"
            f"(t-{cta_start:.3f})/{cta_fade:.3f},1))'"
        )
        cta_font_size = int(cta.get("font_size", 64))
        cta_y_frac = float(cta.get("y_frac", 0.45))
        common = (
            f":fontsize={cta_font_size}:fontcolor=white"
            ":bordercolor=black:borderw=5"
            ":x=(w-text_w)/2"
        )
        line1_filter = (
            f",drawtext=text='{esc(line1)}'{common}:y=h*{cta_y_frac:.3f}-text_h/2{alpha}"
        )
        line2_filter = (
            f",drawtext=text='{esc(line2)}'{common}:y=h*{cta_y_frac:.3f}+text_h/2+10{alpha}"
            if line2 else ""
        )
        drawtext = line1_filter + line2_filter

    vf = f"ass='{cap_path_esc}'{drawtext}"

    # loudnorm brings VO to TikTok/Reels target loudness (-14 LUFS) so it
    # doesn't get drowned by the loud auto-captioned hook segment.
    music_cfg = cfg.get("music", {}) or {}
    music_path = music_cfg.get("path")
    if music_path:
        music_path = Path(music_path)
        if not music_path.is_absolute():
            music_path = ROOT / music_path
        if not music_path.exists():
            raise FileNotFoundError(f"music.path not found: {music_path}")

    if music_path:
        # Mix voice + low-volume looped music bed with fade in/out.
        mv = float(music_cfg.get("volume", 0.15))
        m_fi = float(music_cfg.get("fade_in", 1.0))
        m_fo = float(music_cfg.get("fade_out", 1.5))
        fade_out_start = max(0.0, total_dur - m_fo)
        filter_complex = (
            f"[0:v]{vf}[v];"
            f"[1:a]loudnorm=I=-14:LRA=11:TP=-1.5[voice];"
            f"[2:a]aloop=loop=-1:size=2e9,atrim=0:{total_dur:.3f},"
            f"volume={mv:.3f},"
            f"afade=t=in:st=0:d={m_fi:.3f},"
            f"afade=t=out:st={fade_out_start:.3f}:d={m_fo:.3f}[music];"
            f"[voice][music]amix=inputs=2:duration=longest:dropout_transition=0[a]"
        )
        run_ffmpeg([
            "-i", str(still_motion),
            "-i", str(audio),
            "-i", str(music_path),
            "-filter_complex", filter_complex,
            "-map", "[v]",
            "-map", "[a]",
            "-t", f"{total_dur:.3f}",
            "-r", str(FPS),
            "-c:v", "libx264", "-preset", "medium", "-crf", "18",
            "-pix_fmt", "yuv420p",
            "-c:a", "aac", "-b:a", "192k", "-ar", "44100", "-ac", "2",
            str(out),
        ])
        print(f"  payload built ({total_dur:.2f}s, captions burned, CTA at {cta_start:.2f}s, music bed @ volume={mv})")
        return out

    af = "loudnorm=I=-14:LRA=11:TP=-1.5"

    run_ffmpeg([
        "-i", str(still_motion),
        "-i", str(audio),
        "-vf", vf,
        "-af", af,
        "-t", f"{total_dur:.3f}",
        "-r", str(FPS),
        "-c:v", "libx264", "-preset", "medium", "-crf", "18",
        "-pix_fmt", "yuv420p",
        "-c:a", "aac", "-b:a", "192k", "-ar", "44100", "-ac", "2",
        str(out),
    ])
    print(f"  payload built ({total_dur:.2f}s, captions burned, CTA at {cta_start:.2f}s, loudnorm applied)")
    return out


# ──────────────────────────────────────────────────────────────────────
# Stage 7: concat hook + payload
# ──────────────────────────────────────────────────────────────────────

def concat(hook: Path, payload: Path, work: Path) -> Path:
    manifest = work / "concat.txt"
    out = work / "final.mp4"

    # Both inputs must be encoded with identical codec params; trim_hook
    # and build_payload use the same recipe so stream-copy concat works.
    manifest.write_text(
        f"file '{hook.resolve()}'\n"
        f"file '{payload.resolve()}'\n"
    )
    run_ffmpeg([
        "-f", "concat", "-safe", "0",
        "-i", str(manifest),
        "-c", "copy",
        str(out),
    ])
    print(f"  concatenated → {out.name}")
    return out


# ──────────────────────────────────────────────────────────────────────
# Main
# ──────────────────────────────────────────────────────────────────────

def main():
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("config", type=Path, help="YAML config path")
    ap.add_argument("--variants",   type=int,  help="override variants count")
    ap.add_argument("--variant",    type=int,  help="which variant index to use")
    ap.add_argument("--out",        type=Path, help="working directory override")
    ap.add_argument("--skip-image", action="store_true", help="reuse existing variants")
    ap.add_argument("--skip-voice", action="store_true", help="reuse existing voice.mp3")
    ap.add_argument("--skip-hook",  action="store_true", help="reuse existing hook.mp4")
    ap.add_argument("--no-hook",    action="store_true", help="skip Segment 1 entirely; payload.mp4 becomes the final output")
    ap.add_argument("--upload-r2",  action="store_true", help="upload final.mp4 to Cloudflare R2 and print the public URL")
    args = ap.parse_args()

    cfg = load_config(args.config, no_hook=args.no_hook)
    if args.variants:
        cfg["variants"] = args.variants

    work = args.out or (ROOT / cfg["output_dir"])
    work.mkdir(parents=True, exist_ok=True)
    shutil.copy(args.config, work / "config.yaml")
    print(f"\n=== hook_pivot_pipeline → {work} ===\n")

    # 1. trim hook (skipped entirely under --no-hook)
    if args.no_hook:
        print("[1/7] Trim hook from source MP4 — SKIPPED (--no-hook)")
        hook = None
    else:
        print("[1/7] Trim hook from source MP4")
        hook = trim_hook(cfg, work) if not args.skip_hook else (work / "hook.mp4")
    print()

    # 2. variants (or explicit still_path / background_video override)
    print("[2/7] Background still")
    if cfg.get("background_video"):
        chosen = None
        print(f"  using background_video: {cfg['background_video']}")
    elif cfg.get("still_path"):
        chosen = Path(cfg["still_path"])
        if not chosen.is_absolute():
            chosen = ROOT / chosen
        if not chosen.exists():
            raise FileNotFoundError(f"still_path not found: {chosen}")
        print(f"  using still_path override: {chosen}")
    else:
        variants = (sorted((work / "variants").glob("variant_*.png"))
                    if args.skip_image
                    else generate_variants(cfg, work))
        chosen = pick_variant(variants, args.variant)
        print(f"  using: {chosen.name}")
    print()

    # 3. voice + timestamps
    print("[3/7] ElevenLabs VO + word timestamps")
    audio, words = ((work / "voice.mp3", _load_words(work))
                    if args.skip_voice and (work / "voice.mp3").exists()
                    else generate_voice(cfg, work))
    print()

    # 4. animate still to (audio_dur + cta_dur)
    print("[4/7] Animate still (Ken Burns / locked)")
    still_motion = animate_still(chosen, audio, cfg, work)
    print()

    # 5. captions
    print("[5/7] Source-match ASS captions")
    captions = make_captions(words, cfg, work)
    print()

    # 6. build payload
    print("[6/7] Burn captions + audio + CTA")
    payload = build_payload(still_motion, captions, audio, cfg, work)
    print()

    # 7. concat (skipped under --no-hook — payload IS the final)
    if args.no_hook:
        print("[7/7] Concat hook + payload — SKIPPED (--no-hook)")
        final = payload
    else:
        print("[7/7] Concat hook + payload")
        final = concat(hook, payload, work)
    print(f"\n✓ DONE → {final}")
    print(f"  total: {get_duration(str(final)):.2f}s\n")

    if args.upload_r2 or cfg.get("upload_r2"):
        print("[+] Upload to R2")
        upload_to_r2(str(final), f"{cfg['slug']}.mp4")


def _load_words(work: Path) -> list[dict]:
    import json
    return json.loads((work / "word_timestamps.json").read_text())


if __name__ == "__main__":
    main()
