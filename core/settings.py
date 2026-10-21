"""
Small local settings store (JSON in ~/.thumbcutter/config.json).

Holds the Anthropic API key, model name, theme, and the channel / social-link
details used to build the description footer. Plain text on your machine only.
"""
import json
import os

CONFIG_DIR = os.path.join(os.path.expanduser("~"), ".thumbcutter")
CONFIG_PATH = os.path.join(CONFIG_DIR, "config.json")

# First-run defaults. Everything here can be changed in Settings.
DEFAULTS = {
    "theme": "dark",
    "channel": "",   # your @handle or channel URL, set in Settings
    "socials": "",   # one "Label: https://..." per line, set in Settings
}


def load():
    if os.path.exists(CONFIG_PATH):
        try:
            with open(CONFIG_PATH, encoding="utf-8") as f:
                return json.load(f)
        except Exception:
            return {}
    return {}


def save(cfg):
    os.makedirs(CONFIG_DIR, exist_ok=True)
    with open(CONFIG_PATH, "w", encoding="utf-8") as f:
        json.dump(cfg, f, indent=2)


def get(key, default=None):
    cfg = load()
    if key in cfg:
        return cfg[key]
    return DEFAULTS.get(key, default)


def set_value(key, value):
    cfg = load()
    cfg[key] = value
    save(cfg)
