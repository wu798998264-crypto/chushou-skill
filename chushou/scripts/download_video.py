#!/usr/bin/env python3
"""Download a shared video link into an isolated local asset, then inspect it.

Uses direct HTTP for media URLs and official yt-dlp for supported web pages.
Does not acquire browser cookies, use third-party parsing sites, or enable DRM.
Downloaded bytes must contain a finite video stream and pass full decoding.
"""

import argparse
import datetime as dt
import hashlib
import importlib.util
import json
import math
import os
from pathlib import Path
import re
import shutil
import subprocess
import sys
import urllib.error
import urllib.parse
import urllib.request
import uuid

from inspect_video import find_ffmpeg, find_ffprobe, metadata

SCRIPT_ROOT = Path(__file__).resolve().parent
VIDEO_EXTENSIONS = {".mp4", ".m4v", ".mov", ".mkv", ".webm", ".avi", ".flv", ".ts", ".mpeg", ".mpg"}
PROVIDERS = {
    "douyin": ("douyin.com", "iesdouyin.com"),
    "tiktok": ("tiktok.com",),
    "bilibili": ("bilibili.com", "b23.tv"),
    "xiaohongshu": ("xiaohongshu.com", "xhslink.com"),
    "kuaishou": ("kuaishou.com", "gifshow.com", "kwai.com"),
    "youtube": ("youtube.com", "youtu.be"),
    "instagram": ("instagram.com",),
    "twitter": ("twitter.com", "x.com"),
    "facebook": ("facebook.com", "fb.watch"),
}


class DownloadError(ValueError):
    pass


class PlaylistMediaError(DownloadError):
    """A streaming manifest needs segment extraction, not plain HTTP saving."""


def is_playlist_payload(first):
    sample = first.decode("utf-8", "replace").lstrip("\ufeff \r\n\t")
    return sample.startswith("#EXTM3U") or bool(re.search(r"<(?:\w+:)?MPD(?:\s|>)", sample[:4096], re.I))


def now():
    return dt.datetime.now(dt.timezone.utc).isoformat()


def safe_error(value):
    # yt-dlp/urllib errors can include signed URLs. Keep the actual error text,
    # but remove URLs, terminal colour codes, and cookie/token assignments.
    value = re.sub(r"\x1b\[[0-9;]*m", "", str(value))
    value = re.sub(r"https?://[^\s<>\"']+", "[URL withheld]", value)
    value = re.sub(r"(?i)\b(cookie|authorization|token|signature|session)=\S+", r"\1=[withheld]", value)
    return value[-1800:]


def valid_url(value):
    try:
        parsed = urllib.parse.urlsplit(value)
        if parsed.scheme.lower() not in ("http", "https") or not parsed.hostname:
            raise DownloadError("Only absolute HTTP(S) video links are supported")
        if parsed.username is not None or parsed.password is not None:
            raise DownloadError("URL-embedded credentials are not accepted")
        if any(ord(char) < 32 for char in value):
            raise DownloadError("The URL contains control characters")
        return value
    except ValueError as exc:
        raise DownloadError(safe_error(exc)) from exc


def extract_url(value):
    match = re.search(r"https?://[^\s<>\"'，。！？、；：）】]+", value, re.I)
    if not match:
        raise DownloadError("No HTTP(S) link was found in the supplied share text")
    # XuanNian's share-text cleanup, extended for ASCII surrounding brackets.
    return valid_url(match.group(0).rstrip("，。！？、；：）】}>).,;!?"))


def host_matches(host, domain):
    return host == domain or host.endswith("." + domain)


def provider_for(url):
    host = urllib.parse.urlsplit(url).hostname.lower()
    providers = PROVIDERS
    catalog = SCRIPT_ROOT.parent / "references" / "video-platforms.json"
    if catalog.is_file():
        data = json.loads(catalog.read_text(encoding="utf-8"))
        providers = {item["id"]: tuple(item["hosts"]) for item in data["providers"]}
    return next((name for name, domains in providers.items()
                 if any(host_matches(host, domain) for domain in domains)), "generic")


