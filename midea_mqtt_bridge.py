#!/usr/bin/env python3
"""
Midea CD Heat Pump Water Heater → MQTT Bridge
Publică starea boilerului pe MQTT și acceptă comenzi.
Suportă Home Assistant MQTT Discovery.
"""

import json
import logging
import os
import time
import threading

from midealocal.discover import discover
from midealocal.devices import device_selector
from midealocal.devices.cd import DeviceAttributes

try:
    import paho.mqtt.client as mqtt
    from paho.mqtt.client import CallbackAPIVersion
    _PAHO_V2 = True
except ImportError:
    import paho.mqtt.client as mqtt
    _PAHO_V2 = False

# ── Configurare Midea ──────────────────────────────────────────────────────────
TOKEN     = "ff6c9581597d9a1b9c33c570cdecbe5a42ca9222ce88fa46a902f74174f4cb069118243f5dceb27affe2c694797e5543e67ac91658d5552cbcb6e1331f25835a"
KEY       = "8f9eb0ba344b4d159e2ac0f37b4c308ebd95557386ca4182b9cf4895b4c5d7e9"
TARGET_IP = "192.168.88.231"

# ── Configurare MQTT ───────────────────────────────────────────────────────────
MQTT_HOST      = "core-mosquitto"  # add-on HA; înlocuiește cu IP dacă rulezi standalone
MQTT_PORT      = 1883
MQTT_USER      = ""
MQTT_PASS      = ""
MQTT_CLIENT_ID = "midea_cd_bridge"

# ── Polling ────────────────────────────────────────────────────────────────────
POLL_INTERVAL = 30

# ── Suprascrie din /data/options.json când rulează ca add-on HA ────────────────
_OPTIONS = "/data/options.json"
if os.path.exists(_OPTIONS):
    with open(_OPTIONS) as _f:
        _o = json.load(_f)
    TARGET_IP     = _o.get("target_ip",     TARGET_IP)
    TOKEN         = _o.get("token",         TOKEN)
    KEY           = _o.get("key",           KEY)
    MQTT_HOST     = _o.get("mqtt_host",     MQTT_HOST)
    MQTT_PORT     = int(_o.get("mqtt_port", MQTT_PORT))
    MQTT_USER     = _o.get("mqtt_user",     MQTT_USER)
    MQTT_PASS     = _o.get("mqtt_pass",     MQTT_PASS)
    POLL_INTERVAL = int(_o.get("poll_interval", POLL_INTERVAL))

# ── Topics MQTT ────────────────────────────────────────────────────────────────
BASE          = "midea/boiler"
AVAIL_TOPIC   = f"{BASE}/availability"
STATE_TOPIC   = f"{BASE}/state"

CMD_POWER = f"{BASE}/power/set"   # payload: ON / OFF
CMD_TEMP  = f"{BASE}/temp/set"    # payload: float (38-70)
CMD_MODE  = f"{BASE}/mode/set"    # payload: Economy / Hybrid / E-heater

HA_DISC = "homeassistant"

# ── Corecție temperatură exterior (bug firmware Midea) ─────────────────────────
def _fix_outdoor(raw):
    """outdoor_temperature: raw = temp*2 + 50  →  temp = (raw-50)/2."""
    if raw is not None and raw > 60:
        return round((raw - 50) / 2, 1)
    return raw

def _fix_tank_temp(raw):
    """top/bottom temperature: raw = temp*2 + 30  →  temp = (raw-30)/2."""
    if raw is not None and raw > 60:
        return round((raw - 30) / 2, 1)
    return raw


