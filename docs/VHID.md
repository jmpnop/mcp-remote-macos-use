# vhid — the virtual-HID input stack (submodule, build, deploy)

`vhid` is how this MCP drives **Secure Event Input** surfaces on a target Mac — the lock
screen, the login window, and authd "modify settings" sheets — which silently drop the
synthetic events `screensharingd` injects for RFB. It presents a **real hardware
keyboard + mouse** via a DriverKit virtual-HID device, so its input reaches those places.
The design rationale is in [`DESIGN.md`](../DESIGN.md); this file is how it is built,
shipped, and kept ours.

## It is a submodule, not a separate project

The source lives at **`vendor/vhid`**, a git submodule pinned to a tag. It is a *part of*
this repo, consumed by it — not an independent deliverable.

- Submodule backing repo: `git@github.com:jmpnop/vhid.git` (our fork).
- Upstream it was forked from: `https://github.com/promptctl/vhid` (wired as the `upstream`
  remote inside the submodule, for pulling fixes).
- Currently pinned at **`v0.4.1`**.

```bash
git submodule update --init --recursive      # after a fresh clone of this repo
git -C vendor/vhid fetch upstream             # later: pull upstream changes into the fork
```

## Why we forked it

The upstream pkg ships binaries signed by third parties, which macOS surfaces as "Software
from *Brandon Fryslie* can run in the background" background-item notifications. Our fork
re-signs the whole vhid layer under **our** Developer ID and rebrands the reverse-DNS
namespace, so nothing we ship is signed by someone else and the OS names *us*:

- Signer: `Developer ID Application/Installer: Rodion Nazarov (LGAQBC2VM2)` (the shared
  `macos-codesign` skill).
- Namespace: `ai.promptctl.vhid` → **`io.celestialtech.vhid`** (one constant in
  `Sources/Installations/Installation.swift` drives every launchd label, Mach service, log
  subsystem and receipt).
- `scripts/make-pkg`: `TEAM=LGAQBC2VM2`, `IDENTIFIER=io.celestialtech.vhid`.
- The unused `eyes` screen-reader tool is dropped from the pkg (the MCP never calls it, and
  it needs a Swift 6.3 compiler); the two-arch `--build-system swiftbuild` path is replaced
  with per-arch single-triple builds + `lipo` (the swiftbuild path can't resolve the
  `VersionStamp` build-tool plugin on Swift 6.2.x). See the commit on the fork for detail.

### Still third-party until Apple grants us an entitlement

The **DriverKit driver (.dext) inside the pkg is still pqrs-signed** (Fumihiko Takayama,
`G43BCU2T37`) — `scripts/virtual-hid-driver` downloads and pins the stock
`Karabiner-DriverKit-VirtualHIDDevice` package. Building our *own* dext needs the
restricted `com.apple.developer.driverkit.*` entitlements, which Apple must grant to team
`LGAQBC2VM2` (the *distribution* variant; manual review, weeks). The **development**
DriverKit entitlements need no approval and work on machines registered to our account, so
our own fleet can run a self-built dext before the distribution grant lands. Until then the
vhid/vhidd layer is ours and the dext is pqrs's. (Reference source for that next step is
cloned under `mac-studio-rebuild/tools/karabiner-driverkit-virtualhiddevice` in the
`mac_studio` repo.)

## Build the installer pkg

Requires: Xcode (Swift 6.2+, DriverKit SDK), our Developer ID Application + Installer
certs in the keychain (`security find-identity -v | grep LGAQBC2VM2`), and network access
(the build downloads the pinned driver pkg). Universal (arm64 + x86_64); runs on either
build host.

```bash
cd vendor/vhid
scripts/make-pkg /path/to/out        # -> /path/to/out/vhid-<VERSION>.pkg, signed
~/.claude/skills/macos-codesign/notarize.sh --target /path/to/out/vhid-<VERSION>.pkg
```

`make-pkg` asserts every binary is Developer-ID/`LGAQBC2VM2`, hardened-runtime, secure
timestamp, and that the pkg is Developer-ID-Installer signed. The signed-release path
requires the tree at a clean `v<VERSION>` tag (that is why the fork is tagged).

## What the pkg does (full install — one installer, no loose scripts)

1. Installs the bundled DriverKit **driver** first; its postinstall asks macOS to
   **activate the system extension**.
2. Installs the CLI `/usr/local/bin/vhid`, the root daemon `/usr/local/libexec/vhidd`, the
   menu-bar item, the record tap app, and its own uninstaller.
3. Installs **and bootstraps** `/Library/LaunchDaemons/io.celestialtech.vhid.vhidd.plist`
   as root — a classic LaunchDaemon, so there is **no `.sh` in Login Items** and no
   approval prompt for the daemon itself.

The **only** manual step is approving the DriverKit extension (System Settings → Login
Items & Extensions → Driver Extensions / the "allow" prompt). Apple requires a human to
authorize loading a kernel-level driver; it cannot be automated. Once approved on a Mac, a
reinstall of the same driver needs no re-approval.

## Deploy / replace on a target

Both the old and new installs use the same binary paths but different service names, so
uninstall the old one first (keep the already-approved driver — **no `--driver`**), then
install ours:

```bash
scp vhid-<VERSION>.pkg <user>@<target>:/tmp/
ssh <user>@<target> 'sudo /usr/local/libexec/vhid-uninstall'      # old install, keeps the dext
ssh <user>@<target> 'sudo installer -pkg /tmp/vhid-<VERSION>.pkg -target /'
# verify:
ssh <user>@<target> 'codesign -dvv /usr/local/libexec/vhidd 2>&1 | grep Authority'  # -> Rodion Nazarov
ssh <user>@<target> '/usr/local/bin/vhid doctor'                  # -> ready
```

## How the MCP consumes it

A destination opts in via `~/.config/remote-macos/destinations.json`:

```json
"dima": { "host": "10.10.10.9", "username": "dima", "input": "vhid", "ssh": "dima@10.10.10.9" }
```

`src/destinations.py` → `input_backend()` returns `("vhid", "<ssh endpoint>")`;
`src/vhid_backend.py` runs `ssh <endpoint> /usr/local/bin/vhid <verb> …` (absolute path —
the non-login ssh PATH has no `/usr/local/bin`). Screen capture always stays on RFB; only
keyboard/mouse route through vhid, and only when `vhid doctor` reports `ready`, else the
handler falls back to RFB.
