"""Focused offline acceptance: source parsing, byte integrity, resume, handoff.

Run with Python 3.11+ and FFmpeg on PATH, imageio_ffmpeg, or CHUSHOU_TEST_FFMPEG.
No production provider or account is called. A local HTTP server serves a tiny
generated MP4 so HTTP download, decoding and inspection all execute for real.
"""
import functools
import http.server
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import threading
import unittest
from types import SimpleNamespace
from unittest.mock import patch

if __package__ in (None, ""):
    sys.path.insert(0, str(Path(__file__).resolve().parent))

from download_drama import download_selection, ffmpeg_path
from fruitknife_provider import (DownloadError, FruitknifeClient, choose_quality, is_hongguo_source,
                                parse_episodes, parse_series_page, series_id_from)


class QuietHandler(http.server.SimpleHTTPRequestHandler):
    def log_message(self, *args):
        pass


class FixtureClient:
    def __init__(self, url, fail=()):
        self.url, self.fail, self.calls = url, set(fail), []

    def inspect(self, source):
        return {"provider": "hongguo", "series_id": "1234567890123456789", "title": "离线测试{剧}",
                "source_url": "https://hongguoduanju.com/player/1234567890123456789", "total_episodes": 3,
                "episodes": [{"episode": i, "video_id": "fixture-" + str(i)} for i in range(1, 4)],
                "upstream_commit": "offline-fixture"}

    def resolve_episode(self, video_id, quality):
        self.calls.append(video_id)
        if video_id in self.fail:
            raise DownloadError("Fixture episode failed before download")
        return {"url": self.url, "key": None, "actual_quality": "360p"}


