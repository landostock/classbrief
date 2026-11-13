from __future__ import annotations

from datetime import date, datetime
from html import escape
import time
from typing import Any, Literal
from urllib.parse import urlparse

from mcp.server.fastmcp import FastMCP
from mcp.server.auth.settings import AuthSettings, ClientRegistrationOptions, RevocationOptions
from mcp.server.transport_security import TransportSecuritySettings
from mcp.types import ToolAnnotations
from pydantic import BaseModel, ConfigDict
from sqlalchemy import select
from starlette.requests import Request
from starlette.responses import HTMLResponse, RedirectResponse

from app.config import get_settings
from app.database import SessionLocal
from app.models import Briefing, Lesson, ScheduleEntry, Subject
from app.services.automation import (
    build_automation_plan,
    complete_automation_run as complete_run,
    confirm_automation_sync as confirm_sync,
    resolve_automation_run as resolve_run,
)
from app.services.oauth_provider import ClassBriefOAuthProvider, OAUTH_SCOPE, OAuthRequestError
from app.services.notifications import send_briefing_push
from app.services.plugin_briefing import collect_lesson_context, save_plugin_briefing_with_status
from app.services.schedule import BERLIN, as_berlin, ensure_lessons, upcoming_lessons
from app.services.security import verify_app_password
from app.services.web_access import create_web_access_link as issue_web_access_link


def _transport_security() -> TransportSecuritySettings:
    configured_host = urlparse(get_settings().app_base_url).netloc
    allowed_hosts = ["localhost:*", "127.0.0.1:*", "testserver"]
    allowed_origins = ["http://localhost:*", "http://127.0.0.1:*"]
    if configured_host:
        allowed_hosts.append(configured_host)
        scheme = urlparse(get_settings().app_base_url).scheme or "https"
        allowed_origins.append(f"{scheme}://{configured_host}")
    return TransportSecuritySettings(
        enable_dns_rebinding_protection=True,
        allowed_hosts=allowed_hosts,
        allowed_origins=allowed_origins,
    )


oauth_provider = ClassBriefOAuthProvider(get_settings())
plugin_mcp = FastMCP(
    name="ClassBrief",
    instructions=(
        "ClassBrief provides a private timetable, compact subject memory, and only the IServ materials "
        "that are new or changed since the previous relevant lesson. Never request or infer the rest of "
        "the school-year folder. Use list_briefing_targets first, then prepare_briefing_context for the "
        "chosen lesson. Save a finished structured briefing with save_briefing."
    ),
    streamable_http_path="/mcp",
    stateless_http=True,
    json_response=True,
    transport_security=_transport_security(),
    auth_server_provider=oauth_provider,
    auth=AuthSettings(
        issuer_url=oauth_provider.issuer_url,
        service_documentation_url=get_settings().app_base_url,
        client_registration_options=ClientRegistrationOptions(
            enabled=True,
            valid_scopes=[OAUTH_SCOPE],
            default_scopes=[OAUTH_SCOPE],
        ),
        revocation_options=RevocationOptions(enabled=True),
        required_scopes=[OAUTH_SCOPE],
        resource_server_url=oauth_provider.resource_url,
        validate_token_resource=True,
    ),
)

READ_ONLY = ToolAnnotations(readOnlyHint=True, destructiveHint=False, openWorldHint=False)
INTERNAL_IDEMPOTENT = ToolAnnotations(readOnlyHint=False, destructiveHint=False, idempotentHint=True, openWorldHint=False)
WRITE_IDEMPOTENT = ToolAnnotations(readOnlyHint=False, destructiveHint=False, idempotentHint=True, openWorldHint=False)
WRITE_ACTION = ToolAnnotations(readOnlyHint=False, destructiveHint=False, idempotentHint=False, openWorldHint=False)
OAUTH_META = {"securitySchemes": [{"type": "oauth2", "scopes": [OAUTH_SCOPE]}]}


class ProfileResult(BaseModel):
    model_config = ConfigDict(extra="forbid")

    id: str
    name: str
    nickname: str


_failed_approval_attempts: dict[str, list[float]] = {}


