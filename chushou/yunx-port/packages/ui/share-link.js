// Link coverage follows YunX-Desktop v1.3.4 ShareLinkParser.
// Recognition is local; a match does not imply download permission.
const providers=[
 ['quark','夸克网盘',h=>h==='pan.quark.cn',/^\/s\/([\w-]+)/],
 ['uc','UC 网盘',h=>h==='drive.uc.cn',/^\/s\/([\w-]+)/],
 ['xunlei','迅雷云盘',h=>h==='pan.xunlei.com',/^\/s\/([\w-]+)/],
 ['baidu','百度网盘',h=>h==='pan.baidu.com',/^\/s\/(1[\w-]+)/],
 ['c139','移动云盘',h=>h==='yun.139.com',/\/w\/i\/([\w-]+)/],
 ['pan123','123 云盘',h=>/^(?:www\.)?123(?:pan|865)\.(?:com|cn)$/.test(h)||/^(?:\d+\.)?share\.123pan\.cn$/.test(h),/\/(?:s|123pan)\/([\w-]+)/],
 ['pan115','115 网盘',h=>/^(?:www\.)?115(?:cdn|rc)?\.com$/.test(h),/^\/(?:s\/)?(sw[a-z0-9]+)/i],
 ['guangya','光鸭云盘',h=>/^(?:www\.)?guangyapan\.com$/.test(h),/^\/s\/([\w-]+)/],
 ['ilanzou','蓝奏优享',h=>/^(?:[\w-]+\.)?ilanzou\.(?:com|net|org|cn)$/.test(h),/^\/s\/([\w-]+)/],
 ['lanzou','蓝奏云',h=>/^(?:[\w-]+\.)?(?:lanzou[a-z0-9-]*|lan[zs]o[ux])\.(?:com|net|org|cn)$/.test(h),/^\/([a-z0-9]+)/i],
];
export function inspectShareLink(text){
 const raw=String(text||'').match(/https?:\/\/[^\s<>"']+/i)?.[0]?.replace(/[。，,；;）)\]】}"'、！？…]+$/u,'');
 if(!raw)return null;let url;try{url=new URL(raw);}catch{return null;}
 if(url.username||url.password)return null;
 for(const [platform,label,host,path] of providers){
  if(!host(url.hostname.toLowerCase()))continue;
  const match=(url.pathname+url.hash).match(path),shareId=match?.[1]||(platform==='pan123'&&url.pathname==='/api/srr'?url.searchParams.get('sk'):'');if(!shareId)return null;
  let accessCode='';for(const key of ['pwd','pass','passcode','password']){const v=url.searchParams.get(key)||new URLSearchParams(url.hash.slice(1)).get(key);if(v&&/^[a-z0-9]{1,32}$/i.test(v)){accessCode=v;break;}}
  accessCode ||= String(text).match(/(?:提取码|访问码|密码)\s*[:：]?\s*([a-z0-9]{4,8})(?![a-z0-9])/i)?.[1]||'';
  if(platform==='pan115')accessCode ||= url.pathname.match(/^\/(sw[a-z0-9]+)-([a-z0-9]{4,8})\/?$/i)?.[2]||'';
  for(const key of ['pwd','pass','passcode','password'])url.searchParams.delete(key);
  if(/(?:pwd|pass|password)=/i.test(url.hash))url.hash='';
  if(platform==='pan115'&&accessCode)url.pathname='/s/'+shareId;
  return {platform,label,shareId,url:url.href,accessCode,needsAccessCode:!accessCode};
 }
 return null;
}
export function validateShareCode(value){const code=String(value||'').trim();if(code&&!/^[a-z0-9]{1,32}$/i.test(code))throw Error('提取码只能包含 1–32 位字母或数字');return code;}
