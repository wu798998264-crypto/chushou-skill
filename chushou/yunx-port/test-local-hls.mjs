import {mkdtemp,readFile} from 'node:fs/promises';
import {join,resolve} from 'node:path';
import {createServer} from 'node:http';
import {spawnSync} from 'node:child_process';
import {downloadYunxHls} from './apps/desktop/yunx-hls.mjs';
const ffmpeg=resolve('resources/ffmpeg/ffmpeg.exe'),ffprobe=resolve('resources/ffmpeg/ffprobe.exe');
const root=await mkdtemp(resolve('../hls-test-'));
const video=join(root,'fixture.ts');
let run=spawnSync(ffmpeg,['-v','error','-f','lavfi','-i','testsrc=size=128x96:rate=10','-f','lavfi','-i','sine=frequency=440','-t','2','-c:v','mpeg2video','-c:a','mp2','-f','mpegts',video]);
if(run.status!==0)throw Error('Synthetic fixture generation failed');
const server=createServer(async(req,res)=>{if(req.url==='/index.m3u8'){res.writeHead(200,{'Content-Type':'application/vnd.apple.mpegurl'});res.end('#EXTM3U\n#EXT-X-VERSION:3\n#EXT-X-TARGETDURATION:2\n#EXTINF:2.0,\nfixture.ts\n#EXT-X-ENDLIST\n');}else if(req.url==='/fixture.ts'){res.writeHead(200,{'Content-Type':'video/mp2t'});res.end(await readFile(video));}else{res.writeHead(404);res.end();}});
await new Promise(r=>server.listen(0,'127.0.0.1',r));
try{
 const result=join(root,'output.mp4');
 await downloadYunxHls(`http://127.0.0.1:${server.address().port}/index.m3u8`,result,{directory:root,ffmpeg,allowTestLoopback:true});
 run=spawnSync(ffmpeg,['-nostdin','-v','error','-xerror','-i',result,'-map','0:v:0','-map','0:a:0','-f','null','-']);
 if(run.status!==0)throw Error('Full audio/video decode failed');
 const probe=spawnSync(ffprobe,['-v','error','-show_entries','stream=codec_type','-of','json',result],{encoding:'utf8'});
 const types=JSON.parse(probe.stdout).streams.map(x=>x.codec_type);
 if(!types.includes('video')||!types.includes('audio'))throw Error('Missing stream');
 console.log('PASS: loopback HLS fetch, relative segment resolution, merge and full A/V decode');
}finally{await new Promise(r=>server.close(r));}
