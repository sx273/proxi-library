"""FastAPI dashboard that wraps lucidadl's search and download core."""

from __future__ import annotations

import asyncio
import difflib
import json
import logging
import os
import re
import shutil
import sqlite3
import time
import uuid
from pathlib import Path
from typing import Any
from urllib.parse import urlparse

from fastapi import FastAPI, HTTPException
from fastapi.responses import FileResponse, Response
from fastapi.staticfiles import StaticFiles
import httpx
from pydantic import BaseModel, Field

from lucidadl import api as lucida_api
from lucidadl import downloader, organize, paths, utils
from lucidadl.api import LucidaClient
from lucidadl.session import (
    acquire_clearance,
    chromium_installed,
    install_chromium,
    load_clearance,
)


APP_DIR = Path(__file__).resolve().parent
DATA_DIR = Path(os.environ.get("DASHBOARD_DATA", "/data/dashboard"))
MUSIC_ROOT = Path(os.environ.get("MUSIC_ROOT", "/music")).resolve()
SETTINGS_FILE = DATA_DIR / "settings.json"
DATABASE_FILE = DATA_DIR / "dashboard.sqlite3"
ALLOWED_SERVICES = {"amazon", "qobuz", "grilledcheese"}
CANDIDATE_TTL_SECONDS = 15 * 60

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s  %(levelname)-8s  %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S",
)
logger = logging.getLogger("proxi-library")

app = FastAPI(title="Proxi Library")
app.mount("/static", StaticFiles(directory=APP_DIR / "static"), name="static")


@app.on_event("startup")
async def _startup_checks() -> None:
    """Run health checks at boot and log every component's status."""
    logger.info("=" * 56)
    logger.info("  PROXI LIBRARY — starting up")
    logger.info("=" * 56)

    # 1. Xvfb / virtual display
    display = os.environ.get("DISPLAY", "")
    xvfb_bin = shutil.which("Xvfb") or shutil.which("xvfb")
    xauth_bin = shutil.which("xauth")
    if display:
        logger.info("✔  Virtual display          : %s", display)
    else:
        logger.warning("⚠  DISPLAY not set (Xvfb may not be running)")
    if xvfb_bin:
        logger.info("✔  Xvfb found               : %s", xvfb_bin)
    else:
        logger.warning("⚠  Xvfb not found in PATH")
    if xauth_bin:
        logger.info("✔  xauth found              : %s", xauth_bin)
    else:
        logger.error("✖  xauth NOT found — xvfb-run will fail")

    # 2. Data directories
    try:
        DATA_DIR.mkdir(parents=True, exist_ok=True)
        logger.info("✔  Data directory           : %s", DATA_DIR)
    except OSError as exc:
        logger.error("✖  Data directory           : %s — %s", DATA_DIR, exc)

    # 3. Music directory
    try:
        MUSIC_ROOT.mkdir(parents=True, exist_ok=True)
        logger.info("✔  Music directory          : %s", MUSIC_ROOT)
    except OSError as exc:
        logger.error("✖  Music directory          : %s — %s", MUSIC_ROOT, exc)

    # 4. Database
    try:
        _ensure_storage()
        logger.info("✔  SQLite database          : %s", DATABASE_FILE)
    except Exception as exc:
        logger.error("✖  SQLite database          : %s", exc)

    # 5. Chromium (Playwright)
    try:
        installed = await chromium_installed()
        if installed:
            logger.info("✔  Chromium (Playwright)    : installed")
        else:
            logger.warning("⚠  Chromium (Playwright)    : not installed — will be installed on first use")
    except Exception as exc:
        logger.warning("⚠  Chromium (Playwright)    : check failed — %s", exc)

    # 6. lucida.to session
    try:
        _clearance, user_agent = load_clearance()
        if user_agent:
            logger.info("✔  lucida.to session        : ready")
        else:
            logger.info("⚬  lucida.to session        : not prepared (use the button in the dashboard)")
    except Exception as exc:
        logger.warning("⚠  lucida.to session        : check failed — %s", exc)

    # 7. Settings
    try:
        settings = _read_settings()
        logger.info("✔  Default source           : %s", settings["service"])
    except Exception as exc:
        logger.warning("⚠  Settings                 : %s", exc)

    logger.info("-" * 56)
    logger.info("  Dashboard available at http://0.0.0.0:6067")
    logger.info("=" * 56)

