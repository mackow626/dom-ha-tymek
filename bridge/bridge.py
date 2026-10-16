"""
Most: stary HA (0.118.x) -> nowy HA.

ODCZYT:
- `get_states` i `subscribe_events` (state_changed) ze starego HA,
- czujniki trafiają do nowego HA przez REST (POST /api/states) z prefiksem, np. sensor.dom_temperatura_salon.

STEROWANIE – tylko przez listy dozwolonych encji z pliku .env:
- termostaty, przełączniki i rolety są wystawiane do nowego HA przez MQTT
  pod tymi samymi nazwami co w starym HA (climate.ogrzewanie_salon, switch.kuchnia, cover.roleta_salon),
- termostaty: wyłącznie `climate.set_temperature` dla CLIMATE_CONTROL,
  temperatura przycinana do zakresu termostatu i MIN_SETPOINT..MAX_SETPOINT,
  zmiana trybu (włącz/wyłącz ogrzewanie) jest zablokowana,
- przełączniki: `switch.turn_on` / `switch.turn_off` dla SWITCH_CONTROL,
- rolety i brama: `cover.open_cover` / `close_cover` / `stop_cover` / `set_cover_position` dla COVER_CONTROL,
- rolety z COVER_TIMED: pozycja „na czas” – most wysyła otwórz/zamknij, po wyliczonym czasie stop,
  i sam szacuje pozycję (Domatiq nie wykonuje set_cover_position); czas pełnego przejazdu:
  COVER_TRAVEL_SECONDS (domyślnie) lub COVER_TRAVEL_TIMES=cover.x:25,cover.y:30,
- listy: nazwy encji po przecinku, `*` = wszystkie z domeny, puste = brak sterowania,
- zmiany z suwaków są wygładzane (wysyłana jest tylko ostatnia wartość po DEBOUNCE_SECONDS),
- każde polecenie jest zapisywane w logu ze słowem ZAPIS,
- gdy nie ma połączenia ze starym HA, kontrolki w nowym HA są oznaczone jako niedostępne.

Tokeny czytane są ze zmiennych środowiskowych (plik .env) i nigdzie nie są logowane.
"""
import asyncio
import json
import logging
import os
import time

import aiohttp
import aiomqtt

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
log = logging.getLogger("bridge")


def control_list(name: str) -> set[str] | None:
    """None = wszystkie encje z domeny, pusty zbiór = brak sterowania."""
    raw = os.environ.get(name, "").strip()
    if raw == "*":
        return None
    return {e.strip() for e in raw.split(",") if e.strip()}


OLD_URL = os.environ["OLD_HA_URL"].rstrip("/")
OLD_TOKEN = os.environ["OLD_HA_TOKEN"]
NEW_URL = os.environ.get("NEW_HA_URL", "http://homeassistant:8123").rstrip("/")
NEW_TOKEN = os.environ["NEW_HA_TOKEN"]
DOMAINS = {d.strip() for d in os.environ.get("DOMAINS", "sensor,binary_sensor").split(",") if d.strip()}
PREFIX = os.environ.get("PREFIX", "dom_")
RESYNC_SECONDS = int(os.environ.get("RESYNC_SECONDS", "300"))
VERIFY_SSL = os.environ.get("VERIFY_SSL", "true").lower() == "true"

MQTT_HOST = os.environ.get("MQTT_HOST", "mosquitto")
MQTT_PORT = int(os.environ.get("MQTT_PORT", "1883"))
CONTROL = {
    "climate": control_list("CLIMATE_CONTROL"),
    "switch": control_list("SWITCH_CONTROL"),
    "cover": control_list("COVER_CONTROL"),
}
MIN_SETPOINT = float(os.environ.get("MIN_SETPOINT", "10"))
MAX_SETPOINT = float(os.environ.get("MAX_SETPOINT", "28"))
DEBOUNCE_SECONDS = float(os.environ.get("DEBOUNCE_SECONDS", "1.5"))
COVER_TIMED = control_list("COVER_TIMED") if os.environ.get("COVER_TIMED", "").strip() else set()
COVER_TRAVEL_SECONDS = float(os.environ.get("COVER_TRAVEL_SECONDS", "30"))
COVER_TRAVEL_TIMES = {
    k.strip(): float(v)
    for k, v in (item.split(":", 1) for item in os.environ.get("COVER_TRAVEL_TIMES", "").split(",") if ":" in item)
}

