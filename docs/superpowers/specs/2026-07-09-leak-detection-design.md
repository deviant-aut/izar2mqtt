# Rohrbruch-/Leck-Erkennung für izar2mqtt in Home Assistant — Design

**Datum:** 2026-07-09
**Kontext:** Wasserzähler Diehl IZAR (izar2mqtt → MQTT → Home Assistant). Ziel: Leck-/Rohrbruch-Erkennung, die aus den bereits vorhandenen HA-Sensoren gespeist wird.

## 1. Ziel & Nicht-Ziele

**Ziel:** Home-Assistant-seitige Erkennung von (a) Schleichlecks und (b) Rohrbrüchen für den **eigenen** Zähler, mit Benachrichtigung. Vorbereitung für späteres automatisches Absperrventil.

**Nicht-Ziele (YAGNI):**
- Keine Überwachung der Nachbar-Zähler (nicht handlungsrelevant).
- Kein automatisches Ventil-Schließen jetzt — nur ein leerer Hook.
- Kein ML/Anomalie-Lernen. Feste, nachvollziehbare Regeln.

## 2. Signal-Grundlage — WICHTIG

Es wird **ausschließlich der kumulative Zählerstand** `sensor.watermeter_24000650_total_m3` verwendet, und zwar über den **„Liter-Tick"**: den Zeitpunkt, zu dem der Wert zuletzt gestiegen ist (`last_changed`).

**Bewusst NICHT verwendet:** der abgeleitete `flow_l_min`. Er basiert auf 1‑L‑Auflösung und 8‑s‑Abtastung und ist für „Fluss exakt 0?"-Entscheidungen zu grob/rauschig. `flow_l_min` bleibt reiner Anzeige-Sensor.

**Definition „Liter-Tick":** `last_changed` des Zählerstand-Sensors ändert sich nur, wenn der **Wert** steigt (nicht bei jedem Funk-Update — der Bridge sendet auch unveränderte Stände). Damit ist „Zeit seit letztem Tick" = `now() - states.sensor.watermeter_24000650_total_m3.last_changed`.

**Nebeneffekt (gewollt):** Bei Funk-Empfangsloch kommen keine Ticks → sieht aus wie „Wasser aus" → Timer resetten. Ein Empfangsloch erzeugt somit **keinen Fehlalarm** (statt einen). Preis: ein Leck während längeren Empfangsausfalls wird verzögert erkannt; dafür der Diagnose-Wächter (§4.4).

## 3. Die Regeln (final abgestimmt)

| # | Wächter | Regel | Zweck |
|---|---------|-------|-------|
| **A** | Burst / Dauerfluss (ganztags) | In **jeder rollierenden Stunde** muss es ≥ **1 min ohne Tick** geben. Läuft eine volle Stunde ohne je 1 min Pause (Dauerfluss > ~1 L/min) → **Alarm**. | Größere anhaltende Lecks / Rohrbruch (langsam) |
| **B** | Schleichleck (nachts) | Täglich **03:00–05:00** muss es ≥ **eine zusammenhängende 30‑min‑Phase ohne Tick** geben. Fehlt sie → **Alarm** (Auswertung 05:00). | Schleichlecks ab ~2 L/h; nachts treffsicher |
| **C** | Schnell-Burst (ganztags) | Verbrauch der letzten **5 min** > **120 L** (0,120 m³) → **sofort Alarm**. Wannen-sicher (Wanne ~75 L/5 min). | Extremer Rohrbruch (> 24 L/min), Erkennung in < 5 min |
| **D** | Schlauch/Menge (ganztags) | Verbrauch in **30 min** > **300 L** (0,300 m³) → **Alarm**. Badewannen-sicher. | Vergessener ½"-Gartenschlauch (~360 L/30 min), erkannt in ~25 min |

Alle Schwellen sind über `input_number`-Helfer in der HA-UI justierbar (§5).

## 4. Architektur (native HA, kein Custom-Code)

Alles über HA-Bordmittel: `statistics`-Sensor, Template-Trigger, Automationen, Helfer. Fünf Bausteine:

### 4.1 Wächter A — rollierende Stunde ohne 1‑min‑Pause
Zustand über zwei Helfer: `input_boolean.water_flowing`, `input_datetime.water_flow_start`.
- **Automation „Pause erkannt":** Template-Trigger `now() - total.last_changed >= 1 min`. Aktion: `water_flowing = off`.
- **Automation „Fluss (wieder) gestartet":** Trigger = Zählerstand ändert sich UND `water_flowing == off`. Aktion: `water_flowing = on`, `water_flow_start = now()`.
- **Automation „Burst-A-Alarm":** Trigger = `water_flowing == on` UND `now() - water_flow_start >= 60 min`. Aktion: `script.water_leak_alarm('A', 'Dauerfluss > 60 min ohne Pause')`.

### 4.2 Wächter B — Nachtfenster 03–05
Latch-Helfer `input_boolean.water_night_pause_seen`.
- **Automation „03:00 Reset":** um 03:00 `water_night_pause_seen = off`.
- **Automation „Nacht-Pause gesehen":** Zeitfenster 03:00–05:00, Template-Trigger `now() - total.last_changed >= 30 min`. Aktion: `water_night_pause_seen = on`.
- **Automation „05:00 Auswertung":** um 05:00, falls `water_night_pause_seen == off` → `script.water_leak_alarm('B', 'Kein 30-min-Stillstand zwischen 03 und 05 Uhr')`.

