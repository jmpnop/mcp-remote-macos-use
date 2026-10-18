"""Drive a destination's target-side ``vhid`` over SSH — real-hardware HID input.

Why this exists: macOS Secure Event Input silently drops the *synthetic* events that
screensharingd injects for RFB, so the RFB path cannot type into / click the lock screen,
the login window, or authd "modify settings" sheets. ``vhid`` (promptctl/vhid, built on the
pqrs Karabiner DriverKit VirtualHIDDevice) presents a real hardware keyboard+mouse, so its
input reaches those places. This module hides everything about reaching it: the SSH
transport, shell-safe quoting, MCP-name -> vhid-name key mapping, and readiness probing.

Input only — screen capture stays on the RFB path. Every verb returns ``(ok, detail)`` and
never raises for an unreachable/erroring target, so the caller can fall back to RFB.

Coordinates: the handlers scale a request's source coordinates to the RFB framebuffer, which
is sized in PIXELS. vhid's coordinates are logical SCREEN POINTS (the space the cursor and
CoreGraphics report in). On a Retina/HiDPI display pixels = points x backingScaleFactor (2.0),
so a framebuffer-pixel coordinate is twice the point vhid wants and a click would land
off-screen. This module converts framebuffer pixels -> points itself (``_display_scale`` /
``_to_points``), discovering the main display's scale once per target and caching it, so the
handlers stay dumb about vhid's coordinate space. A non-Retina display is scale 1.0 (a no-op).
"""

import logging
import subprocess
from typing import Dict, List, Optional, Tuple

logger = logging.getLogger("vhid_backend")

# The absolute path the vhid pkg always installs the CLI at. Invoked by full path, never as
# bare ``vhid``: a non-interactive ``ssh host cmd`` runs a non-login shell whose PATH is the
# system default (/usr/bin:/bin:/usr/sbin:/sbin) with no /usr/local/bin, so bare ``vhid``
# is "command not found". [LAW:no-silent-failure]
_VHID = "/usr/local/bin/vhid"

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
    cmd = ["ssh", "-o", "BatchMode=yes", "-o", "ConnectTimeout=10", ssh, _VHID, *vhid_args]
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


# Discovered main-display scale (framebuffer pixels / logical points) per ssh endpoint,
# cached for the process lifetime. (1.0, 1.0) means non-Retina OR an unknown/failed probe:
# treat framebuffer pixels as points, which is exactly right on a 1x display and, on a 2x
# display where the probe failed, lands the unconverted click off-screen so the caller falls
# back to RFB — never a wrong-place click reported as success. [LAW:no-silent-failure]
_scale_cache: Dict[str, Tuple[float, float]] = {}

# Swift snippet that prints the main display's logical size (points) and backing-store size
# (pixels) as "<ptW> <ptH> <pxW> <pxH>". CGMainDisplayID/CGDisplayBounds/CGDisplayCopyDisplayMode
# read geometry without a WindowServer connection, so this answers over a plain (non-GUI) ssh.
_DISPLAY_PROBE = (
    "import CoreGraphics\n"
    "let d = CGMainDisplayID()\n"
    "let b = CGDisplayBounds(d)\n"
    "let m = CGDisplayCopyDisplayMode(d)\n"
    'print("\\(Int(b.width)) \\(Int(b.height)) \\(m?.pixelWidth ?? 0) \\(m?.pixelHeight ?? 0)")\n'
)


def _display_scale(ssh: str) -> Tuple[float, float]:
    """(scale_x, scale_y) = framebuffer pixels / logical points for ``ssh``'s main display.

    Probed once via CoreGraphics (``swift -``) and cached. Any failure — no Swift toolchain,
    probe error, nonsensical numbers — yields (1.0, 1.0): correct on a 1x display, and on a 2x
    display the unconverted click simply misses so the caller falls back to RFB, rather than a
    confident click in the wrong place."""
    cached = _scale_cache.get(ssh)
    if cached is not None:
        return cached
    scale = (1.0, 1.0)
    cmd = ["ssh", "-o", "BatchMode=yes", "-o", "ConnectTimeout=10", ssh, "/usr/bin/swift", "-"]
    try:
        p = subprocess.run(cmd, input=_DISPLAY_PROBE, capture_output=True, text=True, timeout=30)
        if p.returncode == 0:
            parts = (p.stdout or "").split()
            if len(parts) == 4:
                ptw, pth, pxw, pxh = (float(v) for v in parts)
                if ptw > 0 and pth > 0 and pxw > 0 and pxh > 0:
                    scale = (pxw / ptw, pxh / pth)
    except Exception as e:  # noqa: BLE001
        logger.info("display-scale probe failed for %s (%s); assuming 1.0x", ssh, e)
    _scale_cache[ssh] = scale
    logger.info("vhid display scale for %s = %.4gx%.4g", ssh, scale[0], scale[1])
    return scale


def _to_points(ssh: str, x: int, y: int) -> Tuple[int, int]:
    """Convert a framebuffer-PIXEL (x, y) to the logical SCREEN POINTS vhid's verbs expect."""
    sx, sy = _display_scale(ssh)
    return int(round(x / sx)), int(round(y / sy))


def click(ssh: str, x: int, y: int, button: str = "left", times: int = 1,
          modifiers: Optional[str] = None) -> Tuple[bool, str]:
    """Left/right click ``times`` at framebuffer-pixel (x, y) (the handler's scaled coords),
    converted here to the logical screen point vhid clicks. ``modifiers`` is a chord or None."""
    px, py = _to_points(ssh, x, y)
    args = ["click", str(px), str(py), "--button", button, "--times", str(int(times))]
    if modifiers:
        args += ["--modifiers", modifiers]
    return _run(ssh, args)


def move(ssh: str, x: int, y: int) -> Tuple[bool, str]:
    """Move the pointer to framebuffer-pixel (x, y) (converted to logical points), pressing nothing."""
    px, py = _to_points(ssh, x, y)
    return _run(ssh, ["move", str(px), str(py)])


def scroll(ssh: str, x: int, y: int, vertical: int) -> Tuple[bool, str]:
    """Roll the wheel ``vertical`` notches over framebuffer-pixel (x, y) (converted to points)."""
    px, py = _to_points(ssh, x, y)
    return _run(ssh, ["scroll", str(px), str(py), "--vertical", str(int(vertical))])


def drag(ssh: str, x1: int, y1: int, x2: int, y2: int) -> Tuple[bool, str]:
    """Press at (x1, y1) and release at (x2, y2) — framebuffer-pixel coords, each converted to points."""
    p1x, p1y = _to_points(ssh, x1, y1)
    p2x, p2y = _to_points(ssh, x2, y2)
    return _run(ssh, ["drag", str(p1x), str(p1y), str(p2x), str(p2y)])
