// GPL-3.0-only. JSON stdin/stdout boundary; called only by hongguo_provider.py.
const fs=require('node:fs'),api=require('./hongguo.js');
console.warn=(...v)=>process.stderr.write(v.join(' ')+'\n');
(async()=>{
 const raw=fs.readFileSync(0,'utf8');if(Buffer.byteLength(raw)>32768)throw Error('请求过大');const p=JSON.parse(raw);let result;
 if(p.action==='inspect'){const id=await api.resolveSeriesId(p.source);result=await api.fetchEpisodeList(id);}
 else if(p.action==='search')result=await api.searchSeries(p.keyword,{offset:Number(p.cursor||0)});
 else if(p.action==='resolve'){
  if(!/^\d{6,25}$/.test(p.videoId))throw Error('无效视频编号');const v=await api.fetchPlayUrlSingle(p.videoId,p.contentType,'hevc');
  if(!v.url||/bytevc2|bvc2/i.test(v.codec||''))throw Error('未取得标准播放器可解码的播放流');
  const key=v.spadeA?api.deriveKey(v.spadeA):null;if(v.spadeA&&(!key||key.length!==16))throw Error('播放密钥未通过校验');
  result={url:v.url,key:key?.toString('hex')||null,codec:v.codec,content_type:v.content_type};
 }else throw Error('不支持的下载动作');
 process.stdout.write(JSON.stringify(result));
})().catch(error=>{process.stderr.write(String(error.message||error).replace(/https?:\/\/\S+/g,'[URL]').slice(-1800)+'\n');process.exitCode=2;});
