# Security Policy

`bambu-local-cli` controls 3D printers over your local network and downloads
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

- affected version (`bambu-cli --version`) and platform,
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
  and FTPS. Pin it with `cert_fingerprint` (SHA-256), which `bambu-cli setup`
  / `doctor` can capture for you. When a fingerprint is set and does not match,
  the connection is refused on MQTT, FTPS (control + data channel), and the
  direct camera path. `insecure_tls: true` disables verification entirely and
  exists only as a last resort — it is never the default and the CLI warns when
  it is used.
  - **Camera (port 6000):** fails closed if neither pin nor `insecure_tls` is set.
  - **MQTT / FTPS without a pin:** use system CA verification (`CERT_REQUIRED`),
    which fails for typical Bambu self-signed certs (effective fail-closed). Prefer
    an explicit pin for clear errors and uniform policy.
- **Access codes are secrets.** Store the printer access code in a separate file
  via `access_code_file` (the recommended path) rather than inline in
  `config.json`. Inline `access_code` still works for legacy configs but is
  deprecated; migrate with `bambu-cli setup --migrate-access-code`. `config show`
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
- **Printing and other destructive actions require explicit intent.** Physical
  print start, job print step, stop, delete, and raw gcode will not proceed
  without `--confirm`. Agents must not invent confirm flags without user approval.

## Known limitations

Documented residuals from the threat model and the 2026-07 security audit.
Tracked for hardening; not all are “bugs” in the sense of broken claims.

| Topic | Detail | Status |
|-------|--------|--------|
| **Camera Docker port bind** | Default `camera_port` is now `127.0.0.1:1985:1984`, so the streamer publishes the (unauthenticated) camera feed on **loopback only**. Set `camera_port` to `0.0.0.0:1985:1984` to deliberately expose it on the LAN. Host-qualified specs now parse correctly, `camera_port` is validated, and the CLI warns if a *pre-existing* container is still bound to a non-loopback interface (recreate with `docker rm -f bambu_camera`). | Fixed |
| **Camera pin fallback** | A pinned-fingerprint **mismatch**, and now also any `ssl.SSLError` from the direct grab — handshake or post-handshake (e.g. from an active MITM interfering with the port-6000 connection) — hard-abort the snapshot when a pin is configured instead of silently falling back to the Docker streamer (which would ignore the pin). Two unverified paths remain: the *no-pin-configured* case (no `cert_fingerprint`, no `insecure_tls`), which still falls through to the streamer since there is no configured control to downgrade; and, even with a pin, non-TLS network failures on port 6000 (connection refused/reset/timeout — which an on-path attacker can also induce), which must remain a fallback because X1-series printers legitimately refuse port 6000. A planned `camera_direct_only` config option would close this by disabling the streamer fallback entirely. | Fixed |
| **HTTP downloads** | `http://` and `https://` are both accepted. SSRF controls apply; **content integrity** over cleartext HTTP does not (a network attacker can substitute a model). Prefer HTTPS sources. | Residual |
| **pause / resume** | Do not require `--confirm` (unlike stop/print/delete/gcode). | Residual / product choice |
| **Windows secret ACLs** | POSIX `0600` enforcement does not apply on Windows; protect the config directory with NTFS ACLs on shared machines. | Platform residual |
| **Reverse-engineered protocols** | MQTT/FTPS behavior is best-effort; firmware updates can break compatibility. | Out of scope |
| **Access code = full LAN control** | Protect the config directory, especially on agent-operated hosts. | Residual |
| **Model content** | The tool validates packaging/paths/sizes, not whether a model is safe to print. | Residual |
| **TOFU pin capture** | First-time fingerprint probe intentionally disables cert verification to *read* the pin. A MITM during setup can poison the pin if the LAN is already hostile. | Acknowledged |
| **Agent auto-`--confirm`** | Process/policy issue; code cannot stop intentional confirmation. | Out of process scope |
| **Third-party tools** | OrcaSlicer, gmsh, and the optional camera Docker image are outside this package’s SBOM boundary. | Out of scope |

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
