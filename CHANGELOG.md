# Changelog

Alle nennenswerten Änderungen an diesem Projekt werden hier dokumentiert.

Format basiert auf [Keep a Changelog](https://keepachangelog.com/de/1.0.0/).

---

## [1.1.0] – 2026-11-22

### Bugfixes

- **Termine erstellen funktionierte nicht** — die Kalender meldeten das Feature `CREATE_EVENT` nicht an, `calendar.create_event` wurde von Home Assistant abgelehnt. Außerdem ging der Request an einen falschen Endpoint (`calendars/{id}/events`) ohne CSRF-Token. Jetzt: korrekter Endpoint `calendar/{id}/event`, CSRF-Token, automatischer Re-Login bei abgelaufener Session
- **Ganztags-Termine beim Erstellen einen Tag zu lang** — HA liefert ein exklusives Enddatum, TimeTree erwartet ein inklusives
- **Optionen-Dialog („Konfigurieren") stürzte ab** — der Options-Flow wurde mit einem Argument erzeugt, das `OptionsFlow` nicht akzeptiert
- **Wiederkehrende Termine um 1 Stunde verschoben nach Sommer-/Winterzeit-Wechsel** — Serien werden jetzt in der Zeitzone des Termins expandiert
- **Gelöschte Einzeltermine einer Serie wurden trotzdem angezeigt** — `EXDATE` wird jetzt ausgewertet
- **Serien mit mehreren Wochentagen** (z.B. Mo/Mi/Fr) erschienen nur an einem Tag — volle RRULE-Unterstützung (`BYDAY`, `BYMONTHDAY`, `BYSETPOS`, …) über `python-dateutil`
- **`next_event_time` / `next_event_date` zeigten UTC statt Ortszeit**
- **Kein Request-Timeout** — hängende Verbindungen blockierten das Update bis zu 5 Minuten; jetzt 30 s
- **Doppelter API-Abruf beim Start** durch `update_before_add`
- **Große Kalender abgeschnitten** — Sync-Pagination von 20 auf 100 Chunks erhöht, mit Warnung im Log

### Neu

- **Re-Authentifizierung** — bei geändertem TimeTree-Passwort erscheint eine Reparatur-Meldung statt endloser Fehler
- **Diagnose-Download** (Zugangsdaten und Termintexte werden geschwärzt)
- **Geräte-Eintrag** pro Kalender, alle Entities darunter gruppiert
- **Übersetzte Entity-Namen** (Deutsch/Englisch)
- **Brand-Icon** direkt in der Integration (HA ≥ 2026.3)
- Vorschauzeitraum jetzt bis 365 Tage, Sync-Intervall bis 240 Minuten
- Zeitzone wird bei der Einrichtung mit der HA-Zeitzone vorbelegt
- Termine aus Serien erhalten eine `recurrence_id`

### Technisch

- Neuer `DataUpdateCoordinator` mit zentralem Login/Re-Login, Session-Cookie bleibt über Neustarts erhalten und wird beim Entfernen der Integration gelöscht
- Nicht funktionierende Mitglieder-Endpoints entfernt (3 unnötige Requests pro Start)
- Toter Code (`helpers.py`) entfernt
- Tests (pytest), Ruff, hassfest- und HACS-Validierung in CI; automatischer Release beim Merge auf `main`
- Mindestversion Home Assistant: **2024.12**

### Hinweis zum Update

Entity-IDs bleiben unverändert. Die Anzeigenamen werden jetzt aus Geräte- und Entity-Namen gebildet (z.B. „Familie Alle" statt „Familie – Alle").

---

## [1.0.1] – 2026-07-09

### Bugfixes

- **HTTP 400 auf Sync-Endpoint** — TimeTree verlangt seit Juli 2026 den `since=0`-Parameter beim ersten Aufruf zwingend; wurde vorher weggelassen und führte zum kompletten Ausfall der Integration
- **Code -493 als Auth-Fehler erkannt** — HTTP 400 mit Fehlercode -493 wird jetzt korrekt als abgelaufene Session behandelt und löst automatischen Re-Login aus statt die Integration dauerhaft zu stoppen

---

## [1.0.0] – 2026-05-22

Erster stabiler Release. 🎉

### Neu

- **Sub-Kalender pro Label** — für jedes TimeTree-Label mit bevorstehenden Terminen wird automatisch ein eigener Kalender-Entity angelegt (z.B. `Elias`, `Domi`, `Miri`, `Zusammen`, `Geburtstag`)
- **Terminanzeige als „Label: Titel"** — z.B. `Elias: Krippe geschlossen`
- **Wiederkehrende Termine** — vollständige RRULE-Unterstützung mit `FREQ` (DAILY/WEEKLY/MONTHLY/YEARLY), `INTERVAL`, `UNTIL` und `COUNT`; abgeschlossene Serien werden nicht neu angezeigt
- **Mehrtägige Termine** — erscheinen korrekt an jedem Tag den sie dauern
- **Ganztags-Events** — inklusive-zu-exklusive Endkorrektur gemäß iCal-Standard
- **Notizen & Ort** — werden aus TimeTree übernommen und als Kalender-Attribute bereitgestellt
- **Label-Farben** — als `color_hint`-Attribut für Custom Cards nutzbar
- **Session-Persistenz** — der Login-Cookie wird über HA-Neustarts hinweg gespeichert; verhindert HTTP 429 (Rate Limiting)
- **Optionen-Flow** — Sync-Intervall (5–120 min) und Vorschauzeitraum (7–180 Tage) jederzeit anpassbar ohne Neueinrichtung
- **Sensoren** — Zeitstempel der letzten Synchronisierung + Terminanzahl pro Label
- **Event erstellen** — `calendar.create_event` setzt Label automatisch bei Sub-Kalendern

### Bugfixes

- **MONTHLY-Überlauf**: Monatlich wiederkehrende Termine am 31. (z.B. Geburtstag 31. Januar) führten zu einem `ValueError` beim Sprung in kürzere Monate (Februar), was die gesamte Terminserie zum Schweigen brachte — behoben durch Clamp auf die tatsächliche Monatslänge
- **Session-Leak**: Wenn der erste Datenabruf beim Setup fehlschlug, wurde `api.close()` nie aufgerufen und die `aiohttp.ClientSession` leckte — behoben durch garantiertes Cleanup
- **Fehlende `label_id` beim Event-Erstellen**: Events die über einen Sub-Kalender angelegt wurden, hatten kein Label in TimeTree — behoben

### Technisch

- Eigene `aiohttp.ClientSession` mit `CookieJar(unsafe=True)` statt der geteilten HA-Session
- Sync-Endpoint (`/calendar/{id}/events/sync`) mit Chunk-Pagination statt veralteter Endpoints
- Labels-Endpoint (`/calendar/{id}/labels`) für ID-zu-Name-Mapping
- Skip-ahead-Optimierung für RRULE-Expansion (Berechnung in Schritten, nicht Zeiteinheiten)

---

> Weitere Updates und Home Assistant Tutorials demnächst auf [www.jedrimos.de](https://www.jedrimos.de)
