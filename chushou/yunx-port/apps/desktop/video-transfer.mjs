import {request as httpsRequest} from 'node:https';
import {request as httpRequest} from 'node:http';
import {lookup} from 'node:dns/promises';
import {isIP} from 'node:net';
import {open,stat,rename,rm,mkdir,readFile} from 'node:fs/promises';
import {dirname,join} from 'node:path';
import {spawn} from 'node:child_process';
import {createHash} from 'node:crypto';
import {createReadStream} from 'node:fs';
import {request as proxyRequest,ProxyAgent} from 'undici';

const fail=(message,code='VIDEO_TRANSFER_FAILED')=>Object.assign(Error(message),{code});
export function publicAddress(address){
 const value=address.toLowerCase().replace(/^\[|\]$/g,'');
 if(value.includes(':')){if(value.startsWith('::ffff:'))return publicAddress(value.slice(7));return /^2[0-9a-f]{3}:/.test(value)&&!value.startsWith('2001:db8:');}
 const p=value.split('.').map(Number);return p.length===4&&p.every(n=>Number.isInteger(n)&&n>=0&&n<256)&&![0,10,127].includes(p[0])&&!(p[0]===169&&p[1]===254)&&!(p[0]===172&&p[1]>=16&&p[1]<=31)&&!(p[0]===192&&[0,168].includes(p[1]))&&!(p[0]===100&&p[1]>=64&&p[1]<=127)&&p[0]<224&&!(p[0]===198&&[18,19].includes(p[1]));
}
export function remoteUrl(value,{allowTestLoopback=false}={}){
 let u;try{u=new URL(value);}catch{throw fail('解析结果中的下载地址无效');}
 if(!['http:','https:'].includes(u.protocol)||u.username||u.password||u.href.length>8192)throw fail('不支持此下载地址');
 const host=u.hostname.replace(/^\[|\]$/g,'');if(!allowTestLoopback&&(host==='localhost'||host.endsWith('.localhost')||host.endsWith('.local')||isIP(host)&&!publicAddress(host)))throw fail('下载地址不可指向本机或内网');return u;
}
export async function transferFile(url,path,{signal,onProgress=()=>{},referer='',headers={},allowPlaylist=false,allowText=false,useSystemProxy=false,allowTestLoopback=false,maxBytes=1024*1024*1024}={},redirects=0){
 signal?.throwIfAborted();if(redirects>5)throw fail('下载重定向次数过多');const target=remoteUrl(url,{allowTestLoopback});
 const resolveAddress=(host,options,callback)=>{lookup(host,{all:true}).then(records=>{if(!records.length||!allowTestLoopback&&records.some(r=>!publicAddress(r.address)))throw fail('下载地址解析到本机或内网');options?.all?callback(null,records):callback(null,records[0].address,records[0].family);}).catch(callback);};
 const safeHeaders=Object.fromEntries(Object.entries(headers).filter(([k,v])=>/^(user-agent|referer|origin|cookie|authorization|accept)$/i.test(k)&&typeof v==='string'&&!/[\r\n]/.test(v)));
 const requestHeaders={'User-Agent':'Mozilla/5.0','Accept':'*/*',...(referer?{Referer:referer}:{}),...safeHeaders};
 let proxy='';if(useSystemProxy&&!allowTestLoopback){const configured=process.env.HTTPS_PROXY||process.env.HTTP_PROXY;if(configured){const addresses=await lookup(target.hostname,{all:true});if(addresses.length&&addresses.every(a=>/^198\.(?:18|19)\./.test(a.address)))proxy=configured;}}
 let response;if(proxy){const dispatcher=new ProxyAgent(proxy);try{const result=await proxyRequest(target,{method:'GET',headers:requestHeaders,signal,dispatcher,maxRedirections:0,headersTimeout:45000,bodyTimeout:45000});response=result.body;response.statusCode=result.statusCode;response.headers=result.headers;response.once('close',()=>void dispatcher.close());}catch(error){await dispatcher.close();throw error;}}
 else response=await new Promise((resolve,reject)=>{const request=(target.protocol==='https:'?httpsRequest:httpRequest)(target,{method:'GET',signal,lookup:resolveAddress,headers:requestHeaders},resolve);request.once('error',reject);request.setTimeout(45000,()=>request.destroy(fail('下载连接超时')));request.end();});
 if([301,302,303,307,308].includes(response.statusCode)){const location=response.headers.location;response.resume();if(!location)throw fail('下载重定向缺少地址');const next=new URL(location,target);const forwarded=next.origin===target.origin?safeHeaders:Object.fromEntries(Object.entries(safeHeaders).filter(([k])=>!/^(cookie|authorization)$/i.test(k)));return transferFile(next.href,path,{signal,onProgress,referer,headers:forwarded,allowPlaylist,allowText,useSystemProxy,allowTestLoopback,maxBytes},redirects+1);}
 if(response.statusCode!==200){response.resume();throw fail('下载站返回 HTTP '+response.statusCode);}
 const contentType=String(response.headers['content-type']||'');if(!allowPlaylist&&!allowText&&/text\/|(?:json|html|xml|mpegurl)/i.test(contentType)){response.resume();throw fail('下载站返回网页或播放清单，没有返回完整媒体文件');}
 const total=Number(response.headers['content-length'])||0;if(total>maxBytes){response.destroy();throw fail('媒体超过 1 GB 下载限制');}
 await mkdir(dirname(path),{recursive:true});const temporary=path+'.partial',file=await open(temporary,'wx');let bytes=0;const hash=createHash('sha256');
 try{for await(const chunk of response){signal?.throwIfAborted();bytes+=chunk.length;if(bytes>maxBytes)throw fail('媒体超过下载大小限制');hash.update(chunk);await file.writeFile(chunk);onProgress({bytes,total});}signal?.throwIfAborted();if(!bytes||total&&total!==bytes)throw fail('下载结果为空或不完整');await file.sync();await file.close();await rename(temporary,path);return {path,size:bytes,sha256:hash.digest('hex'),contentType};}catch(error){response.destroy();await file.close().catch(()=>{});await rm(temporary,{force:true});throw error;}
}
export function runMediaTool(tool,args,{signal,timeout=180000,cwd}={}){
 signal?.throwIfAborted();return new Promise((resolve,reject)=>{const child=spawn(tool,args,{windowsHide:true,cwd,stdio:['ignore','pipe','pipe']}),output=[],errors=[];let length=0,settled=false,timedOut=false;const stop=()=>child.kill(),timer=setTimeout(()=>{timedOut=true;stop();},timeout);signal?.addEventListener('abort',stop,{once:true});
 const finish=(error,value)=>{if(settled)return;settled=true;clearTimeout(timer);signal?.removeEventListener('abort',stop);error?reject(error):resolve(value);};
 child.stdout.on('data',b=>{length+=b.length;if(length<2*1024*1024)output.push(b);});child.stderr.on('data',b=>{errors.push(b);if(errors.length>40)errors.shift();});child.once('error',error=>finish(fail(error.code==='ENOENT'?'缺少 FFmpeg/FFprobe 环境，请安装软件附带的媒体运行环境':error.message,'MEDIA_RUNTIME_UNAVAILABLE')));child.once('close',code=>finish(signal?.aborted?fail('解析下载已停止','ABORTED'):timedOut?fail('媒体校验或格式转换超时；已下载的源文件仍保留，可重试转换','MEDIA_TOOL_TIMEOUT'):code!==0?fail('媒体解码或格式转换失败：'+Buffer.concat(errors).toString().slice(-350)):null,Buffer.concat(output).toString()));
 });
}
export async function probeMedia(path,{signal,ffprobe=process.env.SHENSI_FFPROBE_PATH||'ffprobe'}={}){
 const p=JSON.parse(await runMediaTool(ffprobe,['-v','error','-show_streams','-show_format','-of','json',path],{signal,timeout:45000}));return p;
}
export const DOWNLOAD_FORMATS=Object.freeze({video:['source','mp4','webm'],image:['png','jpg','webp'],audio:['mp3','wav','m4a','ogg']});
export async function convertMedia(source,{kind,format,directory,signal,ffmpeg=process.env.SHENSI_FFMPEG_PATH||'ffmpeg',ffprobe=process.env.SHENSI_FFPROBE_PATH||'ffprobe',onProgress=()=>{}}){
 if(!DOWNLOAD_FORMATS[kind]?.includes(format))throw fail('不支持所选下载格式');signal?.throwIfAborted();onProgress({message:'下载完成，正在校验源文件',phase:'verifying',bytes:0,total:0});const probe=await probeMedia(source,{signal,ffprobe}),video=probe.streams?.find(s=>s.codec_type==='video'&&!s.disposition?.attached_pic),audio=probe.streams?.find(s=>s.codec_type==='audio');
 if(kind==='video'&&!video||kind==='audio'&&!audio||kind==='image'&&!video)throw fail('解析结果不包含所选媒体内容');
 const ext=format==='source'?(/webm/.test(probe.format?.format_name)?'webm':/matroska/.test(probe.format?.format_name)?'mkv':/mov|mp4/.test(probe.format?.format_name)?'mp4':''):format;
 if(!ext)throw fail('源视频容器不支持直接导入，请选择 MP4 或 WebM');
 const path=format==='source'?source:join(directory,'result.'+ext);
 if(format!=='source'){
  const remux=kind==='video'&&format==='mp4'&&video.codec_name==='h264'&&(!audio||audio.codec_name==='aac');
  onProgress({message:remux?'下载完成，正在无损封装视频（不重新编码）':'下载完成，正在转换所选格式',phase:'converting',bytes:0,total:0});
  const args=['-v','error','-nostdin','-y','-protocol_whitelist','file,pipe','-i',source];
  if(kind==='image')args.push('-map','0:v:0','-frames:v','1','-update','1',...(format==='jpg'?['-q:v','2']:[]));
  if(kind==='audio')args.push('-map','0:a:0','-vn',...(format==='mp3'?['-c:a','libmp3lame','-q:a','2']:format==='wav'?['-c:a','pcm_s16le']:format==='m4a'?['-c:a','aac','-b:a','192k']:['-c:a','libvorbis','-q:a','5']));
  if(kind==='video')args.push('-map','0:v:0','-map','0:a:0?',...(remux?['-c','copy','-movflags','+faststart']:format==='mp4'?['-c:v','libx264','-preset','veryfast','-crf','18','-c:a','aac','-movflags','+faststart']:['-c:v','libvpx-vp9','-deadline','realtime','-cpu-used','5','-crf','30','-b:v','0','-c:a','libopus']));
  args.push(path);await runMediaTool(ffmpeg,args,{signal});
 }
 onProgress({message:'正在校验成品与文件完整性',phase:'verifying',bytes:0,total:0});const verified=await probeMedia(path,{signal,ffprobe}),stream=verified.streams?.find(s=>s.codec_type===(kind==='audio'?'audio':'video'));if(!stream)throw fail('转换结果无法解码');
 const file=await open(path,'r+');try{await file.sync();}finally{await file.close();}const info=await stat(path);if(!info.size)throw fail('下载结果是空文件');const hash=createHash('sha256');for await(const b of createReadStream(path)){signal?.throwIfAborted();hash.update(b);}
 const mime={mp4:'video/mp4',webm:'video/webm',mkv:'video/x-matroska',png:'image/png',jpg:'image/jpeg',webp:'image/webp',mp3:'audio/mpeg',wav:'audio/wav',m4a:'audio/mp4',ogg:'audio/ogg'}[ext];
 return {path,size:info.size,sha256:hash.digest('hex'),mimeType:mime,kind,extension:ext,width:stream.width||0,height:stream.height||0,durationMs:Math.round(Number(verified.format?.duration||stream.duration||0)*1000),frameCount:Number(stream.nb_frames)||0};
}
