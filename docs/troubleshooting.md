# Troubleshooting

Symptom-first. Find the error message you actually saw — the headings quote the
strings `plate` prints. If you don't see yours, run `plate preflight` and
`plate doctor` first; between them they cover config, OrcaSlicer, MQTT, and
FTPS.

Still stuck? [Open a bug report](https://github.com/DLANSAMA/platecli/issues/new?template=bug_report.yml)
or [ask in Discussions](https://github.com/DLANSAMA/platecli/discussions). Paste
the `plate doctor` output — but check first that no access code is visible.

**Contents**

- [First: the two diagnostic commands](#first-the-two-diagnostic-commands)
- [MQTT connection failed (doctor stage 2 of 3)](#mqtt-connection-failed-doctor-stage-2-of-3)
- ["Connection failed: rc=5" (or any other rc)](#connection-failed-rc5-or-any-other-rc)
- [LAN mode is off, or the access code rotated](#lan-mode-is-off-or-the-access-code-rotated)
- [Certificate fingerprint mismatch](#certificate-fingerprint-mismatch)
- [FTPS connection failed, or uploads hang at 0%](#ftps-connection-failed-or-uploads-hang-at-0)
- [The printer is on the network but nothing reaches it (VLAN / AP isolation / guest Wi-Fi)](#the-printer-is-on-the-network-but-nothing-reaches-it-vlan--ap-isolation--guest-wi-fi)
- [OrcaSlicer or its BBL profiles were not found](#orcaslicer-or-its-bbl-profiles-were-not-found)
- [Slicing fails on a headless Linux box (xvfb-run missing)](#slicing-fails-on-a-headless-linux-box-xvfb-run-missing)
- [gmsh not found, so STEP/STP files cannot be converted](#gmsh-not-found-so-stepstp-files-cannot-be-converted)
- [Setup finds no printers (mDNS discovery)](#setup-finds-no-printers-mdns-discovery)
- [zeroconf is not installed, so auto-discovery is disabled](#zeroconf-is-not-installed-so-auto-discovery-is-disabled)
- [Docker not found in PATH when taking a snapshot](#docker-not-found-in-path-when-taking-a-snapshot)
- [The camera TLS certificate does not match the pinned fingerprint](#the-camera-tls-certificate-does-not-match-the-pinned-fingerprint)
- [The file was not found on the printer](#the-file-was-not-found-on-the-printer)
- [Timed out waiting for the printer to acknowledge print start](#timed-out-waiting-for-the-printer-to-acknowledge-print-start)
- [Print failed with error code 83935248](#print-failed-with-error-code-83935248)
- [Downloads: DNS resolution failed, or no safe/reachable IP addresses found](#downloads-dns-resolution-failed-or-no-safereachable-ip-addresses-found)
- [Access code problems: missing file, or an inline code in config.json](#access-code-problems-missing-file-or-an-inline-code-in-configjson)
- ["Atomic replace is unavailable ... writing in place instead"](#atomic-replace-is-unavailable--writing-in-place-instead)
- [Config not found — and where the config file actually lives](#config-not-found--and-where-the-config-file-actually-lives)
- [Missing dependency: paho-mqtt](#missing-dependency-paho-mqtt)
- [Nothing happens when I run a print command](#nothing-happens-when-i-run-a-print-command)

## First: the two diagnostic commands

```bash
plate preflight   # local only: config, OrcaSlicer, profiles, gmsh, xvfb-run, docker, zeroconf
plate doctor      # talks to the printer: config -> MQTT -> FTPS, and prints the TLS fingerprint
plate config show # the effective config, with secrets redacted
plate config validate --strict
```

`preflight` never touches the network, so it works before the printer is set up.
`doctor` runs three numbered stages and tells you which one failed:
`[1/3] Checking config`, `[2/3] Verifying MQTT connectivity`,
`[3/3] Verifying FTPS connectivity`.

Both accept `--json` if you are driving `plate` from a script or an agent.

## MQTT connection failed (doctor stage 2 of 3)

`plate doctor` stage `[2/3]` reports that it could not reach the printer's MQTT
broker and suggests checking that the printer is on and the access code is
correct. In rough order of likelihood:

1. **LAN mode is off.** See [LAN mode is off, or the access code rotated](#lan-mode-is-off-or-the-access-code-rotated).
2. **Wrong or stale access code.** The LAN access code is *not* your Bambu
   account password, and it changes when you toggle LAN mode or factory-reset
   the printer. Re-read it on the printer touchscreen and update it:
   ```bash
   export PLATE_CODE='12345678'
   plate setup --access-code-env PLATE_CODE --access-code-file ~/.config/bambu/access_code
   ```
   (Put the code in `PLATE_CODE` first so it never lands in shell history.)
3. **Wrong IP.** DHCP moved the printer. Give it a static lease, then re-run
   `plate setup --printer-ip <new-ip>`.
4. **Port 8883 blocked** between you and the printer — see
   [VLAN / AP isolation](#the-printer-is-on-the-network-but-nothing-reaches-it-vlan--ap-isolation--guest-wi-fi).

Quick manual check that the port is even open:

```bash
# Linux/macOS
nc -vz <printer-ip> 8883
# Windows PowerShell
Test-NetConnection <printer-ip> -Port 8883
```

## "Connection failed: rc=5" (or any other rc)

This is the raw MQTT CONNACK return code from the printer's broker, surfaced
verbatim. The one you will almost always see is:

| rc | Meaning | What to do |
|----|---------|-----------|
| 4 | Bad username or password | The access code is wrong. Re-read it from the touchscreen. |
| 5 | Not authorised | Access code rotated, or LAN mode was re-enabled (which regenerates it). |
| 3 | Server unavailable | Printer is booting, or mid-firmware-update. Wait and retry. |

The MQTT username is always `bblp`; the password is the LAN access code. If you
changed `username` in `config.json`, change it back.

## LAN mode is off, or the access code rotated

`plate` is LAN-only by design — it never talks to Bambu's cloud. The printer
must have LAN mode (sometimes "LAN Only Mode" / "LAN Mode") enabled in its
network settings, and that screen is where the IP, serial, and access code all
come from.

After *any* of the following, the access code changes and you must re-run setup:

- toggling LAN mode off and on
- a factory reset
- some firmware updates
- binding/unbinding the printer to a different account

```bash
plate setup            # interactive; re-reads everything
plate doctor           # confirm end to end
```

## Certificate fingerprint mismatch

The printer presents a self-signed TLS certificate. `plate` pins its SHA-256 in
the `cert_fingerprint` config key and refuses to connect if it changes, reporting
the expected and actual fingerprints. This is working as intended, and it is the
control that makes LAN traffic trustworthy.

**A firmware update regenerates the certificate**, so this error after an update
is expected and benign. Re-pin it:

```bash
plate doctor
```

`doctor` prints the printer's certificate SHA-256 and warns when it does not
match the `cert_fingerprint` in your config. Copy the printed value into
`cert_fingerprint` in `config.json`, or re-run
`plate setup --cert-fingerprint <hex>`. Both the bare-hex and colon-separated
forms are accepted — the value is normalised (lowercased, colons and spaces
stripped) before comparison.

**If you did NOT just update firmware**, do not blindly re-pin — a mismatch is
also what an on-path attacker looks like. Confirm you are on the network you
think you are on first.

`insecure_tls: true` disables verification entirely. It is a last resort, the
CLI warns whenever it is set, and it should never be your permanent answer to
this error.

## FTPS connection failed, or uploads hang at 0%

Reported by `plate doctor` stage `[3/3]`, or during `plate upload` /
`plate job`.

`plate` uses **implicit FTPS on port 990** (TLS from the first byte), logs in as
`bblp` with the access code, then switches to a protected data channel.
Python's FTP client uses **passive mode**, so the printer picks a high,
ephemeral port for the data connection and your side dials out to it.

This breaks in three common ways:

1. **Port 990 blocked.** `nc -vz <printer-ip> 990` — if the control connection
   itself fails, it's a firewall or VLAN problem, not FTPS.
2. **Passive data ports blocked.** The control connection succeeds (`doctor`
   stage 2 is green, the login works) but a transfer or directory listing hangs
   and eventually times out. A firewall that only allows 990 is not enough —
   the ephemeral passive range must be reachable too. Since the data channel is
   TLS-encrypted, an FTP connection-tracking helper in your router/firewall
   *cannot* see the `PASV` response and open the port for you. Allow outbound
   connections from your machine to the printer's high ports, or move both onto
   the same flat segment.
3. **A local security suite / corporate VPN** intercepting FTPS. Test with the
   VPN off.

A pinned-certificate mismatch also surfaces here, wrapped in the FTPS failure
message — see
[Certificate fingerprint mismatch](#certificate-fingerprint-mismatch).

## The printer is on the network but nothing reaches it (VLAN / AP isolation / guest Wi-Fi)

Symptoms: `plate setup` finds nothing, MQTT times out, and even ping fails —
while the printer's own screen happily says it's connected.

- **AP / client isolation** (also "guest mode", "wireless isolation", "AP
  isolation") on your access point blocks client-to-client traffic outright.
  Move the printer and your computer onto the same non-guest SSID, or disable
  isolation for that SSID.
- **Separate IoT VLAN.** Very common, and the correct security posture — but you
  then need firewall rules permitting your workstation to reach the printer on
  **TCP 8883 (MQTT), 990 (FTPS) + passive data ports, and 6000 (camera)**, plus
  mDNS reflection/repeating across the VLANs if you want `plate setup`
  auto-discovery to work.
- **Mesh networks / band steering** sometimes put the printer on a different
  backhaul segment. Test from a machine on the same band first.

Fastest way to bisect: temporarily put your computer on the same SSID/VLAN as
the printer. If it works there, it's a network policy issue and not `plate`.

## OrcaSlicer or its BBL profiles were not found

From `plate preflight` (and from slicing). `plate` shells out to OrcaSlicer and
needs two paths: the executable, and the bundled `resources/profiles/BBL`
directory. `preflight` emits separate `orca-slicer` and `profiles-dir` checks,
so it tells you which of the two is wrong.

When it can find a working install elsewhere, `preflight` appends a hint naming
the path it detected and telling you to set `orca_slicer` to it in
`config.json`. Take that path and run:

```bash
plate setup --orca-slicer /path/to/orca-slicer --profiles-dir /path/to/resources/profiles/BBL
```

On Linux/macOS the binary must also be executable; if it is not, `preflight`
says so and suggests `chmod +x`.

### If OrcaSlicer is not installed at all

When there is no OrcaSlicer anywhere on the machine there is nothing to point
the config at, so `preflight` and `slice` instead print the one-liner for your
platform. The fastest way to get it:

| Platform | Command |
|----------|---------|
| Windows | `winget install --id SoftFever.OrcaSlicer` |
| macOS | `brew install --cask orcaslicer` |
| Linux | `flatpak install -y flathub com.orcaslicer.OrcaSlicer` |

Or download a build from [the releases page](https://github.com/OrcaSlicer/OrcaSlicer/releases).
Then run `plate setup` — it auto-detects the install and writes both paths for
you. Verify with `plate preflight`.

> On Windows the installer lays the binary down as `orca-slicer.exe` (older
> builds used `OrcaSlicer.exe`); auto-detection probes both names, so you
> should not need to set the path by hand.

Full per-OS install instructions, the auto-detected default locations, and the
Flatpak/AppImage caveats live in the user guide:
[OrcaSlicer](https://github.com/DLANSAMA/platecli/blob/main/docs/manual.md#orcaslicer).

## Slicing fails on a headless Linux box (xvfb-run missing)

`preflight` warns that `xvfb-run` was not found and that headless Linux slicing
may fail. OrcaSlicer is a GUI application and wants an X display even in CLI
mode.

```bash
sudo apt install xvfb                  # Debian/Ubuntu
sudo dnf install xorg-x11-server-Xvfb  # Fedora
sudo pacman -S xorg-server-xvfb        # Arch
```

This only applies to Linux; macOS and Windows are unaffected.

## gmsh not found, so STEP/STP files cannot be converted

A warning, not an error — `preflight` says so explicitly, and STL, 3MF, and
G-code all still work. Install `gmsh` only if you want to feed `.step`/`.stp`
CAD files directly to `plate slice`.

```bash
sudo apt install gmsh   # or: brew install gmsh / winget install gmsh
```

## Setup finds no printers (mDNS discovery)

`plate setup` scans the local network by browsing for the printer's mDNS
service. Nothing found usually means:

- **mDNS doesn't cross subnets/VLANs** — see the [VLAN section](#the-printer-is-on-the-network-but-nothing-reaches-it-vlan--ap-isolation--guest-wi-fi).
- **A firewall is blocking UDP 5353** on your machine.
- **On Linux, a second mDNS responder conflicts with `avahi-daemon`.** Either is
  fine on its own; both fighting is not.
- **You're on a VPN** that captures multicast or changes the default route.
- The scan window was too short — `plate setup --scan-timeout 15`.

Discovery is a convenience, never a requirement. Read the IP, serial, and access
code off the printer's screen and skip it entirely:

```bash
export PLATE_CODE='12345678'
plate setup \
  --printer-ip 192.168.1.50 \
  --serial 01P00A000000000 \
  --access-code-env PLATE_CODE \
  --access-code-file ~/.config/bambu/access_code \
  --model P1S --nozzle 0.4
```

## zeroconf is not installed, so auto-discovery is disabled

`zeroconf` is a declared dependency, so a warning that it is missing means the
install is incomplete — usually a partially-built venv or a `--no-deps` install.

```bash
pip install --force-reinstall platecli
# from a source checkout:
uv pip install -e .
```

Setup falls back to manual configuration, which works fine.

## Docker not found in PATH when taking a snapshot

The full message tells you to install Docker Desktop (Windows/macOS) or
`docker-ce` (Linux) and retry. But check whether you actually need Docker at
all:

- **P1 and A1 series:** a direct TLS grab from the printer on **port 6000** —
  **no Docker involved**. The `--json` output reports `"method": "direct"`.
- **X1 series:** the camera is an RTSP stream, which `plate` cannot decode
  itself. It falls back to a small streamer container, so **Docker is required
  on X1 only**.

So if you are on a P1/A1 and you are *seeing* this message, the direct grab
failed first and fell through. Check that port 6000 is reachable
(`nc -vz <printer-ip> 6000`), that LAN mode is on, and re-run with `-v` /
`--verbose` — the direct-path failure is logged at debug level as
"Direct camera grab unavailable ...; trying Docker streamer."

The streamer container is published to `127.0.0.1` only by default
(`camera_port` defaults to `127.0.0.1:1985:1984`). Do not point
`camera_stream_url` at a non-local host — `plate` refuses it deliberately.

## The camera TLS certificate does not match the pinned fingerprint

You may also see a message about a camera TLS error *with a cert pin configured*,
explicitly refusing to fall back to the unverified Docker streamer.

The camera port is pinned with the same `cert_fingerprint` as everything else,
and a mismatch **fails closed** rather than silently falling back to the
unpinned Docker path. After a firmware update, re-pin with `plate doctor` as in
[Certificate fingerprint mismatch](#certificate-fingerprint-mismatch).

If you have no pin at all, `plate` warns that no `cert_fingerprint` is pinned
for the camera connection and tells you to run `plate setup` to pin one. Pin it
— don't set `insecure_tls`.

## The file was not found on the printer

`plate print` only starts a file that already exists in `/model/` on the
printer's storage; when it isn't there, `plate` says so and tells you to upload
it first. Check what's there and upload if needed:

```bash
plate files
plate upload ./my-model.gcode.3mf
plate print my-model.gcode.3mf --confirm
```

Or let `plate job <url|file> --confirm` do download → slice → upload → print in
one step.

## Timed out waiting for the printer to acknowledge print start

The file uploaded and the print command was published, but the printer never
sent back an acknowledgement within the timeout. Check the printer screen — the
print may in fact have started.

Causes: a busy or paused printer, a lid/door prompt waiting for input on the
screen, or a slow network. Raise the window with the `command_timeout` config
key (seconds; the print acknowledgement waits `command_timeout + 5`).

## Print failed with error code 83935248

That specific code means the printer could not find the file on its SD card, and
`plate` follows it with a hint to check the filename with the `files` command.
Run `plate files`, confirm the exact name (they are case-sensitive), and
re-upload if it's missing. If the SD card was removed or is unformatted, the
printer's own screen will say so.

Other print error codes come straight from the printer's firmware — look them up
in Bambu Lab's own error-code documentation. `plate` prints the hex form
alongside the decimal one because Bambu documents them in hex.

## Downloads: DNS resolution failed, or no safe/reachable IP addresses found

`plate download` and `plate job` are SSRF-hardened: they resolve the URL and
refuse any address that is not globally routable (loopback, RFC1918, link-local).
So pointing `plate` at a model on your own NAS or a LAN web server is blocked by
default, with an error saying no safe or reachable IP addresses were found.

That's intentional. Opt in per invocation:

```bash
plate download http://192.168.1.10/model.stl --allow-private-ips
```

There is deliberately **no** `allow_private_ips` config key — it cannot be made
sticky. A DNS-resolution failure for the host is a different problem: that's a
plain name-resolution error, so check the URL and your resolver.

Downloads and ZIP extraction are also capped at 2048 MB; raise it with
`--max-download-mb`.

## Access code problems: missing file, or an inline code in config.json

The access code should live in its own file, referenced by `access_code_file` —
not inline in `config.json`. `plate` reports separately when the referenced file
is missing and when neither `access_code` nor `access_code_file` is present at
all. If `preflight` warns that `config.json` contains an inline `access_code`
and tells you to move it to an `access_code_file`, migrate it in place:

```bash
plate setup --migrate-access-code
```

`preflight` also checks the file's permissions. On Linux/macOS keep it at `0600`
(`chmod 600 ~/.config/bambu/access_code`).

## "Atomic replace is unavailable ... writing in place instead"

A warning, not an error — the write still succeeds and no action is required.

`plate` normally saves `config.json` and the access-code file by writing a temp
file alongside the target and renaming it over the top, so an interrupted write
can never leave a half-written file. Some Windows environments virtualize
`%APPDATA%`: if you run `plate` from inside an MSIX-packaged app or a sandbox,
your config directory is a reparse point into the package's private storage, and
Windows refuses the rename with `ERROR_NOT_SAME_DEVICE` (`WinError 17`) even
though both paths look like the same folder.

When `plate` sees that specific error it writes the file directly instead and
prints this warning. The tradeoff is real but small: that single write is no
longer crash-safe, so if the machine loses power at exactly the wrong moment the
file could be left incomplete. `config.json.bak` still holds the previous config.

If you would rather keep atomic writes, run `plate` from an ordinary terminal
(PowerShell, Windows Terminal, cmd) rather than from inside the packaged app, or
point the config somewhere outside the redirected tree.

Earlier versions failed outright here with
`Could not migrate access code: [WinError 17] ...` and could not save config at all.

## Config not found — and where the config file actually lives

`plate` tells you the path it looked at and to run `setup` first. Resolution
order, in the order `plate` actually checks:

1. On Linux/BSD only: if `XDG_CONFIG_HOME` is set, `$XDG_CONFIG_HOME/bambu/config.json`
   wins outright.
2. Otherwise, if `~/.config/bambu/config.json` already exists it wins — on
   **every** platform, including macOS and Windows. This keeps older installs
   working.
3. Otherwise the platform-native default:

| Platform | Path |
|---|---|
| Linux | `~/.config/bambu/config.json` |
| macOS | `~/Library/Application Support/bambu/config.json` |
| Windows | `%APPDATA%\bambu\config.json` |

So if you're editing the "right" platform path on macOS or Windows and nothing
changes, check for a stray `~/.config/bambu/config.json`. `plate config show`
always prints the path actually in use.

There is no `plate config set`; `plate config` only supports `show` and
`validate`. Change settings by re-running `plate setup` (it accepts every value
as a flag for non-interactive use) or by editing `config.json` directly.

## Missing dependency: paho-mqtt

A broken or partial install. Reinstall:

```bash
pip install --force-reinstall platecli
# from a source checkout:
uv pip install -e .
```

If you installed with `pipx` / `uv tool`, use `pipx reinstall platecli` or
`uv tool install --force platecli`.

## Nothing happens when I run a print command

Working as designed. Anything that physically moves the printer — starting a
print, pausing or resuming one, stopping one, deleting a file, sending raw
G-code — requires an explicit `--confirm`. Without it the command refuses with
exit code `5` and the printer is untouched. Add `--confirm` once you're sure.

If you want to rehearse the whole pipeline with no hardware at all, use
`--sim`:

```bash
plate --sim status
plate --sim job ./model.stl --json --confirm
```
