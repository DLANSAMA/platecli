## 2026-08-19 - Bounded member decompression for 3MF zip archives
**Vulnerability:** `read_3mf_estimate` extracted `slice_info.config` from `.3mf` zip packages using unbounded `zf.read()`, risking memory exhaustion / Zip Bomb DoS when reading untrusted models.
**Learning:** Even internal helper methods like `read_3mf_estimate` process user-provided or network-downloaded 3MF files.
**Prevention:** Check `info.file_size` against a safety limit (10MB) and use explicit read bounds (`fh.read(limit)`) before parsing XML/JSON from zip archives.
