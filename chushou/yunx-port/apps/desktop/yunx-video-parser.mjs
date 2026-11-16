import {mkdir,stat,rename,copyFile} from 'node:fs/promises';
import {join,basename,extname} from 'node:path';
import {createReadStream} from 'node:fs';
import {createHash} from 'node:crypto';
import {createYunxEngine} from './yunx-engine.mjs';
import {rememberDecodedParserMedia} from '../../packages/core/src/parser-media-receipts.mjs';
import {createParserBrowser} from './parser-browser.mjs';
import {parseXhs} from './parser-xhs.mjs';
import portal from '../../packages/official/legacy/video-portal-automation.cjs';
import {downloadYunxHls} from './yunx-hls.mjs';
import {inspectShareLink,validateShareCode} from '../../packages/ui/share-link.js';
import {remoteUrl,transferFile,convertMedia,runMediaTool,DOWNLOAD_FORMATS} from './video-transfer.mjs';

const fail=(message,code='YUNX_PARSE_FAILED')=>Object.assign(Error(message),{code});
const musicVerification={title:'神思 · 音乐站验证'};
// Leave room for the extension and host timestamp prefix. Long share captions
// must never make the attachment store truncate .mp4 and break local preview.
const safeName=name=>{const clean=basename(String(name||'解析结果').replaceAll('\\','/')).replace(/[<>:"/\\|?*\u0000-\u001f]/g,'_'),suffix=extname(clean),ext=/^\.[a-z0-9]{1,10}$/i.test(suffix)?suffix:'';return (ext?clean.slice(0,-ext.length):clean).slice(0,80-ext.length)+ext||'解析结果';};
export function createYunxVideoParser({engineFactory,BrowserWindow,allowTestLoopback=false,ffmpeg=process.env.SHENSI_FFMPEG_PATH||'ffmpeg',ffprobe=process.env.SHENSI_FFPROBE_PATH||'ffprobe'}={}){
 const browser=createParserBrowser({BrowserWindow,allowTestLoopback});engineFactory??=()=>createYunxEngine({browser});
 const sessions=new Map();let accounts;
 function stop(id){const session=sessions.get(id);if(!session)return;sessions.delete(id);session.controller.abort();session.engine?.close();browser.reset(id);}
 const checked=id=>{const entry=sessions.get(id);if(!entry)throw fail('解析会话已关闭，请重新解析','YUNX_SESSION_CLOSED');return entry;};
 const publicResult=result=>({...result,canOpen:false,options:result.options.map(o=>({...o,formats:DOWNLOAD_FORMATS[o.kind]||(o.kind==='file'?['source']:[])}))});
 async function parse({jobId,sourceText,accessCode,signal,resume=false,mode='video',onProgress=()=>{}}){
  if(!/^video-[a-f0-9-]{36}$/.test(jobId))throw fail('解析任务身份无效');
  if(resume){signal?.throwIfAborted();const prior=checked(jobId);if(prior.result)return prior.result;throw fail('原解析尚未取得结果，请重新解析','YUNX_SESSION_INCOMPLETE');}
  if(mode==='music'){
   const keyword=String(sourceText||'').trim();if(!keyword||keyword.length>200)throw fail('请输入不超过200字的歌曲名或歌手');
   stop(jobId);const entry={controller:new AbortController(),sourceUrl:'https://www.gequbao.com/s/'+encodeURIComponent(keyword),music:new Map(),result:null};sessions.set(jobId,entry);signal=AbortSignal.any([entry.controller.signal,...signal?[signal]:[]]);
   try{onProgress({message:'正在搜索音乐'});const found=JSON.parse(await browser.extract({key:jobId,url:entry.sourceUrl,ready:'!!document.querySelector(\'a[href*="/music/"]\')',extract:portal.buildPortalScript({mode:'music-search',timeoutMs:15000}),manualVerification:musicVerification,onProgress},{signal}));if(!found.ok)throw fail(found.reason==='human-verification'?'音乐站需要人工验证':'音乐站暂未返回搜索结果');
    const options=[];for(const item of found.results||[]){if(!/^https:\/\/(?:www\.)?gequbao\.com\/music\/\d+(?:[/?#]|$)/i.test(item.url))continue;const id='song-'+options.length;entry.music.set(id,item.url);options.push({id,kind:'audio',label:String(item.label||item.title).slice(0,200)});}
    if(!options.length)throw fail('没有找到可下载的音乐');entry.result=publicResult({title:keyword,provider:'歌曲宝',options});return entry.result;
   }catch(error){stop(jobId);throw error;}
  }
  const share=inspectShareLink(sourceText);let url=share?.url||String(sourceText).match(/https?:\/\/[^\s<>"']+/i)?.[0]?.replace(/[。，,；;）)\]】}"'、！？…]+$/u,'');const target=remoteUrl(url,{allowTestLoopback});
  const fid=/^\/(?:tv\/show)\/(\d{4}:\d+)\/?$/.exec(target.pathname)?.[1];if(/^(www\.)?weibo\.com$/.test(target.hostname)&&fid&&!target.searchParams.has('mid'))url='https://video.weibo.com/show?fid='+fid;
  if(/(^|\.)(xiaohongshu\.com|xhslink\.(com|cn))$/i.test(new URL(url).hostname)&&BrowserWindow){
   stop(jobId);const entry={controller:new AbortController(),sourceUrl:url,result:null,media:new Map()};sessions.set(jobId,entry);signal=AbortSignal.any([entry.controller.signal,...signal?[signal]:[]]);
   try{onProgress({message:'正在读取小红书公开分享页'});const note=await parseXhs(browser,url,jobId,signal);for(const item of note.media){remoteUrl(item.url,{allowTestLoopback});entry.media.set(item.id,{url:item.url,headers:{Referer:'https://www.xiaohongshu.com/'}});}entry.result=publicResult({title:note.title,provider:'小红书',options:note.media.map(({url,...item})=>item)});return entry.result;}catch(error){stop(jobId);throw error;}
  }
  if(share?.needsAccessCode&&accessCode===undefined)throw fail('此链接为'+share.label+'分享，请输入提取码；无需提取码可留空继续','SHARE_ACCESS_CODE_REQUIRED');
  const extension=new URL(url).pathname.split('.').pop().toLowerCase(),directKind=!share&&(/^(mp4|webm|mkv|mov|m4v|m3u8)$/.test(extension)?'video':/^(png|jpg|jpeg|webp|gif|bmp)$/.test(extension)?'image':/^(mp3|wav|m4a|ogg|flac|aac)$/.test(extension)?'audio':'');
  stop(jobId);const entry={engine:directKind?null:engineFactory(),controller:new AbortController(),sourceUrl:url,result:null,parents:[],direct:directKind?{url,isHls:extension==='m3u8'}:null};sessions.set(jobId,entry);
  signal=AbortSignal.any([entry.controller.signal,...signal?[signal]:[]]);onProgress({message:'正在解析'+(share?.label||'视频链接')});
  try{entry.result=publicResult(directKind?{title:safeName(decodeURIComponent(new URL(url).pathname.split('/').pop())),provider:'解析 · 原始媒体',options:[{id:'direct',kind:directKind,label:'原始媒体'}]}:await entry.engine.request({action:'parse',url,...(share?{platform:share.platform,shareId:share.shareId,accessCode:share.accessCode||validateShareCode(accessCode)}:{})},{signal}));return entry.result;}
  catch(error){stop(jobId);throw error;}
 }
 async function browse({jobId,optionId,signal}){const entry=checked(jobId);signal=AbortSignal.any([entry.controller.signal,...signal?[signal]:[]]);signal.throwIfAborted();if(optionId==='..'){if(!entry.parents.length)throw fail('已是分享根目录');entry.result=entry.parents.pop();return entry.result;}const option=entry.result.options.find(o=>o.id===optionId);if(option?.kind!=='folder')throw fail('请选择网盘目录');const result=publicResult(await entry.engine.request({action:'select',id:optionId},{signal}));entry.parents.push(entry.result);entry.result={...result,canGoBack:true};return entry.result;}
 async function download({jobId,optionId,format,directory,signal,onProgress=()=>{},shareProfileId,allowTemporaryTransfer=false}){
  const entry=checked(jobId),option=entry.result.options.find(o=>o.id===optionId);if(!option||!option.formats.includes(format))throw fail('下载项无效');signal=AbortSignal.any([entry.controller.signal,...signal?[signal]:[]]);
  let media;if(entry.media)media=entry.media.get(optionId);
  else if(entry.music){onProgress({message:'正在读取所选歌曲的下载地址'});const url=entry.music.get(optionId),result=JSON.parse(await browser.extract({key:jobId,url,ready:'!!document.querySelector("audio")',extract:portal.buildPortalScript({mode:'music-preview',timeoutMs:20000}),manualVerification:musicVerification,onProgress},{signal}));if(!result.ok||!result.previewUrl)throw fail(result.reason==='human-verification'?'音乐站需要人工验证':'该歌曲未返回试听或下载地址');media={url:result.previewUrl,headers:{Referer:url}};}
  else if(entry.result.requiresAccount){if(!accounts||!shareProfileId||allowTemporaryTransfer!==true)throw fail('请选择已登录的百度浏览器配置，并确认本次临时转存','SHARE_ACCOUNT_AUTHORIZATION_REQUIRED');media=await accounts.withSession(shareProfileId,cookie=>entry.engine.request({action:'select',id:optionId,cookie,allowTemporaryTransfer:true},{signal}));}
  else media=entry.direct||await entry.engine.request({action:'select',id:optionId},{signal});remoteUrl(media.url,{allowTestLoopback});await mkdir(directory,{recursive:true});const raw=join(directory,'source.bin');if(media.transferNotice)onProgress({message:media.transferNotice});
  if(media.isHls)await downloadYunxHls(media.url,raw,{signal,onProgress,headers:media.headers||{},directory,ffmpeg,allowTestLoopback});
  else await transferFile(media.url,raw,{signal,onProgress,headers:media.headers||{},useSystemProxy:true,allowTestLoopback,allowText:option.kind==='file'});
  if(media.audioUrl){const audio=join(directory,'audio.bin'),merged=join(directory,'merged.mp4');await transferFile(media.audioUrl,audio,{signal,onProgress,headers:media.audioHeaders||{},useSystemProxy:true,allowTestLoopback});await runMediaTool(ffmpeg,['-v','error','-nostdin','-y','-protocol_whitelist','file,pipe','-i',raw,'-i',audio,'-map','0:v:0','-map','1:a:0','-c','copy',merged],{signal});await rename(merged,raw);}
  if(option.kind==='file'){const hash=createHash('sha256');for await(const chunk of createReadStream(raw))hash.update(chunk);return {path:raw,name:safeName(option.label),kind:'file',mimeType:'application/octet-stream',size:(await stat(raw)).size,sha256:hash.digest('hex'),sourceUrl:entry.sourceUrl,provider:entry.result.provider};}
  const output=await verifiedMedia(raw,{kind:option.kind,format,directory,signal,onProgress});return {...output,name:safeName(entry.result.platform||entry.result.options.length>1?option.label:entry.result.title).replace(/\.[a-z0-9]{2,5}$/i,'')+'.'+output.extension,sourceUrl:entry.sourceUrl,provider:entry.result.provider};
 }
 async function verifiedMedia(path,options){
  const out=await convertMedia(path,{...options,ffmpeg,ffprobe});
  const decoded=await runMediaTool(ffmpeg,['-nostdin','-v','error','-xerror','-protocol_whitelist','file,pipe','-i',out.path,...(options.kind==='audio'?['-map','0:a:0']:['-map','0:v:0','-map','0:a?']),'-progress','pipe:1','-nostats','-f','null','-'],{signal:options.signal,timeout:600000});
  const frames=Number([...decoded.matchAll(/^frame=(\d+)/gm)].at(-1)?.[1]||0);
  if(out.kind!=='image'&&!(out.durationMs>0)||out.kind==='video'&&(!(out.width>0&&out.height>0&&frames>0)||out.frameCount>0&&frames!==out.frameCount))throw fail('解析文件未通过完整时长、画面或帧数校验','MEDIA_VALIDATION_FAILED');
  return rememberDecodedParserMedia(out);
 }
 async function extractAudio({jobId,output,directory,onProgress=()=>{},signal,preserveSource=false}){
  const entry=checked(jobId);if(output.kind!=='video')throw fail('仅视频可提取音频');signal=AbortSignal.any([entry.controller.signal,...signal?[signal]:[]]);signal.throwIfAborted();await mkdir(directory,{recursive:true});
  if(preserveSource){await copyFile(output.path,join(directory,'source.bin'));signal.throwIfAborted();}
  try{const out=await verifiedMedia(output.path,{kind:'audio',format:'mp3',directory,signal,onProgress});return {...out,name:safeName(output.name).replace(/\.[a-z0-9]{2,5}$/i,'')+'.mp3',sourceUrl:output.sourceUrl,provider:output.provider};}
  catch(error){if(error.message==='解析结果不包含所选媒体内容')throw fail('该视频没有音轨，无法仅下载音频','VIDEO_AUDIO_UNAVAILABLE');throw error;}
 }
 return {parse,browse,download,extractAudio,connectAccounts:provider=>{if(accounts)throw fail('网盘账号提供方已绑定');accounts=provider;},accounts:async()=>accounts?accounts.list():[],openAccount:async profileId=>{if(!accounts)throw fail('请先启用浏览器扩展');return accounts.open(profileId);},has:id=>sessions.has(id),stop,close:()=>{for(const id of sessions.keys())stop(id);browser.close();},open:()=>{throw fail('解析在后台直接解析，无需打开解析网站');},recoverDownload:async({directory,kind,format,title,signal,onProgress})=>{if(kind==='file')throw fail('文件下载恢复需要重新解析并核对分享权限');const out=await verifiedMedia(join(directory,'source.bin'),{kind,format,directory,signal,onProgress});return {...out,name:safeName(title).replace(/\.[a-z0-9]{2,5}$/i,'')+'.'+out.extension};}};
}