_candidates: dict[str, dict[str, Any]] = {}
_jobs: dict[str, dict[str, Any]] = {}
_jobs_lock = asyncio.Lock()
_download_lock = asyncio.Lock()
_artwork_cache: dict[tuple[str, str], list[dict[str, Any]]] = {}


class SearchRequest(BaseModel):
    """Validate the user's search query."""

    query: str = Field(min_length=2, max_length=200)


class SettingsRequest(BaseModel):
    """Validate the provider and library subfolder settings."""

    service: str
    music_folder: str = Field(max_length=400)


class DownloadRequest(BaseModel):
    """Validate the requested track numbers for a confirmed result."""

    track_numbers: list[int] | None = None


def _ensure_storage() -> None:
    """Create persistent directories and the recent-downloads table."""
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    MUSIC_ROOT.mkdir(parents=True, exist_ok=True)
    with sqlite3.connect(DATABASE_FILE) as connection:
        connection.execute(
            """CREATE TABLE IF NOT EXISTS downloads (
                id TEXT PRIMARY KEY,
                title TEXT NOT NULL,
                artist TEXT NOT NULL,
                album TEXT NOT NULL,
                kind TEXT NOT NULL,
                service TEXT NOT NULL,
                files TEXT NOT NULL,
                artwork_url TEXT NOT NULL DEFAULT '',
                created_at INTEGER NOT NULL
            )"""
        )
        columns = {row[1] for row in connection.execute("PRAGMA table_info(downloads)")}
        if "artwork_url" not in columns:
            connection.execute(
                "ALTER TABLE downloads ADD COLUMN artwork_url TEXT NOT NULL DEFAULT ''"
            )


