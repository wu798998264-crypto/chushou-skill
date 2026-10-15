#!/usr/bin/env python3
"""Disk seeds and compatible explicit exports for the chushou inline console.

The live console keeps its current text in widget state. Inline saves do not
send a chat message or call this CLI and do not write disk files. ``current``
reads the disk's initial seed/legacy export, not the live widget's current text.
``render`` packages versions into a trusted UI file and returns metadata only;
never load that HTML as downstream script/prompt context. Legacy publish/apply
remain available only when an explicit disk operation is requested.
"""

from __future__ import annotations

import argparse
import copy
import hashlib
import json
import os
from pathlib import Path
import re
import sys
import tempfile
from contextlib import contextmanager
from datetime import datetime, timezone


SCHEMA = 1
ID_RE = re.compile(r"[a-z0-9][a-z0-9_-]{0,63}\Z")
VERSION_RE = re.compile(r"v[1-9][0-9]*\Z")
REQUEST_RE = re.compile(r"[A-Za-z0-9][A-Za-z0-9_-]{0,127}\Z")
HASH_RE = re.compile(r"[a-f0-9]{64}\Z")
BEGIN = "CHUSHOU_TEXT_CONSOLE_REQUEST_BEGIN"
END = "CHUSHOU_TEXT_CONSOLE_REQUEST_END"
TEMPLATE = Path(__file__).resolve().parent.parent / "assets" / "text-console.html"


class ConsoleError(Exception):
    """An invalid operation which must not change the legacy disk store."""


def digest(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def canonical(value) -> bytes:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode("utf-8")


def now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def integer(value, minimum=0) -> bool:
    return isinstance(value, int) and not isinstance(value, bool) and value >= minimum


def check_id(value, pattern, name):
    if not isinstance(value, str) or not pattern.fullmatch(value):
        raise ConsoleError(f"无效的{name}：{value!r}")
    return value


def checked_path(root: Path, relative: str) -> Path:
    """Do not follow an asset, lock, or manifest symlink outside the store."""
    path = root / relative
    try:
        path.resolve().relative_to(root)
    except ValueError as error:
        raise ConsoleError(f"存储路径越界：{relative}") from error
    return path


def version_path(root, document_id, version_id):
    check_id(document_id, ID_RE, "文档 ID")
    check_id(version_id, VERSION_RE, "版本 ID")
    return checked_path(root, f"versions/{document_id}/{version_id}.txt")


def atomic_write(path: Path, data: bytes):
    path.parent.mkdir(parents=True, exist_ok=True)
    descriptor, temporary = tempfile.mkstemp(prefix=f".{path.name}.", suffix=".tmp", dir=path.parent)
    try:
        with os.fdopen(descriptor, "wb") as stream:
            stream.write(data)
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temporary, path)
    finally:
        if os.path.exists(temporary):
            os.unlink(temporary)


def write_manifest(root, manifest):
    atomic_write(checked_path(root, "manifest.json"),
                 (json.dumps(manifest, ensure_ascii=False, indent=2) + "\n").encode("utf-8"))


@contextmanager
def locked_store(store, create=False):
    root = Path(store).expanduser().resolve()
    if create:
        root.mkdir(parents=True, exist_ok=True)
    if not root.is_dir():
        raise ConsoleError(f"文本版本存储不存在：{root}")
    lock = checked_path(root, ".lock")
    try:
        stream = lock.open("x", encoding="utf-8")
    except FileExistsError as error:
        raise ConsoleError("文本版本存储正在使用中；请稍后重试。不要自动删除锁文件。") from error
    try:
        with stream:
            stream.write(f"pid={os.getpid()}\ncreated_at={now()}\n")
        yield root
    finally:
        lock.unlink(missing_ok=True)


def empty_manifest():
    return {"schema": SCHEMA, "active_document_id": None, "documents": {}, "applied_requests": {}}


