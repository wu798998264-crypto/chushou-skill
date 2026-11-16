#!/usr/bin/env python3
"""Launch the isolated YunX/Electron video parser and optional Chushou inspection."""
import argparse
import hashlib
import json
import os
import shutil
from pathlib import Path
import subprocess
import sys

ROOT = Path(__file__).resolve().parent


def verify_yunx(root):
    root = root.resolve()
    manifest = json.loads((root / 'runtime-manifest.json').read_text(encoding='utf-8'))
    if manifest.get('version') != '1.3.4' or not manifest.get('files'):
        raise ValueError('Unrecognized YunX runtime manifest')
    for entry in manifest['files']:
        path = (root / entry['path']).resolve()
        if not path.is_relative_to(root) or not path.is_file():
            raise ValueError('Missing or invalid YunX runtime file')
        with path.open('rb') as stream:
            digest = hashlib.file_digest(stream, 'sha256').hexdigest()
        if path.stat().st_size != entry['size'] or digest != entry['sha256']:
            raise ValueError('YunX runtime integrity failed: ' + entry['path'])
    for name in ('LICENSE', 'BUILD.md', 'yunx-corresponding-source.zip'):
        if not (root / name).is_file(): raise ValueError('Missing YunX license or corresponding source')


def media_tool(value, name):
    found = shutil.which(value or name)
    if not found:
        raise ValueError(name + ' not found; install it separately or supply --' + name)
    return str(Path(found).resolve(strict=True))


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--source-file', required=True)
    parser.add_argument('--out-dir', required=True)
    parser.add_argument('--inspector', help='Path to existing Chushou inspect_video.py')
    parser.add_argument('--download-only', action='store_true')
    parser.add_argument('--option-id', help='Option returned by a prior selection_required result')
    parser.add_argument('--option-label', help='Matching label returned with --option-id')
    parser.add_argument('--selection-key', help='Fingerprint returned with the same option list')
    parser.add_argument('--interval', type=float, default=1.0)
    parser.add_argument('--ffmpeg', help='Path to separately installed ffmpeg executable')
    parser.add_argument('--ffprobe', help='Path to separately installed ffprobe executable')
    args = parser.parse_args()
    try:
        if args.interval <= 0: raise ValueError('Interval must be positive')
        if len({bool(args.option_id),bool(args.option_label),bool(args.selection_key)}) != 1: raise ValueError('--option-id, --option-label and --selection-key must be supplied together')
        if not args.download_only and not args.inspector: raise ValueError('--inspector is required unless --download-only')
        source = Path(args.source_file).resolve(strict=True)
        out = Path(args.out_dir).resolve(); out.mkdir(parents=True, exist_ok=True)
        verify_yunx(ROOT / 'resources/yunx')
        ffmpeg = media_tool(args.ffmpeg, 'ffmpeg')
        ffprobe = media_tool(args.ffprobe, 'ffprobe')
        command = [str(ROOT/'electron/electron.exe'), str(ROOT/'download.cjs'), str(source), str(out)]
        if args.option_id: command += [args.option_id, args.option_label, args.selection_key]
        env = os.environ.copy(); env.pop('ELECTRON_RUN_AS_NODE', None)
        env['SHENSI_FFMPEG_PATH'] = ffmpeg
        env['SHENSI_FFPROBE_PATH'] = ffprobe
        run = subprocess.run(command, capture_output=True, text=True, encoding='utf-8', errors='replace', timeout=900, env=env)
        for line in run.stderr.splitlines(): print(line, file=sys.stderr)
        lines = [line for line in run.stdout.splitlines() if line.startswith('{')]
        if not lines:
            failures = [line for line in run.stderr.splitlines() if line.startswith('{')]
            if failures:
                failure = json.loads(failures[-1]); raise ValueError(failure.get('error', 'Electron parser failed'))
            raise ValueError('Electron parser exited without a result (exit code '+str(run.returncode)+')')
        result = json.loads(lines[-1])
        if result.get('status') != 'downloaded':
            print(json.dumps(result, ensure_ascii=False)); return 2
        video = Path(result['video']).resolve(strict=True)
        if not video.is_relative_to(out): raise ValueError('Downloaded file is outside output directory')
        with video.open('rb') as stream: digest = hashlib.file_digest(stream, 'sha256').hexdigest()
        if digest != result['sha256'] or video.stat().st_size != result['bytes']:
            raise ValueError('Downloaded file failed integrity recheck')
        if not args.download_only:
            inspection = subprocess.run([sys.executable,'-X','utf8',str(Path(args.inspector).resolve(strict=True)),
                '--video',str(video),'--out-dir',str(video.parent/'analysis'),
                '--ffmpeg',ffmpeg, '--ffprobe',ffprobe, '--interval',str(args.interval)],
                capture_output=True,text=True,encoding='utf-8',errors='replace',timeout=900)
            if inspection.returncode:
                raise ValueError('Video downloaded but inspection failed: '+inspection.stderr[-800:])
            data=json.loads(inspection.stdout)
            result['analysis']=data.get('analysis'); result['inspection']=data
        print(json.dumps(result,ensure_ascii=False,indent=2)); return 0
    except (OSError,ValueError,KeyError,subprocess.SubprocessError) as error:
        print(json.dumps({'status':'failed','error':str(error)},ensure_ascii=False),file=sys.stderr); return 2


if __name__ == '__main__': sys.exit(main())
