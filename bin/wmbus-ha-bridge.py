#!/usr/bin/env python3
"""wmbus-ha-bridge: read wmbusmeters JSON from stdin and publish Home Assistant
MQTT discovery + state for every received IZAR water meter, plus a derived
flow-rate (L/min) sensor.

Config via environment variables:
  MQTT_HOST/PORT/USER/PASS   MQTT broker (default localhost:1883)
  WMBUS_LABELS               optional JSON map id -> name suffix,
                             e.g. '{"12345678":"home"}' -> "watermeter 12345678 (home)"
  WMBUS_MAX_FLOW_M3H         plausibility limit for total_m3 (default 5 m³/h)

Plausibility filter: a telegram whose total_m3 is lower than the last accepted
value, or rose faster than WMBUS_MAX_FLOW_M3H allows for the elapsed time, is
dropped (CRC16 + PRIOS check byte occasionally let a corrupted frame through).
The same implausible value received twice in a row, or followed by a reading
that is plausible relative to it, is accepted (meter swap, reset, or the first
reading after a restart).
"""
import sys, json, subprocess, os, time
from datetime import datetime, timezone

BROKER = os.environ.get("MQTT_HOST", "localhost")
PORT   = os.environ.get("MQTT_PORT", "1883")
USER   = os.environ.get("MQTT_USER", "mqtt")
PW     = os.environ.get("MQTT_PASS", "changeme")
DISCO  = os.environ.get("HA_DISCOVERY_PREFIX", "homeassistant")
try:
    LABELS = json.loads(os.environ.get("WMBUS_LABELS", "{}"))
except Exception:
    LABELS = {}

# Sensor defs: (json_key, name, unit, device_class, state_class, entity_category, icon)
SENSORS = [
    ("total_m3",              "Total consumption", "m³",   "water",           "total_increasing", None,         "mdi:water"),
    ("last_month_total_m3",   "Last month total",  "m³",   "water",           "total_increasing", None,         "mdi:calendar-arrow-left"),
    ("flow_l_min",            "Flow",              "L/min","volume_flow_rate","measurement",      None,         "mdi:water-pump"),
    ("current_alarms",        "Alarms",            None,   None,              None,               "diagnostic", "mdi:alert-circle-outline"),
    ("rssi_dbm",              "Signal",            "dBm",  "signal_strength", "measurement",      "diagnostic", None),
    ("remaining_battery_life_y","Battery remaining","a",   None,              None,               "diagnostic", "mdi:battery-clock"),
    ("transmit_period_s",     "Transmit period",   "s",    None,              None,               "diagnostic", "mdi:timer-outline"),
]

# Minimum dt (s) between two readings used for flow calculation. Filters out
# duplicate telegrams (same frame received twice within ~0s).
FLOW_MIN_DT = 2.0

# Plausibility filter for total_m3, see module docstring. The allowed increase
# scales with the time since the last accepted reading, so rare telegrams still
# pass; SLACK covers the 1 L resolution.
MAX_FLOW_M3H = float(os.environ.get("WMBUS_MAX_FLOW_M3H", "5"))
SLACK_M3 = 0.005


