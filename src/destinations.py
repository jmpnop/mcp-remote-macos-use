"""Destination registry for the remote-macos-use MCP server.

ONE server process, MANY target Macs. A tool call picks a target by name via the
optional ``destination`` argument; the name is resolved here to concrete VNC
connection parameters. This replaces the old "one server process per machine,
host baked into env at startup" design.

Resolution order (first hit wins for the registry source):
  1. ``MACOS_DESTINATIONS`` env var holding the registry as JSON.
  2. A JSON file: ``MACOS_DESTINATIONS_FILE`` or ~/.config/remote-macos/destinations.json.
The legacy single-destination env vars (``MACOS_HOST`` etc.), if present, are
always folded in as a destination named ``env`` and used as the default when the
registry declares none — so an old-style env-only setup keeps working.

Registry JSON shape::

    {
      "default": "paimei",
      "destinations": {
        "paimei": {"host": "10.10.10.7", "port": 5900, "username": "pasha",
                   "password": "...", "encryption": "prefer_on"},
        "dima":   {"host": "DIMAS-MBA.local", "username": "dima", "password": "..."}
      }
    }

``port`` defaults to 5900, ``encryption`` to ``prefer_on``, ``prefer_hid`` to
False. Secrets live in the config file (mode 600) under ~/.config, matching the
cloudflare skill's pattern — not in this MIT-licensed package tree.
"""

import json
import logging
import os
from typing import Dict, List, Optional, Tuple

logger = logging.getLogger("destinations")

DEFAULT_REGISTRY_PATH = os.path.expanduser("~/.config/remote-macos/destinations.json")


def _load_registry() -> Tuple[Dict[str, dict], Optional[str]]:
    """Return (destinations_by_name, default_name). Never raises."""
    data: Optional[dict] = None

    raw = os.environ.get("MACOS_DESTINATIONS")
    if raw:
        try:
            data = json.loads(raw)
        except Exception as e:  # noqa: BLE001
            logger.error("MACOS_DESTINATIONS is not valid JSON: %s", e)

    if data is None:
        path = os.environ.get("MACOS_DESTINATIONS_FILE", DEFAULT_REGISTRY_PATH)
        if os.path.exists(path):
            try:
                with open(path, "r") as fh:
                    data = json.load(fh)
            except Exception as e:  # noqa: BLE001
                logger.error("Failed to read destinations file %s: %s", path, e)

    if not isinstance(data, dict):
        data = {}

    destinations: Dict[str, dict] = dict(data.get("destinations", {}) or {})
    default: Optional[str] = data.get("default")

    # Legacy single-destination env vars → folded in as "env".
    legacy_host = os.environ.get("MACOS_HOST", "")
    if legacy_host:
        destinations.setdefault(
            "env",
            {
                "host": legacy_host,
                "port": int(os.environ.get("MACOS_PORT", "5900")),
                "username": os.environ.get("MACOS_USERNAME", ""),
                "password": os.environ.get("MACOS_PASSWORD", ""),
                "encryption": os.environ.get("VNC_ENCRYPTION", "prefer_on"),
            },
        )
        if not default:
            default = "env"

    if not default and destinations:
        default = sorted(destinations.keys())[0]

    return destinations, default


def list_names() -> List[str]:
    """Sorted list of registered destination names (may be empty)."""
    destinations, _ = _load_registry()
    return sorted(destinations.keys())


def default_name() -> Optional[str]:
    """Name used when a tool call omits ``destination`` (or None)."""
    _, default = _load_registry()
    return default


def resolve(name: Optional[str] = None) -> Tuple[str, int, str, str, str]:
    """Resolve a destination name to (host, port, password, username, encryption).

    The tuple order matches the long-standing handler unpacking order. Raises
    ValueError with the known names if the registry is empty or the name is
    unknown, so the failure is actionable instead of a silent connect to "".
    """
    destinations, default = _load_registry()
    key = name or default

    if not destinations:
        raise ValueError(
            "No remote-macOS destinations configured. Create "
            f"{DEFAULT_REGISTRY_PATH} (or set MACOS_DESTINATIONS / MACOS_HOST)."
        )
    if not key or key not in destinations:
        raise ValueError(
            f"Unknown destination {name!r}. Known: {sorted(destinations.keys())} "
            f"(default: {default!r})."
        )

    d = destinations[key]
    return (
        d["host"],
        int(d.get("port", 5900)),
        d.get("password", ""),
        d.get("username", ""),
        d.get("encryption", "prefer_on"),
    )


def input_backend(name: Optional[str] = None) -> Tuple[str, Optional[str]]:
    """Return how to deliver keyboard/mouse input to a destination.

    ('vhid', '<ssh endpoint>') when the destination sets ``input: "vhid"`` and an ``ssh``
    endpoint (e.g. 'dima@10.10.10.91') — input then goes through target-side vhid (real HID,
    reaches Secure-Event-Input dialogs). Otherwise ('rfb', None) — the standard RFB path.
    Screen capture always uses RFB regardless of this.
    """
    destinations, default = _load_registry()
    d = destinations.get((name or default) or "", {})
    if str(d.get("input", "rfb")).lower() == "vhid" and d.get("ssh"):
        return "vhid", d["ssh"]
    return "rfb", None


def prefers_hid(name: Optional[str] = None) -> bool:
    """Whether a destination opts into the QEMU extended-key (HID) input path."""
    destinations, default = _load_registry()
    key = name or default
    if not key or key not in destinations:
        return False
    return bool(destinations[key].get("prefer_hid", False))
