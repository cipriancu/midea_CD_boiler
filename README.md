# Midea CD Boiler — MQTT Bridge for Home Assistant

A Python bridge that connects a **Midea CD heat pump water heater** (model RSJRAC01) to Home Assistant via MQTT, using the [`midea-local`](https://github.com/rokam/midea-local) library.

---

## Files

| File | Purpose |
|---|---|
| `midea_mqtt_bridge.py` | The bridge — polls the boiler and exposes it to HA via MQTT discovery |
| `midea_test_commands.py` | Interactive CLI to test all commands without MQTT |
| `addon/` | Home Assistant local add-on (Dockerfile + config) |

---

## What Works

### Controls
| Feature | MQTT topic | Payload |
|---|---|---|
| Power on/off | `midea/boiler/power/set` | `ON` / `OFF` |
| Target temperature | `midea/boiler/temp/set` | `38`–`70` (°C) |
| Mode | `midea/boiler/mode/set` | `Economy` / `Hybrid` / `E-heater` |

### Sensor readings (published to `midea/boiler/state` as JSON)
| Field | Description |
|---|---|
| `power` | Boolean — boiler on/off |
| `mode` | Active mode: `Economy`, `Hybrid`, `E-heater`, or `""` |
| `target_temperature` | Setpoint in °C |
| `current_temperature` | Water temperature in °C |
| `top_temperature` | Top of tank in °C |
| `bottom_temperature` | Bottom of tank in °C |
| `outdoor_temperature` | Outdoor sensor in °C |
| `condenser_temperature` | Condenser in °C |
| `compressor_temperature` | Compressor in °C |
| `compressor_status` | Boolean |
| `water_pump` | Boolean |
| `elec_heat` | Boolean — electric element active |
| `sterilize` | Boolean — disinfection cycle active |
| `disinfect` | Boolean |
| `error_code` | Integer — 0 = no error |

---

## What Does NOT Work (library limitations)

| Feature | Reason |
|---|---|
| **Sterilize / Immediate disinfection** | Read-only. The `midea-local` SET command is only 8 bytes and has no field for sterilize. It can be observed but not triggered from Python. |
| **Vacation mode with days** | The library sends `mode=Vacation (0x05)` but does not transmit the number of days. The boiler ignores the command without a day count. |

---

## Mode Mapping

The firmware names differ from the app button labels. Confirmed by testing:

| Firmware value | App button label |
|---|---|
| `Energy-save` | Economy |
| `Standard` | Hybrid |
| `Dual` | E-heater |

Modes `None`, `Smart`, and `Vacation` are defined in the firmware but produce no visible effect on this model.

---

## Temperature Encoding Quirks

Some sensors return raw encoded values instead of °C directly. Corrections applied in the bridge:

| Sensor | Formula | Example |
|---|---|---|
| `outdoor_temperature` | `(raw − 50) / 2` | raw 91 → 20.5°C |
| `top_temperature` | `(raw − 30) / 2` | raw 111 → 40.5°C |
| `bottom_temperature` | `(raw − 30) / 2` | raw 110 → 40.0°C |

Applied only when `raw > 60`. Other temperatures (`current`, `condenser`, `compressor`) are returned correctly by the library with no correction needed.

---

## Byte8 / Protocol Notes

The SET command is 8 bytes: `[0x01, power, mode, temperature, trValue, openPTC, ptcTemp, byte8]`

`byte8` is an opaque flags byte echoed back from the device response. Confirmed bit mapping:

| Bit | Mask | Effect |
|---|---|---|
| 7 | `0x80` | Fahrenheit mode ON/OFF |
| 0–6 | — | No visible effect found on this model |

---

## Home Assistant — Entities Created via MQTT Discovery

The bridge auto-publishes discovery configs on startup. HA creates one device **"Boiler"** with:

| Entity | Type |
|---|---|
| Boiler | `climate` — power, temperature setpoint, preset modes |
| Boiler Temperatură Exterior | `sensor` |
| Boiler Temperatură Condensator | `sensor` |
| Boiler Temperatură Compresor | `sensor` |
| Boiler Top Boiler | `sensor` |
| Boiler Bază Boiler | `sensor` |
| Boiler Compresor Activ | `binary_sensor` |
| Boiler Pompă Apă | `binary_sensor` |
| Boiler Rezistență Electrică | `binary_sensor` |
| Boiler Dezinfectare Imediată | `binary_sensor` |
| Boiler Dezinfectare | `binary_sensor` |

---

## Installation — Home Assistant Add-on

### Prerequisites
- Home Assistant OS with **Mosquitto broker** add-on installed and running
- Boiler connected to the same local network

### Steps

**1. Copy the add-on to HA**

Via Samba share, copy the `addon/` folder to:
```
\\<HA-IP>\addons\midea_boiler\
```

The folder must contain: `config.yaml`, `build.yaml`, `Dockerfile`, `midea_mqtt_bridge.py`

**2. Reload add-ons in HA**

Settings → Add-ons → `⋮` (top right) → **Reload add-ons**

**3. Install and configure**

Settings → Add-ons → Local add-ons → **Midea Boiler Bridge** → Configuration tab:

| Option | Value |
|---|---|
| `target_ip` | IP of the boiler (e.g. `192.168.88.231`) |
| `token` | Device token (from Midea cloud or local discovery) |
| `key` | Device key |
| `mqtt_host` | `core-mosquitto` (when using Mosquitto add-on) |
| `mqtt_port` | `1883` |
| `mqtt_user` | Leave empty if Mosquitto has no auth |
| `mqtt_pass` | Leave empty if Mosquitto has no auth |
| `poll_interval` | `30` (seconds between state reads) |

Save → Info tab → **Start** → enable **Start on boot** and **Watchdog**

**4. Check HA**

Settings → Devices & Services → MQTT → device **"Boiler"** should appear automatically.

---

## Running Standalone (without HA add-on)

Edit `MQTT_HOST` in `midea_mqtt_bridge.py` and run:

```bash
pip install midea-local paho-mqtt
python midea_mqtt_bridge.py
```

---

## Testing Commands

```bash
pip install midea-local
python midea_test_commands.py
```

Interactive menu — tests all commands directly against the device without MQTT.
