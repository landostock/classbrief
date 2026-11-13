from __future__ import annotations

import json
import logging
import re
import asyncio
from contextlib import asynccontextmanager
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo

from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse, RedirectResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates

from app.config import get_settings
from app.database import Base, SessionLocal, engine
from app.plugin_server import plugin_http_app, plugin_mcp
from app.routes import api, auth, internal, web
from app.services.security import verify_session
from app.services.scheduler import run_due_briefings

BASE_DIR = Path(__file__).resolve().parent


class JsonFormatter(logging.Formatter):
    def format(self, record):
        return json.dumps({"timestamp": datetime.utcnow().isoformat() + "Z", "level": record.levelname, "event": record.getMessage()}, ensure_ascii=False)


handler = logging.StreamHandler()
handler.setFormatter(JsonFormatter())
logging.getLogger("classbrief").addHandler(handler)
logging.getLogger("classbrief").setLevel(logging.INFO)

@asynccontextmanager
async def lifespan(_app: FastAPI):
    settings = get_settings()
    if settings.production and (settings.session_secret == "dev-only-change-me" or settings.cron_secret == "dev-cron-secret" or not settings.app_password_hash):
        raise RuntimeError("Production secrets APP_PASSWORD_HASH, SESSION_SECRET und CRON_SECRET müssen gesetzt sein")
    Base.metadata.create_all(bind=engine)
    async def scheduler_loop():
        await asyncio.sleep(15)
        while True:
            try:
                with SessionLocal() as db:
                    await run_due_briefings(db, settings)
            except asyncio.CancelledError:
                raise
            except Exception as exc:
                logging.getLogger("classbrief.scheduler").warning("scheduler_tick_failed", extra={"error": type(exc).__name__})
            await asyncio.sleep(60)

    async with plugin_mcp.session_manager.run():
        scheduler_task = asyncio.create_task(scheduler_loop())
        try:
            yield
        finally:
            scheduler_task.cancel()
            try:
                await scheduler_task
            except asyncio.CancelledError:
                pass


app = FastAPI(title="ClassBrief", docs_url=None, redoc_url=None, lifespan=lifespan)
app.state.templates = Jinja2Templates(directory=str(BASE_DIR / "templates"))
app.mount("/static", StaticFiles(directory=str(BASE_DIR / "static")), name="static")

ICONS = {
    "home": '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.9"><path d="m3 11 9-8 9 8"/><path d="M5.5 9.5V21h13V9.5M9 21v-6h6v6"/></svg>',
    "calendar": '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.9"><rect x="3" y="5" width="18" height="16" rx="2"/><path d="M8 3v4m8-4v4M3 10h18"/></svg>',
    "books": '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.9"><path d="M4 4h5v16H4zM10 4h5v16h-5zM16.5 5l3.7-1 3.5 15-3.7 1z"/></svg>',
    "settings": '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.9"><circle cx="12" cy="12" r="3"/><path d="M19.4 15a1.7 1.7 0 0 0 .3 1.9l.1.1-2.8 2.8-.1-.1a1.7 1.7 0 0 0-1.9-.3 1.7 1.7 0 0 0-1 1.6v.2h-4V21a1.7 1.7 0 0 0-1-1.6 1.7 1.7 0 0 0-1.9.3l-.1.1L4.2 17l.1-.1a1.7 1.7 0 0 0 .3-1.9A1.7 1.7 0 0 0 3 14H2.8v-4H3a1.7 1.7 0 0 0 1.6-1 1.7 1.7 0 0 0-.3-1.9L4.2 7 7 4.2l.1.1A1.7 1.7 0 0 0 9 4.6 1.7 1.7 0 0 0 10 3V2.8h4V3a1.7 1.7 0 0 0 1 1.6 1.7 1.7 0 0 0 1.9-.3l.1-.1L19.8 7l-.1.1a1.7 1.7 0 0 0-.3 1.9 1.7 1.7 0 0 0 1.6 1h.2v4H21a1.7 1.7 0 0 0-1.6 1Z"/></svg>',
    "check": '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.3"><path d="m5 12 4 4L19 6"/></svg>',
    "arrow": '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><path d="M5 12h14m-5-5 5 5-5 5"/></svg>',
    "chevron": '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><path d="m9 5 7 7-7 7"/></svg>',
    "back": '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><path d="M19 12H5m5-5-5 5 5 5"/></svg>',
    "file": '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.8"><path d="M6 2h8l4 4v16H6z"/><path d="M14 2v5h5M9 12h6m-6 4h6"/></svg>',
    "spark": '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.8"><path d="m12 2 1.5 5.5L19 9l-5.5 1.5L12 16l-1.5-5.5L5 9l5.5-1.5L12 2Z"/><path d="m19 15 .7 2.3L22 18l-2.3.7L19 21l-.7-2.3L16 18l2.3-.7L19 15Z"/></svg>',
    "history": '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.9"><path d="M3 12a9 9 0 1 0 3-6.7L3 8"/><path d="M3 3v5h5M12 7v5l3 2"/></svg>',
}


