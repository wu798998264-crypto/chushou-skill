"""Use the running Shensi application's existing image/video profiles and queue.

No provider keys are exported. POST submissions are never automatically retried.
"""
import argparse
import base64
import hashlib
import json
import os
from pathlib import Path
import re
import shutil
import subprocess
import sys
from datetime import datetime, timezone
from decimal import Decimal, InvalidOperation, ROUND_HALF_UP
import urllib.error
import urllib.request

PROFILE_FIELDS = (
    "id", "name", "remarkName", "adapter", "provider", "protocol", "model",
    "timeoutMs", "dreaminaCliProfile", "draft", "systemManaged",
)
SECRET_FIELDS = re.compile(r"(?:api.?key|secret|access.?token|refresh.?token|session.?token|password|authorization|cookie|credential|^token$)", re.I)
TERMINAL = {"complete", "failed", "retry_required", "cancelled", "abandoned"}


def now():
    return datetime.now(timezone.utc).isoformat()


def read_json(path):
    return json.loads(Path(path).read_text(encoding="utf-8-sig"))


def write_json(path, value, exclusive=False):
    target = Path(path)
    target.parent.mkdir(parents=True, exist_ok=True)
    with target.open("x" if exclusive else "w", encoding="utf-8", newline="\n") as stream:
        json.dump(value, stream, ensure_ascii=False, indent=2)
        stream.write("\n")


def request_digest(payload):
    return hashlib.sha256(json.dumps(payload, sort_keys=True, ensure_ascii=False).encode()).hexdigest()


def round_video_duration(value):
    """Match the user's composer rule, including half seconds rounded upward."""
    if isinstance(value, bool):
        raise ValueError("视频时长必须为正数秒数")
    try:
        seconds = Decimal(str(value))
    except InvalidOperation as exc:
        raise ValueError("视频时长必须为正数秒数") from exc
    if not seconds.is_finite() or seconds <= 0:
        raise ValueError("视频时长必须为正数秒数")
    rounded = int(seconds.to_integral_value(rounding=ROUND_HALF_UP))
    if rounded < 1:
        raise ValueError("本段时长四舍五入后不足1秒；请合并到相邻段后重新核对")
    return rounded


def prompt_duration_seconds(prompt):
    """Read one segment's shot timecodes; never mistake a source offset for length."""
    content = re.split(r"\[画面内容\]\s*[:：]", prompt)[-1]
    code = r"\d{1,3}[:：]\d{2}(?:[:：]\d{2})?(?:\.\d+)?"
    ranges = re.findall(r"[（(]\s*(" + code + r")\s*[-－—–~～]\s*(" + code + r")\s*[）)]", content)
    if not ranges:
        return None

    def seconds(code):
        total = Decimal(0)
        for part in code.replace("：", ":").split(":"):
            total = total * 60 + Decimal(part)
        return total

    first = previous_end = seconds(ranges[0][0])
    for start, end in ranges:
        start, end = seconds(start), seconds(end)
        if end <= start or start < previous_end:
            raise ValueError("一张视频卡片只能包含一段连续时间轴；请按具体分段创建独立卡片")
        previous_end = end
    return previous_end - first


def validate_video_confirmation(payload, confirmation_file):
    if payload.get("channel") != "video":
        return
    if not confirmation_file:
        raise ValueError("视频生成须先由用户核对并确认具体生成信息；缺少视频确认记录")
    confirmation = read_json(confirmation_file)
    if not (confirmation.get("confirmed") is True
            and confirmation.get("confirmedBy") == "user"
            and str(confirmation.get("confirmationMessage", "")).strip()
            and confirmation.get("submissionId") == payload.get("submissionId")
            and confirmation.get("requestSha256") == request_digest(payload)):
        raise ValueError("视频确认记录不完整或与当前请求不一致；请重新核对本次生成信息")


def reject_secrets(value):
    if isinstance(value, dict):
        for key, child in value.items():
            if SECRET_FIELDS.search(key):
                raise ValueError("请求不得携带凭据；由神思本机配置解析")
            reject_secrets(child)
    elif isinstance(value, list):
        for child in value:
            reject_secrets(child)


class NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        raise ValueError("本地神思接口不允许重定向")


def discover_origins():
    explicit = os.environ.get("SHENSI_ORIGIN", "")
    if explicit:
        return [explicit]
    if os.name != "nt":
        raise ValueError("请用 --origin 指定正在运行的神思本地地址")
    command = (
        "$ErrorActionPreference='Stop'; "
        "Get-CimInstance Win32_Process | Where-Object { "
        "$_.Name -eq 'node.exe' -and $_.CommandLine -match 'server\\.mjs' "
        "-and $_.CommandLine -match '(?i)shensi' } | ForEach-Object { "
        "if ($_.CommandLine -match '--port\\s+(\\d+)') { $Matches[1] } }"
    )
    result = subprocess.run(
        ["powershell.exe", "-NoProfile", "-NonInteractive", "-Command", command],
        capture_output=True, text=True, timeout=20,
        creationflags=subprocess.CREATE_NO_WINDOW,
    )
    return list(dict.fromkeys(
        "http://127.0.0.1:" + port for port in result.stdout.splitlines()
        if port.strip().isdigit()
    ))


class Shensi:
    def __init__(self, origin=None):
        self.opener = urllib.request.build_opener(urllib.request.ProxyHandler({}), NoRedirect())
        candidates = [origin] if origin else discover_origins()
        working = []
        for candidate in candidates:
            candidate = candidate.rstrip("/")
            if not re.fullmatch(r"http://127\.0\.0\.1:[0-9]{1,5}", candidate):
                raise ValueError("仅支持 http://127.0.0.1:端口 的神思本机地址")
            try:
                with self.opener.open(candidate + "/", timeout=8) as response:
                    html = response.read(4_000_000).decode("utf-8")
                match = re.search(r'name="shensi-session-token"\s+content="([^\"]+)"', html)
                if match:
                    working.append((candidate, match.group(1)))
            except (OSError, ValueError):
                continue
        if len(working) != 1:
            raise ValueError("未找到唯一的神思服务；请启动神思，或用 --origin 指定当前实例")
        self.origin, self._session = working[0]

    def api(self, route, body=None):
        request = urllib.request.Request(
            self.origin + route,
            data=None if body is None else json.dumps(body, ensure_ascii=False).encode("utf-8"),
            headers={"X-Shensi-Session": self._session, "Origin": self.origin,
                     "Content-Type": "application/json; charset=utf-8"},
            method="GET" if body is None else "POST",
        )
        try:
            with self.opener.open(request, timeout=45) as response:
                payload = json.load(response)
        except urllib.error.HTTPError as error:
            try:
                payload = json.load(error)
                code = str(payload.get("code") or "HTTP_" + str(error.code))
            except (ValueError, AttributeError):
                code = "HTTP_" + str(error.code)
            raise RuntimeError("神思接口拒绝请求：" + code) from None
        if payload.get("ok") is False:
            raise RuntimeError("神思接口失败：" + str(payload.get("code", "UNKNOWN")))
        return payload

    def catalog(self):
        stored = self.api("/api/generation/profile-settings")
        bindings = self.api("/api/generation/runtime/bindings").get("bindings", [])
        settings = stored.get("settings", {})
        channels = {}
        for channel in ("image", "video"):
            profiles = []
            for profile in settings.get(channel + "Connections", []):
                safe = {k: profile[k] for k in PROFILE_FIELDS if k in profile}
                safe["bindingRegistered"] = any(
                    b.get("channel") == channel
                    and (b.get("profileId") or b.get("connectionId") or b.get("id")) == profile.get("id")
                    for b in bindings
                )
                profiles.append(safe)
            channels[channel] = {
                "activeProfileId": settings.get("active" + channel.title() + "ConnectionId", ""),
                "count": len(profiles), "profiles": profiles,
            }
        return {"ok": True, "origin": self.origin, "checkedAt": now(),
                "profileRevision": stored.get("revision"), "channels": channels,
                "verification": "local-session-and-profile-read; provider generation not tested"}


