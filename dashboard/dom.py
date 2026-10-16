"""
Generator dashboardu „DOM”.

Uruchomienie (zapisuje dashboard w działającym nowym HA):
    python3 dashboard/dom.py            # zapis do HA
    python3 dashboard/dom.py --print    # tylko pokaż JSON

Czyta NEW_HA_TOKEN z .env. Adres nowego HA: NEW_HA_URL (domyślnie http://localhost:8123).
"""
import asyncio
import json
import os
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent

# ---------- pokoje: (nazwa, ikona, [(encja, etykieta)]) ----------

PARTER = [
    ("Salon", "mdi:sofa", [
        ("switch.salon_1", "Światło 1"), ("switch.salon_2", "Światło 2"), ("switch.salon_3", "Światło 3"),
        ("switch.salon_led3", "LED"),
        ("switch.gniazdko_salon_1", "Gniazdko 1"), ("switch.gniazdko_salon_2", "Gniazdko 2"),
        ("switch.gniazdko_salon_3", "Gniazdko 3"),
    ]),
    ("Kuchnia i jadalnia", "mdi:silverware-fork-knife", [
        ("switch.kuchnia", "Kuchnia 1"), ("switch.kuchnia_2", "Kuchnia 2"), ("switch.jadalnia", "Jadalnia"),
    ]),
    ("Siłownia", "mdi:dumbbell", [
        ("switch.pokoj_1_1", "Światło 1"), ("switch.pokoj_1_2", "Światło 2"), ("switch.pokoj_1_3", "Światło 3"),
    ]),
    ("Korytarz i wejście", "mdi:door", [
        ("switch.wiatrolap", "Wiatrołap"), ("switch.korytarz", "Korytarz 1"), ("switch.korytarz_2", "Korytarz 2"),
        ("switch.schody", "Schody"), ("switch.schowek", "Schowek"),
    ]),
    ("WC i kotłownia", "mdi:toilet", [
        ("switch.wc_2", "WC 1"), ("switch.wc_2_2", "WC 2"), ("switch.kotlownia", "Kotłownia"),
    ]),
    ("Garaż", "mdi:garage", [
        ("switch.garaz_1", "Światło 1"), ("switch.garaz_2", "Światło 2"),
    ]),
]

PIETRO = [
    ("Maciek", "mdi:bed", [("switch.p1_pokoj_1_1", "Światło 1"), ("switch.p1_pokoj_1_2", "Światło 2"),
                            ("switch.p1_pokoj_1_3", "Światło 3")]),
    ("Aga", "mdi:bed", [("switch.p1_pokoj_2_1", "Światło 1"), ("switch.p1_pokoj_2_2", "Światło 2"),
                            ("switch.p1_pokoj_2_3", "Światło 3"), ("switch.p1_pokoj_2_4", "Światło 4")]),
    ("Emi", "mdi:bed", [("switch.p1_pokoj_3_1", "Światło 1"), ("switch.p1_pokoj_3_2", "Światło 2"),
                            ("switch.p1_pokoj_3_3", "Światło 3")]),
    ("Maja", "mdi:bed", [("switch.p1_pokoj_4_1", "Światło 1"), ("switch.p1_pokoj_4_2", "Światło 2"),
                            ("switch.p1_pokoj_4_3", "Światło 3")]),
    ("Łazienka sypialnia", "mdi:shower", [("switch.p1_lazienka_1_1", "Światło 1"), ("switch.p1_lazienka_1_2", "Światło 2"),
                                  ("switch.p1_lazienka_1_3", "Światło 3")]),
    ("Duża łazienka", "mdi:bathtub", [("switch.p1_lazienka_2_1", "Światło 1"), ("switch.p1_lazienka_2_2", "Światło 2"),
                                   ("switch.p1_lazienka_2_3", "Światło 3"), ("switch.p1_lazienka_2_4", "Światło 4"),
                                   ("switch.p1_lazienka_2_5", "Światło 5")]),
    ("Korytarz piętro", "mdi:stairs", [("switch.p1_korytarz_1", "Korytarz 1"), ("switch.p1_korytarz_2", "Korytarz 2")]),
]

