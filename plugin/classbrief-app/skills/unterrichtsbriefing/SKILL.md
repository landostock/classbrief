---
name: unterrichtsbriefing
description: Verwende diesen Workflow, wenn der Nutzer für die nächste Stunde, eine ausgewählte Stunde oder den heutigen Schultag gebrieft werden möchte oder wiederkehrende ClassBrief-Briefings einrichten will.
---

# ClassBrief-Unterrichtsbriefing

Nutze ausschließlich die ClassBrief-Werkzeuge für private Stundenplan- und IServ-Daten. Allgemeines Fachwissen darf Erklärungen verständlicher machen, aber niemals ein angeblich bevorstehendes Unterrichtsthema begründen.

## Interaktives Briefing

1. Rufe zuerst `list_briefing_targets` auf. Bei „nächste Stunde“ verwende `scope=next`, bei „heute“ `scope=today`.
2. Ist das Ziel durch den Wunsch und den Stundenplan eindeutig, frage nicht noch einmal nach Fach oder Stunde.
3. Rufe für die ausgewählte Stunde `prepare_briefing_context` auf, bevor du Fragen formulierst.
4. Behandle „Brief mich“ standardmäßig als Ein-Klick-Auftrag: Wenn der Kontext eine nützliche Vorbereitung erlaubt, erzeuge und speichere sie sofort ohne Rückfrage. Stelle nur dann höchstens zwei kurze, inhaltlich konkrete Fragen, wenn eine echte inhaltliche Unklarheit das Ergebnis wesentlich verändern würde. Geeignet sind zum Beispiel:
   - Schwerpunkt zwischen zwei tatsächlich in den gelieferten Materialien erkennbaren Teilthemen;
   - ob ein als unsicher gekennzeichneter Stand aus der letzten Stunde zutrifft;
   - ob der Nutzer eher Begriffe, Zusammenhänge oder Aufgaben üben möchte.
5. Stelle keine generischen Fragen, die der Stundenplan, das Fachgedächtnis oder die Materialien bereits beantworten. Frage niemals nach Ausführlichkeit, Ausgabeformat oder einer Bestätigung zum Start.
6. Frage niemals nach der gewünschten Länge. Erzeuge immer alle drei Fassungen: ungefähr 30 Sekunden, ungefähr 2 Minuten und ausführliche Details.
7. Erzeuge nach den Antworten ein Briefing mit `probable_topic`, `quick_brief`, `medium_brief` und `deep`. Trenne belegte Aussagen klar von Vermutungen. Fülle zusätzlich `deep.presentation.quick`, `deep.presentation.medium` und `deep.presentation.deep` mit übersichtlichen Darstellungsblöcken. Die Website unterstützt `heading`, `paragraph`, `bullets`, `numbered`, `checklist`, `callout` und `key_value`; nutze nie HTML.
8. Erzeuge in `deep.answer_cards` drei bis sechs wahrscheinliche Rückfragen mit kurzen, direkt aussprechbaren Musterantworten. Beziehe den bestätigten Stand der letzten Stunden, das aktuelle Thema und neue Materialien ein. Kennzeichne die Sicherheit jeder Antwort und gib knappe Belegpunkte an.
9. Verwende `deep.connections`, um neue Inhalte ausdrücklich mit bestätigtem Vorwissen zu verbinden.
10. Nenne Dateien und Seiten/Folien als Quellen, wenn sie im Kontext vorhanden sind.
11. Speichere das fertige Ergebnis mit `save_briefing` und genau den `source_hashes` aus dem Kontext.

## Zeitliche Relevanz ist zwingend

- Verwende nur Einträge aus `materials`. Suche nicht selbst im übrigen IServ-Ordner und verlange nicht den gesamten Ordnerinhalt.
- Die Einträge unter `confirmed_recent_lessons`, `confirmed_topics` und `last_confirmed_topic` sind Vorwissen beziehungsweise bereits behandelter Stoff. Behandle sie nicht automatisch als Thema der nächsten Stunde.
- Erscheint ein altes abgeschlossenes Thema überraschend in einem neuen Material, kennzeichne den Zusammenhang als unsicher, solange das Material keine eindeutige Wiederaufnahme zeigt.
- Erfinde keine Themen aus dem restlichen Schuljahr, aus allgemeinen Lehrplänen oder bloßen Dateinamen außerhalb des bereitgestellten Materials.
- Wenn `materials` leer ist, sage ausdrücklich, dass keine neuen relevanten Materialien vorliegen. Gib dann nur eine knappe Wiederholung des bestätigten aktuellen Stands und offene Fragen aus.
- Wenn `selection.confidence=lower` ist, weise knapp auf die unsichere zeitliche Abgrenzung hin.