STATUS_TOPIC = "dom/bridge/status"

cache: dict[str, dict] = {}          # czujniki: entity_id -> {"state", "attributes"}
devices: dict[str, dict] = {}        # termostaty/przełączniki/rolety: entity_id -> {"state", "attributes"}
old_ws: aiohttp.ClientWebSocketResponse | None = None
mqtt: aiomqtt.Client | None = None
msg_id = 100
pending: dict[str, asyncio.Task] = {}
cover_pos: dict[str, float] = {}               # szacowana pozycja rolet „na czas” (0–100)
cover_motion: dict[str, tuple[int, float, float]] = {}  # eid -> (kierunek +1/-1, start, pozycja startowa)
cover_tasks: dict[str, asyncio.Task] = {}


# ---------- pomocnicze ----------

def ws_url(http_url: str) -> str:
    if http_url.startswith("https://"):
        return "wss://" + http_url[len("https://"):] + "/api/websocket"
    return "ws://" + http_url[len("http://"):] + "/api/websocket"


def domain_of(entity_id: str) -> str:
    return entity_id.split(".", 1)[0]


def obj_of(entity_id: str) -> str:
    return entity_id.split(".", 1)[1]


def wanted(entity_id: str) -> bool:
    return domain_of(entity_id) in DOMAINS


def local_id(entity_id: str) -> str:
    return f"{domain_of(entity_id)}.{PREFIX}{obj_of(entity_id)}"


def allowed(entity_id: str) -> bool:
    lst = CONTROL.get(domain_of(entity_id), set())
    return lst is None or entity_id in lst


def mirrored(entity_id: str) -> bool:
    """Termostaty pokazujemy zawsze (podgląd), przełączniki i rolety tylko gdy wolno nimi sterować."""
    dom = domain_of(entity_id)
    return dom == "climate" or (dom in ("switch", "cover") and allowed(entity_id))


def describe(dom: str) -> str:
    lst = CONTROL[dom]
    if lst is None:
        return "wszystkie"
    return ", ".join(sorted(lst)) if lst else "brak"


# ---------- czujniki -> nowy HA (REST) ----------

async def push(session: aiohttp.ClientSession, entity_id: str, data: dict) -> None:
    attrs = dict(data.get("attributes") or {})
    attrs["zrodlo"] = "stary HA (tylko odczyt)"
    try:
        async with session.post(
            f"{NEW_URL}/api/states/{local_id(entity_id)}",
            headers={"Authorization": f"Bearer {NEW_TOKEN}"},
            json={"state": data.get("state"), "attributes": attrs},
            timeout=aiohttp.ClientTimeout(total=10),
        ) as r:
            if r.status >= 400:
                log.warning("Nowy HA odrzucił %s: HTTP %s", entity_id, r.status)
    except Exception as e:
        log.warning("Nie mogę zapisać %s w nowym HA: %s", entity_id, e)


async def resync_loop(session: aiohttp.ClientSession) -> None:
    while True:
        await asyncio.sleep(RESYNC_SECONDS)
        for eid, data in list(cache.items()):
            await push(session, eid, data)
        log.info("Resynchronizacja: %d encji", len(cache))


# ---------- MQTT discovery ----------

def base_topic(eid: str) -> str:
    return f"dom/{domain_of(eid)}/{obj_of(eid)}"


