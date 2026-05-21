# TimeTree Enhanced für Home Assistant

![HACS Badge](https://img.shields.io/badge/HACS-Custom-orange.svg)
![HA Version](https://img.shields.io/badge/Home%20Assistant-2024.1%2B-blue.svg)
![Lizenz](https://img.shields.io/github/license/jedrimos/timetree-enhanced-ha)

Eine erweiterte [TimeTree](https://timetreeapp.com)-Integration für Home Assistant — mit automatischen Sub-Kalendern pro Person, vollständiger Unterstützung für wiederkehrende Termine und Label-basierter Mitgliedererkennung.

> **Mehr Automatisierungen, Integrationen und Tutorials rund um Home Assistant findest du demnächst auf [www.jedrimos.de](https://www.jedrimos.de)**

---

## Features im Überblick

| Feature | Original | TimeTree Enhanced |
|---|---|---|
| Kalender-Entities | 1 (alles gemischt) | 1 pro Label/Person + 1 „Alle" |
| Titelformat | Roher Titel | `Person: Termin` |
| Wiederkehrende Termine | ✗ | ✓ (Geburtstage, Feiertage, Serien) |
| Mehrtägige Termine | teils fehlerhaft | ✓ jeden Tag korrekt angezeigt |
| Ganztags-Events | teils fehlerhaft | ✓ korrekt normalisiert |
| Label-Farben | – | ✓ als `color_hint` Attribut |
| Notizen & Ort | – | ✓ übernommen |
| Event erstellen | ✓ | ✓ mit auto. Label-Zuweisung |
| Sensoren | – | Letzte Sync-Zeit + Anzahl pro Person |
| Session-Persistenz | – | ✓ kein Re-Login nach HA-Neustart |
| Optionen-Flow | – | ✓ Intervall & Vorschauzeitraum |

---

## Installation

### HACS (empfohlen)

1. HACS → Integrationen → ⋮ → **Benutzerdefinierte Repositories**
2. URL: `https://github.com/jedrimos/timetree-enhanced-ha` → Kategorie: **Integration**
3. „TimeTree Enhanced" suchen und installieren
4. Home Assistant neu starten

### Manuell

1. Den Ordner `custom_components/timetree_enhanced` nach `/config/custom_components/` kopieren
2. Home Assistant neu starten

---

## Einrichtung

**Einstellungen → Geräte & Dienste → + Integration hinzufügen → TimeTree Enhanced**

1. E-Mail-Adresse und Passwort des TimeTree-Accounts eingeben
2. Kalender aus der Liste auswählen
3. Zeitzone, Vorschaufenster (Tage) und Sync-Intervall konfigurieren

---

## So funktioniert die Sub-Kalender-Erkennung

Die Integration legt automatisch einen eigenen Kalender-Entity für jedes **Label** an, dem im konfigurierten Vorschauzeitraum mindestens ein Termin zugeordnet ist.

**Beispiel:** Wenn ein TimeTree-Kalender die Labels `Elias`, `Domi`, `Miri`, `Zusammen` und `Geburtstag` enthält und alle davon bevorstehende Termine haben, entstehen folgende Entities:

```
calendar.timetree_enhanced_familienkalender_alle
calendar.timetree_enhanced_familienkalender_elias
calendar.timetree_enhanced_familienkalender_domi
calendar.timetree_enhanced_familienkalender_miri
calendar.timetree_enhanced_familienkalender_zusammen
calendar.timetree_enhanced_familienkalender_geburtstag
```

Labels ohne bevorstehende Termine werden ignoriert — es werden keine leeren Kalender angelegt.

---

## Wiederkehrende Termine

Die Integration expandiert wiederkehrende Termine (Geburtstage, Jahrestage, Feiertage, regelmäßige Meetings) vollständig im konfigurierten Zeitfenster. Unterstützte RRULE-Parameter:

- `FREQ`: DAILY, WEEKLY, MONTHLY, YEARLY
- `INTERVAL`: jede N-te Wiederholung
- `UNTIL`: Serie endet an einem Datum
- `COUNT`: Serie hat eine feste Anzahl von Vorkommen

Serien die bereits abgeschlossen sind (UNTIL/COUNT überschritten) werden nicht erneut im Kalender angezeigt.

---

## Events erstellen

Events können per Automation oder Skript direkt in einem Sub-Kalender angelegt werden. Das Label wird automatisch gesetzt.

```yaml
service: calendar.create_event
target:
  entity_id: calendar.timetree_enhanced_familienkalender_elias
data:
  summary: "Krippe geschlossen"
  description: "Bitte früher abholen"
  start_date_time: "2025-06-15 08:00:00"
  end_date_time: "2025-06-15 17:00:00"
  location: "Krippe Musterstraße"
```

Ganztägige Events:

```yaml
service: calendar.create_event
target:
  entity_id: calendar.timetree_enhanced_familienkalender_zusammen
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
| `next_event_time` | Uhrzeit (`10:30`) oder `Ganztags` |
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
| `sensor.…_zuletzt_synchronisiert` | Zeitpunkt des letzten erfolgreichen Syncs |
| `sensor.…_elias_anzahl` | Anzahl bevorstehender Termine für das Label „Elias" |

Die Sensornamen orientieren sich am gewählten Kalendernamen.

---

## Automations-Beispiel

```yaml
automation:
  alias: "Elias – Termin heute Erinnerung"
  trigger:
    - platform: time
      at: "07:00:00"
  condition:
    - condition: template
      value_template: >
        {{ state_attr('calendar.timetree_enhanced_familienkalender_elias', 'next_event_date')
           == now().strftime('%d.%m.%Y') }}
  action:
    - service: notify.mobile_app_mein_handy
      data:
        title: "Elias heute"
        message: >
          {{ state_attr('calendar.timetree_enhanced_familienkalender_elias', 'next_event_time') }}
          – {{ state_attr('calendar.timetree_enhanced_familienkalender_elias', 'next_event_summary') }}
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

---

## Bekannte Einschränkungen

- **Interne API**: Basiert auf dem reverse-engineerten TimeTree APP-API — kann bei Updates von TimeTree brechen
- **Cloud-Polling**: Benötigt Internetverbindung; kein Webhook-Support
- **Mitglieder-Erkennung**: Basiert auf Labels, nicht auf Email-Einladungen (API gibt keine Mitgliederliste zurück)
- **Zeitfenster**: Nur Termine innerhalb des konfigurierten Vorschauzeitraums werden geladen

---

## Optionen anpassen

Nach der Einrichtung können Sync-Intervall und Vorschauzeitraum jederzeit angepasst werden:

**Einstellungen → Geräte & Dienste → TimeTree Enhanced → Konfigurieren**

---

## Credits

Basiert auf [acdcnow/Timetree-Import-for-Home-Assistant](https://github.com/acdcnow/Timetree-Import-for-Home-Assistant) und dem API-Reverse-Engineering von [eoleedi/TimeTree-Exporter](https://github.com/eoleedi/TimeTree-Exporter).

Entwickelt und erweitert von [@Jedrimos](https://github.com/Jedrimos) — weitere Projekte und Home Assistant Tutorials demnächst auf [www.jedrimos.de](https://www.jedrimos.de).

---

## Lizenz

MIT — Details siehe [LICENSE](LICENSE).
