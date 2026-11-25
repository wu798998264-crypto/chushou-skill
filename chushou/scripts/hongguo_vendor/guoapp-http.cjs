// GPL-3.0-only. Bounded, cookie-free worker for guoapp's synchronous host API.
const fs=require('node:fs');
const hosts=['fqnovel.com','novelquickapp.com','fanqienovel.com','hongguoduanju.com'];
function allowed(value){const u=new URL(value);if(u.protocol!=='https:'||u.username||u.password||u.port||!(u.hostname==='log.snssdk.com'&&u.pathname==='/service/2/device_register/'||u.hostname==='reading.snssdk.com'&&u.pathname==='/novel/player/multi_video_model/preload/v1'||u.hostname==='vas-lf-x.snssdk.com'&&u.pathname.startsWith('/video/fplay/')||hosts.some(h=>u.hostname===h||u.hostname.endsWith('.'+h))))throw Error('不支持的红果接口或分享地址');return u;}
async function request(p){
 let url=allowed(p.url),method=p.method||'GET';if(!['GET','POST'].includes(method))throw Error('不支持的请求方式');
 const signal=AbortSignal.timeout(Math.min(Math.max(p.timeoutMs||20000,1),40000));
 const headers={...p.headers};for(const k of Object.keys(headers))if(/^(cookie|authorization|proxy-authorization|host|accept-encoding)$/i.test(k))delete headers[k];
 const body=p.bodyBase64===undefined?p.body:Buffer.from(p.bodyBase64,'base64');
 for(let i=0;i<8;i++){
  const response=await fetch(url,{method,headers,body:method==='GET'?undefined:body,signal,redirect:'manual',credentials:'omit'});
  if([401,403,429].includes(response.status)){await response.body?.cancel();throw Object.assign(Error((response.status===429?'红果请求受到限流，请稍后重试':'该集需要有效的网站登录授权，当前授权不足或已失效')+'：HTTP '+response.status),{backoff:true});}
  if([301,302,303,307,308].includes(response.status)){const location=response.headers.get('location');await response.body?.cancel();if(!location)throw Error('分享跳转缺少地址');url=allowed(new URL(location,url));if([301,302,303].includes(response.status))method='GET';continue;}
  const chunks=[];let size=0;for await(const chunk of response.body){size+=chunk.length;if(size>Math.min(p.limit||20971520,20971520))throw Object.assign(Error('红果响应过大'),{oversized:true});chunks.push(chunk);}
  return {status:response.status,body:Buffer.concat(chunks).toString('utf8'),url:String(url)};
 }throw Error('红果分享跳转次数过多');
}
module.exports={allowed,request};
if(require.main===module)(async()=>{const raw=fs.readFileSync(0,'utf8');if(Buffer.byteLength(raw)>131072)throw Error('请求过大');process.stdout.write(JSON.stringify({ok:true,result:await request(JSON.parse(raw))}));})().catch(e=>{process.stdout.write(JSON.stringify({ok:false,error:String(e.message).replace(/https?:\/\/\S+/g,'[URL]').slice(0,1200),backoff:!!e.backoff,oversized:!!e.oversized}));process.exitCode=2;});
