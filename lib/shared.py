"""
Shared utilities for all pipeline variants.

Extracts duplicated code from pipeline.py and scene_pipeline.py into
reusable functions: ffmpeg helpers, voice generation, image generation,
fal.ai queue API, and common constants.
"""

from __future__ import annotations

import base64
import json
import os
import shutil
import subprocess
import time
from pathlib import Path

ROOT_DIR = Path(__file__).parent.parent

PERSONA_DIR = ROOT_DIR / "assets" / "persona"
PERSONA_REFS = [
    PERSONA_DIR / "persona_collage_v2.png",
    PERSONA_DIR / "fullbody_reference.png",
    PERSONA_DIR / "podcast.png",
]

VIDEO_W, VIDEO_H = 1080, 1920

# ── ffmpeg / ffprobe ─────────────────────────────────────────────────

def ffmpeg_bin() -> str:
    for candidate in [
        shutil.which("ffmpeg"),
        "/tmp/ffmpeg_bin/ffmpeg",
        str(Path.home() / ".local" / "bin" / "ffmpeg"),
        "/usr/local/bin/ffmpeg",
    ]:
        if candidate and Path(candidate).exists():
            return candidate
    raise RuntimeError("ffmpeg not found. Install via: brew install ffmpeg")


def ffprobe_bin() -> str:
    for candidate in [
        shutil.which("ffprobe"),
        "/tmp/ffmpeg_bin/ffprobe",
        str(Path.home() / ".local" / "bin" / "ffprobe"),
        "/usr/local/bin/ffprobe",
    ]:
        if candidate and Path(candidate).exists():
            return candidate
    raise RuntimeError("ffprobe not found")


def get_duration(path: str) -> float:
    result = subprocess.run(
        [ffprobe_bin(), "-v", "quiet",
         "-show_entries", "format=duration",
         "-of", "default=noprint_wrappers=1:nokey=1",
         path],
        capture_output=True, text=True,
    )
    val = result.stdout.strip()
    return float(val) if val else 0.0


def run_ffmpeg(args: list[str], check: bool = True) -> subprocess.CompletedProcess:
    cmd = [ffmpeg_bin(), "-y"] + args
    proc = subprocess.run(cmd, capture_output=True, text=True)
    if check and proc.returncode != 0:
        # Surface ffmpeg's own error message — tail of stderr is usually enough
        tail = "\n".join(proc.stderr.strip().splitlines()[-15:])
        raise subprocess.CalledProcessError(
            proc.returncode, cmd, output=proc.stdout,
            stderr=f"\n--- ffmpeg stderr (last 15 lines) ---\n{tail}",
        )
    return proc


# ── ElevenLabs Voice Generation ──────────────────────────────────────

DEFAULT_VOICE_SETTINGS = {
    "stability": 0.30,
    "similarity_boost": 0.80,
    "style": 0.35,
    "use_speaker_boost": True,
}


def generate_voice_with_timestamps(
    text: str,
    output_path: Path,
    voice_id: str = "bbGtsRRKUfYO634UxSjz",
    model_id: str = "eleven_multilingual_v2",
    voice_settings: dict | None = None,
) -> tuple[str, list[dict]]:
    """Generate audio with word-level timestamps.

    Returns (audio_path, words) where words = [{word, start, end}, ...]
    """
    import requests

    api_key = os.environ["ELEVENLABS_API_KEY"]
    settings = voice_settings or DEFAULT_VOICE_SETTINGS

    resp = requests.post(
        f"https://api.elevenlabs.io/v1/text-to-speech/{voice_id}/with-timestamps",
        headers={"xi-api-key": api_key, "Content-Type": "application/json"},
        json={"text": text, "model_id": model_id, "voice_settings": settings},
    )
    resp.raise_for_status()
    data = resp.json()

    audio_bytes = base64.b64decode(data["audio_base64"])
    output_path.parent.mkdir(parents=True, exist_ok=True)
    output_path.write_bytes(audio_bytes)

    dur = get_duration(str(output_path))
    print(f"  Audio: {len(audio_bytes)//1024}KB, {dur:.1f}s")

    alignment = data.get("alignment", {})
    words = _chars_to_words(
        alignment.get("characters", []),
        alignment.get("character_start_times_seconds", []),
        alignment.get("character_end_times_seconds", []),
    )
    return str(output_path), words