def load_manifest(root, allow_empty=False):
    path = checked_path(root, "manifest.json")
    if not path.exists() and allow_empty:
        return empty_manifest()
    try:
        manifest = json.loads(path.read_bytes().decode("utf-8"))
    except (FileNotFoundError, UnicodeError, json.JSONDecodeError) as error:
        raise ConsoleError(f"无法读取文本版本清单：{error}") from error
    if (not isinstance(manifest, dict) or manifest.get("schema") != SCHEMA or
            isinstance(manifest.get("schema"), bool)):
        raise ConsoleError("不支持的文本版本清单")
    documents = manifest.get("documents")
    if not isinstance(documents, dict) or not isinstance(manifest.get("applied_requests"), dict):
        raise ConsoleError("文本版本清单结构不完整")
    for document_id, doc in documents.items():
        check_id(document_id, ID_RE, "文档 ID")
        if (not isinstance(doc, dict) or doc.get("id") != document_id or
                doc.get("kind") not in ("script", "prompt") or
                not isinstance(doc.get("title"), str) or not integer(doc.get("revision"), 1) or
                not isinstance(doc.get("versions"), list) or not doc["versions"]):
            raise ConsoleError(f"文档清单无效：{document_id}")
        seen = set()
        for version in doc["versions"]:
            if not isinstance(version, dict):
                raise ConsoleError(f"版本清单无效：{document_id}")
            version_id = check_id(version.get("id"), VERSION_RE, "版本 ID")
            relative = f"versions/{document_id}/{version_id}.txt"
            version_path(root, document_id, version_id)
            if (version_id in seen or version.get("file") != relative or
                    not integer(version.get("revision"), 1) or
                    not isinstance(version.get("label"), str) or
                    not isinstance(version.get("created_at"), str) or
                    not isinstance(version.get("updated_at"), str) or
                    not isinstance(version.get("sha256"), str) or
                    not HASH_RE.fullmatch(version["sha256"])):
                raise ConsoleError(f"版本清单无效：{document_id}/{version_id}")
            seen.add(version_id)
        if doc.get("current_version_id") not in seen:
            raise ConsoleError(f"当前版本不存在：{document_id}")
    active = manifest.get("active_document_id")
    if active not in documents and not (active is None and not documents):
        raise ConsoleError("当前文档不存在")
    for request_id, applied in manifest["applied_requests"].items():
        check_id(request_id, REQUEST_RE, "请求 ID")
        if (not isinstance(applied, dict) or not isinstance(applied.get("payload_sha256"), str) or
                not HASH_RE.fullmatch(applied["payload_sha256"]) or
                not isinstance(applied.get("receipt"), dict) or
                applied["receipt"].get("status") != "saved" or
                applied["receipt"].get("request_id") != request_id):
            raise ConsoleError("已应用请求记录无效；不能安全重试保存")
    return manifest


def get_document(manifest, document_id=None):
    document_id = document_id or manifest["active_document_id"]
    check_id(document_id, ID_RE, "文档 ID")
    try:
        return manifest["documents"][document_id]
    except KeyError as error:
        raise ConsoleError(f"文档不存在：{document_id}") from error


def get_version(doc, version_id=None):
    version_id = version_id or doc["current_version_id"]
    check_id(version_id, VERSION_RE, "版本 ID")
    for version in doc["versions"]:
        if version["id"] == version_id:
            return version
    raise ConsoleError(f"版本不存在：{doc['id']}/{version_id}")


def read_version(root, doc, version):
    try:
        data = version_path(root, doc["id"], version["id"]).read_bytes()
        content = data.decode("utf-8")
    except (OSError, UnicodeError) as error:
        raise ConsoleError(f"无法读取正文：{doc['id']}/{version['id']}：{error}") from error
    if digest(data) != version["sha256"]:
        raise ConsoleError(f"正文摘要不符：{doc['id']}/{version['id']}；请核对外部修改，不能直接覆盖。")
    return content


def summary(root, doc, version=None):
    version = version or get_version(doc)
    return {"store_root": str(root), "document_id": doc["id"], "kind": doc["kind"],
            "title": doc["title"], "document_revision": doc["revision"],
            "current_version_id": doc["current_version_id"], "version_id": version["id"],
            "version_revision": version["revision"], "sha256": version["sha256"],
            "file": str(version_path(root, doc["id"], version["id"]))}


def append_version(doc, content, label):
    number = max((int(v["id"][1:]) for v in doc["versions"]), default=0) + 1
    version_id = f"v{number}"
    timestamp = now()
    version = {"id": version_id, "label": label or f"版本 {number}", "revision": 1,
               "sha256": digest(content.encode("utf-8")),
               "file": f"versions/{doc['id']}/{version_id}.txt",
               "created_at": timestamp, "updated_at": timestamp}
    doc["versions"].append(version)
    doc["current_version_id"] = version_id
    return version