def media_referer(url, fallback):
    parsed = urllib.parse.urlsplit(url)
    host = parsed.hostname.lower()
    if host_matches(host, "xhscdn.com"):
        return "https://www.xiaohongshu.com/"
    if any(host_matches(host, item) for item in ("douyinvod.com", "bytev.com", "douyinpic.com", "snssdk.com", "zjcdn.com")):
        return "https://www.douyin.com/"
    if "/upgcxcode/" in parsed.path:
        return "https://www.bilibili.com/"
    return fallback


def digest(path):
    sha = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            sha.update(chunk)
    return sha.hexdigest()


def write_new_json(path, data):
    with path.open("x", encoding="utf-8") as handle:
        json.dump(data, handle, ensure_ascii=False, indent=2)
        handle.write("\n")


def contained(path, root):
    path = Path(path).resolve()
    if not path.is_relative_to(root.resolve()):
        raise DownloadError("A generated path escaped the download directory")
    return path


def find_reusable(root, request_hash):
    # Only our own one-level run records are scanned, never the entire project.
    for record in sorted(root.glob("video-*/manifest.json"), reverse=True):
        try:
            data = json.loads(record.read_text(encoding="utf-8"))
            if data.get("status") != "downloaded" or data.get("request_sha256") != request_hash:
                continue
            video = contained(data["output_path"], record.parent)
            if video.is_file() and video.stat().st_size == data["bytes"] and digest(video) == data["sha256"]:
                return record, data
        except (OSError, ValueError, KeyError, TypeError):
            continue
    return None, None


class SafeRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        valid_url(newurl)
        return super().redirect_request(req, fp, code, msg, headers, newurl)


def direct_download(url, destination, referer, timeout):
    headers = {"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) DaozeiVideo/1.0",
               "Accept": "video/*,application/octet-stream;q=0.9,*/*;q=0.5", "Accept-Encoding": "identity"}
    if referer:
        headers["Referer"] = referer
    request = urllib.request.Request(url, headers=headers)
    opener = urllib.request.build_opener(SafeRedirect())
    try:
        with opener.open(request, timeout=timeout) as response:
            content_type = response.headers.get_content_type().lower()
            if not 200 <= response.status < 300:
                raise DownloadError("HTTP " + str(response.status))
            if content_type in ("application/vnd.apple.mpegurl", "application/x-mpegurl", "audio/mpegurl", "audio/x-mpegurl", "application/dash+xml"):
                raise PlaylistMediaError("A streaming playlist requires official yt-dlp segment downloading")
            if content_type.startswith(("text/", "image/", "audio/")) or content_type in (
                    "application/json", "application/xml", "application/xhtml+xml") or content_type.endswith(("+json", "+xml")):
                raise DownloadError("The media link returned a non-video Content-Type: " + content_type)
            first = response.read(4096)
            if is_playlist_payload(first):
                raise PlaylistMediaError("The response contains a streaming playlist, not a local video")
            sample = first.decode("utf-8", "replace").lstrip("\ufeff \r\n\t")
            if not first or re.match(r"(?:<!doctype\s+html|<html(?:\s|>)|<head(?:\s|>)|<body(?:\s|>)|<\?xml|\{\s*[\"']|\[\s*\{)", sample, re.I):
                raise DownloadError("The media link returned an empty or textual payload, not video")
            received = len(first)
            expected = int(response.headers.get("Content-Length", "0"))
            with destination.open("xb") as handle:
                handle.write(first)
                for chunk in iter(lambda: response.read(1024 * 1024), b""):
                    handle.write(chunk)
                    received += len(chunk)
            if expected and received != expected:
                raise DownloadError(f"Incomplete HTTP download: expected {expected} bytes, received {received}")
            return {"content_type": content_type, "bytes": received, "mode": "direct_http"}
    except urllib.error.HTTPError as exc:
        raise DownloadError(f"HTTP {exc.code}: {safe_error(exc.reason)}") from exc
    except urllib.error.URLError as exc:
        raise DownloadError("HTTP download failed: " + safe_error(exc.reason)) from exc


