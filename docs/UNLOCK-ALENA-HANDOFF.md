# Handoff — Remotely unlocking Alena's MacBook Pro

**Goal:** make it possible to unlock the login/lock screen of Alena's MacBook Pro
**remotely**, over the `remote-macos-use` MCP server, by typing the login password.

**Target (`alena` destination):**

| | |
|---|---|
| SSH / host | `alenak@Alenas-MacBook-Pro.local` (10.10.10.146) |
| VNC user | `alenak` |
| Login password | `Makeevka54!` |
| Hardware | Intel `x86_64` |
| macOS | 15.7.7 |
| Display | Retina 3360×2100 (backing scale 2.0) |

> ## ✅ COMPLETED — 2026-11-01
>
> This handoff has been fully executed. `vhid-0.4.1.pkg` was built (universal
> x86_64+arm64), notarized, stapled, and installed on **Alena's MacBook Pro**; the
> Karabiner DriverKit extension was approved by a human at the machine, and
> `vhid doctor` now reports **`ready`** (driver extension running, both virtual HID
> devices up). A real-HID keypress was confirmed end-to-end through the MCP
> (`Sent via vhid (real HID)`). The same pkg was also installed and approved on
> **paimei** (`vhid doctor` → `ready`), and paimei's registry entry was switched to
> `input: vhid`. The sections below are retained as the record of why/how.

**Status after this session:** the pure-software RFB bug is **fixed** and the `alena`
destination is **switched to the `vhid` backend** (with automatic RFB fallback). The ONE
thing left was a physical, one-time step on the MacBook Pro itself (now **done** — see the
COMPLETED banner above).

---

## TL;DR

