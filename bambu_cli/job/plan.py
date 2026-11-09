"""Plan fingerprint: a stable digest of what a ``job``/``send`` is about to do.

A caller reviews a ``--dry-run``, reads its ``plan_fingerprint``, and passes it
back as ``--expect-plan`` on the real run. The real run recomputes the digest
from its own arguments and refuses (exit 5) before any download, slice, upload
or print if it differs. That proves the run is the one that was reviewed; it
does not prove a person reviewed it, because anything that can run ``plate`` can
also run the dry run (see SECURITY.md).

What the digest covers: the printer's serial and model, the source, the content
of a local source file, and every option that changes slicing or the print
start. What it cannot cover: the bytes behind a URL, which are only known after
download. A URL plan is bound to the URL, not to what the URL serves later.
"""

from __future__ import annotations

import hashlib
import json
import os

from bambu_cli.argutils import namespace_get as _namespace_get
from bambu_cli.job.predict import _slice_args_for_job
from bambu_cli.paths import expand_path as _expand_path

PLAN_VERSION = "plate-plan/1"

# Print-start and pipeline options that change what happens (flags such as
# --confirm, --dry-run, --json and the output/work directories are deliberately
# not part of the plan: they do not change what gets printed).
_PRINT_OPTIONS = (
    "use_ams",
    "ams_mapping",
    "timelapse",
    "skip_bed_leveling",
    "skip_flow_cali",
    "plate",
    "upload_only",
    "name",
)

# Slice-argument fields that name a place or a resource limit, not the result.
_NOT_PLAN_SLICE_FIELDS = frozenset({"file", "output", "threads"})


def _local_file_sha256(path: str) -> str | None:
    try:
        if not os.path.isfile(path):
            return None
        digest = hashlib.sha256()
        with open(path, "rb") as handle:
            for chunk in iter(lambda: handle.read(1024 * 1024), b""):
                digest.update(chunk)
        return digest.hexdigest()
    except OSError:
        return None


def plan_fingerprint(ctx, args, source: str) -> str:
    """Return the hex SHA-256 of the canonical plan for this job."""
    settings = ctx.settings
    slice_options = {
        k: v for k, v in vars(_slice_args_for_job(None, args, None)).items() if k not in _NOT_PLAN_SLICE_FIELDS
    }
    plan = {
        "version": PLAN_VERSION,
        "printer": {"serial": settings.serial, "model": settings.printer_model},
        "source": source,
        "source_sha256": _local_file_sha256(_expand_path(source)) if "://" not in source else None,
        "slice": slice_options,
        "print": {name: _namespace_get(args, name) for name in _PRINT_OPTIONS},
    }
    canonical = json.dumps(plan, sort_keys=True, separators=(",", ":"), default=str, ensure_ascii=True)
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()