def yt_dlp_launcher(args):
    environment = os.environ.copy()
    environment["PYTHONIOENCODING"] = "utf-8"
    environment["PYTHONUTF8"] = "1"
    if args.yt_dlp_command:
        executable = shutil.which(args.yt_dlp_command) or (str(Path(args.yt_dlp_command).resolve()) if Path(args.yt_dlp_command).is_file() else None)
        if not executable:
            raise DownloadError("--yt-dlp-command executable was not found; supply an executable path, not a shell command")
        return [executable], environment
    module_path = Path(args.yt_dlp_path).expanduser().resolve() if args.yt_dlp_path else SCRIPT_ROOT.parent / "runtime" / "python-packages"
    if args.yt_dlp_path or (module_path / "yt_dlp").is_dir():
        if not (module_path / "yt_dlp").is_dir():
            raise DownloadError("--yt-dlp-path must contain the installed yt_dlp package")
        environment["PYTHONPATH"] = str(module_path) + (os.pathsep + environment["PYTHONPATH"] if environment.get("PYTHONPATH") else "")
        return [sys.executable, "-X", "utf8", "-m", "yt_dlp"], environment
    if importlib.util.find_spec("yt_dlp"):
        return [sys.executable, "-X", "utf8", "-m", "yt_dlp"], environment
    executable = shutil.which("yt-dlp")
    if executable:
        return [executable], environment
    raise DownloadError("Official yt-dlp is unavailable. Install it into the Python runtime or supply --yt-dlp-command / --yt-dlp-path. No alternate parsing site was contacted.")


def reject_partial_metadata(info):
    truthy = lambda value: value is True or value == 1 or value == "1"
    if any(truthy(info.get(key)) for key in ("is_preview", "isPreview", "is_drm", "isDrm", "has_drm")):
        raise DownloadError("The extractor identified a preview or DRM-protected video")
    if info.get("availability") in ("premium_only", "subscriber_only", "needs_auth", "private"):
        raise DownloadError("The extractor identified restricted video access")
    if info.get("is_live") or info.get("is_upcoming") or info.get("live_status") in ("is_live", "is_upcoming"):
        raise DownloadError("Live or upcoming streams are not finite source videos")
    # XuanNian's Bilibili playback guard: reject explicit previews and clearly
    # truncated durl results instead of mistaking the preview for full content.
    result = info.get("result") or info.get("data") or {}
    if isinstance(result, dict):
        if any(truthy(result.get(key)) for key in ("is_preview", "isPreview", "is_drm", "isDrm")):
            raise DownloadError("The playback response identified preview-only or DRM content")
        source_ms = float(result.get("timelength") or 0)
        delivered_ms = sum(float(item.get("length") or 0) for item in result.get("durl", []) if isinstance(item, dict))
        if source_ms >= 90000 and delivered_ms > 0 and delivered_ms + 5000 < source_ms * 0.5:
            raise DownloadError("The playback response is clearly truncated")