def commit(root, old_manifest, new_manifest, doc, version, content=None):
    """Rollback the text if committing/confirming the manifest fails."""
    path = version_path(root, doc["id"], version["id"])
    old_data = path.read_bytes() if content is not None and path.exists() else None
    if content is not None:
        atomic_write(path, content.encode("utf-8"))
    try:
        write_manifest(root, new_manifest)
        confirmed = load_manifest(root)
        if confirmed != new_manifest:
            raise ConsoleError("写入后的版本清单确认失败")
        confirmed_doc = get_document(confirmed, doc["id"])
        read_version(root, confirmed_doc, get_version(confirmed_doc, version["id"]))
    except Exception:
        if content is not None:
            if old_data is not None:
                atomic_write(path, old_data)
            else:
                path.unlink(missing_ok=True)
        # Also undo a completed manifest write if its readback failed.
        manifest_path = checked_path(root, "manifest.json")
        if manifest_path.exists():
            try:
                on_disk = json.loads(manifest_path.read_bytes().decode("utf-8"))
                if on_disk == new_manifest:
                    write_manifest(root, old_manifest)
            except (OSError, UnicodeError, json.JSONDecodeError):
                pass
        raise


def publish(store, document_id, kind, title, source_file, label="", expected_revision=None):
    check_id(document_id, ID_RE, "文档 ID")
    if kind not in ("script", "prompt") or not isinstance(title, str) or not title.strip():
        raise ConsoleError("文档类型或标题无效")
    content = Path(source_file).read_bytes().decode("utf-8")
    with locked_store(store, create=True) as root:
        old = load_manifest(root, allow_empty=True)
        manifest = copy.deepcopy(old)
        doc = manifest["documents"].get(document_id)
        revision = doc["revision"] if doc else 0
        if expected_revision is not None and expected_revision != revision:
            raise ConsoleError(f"版本冲突：预计文档修订 {expected_revision}，当前为 {revision}。请刷新操作台。")
        if doc is None:
            doc = {"id": document_id, "kind": kind, "title": title, "revision": 0,
                   "current_version_id": None, "versions": []}
            manifest["documents"][document_id] = doc
        elif doc["kind"] != kind:
            raise ConsoleError("已有文档的类型不能更改")
        # An explicit disk publish checks only its current seed/export body.
        # Archived bodies are unnecessary here; live UI edits are widget state.
        if doc["versions"]:
            read_version(root, doc, get_version(doc))
        doc["title"] = title
        doc["revision"] += 1
        version = append_version(doc, content, label)
        manifest["active_document_id"] = document_id
        commit(root, old, manifest, doc, version, content)
        return {"status": "saved", "action": "publish", **summary(root, doc, version)}


def read_request(request_file):
    text = Path(request_file).read_bytes().decode("utf-8-sig")
    starts = list(re.finditer(rf"(?m)^{BEGIN}\r?$", text))
    ends = list(re.finditer(rf"(?m)^{END}\r?$", text))
    if starts or ends:
        if len(starts) != 1 or len(ends) != 1 or starts[0].end() >= ends[0].start():
            raise ConsoleError("操作台请求边界无效")
        text = text[starts[0].end():ends[0].start()]
    try:
        request = json.loads(text)
    except json.JSONDecodeError as error:
        raise ConsoleError(f"操作台请求不是有效 JSON：{error}") from error
    if not isinstance(request, dict):
        raise ConsoleError("操作台请求必须为 JSON 对象")
    required = {"schema", "store_root", "document_id", "action", "version_id",
                "expected_document_revision", "expected_sha256", "request_id"}
    if (not required.issubset(request) or set(request) - (required | {"content"}) or
            request.get("schema") != SCHEMA or isinstance(request.get("schema"), bool) or
            request.get("action") not in ("overwrite", "new_version", "activate") or
            not isinstance(request.get("store_root"), str) or
            not integer(request.get("expected_document_revision"), 1) or
            not isinstance(request.get("expected_sha256"), str) or
            not HASH_RE.fullmatch(request["expected_sha256"])):
        raise ConsoleError("操作台请求字段无效或不完整")
    check_id(request["document_id"], ID_RE, "文档 ID")
    check_id(request["version_id"], VERSION_RE, "版本 ID")
    check_id(request["request_id"], REQUEST_RE, "请求 ID")
    if request["action"] in ("overwrite", "new_version"):
        if not isinstance(request.get("content"), str):
            raise ConsoleError("待保存正文必须是字符串")
        # JSON may legally contain an unpaired surrogate; UTF-8 cannot persist it.
        try:
            request["content"].encode("utf-8")
        except UnicodeError as error:
            raise ConsoleError("正文包含无效的 Unicode 字符") from error
    elif "content" in request:
        raise ConsoleError("切换当前版本时不能同时修改正文")
    return request


