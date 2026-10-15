"""Offline checks of portable references; never upload or generate media."""
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
            self.assertFalse(bundle.load_json(root / "manifest.json")["generation_submitted"])

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


if __name__ == "__main__":
    unittest.main()