def pub(topic, payload, retain=True):
    args = ["mosquitto_pub", "-h", BROKER, "-p", PORT, "-u", USER, "-P", PW,
            "-t", topic, "-m", payload]
    if retain:
        args.append("-r")
    try:
        subprocess.run(args, check=False, timeout=10,
                       stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    except Exception as e:
        print(f"[bridge] publish error {topic}: {e}", file=sys.stderr, flush=True)


def parse_ts(s):
    """wmbusmeters receive timestamp (ISO, UTC) -> epoch seconds; fallback: now."""
    try:
        return datetime.strptime(s, "%Y-%m-%dT%H:%M:%SZ").replace(tzinfo=timezone.utc).timestamp()
    except Exception:
        return time.time()


def state_topic(mid):
    return f"wmbusmeters/izar/{mid}"


def publish_discovery(mid):
    label = LABELS.get(mid)
    dev_name = f"watermeter {mid} ({label})" if label else f"watermeter {mid}"
    device = {
        "identifiers": [f"wmbus_izar_{mid}"],
        "name": dev_name,
        "manufacturer": "Diehl",
        "model": "IZAR RC 868 (PRIOS)",
    }
    st = state_topic(mid)
    for key, disp, unit, dclass, sclass, ecat, icon in SENSORS:
        cfg = {
            "name": disp,
            "unique_id": f"wmbus_izar_{mid}_{key}",
            "object_id": f"watermeter_{mid}_{key}",
            "state_topic": st,
            "value_template": "{{ value_json.%s | default('') }}" % key,
            "device": device,
        }
        if unit:   cfg["unit_of_measurement"] = unit
        if dclass: cfg["device_class"] = dclass
        if sclass: cfg["state_class"] = sclass
        if ecat:   cfg["entity_category"] = ecat
        if icon:   cfg["icon"] = icon
        pub(f"{DISCO}/sensor/wmbus_izar_{mid}/{key}/config", json.dumps(cfg))
    print(f"[bridge] discovery published: {mid} -> {dev_name}", flush=True)


def plausible(ref, total, t):
    """ref = (total_m3, epoch_s) of last accepted reading, or None."""
    if ref is None:
        return False
    dv = total - ref[0]
    return dv >= 0 and dv <= MAX_FLOW_M3H * max(t - ref[1], 0) / 3600.0 + SLACK_M3


def main():
    seen = set()
    last_reading = {}   # mid -> (total_m3, epoch_s) of last reading used for flow
    last_flow = {}      # mid -> last computed flow (L/min), always re-sent
    accepted = {}       # mid -> (total_m3, epoch_s) of last accepted reading
    pending = {}        # mid -> (total_m3, epoch_s) implausible, awaiting confirmation
    for line in sys.stdin:
        line = line.strip()
        if not line or not line.startswith("{"):
            continue
        try:
            d = json.loads(line)
        except json.JSONDecodeError:
            continue
        mid = d.get("id")
        if not mid or d.get("meter") != "izar":
            continue

        cur_total = d.get("total_m3")
        cur_t = parse_ts(d.get("timestamp", ""))
        if cur_total is not None:
            if plausible(accepted.get(mid), cur_total, cur_t):
                pending.pop(mid, None)
            elif mid in pending and (pending[mid][0] == cur_total
                                     or plausible(pending[mid], cur_total, cur_t)):
                pending.pop(mid, None)
                if mid in accepted:
                    print(f"[bridge] {mid}: total_m3={cur_total} confirmed, "
                          f"accepting (was {accepted[mid][0]})", flush=True)
                last_reading.pop(mid, None)   # don't derive flow across the jump
            else:
                pending[mid] = (cur_total, cur_t)
                if mid in accepted:   # first reading after start: quietly wait
                    print(f"[bridge] {mid}: REJECT total_m3={cur_total} "
                          f"(last accepted {accepted[mid][0]})", flush=True)
                continue
            accepted[mid] = (cur_total, cur_t)

        # Derive flow rate from meter-reading delta (1 L resolution).
        if cur_total is not None:
            prev = last_reading.get(mid)
            if prev is not None:
                dt = cur_t - prev[1]
                dv = cur_total - prev[0]           # m3
                if dt >= FLOW_MIN_DT and dv >= 0:
                    # L/min = (dv*1000 liters) / (dt/60 minutes)
                    last_flow[mid] = round(dv * 60000.0 / dt, 2)
                    last_reading[mid] = (cur_total, cur_t)
                # dt < FLOW_MIN_DT -> duplicate: ignore, keep reference
            else:
                last_reading[mid] = (cur_total, cur_t)
        # Always include flow (0.0 until first delta) so the sensor never flaps.
        d["flow_l_min"] = last_flow.get(mid, 0.0)

        if mid not in seen:
            publish_discovery(mid)
            seen.add(mid)
        pub(state_topic(mid), json.dumps(d))
        print(f"[bridge] state {mid}: total_m3={cur_total} flow={d['flow_l_min']} L/min "
              f"rssi={d.get('rssi_dbm')}", flush=True)


if __name__ == "__main__":
    main()
