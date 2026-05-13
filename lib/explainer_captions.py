"""
Clean caption generator for explainer video pipeline.

Produces ASS subtitles with white text on semi-transparent dark pill
background. Current word is bolded (no color change). Matches the clean
modern caption style of reference explainer videos.
"""

from __future__ import annotations

import re
from pathlib import Path

from .shared import format_ass_time

VIDEO_W, VIDEO_H = 1080, 1920


def generate_clean_captions(words: list[dict], output_path: str) -> str:
    """
    Generate ASS subtitle file with clean white-on-dark-pill captions.

    Each word gets its own event showing the full chunk, with the current
    word bolded. Words grouped in chunks of 5 for readability.

    Args:
        words: List of {word, start, end} dicts from ElevenLabs timestamps.
        output_path: Where to save the .ass file.

    Returns:
        Path to the generated ASS file.
    """
    ass_header = f"""[Script Info]
ScriptType: v4.00+
PlayResX: {VIDEO_W}
PlayResY: {VIDEO_H}
WrapStyle: 0
ScaledBorderAndShadow: yes
YCbCr Matrix: TV.709

[V4+ Styles]
Format: Name, Fontname, Fontsize, PrimaryColour, SecondaryColour, OutlineColour, BackColour, Bold, Italic, Underline, StrikeOut, ScaleX, ScaleY, Spacing, Angle, BorderStyle, Outline, Shadow, Alignment, MarginL, MarginR, MarginV, Encoding
Style: Clean,-apple-system,80,&H00FFFFFF,&H00FFFFFF,&H00000000,&HB0000000,0,0,0,0,100,100,0,0,3,0,0,5,60,60,80,1

[Events]
Format: Layer, Start, End, Style, Name, MarginL, MarginR, MarginV, Effect, Text
"""

    events = []
    chunk_size = 5
    chunks = [words[i:i + chunk_size] for i in range(0, len(words), chunk_size)]

    for chunk in chunks:
        for word_idx, word_info in enumerate(chunk):
            word_start = word_info["start"]
            word_end = (
                chunk[word_idx + 1]["start"]
                if word_idx + 1 < len(chunk)
                else word_info["end"]
            )
            if word_end <= word_start:
                word_end = word_start + 0.05

            parts = []
            for j, w in enumerate(chunk):
                clean = (
                    w["word"]
                    .strip()
                    .replace("\\", "")
                    .replace("{", "")
                    .replace("}", "")
                )
                if not clean:
                    continue
                if j == word_idx:
                    parts.append(r"{\b1}" + clean + r"{\b0}")
                else:
                    parts.append(clean)

            if not parts:
                continue
            text = " ".join(parts)
            start_str = format_ass_time(word_start)
            end_str = format_ass_time(word_end)
            events.append(
                f"Dialogue: 0,{start_str},{end_str},Clean,,0,0,0,,{text}"
            )

    content = ass_header + "\n".join(events) + "\n"
    with open(output_path, "w", encoding="utf-8") as f:
        f.write(content)
    print(f"  Captions: {len(events)} events ({len(words)} words)")
    return output_path


def generate_source_match_captions(
    words: list[dict],
    output_path: str,
    font_size: int = 72,
    margin_v: int = 180,
    chunk_size: int = 4,
) -> str:
    """
    ASS subtitles styled to match the source dental-reel auto-captions:
    bold white sans-serif with a thick black stroke, NO pill background,
    anchored lower-third. Used for the payload half of hook+pivot reels so
    the splice from Segment 1 to Segment 2 looks like one continuous video.
    """
    # Alignment=5 → middle-center (matches source video's payload positioning).
    # margin_v is an additive vertical offset from center (0 = exact center).
    style = (
        f"Style: SrcMatch,-apple-system,{font_size},"
        f"&H00FFFFFF,&H00FFFFFF,&H00000000,&H00000000,"
        f"1,0,0,0,100,100,0,0,1,5,0,5,80,80,{margin_v},1"
    )
    ass_header = f"""[Script Info]
ScriptType: v4.00+
PlayResX: {VIDEO_W}
PlayResY: {VIDEO_H}
WrapStyle: 0
ScaledBorderAndShadow: yes
YCbCr Matrix: TV.709

[V4+ Styles]
Format: Name, Fontname, Fontsize, PrimaryColour, SecondaryColour, OutlineColour, BackColour, Bold, Italic, Underline, StrikeOut, ScaleX, ScaleY, Spacing, Angle, BorderStyle, Outline, Shadow, Alignment, MarginL, MarginR, MarginV, Encoding
{style}

[Events]
Format: Layer, Start, End, Style, Name, MarginL, MarginR, MarginV, Effect, Text
"""

    events = []
    chunks = [words[i:i + chunk_size] for i in range(0, len(words), chunk_size)]
    for chunk in chunks:
        for word_idx, word_info in enumerate(chunk):
            word_start = word_info["start"]
            word_end = (
                chunk[word_idx + 1]["start"]
                if word_idx + 1 < len(chunk)
                else word_info["end"]
            )
            if word_end <= word_start:
                word_end = word_start + 0.05

            parts = []
            for w in chunk:
                clean = (
                    w["word"]
                    .strip()
                    .replace("\\", "")
                    .replace("{", "")
                    .replace("}", "")
                )
                if clean:
                    parts.append(clean)
            if not parts:
                continue
            text = " ".join(parts)
            start_str = format_ass_time(word_start)
            end_str = format_ass_time(word_end)
            events.append(
                f"Dialogue: 0,{start_str},{end_str},SrcMatch,,0,0,0,,{text}"
            )

    content = ass_header + "\n".join(events) + "\n"
    with open(output_path, "w", encoding="utf-8") as f:
        f.write(content)
    print(f"  SrcMatch captions: {len(events)} events ({len(words)} words)")
    return output_path