## Automatik konfigurieren und synchronisieren

Die ClassBrief-Webseite ist die Quelle der Wahrheit für gewünschte Automatikregeln. Wenn der Nutzer eine Automatik einrichten, abgleichen, reparieren oder aktualisieren möchte:

1. Rufe `get_automation_plan` auf.
2. Erstelle oder aktualisiere für jeden Eintrag unter `tasks` genau eine geplante **ChatGPT-Cloud-Aufgabe** mit `schedule.rrule`, `schedule.timezone` und dem gelieferten `prompt`. Verwende normale ChatGPT-Aufgaben ohne lokalen Projektordner oder Computerbindung.
3. Entferne oder deaktiviere bisherige ClassBrief-Aufgaben, deren `automation_key` nicht mehr im aktuellen Plan vorkommt. Verändere keine fremden geplanten Aufgaben.
4. Rufe erst nach erfolgreichem vollständigem Abgleich `confirm_automation_sync` mit der exakten `revision` und allen aktuellen `automation_key`-Werten auf.
5. Berichte knapp, wie viele Cloud-Aufgaben aktiv sind. Weise nicht auf ChatGPT Work als Voraussetzung hin; ein normaler Chat genügt.

Wenn die ChatGPT-Oberfläche das Erstellen geplanter Aufgaben in diesem Chat oder Plan nicht unterstützt, bestätige die Synchronisierung nicht. Erkläre knapp, wo der Nutzer die Funktion aktivieren muss.

## Ablauf eines geplanten Briefings

Geplante Läufe stellen keine Rückfragen und folgen immer dem in ihrem Prompt enthaltenen `automation_key`:

1. Rufe zuerst `resolve_automation_run` auf. Dieses Werkzeug prüft die heute gültigen Stunden und Ausnahmen erneut.
2. Ist der Status nicht `ready` oder ist `targets` leer, bleibe vollständig still. `resolve_automation_run` protokolliert den Leerlauf bereits.
3. Rufe für jedes Ziel `prepare_briefing_context` auf.
4. Erzeuge immer eine 30-Sekunden-Fassung, eine ungefähr zweiminütige Fassung und strukturierte Details – ohne Längenfrage. Fokussiere neue Materialien, Anschluss, Begriffe, wahrscheinliche Aufgaben, Verständnisfallen und direkt aussprechbare Antworten auf wahrscheinliche Rückfragen. Nutze für alle drei Ansichten `deep.presentation` und fülle `deep.answer_cards` sowie `deep.connections`.
5. Speichere jedes Ergebnis mit `save_briefing` und exakt den `source_hashes` aus dem jeweiligen Kontext.
6. Rufe am Ende `complete_automation_run` mit `status=completed` und der Zahl der gespeicherten Briefings auf. Bei einem Fehler verwende, sofern noch möglich, `status=failed` mit einer kurzen sachlichen Notiz.

Ein alter Serienzeitpunkt darf nie ein falsches Briefing erzeugen. Ist eine Stunde entfallen, verschoben oder die Regel auf der Webseite pausiert worden, gilt ausschließlich das Ergebnis von `resolve_automation_run`.

## Web-Zugang wiederherstellen

Wenn der Nutzer sein ClassBrief-Passwort nicht kennt oder einen neuen Zugang für Handy oder Browser benötigt:

1. Frage niemals nach einem Passwort im Chat.
2. Rufe auf ausdrücklichen Wunsch `create_web_access_link` auf.
3. Gib den erzeugten Link direkt als anklickbaren Link aus und nenne die kurze Gültigkeitsdauer.
4. Weise knapp darauf hin, dass das neue Passwort ausschließlich auf der ClassBrief-Seite eingegeben wird und der Link danach verfällt.
5. Erzeuge nicht vorsorglich mehrere Links; jeder neue Link macht den vorherigen ungültig.

## Datenschutz und Fehler

- Frage niemals im Chat nach dem IServ- oder ClassBrief-Passwort und gib keine Zugangsdaten aus. Die einmalige Freigabe erfolgt ausschließlich auf der ClassBrief-OAuth-Seite.
- Zitiere keine kompletten Dokumente; liefere nur die für das Briefing notwendigen Auszüge und Zusammenfassungen.
- Wenn ClassBrief nicht verbunden ist, erkläre knapp, dass zuerst die private Plugin-Verbindung hergestellt werden muss. Erfinde in diesem Fall kein Briefing.