class FruitknifeChecks(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.tmp = tempfile.TemporaryDirectory(prefix="chushou-fruitknife-")
        cls.root = Path(cls.tmp.name)
        cls.ffmpeg = ffmpeg_path(os.environ.get("CHUSHOU_TEST_FFMPEG"))
        clip = cls.root / "sample.mp4"
        subprocess.run([cls.ffmpeg, "-nostdin", "-hide_banner", "-loglevel", "error", "-f", "lavfi",
                        "-i", "color=c=blue:s=320x180:r=12", "-f", "lavfi", "-i", "sine=frequency=440:sample_rate=16000",
                        "-t", "1", "-c:v", "libx264", "-pix_fmt", "yuv420p", "-c:a", "aac", str(clip)], check=True)
        cls.server = http.server.ThreadingHTTPServer(("127.0.0.1", 0), functools.partial(QuietHandler, directory=str(cls.root)))
        cls.thread = threading.Thread(target=cls.server.serve_forever, daemon=True)
        cls.thread.start()
        cls.url = f"http://127.0.0.1:{cls.server.server_port}/sample.mp4"
        (cls.root / "not-video.mp4").write_text("<html>not a video</html>", encoding="utf-8")

    @classmethod
    def tearDownClass(cls):
        cls.server.shutdown()
        cls.server.server_close()
        cls.thread.join()
        cls.tmp.cleanup()

    def test_share_and_episode_scope(self):
        sid = "123456789012345678901"
        from urllib.parse import urlencode
        link = "https://novelquickapp.com/s/x/?" + urlencode({"zlink": "https://example.test/?" + urlencode({"schemeParams": json.dumps({"vid": sid})})})
        self.assertEqual(series_id_from(link), sid)
        self.assertTrue(is_hongguo_source("分享 " + link))
        self.assertFalse(is_hongguo_source("https://novelquickapp.com.evil.test/s/x"))
        self.assertEqual(parse_episodes("3,1-2,2", 3), [1, 2, 3])
        for value in (None, "0", "4", "3-1", "../1", "1,,2"):
            with self.assertRaises(DownloadError):
                parse_episodes(value, 3)
        self.assertEqual(parse_episodes(None, 3, True), [1, 2, 3])

    def test_parser_preserves_strings_and_all_episodes(self):
        ids = [str(100000000000 + i) for i in range(260)]
        data = {"loaderData": {"video-animation-share_page": {"pageData": {"series_data": {"title": '测试 } "剧"', "chapter_ids": ids}}}}}
        series = parse_series_page("<script>window._ROUTER_DATA=" + json.dumps(data) + ";</script>", "12345678901")
        self.assertEqual(series["total_episodes"], 260)
        self.assertEqual(series["title"], '测试 } "剧"')
        self.assertEqual(series["episodes"][-1]["video_id"], ids[-1])
        player = parse_series_page('var a={"vid_list":["a","b"],"series_name":"甲\\"乙"};', "12345678901")
        self.assertEqual(player["title"], '甲"乙')

    def test_quality_fallback_is_explicit(self):
        self.assertEqual(choose_quality({"video_3": {"main_url": "https://example.test/a.mp4"}}, "1080p")[0], "540p")
        with self.assertRaises(DownloadError):
            choose_quality({}, "1080p")

    def test_access_and_response_errors_are_not_empty_search_results(self):
        client = FruitknifeClient()
        with patch.object(client.session, "request", return_value=SimpleNamespace(status_code=403)) as request:
            with self.assertRaisesRegex(DownloadError, "403"):
                client.request("GET", "https://hongguoduanju.com/player/12345678901")
            self.assertEqual(request.call_count, 1)
        with patch.object(client, "sign", return_value=({}, "https://example.test/")), \
             patch.object(client, "request", return_value=SimpleNamespace(json=lambda: [])):
            with self.assertRaisesRegex(DownloadError, "JSON response format"):
                client.signed_json("https://example.test/", {}, None, {"ua": "fixture"})

    def test_failed_episode_resumes_and_tamper_is_not_reused(self):
        directory = self.root / "resume"
        broken = FixtureClient(self.url, fail=["fixture-3"])
        first = download_selection("1234567890123456789", directory, "1,3", ffmpeg=self.ffmpeg, client=broken)
        self.assertEqual(first["status"], "partial_failure")
        self.assertEqual(first["completed_episodes"], 1)
        ready = FixtureClient(self.url)
        second = download_selection("1234567890123456789", directory, "1,3", ffmpeg=self.ffmpeg, client=ready)
        self.assertEqual([item["status"] for item in second["episodes"]], ["reused", "downloaded"])
        self.assertEqual(ready.calls, ["fixture-3"])
        old = Path(second["episodes"][0]["video"])
        payload = old.read_bytes()
        old.write_bytes(bytes([payload[0] ^ 1]) + payload[1:])  # Same size, different hash.
        third_client = FixtureClient(self.url)
        third = download_selection("1234567890123456789", directory, "1,3", ffmpeg=self.ffmpeg, client=third_client)
        self.assertEqual(third_client.calls, ["fixture-1"])
        self.assertEqual(third["episodes"][0]["status"], "downloaded")
        self.assertNotEqual(third["episodes"][0]["video"], str(old))
        self.assertTrue(old.exists())  # Preserve the earlier file; no blind overwrite.

    def test_real_inspection_handoff_and_false_media_rejection(self):
        good = download_selection("1234567890123456789", self.root / "inspect", "2", download_only=False,
                                  ffmpeg=self.ffmpeg, interval=0.5, client=FixtureClient(self.url))
        self.assertEqual(good["status"], "downloaded")
        self.assertTrue(Path(good["video"]).is_file())
        self.assertTrue(Path(good["analysis"]).is_file())
        self.assertEqual(good["metadata"]["validation"], "full_video_and_available_audio_decode")
        self.assertTrue(good["metadata"]["has_audio"])
        bad_url = self.url.replace("sample.mp4", "not-video.mp4")
        bad = download_selection("1234567890123456789", self.root / "bad", "1", ffmpeg=self.ffmpeg, client=FixtureClient(bad_url))
        self.assertEqual(bad["status"], "partial_failure")
        self.assertFalse(bad["selection_complete"])

    def test_existing_generic_downloader_still_works(self):
        result = subprocess.run([sys.executable, "-X", "utf8", str(Path(__file__).with_name("download_video.py")),
                                 "--source", self.url, "--out-dir", str(self.root / "generic"),
                                 "--download-only", "--ffmpeg", self.ffmpeg], capture_output=True, text=True, encoding="utf-8")
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(json.loads(result.stdout)["status"], "downloaded")


if __name__ == "__main__":
    unittest.main(verbosity=2)
