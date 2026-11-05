"""Isolated persistence and HTTP checks for the Shensi editor adapter."""

import http.client
import json
from pathlib import Path
import tempfile
import threading
import unittest
from unittest.mock import patch
from urllib.parse import urlsplit

import text_console as console
import writing_editor as writing


class WritingEditorTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.root = Path(self.temporary.name)
        self.store = self.root / "中文项目" / "text-workbench"
        source = self.root / "source.txt"
        self.original = '原始正文\r\n中文与🙂\n</script><script>不要执行</script>\n'
        source.write_bytes(self.original.encode("utf-8"))
        console.publish(self.store, "episode-01-script", "script", "测试稿", source)
        self.document = "episode-01-script"

    def tearDown(self):
        self.temporary.cleanup()

    def call(self, action, **payload):
        return writing.dispatch(self.store, self.document, action, payload)

    def read(self, identity="writer-a"):
        return self.call("ui-read", clientId=identity)

    def draft(self, text="新草稿\n保留整篇", identity="writer-a"):
        state = self.read(identity)
        return self.call("ui-write", clientId=identity, expectedEditorRevision=state["editor"]["revision"],
                         versionId=state["currentVersionId"], text=text,
                         ui={"editing": True, "expanded": False, "scrollTop": 123})

    def save_payload(self, state, text="新草稿\n保留整篇", request="save-1"):
        return {"clientId": "writer-a", "expectedRevision": state["revision"],
                "expectedEditorRevision": state["editor"]["revision"],
                "sourceVersionId": state["currentVersionId"], "text": text, "requestId": request}

    def test_real_save_drafts_history_selection_and_restart(self):
        draft = self.draft()
        self.assertEqual(draft["text"], self.original)
        self.assertEqual(console.current(self.store)["content"], self.original)
        self.assertEqual(self.read("writer-b")["editor"]["drafts"], {})
        self.assertEqual(self.read()["editor"]["drafts"]["v1"], "新草稿\n保留整篇")
        saved = self.call("ui-save", **self.save_payload(draft))
        self.assertEqual(saved["currentVersionId"], "v2")
        self.assertEqual(saved["editor"]["drafts"], {})
        self.assertEqual(console.current(self.store)["content"], saved["text"])
        self.assertEqual(len(saved["versions"]), 2)
        self.assertEqual((self.store / "versions/episode-01-script/v1.txt").read_bytes().decode(), self.original)
        selected = self.call("select", versionId="v1", expectedRevision=saved["revision"])
        self.assertEqual(selected["text"], self.original)
        self.assertEqual(console.current(self.store)["version_id"], "v1")
        # No process cache is involved: a fresh read restores the stored UI and pointer.
        self.assertEqual(self.read()["editor"]["ui"]["scrollTop"], 123)
        self.assertEqual(self.read()["currentVersionId"], "v1")

    def test_revision_conflicts_and_idempotent_retry_never_overwrite(self):
        draft = self.draft()
        payload = self.save_payload(draft)
        saved = self.call("ui-save", **payload)
        again = self.call("ui-save", **payload)
        self.assertEqual(again["currentVersionId"], saved["currentVersionId"])
        self.assertEqual(len(again["versions"]), 2)
        with self.assertRaisesRegex(writing.EditorError, "请求 ID"):
            self.call("ui-save", **dict(payload, text="另一个正文"))
        with self.assertRaisesRegex(writing.EditorError, "所选保存版本"):
            self.call("ui-save", **dict(payload, requestId="stale-save"))
        with self.assertRaisesRegex(writing.EditorError, "编辑草稿"):
            self.call("ui-write", clientId="writer-a", expectedEditorRevision=0, versionId="v1", text="冲突", ui={})
        selected = self.call("select", expectedRevision=saved["revision"], versionId="v1")
        with self.assertRaisesRegex(writing.EditorError, "此前保存已完成"):
            self.call("ui-save", **payload)
        self.assertEqual(console.current(self.store)["version_id"], "v1")
        self.assertEqual(self.read()["revision"], selected["revision"])

    def test_replay_after_newer_draft_or_save_cannot_clear_current_text(self):
        state = self.draft()
        payload = self.save_payload(state)
        self.call("ui-save", **payload)
        newer = self.draft("第二轮未保存草稿")
        with self.assertRaisesRegex(writing.EditorError, "此前保存已完成"):
            self.call("ui-save", **payload)
        self.assertEqual(self.read()["editor"]["drafts"]["v2"], "第二轮未保存草稿")
        saved = self.call("ui-save", **self.save_payload(newer, "第二轮未保存草稿", "save-2"))
        with self.assertRaisesRegex(writing.EditorError, "此前保存已完成"):
            self.call("ui-save", **payload)
        self.assertEqual(console.current(self.store)["content"], "第二轮未保存草稿")
        self.assertEqual(self.read()["currentVersionId"], saved["currentVersionId"])

    def test_capacity_counts_historical_bodies_and_candidate_without_writing(self):
        source = self.root / "capacity.txt"
        source.write_bytes(("甲" * 1000).encode("utf-8"))
        console.publish(self.store, self.document, "script", "测试稿", source)
        before = (self.store / "manifest.json").read_bytes()
        self.assertLess(len(before), 5000)
        state = self.read()
        with patch.object(writing, "MAX_STORE", 5000):
            with self.assertRaisesRegex(writing.EditorError, "版本正文与清单"):
                self.call("ui-save", **self.save_payload(state, "乙" * 1000))
        self.assertEqual((self.store / "manifest.json").read_bytes(), before)
        self.assertFalse((self.store / "versions/episode-01-script/v3.txt").exists())
        self.assertEqual(console.current(self.store)["content"], "甲" * 1000)

    def test_write_failure_keeps_draft_and_saved_version(self):
        state = self.draft()
        before = (self.store / "manifest.json").read_bytes()
        with patch.object(console, "write_manifest", side_effect=OSError("disk failure")):
            with self.assertRaisesRegex(OSError, "disk failure"):
                self.call("ui-save", **self.save_payload(state))
        self.assertEqual((self.store / "manifest.json").read_bytes(), before)
        self.assertFalse((self.store / "versions/episode-01-script/v2.txt").exists())
        self.assertEqual(self.read()["editor"]["drafts"]["v1"], "新草稿\n保留整篇")

    def test_accept_instruction_changes_only_persistent_signal(self):
        self.draft("仍然保留的草稿")
        before = json.loads((self.store / "manifest.json").read_bytes())
        plain = console.current(self.store, self.document)
        self.assertNotIn("instruction_revision", plain)
        self.assertEqual(json.loads((self.store / "manifest.json").read_bytes()), before)
        accepted = console.current(self.store, self.document, accept_instruction=True)
        self.assertEqual(accepted["instruction_revision"], 1)
        self.assertEqual(accepted["content"], self.original)
        self.assertEqual(accepted["document_revision"], plain["document_revision"])
        self.assertEqual(accepted["current_version_id"], plain["current_version_id"])
        after = json.loads((self.store / "manifest.json").read_bytes())
        self.assertEqual(after["documents"][self.document].pop("writing_instruction_revision"), 1)
        self.assertEqual(after, before)
        again = console.current(self.store, self.document, accept_instruction=True)
        self.assertEqual(again["instruction_revision"], 2)
        self.assertEqual(self.read()["editor"]["drafts"]["v1"], "仍然保留的草稿")
        latest = (self.store / "manifest.json").read_bytes()
        with patch.object(console, "write_manifest", side_effect=OSError("disk failure")):
            with self.assertRaisesRegex(OSError, "disk failure"):
                console.current(self.store, self.document, accept_instruction=True)
        self.assertEqual((self.store / "manifest.json").read_bytes(), latest)
        # A corrupt current body cannot acknowledge an instruction.
        (self.store / "versions/episode-01-script/v1.txt").write_bytes(b"external edit")
        with self.assertRaisesRegex(console.ConsoleError, "摘要不符"):
            console.current(self.store, self.document, accept_instruction=True)
        self.assertEqual((self.store / "manifest.json").read_bytes(), latest)

    def test_restart_requires_explicit_draft_identity_without_mixing_clients(self):
        self.draft("甲方未保存草稿🙂", "writer-a")
        self.draft("乙方独立未保存草稿", "writer-b")
        metadata = writing.list_drafts(self.store, self.document)
        self.assertEqual(metadata["draft_clients"], [
            {"client_id": "writer-a", "version_id": "v1", "characters": 8, "bytes": 25},
            {"client_id": "writer-b", "version_id": "v1", "characters": 9, "bytes": 27},
        ])
        self.assertNotIn("甲方", json.dumps(metadata, ensure_ascii=False))
        before = (self.store / "manifest.json").read_bytes()
        fresh = writing.create_server(self.store, self.document)
        resumed = writing.create_server(self.store, self.document, "writer-a")
        try:
            self.assertEqual(urlsplit(fresh.editor_url).fragment, "")
            self.assertEqual(urlsplit(resumed.editor_url).fragment, "client=writer-a")
            self.assertNotEqual(urlsplit(fresh.editor_url).path, urlsplit(resumed.editor_url).path)
            self.assertEqual(self.read("new-window")["editor"]["drafts"], {})
            self.assertEqual(self.read("writer-a")["editor"]["drafts"], {"v1": "甲方未保存草稿🙂"})
            self.assertEqual(self.read("writer-b")["editor"]["drafts"], {"v1": "乙方独立未保存草稿"})
            self.assertEqual(resumed.editor_draft_clients, metadata["draft_clients"])
        finally:
            fresh.server_close()
            resumed.server_close()
        self.assertEqual((self.store / "manifest.json").read_bytes(), before)
        for unknown in ("writer-missing", "../outside", "__proto__"):
            with self.assertRaises(console.ConsoleError):
                writing.create_server(self.store, self.document, unknown)

    def test_long_text_and_invalid_data(self):
        long_text = "完整正文🙂\n" * 20000
        state = self.draft(long_text)
        saved = self.call("ui-save", **self.save_payload(state, long_text))
        self.assertEqual(saved["text"], long_text)
        self.assertEqual(console.current(self.store)["content"], long_text)
        for payload in ({"clientId": "../outside"}, {"clientId": "__proto__"},
                        {"clientId": "writer-a", "consoleId": "another-doc"}):
            with self.assertRaises(console.ConsoleError):
                self.call("ui-read", **payload)
        for value in ("x" * (writing.MAX_TEXT + 1), "\ud800", 42):
            with self.assertRaises(console.ConsoleError):
                self.draft(value)
        with self.assertRaises(console.ConsoleError):
            self.call("ui-write", clientId="writer-a", expectedEditorRevision=saved["editor"]["revision"],
                      versionId="v2", text="valid", ui={"scrollTop": float("nan")})

    def test_http_security_and_save(self):
        server = writing.create_server(self.store, self.document)
        thread = threading.Thread(target=server.serve_forever, daemon=True)
        thread.start()
        url = urlsplit(server.editor_url)

        def request(method, suffix, value=None, headers=None):
            connection = http.client.HTTPConnection("127.0.0.1", server.server_port, timeout=5)
            body = json.dumps(value).encode() if value is not None else None
            all_headers = {"Origin": f"http://{url.netloc}", "Content-Type": "application/json"}
            all_headers.update(headers or {})
            connection.request(method, url.path + suffix, body, all_headers)
            response = connection.getresponse()
            result = (response.status, response.read(), dict(response.getheaders()))
            connection.close()
            return result

        try:
            status, body, headers = request("GET", "api/health")
            self.assertEqual(status, 200)
            self.assertEqual(json.loads(body)["document_revision"], 1)
            self.assertEqual(json.loads(body)["current_version_id"], "v1")
            self.assertEqual(json.loads(body)["instruction_revision"], 0)
            console.current(self.store, self.document, accept_instruction=True)
            status, body, _ = request("GET", "api/health")
            self.assertEqual(status, 200)
            self.assertEqual(json.loads(body)["instruction_revision"], 1)
            self.assertNotIn("Access-Control-Allow-Origin", headers)
            status, body, _ = request("POST", "api/ui-read", {"clientId": "browser-1"})
            self.assertEqual(status, 200)
            self.assertEqual(json.loads(body)["text"], self.original)
            for suffix in ("../manifest.json", "%2e%2e/manifest.json", "api/unknown", "C:/Windows/win.ini"):
                self.assertEqual(request("GET", suffix)[0], 404)
            self.assertEqual(request("GET", "api/health", headers={"Host": "attacker.test"})[0], 403)
            self.assertEqual(request("POST", "api/ui-read", {"clientId": "a"}, {"Origin": "https://attacker.test"})[0], 403)
            self.assertEqual(request("POST", "api/ui-read", {"clientId": "a"}, {"Content-Type": "text/plain"})[0], 415)
            self.assertEqual(request("POST", "api/ui-read", ["not-an-object"])[0], 400)
            self.assertEqual(request("POST", "api/ui-read", {"clientId": "a"}, {"Content-Length": str(writing.MAX_BODY + 1)})[0], 413)
            state = self.read()
            status, body, _ = request("POST", "api/ui-save", self.save_payload(state, "HTTP真实保存"))
            self.assertEqual(status, 200)
            self.assertEqual(json.loads(body)["text"], "HTTP真实保存")
            self.assertEqual(console.current(self.store)["content"], "HTTP真实保存")
        finally:
            server.shutdown()
            server.server_close()
            thread.join(2)


if __name__ == "__main__":
    unittest.main(verbosity=2)
