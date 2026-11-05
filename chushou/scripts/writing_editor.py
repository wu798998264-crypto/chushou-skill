#!/usr/bin/env python3
"""Serve the Shensi writing editor against one trusted project text store.

Only selected saved versions are downstream context. Drafts and UI state are
per browser client, persisted atomically with the existing manifest; this
service never calls a model, sends chat messages, or starts media generation.
"""

from __future__ import annotations

import copy
import json
import math
from http.server import BaseHTTPRequestHandler, HTTPServer
from pathlib import Path
import secrets
import sys
from urllib.parse import urlsplit

import text_console as console


ASSETS = Path(__file__).resolve().parent.parent / "assets" / "writing-editor"
FILES = {
    "": ("index.html", "text/html; charset=utf-8"),
    "index.html": ("index.html", "text/html; charset=utf-8"),
    **{name: (name, "text/javascript; charset=utf-8") for name in (
        "host.js", "writing-editor.js", "original-icons.js", "editor.js")},
    **{name: (name, "text/css; charset=utf-8") for name in (
        "host.css", "writing-editor.css")},
}
MAX_BODY = 2 * 1024 * 1024
MAX_TEXT = 1024 * 1024
MAX_STORE = 32 * 1024 * 1024
ACTIONS = {"ui-read", "ui-write", "ui-save", "select"}


class EditorError(console.ConsoleError):
    def __init__(self, message, status=400):
        super().__init__(message)
        self.status = status


def client_id(value):
    console.check_id(value, console.REQUEST_RE, "编辑器身份")
    if value in ("__proto__", "prototype", "constructor"):
        raise EditorError("编辑器身份无效")
    return value


def text_value(value):
    if not isinstance(value, str):
        raise EditorError("正文必须是字符串")
    try:
        length = len(value.encode("utf-8"))
    except UnicodeError as error:
        raise EditorError("正文包含无效的 Unicode 字符") from error
    if length > MAX_TEXT:
        raise EditorError("正文超过 1 MiB，未截断或覆盖现有稿件", 413)
    return value


def editor_for(doc, identity):
    editors = doc.get("writing_editors", {})
    if not isinstance(editors, dict):
        raise EditorError("编辑器记录损坏，已阻止覆盖", 409)
    editor = editors.get(identity, {"revision": 0, "drafts": {}, "ui": {}})
    if (not isinstance(editor, dict) or not console.integer(editor.get("revision")) or
            not isinstance(editor.get("drafts"), dict) or not isinstance(editor.get("ui"), dict)):
        raise EditorError("草稿记录损坏，已阻止覆盖", 409)
    for version_id, content in editor["drafts"].items():
        console.get_version(doc, version_id)
        text_value(content)
    return copy.deepcopy(editor)


def projection(root, doc, identity=None):
    version = console.get_version(doc)
    result = {
        "id": doc["id"], "kind": doc["kind"], "title": doc["title"],
        "revision": doc["revision"], "currentVersionId": version["id"],
        "text": console.read_version(root, doc, version), "sha256": version["sha256"],
        "contextPolicy": "selected-saved-version-only",
        "storeId": console.digest(str(root).encode("utf-8")),
        "versions": [{"id": v["id"], "label": v["label"], "createdAt": v["created_at"],
                      **({"sourceVersionId": v["source_version_id"]} if "source_version_id" in v else {})}
                     for v in doc["versions"]],
    }
    if identity is not None:
        result["editor"] = editor_for(doc, identity)
    return result


def expect(actual, expected, message):
    if not console.integer(expected) or actual != expected:
        raise EditorError(message + "；当前草稿保留，请重新读取", 409)


