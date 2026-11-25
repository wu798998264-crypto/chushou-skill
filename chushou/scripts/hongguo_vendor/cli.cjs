// GPL-3.0-only. JSON subprocess boundary; playback material stays on stdin/stdout.
const fs=require('node:fs'),path=require('node:path');
try{
 const raw=fs.readFileSync(0,'utf8');if(Buffer.byteLength(raw)>32768)throw Error('请求过大');const p=JSON.parse(raw);let result;
 if(p.action==='prepare'){
  if(!/^[a-f0-9]{32}$/.test(p.key)||!require('node:path').isAbsolute(p.source)||!require('node:path').isAbsolute(p.destination)||fs.existsSync(p.destination))throw Error('播放准备参数无效或目标已存在');
  require('./playback.cjs').decryptMp4File(p.source,p.destination,Buffer.from(p.key,'hex'));result={prepared:true};
 }else{
  const {api,seriesId}=require('./guoapp-host.cjs').createProvider({cacheFile:p.cacheFile});
  if(p.action==='search'){const page=Number(p.cursor||1);if(!Number.isInteger(page)||page<1||page>100)throw Error('无效搜索页');const found=api.search(page,p.keyword);result={items:found.items.filter(v=>/^\d{6,25}$/.test(v.sourceId)).map(v=>({series_id:v.sourceId,title:v.title,total_episodes:Number(v.episodeCount)||null,description:v.intro,cover:v.cover})),nextOffset:found.hasMore?page+1:0,hasMore:found.hasMore,warning:found.warning};}
  else if(p.action==='inspect'){const id=seriesId(p.source),detail=api.detail(id);result={series_id:id,series_title:detail.drama.title,episodes:detail.chapters.map(c=>({vid:c.videoUrl.slice('hongguo-cenc://'.length),vid_index:c.currentEpisode,title:c.title}))};}
  else if(p.action==='resolve'){
   if(!/^\d{6,25}$/.test(p.videoId)||!/^\d{6,25}$/.test(p.seriesId))throw Error('无效剧集编号');
   const v=api.resolveMedia({dramaId:'hongguo:'+p.seriesId,chapter:{videoUrl:'hongguo-cenc://'+p.videoId}}),key=v.cencKey?Buffer.from(v.cencKey,'base64'):null;if(key&&key.length!==16)throw Error('播放参数校验失败');
   result={url:v.url,referer:v.referer,key:key?.toString('hex')||null,quality:v.quality,duration:v.durationMs/1000,sourceRoute:v.sourceRoute||'web_page'};
  }else throw Error('不支持的下载动作');
 }
 process.stdout.write(JSON.stringify(result));
}catch(error){process.stderr.write(String(error.message||error).replace(/https?:\/\/\S+/g,'[URL]').slice(-1800)+'\n');process.exitCode=2;}