def common_config(eid: str, data: dict) -> dict:
    a = data.get("attributes") or {}
    dom, obj = domain_of(eid), obj_of(eid)
    return {
        "name": a.get("friendly_name", obj),
        "unique_id": f"dom_{dom}_{obj}",
        "default_entity_id": eid,
        "availability": [
            {"topic": STATUS_TOPIC},
            {"topic": f"{base_topic(eid)}/available"},
        ],
        "availability_mode": "all",
    }


def climate_limits(attrs: dict) -> tuple[float, float]:
    lo = max(float(attrs.get("min_temp", MIN_SETPOINT)), MIN_SETPOINT)
    hi = min(float(attrs.get("max_temp", MAX_SETPOINT)), MAX_SETPOINT)
    return lo, hi


VALID_MODES = {"off", "heat", "cool", "heat_cool", "auto", "dry", "fan_only"}
VALID_ACTIONS = {"off", "heating", "cooling", "drying", "idle", "fan", "preheating", "defrosting"}


def climate_modes(data: dict) -> list[str]:
    # Domatiq: hvac_modes = ["heat"], a stan "off"/"heat" oznacza, czy pokój akurat grzeje.
    modes = [m for m in ((data.get("attributes") or {}).get("hvac_modes") or []) if m in VALID_MODES]
    return modes or ["heat"]


def climate_config(eid: str, data: dict) -> dict:
    a = data.get("attributes") or {}
    base = base_topic(eid)
    lo, hi = climate_limits(a)
    cfg = common_config(eid, data) | {
        "temperature_unit": "C",
        "precision": 0.1,
        "temp_step": float(a.get("target_temp_step", 0.5)),
        "min_temp": lo,
        "max_temp": hi,
        "modes": climate_modes(data),
        "mode_state_topic": f"{base}/state",
        "mode_state_template": "{{ value_json.mode }}",
        "mode_command_topic": f"{base}/set_mode",
        "current_temperature_topic": f"{base}/state",
        "current_temperature_template": "{{ value_json.current }}",
        "temperature_state_topic": f"{base}/state",
        "temperature_state_template": "{{ value_json.target }}",
        "action_topic": f"{base}/state",
        "action_template": "{{ value_json.action }}",
    }
    if allowed(eid):
        cfg["temperature_command_topic"] = f"{base}/set_temperature"
    return cfg


def climate_payload(data: dict) -> dict:
    a = data.get("attributes") or {}
    state = data.get("state")
    modes = climate_modes(data)
    mode = state if state in modes else modes[0]
    action = a.get("hvac_action")
    if action not in VALID_ACTIONS:
        action = "heating" if state == "heat" else "idle"
    return {
        "mode": mode,
        "current": a.get("current_temperature"),
        "target": a.get("temperature"),
        "action": action,
    }


def switch_icon(obj: str) -> str:
    if obj.startswith("gniazdko"):
        return "mdi:power-socket-eu"
    if obj.startswith("ogrzewanie"):
        return "mdi:radiator"
    if "led" in obj:
        return "mdi:led-strip-variant"
    return "mdi:lightbulb"


def switch_config(eid: str, data: dict) -> dict:
    base = base_topic(eid)
    return common_config(eid, data) | {
        "icon": switch_icon(obj_of(eid)),
        "state_topic": f"{base}/state",
        "command_topic": f"{base}/set",
        "payload_on": "ON",
        "payload_off": "OFF",
        "state_on": "ON",
        "state_off": "OFF",
    }


COVER_STATES = {"open", "closed", "opening", "closing", "stopped"}


def cover_config(eid: str, data: dict) -> dict:
    a = data.get("attributes") or {}
    base = base_topic(eid)
    features = int(a.get("supported_features") or 0)
    cfg = common_config(eid, data) | {
        "device_class": "gate" if "brama" in obj_of(eid) else "shutter",
        "command_topic": f"{base}/set",
        "payload_open": "OPEN" if features & 1 else None,
        "payload_close": "CLOSE" if features & 2 else None,
        "payload_stop": "STOP" if features & 8 else None,
        "state_topic": f"{base}/state",
    }
    if timed(eid) or (features & 4 and "current_position" in a):
        cfg |= {
            "position_topic": f"{base}/position",
            "set_position_topic": f"{base}/set_position",
            "position_open": 100,
            "position_closed": 0,
        }
    return cfg