def check_store_capacity(root, manifest, document_id, version_id, content):
    # ponytail: stat registered versions under the store lock; no second index.
    total = len((json.dumps(manifest, ensure_ascii=False, indent=2) + "\n").encode("utf-8"))
    for doc in manifest["documents"].values():
        for version in doc["versions"]:
            if content is not None and doc["id"] == document_id and version["id"] == version_id:
                total += len(content.encode("utf-8"))
            else:
                total += console.version_path(root, doc["id"], version["id"]).stat().st_size
            if total > MAX_STORE:
                raise EditorError("已登记版本正文与清单总量超过 32 MiB，未截断或覆盖现有稿件", 413)


def validate_fields(action, payload, document_id):
    if not isinstance(payload, dict):
        raise EditorError("请求必须是 JSON 对象")
    required = {
        "ui-read": {"clientId"},
        "ui-write": {"clientId", "expectedEditorRevision", "versionId", "text", "ui"},
        "ui-save": {"clientId", "expectedRevision", "expectedEditorRevision", "sourceVersionId", "text", "requestId"},
        "select": {"expectedRevision", "versionId"},
    }[action]
    if not required.issubset(payload) or set(payload) - required - {"consoleId"}:
        raise EditorError("请求字段无效或不完整")
    if payload.get("consoleId", document_id) != document_id:
        raise EditorError("操作台不属于当前服务", 403)


def dispatch(store, document_id, action, payload):
    """The four injected call() actions used by the unmodified Shensi UI."""
    if action not in ACTIONS:
        raise EditorError("未知写作编辑器操作", 404)
    validate_fields(action, payload, document_id)
    identity = client_id(payload["clientId"]) if action != "select" else None
    with console.locked_store(store) as root:
        old = console.load_manifest(root)
        manifest = copy.deepcopy(old)
        doc = console.get_document(manifest, document_id)
        if action == "ui-read":
            return projection(root, doc, identity)
        if action == "ui-save":
            request_id = console.check_id(payload["requestId"], console.REQUEST_RE, "保存请求 ID")
            receipts = doc.setdefault("writing_saves", {})
            if not isinstance(receipts, dict):
                raise EditorError("保存回执记录损坏，已阻止覆盖", 409)
            payload_hash = console.digest(console.canonical(payload))
            previous = receipts.get(request_id)
            if previous:
                if not isinstance(previous, dict) or previous.get("hash") != payload_hash:
                    raise EditorError("请求 ID 已用于不同内容", 409)
                # The UI replaces text and clears its WAL on success. A replay
                # must not acknowledge over a newer selection or draft.
                current_editor = editor_for(doc, identity)
                if (doc["revision"] != previous.get("document_revision") or
                        current_editor["revision"] != previous.get("editor_revision")):
                    raise EditorError("此前保存已完成，但后续版本或草稿已变化；请重新读取，当前草稿保留", 409)
                return projection(root, doc, identity)
        if action in ("select", "ui-save"):
            expect(doc["revision"], payload["expectedRevision"], "所选保存版本已改变")
        editor = editor_for(doc, identity) if identity is not None else None
        if editor is not None:
            expect(editor["revision"], payload["expectedEditorRevision"], "编辑草稿已被另一窗口更新")
        content = None
        if action == "ui-write":
            version = console.get_version(doc, payload["versionId"])
            text = text_value(payload["text"])
            if text == console.read_version(root, doc, version):
                editor["drafts"].pop(version["id"], None)
            else:
                editor["drafts"][version["id"]] = text
            ui = payload["ui"]
            if not isinstance(ui, dict) or set(ui) - {"editing", "collapsed", "expanded", "scrollTop", "inlineScrollTop"}:
                raise EditorError("编辑器界面状态无效")
            for key, value in ui.items():
                if key in ("editing", "collapsed", "expanded"):
                    valid = isinstance(value, bool)
                else:
                    valid = ((type(value) is int and 0 <= value <= 2**53 - 1) or
                             (type(value) is float and math.isfinite(value) and value >= 0))
                if not valid:
                    raise EditorError("编辑器界面状态无效")
                editor["ui"][key] = value
            editor["revision"] += 1
            doc.setdefault("writing_editors", {})[identity] = editor
        elif action == "ui-save":
            source = console.get_version(doc, payload["sourceVersionId"])
            if source["id"] != doc["current_version_id"]:
                raise EditorError("所选保存版本已改变，未创建版本", 409)
            console.read_version(root, doc, source)
            content = text_value(payload["text"])
            version = console.append_version(doc, content, "")
            version["source_version_id"] = source["id"]
            doc["revision"] += 1
            editor["drafts"].pop(source["id"], None)
            editor["revision"] += 1
            doc.setdefault("writing_editors", {})[identity] = editor
            receipts[request_id] = {"hash": payload_hash, "version_id": version["id"],
                                    "document_revision": doc["revision"], "editor_revision": editor["revision"]}
            manifest["active_document_id"] = doc["id"]
        else:
            version = console.get_version(doc, payload["versionId"])
            console.read_version(root, doc, version)
            doc["current_version_id"] = version["id"]
            doc["revision"] += 1
            manifest["active_document_id"] = doc["id"]
        check_store_capacity(root, manifest, doc["id"], doc["current_version_id"], content)
        console.commit(root, old, manifest, doc, console.get_version(doc), content)
        return projection(root, doc, identity)


