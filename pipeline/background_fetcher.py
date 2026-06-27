import hashlib
import json
import random
import re
import urllib.parse
import urllib.request
from pathlib import Path

import config

AESTHETIC_QUERIES = [
    "aesthetic sunset landscape",
    "night drive neon city",
    "dreamy clouds sky",
    "ambient forest landscape",
    "ocean beach waves sunset",
    "starry night galaxy",
    "moody purple sky",
    "golden hour mountains",
    "rain window cinematic",
    "soft bokeh lights",
]


def normalize_url(url: str) -> str:
    """Canonical form so CDN/query variants dedupe correctly."""
    parsed = urllib.parse.urlparse(url.strip())
    return urllib.parse.urlunparse((parsed.scheme, parsed.netloc, parsed.path.rstrip("/"), "", "", ""))


def file_hash(path: Path) -> str:
    return hashlib.md5(path.read_bytes()).hexdigest()


def clean_query_term(text: str) -> str:
    cleaned = re.sub(r"[\(\[][^\]\)]*[\)\]]", "", text)
    cleaned = re.sub(r"[^\w\s]", "", cleaned)
    cleaned = re.sub(r"\s+", " ", cleaned).strip()
    return cleaned


def _build_queries(title: str, artist: str, attempt: int) -> list[str]:
    clean_title = clean_query_term(title)
    clean_artist = clean_query_term(artist)

    song_queries: list[str] = []
    if clean_title and clean_artist:
        song_queries.append(f"{clean_artist} {clean_title}")
    if clean_title:
        song_queries.append(clean_title)
    if clean_artist:
        song_queries.append(clean_artist)

    aesthetics = AESTHETIC_QUERIES.copy()
    random.shuffle(aesthetics)

    if attempt == 0:
        return song_queries + aesthetics

    # On retry, search broader aesthetics first so results diverge from the first pick.
    offset = attempt % len(aesthetics)
    rotated = aesthetics[offset:] + aesthetics[:offset]
    return rotated + song_queries


def search_pexels(query: str, page: int = 1, per_page: int = 15) -> list[str]:
    if not config.PEXELS_API_KEY:
        return []
    try:
        url = (
            f"https://api.pexels.com/v1/search?query={urllib.parse.quote(query)}"
            f"&orientation=landscape&per_page={per_page}&page={page}"
        )
        req = urllib.request.Request(url)
        req.add_header("Authorization", config.PEXELS_API_KEY)
        with urllib.request.urlopen(req, timeout=10) as response:
            data = json.loads(response.read().decode())
            urls = []
            for photo in data.get("photos", []):
                src = photo.get("src", {})
                img = src.get("large2x") or src.get("original") or src.get("large")
                if img:
                    urls.append(img)
            return urls
    except Exception as e:
        print(f"Pexels search failed: {e}")
    return []


def search_pixabay(query: str, page: int = 1, per_page: int = 20) -> list[str]:
    if not config.PIXABAY_API_KEY:
        return []
    try:
        url = (
            f"https://pixabay.com/api/?key={config.PIXABAY_API_KEY}"
            f"&q={urllib.parse.quote(query)}&image_type=photo"
            f"&orientation=horizontal&per_page={per_page}&page={page}"
        )
        with urllib.request.urlopen(url, timeout=10) as response:
            data = json.loads(response.read().decode())
            urls = []
            for hit in data.get("hits", []):
                img = hit.get("largeImageURL") or hit.get("webformatURL")
                if img:
                    urls.append(img)
            return urls
    except Exception as e:
        print(f"Pixabay search failed: {e}")
    return []


def _collect_candidates(queries: list[str], attempt: int, exclude_urls: set[str]) -> list[str]:
    candidates: list[str] = []
    seen_normalized: set[str] = set(exclude_urls)
    pages = [1 + (attempt % 3), 2 + (attempt % 2), 3 + (attempt % 2)]

    for q in queries:
        print(f"Searching background image for query: '{q}'")
        for page in pages:
            for url in search_pexels(q, page=page):
                norm = normalize_url(url)
                if norm not in seen_normalized and norm not in candidates:
                    candidates.append(url)
                    seen_normalized.add(norm)
            for url in search_pixabay(q, page=page):
                norm = normalize_url(url)
                if norm not in seen_normalized and norm not in candidates:
                    candidates.append(url)
                    seen_normalized.add(norm)
        if len(candidates) >= 12:
            break

    # Rotate start index per attempt so retries don't reshuffle back to the same first pick.
    if not candidates:
        return []
    start = attempt % len(candidates)
    return candidates[start:] + candidates[:start]


def _download_image(img_url: str, out_path: Path) -> bool:
    try:
        print(f"Downloading background from: {img_url}")
        req = urllib.request.Request(img_url, headers={"User-Agent": "Mozilla/5.0"})
        with urllib.request.urlopen(req, timeout=15) as response:
            out_path.write_bytes(response.read())
        return True
    except Exception as e:
        print(f"Failed to download image from {img_url}: {e}")
        return False


def run(
    title: str,
    artist: str,
    *,
    attempt: int = 0,
    exclude_urls: list[str] | None = None,
    exclude_hashes: list[str] | None = None,
    job_id: int | None = None,
) -> tuple[Path | None, str | None]:
    """
    Fetch a background image. Returns (local_path, source_url).

    attempt: increments on each 'New Image' retry — widens search and rotates queries.
    exclude_urls: image URLs already shown for this job (never repeat).
    exclude_hashes: MD5 hashes of images already shown (catches same photo, different URL).
    """
    exclude = {normalize_url(u) for u in (exclude_urls or [])}
    blocked_hashes = set(exclude_hashes or [])
    queries = _build_queries(title, artist, attempt)
    candidates = _collect_candidates(queries, attempt, exclude)

    if not candidates:
        print("No new background candidates found.")
        return None, None

    if job_id:
        out_path = config.TEMP_DIR / f"job_{job_id}_background.jpg"
    else:
        out_path = config.TEMP_DIR / "downloaded_background.jpg"

    for img_url in candidates:
        if not _download_image(img_url, out_path):
            continue
        content_hash = file_hash(out_path)
        if content_hash in blocked_hashes:
            print(f"Skipping duplicate image (hash {content_hash[:8]}…) from {img_url}")
            continue
        return out_path, img_url

    return None, None