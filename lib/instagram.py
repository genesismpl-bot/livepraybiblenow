"""Instagram Graph API client (Instagram Login flow) for posting Reels.

Endpoints used (all under graph.instagram.com — the IG Login flow API):
  POST /{ig-user-id}/media         → create a media container
  GET  /{container-id}             → poll container status
  POST /{ig-user-id}/media_publish → publish a FINISHED container
  GET  /refresh_access_token       → refresh the long-lived token

Required env vars (loaded from .env or CI secrets):
  INSTAGRAM_LONG_LIVED_TOKEN
  IG_USER_ID

The token is the 60-day long-lived token issued by the "Add account" flow
on the Instagram API setup screen. IG_USER_ID is the app-scoped user id
returned by GET /me (NOT the same as your Instagram username).
"""
from __future__ import annotations

import os
import time

import requests

API_BASE = "https://graph.instagram.com/v21.0"
REFRESH_BASE = "https://graph.instagram.com"


# ── helpers ──────────────────────────────────────────────────────────
def _token() -> str:
    t = os.environ.get("INSTAGRAM_LONG_LIVED_TOKEN", "").strip()
    if not t:
        raise RuntimeError("INSTAGRAM_LONG_LIVED_TOKEN is not set in env")
    return t


def _ig_user_id() -> str:
    u = os.environ.get("IG_USER_ID", "").strip()
    if not u:
        raise RuntimeError("IG_USER_ID is not set in env")
    return u


def _raise(r: requests.Response) -> None:
    """raise_for_status with the response body included (Meta puts the
    useful error there, not in the HTTP status line)."""
    if r.status_code >= 400:
        raise RuntimeError(f"IG API {r.status_code}: {r.text[:500]}")


# ── public API ───────────────────────────────────────────────────────
def create_reel_container(video_url: str, caption: str,
                          share_to_feed: bool = True) -> str:
    """Create a REELS media container. Returns the container_id (str).

    `video_url` must be a publicly-accessible HTTPS URL to an .mp4
    (1080×1920 ≤ 90s ≤ 1GB, H.264 + AAC). Meta fetches the file from
    this URL — it isn't uploaded through the API.
    """
    r = requests.post(
        f"{API_BASE}/{_ig_user_id()}/media",
        params={
            "media_type":     "REELS",
            "video_url":      video_url,
            "caption":        caption,
            "share_to_feed":  "true" if share_to_feed else "false",
            "access_token":   _token(),
        },
        timeout=60,
    )
    _raise(r)
    cid = r.json()["id"]
    print(f"  IG container created: {cid}")
    return cid


def get_container_status(container_id: str) -> dict:
    r = requests.get(
        f"{API_BASE}/{container_id}",
        params={"fields": "status_code,status", "access_token": _token()},
        timeout=20,
    )
    _raise(r)
    return r.json()


def wait_for_container(container_id: str, timeout: int = 600,
                       interval: int = 10) -> dict:
    """Poll until status_code == FINISHED (or terminal error)."""
    deadline = time.time() + timeout
    while time.time() < deadline:
        s = get_container_status(container_id)
        sc = s.get("status_code")
        print(f"  container {container_id}: {sc}")
        if sc == "FINISHED":
            return s
        if sc in ("ERROR", "EXPIRED"):
            raise RuntimeError(f"container {container_id} failed: {s}")
        time.sleep(interval)
    raise TimeoutError(f"container {container_id} not ready in {timeout}s")


def publish_container(container_id: str) -> str:
    """Publish a FINISHED container. Returns the published media_id."""
    r = requests.post(
        f"{API_BASE}/{_ig_user_id()}/media_publish",
        params={"creation_id": container_id, "access_token": _token()},
        timeout=60,
    )
    _raise(r)
    mid = r.json()["id"]
    print(f"  IG published: media_id={mid}")
    return mid


def post_reel(video_url: str, caption: str, dry_run: bool = False) -> dict:
    """End-to-end: create container → wait FINISHED → publish.

    With dry_run=True, stops after the container is FINISHED (so you can
    verify Meta accepted the file without actually posting publicly).
    """
    cid = create_reel_container(video_url, caption)
    wait_for_container(cid)
    if dry_run:
        print(f"  DRY RUN: not publishing container {cid}")
        return {"container_id": cid, "published": False}
    mid = publish_container(cid)
    return {"container_id": cid, "media_id": mid, "published": True}


def refresh_long_lived_token() -> dict:
    """Refresh the long-lived token (extends another 60 days).

    Returns {access_token, token_type, expires_in}. The caller is
    responsible for persisting the new access_token. The token must be
    at least 24 hours old before it can be refreshed.
    """
    r = requests.get(
        f"{REFRESH_BASE}/refresh_access_token",
        params={"grant_type": "ig_refresh_token", "access_token": _token()},
        timeout=20,
    )
    _raise(r)
    return r.json()
