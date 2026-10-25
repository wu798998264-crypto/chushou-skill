#!/usr/bin/env python3
"""Export prompts, ordered model refs and optional analysis provenance. No network or generation."""

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
# Native keys are opaque platform identifiers, not our portable asset IDs.  Reject
# token delimiters and whitespace so the key cannot escape a {{Node ...}} token.
NODE_KEY = re.compile(r"[^\s{}\x00-\x1f\x7f]+\Z")
WINDOWS_RESERVED = {"con", "prn", "aux", "nul"} | {
    prefix + str(index) for prefix in ("com", "lpt") for index in range(1, 10)}
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


def validate_provenance(plan, assets):
    """Validate an optional analysis graph without changing generation refs."""
    if "provenance" not in plan:
        return None
    graph = plan["provenance"]
    if not isinstance(graph, dict):
        raise ValueError("provenance must be an object")
    sources = graph.get("source_videos")
    frames = graph.get("frames")
    edges = graph.get("edges")
    if (not isinstance(sources, list) or not sources
            or any(not isinstance(aid, str) or aid not in assets
                   or assets[aid]["kind"] != "video" for aid in sources)
            or len(sources) != len(set(sources))):
        raise ValueError("provenance source_videos must be unique known video asset IDs")
    if not isinstance(frames, list) or not frames:
        raise ValueError("provenance requires timestamped source frames")
    frame_records = {}
    for frame in frames:
        if not isinstance(frame, dict):
            raise ValueError("Each provenance frame must be an object")
        aid, source = frame.get("asset_id"), frame.get("source_video_id")
        if (not isinstance(aid, str) or aid not in assets or assets[aid]["kind"] != "image"
                or aid in frame_records or source not in sources):
            raise ValueError("Each provenance frame needs a unique image and known source video")
        value = frame.get("timestamp_seconds")
        try:
            timestamp = Decimal(str(value))
        except InvalidOperation as error:
            raise ValueError("Invalid provenance frame timestamp") from error
        if (isinstance(value, bool) or not timestamp.is_finite() or timestamp < 0
                or timestamp >= Decimal(str(assets[source]["duration_seconds"]))):
            raise ValueError("Frame timestamp must be within the source video: " + aid)
        frame_records[aid] = {"asset_id": aid, "source_video_id": source,
                              "timestamp_seconds": float(timestamp),
                              "source_sha256": assets[source]["sha256"]}
    frame_hashes = {assets[aid]["sha256"] for aid in frame_records}
    if any(assets[aid]["sha256"] in frame_hashes
           for segment in plan["segments"] for aid in segment["refs"]):
        raise ValueError("Source frames are analysis-only and cannot be generation references")
    if not isinstance(edges, list) or not edges:
        raise ValueError("provenance requires explicit analysis edges")
    segment_nodes = {"segment:" + segment["id"] for segment in plan["segments"]}
    known = {"asset:" + aid for aid in assets} | segment_nodes
    source_nodes = {"asset:" + aid for aid in sources}
    frame_nodes = {"asset:" + aid for aid in frame_records}
    nodes = source_nodes | frame_nodes | segment_nodes
    pairs = set()
    for edge in edges:
        if not isinstance(edge, dict):
            raise ValueError("Each provenance edge must be an object")
        start, end = edge.get("from"), edge.get("to")
        if (not isinstance(start, str) or not isinstance(end, str)
                or start not in known or end not in known or start == end
                or (start, end) in pairs):
            raise ValueError("Unknown, duplicate or self-referencing provenance edge")
        if start in segment_nodes or end in source_nodes:
            raise ValueError("Original videos must be roots and segments must be sinks")
        if start in source_nodes or end in frame_nodes:
            expected_source = frame_records.get(end.removeprefix("asset:"), {}).get("source_video_id")
            if start != "asset:" + str(expected_source):
                raise ValueError("An original video must connect only to its own source frames")
        pairs.add((start, end))
        nodes.update((start, end))
    required = {("asset:" + frame["source_video_id"], "asset:" + aid)
                for aid, frame in frame_records.items()}
    if not required.issubset(pairs):
        raise ValueError("Every source frame requires its original-video edge")
    if not all(any(start == source for start, _ in pairs) for source in source_nodes):
        raise ValueError("Every original video requires a source frame")
    outgoing = {node: [] for node in nodes}
    indegree = dict.fromkeys(nodes, 0)
    for start, end in pairs:
        outgoing[start].append(end)
        indegree[end] += 1
    pending = [node for node in nodes if indegree[node] == 0]
    visited = set()
    while pending:
        node = pending.pop()
        visited.add(node)
        for child in outgoing[node]:
            indegree[child] -= 1
            if indegree[child] == 0:
                pending.append(child)
    if len(visited) != len(nodes):
        raise ValueError("provenance must not contain cycles")
    reached, pending = set(source_nodes), list(source_nodes)
    while pending:
        for child in outgoing[pending.pop()]:
            if child not in reached:
                reached.add(child)
                pending.append(child)
    if reached != nodes:
        raise ValueError("Every provenance node and segment must be reachable from an original video")
    return {"source_videos": sources, "frames": list(frame_records.values()),
            "asset_ids": [aid for aid in assets if "asset:" + aid in nodes],
            "edges": [{"from": edge["from"], "to": edge["to"],
                       "fromSide": "right", "toSide": "left",
                       "binding_status": "pending-platform-readback"} for edge in edges]}