def yt_dlp_download(url, root, referer, ffmpeg, args):
    launcher, environment = yt_dlp_launcher(args)
    command = launcher + ["--ignore-config", "--no-playlist", "--no-overwrites", "--no-progress", "--no-cache-dir",
                          "--no-simulate", "--write-info-json", "--socket-timeout", str(args.timeout),
                          "--retries", "2", "--fragment-retries", "2", "--ffmpeg-location", ffmpeg,
                          "--format", "bv*+ba/b", "--merge-output-format", "mp4",
                          "--output", str(root / "source.%(ext)s"),
                          "--print", "after_move:CHUSHOU_FILE:%(filepath)j",
                          "--match-filters", "!is_live & !is_upcoming"]
    node = shutil.which("node") or (str(Path("C:/Program Files/nodejs/node.exe")) if Path("C:/Program Files/nodejs/node.exe").is_file() else None)
    if node:
        command += ["--js-runtimes", "node:" + node]
    if referer:
        command += ["--referer", referer]
    command += ["--", url]
    result = subprocess.run(command, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                            text=True, encoding="utf-8", errors="replace", env=environment, shell=False)
    info_path = root / "source.info.json"
    info = {}
    if info_path.is_file():
        try:
            info = json.loads(info_path.read_text(encoding="utf-8"))
        finally:
            # Extractor metadata contains signed stream URLs; keep only sanitized
            # provenance in our manifest, never retain the raw extractor response.
            info_path.unlink()
    if result.returncode != 0:
        raise DownloadError("yt-dlp failed: " + safe_error(result.stderr or result.stdout))
    reject_partial_metadata(info)
    lines = [line.split("CHUSHOU_FILE:", 1)[1] for line in result.stdout.splitlines() if line.startswith("CHUSHOU_FILE:")]
    if len(lines) != 1:
        raise DownloadError("yt-dlp did not return exactly one finished video; no playlist was accepted")
    video = contained(json.loads(lines[0]), root)
    if not video.is_file():
        raise DownloadError("yt-dlp's finished file was not found")
    return video, {"mode": "yt_dlp", "title": info.get("title") or video.stem,
                   "extractor": info.get("extractor_key") or info.get("extractor"),
                   "source_video_id": info.get("id"), "reported_duration_seconds": info.get("duration"),
                   "warnings": safe_error(result.stderr) or None}


def validate_video(video, ffmpeg, ffprobe, expected_duration=None):
    with video.open("rb") as handle:
        if is_playlist_payload(handle.read(4096)):
            raise DownloadError("A streaming playlist cannot be accepted as a finished local video")
    info = metadata(video, ffmpeg, ffprobe)
    duration = info.get("duration_seconds")
    if not info.get("video_codec") or not info.get("width") or not info.get("height"):
        raise DownloadError("The downloaded file has no usable video stream")
    if duration is None or not math.isfinite(duration) or duration <= 0:
        raise DownloadError("The downloaded file has no finite positive duration")
    if expected_duration and float(expected_duration) >= 90 and duration + 5 < float(expected_duration) * 0.5:
        raise DownloadError("The downloaded file is far shorter than the reported source; suspected preview/truncation")
    video_map = "0:" + str(info["video_stream_index"]) if info.get("video_stream_index") is not None else "0:v:0"
    result = subprocess.run([ffmpeg, "-nostdin", "-hide_banner", "-loglevel", "error", "-xerror", "-protocol_whitelist", "file,pipe", "-i", str(video),
                             "-map", video_map, "-map", "0:a?", "-f", "null", "-"],
                            stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True, encoding="utf-8", errors="replace", shell=False)
    if result.returncode != 0:
        raise DownloadError("Downloaded video failed complete decode validation: " + safe_error(result.stderr))
    info["audio_status"] = "audio_stream_present" if info.get("has_audio") else "no_audio_stream_confirmed"
    info["validation"] = "full_video_and_available_audio_decode"
    return info


