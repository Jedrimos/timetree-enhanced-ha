# TimeTree Enhanced für Home Assistant

[![HACS](https://img.shields.io/badge/HACS-Integration-41BDF5.svg)](https://hacs.xyz)
![HA Version](https://img.shields.io/badge/Home%20Assistant-2024.12%2B-blue.svg)
[![Release](https://img.shields.io/github/v/release/Jedrimos/timetree-enhanced-ha)](https://github.com/Jedrimos/timetree-enhanced-ha/releases)
[![Validate](https://github.com/Jedrimos/timetree-enhanced-ha/actions/workflows/validate.yaml/badge.svg)](https://github.com/Jedrimos/timetree-enhanced-ha/actions/workflows/validate.yaml)
![Lizenz](https://img.shields.io/github/license/Jedrimos/timetree-enhanced-ha)

Eine erweiterte [TimeTree](https://timetreeapp.com)-Integration für Home Assistant — mit automatischen Sub-Kalendern pro Person, vollständiger Unterstützung für wiederkehrende Termine und Label-basierter Mitgliedererkennung.

> **English:** TimeTree Enhanced brings your [TimeTree](https://timetreeapp.com) calendars into Home Assistant: one calendar entity for all events plus one per label (e.g. per family member), full support for recurring events (RRULE incl. BYDAY and deleted occurrences), all-day and multi-day events, creating events from automations, per-label event counters and a re-authentication flow. Sign in with your TimeTree e-mail and password — no developer token required. The UI is available in English and German.

> **Mehr Automatisierungen, Integrationen und Tutorials rund um Home Assistant findest du demnächst auf [www.jedrimos.de](https://www.jedrimos.de)**

---

## Features im Überblick

| Feature | Original | TimeTree Enhanced |
|---|---|---|
| Kalender-Entities | 1 (alles gemischt) | 1 pro Label/Person + 1 „Alle" |
| Titelformat | Roher Titel | `Person: Termin` |
| Wiederkehrende Termine | ✗ | ✓ volle RRULE inkl. Wochentage & gelöschter Einzeltermine |
| Mehrtägige Termine | teils fehlerhaft | ✓ jeden Tag korrekt angezeigt |
| Ganztags-Events | teils fehlerhaft | ✓ korrekt normalisiert |
| Label-Farben | – | ✓ als `color_hint` Attribut |
| Notizen & Ort | – | ✓ übernommen |
| Event erstellen | ✓ | ✓ mit auto. Label-Zuweisung |
| Sensoren | – | Letzte Sync-Zeit + Anzahl pro Person |
| Session-Persistenz | – | ✓ kein Re-Login nach HA-Neustart |
| Optionen-Flow | – | ✓ Intervall & Vorschauzeitraum |
| Re-Authentifizierung | – | ✓ bei geändertem Passwort |
| Diagnose-Download | – | ✓ mit geschwärzten Zugangsdaten |

---

## Installation

### HACS (empfohlen)

[![In HACS öffnen](https://my.home-assistant.io/badges/hacs_repository.svg)](https://my.home-assistant.io/redirect/hacs_repository/?owner=Jedrimos&repository=timetree-enhanced-ha&category=integration)

1. In HACS nach **„TimeTree Enhanced"** suchen und herunterladen
   *(falls noch nicht im HACS-Store: HACS → ⋮ → **Benutzerdefinierte Repositories** → `https://github.com/Jedrimos/timetree-enhanced-ha`, Kategorie **Integration**)*
2. Home Assistant neu starten

### Manuell

1. `timetree_enhanced.zip` aus dem [neuesten Release](https://github.com/Jedrimos/timetree-enhanced-ha/releases/latest) herunterladen
2. Nach `/config/custom_components/timetree_enhanced/` entpacken
3. Home Assistant neu starten

---

## Einrichtung

[![Integration einrichten](https://my.home-assistant.io/badges/config_flow_start.svg)](https://my.home-assistant.io/redirect/config_flow_start/?domain=timetree_enhanced)

**Einstellungen → Geräte & Dienste → + Integration hinzufügen → TimeTree Enhanced**

1. E-Mail-Adresse und Passwort des TimeTree-Accounts eingeben
2. Kalender aus der Liste auswählen
3. Zeitzone, Vorschaufenster (Tage) und Sync-Intervall konfigurieren

Ändert sich später das TimeTree-Passwort, meldet Home Assistant eine **erneute Anmeldung** unter *Einstellungen → Geräte & Dienste*.

---

## So funktioniert die Sub-Kalender-Erkennung

Die Integration legt automatisch einen eigenen Kalender-Entity für jedes **Label** an, dem im konfigurierten Vorschauzeitraum mindestens ein Termin zugeordnet ist.

**Beispiel:** Wenn der TimeTree-Kalender „Familienkalender" die Labels `Elias`, `Domi`, `Miri`, `Zusammen` und `Geburtstag` enthält und alle davon bevorstehende Termine haben, entstehen folgende Entities (bei deutscher HA-Sprache):

```
calendar.familienkalender_alle
calendar.familienkalender_elias
calendar.familienkalender_domi
calendar.familienkalender_miri
calendar.familienkalender_zusammen
calendar.familienkalender_geburtstag
```

Alle Entities eines Kalenders sind unter einem gemeinsamen Geräte-Eintrag gruppiert. Labels ohne bevorstehende Termine sowie Standard-Labels („Label 1" …) werden ignoriert — es werden keine leeren Kalender angelegt. Neue Labels erscheinen nach einem Neuladen der Integration.

---

## Wiederkehrende Termine

Die Integration expandiert wiederkehrende Termine (Geburtstage, Jahrestage, Feiertage, regelmäßige Meetings) vollständig im konfigurierten Zeitfenster — mit der kompletten RRULE-Syntax (`FREQ`, `INTERVAL`, `UNTIL`, `COUNT`, `BYDAY`, `BYMONTHDAY`, `BYSETPOS`, …).

- Serien werden in der Zeitzone des Termins berechnet → ein Termin um 08:00 bleibt auch nach der Zeitumstellung um 08:00
- In TimeTree gelöschte Einzeltermine (`EXDATE`) werden nicht angezeigt; einzeln bearbeitete Termine erscheinen mit ihren geänderten Daten
- Abgeschlossene Serien (UNTIL/COUNT überschritten) werden nicht erneut angezeigt

---

## Events erstellen

Events können per Automation oder Skript angelegt werden — im „Alle"-Kalender ohne Label, in einem Sub-Kalender wird das Label automatisch gesetzt. Wiederkehrende Termine können (noch) nicht aus Home Assistant angelegt werden.

```yaml
action: calendar.create_event
target:
  entity_id: calendar.familienkalender_elias
data:
  summary: "Krippe geschlossen"
  description: "Bitte früher abholen"
  start_date_time: "2025-06-15 08:00:00"
  end_date_time: "2025-06-15 17:00:00"
  location: "Krippe Musterstraße"
```

Ganztägige Events:

```yaml
action: calendar.create_event
target:
  entity_id: calendar.familienkalender_zusammen
data:
  summary: "Urlaub"
  start_date: "2025-07-01"
  end_date: "2025-07-14"
```

---

## Kalender-Attribute

Jeder Kalender-Entity enthält diese Attribute für Automationen und Dashboards:

| Attribut | Beschreibung |
|---|---|
| `next_event_summary` | Titel des nächsten Termins |
| `next_event_time` | Uhrzeit in HA-Ortszeit (`10:30`) oder `Ganztags` |
| `next_event_date` | Datum (`15.06.2025`) |
| `next_event_start` | ISO-Timestamp Beginn |
| `next_event_end` | ISO-Timestamp Ende |
| `next_event_all_day` | `true` / `false` |
| `next_event_location` | Ort des Termins |
| `next_event_description` | Notiz / Beschreibung |
| `member` | Labelname (nur Sub-Kalender) |
| `color_hint` | Labelfarbe als Hex (nur Sub-Kalender) |

---

## Sensoren

| Sensor | Beschreibung |
|---|---|
| `sensor.…_zuletzt_synchronisiert` | Zeitpunkt des letzten erfolgreichen Syncs (Diagnose) |
| `sensor.…_elias_anzahl` | Anzahl bevorstehender Termine im Vorschauzeitraum für das Label „Elias" |

Die Sensornamen orientieren sich am gewählten Kalendernamen.

---

## Automations-Beispiel

```yaml
automation:
  alias: "Elias – Termin heute Erinnerung"
  triggers:
    - trigger: time
      at: "07:00:00"
  conditions:
    - condition: template
      value_template: >
        {{ state_attr('calendar.familienkalender_elias', 'next_event_date')
           == now().strftime('%d.%m.%Y') }}
  actions:
    - action: notify.mobile_app_mein_handy
      data:
        title: "Elias heute"
        message: >
          {{ state_attr('calendar.familienkalender_elias', 'next_event_time') }}
          – {{ state_attr('calendar.familienkalender_elias', 'next_event_summary') }}
```

---

## Debugging

Bei Problemen zuerst das Debug-Logging aktivieren:

```yaml
# configuration.yaml
logger:
  default: info
  logs:
    custom_components.timetree_enhanced: debug
```

Dann Home Assistant neu starten und das Log unter **Einstellungen → System → Protokoll** prüfen.

Für Bug-Reports bitte zusätzlich die **Diagnose** herunterladen (*Einstellungen → Geräte & Dienste → TimeTree Enhanced → ⋮ → Diagnose herunterladen*) — Zugangsdaten und Termintexte werden darin automatisch geschwärzt.

---

## Bekannte Einschränkungen

- **Interne API**: TimeTree hat seine offizielle Entwickler-API 2023 eingestellt. Die Integration nutzt die (reverse-engineerte) API der TimeTree-Web-App — sie kann bei Änderungen durch TimeTree brechen
- **Zugangsdaten**: E-Mail und Passwort werden (wie bei allen HA-Integrationen) in der HA-Konfiguration gespeichert; Login per Google/Apple/LINE wird nicht unterstützt — ggf. in TimeTree ein Passwort setzen
- **Cloud-Polling**: Benötigt Internetverbindung; kein Webhook-Support
- **Mitglieder-Erkennung**: Basiert auf Labels, nicht auf Kalender-Mitgliedern
- **Zeitfenster**: Nur Termine innerhalb des konfigurierten Vorschauzeitraums werden geladen

---

## Optionen anpassen

Nach der Einrichtung können Sync-Intervall und Vorschauzeitraum jederzeit angepasst werden:

**Einstellungen → Geräte & Dienste → TimeTree Enhanced → Konfigurieren**

---

## Entwicklung

```bash
pip install -r requirements_test.txt ruff
ruff check . && ruff format --check .
pytest
```

**Releases laufen automatisch:** Version in `custom_components/timetree_enhanced/manifest.json` erhöhen, einen passenden Abschnitt `## [x.y.z]` in `CHANGELOG.md` anlegen und den PR nach `main` mergen. Der Workflow `release.yaml` erstellt dann Tag `vx.y.z`, die GitHub-Release mit den Changelog-Notizen und `timetree_enhanced.zip` für HACS. Die PR-Prüfung schlägt fehl, wenn Integrationscode geändert, aber die Version nicht erhöht wurde.

---

## Credits

Basiert auf [acdcnow/Timetree-Import-for-Home-Assistant](https://github.com/acdcnow/Timetree-Import-for-Home-Assistant) und dem API-Reverse-Engineering von [eoleedi/TimeTree-Exporter](https://github.com/eoleedi/TimeTree-Exporter).

Entwickelt und erweitert von [@Jedrimos](https://github.com/Jedrimos) — weitere Projekte und Home Assistant Tutorials demnächst auf [www.jedrimos.de](https://www.jedrimos.de).

---

## Lizenz

MIT — Details siehe [LICENSE](LICENSE).
