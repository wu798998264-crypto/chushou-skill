const axios = require('./http.cjs');
const fs = require('fs');
const path = require('path');
const crypto = require('crypto');
const { coreSixgod } = require('./sixgod');

const API = "https://api5-normal-sinfonlineb.fqnovel.com";

// ===== 取流接口签名（六神签名：x-gorgon/x-khronos/x-ladon/x-argus/x-helios/x-medusa）=====
const SIGNED_UA = "com.phoenix.read/71532 (Linux; U; Android 9; zh_CN; 2509FPN0BC; Build/PQ3A.190605.07291804)";
const SIGNED_IID = "4368802899821595";
const SIGNED_DEVICE_ID = "4368802900374539";

function buildBaseParams() {
  return {
    iid: SIGNED_IID, device_id: SIGNED_DEVICE_ID,
    ac: 'wifi', channel: 'oppo_8662_64', aid: '8662', app_name: 'novelread',
    version_code: '71532', version_name: '7.1.5.32',
    device_platform: 'android', os: 'android', ssmix: 'a',
    device_type: '2509FPN0BC', device_brand: 'XIAOMI', language: 'zh',
    os_api: '28', os_version: '9', manifest_version_code: '71532',
    resolution: '1280*2772', dpi: '520', update_version_code: '71532',
    host_abi: 'arm64-v8a', dragon_device_type: 'phone', pv_player: '71532',
    compliance_status: '0', need_personal_recommend: '1',
    player_so_load: '1', is_android_pad_screen: '0',
  };
}

function buildDevices() {
  return {
    device_id: SIGNED_DEVICE_ID, iid: SIGNED_IID, install_id: SIGNED_IID,
    device_brand: 'XIAOMI', device_model: '2509FPN0BC',
    device_type: '2509FPN0BC', device_manufacturer: 'XIAOMI',
    os_version: '9', version_name: '7.1.5.32', ua: SIGNED_UA,
  };
}

function buildSignHeaders() {
  return {
    "User-Agent": SIGNED_UA,
    "Accept": "application/json; charset=utf-8,application/x-protobuf",
    "Content-Type": "application/json; charset=UTF-8",
    "x-xs-from-web": "0",
    "x-ss-req-ticket": String(Date.now()),
    "x-tt-request-tag": "t=0;n=0",
    "sdk-version": "2",
    "passport-sdk-version": "50561",
    "x-vc-bdturing-sdk-version": "3.7.2.cn",
  };
}

/**
 * 带六神签名的 API 请求
 *
 * 服务端要求 X-Gorgon / X-Khronos / X-SS-STUB / X-Ladon / X-Argus / X-Helios / X-Medusa，
 * 缺任一签名会被静默拒绝（HTTP 200 但响应体为空或 101000 service error）。
 * 注意：签名是对「最终发送的 URL 查询串」与 request body 计算，必须与实际发送内容完全一致。
 */
async function signedPost(path, body, opts) {
  const bodyStr = JSON.stringify(body);
  const { url, headers } = coreSixgod({
    surl: API + path,
    params: buildBaseParams(),
    devices: buildDevices(),
    data: body,
    header: buildSignHeaders(),
    lanusk: '',
  });
  const res = await axios.post(url, bodyStr, {
    headers,
    timeout: 25000,
    responseType: 'text',
  });
  if (opts && opts.returnRaw) return typeof res.data === 'string' ? res.data : JSON.stringify(res.data);
  return typeof res.data === 'string' ? JSON.parse(res.data) : res.data;
}

const sleep = (ms) => new Promise((r) => setTimeout(r, ms));

/**
 * 带六神签名的 GET 请求（签名覆盖完整查询串）
 */
async function signedGet(path, extraParams, retries = 2) {
  let lastErr = null;
  for (let attempt = 0; attempt < retries; attempt++) {
    const params = { ...buildBaseParams(), _rticket: String(Date.now()), ...(extraParams || {}) };
    const { url, headers } = coreSixgod({
      surl: API + path,
      params,
      devices: buildDevices(),
      header: buildSignHeaders(),
      lanusk: '',
    });
    try {
      const res = await axios.get(url, { headers, timeout: 20000, responseType: 'text' });
      const txt = typeof res.data === 'string' ? res.data : JSON.stringify(res.data);
      if (!txt || !txt.trim()) throw new Error('接口返回为空（可能被风控拦截，请稍后重试）');
      return JSON.parse(txt);
    } catch (err) {
      lastErr = err;
      if (attempt < retries - 1) await sleep(1000 * (attempt + 1));
    }
  }
  throw lastErr || new Error('请求失败');
}

const UA = "com.phoenix.read/71532 (Linux; U; Android 9; SM-N9860; Build/PQ3A.190705.10241111;tt-ok/3.12.13.20)";
const VIDEO_REFERER = "https://novelquickapp.com/";

const COMMON_QUERY = {
  klink_egdi: "AAI29o4dI-eMiO73_SRSbZ_0By1v3fUSriNeu8-L951MoXhWT88pzj5B",
  iid: "3788260546453235",
  device_id: "538083340353620",
  ac: "wifi",
  channel: "oppo_8662_64",
  aid: "8662",
  app_name: "novelread",
  version_code: "71532",
  version_name: "7.1.5.32",
  device_platform: "android",
  os: "android",
  ssmix: "a",
  device_type: "SM-N9860",
  device_brand: "Samsung",
  language: "zh",
  os_api: "28",
  os_version: "9",
  manifest_version_code: "71532",
  resolution: "900*1600",
  dpi: "320",
  update_version_code: "71532",
  host_abi: "arm64-v8a",
  dragon_device_type: "pad",
  pv_player: "71532",
  compliance_status: "0",
  need_personal_recommend: "1",
  player_so_load: "1",
  is_android_pad_screen: "0",
  rom_version: "PQ3A.190705.10241111+release-keys",
  cdid: "b4f93387-5319-4134-aab9-2cd4e9279b8f",
};

const HEADERS = {
  "User-Agent": UA,
  "Accept-Encoding": "gzip",
  "Accept": "application/json; charset=utf-8,application/x-protobuf",
  "Content-Type": "application/json; charset=utf-8",
  "Host": "api5-normal-sinfonlineb.fqnovel.com",
};

const DETAIL_BIZ_PARAM = {
  detail_page_version: 0,
  disable_digg_stat: false,
  image_shrink_datas_str: "W3siaW1hZ2VfdHlwZSI6MywiaW1hZ2Vfd2lkdGgiOjkwMCwic2hyaW5rX3R5cGUiOjN9LHsiaW1hZ2VfdHlwZSI6NCwiaW1hZ2Vfd2lkdGgiOjcyLCJzaHJpbmtfdHlwZSI6NH1d\n",
  need_all_video_definition: false,
  need_mp4_align: false,
  screen_width_px: "900",
  source: 7,
  use_os_player: false,
  use_server_dns: false,
};

