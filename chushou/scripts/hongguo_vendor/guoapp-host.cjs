// GPL-3.0-only. Node host for the supplied guoapp source, without Flutter/Go.
const fs=require('node:fs'),path=require('node:path'),crypto=require('node:crypto'),vm=require('node:vm'),{spawnSync}=require('node:child_process');
const {allowed}=require('./guoapp-http.cjs');
const sleep=ms=>Atomics.wait(new Int32Array(new SharedArrayBuffer(4)),0,0,Math.min(ms,1000));
function preserveIntegers(text){return text.replace(/"(?:[^"\\]|\\.)*"|-?\d+(?:\.\d+)?(?:[eE][+-]?\d+)?/g,v=>v[0]!=='"'&&/^-?\d+$/.test(v)&&!Number.isSafeInteger(Number(v))?JSON.stringify(v):v);}
function base64Decode(value){const v=value.trim();if(!/^[A-Za-z0-9+/]*={0,2}$/.test(v)||v.length%4===1)throw Error('Base64 编码无效');const bytes=Buffer.from(v,'base64');if(bytes.toString('base64').replace(/=+$/,'')!==v.replace(/=+$/,''))throw Error('Base64 编码无效');return bytes;}
function fetchSync(p){
 allowed(p.url);if(p.body instanceof Uint8Array)p={...p,body:undefined,bodyBase64:Buffer.from(p.body).toString('base64')};
 const r=spawnSync(process.execPath,[path.join(__dirname,'guoapp-http.cjs')],{input:JSON.stringify(p),encoding:'utf8',maxBuffer:25*1024*1024,timeout:45000,windowsHide:true});
 let response;try{response=JSON.parse(r.stdout);}catch{}
 if(!response?.ok)throw Object.assign(Error(response?.error||r.error?.message||'红果网络请求失败'),{backoff:!!response?.backoff,oversized:!!response?.oversized});return response.result;
}
function createProvider({cacheFile,fetchImpl=fetchSync}={}){
 const cache=new Map();let lockDepth=0;
 if(cacheFile&&!path.isAbsolute(cacheFile))throw Error('下载缓存目录无效');
 function load(){if(!cacheFile)return {};try{const s=fs.statSync(cacheFile);if(s.size>65536)throw Error('红果设备缓存过大');return JSON.parse(fs.readFileSync(cacheFile,'utf8'));}catch(e){if(e.code==='ENOENT')return {};throw e;}}
 function exclusive(fn){
  if(!cacheFile||lockDepth)return fn();fs.mkdirSync(path.dirname(cacheFile),{recursive:true});const lock=cacheFile+'.lock',deadline=Date.now()+45000;let fd;
  while(fd===undefined){try{fd=fs.openSync(lock,'wx',0o600);fs.writeFileSync(fd,String(process.pid));}catch(e){if(e.code!=='EEXIST')throw e;let pid;try{pid=Number(fs.readFileSync(lock,'utf8'));}catch(err){if(err.code==='ENOENT')continue;throw err;}if(Number.isInteger(pid)&&pid>0){try{process.kill(pid,0);}catch(err){if(err.code==='ESRCH'){try{fs.unlinkSync(lock);}catch(x){if(x.code!=='ENOENT')throw x;}continue;}}}if(Date.now()>=deadline)throw Error('红果设备缓存正在使用，请稍后重试');sleep(100);}}
  lockDepth++;try{return fn();}finally{lockDepth--;fs.closeSync(fd);fs.unlinkSync(lock);}
 }
 const persisted=k=>k==='state'||k==='registerPause'||k==='appRoutes';
 const host={source:'hongguo',retries:()=>1,maxPages:()=>1,reserve:p=>{allowed(p.url);return 0;},sleep,exclusive,
  dramaId:id=>'hongguo:'+id,chapterId:(series,id)=>'hongguo:'+series+':'+id,
  cacheGet:key=>{const row=persisted(key)&&cacheFile?load()[key]:cache.get(key);return row&&(!row.expires||row.expires>Date.now())?row.value:null;},
  cacheSet:(key,value,ttl)=>{const row={value,expires:ttl?Date.now()+ttl:0};if(persisted(key)&&cacheFile)exclusive(()=>{const data=load();data[key]=row;const temporary=cacheFile+'.'+process.pid+'.tmp';fs.writeFileSync(temporary,JSON.stringify(data),{mode:0o600});fs.renameSync(temporary,cacheFile);});else cache.set(key,row);},
  preserveIntegers,gzip:v=>require('node:zlib').gzipSync(Buffer.from(v)),randomBytes:crypto.randomBytes,md5:v=>crypto.createHash('md5').update(Buffer.from(v)).digest(),sha256:v=>crypto.createHash('sha256').update(Buffer.from(v)).digest(),sha512:v=>crypto.createHash('sha512').update(Buffer.from(v)).digest(),
  utf8Encode:v=>Buffer.from(v),utf8Decode:v=>Buffer.from(v).toString('utf8'),hexEncode:v=>Buffer.from(v).toString('hex'),hexDecode:v=>{if(!/^(?:[0-9a-f]{2})*$/i.test(v))throw Error('十六进制无效');return Buffer.from(v,'hex');},base64Encode:v=>Buffer.from(v).toString('base64'),base64Decode,
  aesCbcEncrypt:(key,iv,bytes)=>{const cipher=crypto.createCipheriv('aes-'+key.length*8+'-cbc',Buffer.from(key),Buffer.from(iv));cipher.setAutoPadding(false);return Buffer.concat([cipher.update(Buffer.from(bytes)),cipher.final()]);},
  aesCbcDecrypt:(key,iv,bytes)=>{const cipher=crypto.createDecipheriv('aes-'+key.length*8+'-cbc',Buffer.from(key),Buffer.from(iv));cipher.setAutoPadding(false);return Buffer.concat([cipher.update(Buffer.from(bytes)),cipher.final()]);},
  parseUrl:v=>{const u=new URL(v);return {scheme:u.protocol.slice(0,-1),hostname:u.hostname,userInfo:u.username||u.password,path:u.pathname,query:u.search.slice(1)};},
  fetch:fetchImpl,fetchText:p=>fetchImpl({...p,headers:{'User-Agent':'Mozilla/5.0',Referer:p.referer}}),finishMedia:v=>v,
  catalogContext:()=>({}),isKnown:()=>false,reportProgress:()=>{}
 };
 const module={exports:{}};vm.runInNewContext(fs.readFileSync(path.join(__dirname,'guoapp.js'),'utf8'),{module,host},{filename:'guoapp-hongguo.js',timeout:5000});
 function seriesId(source){
  const text=String(source).trim();if(/^\d{6,25}$/.test(text))return text;const match=text.match(/https:\/\/[^\s，,、"'）)】\]<>]+/i);if(!match)throw Error('请输入红果分享链接或已核对剧集编号');
  const sourceUrl=allowed(match[0]),fromUrl=u=>{for(const key of ['video_series_id','series_id']){const v=u.searchParams.get(key);if(/^\d{6,25}$/.test(v||''))return v;}const m=u.pathname.match(/^\/(?:player|detail)\/(\d{6,25})(?:\/|$)/);return m?.[1];};
  let id=fromUrl(sourceUrl);if(id)return id;const response=host.fetchText({url:String(sourceUrl),referer:'https://hongguoduanju.com/'});id=fromUrl(allowed(response.url));if(id)return id;
  for(let value of [response.url,response.body]){for(let i=0;i<2;i++)try{value=decodeURIComponent(value);}catch{break;}const ids=[...value.matchAll(/(?:video_series_id|series_id)(?:["'\s:=]|%22|&quot;)+(\d{6,25})/g)].map(m=>m[1]);if(new Set(ids).size===1)return ids[0];}
  throw Error('分享链接未返回唯一的整剧编号；未把单集或任意数字当作整剧');
 }
 return {api:module.exports,seriesId};
}
module.exports={createProvider,preserveIntegers,base64Decode};
