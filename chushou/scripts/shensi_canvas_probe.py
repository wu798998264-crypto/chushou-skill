"""Identify running Shensi canvas protocols without reading or changing user content.

Only GET / is requested. No authenticated APIs, notes, settings, or media are read.
This is a routing probe, not a canvas importer or a generation client.
"""
import argparse
from concurrent.futures import ThreadPoolExecutor
from html.parser import HTMLParser
import json
import os
from pathlib import Path
import re
import subprocess
import sys
import urllib.error
import urllib.request

MAX_CANDIDATES = 32
MAX_HTML_BYTES = 2 * 1024 * 1024
PROTOCOLS = {"notes", "legacy"}


class ProbeError(ValueError):
    """An error with a fixed, non-sensitive diagnostic code."""


def normalize_origin(value):
    value = str(value or "").strip().rstrip("/")
    match = re.fullmatch(r"http://127\.0\.0\.1:([0-9]{1,5})", value)
    if not match or not 1 <= int(match.group(1)) <= 65535:
        raise ProbeError("invalid-loopback-origin")
    return "http://127.0.0.1:" + str(int(match.group(1)))


class _MetaNames(HTMLParser):
    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.names = set()

    def handle_starttag(self, tag, attrs):
        if tag.lower() == "meta":
            # Intentionally discard all content attributes, including session tokens.
            for name, value in attrs:
                if name.lower() == "name" and value:
                    self.names.add(value.lower())

    handle_startendtag = handle_starttag


def identify_html(html):
    """Return a protocol marker, never a title, version, or session value."""
    parser = _MetaNames()
    parser.feed(str(html))
    notes = "shensi-session" in parser.names
    legacy = "shensi-session-token" in parser.names
    if notes and legacy:
        return "ambiguous"
    return "notes" if notes else "legacy" if legacy else "unknown"


def select_instance(instances, kind="auto", origin=None):
    """Choose one verified protocol; auto never silently falls back to legacy."""
    if kind not in {"auto", *PROTOCOLS}:
        raise ProbeError("invalid-kind")
    unique = {}
    for item in instances:
        if item.get("protocol") in PROTOCOLS:
            unique[item["origin"]] = item
    available = list(unique.values())
    if origin is not None:
        wanted = normalize_origin(origin)
        item = unique.get(wanted)
        if item is None:
            return {"status": "target-unavailable", "selected": None}
        if kind != "auto" and item["protocol"] != kind:
            return {"status": "target-kind-mismatch", "selected": None}
        return {"status": "selected", "reason": "requested-origin", "selected": item}
    wanted_kind = "notes" if kind == "auto" else kind
    matches = [item for item in available if item["protocol"] == wanted_kind]
    if len(matches) > 1:
        return {"status": "multiple-" + wanted_kind + "-instances", "selected": None}
    if not matches:
        return {"status": "no-default-notes" if kind == "auto" else "no-" + kind + "-instance",
                "selected": None}
    return {"status": "selected", "reason": "default-notes" if kind == "auto" else "requested-kind",
            "selected": matches[0]}


class _NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        raise ProbeError("redirect-refused")


def probe_origin(origin, timeout=3):
    """Fetch just a bounded local homepage; return no page text or session data."""
    origin = normalize_origin(origin)
    opener = urllib.request.build_opener(urllib.request.ProxyHandler({}), _NoRedirect())
    result = {"origin": origin, "protocol": "unknown"}
    try:
        request = urllib.request.Request(origin + "/", headers={"Accept": "text/html"}, method="GET")
        with opener.open(request, timeout=timeout) as response:
            data = response.read(MAX_HTML_BYTES + 1)
            if len(data) > MAX_HTML_BYTES:
                raise ProbeError("homepage-too-large")
            result["protocol"] = identify_html(data.decode("utf-8", errors="replace"))
            result["httpStatus"] = response.status
    except ProbeError as error:
        result["error"] = str(error)
    except urllib.error.HTTPError as error:
        result.update({"error": "http-error", "httpStatus": error.code})
    except (OSError, ValueError):
        result["error"] = "homepage-unavailable"
    result["verification"] = "homepage-marker-only" if result["protocol"] in PROTOCOLS else "unidentified"
    result["capabilities"] = {
        "authenticatedRead": "not-probed", "canvasWrite": "not-probed",
        "mediaImport": "not-probed", "generation": "not-probed",
        "schema": "requires-current-version-verification",
    }
    return result