# ── Payloads Home Assistant MQTT Discovery ─────────────────────────────────────
def build_discovery_payloads():
    dev = {
        "identifiers": ["midea_boiler"],
        "name": "Boiler",
        "manufacturer": "Midea",
        "model": "RSJRAC01 (CD)",
    }
    payloads = {}

    # Climate (control principal: power + temperatură + preset mod)
    payloads[f"{HA_DISC}/climate/midea_boiler/config"] = {
        "name": "Boiler",
        "unique_id": "midea_boiler_climate",
        "device": dev,
        "availability_topic": AVAIL_TOPIC,
        "payload_available": "online",
        "payload_not_available": "offline",
        # HVAC modes = on/off
        "modes": ["off", "heat"],
        "mode_state_topic": STATE_TOPIC,
        "mode_state_template": "{{ 'heat' if value_json.power else 'off' }}",
        "mode_command_topic": CMD_POWER,
        "mode_command_template": "{{ 'ON' if value == 'heat' else 'OFF' }}",
        # Temperatură curentă & setpoint
        "current_temperature_topic": STATE_TOPIC,
        "current_temperature_template": "{{ value_json.current_temperature }}",
        "temperature_state_topic": STATE_TOPIC,
        "temperature_state_template": "{{ value_json.target_temperature }}",
        "temperature_command_topic": CMD_TEMP,
        "min_temp": 38,
        "max_temp": 70,
        "temp_step": 1,
        # Presets = moduri de funcționare
        "preset_modes": ["Economy", "Hybrid", "E-heater"],
        "preset_mode_state_topic": STATE_TOPIC,
        "preset_mode_value_template": "{{ value_json.mode }}",
        "preset_mode_command_topic": CMD_MODE,
    }

    # Senzori de temperatură
    for attr, label in [
        ("outdoor_temperature",    "Temperatură Exterior"),
        ("condenser_temperature",  "Temperatură Condensator"),
        ("compressor_temperature", "Temperatură Compresor"),
        ("top_temperature",        "Temperatură Top Boiler"),
        ("bottom_temperature",     "Temperatură Bază Boiler"),
    ]:
        payloads[f"{HA_DISC}/sensor/midea_boiler_{attr}/config"] = {
            "name": f"Boiler {label}",
            "unique_id": f"midea_boiler_{attr}",
            "device": dev,
            "availability_topic": AVAIL_TOPIC,
            "state_topic": STATE_TOPIC,
            "value_template": f"{{{{ value_json.{attr} }}}}",
            "unit_of_measurement": "°C",
            "device_class": "temperature",
            "state_class": "measurement",
        }

    # Senzori binari
    for attr, label, dev_class in [
        ("compressor_status", "Compresor Activ",       "running"),
        ("water_pump",        "Pompă Apă",             "running"),
        ("elec_heat",         "Rezistență Electrică",  "heat"),
        ("sterilize",         "Dezinfectare Imediată", "running"),
    ]:
        payloads[f"{HA_DISC}/binary_sensor/midea_boiler_{attr}/config"] = {
            "name": f"Boiler {label}",
            "unique_id": f"midea_boiler_{attr}",
            "device": dev,
            "availability_topic": AVAIL_TOPIC,
            "state_topic": STATE_TOPIC,
            "value_template": f"{{{{ value_json.{attr} | lower }}}}",
            "payload_on": "true",
            "payload_off": "false",
            "device_class": dev_class,
        }

    # Disinfect — read-only (nu poate fi controlat prin librărie)
    payloads[f"{HA_DISC}/binary_sensor/midea_boiler_disinfect/config"] = {
        "name": "Boiler Dezinfectare",
        "unique_id": "midea_boiler_disinfect",
        "device": dev,
        "availability_topic": AVAIL_TOPIC,
        "state_topic": STATE_TOPIC,
        "value_template": "{{ value_json.disinfect | lower }}",
        "payload_on": "true",
        "payload_off": "false",
    }

    return payloads


# ── Bridge ─────────────────────────────────────────────────────────────────────