1. ✅ **Done:** fixed the RFB double-shift bug that mis-typed `!` (and every other
   shifted symbol). See [Path A](#path-a--the-rfb-double-shift-fix-done).
2. ✅ **Done:** `alena` now routes keyboard/mouse through **vhid** (real hardware HID over
   SSH), which reaches the lock screen that synthetic RFB input cannot. Falls back to RFB
   automatically if vhid is unavailable. See [Path B](#path-b--vhid-the-deterministic-path).
3. ✅ **Done (2026-11-01):** built + notarized `vhid-0.4.1.pkg`, installed it on the MBP, and a
   human approved the DriverKit system extension in System Settings (the one step that cannot
   be driven over VNC — a Secure-Event-Input GUI action). `vhid doctor` → `ready`.
4. ✅ **Verified:** real-HID input flows through the MCP (`Sent via vhid (real HID)`); unlock is
   now the two MCP calls in [The unlock procedure](#the-unlock-procedure).
5. ✅ **Also done:** same pkg installed + extension approved on **paimei**, and `paimei` switched
   to `input: vhid` in the registry.

---

## Why remote unlock is hard (the root problem)

macOS **Secure Event Input** silently drops the *synthetic* keyboard/mouse events that
`screensharingd` injects for VNC/RFB. So over plain screen sharing you cannot type into or
click the **lock screen**, the **login window**, or authd "… wants to make changes" sheets.
The password field may *look* right (correct dot count) yet reject the password, because the
keystrokes either never reach the secure field or arrive corrupted.

There are two distinct problems, and this handoff addresses both:

- **A software encoding bug** (now fixed) that corrupted the `!` even when RFB input *did*
  reach a field.
- **The Secure-Event-Input wall** itself, which no amount of correct RFB encoding reliably
  defeats. The deterministic fix is **vhid** — a real hardware-HID device whose events are
  not synthetic.

---

## Path A — the RFB double-shift fix (DONE)

### The bug

`src/vnc_client.py`, `send_text()`. For a shifted symbol the old code did:

```python
key = ord(char)                                   # for '!', ord('!') == 0x21
need_shift = char.isupper() or char in '~!@#$%^&*()_+{}|:"<>?'
# ... then press Shift + key
```

`ord('!')` is `0x21`, which **is already the X11 keysym `exclam`** — the *shifted* glyph.
Pressing **Shift + an already-shifted keysym** is the classic RFB "double-shift": the server
applies shift to a symbol that is itself the shifted form and mis-resolves it (commonly to
the base key `1`, or a wrong glyph). That is why `Makeevka54!` was rejected — 11 characters
went in, but the last one was wrong.

### The fix (applied this session)

For a shifted symbol, send **Shift + the BASE key's keysym** on a US layout — e.g. `!` →
Shift + `0x31` (`1`), `:` → Shift + `0x3b` (`;`), `"` → Shift + `0x27` (`'`). A new
module-level map `_US_SHIFTED_SYMBOL_BASE` in `src/vnc_client.py` holds all 21 shifted
symbols (exactly the set the old code flagged) → their base keysyms. Uppercase **letters**
are deliberately unchanged (Shift + `0x41` `A` is correct; redundant shift on a letter keysym
is idempotent). Space / Enter / Tab / Backspace / lowercase are untouched.

- Code: `src/vnc_client.py` — `_US_SHIFTED_SYMBOL_BASE` map + the `send_text()` symbol branch.
- Tests: `tests/test_vnc_client.py` → `class TestSendText` locks the exact on-the-wire byte
  sequence for `!` (Shift + `0x31`, and asserts `0x21` never appears), `:` , an uppercase
  letter (unchanged), a lowercase letter (no shift), and the full `Makeevka54!` password.

### Honest caveat (why Path A is NOT sufficient on its own)

Fixing the encoding is real and improves *all* symbol typing, **but it is not proven to
unlock the login window.** A prior attempt that typed `Makeevka54` and then sent
`key_combination="shift+1"` — the *correct* encoding for `!` — plus Enter was **also
rejected**. That strongly suggests the login window does not reliably accept synthetic RFB
input *regardless of correct keysyms* — which is exactly the Secure-Event-Input wall that
vhid exists to defeat. **Treat Path B (vhid) as the deterministic unlock path; Path A is the
correct-and-necessary software fix underneath it and the RFB fallback.**

---

## Path B — vhid (the deterministic path)

`vhid` (our fork of `promptctl/vhid`, re-signed `io.celestialtech.vhid`, Developer ID Rodion
Nazarov `LGAQBC2VM2`, pinned `v0.4.1` at `vendor/vhid`) is a DriverKit **virtual-HID** stack
that presents a **real hardware** keyboard + mouse. Its events are *not* synthetic, so they
reach the lock screen / login window / authd sheets that RFB cannot.

- `src/vhid_backend.py` → `type_text()` runs `ssh <endpoint> /usr/local/bin/vhid type -- <text>`
  on the target — real keystrokes, no keysym/shift ambiguity at all (no US-layout map needed).
- `src/action_handlers.py` → `handle_remote_macos_send_keys()` routes through vhid when the
  destination resolves to the vhid backend, and **falls back to RFB** on any vhid failure
  (`_send_keys_via_vhid` returns `handled=False` → the RFB `VNCClient` path runs).
- Screen capture always stays on RFB regardless.

### Config change (applied this session)

`~/.config/remote-macos/destinations.json` (mode `600`), `alena` entry gained:

```json
"input": "vhid"
```

(`"ssh": "alenak@Alenas-MacBook-Pro.local"` was already present.) `input_backend("alena")`
now returns `("vhid", "alenak@Alenas-MacBook-Pro.local")`, confirmed live. The registry is
read **fresh per call**, so this took effect immediately — no MCP restart needed.

**This is harmless before vhid is installed:** with vhid absent, the SSH command
`/usr/local/bin/vhid …` returns "command not found" (non-zero exit) → `_send_keys_via_vhid`
returns `handled=False` → the handler falls back to the RFB path and everything behaves as
it did before. Verified in code, and the RFB path now also carries the Path-A fix.

### The gap (what is NOT done, and why)

vhid is **not installed** on the MacBook Pro. Verified this session over SSH:
`/usr/local/bin/vhid` → *no such file or directory*. No pqrs/Karabiner VirtualHID extension
is present either. To close the gap:

1. **Build** the signed+notarized `vhid-0.4.1.pkg` (all tooling is present on `asgard`, see
   below). No notarized artifact was found on this machine.
2. **Install** the pkg on the MBP (`sudo installer`).
3. **Approve** the DriverKit system extension in System Settings — **the one physical step.**

---

## The one remaining step

**A human must, once, at the MacBook Pro:** approve the vhid **Driver Extension**
(System Settings → General → **Login Items & Extensions** → **Driver Extensions** → enable
`io.celestialtech.vhid` / the pqrs VirtualHIDDevice, and answer the "Allow" prompt).

Why it cannot be automated:
- Apple requires a **human** to authorize loading a kernel-level driver. The approval UI is
  itself a Secure-Event-Input surface — it **cannot be driven over VNC**.
- The MBP is currently **locked**, so there is no session to drive anyway.
- `sudo` on the MBP **requires a password** (verified: `sudo -n` fails), so the `installer`
  step also needs either an interactive login or the person at the machine. Since a human is
  needed for the dext approval regardless, have them run the install in the same sitting.

Once approved, it is **permanent** — reinstalling the same driver later needs no re-approval,
and remote unlock works from then on.

---

## Exact deploy + approve + verify steps

All build tooling is confirmed present on `asgard` (this machine): both Developer ID
Application + Installer identities for `LGAQBC2VM2`, `vendor/vhid/scripts/make-pkg`, the
`macos-codesign` skill's `notarize.sh`, DriverKit SDK 25.2, Swift 6.2.4.

### 1. Build the signed + notarized pkg (on `asgard`, no physical access needed)

```bash
cd /Users/Pasha/PycharmProjects/mac_studio/mcp-remote-macos-use
git submodule update --init --recursive           # if vendor/vhid is not populated
cd vendor/vhid
scripts/make-pkg /tmp/vhid-out                    # -> /tmp/vhid-out/vhid-0.4.1.pkg (signed)
~/.claude/skills/macos-codesign/notarize.sh --target /tmp/vhid-out/vhid-0.4.1.pkg
```

`make-pkg` asserts every binary is Developer-ID / `LGAQBC2VM2`, hardened-runtime, secure
timestamp, and the pkg is Developer-ID-Installer signed. The DriverKit `.dext` inside is
still pqrs-signed (Fumihiko Takayama `G43BCU2T37`) until Apple grants us the distribution
DriverKit entitlement — that does not block this install. Full build rationale: `docs/VHID.md`.

### 2. Copy + install on the MacBook Pro (needs the person / interactive sudo)

Passwordless SSH to the MBP **already works** (verified this session), but `installer` needs
root and sudo there needs a password, so run these at the machine or over `ssh -t`:

```bash
scp /tmp/vhid-out/vhid-0.4.1.pkg alenak@Alenas-MacBook-Pro.local:/tmp/
ssh -t alenak@Alenas-MacBook-Pro.local 'sudo installer -pkg /tmp/vhid-0.4.1.pkg -target /'
# (If a previous vhid install exists, uninstall it first, KEEPING the approved dext:
#  ssh -t alenak@Alenas-MacBook-Pro.local 'sudo /usr/local/libexec/vhid-uninstall' )
```

### 3. Approve the Driver Extension — THE physical step

At the MacBook Pro: System Settings → General → Login Items & Extensions → Driver Extensions
→ enable the vhid driver and answer the Allow prompt. (If the Mac is locked, unlock it once
by hand for this.)

### 4. Verify vhid is live (from `asgard`, over SSH)

```bash
ssh alenak@Alenas-MacBook-Pro.local 'codesign -dvv /usr/local/libexec/vhidd 2>&1 | grep Authority'  # -> Rodion Nazarov
ssh alenak@Alenas-MacBook-Pro.local '/usr/local/bin/vhid doctor'                                     # -> ready
```

`vhid doctor` prints exactly `ready` when good. Anything else (`not ready`, `withdrawing`)
means the dext isn't approved/loaded yet — the MCP will keep falling back to RFB until it
reads `ready`.

---

## The unlock procedure

Once `vhid doctor` → `ready`, unlock is driven entirely from `asgard` through the MCP.

### Via the MCP tools (the normal path)

1. Screenshot to confirm the lock screen and that the password field has focus:
   `remote_macos_get_screen(destination="alena")`
   - If the field is not focused, click it: `remote_macos_mouse_click(destination="alena", x=…, y=…, source_width=…, source_height=…)` — see the Retina note below.
2. Type the password: `remote_macos_send_keys(destination="alena", text="Makeevka54!")`
3. Submit: `remote_macos_send_keys(destination="alena", special_key="enter")`
4. Screenshot again to confirm the desktop appeared:
   `remote_macos_get_screen(destination="alena")`

With `alena` on the vhid backend, steps 2–3 go out as **real HID** keystrokes
(`vhid type -- Makeevka54!` then `vhid press return`). No shift/keysym ambiguity exists on
this path — vhid types the literal string. (The `--` guards text that starts with `-`.)

### Direct SSH smoke test (for debugging, bypassing the MCP)

```bash
ssh alenak@Alenas-MacBook-Pro.local '/usr/local/bin/vhid type -- "Makeevka54!"'
ssh alenak@Alenas-MacBook-Pro.local '/usr/local/bin/vhid press return'
```

> ⚠️ **Do not brute-force the VNC lock screen.** Only a few failed password attempts are
> safe before macOS imposes a lockout/delay. Verify with ONE careful attempt; if it fails,
> screenshot and diagnose — do not retry blindly.

---

## Retina coordinate note

The MBP is Retina (3360×2100, backing scale **2.0**). The handlers scale request coordinates
to the RFB **framebuffer (pixels)**; vhid's verbs take **logical screen points**
(pixels = points × 2.0). `src/vhid_backend.py` converts framebuffer-pixels → points itself
(`_display_scale` / `_to_points`), probing the main display's scale once via a read-only
CoreGraphics `swift -` snippet over SSH and caching it. `/usr/bin/swift` is present on the MBP
(verified), so the probe will succeed and clicks will land correctly. If the probe ever fails
it falls back to scale `(1.0, 1.0)`, which on a 2× display makes a click miss → the handler
falls back to RFB rather than click confidently in the wrong place.

**For the unlock itself this usually doesn't matter:** the lock screen password field is
normally already focused, so steps 2–3 (type + Enter) need no click. A click is only needed
if the field must be focused first.

---

## Ground truth verified this session

- `src/vnc_client.py` `send_text` double-shift bug — **confirmed** (`ord('!')==0x21`,
  shifted-symbol set pressed Shift). **Fixed** + tests added.
- `alena` registry entry had `ssh` but not `input:vhid` — **confirmed**; `input:vhid`
  **added** (mode 600 preserved, JSON valid, secrets untouched). `dima` is the working
  template (`input:vhid` + `prefer_hid` + `ssh`).
- RFB fallback when vhid fails — **confirmed in code** (`_send_keys_via_vhid` → `handled=False`
  → RFB path; mouse handlers use `_ok or vnc.send_mouse_click(...)`).
- MBP over SSH (passwordless `BatchMode=yes`) — **works** (returned `OK`, macOS `15.7.7`,
  `x86_64`).
- `/usr/local/bin/vhid` on MBP — **absent** (not installed; dext not present).
- `/usr/bin/swift` on MBP — **present** (Retina scale probe will work).
- `sudo` on MBP — **needs a password** (install must be interactive / at the machine).
- Build tooling on `asgard` — Developer ID Application + Installer (`LGAQBC2VM2`), `make-pkg`,
  `notarize.sh`, DriverKit SDK 25.2, Swift 6.2.4 — **all present**; the pkg can be built here.
- No pre-built `vhid-0.4.1.pkg` artifact found on `asgard`; it must be built.
- Full test suite: **49 passed** (44 prior + 5 new; 2 pre-existing stale vhid tests also
  corrected to match already-committed behavior).
