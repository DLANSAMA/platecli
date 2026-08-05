"""Raw Printables GraphQL calls — the fragile part, quarantined.

Everything that knows the shape of Printables' undocumented API lives in this
module and nowhere else. It raises the typed errors from ``errors.py`` rather
than returning sentinels, so the distinction between "network down", "they
changed the schema", and "this model has no STL" survives long enough to be
reported. ``adapter.py`` turns those back into a safe return value.

Nothing outside ``bambu_cli.printables`` should import this module.
"""

from __future__ import annotations

import json
import re
import urllib.error
import urllib.request
from urllib.parse import urlparse

from bambu_cli.constants import DEFAULT_NETWORK_TIMEOUT
from bambu_cli.logging_utils import logger
from bambu_cli.netsafety import build_safe_opener, platecli_user_agent, polite_open
from bambu_cli.printables.errors import (
    PrintablesContractChanged,
    PrintablesModelUnavailable,
    PrintablesUnavailable,
)

_API_URL = "https://api.printables.com/graphql/"

# A GraphQL metadata response is a few KB. Cap the read so a hostile or broken
# endpoint cannot stream unbounded data into memory before we even parse it —
# the file download path has size limits, this metadata path had none.
_MAX_RESPONSE_BYTES = 4 * 1024 * 1024

_MODEL_PATH_RE = re.compile(r"/model/(\d+)")


def is_printables_model_url(value):
    """True if *value* is a printables.com model page URL."""
    try:
        parsed = urlparse(value)
    except Exception:
        return False
    host = (parsed.hostname or "").lower()
    return host in ("printables.com", "www.printables.com") and bool(_MODEL_PATH_RE.search(parsed.path))


def model_id_from_url(value):
    """Extract the numeric model id from a Printables model URL, or None."""
    if not is_printables_model_url(value):
        return None
    match = _MODEL_PATH_RE.search(urlparse(value).path)
    return match.group(1) if match else None


def gql_headers():
    """Headers for API calls.

    platecli identifies itself honestly to Printables and does not forge
    browser-only headers (Origin/Referer). Verified against
    https://api.printables.com/graphql/ on 2026-07-25: both the file-info query
    and the GetDownloadLink mutation return HTTP 200 with data using an honest
    User-Agent and no Origin/Referer.
    """
    return {
        "User-Agent": platecli_user_agent(),
        "Accept": "*/*",
        "Content-Type": "application/json",
    }


def _post_graphql(payload, opener, headers):
    """POST a GraphQL document and return the decoded JSON body.

    Raises ``PrintablesUnavailable`` if the endpoint could not be reached, and
    ``PrintablesContractChanged`` if what came back is not a JSON object.
    """
    req = urllib.request.Request(_API_URL, data=json.dumps(payload).encode(), headers=headers)
    try:
        with polite_open(opener, req, timeout=DEFAULT_NETWORK_TIMEOUT) as resp:
            raw = resp.read(_MAX_RESPONSE_BYTES)
    except urllib.error.URLError as exc:
        raise PrintablesUnavailable(f"Network error querying the Printables API: {exc}") from exc
    except Exception as exc:
        raise PrintablesUnavailable(f"Could not query the Printables API: {exc}") from exc

    try:
        result = json.loads(raw)
    except Exception as exc:
        raise PrintablesContractChanged(f"Printables API returned a body that is not JSON: {exc}") from exc

    if not isinstance(result, dict):
        raise PrintablesContractChanged(f"Printables API returned {type(result).__name__}, expected a JSON object.")
    return result


def _raise_for_graphql_errors(result, model_id):
    """Raise if *result* carries a GraphQL error envelope.

    The standard envelope is ``{"errors": [...], "data": null}`` — note "data"
    EXISTS with value None, so ``result.get("data", {})`` yields None rather
    than {}. Every read of "data" in this module coerces with ``or {}`` for
    exactly that reason.
    """
    errors = result.get("errors")
    if not errors:
        return
    first = errors[0] if isinstance(errors, list) and errors else None
    detail = ""
    if isinstance(first, dict) and first.get("message"):
        detail = f": {first['message']}"
    raise PrintablesModelUnavailable(f"Printables API returned an error for model #{model_id}{detail}")


def _select_file(files, file_desc, type_key="stl"):
    if len(files) > 1:
        logger.info(f"   Found {len(files)} {file_desc} files:")
        for entry in files:
            logger.info(f"      • {entry.get('name', '?')} ({entry.get('fileSize', 0) // 1024}KB)")
    chosen = max(files, key=lambda x: x.get("fileSize", 0))
    logger.info(f"   → Using {file_desc}: {chosen.get('name', '?')} ({chosen.get('fileSize', 0) // 1024}KB)")
    return chosen, type_key


