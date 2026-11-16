// Isolated desktop parser using the same components as Shensi.
const {app,BrowserWindow}=require('electron');
const {readFile,mkdir,writeFile}=require('node:fs/promises');
const {join,resolve}=require('node:path');
const {pathToFileURL}=require('node:url');
const {randomUUID,createHash}=require('node:crypto');
const root=__dirname;
const [sourceFileArg,outArg,selectedId,selectedLabel,selectionKey]=process.argv.slice(2);
if(!sourceFileArg||!outArg){console.error('Usage: electron download.cjs <source-file> <output-dir> [option-id]');app.exit(2);}
const output=resolve(outArg||'.');
app.setPath('userData',join(output,'electron-profile'));
app.commandLine.appendSwitch('disable-gpu');
app.on('window-all-closed',()=>{});
app.whenReady().then(async()=>{
 let parser,exitCode=0;
 try{
  const source=await readFile(sourceFileArg,'utf8');
  const ffmpeg=join(root,'resources/ffmpeg/ffmpeg.exe'),ffprobe=join(root,'resources/ffmpeg/ffprobe.exe');
  const {createYunxVideoParser}=await import(pathToFileURL(join(root,'apps/desktop/yunx-video-parser.mjs')));
  parser=createYunxVideoParser({BrowserWindow,ffmpeg,ffprobe});
  const jobId='video-'+randomUUID();
  let lastProgress='';const progress=e=>{const message=String(e.message||'正在解析').replace(/https?:\/\/\S+/g,'[链接]');if(message!==lastProgress){console.error(message);lastProgress=message;}};
  const parsed=await parser.parse({jobId,sourceText:source,onProgress:progress});
  const options=parsed.options.filter(o=>o.kind==='video');
  if(!options.length)throw Error('解析结果没有视频，未将封面或音频作为视频');
  const selectionOptions=options.map(({id,label,quality})=>({id,label,quality}));
  const currentKey=createHash('sha256').update(JSON.stringify([source,parsed.title,selectionOptions])).digest('hex');
  if(options.length!==1&&!selectedId){console.log(JSON.stringify({status:'selection_required',title:parsed.title,selection_key:currentKey,options:selectionOptions}));exitCode=2;}
  else{
   if(selectedId&&currentKey!==selectionKey)throw Error('解析结果已变化，请重新选择');
   if(selectedId&&options.filter(x=>x.label===selectedLabel).length!==1)throw Error('多个选项标签相同，无法安全确认选择');
   const selected=selectedId?options.find(x=>x.id===selectedId&&x.label===selectedLabel):options[0];
   if(!selected)throw Error('选项的编号或标签已变化，请重新选择');
   const directory=join(output,jobId);await mkdir(directory,{recursive:true});
   const downloaded=await parser.download({jobId,optionId:selected.id,format:'source',directory,onProgress:progress});
   const manifest={status:'downloaded',download_mode:'yunx',title:parsed.title,provider:parsed.provider,output_path:downloaded.path,sha256:downloaded.sha256,bytes:downloaded.size,metadata:{width:downloaded.width,height:downloaded.height,duration_seconds:downloaded.durationMs/1000,validation:'full_video_and_available_audio_decode'}};
   await writeFile(join(directory,'manifest.json'),JSON.stringify(manifest,null,2),{flag:'wx'});
   console.log(JSON.stringify({...manifest,video:downloaded.path,manifest:join(directory,'manifest.json')}));
  }
  parser.stop(jobId);
 }catch(error){console.error(JSON.stringify({status:'failed',code:error.code||'ERROR',error:String(error.message).replace(/https?:\/\/\S+/g,'[链接]')}));exitCode=2;}
 finally{parser?.close();app.exit(exitCode);}
}).catch(error=>{console.error(String(error));app.exit(2);});
