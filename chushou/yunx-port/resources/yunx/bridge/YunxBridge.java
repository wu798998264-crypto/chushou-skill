import java.io.*;
import java.lang.reflect.*;
import java.net.*;
import java.nio.charset.StandardCharsets;
import java.util.*;
import java.util.concurrent.*;
import kotlin.coroutines.*;
import kotlin.coroutines.intrinsics.IntrinsicsKt;
import kotlin.ResultKt;
import org.json.*;
import okhttp3.*;
import com.yunx.app.data.network.HttpClients;

/** JSON-lines adapter for the unmodified YunX-Desktop 1.3.4 parser classes.
 * No desktop launch, database, account import or paid action.
 * Baidu temporary transfer requires explicit host authorization.
 * Each host job owns this process; share credentials live only in its memory.
 */
public final class YunxBridge {
 static final String NETWORK="com.yunx.app.data.network.";
 final Map<String,Object> entries=new LinkedHashMap<>();
 Object api,session;String platform="",shareId="",code="",url="";
 static Object instance(String name)throws Exception {Class<?> c=Class.forName(name);try{return c.getField("INSTANCE").get(null);}catch(NoSuchFieldException e){return c.getConstructor().newInstance();}}
 static Object call(Object target,String name,Object...args)throws Exception {
  for(Method m:target.getClass().getMethods())if(m.getName().equals(name)&&m.getParameterCount()==args.length)return m.invoke(target,args);
  throw new NoSuchMethodException(target.getClass().getSimpleName()+"."+name);
 }
 static Object await(Object target,String name,Object...args)throws Exception {
  CompletableFuture<Object> result=new CompletableFuture<>();
  Continuation<Object> continuation=new Continuation<>(){
   public CoroutineContext getContext(){return EmptyCoroutineContext.INSTANCE;}
   public void resumeWith(Object value){try{ResultKt.throwOnFailure(value);result.complete(value);}catch(Throwable e){result.completeExceptionally(e);}}
  };
  Object[] params=Arrays.copyOf(args,args.length+1);params[args.length]=continuation;
  Object immediate=call(target,name,params);if(immediate!=IntrinsicsKt.getCOROUTINE_SUSPENDED())return immediate;
  return result.get(110,TimeUnit.SECONDS);
 }
 static Object get(Object value,String name)throws Exception {try{return call(value,"get"+Character.toUpperCase(name.charAt(0))+name.substring(1));}catch(NoSuchMethodException e){return call(value,name);}}
 static String str(Object value,String name)throws Exception {Object v=get(value,name);return v==null?"":String.valueOf(v);}
 static Object json(Object value)throws Exception {
  if(value==null)return JSONObject.NULL;
  if(value instanceof String||value instanceof Number||value instanceof Boolean)return value;
  if(value instanceof Enum<?>)return value.toString();
  if(value instanceof Map<?,?> m){JSONObject out=new JSONObject();for(var e:m.entrySet())out.put(String.valueOf(e.getKey()),json(e.getValue()));return out;}
  if(value instanceof Iterable<?> list){JSONArray out=new JSONArray();for(Object item:list)out.put(json(item));return out;}
  JSONObject out=new JSONObject();for(Method m:value.getClass().getMethods())if(m.getParameterCount()==0){if(m.getName().startsWith("get")&&!m.getName().equals("getClass")){String n=m.getName().substring(3);if(!n.isEmpty())out.put(Character.toLowerCase(n.charAt(0))+n.substring(1),json(m.invoke(value)));}else if(m.getName().startsWith("is")&&m.getReturnType()==boolean.class)out.put(m.getName(),m.invoke(value));}return out;
 }
 static void networkPolicy()throws Exception {
  OkHttpClient client=HttpClients.INSTANCE.apiClient().newBuilder().dns(host->{
   List<InetAddress> values=Dns.SYSTEM.lookup(host);for(InetAddress a:values)if(a.isAnyLocalAddress()||a.isLoopbackAddress()||a.isLinkLocalAddress()||a.isSiteLocalAddress()||a.isMulticastAddress())throw new UnknownHostException("解析目标不能指向本机或内网");return values;
  }).build();
  Field cache=HttpClients.class.getDeclaredField("apiCache");cache.setAccessible(true);cache.set(null,client);
 }
 JSONObject parse(JSONObject input)throws Exception {
  entries.clear();url=input.getString("url");platform=input.optString("platform");code=input.optString("accessCode");shareId=input.optString("shareId");
  if(platform.isEmpty()){
   Object detector=instance(NETWORK+"video.VideoLinkParser"),link=call(detector,"parse",url);if(link==null)throw new IllegalArgumentException("解析不支持此视频链接");
   for(int i=0;i<5&&Boolean.TRUE.equals(get(link,"isShortLink"));i++){
    String address=str(link,"url");Request request=new Request.Builder().url(address).header("User-Agent","Mozilla/5.0 (iPhone; CPU iPhone OS 17_0 like Mac OS X) AppleWebKit/605.1.15 Mobile/15E148").build();
    String next;try(Response response=HttpClients.INSTANCE.apiClient().newCall(request).execute()){next=response.request().url().toString();}
    Object resolved=call(detector,"parse",next);if(resolved==null||next.equals(address))break;link=resolved;
   }
   Object parser=call(instance(NETWORK+"video.VideoParsers"),"of",get(link,"platform"));
   Object result=await(parser,"parse",link,"");JSONObject data=(JSONObject)json(result);JSONArray media=data.getJSONArray("medias"),options=new JSONArray();
   for(int i=0;i<media.length();i++){JSONObject m=media.getJSONObject(i);String id="media-"+i;entries.put(id,m);options.put(new JSONObject().put("id",id).put("kind",m.getString("kind").toLowerCase()).put("quality",m.optString("quality")).put("label",m.optString("quality").isEmpty()?m.optString("fileName","媒体 "+(i+1)):m.getString("quality")));}
   return new JSONObject().put("title",data.optString("title","解析结果")).put("provider","解析 · "+data.optString("platform")).put("notice",data.optString("notice")).put("options",options);
  }
  String type=switch(platform){case "quark"->"QuarkApi";case "uc"->"UCApi";case "lanzou"->"LanzouApi";case "pan123"->"Pan123Api";case "pan115"->"Pan115Api";case "guangya"->"GuangYaApi";case "baidu"->"BaiduApi";case "c139"->"C139Api";default->"";};
  if(type.isEmpty())throw new IllegalArgumentException(platform.equals("ilanzou")?"解析当前未提供蓝奏优享分享解析接口":"此网盘需要授权账号，当前解析任务没有已授权的登录会话");
  api=instance(NETWORK+type);
  if(platform.equals("quark")||platform.equals("uc")){session=await(api,"getShareToken",shareId,code,"");if(session==null)throw new IllegalArgumentException("分享无效或提取码不正确");}
  if(platform.equals("lanzou"))session=await(api,"resolveShare",url,code);
  if(platform.equals("guangya"))session=await(api,"getShareAccessToken",shareId,code);
  if(platform.equals("baidu"))session=code.isEmpty()?"":await(api,"verifyShare",shareId.substring(1),code,"");
  return folder("0");
 }
 @SuppressWarnings("unchecked") JSONObject folder(String folder)throws Exception {
  List<Object> files=new ArrayList<>();Object result;
  switch(platform){
   case "quark":case "uc":for(int page=1;page<=20;page++){List<Object> batch=(List<Object>)await(api,"getShareFiles",shareId,str(session,"stoken"),folder,"",page,100);if(batch==null)throw new IllegalArgumentException("解析未返回分享文件列表");files.addAll(batch);if(batch.size()<100)break;if(page==20)throw new IllegalArgumentException("分享目录超过2000项，请分享更小的子目录；未返回不完整列表");}break;
   case "pan123": {String cursor="";for(int page=1;page<=20;page++){result=await(api,"getShareFiles",shareId,code,folder,cursor,page);files.addAll((List<Object>)get(result,"first"));Object next=get(result,"second");if(next==null)break;if(page==20)throw new IllegalArgumentException("分享目录超过20页，请分享更小的子目录；未返回不完整列表");cursor=String.valueOf(next);}break;}
   case "guangya":{String cursor=null;for(int page=1;page<=20;page++){result=await(api,"listShareFiles",session,folder,cursor);files.addAll((List<Object>)get(result,"first"));Object next=get(result,"second");if(next==null)break;if(page==20)throw new IllegalArgumentException("分享目录超过20页，请分享更小的子目录；未返回不完整列表");cursor=String.valueOf(next);}break;}
   case "lanzou":if(Boolean.TRUE.equals(get(session,"isFolder")))files.addAll((List<Object>)await(api,"listShareFolder",session,code,folder));else{Object file=get(session,"singleFile");if(file!=null)files.add(file);}break;
   case "pan115":session=await(api,"getSharePage",shareId,code,folder,"",0,1000);files.addAll((List<Object>)get(session,"files"));if(((Number)get(session,"total")).longValue()>files.size())throw new IllegalArgumentException("分享目录超过1000项，请分享更小的子目录；未返回不完整列表");break;
   case "c139":files.addAll((List<Object>)await(api,"getShareFiles",shareId,folder.equals("0")?"root":folder,code,1,1000));if(files.size()>=1000)throw new IllegalArgumentException("分享目录可能超过1000项，请分享更小的子目录；未返回不完整列表");break;
   case "baidu":for(int page=1;page<=20;page++){result=await(api,"listShare",shareId.substring(1),(String)session,folder.equals("0")?"/":folder,"",page);List<Object> batch=(List<Object>)get(result,"files");files.addAll(batch);if(batch.size()<100)break;if(page==20)throw new IllegalArgumentException("分享目录超过2000项，请分享更小的子目录；未返回不完整列表");}break;
   default:throw new IllegalArgumentException("分享会话已关闭");
  }
  JSONArray options=new JSONArray();for(Object file:files){String id=UUID.randomUUID().toString(),name=str(file,"fname");boolean directory=Boolean.TRUE.equals(get(file,"isdir"));entries.put(id,file);String kind=directory?"folder":name.matches("(?i).*\\.(mp4|webm|mkv|mov|avi|flv|m4v|ts)$")?"video":name.matches("(?i).*\\.(png|jpe?g|webp|gif|bmp)$")?"image":name.matches("(?i).*\\.(mp3|wav|m4a|ogg|flac|aac)$")?"audio":"file";options.put(new JSONObject().put("id",id).put("kind",kind).put("label",name).put("size",get(file,"fsize")));}
  return new JSONObject().put("title","网盘分享").put("provider","解析 · "+platform).put("platform",platform).put("requiresAccount",platform.equals("baidu")).put("options",options).put("folderId",folder);
 }
 JSONObject select(JSONObject input)throws Exception {
  Object file=entries.get(input.getString("id"));if(file==null)throw new IllegalArgumentException("下载项已失效，请重新解析");
  if(file instanceof JSONObject media)return media;
  if(Boolean.TRUE.equals(get(file,"isdir")))return folder(str(file,"fid"));
  if(platform.equals("baidu"))return baiduDownload(file,input);
  Object result=switch(platform){
   case "quark","uc"->await(api,"getGuestShareDownloadLink",str(file,"fid"),str(file,"fidToken"),shareId,str(session,"stoken"));
   case "lanzou"->await(api,"getShareDownloadLink",session,file,code);
   case "guangya"->await(api,"getShareDownloadUrl",file,session);
   case "pan115"->await(api,"getShareDownloadLink",shareId,code,str(session,"userId"),file,"");
   case "c139"->await(api,"getShareDownloadLink",str(file,"fid"),shareId,"",null);
   case "baidu"->throw new IllegalArgumentException("百度分享已解析；解析下载需要百度账号登录并转存到该账号，当前没有这项授权，未转存或删除网盘文件");
   default->throw new IllegalArgumentException("此网盘的文件下载需要账号授权，当前只有公开分享列表权限");
  };
  if(result==null)throw new IllegalArgumentException("解析未返回有权限的下载地址");JSONObject link=(JSONObject)json(result),headers=new JSONObject();if(!link.optString("guestCookie").isEmpty())headers.put("Cookie",link.getString("guestCookie"));headers.put("Referer",url);
  return new JSONObject().put("url",link.getString("downloadUrl")).put("isHls",link.optBoolean("isHls")).put("headers",headers).put("fileName",str(file,"fname"));
 }
 JSONObject baiduDownload(Object file,JSONObject input)throws Exception {
  String cookie=input.optString("cookie");if(!input.optBoolean("allowTemporaryTransfer"))throw new IllegalArgumentException("百度下载需要确认本次临时转存");
  if(!cookie.contains("BDUSS="))throw new IllegalArgumentException("请先在所选浏览器配置登录百度网盘");
  String temp="/神思解析临时-"+UUID.randomUUID(),path="",stage="核验分享权限";
  try{
   call(api,"clearSessionCache");
   String accountKey=code.isEmpty()?"":(String)await(api,"verifyShare",shareId.substring(1),code,cookie);
   Object info=await(api,"listShare",shareId.substring(1),accountKey,"/",cookie,1);
   // The same account may own the shared file. The server checks its fs_id;
   // read that authorized original without transfer and never delete it.
   try {
    String original=(String)await(api,"fileMetasDlink",str(file,"fid"),cookie);
    return new JSONObject().put("url",original).put("fileName",str(file,"fname")).put("headers",new JSONObject().put("Cookie",cookie).put("User-Agent","netdisk;12.24.6;piano;android-android;16;JSbridge4.4.0;jointBridge;1.1.0")).put("transferNotice","分享文件可由当前账号直接下载，未创建或删除网盘副本");
   }catch(Exception ignored){}
   stage="创建临时目录";
   if(!Boolean.TRUE.equals(await(api,"createDir",temp,cookie)))throw new IllegalArgumentException("无法创建本次独立临时目录，未转存到其他位置");
   stage="转存本次分享文件";
   Object transferred=await(api,"transfer",str(info,"shareId"),str(info,"uk"),accountKey,str(file,"fid"),temp,cookie);path=str(transferred,"path");
   if(!path.startsWith(temp+"/")||path.equals(temp+"/"))throw new IllegalArgumentException("百度返回的转存位置不属于本次目录，未继续读取或清理");
   stage="获取本次副本下载地址";
   String link=(String)await(api,"locateDownload",path,cookie);
   // Only the exact newly transferred file can be removed. Never recursively
   // delete the directory: the user may have added another file concurrently.
   boolean cleaned=Boolean.TRUE.equals(await(api,"deleteFile",path,cookie));
   JSONObject headers=new JSONObject().put("Cookie",cookie).put("User-Agent","netdisk;12.24.6;piano;android-android;16;JSbridge4.4.0;jointBridge;1.1.0");
   return new JSONObject().put("url",link).put("fileName",str(file,"fname")).put("headers",headers).put("transferNotice",cleaned?"本次转存副本已清理；独立空目录保留":"本次转存副本未能清理，请到百度网盘检查本次临时目录");
  }catch(Exception e){throw new IllegalArgumentException("百度"+stage+"失败："+Objects.toString(e.getMessage(),"百度下载失败").replace(cookie,"[登录信息]")+(stage.equals("核验分享权限")?"":"；本次临时目录："+temp),e);}
 }
 public static void main(String[] args)throws Exception {
  networkPolicy();YunxBridge bridge=new YunxBridge();BufferedReader reader=new BufferedReader(new InputStreamReader(System.in,StandardCharsets.UTF_8));PrintWriter output=new PrintWriter(new OutputStreamWriter(System.out,StandardCharsets.UTF_8),true);
  com.yunx.app.data.network.video.HeadlessWebView.input=reader;com.yunx.app.data.network.video.HeadlessWebView.output=output;
  String line;while((line=reader.readLine())!=null){JSONObject answer=new JSONObject();try{if(line.length()>16000)throw new IllegalArgumentException("请求过长");JSONObject in=new JSONObject(line);Object value=switch(in.getString("action")){case "version"->new JSONObject().put("engine","YunX-Desktop").put("version","1.3.4");case "parse"->bridge.parse(in);case "select"->bridge.select(in);default->throw new IllegalArgumentException("未知解析操作");};answer.put("ok",true).put("data",value);}catch(Throwable e){while((e instanceof InvocationTargetException||e instanceof ExecutionException)&&e.getCause()!=null)e=e.getCause();String message=Objects.toString(e.getMessage(),e.getClass().getSimpleName());if(!bridge.code.isEmpty())message=message.replace(bridge.code,"[提取码]");answer.put("ok",false).put("error",message.substring(0,Math.min(500,message.length())));}output.println(answer);}
  System.exit(0);
 }
}