def _request_address(request: Request) -> str:
    if address := request.headers.get("fly-client-ip"):
        return address
    return request.client.host if request.client else "unknown"


def _approval_rate_limited(address: str) -> bool:
    cutoff = time.time() - 5 * 60
    attempts = [timestamp for timestamp in _failed_approval_attempts.get(address, []) if timestamp >= cutoff]
    _failed_approval_attempts[address] = attempts
    return len(attempts) >= 5


def _record_failed_approval(address: str) -> None:
    _failed_approval_attempts.setdefault(address, []).append(time.time())


def _approval_response(request_token: str, payload: dict[str, Any], error: str | None = None, status: int = 200):
    client_name = escape(str(payload.get("client_name") or "ChatGPT"))
    token = escape(request_token, quote=True)
    error_html = f'<p class="error" role="alert">{escape(error)}</p>' if error else ""
    body = f"""<!doctype html>
<html lang="de"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<title>ClassBrief verbinden</title><style>
:root{{color-scheme:light dark;font-family:Inter,system-ui,sans-serif}}body{{margin:0;background:#07131f;color:#eaf7ff;min-height:100vh;display:grid;place-items:center}}
main{{width:min(92vw,520px);background:#102235;border:1px solid #28465d;border-radius:18px;padding:28px;box-shadow:0 22px 60px #0008}}h1{{margin-top:0;font-size:1.55rem}}p{{line-height:1.5;color:#bfd4e2}}label{{display:grid;gap:8px;margin:22px 0 16px;font-weight:700}}input{{font:inherit;padding:12px 14px;border:1px solid #49687d;border-radius:10px;background:#081724;color:#fff}}.actions{{display:flex;gap:10px;justify-content:flex-end}}button{{font:inherit;font-weight:700;border-radius:10px;padding:11px 16px;border:1px solid #4a6a80;cursor:pointer}}.approve{{background:#65e0ff;color:#042331;border-color:#65e0ff}}.deny{{background:transparent;color:#dceefa}}.error{{color:#ffb5b5;font-weight:700}}small{{color:#91adbf}}
</style></head><body><main><h1>ClassBrief mit {client_name} verbinden</h1>
<p>Die Verbindung darf Stundenplan, Fachgedächtnis und zeitlich relevante IServ-Materialien lesen sowie fertige Briefings speichern.</p>
{error_html}<form id="oauth-approval-form" method="post" action="/oauth/approve" autocomplete="off">
<input type="hidden" name="request" value="{token}"><label>ClassBrief-Passwort
<input type="password" name="password" autocomplete="current-password" required autofocus></label>
<small>Das IServ-Passwort wird hier nicht benötigt.</small><div class="actions">
<button class="deny" name="decision" value="deny" formnovalidate>Abbrechen</button>
<button class="approve" name="decision" value="approve">Verbindung freigeben</button></div></form>
<script>
const form = document.getElementById("oauth-approval-form");
let approvalSubmitted = false;
form.addEventListener("submit", (event) => {{
  if (event.submitter?.value !== "approve") return;
  if (approvalSubmitted) {{
    event.preventDefault();
    return;
  }}
  approvalSubmitted = true;
  const button = form.querySelector(".approve");
  button.disabled = true;
  button.textContent = "Verbindung wird freigegeben…";
}});
</script></main></body></html>"""
    return HTMLResponse(
        body,
        status_code=status,
        headers={
            "Cache-Control": "no-store",
            "Content-Security-Policy": (
                "default-src 'none'; style-src 'unsafe-inline'; script-src 'unsafe-inline'; "
                "form-action 'self' https://chatgpt.com https://chat.openai.com; "
                "base-uri 'none'; frame-ancestors 'none'"
            ),
            "X-Frame-Options": "DENY",
        },
    )


