import contextlib
import io
from pathlib import Path
import tempfile
import unittest

import shensi_transcription as cli


class FakeShensi:
    def __init__(self, replies):
        self.replies = {key: list(value) for key, value in replies.items()}
        self.calls = []

    def api(self, route, body):
        self.calls.append((route, body))
        key = route.rsplit("/", 1)[-1]
        replies = self.replies[key]
        reply = replies.pop(0) if len(replies) > 1 else replies[0]
        return {"ok": True, "result": reply}


class TranscriptionTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix="chushou-asr-cli-")
        self.root = Path(self.temp.name)
        self.binding = self.root / "binding.json"
        cli.write_json(self.binding, {"workspaceKind": "notebook", "workspacePath": str(self.root / "notebook")})
        self.scope = {"workspaceKind": "notebook", "workspacePath": str(self.root / "notebook"), "documentId": "board-real"}

    def tearDown(self):
        self.temp.cleanup()

    def args(self, *parts):
        return cli.parser().parse_args(["--binding-file", str(self.binding), "--document-id", "board-real", *parts])

    def test_start_all_uses_sources_and_writes_idempotent_receipt(self):
        job = {"id": "job-1", "nodeId": "n1", "status": "queued", "text": "should not print"}
        fake = FakeShensi({"sources": [{"items": [{"nodeId": "n1", "name": "one", "kind": "video"}, {"nodeId": "n1"}]}], "start": [{"jobs": [job]}]})
        args = self.args("start", "--all", "--request-id", "test_batch_01", "--confirm-cloud-upload", "--out", str(self.root / "receipt.json"))
        result, code = cli.run(args, fake)
        self.assertEqual(code, 0)
        self.assertEqual(fake.calls[1][1], {**self.scope, "nodeIds": ["n1"], "requestId": "test_batch_01", "cloudUploadConfirmed": True})
        self.assertEqual(cli.receipt_ids(self.root / "receipt.json", self.scope), ["job-1"])
        self.assertNotIn("text", result["jobs"][0])
        self.assertEqual(cli.run(args, fake)[1], 0)

    def test_upload_confirmation_and_wait_target_are_mandatory(self):
        with contextlib.redirect_stderr(io.StringIO()):
            with self.assertRaises(SystemExit):
                self.args("start", "--node-id", "n1", "--request-id", "test_batch", "--out", str(self.root / "r.json"))
            with self.assertRaises(SystemExit):
                self.args("wait")
        with self.assertRaises(ValueError):
            cli.wait_jobs(FakeShensi({}), self.scope, [])

    def test_wait_filters_requested_ids_and_gets_no_transcript(self):
        fake = FakeShensi({"list": [{"jobs": [{"id": "old", "status": "failed"}, {"id": "wanted", "status": "processing"}]}, {"jobs": [{"id": "old", "status": "failed"}, {"id": "wanted", "status": "completed"}]}]})
        sleeps = []
        result = cli.wait_jobs(fake, self.scope, ["wanted"], sleep=sleeps.append, monotonic=lambda: 0)
        self.assertTrue(result["ok"])
        self.assertEqual(sleeps, [10])
        self.assertEqual([j["id"] for j in result["jobs"]], ["wanted"])
        self.assertTrue(all(route.endswith("/list") for route, _ in fake.calls))

    def test_wait_stops_on_fail_pause_storage_missing_and_timeout(self):
        for status in ("failed", "interrupted", "cancelled"):
            fake = FakeShensi({"list": [{"jobs": [{"id": "wanted", "status": status}]}]})
            result = cli.wait_jobs(fake, self.scope, ["wanted"], sleep=lambda _: self.fail("must not sleep"))
            self.assertEqual(result["state"], "failed")
        for extra, state in (({"paused": True}, "paused"), ({"storageError": "disk full"}, "storage_error")):
            fake = FakeShensi({"list": [{"jobs": [{"id": "wanted", "status": "queued"}], **extra}]})
            self.assertEqual(cli.wait_jobs(fake, self.scope, ["wanted"])["state"], state)
        fake = FakeShensi({"list": [{"jobs": []}]})
        with self.assertRaises(ValueError):
            cli.wait_jobs(fake, self.scope, ["missing"])
        ticks = iter([0, 1])
        fake = FakeShensi({"list": [{"jobs": [{"id": "wanted", "status": "queued"}]}]})
        self.assertEqual(cli.wait_jobs(fake, self.scope, ["wanted"], timeout_seconds=1, monotonic=lambda: next(ticks))["state"], "timeout")

    def test_long_series_wait_runs_without_model_resubmission(self):
        fake = FakeShensi({"list": [{"jobs": [{"id": "wanted", "status": "processing"}]}, {"jobs": [{"id": "wanted", "status": "completed"}]}]})
        ticks = iter([0, 3601])
        result = cli.wait_jobs(fake, self.scope, ["wanted"], timeout_seconds=7200, sleep=lambda _: None, monotonic=lambda: next(ticks))
        self.assertTrue(result["ok"])
        self.assertTrue(all(route.endswith("/list") for route, _ in fake.calls))

    def test_export_safe_utf8_names_with_same_display_name(self):
        jobs = [{"id": "one", "name": "../同名:视频", "status": "completed"}, {"id": "two", "name": "../同名:视频", "status": "completed"}]
        fake = FakeShensi({"get": [{"job": jobs[0], "text": "第一段原文。"}, {"job": jobs[1], "text": "第二段原文。"}]})
        target = self.root / "exports"
        exported = cli.export_results(fake, self.scope, jobs, target)
        self.assertEqual(len(exported), 2)
        self.assertNotEqual(exported[0]["txt"], exported[1]["txt"])
        for item in exported:
            self.assertEqual(Path(item["txt"]).parent, target.resolve())
            self.assertTrue(Path(item["txt"]).read_text(encoding="utf-8").endswith("原文。"))
        self.assertTrue(cli.safe_name("CON", "one").startswith("素材_"))

    def test_no_different_job_overwrite_or_cross_scope_receipt(self):
        target = self.root / "same.json"
        cli.save_json_once(target, {"job": {"id": "one"}, "text": "a"})
        with self.assertRaises(ValueError):
            cli.save_json_once(target, {"job": {"id": "two"}, "text": "b"})
        receipt = self.root / "receipt.json"
        cli.write_json(receipt, {"scope": {**self.scope, "documentId": "other"}, "jobIds": ["one"]})
        with self.assertRaises(ValueError):
            cli.receipt_ids(receipt, self.scope)
        fake = FakeShensi({})
        args = self.args("start", "--node-id", "n1", "--request-id", "test_batch_02", "--confirm-cloud-upload", "--out", str(receipt))
        with self.assertRaises(ValueError):
            cli.run(args, fake)
        self.assertEqual(fake.calls, [])

    def test_get_writes_body_but_only_prints_summary(self):
        fake = FakeShensi({"get": [{"job": {"id": "one", "status": "completed"}, "text": "正文保存在文件，不输出上下文。"}]})
        out = self.root / "raw.json"
        result, code = cli.run(self.args("get", "--job-id", "one", "--out", str(out)), fake)
        self.assertEqual(code, 0)
        self.assertNotIn("text", result)
        self.assertTrue(cli.read_json(out)["text"].startswith("正文"))

    def test_unconfirmed_failed_or_wrong_job_response_is_not_success(self):
        fake = FakeShensi({"cancel": [{"job": {"id": "other", "status": "cancelled"}}]})
        with self.assertRaises(RuntimeError):
            cli.run(self.args("cancel", "--job-id", "one"), fake)
        class Denied:
            def api(self, route, body):
                return {"ok": False, "code": "NO_ACCESS"}
        with self.assertRaises(RuntimeError):
            cli.api(Denied(), self.scope, "cancel", jobId="one")
        for action in ("retry", "cancel"):
            fake = FakeShensi({action: [{"job": {"id": "one", "status": "queued" if action == "retry" else "cancelled"}}]})
            self.assertEqual(cli.run(self.args(action, "--job-id", "one"), fake)[1], 0)


if __name__ == "__main__":
    unittest.main()