ZEWNATRZ = [
    ("Ogród i taras", "mdi:pine-tree", [
        ("switch.przed_wejsciem", "Przed wejściem"), ("switch.balkon", "Balkon"),
        ("switch.pergola", "Pergola"), ("switch.pergola_led", "Pergola LED"),
        ("switch.gniazdko_ogrod", "Gniazdko ogród"),
    ]),
]

ROLETY = [
    ("cover.roleta_salon", "Salon"), ("cover.roleta_kuchnia", "Kuchnia"), ("cover.roleta_gabinet", "Gabinet"),
    ("cover.roleta_pokoj_3", "Emi"), ("cover.roleta_pokoj_4", "Maja"), ("cover.roleta_pergola", "Pergola"),
]

# (pokój, termostat, czujnik temperatury, przekaźnik grzania)
OGRZEWANIE_PARTER = [
    ("Salon", "salon"), ("Kuchnia", "kuchnia"), ("Siłownia", "pokoj"), ("WC", "wc"), ("Wiatrołap", "wiatrolap"),
]
OGRZEWANIE_PIETRO = [
    ("Maciek", "p1_pokoj_1"), ("Aga", "p1_pokoj_2"), ("Emi", "p1_pokoj_3"), ("Maja", "p1_pokoj_4"),
    ("Łazienka sypialnia", "p1_lazienka_1"), ("Duża łazienka", "p1_lazienka_2"),
]

ALARMY = [
    ("binary_sensor.dom_pozar", "Pożar"),
    ("binary_sensor.dom_woda_1", "Zalanie 1"),
    ("binary_sensor.dom_woda_2", "Zalanie 2"),
]

# Klimatyzatory Gree (integracja Gree Climate, lokalnie przez VLAN 2)
KLIMY = [
    ("climate.7cb8e62f5795", "Salon"),
    ("climate.7cb8e61ea946", "Maja"),
    ("climate.7cb8e61f5a27", "Maciek"),
    ("climate.7cb8e61fc3e6", "Emi"),
    ("climate.7cb8e61fb72f", "Aga"),
]


# ---------- klocki ----------

def heading(text, icon=None, style="title", **extra):
    card = {"type": "heading", "heading": text, "heading_style": style}
    if icon:
        card["icon"] = icon
    return card | extra


# Kolory kafelków świateł dla każdego piętra – działa przez config/www/dom-kafelek.js.
# Wyłączony: delikatne tło i przygaszona ikona. Włączony: mocne tło i obramowanie w kolorze piętra.
FLOOR_RGB = {
    "amber": "255, 193, 7",
    "light-blue": "3, 169, 244",
    "light-green": "139, 195, 74",
}


def light(entity, name, color="amber"):
    grid = {"columns": 6}
    tile = {"type": "tile", "entity": entity, "name": name, "vertical": False, "color": color,
            "tap_action": {"action": "toggle"}, "icon_tap_action": {"action": "toggle"},
            "grid_options": grid}
    if color not in FLOOR_RGB:
        return tile
    rgb = FLOOR_RGB[color]
    return {"type": "custom:dom-kafelek", "grid_options": grid, "card": tile,
            "stany": {
                "off": {"--ha-card-background": f"rgba({rgb}, 0.06)",
                        "--state-inactive-color": f"rgba({rgb}, 0.55)",
                        "--state-switch-inactive-color": f"rgba({rgb}, 0.55)",
                        "--state-switch-off-color": f"rgba({rgb}, 0.55)"},
                "on": {"--ha-card-background": f"rgba({rgb}, 0.28)",
                       "--ha-card-border-color": f"rgba({rgb}, 0.9)",
                       "--ha-card-border-width": "2px"},
            }}


def cover(entity, name, wide=False):
    return {"type": "tile", "entity": entity, "name": name,
            "tap_action": {"action": "more-info"},
            "features": [{"type": "cover-open-close"}, {"type": "cover-position"}],
            "features_position": "bottom",
            "grid_options": {"columns": 12 if wide else 6}}


def thermostat(name, obj):
    return {"type": "tile", "entity": f"climate.ogrzewanie_{obj}", "name": name,
            "state_content": ["current_temperature", "hvac_action"],
            "features": [{"type": "target-temperature"}],
            "features_position": "bottom",
            "grid_options": {"columns": 6}}


