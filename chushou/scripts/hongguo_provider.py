"""Adapter for supplied hongguo-downloader 1.0.4; no fruitknife fallback."""
import json
import os
from pathlib import Path
import re
import shutil
import subprocess
import sys
import urllib.parse

from download_video import DownloadError, extract_url, host_matches, safe_error

ROOT = Path(__file__).resolve().parent
UPSTREAM = "hongguo-downloader-1.0.4-user-archive-20261109"
QUALITIES = ("360p", "480p", "540p", "720p", "1080p")
HOSTS = ("novelquickapp.com", "hongguoduanju.com", "fanqienovel.com", "fqnovel.com")


def is_hongguo_source(value):
    if re.fullmatch(r"\d{6,25}", str(value).strip()):
        return True
    try:
        host = urllib.parse.urlsplit(extract_url(value)).hostname or ""
        return any(host_matches(host.lower(), domain) for domain in HOSTS)
    except ValueError:
        return False


def parse_episodes(value, total, all_episodes=False):
    if all_episodes:
        if value:
            raise DownloadError("Use either episode selection or all episodes")
        return list(range(1, total + 1))
    if not value:
        raise DownloadError("Specify --episodes or --all-episodes")
    selected = set()
    for part in str(value).replace("，", ",").split(","):
        match = re.fullmatch(r"\s*(\d+)(?:\s*-\s*(\d+))?\s*", part)
        if not match:
            raise DownloadError("Invalid episode range; use 1,3-5")
        start, end = int(match[1]), int(match[2] or match[1])
        if start < 1 or end < start or end > total:
            raise DownloadError(f"Episode range must be between 1 and {total}")
        selected.update(range(start, end + 1))
    return sorted(selected)


class HongguoClient:
    def __init__(self, timeout=30):
        self.timeout = timeout
        self.episodes = {}

    def _call(self, action, **args):
        node = os.environ.get("SHENSI_NODE_PATH") or os.environ.get("CHUSHOU_NODE_PATH") or shutil.which("node")
        if not node:
            raise DownloadError("红果下载需要 Node.js 22+；神思使用随包 Node，本地触手请安装 Node.js")
        env = {k: v for k, v in os.environ.items() if k not in ("NODE_OPTIONS", "NODE_PATH", "ELECTRON_RUN_AS_NODE")}
        result = subprocess.run([node, str(ROOT / "hongguo_vendor" / "cli.cjs")],
                                input=json.dumps({"action": action, **args}, ensure_ascii=False),
                                capture_output=True, text=True, encoding="utf-8", errors="replace",
                                timeout=max(60, self.timeout * 10), shell=False, env=env)
        if result.returncode:
            raise DownloadError(safe_error(result.stderr))
        try:
            return json.loads(result.stdout)
        except ValueError as exc:
            raise DownloadError("红果下载器未返回有效JSON") from exc

    def search(self, keyword, cursor=""):
        result = self._call("search", keyword=keyword, cursor=cursor)
        return {"status": "search_ready", "provider": "hongguo", "keyword": keyword,
                "items": result.get("items", []), "cursor": str(result.get("nextOffset", 0)),
                "has_more": bool(result.get("hasMore")), "upstream_commit": UPSTREAM}

    def inspect(self, source):
        if not is_hongguo_source(source):
            raise DownloadError("请输入红果分享链接或已核对剧集编号")
        result = self._call("inspect", source=source)
        episodes = result.get("episodes", [])
        if not episodes or any(not re.fullmatch(r"\d{6,25}", str(e.get("vid", ""))) for e in episodes):
            raise DownloadError("红果未返回有效集表")
        self.episodes = {str(e["vid"]): e for e in episodes}
        return {"provider": "hongguo", "series_id": result["series_id"], "title": result["series_title"],
                "source_url": extract_url(source) if not str(source).strip().isdigit() else None,
                "total_episodes": len(episodes), "episodes": [{"episode": i, "video_id": str(e["vid"]),
                "title": e.get("title", ""), "source_episode_index": e.get("vid_index")} for i, e in enumerate(episodes, 1)],
                "upstream_commit": UPSTREAM}

    def resolve_episode(self, video_id, quality="1080p"):
        episode = self.episodes.get(str(video_id))
        if not episode:
            raise DownloadError("请先核对集表，再取流")
        result = self._call("resolve", videoId=str(video_id), contentType=episode.get("content_type"))
        return {"url": result["url"], "key": bytes.fromhex(result["key"]) if result.get("key") else None,
                "actual_quality": "source-best", "reported_duration_seconds": episode.get("duration") or None}
