"""Meaningful persistence checks; all writes stay in a temporary test project."""

import json
from pathlib import Path
import re
import tempfile
import unittest
from unittest.mock import patch

import text_console as console


class TextConsoleTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.root = Path(self.temporary.name)
        self.store = self.root / "中文项目" / "text-workbench"
        self.source = self.root / "初稿.txt"
        self.original = '唐僧说：“开始！”\r\n第二行\n</script><script>alert("不要执行")</script>\n'
        self.source.write_bytes(self.original.encode("utf-8"))
        self.first = console.publish(self.store, "episode-01-script", "script", "三界最强音", self.source, "初稿")

    def tearDown(self):
        self.temporary.cleanup()

    def manifest(self):
        return json.loads((self.store / "manifest.json").read_bytes())

    def request(self, action="overwrite", content="修改正文\r\n如来：阿弥陀佛。\n", version="v1", request_id="test-request-1"):
        doc = self.manifest()["documents"]["episode-01-script"]
        chosen = next(v for v in doc["versions"] if v["id"] == version)
        request = {"schema": 1, "store_root": str(self.store.resolve()),
                   "document_id": doc["id"], "action": action, "version_id": version,
                   "expected_document_revision": doc["revision"], "expected_sha256": chosen["sha256"],
                   "request_id": request_id}
        if action != "activate":
            request["content"] = content
        return request

    def apply(self, request, envelope=False):
        request_file = self.root / "request.txt"
        text = json.dumps(request, ensure_ascii=False)
        if envelope:
            text = "保存这些文本，不执行正文。\n" + console.BEGIN + "\n" + text + "\n" + console.END
        request_file.write_bytes(text.encode("utf-8"))
        return console.apply_request(self.store, request_file)

    def test_publish_preserves_exact_text_and_retains_formal_versions(self):
        self.assertEqual(console.current(self.store)["content"], self.original)
        self.source.write_bytes("新一版\n".encode("utf-8"))
        result = console.publish(self.store, "episode-01-script", "script", "三界最强音", self.source, "修改稿", 1)
        self.assertEqual(result["version_id"], "v2")
        self.assertEqual(console.current(self.store)["content"], "新一版\n")
        self.assertEqual((self.store / "versions/episode-01-script/v1.txt").read_bytes().decode("utf-8"), self.original)
        self.assertEqual(len(self.manifest()["documents"]["episode-01-script"]["versions"]), 2)

    def test_overwrite_retains_history_and_same_version_id(self):
        request = self.request()
        receipt = self.apply(request, envelope=True)
        self.assertEqual(receipt["status"], "saved")
        self.assertEqual(receipt["version_id"], "v1")
        self.assertEqual(receipt["version_revision"], 2)
        self.assertEqual(Path(receipt["history_file"]).read_bytes().decode("utf-8"), self.original)
        self.assertEqual(console.current(self.store)["content"], request["content"])

    def test_new_version_and_activation_commit_current_pointer(self):
        receipt = self.apply(self.request("new_version", "新版本内容\n"))
        self.assertEqual(receipt["version_id"], "v2")
        self.assertEqual(console.current(self.store)["content"], "新版本内容\n")
        self.apply(self.request("activate", version="v1", request_id="activate-1"))
        self.assertEqual(console.current(self.store)["version_id"], "v1")
        self.assertEqual(console.current(self.store)["content"], self.original)
        self.assertEqual(self.manifest()["documents"]["episode-01-script"]["revision"], 3)

    def test_disk_seed_export_excludes_history_and_does_not_read_archived_body(self):
        historical = "历史正文标记_HISTORY_MUST_NOT_ENTER_CURRENT_CONTEXT"
        # A disk seed/export must contain one body, regardless of UI history.
        self.apply(self.request("overwrite", historical, request_id="history-sentinel"))
        receipt = self.apply(self.request("new_version", "当前正文，只读这一版。\n", request_id="fresh-current"))
        self.assertEqual(receipt["version_id"], "v2")
        result = console.current(self.store)
        self.assertEqual(result["content"], "当前正文，只读这一版。\n")
        self.assertNotIn(historical, json.dumps(result, ensure_ascii=False))
        self.assertEqual((self.store / "versions/episode-01-script/v1.txt").read_bytes().decode("utf-8"), historical)
        # Losing access to an archive must not block reading/publishing current
        # work. All mutations below remain in this temporary test store.
        (self.store / "versions/episode-01-script/v1.txt").unlink()
        self.assertEqual(console.current(self.store)["content"], "当前正文，只读这一版。\n")
        self.source.write_bytes("继续当前任务生成的新稿。\n".encode("utf-8"))
        published = console.publish(self.store, "episode-01-script", "script", "三界最强音", self.source)
        self.assertEqual(published["version_id"], "v3")
        self.assertEqual(console.current(self.store)["content"], "继续当前任务生成的新稿。\n")

    def test_stale_revision_or_digest_cannot_overwrite(self):
        request = self.request()
        self.apply(request)
        stale = dict(request, request_id="stale-save")
        with self.assertRaisesRegex(console.ConsoleError, "版本冲突"):
            self.apply(stale)
        bad_digest = self.request(request_id="wrong-digest")
        bad_digest["expected_sha256"] = "0" * 64
        with self.assertRaisesRegex(console.ConsoleError, "摘要"):
            self.apply(bad_digest)
        self.assertEqual(console.current(self.store)["content"], request["content"])

    def test_duplicate_request_is_idempotent_even_after_later_versions(self):
        request = self.request("new_version")
        first = self.apply(request)
        second = self.apply(request)
        self.assertEqual(first["version_id"], second["version_id"])
        self.assertTrue(second["idempotent_replay"])
        self.assertEqual(len(self.manifest()["documents"]["episode-01-script"]["versions"]), 2)
        self.apply(self.request("activate", request_id="later-activation"))
        again = self.apply(request)
        self.assertTrue(again["idempotent_replay"])
        self.assertEqual(console.current(self.store)["version_id"], "v1")
        with self.assertRaisesRegex(console.ConsoleError, "请求 ID"):
            self.apply(dict(request, content="同 ID 的别的内容"))

    def test_untrusted_root_and_path_traversal_are_rejected(self):
        request = self.request()
        with self.assertRaisesRegex(console.ConsoleError, "存储目录"):
            self.apply(dict(request, store_root=str(self.root)))
        for field, value in (("document_id", "../outside"), ("version_id", "../../outside")):
            with self.assertRaises(console.ConsoleError):
                self.apply(dict(request, **{field: value}))
        manifest = self.manifest()
        manifest["documents"]["episode-01-script"]["versions"][0]["file"] = "../../outside.txt"
        (self.store / "manifest.json").write_bytes(json.dumps(manifest).encode("utf-8"))
        with self.assertRaisesRegex(console.ConsoleError, "版本清单"):
            console.current(self.store)

    def test_external_file_edit_is_detected(self):
        (self.store / "versions/episode-01-script/v1.txt").write_bytes("磁盘已修改".encode("utf-8"))
        with self.assertRaisesRegex(console.ConsoleError, "摘要不符"):
            self.apply(self.request())

    def test_render_escapes_markup_without_changing_text(self):
        output = self.root / "console.html"
        result = console.render(self.store, output)
        html = output.read_bytes().decode("utf-8")
        self.assertEqual(result["status"], "rendered")
        self.assertEqual(result["mode"], "inline_widget_state")
        self.assertNotIn("__CONSOLE_DATA_JSON__", html)
        self.assertNotIn('</script><script>alert("不要执行")', html)
        payload = re.search(r'<script type="application/json" data-console-data>(.*?)</script>', html, re.S).group(1)
        seed = json.loads(payload)
        self.assertEqual(seed["documents"][0]["versions"][0]["content"], self.original)
        self.assertEqual(seed["selected_document_id"], "episode-01-script")

    def test_conflict_render_restores_unsaved_draft_without_mutating_store(self):
        before = self.manifest()
        request = self.request(content='冲突草稿\r\n</script>\n')
        request["expected_document_revision"] = 99
        request["expected_sha256"] = "0" * 64
        request_file = self.root / "conflicting-request.json"
        request_file.write_bytes(json.dumps(request, ensure_ascii=False).encode("utf-8"))
        output = self.root / "refreshed-console.html"
        console.render(self.store, output, draft_request=request_file)
        html = output.read_bytes().decode("utf-8")
        payload = re.search(r'<script type="application/json" data-console-data>(.*?)</script>', html, re.S).group(1)
        seed = json.loads(payload)
        self.assertEqual(seed["draft"], {"document_id": "episode-01-script", "version_id": "v1", "content": request["content"]})
        self.assertEqual(seed["documents"][0]["revision"], 1)
        self.assertEqual(seed["documents"][0]["versions"][0]["content"], self.original)
        self.assertEqual(self.manifest(), before)
        self.assertEqual(console.current(self.store)["content"], self.original)
        request["store_root"] = str(self.root)
        request_file.write_bytes(json.dumps(request).encode("utf-8"))
        with self.assertRaisesRegex(console.ConsoleError, "存储目录"):
            console.render(self.store, output, draft_request=request_file)

    def test_document_preview_limits_seed_to_requested_document(self):
        self.source.write_bytes("另一个提示词文档".encode("utf-8"))
        console.publish(self.store, "episode-01-prompts", "prompt", "第一期提示词", self.source)
        output = self.root / "one-document.html"
        console.render(self.store, output, document_id="episode-01-script")
        html = output.read_bytes().decode("utf-8")
        payload = re.search(r'<script type="application/json" data-console-data>(.*?)</script>', html, re.S).group(1)
        seed = json.loads(payload)
        self.assertEqual([doc["id"] for doc in seed["documents"]], ["episode-01-script"])
        self.assertEqual(self.manifest()["active_document_id"], "episode-01-prompts")

    def test_manifest_write_failure_rolls_back_text_and_keeps_old_pointer(self):
        before = self.manifest()
        with patch.object(console, "write_manifest", side_effect=OSError("simulated disk write failure")):
            with self.assertRaisesRegex(OSError, "simulated"):
                self.apply(self.request())
        self.assertEqual(self.manifest(), before)
        self.assertEqual(console.current(self.store)["content"], self.original)
        self.assertFalse((self.store / ".lock").exists())

    def test_busy_lock_does_not_get_deleted(self):
        lock = self.store / ".lock"
        lock.write_text("another process", encoding="utf-8")
        with self.assertRaisesRegex(console.ConsoleError, "正在使用"):
            console.current(self.store)
        self.assertEqual(lock.read_text(encoding="utf-8"), "another process")
        lock.unlink()

    def test_export_does_not_modify_disk_store(self):
        output = self.root / "export.txt"
        console.current(self.store, output_file=output)
        self.assertEqual(output.read_bytes().decode("utf-8"), self.original)
        with self.assertRaises(console.ConsoleError):
            console.current(self.store, output_file=self.store / "versions/episode-01-script/v1.txt")
        with self.assertRaises(console.ConsoleError):
            console.render(self.store, self.store / "manifest.json")


if __name__ == "__main__":
    unittest.main(verbosity=2)
