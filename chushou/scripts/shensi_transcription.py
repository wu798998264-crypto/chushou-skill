"""Submit durable Shensi ASR batches; wait locally without LLM polling.

Only the running local Shensi receives requests. Audio uploads to the official
Fun-ASR demo require --confirm-cloud-upload on each start command. No credentials
or transcript bodies are printed. All processing and persistence live in Shensi.
"""
import argparse
import hashlib
import json
from pathlib import Path
import re
import sys
import time

from shensi_bridge import Shensi, read_json, write_json

ROUTE = "/api/whiteboard-video/transcription/"
SUCCESS = {"completed"}
FAILED = {"failed", "interrupted", "cancelled"}
ACTIVE = {"queued", "preparing", "uploading", "processing"}
JOB_FIELDS = ("id", "nodeId", "name", "status", "error", "characters", "durationSeconds", "performance", "attempts", "createdAt", "updatedAt", "completedAt", "reusedFrom", "sourceSha256", "model")


def compact_job(job):
    if not isinstance(job, dict) or not isinstance(job.get("id"), str):
        raise ValueError("神思返回的任务摘要无效")
    return {key: job[key] for key in JOB_FIELDS if key in job}


def api(client, scope, action, **body):
    response = client.api(ROUTE + action, {**scope, **body})
    if not isinstance(response, dict) or response.get("ok") is not True or not isinstance(response.get("result"), dict):
        raise RuntimeError("神思转写接口未确认成功：" + str(response.get("code", "INVALID_RESPONSE") if isinstance(response, dict) else "INVALID_RESPONSE"))
    return response["result"]


def scope_from_args(args):
    binding = read_json(args.binding_file)
    if not isinstance(binding, dict) or binding.get("workspaceKind") not in {"notebook", "project"} or not isinstance(binding.get("workspacePath"), str) or not binding["workspacePath"].strip():
        raise ValueError("绑定文件必须包含真实 workspaceKind（notebook/project）及 workspacePath")
    if not args.document_id.strip():
        raise ValueError("必须指定真实白板 documentId")
    return {"workspaceKind": binding["workspaceKind"], "workspacePath": binding["workspacePath"], "documentId": args.document_id}


def safe_name(name, job_id):
    name = re.sub(r'[<>:"/\\|?*\x00-\x1f]', "_", str(name or "素材"))
    name = name.strip(" .")[:70] or "素材"
    if re.fullmatch(r"(?:CON|PRN|AUX|NUL|COM[1-9]|LPT[1-9])(?:\..*)?", name, re.I):
        name = "素材_" + name
    suffix = hashlib.sha256(job_id.encode("utf-8")).hexdigest()[:16]
    return name + "--" + suffix


def save_json_once(path, value):
    """Do not replace an unrelated task or follow an existing output symlink."""
    target = Path(path)
    if target.is_symlink():
        raise ValueError("拒绝写入符号链接输出文件：" + str(target))
    if target.exists():
        if read_json(target) == value:
            return str(target)
        raise ValueError("输出文件已存在且内容不同，请换用新文件名：" + str(target))
    write_json(target, value, exclusive=True)
    return str(target)


def get_result(client, scope, job_id):
    result = api(client, scope, "get", jobId=job_id)
    job = compact_job(result.get("job"))
    if job["id"] != job_id:
        raise RuntimeError("神思返回了其他任务，已停止保存")
    text = result.get("text")
    if not isinstance(text, str):
        raise RuntimeError("神思未返回有效转写原文")
    if job.get("status") == "completed" and not text.strip():
        raise RuntimeError("任务声称完成但转写原文为空")
    return {"job": job, "text": text}


def export_results(client, scope, jobs, directory):
    root = Path(directory).resolve()
    root.mkdir(parents=True, exist_ok=True)
    exported = []
    for job in jobs:
        if job.get("status") != "completed":
            continue
        result = get_result(client, scope, job["id"])
        stem = safe_name(job.get("name"), job["id"])
        json_path, text_path = root / (stem + ".json"), root / (stem + ".txt")
        if json_path.is_symlink() or text_path.is_symlink():
            raise ValueError("导出目录存在同名符号链接，已停止保存")
        # Include the job id in both filenames; never overwrite differing output.
        if text_path.exists():
            if text_path.read_text(encoding="utf-8") != result["text"]:
                raise ValueError("同名 TXT 已存在且内容不同：" + str(text_path))
        else:
            with text_path.open("x", encoding="utf-8", newline="") as stream:
                stream.write(result["text"])
        save_json_once(json_path, result)
        exported.append({"jobId": job["id"], "txt": str(text_path), "json": str(json_path), "characters": len(result["text"])})
    return exported


