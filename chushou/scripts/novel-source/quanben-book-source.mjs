import { createHash, randomUUID } from "node:crypto";
import { lookup } from "node:dns/promises";
import { isIP } from "node:net";
import { mkdir, readFile, readdir, rename, rm, stat, writeFile } from "node:fs/promises";
import { join } from "node:path";
import { closePinnedPublicDispatcher, createPinnedPublicDispatcher, fetchPinnedPublicUrl } from "./pinned-public-fetch.mjs";

export const QUANBEN_SOURCE = Object.freeze({
  id: "quanben-xiaoshuo",
  name: "全本小说网",
  homepage: "https://quanben-xiaoshuo.com/",
  domains: ["quanben-xiaoshuo.com"],
  kind: "public_book_site",
});

const USER_AGENT = "ShensiCreativeEngine/1.0 (+user-selected-public-book-reference)";
const MAX_PAGE_BYTES = 4 * 1024 * 1024;
const MAX_SELECTED_CHAPTERS = 600;
const MAX_REFERENCE_CHARACTERS = 8_000_000;
const MIN_CHAPTER_CHARACTERS = 120;
const TEMP_REFERENCE_TTL_MS = 7 * 24 * 60 * 60 * 1000;

const decodeEntities = (value = "") => String(value)
  .replace(/&#(\d+);/g, (_, code) => String.fromCodePoint(Number(code)))
  .replace(/&#x([0-9a-f]+);/gi, (_, code) => String.fromCodePoint(parseInt(code, 16)))
  .replace(/&nbsp;|&#160;/gi, " ")
  .replace(/&amp;/gi, "&")
  .replace(/&lt;/gi, "<")
  .replace(/&gt;/gi, ">")
  .replace(/&quot;/gi, '"')
  .replace(/&#39;|&apos;/gi, "'");

const stripHtml = (html = "") => decodeEntities(String(html)
  .replace(/<!--[^]*?-->/g, " ")
  .replace(/<(?:script|style|noscript|svg|canvas|template|iframe)\b[^>]*>[^]*?<\/(?:script|style|noscript|svg|canvas|template|iframe)>/gi, " ")
  .replace(/<(?:br|hr)\b[^>]*>/gi, "\n")
  .replace(/<\/(?:p|div|article|section|main|li|h[1-6]|tr)>/gi, "\n")
  .replace(/<[^>]+>/g, " "))
  .replace(/\r/g, "")
  .replace(/[\t\f\v ]+/g, " ")
  .replace(/ *\n */g, "\n")
  .replace(/\n{3,}/g, "\n\n")
  .trim();

const plainLine = (value = "", limit = 240) => stripHtml(value).replace(/\s+/g, " ").trim().slice(0, limit);
const safeFileName = (value = "", fallback = "章节") => {
  const safe = String(value).replace(/[<>:"/\\|?*\u0000-\u001f]/g, "-").replace(/\s+/g, " ").trim().slice(0, 100);
  return safe && safe !== "." && safe !== ".." ? safe : fallback;
};
const sha256 = (value) => createHash("sha256").update(String(value), "utf8").digest("hex");

const normalizeHost = (value = "") => String(value).toLowerCase().replace(/^www\./, "").replace(/\.$/, "");
const isQuanbenHost = (hostname = "") => normalizeHost(hostname) === "quanben-xiaoshuo.com";

const privateIpv4 = (address) => {
  const parts = String(address).split(".").map(Number);
  if (parts.length !== 4 || parts.some((part) => !Number.isInteger(part) || part < 0 || part > 255)) return true;
  return parts[0] === 0 || parts[0] === 10 || parts[0] === 127
    || (parts[0] === 100 && parts[1] >= 64 && parts[1] <= 127)
    || (parts[0] === 169 && parts[1] === 254)
    || (parts[0] === 172 && parts[1] >= 16 && parts[1] <= 31)
    || (parts[0] === 192 && parts[1] === 168) || parts[0] >= 224;
};
const privateIp = (address = "") => {
  const normalized = String(address).toLowerCase();
  if (isIP(normalized) === 4) return privateIpv4(normalized);
  if (isIP(normalized) !== 6) return true;
  // Canonicalize numeric IPv6 only (no DNS/network access), including expanded
  // and dotted-tail spellings, before checking local or IPv4-mapped addresses.
  let canonical;
  try { canonical = new URL(`http://[${normalized}]/`).hostname.slice(1, -1); }
  catch { return true; }
  const mapped = canonical.match(/^::ffff:([0-9a-f]{1,4}):([0-9a-f]{1,4})$/);
  if (mapped) {
    const high = parseInt(mapped[1], 16);
    const low = parseInt(mapped[2], 16);
    return privateIpv4(`${high >>> 8}.${high & 255}.${low >>> 8}.${low & 255}`);
  }
  return canonical === "::" || canonical === "::1" || canonical.startsWith("fc") || canonical.startsWith("fd") || /^fe[89ab]/.test(canonical);
};

const resolveQuanbenTarget = async (value, { lookupImpl = lookup } = {}) => {
  let url;
  try { url = new URL(String(value)); } catch { throw new Error("全本小说网链接格式无效"); }
  if (url.protocol !== "https:" || !isQuanbenHost(url.hostname) || url.username || url.password || url.port) {
    throw new Error("只允许读取全本小说网的 HTTPS 页面");
  }
  const records = await lookupImpl(url.hostname, { all: true });
  if (!records?.length || records.some((record) => privateIp(record.address))) throw new Error("小说站点解析到了不允许访问的网络地址");
  url.hash = "";
  return {
    url,
    addresses: records.map((record) => ({ address: String(record.address), family: Number(record.family) || isIP(record.address) })),
  };
};

const validateQuanbenUrl = async (value, options = {}) => (await resolveQuanbenTarget(value, options)).url;

const readResponseBody = async (response) => {
  const declared = Number(response.headers.get("content-length")) || 0;
  if (declared > MAX_PAGE_BYTES) throw new Error("小说网页超过单页读取上限");
  if (!response.body) return "";
  const reader = response.body.getReader();
  const chunks = [];
  let total = 0;
  while (true) {
    const { value, done } = await reader.read();
    if (done) break;
    total += value.byteLength;
    if (total > MAX_PAGE_BYTES) {
      await reader.cancel();
      throw new Error("小说网页超过单页读取上限");
    }
    chunks.push(value);
  }
  const bytes = new Uint8Array(total);
  let offset = 0;
  for (const chunk of chunks) { bytes.set(chunk, offset); offset += chunk.byteLength; }
  const charset = response.headers.get("content-type")?.match(/charset=([^;\s]+)/i)?.[1]?.replace(/["']/g, "").toLowerCase() || "utf-8";
  try { return new TextDecoder(["gbk", "gb2312"].includes(charset) ? "gb18030" : charset).decode(bytes); }
  catch { return new TextDecoder("utf-8").decode(bytes); }
};

const fetchQuanbenText = async (value, {
  fetchImpl = fetchPinnedPublicUrl,
  lookupImpl = lookup,
  dispatcherFactory = createPinnedPublicDispatcher,
  acceptedContentType = /text\/(?:html|plain)|application\/xhtml\+xml/i,
  headers = {},
} = {}) => {
  let current = await resolveQuanbenTarget(value, { lookupImpl });
  for (let redirects = 0; redirects <= 4; redirects += 1) {
    const controller = new AbortController();
    const timeout = setTimeout(() => controller.abort(), 20_000);
    const dispatcher = dispatcherFactory({ hostname: current.url.hostname, addresses: current.addresses });
    try {
      const response = await fetchImpl(current.url, {
        redirect: "manual",
        signal: controller.signal,
        dispatcher,
        maxResponseBytes: MAX_PAGE_BYTES,
        headers: { "User-Agent": USER_AGENT, Accept: "text/html,application/xhtml+xml;q=0.9,text/plain;q=0.8", ...headers },
      });
      if ([301, 302, 303, 307, 308].includes(response.status)) {
        const location = response.headers.get("location");
        if (!location || redirects === 4) throw new Error("小说页面重定向异常");
        try { await response.body?.cancel?.(); } catch {}
        current = await resolveQuanbenTarget(new URL(location, current.url), { lookupImpl });
        continue;
      }
      if (!response.ok) throw new Error(`全本小说网页返回 HTTP ${response.status}`);
      if (!acceptedContentType.test(response.headers.get("content-type") || "")) throw new Error("小说页面返回了不支持的内容类型");
      return { url: current.url.href, html: await readResponseBody(response) };
    } finally {
      clearTimeout(timeout);
      await closePinnedPublicDispatcher(dispatcher);
    }
  }
  throw new Error("小说页面重定向次数过多");
};

const fetchQuanbenHtml = (value, options = {}) => fetchQuanbenText(value, options);

const SEARCH_STATIC_CHARACTERS = "PAhw7UT1B0a9kQDKZsjIASmOezxYG4CHo5Jyfg2b8FLpEvRr3WtVnlqMidu6oN";
const quanbenSearchProof = (query, random = Math.random) => [...encodeURI(String(query))].map((character) => {
  const index = SEARCH_STATIC_CHARACTERS.indexOf(character);
  const encoded = index < 0 ? character : SEARCH_STATIC_CHARACTERS[(index + 3) % SEARCH_STATIC_CHARACTERS.length];
  const prefix = SEARCH_STATIC_CHARACTERS[Math.floor(random() * SEARCH_STATIC_CHARACTERS.length) % SEARCH_STATIC_CHARACTERS.length];
  const suffix = SEARCH_STATIC_CHARACTERS[Math.floor(random() * SEARCH_STATIC_CHARACTERS.length) % SEARCH_STATIC_CHARACTERS.length];
  return `${prefix}${encoded}${suffix}`;
}).join("");

const parseQuanbenSearchJsonp = (source, callback = "shensiSearch") => {
  const text = String(source).trim();
  const prefix = `${callback}(`;
  if (!text.startsWith(prefix)) throw new Error("全本小说网搜索结果格式无效");
  const end = text.lastIndexOf(")");
  if (end <= prefix.length) throw new Error("全本小说网搜索结果不完整");
  let payload;
  try { payload = JSON.parse(text.slice(prefix.length, end)); }
  catch { throw new Error("全本小说网搜索结果无法解析"); }
  if (!payload || typeof payload.content !== "string") throw new Error("全本小说网搜索结果缺少作品列表");
  return payload.content;
};

const anchorRecords = (html = "") => [...String(html).matchAll(/<a\b[^>]*href\s*=\s*["']([^"'#]+)["'][^>]*>([^]*?)<\/a>/gi)].map((match) => ({
  href: decodeEntities(match[1]),
  label: plainLine(match[2], 180),
  index: match.index ?? 0,
}));

const workIdentity = (value) => {
  try {
    const url = new URL(value, QUANBEN_SOURCE.homepage);
    if (!isQuanbenHost(url.hostname)) return null;
    const match = url.pathname.match(/^\/n\/([^/]+)\/(?:xiaoshuo\.html)?$/i);
    return match ? { slug: match[1], rootUrl: `https://quanben-xiaoshuo.com/n/${match[1]}/` } : null;
  } catch { return null; }
};

export const parseQuanbenSearchHtml = (html = "", { query = "" } = {}) => {
  const source = String(html);
  const seen = new Set();
  const items = [];
  const appendItem = ({ anchor, context = "", author = "", description = "" }) => {
    const identity = workIdentity(anchor?.href);
    if (!identity || seen.has(identity.rootUrl) || !anchor.label || /^(?:开始阅读|章节目录|全部章节|点击阅读)$/.test(anchor.label)) return;
    const contextText = plainLine(context, 1_000);
    const resolvedAuthor = plainLine(author, 60) || contextText.match(/作者[：:]\s*([^\s|·,，]{1,40})/)?.[1] || "";
    const resolvedDescription = plainLine(description, 320)
      || plainLine(context.replace(/<a\b[^>]*>[^]*?<\/a>/i, " "), 320).replace(/^作者[：:]\s*\S+\s*/, "");
    seen.add(identity.rootUrl);
    items.push({
      id: sha256(identity.rootUrl).slice(0, 24),
      providerId: QUANBEN_SOURCE.id,
      sourceId: QUANBEN_SOURCE.id,
      sourceName: QUANBEN_SOURCE.name,
      title: anchor.label,
      author: resolvedAuthor,
      description: resolvedDescription,
      url: identity.rootUrl,
      readableUrl: `${identity.rootUrl}xiaoshuo.html`,
      availability: "public_chapters",
      metadata: { query: String(query).slice(0, 160), directoryUrl: `${identity.rootUrl}xiaoshuo.html` },
    });
  };

  // The live search endpoint returns one `.book` card per work. Parse each
  // card independently so a synopsis can never bleed into the next result.
  const bookCards = [...source.matchAll(/<div\b[^>]*class\s*=\s*["'][^"']*\bbook\b[^"']*["'][^>]*>([^]*?<div\b[^>]*class\s*=\s*["'][^"']*\bdescription\b[^"']*["'][^>]*>[^]*?<\/div>)[^]*?<\/div>/gi)];
  for (const cardMatch of bookCards) {
    const card = cardMatch[1];
    const anchor = anchorRecords(card).find((candidate) => workIdentity(candidate.href));
    if (!anchor) continue;
    const author = card.match(/<[^>]+\bitemprop\s*=\s*["']author["'][^>]*>([^]*?)<\/[^>]+>/i)?.[1] || "";
    const description = card.match(/<div\b[^>]*class\s*=\s*["'][^"']*\bdescription\b[^"']*["'][^>]*>([^]*?)<\/div>/i)?.[1] || "";
    appendItem({ anchor, context: card, author, description });
  }

  if (!bookCards.length) {
  for (const anchor of anchorRecords(source)) {
    const liStart = source.lastIndexOf("<li", anchor.index);
    const liEnd = source.indexOf("</li>", anchor.index);
    const context = liStart >= 0 && liEnd > anchor.index ? source.slice(liStart, liEnd + 5) : source.slice(Math.max(0, anchor.index - 240), anchor.index + 500);
      appendItem({ anchor, context });
    }
  }
  const normalizedQuery = String(query).toLowerCase().replace(/[^\p{L}\p{N}]+/gu, "");
  const relevant = normalizedQuery
    ? items.filter((item) => `${item.title}${item.author}`.toLowerCase().replace(/[^\p{L}\p{N}]+/gu, "").includes(normalizedQuery))
    : items;
  return relevant.slice(0, 60);
};

const documentTitle = (html = "", fallback = "") => plainLine(
  String(html).match(/<h1\b[^>]*>([^]*?)<\/h1>/i)?.[1]
    || String(html).match(/<title\b[^>]*>([^]*?)<\/title>/i)?.[1]
    || fallback,
  180,
).replace(/\s*[-_|｜]\s*全本小说网.*$/i, "");

export const parseQuanbenDirectoryHtml = (html = "", { bookUrl = "" } = {}) => {
  const identity = workIdentity(bookUrl);
  if (!identity) throw new Error("作品目录不是有效的全本小说网页");
  const source = String(html);
  const title = documentTitle(source, "未命名小说");
  const author = plainLine(source, 8_000).match(/作者[：:]\s*([^\s|·,，]{1,60})/)?.[1] || "";
  const prefix = `/n/${identity.slug}/`;
  const seen = new Set();
  const chapters = [];
  for (const anchor of anchorRecords(source)) {
    let url;
    try { url = new URL(anchor.href, identity.rootUrl); } catch { continue; }
    if (!isQuanbenHost(url.hostname) || !url.pathname.startsWith(prefix) || !/\/[^/]+\.html$/i.test(url.pathname) || /\/xiaoshuo\.html$/i.test(url.pathname)) continue;
    url.protocol = "https:";
    url.hostname = "quanben-xiaoshuo.com";
    url.hash = "";
    if (seen.has(url.href) || !anchor.label) continue;
    seen.add(url.href);
    chapters.push({ id: sha256(url.href).slice(0, 24), index: chapters.length + 1, title: anchor.label, url: url.href });
  }
  return { source: QUANBEN_SOURCE, title, author, bookUrl: identity.rootUrl, directoryUrl: `${identity.rootUrl}xiaoshuo.html`, chapters };
};

const removePageChrome = (html = "") => String(html)
  .replace(/<(?:header|footer|nav|aside)\b[^>]*>[^]*?<\/(?:header|footer|nav|aside)>/gi, " ")
  .replace(/<(?:script|style|noscript|svg|canvas|template|iframe)\b[^>]*>[^]*?<\/(?:script|style|noscript|svg|canvas|template|iframe)>/gi, " ");

const bodyCandidates = (html = "") => {
  const clean = removePageChrome(html);
  const patterns = [
    /<(?:article|div|section)\b[^>]*(?:id|class)\s*=\s*["'][^"']*(?:articlebody|article-body|chapter-content|read-content|novelcontent|content)[^"']*["'][^>]*>([^]*?)<\/(?:article|div|section)>/gi,
    /<main\b[^>]*>([^]*?)<\/main>/gi,
  ];
  const values = [];
  for (const pattern of patterns) for (const match of clean.matchAll(pattern)) values.push(match[1]);
  return values.length ? values : [clean.match(/<body\b[^>]*>([^]*?)<\/body>/i)?.[1] || clean];
};

export const parseQuanbenChapterHtml = (html = "", { chapterUrl = "", fallbackTitle = "" } = {}) => {
  const title = documentTitle(html, fallbackTitle || "未命名章节");
  const candidates = bodyCandidates(html).map((candidate) => stripHtml(candidate)).filter(Boolean).sort((a, b) => b.length - a.length);
  let text = candidates[0] || "";
  const boilerplate = [title, "全本小说网", "上一章", "下一章", "返回目录", "加入书签"];
  text = text.split("\n").map((line) => line.trim()).filter((line) => line && !boilerplate.includes(line)).join("\n\n").trim();
  if (text.length < MIN_CHAPTER_CHARACTERS) throw new Error(`章节“${fallbackTitle || title}”没有读取到足够正文`);
  return { title: fallbackTitle || title, url: String(chapterUrl), text, characters: text.length, sha256: sha256(text) };
};

export const searchQuanbenBooks = async (query, { fetchImpl = fetchPinnedPublicUrl, lookupImpl = lookup, dispatcherFactory = createPinnedPublicDispatcher } = {}) => {
  const normalized = String(query ?? "").trim();
  if (normalized.length < 2) return { items: [], sources: [QUANBEN_SOURCE], providers: [{ id: QUANBEN_SOURCE.id, name: QUANBEN_SOURCE.name, ok: true, count: 0 }] };
  if (/^https?:\/\//i.test(normalized)) {
    const validated = await validateQuanbenUrl(normalized, { lookupImpl });
    const identity = workIdentity(validated);
    if (!identity) throw new Error("链接不是全本小说网的作品主页或目录页");
    const directory = await fetchQuanbenDirectory({ bookUrl: identity.rootUrl, fetchImpl, lookupImpl, dispatcherFactory });
    const item = {
      id: sha256(identity.rootUrl).slice(0, 24), providerId: QUANBEN_SOURCE.id, sourceId: QUANBEN_SOURCE.id, sourceName: QUANBEN_SOURCE.name,
      title: directory.title, author: directory.author, description: "", url: identity.rootUrl, readableUrl: directory.directoryUrl,
      availability: "public_chapters", metadata: { directoryUrl: directory.directoryUrl, chapterCount: directory.chapters.length },
    };
    return { items: [item], sources: [QUANBEN_SOURCE], providers: [{ id: QUANBEN_SOURCE.id, name: QUANBEN_SOURCE.name, ok: true, count: 1 }] };
  }
  const searchUrl = new URL(QUANBEN_SOURCE.homepage);
  searchUrl.searchParams.set("c", "book");
  searchUrl.searchParams.set("a", "search");
  searchUrl.searchParams.set("keyword", normalized);
  await fetchQuanbenHtml(searchUrl, { fetchImpl, lookupImpl, dispatcherFactory });
  const callback = "search";
  const resultUrl = new URL(QUANBEN_SOURCE.homepage);
  resultUrl.searchParams.set("c", "book");
  resultUrl.searchParams.set("a", "search.json");
  resultUrl.searchParams.set("callback", callback);
  resultUrl.searchParams.set("t", String(Date.now()));
  resultUrl.searchParams.set("keywords", normalized);
  resultUrl.searchParams.set("b", quanbenSearchProof(normalized));
  const resultPage = await fetchQuanbenText(resultUrl, {
    fetchImpl,
    lookupImpl,
    dispatcherFactory,
    acceptedContentType: /(?:application|text)\/(?:javascript|x-javascript|json|plain|html)/i,
    headers: { Referer: searchUrl.href, Accept: "*/*" },
  });
  const items = parseQuanbenSearchHtml(parseQuanbenSearchJsonp(resultPage.html, callback), { query: normalized });
  return { items, sources: [QUANBEN_SOURCE], providers: [{ id: QUANBEN_SOURCE.id, name: QUANBEN_SOURCE.name, ok: true, count: items.length }] };
};

export const fetchQuanbenDirectory = async ({ bookUrl, fetchImpl = fetchPinnedPublicUrl, lookupImpl = lookup, dispatcherFactory = createPinnedPublicDispatcher } = {}) => {
  const identity = workIdentity(bookUrl);
  if (!identity) throw new Error("作品链接不是有效的全本小说网页");
  const directoryUrl = `${identity.rootUrl}xiaoshuo.html`;
  const page = await fetchQuanbenHtml(directoryUrl, { fetchImpl, lookupImpl, dispatcherFactory });
  const directory = parseQuanbenDirectoryHtml(page.html, { bookUrl: identity.rootUrl });
  if (!directory.chapters.length) throw new Error("该作品目录中没有找到可选择章节");
  return directory;
};

export const previewQuanbenChapter = async ({
  bookUrl, chapter = {}, fetchImpl = fetchPinnedPublicUrl, lookupImpl = lookup, dispatcherFactory = createPinnedPublicDispatcher,
} = {}) => {
  const identity = workIdentity(bookUrl);
  if (!identity) throw new Error("作品链接不是有效的全本小说网页");
  let chapterUrl;
  try { chapterUrl = new URL(String(chapter.url || "")); } catch { throw new Error("章节链接格式无效"); }
  if (chapterUrl.protocol !== "https:"
    || !isQuanbenHost(chapterUrl.hostname)
    || chapterUrl.username
    || chapterUrl.password
    || chapterUrl.port
    || !chapterUrl.pathname.startsWith(`/n/${identity.slug}/`)
    || !/\/[^/]+\.html$/iu.test(chapterUrl.pathname)
    || /\/xiaoshuo\.html$/iu.test(chapterUrl.pathname)) {
    throw new Error("章节不属于当前作品或不是可读取的公开章节");
  }
  const page = await fetchQuanbenHtml(chapterUrl, { fetchImpl, lookupImpl, dispatcherFactory });
  const parsed = parseQuanbenChapterHtml(page.html, { chapterUrl: page.url, fallbackTitle: chapter.title });
  return {
    id: String(chapter.id || ""),
    index: Number(chapter.index) || 0,
    title: parsed.title,
    url: parsed.url,
    characters: parsed.characters,
    sha256: parsed.sha256,
    text: parsed.text,
  };
};

const atomicJson = async (path, value) => {
  const temporary = `${path}.${process.pid}.${randomUUID()}.tmp`;
  await writeFile(temporary, `${JSON.stringify(value, null, 2)}\n`, { encoding: "utf8", mode: 0o600 });
  await rename(temporary, path);
};

const pruneExpiredTemporaryReferences = async (root, now = Date.now()) => {
  const entries = await readdir(root, { withFileTypes: true }).catch(() => []);
  await Promise.all(entries.filter((entry) => entry.isDirectory()).map(async (entry) => {
    const target = join(root, entry.name);
    const info = await stat(target).catch(() => null);
    if (info && now - info.mtimeMs > TEMP_REFERENCE_TTL_MS) await rm(target, { recursive: true, force: true }).catch(() => {});
  }));
};

export const createQuanbenChapterReference = async ({
  book = {}, chapters = [], selectedChapterIds = [], temporaryRoot, fetchImpl = fetchPinnedPublicUrl, lookupImpl = lookup, dispatcherFactory = createPinnedPublicDispatcher,
} = {}) => {
  if (!temporaryRoot) throw new Error("迁移模块需要显式提供 temporaryRoot");
  const identity = workIdentity(book.url || book.readableUrl);
  if (!identity) throw new Error("作品链接不是有效的全本小说网页");
  const requestedIds = [...new Set((selectedChapterIds ?? []).map(String))];
  if (!requestedIds.length) throw new Error("请至少选择一个需要读取的章节");
  if (requestedIds.length > MAX_SELECTED_CHAPTERS) throw new Error(`单次最多选择 ${MAX_SELECTED_CHAPTERS} 个章节`);
  const allowed = new Map((chapters ?? []).filter((chapter) => {
    try {
      const url = new URL(String(chapter.url));
      return url.protocol === "https:"
        && isQuanbenHost(url.hostname)
        && !url.username
        && !url.password
        && !url.port
        && url.pathname.startsWith(`/n/${identity.slug}/`)
        && /\/[^/]+\.html$/i.test(url.pathname)
        && !/\/xiaoshuo\.html$/i.test(url.pathname);
    } catch { return false; }
  }).map((chapter) => [String(chapter.id), chapter]));
  const selected = requestedIds.map((id) => allowed.get(id));
  if (selected.some((chapter) => !chapter)) throw new Error("章节选择已过期，请重新读取作品目录");

  await mkdir(temporaryRoot, { recursive: true, mode: 0o700 });
  await pruneExpiredTemporaryReferences(temporaryRoot);
  const referenceId = `book-${Date.now()}-${randomUUID()}`;
  const target = join(temporaryRoot, referenceId);
  await mkdir(target, { recursive: true, mode: 0o700 });
  const documents = [];
  const failures = [];
  let totalCharacters = 0;
  try {
    for (const selectedChapter of selected) {
      try {
        const page = await fetchQuanbenHtml(selectedChapter.url, { fetchImpl, lookupImpl, dispatcherFactory });
        const parsed = parseQuanbenChapterHtml(page.html, { chapterUrl: page.url, fallbackTitle: selectedChapter.title });
        totalCharacters += parsed.characters;
        if (totalCharacters > MAX_REFERENCE_CHARACTERS) throw new Error("所选章节正文超过单次引用上限，请分批选择");
        const fileName = `${String(selectedChapter.index || documents.length + 1).padStart(4, "0")}-${safeFileName(parsed.title)}.txt`;
        await writeFile(join(target, fileName), `${parsed.text}\n`, { encoding: "utf8", mode: 0o600 });
        documents.push({ id: selectedChapter.id, index: selectedChapter.index, title: parsed.title, url: parsed.url, characters: parsed.characters, sha256: parsed.sha256, fileName });
      } catch (error) {
        failures.push({ id: selectedChapter.id, title: selectedChapter.title, url: selectedChapter.url, message: String(error.message || error) });
      }
    }
    if (failures.length || documents.length !== selected.length) {
      throw new Error(`没有完整读取所选章节正文：成功 ${documents.length}/${selected.length}${failures[0]?.message ? `；${failures[0].message}` : ""}`);
    }
    const createdAt = Date.now();
    const manifest = {
      schemaVersion: 1, referenceId, source: QUANBEN_SOURCE, book: { title: String(book.title || "未命名小说"), author: String(book.author || ""), url: identity.rootUrl },
      createdAt, expiresAt: createdAt + TEMP_REFERENCE_TTL_MS, chapters: documents,
    };
    await atomicJson(join(target, "manifest.json"), manifest);
    const title = String(book.title || "未命名小说").replace(/[\r\n]+/g, " ").slice(0, 160);
    const header = [
      `# ${title}｜所选章节真实正文引用`, "", `- 来源：${QUANBEN_SOURCE.name}`, `- 作品页：${identity.rootUrl}`,
      `- 已选择并读取：${documents.length} 章`, `- 正文字符：${totalCharacters}`, "- 覆盖结论：所选章节已逐章读取并校验；未选择章节不在本次引用范围。",
      "- 权威状态：外部参考资料，不自动写入作品正史。",
    ].join("\n");
    const chapterTexts = [];
    for (const [index, document] of documents.entries()) {
      const parsedPage = await readFile(join(target, document.fileName), "utf8");
      chapterTexts.push(`\n\n---\n\n## ${index + 1}｜${document.title}\n\n来源：${document.url}\n\n${parsedPage.trim()}`);
    }
    await writeFile(join(target, "reference.txt"), `${header}${chapterTexts.join("")}\n`, { encoding: "utf8", mode: 0o600 });
    return {
      title, source: QUANBEN_SOURCE, temporaryDirectory: target, documents, failures: [],
      coverage: { sourceId: QUANBEN_SOURCE.id, sourceName: QUANBEN_SOURCE.name, rootUrl: identity.rootUrl, availableChapterCount: chapters.length, requestedChapterCount: selected.length, successfulChapterCount: documents.length, characters: totalCharacters, completeSelectedCoverage: true, completeDirectoryCoverage: selected.length === chapters.length, selectedChapterIds: requestedIds },
      text: `${header}${chapterTexts.join("")}`,
    };
  } catch (error) {
    await rm(target, { recursive: true, force: true }).catch(() => {});
    throw error;
  }
};
