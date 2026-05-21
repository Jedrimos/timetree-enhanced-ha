# TimeTree Enhanced für Home Assistant

Verbesserte TimeTree-Integration mit **pro-Mitglied-Kalendern**, **wiederkehrenden Terminen (Geburtstage, Jahrestage)**, **Label-Erkennung** und vollem Write-Support.

---

## Unterschiede zur Original-Integration

| Feature | Original | TimeTree Enhanced |
|---|---|---|
| Kalender-Entities | 1 (alles gemischt) | 1 pro Mitglied + 1 „Alle" |
| Titelformat | roher Titel | `Mitglied · Termin` |
| Wiederkehrende Termine | ✗ | ✓ (Geburtstage, Jahrestage) |
| Ganztags-Events | teils fehlerhaft | korrekt normalisiert |
| Feiertage | ✗ | korrekt im „Alle"-Kalender |
| Mitglieder-Erkennung | – | Präfix `Name:` + Label-Fallback |
| Neue Mitglieder | Neustart nötig | automatisch erkannt |
| Event erstellen | ✓ | ✓ (Mitglied wird auto-vorgesetzt) |
| Sensoren | – | last_updated + Anzahl pro Mitglied |
| Zeit-Attribute | – | next_event_time, next_event_date, … |
| Optionen-Flow | – | Intervall + Vorschauzeitraum |

---

## Installation

### HACS (empfohlen)
1. HACS → Integrationen → ⋮ → Benutzerdefinierte Repositories
2. URL: `https://github.com/jedrimos/timetree-enhanced-ha` → Kategorie: **Integration**
3. „TimeTree Enhanced" suchen und herunterladen
4. Home Assistant neu starten

### Manuell
1. Ordner `custom_components/timetree_enhanced` nach `/config/custom_components/` kopieren
2. Restart

---

## Einrichtung

**Einstellungen → Geräte & Dienste → + Integration hinzufügen → TimeTree Enhanced**

1. E-Mail + Passwort eingeben
2. Kalender aus der Liste wählen
3. Zeitzone, Vorschaufenster (Tage) und Sync-Intervall einstellen

---

## Wie die Mitglieder-Erkennung funktioniert

### Strategie 1 – Name-Präfix im Titel (Priorität)

Termine in der Form `Mama: Zahnarzt` → Integration erkennt `Mama` als Mitglied und zeigt den Termin als **`Mama · Zahnarzt`** an.

Der Termin erscheint:
- im Kalender **„Familienkalender – Mama"**
- im Kalender **„Familienkalender – Alle"**

### Strategie 2 – TimeTree-Label (Fallback)

Wenn kein `Name:`-Präfix gefunden wird, aber das Event ein Label mit einem echten Namen hat (z.B. Label 1 wurde in „Papa" umbenannt), wird dieses als Mitglied verwendet.

### Termine ohne erkennbares Mitglied

Tauchen nur im **„Alle"**-Kalender auf, nicht in einem Mitglieder-Kalender.

---

## Wiederkehrende Termine (Geburtstage, Jahrestage)

Die Integration nutzt den Range-Endpoint der TimeTree-API, der **alle Instanzen** wiederkehrender Termine liefert. Geburtstage und Jahrestage werden dadurch korrekt im Kalender angezeigt.

**Cache-Fenster**: 14 Tage zurück + konfigurierbares Vorschaufenster (Standard: 60 Tage).

---

## Events erstellen (per Automation/Skript)

```yaml
service: calendar.create_event
target:
  entity_id: calendar.timetree_enhanced_familienkalender_mama
data:
  summary: "Zahnarzt"           # Wird auto zu "Mama: Zahnarzt" in TimeTree
  description: "Bitte nüchtern kommen"
  start_date_time: "2025-03-15 10:00:00"
  end_date_time:   "2025-03-15 11:00:00"
  location: "Praxis Dr. Müller"
```

---

## Kalender-Entity Attribute

| Attribut | Beschreibung |
|---|---|
| `next_event_summary` | Titel des nächsten Termins |
| `next_event_time` | Uhrzeit (`10:30`) oder `Ganztags` |
| `next_event_date` | Datum (`15.03.2025`) |
| `next_event_start` | ISO-Timestamp Start |
| `next_event_end` | ISO-Timestamp Ende |
| `next_event_all_day` | `true`/`false` |
| `next_event_location` | Ort |
| `next_event_description` | Beschreibung |
| `member` | Mitgliedsname (nur Mitglieder-Entities) |
| `color_hint` | Farbe für Custom Cards (nur Mitglieder-Entities) |

---

## Sensoren

| Sensor | Beschreibung |
|---|---|
| `sensor.timetree_enhanced_familienkalender_zuletzt_synchronisiert` | Zeitstempel des letzten Syncs |
| `sensor.timetree_enhanced_familienkalender_mama_anzahl` | Anzahl bevorstehender Termine für Mama |
| `sensor.timetree_enhanced_familienkalender_papa_anzahl` | Anzahl bevorstehender Termine für Papa |

---

## Debugging

```yaml
# configuration.yaml
logger:
  default: info
  logs:
    custom_components.timetree_enhanced: debug
```

---

## Bekannte Einschränkungen

- **Cache-Fenster**: Nur Termine der letzten 14 Tage bis zum konfigurierten Vorschauzeitraum sind im Cache.
- **Cloud-Polling**: Benötigt Internetverbindung. Kein Webhook-Support (TimeTree-API-Limitierung).
- **Interne API**: Basiert auf dem reverse-engineerten APP-API – kann bei TimeTree-Updates brechen.

---

## Credits

Basiert auf [acdcnow/Timetree-Import-for-Home-Assistant](https://github.com/acdcnow/Timetree-Import-for-Home-Assistant) und dem API-Reverse-Engineering von [eoleedi/TimeTree-Exporter](https://github.com/eoleedi/TimeTree-Exporter). Erweitert und überarbeitet von Jedrimos.
