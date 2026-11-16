import {spawn} from 'node:child_process';
import {join,delimiter,resolve} from 'node:path';
import {fileURLToPath} from 'node:url';
import {existsSync} from 'node:fs';
import {createInterface} from 'node:readline';

const fail=(message,code='YUNX_PARSE_FAILED')=>Object.assign(Error(message),{code});
export function createYunxEngine({runtimeRoot=process.env.SHENSI_YUNX_ROOT||fileURLToPath(new URL('../../resources/yunx/runtime/',import.meta.url)),classes=process.env.SHENSI_YUNX_CLASSES||fileURLToPath(new URL('../../resources/yunx/classes/',import.meta.url)),browser}={}){
 const executable=join(resolve(runtimeRoot),'runtime','bin','java.exe');if(!existsSync(executable)||!existsSync(join(classes,'YunxBridge.class')))throw fail('解析解析组件未就绪，请更新完整桌面组件','YUNX_RUNTIME_MISSING');
 const child=spawn(executable,['-Dfile.encoding=UTF-8','-Djava.awt.headless=true','-cp',[classes,join(runtimeRoot,'app','*')].join(delimiter),'YunxBridge'],{windowsHide:true,shell:false,stdio:['pipe','pipe','pipe']});
 let pending,closed=false,terminal;const controller=new AbortController(),close=()=>{closed=true;controller.abort();child.kill();};
 const rejectAll=error=>{terminal=error;pending?.reject(error);pending=null;};
 const lines=createInterface({input:child.stdout});lines.on('line',async line=>{if(!pending)return;let result;try{result=JSON.parse(line);}catch{return;}
  if(result.event==='browser-extract'){try{if(!browser)throw fail('此网站需要桌面解析浏览器','PARSER_BROWSER_REQUIRED');const value=await browser.extract(result,{signal:controller.signal});if(!closed)child.stdin.write(JSON.stringify({id:result.id,value})+'\n');}catch(error){rejectAll(error);close();}return;}
  const waiting=pending;pending=null;result.ok?waiting.resolve(result.data):waiting.reject(fail(result.error||'解析未返回结果'));});
 child.stderr.resume();child.on('error',()=>rejectAll(fail('解析解析核心无法启动','YUNX_RUNTIME_MISSING')));child.on('exit',()=>{closed=true;rejectAll(fail('解析解析任务已结束','YUNX_SESSION_CLOSED'));lines.close();});
 async function request(input,{signal}={}){
  signal?.throwIfAborted();if(closed||terminal)throw terminal||fail('解析会话已关闭');if(pending)throw fail('当前解析尚在运行，请等待完成');
  let timer,onAbort;try{return await new Promise((resolve,reject)=>{pending={resolve,reject};onAbort=()=>{rejectAll(fail('解析已停止','ABORTED'));close();};signal?.addEventListener('abort',onAbort,{once:true});timer=setTimeout(()=>{rejectAll(fail('解析解析超时，请核对网站状态','YUNX_TIMEOUT'));close();},120000);child.stdin.write(JSON.stringify(input)+'\n',error=>{if(error)rejectAll(fail('解析解析通道已关闭'));});if(signal?.aborted)onAbort();});}
  finally{clearTimeout(timer);signal?.removeEventListener('abort',onAbort);}
 }
 return {request,close};
}