@plugin_mcp.custom_route("/oauth/approve", methods=["GET", "POST"])
async def approve_oauth(request: Request):
    if request.method == "GET":
        request_token = request.query_params.get("request", "")
        try:
            payload = oauth_provider.read_authorization_request(request_token)
        except OAuthRequestError as exc:
            return HTMLResponse(escape(str(exc)), status_code=400, headers={"Cache-Control": "no-store"})
        return _approval_response(request_token, payload)

    form = await request.form()
    request_token = str(form.get("request") or "")
    try:
        payload = oauth_provider.read_authorization_request(request_token)
    except OAuthRequestError as exc:
        return HTMLResponse(escape(str(exc)), status_code=400, headers={"Cache-Control": "no-store"})
    if form.get("decision") == "deny":
        return RedirectResponse(oauth_provider.deny_authorization(request_token), status_code=302)

    address = _request_address(request)
    if _approval_rate_limited(address):
        return _approval_response(request_token, payload, "Zu viele Fehlversuche. Bitte später erneut versuchen.", 429)
    password = str(form.get("password") or "")
    with SessionLocal() as db:
        password_ok = verify_app_password(password, get_settings(), db)
    if not password_ok:
        _record_failed_approval(address)
        return _approval_response(request_token, payload, "Das ClassBrief-Passwort stimmt nicht.", 401)
    _failed_approval_attempts.pop(address, None)
    try:
        redirect_url = await oauth_provider.complete_authorization(request_token)
    except OAuthRequestError as exc:
        return HTMLResponse(escape(str(exc)), status_code=400, headers={"Cache-Control": "no-store"})
    return RedirectResponse(redirect_url, status_code=302)


def _briefing_status(db, lesson_id: int) -> str:
    briefing = db.scalar(
        select(Briefing)
        .where(Briefing.lesson_id == lesson_id, Briefing.status == "ready")
        .order_by(Briefing.created_at.desc())
        .limit(1)
    )
    return "ready" if briefing else "not_prepared"


@plugin_mcp.tool(
    title="Briefing-Ziele auflisten",
    description=(
        "Listet die nächste Stunde oder die Stunden eines Tages aus dem ClassBrief-Stundenplan auf. "
        "Vor jedem Materialabruf verwenden; liefert keine IServ-Dateiinhalte."
    ),
    annotations=INTERNAL_IDEMPOTENT,
    structured_output=True,
    meta=OAUTH_META,
)
def list_briefing_targets(
    scope: Literal["next", "today"] = "next",
    day: str | None = None,
    subject_id: str | None = None,
) -> dict[str, Any]:
    now = datetime.now(BERLIN)
    with SessionLocal() as db:
        if scope == "next":
            lessons = upcoming_lessons(db, now, days=21, limit=30)
            lessons = [item for item in lessons if item.subject.briefing_enabled]
            if subject_id:
                lessons = [item for item in lessons if item.subject_id == subject_id]
            lessons = lessons[:1]
            target_day = lessons[0].scheduled_start.date() if lessons else now.date()
        else:
            try:
                target_day = date.fromisoformat(day) if day else now.date()
            except ValueError as exc:
                raise ValueError("day muss im Format YYYY-MM-DD angegeben werden") from exc
            lessons = ensure_lessons(db, target_day)
            lessons = [item for item in lessons if item.subject.briefing_enabled]
            if target_day == now.date():
                lessons = [item for item in lessons if as_berlin(item.scheduled_end) >= now]
            if subject_id:
                lessons = [item for item in lessons if item.subject_id == subject_id]

        return {
            "scope": scope,
            "day": target_day.isoformat(),
            "timezone": "Europe/Berlin",
            "lessons": [
                {
                    "lesson_id": item.id,
                    "subject_id": item.subject_id,
                    "subject": item.subject.name,
                    "starts_at": as_berlin(item.scheduled_start).isoformat(),
                    "ends_at": as_berlin(item.scheduled_end).isoformat(),
                    "room": item.room,
                    "teacher": item.teacher,
                    "schedule_status": item.status,
                    "change_kind": item.change_kind,
                    "change_note": item.note,
                    "schedule_source": item.source,
                    "briefing_status": _briefing_status(db, item.id),
                }
                for item in lessons
            ],
        }


