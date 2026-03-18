from pathlib import Path

import yaml

_CONFIG = None


def get_config():
    global _CONFIG

    if _CONFIG is None:
        config_path = Path(__file__).with_name("config.yaml")
        with config_path.open("r", encoding="utf-8") as file:
            _CONFIG = yaml.safe_load(file)

    return _CONFIG
