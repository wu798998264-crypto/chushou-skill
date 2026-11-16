package com.yunx.app.util;
/** Isolated parser never uses the desktop application's diagnostic store. */
public final class DiagnosticLog {
 public static final DiagnosticLog INSTANCE=new DiagnosticLog();
 public boolean isEnabled(){return false;}
 public void install(){}
 public void webview(String a,String b,Object c,String d){}
 public static void webview$default(DiagnosticLog a,String b,String c,Object d,String e,int f,Object g){}
 public void network(String a,String b,int c,long d,String e,String f,String g){}
 public static void network$default(DiagnosticLog a,String b,String c,int d,long e,String f,String g,String h,int i,Object j){}
 public void event(String a,String b,String c){}
 public static void event$default(DiagnosticLog a,String b,String c,String d,int e,Object f){}
 public void warn(String a,String b,String c){}
 public static void warn$default(DiagnosticLog a,String b,String c,String d,int e,Object f){}
 public void error(String a,String b,Object c,String d){}
 public static void error$default(DiagnosticLog a,String b,String c,Object d,String e,int f,Object g){}
}