const MODEL_BIZ_PARAM = {
  detail_page_version: 0,
  // 4 才能稳定拿到 bytevc1（HEVC，容器标记 hvc1）。
  // 3 对部分剧集只返回 bytevc2（字节私有编码，标准播放器/ffmpeg 均无法解码，文件看起来正常但播不了）
  device_level: 4,
  disable_digg_stat: false,
  need_all_video_definition: true,
  need_mp4_align: false,
  use_os_player: false,
  use_server_dns: false,
  video_platform: 1024,
};

function modelBizParam(deviceLevel) {
  return { ...MODEL_BIZ_PARAM, device_level: deviceLevel };
}

/**
 * API 调用封装
 */
async function apiCall(apiPath, body, retries = 3) {
  const q = { ...COMMON_QUERY, _rticket: Date.now().toString() };
  for (let i = 0; i < retries; i++) {
    try {
      const res = await axios.post(API + apiPath, body, {
        params: q,
        headers: HEADERS,
        timeout: 25000,
      });
      if (res.status !== 200) {
        throw new Error(`HTTP ${res.status}`);
      }
      return res.data;
    } catch (err) {
      if (i === retries - 1) throw err;
      await new Promise(r => setTimeout(r, 2000 * (i + 1)));
    }
  }
}

/**
 * 从任意文本中提取分享链接 / series_id
 *  支持：纯数字 ID、纯链接、带引导文案的长文本（如「复制这段文字，打开红果短剧查看…」）
 */
