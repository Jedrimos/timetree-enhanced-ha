# TimeTree Pro für Home Assistant

Verbesserte TimeTree-Integration mit **pro-Mitglied-Kalendern**, **Label-Erkennung** und vollem Write-Support.

---

## Unterschiede zur Original-Integration

| Feature | Original | TimeTree Pro |
|---|---|---|
| Kalender-Entities | 1 (alles gemischt) | 1 pro Mitglied + 1 „Alle" |
| Titelformat | roher Titel | `Mitglied · Termin` |
| Mitglieder-Erkennung | – | Präfix `Name:` + Label-Fallback |
| Neue Mitglieder | Neustart nötig | automatisch erkannt |
| Event erstellen | ✓ | ✓ (Mitglied wird auto-vorgesetzt) |
| Sensoren | last_updated | last_updated + Anzahl pro Mitglied |
| Optionen-Flow | Intervall-Slider | Intervall + Vorschauzeitraum |

---

## Installation

### HACS (empfohlen)
1. HACS → Integrationen → ⋮ → Benutzerdefinierte Repositories
2. URL dieses Repos eingeben, Kategorie: Integration
3. „TimeTree Pro" suchen und herunterladen
4. Home Assistant neu starten

### Manuell
1. Ordner `custom_components/timetree_pro` in `/config/custom_components/` kopieren
2. Restart

---

## Einrichtung

**Einstellungen → Geräte & Dienste → + Integration hinzufügen → TimeTree Pro**

1. E-Mail + Passwort eingeben
2. Kalender aus der Liste wählen
3. Zeitzone, Vorschaufenster (Tage) und Sync-Intervall einstellen

---

## Wie die Mitglieder-Erkennung funktioniert

### Strategie 1 – Name-Präfix im Titel (Priorität)

Ihr schreibt Termine in der Form `Mama: Zahnarzt` → die Integration erkennt `Mama` als Mitglied und zeigt den Termin als **`Mama · Zahnarzt`** an.

Der Termin erscheint:
- im Kalender **„Familienkalender – Mama"**
- im Kalender **„Familienkalender – Alle"**

### Strategie 2 – TimeTree-Label (Fallback)

Wenn kein `Name:`-Präfix gefunden wird, aber das Event ein Label mit einem echten Namen hat (z.B. Label 1 wurde in „Papa" umbenannt), wird dieses als Mitglied verwendet.

### Termine ohne erkennbares Mitglied

Tauchen nur im **„Alle"**-Kalender auf, nicht in einem Mitglieder-Kalender.

---

## Neue Mitglieder – kein Neustart nötig

Wenn ein neuer Name zum ersten Mal in einem Termin auftaucht (z.B. bei einem Neuhaushaltsmitglied), wird **automatisch** eine neue Kalender-Entity erstellt – ohne Neustart.

---

## Events erstellen (per Automation/Skript)

```yaml
service: calendar.create_event
target:
  entity_id: calendar.familienkalender_mama   # Mitglieder-Kalender
data:
  summary: "Zahnarzt"           # Wird auto zu "Mama: Zahnarzt" in TimeTree
  description: "Bitte nüchtern kommen"
  start_date_time: "2025-03-15 10:00:00"
  end_date_time:   "2025-03-15 11:00:00"
  location: "Praxis Dr. Müller"
```

Der Mitgliedsname wird automatisch als Präfix hinzugefügt, sodass der Termin nach dem nächsten Sync korrekt erkannt wird.

---

## Sensoren

| Sensor | Beschreibung |
|---|---|
| `sensor.familienkalender_zuletzt_synchronisiert` | Zeitstempel des letzten erfolgreichen Syncs |
| `sensor.familienkalender_mama_anzahl` | Anzahl bevorstehender Termine für Mama |
| `sensor.familienkalender_papa_anzahl` | Anzahl bevorstehender Termine für Papa |

---

## Debugging

```yaml
# configuration.yaml
logger:
  default: info
  logs:
    custom_components.timetree_pro: debug
```

---

## Bekannte Einschränkungen

- **Nur Upcoming-Events**: Die Integration holt Termine für das konfigurierte Vorschaufenster (Standard: 60 Tage). Vergangene Termine sind im Kalender-Dashboard nicht sichtbar.
- **Cloud-Polling**: Benötigt Internetverbindung. Kein Webhook-Support (TimeTree-API-Limitierung).
- **Interne API**: Basiert auf dem reverse-engineerten APP-API – kann bei TimeTree-Updates brechen.

---

## Credits

Basiert auf [acdcnow/Timetree-Import-for-Home-Assistant](https://github.com/acdcnow/Timetree-Import-for-Home-Assistant) und dem API-Reverse-Engineering von [eoleedi/TimeTree-Exporter](https://github.com/eoleedi/TimeTree-Exporter). Erweitert und überarbeitet von Jedrimos.
