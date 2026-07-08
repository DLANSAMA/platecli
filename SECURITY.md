# Security Policy

`bambu-local-cli` controls 3D printers over your local network and downloads
untrusted model files from the internet. Both are security-sensitive, so this
document explains the threat model, the mitigations already in place, and how to
report a vulnerability.

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
  can capture for you. Certificate verification is **fail-closed**: if a
  fingerprint is pinned and does not match, the connection is refused.
  `insecure_tls: true` disables verification entirely and exists only as a last
  resort — it is never the default and the CLI warns when it is used.
- **Access codes are secrets.** Store the printer access code in a separate file
  via `access_code_file` (the recommended path) rather than inline in
  `config.json`. Inline `access_code` still works for legacy configs but is
  deprecated; migrate with `bambu setup --migrate-access-code`. `config show`
  redacts the access code.
- **Downloads are SSRF-hardened.** URL fetches (including Printables) resolve
  and validate targets before connecting to block requests to private/loopback
  address ranges, and enforce a size cap (2048 MB by default, `--max-download-mb`).
- **Archive extraction is path-traversal safe.** ZIPs are extracted without
  allowing entries to escape the destination directory; existing files are never
  overwritten (a numbered sibling such as `model-1.stl` is created instead).
- **Printing requires explicit intent.** One-shot flows will not start a physical
  print without `--confirm`.

## Known limitations

- The MQTT and FTPS printer protocols are **reverse-engineered**. A printer
  firmware update can change them and break compatibility; treat protocol
  behavior as best-effort, not guaranteed.
- The access code grants full LAN control of the printer. Protect the config
  directory with appropriate filesystem permissions, especially on shared or
  agent-operated machines.
- Sliced/downloaded model files come from untrusted sources. Review what you
  print; the tool validates file handling, not model content.

## Scope

In scope: the `bambu_cli` package and its published wheels/sdist, the CLI
surface, download/extraction handling, and the printer transport layer.

Out of scope: vulnerabilities in third-party dependencies (report those
upstream), OrcaSlicer itself, the printer firmware, and issues that require an
already-compromised local machine or a malicious printer on your own LAN.
