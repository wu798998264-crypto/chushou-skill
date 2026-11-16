import {readFile,writeFile,mkdir,rename} from 'node:fs/promises';
import {join} from 'node:path';
import {transferFile,remoteUrl,runMediaTool} from './video-transfer.mjs';

// Download every HTTP object through the host's DNS/redirect guard. FFmpeg
// receives a fully local VOD playlist and cannot open network protocols.
export async function downloadYunxHls(url,path,{headers={},directory,signal,onProgress=()=>{},allowTestLoopback=false,ffmpeg='ffmpeg',maxBytes=1024**3}={}){
 const root=join(directory,'hls');await mkdir(root,{recursive:true});let bytes=0,serial=0;
 const baseOrigin=new URL(url).origin;
 const safeHeaders=value=>new URL(value).origin===baseOrigin?headers:Object.fromEntries(Object.entries(headers).filter(([k])=>!/^(cookie|authorization)$/i.test(k)));
 const fetch=async(value,file,playlist=false)=>{remoteUrl(value,{allowTestLoopback});const result=await transferFile(value,file,{signal,headers:safeHeaders(value),allowTestLoopback,useSystemProxy:true,allowPlaylist:playlist,maxBytes:playlist?1024**2:Math.max(1,maxBytes-bytes),onProgress:p=>onProgress({message:playlist?'正在读取解析播放清单':'正在下载解析视频分片',bytes:bytes+p.bytes,total:0})});bytes+=result.size;if(bytes>maxBytes)throw Error('视频超过下载大小限制');return file;};
 async function manifest(value,depth=0){
  if(depth>5)throw Error('播放清单嵌套过深');const file=join(root,'playlist-'+serial+++'.txt');await fetch(value,file,true);const source=await readFile(file,'utf8'),lines=source.replace(/^\uFEFF/,'').split(/\r?\n/).map(s=>s.trim()).filter(Boolean);
  if(lines[0]!=='#EXTM3U'||lines.length>20000)throw Error('解析返回的播放清单无效');
  const variants=[];for(let i=0;i<lines.length;i++)if(lines[i].startsWith('#EXT-X-STREAM-INF:')){const next=lines.slice(i+1).find(s=>!s.startsWith('#'));if(next)variants.push({url:new URL(next,value).href,bandwidth:Number(lines[i].match(/BANDWIDTH=(\d+)/)?.[1]||0)});}
  if(variants.length){if(lines.some(l=>l.startsWith('#EXT-X-MEDIA:')&&/TYPE=AUDIO/.test(l)))throw Error('此播放清单需要独立音轨，未下载无声残缺视频');return manifest(variants.sort((a,b)=>b.bandwidth-a.bandwidth)[0].url,depth+1);}
  if(!lines.includes('#EXT-X-ENDLIST'))throw Error('仅下载已结束的视频，不抓取无限直播');
  let segments=0;const cache=new Map(),local=[];
  async function localFile(target,extension){const resolved=new URL(target,value).href;if(cache.has(resolved))return cache.get(resolved);const name='part-'+serial+++extension;await fetch(resolved,join(root,name));cache.set(resolved,name);return name;}
  for(const line of lines){signal?.throwIfAborted();if(!line.startsWith('#')){if(++segments>5000)throw Error('视频分片超过上限，请分段处理');local.push(await localFile(line,'.bin'));continue;}
   if(line.startsWith('#EXT-X-KEY:')){if(/METHOD=NONE/.test(line)){local.push('#EXT-X-KEY:METHOD=NONE');continue;}if(!/METHOD=AES-128(?:,|$)/.test(line)||/KEYFORMAT=/.test(line))throw Error('受保护的加密流不支持下载');const uri=line.match(/URI="([^"]+)"/)?.[1];if(!uri)throw Error('播放清单缺少密钥地址');const name=await localFile(uri,'.key');if((await readFile(join(root,name))).length!==16)throw Error('流密钥格式无效');local.push('#EXT-X-KEY:METHOD=AES-128,URI="'+name+'"'+(line.match(/,IV=0x[a-f0-9]+/i)?.[0]||''));continue;}
   if(line.startsWith('#EXT-X-MAP:')){const uri=line.match(/URI="([^"]+)"/)?.[1];if(!uri)throw Error('播放清单初始化片段无效');local.push(line.replace(/URI="[^"]+"/,'URI="'+await localFile(uri,'.init')+'"'));continue;}
   if(/URI=/.test(line))throw Error('播放清单包含未支持的外部资源');
   if(/^#(?:EXTM3U|EXTINF:|EXT-X-(?:VERSION:|TARGETDURATION:|MEDIA-SEQUENCE:|DISCONTINUITY-SEQUENCE:|PLAYLIST-TYPE:|BYTERANGE:|DISCONTINUITY$|ENDLIST$|INDEPENDENT-SEGMENTS$|PROGRAM-DATE-TIME:))/.test(line))local.push(line);
  }
  if(!segments)throw Error('播放清单没有视频分片');const list=join(root,'local.m3u8');await writeFile(list,local.join('\n')+'\n');return list;
 }
 await manifest(url);const output=join(root,'merged.mp4');onProgress({message:'分片已下载，正在本地无损合并'});await runMediaTool(ffmpeg,['-v','error','-nostdin','-y','-protocol_whitelist','file,crypto','-allowed_extensions','ALL','-i','local.m3u8','-map','0:v:0','-map','0:a?','-c','copy','-movflags','+faststart',output],{signal,timeout:600000,cwd:root});await rename(output,path);return {path,bytes};
}