def available(data: dict) -> bool:
    return data.get("state") not in ("unavailable", None)


async def mqtt_publish(eid: str, with_config: bool) -> None:
    if mqtt is None or eid not in devices:
        return
    data = devices[eid]
    dom, obj, base = domain_of(eid), obj_of(eid), base_topic(eid)
    state = data.get("state")
    try:
        if with_config:
            cfg = {"climate": climate_config, "switch": switch_config, "cover": cover_config}[dom](eid, data)
            await mqtt.publish(f"homeassistant/{dom}/dom_{obj}/config", json.dumps(cfg), retain=True)
        await mqtt.publish(f"{base}/available", "online" if available(data) else "offline", retain=True)
        if dom == "climate":
            await mqtt.publish(f"{base}/state", json.dumps(climate_payload(data)), retain=True)
        elif dom == "switch":
            await mqtt.publish(f"{base}/state", {"on": "ON", "off": "OFF"}.get(state, "None"), retain=True)
        elif dom == "cover":
            await mqtt.publish(f"{base}/state", state if state in COVER_STATES else "None", retain=True)
            pos = round(cover_estimate(eid)) if timed(eid) else (data.get("attributes") or {}).get("current_position")
            if pos is not None:
                await mqtt.publish(f"{base}/position", str(pos), retain=True)
    except Exception as e:
        log.warning("MQTT: nie mogę opublikować %s: %s", eid, e)


async def mqtt_status() -> None:
    if mqtt is None:
        return
    online = old_ws is not None and not old_ws.closed and bool(devices)
    try:
        await mqtt.publish(STATUS_TOPIC, "online" if online else "offline", retain=True)
    except Exception as e:
        log.warning("MQTT: nie mogę opublikować statusu: %s", e)


async def mqtt_publish_all() -> None:
    for eid in list(devices):
        await mqtt_publish(eid, with_config=True)
    await mqtt_status()
    if devices:
        counts = {d: sum(1 for e in devices if domain_of(e) == d) for d in ("climate", "switch", "cover")}
        log.info("MQTT: opublikowano termostaty: %d, przełączniki: %d, rolety: %d",
                 counts["climate"], counts["switch"], counts["cover"])


# ---------- rolety „na czas” ----------

def timed(eid: str) -> bool:
    return domain_of(eid) == "cover" and (COVER_TIMED is None or eid in COVER_TIMED)


def travel(eid: str) -> float:
    return COVER_TRAVEL_TIMES.get(eid, COVER_TRAVEL_SECONDS)


def cover_estimate(eid: str) -> float:
    if eid in cover_motion:
        direction, start, start_pos = cover_motion[eid]
        pos = start_pos + direction * (time.monotonic() - start) / travel(eid) * 100
        return min(max(pos, 0.0), 100.0)
    if eid not in cover_pos:
        cover_pos[eid] = 0.0 if (devices.get(eid) or {}).get("state") == "closed" else 100.0
    return cover_pos[eid]


def motion_start(eid: str, direction: int) -> None:
    cover_motion[eid] = (direction, time.monotonic(), cover_estimate(eid))


def motion_stop(eid: str, final: float | None = None) -> None:
    pos = cover_estimate(eid) if final is None else final
    cover_motion.pop(eid, None)
    cover_pos[eid] = pos


def cancel_cover_task(eid: str) -> None:
    task = cover_tasks.pop(eid, None)
    if task and task is not asyncio.current_task():
        task.cancel()


async def cover_full(eid: str, direction: int) -> None:
    """Pełne otwarcie/zamknięcie: po czasie przejazdu (z zapasem) pozycja = 100/0."""
    try:
        motion_start(eid, direction)
        await mqtt_publish(eid, with_config=False)
        remaining = (100 - cover_estimate(eid) if direction > 0 else cover_estimate(eid)) / 100 * travel(eid)
        await asyncio.sleep(remaining + 2)
        motion_stop(eid, 100.0 if direction > 0 else 0.0)
        await mqtt_publish(eid, with_config=False)
    finally:
        if cover_tasks.get(eid) is asyncio.current_task():
            cover_tasks.pop(eid, None)