function extractInput(raw) {
  const text = String(raw || '').trim();
  if (!text) throw new Error('输入内容为空');

  // 1) 纯数字 series_id
  if (/^\d{6,}$/.test(text)) return { type: 'id', value: text };

  // 2) 文本中的链接（含未带协议头的短链）
  const urlMatch = text.match(/https?:\/\/[^\s，,、"'）)】\]<>]+/i);
  if (urlMatch) return { type: 'url', value: urlMatch[0] };

  const shortMatch = text.match(/\b(?:novelquickapp|fanqienovel|fqnovel|hongguo)\.[a-z.]+\/[^\s，,、"'）)】\]<>]+/i);
  if (shortMatch) return { type: 'url', value: 'https://' + shortMatch[0] };

  // 3) 文本里直接夹带的 video_series_id / series_id / video_id
  const idMatch =
    text.match(/video_series_id=(\d+)/) ||
    text.match(/series_id["':=\s]+(\d{6,})/) ||
    text.match(/video_id["':=\s]+(\d{6,})/);
  if (idMatch) return { type: 'id', value: idMatch[1] };

  // 4) 长文本中的长数字串（最后一个），大概率是剧集 ID
  const digits = text.match(/\d{15,25}/g);
  if (digits && digits.length) return { type: 'id', value: digits[digits.length - 1] };

  throw new Error('未能从输入内容中识别出短剧链接或 ID');
}

/**
 * 分享链接 -> series_id
 */
async function resolveSeriesId(shareUrl) {
  const { type, value } = extractInput(shareUrl);
  if (type === 'id') return value;

  let res;
  try {
    res = await axios.get(value, {
      headers: {
        "User-Agent": "Mozilla/5.0 (Linux; Android 9; SM-N9860) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/88.0.4324.152 Mobile Safari/537.36",
      },
      timeout: 25000,
      maxRedirects: 10,
    });
  } catch (err) {
    // 短链跳转异常时，仍尝试从错误响应的地址里解析
    const badUrl = err?.request?.res?.responseUrl || err?.config?.url || '';
    const m = String(badUrl).match(/video_series_id=(\d+)/);
    if (m) return m[1];
    throw new Error('链接请求失败: ' + err.message);
  }

  const finalUrl = res.request?.res?.responseUrl || res.config?.url || value;
  let m = finalUrl.match(/video_series_id=(\d+)/) || finalUrl.match(/[?&]series_id=(\d+)/);
  if (m) return m[1];

  m = finalUrl.match(/schemeParams[^&]*/);
  if (m) {
    let raw = m[0];
    try {
      raw = decodeURIComponent(decodeURIComponent(raw));
    } catch (_) {}
    const jm = raw.match(/"(?:video_id|series_id|video_series_id)"\s*:\s*"?(\d+)"?/);
    if (jm) return jm[1];
  }

  const bodyText = typeof res.data === 'string' ? res.data : JSON.stringify(res.data || '');
  m =
    bodyText.match(/video_series_id=(\d+)/) ||
    bodyText.match(/"(?:video_id|video_series_id|series_id)"\s*:\s*"?(\d+)"?/);
  if (m) return m[1];

  // 兜底：最终地址或响应体中的长数字
  const digits = (finalUrl + ' ' + bodyText).match(/\d{17,20}/g);
  if (digits && digits.length) return digits[0];

  throw new Error("无法从链接解析 series_id: " + finalUrl);
}

/**
 * 获取全集剧集列表
 *
 * 服务端已改为只接受不带 dr_scene 的路径，且需要签名。
 */
/**
 * 用户投稿合集（P-UGC album）信息
 * 单条视频分享（video-pugc-share）背后通常会挂一个「合集」，用 album_detail 能拿到合集名与集数
 */
/**
 * 中文/阿拉伯数字转整数（合集标题里的「第十一集」这类）
 */
function cnToNumber(input) {
  const s = String(input || '').trim();
  if (!s) return 0;
  if (/^\d+$/.test(s)) return parseInt(s, 10);
  const d = { 零: 0, 一: 1, 二: 2, 三: 3, 四: 4, 五: 5, 六: 6, 七: 7, 八: 8, 九: 9 };
  if (s === '十') return 10;
  const m = s.match(/^([一二三四五六七八九])?十([一二三四五六七八九])?$/);
  if (m) return (m[1] ? d[m[1]] : 1) * 10 + (m[2] ? d[m[2]] : 0);
  if (s.length === 1 && s in d) return d[s];
  return 0;
}

/**
 * 清理合集成员标题：
 * 接口给的是「一口气看完《神坠》 ：第一集」这种，统一收敛成「第一集」
 */
function cleanAlbumEpTitle(title) {
  const t = String(title || '').trim();
  if (!t) return '';
  const m = t.match(/第\s*[\d零一二三四五六七八九十百千]+\s*集/);
  if (m) return m[0].replace(/\s+/g, '');
  return t.length > 24 ? t.slice(0, 24) : t;
}

async function fetchAlbumInfo(albumId) {
  const raw = await signedPost(
    "/novel/player/album_detail/v1/",
    { album_id: String(albumId) },
    { returnRaw: true }
  );
  const j = typeof raw === 'string' ? JSON.parse(raw) : raw;
  const ad = (j && j.data && j.data.album_data) || {};
  const list = ad.video_detail_list || [];
  // 合集成员的 video_list 里 vid 是空串，真正的视频 id 就是成员的 series_id（P-UGC 单条内容 series_id == vid）
  const base = list
    .map((m, i) => {
      const v = (m.video_list || [])[0] || {};
      const sid = String(m.series_id_str || m.series_id || '');
      const rawTitle = String(m.series_title || '');
      const numMatch = rawTitle.match(/第\s*([\d零一二三四五六七八九十百]+)\s*集/);
      return {
        vid: String(v.vid || sid),
        seq: i + 1,
        realIndex: numMatch ? cnToNumber(numMatch[1]) : 0,
        title: cleanAlbumEpTitle(rawTitle),
        cover: m.series_cover || v.cover || '',
        content_type: String(m.content_type || '1001'),
        duration: Number(v.duration || m.duration || 0),
        series_id: sid,
      };
    })
    .filter((x) => x.vid);

  // 标题里的真实集数优先（合集可能只有部分切片，如 1,2,3,4,6,7,8,11）；重号则退回顺序号
  const usedIdx = new Set();
  const members = base.map((x) => {
    let idx = x.realIndex && !usedIdx.has(x.realIndex) ? x.realIndex : x.seq;
    while (usedIdx.has(idx)) idx += 1;
    usedIdx.add(idx);
    return { ...x, vid_index: idx };
  });

  return {
    album_id: String(ad.album_id_str || albumId),
    title: ad.title || '',
    episode_count: Number(ad.episode_cnt) || list.length || 0,
    cover: ad.cover || '',
    intro: ad.intro || '',
    members,
  };
}

async function fetchEpisodeList(seriesId) {
  // 用原始文本解析：related_album_id 是 19 位大整数，JSON.parse 会丢精度
  const raw = await signedPost(
    "/novel/player/multi_video_detail/v1/",
    { biz_param: DETAIL_BIZ_PARAM, series_id: seriesId },
    { returnRaw: true }
  );
  const j = typeof raw === 'string' ? JSON.parse(raw) : raw;
  if (!j || j.code !== 0) {
    throw new Error("detail API 失败: " + JSON.stringify(j || {}).slice(0, 200));
  }
  const d = j.data || {};
  const sid = Object.keys(d)[0];
  if (!sid) {
    throw new Error("未获取到剧集数据");
  }
  const vd = d[sid]?.video_data || {};
  const vl = vd.video_list || [];
  const eps = vl.map((item) => ({
    vid: String(item.vid),
    vid_index: item.vid_index || 0,
    title: item.title || '',
    series_id: String(item.series_id || sid),
    series_title: vd.series_title || '',
    cover: vd.series_cover || vd.cover_url || item.cover_url || '',
  }));
  eps.sort((a, b) => a.vid_index - b.vid_index);

  // 接口给每个分集返回的 title 其实是整部剧的剧情简介（各集完全相同、且很长），
  // 直接展示/拼进文件名毫无意义，这里统一清空。
  const allSameTitle = eps.length > 1 && eps.every((e) => e.title === eps[0].title);
  eps.forEach((e) => {
    if (!e.title) return;
    if (allSameTitle || e.title.length > 30) e.title = '';
  });

  // ===== 单条视频（P-UGC 用户投稿）识别 =====
  // 这类内容 series_id == vid，episode_cnt 为 0，video_list 只有 1 条，
  // 背后挂着一个「合集」（related_album_id），合集会真实报出总集数。
  const albumMatch = /"related_album_id":(\d+)/.exec(typeof raw === 'string' ? raw : '');
  const relatedAlbumId = albumMatch ? albumMatch[1] : '';
  const isSinglePugc = eps.length === 1 && !Number(vd.episode_cnt) && !!relatedAlbumId;

  let relatedAlbum = null;
  if (isSinglePugc) {
    try {
      relatedAlbum = await fetchAlbumInfo(relatedAlbumId);
    } catch (_) {
      relatedAlbum = { album_id: relatedAlbumId, title: '', episode_count: 0, cover: '', intro: '', members: [] };
    }
  }

  // 单条视频背后挂着合集时，把合集里的全部剧集展开出来（这才是用户真正想看的整部剧）
  let episodes = eps;
  let fromAlbum = false;
  if (isSinglePugc && relatedAlbum && Array.isArray(relatedAlbum.members) && relatedAlbum.members.length > 1) {
    episodes = relatedAlbum.members.map((m) => ({
      vid: m.vid,
      vid_index: m.vid_index,
      title: m.title,
      series_id: m.series_id || m.vid,
      series_title: relatedAlbum.title || vd.series_title || '',
      cover: m.cover || relatedAlbum.cover || '',
      content_type: m.content_type,
      duration: m.duration,
    }));
    fromAlbum = true;
  } else {
    // 普通剧集也带上 content_type，取流时优先用它（官方剧是 1004，用户投稿是 1001）
    episodes = eps.map((e) => ({ ...e, content_type: String(vd.content_type || '') }));
  }

  const stripBookMark = (s) => String(s || '').replace(/^《/, '').replace(/》$/, '').trim();

  // 剧集简介与题材（用于随剧保存「简介.txt」）
  let categories = [];
  try {
    const schema = JSON.parse(vd.category_schema || '[]');
    categories = schema.map((s) => s && s.name).filter(Boolean);
  } catch (_) {}
  const intro = (fromAlbum && relatedAlbum && relatedAlbum.intro) || vd.series_intro || '';
  const playCount = parseInt(vd.series_play_cnt, 10) || 0;

  return {
    series_id: String(sid),
    series_title: fromAlbum
      ? (stripBookMark(relatedAlbum.title) || vd.series_title || '未命名短剧')
      : (vd.series_title || '未命名短剧'),
    cover: (fromAlbum && relatedAlbum.cover) || vd.series_cover || vd.cover_url || (eps[0] && eps[0].cover) || '',
    total: episodes.length,
    intro,
    categories,
    play_count: playCount,
    episodes,
    // 单条视频分享（用户投稿 P-UGC），背后可能挂着合集
    is_single_pugc: isSinglePugc,
    from_album: fromAlbum,
    related_album: relatedAlbum
      ? {
          album_id: relatedAlbum.album_id,
          title: relatedAlbum.title,
          episode_count: relatedAlbum.episode_count,
          cover: relatedAlbum.cover,
          intro: relatedAlbum.intro,
        }
      : null,
  };
}

// ===== 短剧搜索 =====
const SEARCH_PATH = '/reading/bookapi/search/tab/v1/';
const SUGGEST_PATH = '/reading/bookapi/search/suggest/v1/';
const SEARCH_CACHE_TTL = 3 * 60 * 1000;
const searchCache = new Map(); // key: `${q}@${offset}` -> { t, v }

function trimSearchCache() {
  if (searchCache.size <= 60) return;
  const keys = Array.from(searchCache.keys()).slice(0, searchCache.size - 60);
  keys.forEach((k) => searchCache.delete(k));
}

/**
 * 把搜索接口返回的 cell 归一化成前端可用的短剧卡片
 */
function normalizeSearchCell(cell) {
  if (!cell) return null;
  const vd = Array.isArray(cell.video_data) ? cell.video_data[0] : null;
  if (!vd) return null;

  const detail = vd.video_detail || {};
  const seriesId = String(detail.series_id_str || vd.series_id || cell.book_id || '');
  if (!/^\d{6,}$/.test(seriesId)) return null;

  const title =
    detail.series_title ||
    vd.title ||
    (cell.search_high_light && cell.search_high_light.title && cell.search_high_light.title.text) ||
    vd.raw_book_name ||
    '未命名短剧';

  const tags = [];
  const pushTag = (t) => {
    const s = String(t || '').trim();
    if (s && !tags.includes(s) && tags.length < 5) tags.push(s);
  };
  try {
    const schema = JSON.parse(detail.category_schema || vd.category_schema || '[]');
    schema.forEach((it) => pushTag(it && it.name));
  } catch (_) {}
  if (Array.isArray(vd.rec_tags)) {
    vd.rec_tags.forEach((t) => {
      if (t && typeof t.content === 'string' && !/万|亿|收藏|热度/.test(t.content)) pushTag(t.content);
    });
  }

  return {
    series_id: seriesId,
    title: String(title).trim(),
    cover: detail.series_cover || vd.cover || '',
    total: parseInt(detail.episode_cnt || vd.episode_cnt, 10) || 0,
    play_count: parseInt(vd.play_cnt || detail.series_play_cnt, 10) || 0,
    followed_count: parseInt(detail.followed_cnt, 10) || 0,
    score: vd.score || '',
    intro: detail.series_intro || '',
    actors: detail.role || '',
    tag: (vd.tag_info && vd.tag_info.text) || '',
    tags,
    first_vid: detail.first_vid || vd.vid || '',
  };
}

/**
 * 按关键词搜索短剧
 * @param {string} keyword
 * @param {{offset?: number, count?: number}} [opts]
 * @returns {Promise<{keyword:string, items:Array, rawCount:number, nextOffset:number, hasMore:boolean}>}
 */
async function searchSeries(keyword, opts = {}) {
  const q = String(keyword || '').trim();
  if (!q) throw new Error('请输入搜索关键词');
  if (q.length > 40) throw new Error('搜索关键词过长');

  const offset = Math.max(0, parseInt(opts.offset, 10) || 0);
  const cacheKey = `${q}@${offset}`;
  const cached = searchCache.get(cacheKey);
  if (cached && Date.now() - cached.t < SEARCH_CACHE_TTL) {
    return cached.v;
  }

  const j = await signedGet(SEARCH_PATH, { query: q, offset: String(offset) });
  if (!j || (j.code !== undefined && j.code !== 0)) {
    throw new Error('搜索接口异常: ' + JSON.stringify(j || {}).slice(0, 160));
  }

  const tabs = Array.isArray(j.search_tabs) ? j.search_tabs : [];
  const mainTab = tabs.find((t) => t.tab_type === 11) || tabs[0] || {};
  const cells = Array.isArray(mainTab.data) ? mainTab.data : [];

  const items = [];
  const seen = new Set();
  for (const cell of cells) {
    const item = normalizeSearchCell(cell);
    if (!item || seen.has(item.series_id)) continue;
    seen.add(item.series_id);
    items.push(item);
  }

  const result = {
    keyword: q,
    items,
    rawCount: cells.length,
    nextOffset: offset + cells.length,
    hasMore: cells.length > 0 && items.length > 0,
  };

  searchCache.set(cacheKey, { t: Date.now(), v: result });
  trimSearchCache();
  return result;
}

/**
 * 搜索关键词联想
 */
async function searchSuggest(keyword) {
  const q = String(keyword || '').trim();
  if (!q) return [];
  const j = await signedGet(SUGGEST_PATH, { query: q }, 1);
  const list = (j && j.data && Array.isArray(j.data.query_result)) ? j.data.query_result : [];
  return list.filter((x) => typeof x === 'string' && x.trim()).slice(0, 10);
}

/**
 * 单条视频流的清晰度评分
 * 返回 { defNum, pixels, bitrate }，数值越大越清晰
 */function streamScore(v) {
  const meta = v.video_meta || {};
  const defStr = String(meta.definition || '');
  let defNum = 0;
  const m = defStr.match(/(\d+)\s*p/i);
  if (m) {
    defNum = parseInt(m[1], 10);
  } else {
    const map = { '4k': 2160, '2k': 1440, 'fhd': 1080, 'fullhd': 1080, 'hd': 720, 'sd': 480, 'ld': 360 };
    defNum = map[defStr.toLowerCase()] || 0;
  }
  const w = parseInt(meta.vwidth, 10) || 0;
  const h = parseInt(meta.vheight, 10) || 0;
  const bitrate = parseInt(meta.bitrate || meta.real_bitrate, 10) || 0;
  return { defNum, pixels: w * h, bitrate };
}

/**
 * 编码格式归类：hevc / h264 / other
 * 平台返回的 codec_type 不统一（h265、hevc、hvc1、h265_hvc1…），统一归一
 */
function codecFamily(codec) {
  const c = String(codec || '').toLowerCase();
  // bytevc2 是字节私有编码（容器标记 bvc2），不是 HEVC，标准播放器与 ffmpeg 都无法解码
  if (c.includes('bytevc2') || c === 'bvc2') return 'bytevc2';
  // bytevc1 实测以 hvc1 封装为 HEVC（1080p 已验证），归入 HEVC 族
  if (c.includes('265') || c.includes('hevc') || c.includes('hvc1') || c.includes('bytevc1')) return 'hevc';
  if (c.includes('264') || c.includes('avc')) return 'h264';
  return 'other';
}

/**
 * 解析单个 video_model JSON
 * 选流优先级：清晰度标签 > 像素 > 编码格式 > 码率（取最高分辨率）
 *
 * @param {string} vm          video_model JSON 字符串
 * @param {string} preference  编码偏好：'hevc' | 'h264' | 'auto'（默认）
 *                             hevc/h264 时先在指定的编码族里挑最高画质；
 *                             该族不存在时回退到全量候选（不丢集数）
 */
function parseModelVideo(vm, preference) {
  if (typeof vm !== 'string') return [null, null, null];
  let vmj;
  try {
    vmj = JSON.parse(vm);
  } catch (e) {
    return [null, null, null];
  }
  const vl = vmj.video_list || [];
  const rank = (codec) => {
    if (['h265', 'h264', 'h265_hvc1', 'hevc', 'hvc1', 'avc1'].includes(codec)) return 0;
    if (codec === 'bytevc1') return 1;
    return 2;
  };
  // 比较两条流：返回 >0 表示 a 更清晰
  const cmp = (a, b) => {
    if (a.defNum !== b.defNum) return a.defNum - b.defNum;
    if (a.pixels !== b.pixels) return a.pixels - b.pixels;
    if (a.codecRank !== b.codecRank) return b.codecRank - a.codecRank; // rank 越小越好
    return a.bitrate - b.bitrate;
  };

  const candidates = [];
  for (const v of vl) {
    if (!v.main_url) continue;
    const codec = v.video_meta?.codec_type || '';
    candidates.push({
      url: v.main_url,
      spadeA: (v.encrypt_info || {}).spade_a || null,
      codec,
      family: codecFamily(codec),
      score: { ...streamScore(v), codecRank: rank(codec) },
    });
  }
  if (candidates.length === 0) return [null, null, null];

  let pool = candidates;
  const pref = preference === 'hevc' || preference === 'h264' ? preference : 'auto';
  if (pref !== 'auto') {
    const matched = candidates.filter((c) => c.family === pref);
    if (matched.length > 0) pool = matched;
    else console.warn(`[Hongguo] 本集没有 ${pref.toUpperCase()} 流，回退到其他编码`);
  }

  let best = pool[0];
  for (const c of pool) {
    if (cmp(c.score, best.score) > 0) best = c;
  }
  return [best.url, best.spadeA, best.codec];
}

/**
 * 获取单集播放直链与 spade_a 加密 key
 *
 * 注意：该接口服务端要求请求签名（X-Gorgon / X-Khronos / X-SS-STUB），
 * 未签名的请求会被静默拒绝（HTTP 200 但响应体为空）。
 */
/**
 * 取播放流。
 * content_type 决定平台走哪条内容池：官方剧 1004、用户投稿(P-UGC) 1001。
 * 传错会返回 101000「服务异常」，所以这里按优先级自动回退，并记住上一次成功的类型。
 */
const PLAY_CONTENT_TYPES = ['1004', '1001', '1003', '1002'];
let lastGoodContentType = '1004';

function buildContentTypeOrder(ct) {
  const first = ct ? String(ct) : lastGoodContentType;
  const rest = PLAY_CONTENT_TYPES.filter((x) => x !== first);
  return [first, ...rest];
}

async function fetchPlayUrlSingle(vid, contentType, codecPreference) {
  const order = buildContentTypeOrder(contentType);
  const wantHevc = codecPreference === 'hevc';
  // device_level 4 优先；万一该等级没有可用地址再退回 3
  const levels = [4, 3];
  let fallback = null;
  for (const ct of order) {
    for (const dl of levels) {
      try {
        const j = await signedPost("/novel/player/multi_video_model/v1/", {
          biz_param: modelBizParam(dl),
          mixed_video_id_map: { [ct]: [vid] },
        });
        if (!j || j.code !== 0) continue;
        const item = (j.data || {})[vid] || {};
        const [playUrl, spadeA, codec] = parseModelVideo(item.video_model, codecPreference);
        if (!playUrl) continue;
        const res = { url: playUrl, spadeA, codec, content_type: ct, device_level: dl };
        if (!wantHevc || codecFamily(codec) === 'hevc') {
          lastGoodContentType = ct;
          return res;
        }
        // 不是 HEVC（例如个别剧集只给 bytevc2）时先记下，继续找 HEVC 流
        if (!fallback) fallback = res;
      } catch (e) {
        // 换下一种类型继续试
      }
    }
  }
  if (fallback) {
    console.warn(`[Hongguo] ${vid} 未取到 HEVC 流，实际编码: ${fallback.codec}`);
    lastGoodContentType = fallback.content_type;
    return fallback;
  }
  console.error("[Hongguo] 取流失败: 所有 content_type 均无有效地址", vid);
  return { url: null, spadeA: null, codec: null, content_type: null };
}

/**
 * FFmpeg 兼容 Base64 解码
 */
function avBase64Decode(s) {
  const chars = 'ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz0123456789+/';
  const table = {};
  for (let i = 0; i < chars.length; i++) table[chars.charCodeAt(i)] = i;
  const dst = [];
  let i = 0;
  while (i + 4 <= s.length) {
    const vals = [];
    let ok = true;
    for (let j = 0; j < 4; j++) {
      const c = s.charCodeAt(i + j);
      if (c in table) {
        vals.push(table[c]);
      } else {
        ok = false;
        break;
      }
    }
    if (!ok) {
      let v = 0;
      for (let j2 = 0; j2 < vals.length; j2++) {
        v = (v << 6) | vals[j2];
      }
      if (vals.length === 3) {
        dst.push((v >> 10) & 0xff);
        dst.push((v >> 2) & 0xff);
      } else if (vals.length === 2) {
        dst.push((v >> 4) & 0xff);
      }
      break;
    }
    const v = (vals[0] << 18) | (vals[1] << 12) | (vals[2] << 6) | vals[3];
    dst.push((v >> 16) & 0xff);
    dst.push((v >> 8) & 0xff);
    dst.push(v & 0xff);
    i += 4;
  }
  return Buffer.from(dst);
}

/**
 * spade_a -> 16 字节 AES Key
 */
function deriveKey(spadeA) {
  if (!spadeA) return null;
  const buf = avBase64Decode(spadeA);
  const L = buf.length;
  if (L < 3) return null;

  const key0 = buf[0] ^ buf[1] ^ buf[2];
  const x27 = key0 - 0x30;
  const w22 = L - key0 + 0x2f;
  if (x27 < 2 || w22 < 2 || w22 > L - 1) return null;

  const x21 = Buffer.from(buf.subarray(1, 1 + w22));
  let w11 = 0x55, w12 = 0xfa, w10 = 0xeb;
  for (let i = 0; i < x21.length; i++) {
    const w13 = x21[i];
    const pc = i.toString(2).split('1').length - 1;
    const w14 = (i & 1) === 0 ? w12 : w11;
    if ((i & 1) === 1) {
      w11 = w13;
    } else {
      w12 = w13;
    }
    const w13_xor = w14 ^ x21[i];
    const w14_sub = w10 - pc;
    x21[i] = (w14_sub + w13_xor) & 0xff;
  }

  const c0 = x21[0];
  let hval = 0;
  if (0x30 <= c0 && c0 <= 0x39) {
    hval = c0 - 0x30;
  } else if (0x61 <= c0 && c0 <= 0x7a) {
    hval = c0 - 0x57;
  } else {
    return null;
  }

  const w9 = w22 - hval;
  if (w9 < 2) return null;

  const strA = x21.subarray(1, 1 + w9 - 1).toString('ascii');
  if (strA.length !== 32) return null;

  try {
    return Buffer.from(strA, 'hex');
  } catch (e) {
    return null;
  }
}

/**
 * AES-128-ECB 加密
 */
function aes128EcbEncrypt(key16, data) {
  const cipher = crypto.createCipheriv('aes-128-ecb', key16, null);
  cipher.setAutoPadding(false);
  return Buffer.concat([cipher.update(data), cipher.final()]);
}

/**
 * 生成 CENC-AES-CTR 的密钥流
 *   - 8 字节 IV：前 8 字节为 nonce，后 8 字节为大端计数器（从 0 开始）
 *   - 16 字节 IV：整个 16 字节作为一个 128 位大端计数器，逐块 +1
 */
function buildKeystream(key, iv, length) {
  if (length <= 0) return Buffer.alloc(0);
  const nblocks = Math.ceil(length / 16);
  const blocks = Buffer.alloc(nblocks * 16);
  if (iv.length >= 16) {
    const mask = (1n << 128n) - 1n;
    let base = 0n;
    for (let i = 0; i < 16; i++) base = (base << 8n) | BigInt(iv[i]);
    for (let k = 0; k < nblocks; k++) {
      const v = (base + BigInt(k)) & mask;
      blocks.writeBigUInt64BE(v >> 64n, k * 16);
      blocks.writeBigUInt64BE(v & 0xffffffffffffffffn, k * 16 + 8);
    }
  } else {
    for (let k = 0; k < nblocks; k++) {
      iv.copy(blocks, k * 16, 0, 8);
      blocks.writeBigUInt64BE(BigInt(k), k * 16 + 8);
    }
  }
  return aes128EcbEncrypt(key, blocks);
}

/**
 * CENC-AES-CTR 解密 sample
 * subs 为 senc 里的 subsample 列表 [[clearBytes, encBytes], ...]；
 * 没有 subsample 信息时按整段加密处理（兼容原有行为）。
 */
function decryptSample(key, nonce, cipher, subs) {
  if (!cipher || cipher.length === 0) return Buffer.alloc(0);

  if (!subs || subs.length === 0) {
    const ks = buildKeystream(key, nonce, cipher.length);
    const plain = Buffer.alloc(cipher.length);
    for (let i = 0; i < cipher.length; i++) {
      plain[i] = cipher[i] ^ ks[i];
    }
    return plain;
  }

  // subsample 模式：clear 段原样保留，只有 enc 段消耗密钥流（同一 sample 内连续）
  let encTotal = 0;
  for (const s of subs) encTotal += s[1] | 0;
  const ks = buildKeystream(key, nonce, encTotal);
  const plain = Buffer.alloc(cipher.length);
  let src = 0;
  let kpos = 0;
  for (const [clear, enc] of subs) {
    const cLen = Math.max(0, Math.min(clear | 0, cipher.length - src));
    if (cLen > 0) {
      cipher.copy(plain, src, src, src + cLen);
      src += cLen;
    }
    const eLen = Math.max(0, Math.min(enc | 0, cipher.length - src));
    for (let i = 0; i < eLen; i++) {
      plain[src + i] = cipher[src + i] ^ ks[kpos + i];
    }
    src += eLen;
    kpos += eLen;
  }
  if (src < cipher.length) cipher.copy(plain, src, src, cipher.length);
  return plain;
}

/**
 * 从 stbl -> stsd -> sample entry -> sinf/schi/tenc 读取加密参数
 * 返回 { ivSize, constantIv }，读不到时回退到 8 字节 per-sample IV
 */
function readTencInfo(data, stbl) {
  const info = { ivSize: 8, constantIv: null };
  const stsd = (stbl.children || []).find((b) => b.typ === 'stsd');
  if (!stsd) return info;

  const stsdEnd = stsd.off + stsd.size;
  const p = stsd.off + 16; // FullBox(4) + entry_count(4)
  if (p + 8 > stsdEnd) return info;
  const esize = data.readUInt32BE(p);
  if (esize < 8 || p + esize > stsdEnd) return info;

  const etyp = data.subarray(p + 4, p + 8).toString('latin1');
  let hdrSize = 78;
  if (etyp === 'enca') {
    hdrSize = 28;
    if (p + 18 <= p + esize) {
      const audioVer = data.readUInt16BE(p + 16);
      if (audioVer === 1) hdrSize = 44;
      else if (audioVer === 2) hdrSize = 64;
    }
  }

  let tencOff = -1;
  forEachChildBox(data, p + 8 + hdrSize, p + esize, (typ, off, size, hdr) => {
    if (typ !== 'sinf') return;
    forEachChildBox(data, off + hdr, off + size, (t2, o2, s2, h2) => {
      if (t2 !== 'schi') return;
      forEachChildBox(data, o2 + h2, o2 + s2, (t3, o3) => {
        if (t3 === 'tenc') tencOff = o3;
      });
    });
  });
  if (tencOff < 0) return info;

  const q = tencOff + 8; // 跳过 size + 'tenc'
  const isProtected = data[q + 6];
  const perSampleIvSize = data[q + 7];
  if (!isProtected) return info;
  if (perSampleIvSize > 0) {
    info.ivSize = perSampleIvSize;
  } else {
    const civSize = data[q + 24];
    info.constantIv = data.subarray(q + 25, q + 25 + civSize);
    info.ivSize = civSize;
  }
  return info;
}

/**
 * 解析 senc：每个 sample 的 IV + 可选的 subsample 表
 */
function parseSencSamples(data, senc, count, ivSize, constantIv) {
  const samples = [];
  const boxEnd = senc.off + senc.size;
  const flags = data.readUIntBE(senc.off + 9, 3);
  const hasSub = (flags & 0x02) !== 0;
  let p = senc.off + 16;

  for (let i = 0; i < count; i++) {
    if (!constantIv && p + ivSize > boxEnd) break;
    let iv;
    if (constantIv) {
      iv = constantIv;
    } else {
      iv = data.subarray(p, p + ivSize);
      p += ivSize;
    }
    let subs = null;
    if (hasSub) {
      if (p + 2 > boxEnd) break;
      const ns = data.readUInt16BE(p);
      p += 2;
      subs = [];
      for (let j = 0; j < ns; j++) {
        if (p + 6 > boxEnd) break;
        subs.push([data.readUInt16BE(p), data.readUInt32BE(p + 2)]);
        p += 6;
      }
    }
    samples.push({ iv, subs });
  }
  return samples;
}

/**
 * MP4 Box 结构解析
 */
function parseBoxes(data, start, end) {
  const boxes = [];
  let off = start;
  while (off + 8 <= end) {
    let size = data.readUInt32BE(off);
    const typ = data.subarray(off + 4, off + 8).toString('latin1');
    let hdr = 8;
    if (size === 1) {
      size = Number(data.readBigUInt64BE(off + 8));
      hdr = 16;
    } else if (size === 0) {
      size = end - off;
    }
    if (size < hdr || off + size > end) break;
    let children = null;
    if (['moov', 'trak', 'mdia', 'minf', 'stbl', 'edts', 'dinf', 'udta', 'meta'].includes(typ)) {
      children = parseBoxes(data, off + hdr, off + size);
    }
    boxes.push({ typ, off, size, hdr, children });
    off += size;
  }
  return boxes;
}

/**
 * 遍历一个 box 范围内的直接子 box
 * 回调参数：(typ, off, size, hdr)
 */
function forEachChildBox(data, start, end, cb) {
  let off = start;
  while (off + 8 <= end) {
    let size = data.readUInt32BE(off);
    const typ = data.subarray(off + 4, off + 8).toString('latin1');
    let hdr = 8;
    if (size === 1) {
      if (off + 16 > end) break;
      size = Number(data.readBigUInt64BE(off + 8));
      hdr = 16;
    } else if (size === 0) {
      size = end - off;
    }
    if (size < hdr || off + size > end) break;
    cb(typ, off, size, hdr);
    off += size;
  }
}

/**
 * 还原加密 sample entry（encv/enca）解密后应该使用的真实编码格式
 *
 * CENC 会把原始 sample entry 的类型藏进 sinf/frma。之前直接写死 hvc1/mp4a，
 * 会把 hev1（参数集在码流里）、avc1、ec-3 等一律标错，播放器按 hvc1 规则
 * 去 hvcC 找参数集找不到，就黑屏/“无法播放”。
 *
 * 优先取 frma 的原始 fourcc；frma 缺失时按 entry 内的配置盒推断；
 * 再不行才回退到 encv→hvc1 / enca→mp4a。
 */
function detectSampleEntryType(data, p, esize, hdrSize, encType) {
  let orig = '';
  let hasHvcC = false;
  let hasAvcC = false;
  let hasDac3 = false;
  let hasDec3 = false;
  let hasDops = false;

  forEachChildBox(data, p + 8 + hdrSize, p + esize, (typ, off, size, hdr) => {
    if (typ === 'hvcC') hasHvcC = true;
    else if (typ === 'avcC') hasAvcC = true;
    else if (typ === 'dac3') hasDac3 = true;
    else if (typ === 'dec3') hasDec3 = true;
    else if (typ === 'dOps') hasDops = true;
    else if (typ === 'sinf') {
      forEachChildBox(data, off + hdr, off + size, (t2, o2, s2, h2) => {
        if (t2 === 'frma' && !orig && h2 + 4 <= s2) {
          orig = data.subarray(o2 + h2, o2 + h2 + 4).toString('latin1');
        }
      });
    }
  });

  // frma 里的 fourcc 必须可打印，且不能是加密类型本身
  if (orig && /^[\x20-\x7e]{4}$/.test(orig) && orig !== 'encv' && orig !== 'enca') {
    return orig;
  }

  if (encType === 'enca') {
    if (hasDec3) return 'ec-3';
    if (hasDac3) return 'ac-3';
    if (hasDops) return 'opus';
    return 'mp4a';
  }
  if (hasHvcC) return 'hvc1';
  if (hasAvcC) return 'avc1';
  return encType === 'encv' ? 'hvc1' : 'mp4a';
}

/**
 * 重建 moov box
 */
function rebuildMoov(data, moovOff, moovSize, top) {
  const moovBox = top.find(b => b.typ === 'moov');
  if (!moovBox) throw new Error('no moov box');

  function clone(box) {
    return {
      typ: box.typ,
      off: box.off,
      size: box.size,
      hdr: box.hdr,
      children: box.children ? box.children.map(clone) : null
    };
  }

  function prune(box) {
    if (!box.children) return box;
    const keep = [];
    for (const c of box.children) {
      if (['senc', 'saio', 'saiz', 'sgpd', 'sbgp'].includes(c.typ)) continue;
      keep.push(prune(c));
    }
    box.children = keep;
    return box;
  }

  const tree = prune(clone(moovBox));

  function* walkChildren(box) {
    for (const c of (box.children || [])) {
      yield c;
      for (const x of walkChildren(c)) yield x;
    }
  }

  const patches = {};
  for (const b of walkChildren(tree)) {
    if (b.typ === 'stsd') {
      const content = b.off + 8;
      const count = data.readUInt32BE(content + 4);
      let p = content + 8;
      const entries = [];
      for (let i = 0; i < count; i++) {
        let esize = data.readUInt32BE(p);
        const etyp = data.subarray(p + 4, p + 8).toString('latin1');
        if (etyp === 'encv' || etyp === 'enca') {
          // 视觉 sample entry 固定头 78 字节；音频固定头 28 字节，
          // QuickTime v1/v2 会在固定头里再追加 16/36 字节
          let hdrSize = 78;
          if (etyp === 'enca') {
            hdrSize = 28;
            if (p + 18 <= p + esize) {
              const audioVer = data.readUInt16BE(p + 16);
              if (audioVer === 1) hdrSize = 44;
              else if (audioVer === 2) hdrSize = 64;
            }
          }
          // 关键：还原真实编码格式，而不是写死 hvc1/mp4a
          const newType = detectSampleEntryType(data, p, esize, hdrSize, etyp);
          const entry = Buffer.alloc(8 + hdrSize);
          entry.writeUInt32BE(0, 0);
          entry.write(newType, 4, 4, 'latin1');
          data.copy(entry, 8, p + 8, p + 8 + hdrSize);

          let entryExtra = Buffer.alloc(0);
          let q = p + 8 + hdrSize;
          while (q + 8 <= p + esize) {
            let s2 = data.readUInt32BE(q);
            const t2 = data.subarray(q + 4, q + 8).toString('latin1');
            if (s2 === 1) {
              s2 = Number(data.readBigUInt64BE(q + 8));
            } else if (s2 === 0) {
              s2 = p + esize - q;
            }
            if (t2 !== 'sinf') {
              entryExtra = Buffer.concat([entryExtra, data.subarray(q, q + s2)]);
            }
            q += s2;
          }
          const fullEntry = Buffer.concat([entry, entryExtra]);
          fullEntry.writeUInt32BE(fullEntry.length, 0);
          entries.push(fullEntry);
        } else {
          entries.push(data.subarray(p, p + esize));
        }
        p += esize;
      }
      const stsdHdr = Buffer.alloc(16);
      data.copy(stsdHdr, 0, b.off, content + 8);
      stsdHdr.writeUInt32BE(entries.length, 12);
      const fullStsd = Buffer.concat([stsdHdr, ...entries]);
      fullStsd.writeUInt32BE(fullStsd.length, 0);
      patches[b.off] = fullStsd;
    }
  }

  function serialize(box) {
    if (!box.children) {
      return patches[box.off] || data.subarray(box.off, box.off + box.size);
    }
    const body = Buffer.concat(box.children.map(serialize));
    const hdrb = Buffer.alloc(box.hdr);
    data.copy(hdrb, 0, box.off, box.off + box.hdr);
    if (box.hdr === 8) {
      hdrb.writeUInt32BE(8 + body.length, 0);
    } else {
      hdrb.writeUInt32BE(1, 0);
      hdrb.writeBigUInt64BE(BigInt(16 + body.length), 8);
    }
    return Buffer.concat([hdrb, body]);
  }

  const first = serialize(tree);
  const delta = moovSize - first.length;

  // stco/co64 是文件绝对偏移：
  //   - moov 在 mdat 之前：moov 变小后，后面所有数据整体前移，偏移要减去 delta
  //   - moov 在 mdat 之后（红果/ffmpeg 默认布局）：mdat 在前面，偏移不能动
  // 以前无条件按「v >= delta」减，多轨文件会整片错位导致花屏。
  if (delta !== 0) {
    const moovEnd = moovOff + moovSize;
    for (const b of walkChildren(tree)) {
      if (b.typ === 'stco') {
        const nc = data.readUInt32BE(b.off + 12);
        for (let i = 0; i < nc; i++) {
          const pos = b.off + 16 + i * 4;
          const v = data.readUInt32BE(pos);
          if (v >= moovEnd) data.writeUInt32BE(v - delta, pos);
        }
      } else if (b.typ === 'co64') {
        const nc = data.readUInt32BE(b.off + 12);
        for (let i = 0; i < nc; i++) {
          const pos = b.off + 16 + i * 8;
          const v = Number(data.readBigUInt64BE(pos));
          if (v >= moovEnd) data.writeBigUInt64BE(BigInt(v - delta), pos);
        }
      }
    }
  }

  return serialize(tree);
}

/**
 * 解密 MP4 文件并保存
 */
function decryptMp4File(srcPath, dstPath, key) {
  const data = fs.readFileSync(srcPath);
  const total = data.length;
  const top = parseBoxes(data, 0, total);
  const moov = top.find(b => b.typ === 'moov');
  if (!moov) throw new Error('no moov box found in MP4');

  function getBox(boxes, typ) {
    return (boxes || []).find(b => b.typ === typ);
  }

  function* walkBoxes(boxes) {
    for (const b of boxes) {
      if (b.children) {
        for (const x of walkBoxes(b.children)) yield x;
      }
      yield b;
    }
  }

  const stbls = Array.from(walkBoxes(top)).filter(b => b.typ === 'stbl');
  const trackInfo = [];
  for (const stbl of stbls) {
    const stsz = getBox(stbl.children, 'stsz');
    const stco = getBox(stbl.children, 'stco');
    const stsc = getBox(stbl.children, 'stsc');
    const senc = getBox(stbl.children, 'senc');
    if (!stsz || !stco || !stsc) continue;
    const ss = data.readUInt32BE(stsz.off + 12);
    const n = data.readUInt32BE(stsz.off + 16);
    let sizes = [];
    if (ss === 0) {
      for (let i = 0; i < n; i++) {
        sizes.push(data.readUInt32BE(stsz.off + 20 + i * 4));
      }
    } else {
      sizes = new Array(n).fill(ss);
    }

    const nc = data.readUInt32BE(stco.off + 12);
    const chunkOffs = [];
    for (let i = 0; i < nc; i++) {
      chunkOffs.push(data.readUInt32BE(stco.off + 16 + i * 4));
    }

    const ns = data.readUInt32BE(stsc.off + 12);
    const stscTab = [];
    for (let i = 0; i < ns; i++) {
      stscTab.push([
        data.readUInt32BE(stsc.off + 16 + i * 12),
        data.readUInt32BE(stsc.off + 20 + i * 12),
        data.readUInt32BE(stsc.off + 24 + i * 12),
      ]);
    }

    const tenc = readTencInfo(data, stbl);
    const samples = senc
      ? parseSencSamples(data, senc, sizes.length, tenc.ivSize, tenc.constantIv)
      : [];

    const chunkSpc = {};
    for (let i = 0; i < stscTab.length; i++) {
      const fc = stscTab[i][0];
      const spc = stscTab[i][1];
      const nxt = (i + 1 < stscTab.length) ? stscTab[i + 1][0] : (nc + 1);
      for (let ci = fc - 1; ci < nxt - 1; ci++) {
        chunkSpc[ci] = spc;
      }
    }

    const sampleOffs = [];
    let si = 0;
    for (let ci = 0; ci < nc; ci++) {
      let off = chunkOffs[ci];
      const spc = chunkSpc[ci] || 1;
      for (let s = 0; s < spc; s++) {
        if (si >= n) break;
        sampleOffs.push(off);
        off += sizes[si];
        si++;
      }
    }

    trackInfo.push({ sizes, offs: sampleOffs, samples });
  }

  for (const ti of trackInfo) {
    for (let i = 0; i < ti.offs.length; i++) {
      const sample = ti.samples[i];
      if (!sample || !sample.iv || sample.iv.length === 0) continue;
      const off = ti.offs[i];
      const sz = ti.sizes[i];
      if (off + sz > total) continue;
      const cipher = data.subarray(off, off + sz);
      const plain = decryptSample(key, sample.iv, cipher, sample.subs);
      plain.copy(data, off);
    }
  }

  const newMoov = rebuildMoov(data, moov.off, moov.size, top);
  const out = [];
  let off = 0;
  while (off < total) {
    let size = data.readUInt32BE(off);
    const typ = data.subarray(off + 4, off + 8).toString('latin1');
    let hdr = 8;
    if (size === 1) {
      size = Number(data.readBigUInt64BE(off + 8));
      hdr = 16;
    } else if (size === 0) {
      size = total - off;
    }
    if (typ === 'moov') {
      out.push(newMoov);
    } else {
      out.push(data.subarray(off, off + size));
    }
    off += size;
  }

  const finalBuf = Buffer.concat(out);
  fs.writeFileSync(dstPath, finalBuf);
}

module.exports = {
  resolveSeriesId,
  fetchEpisodeList,
  fetchPlayUrlSingle,
  parseModelVideo,
  searchSeries,
  searchSuggest,
  signedPost,
  signedGet,
  DETAIL_BIZ_PARAM,
  MODEL_BIZ_PARAM,
  deriveKey,
  decryptMp4File,
  extractInput,
  VIDEO_REFERER,
  UA,
};