def validate(plan_path, max_duration=30):
    plan_path = Path(plan_path).resolve()
    plan = load_json(plan_path)
    if not isinstance(plan, dict) or plan.get("schema") != 1 or not isinstance(plan.get("assets"), list):
        raise ValueError("Expected schema 1 and assets array")
    if not isinstance(plan.get("segments"), list) or not plan["segments"]:
        raise ValueError("At least one segment is required")
    assets, kinds_by_hash = {}, {}
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
        if data["sha256"] in kinds_by_hash and kinds_by_hash[data["sha256"]] != kind:
            raise ValueError("Identical media cannot have conflicting asset kinds: " + aid)
        kinds_by_hash[data["sha256"]] = kind
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
        if (not isinstance(sid, str) or not ID.fullmatch(sid)
                or sid.casefold() in ids or sid.casefold() in WINDOWS_RESERVED
                or ("provenance" in plan and sid.casefold() == "provenance")):
            raise ValueError("Segment IDs must be portable, valid and unique ignoring case")
        ids.add(sid.casefold())
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
        if len({assets[aid]["sha256"] for aid in refs}) != len(refs):
            raise ValueError("Duplicate media in one segment; reuse the same asset ID: " + sid)
        prompt = segment.get("prompt")
        if not isinstance(prompt, str) or not prompt.strip():
            raise ValueError("A full prompt is required: " + sid)
        if set(TOKEN.findall(prompt)) != set(refs):
            raise ValueError("Prompt asset markers must match declared refs: " + sid)
    validate_provenance(plan, assets)
    return plan, assets


def export_bundle(plan_path, out_dir, platform="generic", bindings=None, max_duration=30):
    if platform not in PLATFORMS:
        raise ValueError("Unsupported platform")
    plan, assets = validate(plan_path, max_duration)
    provenance = validate_provenance(plan, assets)
    bindings = {} if bindings is None else bindings
    if not isinstance(bindings, dict) or any(
            aid not in assets or not isinstance(key, str) or not NODE_KEY.fullmatch(key)
            for aid, key in bindings.items()):
        raise ValueError("Bindings must map known assets to actual node keys")
    identities_by_key = {}
    keys_by_identity = {}
    for aid, key in bindings.items():
        identity = assets[aid]["sha256"]
        if key in identities_by_key and identities_by_key[key] != identity:
            raise ValueError("A native node key cannot bind different media")
        if identity in keys_by_identity and keys_by_identity[identity] != key:
            raise ValueError("Identical media must reuse one native node key")
        identities_by_key[key], keys_by_identity[identity] = identity, key
    used = {aid for segment in plan["segments"] for aid in segment["refs"]}
    if platform == "libtv" and bindings and not used.issubset(bindings):
        raise ValueError("Incomplete LibTV bindings; supply all references or none")
    # Unique export directories preserve every previous version.
    root = Path(out_dir).resolve() / ("chushou-" + datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
                                    + "-" + uuid.uuid4().hex[:8])
    root.mkdir(parents=True, exist_ok=False)
    manifest = {"schema": 1, "skill": "chushou", "platform": platform,
                "status": "prepared", "generation_submitted": False,
                "platform_readback_verified": False, "segments": [], "assets": []}
    exported_media, exported_assets = {}, {}
    needed_ids = [aid for segment in plan["segments"] for aid in segment["refs"]]
    if provenance is not None:
        needed_ids = provenance["source_videos"] + needed_ids + provenance["asset_ids"]
    for aid in dict.fromkeys(needed_ids):
        asset = assets[aid]
        if asset["sha256"] not in exported_media:
            suffix = asset["path"].suffix.lower()
            suffix = suffix if re.fullmatch(r"\.[a-z0-9]{1,10}", suffix) else ".bin"
            target = root / "_media" / (asset["sha256"] + suffix)
            target.parent.mkdir(exist_ok=True)
            shutil.copyfile(asset["path"], target)
            if digest(target) != asset["sha256"]:
                raise ValueError("Copied reference checksum mismatch: " + aid)
            exported_media[asset["sha256"]] = target.relative_to(root).as_posix()
        record = {key: value for key, value in asset.items() if key != "path"}
        record.update(file=exported_media[asset["sha256"]],
                      binding_status="pending-platform-readback")
        if aid in bindings:
            record["supplied_node_key"] = bindings[aid]
        exported_assets[aid] = record
        manifest["assets"].append(record.copy())
    for segment in plan["segments"]:
        folder = root / segment["id"]
        folder.mkdir()
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
            record = exported_assets[aid].copy()
            record.update(order=order, type_index=counts[kind], label=label,
                          token=token)
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
    if provenance is not None:
        folder = root / "provenance"
        folder.mkdir()
        records = []
        frames = {frame["asset_id"]: frame for frame in provenance["frames"]}
        ordered_ids = list(dict.fromkeys(provenance["source_videos"] + provenance["asset_ids"]))
        for aid in ordered_ids:
            record = exported_assets[aid].copy()
            record.update(analysis_only=True,
                          role="original-video" if aid in provenance["source_videos"]
                          else "source-frame" if aid in frames else "asset")
            if aid in frames:
                record.update(frames[aid])
            records.append(record)
        graph = {"schema": 1, "status": "prepared", "analysis_only": True,
                 "timestamp_unit": "seconds", "native_binding_verified": False,
                 "source_videos": provenance["source_videos"], "assets": records,
                 "segments": [segment["id"] for segment in plan["segments"]],
                 "edges": provenance["edges"]}
        dump(folder / "graph.json", graph)
        manifest["provenance"] = {"graph": "provenance/graph.json", "status": "prepared",
                                  "native_binding_verified": False, "analysis_only": True}
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
