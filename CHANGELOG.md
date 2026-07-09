# Changelog

Alle nennenswerten Änderungen an diesem Projekt werden hier dokumentiert.

Format basiert auf [Keep a Changelog](https://keepachangelog.com/de/1.0.0/).

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
