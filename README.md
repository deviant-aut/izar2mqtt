# izar2mqtt

Read **Diehl IZAR RC 868** water meters with a cheap RTL-SDR and publish them to
**Home Assistant** via MQTT auto-discovery — including a derived **flow-rate
(L/min)** sensor. No cloud, no meter key required.

IZAR uses Diehl's **PRIOS** protocol with LFSR obfuscation (not AES), so meters
can be decoded with `NOKEY` — no per-meter key from your utility needed.

> This is a thin bridge on top of the excellent
> [wmbusmeters](https://github.com/wmbusmeters/wmbusmeters) and
> [rtl-wmbus](https://github.com/xaelsouth/rtl-wmbus) projects — see
> [Credits](#credits). It does not reimplement any decoding; it turns
> wmbusmeters' JSON into Home Assistant MQTT-discovery entities and adds a
> computed flow sensor.

## How it works

```
 RTL-SDR (868 MHz)                 this host (SDR)                 Home Assistant
┌────────────┐   I/Q   ┌───────────────────────────────┐  MQTT  ┌──────────────┐
│  dongle    │────────▶│ rtl_sdr | rtl_wmbus            │───────▶│ auto-discovery│
│            │         │   → wmbusmeters (JSON, stdout) │        │  devices +   │
└────────────┘         │   → wmbus-ha-bridge.py         │        │  sensors     │
                       └───────────────────────────────┘        └──────────────┘
```

**Why MQTT and not a native HA integration?** In the typical setup the RTL-SDR
lives on a Linux box while Home Assistant runs as a separate HAOS appliance —
so the radio data has to cross machines. MQTT auto-discovery is the natural,
low-maintenance decoupling layer, and the entities still look fully native in
HA. (If your dongle is on the HA host itself, look at the wmbusmeters HAOS
add-on instead.)

## Entities per meter

One HA **device** per received meter id, with sensors:

| Sensor | Unit | Notes |
|--------|------|-------|
| Total consumption | m³ | `device_class: water`, `total_increasing` (Energy/water dashboard ready) |
| Flow | L/min | derived, see below |
| Last month total | m³ | meter's stored month boundary value |
| Alarms | — | diagnostic |
| Signal | dBm | diagnostic |
| Battery remaining | a (years) | diagnostic |
| Transmit period | s | diagnostic (8 = R3 mode) |

### The flow sensor (honest caveats)

The meter only transmits a **cumulative total** at ~1 litre resolution, so flow
is computed by the bridge as `Δlitres / Δtime` between two telegrams:

- **Quantised.** With 1 L resolution and an ~8 s transmit interval, a running
  tap shows up in ~7.5 L/min steps (1 L per 8 s). Good for "is water flowing
  and roughly how much", not for precise instantaneous readings.
- Duplicate telegrams (same frame received twice, Δt≈0) are filtered.
- Flow is sent in **every** state update (falls back to 0.0 within ~8 s when the
  meter stops counting) so the sensor never flaps to *unavailable*.

If you want it smoother, add a Home Assistant `filter` sensor (moving average)
on top.

## Hardware

- Any RTL2832U RTL-SDR dongle. A meter transmits on **868.95 MHz** (IZAR "R3",
  ~8 s) and/or **868.30 MHz** ("R4", ~15 min).
- **Tuner note:** weak tuners such as the **Fitipower FC0012** (max gain ~19 dB)
  receive 868 MHz poorly. The big lever is running `rtl_wmbus` **without `-a`**
  (accurate atan demodulation) and **with `-o`** (DC-offset removal) — that took
  the decode rate from ~0% to ~50% on an FC0012 here. A **R820T2** tuner is much
  stronger and works with default flags and higher gain.

## Install

```bash
git clone https://github.com/deviant-aut/izar2mqtt.git
cd izar2mqtt
sudo ./install.sh
```

`install.sh` installs dependencies, builds `rtl_wmbus` and `wmbusmeters` from
source (neither is packaged in apt), and installs the bridge, example config and
a systemd service. Then:

1. Edit `/etc/wmbusmeters.conf` — RTL device string, frequency, gain, tuner flags.
2. Edit the `MQTT_*` lines in `/etc/systemd/system/wmbusmeters-izar2mqtt.service`.
3. Check the dongle: `rtl_test -t`
4. Start it:
   ```bash
   sudo systemctl daemon-reload
   sudo systemctl enable --now wmbusmeters-izar2mqtt.service
   sudo journalctl -u wmbusmeters-izar2mqtt -f
   ```

Meters appear in **Settings → Devices → MQTT** in Home Assistant. Any IZAR meter
in range (including neighbours') is picked up automatically via the `id=*`
wildcard — just delete the ones you don't want in HA.

## Configuration (environment variables)

Set in the systemd unit:

| Variable | Default | Meaning |
|----------|---------|---------|
| `MQTT_HOST` / `MQTT_PORT` | `localhost` / `1883` | MQTT broker |
| `MQTT_USER` / `MQTT_PASS` | `mqtt` / `changeme` | broker credentials |
| `HA_DISCOVERY_PREFIX` | `homeassistant` | HA discovery prefix |
| `WMBUS_LABELS` | `{}` | JSON map `id → name suffix`, e.g. `{"12345678":"home"}` → device *watermeter 12345678 (home)* |

## MQTT topics

- Discovery: `homeassistant/sensor/wmbus_izar_<id>/<key>/config` (retained)
- State: `wmbusmeters/izar/<id>` (retained JSON, all fields + `flow_l_min`)

## Credits

This project stands entirely on the shoulders of others:

- **[wmbusmeters](https://github.com/wmbusmeters/wmbusmeters)** by Fredrik
  Öhrström — the wM-Bus decoder incl. the IZAR/PRIOS driver (GPLv3).
- **[rtl-wmbus](https://github.com/xaelsouth/rtl-wmbus)** by xaelsouth — the
  software T1/C1/S1 receiver that turns RTL-SDR I/Q into wM-Bus frames.
- **[rtl-sdr / librtlsdr](https://osmocom.org/projects/rtl-sdr/wiki)** by
  Osmocom — the SDR driver and `rtl_sdr` / `rtl_test` tools.
- **[IZAR PRIOS / LFSR reverse engineering](https://zewaren.net/wmbus-izar-meter.html)**
  by Erwan Martin — the write-up that made keyless IZAR decoding possible.
- **[ha-watermeter](https://github.com/zibous/ha-watermeter)** by zibous — a
  reference for RTL-SDR + wmbusmeters + Home Assistant setups.
- **Home Assistant** MQTT discovery.

"IZAR" and "PRIOS" are trademarks of **Diehl Metering**. This is an independent,
non-commercial project and is not affiliated with or endorsed by Diehl; the
names are used only to describe the compatible meters.

Only the glue in this repository (`wmbus-ha-bridge.py`, config, service,
installer) is original work.

## License

[MIT](LICENSE) for the code in this repository. The upstream tools it builds on
have their own licenses (wmbusmeters is GPLv3) — they are fetched/built by
`install.sh`, not redistributed here.
