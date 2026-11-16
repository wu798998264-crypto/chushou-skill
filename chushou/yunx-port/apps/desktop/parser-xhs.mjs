// Read only the publicly rendered note selected by the official URL. No private
// API, login-cookie import, access-control modification or related-note fallback.
import {remoteUrl} from './video-transfer.mjs';
export async function parseXhs(browser,url,jobId,signal){
 const find=String.raw`const id=location.pathname.match(/\/(?:explore|discovery\/item)\/([a-z0-9]+)/i)?.[1],s=window.__INITIAL_STATE__||{},fallback=s.noteData?.data?.noteData,n=id&&(s.note?.noteDetailMap?.[id]?.note||((fallback?.noteId||fallback?.id)===id?fallback:null));`;
 const value=JSON.parse(await browser.extract({key:jobId,url,ready:`(()=>{${find}return !!n})()`,extract:`(()=>{${find}if(!n)throw Error('未读取到当前笔记');const media=[],seen=new Set(),add=(kind,url,label)=>{if(typeof url!=='string'||!/^https?:/.test(url)||seen.has(url))return;seen.add(url);media.push({id:'media-'+media.length,kind,url,label})};
  const isVideo=!!n.video||n.type==='video';if(isVideo){for(const codec of ['h264','h265','av1','h266'])for(const item of n.video?.media?.stream?.[codec]||[])add('video',item.masterUrl||item.master_url,codec.toUpperCase()+' '+(item.height?item.height+'P':''));}
  else for(const item of n.imageList||[])add('image',item.urlDefault||item.url||item.infoList?.find(x=>x.url)?.url,'图片 '+(media.length+1));
  return JSON.stringify({title:n.title||n.desc||'小红书笔记',type:n.type,video:isVideo,media});})()`},{signal}));
 if(!value.media?.length)throw Object.assign(Error(value.video?'小红书已返回笔记，但没有可下载的视频，未将封面冒充视频':'小红书笔记没有可下载的媒体'),{code:'PARSER_XHS_EMPTY'});
 for(const item of value.media)remoteUrl(item.url);
 return value;
}
