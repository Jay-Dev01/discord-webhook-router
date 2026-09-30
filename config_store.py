"""Load and atomically save routing rules, including on a persistent volume."""

import json
import os
import tempfile
from pathlib import Path

from routing import RoutingConfig


def save_config(path: Path, config: RoutingConfig):
    temporary = None
    try:
        with tempfile.NamedTemporaryFile(mode="w", encoding="utf-8", dir=path.parent,
                                         prefix=".routing-", suffix=".tmp", delete=False) as handle:
            temporary = Path(handle.name)
            json.dump(config.data, handle, indent=2)
            handle.write("\n")
            handle.flush()
            os.fsync(handle.fileno())
        temporary.replace(path)
    finally:
        if temporary is not None:
            temporary.unlink(missing_ok=True)


def load_config(path: Path, initial_source: str = "") -> RoutingConfig:
    try:
        data = path.read_text(encoding="utf-8-sig")
    except FileNotFoundError:
        if not initial_source.strip():
            raise ValueError("Set SOURCE_CHANNEL_ID for first-time setup, or supply an existing config.json.") from None
        config = RoutingConfig.from_dict({"source_channel_id": initial_source.strip(),
                                          "address_field": "Delivered To", "routes": []})
        path.parent.mkdir(parents=True, exist_ok=True)
        save_config(path, config)
        return config
    # Never replace existing rules using bootstrap environment variables.
    return RoutingConfig.from_dict(json.loads(data))
