"""Decode motion ranges sequentially and record real selected-frame timestamps."""
import argparse
import json
import math
from pathlib import Path
import re
import subprocess
import inspect_video


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--analysis-file',required=True,help='Existing inspect_video analysis.json')
    parser.add_argument('--range',action='append',required=True,help='START:END:SAMPLES_PER_SECOND; may repeat')
    parser.add_argument('--out-dir',required=True)
    args=parser.parse_args()
    previous=json.loads(Path(args.analysis_file).read_text(encoding='utf-8-sig'))
    source=Path(previous['source']).resolve()
    if not source.is_file():
        raise ValueError('Original local video does not exist')
    info=previous['metadata']
    index=info.get('video_stream_index')
    if index is None:
        raise ValueError('Identify the moving video stream before motion inspection')
    ranges=[]
    for raw in args.range:
        start,end,rate=map(float,raw.split(':'))
        if not all(math.isfinite(v) for v in (start,end,rate)) or start<0 or end<=start or rate<=0 or rate>info['fps'] or end>info['duration_seconds']:
            raise ValueError('Ranges must be positive and cannot exceed original frame rate')
        ranges.append({'start_seconds':start,'end_seconds':end,'sample_fps':rate})
    out=inspect_video.output_directory(args.out_dir)
    frames_dir=out/'frames';frames_dir.mkdir()
    expressions=[f'between(t,{r["start_seconds"]},{r["end_seconds"]})*(isnan(prev_selected_t)+gte(t-prev_selected_t,{1/r["sample_fps"]-0.000001:.9f}))' for r in ranges]
    selection='+'.join(expressions)
    command=[previous['tools']['ffmpeg'],'-nostdin','-hide_banner','-loglevel','info','-n',
        '-protocol_whitelist','file,pipe','-i',str(source),'-t',str(max(r['end_seconds'] for r in ranges)+0.1),
        '-map',f'0:{index}','-an','-vf',f"select='{selection}',showinfo",'-vsync','0','-q:v','2',str(frames_dir/'motion-%05d.jpg')]
    result=subprocess.run(command,capture_output=True,text=True,encoding='utf-8',errors='replace',shell=False)
    (out/'decode.log').write_text(result.stderr,encoding='utf-8')
    if result.returncode:
        raise RuntimeError(result.stderr[-1800:])
    frames=[]
    for line in result.stderr.splitlines():
        match=re.search(r'showinfo[^]]*\].*\bn:\s*(\d+).*\bpts_time:\s*([-\d.eE+]+)',line)
        if not match:
            continue
        number=int(match[1]);pts=float(match[2]);path=frames_dir/f'motion-{number+1:05d}.jpg'
        if path.is_file():
            frames.append({'decoded_pts_seconds':pts,'requested_timestamp_seconds':pts,'path':str(path)})
    if not frames or len(frames)!=len(list(frames_dir.glob('motion-*.jpg'))):
        raise ValueError('Frame files and actual PTS records do not match; do not guess timecodes')
    sheets,note=inspect_video.contact_sheets(frames,out)
    report={'source':str(source),'metadata':info,'tools':previous['tools'],'ranges':ranges,'frames':frames,
        'contact_sheets':sheets,'contact_sheet_note':note,
        'method':'sequential_decode_select_showinfo_passthrough',
        'notes':['No interpolated or duplicated frames. Captions record decoded PTS.',
                 'Contact sheets are only an overview; inspect original-resolution frames for contacts and identity.',
                 'Cut detection and camera/subject-motion interpretation still require visual sequence review.']}
    (out/'analysis.json').write_text(json.dumps(report,ensure_ascii=False,indent=2)+'\n',encoding='utf-8')
    print(json.dumps({'analysis':str(out/'analysis.json'),'frames':len(frames),'sheets':len(sheets)},ensure_ascii=False))


if __name__=='__main__':
    main()