@plugin_mcp.tool(
    title="Briefing-Kontext vorbereiten",
    description=(
        "Liest für genau eine Stunde das Fachgedächtnis und ausschließlich neue oder geänderte, noch "
        "nicht in einem Briefing verwendete IServ-Materialien. Alte Bestandsdateien werden beim ersten "
        "Scan nur katalogisiert und nicht ausgegeben."
    ),
    annotations=INTERNAL_IDEMPOTENT,
    structured_output=True,
    meta=OAUTH_META,
)
async def prepare_briefing_context(lesson_id: int) -> dict[str, Any]:
    with SessionLocal() as db:
        lesson = db.get(Lesson, lesson_id)
        if lesson is None:
            raise ValueError("Unbekannte lesson_id")
        if not lesson.subject.enabled:
            raise ValueError("Das Fach ist deaktiviert")
        if not lesson.subject.briefing_enabled:
            raise ValueError("Für dieses Fach sind Briefings ausgeschaltet")
        return await collect_lesson_context(db, lesson, get_settings())


@plugin_mcp.tool(
    title="Briefing speichern",
    description=(
        "Speichert ein fertig erzeugtes Unterrichtsbriefing in ClassBrief und markiert die angegebenen "
        "Materialversionen als verwendet. deep kann sichere Präsentationsblöcke für alle drei Längen, "
        "Verbindungen zum Vorwissen und direkt aussprechbare Antwortkarten enthalten. Erneute identische "
        "Aufrufe sind idempotent."
    ),
    annotations=WRITE_IDEMPOTENT,
    structured_output=True,
    meta=OAUTH_META,
)
async def save_briefing(
    lesson_id: int,
    probable_topic: str,
    quick_brief: str,
    medium_brief: str,
    deep: dict[str, Any],
    source_hashes: list[str],
    confidence: Literal["normal", "lower"] = "normal",
) -> dict[str, Any]:
    with SessionLocal() as db:
        lesson = db.get(Lesson, lesson_id)
        if lesson is None:
            raise ValueError("Unbekannte lesson_id")
        if not lesson.subject.briefing_enabled:
            raise ValueError("Für dieses Fach sind Briefings ausgeschaltet")
        briefing, created = save_plugin_briefing_with_status(
            db,
            lesson,
            {
                "probable_topic": probable_topic,
                "quick_brief": quick_brief,
                "medium_brief": medium_brief,
                "deep": deep,
            },
            source_hashes,
            confidence=confidence,
        )
        notifications_sent = await send_briefing_push(db, briefing, get_settings(), new_materials=len(set(source_hashes))) if created else 0
        return {
            "status": "saved" if created else "already_saved",
            "briefing_id": briefing.id,
            "lesson_id": lesson.id,
            "url": f"{get_settings().app_base_url.rstrip('/')}/briefings/{briefing.id}",
            "notifications_sent": notifications_sent,
        }


@plugin_mcp.tool(
    title="Cloud-Automatikplan abrufen",
    description=(
        "Liefert den vollständigen, auf der ClassBrief-Webseite konfigurierten Automatikplan als konkrete "
        "ChatGPT-Zeitpläne mit RRULE, Zeitzone, stabilen Schlüsseln und fertigen Laufanweisungen."
    ),
    annotations=READ_ONLY,
    structured_output=True,
    meta=OAUTH_META,
)
def get_automation_plan() -> dict[str, Any]:
    with SessionLocal() as db:
        return build_automation_plan(db)


@plugin_mcp.tool(
    title="Automatikplan als synchron bestätigt",
    description=(
        "Nach erfolgreichem Erstellen, Aktualisieren und Entfernen der geplanten ChatGPT-Aufgaben aufrufen. "
        "Akzeptiert nur exakt den aktuellen Plan und verhindert einen falschen Synchronstatus."
    ),
    annotations=WRITE_IDEMPOTENT,
    structured_output=True,
    meta=OAUTH_META,
)
def confirm_automation_sync(plan_revision: str, automation_keys: list[str]) -> dict[str, Any]:
    with SessionLocal() as db:
        return confirm_sync(db, plan_revision, automation_keys)