def receipt_ids(path, scope):
    receipt = read_json(path)
    if not isinstance(receipt, dict) or receipt.get("scope") != scope:
        raise ValueError("收据不属于当前绑定工作区和白板")
    ids = receipt.get("jobIds")
    if not isinstance(ids, list) or not ids or any(not isinstance(item, str) or not item for item in ids):
        raise ValueError("收据缺少有效 jobIds")
    return list(dict.fromkeys(ids))


def wait_jobs(client, scope, job_ids, timeout_seconds=3600, sleep=time.sleep, monotonic=time.monotonic):
    """Fetch summaries every 10s for the requested ids only; never resubmit jobs."""
    if not job_ids:
        raise ValueError("等待必须指定至少一个任务，不会默认等待所有历史任务")
    deadline = monotonic() + max(1, min(float(timeout_seconds), 86400))
    ids = list(dict.fromkeys(job_ids))
    while True:
        result = api(client, scope, "list")
        raw = result.get("jobs")
        if not isinstance(raw, list):
            raise RuntimeError("神思未返回有效任务列表")
        by_id = {item.get("id"): item for item in raw if isinstance(item, dict)}
        missing = [job_id for job_id in ids if job_id not in by_id]
        if missing:
            raise ValueError("当前白板不存在所需任务：" + ", ".join(missing))
        jobs = [compact_job(by_id[job_id]) for job_id in ids]
        report = {"ok": False, "jobIds": ids, "jobs": jobs}
        if all(job.get("status") in SUCCESS for job in jobs):
            return {**report, "ok": True, "state": "completed"}
        if any(job.get("status") in FAILED for job in jobs):
            return {**report, "state": "failed", "message": "所需任务中有失败、中断或取消项；未自动重提"}
        if result.get("storageError"):
            return {**report, "state": "storage_error", "message": str(result["storageError"])}
        if result.get("paused"):
            return {**report, "state": "paused", "message": "神思转写队列已暂停；未自动重提或切换模型"}
        if any(job.get("status") not in ACTIVE | SUCCESS for job in jobs):
            return {**report, "state": "unknown_status", "message": "收到未知任务状态，停止等待"}
        remaining = deadline - monotonic()
        if remaining <= 0:
            return {**report, "state": "timeout", "message": "本地等待已到上限；神思后台任务不会因此取消"}
        sleep(min(10, remaining))


def parser():
    result = argparse.ArgumentParser(description="神思官方 Fun-ASR 批量音视频转写；不会调用 Codex 识别文字")
    result.add_argument("--origin", help="可选神思本机地址；省略时由 shensi_bridge 自动发现")
    result.add_argument("--binding-file", required=True, help="含真实 workspaceKind/workspacePath 的 JSON")
    result.add_argument("--document-id", required=True, help="真实白板 ID")
    commands = result.add_subparsers(dest="command", required=True)
    commands.add_parser("sources")
    commands.add_parser("list")
    start = commands.add_parser("start")
    selection = start.add_mutually_exclusive_group(required=True)
    selection.add_argument("--node-id", action="append", dest="node_ids")
    selection.add_argument("--all", action="store_true")
    start.add_argument("--request-id", required=True, help="稳定批次编号，8至100位字母数字下划线或短横线")
    start.add_argument("--confirm-cloud-upload", required=True, action="store_true", help="用户已明确授权将所选音轨上传官方服务")
    start.add_argument("--out", required=True, help="保存批次收据 JSON")
    start.add_argument("--wait", action="store_true")
    start.add_argument("--export-dir", help="等待结束后导出已完成项 TXT/JSON，需同时 --wait")
    start.add_argument("--timeout-seconds", type=float, default=3600, help="本地等待秒数，默认3600，整剧可设86400（上限24小时）")
    get = commands.add_parser("get")
    get.add_argument("--job-id", required=True)
    get.add_argument("--out", required=True, help="将单项原文及任务信息保存为 JSON")
    wait = commands.add_parser("wait")
    target = wait.add_mutually_exclusive_group(required=True)
    target.add_argument("--receipt")
    target.add_argument("--job-id", action="append", dest="job_ids")
    wait.add_argument("--export-dir")
    wait.add_argument("--out", help="可选等待结果摘要 JSON")
    wait.add_argument("--timeout-seconds", type=float, default=3600, help="本地等待秒数，默认3600，上限86400；到期不取消后台任务")
    for name in ("retry", "cancel"):
        command = commands.add_parser(name)
        command.add_argument("--job-id", required=True)
    return result


