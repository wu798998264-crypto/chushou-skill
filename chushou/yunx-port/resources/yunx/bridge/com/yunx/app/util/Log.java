package com.yunx.app.util;
/** Headless host override: never persist URLs, access codes, cookies or tokens. */
public final class Log {
 public static final Log INSTANCE=new Log();
 public void i(String tag,String message) {}
 public void d(String tag,String message) {}
 public void w(String tag,String message) {}
 public void e(String tag,String message) {}
 public void e(String tag,String message,Throwable error) {}
}