async def cover_goto(eid: str, target: int) -> None:
    try:
        await asyncio.sleep(DEBOUNCE_SECONDS)
        if eid in cover_motion:                      # najpierw zatrzymaj trwający ruch
            await call_old("cover", "stop_cover", {"entity_id": eid})
            motion_stop(eid)
            await asyncio.sleep(0.7)
        current = cover_estimate(eid)
        if target >= 98:
            await call_old("cover", "open_cover", {"entity_id": eid})
            await cover_full(eid, +1)
            return
        if target <= 2:
            await call_old("cover", "close_cover", {"entity_id": eid})
            await cover_full(eid, -1)
            return
        delta = target - current
        if abs(delta) < 2:
            await mqtt_publish(eid, with_config=False)
            return
        direction = 1 if delta > 0 else -1
        duration = abs(delta) / 100 * travel(eid)
        log.info("Roleta %s na czas: %.0f%% -> %d%% (%s %.1f s)", eid, current, target,
                 "w górę" if direction > 0 else "w dół", duration)
        await call_old("cover", "open_cover" if direction > 0 else "close_cover", {"entity_id": eid})
        motion_start(eid, direction)
        await mqtt_publish(eid, with_config=False)
        await asyncio.sleep(duration)
        await call_old("cover", "stop_cover", {"entity_id": eid})
        motion_stop(eid, float(target))
        await mqtt_publish(eid, with_config=False)
    finally:
        if cover_tasks.get(eid) is asyncio.current_task():
            cover_tasks.pop(eid, None)


async def timed_command(eid: str, action: str, payload: str) -> None:
    cancel_cover_task(eid)
    if action == "set_position":
        try:
            target = min(max(int(float(payload)), 0), 100)
        except ValueError:
            log.warning("ODRZUCONO: nieprawidłowa pozycja '%s' dla %s", payload, eid)
            return
        cover_tasks[eid] = asyncio.create_task(cover_goto(eid, target))
        return
    service = {"OPEN": "open_cover", "CLOSE": "close_cover", "STOP": "stop_cover"}.get(payload)
    if service is None:
        log.warning("ODRZUCONO: nieznane polecenie '%s' dla %s", payload, eid)
        return
    await call_old("cover", service, {"entity_id": eid})
    if payload == "STOP":
        motion_stop(eid)
        await mqtt_publish(eid, with_config=False)
    else:
        cover_tasks[eid] = asyncio.create_task(cover_full(eid, +1 if payload == "OPEN" else -1))


# ---------- polecenia -> stary HA ----------

async def call_old(domain: str, service: str, data: dict) -> None:
    global msg_id
    if old_ws is None or old_ws.closed:
        log.warning("Brak połączenia ze starym HA – pomijam %s.%s %s", domain, service, data)
        return
    msg_id += 1
    log.info("ZAPIS: %s.%s %s (id=%d)", domain, service, data, msg_id)
    await old_ws.send_json({
        "id": msg_id,
        "type": "call_service",
        "domain": domain,
        "service": service,
        "service_data": data,
    })


async def debounced(key: str, domain: str, service: str, data: dict) -> None:
    await asyncio.sleep(DEBOUNCE_SECONDS)
    pending.pop(key, None)
    await call_old(domain, service, data)


def schedule(key: str, domain: str, service: str, data: dict) -> None:
    if key in pending:
        pending[key].cancel()
    pending[key] = asyncio.create_task(debounced(key, domain, service, data))