def icon(name: str) -> str:
    return ICONS.get(name, "")


def replace_names(value: str) -> str:
    names = {"Monday": "Montag", "Tuesday": "Dienstag", "Wednesday": "Mittwoch", "Thursday": "Donnerstag", "Friday": "Freitag", "Saturday": "Samstag", "Sunday": "Sonntag", "Mon": "Mo", "Tue": "Di", "Wed": "Mi", "Thu": "Do", "Fri": "Fr", "Sat": "Sa", "Sun": "So", "January": "Januar", "February": "Februar", "March": "März", "May": "Mai", "June": "Juni", "July": "Juli", "October": "Oktober", "December": "Dezember"}
    for source, target in names.items():
        value = re.sub(rf"\b{re.escape(source)}\b", target, value)
    return value


def legacy_brief_blocks(value: str) -> list[dict[str, object]]:
    """Turn older free-form briefing prose into readable, escaped UI blocks."""
    text = re.sub(r"\s+", " ", str(value or "")).strip()
    if not text:
        return []
    labels = r"EINLEITUNG|HAUPTTEIL|SCHLUSS|VERSTÄNDNISFALLEN|MÖGLICHE AUFGABEN|DAS SOLLTEST DU MITBRINGEN"
    text = re.sub(rf"\s*({labels})\s*:\s*", r"\n\1\n", text, flags=re.IGNORECASE)
    text = re.sub(r"(?<=[.!?])\s+([1-9]\.)\s+", r"\n\1 ", text)
    blocks: list[dict[str, object]] = []
    numbered: list[str] = []
    for line in (part.strip() for part in text.splitlines()):
        if not line:
            continue
        if re.fullmatch(labels, line, flags=re.IGNORECASE):
            if numbered:
                blocks.append({"type": "numbered", "items": numbered})
                numbered = []
            blocks.append({"type": "heading", "text": line.title()})
            continue
        match = re.match(r"^[1-9]\.\s*(.+)$", line)
        if match:
            numbered.append(match.group(1).strip())
            continue
        if numbered:
            blocks.append({"type": "numbered", "items": numbered})
            numbered = []
        blocks.append({"type": "paragraph", "text": line})
    if numbered:
        blocks.append({"type": "numbered", "items": numbered})
    return blocks


app.state.templates.env.globals["icon"] = icon
app.state.templates.env.globals["legacy_brief_blocks"] = legacy_brief_blocks
app.state.templates.env.filters["day_de"] = replace_names
app.state.templates.env.filters["month_de"] = replace_names
app.state.templates.env.filters["status_de"] = lambda value: {"analyzed": "Analysiert", "deduplicated": "Bereits bekannt", "ocr_required": "Scan – OCR nötig", "ai_error": "KI-Auswertung fehlgeschlagen", "extract_error": "Datei fehlerhaft", "download_error": "Download fehlgeschlagen", "too_large": "Datei zu groß", "unsupported": "Nicht automatisch auswertbar", "budget_blocked": "Budgetlimit erreicht", "discovered": "Neu erkannt", "historical": "Historischer Bestand", "plugin_pending": "Für Plugin vorgemerkt", "context_ready": "Für Briefing bereit", "already_briefed": "Bereits gebrieft", "context_limit": "Kontextgrenze erreicht"}.get(value, value)
app.state.templates.env.filters["exception_de"] = lambda value: {"cancel": "Unterricht entfällt", "move": "Unterricht verschoben", "add": "Zusatzstunde", "global_disabled_date": "Schulfreier Tag"}.get(value, value)
app.state.templates.env.filters["datetime_from_timestamp"] = lambda value, fmt="%H:%M": datetime.fromtimestamp(float(value), ZoneInfo("Europe/Berlin")).strftime(fmt)