def _read_settings() -> dict[str, str]:
    """Load settings, falling back to Amazon Music and the library root."""
    _ensure_storage()
    try:
        value = json.loads(SETTINGS_FILE.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        value = {}
    if not isinstance(value, dict):
        value = {}
    service = value.get("service", "amazon")
    if service not in ALLOWED_SERVICES:
        service = "amazon"
    music_folder = str(value.get("music_folder", "")).strip()
    return {"service": service, "music_folder": music_folder}


def _music_directory(music_folder: str) -> Path:
    """Resolve a configured destination while keeping it inside /music."""
    requested = Path(music_folder).expanduser()
    if requested.is_absolute():
        destination = requested.resolve()
    else:
        destination = (MUSIC_ROOT / requested).resolve()
    if not destination.is_relative_to(MUSIC_ROOT):
        raise HTTPException(status_code=422, detail="La cartella deve restare dentro il volume /music.")
    return destination


def _write_settings(settings: dict[str, str]) -> None:
    """Atomically persist validated dashboard settings."""
    SETTINGS_FILE.parent.mkdir(parents=True, exist_ok=True)
    temporary = SETTINGS_FILE.with_suffix(".tmp")
    temporary.write_text(json.dumps(settings, ensure_ascii=False, indent=2), encoding="utf-8")
    temporary.replace(SETTINGS_FILE)


def _prune_candidates() -> None:
    """Discard expired search selections and their short-lived provider data."""
    now = time.time()
    expired = [key for key, value in _candidates.items() if value["expires_at"] < now]
    for key in expired:
        _candidates.pop(key, None)


async def _open_client(service: str) -> tuple[LucidaClient, str]:
    """Open lucidadl's HTTP client using its persisted Cloudflare session."""
    clearance, user_agent = load_clearance()
    if not user_agent:
        raise HTTPException(
            status_code=409,
            detail="Prepara prima la sessione lucida.to dalla dashboard.",
        )
    country = lucida_api.default_country(service)
    client = LucidaClient(
        clearance,
        user_agent,
        acquire=lambda: acquire_clearance(hidden=True),
        country=country,
        downscale="original",
        metadata=True,
        jobs=3,
        log=lambda _message: None,
    )
    await client.start_http()
    return client, country


async def _fetch_item_page(
    client: LucidaClient, item_url: str, country: str
) -> tuple[dict[str, Any], str]:
    """Fetch item metadata and apply lucidadl's Amazon region fallback."""
    host = (urlparse(item_url).hostname or "").lower()
    if not host.startswith("music.amazon."):
        return await client.fetch_page_data(item_url, country), country

    last_error: Exception | None = None
    for region in ("", "US", "GB", "JP"):
        try:
            page = await client.fetch_page_data(item_url, region)
            info = page.get("info") or {}
            tracks = client.tracks_from_pd(page)
            if (
                info.get("type") not in ("track", "album")
                or page.get("tokenExpiry") is None
                or not any(track.get("url") and track.get("csrf") for track in tracks)
            ):
                raise lucida_api.LucidaError("Amazon Music non ha restituito tracce disponibili.")
            return page, region
        except Exception as exc:
            last_error = exc
    if last_error:
        raise last_error
    raise lucida_api.LucidaError("Impossibile leggere i dettagli del risultato Amazon Music.")


def _artists(value: Any) -> str:
    """Format the artist objects returned by lucidadl as a readable string."""
    if isinstance(value, str):
        return value
    if isinstance(value, list):
        return ", ".join(
            str(item.get("name", ""))
            for item in value
            if isinstance(item, dict) and item.get("name")
        )
    return ""


def _normalized_text(value: str) -> str:
    """Normalize catalog text before comparing artwork metadata."""
    return " ".join(
        "".join(character.casefold() if character.isalnum() else " " for character in value).split()
    )


def _match_artwork(candidate: dict[str, Any], catalog: list[dict[str, Any]], kind: str) -> str:
    """Choose artwork only when both the catalog title and artist match confidently."""
    candidate_title = _normalized_text(str(candidate.get("title") or ""))
    candidate_artist = _normalized_text(str(candidate.get("artist") or ""))
    best_score = 0.0
    best_url = ""
    title_key = "collectionName" if kind == "album" else "trackName"

    for entry in catalog:
        title = _normalized_text(str(entry.get(title_key) or ""))
        artist = _normalized_text(str(entry.get("artistName") or ""))
        if not title or not candidate_title:
            continue
        title_score = difflib.SequenceMatcher(None, candidate_title, title).ratio()
        artist_score = difflib.SequenceMatcher(None, candidate_artist, artist).ratio()
        if candidate_artist and (candidate_artist in artist or artist in candidate_artist):
            artist_score = max(artist_score, 0.95)
        if title_score < 0.88 or (candidate_artist and artist_score < 0.82):
            continue
        score = title_score * 0.65 + artist_score * 0.35
        artwork_url = str(entry.get("artworkUrl100") or "")
        if (
            score > best_score
            and _is_artwork_url(artwork_url)
        ):
            best_score = score
            if (urlparse(artwork_url).hostname or "").lower().endswith("mzstatic.com"):
                artwork_url = re.sub(r"/\d+x\d+bb(?=\.)", "/400x400bb", artwork_url)
            best_url = artwork_url
    return best_url


async def _fetch_artwork_catalog(query: str, kind: str) -> list[dict[str, Any]]:
    """Fetch one small artwork search result page for a result category."""
    cache_key = (query.casefold(), kind)
    if cache_key in _artwork_cache:
        return _artwork_cache[cache_key]
    entity = "album" if kind == "album" else "song"
    try:
        async with httpx.AsyncClient(
            timeout=8,
            headers={"User-Agent": "ProxiLibrary/1.0 (artwork lookup)"},
        ) as client:
            response = await client.get(
                "https://itunes.apple.com/search",
                params={"term": query, "entity": entity, "limit": 50},
            )
            response.raise_for_status()
            results = response.json().get("results", [])
            if not isinstance(results, list):
                results = []
    except (httpx.HTTPError, ValueError, AttributeError):
        results = []
    if results:
        if len(_artwork_cache) >= 100:
            _artwork_cache.pop(next(iter(_artwork_cache)))
        _artwork_cache[cache_key] = results
    return results


async def _fetch_deezer_artwork_catalog(query: str, kind: str) -> list[dict[str, Any]]:
    """Fetch title, artist, and cover metadata from Deezer's public catalog API."""
    cache_key = (f"deezer:{query.casefold()}", kind)
    if cache_key in _artwork_cache:
        return _artwork_cache[cache_key]
    entity = "album" if kind == "album" else "track"
    try:
        async with httpx.AsyncClient(timeout=8) as client:
            response = await client.get(
                f"https://api.deezer.com/search/{entity}",
                params={"q": query, "limit": 50},
            )
            response.raise_for_status()
            data = response.json().get("data", [])
    except (httpx.HTTPError, ValueError, AttributeError):
        data = []
    results = []
    if isinstance(data, list):
        for item in data:
            artist = item.get("artist") if isinstance(item.get("artist"), dict) else {}
            album = item.get("album") if isinstance(item.get("album"), dict) else {}
            results.append(
                {
                    "collectionName": item.get("title", "") if kind == "album" else album.get("title", ""),
                    "trackName": item.get("title", "") if kind == "track" else "",
                    "artistName": artist.get("name", ""),
                    "artworkUrl100": (item.get("cover_xl") or item.get("cover_big"))
                    if kind == "album"
                    else (album.get("cover_xl") or album.get("cover_big")),
                }
            )
    if results:
        if len(_artwork_cache) >= 100:
            _artwork_cache.pop(next(iter(_artwork_cache)))
        _artwork_cache[cache_key] = results
    return results


async def _candidate_artwork(
    item: dict[str, Any],
    kind: str,
    query: str,
    albums: list[dict[str, Any]],
    tracks: list[dict[str, Any]],
    fallback_limit: asyncio.Semaphore,
) -> str:
    """Find artwork from broad results, album metadata, then a targeted lookup."""
    artwork = _match_artwork(item, albums if kind == "album" else tracks, kind)
    album_title = str(item.get("album") or "").strip()
    artist = str(item.get("artist") or "").strip()
    if not artwork and kind == "track" and album_title:
        artwork = _match_artwork(
            {"title": album_title, "artist": artist}, albums, "album"
        )
    if artwork:
        return artwork

    title = str(item.get("title") or "").strip()
    specific_query = " ".join(part for part in (title, artist) if part)
    if specific_query and _normalized_text(specific_query) != _normalized_text(query):
        async with fallback_limit:
            catalog = await _fetch_artwork_catalog(specific_query, kind)
        artwork = _match_artwork(item, catalog, kind)
    if not artwork and kind == "track" and album_title:
        album_query = " ".join(part for part in (album_title, artist) if part)
        if album_query and _normalized_text(album_query) != _normalized_text(query):
            async with fallback_limit:
                catalog = await _fetch_artwork_catalog(album_query, "album")
            artwork = _match_artwork(
                {"title": album_title, "artist": artist}, catalog, "album"
            )
    if not artwork:
        deezer_query = specific_query or query or title
        if deezer_query:
            async with fallback_limit:
                catalog = await _fetch_deezer_artwork_catalog(deezer_query, kind)
            artwork = _match_artwork(item, catalog, kind)
    if not artwork and kind == "track" and album_title:
        album_query = " ".join(part for part in (album_title, artist) if part)
        if album_query:
            async with fallback_limit:
                catalog = await _fetch_deezer_artwork_catalog(album_query, "album")
            artwork = _match_artwork(
                {"title": album_title, "artist": artist}, catalog, "album"
            )
    return artwork


def _is_artwork_url(url: str) -> bool:
    """Allow only HTTPS artwork images hosted by Apple or Deezer image CDNs."""
    parsed = urlparse(url)
    host = (parsed.hostname or "").lower()
    approved_host = (
        host == "mzstatic.com"
        or host.endswith(".mzstatic.com")
        or host == "cdn-images.dzcdn.net"
    )
    return parsed.scheme == "https" and approved_host


def _save_download(
    title: str,
    artist: str,
    album: str,
    kind: str,
    service: str,
    files: list[str],
    artwork_url: str,
) -> None:
    """Store a completed download for the dashboard's recent-items list."""
    with sqlite3.connect(DATABASE_FILE) as connection:
        connection.execute(
            """INSERT INTO downloads
                    (id, title, artist, album, kind, service, files, artwork_url, created_at)
                    VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)""",
            (
                str(uuid.uuid4()),
                title,
                artist,
                album,
                kind,
                service,
                json.dumps(files, ensure_ascii=False),
                artwork_url,
                int(time.time()),
            ),
        )


class JobReporter:
    """Capture lucidadl download messages and per-track progress for polling."""

    def __init__(self, job: dict[str, Any]) -> None:
        """Attach the reporter to one in-memory dashboard job."""
        self.job = job

    def log(self, message: object = "") -> None:
        """Append a human-readable event to the job log."""
        self.job["logs"].append(str(message))

    def start(self, key: str, label: str) -> None:
        """Record that a track has entered the download queue."""
        self.job["tracks"][key] = {"label": label, "status": "In preparazione", "progress": None}

    def status(self, key: str, message: str) -> None:
        """Update the current provider status for a track."""
        if key in self.job["tracks"]:
            self.job["tracks"][key]["status"] = message

    def progress(self, key: str, downloaded: int, total: int | None) -> None:
        """Expose byte-level transfer progress when lucidadl provides it."""
        if key in self.job["tracks"]:
            percent = round(downloaded * 100 / total) if total else None
            self.job["tracks"][key]["progress"] = percent

    def finish(self, key: str, ok: bool, line: str) -> None:
        """Mark a track complete or failed and retain lucidadl's message."""
        if key in self.job["tracks"]:
            self.job["tracks"][key]["status"] = "Completata" if ok else "Errore"
            self.job["tracks"][key]["progress"] = 100 if ok else None
        self.log(line)

    def close(self) -> None:
        """Satisfy lucidadl's reporter contract; no resources need closing."""


@app.get("/")
async def dashboard() -> FileResponse:
    """Serve the single-page music dashboard."""
    return FileResponse(APP_DIR / "static" / "index.html")


@app.get("/api/settings")
async def get_settings() -> dict[str, str]:
    """Return current provider and destination settings."""
    settings = _read_settings()
    destination = _music_directory(settings["music_folder"])
    return {
        **settings,
        "music_root": str(MUSIC_ROOT),
        "music_directory": str(destination),
    }


@app.put("/api/settings")
async def update_settings(body: SettingsRequest) -> dict[str, str]:
    """Validate and save a provider plus a destination under /music."""
    if body.service not in ALLOWED_SERVICES:
        raise HTTPException(status_code=422, detail="Sorgente non supportata da lucidadl.")
    folder = body.music_folder.strip()
    destination = _music_directory(folder)
    folder = str(destination.relative_to(MUSIC_ROOT))
    if folder == ".":
        folder = ""
    try:
        destination.mkdir(parents=True, exist_ok=True)
        probe = destination / f".lucida-write-check-{uuid.uuid4().hex}"
        probe.write_text("ok", encoding="utf-8")
        probe.unlink()
    except OSError as exc:
        raise HTTPException(status_code=422, detail=f"Cartella non scrivibile: {exc}") from exc
    settings = {"service": body.service, "music_folder": folder}
    _write_settings(settings)
    return await get_settings()


@app.get("/api/access")
async def access_status() -> dict[str, bool]:
    """Report whether lucidadl has a saved browser session."""
    _clearance, user_agent = load_clearance()
    return {"ready": bool(user_agent)}


@app.get("/api/artwork")
async def artwork_image(url: str) -> Response:
    """Proxy approved catalog artwork so browsers need no cross-origin access."""
    if not _is_artwork_url(url):
        raise HTTPException(status_code=422, detail="Host copertina non consentito.")
    try:
        async with httpx.AsyncClient(timeout=8, follow_redirects=False) as client:
            response = await client.get(url)
    except httpx.HTTPError as exc:
        raise HTTPException(status_code=502, detail="Copertina non raggiungibile.") from exc
    media_type = response.headers.get("content-type", "").split(";", 1)[0].lower()
    if response.status_code != 200 or not media_type.startswith("image/"):
        raise HTTPException(status_code=404, detail="Copertina non disponibile.")
    if len(response.content) > 5 * 1024 * 1024:
        raise HTTPException(status_code=413, detail="Copertina troppo grande.")
    return Response(
        content=response.content,
        media_type=media_type,
        headers={"Cache-Control": "public, max-age=604800"},
    )


@app.post("/api/access/prepare")
async def prepare_access() -> dict[str, str]:
    """Run lucidadl's headed Chromium clearance flow inside the Xvfb display."""
    try:
        if not await chromium_installed() and not await install_chromium():
            raise RuntimeError("Chromium non è stato installato. Riprova dalla dashboard.")
        await acquire_clearance(hidden=True)
    except Exception as exc:
        raise HTTPException(
            status_code=502,
            detail=f"Preparazione sessione non riuscita: {exc}",
        ) from exc
    return {"message": "Sessione lucida.to preparata."}


@app.post("/api/search")
async def search(body: SearchRequest) -> dict[str, Any]:
    """Search tracks and albums, returning opaque IDs for safe preview steps."""
    settings = _read_settings()
    service = settings["service"]
    query = body.query.strip()
    if len(query) < 2:
        raise HTTPException(status_code=422, detail="Inserisci almeno due caratteri.")

    client, _country = await _open_client(service)
    try:
        results = await client.search(query, service)
    except Exception as exc:
        raise HTTPException(status_code=502, detail=f"Ricerca non riuscita: {exc}") from exc
    finally:
        await client.aclose()

    artwork_albums, artwork_tracks = await asyncio.gather(
        _fetch_artwork_catalog(query, "album"),
        _fetch_artwork_catalog(query, "track"),
    )
    _prune_candidates()
    entries: list[dict[str, Any]] = []
    pending = []
    for kind in ("album", "track"):
        key = "albums" if kind == "album" else "tracks"
        pending.extend((kind, item) for item in (results.get(key) or [])[:15])
    fallback_limit = asyncio.Semaphore(4)
    artwork_urls = await asyncio.gather(
        *(
            _candidate_artwork(
                item, kind, query, artwork_albums, artwork_tracks, fallback_limit
            )
            for kind, item in pending
        )
    )
    for (kind, item), artwork_url in zip(pending, artwork_urls):
            item["artwork_url"] = artwork_url
            candidate_id = uuid.uuid4().hex
            candidate = {
                "id": candidate_id,
                "kind": kind,
                "item": item,
                "service": service,
                "expires_at": time.time() + CANDIDATE_TTL_SECONDS,
                "preview": None,
            }
            _candidates[candidate_id] = candidate
            entries.append(
                {
                    "id": candidate_id,
                    "kind": kind,
                    "title": item.get("title") or "Titolo sconosciuto",
                    "artist": item.get("artist") or "Artista sconosciuto",
                    "album": item.get("album") or "",
                    "artwork_url": artwork_url,
                }
            )
    return {"results": entries, "query": query}


@app.post("/api/candidates/{candidate_id}/preview")
async def preview_candidate(candidate_id: str) -> dict[str, Any]:
    """Resolve the selected result and show its track list before any download."""
    _prune_candidates()
    candidate = _candidates.get(candidate_id)
    if not candidate:
        raise HTTPException(status_code=404, detail="Risultato scaduto. Ripeti la ricerca.")
    client, country = await _open_client(candidate["service"])
    try:
        page, _resolved_country = await _fetch_item_page(
            client, candidate["item"]["url"], country
        )
        info = page.get("info") or {}
        tracks = client.tracks_from_pd(page)
        preview_tracks = []
        track_urls = {}
        for number, track in enumerate(tracks, 1):
            available = track.get("producers", "x") is not None and bool(track.get("url"))
            if available:
                track_urls[number] = track["url"]
            preview_tracks.append(
                {
                    "number": number,
                    "title": track.get("title") or "Traccia senza titolo",
                    "artist": _artists(track.get("artists")) or _artists(info.get("artists")),
                    "available": available,
                    "artwork_url": candidate["item"].get("artwork_url", ""),
                }
            )
        if not preview_tracks:
            raise HTTPException(status_code=404, detail="Il risultato non contiene tracce disponibili.")
        candidate["preview"] = {
            "tracks": preview_tracks,
            "title": info.get("title") or candidate["item"].get("title") or "",
            "artist": _artists(info.get("artists")) or candidate["item"].get("artist", ""),
            "album": info.get("title") if info.get("type") == "album" else candidate["item"].get("album", ""),
            "artwork_url": candidate["item"].get("artwork_url", ""),
        }
        candidate["track_urls"] = track_urls
        return {
            "id": candidate_id,
            "kind": candidate["kind"],
            **candidate["preview"],
            "available_count": sum(track["available"] for track in preview_tracks),
        }
    except HTTPException:
        raise
    except Exception as exc:
        raise HTTPException(status_code=502, detail=f"Anteprima non riuscita: {exc}") from exc
    finally:
        await client.aclose()


async def _run_download(job_id: str, candidate_id: str) -> None:
    """Run one confirmed lucidadl download and persist its result."""
    async with _download_lock:
        await _run_download_serial(job_id, candidate_id)


async def _run_download_serial(job_id: str, candidate_id: str) -> None:
    """Execute a queued download while holding the single-download lock."""
    job = _jobs[job_id]
    candidate = _candidates.get(candidate_id)
    if not candidate or not candidate.get("preview"):
        job.update(status="error", error="Anteprima scaduta; ripeti la ricerca.")
        return

    settings = _read_settings()
    service = candidate["service"]
    country = lucida_api.default_country(service)
    destination = _music_directory(settings["music_folder"])
    job["status"] = "downloading"
    selected_numbers = job["track_numbers"]
    available_numbers = sorted(candidate.get("track_urls", {}).keys())
    download_all = candidate["kind"] == "album" and selected_numbers == available_numbers
    if download_all:
        items = [candidate["item"]["url"]]
        kind = "album"
    else:
        items = [candidate["track_urls"][number] for number in selected_numbers]
        kind = "track"
    state = utils.State(paths.STATE_PATH)
    before = {path for saved_paths in state.done.values() for path in saved_paths}
    client: LucidaClient | None = None
    try:
        client, country = await _open_client(service)
        totals, failures = await downloader.run_batch(
            client,
            state,
            items,
            kind,
            service,
            country,
            str(destination),
            jobs=3,
            dedup=True,
            organize_on=True,
            reporter=JobReporter(job),
            track_numbers=[str(number) for number in selected_numbers],
        )
        new_files = sorted(
            {path for saved_paths in state.done.values() for path in saved_paths} - before
        )
        if failures or totals["fail"]:
            job.update(status="error", error="Download non completato; consulta i dettagli.")
            return
        preview = candidate["preview"]
        _save_download(
            title=preview["title"],
            artist=preview["artist"],
            album=preview["album"],
            kind=candidate["kind"],
            service=service,
            files=new_files,
            artwork_url=candidate["item"].get("artwork_url", ""),
        )
        job.update(status="complete", files=new_files, error=None)
    except Exception as exc:
        job.update(status="error", error=str(exc))
    finally:
        if client is not None:
            await client.aclose()


@app.post("/api/candidates/{candidate_id}/download", status_code=202)
async def confirm_download(
    candidate_id: str, body: DownloadRequest | None = None
) -> dict[str, str]:
    """Queue a download only after the selected result has been previewed."""
    _prune_candidates()
    candidate = _candidates.get(candidate_id)
    if not candidate:
        raise HTTPException(status_code=404, detail="Risultato scaduto. Ripeti la ricerca.")
    if not candidate.get("preview"):
        raise HTTPException(status_code=409, detail="Visualizza prima l'anteprima del risultato.")
    available_numbers = {
        track["number"] for track in candidate["preview"]["tracks"] if track["available"]
    }
    requested_numbers = (
        set(body.track_numbers)
        if body is not None and body.track_numbers is not None
        else available_numbers
    )
    if not requested_numbers or not requested_numbers.issubset(available_numbers):
        raise HTTPException(status_code=422, detail="Seleziona almeno una traccia disponibile.")
    if any(job["status"] in {"queued", "downloading"} for job in _jobs.values()):
        raise HTTPException(status_code=409, detail="È già attivo un download.")
    job_id = uuid.uuid4().hex
    job = {
        "id": job_id,
        "candidate_id": candidate_id,
        "title": candidate["preview"]["title"],
        "artist": candidate["preview"]["artist"],
        "album": candidate["preview"]["album"],
        "track_numbers": sorted(requested_numbers),
        "status": "queued",
        "tracks": {},
        "logs": [],
        "files": [],
        "error": None,
    }
    async with _jobs_lock:
        _jobs[job_id] = job
    asyncio.create_task(_run_download(job_id, candidate_id))
    return {"id": job_id}


@app.get("/api/jobs/{job_id}")
async def get_job(job_id: str) -> dict[str, Any]:
    """Return the live status, track progress, and logs for one job."""
    job = _jobs.get(job_id)
    if not job:
        raise HTTPException(status_code=404, detail="Download non trovato.")
    return dict(job)


@app.get("/api/recent")
async def recent_downloads() -> dict[str, list[dict[str, Any]]]:
    """Return the latest completed downloads, newest first."""
    _ensure_storage()
    with sqlite3.connect(DATABASE_FILE) as connection:
        connection.row_factory = sqlite3.Row
        rows = connection.execute(
            "SELECT * FROM downloads ORDER BY created_at DESC LIMIT 12"
        ).fetchall()
    downloads = [
            {
                "id": row["id"],
                "title": row["title"],
                "artist": row["artist"],
                "album": row["album"],
                "kind": row["kind"],
                "service": row["service"],
                "files": json.loads(row["files"]),
                "artwork_url": row["artwork_url"],
                "created_at": row["created_at"],
            }
            for row in rows
        ]
    artwork_limit = asyncio.Semaphore(4)

    async def fill_missing_artwork(item: dict[str, Any]) -> None:
        """Look up missing historical cover art and cache successful matches."""
        if item["artwork_url"]:
            return
        item["artwork_url"] = await _candidate_artwork(
            item, item["kind"], "", [], [], artwork_limit
        )
        if item["artwork_url"]:
            with sqlite3.connect(DATABASE_FILE) as connection:
                connection.execute(
                    "UPDATE downloads SET artwork_url = ? WHERE id = ?",
                    (item["artwork_url"], item["id"]),
                )

    await asyncio.gather(*(fill_missing_artwork(item) for item in downloads))
    return {"downloads": downloads}