async def handle_command(topic: str, payload: str) -> None:
    parts = topic.split("/")  # dom/<domena>/<obj>/<akcja>
    if len(parts) != 4 or parts[0] != "dom":
        return
    dom, obj, action = parts[1], parts[2], parts[3]
    eid = f"{dom}.{obj}"
    if dom == "climate" and action == "set_mode":
        log.warning("ODRZUCONO: zmiana trybu %s na '%s' jest zablokowana", eid, payload)
        await mqtt_publish(eid, with_config=False)  # przywróć prawdziwy stan w nowym HA
        return
    if eid not in devices or not allowed(eid):
        log.warning("ODRZUCONO: %s (%s) nie jest na liście dozwolonych", eid, action)
        return

    if timed(eid) and action in ("set", "set_position"):
        await timed_command(eid, action, payload)
        return

    if dom == "climate" and action == "set_temperature":
        try:
            temp = float(payload)
        except ValueError:
            log.warning("ODRZUCONO: nieprawidłowa temperatura '%s' dla %s", payload, eid)
            return
        lo, hi = climate_limits(devices[eid].get("attributes") or {})
        clamped = min(max(temp, lo), hi)
        if clamped != temp:
            log.warning("Przycięto %s: %.1f -> %.1f °C (zakres %.1f–%.1f)", eid, temp, clamped, lo, hi)
        schedule(eid, "climate", "set_temperature", {"entity_id": eid, "temperature": clamped})

    elif dom == "switch" and action == "set":
        service = {"ON": "turn_on", "OFF": "turn_off"}.get(payload)
        if service is None:
            log.warning("ODRZUCONO: nieznane polecenie '%s' dla %s", payload, eid)
            return
        await call_old("switch", service, {"entity_id": eid})

    elif dom == "cover" and action == "set":
        service = {"OPEN": "open_cover", "CLOSE": "close_cover", "STOP": "stop_cover"}.get(payload)
        if service is None:
            log.warning("ODRZUCONO: nieznane polecenie '%s' dla %s", payload, eid)
            return
        if eid in pending:  # STOP/OTWÓRZ/ZAMKNIJ anuluje czekającą zmianę pozycji
            pending.pop(eid).cancel()
        await call_old("cover", service, {"entity_id": eid})

    elif dom == "cover" and action == "set_position":
        try:
            pos = min(max(int(float(payload)), 0), 100)
        except ValueError:
            log.warning("ODRZUCONO: nieprawidłowa pozycja '%s' dla %s", payload, eid)
            return
        schedule(eid, "cover", "set_cover_position", {"entity_id": eid, "position": pos})


COMMAND_TOPICS = [
    "dom/climate/+/set_temperature",
    "dom/climate/+/set_mode",
    "dom/switch/+/set",
    "dom/cover/+/set",
    "dom/cover/+/set_position",
]


async def mqtt_loop() -> None:
    global mqtt
    delay = 5
    while True:
        try:
            will = aiomqtt.Will(STATUS_TOPIC, "offline", retain=True)
            async with aiomqtt.Client(MQTT_HOST, MQTT_PORT, identifier="dom-bridge", will=will) as client:
                mqtt = client
                delay = 5
                for t in COMMAND_TOPICS:
                    await client.subscribe(t)
                log.info("Połączono z MQTT (%s:%d)", MQTT_HOST, MQTT_PORT)
                await mqtt_publish_all()
                async for message in client.messages:
                    await handle_command(str(message.topic), message.payload.decode(errors="replace"))
        except aiomqtt.MqttError as e:
            log.warning("MQTT niedostępne (%s) – ponawiam za %d s", e, delay)
        finally:
            mqtt = None
        await asyncio.sleep(delay)
        delay = min(delay * 2, 60)


# ---------- stary HA (WebSocket) ----------

