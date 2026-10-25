"""Offline routing, discovery, and no-secret-output checks for the canvas probe."""
import contextlib
import io
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

import shensi_canvas_probe as probe


def instance(port, protocol):
    return {"origin": "http://127.0.0.1:" + str(port), "protocol": protocol}


class MarkerTests(unittest.TestCase):
    def test_notes_marker_ignores_title_and_token(self):
        self.assertEqual(probe.identify_html('<title>legacy 999</title><meta content="secret" NAME="shensi-session"/>'), "notes")

    def test_legacy_marker(self):
        self.assertEqual(probe.identify_html('<meta name="shensi-session-token" content="secret">'), "legacy")

    def test_text_or_comment_is_not_marker(self):
        self.assertEqual(probe.identify_html('shensi-session <!-- <meta name="shensi-session"> -->'), "unknown")

    def test_both_markers_are_ambiguous(self):
        self.assertEqual(probe.identify_html('<meta name="shensi-session"><meta name="shensi-session-token">'), "ambiguous")


class SelectionTests(unittest.TestCase):
    def test_default_notes_beats_legacy(self):
        result = probe.select_instance([instance(1, "legacy"), instance(2, "notes")])
        self.assertEqual(result["selected"]["origin"], "http://127.0.0.1:2")

    def test_legacy_only_does_not_silently_fallback(self):
        result = probe.select_instance([instance(1, "legacy")])
        self.assertEqual(result["status"], "no-default-notes")
        self.assertIsNone(result["selected"])

    def test_same_protocol_requires_explicit_origin(self):
        result = probe.select_instance([instance(1, "notes"), instance(2, "notes")])
        self.assertEqual(result["status"], "multiple-notes-instances")

    def test_explicit_legacy_target_wins_default(self):
        result = probe.select_instance([instance(1, "legacy"), instance(2, "notes")], origin="http://127.0.0.1:1")
        self.assertEqual(result["selected"]["protocol"], "legacy")

    def test_missing_explicit_target_never_falls_back(self):
        result = probe.select_instance([instance(2, "notes")], origin="http://127.0.0.1:1")
        self.assertEqual(result["status"], "target-unavailable")

    def test_explicit_kind_and_origin_must_match(self):
        result = probe.select_instance([instance(1, "legacy")], kind="notes", origin="http://127.0.0.1:1")
        self.assertEqual(result["status"], "target-kind-mismatch")

    def test_explicit_legacy_kind_is_supported(self):
        self.assertEqual(probe.select_instance([instance(1, "legacy")], kind="legacy")["status"], "selected")

    def test_duplicate_origin_is_not_ambiguous(self):
        self.assertEqual(probe.select_instance([instance(1, "notes"), instance(1, "notes")])["status"], "selected")

    def test_unknown_or_ambiguous_protocol_never_selected(self):
        self.assertIsNone(probe.select_instance([instance(1, "unknown"), instance(2, "ambiguous")])["selected"])


class TargetTests(unittest.TestCase):
    def test_reject_remote_credentials_path_query_and_invalid_ports(self):
        for value in ["https://127.0.0.1:10", "http://example.com:10", "http://u:p@127.0.0.1:10",
                      "http://127.0.0.1:0", "http://127.0.0.1:65536", "http://127.0.0.1:1/path",
                      "http://127.0.0.1:1?token=secret", "http://127.0.0.1:1#x"]:
            with self.subTest(value=value), self.assertRaises(probe.ProbeError):
                probe.normalize_origin(value)

    def test_cli_overrides_binding_without_reading_it(self):
        with patch.object(Path, "read_text", side_effect=AssertionError("must not read")):
            target = probe.requested_target("http://127.0.0.1:7", "unused.json", {"SHENSI_ORIGIN": "bad"})
        self.assertEqual(target["source"], "cli")

    def test_binding_target_overrides_environment(self):
        with tempfile.TemporaryDirectory() as folder:
            path = Path(folder) / "binding.json"
            path.write_text(json.dumps({"origin": "http://127.0.0.1:8", "protocol": "legacy"}), encoding="utf-8")
            target = probe.requested_target(binding_file=path, environment={"SHENSI_ORIGIN": "http://127.0.0.1:9"})
        self.assertEqual((target["source"], target["protocol"]), ("binding", "legacy"))

    def test_binding_without_origin_does_not_fallback(self):
        with tempfile.TemporaryDirectory() as folder:
            path = Path(folder) / "binding.json"
            path.write_text('{"workspacePath":"old"}', encoding="utf-8")
            with self.assertRaisesRegex(probe.ProbeError, "binding-missing-origin"):
                probe.requested_target(binding_file=path, environment={"SHENSI_ORIGIN": "http://127.0.0.1:9"})


