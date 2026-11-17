"""Explicit release acceptance; downloads only the requested first episode."""
import argparse
import json
import platform
import sys
import tempfile
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from download_drama import download_selection

parser = argparse.ArgumentParser()
parser.add_argument('--output', required=True)
args = parser.parse_args()
output = Path(args.output).resolve()
output.mkdir(parents=True, exist_ok=True)
started = time.monotonic()
report = {'passed': False, 'platform': sys.platform, 'arch': platform.machine(), 'realDownload': True}
try:
    with tempfile.TemporaryDirectory(prefix='chushou-real-') as folder:
        result = download_selection('7690184933037329432', Path(folder), '1')
        assert result['metadata']['validation'] == 'full_video_and_available_audio_decode'
        assert result['metadata']['duration_seconds'] > 330
        assert result['metadata']['has_audio']
        assert result['completed_episodes'] == 1
        replay = download_selection('7690184933037329432', Path(folder), '1')
        assert replay['status'] == 'reused'
        report.update(passed=True, title=result['title'], metadata=result['metadata'],
                      sha256=result['episodes'][0]['sha256'], bytes=Path(result['video']).stat().st_size,
                      elapsedSeconds=round(time.monotonic()-started, 3), replayed=True)
finally:
    (output / ('real-'+sys.platform+'.json')).write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding='utf-8')
    print(json.dumps(report, ensure_ascii=True))
