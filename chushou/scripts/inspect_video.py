#!/usr/bin/env python3
"""Read a local video and make timestamped review frames and optional audio.

No transcription or cut detection is performed. Review suspected cuts with
--timestamps containing closely spaced values. Source media is never modified.
"""

import argparse
import datetime as dt
import json
import math
import re
import shutil
import subprocess
import sys
from pathlib import Path

if __package__ in (None, ""):
    sys.path.insert(0, str(Path(__file__).resolve().parent))

from fruitknife_runtime import activate_packages

activate_packages()


def run(args):
    return subprocess.run(args, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                          text=True, encoding="utf-8", errors="replace", shell=False)


def executable(value):
    if not value:
        return None
    path = Path(value).expanduser()
    if path.is_file():
        return str(path.resolve())
    return shutil.which(value)


def find_ffmpeg(value):
    if value:
        found = executable(value)
        if not found:
            raise ValueError("--ffmpeg executable was not found")
        return found
    found = shutil.which("ffmpeg")
    if found:
        return found
    try:
        import imageio_ffmpeg
        return imageio_ffmpeg.get_ffmpeg_exe()
    except (ImportError, RuntimeError):
        pass
    # Optional compatibility for existing users; a fresh installation uses
    # fruitknife_runtime's own dependencies and bundled FFmpeg above.
    # This keeps existing users working without copying an 80+ MB executable.
    skill_root = Path(__file__).resolve().parent.parent
    binary = "ffmpeg.exe" if sys.platform == "win32" else "ffmpeg"
    for runtime in (skill_root / "runtime" / "ffmpeg",
                    skill_root.parent / "daozei-de-jiyi" / "runtime" / "ffmpeg"):
        candidate = runtime / binary
        if candidate.is_file():
            return str(candidate)
    try:
        import imageio_ffmpeg
        return imageio_ffmpeg.get_ffmpeg_exe()
    except (ImportError, RuntimeError):
        raise ValueError("FFmpeg was not found; supply --ffmpeg PATH or run python scripts/fruitknife_runtime.py --install")


def find_ffprobe(value, ffmpeg):
    if value:
        found = executable(value)
        if not found:
            raise ValueError("--ffprobe executable was not found")
        return found
    found = shutil.which("ffprobe")
    if found:
        return found
    sibling = Path(ffmpeg).with_name("ffprobe.exe" if sys.platform == "win32" else "ffprobe")
    return str(sibling) if sibling.is_file() else None


def number(value):
    try:
        if "/" in str(value):
            numerator, denominator = str(value).split("/", 1)
            value = float(numerator) / float(denominator)
        value = float(value)
        return value if math.isfinite(value) else None
    except (ValueError, TypeError, ZeroDivisionError):
        return None


def metadata(video, ffmpeg, ffprobe):
    if ffprobe:
        result = run([ffprobe, "-v", "error", "-protocol_whitelist", "file,pipe", "-show_format", "-show_streams",
                      "-of", "json", str(video)])
        if result.returncode == 0:
            try:
                info = json.loads(result.stdout)
                streams = info.get("streams", [])
                stream = next((s for s in streams if s.get("codec_type") == "video"
                               and not s.get("disposition", {}).get("attached_pic")), {})
                if not stream:
                    raise ValueError("No moving-video stream was found; audio and attached cover art are not video")
                return {
                    "duration_seconds": number(info.get("format", {}).get("duration"))
                                        or number(stream.get("duration")),
                    "width": stream.get("width"), "height": stream.get("height"),
                    "fps": number(stream.get("avg_frame_rate")) or number(stream.get("r_frame_rate")),
                    "video_codec": stream.get("codec_name"),
                    "video_stream_index": stream.get("index"),
                    "has_audio": any(s.get("codec_type") == "audio" for s in streams),
                    "method": "ffprobe_json",
                }
            except (ValueError, TypeError):
                pass
    result = run([ffmpeg, "-nostdin", "-hide_banner", "-protocol_whitelist", "file,pipe", "-i", str(video)])
    log = result.stderr
    duration = re.search(r"Duration:\s*(\d+):(\d+):([\d.]+)", log)
    video_line = next((line for line in log.splitlines() if "Video:" in line
                       and "attached pic" not in line.lower()), "")
    dimensions = re.search(r"(?<!\d)(\d{2,6})x(\d{2,6})(?!\d)", video_line)
    fps = re.search(r"([\d.]+)\s+fps\b", video_line)
    codec = re.search(r"Video:\s*([^,\s]+)", video_line)
    stream_index = re.search(r"Stream #0:(\d+)", video_line)
    if not video_line:
        raise ValueError("Could not read a video stream: " + log[-1500:])
    return {
        "duration_seconds": (int(duration[1]) * 3600 + int(duration[2]) * 60 + float(duration[3]))
                            if duration else None,
        "width": int(dimensions[1]) if dimensions else None,
        "height": int(dimensions[2]) if dimensions else None,
        "fps": float(fps[1]) if fps else None,
        "video_codec": codec[1] if codec else None,
        "video_stream_index": int(stream_index[1]) if stream_index else None,
        "has_audio": "Audio:" in log,
        "method": "ffmpeg_stderr_fallback",
    }