def button(name, icon, script, confirm):
    return {"type": "button", "name": name, "icon": icon, "show_state": False,
            "tap_action": {"action": "perform-action", "perform_action": f"script.{script}",
                           "confirmation": {"text": confirm}},
            "grid_options": {"columns": 4, "rows": 2}}


def badge(entity, name, **extra):
    return {"type": "entity", "entity": entity, "name": name, "show_name": True, "show_state": True,
            "show_icon": True} | extra


def room_section(title, icon, items, color="amber"):
    return {"type": "grid", "cards": [heading(title, icon)] + [light(e, n, color) for e, n in items]}


def floor_header(title, icon, counter, zone, color, rooms):
    """Pełnoszeroki pasek oddzielający piętra: nazwa, licznik włączonych świateł, „Zgaś”."""
    return {"type": "grid", "column_span": 4, "cards": [
        {"type": "heading", "heading": title, "icon": icon, "heading_style": "title",
         "badges": [
             {"type": "entity", "entity": counter, "color": color, "show_icon": True, "show_state": True,
              "state_content": "state"},
             {"type": "entity", "entity": "script.zgas_swiatla", "name": "Zgaś", "show_name": True,
              "show_state": False, "icon": "mdi:lightbulb-off", "color": color,
              "tap_action": {"action": "perform-action", "perform_action": "script.zgas_swiatla",
                             "data": {"strefa": zone},
                             "confirmation": {"text": f"Zgasić wszystkie światła: {title.lower()}?"}}},
         ]},
        {"type": "markdown", "text_only": True,
         "content": " · ".join(r for r, _, _ in rooms)},
    ]}


def all_switches(rooms):
    return [e for _, _, items in rooms for e, _ in items]


LIGHTS = [e for e in all_switches(PARTER + PIETRO + ZEWNATRZ) if "gniazdko" not in e]


# ---------- zakładki ----------

def view_przeglad():
    alarm_badges = [badge(e, n, color="red", visibility=[{"condition": "state", "entity": e, "state": "on"}])
                    for e, n in ALARMY]
    return {
        "title": "Przegląd", "path": "przeglad", "icon": "mdi:home", "type": "sections", "max_columns": 3,
        "badges": alarm_badges + [
            badge("binary_sensor.brama_otwarta", "Brama"),
            badge("sensor.dom_temperatura_salon", "Salon"),
            badge("sensor.dom_temperatura_p1_pokoj_1", "Maciek"),
            badge("sensor.dom_ls", "Światło"),
        ],
        "sections": [
            {"type": "grid", "cards": [
                heading("Bezpieczeństwo", "mdi:shield-home"),
                *[{"type": "tile", "entity": e, "name": n, "grid_options": {"columns": 4},
                   "vertical": True} for e, n in ALARMY],
                {"type": "tile", "entity": "binary_sensor.brama_otwarta", "name": "Brama",
                 "grid_options": {"columns": 12}},
                cover("cover.brama", "Sterowanie bramą", wide=True),
            ]},
            {"type": "grid", "cards": [
                heading("Szybkie akcje", "mdi:lightning-bolt"),
                button("Zgaś światła", "mdi:lightbulb-group-off", "wylacz_wszystkie_swiatla",
                       "Wyłączyć wszystkie światła w domu?"),
                button("Rolety w dół", "mdi:window-shutter", "zamknij_wszystkie_rolety",
                       "Zamknąć wszystkie rolety?"),
                button("Rolety w górę", "mdi:window-shutter-open", "otworz_wszystkie_rolety",
                       "Otworzyć wszystkie rolety?"),
                {"type": "entity-filter",
                 "entities": [{"entity": e} for e in LIGHTS],
                 "conditions": [{"condition": "state", "state": "on"}],
                 "show_empty": True,
                 "card": {"type": "entities", "title": "Włączone światła",
                          "state_color": True}},
            ]},
            {"type": "grid", "cards": [
                heading("Temperatura", "mdi:thermometer"),
                {"type": "glance", "title": "Parter", "columns": 3, "state_color": True,
                 "entities": [{"entity": f"sensor.dom_temperatura_{o}", "name": n} for n, o in OGRZEWANIE_PARTER]},
                {"type": "glance", "title": "Piętro", "columns": 3, "state_color": True,
                 "entities": [{"entity": f"sensor.dom_temperatura_{o}", "name": n} for n, o in OGRZEWANIE_PIETRO]},
            ]},
        ],
    }


