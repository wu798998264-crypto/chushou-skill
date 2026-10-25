import copy
import json
from pathlib import Path
import tempfile
import unittest

import compile_drama_transcripts as cli


class CompilationTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix="chushou-compile-")
        self.root = Path(self.temp.name)
        self.catalog = {"series_id": "series-1", "title": "测试剧", "total_episodes": 10,
                        "episodes": [{"episode": i, "video_id": f"v{i}"} for i in range(1, 11)]}
        self.batch = {"series_id": "series-1", "title": "测试剧", "source_total_episodes": 10, "catalog": "catalog.json",
                      "episodes": [{"episode": i, "video_id": f"v{i}", "status": "downloaded", "sha256": f"{i:064x}",
                                    "metadata": {"has_audio": True}} for i in range(1, 11)]}
        self.mapping = {"series_id": "series-1", "episodes": []}
        for i in range(1, 11):
            self.mapping["episodes"].append({"episode": i, "video_id": f"v{i}", "source_sha256": f"{i:064x}",
                                                "job_id": f"j{i}", "node_id": f"n{i}", "transcript_file": f"exports/{i}.json"})
            self.save(f"maps/exports/{i}.json", {"job": {"id": f"j{i}", "nodeId": f"n{i}", "status": "completed", "sourceSha256": f"{i:064x}"},
                                              "text": f"原文第{i}集。\n  保留空格\r\n\n"})
        self.output = self.root / "汇总.txt"
        self.flush()

    def tearDown(self):
        self.temp.cleanup()

    def save(self, name, value):
        target = self.root / name
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(json.dumps(value, ensure_ascii=False), encoding="utf-8")

    def flush(self):
        self.save("catalog.json", self.catalog)
        self.save("batch.json", self.batch)
        self.save("maps/map.json", self.mapping)

    def args(self, *extra):
        return cli.parser().parse_args(["--download-manifest", str(self.root / "batch.json"), "--transcript-map", str(self.root / "maps/map.json"), "--out", str(self.output), *extra])

    def test_numeric_order_body_and_relative_paths(self):
        self.mapping["episodes"].reverse()
        self.batch["episodes"].reverse()
        self.catalog["episodes"].reverse()
        self.flush()
        result, code = cli.run(self.args())
        self.assertEqual(code, 0)
        content = Path(result["out"]).read_bytes().decode("utf-8")
        self.assertIn("识别状态：全集完成", content)
        self.assertLess(content.index("第1集\n"), content.index("第2集\n"))
        self.assertLess(content.index("第2集\n"), content.index("第10集\n"))
        self.assertIn("原文第1集。\n  保留空格\r\n\n", content)
        self.assertEqual(cli.run(self.args())[0]["out"], result["out"])

    def test_missing_failed_and_absent_audio_keep_all_headings(self):
        self.mapping["episodes"] = [item for item in self.mapping["episodes"] if item["episode"] not in {2, 3, 4}]
        self.batch["episodes"][2] = {"episode": 3, "video_id": "v3", "status": "failed", "error": "network"}
        self.batch["episodes"][3]["metadata"]["has_audio"] = False
        (self.root / "maps/exports/5.json").unlink()
        self.save("maps/exports/6.json", {"job": {"id": "j6", "nodeId": "n6", "status": "failed", "error": "quota"}, "text": ""})
        self.flush()
        result, code = cli.run(self.args())
        self.assertEqual(code, 2)
        report = cli.read_json(result["report"])
        self.assertEqual(report["incomplete_episodes"], [2, 3, 4, 5, 6])
        text = Path(result["out"]).read_text(encoding="utf-8")
        self.assertIn("识别状态：未完成草稿", text)
        self.assertEqual(text.count("【未完成："), 5)
        for i in range(1, 11):
            self.assertIn(f"第{i}集\n", text)

    def test_require_complete_only_writes_report(self):
        self.mapping["episodes"].pop()
        self.flush()
        result, code = cli.run(self.args("--require-complete"))
        self.assertEqual(code, 2)
        self.assertIsNone(result["out"])
        self.assertFalse(self.output.exists())
        self.assertEqual(cli.read_json(result["report"])["document_status"], "blocked_incomplete")

    def test_conflicting_map_identity_is_rejected(self):
        original = copy.deepcopy(self.mapping)
        for field, value in (("episode", 11), ("video_id", "other"), ("source_sha256", "f" * 64), ("job_id", "wrong"), ("node_id", "wrong")):
            self.mapping = copy.deepcopy(original)
            self.mapping["episodes"][0][field] = value
            self.flush()
            with self.subTest(field=field), self.assertRaises(ValueError):
                cli.run(self.args())
        self.mapping = copy.deepcopy(original)
        self.mapping["series_id"] = "other-series"
        self.flush()
        with self.assertRaises(ValueError):
            cli.run(self.args())

    def test_duplicates_and_catalog_conflicts_are_rejected(self):
        self.mapping["episodes"].append(copy.deepcopy(self.mapping["episodes"][0]))
        self.flush()
        with self.assertRaises(ValueError):
            cli.run(self.args())
        self.mapping["episodes"].pop()
        self.catalog["episodes"][0]["episode"] = 2
        self.flush()
        with self.assertRaises(ValueError):
            cli.run(self.args())

    def test_export_source_hash_conflict_rejected(self):
        self.save("maps/exports/1.json", {"job": {"id": "j1", "nodeId": "n1", "status": "completed", "sourceSha256": "f" * 64}, "text": "错源"})
        with self.assertRaises(ValueError):
            cli.run(self.args())

    def test_different_output_preserved_as_version(self):
        self.output.write_text("用户手改文档", encoding="utf-8")
        result, code = cli.run(self.args())
        self.assertEqual(code, 0)
        self.assertEqual(self.output.read_text(encoding="utf-8"), "用户手改文档")
        self.assertEqual(Path(result["out"]).name, "汇总.v002.txt")
        self.assertEqual(cli.run(self.args())[0]["out"], result["out"])
        self.assertFalse(list(self.root.glob("*.tmp")))

    def test_shared_job_requires_same_sha_and_compatible_node(self):
        self.mapping["episodes"][1].update({"job_id": "j1", "node_id": "n1", "transcript_file": "exports/1.json"})
        self.flush()
        with self.assertRaises(ValueError):
            cli.run(self.args())
        self.mapping["episodes"][1]["source_sha256"] = self.batch["episodes"][1]["sha256"] = f"{1:064x}"
        self.flush()
        result, code = cli.run(self.args())
        self.assertEqual(code, 0)
        self.assertIn("第2集\n\n原文第1集。", Path(result["out"]).read_text(encoding="utf-8"))
        self.mapping["episodes"][1]["node_id"] = "conflict"
        self.flush()
        with self.assertRaises(ValueError):
            cli.run(self.args())

    def test_shared_job_conflicting_export_is_rejected(self):
        self.mapping["episodes"][1].update({"job_id": "j1", "source_sha256": f"{1:064x}"})
        self.mapping["episodes"][1].pop("node_id")
        self.batch["episodes"][1]["sha256"] = f"{1:064x}"
        self.save("maps/exports/2.json", {"job": {"id": "j1", "nodeId": "other", "status": "completed"}, "text": "不同正文"})
        self.flush()
        with self.assertRaises(ValueError):
            cli.run(self.args())

    def test_old_export_is_compatible_but_not_marked_hash_verified(self):
        self.save("maps/exports/1.json", {"job": {"id": "j1", "nodeId": "n1", "status": "completed"}, "text": "旧版原文"})
        result, code = cli.run(self.args())
        self.assertEqual(code, 0)
        self.assertFalse(cli.read_json(result["report"])["episodes"][0]["export_source_sha256_verified"])


if __name__ == "__main__":
    unittest.main()