def discover_origins():
    """Find only relevant Windows processes' actual loopback listeners.

    Electron can host the Notes server in its main process without a --port
    argument. Process command lines are used only for matching inside PowerShell;
    they are never returned, logged, or used as shell commands.
    """
    if os.name != "nt":
        raise ProbeError("explicit-origin-required-on-this-os")
    command = r"""
$ErrorActionPreference='Stop'
$processIds = @(Get-CimInstance Win32_Process | Where-Object {
  $_.Name -match '(?i)shensi|神思' -or
  ($_.Name -match '^(?i:node|electron)\.exe$' -and
   ($_.ExecutablePath -match '(?i)shensi|神思' -or $_.CommandLine -match '(?i)shensi|神思'))
} | ForEach-Object { $_.ProcessId })
$ports = @(Get-NetTCPConnection -State Listen | Where-Object {
  $_.LocalAddress -eq '127.0.0.1' -and $_.OwningProcess -in $processIds
} | Select-Object -ExpandProperty LocalPort -Unique)
ConvertTo-Json -Compress -InputObject $ports
"""
    try:
        result = subprocess.run(
            ["powershell.exe", "-NoProfile", "-NonInteractive", "-Command", command],
            capture_output=True, text=True, encoding="utf-8", errors="replace", timeout=25,
            creationflags=subprocess.CREATE_NO_WINDOW,
        )
    except (OSError, subprocess.TimeoutExpired):
        raise ProbeError("process-discovery-failed") from None
    if result.returncode:
        raise ProbeError("process-discovery-failed")
    try:
        ports = json.loads(result.stdout.strip() or "[]")
    except ValueError:
        raise ProbeError("process-discovery-invalid-result") from None
    if not isinstance(ports, list) or any(type(port) is not int or not 1 <= port <= 65535 for port in ports):
        raise ProbeError("process-discovery-invalid-result")
    if len(ports) > MAX_CANDIDATES:
        raise ProbeError("too-many-candidates-use-explicit-origin")
    return ["http://127.0.0.1:" + str(port) for port in sorted(set(ports))]


def requested_target(origin=None, binding_file=None, environment=None):
    """Explicit CLI target wins; otherwise use supplied binding, then environment.

    A portable binding is {"origin": "http://127.0.0.1:...", "protocol":
    "notes"|"legacy"}. Do not pass app settings or credential files here.
    """
    if origin:
        return {"origin": normalize_origin(origin), "source": "cli", "protocol": None}
    if binding_file:
        try:
            path = Path(binding_file)
            if path.stat().st_size > 65536:
                raise ProbeError("binding-too-large")
            binding = json.loads(path.read_text(encoding="utf-8-sig"))
        except (OSError, ValueError) as error:
            if isinstance(error, ProbeError):
                raise
            raise ProbeError("binding-unreadable") from None
        if not isinstance(binding, dict) or not binding.get("origin"):
            raise ProbeError("binding-missing-origin")
        protocol = binding.get("protocol")
        if protocol is not None and protocol not in PROTOCOLS:
            raise ProbeError("binding-invalid-protocol")
        return {"origin": normalize_origin(binding["origin"]), "source": "binding", "protocol": protocol}
    env_origin = (os.environ if environment is None else environment).get("SHENSI_ORIGIN")
    if env_origin:
        return {"origin": normalize_origin(env_origin), "source": "environment", "protocol": None}
    return {"origin": None, "source": "discovery", "protocol": None}


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--origin", help="Explicit http://127.0.0.1:port; overrides saved binding and environment")
    parser.add_argument("--binding-file", help="Previously saved routing identity JSON; no app settings/credentials")
    parser.add_argument("--kind", choices=["auto", "notes", "legacy"], default="auto")
    parser.add_argument("--list", action="store_true", help="List verified protocols without requiring a selection")
    parser.add_argument("--timeout", type=float, default=3, help="Homepage timeout, 0.2–10 seconds")
    args = parser.parse_args(argv)
    if not .2 <= args.timeout <= 10:
        raise ProbeError("invalid-timeout")
    target = requested_target(args.origin, args.binding_file)
    if target["protocol"] and args.kind != "auto" and target["protocol"] != args.kind:
        raise ProbeError("binding-kind-mismatch")
    kind = target["protocol"] or args.kind
    origins = [target["origin"]] if target["origin"] else discover_origins()
    with ThreadPoolExecutor(max_workers=4) as pool:
        instances = list(pool.map(lambda candidate: probe_origin(candidate, args.timeout), origins))
    selection = select_instance(instances, kind, target["origin"])
    output = {"ok": args.list or selection["status"] == "selected", "readOnly": True,
              "listOnly": args.list, "targetSource": target["source"],
              "instances": instances, **selection}
    print(json.dumps(output, ensure_ascii=False, indent=2))
    return 0 if output["ok"] else 2


if __name__ == "__main__":
    try:
        sys.exit(main())
    except ProbeError as error:
        print(json.dumps({"ok": False, "readOnly": True, "error": str(error)}, ensure_ascii=False), file=sys.stderr)
        sys.exit(2)