class HttpTests(unittest.TestCase):
    def test_get_homepage_only_and_no_token_output(self):
        class Response(io.BytesIO):
            status = 200
        class Opener:
            def open(self, request, timeout):
                self_request.append(request)
                return Response(b'<meta name="shensi-session" content="VERY_SECRET">')
        self_request = []
        with patch.object(probe.urllib.request, "build_opener", return_value=Opener()):
            result = probe.probe_origin("http://127.0.0.1:9")
        self.assertEqual(self_request[0].full_url, "http://127.0.0.1:9/")
        self.assertEqual(self_request[0].method, "GET")
        self.assertEqual(result["protocol"], "notes")
        self.assertNotIn("VERY_SECRET", json.dumps(result))
        self.assertEqual(result["capabilities"]["canvasWrite"], "not-probed")

    def test_page_size_is_bounded(self):
        class Response(io.BytesIO):
            status = 200
        opener = unittest.mock.Mock()
        opener.open.return_value = Response(b'x' * (probe.MAX_HTML_BYTES + 1))
        with patch.object(probe.urllib.request, "build_opener", return_value=opener):
            result = probe.probe_origin("http://127.0.0.1:9")
        self.assertEqual(result["error"], "homepage-too-large")

    def test_redirect_refused(self):
        with self.assertRaisesRegex(probe.ProbeError, "redirect-refused"):
            probe._NoRedirect().redirect_request(None, None, 302, "", {}, "https://example.com")

    def test_network_errors_do_not_leak_exception_text(self):
        opener = unittest.mock.Mock()
        opener.open.side_effect = OSError("SECRET")
        with patch.object(probe.urllib.request, "build_opener", return_value=opener):
            result = probe.probe_origin("http://127.0.0.1:9")
        self.assertNotIn("SECRET", json.dumps(result))
        self.assertEqual(result["error"], "homepage-unavailable")


class DiscoveryTests(unittest.TestCase):
    def test_listener_discovery_not_command_line_port_assumption(self):
        result = unittest.mock.Mock(returncode=0, stdout='[59133,33326]', stderr='')
        with patch.object(probe.os, "name", "nt"), patch.object(probe.subprocess, "CREATE_NO_WINDOW", 0, create=True), \
             patch.object(probe.subprocess, "run", return_value=result) as run:
            origins = probe.discover_origins()
        command = run.call_args.args[0][-1]
        self.assertIn("Get-NetTCPConnection", command)
        self.assertIn("OwningProcess", command)
        self.assertNotIn("--port", command)
        self.assertEqual(origins, ["http://127.0.0.1:33326", "http://127.0.0.1:59133"])

    def test_discovery_is_bounded(self):
        result = unittest.mock.Mock(returncode=0, stdout=json.dumps(list(range(1, 35))), stderr='')
        with patch.object(probe.os, "name", "nt"), patch.object(probe.subprocess, "CREATE_NO_WINDOW", 0, create=True), \
             patch.object(probe.subprocess, "run", return_value=result), self.assertRaisesRegex(probe.ProbeError, "too-many-candidates"):
            probe.discover_origins()

    def test_list_mode_reports_legacy_without_default_binding(self):
        output = io.StringIO()
        with patch.dict(probe.os.environ, {}, clear=True), patch.object(probe, "discover_origins", return_value=["http://127.0.0.1:1"]), \
             patch.object(probe, "probe_origin", return_value=instance(1, "legacy")), contextlib.redirect_stdout(output):
            exit_code = probe.main(["--list"])
        result = json.loads(output.getvalue())
        self.assertEqual(exit_code, 0)
        self.assertEqual(result["status"], "no-default-notes")
        self.assertIsNone(result["selected"])


if __name__ == "__main__":
    unittest.main()
