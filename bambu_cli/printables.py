"""Printables.com-specific model resolution: detect Printables model page
URLs and resolve them to a direct downloadable file URL via the Printables
GraphQL API."""

import json
import re
import urllib.error
import urllib.request
from urllib.parse import urlparse

from bambu_cli.constants import DEFAULT_NETWORK_TIMEOUT
from bambu_cli.logging_utils import logger
from bambu_cli.netsafety import _default_user_agent, build_safe_opener


def _is_printables_model_url(value):  # pragma: no cover -- printables helper
    parsed = urlparse(value)
    host = (parsed.hostname or "").lower()
    return host in ("printables.com", "www.printables.com") and bool(re.search(r"/model/(\d+)", parsed.path))


def _select_printables_file(files, file_desc, type_key="stl"):  # pragma: no cover -- printables helper
    if len(files) > 1:
        logger.info(f"   Found {len(files)} {file_desc} files:")
        for s in files:
            logger.info(f"      • {s['name']} ({s.get('fileSize', 0) // 1024}KB)")
    file_to_use = max(files, key=lambda x: x.get("fileSize", 0))
    logger.info(f"   → Using {file_desc}: {file_to_use['name']} ({file_to_use.get('fileSize', 0) // 1024}KB)")
    return file_to_use, type_key


def _get_printables_file_info(model_id, gql_headers, opener):  # pragma: no cover -- printables helper
    """Helper to fetch file info from Printables API."""

    payload = json.dumps(
        {
            "variables": {"id": model_id},
            "query": "query($id: ID!){print(id: $id){name stls{name fileSize id} gcodes{name fileSize id}}}",
        }
    )
    req = urllib.request.Request("https://api.printables.com/graphql/", data=payload.encode(), headers=gql_headers)

    file_type = "stl"
    try:
        with opener.open(req, timeout=DEFAULT_NETWORK_TIMEOUT) as resp:
            response_data = resp.read()
    except urllib.error.URLError as e:
        logger.error(f"Network error querying Printables API: {e}")
        return None, None, None
    except Exception as e:
        logger.error(f"Failed to query Printables API: {e}")
        return None, None, None

    try:
        result = json.loads(response_data)
    except Exception as e:
        logger.error(f"Failed to parse Printables API response: {e}")
        return None, None, None

    if not isinstance(result, dict):
        logger.error("Invalid Printables API response structure.")
        return None, None, None

    model = result.get("data", {}).get("print")
    if not model:
        logger.error(f"Model #{model_id} not found on Printables")
        return None, None, None

    stls_raw = model.get("stls", [])
    gcodes_raw = model.get("gcodes", [])

    stls, steps, threemfs = [], [], []
    for s in stls_raw:
        ext = s.get("name", "").lower().rpartition(".")[-1]
        if ext == "stl":
            stls.append(s)
        elif ext in ("step", "stp"):
            steps.append(s)
        elif ext == "3mf":
            threemfs.append(s)
    for g in gcodes_raw:
        ext = g.get("name", "").lower().rpartition(".")[-1]
        if ext == "3mf":
            threemfs.append(g)

    logger.info(f"   Model: {model.get('name', '?')}")
    if stls:
        file_to_use, file_type = _select_printables_file(stls, "STL", "stl")
    elif steps:
        file_to_use, file_type = _select_printables_file(steps, "STEP", "stl")
    elif threemfs:
        logger.warning("   ⚠️  No STL/STEP files — falling back to 3MF (cannot re-slice with custom settings)")
        file_to_use = max(threemfs, key=lambda x: x.get("fileSize", 0))
        file_type = "gcode" if file_to_use in gcodes_raw else "stl"
        logger.info(f"   → Using 3MF: {file_to_use['name']} ({file_to_use.get('fileSize', 0) // 1024}KB)")
    else:
        logger.error("No STL, STEP, or 3MF files found for this model")
        return None, None, None

    return file_to_use["id"], file_type, file_to_use["name"]


def _get_printables_download_link(file_id, model_id, file_type, stl_name, gql_headers, opener):  # pragma: no cover -- printables helper
    """Helper to fetch download link from Printables API."""

    payload = json.dumps(
        {
            "operationName": "GetDownloadLink",
            "variables": {"id": file_id, "printId": model_id, "source": "model_detail", "fileType": file_type},
            "query": "mutation GetDownloadLink($id: ID!, $printId: ID!, $source: DownloadSourceEnum!, $fileType: DownloadFileTypeEnum!) { getDownloadLink(id: $id, printId: $printId, source: $source, fileType: $fileType) { ok output { link } errors { field messages } } }",
        }
    )
    req = urllib.request.Request("https://api.printables.com/graphql/", data=payload.encode(), headers=gql_headers)

    try:
        with opener.open(req, timeout=DEFAULT_NETWORK_TIMEOUT) as resp:
            result = json.loads(resp.read())
            dl = result.get("data", {}).get("getDownloadLink", {})
            if dl.get("ok") and dl.get("output", {}).get("link"):
                download_url = dl["output"]["link"]
                return download_url, stl_name
            else:
                errs = dl.get("errors", [])
                msg = errs[0]["messages"][0] if errs else "unknown error"
                logger.error(f"Failed to get download link: {msg}")
                return None, None
    except urllib.error.URLError as e:
        logger.error(f"Network error getting download link: {e}")
        return None, None
    except Exception as e:
        logger.error(f"Failed to get download link: {e}")
        return None, None


def resolve_printables_url(url):  # pragma: no cover -- printables helper
    """Resolve a Printables model URL to a direct file download URL and filename.
    Returns (download_url, filename) or (None, None) if resolution fails.
    """
    if not _is_printables_model_url(url):
        return None, None

    printables_match = re.search(r"/model/(\d+)", urlparse(url).path)
    if not printables_match:
        return None, None

    model_id = printables_match.group(1)
    logger.info(f"🔍 Detected Printables model #{model_id}, resolving files...")

    headers = {
        "User-Agent": _default_user_agent(),
        "Accept": "*/*",
    }
    gql_headers = {
        **headers,
        "Content-Type": "application/json",
        "Origin": "https://www.printables.com",
        "Referer": "https://www.printables.com/",
    }

    opener = build_safe_opener()
    file_id, file_type, stl_name = _get_printables_file_info(model_id, gql_headers, opener)
    if not file_id:
        return None, None

    return _get_printables_download_link(file_id, model_id, file_type, stl_name, gql_headers, opener)
