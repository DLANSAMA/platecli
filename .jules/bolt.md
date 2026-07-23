## 2026-07-19 - URL parsing bottleneck in JSON serialization
**Learning:** `urllib.parse.urlparse` has significant overhead when called iteratively on regular strings. In `bambu-cli`, the JSON output compaction loops through every string and calls `_redact_url_credentials`, which was doing a full `urlparse`. An initial attempt to fast-path using `if "://" not in url:` was rejected in review because protocol-relative credentials (e.g. `//user:pass@host`) lack `://`.
**Action:** When adding fast-paths for string checks, ensure the criteria exactly matches structural requirements. For URL credentials, an `"@"` is always required.

## 2026-07-20 - Optimizing path expansions and DNS resolution
**Learning:** `os.path.expanduser("~")` and `socket.getaddrinfo` (DNS resolution) can become significant bottlenecks when repeatedly invoked inside inner loops or status monitoring loops respectively. In `bambu-cli`, `_display_path` was re-computing the home directory multiple times on every path string in JSON emission. Additionally, `_resolve_ip` was spinning up a new thread and doing DNS resolution on every single invocation, which happens frequently during operations like FTP uploads and camera streaming that use `_resolve_ip` dynamically.
**Action:** Caching the result of `os.path.expanduser("~")` globally inside the module significantly speeds up path string compacting operations. For network operations, caching successful IP resolutions via a simple dictionary `_RESOLVE_IP_CACHE` avoids repeating thread-creation and DNS lookup delays across the same execution.
## 2024-07-21 - Caching Slicer Profile Key Discovery
**Learning:** The CLI reads and parses all OrcaSlicer `.json` profiles in a directory multiple times during a single `slice` operation to discover all possible override keys. This causes significant, unnecessary file I/O and JSON parsing for directories that are completely static for the duration of the command's execution.
**Action:** When a function iterates over and parses many files in a static configuration directory, add `@lru_cache` if it's called repeatedly within the same execution context.

## 2026-07-22 - [JSON parsing bottleneck in profile auto-discovery]
**Learning:** Checking compatibility during profile discovery repeatedly opens and parses JSON files (`_process_profile_compatible`). Since the files on disk don't change during the lifecycle of the CLI command, doing this in loops over many profiles incurs a severe I/O and JSON parsing overhead (~25x slower without memoization).
**Action:** Use `@lru_cache` to memoize the results of repetitive file reads/JSON parsing during hot paths like profile discovery where the data is static per execution.