def view_swiatla():
    # Każde piętro ma własny kolor włączonych świateł i pełnoszeroki nagłówek.
    zones = [
        ("Parter", "mdi:home-floor-0", PARTER, "sensor.swiatla_parter", "parter", "amber"),
        ("Piętro", "mdi:home-floor-1", PIETRO, "sensor.swiatla_pietro", "pietro", "light-blue"),
        ("Na zewnątrz", "mdi:tree", ZEWNATRZ, "sensor.swiatla_na_zewnatrz", "zewnatrz", "light-green"),
    ]
    sections = []
    for zone, zicon, rooms, counter, key, color in zones:
        sections.append(floor_header(zone, zicon, counter, key, color, rooms))
        sections += [room_section(title, icon, items, color) for title, icon, items in rooms]
    return {"title": "Światła", "path": "swiatla", "icon": "mdi:lightbulb-group", "type": "sections",
            "max_columns": 4, "dense_section_placement": False, "sections": sections}


def view_rolety():
    return {
        "title": "Rolety", "path": "rolety", "icon": "mdi:window-shutter", "type": "sections", "max_columns": 2,
        "sections": [
            {"type": "grid", "cards": [
                heading("Wszystkie rolety", "mdi:window-shutter"),
                button("W dół", "mdi:window-shutter", "zamknij_wszystkie_rolety", "Zamknąć wszystkie rolety?"),
                button("W górę", "mdi:window-shutter-open", "otworz_wszystkie_rolety", "Otworzyć wszystkie rolety?"),
                *[cover(e, n) for e, n in ROLETY],
            ]},
            {"type": "grid", "cards": [
                heading("Brama", "mdi:gate"),
                cover("cover.brama", "Brama", wide=True),
                {"type": "tile", "entity": "binary_sensor.brama_otwarta", "name": "Czujnik bramy",
                 "grid_options": {"columns": 12}},
            ]},
        ],
    }


def view_ogrzewanie():
    def floor(title, icon, rooms, zone):
        return {"type": "grid", "cards": [
            heading(title, icon),
            {"type": "tile", "entity": f"input_number.temperatura_{zone}", "name": f"Cały {title.lower()}",
             "features": [{"type": "numeric-input", "style": "slider"}], "grid_options": {"columns": 12}},
            *[thermostat(n, o) for n, o in rooms],
        ]}

    return {
        "title": "Ogrzewanie", "path": "ogrzewanie", "icon": "mdi:radiator", "type": "sections", "max_columns": 3,
        "sections": [
            {"type": "grid", "cards": [
                heading("Cały dom", "mdi:home-thermometer"),
                {"type": "tile", "entity": "input_number.temperatura_dom", "name": "Ustaw wszędzie",
                 "features": [{"type": "numeric-input", "style": "slider"}], "grid_options": {"columns": 12}},
                {"type": "markdown", "content":
                    "Suwak strefy ustawia tę samą temperaturę na wszystkich termostatach w strefie. "
                    "Pojedynczy pokój zmienisz przyciskami +/− na jego kafelku."},
            ]},
            floor("Parter", "mdi:home-floor-0", OGRZEWANIE_PARTER, "parter"),
            floor("Piętro", "mdi:home-floor-1", OGRZEWANIE_PIETRO, "pietro"),
            {"type": "grid", "cards": [
                heading("Przekaźniki grzania", "mdi:radiator", style="subtitle"),
                {"type": "glance", "columns": 4, "state_color": True, "show_state": False,
                 "entities": [{"entity": f"switch.ogrzewanie_{o}", "name": n, "tap_action": {"action": "more-info"}}
                              for n, o in OGRZEWANIE_PARTER + OGRZEWANIE_PIETRO]},
            ]},
        ],
    }


def klima(entity, name):
    return {"type": "tile", "entity": entity, "name": name, "icon": "mdi:air-conditioner",
            "state_content": ["hvac_action", "current_temperature"],
            "tap_action": {"action": "more-info"},
            "features": [{"type": "climate-hvac-modes", "style": "icons",
                          "hvac_modes": ["off", "cool", "heat", "dry", "fan_only", "auto"]},
                         {"type": "target-temperature"},
                         {"type": "climate-fan-modes", "style": "dropdown"}],
            "features_position": "bottom",
            "grid_options": {"columns": 6}}


