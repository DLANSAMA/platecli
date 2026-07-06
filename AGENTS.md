# Bambu CLI
Runs on Linux, macOS, and Windows.

**Script:** `python3 <path>/scripts/bambu.py` (legacy path, but installed system-wide as `bambu-cli`)

Prefer `job`/`send` for agent work. Always ask the user before running any command with `--confirm`.

## Data Handling
ZIP files are opened safely. URL downloads and ZIP extraction have a 2048 MB safety limit via `--max-download-mb`. Conflicting files use a numbered sibling such as `model-1.stl`.

Agent-facing JSON path fields compact paths under the current home directory to `~`. Path-bearing JSON error messages use the same `~` compaction.

## Agent Workflows & Client Architecture
The core interaction with the printer is handled via the `BambuPrinter` class located in `bambu_cli/printer.py`. 
Agents interacting directly with the codebase should instantiate this class via the `get_printer()` factory instead of manipulating globals.
- `BambuPrinter` handles FTPS and MQTT connections.
- Set `insecure_tls = False` and supply the `cert_fingerprint` to ensure MITM protection. All TLS channels (MQTT, FTPS, camera port 6000) fail closed when no fingerprint is pinned and `insecure_tls` is not set.
- The `bambu.py` script acts as a legacy bridge. When implementing new agent capabilities, write modular commands in `bambu_cli/commands.py` leveraging the `BambuPrinter` object.
- Network operations (like MQTT request-response) support `timeout` and `retries` out of the box through `printer.send_command()` and `printer.status()`.

## Agent Usage
Agents may place `--json` before or after the subcommand; `bambu-cli --json --version` emits machine-readable version details. Slicing accepts meshes in the precedence order STL > STEP/STP > OBJ > 3MF > G-code. AMS slot mappings are zero-or-positive integers.

## Packaging
In pyproject.toml:
```toml
[tool.setuptools]
packages = ["bambu_cli", "bambu_cli.protocols"]

[tool.setuptools.package-data]
"bambu_cli" = ["README.md", "AGENTS.md", "requirements.txt"]
```