def output_directory(requested):
    root = Path(requested).expanduser().resolve()
    if not root.exists():
        root.mkdir(parents=True, exist_ok=False)
        return root
    if not root.is_dir():
        raise ValueError("--out-dir must be a directory")
    if not any(root.iterdir()):
        return root
    name = "run-" + dt.datetime.now(dt.timezone.utc).strftime("%Y%m%dT%H%M%S%fZ")
    candidate = root / name
    counter = 1
    while candidate.exists():
        candidate = root / (name + "-" + str(counter))
        counter += 1
    candidate.mkdir(exist_ok=False)
    return candidate


def time_label(seconds):
    minutes, remainder = divmod(seconds, 60)
    return f"{int(minutes):02d}:{remainder:06.3f}"


def contact_sheets(frames, out):
    try:
        from PIL import Image, ImageDraw
    except ImportError:
        return [], "Pillow unavailable; individual JPEG frames remain available."
    paths = []
    tile_w, tile_h, caption_h, columns, per_page = 320, 200, 24, 4, 20
    for page, start in enumerate(range(0, len(frames), per_page), 1):
        chunk = frames[start:start + per_page]
        rows = math.ceil(len(chunk) / columns)
        sheet = Image.new("RGB", (tile_w * columns, (tile_h + caption_h) * rows), "#202020")
        draw = ImageDraw.Draw(sheet)
        for position, frame in enumerate(chunk):
            x = (position % columns) * tile_w
            y = (position // columns) * (tile_h + caption_h)
            with Image.open(frame["path"]) as source:
                thumb = source.convert("RGB")
                thumb.thumbnail((tile_w, tile_h))
                sheet.paste(thumb, (x + (tile_w - thumb.width) // 2,
                                    y + (tile_h - thumb.height) // 2))
            draw.text((x + 8, y + tile_h + 4), time_label(frame["requested_timestamp_seconds"]), fill="white")
        target = out / f"contact-sheet-{page:03d}.jpg"
        with target.open("xb") as handle:
            sheet.save(handle, format="JPEG", quality=90)
        paths.append(str(target))
    return paths, None


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--video", required=True)
    parser.add_argument("--out-dir", required=True)
    parser.add_argument("--ffmpeg")
    parser.add_argument("--ffprobe")
    parser.add_argument("--interval", type=float, default=1.0)
    parser.add_argument("--timestamps", help="Comma-separated seconds; replaces interval sampling")
    parser.add_argument("--no-audio", action="store_true")
    args = parser.parse_args()
    try:
        video = Path(args.video).expanduser().resolve()
        if not video.is_file():
            raise ValueError("Source video was not found")
        if not math.isfinite(args.interval) or args.interval <= 0:
            raise ValueError("--interval must be a finite positive number")
        ffmpeg = find_ffmpeg(args.ffmpeg)
        ffprobe = find_ffprobe(args.ffprobe, ffmpeg)
        info = metadata(video, ffmpeg, ffprobe)
        duration = info["duration_seconds"]
        if args.timestamps is not None:
            timestamps = sorted(set(float(item.strip()) for item in args.timestamps.split(",")))
            if any(not math.isfinite(t) or t < 0 for t in timestamps):
                raise ValueError("--timestamps must contain finite, nonnegative seconds")
            if duration is not None and any(t >= duration for t in timestamps):
                raise ValueError("Each timestamp must be before the end of the video")
            sampling = {"method": "explicit_timestamps", "timestamps_seconds": timestamps}
        else:
            if duration is None or duration <= 0:
                raise ValueError("Duration unavailable; supply --timestamps for explicit sampling")
            timestamps = [i * args.interval for i in range(math.ceil(duration / args.interval))
                          if i * args.interval < duration]
            sampling = {"method": "interval", "interval_seconds": args.interval}
        out = output_directory(args.out_dir)
        frames_dir = out / "frames"
        frames_dir.mkdir(exist_ok=False)
        frames, errors = [], []
        for index, timestamp in enumerate(timestamps, 1):
            target = frames_dir / f"frame-{index:04d}-{timestamp:010.3f}s.jpg"
            result = run([ffmpeg, "-nostdin", "-hide_banner", "-loglevel", "error", "-n",
                          "-ss", f"{timestamp:.6f}", "-protocol_whitelist", "file,pipe", "-i", str(video), "-map",
                          "0:" + str(info["video_stream_index"]) if info.get("video_stream_index") is not None else "0:v:0",
                          "-frames:v", "1", "-q:v", "2", str(target)])
            if result.returncode == 0 and target.is_file() and target.stat().st_size:
                frames.append({"requested_timestamp_seconds": timestamp, "path": str(target)})
            else:
                errors.append({"step": "frame", "timestamp_seconds": timestamp,
                               "message": result.stderr[-1500:] or "No frame was produced"})
        sheets, sheet_note = contact_sheets(frames, out)
        audio = {"status": "skipped_by_request" if args.no_audio else "no_audio_stream", "path": None}
        if not args.no_audio and info["has_audio"]:
            target = out / "audio.wav"
            result = run([ffmpeg, "-nostdin", "-hide_banner", "-loglevel", "error", "-n",
                          "-protocol_whitelist", "file,pipe", "-i", str(video), "-map", "0:a:0", "-vn", "-ac", "1", "-ar", "16000",
                          "-c:a", "pcm_s16le", str(target)])
            if result.returncode == 0 and target.is_file():
                audio = {"status": "extracted", "path": str(target),
                         "sample_rate_hz": 16000, "channels": 1, "encoding": "pcm_s16le"}
            else:
                audio["status"] = "failed"
                errors.append({"step": "audio", "message": result.stderr[-1500:]})
        analysis = {"source": str(video), "output_directory": str(out), "metadata": info,
                    "tools": {"ffmpeg": ffmpeg, "ffprobe": ffprobe}, "sampling": sampling,
                    "frames": frames, "contact_sheets": sheets, "contact_sheet_note": sheet_note,
                    "audio": audio, "errors": errors,
                    "notes": ["Timestamps are requested seek positions, not measured decoded frame PTS.",
                              "For precise cut boundaries, inspect nearby frames with closely spaced --timestamps.",
                              "No automatic dialogue transcription or hard-cut classification was performed."]}
        target = out / "analysis.json"
        with target.open("x", encoding="utf-8") as handle:
            json.dump(analysis, handle, ensure_ascii=False, indent=2)
            handle.write("\n")
        print(json.dumps({"analysis": str(target), "output_directory": str(out),
                          "metadata": info, "frames": len(frames), "contact_sheets": len(sheets),
                          "audio": audio, "errors": len(errors)}, ensure_ascii=False, indent=2))
        return 0 if frames and not errors else 1
    except (ValueError, OSError) as error:
        print(str(error), file=sys.stderr)
        return 2


if __name__ == "__main__":
    sys.exit(main())
