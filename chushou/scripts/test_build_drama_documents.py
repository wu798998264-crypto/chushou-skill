"""Offline regression tests: source binding, audited edits, and lossless layout."""
import hashlib
import importlib.util
import json
from pathlib import Path
import re
import tempfile
import unittest
from unittest.mock import patch
import xml.etree.ElementTree as ET
import zipfile

import build_drama_documents as builder


def sha(data):
    return hashlib.sha256(data).hexdigest()


class Fixture:
    def __init__(self, directory, total=3, listed=None, reviewed=True, selected=None, download=True):
        self.root = Path(directory)
        self.manifest_path = self.root / "input" / "manifest.json"
        self.out = self.root / "never-existed" / "output"
        self.base = self.manifest_path.parent
        self.base.mkdir(parents=True)
        numbers = list(range(1, total + 1)) if listed is None else listed
        self.data = {"schema_version": 1, "title": "离线测试剧", "series_id": "test-series",
                     "total_episodes": total, "episodes": []}
        if selected is not None:
            self.data["selected_episodes"] = selected
        self.catalog = {"series_id": "test-series", "total_episodes": total,
                        "episodes": [{"episode": number, "video_id": "video-" + str(number)} for number in range(1, total + 1)]}
        self.batch = {"series_id": "test-series", "source_total_episodes": total,
                      "selected_episodes": list(range(1, total + 1)) if selected is None else selected,
                      "catalog": "catalog.json", "episodes": []}
        for number in numbers:
            content = ("原始台词" + str(number) + "。\r\n这不是摘要！").encode("utf-8")
            source = b"not-a-real-video-for-offline-hash-test-" + str(number).encode("ascii")
            (self.base / (str(number) + ".mp4")).write_bytes(source)
            (self.base / (str(number) + ".raw.txt")).write_bytes(content)
            item = {"episode": number, "video_id": "video-" + str(number),
                    "source_file": str(number) + ".mp4", "source_sha256": sha(source),
                    "backend": {"name": "真实测试后端", "url": "https://example.invalid/offline-fixture", "model": "fixture-model"},
                    "raw_file": str(number) + ".raw.txt", "raw_sha256": sha(content)}
            if reviewed:
                item["review_file"] = str(number) + ".review.json"
                self.write_json(item["review_file"], {"raw_sha256": sha(content), "reviewed": True, "edits": [], "uncertain": []})
            self.data["episodes"].append(item)
            self.batch["episodes"].append({"episode": number, "video_id": item["video_id"],
                                           "status": "downloaded", "video": item["source_file"],
                                           "sha256": sha(source), "metadata": {"has_audio": True}})
        if download:
            self.data["download_manifest"] = "batch.json"
        self.save()

    def write_json(self, name, data):
        (self.base / name).write_text(json.dumps(data, ensure_ascii=False), encoding="utf-8")

    def save(self):
        self.write_json("manifest.json", self.data)
        self.write_json("batch.json", self.batch)
        self.write_json("catalog.json", self.catalog)

    def raw(self, number, text):
        item = next(item for item in self.data["episodes"] if item["episode"] == number)
        payload = text.encode("utf-8")
        (self.base / item["raw_file"]).write_bytes(payload)
        item["raw_sha256"] = sha(payload)
        if "review_file" in item:
            review = builder.read_json(self.base / item["review_file"])
            review["raw_sha256"] = item["raw_sha256"]
            self.write_json(item["review_file"], review)
        self.save()

    def review(self, number, **changes):
        item = next(item for item in self.data["episodes"] if item["episode"] == number)
        name = item.get("review_file", str(number) + ".review.json")
        old = builder.read_json(self.base / name) if (self.base / name).exists() else {"raw_sha256": item["raw_sha256"]}
        old.update(changes)
        item["review_file"] = name
        self.write_json(name, old)
        self.save()

    def run(self, **options):
        self.save()
        result, code = builder.build(self.manifest_path, self.out, **options)
        report = builder.read_json(result["report"])
        return result, report, code


class DocumentBuilderTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)

    def fixture(self, **kwargs):
        return Fixture(self.temporary.name, **kwargs)

    def test_first_directory_full_review_and_exact_raw_bytes(self):
        case = self.fixture(total=1)
        original = "\ufeff甲，乙。\r\n丙\t丁！"
        case.raw(1, original)
        result, report, code = case.run()
        self.assertEqual(code, 0)
        self.assertTrue(report["complete"])
        self.assertEqual(report["document_label"], "校对版")
        raw_file = Path(report["episodes"][0]["artifacts"]["raw"])
        self.assertEqual(raw_file.read_bytes(), original.encode("utf-8"))
        final = Path(result["documents"]["review_txt"]).read_text(encoding="utf-8")
        self.assertIn("全集校对版", final)
        self.assertIn("第1集", final)
        self.assertEqual(report["episodes"][0]["backend"]["model"], "fixture-model")

    def test_numeric_order_does_not_follow_string_or_manifest_order(self):
        case = self.fixture(total=12, listed=list(range(12, 0, -1)))
        result, report, code = case.run()
        self.assertEqual(code, 0)
        body = Path(result["documents"]["review_txt"]).read_text(encoding="utf-8")
        self.assertEqual([int(n) for n in re.findall(r"^第(\d+)集$", body, re.M)], list(range(1, 13)))
        self.assertEqual([item["episode"] for item in report["episodes"]], list(range(1, 13)))

    def test_missing_episode_placeholder_and_nonzero_exit(self):
        case = self.fixture(total=3, listed=[1, 3])
        result, report, code = case.run()
        self.assertEqual(code, 2)
        self.assertEqual(report["missing_episodes"], [2])
        text = Path(result["documents"]["review_txt"]).read_text(encoding="utf-8")
        self.assertIn("第2集\n\n【未完成：", text)
        self.assertEqual(report["document_label"], "校对草稿")

    def test_require_complete_blocks_aggregate_not_report_or_audits(self):
        case = self.fixture(total=2, listed=[1])
        result, report, code = case.run(require_complete=True)
        self.assertEqual(code, 2)
        self.assertEqual(result["documents"], {})
        self.assertEqual(report["document_status"], "blocked_incomplete")
        self.assertFalse(list(case.out.glob("*.txt")))
        self.assertTrue(Path(report["episodes"][0]["audit_file"]).is_file())

    def test_no_review_is_pending_never_falsely_reviewed(self):
        case = self.fixture(total=1, reviewed=False)
        result, report, code = case.run()
        self.assertEqual(code, 2)
        self.assertTrue(report["recognition_complete"])
        self.assertFalse(report["complete"])
        self.assertIn("【待校对】", Path(result["documents"]["review_txt"]).read_text(encoding="utf-8"))
        strict, _, strict_code = case.run(require_complete=True)
        self.assertEqual(strict_code, 2)
        self.assertEqual(strict["documents"], {})

    def test_false_reviewed_flag_does_not_promote_to_reviewed(self):
        case = self.fixture(total=1)
        case.review(1, reviewed=False)
        _, report, code = case.run()
        self.assertEqual(code, 2)
        self.assertEqual(report["episodes"][0]["status"], "pending_review")

    def test_wrong_mapped_video_id_is_failed_not_success_body(self):
        case = self.fixture(total=1)
        case.data["episodes"][0]["video_id"] = "wrong-episode"
        result, report, code = case.run()
        self.assertEqual(code, 1)
        self.assertEqual(report["failed_episodes"], [1])
        self.assertIn("video_id", report["episodes"][0]["reason"])
        self.assertNotIn("原始台词", Path(result["documents"]["review_txt"]).read_text(encoding="utf-8"))

    def test_download_batch_wrong_id_rejected_globally(self):
        case = self.fixture(total=2)
        case.batch["episodes"][1]["video_id"] = "video-1"
        result, report, code = case.run()
        self.assertEqual(code, 1)
        self.assertEqual(result["documents"], {})
        self.assertIn("video_id", report["error"])

    def test_mixed_series_rejected(self):
        case = self.fixture(total=1)
        case.catalog["series_id"] = "other-series"
        _, report, code = case.run()
        self.assertEqual(code, 1)
        self.assertIn("series_id", report["error"])

    def test_catalog_must_represent_actual_full_series(self):
        case = self.fixture(total=3)
        case.catalog["episodes"].pop()
        _, report, code = case.run()
        self.assertEqual(code, 1)
        self.assertIn("真实全集", report["error"])

    def test_source_sha_is_checked_against_actual_file(self):
        case = self.fixture(total=1)
        (case.base / "1.mp4").write_bytes(b"different-content")
        _, report, code = case.run()
        self.assertEqual(code, 1)
        self.assertIn("源视频 SHA256", report["episodes"][0]["reason"])
        self.assertFalse(report["episodes"][0]["source_sha256_verified"])
        self.assertNotEqual(report["episodes"][0]["source_sha256"], report["episodes"][0]["source_sha256_actual"])
        self.assertIn("backend", report["episodes"][0])

    def test_raw_sha_is_checked_against_actual_file(self):
        case = self.fixture(total=1)
        (case.base / "1.raw.txt").write_text("篡改", encoding="utf-8")
        _, report, code = case.run()
        self.assertEqual(code, 1)
        self.assertIn("原始识别 SHA256", report["episodes"][0]["reason"])

    def test_source_copy_with_same_hash_can_differ_from_batch_video_path(self):
        case = self.fixture(total=1)
        copy_file = case.base / "portable-copy.mp4"
        copy_file.write_bytes((case.base / "1.mp4").read_bytes())
        case.data["episodes"][0]["source_file"] = copy_file.name
        case.batch["episodes"][0]["video"] = "old-machine-file-that-no-longer-exists.mp4"
        _, report, code = case.run()
        self.assertEqual(code, 0)
        self.assertEqual(report["episodes"][0]["source_sha256"], sha(copy_file.read_bytes()))

    def test_review_must_bind_exact_original_hash(self):
        case = self.fixture(total=1)
        case.review(1, raw_sha256="0" * 64)
        _, report, code = case.run()
        self.assertEqual(code, 1)
        self.assertIn("review raw_sha256", report["episodes"][0]["reason"])

    def test_edits_apply_once_to_original_offsets_without_global_replace(self):
        case = self.fixture(total=1)
        case.raw(1, "𠀀甲甲猫猫结束")
        edits = [{"start": 1, "before": "甲", "after": "长文本", "reason": "人名核对", "evidence": "原片 00:01 音轨"},
                 {"start": 4, "before": "猫", "after": "犬", "reason": "名词核对", "evidence": {"type": "subtitle", "at": "00:02"}}]
        case.review(1, edits=edits)
        _, report, code = case.run()
        self.assertEqual(code, 0)
        entry = report["episodes"][0]
        self.assertEqual(Path(entry["artifacts"]["corrected"]).read_text(encoding="utf-8"), "𠀀长文本甲猫犬结束")
        self.assertEqual(entry["edits"][1]["start"], 4)
        self.assertEqual(len(entry["edits"]), 2)

    def test_insertion_uses_unicode_codepoint_not_utf16_offset(self):
        case = self.fixture(total=1)
        case.raw(1, "𠀀甲")
        case.review(1, edits=[{"start": 1, "before": "", "after": "乙", "reason": "补漏字", "evidence": "00:01 音轨"}])
        _, report, code = case.run()
        self.assertEqual(code, 0)
        self.assertEqual(Path(report["episodes"][0]["artifacts"]["corrected"]).read_text(encoding="utf-8"), "𠀀乙甲")

    def test_overlapping_edits_rejected(self):
        case = self.fixture(total=1)
        case.raw(1, "甲乙丙")
        case.review(1, edits=[{"start": 0, "before": "甲乙", "after": "丁", "reason": "核对", "evidence": "音轨"},
                              {"start": 1, "before": "乙", "after": "戊", "reason": "核对", "evidence": "音轨"}])
        _, report, code = case.run()
        self.assertEqual(code, 1)
        self.assertIn("重叠", report["episodes"][0]["reason"])

    def test_before_must_match_and_edit_requires_reason_and_evidence(self):
        for field, value in (("before", "错误原文"), ("reason", ""), ("evidence", {})):
            with self.subTest(field=field):
                with tempfile.TemporaryDirectory() as root:
                    case = Fixture(root, total=1)
                    case.raw(1, "甲乙丙")
                    edit = {"start": 0, "before": "甲", "after": "丁", "reason": "听音核对", "evidence": "原片00:01"}
                    edit[field] = value
                    case.review(1, edits=[edit])
                    _, report, code = case.run()
                    self.assertEqual(code, 1)
                    self.assertEqual(report["episodes"][0]["status"], "failed")

    def test_punctuation_only_preserves_nonbmp_letters_numbers_and_symbols(self):
        case = self.fixture(total=1)
        case.raw(1, "𠀀甲AB12+🌟乙=3.14")
        punct = "「𠀀甲，AB12+🌟乙」=3.14！\n"
        (case.base / "punct.txt").write_text(punct, encoding="utf-8")
        case.data["episodes"][0]["punct_file"] = "punct.txt"
        _, report, code = case.run()
        self.assertEqual(code, 0)
        text = Path(report["episodes"][0]["artifacts"]["formatted"]).read_text(encoding="utf-8")
        self.assertEqual(builder.semantic_characters(text), builder.semantic_characters(punct))
        self.assertIn("3.14", text)

    def test_punct_missing_nonbmp_or_semantic_symbol_fails(self):
        for punct in ("甲ABC1+🌟", "𠀀甲ABC1🌟", "𠀀甲ABC1+", "𠀀甲ABC2+🌟"):
            with self.subTest(punct=punct):
                with tempfile.TemporaryDirectory() as root:
                    case = Fixture(root, total=1)
                    case.raw(1, "𠀀甲ABC1+🌟")
                    (case.base / "punct.txt").write_text(punct, encoding="utf-8")
                    case.data["episodes"][0]["punct_file"] = "punct.txt"
                    result, report, code = case.run()
                    self.assertEqual(code, 1)
                    self.assertIn("非标点正文", report["episodes"][0]["reason"])
                    self.assertNotIn("𠀀甲ABC", Path(result["documents"]["review_txt"]).read_text(encoding="utf-8"))

    def test_punct_validates_against_corrected_not_raw(self):
        case = self.fixture(total=1)
        case.raw(1, "甲猫")
        case.review(1, edits=[{"start": 1, "before": "猫", "after": "犬", "reason": "音轨核验", "evidence": "00:01"}])
        (case.base / "punct.txt").write_text("甲犬！", encoding="utf-8")
        case.data["episodes"][0]["punct_file"] = "punct.txt"
        _, _, code = case.run()
        self.assertEqual(code, 0)

    def test_semantic_punctuation_cannot_be_lost(self):
        pairs = [("1.5", "15"), ("-3", "3"), ("50%", "50"), ("a-b", "ab"),
                 ("https://example.com/a?b=1", "https:example.coma?b=1"),
                 ("5 m/s", "5 ms"), ("don't", "dont"), ("12:30", "1230"),
                 ("3.14 kg", "314 kg"), ("α·β", "αβ")]
        for original, altered in pairs:
            with self.subTest(original=original):
                with self.assertRaises(ValueError):
                    builder.require_punctuation_only(original, altered, "test")
        builder.require_punctuation_only('“他说：你好。”', '他说,你好！', "test")

    def test_semantic_number_loss_fails_document_not_only_helper(self):
        case = self.fixture(total=1)
        case.raw(1, "速度1.5，温度-3，50%成功。")
        (case.base / "punct.txt").write_text("速度15，温度3，50成功！", encoding="utf-8")
        case.data["episodes"][0]["punct_file"] = "punct.txt"
        result, report, code = case.run()
        self.assertEqual(code, 1)
        self.assertFalse(report["complete"])
        self.assertEqual(report["episodes"][0]["status"], "failed")
        self.assertNotIn("速度15", Path(result["documents"]["review_txt"]).read_text(encoding="utf-8"))

    def test_sentence_layout_is_lossless_and_splits_at_terminators(self):
        text = '“甲？！” 乙𠀀+🌟。AB 3.14。'
        result = builder.sentence_lines(text)
        self.assertEqual(builder.semantic_characters(text), builder.semantic_characters(result))
        self.assertEqual(result, '“甲？！”\n乙𠀀+🌟。\nAB 3.14。')

    def test_stale_unmapped_files_are_never_scanned_or_imported(self):
        case = self.fixture(total=2, listed=[1])
        (case.base / "2.raw.txt").write_text("这是之前任务的旧文本", encoding="utf-8")
        (case.base / "episode-002.txt").write_text("旧文件", encoding="utf-8")
        result, report, code = case.run()
        self.assertEqual(code, 2)
        self.assertEqual(report["missing_episodes"], [2])
        self.assertNotIn("旧文本", Path(result["documents"]["review_txt"]).read_text(encoding="utf-8"))

    def test_existing_outputs_and_intermediates_are_versioned_and_reused(self):
        case = self.fixture(total=1)
        first, report, _ = case.run()
        original_path = Path(first["documents"]["review_txt"])
        original = original_path.read_bytes()
        again, _, _ = case.run()
        self.assertEqual(first, again)
        case.raw(1, "新原始台词。")
        second, second_report, code = case.run()
        self.assertEqual(code, 0)
        self.assertEqual(original_path.read_bytes(), original)
        self.assertNotEqual(second["documents"]["review_txt"], str(original_path))
        self.assertIn(".v002.", second["documents"]["review_txt"])
        self.assertIn(".v002.", second_report["episodes"][0]["artifacts"]["raw"])
        self.assertNotEqual(second["report"], first["report"])

    def test_selected_range_uses_true_episode_numbers_without_missing_unselected(self):
        case = self.fixture(total=12, listed=[10, 2], selected=[10, 2])
        result, report, code = case.run()
        self.assertEqual(code, 0)
        self.assertEqual(report["total_episodes"], 12)
        self.assertEqual(report["selected_episodes"], [2, 10])
        self.assertEqual(report["missing_episodes"], [])
        text = Path(result["documents"]["review_txt"]).read_text(encoding="utf-8")
        self.assertIn("选集校对版", text)
        self.assertNotIn("全集", text)
        self.assertEqual(re.findall(r"^第(\d+)集$", text, re.M), ["2", "10"])

    def test_selected_range_cannot_exceed_download_batch(self):
        case = self.fixture(total=12, listed=[2], selected=[2])
        case.batch["selected_episodes"] = [1]
        _, report, code = case.run()
        self.assertEqual(code, 1)
        self.assertIn("选集范围", report["error"])

    def test_full_download_batch_can_supply_smaller_review_selection(self):
        case = self.fixture(total=12)
        case.data["selected_episodes"] = [2, 10]
        case.data["episodes"] = [item for item in case.data["episodes"] if item["episode"] in (2, 10)]
        result, report, code = case.run()
        self.assertEqual(code, 0)
        self.assertEqual(report["selected_episodes"], [2, 10])
        self.assertEqual(report["download_binding"]["selected_episodes"], list(range(1, 13)))
        self.assertEqual(report["missing_episodes"], [])
        text = Path(result["documents"]["review_txt"]).read_text(encoding="utf-8")
        self.assertEqual(re.findall(r"^第(\d+)集$", text, re.M), ["2", "10"])

    def test_unselected_batch_episode_identity_is_still_verified(self):
        case = self.fixture(total=3)
        case.data["selected_episodes"] = [2]
        case.data["episodes"] = [case.data["episodes"][1]]
        case.batch["episodes"][0]["video_id"] = "wrong-first-episode"
        result, report, code = case.run()
        self.assertEqual(code, 1)
        self.assertEqual(result["documents"], {})
        self.assertIn("video_id", report["error"])

    def test_selected_missing_episode_is_explicit(self):
        case = self.fixture(total=12, listed=[2], selected=[2, 10])
        _, report, code = case.run()
        self.assertEqual(code, 2)
        self.assertEqual(report["missing_episodes"], [10])

    def test_duplicate_out_of_range_boolean_and_noninteger_episodes_rejected(self):
        for bad_number in (1, 2, True, "1", 1.0):
            with self.subTest(number=bad_number):
                with tempfile.TemporaryDirectory() as root:
                    case = Fixture(root, total=1)
                    case.data["episodes"].append({**case.data["episodes"][0], "episode": bad_number})
                    result, _, code = case.run()
                    self.assertEqual(code, 1)
                    self.assertEqual(result["documents"], {})

    def test_catalog_duplicate_ids_and_source_total_mismatch_rejected(self):
        case = self.fixture(total=2)
        case.catalog["episodes"][1]["video_id"] = "video-1"
        _, report, code = case.run()
        self.assertEqual(code, 1)
        self.assertIn("video_id 重复", report["error"])
        case.catalog["episodes"][1]["video_id"] = "video-2"
        case.batch["source_total_episodes"] = 1
        _, report, code = case.run()
        self.assertEqual(code, 1)
        self.assertIn("总集数", report["error"])

    def test_download_failure_not_accepted_even_with_local_text(self):
        case = self.fixture(total=1)
        case.batch["episodes"][0]["status"] = "failed"
        _, report, code = case.run()
        self.assertEqual(code, 1)
        self.assertIn("下载未完成", report["episodes"][0]["reason"])

    def test_report_retains_uncertainty_and_edit_evidence(self):
        case = self.fixture(total=1)
        case.review(1, uncertain=[{"text": "此人姓名", "reason": "音轨与字幕不同", "at": "00:03"}])
        result, report, code = case.run()
        self.assertEqual(code, 0)
        self.assertEqual(report["uncertain_count"], 1)
        self.assertEqual(report["episodes"][0]["uncertain"][0]["at"], "00:03")
        self.assertIn("待核事项：1项", Path(result["documents"]["review_txt"]).read_text(encoding="utf-8"))

    def test_local_project_needs_no_series_or_downloader(self):
        case = self.fixture(total=1, download=False)
        case.data.pop("series_id")
        case.data["episodes"][0].pop("video_id")
        _, report, code = case.run()
        self.assertEqual(code, 0)
        self.assertIsNone(report["series_id"])
        self.assertIsNone(report["download_binding"])

    def test_invalid_json_saves_error_report_in_new_directory(self):
        case = self.fixture(total=1)
        case.manifest_path.write_bytes(b"{bad-json")
        result, code = builder.build(case.manifest_path, case.out)
        self.assertEqual(code, 1)
        self.assertEqual(result["documents"], {})
        self.assertIn("error", builder.read_json(result["report"]))

    @unittest.skipUnless(importlib.util.find_spec("docx"), "optional python-docx is not installed")
    def test_optional_docx_contains_ordered_reviewed_unicode_body(self):
        case = self.fixture(total=2, listed=[2, 1])
        case.raw(1, "𠀀角色说：甲乙！")
        result, report, code = case.run(docx=True)
        self.assertEqual(code, 0)
        with zipfile.ZipFile(result["documents"]["review_docx"]) as archive:
            document = ET.fromstring(archive.read("word/document.xml"))
        text = "\n".join(element.text or "" for element in document.iter("{http://schemas.openxmlformats.org/wordprocessingml/2006/main}t"))
        self.assertIn("𠀀角色说：甲乙！", text)
        self.assertLess(text.index("第1集"), text.index("第2集"))
        self.assertEqual(report["document_status"], "complete")

    def test_optional_docx_failure_is_reported_with_nonzero_exit(self):
        case = self.fixture(total=1)
        with patch.object(builder, "docx_bytes", side_effect=ValueError("python-docx unavailable")):
            _, report, code = case.run(docx=True)
        self.assertEqual(code, 1)
        self.assertFalse(report["complete"])
        self.assertIn("python-docx unavailable", report["error"])
        self.assertNotIn("review_docx", report["documents"])


if __name__ == "__main__":
    unittest.main()
