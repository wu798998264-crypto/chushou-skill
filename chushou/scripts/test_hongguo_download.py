"""Portable downloader contract checks, with no external requests."""
import hashlib
import json
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parent))
from hongguo_provider import HongguoClient, parse_episodes, is_hongguo_source
from download_video import DownloadError
from download_drama import download_selection


class HongguoTests(unittest.TestCase):
    def test_selection_and_catalog_identity(self):
        self.assertEqual(parse_episodes("1,3-4", 4), [1, 3, 4])
        self.assertEqual(parse_episodes(None, 4, True), [1, 2, 3, 4])
        for value in [None, "0", "1-5", "bad"]:
            with self.assertRaises(DownloadError):
                parse_episodes(value, 4)
        self.assertTrue(is_hongguo_source("https://novelquickapp.com/s/example/"))
        self.assertFalse(is_hongguo_source("https://novelquickapp.com.evil.invalid/s/"))
        client = HongguoClient()
        with patch.object(client, "_call", return_value={"series_id": "1234567890123456789", "series_title": "合成剧", "episodes": [{"vid": "2234567890123456789", "vid_index": 1, "content_type": "1004"}]}):
            result = client.inspect("1234567890123456789")
        self.assertEqual(result["total_episodes"], 1)
        self.assertEqual(result["episodes"][0]["video_id"], "2234567890123456789")
        with self.assertRaises(DownloadError):
            client.resolve_episode("9999999999999999999")

    def test_native_signer_and_network_boundary(self):
        node = shutil.which("node")
        self.assertTrue(node, "Node.js 22+ is required")
        vendor = Path(__file__).resolve().parent / "hongguo_vendor"
        source = "const {coreSixgod}=require('./sixgod');const a=coreSixgod({surl:'https://example.com/test',params:{aid:'8662'},devices:{},header:{},now:1700000000,rng:()=>1});if(!a.headers['x-gorgon']||!a.headers['x-medusa'])process.exit(2);require('./http.cjs').get('http://127.0.0.1/').then(()=>process.exit(3),()=>process.stdout.write('blocked'));"
        result = subprocess.run([node, "-e", source], cwd=vendor, capture_output=True, text=True, timeout=20)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(result.stdout, "blocked")

    def test_download_decode_reuse_and_corruption(self):
        from chushou_runtime import activate_packages
        activate_packages()
        from inspect_video import find_ffmpeg
        ffmpeg = find_ffmpeg(None)
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            video = root / "fixture.mp4"
            subprocess.run([ffmpeg, "-v", "error", "-f", "lavfi", "-i", "color=c=blue:s=64x64:d=0.5", "-c:v", "libx264", str(video)], check=True)
            client = HongguoClient()
            catalog = {"provider": "hongguo", "series_id": "1234567890123456789", "title": "合成剧", "source_url": None, "total_episodes": 1, "episodes": [{"video_id": "2234567890123456789"}], "upstream_commit": "synthetic"}
            def copy_media(url, target, referer, timeout):
                target.write_bytes(video.read_bytes())
                return {"mode": "isolated_fixture"}
            with patch.object(client, "inspect", return_value=catalog), patch.object(client, "resolve_episode", return_value={"url": "https://fixture.invalid/video", "actual_quality": "source-best"}), patch("download_drama.direct_download", side_effect=copy_media):
                first = download_selection("synthetic", root / "out", "1", ffmpeg=ffmpeg, client=client)
                second = download_selection("synthetic", root / "out", "1", ffmpeg=ffmpeg, client=client)
                self.assertEqual(first["status"], "downloaded")
                self.assertEqual(second["status"], "reused")
                self.assertEqual(first["metadata"]["width"], 64)
                Path(first["video"]).write_bytes(b"corrupt")
                third = download_selection("synthetic", root / "out", "1", ffmpeg=ffmpeg, client=client)
                self.assertEqual(third["status"], "downloaded")
                self.assertNotEqual(first["video"], third["video"])
                self.assertEqual(hashlib.sha256(Path(third["video"]).read_bytes()).hexdigest(), third["episodes"][0]["sha256"])


if __name__ == "__main__":
    unittest.main()
