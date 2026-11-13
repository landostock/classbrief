# ClassBrief

ClassBrief ist eine private Unterrichtsbriefing-PWA. Sie führt den aktuellen Stundenplan aus Pebbule/WebUntis, Änderungen und Vertretungen, neue IServ-Materialien und den bestätigten Stand der letzten Stunde zusammen. Daraus entstehen drei aufeinander abgestimmte Ansichten: 30 Sekunden, 2 Minuten und eine ausführliche Vorbereitung mit möglichen Aufgaben, Begriffen, Verständnisfallen und direkt aussprechbaren Antworten.

Die App ist für einen einzelnen Nutzer ausgelegt und läuft als FastAPI-/Jinja-PWA auf Fly.io. Der Mac oder PC darf ausgeschaltet sein: Zeitregeln werden auf der Cloud-Machine ausgewertet, und fertige Briefings stehen auf dem Handy in ClassBrief bereit.

## Kernprinzipien

- Ein zentraler Live-Stundenplan statt mehrfach gepflegter Fächerlisten.
- Sport und andere deaktivierte Fächer bleiben im Plan sichtbar, erzeugen aber nie Briefings.
- Entfall, Vertretung, Verschiebung, Raum und Lehrkraft werden vor jedem Lauf erneut geprüft.
- Alte Ordnerbestände werden nicht als aktueller Unterricht ausgegeben. Relevant sind neue oder geänderte Materialien und der bestätigte Fachstand.
- Leere Briefings werden vermieden: Ohne neue Belege zeigt ClassBrief nur den bestätigten Stand, offene Fragen und die Unsicherheit der zeitlichen Zuordnung.

## KI-Betriebsarten

### Verbundenes ChatGPT-Abo

OpenAIs „Sign in with ChatGPT“ erlaubt berechtigten Plus-/Pro-Konten, eine Open-Source-App ohne eigenen API-Schlüssel zu autorisieren. ClassBrief verwendet dabei den offiziellen OAuth-Flow mit PKCE, `127.0.0.1` als lokalem Callback und einer einmaligen, 15 Minuten gültigen Kopplung. Refresh- und Zugriffstoken werden ausschließlich verschlüsselt auf dem ClassBrief-Server gespeichert.

Nach der Kopplung:

- funktioniert „Jetzt vorbereiten“ direkt in ClassBrief,
- läuft die Automatik bei ausgeschaltetem Computer,
- gibt es keinen separaten API-Geldzähler in ClassBrief,
- hat ClassBrief weder Zugriff auf das ChatGPT-Passwort noch auf bestehende Chats.

Die Verfügbarkeit und Nutzungsgrenzen richten sich nach OpenAIs Regeln für den verbundenen ChatGPT-Plan. Der Fly.io-Betrieb kann unabhängig davon Hostingkosten verursachen.

### ClassBrief-Plugin

Alternativ stellt der MCP-Server unter `/mcp` ChatGPT einen privaten, zeitlich eingegrenzten Kontext bereit. Der OAuth-2.1-Autorisierungscodefluss verwendet PKCE; ChatGPT erhält niemals das IServ-Passwort. Diese Betriebsart eignet sich für interaktive Briefings im normalen Chat sowie als Rückfalloption.

### Klassischer API-Modus

Optional kann `AI_PROVIDER=openai` mit `OPENAI_API_KEY` verwendet werden. Nur in diesem Modus gilt der konfigurierbare Dollarzähler.

## Lokal starten

Voraussetzungen: Python 3.12+ und `uv`.

```bash
cp .env.example .env
uv sync --extra dev
uv run --env-file .env alembic upgrade head
uv run --env-file .env uvicorn app.main:app --reload
```

Im Entwicklungsmodus lautet das lokale Standardpasswort `classbrief`, solange `APP_PASSWORD_HASH` leer ist. Das ist in Produktion absichtlich nicht erlaubt. Ein Passwort-Hash lässt sich mit `uv run python scripts/hash_password.py` erzeugen.

## Konfiguration

Mindestens folgende Produktionswerte gehören als geschützte Umgebungsvariablen auf den Server:

```text
APP_PASSWORD_HASH
SESSION_SECRET
CREDENTIAL_ENCRYPTION_KEY
CRON_SECRET
ISERV_WEBDAV_URL
ISERV_USERNAME
ISERV_PASSWORD
```

`CREDENTIAL_ENCRYPTION_KEY` muss unabhängig von `SESSION_SECRET` erzeugt werden. Geheimnisse, die SQLite-Datenbank und lokale `.env`-Dateien sind über `.gitignore` und `.dockerignore` ausgeschlossen.

## Fly.io

```bash
fly volumes create classbrief_data --region fra --size 1
fly secrets set APP_PASSWORD_HASH=... SESSION_SECRET=... CREDENTIAL_ENCRYPTION_KEY=... CRON_SECRET=... \
  ISERV_WEBDAV_URL=... ISERV_USERNAME=... ISERV_PASSWORD=...
fly deploy
```

`min_machines_running = 1` hält eine Machine in Frankfurt bereit, damit Zeitregeln ohne eingeschalteten Privatcomputer ausgelöst werden. Das persistente Volume enthält SQLite und verschlüsselte OAuth-Daten.

## Automatik

ClassBrief wertet die gespeicherten Regeln minütlich in der Zeitzone `Europe/Berlin` aus. Vor der KI-Arbeit wird die Zielstunde aus dem aktuellen Plan aufgelöst. Ein stabiler Marker verhindert doppelte Ausführung. Bei reinem Plugin-Betrieb können dieselben Regeln stattdessen einmalig mit geplanten ChatGPT-Aufgaben synchronisiert werden.

Der geschützte Endpoint `/internal/tick` bleibt für externe Scheduler und Diagnose erhalten.

## Verarbeitung und Datenschutz

- WebDAV nutzt TLS-Validierung und iterative `Depth: 1`-Abfragen.
- Originaldateien liegen nur temporär auf dem Dateisystem und werden nach der Extraktion gelöscht.
- ETag, Änderungszeit, Größe und SHA-256 vermeiden unnötige Downloads und doppelte Analyse.
- Normale Seitenaufrufe starten weder WebDAV- noch KI-Arbeit.
- KI-Anfragen werden nicht für spätere API-Abrufe gespeichert (`store: false`).
- Logs enthalten keine Zugangsdaten, Dokumenttexte oder vollständigen Prompts.
- Die ChatGPT-Kopplung speichert den einmaligen Code nur als SHA-256-Hash und OAuth-Daten verschlüsselt.
- Beim Trennen wird das Refresh-Token widerrufen und der lokale Datensatz gelöscht.

## Pluginquelle

Die Pluginquelle liegt unter `plugin/classbrief`. Eine installierbare Verbindung darf erst nach dem Deployment mit der tatsächlich erreichbaren HTTPS-Adresse erzeugt werden. Die OAuth-Clientregistrierung erfolgt dynamisch; Zugangsdaten gehören niemals in das Pluginarchiv.

## Tests

```bash
uv run pytest
```

Externe WebDAV-, Pebbule- und KI-Dienste werden in Tests nicht angesprochen.

## Lizenz

MIT