def _chars_to_words(chars, starts, ends):
    words = []
    current_word = ""
    word_start = None

    for i, char in enumerate(chars):
        if char in (" ", "\n", "\t"):
            if current_word and word_start is not None:
                words.append({
                    "word": current_word,
                    "start": word_start,
                    "end": starts[i] if i < len(starts) else ends[-1] if ends else 0.0,
                })
                current_word = ""
                word_start = None
        else:
            if word_start is None:
                word_start = starts[i] if i < len(starts) else 0.0
            current_word += char

    if current_word and word_start is not None:
        words.append({
            "word": current_word,
            "start": word_start,
            "end": ends[-1] if ends else word_start + 0.3,
        })
    return words


# ── Phone-Mic Filter ─────────────────────────────────────────────────

PHONE_MIC_FILTER = (
    "highpass=f=120,"
    "lowpass=f=8000,"
    "acompressor=threshold=-18dB:ratio=3:attack=5:release=100,"
    "aecho=0.8:0.7:12:0.3,"
    "volume=0.95"
)


def apply_phone_mic_filter(input_path: str, output_path: str) -> str:
    run_ffmpeg([
        "-i", input_path,
        "-af", PHONE_MIC_FILTER,
        "-ar", "44100", "-ac", "1",
        "-b:a", "128k",
        output_path,
    ])
    print(f"  Phone-mic filter applied: {output_path}")
    return output_path


# ── Phone-Camera Video Filter ────────────────────────────────────────

PHONE_CAMERA_FILTER = (
    "gblur=sigma=0.6,"
    "eq=saturation=0.85:contrast=0.95:brightness=-0.02:gamma=1.05,"
    "noise=alls=16:allf=t+u,"
    "vignette=PI/4,"
    "scale=720:-2,scale=1080:-2:flags=bilinear"
)


def apply_phone_camera_filter(input_path: str, output_path: str) -> str:
    run_ffmpeg([
        "-i", input_path,
        "-vf", PHONE_CAMERA_FILTER,
        "-crf", "28",
        "-c:a", "copy",
        output_path,
    ])
    print(f"  Phone-camera filter applied: {output_path}")
    return output_path


# ── fal.ai Queue API ─────────────────────────────────────────────────

def fal_upload(file_path: str) -> str:
    """Upload a file to fal.ai storage, return the CDN URL."""
    import requests

    fal_key = os.environ["FAL_KEY"]
    filename = Path(file_path).name
    content_type = "image/png" if filename.endswith(".png") else \
                   "image/jpeg" if filename.endswith((".jpg", ".jpeg")) else \
                   "audio/mpeg" if filename.endswith(".mp3") else \
                   "application/octet-stream"

    # Initiate upload
    resp = requests.post(
        "https://rest.alpha.fal.ai/storage/upload/initiate",
        headers={"Authorization": f"Key {fal_key}", "Content-Type": "application/json"},
        json={"file_name": filename, "content_type": content_type},
    )
    resp.raise_for_status()
    data = resp.json()

    # Upload binary
    with open(file_path, "rb") as f:
        requests.put(data["upload_url"], data=f.read(),
                     headers={"Content-Type": content_type}).raise_for_status()

    return data["file_url"]


def fal_submit(model_id: str, payload: dict) -> dict:
    """Submit a job to fal.ai queue, return {request_id, status_url, response_url}."""
    import requests

    fal_key = os.environ["FAL_KEY"]
    resp = requests.post(
        f"https://queue.fal.run/{model_id}",
        headers={"Authorization": f"Key {fal_key}", "Content-Type": "application/json"},
        json=payload,
    )
    resp.raise_for_status()
    return resp.json()


def fal_poll(status_url: str, timeout: int = 600, interval: int = 10) -> dict:
    """Poll fal.ai job until complete or failed."""
    import requests

    fal_key = os.environ["FAL_KEY"]
    deadline = time.time() + timeout

    while time.time() < deadline:
        resp = requests.get(
            status_url,
            headers={"Authorization": f"Key {fal_key}"},
        )
        resp.raise_for_status()
        data = resp.json()
        status = data.get("status", "UNKNOWN")

        if status == "COMPLETED":
            return data
        elif status == "FAILED":
            raise RuntimeError(f"fal.ai job failed: {data}")

        print(f"    fal.ai: {status}...")
        time.sleep(interval)

    raise TimeoutError(f"fal.ai job timed out after {timeout}s")


def fal_fetch_result(response_url: str) -> dict:
    """Fetch the result of a completed fal.ai job."""
    import requests

    fal_key = os.environ["FAL_KEY"]
    resp = requests.get(
        response_url,
        headers={"Authorization": f"Key {fal_key}"},
    )
    resp.raise_for_status()
    return resp.json()


# ── ASS Caption Formatting ───────────────────────────────────────────

