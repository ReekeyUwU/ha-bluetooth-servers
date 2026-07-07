import json
import os
import sys
import urllib.request

from led_strip_modes import MODES

TOKEN = os.environ["LED_STRIP_AUTH_TOKEN"]
BASE = "http://localhost:8200/led_strip"

NAME_TO_ID = {v: k for k, v in MODES.items()}

CATEGORY_RULES = [
    ("Sprung-Effekte", ["Jump"]),
    ("Stroboskop", ["Strobe"]),
    ("Farbverlauf", ["Gradual"]),
    ("Lichtband", ["Marquee"]),
    ("Rennen", ["Race"]),
    ("Wellen", ["Wave"]),
    ("Blitz-Effekte", ["Flush"]),
    ("Auf-Zu", ["Open", "Close"]),
    ("Uebergaenge", ["Trans", "6-Color to"]),
    ("Wasser-Effekte", ["Water"]),
    ("Fliessend", ["Flow"]),
    ("Schweif-Effekte", ["Tail"]),
    ("Lauflicht", ["Running"]),
]


def categorize():
    categories = {name: [] for name, _ in CATEGORY_RULES}
    categories["Sonstige"] = []
    for mode_id, mode_name in MODES.items():
        matched = False
        for cat_name, keywords in CATEGORY_RULES:
            if any(kw in mode_name for kw in keywords):
                categories[cat_name].append(mode_name)
                matched = True
                break
        if not matched:
            categories["Sonstige"].append(mode_name)
    return categories


def post(path, data=None):
    req = urllib.request.Request(
        f"{BASE}/{path}",
        data=json.dumps(data).encode() if data is not None else None,
        headers={"X-Auth-Token": TOKEN, "Content-Type": "application/json"},
        method="POST",
    )
    with urllib.request.urlopen(req, timeout=10) as resp:
        return resp.read().decode()


if __name__ == "__main__":
    action = sys.argv[1] if len(sys.argv) > 1 else ""

    if action == "list_mode_names":
        for name in MODES.values():
            print(name)
    elif action == "list_mode_names_json":
        print(json.dumps({"modes": ["Aus"] + list(MODES.values())}))
    elif action == "list_categories_json":
        cats = categorize()
        cats = {k: ["Aus"] + v for k, v in cats.items() if v}
        cats["Regenbogen-Kandidaten"] = ["Aus"] + [n for n in MODES.values() if "7-Color" in n]
        print(json.dumps(cats))
    elif action == "mode_set_by_name":
        mode_name = sys.argv[2]
        mode_id = NAME_TO_ID.get(mode_name)
        if mode_id is None:
            print("FEHLER: unbekannter Modus")
            sys.exit(1)
        print(post("mode", {"value": mode_id}))
    else:
        print("Unbekannte Aktion:", action)
        sys.exit(1)
