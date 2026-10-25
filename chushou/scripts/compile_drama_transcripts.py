#!/usr/bin/env python3
"""Merge explicitly mapped Shensi transcripts in the Fruitknife source order.

No downloading, recognition, rewriting, or network access is performed. Relative
catalog paths resolve from the download manifest; relative transcript_file paths
resolve from the transcript-map JSON. Existing differing outputs are versioned.
"""
import argparse
import json
import os
from pathlib import Path
import re
import sys
import tempfile


def read_json(path):
    with Path(path).open("r", encoding="utf-8-sig") as stream:
        return json.load(stream)


def object_value(value, label):
    if not isinstance(value, dict):
        raise ValueError(label + " 必须是 JSON 对象")
    return value


def text_value(value, label):
    if not isinstance(value, str) or not value.strip():
        raise ValueError(label + " 必须是非空字符串")
    return value


def number_value(value, label):
    if type(value) is not int or value < 1:
        raise ValueError(label + " 必须是正整数")
    return value


def sha_value(value, label):
    if not isinstance(value, str) or not re.fullmatch(r"[0-9a-fA-F]{64}", value):
        raise ValueError(label + " 必须是 64 位 SHA256")
    return value.lower()


def relative_path(value, base, label):
    path = Path(text_value(value, label)).expanduser()
    return (path if path.is_absolute() else base / path).resolve()


def indexed(items, catalog, label, series_id):
    if not isinstance(items, list):
        raise ValueError(label + " episodes 必须是数组")
    result = {}
    for item in items:
        object_value(item, label + " episode")
        if item.get("series_id", series_id) != series_id:
            raise ValueError(label + " 包含其他剧集")
        number = number_value(item.get("episode"), label + " episode")
        if number in result:
            raise ValueError(f"{label} 重复集次：第{number}集")
        if number not in catalog:
            raise ValueError(f"{label} 集次超出源目录：第{number}集")
        if item.get("video_id") != catalog[number]["video_id"]:
            raise ValueError(f"{label} video_id 不匹配：第{number}集")
        result[number] = item
    return result


def load_inputs(download_path, map_path):
    download_path, map_path = Path(download_path).resolve(), Path(map_path).resolve()
    batch = object_value(read_json(download_path), "下载批次")
    mapping = object_value(read_json(map_path), "转写映射")
    series_id = text_value(batch.get("series_id"), "下载批次 series_id")
    if mapping.get("series_id") != series_id:
        raise ValueError("转写映射与下载批次的 series_id 不匹配")
    catalog_path = relative_path(batch.get("catalog"), download_path.parent, "下载批次 catalog")
    source = object_value(read_json(catalog_path), "源目录")
    if source.get("series_id") != series_id:
        raise ValueError("源目录与下载批次的 series_id 不匹配")
    total = number_value(source.get("total_episodes"), "源目录 total_episodes")
    if number_value(batch.get("source_total_episodes"), "下载批次 source_total_episodes") != total:
        raise ValueError("下载批次与源目录的总集数不匹配")
    raw = source.get("episodes")
    if not isinstance(raw, list) or len(raw) != total:
        raise ValueError("源目录 episodes 数量与 total_episodes 不匹配")
    catalog = {}
    for item in raw:
        object_value(item, "源目录 episode")
        if item.get("series_id", series_id) != series_id:
            raise ValueError("源目录包含其他剧集")
        number = number_value(item.get("episode"), "源目录 episode")
        if number > total or number in catalog:
            raise ValueError("源目录存在重复或越界集次")
        text_value(item.get("video_id"), "源目录 video_id")
        catalog[number] = item
    if set(catalog) != set(range(1, total + 1)):
        raise ValueError("源目录集次不连续，无法确定全集")
    downloads = indexed(batch.get("episodes"), catalog, "下载批次", series_id)
    mapped = indexed(mapping.get("episodes"), catalog, "转写映射", series_id)
    jobs = {}
    for number, item in mapped.items():
        sha = sha_value(item.get("source_sha256"), "映射 source_sha256")
        job_id = text_value(item.get("job_id"), "映射 job_id")
        text_value(item.get("transcript_file"), "映射 transcript_file")
        node_id = item.get("node_id")
        if node_id is not None:
            text_value(node_id, "映射 node_id")
        download = downloads.get(number)
        if not download or download.get("status") not in {"downloaded", "reused"}:
            raise ValueError(f"映射没有对应的已验证下载结果：第{number}集")
        if sha != sha_value(download.get("sha256"), "下载 sha256"):
            raise ValueError(f"映射源 SHA256 不匹配：第{number}集")
        previous = jobs.get(job_id)
        if previous and (previous[0] != sha or previous[1] and node_id and previous[1] != node_id):
            raise ValueError("同一 job_id 对应不同源 SHA256 或冲突 node_id：" + job_id)
        jobs[job_id] = (sha, node_id or (previous[1] if previous else None))
    title = text_value(batch.get("title") or source.get("title"), "剧名")
    return batch, catalog, downloads, mapped, map_path.parent, title, series_id


