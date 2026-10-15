#!/usr/bin/env python3
"""Export ordered per-card references and prompts. No network or generation."""

import argparse
from collections import Counter
from datetime import datetime, timezone
from decimal import Decimal, InvalidOperation, ROUND_HALF_UP
import hashlib
import json
from pathlib import Path
import re
import shutil
import uuid
import wave


ID = re.compile(r"[A-Za-z0-9][A-Za-z0-9_-]{0,63}\Z")
TOKEN = re.compile(r"\{\{asset:([^{}]+)\}\}")
PLATFORMS = ("generic", "shensi", "jimeng", "libtv")


def load_json(path):
    return json.loads(Path(path).read_text(encoding="utf-8-sig"))


def seconds(value):
    if isinstance(value, bool):
        raise ValueError("Duration must be a positive number")
    try:
        number = Decimal(str(value))
    except InvalidOperation as error:
        raise ValueError("Invalid duration") from error
    if not number.is_finite() or number <= 0:
        raise ValueError("Duration must be finite and positive")
    return number


def digest(path):
    result = hashlib.sha256()
    with path.open("rb") as handle:
        for block in iter(lambda: handle.read(1024 * 1024), b""):
            result.update(block)
    return result.hexdigest()


def dump(path, value):
    path.write_text(json.dumps(value, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def validate(plan_path, max_duration=30):
    plan_path = Path(plan_path).resolve()
    plan = load_json(plan_path)
    if not isinstance(plan, dict) or plan.get("schema") != 1 or not isinstance(plan.get("assets"), list):
        raise ValueError("Expected schema 1 and assets array")
    if not isinstance(plan.get("segments"), list) or not plan["segments"]:
        raise ValueError("At least one segment is required")
    assets = {}
    for item in plan["assets"]:
        if not isinstance(item, dict):
            raise ValueError("Each asset must be an object")
        aid = item.get("id", "")
        if not isinstance(aid, str) or not ID.fullmatch(aid) or aid in assets:
            raise ValueError("Asset IDs must be valid and unique")
        kind = item.get("kind")
        if kind not in ("image", "audio", "video"):
            raise ValueError("Asset kind must be image, audio or video")
        raw = item.get("path")
        if not isinstance(raw, str) or not raw:
            raise ValueError("Every asset needs an explicit local path")
        path = Path(raw).expanduser()
        path = (plan_path.parent / path).resolve() if not path.is_absolute() else path.resolve()
        if not path.is_file() or path.stat().st_size == 0:
            raise ValueError("Missing or empty asset: " + aid)
        data = {"id": aid, "kind": kind, "path": path, "sha256": digest(path),
                "bytes": path.stat().st_size}
        if kind in ("audio", "video"):
            duration = seconds(item.get("duration_seconds"))
            data.update(duration_seconds=float(duration), duration_verification="plan-provided")
            if kind == "audio" and path.suffix.lower() == ".wav":
                with wave.open(str(path), "rb") as audio:
                    measured = Decimal(audio.getnframes()) / audio.getframerate()
                if abs(measured - duration) > Decimal("0.001"):
                    raise ValueError("WAV duration does not match plan: " + aid)
                data.update(duration_seconds=float(measured), duration_verification="wav-header")
        assets[aid] = data
    ids = set()
    for segment in plan["segments"]:
        if not isinstance(segment, dict):
            raise ValueError("Each segment must be an object")
        sid = segment.get("id", "")
        if not isinstance(sid, str) or not ID.fullmatch(sid) or sid in ids:
            raise ValueError("Segment IDs must be valid and unique")
        ids.add(sid)
        duration = seconds(segment.get("duration_seconds"))
        if duration > seconds(max_duration):
            raise ValueError("Segment exceeds selected duration limit: " + sid)
        if int(duration.to_integral_value(rounding=ROUND_HALF_UP)) < 1:
            raise ValueError("Segment rounds to zero seconds: " + sid)
        if not re.fullmatch(r"[1-9]\d*:[1-9]\d*", str(segment.get("aspect_ratio", ""))):
            raise ValueError("Explicit positive aspect_ratio is required: " + sid)
        refs = segment.get("refs")
        if not isinstance(refs, list) or any(not isinstance(x, str) for x in refs):
            raise ValueError("refs must be an ordered array of asset IDs")
        if len(refs) != len(set(refs)) or any(aid not in assets for aid in refs):
            raise ValueError("Unknown or duplicate reference: " + sid)
        prompt = segment.get("prompt")
        if not isinstance(prompt, str) or not prompt.strip():
            raise ValueError("A full prompt is required: " + sid)
        if set(TOKEN.findall(prompt)) != set(refs):
            raise ValueError("Prompt asset markers must match declared refs: " + sid)
    return plan, assets


def export_bundle(plan_path, out_dir, platform="generic", bindings=None, max_duration=30):
    if platform not in PLATFORMS:
        raise ValueError("Unsupported platform")
    plan, assets = validate(plan_path, max_duration)
    bindings = bindings or {}
    if not isinstance(bindings, dict) or any(
            aid not in assets or not isinstance(key, str) or not ID.fullmatch(key)
            for aid, key in bindings.items()):
        raise ValueError("Bindings must map known assets to actual node keys")
    used = {aid for segment in plan["segments"] for aid in segment["refs"]}
    if platform == "libtv" and bindings and not used.issubset(bindings):
        raise ValueError("Incomplete LibTV bindings; supply all references or none")
    # Unique export directories preserve every previous version.
    root = Path(out_dir).resolve() / ("chushou-" + datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
                                    + "-" + uuid.uuid4().hex[:8])
    root.mkdir(parents=True, exist_ok=False)
    manifest = {"schema": 1, "skill": "chushou", "platform": platform,
                "status": "prepared", "generation_submitted": False,
                "platform_readback_verified": False, "segments": []}
    for segment in plan["segments"]:
        folder = root / segment["id"]
        media = folder / "media"
        media.mkdir(parents=True)
        labels, refs, counts = {}, [], Counter()
        for order, aid in enumerate(segment["refs"], 1):
            asset = assets[aid]
            kind = asset["kind"]
            counts[kind] += 1
            chinese = {"image": "图片", "video": "视频", "audio": "音频"}[kind]
            label = chinese + str(counts[kind])
            if platform == "shensi":
                token = "@「" + label + "」"
            elif platform == "jimeng":
                token = "@" + label
            elif platform == "libtv" and bindings:
                token = "{{Node " + bindings[aid] + "}}"
            else:
                token = "{{asset:" + aid + "}}"
            labels[aid] = token
            suffix = asset["path"].suffix.lower()
            suffix = suffix if re.fullmatch(r"\.[a-z0-9]{1,10}", suffix) else ".bin"
            target = media / (f"{order:02d}_" + aid + suffix)
            shutil.copyfile(asset["path"], target)
            if digest(target) != asset["sha256"]:
                raise ValueError("Copied reference checksum mismatch: " + aid)
            record = {key: value for key, value in asset.items() if key != "path"}
            record.update(order=order, type_index=counts[kind], label=label,
                          token=token, file=target.relative_to(root).as_posix(),
                          binding_status="pending-platform-readback")
            if aid in bindings:
                record["supplied_node_key"] = bindings[aid]
            refs.append(record)
        sequence = TOKEN.findall(segment["prompt"])
        compiled = TOKEN.sub(lambda match: labels[match[1]], segment["prompt"])
        (folder / "prompt.txt").write_text(compiled, encoding="utf-8")
        (folder / "source-prompt.txt").write_text(segment["prompt"], encoding="utf-8")
        metadata = {key: segment[key] for key in ("id", "title", "aspect_ratio", "resolution",
                    "model", "revision_of", "duration_seconds") if key in segment}
        metadata.update(status="prepared", generation_submitted=False,
                        composer_duration_seconds=int(seconds(segment["duration_seconds"])
                            .to_integral_value(rounding=ROUND_HALF_UP)),
                        references=refs, prompt_reference_sequence=sequence,
                        prompt_sha256=hashlib.sha256(compiled.encode("utf-8")).hexdigest(),
                        native_binding_verified=False)
        dump(folder / "references.json", metadata)
        manifest["segments"].append({"id": segment["id"], "metadata": segment["id"]
                                     + "/references.json", "prompt": segment["id"] + "/prompt.txt"})
    dump(root / "manifest.json", manifest)
    return root


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--plan", required=True)
    parser.add_argument("--out-dir", required=True)
    parser.add_argument("--platform", choices=PLATFORMS, default="generic")
    parser.add_argument("--bindings", help="JSON mapping asset IDs to actual platform node keys")
    parser.add_argument("--max-duration", type=float, default=30,
                        help="Use a different limit only after checking the target model")
    args = parser.parse_args()
    try:
        root = export_bundle(args.plan, args.out_dir, args.platform,
                             load_json(args.bindings) if args.bindings else None, args.max_duration)
    except (ValueError, TypeError, OSError, wave.Error) as error:
        parser.exit(2, "Bundle preparation failed: " + str(error) + "\n")
    print(json.dumps({"status": "prepared", "directory": str(root),
                      "generation_submitted": False}, ensure_ascii=False))


if __name__ == "__main__":
    main()
