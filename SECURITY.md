# Security Policy

`platecli` controls 3D printers over your local network and downloads
untrusted model files from the internet. Both are security-sensitive, so this
document explains the threat model, the mitigations already in place, known
limitations, and how to report a vulnerability.

## Reporting a vulnerability

**Please do not open a public issue for security problems.**

Report privately through GitHub's [private vulnerability reporting][gh-report]
on this repository (Security → *Report a vulnerability*). If that is unavailable,
open a normal issue that says only "security report — please open a private
channel" **without any details**, and a maintainer will follow up.

Please include, where possible:

- affected version (`plate --version`) and platform,
- a description of the issue and its impact,
- reproduction steps or a proof of concept,
- any known workaround.

You can expect an initial acknowledgement within a few days. Because this is a
small volunteer project there is no formal SLA, but confirmed issues will be
fixed as a priority and disclosed once a fix is available. Coordinated
disclosure is appreciated.

[gh-report]: https://docs.github.com/en/code-security/security-advisories/guidance-on-reporting-and-writing-information-about-vulnerabilities/privately-reporting-a-security-vulnerability

## Supported versions

The project is pre-1.0 and under active development. Security fixes are made
against `main` and land in the next tagged release; there is no long-term
support branch for older versions. Always run the latest release.

| Version | Supported |
| ------- | --------- |
| latest release / `main` | ✅ |
| older tags | ❌ |

## Security model

The tool is designed to run **fully locally** — there is no cloud account, and
no telemetry or data leaves your machine except (a) LAN traffic to your printer
and (b) explicit model downloads you request. Key properties:

- **Printer TLS.** Bambu printers present a self-signed certificate over MQTTS
  and FTPS. Pin it with `cert_fingerprint` (SHA-256), which `plate setup`
  / `doctor` can capture for you. When a fingerprint is set and does not match,
  the connection is refused on MQTT, FTPS (control + data channel), and the
  direct camera path. All three transports verify through one shared checker
  (`bambu_cli.tlspin.verify_cert_fingerprint`, constant-time compare on normalized
  hex) so there is a single, audited fail-closed code path rather than three
  hand-written copies. `insecure_tls: true` disables verification entirely and
  exists only as a last resort — it is never the default and the CLI warns when
  it is used.
  - **Camera (port 6000):** the *direct* grab refuses to proceed if neither pin nor
    `insecure_tls` is set. Note that `snapshot` then falls back to the Docker streamer,
    which does not honour the pin, so the command as a whole is not fail-closed by
    default — set `camera_direct_only: true` to refuse that fallback (see
    [Known limitations](#known-limitations)).
  - **MQTT / FTPS without a pin:** use system CA verification (`CERT_REQUIRED`),
    which fails for typical Bambu self-signed certs (effective fail-closed). Prefer
    an explicit pin for clear errors and uniform policy.
- **Access codes are secrets.** Store the printer access code in a separate file
  via `access_code_file` (the recommended path) rather than inline in
  `config.json`. Inline `access_code` still works for legacy configs but is
  deprecated; migrate with `plate setup --migrate-access-code`. `config show`
  redacts the access code. On POSIX, config and access-code files are tightened
  toward `0600` on load.
- **Downloads are SSRF-hardened.** URL fetches (including Printables) resolve
  and validate targets before connecting to block requests to private/loopback
  address ranges (unless `--allow-private-ips`, which is CLI-only and never
  sticky config), disable environment proxies, cap redirect hops, and enforce a
  size cap (2048 MB by default, `--max-download-mb`).
- **Archive extraction is path-traversal safe.** ZIPs are extracted without
  allowing entries to escape the destination directory; symlinks are skipped;
  existing files are never overwritten (a numbered sibling such as `model-1.stl`
  is created instead).
- **Printing and other physical/destructive actions require explicit intent.** Physical
  print start, stop, pause, resume, delete, and raw gcode will not proceed without
  `--confirm` (they refuse with exit code `5`). `job` / `send` without `--confirm`
  still runs download → slice → upload and exits `0` with `"status": "uploaded_not_printed"`;
  only the print step is withheld. `--confirm` is a deliberate-action gate against
  accidents, not an authorization boundary: the CLI cannot distinguish a human's flag
  from an agent's. Agents must not invent confirm flags without user approval.
- **In the interactive front-ends the gate is a dialog, not the flag.** `plate go`
  (wizard) and `plate tui` (full-screen UI) never require the user to type
  `--confirm`; the equivalent deliberate action is an explicit confirmation step —
  the wizard's final question and the TUI's confirm dialog — which is the single
  place in each front-end that requests a print. Neither is scriptable: both refuse
  `--json` and a non-TTY stdin with exit `5`, so an agent cannot drive them, and
  choosing *Upload only* leaves the file on the printer unstarted. The property the
  flag protects is preserved (no print without a distinct, deliberate second action);
  only its form differs.

## Known limitations

Documented residuals from the threat model and the 2026-07 security audit.
Tracked for hardening; not all are “bugs” in the sense of broken claims.

| Topic | Detail | Status |
|-------|--------|--------|
| **Camera Docker port bind** | Default `camera_port` is now `127.0.0.1:1985:1984`, so the streamer publishes the (unauthenticated) camera feed on **loopback only**. Set `camera_port` to `0.0.0.0:1985:1984` to deliberately expose it on the LAN. Host-qualified specs now parse correctly, `camera_port` is validated, and the CLI warns if a *pre-existing* container is still bound to a non-loopback interface (recreate with `docker rm -f bambu_camera`). | Fixed |
| **Camera pin fallback** | A pinned-fingerprint **mismatch**, and any `ssl.SSLError` from the direct grab — handshake or post-handshake — hard-abort the snapshot when a pin is configured. The `camera_direct_only` config key (default `false`) closes the remaining fallback routes: when set, any failure of the direct port-6000 grab — including no-pin SSLError, non-TLS connection failures (refused/reset/timeout), or a silent no-frame return — refuses to fall back to the Docker streamer and aborts with `EXIT_NETWORK_ERROR`. **What it does not cover:** (a) `camera_direct_only=true` with `insecure_tls=true` is direct-only but **completely unverified** — direct-only ≠ verified; verification requires a `cert_fingerprint`. (b) It stops platecli from using or starting the streamer, but an **already-running `bambu_camera` container keeps serving the unauthenticated feed** — run `docker rm -f bambu_camera` to stop it. (Re-running `plate setup` used to drop this hand-added key, silently disabling the control; setup now preserves every key it does not manage, and reports which ones it kept.) | Fixed |
| **HTTP downloads** | `http://` and `https://` are both accepted. SSRF controls apply; **content integrity** over cleartext HTTP does not (a network attacker can substitute a model). Prefer HTTPS sources. | Residual |
| **pause / resume** | Required `--confirm` as of 0.3.0, matching stop/print/delete/gcode. | Fixed |
| **Windows secret ACLs** | POSIX `0600` enforcement does not apply on Windows; protect the config directory with NTFS ACLs on shared machines. | Platform residual |
| **Reverse-engineered protocols** | MQTT/FTPS behavior is best-effort; firmware updates can break compatibility. | Out of scope |
| **Access code = full LAN control** | Protect the config directory, especially on agent-operated hosts. | Residual |
| **Model content** | The tool validates packaging/paths/sizes, not whether a model is safe to print. | Residual |
| **TOFU pin capture** | First-time fingerprint probe intentionally disables cert verification to *read* the pin. A MITM during setup can poison the pin if the LAN is already hostile. | Acknowledged |
| **Agent auto-`--confirm`** | Process/policy issue; code cannot stop intentional confirmation. | Out of process scope |
| **Third-party tools** | OrcaSlicer, gmsh, and the optional camera Docker image are outside this package’s SBOM boundary. | Out of scope |
| **zeroconf / pytest on Python 3.9** | The newest `zeroconf` and `pytest` releases that still support Python 3.9 (`0.148.0`, `8.4.2`) carry open moderate advisories, and **every patched release requires Python >= 3.10**, so they cannot be upgraded on the 3.9 resolution branch. On 3.10+ the dependency floor requires patched `zeroconf` (see `pyproject.toml`). Exposure on 3.9 is limited: the advisories are LAN-local DoS / cache-corruption reachable only from a hostile device on the same network, `zeroconf` is imported lazily and used solely for optional mDNS discovery during `plate setup` (which degrades gracefully and can be skipped by passing printer details manually), and `pytest` is a test-only extra that is never installed for end users or shipped in the wheel. Revisit when Python 3.9 support is dropped. | Accepted (3.9 only) |

## Scope

**In scope:** the `bambu_cli` package and its published wheels/sdist, the CLI
surface, download/extraction handling, and the printer transport layer.

**Out of scope:** vulnerabilities in third-party dependencies (report those
upstream), OrcaSlicer itself, the printer firmware, and issues that require an
already-compromised local machine or a malicious printer on your own LAN
(beyond what pin/`insecure_tls` policy can mitigate).

## Related docs

- [AGENTS.md](AGENTS.md) — agent safety checklist  
- [docs/quality-roadmap.md](docs/quality-roadmap.md) — security scoreboard  
- [docs/api.md](docs/api.md) — agent JSON contracts  