### 4.3 Wächter C — Schnell-Burst
- **Statistics-Sensor** `sensor.water_consumption_5min`: Quelle `total_m3`, Charakteristik `change`, `max_age: 00:05:00`.
- **Automation „Burst-C-Alarm":** Trigger = `sensor.water_consumption_5min > 0.120`. Aktion: `script.water_leak_alarm('C', 'Über 120 L in 5 min')`.
- **Wannen-sicher:** eine Wannenfüllung ~75 L/5 min bleibt unter 120 L. Wanne + gleichzeitige Dusche (~120 L/5 min) käme grenzwertig nah — bei Bedarf Schwelle leicht anheben.

### 4.3b Wächter D — Schlauch/Menge über 30 min
- **Statistics-Sensor** `sensor.water_consumption_30min`: Quelle `total_m3`, Charakteristik `change`, `max_age: 00:30:00`.
- **Automation „Burst-D-Alarm":** Trigger = `sensor.water_consumption_30min > 0.300`. Aktion: `script.water_leak_alarm('D', 'Über 300 L in 30 min — Schlauch/Bewässerung?')`.
- **Badewannen-sicher:** eine Wannenfüllung ~150–200 L bleibt klar unter 300 L (auch Wanne + Dusche + Maschine in 30 min ≈ 275 L). Ein gedrosselter Schlauch < ~10 L/min (< 300 L/30 min) rutscht durch D, wird aber von Wächter A (1 h) gefangen.

### 4.4 Diagnose — „Zähler stumm"
- **Automation:** wenn `now() - total.last_updated > 30 min` (kein Funk-Update, nicht nur kein Tick) → leise Notification „Wasserzähler seit >30 min kein Empfang". Kein Leckalarm.

### 4.5 Alarm-Handling (gemeinsames Skript)
`script.water_leak_alarm(typ, detail)`:
1. Wenn `input_boolean.water_leak_monitoring == off` → abbrechen (scharf/unscharf-Schalter, z. B. für Poolfüllen).
2. Wenn `input_boolean.water_leak_alarm == on` → abbrechen (kein Spam bis quittiert; Cooldown).
3. `input_boolean.water_leak_alarm = on`.
4. **Actionable Notification** (mobile_app) + `persistent_notification`: „💧 Leck-Verdacht ({typ}): {detail}". Aktion-Buttons: **Quittieren** (löscht Alarm) und **Für 4 h stummschalten** (Monitoring 4 h aus).
5. `script.water_leak_shutoff` aufrufen — **leerer Hook** (später Ventil-Aktion).

## 5. Helfer & justierbare Parameter

**input_boolean:** `water_leak_monitoring` (scharf, Default on), `water_leak_alarm` (Latch), `water_flowing`, `water_night_pause_seen`.
**input_datetime:** `water_flow_start`.
**input_number (Tuning in UI):** `water_burst_a_minutes` (60), `water_pause_seconds` (60), `water_night_pause_minutes` (30), `water_burst_c_liters` (120), `water_hose_liters_30min` (300), `water_meter_silent_minutes` (30).

| Parameter | Default | Wirkung |
|-----------|---------|---------|
| `water_pause_seconds` | 60 | „Pause"-Definition für Wächter A |
| `water_burst_a_minutes` | 60 | Dauerfluss bis A-Alarm |
| `water_night_pause_minutes` | 30 | geforderter Nacht-Stillstand |
| `water_burst_c_liters` | 120 | Schnell-Burst-Schwelle / 5 min (wannensicher) |
| `water_hose_liters_30min` | 300 | Schlauch-Schwelle / 30 min (badewannensicher) |

## 6. Randfälle

- **Empfangsloch:** kein Tick → Timer reset → kein Fehlalarm (A/B). Diagnose §4.4 meldet lange Stille.
- **HA-Neustart:** `last_changed` startet neu; A-Timer beginnt frisch (max. 60 min blinder Fleck einmalig). Nacht-Latch über Zeit-Automationen unkritisch.
- **Poolfüllen o. ä. bewusster Langlauf:** vorher `water_leak_monitoring` aus (oder „4 h stumm" bei Alarm) — bewusst als „Jahres-Selbsttest" akzeptiert.
- **Sehr langsames Leck < ~2 L/h:** unter der Nacht-Nachweisgrenze — bewusst akzeptiert (größere `water_night_pause_minutes` würde empfindlicher).

## 7. Lieferform

Ein HA-**Package** (`packages/water_leak.yaml`): alle Helfer, Statistics-Sensor, Automationen und Skripte in einer Datei. Deployment: Datei nach HA `config/packages/` + `homeassistant: packages: !include_dir_named packages` (falls noch nicht aktiv), dann Reload. Wird zusätzlich als optionales Beispiel dem izar2mqtt-Repo beigelegt (generisch mit `<METER_ID>`-Platzhalter).

## 8. Test / Verifikation

- **Wächter C:** `water_burst_c_liters` temporär klein setzen (oder mehrere Hähne 5 min voll auf, >120 L) → `sensor.water_consumption_5min` > Schwelle → Alarm C. Gegenprobe: Wanne füllen → ~75 L/5 min, kein Alarm.
- **Wächter D:** Gartenschlauch ~25–30 min laufen lassen (oder `water_hose_liters_30min` temporär klein) → `sensor.water_consumption_30min` > Schwelle → Alarm D. Gegenprobe: Badewanne füllen → bleibt < 300 L, kein Alarm.
- **Wächter A:** Simulierbar durch Absenken `water_burst_a_minutes` auf 2 min + Hahn 2 min offen halten.
- **Wächter B:** Simulierbar durch temporäres Nachtfenster = aktuelle Uhrzeit + `water_night_pause_minutes` klein.
- **Empfangsloch-Sicherheit:** Antenne kurz abziehen während „Fluss" → A-Timer resettet, kein Alarm; nach 30 min Diagnose-Meldung.