def compile_text(download_path, map_path):
    batch, catalog, downloads, mapped, map_base, title, series_id = load_inputs(download_path, map_path)
    sections, episodes, seen_exports = [], [], {}
    for number in sorted(catalog):
        entry = {"episode": number, "video_id": catalog[number]["video_id"], "status": "incomplete"}
        download, item = downloads.get(number), mapped.get(number)
        text, reason = None, None
        if not download:
            reason = "未找到本集下载结果"
        elif download.get("status") not in {"downloaded", "reused"}:
            reason = "下载未完成：" + str(download.get("status", "unknown"))
            if download.get("error"):
                entry["download_error"] = str(download["error"])
        elif not download.get("metadata", {}).get("has_audio", True):
            reason = "本集已确认没有音轨"
        elif not item:
            reason = "缺少转写映射"
        else:
            path = relative_path(item["transcript_file"], map_base, "transcript_file")
            entry.update({"source_sha256": item["source_sha256"].lower(), "job_id": item["job_id"], "transcript_file": str(path)})
            if not path.is_file():
                reason = "转写结果文件不存在"
            else:
                exported = object_value(read_json(path), "神思转写结果")
                job = object_value(exported.get("job"), "神思 job")
                if job.get("id") != item["job_id"]:
                    raise ValueError(f"神思 job_id 不匹配：第{number}集")
                if item.get("node_id") is not None and job.get("nodeId") != item["node_id"]:
                    raise ValueError(f"神思 node_id 不匹配：第{number}集")
                identity = (job.get("nodeId"), job.get("status"), exported.get("text"))
                if job["id"] in seen_exports and seen_exports[job["id"]] != identity:
                    raise ValueError("同一 job_id 的多个导出结果存在节点、状态或正文冲突：" + job["id"])
                seen_exports[job["id"]] = identity
                if job.get("sourceSha256") is not None:
                    if sha_value(job["sourceSha256"], "神思 sourceSha256") != item["source_sha256"].lower():
                        raise ValueError(f"神思源 SHA256 不匹配：第{number}集")
                    entry["export_source_sha256_verified"] = True
                else:
                    entry["export_source_sha256_verified"] = False
                if job.get("model") is not None:
                    entry["model"] = job["model"]
                status = text_value(job.get("status"), "神思 job.status")
                if status != "completed":
                    reason = "转写未完成：" + status
                    if job.get("error"):
                        entry["transcription_error"] = str(job["error"])
                elif not isinstance(exported.get("text"), str):
                    raise ValueError(f"神思转写正文不是字符串：第{number}集")
                elif not exported["text"].strip():
                    reason = "转写正文为空"
                else:
                    text = exported["text"]
                    entry.update({"status": "completed", "characters": len(text)})
        if reason:
            entry["reason"] = reason
            text = "【未完成：" + reason + "】"
        sections.append(f"第{number}集\n\n" + text)
        episodes.append(entry)
    complete = all(item["status"] == "completed" for item in episodes)
    state = "全集完成" if complete else "未完成草稿"
    body = f"{title}\n剧集编号：{series_id}\n识别状态：{state}\n共{len(catalog)}集\n\n" + "\n\n".join(sections) + "\n"
    report = {"schema_version": 1, "series_id": series_id, "title": title, "complete": complete,
              "total_episodes": len(catalog), "completed_episodes": sum(item["status"] == "completed" for item in episodes),
              "incomplete_episodes": [item["episode"] for item in episodes if item["status"] != "completed"],
              "download_manifest": str(Path(download_path).resolve()), "transcript_map": str(Path(map_path).resolve()),
              "episodes": episodes}
    return body, report