async def run_once(session: aiohttp.ClientSession) -> None:
    global old_ws
    async with session.ws_connect(ws_url(OLD_URL), heartbeat=30, max_msg_size=0, ssl=VERIFY_SSL) as ws:
        msg = await ws.receive_json()
        if msg.get("type") != "auth_required":
            raise RuntimeError(f"Nieoczekiwana odpowiedź: {msg.get('type')}")
        await ws.send_json({"type": "auth", "access_token": OLD_TOKEN})
        msg = await ws.receive_json()
        if msg.get("type") != "auth_ok":
            raise RuntimeError("Uwierzytelnienie w starym HA nieudane – sprawdź token")
        log.info("Połączono ze starym HA (wersja %s)", msg.get("ha_version"))
        old_ws = ws

        await ws.send_json({"id": 1, "type": "get_states"})
        await ws.send_json({"id": 2, "type": "subscribe_events", "event_type": "state_changed"})

        try:
            async for raw in ws:
                if raw.type != aiohttp.WSMsgType.TEXT:
                    if raw.type in (aiohttp.WSMsgType.CLOSED, aiohttp.WSMsgType.ERROR):
                        break
                    continue
                msg = raw.json()
                if msg.get("type") == "result":
                    mid = msg.get("id")
                    if not msg.get("success"):
                        log.error("Stary HA odrzucił komendę id=%s: %s", mid, msg.get("error"))
                        continue
                    if mid == 1:
                        states = msg.get("result") or []
                        sensors = [s for s in states if wanted(s["entity_id"])]
                        for s in sensors:
                            cache[s["entity_id"]] = {"state": s["state"], "attributes": s.get("attributes")}
                            await push(session, s["entity_id"], cache[s["entity_id"]])
                        log.info("Pobrano %d encji (domeny: %s)", len(sensors), ", ".join(sorted(DOMAINS)))
                        for s in states:
                            if mirrored(s["entity_id"]):
                                devices[s["entity_id"]] = {"state": s["state"], "attributes": s.get("attributes")}
                        for dom, lst in CONTROL.items():
                            missing = (lst or set()) - set(devices)
                            if missing:
                                log.warning("Lista sterowania %s zawiera nieistniejące encje: %s",
                                            dom, ", ".join(sorted(missing)))
                        await mqtt_publish_all()
                    elif mid and mid > 100:
                        log.info("Stary HA potwierdził polecenie id=%s", mid)
                elif msg.get("type") == "event":
                    data = msg["event"]["data"]
                    new = data.get("new_state")
                    eid = data.get("entity_id")
                    if not new or not eid:
                        continue
                    if mirrored(eid):
                        known = eid in devices
                        devices[eid] = {"state": new["state"], "attributes": new.get("attributes")}
                        if timed(eid) and new["state"] == "closed" and eid not in cover_motion:
                            cover_pos[eid] = 0.0
                        await mqtt_publish(eid, with_config=not known)
                    if wanted(eid):
                        cache[eid] = {"state": new["state"], "attributes": new.get("attributes")}
                        await push(session, eid, cache[eid])
        finally:
            old_ws = None
            await mqtt_status()


async def main() -> None:
    global old_ws
    log.info("Sterowanie – termostaty (temperatura): %s", describe("climate"))
    log.info("Sterowanie – przełączniki: %s", describe("switch"))
    log.info("Sterowanie – rolety i brama: %s", describe("cover"))
    if COVER_TIMED is None or COVER_TIMED:
        log.info("Rolety z pozycją na czas: %s (czas przejazdu domyślnie %.0f s, osobno: %s)",
                 "wszystkie" if COVER_TIMED is None else ", ".join(sorted(COVER_TIMED)),
                 COVER_TRAVEL_SECONDS, COVER_TRAVEL_TIMES or "brak")
    delay = 5
    async with aiohttp.ClientSession() as session:
        asyncio.create_task(resync_loop(session))
        asyncio.create_task(mqtt_loop())
        while True:
            try:
                await run_once(session)
                delay = 5
                log.warning("Połączenie ze starym HA zamknięte – ponawiam")
            except Exception as e:
                log.error("Błąd połączenia ze starym HA: %s", e)
            old_ws = None
            await asyncio.sleep(delay)
            delay = min(delay * 2, 300)


if __name__ == "__main__":
    asyncio.run(main())
