import {randomUUID} from 'node:crypto';
import {setTimeout as pause} from 'node:timers/promises';
import {lookup} from 'node:dns/promises';
import {remoteUrl,publicAddress} from './video-transfer.mjs';

const pageGate=`(()=>{const text=(document.body?.innerText||'').slice(0,12000);return /verify you are human|security verification|验证您是真人|请完成人机验证|安全验证|人机验证|人工验证|captcha|cloudflare/i.test(text)?'verification':/登录后查看|登录后继续|请先登录/.test(text)?'login':''})()`;
const failure=(message,code)=>Object.assign(Error(message),{code});
function asksForVerification(result){try{const value=typeof result==='string'?JSON.parse(result):result;return value?.ok===false&&value.reason==='human-verification';}catch{return false;}}

// Only bundled parser code can supply scripts. Website content stays in a sandbox
// with an ephemeral partition, without access to any logged-in browser profile.
export function createParserBrowser({BrowserWindow,allowTestLoopback=false}={}){
 const windows=new Set(),sessions=new Map();
 async function extract({url,ready,extract,scripts=[],timeout=40000,key=randomUUID(),manualVerification,onProgress=()=>{}},{signal}={}){
  if(!BrowserWindow)throw Object.assign(Error('此网站需要桌面解析浏览器，请在桌面软件中重试'),{code:'PARSER_BROWSER_REQUIRED'});
  remoteUrl(url,{allowTestLoopback});signal?.throwIfAborted();
  if(!/^[a-z0-9-]{1,80}$/i.test(key))throw Error('解析浏览器身份无效');
  const win=new BrowserWindow({show:false,width:1280,height:900,webPreferences:{partition:'parser-'+key,sandbox:true,contextIsolation:true,nodeIntegration:false,backgroundThrottling:false}});sessions.set(key,win.webContents.session);
  let terminalError,timer,waiting=false,verificationDeadline;
  const normalTimeout=Math.min(60000,Math.max(1000,Number(timeout)||40000)),verificationTimeout=Math.min(600000,Math.max(1000,Number(manualVerification?.timeoutMs)||600000));
  const title=String(manualVerification?.title||'神思 · 网站验证');
  windows.add(win);const close=()=>{windows.delete(win);if(!win.isDestroyed())win.destroy();};signal?.addEventListener('abort',close,{once:true});
  const check=()=>{signal?.throwIfAborted();if(terminalError)throw terminalError;if(win.isDestroyed())throw failure('验证页面已关闭，本次解析下载已取消','PARSER_BROWSER_CLOSED');};
  const notify=(value,message)=>{try{Promise.resolve(onProgress({message,manualVerification:{id:key,waiting:value}})).catch(()=>{});}catch{}};
  const arm=(ms,error)=>{clearTimeout(timer);timer=setTimeout(()=>{terminalError=error;close();},ms);};
  const beginVerification=()=>{
   if(!manualVerification)throw failure('网站要求人工验证，未自动绕过；请稍后重新解析','PARSER_VERIFICATION_REQUIRED');
   if(waiting)return;
   verificationDeadline??=Date.now()+verificationTimeout;
   waiting=true;
   arm(Math.max(1,verificationDeadline-Date.now()),failure('等待人工验证超时，本次下载已停止；请重新发起任务','PARSER_VERIFICATION_TIMEOUT'));
   win.setTitle(title);win.show();if(!win.isVisible())win.show();win.focus();
   notify(true,'音乐站需要人工验证，请在弹出的页面手动完成；完成后会自动继续下载和任务');
  };
  const endVerification=completed=>{if(!waiting)return;waiting=false;if(!win.isDestroyed())win.hide();notify(false,completed?'验证通过，正在继续原任务':'人工验证等待已结束');};
  win.on('page-title-updated',event=>{if(waiting){event.preventDefault();win.setTitle(title);}});
  const log=stage=>{if(process.env.SHENSI_PARSER_DIAGNOSTICS==='1')console.log('parser-browser: '+stage);};
  arm(normalTimeout+5000,failure('解析网页等待超时，请检查链接或稍后重试','PARSER_BROWSER_NO_RESULT'));log('created');
  try{
   const wc=win.webContents,cache=new Map(),proxy=process.env.HTTPS_PROXY||process.env.HTTP_PROXY;wc.setAudioMuted(true);wc.setWindowOpenHandler(()=>({action:'deny'}));wc.session.setPermissionRequestHandler((_w,_p,reply)=>reply(false));
   if(proxy&&!allowTestLoopback)await wc.session.setProxy({proxyRules:proxy});
   wc.session.webRequest.onBeforeRequest({urls:['<all_urls>']},(request,reply)=>{
    if(/^(data|blob):/.test(request.url))return reply({cancel:false});
    try{const u=remoteUrl(request.url,{allowTestLoopback});if(allowTestLoopback)return reply({cancel:false});let check=cache.get(u.hostname);if(!check){check=lookup(u.hostname,{all:true}).then(list=>list.length>0&&list.every(a=>publicAddress(a.address)||proxy&&/^198\.(18|19)\./.test(a.address))).catch(()=>false);cache.set(u.hostname,check);}void check.then(ok=>reply({cancel:!ok}));}catch{reply({cancel:true});}
   });
   await win.loadURL('about:blank');log('blank-ready');wc.debugger.attach('1.3');log('attached');await wc.debugger.sendCommand('Page.enable');log('enabled');
   for(const script of scripts)if(typeof script==='string')await wc.debugger.sendCommand('Page.addScriptToEvaluateOnNewDocument',{source:script});
   let navigationError;void win.loadURL(url).then(()=>log('loaded')).catch(e=>navigationError=e);log('navigating');
   const deadline=Date.now()+normalTimeout;let reason;
   while(waiting||Date.now()<deadline){check();
    try{reason=await wc.executeJavaScript(pageGate);}catch{}check();
    if(reason==='verification')beginVerification();
    else{
     let state;try{state=await wc.executeJavaScript(ready);}catch{}check();
     if(state===true||state==='true'){
      const result=await wc.executeJavaScript(extract);check();
      // A challenge can arrive between the readiness check and extraction.
      if(asksForVerification(result))beginVerification();
      else{endVerification(true);return typeof result==='string'?result:JSON.stringify(result);}
     }
    }
    await pause(250,undefined,{signal});
   }
   check();
   throw Object.assign(Error(reason==='verification'?'网站要求人工验证，未自动绕过；请稍后重新解析':reason==='login'?'网站要求登录后查看，当前公开链接不可直接下载':navigationError?'解析网页加载失败：'+(navigationError.code||navigationError.message):'网页未返回可下载的公开内容（等待 '+Math.round(timeout/1000)+' 秒）；请检查链接是否有效或稍后重新解析'),{code:reason==='verification'?'PARSER_VERIFICATION_REQUIRED':reason==='login'?'PARSER_LOGIN_REQUIRED':'PARSER_BROWSER_NO_RESULT'});
  }catch(error){check();throw error;}finally{clearTimeout(timer);signal?.removeEventListener('abort',close);endVerification(false);close();}
 }
 const reset=key=>{const session=sessions.get(key);sessions.delete(key);void session?.clearStorageData().catch(()=>{});};
 return {extract,reset,close(){for(const win of windows)if(!win.isDestroyed())win.destroy();windows.clear();for(const key of sessions.keys())reset(key);}};
}