def generate_sentence_match_captions(
    words: list[dict],
    script: str,
    output_path: str,
    font_size: int = 84,
    margin_v: int = 0,
    alignment: int = 5,
    primary_colour: str = "&H00FFFFFF",
    emphasis_colour: str | None = None,
) -> str:
    """
    One ASS event per sentence — the sentence stays on screen for the
    full duration of its spoken words. Splits on either explicit newlines
    in the script (user-authored phrasing) OR sentence-ending punctuation
    within a line, whichever is finer.

    Words wrapped in *asterisks* in the script are recoloured with
    `emphasis_colour` (e.g. gold). Asterisks are stripped from the
    rendered text and never count toward ElevenLabs word alignment.
    """
    style = (
        f"Style: SrcMatch,-apple-system,{font_size},"
        f"{primary_colour},{primary_colour},&H00000000,&H00000000,"
        f"1,0,0,0,100,100,0,0,1,5,0,{alignment},80,80,{margin_v},1"
    )
    ass_header = f"""[Script Info]
ScriptType: v4.00+
PlayResX: {VIDEO_W}
PlayResY: {VIDEO_H}
WrapStyle: 0
ScaledBorderAndShadow: yes
YCbCr Matrix: TV.709

[V4+ Styles]
Format: Name, Fontname, Fontsize, PrimaryColour, SecondaryColour, OutlineColour, BackColour, Bold, Italic, Underline, StrikeOut, ScaleX, ScaleY, Spacing, Angle, BorderStyle, Outline, Shadow, Alignment, MarginL, MarginR, MarginV, Encoding
{style}

[Events]
Format: Layer, Start, End, Style, Name, MarginL, MarginR, MarginV, Effect, Text
"""

    # Build phrase list, respecting both newlines and sentence punctuation.
    phrases: list[str] = []
    for line in script.strip().split("\n"):
        line = line.strip()
        if not line:
            continue
        for part in re.split(r"(?<=[.!?])\s+", line):
            p = part.strip()
            if p:
                phrases.append(p)

    # Map each phrase to a contiguous slice of the words list by counting tokens.
    # Word-count is done on the asterisk-stripped phrase so emphasis markers
    # never throw off ElevenLabs alignment.
    events = []
    word_idx = 0
    for phrase in phrases:
        clean_for_count = phrase.replace("*", "")
        n = len(clean_for_count.split())
        if word_idx >= len(words):
            break
        end_idx = min(word_idx + n - 1, len(words) - 1)
        start_t = words[word_idx]["start"]
        end_t = words[end_idx]["end"]
        if end_t <= start_t:
            end_t = start_t + 0.4

        # Build display text with ASS colour-override codes around *emphasis*.
        if emphasis_colour and "*" in phrase:
            segments = re.split(r"(\*[^*]+\*)", phrase)
            rendered = []
            for seg in segments:
                if not seg:
                    continue
                clean = seg.replace("\\", "").replace("{", "").replace("}", "")
                if seg.startswith("*") and seg.endswith("*"):
                    inner = clean.strip("*")
                    rendered.append(r"{\c" + emphasis_colour + r"&}" + inner + r"{\c}")
                else:
                    rendered.append(clean)
            text = "".join(rendered)
        else:
            text = phrase.replace("\\", "").replace("{", "").replace("}", "").replace("*", "")

        events.append(
            f"Dialogue: 0,{format_ass_time(start_t)},{format_ass_time(end_t)},SrcMatch,,0,0,0,,{text}"
        )
        word_idx = end_idx + 1

    content = ass_header + "\n".join(events) + "\n"
    with open(output_path, "w", encoding="utf-8") as f:
        f.write(content)
    print(f"  Sentence captions: {len(events)} events ({len(phrases)} phrases, {len(words)} words)")
    return output_path
