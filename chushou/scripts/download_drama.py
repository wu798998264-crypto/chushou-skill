#!/usr/bin/env python3
"""Fruitknife short-drama preparation: search, inspect, selected downloads.

No GUI is needed. Each completed episode has the same verified manifest and
optional inspection output as download_video.py. Resume verifies file hashes;
an episode missing a valid manifest is downloaded into a new attempt directory.
"""
import argparse
from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path
import subprocess
import sys
from types import SimpleNamespace
import uuid

if __package__ in (None, ""):
    sys.path.insert(0, str(Path(__file__).resolve().parent))

from download_video import (DownloadError, contained, digest, direct_download, find_reusable,
                            inspect_asset, now, safe_error, validate_video, write_new_json)
from inspect_video import find_ffmpeg, find_ffprobe
from fruitknife_provider import FruitknifeClient, QUALITIES, ROOT, UPSTREAM, parse_episodes


def ffmpeg_path(value=None):
    from fruitknife_runtime import activate_packages
    activate_packages()
    bundled = ROOT.parent / "runtime" / "ffmpeg" / ("ffmpeg.exe" if sys.platform == "win32" else "ffmpeg")
    return find_ffmpeg(value or (str(bundled) if bundled.is_file() else None))


def save_optional(path, data):
    if path:
        path = Path(path).expanduser().resolve()
        path.parent.mkdir(parents=True, exist_ok=True)
        # An explicit export may update its own JSON, never a downloaded video.
        temporary = path.with_name(path.name + "." + uuid.uuid4().hex[:8] + ".tmp")
        temporary.write_text(json.dumps(data, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
        temporary.replace(path)


def download_selection(source, out_dir, episodes=None, all_episodes=False, quality="1080p",
                       download_only=True, ffmpeg=None, ffprobe=None, timeout=30,
                       interval=1.0, timestamps=None, client=None):
    if quality not in QUALITIES or timeout <= 0 or interval <= 0:
        raise DownloadError("Quality, timeout or inspection interval is invalid")
    client = client or FruitknifeClient(timeout)
    series = client.inspect(source)
    selected = parse_episodes(episodes, series["total_episodes"], all_episodes)
    ffmpeg = ffmpeg_path(ffmpeg)
    ffprobe = find_ffprobe(ffprobe, ffmpeg)
    requested = Path(out_dir).expanduser().resolve()
    directory = contained(requested / ("hongguo-" + series["series_id"]) / quality, requested)
    directory.mkdir(parents=True, exist_ok=True)
    batch_id = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ") + "-" + uuid.uuid4().hex[:8]
    # Do not retain share-page transport URLs or transient playback materials.
    provenance = {key: series[key] for key in ("provider", "series_id", "title", "source_url", "total_episodes", "episodes", "upstream_commit")}
    catalog = directory / ("series-" + batch_id + ".json")
    write_new_json(catalog, provenance)
    results = []
    for number in selected:
        episode = series["episodes"][number - 1]
        video_id = episode["video_id"]
        identity = {"provider": "hongguo", "series_id": series["series_id"], "video_id": video_id,
                    "episode": number, "requested_quality": quality}
        request_hash = hashlib.sha256(json.dumps(identity, sort_keys=True).encode()).hexdigest()
        episode_dir = contained(directory / (f"episode-{number:04d}-" + request_hash[:8]), directory)
        episode_dir.mkdir(exist_ok=True)
        manifest_path, manifest = find_reusable(episode_dir, request_hash)
        reused = manifest is not None
        run_dir = manifest_path.parent if reused else episode_dir / ("video-" + batch_id + "-" + uuid.uuid4().hex[:6])
        if not reused:
            run_dir.mkdir(exist_ok=False)
        try:
            if reused:
                video = contained(manifest["output_path"], run_dir)
            else:
                write_new_json(run_dir / "request.json", {**identity, "created_at": now(), "request_sha256": request_hash,
                                                           "upstream_commit": UPSTREAM})
                media = client.resolve_episode(video_id, quality)
                partial, video = run_dir / "source.download.part", run_dir / "source.mp4"
                transport = direct_download(media["url"], partial, "https://novel.snssdk.com/", timeout)
                key = media.get("key")
                if key:
                    # The source API supplied this playback key. Only local bytes
                    # reach FFmpeg; no external licence or alternate account is used.
                    command = [ffmpeg, "-nostdin", "-hide_banner", "-loglevel", "error", "-n",
                               "-decryption_key", key.hex(), "-protocol_whitelist", "file,pipe", "-i", str(partial),
                               "-map", "0:v:0", "-map", "0:a?", "-c", "copy", "-movflags", "+faststart", str(video)]
                    process = subprocess.run(command, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                                             text=True, encoding="utf-8", errors="replace", timeout=600, shell=False)
                    if process.returncode:
                        raise DownloadError("Playback preparation failed: " + safe_error(process.stderr.replace(key.hex(), "[key withheld]")))
                else:
                    partial.rename(video)
                info = validate_video(video, ffmpeg, ffprobe, media.get("reported_duration_seconds"))
                manifest = {**identity, "schema_version": 1, "status": "downloaded", "created_at": now(),
                            "title": f"{series['title']} 第{number}集", "source_url": series["source_url"],
                            "request_sha256": request_hash, "sha256": digest(video), "bytes": video.stat().st_size,
                            "download_mode": "fruitknife_hongguo", "output_path": str(video), "metadata": info,
                            "requested_quality": quality, "actual_quality": media["actual_quality"],
                            "quality_fallback": media["actual_quality"] != quality,
                            "transport": transport, "upstream_commit": UPSTREAM}
                manifest_path = run_dir / "manifest.json"
                write_new_json(manifest_path, manifest)
                if partial.exists():
                    partial.unlink()
            inspection = None if download_only else inspect_asset(video, run_dir, ffmpeg, ffprobe,
                                          SimpleNamespace(interval=interval, timestamps=timestamps))
            result = {"episode": number, "video_id": video_id, "status": "reused" if reused else "downloaded",
                      "video": str(video), "manifest": str(manifest_path), "sha256": manifest["sha256"],
                      "metadata": manifest["metadata"], "requested_quality": quality,
                      "actual_quality": manifest.get("actual_quality"), "quality_fallback": manifest.get("quality_fallback", False),
                      "analysis": inspection.get("analysis") if inspection else None, "inspection": inspection}
        except (OSError, ValueError, KeyError, TypeError, subprocess.SubprocessError) as exc:
            result = {"episode": number, "video_id": video_id, "status": "failed",
                      "error": safe_error(exc), "output_directory": str(run_dir)}
            write_new_json(run_dir / ("failure-" + uuid.uuid4().hex[:8] + ".json"), {**result, "created_at": now()})
        results.append(result)
        with (directory / "progress.jsonl").open("a", encoding="utf-8") as stream:
            stream.write(json.dumps({"batch_id": batch_id, "at": now(), **result}, ensure_ascii=False) + "\n")
        print(json.dumps({"episode": number, "status": result["status"], "selected_total": len(selected)}, ensure_ascii=False), file=sys.stderr)
    good = [item for item in results if item["status"] in ("downloaded", "reused")]
    batch_path = directory / ("batch-" + batch_id + ".json")
    status = "partial_failure" if len(good) != len(selected) else "reused" if all(item["status"] == "reused" for item in good) else "downloaded"
    summary = {"status": status, "provider": "hongguo",
               "series_id": series["series_id"], "title": series["title"], "source_total_episodes": series["total_episodes"],
               "selected_episodes": selected, "completed_episodes": len(good), "failed_episodes": len(results) - len(good),
               "selection_complete": len(good) == len(selected), "catalog": str(catalog), "manifest": str(batch_path),
               "episodes": results, "upstream_commit": UPSTREAM}
    # Preserve the normal single-video handoff fields for one-episode requests.
    if len(results) == 1 and good:
        summary.update({key: good[0][key] for key in ("video", "metadata", "analysis", "inspection")})
    write_new_json(batch_path, summary)
    return summary


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    commands = parser.add_subparsers(dest="command", required=True)
    search = commands.add_parser("search", help="Search a drama name; does not download")
    search.add_argument("--keyword", required=True)
    search.add_argument("--cursor", default="")
    search.add_argument("--out")
    search.add_argument("--timeout", type=int, default=30)
    for name in ("inspect", "download"):
        command = commands.add_parser(name)
        source = command.add_mutually_exclusive_group(required=True)
        source.add_argument("--source", help="Share text/link or series_id")
        source.add_argument("--source-file", help="UTF-8 source/share-text file")
        command.add_argument("--timeout", type=int, default=30)
        if name == "inspect":
            command.add_argument("--out")
        else:
            selection = command.add_mutually_exclusive_group(required=True)
            selection.add_argument("--episodes", help="1,3-5")
            selection.add_argument("--all-episodes", action="store_true")
            command.add_argument("--quality", choices=QUALITIES, default="1080p")
            command.add_argument("--out-dir", required=True)
            command.add_argument("--out", help="Optional stable batch summary JSON for later transcription")
            command.add_argument("--ffmpeg")
            command.add_argument("--ffprobe")
            command.add_argument("--inspect", action="store_true", help="Also extract timestamped frames/audio for selected episodes")
            command.add_argument("--interval", type=float, default=1.0)
            command.add_argument("--timestamps")
    args = parser.parse_args()
    try:
        if args.timeout <= 0:
            raise DownloadError("Timeout must be positive")
        client = FruitknifeClient(args.timeout)
        if args.command == "search":
            result = client.search(args.keyword, args.cursor)
        else:
            source = Path(args.source_file).read_text(encoding="utf-8-sig") if args.source_file else args.source
            if args.command == "inspect":
                result = {"status": "series_ready", **client.inspect(source)}
            else:
                result = download_selection(source, args.out_dir, args.episodes, args.all_episodes,
                                            args.quality, not args.inspect, args.ffmpeg, args.ffprobe,
                                            args.timeout, args.interval, args.timestamps, client)
        if getattr(args, "out", None):
            save_optional(args.out, result)
        print(json.dumps(result, ensure_ascii=False, indent=2))
        return 0 if result.get("status") != "partial_failure" else 2
    except KeyboardInterrupt:
        print(json.dumps({"status": "interrupted", "resume": "Repeat the same command; verified episodes will be reused"}), file=sys.stderr)
        return 130
    except (OSError, ValueError, KeyError, TypeError, subprocess.SubprocessError) as exc:
        error = {"status": "failed", "provider": "hongguo", "error": safe_error(exc)}
        if getattr(args, "out", None):
            save_optional(args.out, error)
        print(json.dumps(error, ensure_ascii=False, indent=2), file=sys.stderr)
        return 2


if __name__ == "__main__":
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")
        sys.stderr.reconfigure(encoding="utf-8")
    sys.exit(main())