def apply_request(store, request_file):
    request = read_request(request_file)
    trusted_root = Path(store).expanduser().resolve()
    requested_root = Path(request["store_root"]).expanduser()
    if not requested_root.is_absolute() or requested_root.resolve() != trusted_root:
        raise ConsoleError("请求的存储目录与本项目登记的目录不一致")
    payload_sha256 = digest(canonical(request))
    with locked_store(trusted_root) as root:
        old = load_manifest(root)
        previous = old["applied_requests"].get(request["request_id"])
        if previous:
            if previous.get("payload_sha256") != payload_sha256:
                raise ConsoleError("请求 ID 已用于不同内容；不能重复覆盖")
            result = dict(previous["receipt"])
            result["idempotent_replay"] = True
            return result
        manifest = copy.deepcopy(old)
        doc = get_document(manifest, request["document_id"])
        version = get_version(doc, request["version_id"])
        if request["expected_document_revision"] != doc["revision"]:
            raise ConsoleError(f"版本冲突：预计文档修订 {request['expected_document_revision']}，"
                               f"当前为 {doc['revision']}。保留编辑内容并刷新操作台。")
        old_content = read_version(root, doc, version)
        if request["expected_sha256"] != version["sha256"]:
            raise ConsoleError("版本冲突：正文摘要已改变。保留编辑内容并刷新操作台。")
        action = request["action"]
        content = request.get("content")
        history_file = None
        if action == "overwrite":
            relative = f"history/{doc['id']}/{version['id']}/r{version['revision']}.txt"
            snapshot = checked_path(root, relative)
            previous_bytes = old_content.encode("utf-8")
            if snapshot.exists() and snapshot.read_bytes() != previous_bytes:
                raise ConsoleError("覆盖历史与旧正文不一致；不能覆盖历史记录")
            if not snapshot.exists():
                atomic_write(snapshot, previous_bytes)
            history_file = str(snapshot)
            version["revision"] += 1
            version["updated_at"] = now()
            version["sha256"] = digest(content.encode("utf-8"))
        elif action == "new_version":
            version = append_version(doc, content, "")
        doc["revision"] += 1
        doc["current_version_id"] = version["id"]
        manifest["active_document_id"] = doc["id"]
        receipt = {"status": "saved", "action": action, "request_id": request["request_id"],
                   "saved_at": now(), **summary(root, doc, version)}
        if history_file:
            receipt["history_file"] = history_file
        manifest["applied_requests"][request["request_id"]] = {
            "payload_sha256": payload_sha256, "receipt": receipt}
        commit(root, old, manifest, doc, version, content)
        return receipt


def current(store, document_id=None, output_file=None):
    """Read one disk seed/explicit export body, not the live widget's current text."""
    with locked_store(store) as root:
        manifest = load_manifest(root)
        doc = get_document(manifest, document_id)
        version = get_version(doc)
        content = read_version(root, doc, version)
        result = {**summary(root, doc, version), "content": content}
        if output_file:
            output = Path(output_file).expanduser().resolve()
            try:
                output.relative_to(root)
            except ValueError:
                pass
            else:
                raise ConsoleError("导出路径不能位于权威文本版本存储内")
            atomic_write(output, content.encode("utf-8"))
            result["output_file"] = str(output)
        return result


def list_documents(store):
    with locked_store(store) as root:
        manifest = load_manifest(root)
        return {"schema": SCHEMA, "store_root": str(root),
                "active_document_id": manifest["active_document_id"],
                "documents": [{**summary(root, doc), "versions": doc["versions"]}
                              for doc in manifest["documents"].values()]}