def format_ass_time(seconds: float) -> str:
    h = int(seconds // 3600)
    m = int((seconds % 3600) // 60)
    s = seconds % 60
    return f"{h}:{m:02d}:{s:05.2f}"


# ── R2 (Cloudflare) upload ───────────────────────────────────────────

def upload_to_r2(local_path: str, key: str | None = None) -> str | None:
    """Upload a file to the Cloudflare R2 bucket via rclone (S3 API).

    Reads credentials from the environment (loaded from .env or CI secrets):
      R2_ACCESS_KEY_ID, R2_SECRET_ACCESS_KEY, R2_ENDPOINT, R2_BUCKET
    Optional:
      R2_PUBLIC_BASE_URL  → if set, the public link is returned/printed.

    Returns the public URL (or the r2:// path if no public base is set),
    or None if R2 env vars are missing (upload is skipped, not fatal).
    Requires `rclone` on PATH.
    """
    bucket   = os.environ.get("R2_BUCKET")
    ak       = os.environ.get("R2_ACCESS_KEY_ID")
    sk       = os.environ.get("R2_SECRET_ACCESS_KEY")
    endpoint = os.environ.get("R2_ENDPOINT")
    if not all([bucket, ak, sk, endpoint]):
        print("  R2 upload skipped (R2_* env vars not set)")
        return None
    if shutil.which("rclone") is None:
        print("  R2 upload skipped (rclone not found on PATH)")
        return None

    key = key or Path(local_path).name
    # Configure an ephemeral rclone remote 'r2up' purely via env vars so no
    # secret ever touches the command line or a config file on disk.
    env = dict(
        os.environ,
        RCLONE_CONFIG_R2UP_TYPE="s3",
        RCLONE_CONFIG_R2UP_PROVIDER="Cloudflare",
        RCLONE_CONFIG_R2UP_ACCESS_KEY_ID=ak,
        RCLONE_CONFIG_R2UP_SECRET_ACCESS_KEY=sk,
        RCLONE_CONFIG_R2UP_ENDPOINT=endpoint,
    )
    # Flags for scoped R2 tokens (object read+write, no bucket-create):
    #   --s3-no-check-bucket  : don't attempt CreateBucket/HeadBucket
    #   --no-update-modtime   : skip the server-side CopyObject mtime update
    #   --s3-no-head          : skip the post-upload HEAD check
    subprocess.run(
        ["rclone", "copyto",
         "--s3-no-check-bucket", "--no-update-modtime", "--s3-no-head",
         str(local_path), f"r2up:{bucket}/{key}"],
        env=env, check=True,
    )
    base = (os.environ.get("R2_PUBLIC_BASE_URL") or "").rstrip("/")
    url = f"{base}/{key}" if base else f"r2://{bucket}/{key}"
    print(f"  uploaded to R2: {url}")
    return url


def download_from_r2(key: str, dest: str) -> str:
    """Download an object from the R2 bucket via the S3 API (rclone).

    Goes through the R2 S3 endpoint (NOT the public pub-*.r2.dev URL), so
    works for objects that don't need to be publicly fetchable — e.g.
    copyrighted music tracks we want to render with but not expose.

    Required env: R2_BUCKET, R2_ACCESS_KEY_ID, R2_SECRET_ACCESS_KEY,
                  R2_ENDPOINT. Requires `rclone` on PATH.

    Returns the local destination path. Raises on failure.
    """
    bucket   = os.environ.get("R2_BUCKET")
    ak       = os.environ.get("R2_ACCESS_KEY_ID")
    sk       = os.environ.get("R2_SECRET_ACCESS_KEY")
    endpoint = os.environ.get("R2_ENDPOINT")
    if not all([bucket, ak, sk, endpoint]):
        raise RuntimeError(
            "R2 env vars not set "
            "(R2_BUCKET / R2_ACCESS_KEY_ID / R2_SECRET_ACCESS_KEY / R2_ENDPOINT)"
        )
    if shutil.which("rclone") is None:
        raise RuntimeError("rclone not found on PATH")
    env = dict(
        os.environ,
        RCLONE_CONFIG_R2DL_TYPE="s3",
        RCLONE_CONFIG_R2DL_PROVIDER="Cloudflare",
        RCLONE_CONFIG_R2DL_ACCESS_KEY_ID=ak,
        RCLONE_CONFIG_R2DL_SECRET_ACCESS_KEY=sk,
        RCLONE_CONFIG_R2DL_ENDPOINT=endpoint,
    )
    subprocess.run(
        ["rclone", "copyto",
         "--s3-no-check-bucket", "--no-update-modtime", "--s3-no-head",
         f"r2dl:{bucket}/{key}", str(dest)],
        env=env, check=True,
    )
    return str(dest)