@app.middleware("http")
async def auth_middleware(request: Request, call_next):
    oauth_public = request.url.path in {"/authorize", "/token", "/register", "/revoke"} or request.url.path.startswith("/.well-known/") or request.url.path.startswith("/oauth/")
    public = request.url.path in {"/login", "/access", "/health", "/offline", "/manifest.webmanifest", "/service-worker.js", "/api/chatgpt/pairing"} or request.url.path.startswith("/static/") or request.url.path.startswith("/internal/") or request.url.path.startswith("/mcp") or oauth_public
    if not public and not verify_session(request.cookies.get("classbrief_session"), get_settings().session_secret):
        if request.url.path.startswith("/api/"):
            return JSONResponse({"detail": "Anmeldung erforderlich"}, status_code=401)
        return RedirectResponse(f"/login?next={request.url.path}", status_code=303)
    response = await call_next(request)
    response.headers.setdefault("X-Content-Type-Options", "nosniff")
    response.headers.setdefault("Referrer-Policy", "same-origin")
    response.headers.setdefault("Permissions-Policy", "camera=(), microphone=(), geolocation=()")
    return response


@app.get("/health")
async def health():
    return {"status": "ok"}


@app.get("/manifest.webmanifest")
async def manifest():
    return JSONResponse({
        "name": "ClassBrief", "short_name": "ClassBrief", "description": "Dein Unterrichtsbriefing vor der nächsten Stunde",
        "id": "/", "start_url": "/", "scope": "/", "display": "standalone", "display_override": ["window-controls-overlay", "standalone"],
        "orientation": "portrait-primary", "background_color": "#07131f", "theme_color": "#07131f", "categories": ["education", "productivity"],
        "shortcuts": [
            {"name": "Heutiges Briefing", "short_name": "Heute", "url": "/"},
            {"name": "Zentraler Stundenplan", "short_name": "Plan", "url": "/schedule"},
            {"name": "Briefing-Archiv", "short_name": "Archiv", "url": "/briefings"},
        ],
        "icons": [
            {"src": "/static/icons/icon.svg", "sizes": "any", "type": "image/svg+xml", "purpose": "any"},
            {"src": "/static/icons/maskable.svg", "sizes": "any", "type": "image/svg+xml", "purpose": "maskable"},
        ],
    }, media_type="application/manifest+json")


@app.get("/offline")
async def offline():
    from fastapi.responses import HTMLResponse
    return HTMLResponse(
        "<!doctype html><html lang='de'><head><meta charset='utf-8'><meta name='viewport' content='width=device-width,initial-scale=1'>"
        "<meta name='theme-color' content='#07131f'><title>ClassBrief · Offline</title><style>body{margin:0;min-height:100vh;display:grid;"
        "place-items:center;background:#07131f;color:#eaf7ff;font:16px/1.5 system-ui;padding:24px}main{max-width:420px;text-align:center}"
        "h1{font-size:2rem}p{color:#b7cbd9}button{border:0;border-radius:12px;padding:12px 18px;font:inherit;font-weight:700;cursor:pointer}</style>"
        "</head><body><main><h1>Gerade offline.</h1><p>Deine privaten Briefings werden aus Datenschutzgründen nicht im Browser-Cache gespeichert. "
        "Sobald du wieder verbunden bist, ist alles unverändert da.</p><button onclick='location.reload()'>Erneut versuchen</button></main></body></html>",
        headers={"Cache-Control": "public, max-age=86400"},
    )


@app.get("/service-worker.js")
async def service_worker():
    from fastapi.responses import FileResponse
    return FileResponse(BASE_DIR / "pwa" / "service-worker.js", media_type="application/javascript", headers={"Service-Worker-Allowed": "/"})


app.include_router(auth.router)
app.include_router(web.router)
app.include_router(api.router)
app.include_router(internal.router)
app.mount("/", plugin_http_app, name="classbrief-plugin")