def inspect_asset(video, root, ffmpeg, ffprobe, args):
    # Sampling requests are part of the analysis identity. Search newest-first
    # so a changed/corrupt old inspection never forces repeated resampling.
    requested_sampling = ({"method": "explicit_timestamps", "timestamps_seconds": sorted(set(float(item.strip()) for item in args.timestamps.split(",")))}
                          if args.timestamps is not None else {"method": "interval", "interval_seconds": args.interval})
    record = root / "inspection-result.json"
    for candidate in sorted(root.glob("inspection-result*.json"), key=lambda item: item.stat().st_mtime_ns, reverse=True):
        try:
            data = json.loads(candidate.read_text(encoding="utf-8"))
            analysis_path = contained(data["analysis"], root)
            if not analysis_path.is_file():
                continue
            analysis = json.loads(analysis_path.read_text(encoding="utf-8"))
            if analysis.get("sampling") != requested_sampling:
                continue
            frame_files_exist = bool(analysis.get("frames")) and all(
                contained(frame["path"], root).is_file() for frame in analysis.get("frames", []))
            sheets_exist = all(contained(path, root).is_file() for path in analysis.get("contact_sheets", []))
            audio = analysis.get("audio", {})
            audio_exists = not analysis.get("metadata", {}).get("has_audio") or (
                audio.get("status") == "extracted" and audio.get("path") and contained(audio["path"], root).is_file())
            if analysis.get("source") == str(video) and frame_files_exist and sheets_exist and audio_exists and not analysis.get("errors"):
                return data
        except (OSError, ValueError, KeyError, TypeError):
            continue
    command = [sys.executable, "-X", "utf8", str(SCRIPT_ROOT / "inspect_video.py"), "--video", str(video),
               "--out-dir", str(root / "analysis"), "--ffmpeg", ffmpeg, "--interval", str(args.interval)]
    if ffprobe:
        command += ["--ffprobe", ffprobe]
    if args.timestamps is not None:
        command += ["--timestamps", args.timestamps]
    result = subprocess.run(command, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                            text=True, encoding="utf-8", errors="replace", shell=False)
    if result.returncode != 0:
        raise DownloadError("Video download succeeded, but frame/audio inspection failed: " + safe_error(result.stderr or result.stdout))
    data = json.loads(result.stdout)
    if not record.exists():
        write_new_json(record, data)
    else:
        write_new_json(root / ("inspection-result-" + uuid.uuid4().hex[:8] + ".json"), data)
    return data


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    source = parser.add_mutually_exclusive_group(required=True)
    source.add_argument("--source", help="URL or complete share text")
    source.add_argument("--source-file", help="UTF-8 file containing the URL/share text")
    parser.add_argument("--out-dir", required=True, help="Project-local download asset directory")
    media_source = parser.add_mutually_exclusive_group()
    media_source.add_argument("--media-url", help="Actual media URL resolved from the source page; not printed")
    media_source.add_argument("--media-url-file", help="UTF-8 file containing one actual media URL; avoids signed URLs in process arguments")
    referer_source = parser.add_mutually_exclusive_group()
    referer_source.add_argument("--referer")
    referer_source.add_argument("--referer-file", help="UTF-8 file containing the HTTP(S) Referer")
    parser.add_argument("--ffmpeg")
    parser.add_argument("--ffprobe")
    parser.add_argument("--yt-dlp-command", help="Official yt-dlp executable path")
    parser.add_argument("--yt-dlp-path", help="Python module directory containing yt_dlp")
    parser.add_argument("--download-only", action="store_true")
    parser.add_argument("--timeout", type=int, default=30)
    parser.add_argument("--interval", type=float, default=1.0)
    parser.add_argument("--timestamps", help="Explicit comma-separated inspection timestamps")
    args = parser.parse_args()
    root = None
    try:
        if args.timeout <= 0 or not math.isfinite(args.interval) or args.interval <= 0:
            raise DownloadError("Timeout and inspection interval must be positive")
        share_text = Path(args.source_file).read_text(encoding="utf-8-sig") if args.source_file else args.source
        url = extract_url(share_text)
        media_value = Path(args.media_url_file).read_text(encoding="utf-8-sig").strip() if args.media_url_file else args.media_url
        media_url = valid_url(media_value) if media_value else None
        referer_value = Path(args.referer_file).read_text(encoding="utf-8-sig").strip() if args.referer_file else args.referer
        if referer_value:
            valid_url(referer_value)
        bundled_ffmpeg = SCRIPT_ROOT.parent / "runtime" / "ffmpeg" / ("ffmpeg.exe" if sys.platform == "win32" else "ffmpeg")
        ffmpeg = find_ffmpeg(args.ffmpeg or (str(bundled_ffmpeg) if bundled_ffmpeg.is_file() else None))
        ffprobe = find_ffprobe(args.ffprobe, ffmpeg)
        requested = Path(args.out_dir).expanduser().resolve()
        requested.mkdir(parents=True, exist_ok=True)
        request_hash = hashlib.sha256(json.dumps({"source": url, "media_url": media_url, "referer": referer_value},
                                               sort_keys=True).encode("utf-8")).hexdigest()
        manifest_path, manifest = find_reusable(requested, request_hash)
        reused = manifest is not None
        if reused:
            root = manifest_path.parent
            video = Path(manifest["output_path"])
        else:
            root = requested / ("video-" + dt.datetime.now(dt.timezone.utc).strftime("%Y%m%dT%H%M%SZ") + "-" + request_hash[:8] + "-" + uuid.uuid4().hex[:6])
            root.mkdir(exist_ok=False)
            write_new_json(root / "request.json", {"created_at": now(), "source_url": url,
                           "request_sha256": request_hash, "provider": provider_for(url),
                           "media_url_sha256": hashlib.sha256(media_url.encode()).hexdigest() if media_url else None,
                           "notes": "Partial files are retained on failure. Retry with the original share text; signed media URL is not persisted."})
            target_url = media_url or url
            extension = Path(urllib.parse.urlsplit(target_url).path).suffix.lower()
            referer = media_referer(target_url, referer_value or (url if media_url else ""))
            use_direct = extension not in (".m3u8", ".mpd") and (media_url is not None or extension in VIDEO_EXTENSIONS)
            if use_direct:
                partial = root / "source.download.part"
                try:
                    transport = direct_download(target_url, partial, referer, args.timeout)
                except PlaylistMediaError:
                    use_direct = False
            if use_direct:
                info = validate_video(partial, ffmpeg, ffprobe)
                video = root / ("source" + (extension if extension in VIDEO_EXTENSIONS else ".mp4"))
                # Exclusive destination creation prevents replacing any asset.
                with partial.open("rb") as incoming, video.open("xb") as outgoing:
                    shutil.copyfileobj(incoming, outgoing, 1024 * 1024)
                partial.unlink()
                title = urllib.parse.unquote(Path(urllib.parse.urlsplit(url).path).name) or "Shared video"
            else:
                video, transport = yt_dlp_download(target_url, root, referer, ffmpeg, args)
                info = validate_video(video, ffmpeg, ffprobe, transport.get("reported_duration_seconds"))
                title = transport["title"]
            manifest = {"schema_version": 1, "status": "downloaded", "created_at": now(),
                        "source_url": url, "provider": provider_for(url), "title": title,
                        "request_sha256": request_hash, "sha256": digest(video), "bytes": video.stat().st_size,
                        "download_mode": transport["mode"], "output_path": str(video), "metadata": info,
                        "transport": transport,
                        "access_note": "No browser cookies or DRM bypass used. Unknown source completeness is not claimed."}
            manifest_path = root / "manifest.json"
            write_new_json(manifest_path, manifest)
        inspection = None if args.download_only else inspect_asset(video, root, ffmpeg, ffprobe, args)
        print(json.dumps({"status": "reused" if reused else "downloaded", "video": str(video),
                          "manifest": str(manifest_path), "metadata": manifest["metadata"],
                          "analysis": inspection.get("analysis") if inspection else None,
                          "inspection": inspection}, ensure_ascii=False, indent=2))
        return 0
    except (OSError, ValueError, json.JSONDecodeError, subprocess.SubprocessError) as exc:
        failure = {"status": "failed", "error": safe_error(exc), "output_directory": str(root) if root else None}
        if root is not None:
            try:
                write_new_json(root / ("failure-" + uuid.uuid4().hex[:8] + ".json"), {**failure, "created_at": now()})
            except OSError:
                pass
        print(json.dumps(failure, ensure_ascii=False, indent=2), file=sys.stderr)
        return 2


if __name__ == "__main__":
    sys.exit(main())