def safe_job(job):
    result = {key: job[key] for key in (
        "id", "channel", "status", "createdAt", "updatedAt", "completedAt", "progressPercent",
        "providerStatus", "providerErrorCode", "providerTaskId", "target", "reused",
    ) if key in job}
    attachment = (job.get("result") or {}).get("attachment")
    attachments = (job.get("result") or {}).get("attachments")
    if attachment or attachments:
        result["result"] = {"attachment": attachment, "attachments": attachments}
    result["terminal"] = job.get("status") in TERMINAL
    result["errorPresent"] = bool(job.get("error"))
    return result


def prepare(client, args):
    catalog = client.catalog()
    group = catalog["channels"][args.channel]
    requested_id = args.profile or group["activeProfileId"]
    matches = [p for p in group["profiles"] if requested_id in
               (p.get("id"), p.get("name"), p.get("remarkName"))]
    if len(matches) != 1 or matches[0].get("draft"):
        raise ValueError("配置名称不存在、重复或尚为草稿；请使用 catalog 中的准确配置ID")
    profile = {key: matches[0][key] for key in PROFILE_FIELDS if key in matches[0]}
    target = read_json(args.target_file)
    if target.get("targetType") not in {"whiteboard-node", "conversation-message", "document-artifact"}:
        raise ValueError("需指定真实神思工作区的白板、对话或文档目标")
    required = {"whiteboard-node": ["documentId", "nodeId"],
                "conversation-message": ["conversationId", "messageId"],
                "document-artifact": ["documentId", "anchorId", "artifactId"]}
    if not target.get("workspacePath") or any(not target.get(k) for k in required[target["targetType"]]):
        raise ValueError("目标工作区或目标标识不完整")
    loaded_target = client.api("/api/workspace/load", {"workspacePath": target["workspacePath"], "fresh": True})
    target_state = loaded_target.get("state")
    if not target_state:
        raise ValueError("目标工作区尚未初始化")
    actual_kind = "notebook" if target_state.get("workspaceKind") == "notebook" else "project"
    if target.get("workspaceKind", actual_kind) != actual_kind:
        raise ValueError("生成目标的工作区类型与神思真实工作区不一致")
    target["workspaceKind"] = actual_kind
    request = read_json(args.params_file) if args.params_file else {}
    reject_secrets(request)
    if any(key in request for key in ("settings", "forceNewGeneration", "submissionId")):
        raise ValueError("参数文件不能覆盖配置、提交ID或强制重新生成")
    prompt = Path(args.prompt_file).read_text(encoding="utf-8-sig").strip()
    if not prompt:
        raise ValueError("生成提示词为空")
    request.update({"prompt": prompt, "executionPrompt": prompt, "settings": profile})
    request.setdefault("aspectRatio", "16:9")
    if args.channel == "video":
        duration = prompt_duration_seconds(prompt)
        if duration is None:
            duration = request.get("duration")
        request["duration"] = round_video_duration(duration)
        request.setdefault("resolution", "720p")
        request.setdefault("generateAudio", True)
    elif profile.get("provider") == "即梦":
        request.setdefault("quality", "2k")
    elif profile.get("provider") == "LibTV":
        request.setdefault("quality", "standard")
        request.setdefault("resolution", "2k")
    else:
        request.setdefault("quality", "high")
        request.setdefault("resolution", "2k")
    payload = {"channel": args.channel, "submissionId": args.operation_id,
               "target": target, "request": request}
    if args.channel == "video":
        payload["timing"] = {"expectedDurationSeconds": float(duration),
                             "composerDurationSeconds": request["duration"],
                             "rounding": "half_up"}
    reject_secrets(payload)
    write_json(args.out, payload, exclusive=True)
    return {"ok": True, "prepared": str(Path(args.out).resolve()),
            "channel": args.channel, "profileId": profile["id"], "submitted": False}