class MideaMqttBridge:
    def __init__(self):
        self.device = None
        self.client = None
        self._lock = threading.Lock()   # protejează accesul la self.device
        self._running = False

    # ── Midea ──────────────────────────────────────────────────────────────────
    def _connect_midea(self):
        logging.info("Caut dispozitiv Midea la %s ...", TARGET_IP)
        found = discover(ip_address=TARGET_IP)
        if not found:
            raise RuntimeError(f"Niciun dispozitiv găsit la {TARGET_IP}")

        d = list(found.values())[0]
        logging.info("Găsit: model=%s id=%s", d.get("model"), d.get("device_id"))

        self.device = device_selector(
            name="Boiler Midea",
            device_id=d["device_id"],
            device_type=d["type"],
            ip_address=d["ip_address"],
            port=d["port"],
            token=TOKEN,
            key=KEY,
            device_protocol=d["protocol"],
            model=d["model"],
            subtype=0,
            customize="",
        )
        if not self.device.connect():
            raise RuntimeError("Conectare eșuată — verifică TOKEN și KEY")
        logging.info("Conectat la Midea.")

    # Mapare firmware → nume afișat în HA (confirmat prin test)
    _FW_TO_APP = {
        "Energy-save": "Economy",
        "Standard":    "Hybrid",
        "Dual":        "E-heater",
    }
    _APP_TO_FW = {v: k for k, v in _FW_TO_APP.items()}

    def _reconnect_socket(self):
        """Reconectare ușoară — doar re-deschide socket-ul, fără re-discover."""
        if not self.device.connect():
            raise RuntimeError("Reconectare socket eșuată")

    def _read_state(self):
        """Reconectare + citire stare (device-ul închide conexiunea după ~30s)."""
        self._reconnect_socket()
        self.device.refresh_status(check_protocol=True)
        a = self.device.attributes

        raw_outdoor = a.get(DeviceAttributes.outdoor_temperature)
        raw_mode    = a.get(DeviceAttributes.mode, "")

        # Traduce modul firmware în numele din app
        mode = self._FW_TO_APP.get(str(raw_mode), "")

        disinfect = bool(
            a.get(DeviceAttributes.disinfect, False) or
            a.get(DeviceAttributes.sterilize, False)
        )

        return {
            "power":                bool(a.get(DeviceAttributes.power, False)),
            "mode":                 mode,
            "target_temperature":   a.get(DeviceAttributes.target_temperature),
            "current_temperature":  a.get(DeviceAttributes.current_temperature),
            "top_temperature":      _fix_tank_temp(a.get(DeviceAttributes.top_temperature)),
            "bottom_temperature":   _fix_tank_temp(a.get(DeviceAttributes.bottom_temperature)),
            "outdoor_temperature":  _fix_outdoor(raw_outdoor),
            "condenser_temperature":  a.get(DeviceAttributes.condenser_temperature),
            "compressor_temperature": a.get(DeviceAttributes.compressor_temperature),
            "compressor_status":    bool(a.get(DeviceAttributes.compressor_status, False)),
            "water_pump":           bool(a.get(DeviceAttributes.water_pump, False)),
            "elec_heat":            bool(a.get(DeviceAttributes.elec_heat, False)),
            "sterilize":            bool(a.get(DeviceAttributes.sterilize, False)),
            "disinfect":            disinfect,
            "error_code":           a.get(DeviceAttributes.error_code, 0),
        }

    def _publish_state(self):
        state = self._read_state()
        self.client.publish(STATE_TOPIC, json.dumps(state), retain=True)
        logging.debug("State: %s", state)

    # ── MQTT callbacks ─────────────────────────────────────────────────────────
    def _on_connect(self, client, userdata, flags, reason_code, properties=None):
        if reason_code != 0:
            logging.error("MQTT connect eșuat: %s", reason_code)
            return
        logging.info("Conectat la MQTT broker.")
        for topic in [CMD_POWER, CMD_TEMP, CMD_MODE]:
            client.subscribe(topic)
        for topic, payload in build_discovery_payloads().items():
            client.publish(topic, json.dumps(payload), retain=True)
        client.publish(AVAIL_TOPIC, "online", retain=True)

    def _on_disconnect(self, client, userdata, disconnect_flags=None, reason_code=None, properties=None):
        logging.warning("MQTT deconectat: %s", reason_code)

    def _on_message(self, client, userdata, msg):
        topic   = msg.topic
        payload = msg.payload.decode().strip()
        logging.info("Comandă MQTT: %s = %r", topic, payload)

        try:
            with self._lock:
                self._handle_command(topic, payload)
            time.sleep(1)
            with self._lock:
                self._publish_state()
        except Exception as exc:
            logging.error("Eroare comandă %s: %s", topic, exc)

    def _handle_command(self, topic, payload):
        self._reconnect_socket()
        upper = payload.upper()

        if topic == CMD_POWER:
            val = upper in ("ON", "HEAT", "TRUE", "1")
            self.device.set_attribute(DeviceAttributes.power, val)

        elif topic == CMD_TEMP:
            temp = float(payload)
            if not (38 <= temp <= 70):
                logging.warning("Temperatură în afara intervalului: %.1f", temp)
                return
            # Dacă boilerul e oprit, nu îl pornim automat — trimitem doar setpoint-ul
            self.device.set_attribute(DeviceAttributes.target_temperature, int(temp))

        elif topic == CMD_MODE:
            # payload = "Economy" | "Hybrid" | "E-heater"
            fw_mode = self._APP_TO_FW.get(payload)
            if fw_mode:
                self.device.set_attribute(DeviceAttributes.mode, fw_mode)
            else:
                logging.warning("Mod necunoscut: %r (valori valide: %s)",
                                payload, ", ".join(self._APP_TO_FW))

    # ── Main loop ──────────────────────────────────────────────────────────────
    def run(self):
        self._connect_midea()

        if _PAHO_V2:
            try:
                self.client = mqtt.Client(
                    client_id=MQTT_CLIENT_ID,
                    callback_api_version=CallbackAPIVersion.VERSION2,
                )
            except Exception:
                self.client = mqtt.Client(client_id=MQTT_CLIENT_ID)
        else:
            self.client = mqtt.Client(client_id=MQTT_CLIENT_ID)

        if MQTT_USER:
            self.client.username_pw_set(MQTT_USER, MQTT_PASS)

        self.client.will_set(AVAIL_TOPIC, "offline", retain=True)
        self.client.on_connect    = self._on_connect
        self.client.on_disconnect = self._on_disconnect
        self.client.on_message    = self._on_message

        self.client.connect(MQTT_HOST, MQTT_PORT, keepalive=60)
        self.client.loop_start()

        self._running = True
        logging.info("Bridge pornit. Polling la fiecare %ds.", POLL_INTERVAL)

        try:
            while self._running:
                try:
                    with self._lock:
                        self._publish_state()
                except Exception as exc:
                    logging.error("Eroare polling Midea: %s", exc)
                    self.client.publish(AVAIL_TOPIC, "offline", retain=True)
                    # Încearcă reconectare
                    for delay in (10, 30, 60):
                        logging.info("Reconectare în %ds ...", delay)
                        time.sleep(delay)
                        try:
                            with self._lock:
                                self._connect_midea()
                            self.client.publish(AVAIL_TOPIC, "online", retain=True)
                            break
                        except Exception as re_exc:
                            logging.error("Reconectare eșuată: %s", re_exc)

                time.sleep(POLL_INTERVAL)

        except KeyboardInterrupt:
            logging.info("Oprit.")
        finally:
            self.client.publish(AVAIL_TOPIC, "offline", retain=True)
            self.client.loop_stop()
            self.client.disconnect()


if __name__ == "__main__":
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s  %(levelname)-8s  %(message)s",
    )
    bridge = MideaMqttBridge()
    bridge.run()