def view_klimatyzacja():
    wszystkie = [e for e, _ in KLIMY]
    return {
        "title": "Klimatyzacja", "path": "klimatyzacja", "icon": "mdi:air-conditioner", "type": "sections",
        "max_columns": 2,
        "sections": [
            {"type": "grid", "cards": [
                heading("Klimatyzacja", "mdi:air-conditioner"),
                {"type": "button", "name": "Wyłącz wszystkie", "icon": "mdi:power", "show_state": False,
                 "tap_action": {"action": "perform-action", "perform_action": "climate.turn_off",
                                "target": {"entity_id": wszystkie},
                                "confirmation": {"text": "Wyłączyć wszystkie klimatyzatory?"}},
                 "grid_options": {"columns": 4, "rows": 2}},
                *[klima(e, n) for e, n in KLIMY],
            ]},
        ],
    }


def view_historia():
    def graph(title, rooms):
        return {"type": "history-graph", "title": title, "hours_to_show": 48,
                "entities": [{"entity": f"sensor.dom_temperatura_{o}", "name": n} for n, o in rooms]}

    return {
        "title": "Historia", "path": "historia", "icon": "mdi:chart-line", "type": "sections", "max_columns": 2,
        "sections": [
            {"type": "grid", "cards": [heading("Temperatury", "mdi:chart-line"),
                                       graph("Parter – 48 h", OGRZEWANIE_PARTER),
                                       graph("Piętro – 48 h", OGRZEWANIE_PIETRO)]},
            {"type": "grid", "cards": [
                heading("Pozostałe", "mdi:information-outline"),
                {"type": "history-graph", "title": "Natężenie światła", "hours_to_show": 48,
                 "entities": [{"entity": "sensor.dom_ls", "name": "Czujnik światła"}]},
                {"type": "logbook", "title": "Ostatnie zdarzenia", "hours_to_show": 24,
                 "target": {"entity_id": ["cover.brama", "binary_sensor.brama_otwarta",
                                          *[e for e, _ in ALARMY]]}},
                {"type": "entities", "title": "Drukarka", "entities": [
                    {"entity": "sensor.dom_hp_laserjet_mfp_m129_m134", "name": "Stan"},
                    {"entity": "sensor.dom_hp_laserjet_mfp_m129_m134_black_cartridge_hp_cf217a", "name": "Toner"},
                    {"entity": "sensor.dom_hp_laserjet_mfp_m129_m134_imaging_drum_hp_cf219a", "name": "Bęben"},
                ]},
            ]},
        ],
    }


def dashboard():
    return {"title": "Dom", "views": [view_przeglad(), view_swiatla(), view_rolety(), view_ogrzewanie(),
                                      view_klimatyzacja(), view_historia()]}


# ---------- zapis do HA ----------

def env(name, default=None):
    if name in os.environ:
        return os.environ[name]
    for line in (ROOT / ".env").read_text().splitlines():
        if line.startswith(f"{name}="):
            return line.split("=", 1)[1].strip()
    return default


async def save(config):
    import aiohttp
    url = env("NEW_HA_URL", "http://localhost:8123").replace("http", "ws", 1) + "/api/websocket"
    async with aiohttp.ClientSession() as s, s.ws_connect(url, max_msg_size=0) as ws:
        await ws.receive_json()
        await ws.send_json({"type": "auth", "access_token": env("NEW_HA_TOKEN")})
        if (await ws.receive_json()).get("type") != "auth_ok":
            sys.exit("Nie mogę zalogować się do nowego HA – sprawdź NEW_HA_TOKEN")
        await ws.send_json({"id": 1, "type": "lovelace/config/save", "url_path": "dashboard-dom",
                            "config": config})
        r = await ws.receive_json()
        if not r.get("success"):
            sys.exit(f"HA odrzucił dashboard: {r.get('error')}")
        print("Zapisano dashboard „DOM”:", ", ".join(v["title"] for v in config["views"]))


if __name__ == "__main__":
    cfg = dashboard()
    if "--print" in sys.argv:
        print(json.dumps(cfg, ensure_ascii=False, indent=2))
    else:
        asyncio.run(save(cfg))