def write_versioned(path, payload):
    """Publish complete bytes atomically; never replace an existing user file."""
    path = Path(path).absolute()
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = None
    try:
        with tempfile.NamedTemporaryFile(mode="wb", dir=path.parent, prefix="." + path.name + ".", suffix=".tmp", delete=False) as stream:
            temporary = Path(stream.name)
            stream.write(payload)
            stream.flush()
            os.fsync(stream.fileno())
        version = 1
        while True:
            candidate = path if version == 1 else path.with_name(f"{path.stem}.v{version:03d}{path.suffix}")
            if candidate.is_symlink():
                raise ValueError("输出目标是符号链接：" + str(candidate))
            if candidate.exists():
                if candidate.is_file() and candidate.read_bytes() == payload:
                    return candidate
                version += 1
                continue
            try:
                # A same-directory hard link publishes the fully written file in
                # one atomic operation and cannot overwrite a racing writer.
                os.link(temporary, candidate)
                return candidate
            except FileExistsError:
                continue
    finally:
        if temporary is not None:
            temporary.unlink(missing_ok=True)


def parser():
    result = argparse.ArgumentParser(description=__doc__)
    result.add_argument("--download-manifest", required=True, help="水果刀 batch.json；catalog 相对该 JSON 所在目录解析")
    result.add_argument("--transcript-map", required=True, help="显式 series_id/episodes 映射；transcript_file 相对该映射 JSON 所在目录解析")
    result.add_argument("--out", required=True, help="UTF-8 TXT；已有不同内容保存为 .v002 等版本，不覆盖")
    result.add_argument("--report", help="可选 JSON 报告路径；默认与实际 TXT 同名 .report.json")
    result.add_argument("--require-complete", action="store_true", help="缺集时仅保存报告，不写不完整 TXT；退出码仍为 2")
    return result


def run(args):
    body, report = compile_text(args.download_manifest, args.transcript_map)
    target = None
    if report["complete"] or not args.require_complete:
        target = write_versioned(args.out, body.encode("utf-8"))
    report["document"] = str(target) if target else None
    report["document_status"] = "complete" if report["complete"] else "blocked_incomplete" if args.require_complete else "draft_with_placeholders"
    report_path = Path(args.report) if args.report else (target or Path(args.out)).with_suffix(".report.json")
    path = write_versioned(report_path, (json.dumps(report, ensure_ascii=False, indent=2) + "\n").encode("utf-8"))
    return {"ok": report["complete"], "complete": report["complete"], "out": report["document"], "report": str(path),
            "completed_episodes": report["completed_episodes"], "total_episodes": report["total_episodes"],
            "incomplete_episodes": report["incomplete_episodes"]}, 0 if report["complete"] else 2


def main(argv=None):
    for stream in (sys.stdout, sys.stderr):
        if hasattr(stream, "reconfigure"):
            stream.reconfigure(encoding="utf-8")
    try:
        result, code = run(parser().parse_args(argv))
    except (OSError, ValueError, TypeError, KeyError) as exc:
        result, code = {"ok": False, "error": str(exc)}, 1
    print(json.dumps(result, ensure_ascii=False, indent=2))
    return code


if __name__ == "__main__":
    sys.exit(main())