def run(args, client=None):
    scope = scope_from_args(args)
    if args.command == "start":
        if not re.fullmatch(r"[A-Za-z0-9_-]{8,100}", args.request_id):
            raise ValueError("request-id 必须为8至100位字母数字下划线或短横线")
        if not args.confirm_cloud_upload:
            raise ValueError("提交前需要明确授权云端音轨上传")
        if args.export_dir and not args.wait:
            raise ValueError("start --export-dir 需要同时指定 --wait；也可稍后单独 wait 导出")
    client = client or Shensi(args.origin)
    if args.command == "sources":
        result = api(client, scope, "sources")
        items = result.get("items")
        if not isinstance(items, list):
            raise RuntimeError("神思未返回有效素材列表")
        return {"ok": True, "items": [{key: item[key] for key in ("nodeId", "name", "kind") if key in item} for item in items]}, 0
    if args.command == "list":
        result = api(client, scope, "list")
        return {"ok": True, "jobs": [compact_job(job) for job in result.get("jobs", [])], "paused": bool(result.get("paused")), "storageError": result.get("storageError", "")}, 0
    if args.command in {"cancel", "retry"}:
        result = api(client, scope, args.command, jobId=args.job_id)
        job = compact_job(result.get("job"))
        if job["id"] != args.job_id:
            raise RuntimeError("神思返回了其他任务，未确认操作成功")
        return {"ok": True, "action": args.command, "job": job}, 0
    if args.command == "get":
        result = get_result(client, scope, args.job_id)
        path = save_json_once(args.out, result)
        return {"ok": True, "job": result["job"], "out": path, "characters": len(result["text"])}, 0
    if args.command == "start":
        ids = args.node_ids
        if args.all:
            sources = api(client, scope, "sources").get("items")
            if not isinstance(sources, list):
                raise RuntimeError("神思未返回有效素材列表")
            ids = [item["nodeId"] for item in sources if isinstance(item, dict) and isinstance(item.get("nodeId"), str) and item["nodeId"]]
        ids = list(dict.fromkeys(ids or []))
        if not ids or len(ids) > 50:
            raise ValueError("每批请选择1至50个有效音视频素材；未自动拆批")
        receipt_path = Path(args.out)
        if receipt_path.is_symlink():
            raise ValueError("拒绝写入符号链接收据")
        if receipt_path.exists():
            previous = read_json(receipt_path)
            if not isinstance(previous, dict) or previous.get("scope") != scope or previous.get("requestId") != args.request_id or previous.get("nodeIds") != ids:
                raise ValueError("收据路径已属于其他批次；尚未提交，请换用新收据文件名")
        started = api(client, scope, "start", nodeIds=ids, requestId=args.request_id, cloudUploadConfirmed=True)
        jobs = [compact_job(job) for job in started.get("jobs", [])]
        if not jobs:
            raise RuntimeError("神思没有确认任何转写任务")
        job_ids = list(dict.fromkeys(job["id"] for job in jobs))
        receipt = {"version": 1, "scope": scope, "requestId": args.request_id, "nodeIds": ids, "jobIds": job_ids}
        save_json_once(args.out, receipt)
        if not args.wait:
            return {"ok": True, "state": "accepted", "receipt": str(args.out), "jobs": jobs, "message": "神思后台将串行处理；收据可用于稍后等待与导出"}, 0
    else:
        job_ids = receipt_ids(args.receipt, scope) if args.receipt else args.job_ids
    result = wait_jobs(client, scope, job_ids, args.timeout_seconds)
    if args.export_dir:
        result["exported"] = export_results(client, scope, result["jobs"], args.export_dir)
    if args.command == "wait" and args.out:
        save_json_once(args.out, result)
    if args.command == "start":
        result["receipt"] = str(args.out)
    return result, 0 if result["ok"] else 2


def main(argv=None):
    for stream in (sys.stdout, sys.stderr):
        if hasattr(stream, "reconfigure"):
            stream.reconfigure(encoding="utf-8")
    try:
        result, code = run(parser().parse_args(argv))
    except (OSError, ValueError, RuntimeError) as error:
        result, code = {"ok": False, "error": str(error)}, 1
    except KeyboardInterrupt:
        result, code = {"ok": False, "error": "本地等待已停止；神思后台任务未被取消"}, 130
    print(json.dumps(result, ensure_ascii=False, indent=2))
    return code


if __name__ == "__main__":
    sys.exit(main())