@plugin_mcp.tool(
    title="Geplanten Briefing-Lauf auflösen",
    description=(
        "Muss am Anfang jeder geplanten ClassBrief-Aufgabe mit deren automation_key aufgerufen werden. "
        "Prüft Regeln, aktuellen Stundenplan und Tagesausnahmen erneut und liefert nur die jetzt gültigen Ziele."
    ),
    annotations=INTERNAL_IDEMPOTENT,
    structured_output=True,
    meta=OAUTH_META,
)
def resolve_automation_run(automation_key: str) -> dict[str, Any]:
    with SessionLocal() as db:
        return resolve_run(db, automation_key)


@plugin_mcp.tool(
    title="Geplanten Briefing-Lauf abschließen",
    description="Speichert den Erfolg oder Fehler eines zuvor aufgelösten Automatiklaufs für die mobile Übersicht.",
    annotations=WRITE_IDEMPOTENT,
    structured_output=True,
    meta=OAUTH_META,
)
def complete_automation_run(
    run_id: int,
    status: Literal["completed", "failed"],
    briefing_count: int = 0,
    note: str = "",
) -> dict[str, Any]:
    with SessionLocal() as db:
        return complete_run(db, run_id, status, briefing_count, note)


@plugin_mcp.tool(
    title="Sicheren ClassBrief-Webzugang erstellen",
    description=(
        "Erstellt auf ausdrücklichen Wunsch einen einmal nutzbaren, 15 Minuten gültigen Link. Dort kann der "
        "Nutzer sein ClassBrief-Passwort selbst festlegen und sich anmelden, ohne es im Chat offenzulegen."
    ),
    annotations=WRITE_ACTION,
    structured_output=True,
    meta=OAUTH_META,
)
def create_web_access_link() -> dict[str, Any]:
    with SessionLocal() as db:
        result = issue_web_access_link(db, get_settings())
        return {
            "status": "created",
            **result,
            "note": "Öffne den Link direkt. Gib das neue Passwort ausschließlich auf der ClassBrief-Seite ein.",
        }


@plugin_mcp.tool(
    title="Automatisierbare Stunden auflisten",
    description=(
        "Liefert die wiederkehrenden Stundenplanpositionen und ihre Vorbereitungszeit. Nutze sie, um mit "
        "dem Nutzer morgendliche oder fachbezogene geplante ChatGPT-Aufgaben einzurichten."
    ),
    annotations=READ_ONLY,
    structured_output=True,
    meta=OAUTH_META,
)
def list_automation_slots() -> dict[str, Any]:
    weekdays = ["MO", "TU", "WE", "TH", "FR", "SA", "SU"]
    with SessionLocal() as db:
        rows = list(
            db.scalars(
                select(ScheduleEntry)
                .join(Subject, Subject.id == ScheduleEntry.subject_id)
                .where(ScheduleEntry.enabled.is_(True), Subject.enabled.is_(True))
                .order_by(ScheduleEntry.weekday, ScheduleEntry.start_time)
            )
        )
        return {
            "timezone": "Europe/Berlin",
            "morning_recommendation": {
                "prompt_scope": "today",
                "note": "Eine tägliche Aufgabe kann den ganzen aktuellen Schultag in einem Lauf briefen.",
            },
            "slots": [
                {
                    "schedule_entry_id": item.id,
                    "subject_id": item.subject_id,
                    "subject": item.subject.name,
                    "weekday": weekdays[item.weekday],
                    "starts_at": item.start_time.isoformat(timespec="minutes"),
                    "preparation_minutes": item.subject.preparation_minutes,
                }
                for item in rows
            ],
        }


@plugin_mcp.tool(
    title="Verbundene ClassBrief-Instanz",
    description="Zeigt die verbundene private ClassBrief-Instanz ohne Zugangsdaten an.",
    annotations=READ_ONLY,
    structured_output=True,
    meta={"openai/profile": True, **OAUTH_META},
)
def get_profile() -> ProfileResult:
    return ProfileResult(
        id="classbrief-single-user",
        name="ClassBrief",
        nickname="Private Unterrichtsbriefings",
    )


plugin_http_app = plugin_mcp.streamable_http_app()