def list_drafts(store, document_id=None):
    """Discover explicit recovery identities without printing draft bodies."""
    with console.locked_store(store) as root:
        doc = console.get_document(console.load_manifest(root), document_id)
        editors = doc.get("writing_editors", {})
        if not isinstance(editors, dict):
            raise EditorError("编辑器记录损坏，已阻止恢复", 409)
        drafts = []
        for identity in editors:
            editor = editor_for(doc, client_id(identity))
            drafts.extend({"client_id": identity, "version_id": version_id,
                           "characters": len(text), "bytes": len(text.encode("utf-8"))}
                          for version_id, text in editor["drafts"].items())
        return {"store_root": str(root), "document_id": doc["id"], "draft_clients": drafts}


def create_server(store, document_id, resume_client=None):
    root = Path(store).expanduser().resolve()
    # Fail before listening if the selected saved text cannot be verified.
    console.current(root, document_id)
    recovery = list_drafts(root, document_id)
    if resume_client is not None:
        client_id(resume_client)
        if not any(draft["client_id"] == resume_client for draft in recovery["draft_clients"]):
            raise EditorError("所选客户端没有可恢复的草稿；请先用 drafts 查看本稿的恢复身份", 404)
    token = secrets.token_urlsafe(32)

    class Handler(BaseHTTPRequestHandler):
        server_version = "ChushouWritingEditor/1"

        def setup(self):
            super().setup()
            self.connection.settimeout(10)

        def log_message(self, *args):
            # URL paths contain the private session token; do not log them.
            pass

        def respond(self, status, data, content_type="application/json; charset=utf-8"):
            if not isinstance(data, bytes):
                data = console.canonical(data)
            self.send_response(status)
            self.send_header("Content-Type", content_type)
            self.send_header("Content-Length", str(len(data)))
            self.send_header("Cache-Control", "no-store")
            self.send_header("X-Content-Type-Options", "nosniff")
            self.send_header("Referrer-Policy", "no-referrer")
            self.send_header("Content-Security-Policy", "default-src 'none'; script-src 'self'; style-src 'self'; connect-src 'self'; font-src 'self'; img-src 'self'; base-uri 'none'; form-action 'none'")
            self.end_headers()
            self.wfile.write(data)

        def path_in_session(self, require_origin=False):
            host = f"127.0.0.1:{self.server.server_port}"
            origin = self.headers.get("Origin")
            if (self.headers.get("Host") != host or
                    (origin is not None and origin != "http://" + host) or
                    (require_origin and origin != "http://" + host) or
                    self.headers.get("Sec-Fetch-Site") == "cross-site"):
                raise EditorError("拒绝来自其他站点的请求", 403)
            parsed = urlsplit(self.path)
            prefix = f"/{token}/"
            if parsed.scheme or parsed.netloc or parsed.query or parsed.fragment or not parsed.path.startswith(prefix):
                raise EditorError("编辑器会话不存在", 404)
            return parsed.path[len(prefix):]

        def do_GET(self):
            try:
                path = self.path_in_session()
                if path == "api/health":
                    with console.locked_store(root) as locked_root:
                        doc = console.get_document(console.load_manifest(locked_root), document_id)
                    self.respond(200, {"status": "ready", "document_id": document_id,
                                       "document_revision": doc["revision"],
                                       "current_version_id": doc["current_version_id"],
                                       "instruction_revision": doc.get("writing_instruction_revision", 0),
                                       "contextPolicy": "selected-saved-version-only"})
                elif path in FILES:
                    filename, content_type = FILES[path]
                    asset = console.checked_path(ASSETS.resolve(), filename)
                    self.respond(200, asset.read_bytes(), content_type)
                else:
                    raise EditorError("资源不存在", 404)
            except (console.ConsoleError, OSError, ValueError) as error:
                self.respond(getattr(error, "status", 400), {"error": str(error)})

        def do_POST(self):
            try:
                path = self.path_in_session(require_origin=True)
                if not path.startswith("api/") or path[4:] not in ACTIONS:
                    raise EditorError("接口不存在", 404)
                if self.headers.get("Transfer-Encoding"):
                    raise EditorError("不接受分块请求")
                if self.headers.get("Content-Type", "").split(";", 1)[0].strip() != "application/json":
                    raise EditorError("请求必须使用 application/json", 415)
                length = int(self.headers.get("Content-Length", "0"))
                if not 0 < length <= MAX_BODY:
                    raise EditorError("请求正文为空或超过 2 MiB", 413)
                raw = self.rfile.read(length)
                if len(raw) != length:
                    raise EditorError("请求正文不完整")
                def reject_constant(value):
                    raise ValueError("请求包含非有限数字")
                payload = json.loads(raw.decode("utf-8"), parse_constant=reject_constant)
                result = dispatch(root, document_id, path[4:], payload)
                self.respond(200, result)
            except (console.ConsoleError, OSError, UnicodeError, ValueError) as error:
                self.respond(getattr(error, "status", 409 if isinstance(error, console.ConsoleError) else 400),
                             {"error": str(error)})

    # ponytail: one local editor lane; the existing file lock also guards CLI writes.
    server = HTTPServer(("127.0.0.1", 0), Handler)
    server.editor_url = f"http://127.0.0.1:{server.server_port}/{token}/"
    if resume_client is not None:
        server.editor_url += "#client=" + resume_client
    server.editor_document = document_id
    server.editor_draft_clients = recovery["draft_clients"]
    return server


def serve(store, document_id=None, resume_client=None):
    selected = console.current(store, document_id)
    server = create_server(store, selected["document_id"], resume_client)
    print(json.dumps({"status": "ready", "mode": "shensi_writing_editor", "url": server.editor_url,
                      "store_root": selected["store_root"], "document_id": selected["document_id"],
                      "draft_clients": server.editor_draft_clients},
                     ensure_ascii=False), flush=True)
    try:
        server.serve_forever(poll_interval=0.25)
    except KeyboardInterrupt:
        pass
    finally:
        server.server_close()
    return 0


if __name__ == "__main__":
    import argparse
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--store", required=True)
    parser.add_argument("--document")
    parser.add_argument("--resume-client", help="Explicit client ID from the drafts metadata")
    arguments = parser.parse_args()
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")
    try:
        raise SystemExit(serve(arguments.store, arguments.document, arguments.resume_client))
    except (console.ConsoleError, OSError) as error:
        print(json.dumps({"error": str(error)}, ensure_ascii=False), file=sys.stderr)
        raise SystemExit(2)
