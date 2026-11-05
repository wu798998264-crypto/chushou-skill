import {createWritingEditor} from './writing-editor.js';
import {escapeHtml as h} from './editor.js';

const $=selector=>document.querySelector(selector);
let toastTimer;
function toast(message){clearTimeout(toastTimer);$('#toast').textContent=message;$('#toast').style.display='block';toastTimer=setTimeout(()=>$('#toast').style.display='none',4500);}
// Same three-choice unsaved-draft modal as the Shensi host.
function modal(title,body,buttons=[['取消','cancel'],['确定','ok']]){
  const dialog=$('#dialog');if(dialog.open)dialog.close();
  dialog.innerHTML=`<h2>${h(title)}</h2>${body}<div class="dialog-actions">${buttons.map(([label,value],i)=>`<button data-choice="${value}" class="${i===buttons.length-1?'primary':''}">${h(label)}</button>`).join('')}</div>`;
  dialog.showModal();
  return new Promise(resolve=>{
    const done=value=>{dialog.close();resolve({value});};
    dialog.onclick=event=>{const button=event.target.closest('[data-choice]');if(button)done(button.dataset.choice);};
    dialog.oncancel=event=>{event.preventDefault();done('cancel');};
  });
}
async function call(action,payload){
  const response=await fetch('./api/'+action,{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify(payload)});
  const result=await response.json();
  if(!response.ok)throw new Error(result.error||result.message||'写作版本服务暂时不可用');
  return result;
}
const dark=matchMedia('(prefers-color-scheme: dark)');
const theme=()=>document.documentElement.dataset.theme=dark.matches?'dark':'light';
theme();dark.addEventListener('change',theme);

try{
  const key='shensi-writing-client';
  const resume=new URLSearchParams(location.hash.slice(1)).get('client');
  if(resume){
    if(!/^[A-Za-z0-9][A-Za-z0-9_-]{0,127}$/.test(resume))throw new Error('草稿恢复身份无效');
    sessionStorage.setItem(key,resume);
  }
  let clientId=sessionStorage.getItem(key);
  if(!clientId){clientId='writer-'+crypto.randomUUID();sessionStorage.setItem(key,clientId);}
  const initial=await call('ui-read',{clientId});
  document.title=initial.title+' · 触手写作编辑器';
  $('#document-title').textContent=initial.title;
  const editor=createWritingEditor({item:initial,call,modal,toast,scopeKey:(initial.storeId||initial.storeRoot||'chushou')+'|'+initial.id});
  // Reuse the live controls so their handlers, history and focus remain intact.
  const head=editor.root.querySelector('.writing-head');
  const save=editor.root.querySelector('[data-writing-action=save]');
  head.prepend($('#document-title'));
  for(const action of ['undo','redo','copy']){
    head.insertBefore(editor.root.querySelector(`[data-writing-action=${action}]`),save);
  }
  editor.root.querySelector('.writing-tools').remove();
  $('.host-heading').remove();
  editor.attach($('#writing-home'));
  await editor.ready;
  // Observe accepted agent edits. The saved-version pointer stays authoritative;
  // the component never refreshes over an active edit or an unsaved draft.
  let revision=initial.revision,instructionRevision=null,checking=false;
  async function refresh(){
    if(document.hidden||checking)return;
    checking=true;
    try{
      const response=await fetch('./api/health');
      if(!response.ok)return;
      const state=await response.json();
      if(instructionRevision===null)instructionRevision=state.instruction_revision;
      if(state.instruction_revision!==instructionRevision){
        const folded=editor.collapseLongContent();
        if(folded||(!editor.root.classList.contains('is-editing')&&
            !editor.root.querySelector('[data-writing-action=edit]').disabled)){
          instructionRevision=state.instruction_revision;
        }
      }
      if(state.document_revision!==revision){
        const adopted=await editor.refresh({revision:state.document_revision});
        if(adopted!==null)revision=adopted;
      }
    }catch{/* The editor's own save path reports errors and preserves its WAL. */}
    finally{checking=false;}
  }
  window.addEventListener('focus',refresh);
  const timer=setInterval(refresh,2000);
  window.addEventListener('pagehide',()=>{clearInterval(timer);void editor.flush().catch(()=>{});});
}catch(error){
  // The title stays inside the editor after the compact toolbar is mounted.
  $('#document-title').textContent='写作编辑器未能打开';
  $('#writing-home').textContent=error.message+'。请重新启动本项目编辑器服务；不要用旧初稿替代已修改的稿件。';
}
