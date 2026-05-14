#!/usr/bin/env python3
"""Upload a file (typically a rendered MP4 from output/) to Google Drive.

Auth resolution order:
  1. GDRIVE_SA_KEY env var holding the service-account JSON (CI path).
  2. GOOGLE_APPLICATION_CREDENTIALS env var pointing at a JSON file (local path).
  3. ./secrets/gdrive-sa.json relative to the repo root (local fallback).

Target folder:
  GDRIVE_FOLDER_ID env var, or --folder on the command line.

Usage:
  python scripts/upload_to_drive.py output/behind/final.mp4
  python scripts/upload_to_drive.py output/behind/final.mp4 --folder <folderId>
  python scripts/upload_to_drive.py output/behind/final.mp4 --name custom_name.mp4
"""

from __future__ import annotations

import argparse
import json
import mimetypes
import os
import sys
from pathlib import Path

from google.oauth2 import service_account
from googleapiclient.discovery import build
from googleapiclient.http import MediaFileUpload

SCOPES = ["https://www.googleapis.com/auth/drive.file"]
REPO_ROOT = Path(__file__).resolve().parent.parent


def load_credentials() -> service_account.Credentials:
    raw = os.environ.get("GDRIVE_SA_KEY")
    if raw:
        try:
            info = json.loads(raw)
        except json.JSONDecodeError as e:
            sys.exit(f"GDRIVE_SA_KEY is set but not valid JSON: {e}")
        return service_account.Credentials.from_service_account_info(info, scopes=SCOPES)

    path = os.environ.get("GOOGLE_APPLICATION_CREDENTIALS")
    if not path:
        fallback = REPO_ROOT / "secrets" / "gdrive-sa.json"
        if fallback.exists():
            path = str(fallback)

    if not path:
        sys.exit(
            "No credentials found. Set GDRIVE_SA_KEY (JSON content) or "
            "GOOGLE_APPLICATION_CREDENTIALS (path to JSON), or place the key "
            "at secrets/gdrive-sa.json."
        )
    if not Path(path).exists():
        sys.exit(f"Service-account key not found at: {path}")
    return service_account.Credentials.from_service_account_file(path, scopes=SCOPES)


def upload(file_path: Path, folder_id: str, display_name: str | None) -> dict:
    creds = load_credentials()
    service = build("drive", "v3", credentials=creds, cache_discovery=False)

    mime_type, _ = mimetypes.guess_type(file_path.name)
    if mime_type is None:
        mime_type = "application/octet-stream"

    media = MediaFileUpload(str(file_path), mimetype=mime_type, resumable=True)
    metadata = {
        "name": display_name or file_path.name,
        "parents": [folder_id],
    }
    request = service.files().create(
        body=metadata,
        media_body=media,
        fields="id, name, webViewLink, size",
        supportsAllDrives=True,
    )

    response = None
    while response is None:
        status, response = request.next_chunk()
        if status:
            pct = int(status.progress() * 100)
            print(f"  uploading… {pct}%", file=sys.stderr)
    return response


def main() -> None:
    parser = argparse.ArgumentParser(description="Upload a file to Google Drive.")
    parser.add_argument("file", type=Path, help="Path to the file to upload.")
    parser.add_argument(
        "--folder",
        default=os.environ.get("GDRIVE_FOLDER_ID"),
        help="Drive folder ID (defaults to $GDRIVE_FOLDER_ID).",
    )
    parser.add_argument(
        "--name",
        default=None,
        help="Override the filename shown in Drive (defaults to local filename).",
    )
    args = parser.parse_args()

    if not args.folder:
        sys.exit("No folder specified. Pass --folder or set GDRIVE_FOLDER_ID.")
    if not args.file.exists():
        sys.exit(f"File not found: {args.file}")

    result = upload(args.file, args.folder, args.name)
    print(
        f"Uploaded {result['name']} ({int(result.get('size', 0)):,} bytes)\n"
        f"  id:   {result['id']}\n"
        f"  link: {result.get('webViewLink', '(no link)')}"
    )


if __name__ == "__main__":
    main()