def submit(client, args):
    payload = read_json(args.request_file)
    reject_secrets(payload)
    if payload.get("channel") not in {"image", "video"} or not payload.get("submissionId"):
        raise ValueError("请求必须有 image/video 通道及稳定 submissionId")
    if payload.get("forceNewGeneration") or payload.get("request", {}).get("forceNewGeneration"):
        raise ValueError("桥接不支持强制重新提交；先查询已有任务")
    validate_video_confirmation(payload, getattr(args, "video_confirmation_file", None))
    receipt = {"submissionId": payload["submissionId"], "state": "submitting", "createdAt": now(),
               "requestFile": str(Path(args.request_file).resolve()),
               "requestSha256": request_digest(payload)}
    write_json(args.receipt, receipt, exclusive=True)
    try:
        response = client.api("/api/generation/jobs/media", payload)
    except Exception:
        receipt["state"] = "outcome_unknown"
        write_json(args.receipt, receipt)
        raise RuntimeError("提交未确认；已保存回执。先查询 jobs，不能换ID盲目重提") from None
    receipt.update({"state": "accepted", "job": safe_job(response.get("job", {})),
                    "reused": response.get("reused", False)})
    write_json(args.receipt, receipt)
    return receipt


def workspace(client, args):
    record = Path(args.out)
    requested_kind = getattr(args, "kind", "notebook")
    if record.exists():
        binding = read_json(record)
        if binding.get("operationId") != args.operation_id:
            raise ValueError("已有绑定对应其他操作，不能覆盖")
        if binding.get("workspaceKind") and binding["workspaceKind"] != requested_kind:
            raise ValueError("已有绑定的模式不同；保留原绑定，为笔记模式使用独立绑定文件，或明确 --kind project")
        if binding.get("workspacePath") and binding.get("conversationId"):
            return binding
    else:
        binding = {"operationId": args.operation_id, "state": "creating", "workspaceKind": requested_kind,
                   "createdAt": now()}
        write_json(record, binding, exclusive=True)
    # Both native creation endpoints use a stable operationId for recovery.
    if not binding.get("workspacePath"):
        if requested_kind == "notebook":
            matches = [item for item in client.api("/api/notebooks/list").get("notebooks", [])
                       if item.get("name") == args.name and not item.get("temporary")]
            if len(matches) > 1:
                raise ValueError("同名笔记本不唯一，不能自动选择")
            existing = matches[0] if matches else None
        else:
            existing = None
        if existing:
            created_workspace = existing
            binding["reusedExisting"] = True
        else:
            endpoint = "/api/notebooks/create" if requested_kind == "notebook" else "/api/projects/create"
            created = client.api(endpoint, {"name": args.name, "operationId": args.operation_id})
            created_workspace = created.get("notebook" if requested_kind == "notebook" else "project") or {}
            binding["createdByBinding"] = True
        binding["workspacePath"] = created_workspace.get("workspacePath") or created_workspace.get("path")
        if not binding["workspacePath"]:
            raise ValueError("神思未返回托管工作区路径")
        write_json(record, binding)
    loaded = client.api("/api/workspace/load", {"workspacePath": binding["workspacePath"], "fresh": True})
    state = loaded.get("state") or {}
    conversation_id = state.get("activeConversationId")
    if not state and binding.get("state") == "creating":
        if binding.get("reusedExisting") and not binding.get("createdByBinding"):
            raise ValueError("同名已有笔记本未能安全读取，禁止用空模板覆盖")
        # Initialize only the new, empty workspace created by this binding,
        # using the installed application's own state factory.
        app_root = Path(args.app_root) if args.app_root else Path(os.environ.get("LOCALAPPDATA", "")) / "Programs/Shensi/resources/app"
        app_root = app_root.resolve()
        runtime = app_root.parent / "node.exe"
        factory = app_root / "src/data.js"
        if not runtime.is_file() or not factory.is_file():
            raise ValueError("找不到神思已安装的空工作区工厂；用 --app-root 指定 resources/app")
        factory_name = "createBlankNotebookState" if requested_kind == "notebook" else "createBlankProjectState"
        program = ("const m=await import(process.argv[1]);const s=m[process.argv[2]](JSON.parse(process.argv[3]));"
                   "process.stdout.write(JSON.stringify(s));")
        generated = subprocess.run(
            [str(runtime), "--input-type=module", "-e", program, factory.as_uri(), factory_name,
             json.dumps({"name": args.name, "workspacePath": binding["workspacePath"]}, ensure_ascii=False)],
            capture_output=True, text=True, encoding="utf-8", timeout=30,
            creationflags=subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0,
        )
        if generated.returncode:
            raise ValueError("神思空工作区工厂执行失败；保留创建回执")
        state = json.loads(generated.stdout)
        state["workspaceCreationOperationId"] = args.operation_id
        client.api("/api/workspace/save", {"workspacePath": binding["workspacePath"], "state": state,
                   "expectedStateStamp": loaded.get("stateStamp", "")})
        verified = client.api("/api/workspace/load", {"workspacePath": binding["workspacePath"], "fresh": True})
        conversation_id = (verified.get("state") or {}).get("activeConversationId")
    if not conversation_id:
        raise ValueError("神思工作区未返回真实对话ID；保留绑定，请检查工作区")
    if state and state.get("workspaceKind", "project") != requested_kind:
        raise ValueError("已连接工作区类型与请求模式不一致，保留绑定并停止")
    binding.update({"state": "connected", "workspaceKind": requested_kind, "conversationId": conversation_id})
    write_json(record, binding)
    return binding


