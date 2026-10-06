"""Drive a destination's target-side ``vhid`` over SSH — real-hardware HID input.

Why this exists: macOS Secure Event Input silently drops the *synthetic* events that
screensharingd injects for RFB, so the RFB path cannot type into / click the lock screen,
the login window, or authd "modify settings" sheets. ``vhid`` (promptctl/vhid, built on the
pqrs Karabiner DriverKit VirtualHIDDevice) presents a real hardware keyboard+mouse, so its
input reaches those places. This module hides everything about reaching it: the SSH
transport, shell-safe quoting, MCP-name -> vhid-name key mapping, and readiness probing.

Input only — screen capture stays on the RFB path. Every verb returns ``(ok, detail)`` and
never raises for an unreachable/erroring target, so the caller can fall back to RFB.

Coordinates are passed through unchanged: the handlers already scale a request's
source coordinates to the framebuffer, and the framebuffer is the display, so those integers
are exactly vhid's "absolute screen point from the top-left of the main display".
"""

import logging
import subprocess
from typing import List, Optional, Tuple

logger = logging.getLogger("vhid_backend")

# MCP key/modifier names (see action_handlers special_keys/modifier_keys) -> vhid names.
# vhid is built on Karabiner-DriverKit, which uses these identifiers. type_text and click
# (the secure-sheet-critical verbs) need none of this; an unmapped key makes map_keys
# return None so the caller falls back to RFB rather than press a wrong key.
_MODIFIERS = {
    "cmd": "leftCommand", "command": "leftCommand", "win": "leftCommand",
    "super": "leftCommand", "meta": "leftCommand",
    "shift": "leftShift", "ctrl": "leftControl", "control": "leftControl",
    "alt": "leftOption", "option": "leftOption", "fn": "fn",
}
_SPECIALS = {
    "enter": "return_or_enter", "return": "return_or_enter",
    "escape": "escape", "esc": "escape", "tab": "tab", "space": "spacebar",
    "delete": "delete_forward", "del": "delete_forward", "backspace": "delete_or_backspace",
    "up": "up_arrow", "down": "down_arrow", "left": "left_arrow", "right": "right_arrow",
    "home": "home", "end": "end", "page_up": "page_up", "page_down": "page_down",
    **{f"f{i}": f"f{i}" for i in range(1, 13)},
}


def _run(ssh: str, vhid_args: List[str], timeout: int = 20) -> Tuple[bool, str]:
    """Run ``vhid <args>`` on the target reached by the ssh endpoint (e.g. 'dima@host').

    vhidd runs as root on the target, so the SSH user needs no sudo. Returns (ok, detail);
    a non-zero exit, a timeout, or SSH failure is (False, <stderr/first line>), never raised.
    """
    cmd = ["ssh", "-o", "BatchMode=yes", "-o", "ConnectTimeout=10", ssh, "vhid", *vhid_args]
    try:
        p = subprocess.run(cmd, capture_output=True, text=True, timeout=timeout)
    except subprocess.TimeoutExpired:
        return False, "vhid over ssh timed out"
    except Exception as e:  # noqa: BLE001
        return False, f"ssh/vhid invocation failed: {e}"
    if p.returncode == 0:
        return True, (p.stdout or "").strip()
    detail = (p.stderr or p.stdout or "").strip().splitlines()
    return False, detail[0] if detail else f"vhid exited {p.returncode}"


def is_ready(ssh: str) -> bool:
    """True iff ``vhid doctor`` on the target reports 'ready'.

    A False means the DriverKit extension isn't approved yet (one-time manual step), so the
    caller should fall back to RFB rather than issue vhid verbs that will be refused.
    """
    ok, out = _run(ssh, ["doctor"], timeout=15)
    # `vhid doctor` prints exactly "ready" when good, or "not ready" + follow-up lines.
    # Match the first line exactly so "not ready" isn't mistaken for ready.
    first = out.strip().lower().split("\n", 1)[0].strip()
    return ok and first == "ready"


def map_keys(mcp_names: List[str]) -> Optional[str]:
    """Translate MCP key/modifier names to a vhid chord (e.g. 'leftCommand+a'), or None.

    None whenever ANY name is unmapped — the caller then falls back to RFB instead of
    pressing a wrong/partial chord. Letters a-z and digits 0-9 pass through as themselves.
    """
    out: List[str] = []
    for raw in mcp_names:
        name = raw.strip().lower()
        if name in _MODIFIERS:
            out.append(_MODIFIERS[name])
        elif name in _SPECIALS:
            out.append(_SPECIALS[name])
        elif len(name) == 1 and (name.isalnum()):
            out.append(name)
        else:
            return None
    return "+".join(out) if out else None


def type_text(ssh: str, text: str) -> Tuple[bool, str]:
    """Type ``text`` as real keystrokes wherever focus is (no --into: so it targets the
    lock screen / a cross-process sheet, which have no owning app)."""
    return _run(ssh, ["type", text], timeout=max(20, len(text) // 5 + 15))


def press(ssh: str, chord: str) -> Tuple[bool, str]:
    """Press one vhid chord already in vhid syntax (e.g. 'return_or_enter', 'leftCommand+a').
    Use map_keys() to build ``chord`` from MCP names first."""
    # ponytail: whole-press only (no held-across-calls chord) — the MCP never holds keys
    # between calls, so down/up split isn't needed; revisit if a hold-key tool is added.
    return _run(ssh, ["press", chord])


def click(ssh: str, x: int, y: int, button: str = "left", times: int = 1,
          modifiers: Optional[str] = None) -> Tuple[bool, str]:
    """Left/right click ``times`` at absolute screen point (x, y) — the framebuffer
    coordinates the handler already computed. ``modifiers`` is a vhid chord or None."""
    args = ["click", str(int(x)), str(int(y)), "--button", button, "--times", str(int(times))]
    if modifiers:
        args += ["--modifiers", modifiers]
    return _run(ssh, args)


def move(ssh: str, x: int, y: int) -> Tuple[bool, str]:
    """Move the pointer to absolute screen point (x, y), pressing nothing."""
    return _run(ssh, ["move", str(int(x)), str(int(y))])


def scroll(ssh: str, x: int, y: int, vertical: int) -> Tuple[bool, str]:
    """Roll the wheel ``vertical`` notches over the point (x, y) (sign = direction)."""
    return _run(ssh, ["scroll", str(int(x)), str(int(y)), "--vertical", str(int(vertical))])


def drag(ssh: str, x1: int, y1: int, x2: int, y2: int) -> Tuple[bool, str]:
    """Press at (x1, y1) and release at (x2, y2) — a drag between two screen points."""
    return _run(ssh, ["drag", str(int(x1)), str(int(y1)), str(int(x2)), str(int(y2))])