def _bucket_files(stls_raw, gcodes_raw):
    """Sort the API's two file lists into stl / step / 3mf buckets."""
    stls, steps, threemfs = [], [], []
    for entry in stls_raw:
        if not isinstance(entry, dict):
            continue
        ext = str(entry.get("name", "")).lower().rpartition(".")[-1]
        if ext == "stl":
            stls.append(entry)
        elif ext in ("step", "stp"):
            steps.append(entry)
        elif ext == "3mf":
            threemfs.append(entry)
    for entry in gcodes_raw:
        if not isinstance(entry, dict):
            continue
        if str(entry.get("name", "")).lower().rpartition(".")[-1] == "3mf":
            threemfs.append(entry)
    return stls, steps, threemfs


def get_file_info(model_id, headers, opener):
    """Resolve a model id to ``(file_id, file_type, file_name)``."""
    result = _post_graphql(
        {
            "variables": {"id": model_id},
            "query": "query($id: ID!){print(id: $id){name stls{name fileSize id} gcodes{name fileSize id}}}",
        },
        opener,
        headers,
    )
    _raise_for_graphql_errors(result, model_id)

    model = (result.get("data") or {}).get("print")
    if not isinstance(model, dict):
        raise PrintablesModelUnavailable(f"Model #{model_id} not found on Printables.")

    stls_raw = model.get("stls") or []
    gcodes_raw = model.get("gcodes") or []
    if not isinstance(stls_raw, list):
        stls_raw = []
    if not isinstance(gcodes_raw, list):
        gcodes_raw = []

    stls, steps, threemfs = _bucket_files(stls_raw, gcodes_raw)

    logger.info(f"   Model: {model.get('name', '?')}")
    if stls:
        chosen, file_type = _select_file(stls, "STL", "stl")
    elif steps:
        chosen, file_type = _select_file(steps, "STEP", "stl")
    elif threemfs:
        logger.warning("   ⚠️  No STL/STEP files — falling back to 3MF (cannot re-slice with custom settings)")
        chosen = max(threemfs, key=lambda x: x.get("fileSize", 0))
        file_type = "gcode" if chosen in gcodes_raw else "stl"
        logger.info(f"   → Using 3MF: {chosen.get('name', '?')} ({chosen.get('fileSize', 0) // 1024}KB)")
    else:
        raise PrintablesModelUnavailable("No STL, STEP, or 3MF files found for this model.")

    file_id = chosen.get("id")
    file_name = chosen.get("name")
    if not file_id or not file_name:
        raise PrintablesContractChanged(
            f"The Printables file chosen for model #{model_id} has no id or name — "
            f"the API's file records changed shape."
        )
    return file_id, file_type, file_name


def get_download_link(file_id, model_id, file_type, file_name, headers, opener):
    """Exchange a file id for a time-limited direct download URL."""
    result = _post_graphql(
        {
            "operationName": "GetDownloadLink",
            "variables": {"id": file_id, "printId": model_id, "source": "model_detail", "fileType": file_type},
            "query": (
                "mutation GetDownloadLink($id: ID!, $printId: ID!, $source: DownloadSourceEnum!, "
                "$fileType: DownloadFileTypeEnum!) { getDownloadLink(id: $id, printId: $printId, "
                "source: $source, fileType: $fileType) { ok output { link } errors { field messages } } }"
            ),
        },
        opener,
        headers,
    )
    _raise_for_graphql_errors(result, model_id)

    # `or {}` at every hop. The previous code used `result.get("data", {})`,
    # which returns None (not {}) for the `{"errors": [...], "data": null}`
    # envelope and then raised AttributeError — surfacing to the user as
    # "Failed to get download link: 'NoneType' object has no attribute 'get'".
    link_payload = (result.get("data") or {}).get("getDownloadLink") or {}
    if not isinstance(link_payload, dict):
        raise PrintablesContractChanged("Printables returned an unexpected getDownloadLink payload.")

    if link_payload.get("ok"):
        link = (link_payload.get("output") or {}).get("link")
        if link:
            return link, file_name
        raise PrintablesContractChanged("Printables reported success but returned no download link.")

    errs = link_payload.get("errors") or []
    message = "unknown error"
    if isinstance(errs, list) and errs and isinstance(errs[0], dict):
        messages = errs[0].get("messages") or []
        if isinstance(messages, list) and messages:
            message = str(messages[0])
    raise PrintablesModelUnavailable(f"Printables refused the download link: {message}")


def default_opener():
    """The SSRF-guarded opener used unless a caller injects its own."""
    return build_safe_opener()
