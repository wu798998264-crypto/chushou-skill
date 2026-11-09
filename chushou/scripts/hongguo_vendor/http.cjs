// GPL-3.0-only. Small native HTTP adapter for the supplied downloader.
// No npm installation, browser cookies, device registration or login collection.
const allowed=value=>{const u=new URL(value);if(u.protocol!=='https:'||u.username||u.password||u.port||!['fqnovel.com','novelquickapp.com','fanqienovel.com','hongguoduanju.com'].some(h=>u.hostname===h||u.hostname.endsWith('.'+h)))throw Error('不支持的红果接口或跳转地址');return u;};
async function request(method,value,data,options={}){
 let url=allowed(value);for(const [k,v]of Object.entries(options.params||{}))url.searchParams.set(k,String(v));
 const signal=AbortSignal.timeout(options.timeout||25000),headers={...options.headers};
 delete headers.Host;delete headers.host;delete headers['Accept-Encoding'];
 for(let i=0;i<=10;i++){
  const response=await fetch(url,{method,body:method==='GET'?undefined:typeof data==='string'?data:JSON.stringify(data),headers,signal,redirect:'manual',credentials:'omit'});
  if([301,302,303,307,308].includes(response.status)){const location=response.headers.get('location');await response.body?.cancel();if(!location||i===10)throw Error('红果跳转次数过多或地址缺失');url=allowed(new URL(location,url));if([301,302,303].includes(response.status))method='GET';continue;}
  if(!response.ok){await response.body?.cancel();throw Error('红果接口 HTTP '+response.status);}
  const chunks=[];let size=0;for await(const c of response.body){size+=c.length;if(size>16*1024*1024){await response.body.cancel().catch(()=>{});throw Error('红果接口响应过大');}chunks.push(c);}
  let result=Buffer.concat(chunks).toString('utf8');if(options.responseType!=='text'){try{result=JSON.parse(result);}catch{}}
  return {data:result,status:response.status,request:{res:{responseUrl:url.href}},config:{url:url.href}};
 }
}
module.exports={get:(url,options)=>request('GET',url,null,options),post:(url,body,options)=>request('POST',url,body,options)};
