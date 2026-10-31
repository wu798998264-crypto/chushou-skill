import assert from 'node:assert/strict';
import { createHash } from 'node:crypto';
import { mkdir, mkdtemp, readFile, readdir, writeFile } from 'node:fs/promises';
import { join } from 'node:path';
import { tmpdir } from 'node:os';
import { execFileSync } from 'node:child_process';
import { fileURLToPath } from 'node:url';
import http from 'node:http';
import https from 'node:https';
import dns from 'node:dns/promises';
import { syncBuiltinESMExports } from 'node:module';

// Fail closed if any test accidentally escapes its injected, synthetic transport.
let realNetworkAttempts = 0;
const rejectNetwork = () => {
  realNetworkAttempts += 1;
  throw new Error('Offline regression attempted real network access');
};
http.request = rejectNetwork;
https.request = rejectNetwork;
dns.lookup = rejectNetwork;
globalThis.fetch = rejectNetwork;
syncBuiltinESMExports();

const scriptUrl = new URL('../capture_novel.mjs', import.meta.url);
const script = fileURLToPath(scriptUrl);
const { captureNovel, selectChapters } = await import(scriptUrl);
const { parseQuanbenDirectoryHtml, previewQuanbenChapter, fetchQuanbenDirectory } = await import(new URL('../novel-source/quanben-book-source.mjs', import.meta.url));
const target = await mkdtemp(join(tmpdir(), 'chushou-novel-capture-test-'));
const root = 'https://quanben-xiaoshuo.com/n/synthetic-demo/';
const titles = ['序章 雨后', '第一章 灯火', '同名章节', '同名章节'];
const paragraph = '这是完全合成的中文测试段落，用于核对正文、标点、段落和保存路径，不来自任何真实小说。';
const body = `<p>${paragraph.repeat(3)}</p><p>${paragraph.repeat(3)}</p>`;
const htmlDirectory = (items = titles) => `<html><h1>合成作品</h1><p>作者：合成作者</p>${items.map((title, i) => `<a href="${i + 1}.html">${title}</a>`).join('')}<a href="/n/other/1.html">跨作品章节</a></html>`;
const parseDirectory = (items = titles) => parseQuanbenDirectoryHtml(htmlDirectory(items), { bookUrl: root });
let injectedRequests = 0;
const transport = (mode = 'ok', items = titles) => ({
  lookupImpl: async () => [{ address: '93.184.216.34', family: 4 }],
  fetchImpl: async (value) => {
    injectedRequests += 1;
    const url = new URL(value);
    if (url.searchParams.get('a') === 'search.json') {
      const content = '<li><a href="/n/book-one/">同名作品</a> 作者：甲作者</li><li><a href="/n/book-two/">同名作品</a> 作者：乙作者</li>';
      return new Response(`search(${JSON.stringify({ content })})`, { headers: { 'content-type': 'application/javascript' } });
    }
    if (url.search) return new Response('<html>合成搜索页</html>', { headers: { 'content-type': 'text/html; charset=utf-8' } });
    if (url.pathname.endsWith('/xiaoshuo.html')) return new Response(htmlDirectory(items), { headers: { 'content-type': 'text/html; charset=utf-8' } });
    const index = Number(url.pathname.match(/(\d+)\.html$/)?.[1]) - 1;
    if (mode === 'missing' && index === 1) return new Response('找不到章节', { status: 404, headers: { 'content-type': 'text/html' } });
    if (mode === 'redirect' && index === 0 && !url.pathname.includes('/other/')) return new Response(null, { status: 302, headers: { location: 'https://quanben-xiaoshuo.com/n/other/1.html' } });
    if (mode === 'fake-body') return new Response(`<h1>${items[index]}</h1><body>${paragraph.repeat(10)}<nav>上一章 下一章 返回目录</nav></body>`, { headers: { 'content-type': 'text/html' } });
    if (mode === 'wrong-title') return new Response(`<h1>合成作品简介</h1><main>${body}</main>`, { headers: { 'content-type': 'text/html' } });
    return new Response(`<html><h1>${items[index]}</h1><div id="content">${body}</div><footer>下一章</footer></html>`, { headers: { 'content-type': 'text/html; charset=utf-8' } });
  },
});
const results = [];
const test = async (name, fn) => {
  try { const evidence = await fn(); results.push({ name, passed: true, ...(evidence || {}) }); }
  catch (error) { results.push({ name, passed: false, error: error.stack }); }
};
let success;
await test('CLI sources uses migrated source without network', async () => {
  // This command only returns static source metadata; no request file is supplied.
  const result = JSON.parse(execFileSync(process.execPath, [script, 'sources'], { encoding: 'utf8' }));
  assert.equal(result.sources.length, 1);
  assert.equal(result.sources[0].id, 'quanben-xiaoshuo');
  assert.equal(injectedRequests, 0);
});
await test('cross-work chapter URL rejected before fetching', async () => {
  assert.equal(parseDirectory().chapters.length, 4);
  const before = injectedRequests;
  await assert.rejects(previewQuanbenChapter({ bookUrl: root, chapter: { url: 'https://quanben-xiaoshuo.com/n/other/1.html', title: titles[0] }, ...transport() }), /不属于当前作品/);
  assert.equal(injectedRequests, before);
});
await test('chapterIds are ordered by directory and UTF8/hash/permanent text are correct', async () => {
  const chapters = parseDirectory().chapters;
  success = await captureNovel('export', { bookUrl: root, selection: { chapterIds: [chapters[1].id, chapters[0].id] }, intervalMs: 0 }, { outDir: target, ...transport() });
  assert.equal(success.status, 'success');
  const manifest = JSON.parse(await readFile(success.manifest, 'utf8'));
  assert.deepEqual(manifest.chapters.map((chapter) => chapter.index), [1, 2]);
  assert.equal(manifest.book.author, '合成作者');
  assert.equal(manifest.coverage.completeDirectoryCoverage, false);
  const text = await readFile(success.document, 'utf8');
  assert(text.includes('作者：合成作者'));
  assert(text.indexOf(titles[0]) < text.indexOf(titles[1]));
  assert(text.includes(`${paragraph.repeat(3)}\n\n${paragraph.repeat(3)}`));
  assert.equal(createHash('sha256').update(text, 'utf8').digest('hex'), manifest.document.sha256);
  for (const chapter of manifest.chapters) {
    const content = await readFile(join(success.directory, chapter.file), 'utf8');
    assert.equal(createHash('sha256').update(content, 'utf8').digest('hex'), chapter.fileSha256);
    const prose = content.slice(content.indexOf('\n\n') + 2).trimEnd();
    assert.equal(createHash('sha256').update(prose, 'utf8').digest('hex'), chapter.sha256);
  }
  return { manifest: success.manifest, document: success.document };
});
await test('same-book re-export never overwrites prior files', async () => {
  const previous = await readFile(success.document, 'utf8');
  const again = await captureNovel('export', { bookUrl: root, selection: { positions: [2, 1] }, intervalMs: 0 }, { outDir: target, ...transport() });
  assert.equal(again.status, 'success');
  assert.notEqual(again.directory, success.directory);
  assert.equal(await readFile(success.document, 'utf8'), previous);
});
await test('duplicate chapter titles and missing selectors do not default to full book', async () => {
  assert.throws(() => selectChapters(parseDirectory().chapters, { titles: ['同名章节'] }), /歧义/);
  const absent = await captureNovel('export', { bookUrl: root }, { outDir: target, ...transport() });
  assert.equal(absent.status, 'failure');
  assert.equal(absent.coverage.successful, 0);
  const ambiguous = await captureNovel('export', { bookUrl: root, selection: { titles: ['同名章节'] } }, { outDir: target, ...transport() });
  assert.equal(ambiguous.status, 'failure');
  const manifest = JSON.parse(await readFile(ambiguous.manifest, 'utf8'));
  assert.equal(manifest.candidates.length, 2);
});
await test('ambiguous book name returns candidates without chapter fetching', async () => {
  const result = await captureNovel('export', { query: '同名作品', selection: { all: true } }, { outDir: target, ...transport() });
  assert.equal(result.status, 'needs_selection');
  assert.equal(result.candidates.length, 2);
  const manifest = JSON.parse(await readFile(result.manifest, 'utf8'));
  assert.equal(manifest.chapters.length, 0);
});
await test('missing chapter leaves incomplete manifest and successful chapter available for supplement', async () => {
  const result = await captureNovel('export', { bookUrl: root, selection: { range: { start: 1, end: 2 } }, intervalMs: 0 }, { outDir: target, ...transport('missing') });
  assert.equal(result.status, 'incomplete');
  assert.equal(result.coverage.successful, 1);
  assert.equal(result.coverage.completeSelectedCoverage, false);
  const manifest = JSON.parse(await readFile(result.manifest, 'utf8'));
  assert.equal(manifest.failures.length, 1);
  assert.equal(manifest.failures[0].index, 2);
  assert((await readFile(join(result.directory, manifest.chapters[0].file), 'utf8')).includes(paragraph));
  assert((await readFile(result.document, 'utf8')).includes('不完整'));
  return { manifest: result.manifest };
});
await test('same-site redirect into another work rejected', async () => {
  const result = await captureNovel('export', { bookUrl: root, selection: { positions: [1] }, intervalMs: 0 }, { outDir: target, ...transport('redirect') });
  assert.equal(result.status, 'failure');
  const manifest = JSON.parse(await readFile(result.manifest, 'utf8'));
  assert.match(manifest.failures[0].error, /其他作品/);
});
await test('long navigation/synopsis or a mismatched page title never substitutes for prose', async () => {
  for (const mode of ['fake-body', 'wrong-title']) {
    const result = await captureNovel('export', { bookUrl: root, selection: { positions: [1] }, intervalMs: 0 }, { outDir: target, ...transport(mode) });
    assert.equal(result.status, 'failure');
    assert.equal(result.coverage.successful, 0);
  }
});
await test('explicit full book supports more than 600 chapters without temporary reference cleanup', async () => {
  const many = Array.from({ length: 602 }, (_, i) => `合成章节 ${i + 1}`);
  const result = await captureNovel('export', { bookUrl: root, selection: { all: true }, intervalMs: 0 }, { outDir: target, ...transport('ok', many) });
  assert.equal(result.status, 'success');
  assert.equal(result.coverage.successful, 602);
  assert.equal(result.coverage.completeDirectoryCoverage, true);
  return { manifest: result.manifest };
});
await test('document write failure cannot retain a complete coverage claim', async () => {
  const writeFailureRoot = join(target, 'write-failure-case');
  const options = transport();
  const originalFetch = options.fetchImpl;
  options.fetchImpl = async (url, fetchOptions) => {
    if (String(url).endsWith('/1.html')) {
      const run = (await readdir(writeFailureRoot)).find((entry) => entry.startsWith('novel-'));
      await mkdir(join(writeFailureRoot, run, '合成作品.txt'));
    }
    return originalFetch(url, fetchOptions);
  };
  const result = await captureNovel('export', { bookUrl: root, selection: { positions: [1] }, intervalMs: 0 }, { outDir: writeFailureRoot, ...options });
  assert.equal(result.status, 'incomplete');
  assert.equal(result.document, null);
  assert.equal(result.coverage.completeSelectedCoverage, false);
  assert.equal(result.coverage.completeDirectoryCoverage, false);
  const manifest = JSON.parse(await readFile(result.manifest, 'utf8'));
  assert.equal(manifest.chapters.length, 1);
  assert(manifest.error);
  return { manifest: result.manifest };
});

