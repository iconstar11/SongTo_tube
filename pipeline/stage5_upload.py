import os
import pickle
from pathlib import Path

from google_auth_oauthlib.flow import InstalledAppFlow
from googleapiclient.discovery import build
from googleapiclient.http import MediaFileUpload

from config import ENABLE_YOUTUBE_UPLOAD, YOUTUBE_CLIENT_SECRETS, BASE_DIR

SCOPES = ["https://www.googleapis.com/auth/youtube.upload"]


def youtube_configured() -> bool:
    secrets = BASE_DIR / YOUTUBE_CLIENT_SECRETS
    return ENABLE_YOUTUBE_UPLOAD and secrets.exists()


def get_authenticated_service():
    creds = None
    token_file = BASE_DIR / "token.pickle"

    if token_file.exists():
        with open(token_file, "rb") as token:
            creds = pickle.load(token)

    if not creds or not creds.valid:
        secrets = BASE_DIR / YOUTUBE_CLIENT_SECRETS
        if not secrets.exists():
            raise FileNotFoundError(f"Missing {YOUTUBE_CLIENT_SECRETS}")

        flow = InstalledAppFlow.from_client_secrets_file(str(secrets), SCOPES)
        creds = flow.run_local_server(port=0)

        with open(token_file, "wb") as token:
            pickle.dump(creds, token)

    return build("youtube", "v3", credentials=creds)


def run(video_path, info) -> dict:
    """
    Upload video to YouTube.

    Returns:
        {"ok": True, "video_id": "..."}
        {"ok": False, "skipped": True, "reason": "..."}
        {"ok": False, "skipped": False, "reason": "..."}
    """
    if not ENABLE_YOUTUBE_UPLOAD:
        return {"ok": False, "skipped": True, "reason": "disabled"}

    secrets = BASE_DIR / YOUTUBE_CLIENT_SECRETS
    if not secrets.exists():
        return {"ok": False, "skipped": True, "reason": "missing_credentials"}

    try:
        youtube = get_authenticated_service()
    except Exception as e:
        return {"ok": False, "skipped": False, "reason": str(e)}

    title = f"{info['artist']} - {info['title']} (Lyrics)"
    description = f"Lyric video for {info['title']} by {info['artist']}.\n\nOriginal: {info['url']}"

    body = {
        "snippet": {
            "title": title[:100],
            "description": description,
            "tags": ["lyrics", "lyric video", info["artist"], info["title"]],
            "categoryId": "10",
        },
        "status": {
            "privacyStatus": "public",
            "selfDeclaredMadeForKids": False,
        },
    }

    media = MediaFileUpload(
        str(video_path),
        chunksize=-1,
        resumable=True,
        mimetype="video/mp4",
    )

    request = youtube.videos().insert(
        part="snippet,status",
        body=body,
        media_body=media,
    )

    print(f"Uploading {video_path}...")
    response = None
    while response is None:
        status, response = request.next_chunk()
        if status:
            print(f"Uploaded {int(status.progress() * 100)}%")

    video_id = response["id"]
    print(f"Upload complete! Video ID: {video_id}")
    return {"ok": True, "video_id": video_id}