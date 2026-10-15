#!/usr/bin/env node
import { createHash, randomUUID } from 'node:crypto';
import { appendFile, mkdir, readFile, rename, writeFile } from 'node:fs/promises';
import { isAbsolute, join, resolve } from 'node:path';
import { fileURLToPath } from 'node:url';
import { QUANBEN_SOURCE, searchQuanbenBooks, fetchQuanbenDirectory, previewQuanbenChapter, parseQuanbenChapterHtml } from './novel-source/quanben-book-source.mjs';
import { fetchPinnedPublicUrl } from './novel-source/pinned-public-fetch.mjs';

const hash = (text) => createHash('sha256').update(text, 'utf8').digest('hex');
const cleanName = (text) => String(text || '未命名小说').replace(/[<>:"/\\|?*\x00-\x1f]/g, '-').replace(/[. ]+$/g, '').slice(0, 60) || '小说';
const sameTitle = (text) => String(text || '').normalize('NFKC').trim().replace(/\s+/g, '').toLowerCase();
const saveJson = async (path, value) => {
  const temporary = `${path}.${randomUUID()}.tmp`;
  await writeFile(temporary, `${JSON.stringify(value, null, 2)}\n`, { encoding: 'utf8', flag: 'wx' });
  await rename(temporary, path);
};
const bookRoot = (value) => {
  const url = new URL(String(value));
  if (url.protocol !== 'https:' || url.hostname.replace(/^www\./, '').toLowerCase() !== 'quanben-xiaoshuo.com' || url.username || url.password || url.port) throw new Error('只支持全本小说网 HTTPS 作品链接');
  const match = url.pathname.match(/^\/n\/([^/]+)\/(?:xiaoshuo\.html)?$/i);
  if (!match) throw new Error('请提供作品主页或目录链接');
  return `https://quanben-xiaoshuo.com/n/${match[1]}/`;
};
const belongsToBook = (chapterUrl, root) => {
  try {
    const url = new URL(chapterUrl);
    return url.protocol === 'https:' && !url.username && !url.password && !url.port
      && url.hostname.replace(/^www\./, '').toLowerCase() === 'quanben-xiaoshuo.com'
      && url.pathname.startsWith(new URL(root).pathname) && /\/[^/]+\.html$/i.test(url.pathname) && !/\/xiaoshuo\.html$/i.test(url.pathname);
  } catch { return false; }
};
const chapterTransport = (transport, chapter, root) => ({ ...transport, fetchImpl: async (url, options) => {
  if (!belongsToBook(String(url), root)) throw new Error('章节请求转到了其他作品或目录，拒绝读取');
  const response = await (transport.fetchImpl || fetchPinnedPublicUrl)(url, options);
  if (response.ok) {
    const charset = response.headers.get('content-type')?.match(/charset=([^;\s]+)/i)?.[1]?.replace(/["']/g, '').toLowerCase() || 'utf-8';
    const html = new TextDecoder(['gbk', 'gb2312'].includes(charset) ? 'gb18030' : charset).decode(await response.clone().arrayBuffer());
    if (!/<(?:article|div|section)\b[^>]*(?:id|class)\s*=\s*["'][^"']*(?:articlebody|article-body|chapter-content|read-content|novelcontent|content)[^"']*["'][^>]*>|<main\b/i.test(html)) throw new Error('章节页面缺少明确正文容器，不能以简介或目录替代正文');
    const actual = parseQuanbenChapterHtml(html, { chapterUrl: String(url) });
    const expected = sameTitle(chapter.title);
    const title = sameTitle(actual.title);
    if (title !== expected && !['-', '_', '|', '｜'].some((delimiter) => title.startsWith(`${expected}${delimiter}`))) throw new Error(`章节页面标题与目录不符：${actual.title}`);
  }
  return response;
} });

export const selectChapters = (chapters, selection) => {
  if (!selection || typeof selection !== 'object' || Array.isArray(selection)) throw new Error('必须明确选择全书或章节，不能默认抓取全书');
  const keys = Object.keys(selection);
  const supported = ['all', 'chapterIds', 'titles', 'positions', 'range'];
  if (keys.length !== 1 || !supported.includes(keys[0])) throw new Error('selection 必须仅包含 all、chapterIds、titles、positions、range 中的一种');
  const key = keys[0];
  if (key === 'all') {
    if (selection.all !== true) throw new Error('抓取全书须 selection.all=true');
    return [...chapters];
  }
  let positions;
  if (key === 'range') {
    const { start, end } = selection.range || {};
    if (!Number.isInteger(start) || !Number.isInteger(end) || start < 1 || end < start || end > chapters.length) throw new Error('range 必须是有效的 1-based 目录序号区间');
    positions = Array.from({ length: end - start + 1 }, (_, i) => start + i);
  } else {
    const values = selection[key];
    if (!Array.isArray(values) || !values.length) throw new Error(`${key} 不能为空`);
    positions = values.map((value) => {
      if (key === 'positions') {
        if (!Number.isInteger(value) || value < 1 || value > chapters.length) throw new Error('positions 必须是有效的 1-based 目录序号');
        return value;
      }
      if (typeof value !== 'string' || !value.trim()) throw new Error(`${key} 中的值必须是非空字符串`);
      const matches = chapters.map((chapter, i) => ({ chapter, position: i + 1 })).filter(({ chapter }) => key === 'chapterIds' ? chapter.id === value : chapter.title === value);
      if (matches.length !== 1) {
        const error = new Error(matches.length ? `章节标题存在歧义：${value}，请使用 chapterIds 或目录序号` : `目录中未找到章节：${value}`);
        error.candidates = matches;
        throw error;
      }
      return matches[0].position;
    });
  }
  const wanted = new Set(positions);
  return chapters.filter((_, index) => wanted.has(index + 1));
};

const resolveBook = async (request, transport) => {
  if (request.bookUrl) return { root: bookRoot(request.bookUrl) };
  if (!String(request.query || '').trim()) throw new Error('请提供 query 书名或 bookUrl 作品链接');
  const { items } = await searchQuanbenBooks(request.query, transport);
  const exact = items.filter((item) => sameTitle(item.title) === sameTitle(request.query) && (!request.author || sameTitle(item.author) === sameTitle(request.author)));
  if (exact.length !== 1) return { candidates: exact.length ? exact : items };
  return { root: bookRoot(exact[0].url) };
};

export const captureNovel = async (command, request = {}, { outDir, ...transport } = {}) => {
  if (command === 'sources') return { status: 'success', sources: [QUANBEN_SOURCE] };
  if (!['search', 'directory', 'export'].includes(command)) throw new Error('命令须为 sources、search、directory 或 export');
  if (command === 'search') {
    if (!String(request.query || request.bookUrl || '').trim()) throw new Error('search 需要 query 或 bookUrl');
    return { status: 'success', ...(await searchQuanbenBooks(request.query || bookRoot(request.bookUrl), transport)) };
  }
  if (command === 'directory') {
    const book = await resolveBook(request, transport);
    if (!book.root) return { status: 'needs_selection', candidates: book.candidates };
    return { status: 'success', directory: await fetchQuanbenDirectory({ bookUrl: book.root, ...transport }) };
  }
  if (!outDir || !isAbsolute(outDir)) throw new Error('export 必须使用 --out-dir 指定当前项目中的绝对输出路径');
  const target = join(outDir, `novel-${new Date().toISOString().replace(/[-:.]/g, '')}-${randomUUID().slice(0, 8)}`);
  await mkdir(join(target, 'chapters'), { recursive: true });
  const manifestPath = join(target, 'manifest.json');
  const manifest = { schemaVersion: 1, status: 'running', createdAt: new Date().toISOString(), source: QUANBEN_SOURCE,
    request, book: null, selected: [], chapters: [], failures: [], coverage: { available: 0, requested: 0, successful: 0, completeSelectedCoverage: false, completeDirectoryCoverage: false } };
  await saveJson(manifestPath, manifest);
  try {
    const book = await resolveBook(request, transport);
    if (!book.root) {
      manifest.status = 'needs_selection';
      manifest.candidates = book.candidates;
      await saveJson(manifestPath, manifest);
      return { status: manifest.status, manifest: manifestPath, directory: target, candidates: book.candidates };
    }
    const directory = await fetchQuanbenDirectory({ bookUrl: book.root, ...transport });
    manifest.book = { title: directory.title, author: directory.author, url: directory.bookUrl, directoryUrl: directory.directoryUrl };
    manifest.coverage.available = directory.chapters.length;
    const selected = selectChapters(directory.chapters, request.selection);
    if (!selected.length) throw new Error('所选范围没有章节');
    const intervalMs = request.intervalMs === undefined ? 500 : request.intervalMs;
    if (!Number.isInteger(intervalMs) || intervalMs < 0 || intervalMs > 60000) throw new Error('intervalMs 必须为 0 到 60000 的整数');
    manifest.selected = selected.map(({ id, index, title, url }) => ({ id, index, title, url }));
    manifest.coverage.requested = selected.length;
    await saveJson(manifestPath, manifest);
    for (const [offset, chapter] of selected.entries()) {
      try {
        const parsed = await previewQuanbenChapter({ bookUrl: book.root, chapter, ...chapterTransport(transport, chapter, book.root) });
        if (!belongsToBook(parsed.url, book.root)) throw new Error('章节重定向到了其他作品，拒绝保存');
        if (!parsed.text || parsed.characters < 120 || hash(parsed.text) !== parsed.sha256) throw new Error('章节正文或校验值无效');
        const fileName = `${String(chapter.index).padStart(5, '0')}-${chapter.id}.txt`;
        const text = `${parsed.title}\n\n${parsed.text}\n`;
        await writeFile(join(target, 'chapters', fileName), text, { encoding: 'utf8', flag: 'wx' });
        manifest.chapters.push({ ...chapter, title: parsed.title, url: parsed.url, characters: parsed.characters, sha256: parsed.sha256, fileSha256: hash(text), file: join('chapters', fileName) });
      } catch (error) {
        manifest.failures.push({ id: chapter.id, index: chapter.index, title: chapter.title, url: chapter.url, error: String(error.message || error) });
      }
      manifest.coverage.successful = manifest.chapters.length;
      await saveJson(manifestPath, manifest);
      if (intervalMs && offset + 1 < selected.length) await new Promise((done) => setTimeout(done, intervalMs));
    }
    manifest.coverage.completeSelectedCoverage = manifest.chapters.length === selected.length;
    manifest.coverage.completeDirectoryCoverage = manifest.coverage.completeSelectedCoverage && selected.length === directory.chapters.length;
    manifest.status = manifest.coverage.completeSelectedCoverage ? 'success' : manifest.chapters.length ? 'incomplete' : 'failure';
    if (manifest.chapters.length) {
      const header = `${directory.title}\n作者：${directory.author || '来源未标明'}\n来源：${QUANBEN_SOURCE.name}\n作品：${book.root}\n目录：${directory.directoryUrl}\n本次范围：${selected.length} 章；保存正文：${manifest.chapters.length} 章\n覆盖状态：${manifest.status === 'success' ? '所选章节全部读取' : '不完整，缺失章节详见 manifest.json'}\n\n`;
      const fileName = `${cleanName(directory.title)}${manifest.status === 'success' ? '' : '-不完整'}.txt`;
      const documentPath = join(target, fileName);
      const digest = createHash('sha256').update(header, 'utf8');
      await writeFile(documentPath, header, { encoding: 'utf8', flag: 'wx' });
      for (const [index, chapter] of manifest.chapters.entries()) {
        const part = `${index ? '\n\n' : ''}${await readFile(join(target, chapter.file), 'utf8')}`;
        await appendFile(documentPath, part, 'utf8');
        digest.update(part, 'utf8');
      }
      await appendFile(documentPath, '\n', 'utf8');
      manifest.document = { file: fileName, sha256: digest.update('\n', 'utf8').digest('hex'), encoding: 'UTF-8' };
    }
  } catch (error) {
    manifest.status = manifest.chapters.length ? 'incomplete' : 'failure';
    manifest.coverage.completeSelectedCoverage = false;
    manifest.coverage.completeDirectoryCoverage = false;
    manifest.error = String(error.message || error);
    if (error.candidates) manifest.candidates = error.candidates;
  }
  manifest.completedAt = new Date().toISOString();
  await saveJson(manifestPath, manifest);
  return { status: manifest.status, directory: target, manifest: manifestPath, document: manifest.document ? join(target, manifest.document.file) : null, coverage: manifest.coverage, ...(manifest.error ? { error: manifest.error } : {}) };
};

const usage = 'node capture_novel.mjs sources | search|directory|export --request-file <UTF8.json> [--out-dir <absolute-project-folder>]';
if (process.argv[1] && resolve(process.argv[1]) === fileURLToPath(import.meta.url)) {
  try {
    const [command, ...args] = process.argv.slice(2);
    if (!command || command === '--help') { console.log(usage); }
    else {
      const options = {};
      for (let i = 0; i < args.length; i += 2) {
        if (!['--request-file', '--out-dir'].includes(args[i]) || !args[i + 1] || args[i + 1].startsWith('--')) throw new Error(usage);
        if (options[args[i]]) throw new Error('参数不能重复');
        options[args[i]] = args[i + 1];
      }
      if (command !== 'sources' && !options['--request-file']) throw new Error(usage);
      const request = options['--request-file'] ? JSON.parse((await readFile(options['--request-file'], 'utf8')).replace(/^\uFEFF/, '')) : {};
      if (!request || typeof request !== 'object' || Array.isArray(request)) throw new Error('request-file 必须是 JSON 对象');
      const result = await captureNovel(command, request, { outDir: options['--out-dir'] });
      console.log(JSON.stringify(result, null, 2));
      if (['failure', 'incomplete'].includes(result.status)) process.exitCode = 1;
      if (result.status === 'needs_selection') process.exitCode = 2;
    }
  } catch (error) {
    console.log(JSON.stringify({ status: 'failure', error: String(error.message || error) }, null, 2));
    process.exitCode = 1;
  }
}
