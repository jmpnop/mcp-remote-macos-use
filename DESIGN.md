# Design: vhid HID-input backend for remote-macos-use

## Decision Date
2026-10-06

## Ponytail (minimalism) verdict
- Rung 1 (needs to exist?): **Yes, justified** — a real, blocking caller: macOS Secure
  Event Input drops the synthetic events `screensharingd` injects, so RFB can't drive the
  lock screen / authd "modify settings" sheets. Not speculative.
- Rungs 2–5: not covered. RFB (in-repo) can't reach secure input; stdlib/platform don't;
  the platform feature that *does* (DriverKit virtual HID) is wrapped by the external
  `vhid` tool installed **on the target**, not something we build.
- Rung 6/7: this is a **thin SSH adapter** that shells to `vhid` on the target — one
  cohesive module, not a deep new abstraction. Proceeded to a short Design-It-Twice.

## What We're Building
An input backend that, for destinations that opt in, routes keyboard/mouse actions through
the target-side `vhid` CLI over SSH (real-hardware DriverKit HID) instead of RFB, so input
reaches Secure-Event-Input dialogs. Screen capture stays on RFB (vhid is input-only). The
choice is per-destination; anything not opted in, or where `vhid doctor` isn't `ready`,
uses the existing RFB path unchanged.

## Design Considered: A — "backend flag threaded through each handler"
Each of the 8 handlers, after resolving the destination, branches: if the destination's
`input == "vhid"`, build the `vhid` command inline and run it over SSH; else do the current
RFB work. Key/mouse mapping helpers live as module functions in `action_handlers.py`.
- **Depth score: 4/10** — interface simplicity 1, power 1, info-hiding 0 (every handler
  learns both backends), error absorption 1, generality 1. Shallow; leaks the RFB-vs-HID
  decision into all 8 handlers (change amplification, P7/P8 violations).

## Design Considered: B — "vhid_backend module hiding target-side vhid over SSH"
A new `src/vhid_backend.py` hides *everything* about driving vhid over SSH: SSH transport,
coordinate pass-through (framebuffer points == vhid absolute screen points), keysym/name →
vhid-name mapping, readiness (`vhid doctor`), and command construction. It exposes a few
verbs (`type_text`, `press`, `click`, `move`, `scroll`, `drag`, `is_ready`). Handlers ask
`destinations` for the input backend of the destination; if `vhid`, they call these verbs;
else the existing RFB path. Screen capture is untouched.
- **Depth score: 8/10** — interface simplicity 2 (handlers call one verb), power 2 (SSH +
  mapping + quoting absorbed), info-hiding 2 (handlers never see SSH/vhid), error
  absorption 1 (readiness + SSH failure → typed result, RFB fallback decided by caller),
  generality 1 (reusable for any vhid-over-SSH target).

## Chosen Design: B — `vhid_backend` module
### Why
It pulls all HID/SSH complexity **downward** into one deep module (P4/P8) and keeps the 8
handlers almost unchanged: they make a single routing decision and call one verb. The
RFB-vs-HID decision is hidden from the handlers' bodies, so adding/altering HID behavior
touches one file, not eight (no change amplification). Coordinates need no new concept —
the handlers already scale source→framebuffer, and framebuffer points equal vhid's
absolute screen points, so the same scaled integers pass straight through.

### Tradeoffs Accepted
| Dimension | Chosen (B) | Rejected (A) |
|---|---|---|
| Caller simplicity | one verb per action | inline branch per handler |
| Implementation complexity | +1 module | spread across 8 handlers |
| Extensibility | new verbs in one place | edit every handler |
| Error handling | centralized readiness/SSH | duplicated per handler |
| Testability | mock one module | mock SSH in 8 tests |

### Red Flags Addressed
- **Information leakage / change amplification** (A spread the backend choice across 8
  handlers) — B confines it to `vhid_backend` + a one-line routing check.
- **Pass-through methods** — avoided: `vhid_backend` verbs add mapping/quoting/SSH, not a
  rename of RFB calls.
- **Config proliferation** — destination gains only `input` (+ reuses an `ssh` endpoint);
  no per-call flags.

### Remaining Risks
- `vhid press` is atomic combos, not down/up — the MCP's rare down-only/up-only key use
  (chords held across calls) isn't expressible; mapped to whole press/combo. `ponytail:`
  note in code. Acceptable: send_keys only ever does full presses.
- Key-name coverage: map the keys we actually emit; unknown keys fall back to RFB with a
  clear message rather than a wrong keystroke.
- First-run `vhid doctor != ready` (driver not approved) → `is_ready` false → RFB fallback.

## Interface Comments (comments-first)
```
# vhid_backend.py — drive a destination's target-side `vhid` (DriverKit virtual HID,
# real-hardware input) over SSH, so keystrokes/clicks reach Secure-Event-Input dialogs
# (lock screen, authd sheets) that RFB/screensharingd synthetic events cannot. Input only;
# screen capture stays on RFB. Every verb returns (ok: bool, detail: str) and never raises
# for an unreachable target — the caller decides whether to fall back to RFB.

def is_ready(ssh: str) -> bool:
    """True iff `vhid doctor` on the target (reached via the ssh endpoint, e.g.
    'dima@host') reports 'ready'. Caller uses this to decide HID-vs-RFB; a False here
    means the driver extension isn't approved yet."""

def type_text(ssh: str, text: str) -> tuple[bool, str]:
    """Type `text` as real hardware keystrokes wherever focus is (no --into: targets the
    lock screen / a cross-process sheet). Hidden: SSH, shell-safe quoting, layout."""

def press(ssh: str, chord: str) -> tuple[bool, str]:
    """Press one vhid chord already in vhid syntax (e.g. 'leftCommand+a', 'return'). The
    caller maps MCP names→vhid names via map_keys(); this just transports it."""

def click(ssh: str, x: int, y: int, button: str = "left", times: int = 1,
          modifiers: str | None = None) -> tuple[bool, str]:
    """Click at absolute screen point (x, y) — the same framebuffer coordinates the
    handler already computed. Hidden: vhid arg shape (--button/--times/--modifiers)."""

def move(ssh, x, y) -> tuple[bool, str]: ...
def scroll(ssh, x, y, vertical: int) -> tuple[bool, str]: ...
def drag(ssh, x1, y1, x2, y2) -> tuple[bool, str]: ...

def map_keys(mcp_names: list[str]) -> str | None:
    """Translate MCP key/modifier names (cmd, shift, enter, esc, a…) to a vhid chord
    string, or None if any name is unmapped (caller then falls back to RFB)."""
```

## Information Hiding Map
| Module | Hidden Design Decision |
|---|---|
| `vhid_backend` | that input is delivered by SSH-ing to the target and running `vhid`; command/arg shape; key-name translation; readiness probing |
| `destinations` | that a destination may carry an `input` backend + `ssh` endpoint |
| handlers | unchanged RFB mechanics; they only pick a backend and call a verb |

## Temporal Decomposition Watch Points
- Do NOT split `vhid_backend` into "build command" / "run ssh" / "parse result" modules by
  execution order — they share the single secret (how to talk to vhid) and belong together.
- Keep readiness (`is_ready`) as a query the caller composes, not a mandatory pre-step baked
  into every verb (avoids conjoined methods).
