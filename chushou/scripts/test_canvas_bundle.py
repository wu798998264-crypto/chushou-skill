"""Offline checks of portable references; never upload or generate media."""
import copy
import json
from pathlib import Path
import tempfile
import unittest
import wave

import canvas_bundle as bundle


class BundleTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        (self.root / "hero.png").write_bytes(b"reference-image-test-fixture")
        with wave.open(str(self.root / "voice.wav"), "wb") as audio:
            audio.setparams((1, 2, 8000, 8000, "NONE", "not compressed"))
            audio.writeframes(b"\0\0" * 8000)
        self.prompt = "人{{asset:hero}}唱‘原词’。声音{{asset:voice}}，再看{{asset:hero}}。\n保持换行。"
        self.plan = {"schema": 1, "assets": [
            {"id": "hero", "kind": "image", "path": "hero.png"},
            {"id": "voice", "kind": "audio", "path": "voice.wav", "duration_seconds": 1}],
            "segments": [{"id": "S01", "title": "测试", "duration_seconds": 1,
                          "aspect_ratio": "16:9", "refs": ["voice", "hero"], "prompt": self.prompt}]}
        self.path = self.root / "plan.json"

    def tearDown(self):
        self.temp.cleanup()

    def write(self):
        self.path.write_text(json.dumps(self.plan, ensure_ascii=False), encoding="utf-8")

    def add_provenance(self):
        (self.root / "original.mp4").write_bytes(b"original-video-fixture")
        (self.root / "frame.png").write_bytes(b"extracted-frame-fixture")
        (self.root / "frame-alias.png").write_bytes(b"extracted-frame-fixture")
        self.plan["assets"].extend([
            {"id": "original", "kind": "video", "path": "original.mp4", "duration_seconds": 10},
            {"id": "frame01", "kind": "image", "path": "frame.png"},
            {"id": "frame02", "kind": "image", "path": "frame-alias.png"}])
        self.plan["provenance"] = {
            "source_videos": ["original"],
            "frames": [{"asset_id": "frame01", "source_video_id": "original", "timestamp_seconds": 0},
                       {"asset_id": "frame02", "source_video_id": "original", "timestamp_seconds": 9.96}],
            "edges": [{"from": "asset:original", "to": "asset:frame01"},
                      {"from": "asset:original", "to": "asset:frame02"},
                      {"from": "asset:frame01", "to": "asset:hero"},
                      {"from": "asset:frame02", "to": "segment:S01"},
                      {"from": "asset:hero", "to": "segment:S01"}]}

    def test_all_platforms_preserve_text_and_file_identity(self):
        self.write()
        for platform in bundle.PLATFORMS:
            bindings = {"hero": "real-image-key", "voice": "real-audio-key"} if platform == "libtv" else None
            root = bundle.export_bundle(self.path, self.root / "out", platform, bindings)
            metadata = bundle.load_json(root / "S01/references.json")
            refs = {item["id"]: item for item in metadata["references"]}
            expected = self.prompt.replace("{{asset:hero}}", refs["hero"]["token"])
            expected = expected.replace("{{asset:voice}}", refs["voice"]["token"])
            self.assertEqual((root / "S01/prompt.txt").read_text(encoding="utf-8"), expected)
            self.assertEqual(metadata["prompt_reference_sequence"], ["hero", "voice", "hero"])
            self.assertEqual(refs["hero"]["label"], "图片1")
            self.assertEqual(refs["voice"]["label"], "音频1")
            for aid, ref in refs.items():
                source = self.root / ("hero.png" if aid == "hero" else "voice.wav")
                self.assertEqual((root / ref["file"]).read_bytes(), source.read_bytes())
            self.assertFalse(metadata["native_binding_verified"])
            manifest = bundle.load_json(root / "manifest.json")
            self.assertFalse(manifest["generation_submitted"])
            self.assertNotIn("provenance", manifest)

    def test_invalid_assets_or_timing_leave_no_export(self):
        for mode in ("missing", "duration", "unknown-marker"):
            with self.subTest(mode=mode):
                if mode == "missing":
                    self.plan["assets"][0]["path"] = "missing.png"
                elif mode == "duration":
                    self.plan["assets"][0]["path"] = "hero.png"
                    self.plan["assets"][1]["duration_seconds"] = 2
                else:
                    self.plan["assets"][1]["duration_seconds"] = 1
                    self.plan["segments"][0]["prompt"] += "{{asset:unknown}}"
                self.write()
                with self.assertRaises(ValueError):
                    bundle.export_bundle(self.path, self.root / "out")
                self.assertFalse((self.root / "out").exists())

    def test_new_export_preserves_previous_version_and_source(self):
        self.write()
        first = bundle.export_bundle(self.path, self.root / "out")
        saved = (first / "S01/prompt.txt").read_bytes()
        self.plan["segments"][0]["prompt"] += "新版"
        self.write()
        second = bundle.export_bundle(self.path, self.root / "out")
        self.assertNotEqual(first, second)
        self.assertEqual((first / "S01/prompt.txt").read_bytes(), saved)
        self.assertEqual((self.root / "hero.png").read_bytes(), b"reference-image-test-fixture")

    def test_partial_or_unsafe_node_bindings_rejected(self):
        self.write()
        for bindings in ({"hero": "valid-key"}, {"hero": "{{injected}}", "voice": "valid"}):
            with self.assertRaises(ValueError):
                bundle.export_bundle(self.path, self.root / "out", "libtv", bindings)
        self.assertFalse((self.root / "out").exists())

    def test_provenance_exports_unreferenced_original_and_frames_without_model_inputs(self):
        self.add_provenance()
        self.write()
        root = bundle.export_bundle(self.path, self.root / "out", "shensi")
        manifest = bundle.load_json(root / "manifest.json")
        graph = bundle.load_json(root / manifest["provenance"]["graph"])
        records = {asset["id"]: asset for asset in graph["assets"]}
        self.assertEqual(graph["status"], "prepared")
        self.assertFalse(graph["native_binding_verified"])
        self.assertTrue(graph["analysis_only"])
        self.assertEqual(graph["timestamp_unit"], "seconds")
        self.assertEqual(records["original"]["role"], "original-video")
        self.assertEqual(records["frame01"]["timestamp_seconds"], 0)
        self.assertEqual(records["frame02"]["timestamp_seconds"], 9.96)
        self.assertEqual(records["frame01"]["source_sha256"], records["original"]["sha256"])
        for record in records.values():
            self.assertFalse(Path(record["file"]).is_absolute())
            self.assertEqual(bundle.digest(root / record["file"]), record["sha256"])
            self.assertTrue(record["analysis_only"])
        for edge in graph["edges"]:
            self.assertEqual((edge["fromSide"], edge["toSide"]), ("right", "left"))
            self.assertEqual(edge["binding_status"], "pending-platform-readback")
        metadata = bundle.load_json(root / "S01/references.json")
        self.assertEqual([item["id"] for item in metadata["references"]], ["voice", "hero"])
        self.assertEqual(metadata["prompt_reference_sequence"], ["hero", "voice", "hero"])
        self.assertNotIn("视频", (root / "S01/prompt.txt").read_text(encoding="utf-8"))
        # Identical frame aliases have distinct timestamp nodes but only one media file.
        self.assertEqual(records["frame01"]["file"], records["frame02"]["file"])
        self.assertEqual(len(list((root / "_media").iterdir())), 4)
        # An asset already copied for model refs is reused by the provenance graph.
        self.assertEqual(records["hero"]["file"], metadata["references"][1]["file"])

    def test_bad_provenance_rejected_before_creating_export(self):
        self.add_provenance()
        baseline = copy.deepcopy(self.plan)
        cases = [
            ("missing-source", lambda graph: graph["source_videos"].__setitem__(0, "missing")),
            ("source-not-video", lambda graph: graph["source_videos"].__setitem__(0, "hero")),
            ("duplicate-source", lambda graph: graph["source_videos"].append("original")),
            ("missing-frame", lambda graph: graph["frames"][0].__setitem__("asset_id", "missing")),
            ("frame-not-image", lambda graph: graph["frames"][0].__setitem__("asset_id", "voice")),
            ("wrong-frame-source", lambda graph: graph["frames"][0].__setitem__("source_video_id", "missing")),
            ("duplicate-frame", lambda graph: graph["frames"].append(copy.deepcopy(graph["frames"][0]))),
            ("negative-timestamp", lambda graph: graph["frames"][0].__setitem__("timestamp_seconds", -0.01)),
            ("end-timestamp", lambda graph: graph["frames"][0].__setitem__("timestamp_seconds", 10)),
            ("nonfinite-timestamp", lambda graph: graph["frames"][0].__setitem__("timestamp_seconds", "NaN")),
            ("bool-timestamp", lambda graph: graph["frames"][0].__setitem__("timestamp_seconds", False)),
            ("missing-source-edge", lambda graph: graph["edges"].pop(0)),
            ("unknown-target", lambda graph: graph["edges"].append({"from": "asset:hero", "to": "segment:unknown"})),
            ("duplicate-edge", lambda graph: graph["edges"].append(copy.deepcopy(graph["edges"][0]))),
            ("self-edge", lambda graph: graph["edges"].append({"from": "asset:hero", "to": "asset:hero"})),
            ("segment-not-sink", lambda graph: graph["edges"].append({"from": "segment:S01", "to": "asset:hero"})),
            ("original-not-root", lambda graph: graph["edges"].append({"from": "asset:hero", "to": "asset:original"})),
            ("original-bypasses-frame", lambda graph: graph["edges"].append({"from": "asset:original", "to": "segment:S01"})),
            ("frame-wrong-parent", lambda graph: graph["edges"].append({"from": "asset:hero", "to": "asset:frame01"})),
            ("unreachable-asset", lambda graph: graph["edges"].append({"from": "asset:voice", "to": "asset:hero"})),
            ("cycle", lambda graph: graph["edges"].extend([
                {"from": "asset:hero", "to": "asset:voice"},
                {"from": "asset:voice", "to": "asset:hero"}]))]
        for name, change in cases:
            with self.subTest(name=name):
                self.plan = copy.deepcopy(baseline)
                change(self.plan["provenance"])
                self.write()
                with self.assertRaises(ValueError):
                    bundle.export_bundle(self.path, self.root / "out")
                self.assertFalse((self.root / "out").exists())

    def test_provenance_requires_every_segment_to_have_a_source_path(self):
        self.add_provenance()
        second = copy.deepcopy(self.plan["segments"][0])
        second["id"] = "S02"
        self.plan["segments"].append(second)
        self.write()
        with self.assertRaisesRegex(ValueError, "reachable"):
            bundle.export_bundle(self.path, self.root / "out")
        self.plan["provenance"]["edges"].append({"from": "asset:frame02", "to": "segment:S02"})
        self.write()
        root = bundle.export_bundle(self.path, self.root / "out")
        graph = bundle.load_json(root / "provenance/graph.json")
        self.assertEqual(graph["segments"], ["S01", "S02"])
        self.assertEqual(sum(asset["role"] == "original-video" for asset in graph["assets"]), 1)

    def test_source_frames_cannot_be_declared_as_generation_references(self):
        self.add_provenance()
        self.plan["segments"][0]["refs"].append("frame01")
        self.plan["segments"][0]["prompt"] += "{{asset:frame01}}"
        self.write()
        with self.assertRaisesRegex(ValueError, "analysis-only"):
            bundle.export_bundle(self.path, self.root / "out")
        self.assertFalse((self.root / "out").exists())

    def test_source_frame_alias_cannot_bypass_analysis_only_rule(self):
        self.add_provenance()
        (self.root / "hero.png").write_bytes((self.root / "frame.png").read_bytes())
        self.write()
        with self.assertRaisesRegex(ValueError, "analysis-only"):
            bundle.export_bundle(self.path, self.root / "out", "jimeng")
        self.assertFalse((self.root / "out").exists())

    def test_shared_media_catalog_avoids_duplicate_copies_across_segments(self):
        self.plan["assets"].append({"id": "hero_alias", "kind": "image", "path": "hero.png"})
        second = copy.deepcopy(self.plan["segments"][0])
        second.update(id="S02", refs=["hero_alias", "voice"],
                      prompt="{{asset:hero_alias}}，{{asset:voice}}，{{asset:hero_alias}}")
        self.plan["segments"].append(second)
        self.write()
        root = bundle.export_bundle(self.path, self.root / "out", "jimeng")
        manifest = bundle.load_json(root / "manifest.json")
        catalog = {asset["id"]: asset for asset in manifest["assets"]}
        self.assertEqual(set(catalog), {"hero", "voice", "hero_alias"})
        self.assertEqual(catalog["hero"]["file"], catalog["hero_alias"]["file"])
        self.assertEqual(len(list((root / "_media").iterdir())), 2)
        first = bundle.load_json(root / "S01/references.json")
        second = bundle.load_json(root / "S02/references.json")
        self.assertEqual(first["references"][1]["file"], second["references"][0]["file"])
        self.assertEqual([r["label"] for r in second["references"]], ["图片1", "音频1"])
        self.assertFalse(manifest["platform_readback_verified"])
        for record in catalog.values():
            self.assertFalse(Path(record["file"]).is_absolute())
            self.assertEqual(record["binding_status"], "pending-platform-readback")
            self.assertEqual(bundle.digest(root / record["file"]), record["sha256"])

    def test_duplicate_content_within_one_card_is_rejected(self):
        self.plan["assets"].append({"id": "hero_alias", "kind": "image", "path": "hero.png"})
        self.plan["segments"][0]["refs"].append("hero_alias")
        self.plan["segments"][0]["prompt"] += "{{asset:hero_alias}}"
        self.write()
        with self.assertRaisesRegex(ValueError, "Duplicate media"):
            bundle.export_bundle(self.path, self.root / "out")
        self.assertFalse((self.root / "out").exists())

    def test_same_bytes_cannot_claim_conflicting_media_types(self):
        self.plan["assets"].append({"id": "not_video", "kind": "video", "path": "hero.png",
                                     "duration_seconds": 1})
        self.write()
        with self.assertRaisesRegex(ValueError, "conflicting asset kinds"):
            bundle.export_bundle(self.path, self.root / "out")
        self.assertFalse((self.root / "out").exists())

    def test_platform_keys_are_opaque_but_cannot_escape_tokens(self):
        self.write()
        keys = {"hero": "image:real/project.key", "voice": "audio:real/project.key"}
        root = bundle.export_bundle(self.path, self.root / "out", "libtv", keys)
        self.assertIn("{{Node image:real/project.key}}",
                      (root / "S01/prompt.txt").read_text(encoding="utf-8"))
        for key in ("", "bad key", "bad\nkey", "bad\x00key", "bad}key", "bad{key"):
            with self.subTest(key=key):
                with self.assertRaisesRegex(ValueError, "actual node keys"):
                    bundle.export_bundle(self.path, self.root / "bad", "libtv",
                                         {"hero": key, "voice": "valid"})
        self.assertFalse((self.root / "bad").exists())

    def test_binding_identity_must_match_media_reuse(self):
        self.write()
        with self.assertRaisesRegex(ValueError, "different media"):
            bundle.export_bundle(self.path, self.root / "bad", "libtv",
                                 {"hero": "same-node", "voice": "same-node"})
        with self.assertRaisesRegex(ValueError, "actual node keys"):
            bundle.export_bundle(self.path, self.root / "bad", "libtv", [])
        self.plan["assets"].append({"id": "hero_alias", "kind": "image", "path": "hero.png"})
        self.write()
        with self.assertRaisesRegex(ValueError, "reuse one native node key"):
            bundle.export_bundle(self.path, self.root / "bad", "libtv",
                                 {"hero": "image-a", "hero_alias": "image-b", "voice": "audio"})
        root = bundle.export_bundle(self.path, self.root / "out", "libtv",
                                    {"hero": "image", "hero_alias": "image", "voice": "audio"})
        self.assertTrue((root / "manifest.json").is_file())
        self.assertFalse((self.root / "bad").exists())

    def test_supplied_source_binding_survives_in_manifest_without_claiming_verification(self):
        self.add_provenance()
        self.write()
        root = bundle.export_bundle(self.path, self.root / "out", "libtv", {
            "hero": "image:hero", "voice": "audio:voice", "original": "video:original",
            "frame01": "image:frame", "frame02": "image:frame"})
        manifest = bundle.load_json(root / "manifest.json")
        catalog = {record["id"]: record for record in manifest["assets"]}
        graph = bundle.load_json(root / manifest["provenance"]["graph"])
        graph_assets = {record["id"]: record for record in graph["assets"]}
        self.assertEqual(catalog["original"]["supplied_node_key"], "video:original")
        self.assertEqual(graph_assets["original"]["supplied_node_key"], "video:original")
        self.assertEqual(graph_assets["frame02"]["supplied_node_key"], "image:frame")
        self.assertEqual(graph_assets["original"]["binding_status"], "pending-platform-readback")
        self.assertFalse(graph["native_binding_verified"])

    def test_segment_directories_are_portable_and_cannot_collide(self):
        baseline = copy.deepcopy(self.plan)
        for sid in ("CON", "prn", "AUX", "nul", "COM1", "lpt9"):
            with self.subTest(sid=sid):
                self.plan = copy.deepcopy(baseline)
                self.plan["segments"][0]["id"] = sid
                self.write()
                with self.assertRaisesRegex(ValueError, "portable"):
                    bundle.export_bundle(self.path, self.root / "out")
        self.plan = copy.deepcopy(baseline)
        second = copy.deepcopy(self.plan["segments"][0])
        second["id"] = "s01"
        self.plan["segments"].append(second)
        self.write()
        with self.assertRaisesRegex(ValueError, "ignoring case"):
            bundle.export_bundle(self.path, self.root / "out")
        self.plan = copy.deepcopy(baseline)
        self.add_provenance()
        self.plan["segments"][0]["id"] = "Provenance"
        self.write()
        with self.assertRaisesRegex(ValueError, "portable"):
            bundle.export_bundle(self.path, self.root / "out")
        self.assertFalse((self.root / "out").exists())


if __name__ == "__main__":
    unittest.main()