def render(store, output, document_id=None, draft_request=None):
    """Package the initial UI seed; subsequent inline saves stay in widget state."""
    draft = read_request(draft_request) if draft_request else None
    with locked_store(store) as root:
        manifest = load_manifest(root)
        if draft:
            requested_root = Path(draft["store_root"]).expanduser()
            if not requested_root.is_absolute() or requested_root.resolve() != root:
                raise ConsoleError("草稿请求的存储目录与本项目登记的目录不一致")
            if draft["action"] not in ("overwrite", "new_version"):
                raise ConsoleError("草稿请求必须包含待保存正文")
            if document_id and document_id != draft["document_id"]:
                raise ConsoleError("草稿请求与指定预览文档不一致")
            draft_doc = get_document(manifest, draft["document_id"])
            get_version(draft_doc, draft["version_id"])
            # A rejected save deliberately carries an old revision/hash. Refresh
            # those from disk, while keeping content only as an unsaved UI draft.
            document_id = draft_doc["id"]
        selected = get_document(manifest, document_id)
        data = {"schema": SCHEMA, "store_root": str(root),
                "selected_document_id": selected["id"], "documents": []}
        if draft:
            data["draft"] = {"document_id": draft["document_id"],
                             "version_id": draft["version_id"], "content": draft["content"]}
        documents = [selected] if document_id is not None else manifest["documents"].values()
        for doc in documents:
            seed_doc = {key: doc[key] for key in ("id", "kind", "title", "revision", "current_version_id")}
            seed_doc["versions"] = []
            for version in doc["versions"]:
                seed_doc["versions"].append({**version, "content": read_version(root, doc, version)})
            data["documents"].append(seed_doc)
        template = TEMPLATE.read_bytes().decode("utf-8")
        placeholder = "__CONSOLE_DATA_JSON__"
        if template.count(placeholder) != 1:
            raise ConsoleError("操作台模板缺少唯一的数据占位符")
        encoded = json.dumps(data, ensure_ascii=False, separators=(",", ":"))
        encoded = encoded.replace("<", "\\u003c").replace("\u2028", "\\u2028").replace("\u2029", "\\u2029")
        fragment = template.replace(placeholder, encoded).encode("utf-8")
        if len(fragment) > 1_000_000:
            raise ConsoleError("操作台超过 1 MB 内联容量；请精简重复说明或导出完整文档，不能截断正文冒充完整预览。正文已保存在磁盘。")
        output_path = Path(output).expanduser().resolve()
        try:
            output_path.relative_to(root)
        except ValueError:
            pass
        else:
            raise ConsoleError("操作台 HTML 不能写入权威文本版本存储内")
        atomic_write(output_path, fragment)
        return {"status": "rendered", "mode": "inline_widget_state",
                "output_file": str(output_path), "bytes": len(fragment),
                "document_id": selected["id"], "document_revision": selected["revision"]}


def build_parser():
    parser = argparse.ArgumentParser(description=__doc__)
    commands = parser.add_subparsers(dest="command", required=True)
    for name in ("publish", "list", "current", "render", "apply"):
        command = commands.add_parser(name)
        command.add_argument("--store", required=True, help="Trusted, registered project text store")
        if name in ("publish", "current", "render"):
            command.add_argument("--document", required=name == "publish")
        if name == "publish":
            command.add_argument("--kind", required=True, choices=("script", "prompt"))
            command.add_argument("--title", required=True)
            command.add_argument("--source-file", required=True)
            command.add_argument("--label", default="")
            command.add_argument("--expected-revision", type=int)
        elif name == "current":
            command.add_argument("--output-file")
        elif name == "render":
            command.add_argument("--output", required=True)
            command.add_argument("--draft-request", help="Rejected save JSON/envelope; restore as unsaved draft only")
        elif name == "apply":
            command.add_argument("--request-file", required=True)
    return parser


def main():
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")
        sys.stderr.reconfigure(encoding="utf-8")
    args = build_parser().parse_args()
    try:
        if args.command == "publish":
            result = publish(args.store, args.document, args.kind, args.title, args.source_file,
                             args.label, args.expected_revision)
        elif args.command == "list":
            result = list_documents(args.store)
        elif args.command == "current":
            result = current(args.store, args.document, args.output_file)
        elif args.command == "render":
            result = render(args.store, args.output, args.document, args.draft_request)
        else:
            result = apply_request(args.store, args.request_file)
    except (ConsoleError, OSError, UnicodeError) as error:
        print(json.dumps({"status": "error", "error": str(error)}, ensure_ascii=False), file=sys.stderr)
        return 2
    print(json.dumps(result, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