def upload(client, args):
    binding = read_json(args.binding_file)
    source = Path(args.file).resolve()
    if not source.is_file() or source.stat().st_size > 40 * 1024 * 1024:
        raise ValueError("单个JSON附件导入必须是实际文件且不超过40MiB；更大视频使用神思multipart导入")
    import mimetypes
    mime = args.mime_type or mimetypes.guess_type(source.name)[0]
    if not mime or not mime.startswith(("image/", "video/", "audio/")):
        raise ValueError("仅导入图片、视频或音频参考")
    digest = hashlib.sha256(source.read_bytes()).hexdigest()
    record = Path(args.out)
    if record.exists():
        previous = read_json(record)
        if previous.get("sourceSha256") != digest or previous.get("workspacePath") != binding["workspacePath"]:
            raise ValueError("已有导入记录对应其他文件或工作区")
        return previous
    result = client.api("/api/workspace/attachment", {"workspacePath": binding["workspacePath"],
                        "name": source.name, "mimeType": mime,
                        "base64": base64.b64encode(source.read_bytes()).decode("ascii")})
    saved = {"ok": True, "workspacePath": binding["workspacePath"], "sourcePath": str(source),
             "sourceSha256": digest, "attachment": result["attachment"]}
    write_json(record, saved, exclusive=True)
    return saved