await test('mapped private IPv4 and equivalent IPv6 loopback/unspecified never reach transport', async () => {
  const blocked = [
    '::ffff:127.0.0.1', '::ffff:7f00:1', '0:0:0:0:0:ffff:7f00:1',
    '::ffff:10.0.0.1', '::ffff:a00:1', '0000:0000:0000:0000:0000:FFFF:0a00:0001',
    '::ffff:172.16.0.1', '::ffff:192.168.1.1', '::ffff:169.254.169.254',
    '::ffff:100.64.0.1', '::ffff:224.0.0.1', '::ffff:0.0.0.0',
    '::1', '0:0:0:0:0:0:0:1', '0000:0000:0000:0000:0000:0000:0000:0001',
    '::', '0:0:0:0:0:0:0:0', 'fc00::1', 'fd00::1', 'fe80::1',
  ];
  for (const address of blocked) {
    const before = injectedRequests;
    await assert.rejects(fetchQuanbenDirectory({
      bookUrl: root, ...transport(), lookupImpl: async () => [{ address, family: 6 }],
    }), /不允许访问的网络地址/, address);
    assert.equal(injectedRequests, before, address);
  }
  return { checkedAddresses: blocked.length };
});
await test('public mapped IPv4 and native IPv6 preserve validated pinned routing', async () => {
  const allowed = [
    '::ffff:93.184.216.34', '::ffff:5db8:d822', '0:0:0:0:0:ffff:5db8:d822',
    '2606:4700:4700::1111', '93.184.216.34',
  ];
  for (const address of allowed) {
    const options = transport();
    const originalFetch = options.fetchImpl;
    options.lookupImpl = async () => [{ address, family: address.includes(':') ? 6 : 4 }];
    options.fetchImpl = async (url, fetchOptions) => {
      assert.equal(fetchOptions.dispatcher.hostname, 'quanben-xiaoshuo.com');
      assert.equal(fetchOptions.dispatcher.addresses[0].address, address);
      return originalFetch(url, fetchOptions);
    };
    const result = await fetchQuanbenDirectory({ bookUrl: root, ...options });
    assert.equal(result.chapters.length, titles.length, address);
  }
  return { checkedAddresses: allowed.length };
});
await test('mixed DNS results reject the entire target before transport', async () => {
  const before = injectedRequests;
  await assert.rejects(fetchQuanbenDirectory({
    bookUrl: root, ...transport(), lookupImpl: async () => [
      { address: '93.184.216.34', family: 4 }, { address: '::ffff:7f00:1', family: 6 },
    ],
  }), /不允许访问的网络地址/);
  assert.equal(injectedRequests, before);
});
await test('redirects resolve and reject newly private addresses before next request', async () => {
  let resolutions = 0;
  let requests = 0;
  await assert.rejects(fetchQuanbenDirectory({
    bookUrl: root,
    lookupImpl: async () => [{ address: ++resolutions === 1 ? '93.184.216.34' : '::ffff:127.0.0.1', family: resolutions === 1 ? 4 : 6 }],
    fetchImpl: async () => {
      injectedRequests += 1;
      requests += 1;
      return new Response(null, { status: 302, headers: { location: `${root}xiaoshuo.html?redirected=1` } });
    },
  }), /不允许访问的网络地址/);
  assert.equal(resolutions, 2);
  assert.equal(requests, 1);
});

const report = {
  passed: results.every((result) => result.passed) && realNetworkAttempts === 0,
  syntheticOnly: true, realNovelNetworkRequests: 0, realNetworkAttempts, injectedRequests, results,
};
const reportPath = join(target, 'test-results.json');
await writeFile(reportPath, `${JSON.stringify(report, null, 2)}\n`, 'utf8');
console.log(JSON.stringify({ passed: report.passed, syntheticOnly: true, realNovelNetworkRequests: 0, realNetworkAttempts, report: reportPath, checks: results.length, failed: results.filter((result) => !result.passed) }, null, 2));
if (!report.passed) process.exitCode = 1;
