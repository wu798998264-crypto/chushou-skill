package com.yunx.app.data.network.video;

import java.io.*;
import java.util.*;
import kotlin.coroutines.Continuation;
import org.json.*;

/** Host adapter: use Electron's isolated, hidden browser instead of a second JCEF runtime. */
public final class HeadlessWebView {
 public static final HeadlessWebView INSTANCE=new HeadlessWebView();
 public static BufferedReader input;
 public static PrintWriter output;
 public Object loadAndExtract(String url,String userAgent,String ready,String extract,long timeout,long interval,List<String> scripts,Continuation<? super String> continuation){
  try {
   String id=UUID.randomUUID().toString();
   output.println(new JSONObject().put("event","browser-extract").put("id",id).put("url",url).put("ready",ready).put("extract",extract).put("timeout",timeout).put("scripts",new JSONArray(scripts)));
   String line=input.readLine();if(line==null||line.length()>8*1024*1024)return null;
   JSONObject answer=new JSONObject(line);if(!id.equals(answer.optString("id")))throw new IOException("浏览器解析响应身份不一致");
   return answer.isNull("value")?null:answer.optString("value",null);
  }catch(Exception error){throw new IllegalStateException("网页解析连接失败",error);}
 }
 @SuppressWarnings("unchecked") public static Object loadAndExtract$default(HeadlessWebView self,String url,String ua,String ready,String extract,long timeout,long interval,List scripts,Continuation continuation,int mask,Object marker){
  return self.loadAndExtract(url,ua,ready,extract,(mask&16)!=0?25000:timeout,(mask&32)!=0?120:interval,(mask&64)!=0?Collections.emptyList():scripts,continuation);
 }
 public void prewarm(String url){}
 public void closeQuietly(){}
}