def export_job(client, args):
    job = client.api("/api/generation/jobs/" + args.job_id)["job"]
    if job.get("status") != "complete":
        raise ValueError("任务尚未完成，不能交付")
    result = job.get("result") or {}
    attachments = result.get("attachments") or [result.get("attachment")]
    root = Path(job["target"]["workspacePath"]).resolve()
    out = Path(args.out_dir).resolve()
    out.mkdir(parents=True, exist_ok=True)
    delivered = []
    for item in attachments:
        if not item or not item.get("relativePath") or not item.get("sha256"):
            raise ValueError("结果缺少验收路径或SHA256")
        source = (root / item["relativePath"]).resolve()
        if not source.is_relative_to(root) or not source.is_file():
            raise ValueError("结果文件不存在或超出神思工作区")
        with source.open("rb") as stream:
            digest = hashlib.file_digest(stream, "sha256").hexdigest()
        if digest != item["sha256"]:
            raise ValueError("神思结果文件SHA256不一致，停止交付")
        name = re.sub(r'[<>:"/\\|?*\x00-\x1f]', "_", item.get("name") or source.name)
        destination = out / name
        if destination.is_symlink() or not destination.resolve().is_relative_to(out):
            raise ValueError("输出文件是链接或超出指定目录")
        created = False
        if destination.exists():
            if not destination.is_file():
                raise ValueError("输出位置已存在非普通文件")
            with destination.open("rb") as stream:
                if hashlib.file_digest(stream, "sha256").hexdigest() != digest:
                    raise ValueError("输出目录已有同名不同内容文件，不能覆盖")
        else:
            with destination.open("xb") as stream, source.open("rb") as origin:
                created = True
                shutil.copyfileobj(origin, stream)
        with destination.open("rb") as stream:
            copied_digest = hashlib.file_digest(stream, "sha256").hexdigest()
        if copied_digest != digest:
            if created:
                destination.unlink()
            raise ValueError("复制后文件SHA256不一致，未交付本次不合格文件")
        delivered.append({"path": str(destination), "sha256": digest, "mimeType": item.get("mimeType")})
    return {"ok": True, "jobId": job["id"], "files": delivered}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--origin", help="否则自动发现当前神思后台端口")
    sub = parser.add_subparsers(dest="command", required=True)
    catalog = sub.add_parser("catalog", help="读取全部图片/视频配置，不生成")
    catalog.add_argument("--out")
    setup = sub.add_parser("workspace", help="创建或恢复专用神思托管工作区，不提交媒体任务")
    for name in ["name", "operation-id", "out"]:
        setup.add_argument("--" + name, required=True)
    setup.add_argument("--app-root", help="必要时指定已安装神思的 resources/app")
    setup.add_argument("--kind", choices=["notebook", "project"], default="notebook",
                       help="默认笔记模式；用户明确要求作品模式时使用 project")
    target = sub.add_parser("target", help="为已连接工作区准备一个独立生成目标")
    for name in ["binding-file", "operation-id", "out"]:
        target.add_argument("--" + name, required=True)
    reference = sub.add_parser("upload", help="将选定参考图/音视频导入已绑定的神思工作区")
    for name in ["binding-file", "file", "out"]:
        reference.add_argument("--" + name, required=True)
    reference.add_argument("--mime-type")
    plan = sub.add_parser("prepare", help="从原有配置准备请求，不提交")
    plan.add_argument("--channel", choices=["image", "video"], required=True)
    plan.add_argument("--profile", help="配置ID或唯一名称；省略用神思当前选择")
    for name in ["prompt-file", "target-file", "operation-id", "out"]:
        plan.add_argument("--" + name, required=True)
    plan.add_argument("--params-file")
    send = sub.add_parser("submit", help="提交一次后台生成任务")
    send.add_argument("--request-file", required=True)
    send.add_argument("--receipt", required=True)
    send.add_argument("--video-confirmation-file", help="用户核对本次具体视频请求后的确认记录；图片不需要")
    job = sub.add_parser("status", help="查询已有任务；不重新提交")
    job.add_argument("job_id")
    jobs = sub.add_parser("jobs", help="查目标工作区的任务")
    jobs.add_argument("--workspace", required=True)
    export = sub.add_parser("export", help="校验已完成的文件并复制到本地项目")
    export.add_argument("job_id")
    export.add_argument("--out-dir", required=True)
    args = parser.parse_args()
    client = Shensi(args.origin)
    if args.command == "catalog":
        result = client.catalog()
        if args.out:
            write_json(args.out, result)
    elif args.command == "workspace":
        result = workspace(client, args)
    elif args.command == "target":
        binding = read_json(args.binding_file)
        result = {"targetType": "conversation-message", "workspaceKind": binding["workspaceKind"],
                  "workspacePath": binding["workspacePath"], "conversationId": binding["conversationId"],
                  "messageId": args.operation_id, "landAfterGeneration": False}
        write_json(args.out, result, exclusive=True)
    elif args.command == "upload":
        result = upload(client, args)
    elif args.command == "prepare":
        result = prepare(client, args)
    elif args.command == "submit":
        result = submit(client, args)
    elif args.command == "status":
        if not re.fullmatch(r"generation-[a-z0-9-]+", args.job_id, re.I):
            raise ValueError("任务ID无效")
        result = {"ok": True, "job": safe_job(client.api("/api/generation/jobs/" + args.job_id)["job"])}
    elif args.command == "export":
        if not re.fullmatch(r"generation-[a-z0-9-]+", args.job_id, re.I):
            raise ValueError("任务ID无效")
        result = export_job(client, args)
    else:
        import urllib.parse
        route = "/api/generation/jobs?includeApplied=true&workspacePath=" + urllib.parse.quote(args.workspace, safe="")
        result = {"ok": True, "jobs": [safe_job(job) for job in client.api(route).get("jobs", [])]}
    print(json.dumps(result, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    try:
        main()
    except (ValueError, RuntimeError, OSError) as error:
        print(json.dumps({"ok": False, "error": str(error)}, ensure_ascii=False), file=sys.stderr)
        sys.exit(1)
