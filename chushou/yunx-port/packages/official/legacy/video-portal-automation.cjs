function safeJson(value) {
  return JSON.stringify(value == null ? '' : value);
}

function isHttpUrl(value) {
  try {
    const url = new URL(String(value || ''));
    return url.protocol === 'http:' || url.protocol === 'https:';
  } catch {
    return false;
  }
}

function isMediaUrl(value) {
  if (!isHttpUrl(value)) return false;
  const raw = String(value || '');
  if (/\.(?:mp4|m4s|m4v|mov|webm|mkv|mp3|m4a|wav|flac|aac|ogg)(?:[?#]|$)/i.test(raw)) return true;
  let parsed;
  try { parsed = new URL(raw); } catch { return false; }
  const host = parsed.hostname.toLowerCase().replace(/^www\./, '');
  const mediaCdnHost = [
    'douyinvod.com',
    'douyinpic.com',
    'snssdk.com',
    'zjcdn.com',
    'bytev.com',
    'bilivideo.com',
    'hdslb.com',
    'xhscdn.com',
    'xhscdn.net',
    'kwimgs.com',
    'ksapisrv.com',
    'googlevideo.com',
    'fbcdn.net',
    'cdninstagram.com',
    'akamaized.net',
  ].some((domain) => host === domain || host.endsWith(`.${domain}`));
  const mediaSignal = /(?:^|[?&])(?:mime_type|mime|format|type)=(?:video|audio)(?:_|%2f|\/)/i.test(parsed.search)
    || /\/(?:video|audio)\/(?:tos|play|download|stream)(?:\/|$)/i.test(parsed.pathname)
    || /\/(?:video|audio)\/tos\//i.test(parsed.pathname)
    || /\/aweme\/v1\/play(?:\/|$)/i.test(parsed.pathname);
  return mediaCdnHost && mediaSignal;
}

function normalizedHost(value) {
  try {
    return new URL(String(value || '')).hostname.toLowerCase().replace(/^www\./, '');
  } catch {
    return '';
  }
}

function classifyMediaPortalPopup(value, currentUrl = '') {
  if (!isHttpUrl(value)) return 'block';
  if (isMediaUrl(value)) return 'download';
  const targetHost = normalizedHost(value);
  const currentHost = normalizedHost(currentUrl);
  if (targetHost && currentHost && (targetHost === currentHost || targetHost.endsWith(`.${currentHost}`) || currentHost.endsWith(`.${targetHost}`))) {
    return 'same-site';
  }
  return 'block';
}

function isCurrentMediaPortalRequest(expectedState, activeState, activeRequestId) {
  return !!expectedState
    && expectedState === activeState
    && Number(expectedState.requestId || 0) === Number(activeRequestId || 0);
}

function isBenignMediaPortalNavigationError(error) {
  const code = Number(error?.errno ?? error?.errorCode ?? error?.code);
  const message = String(error?.message || error || '');
  return code === -3 || /(?:^|\b)ERR_ABORTED(?:\b|\s*\(-3\))/i.test(message);
}

function mediaPortalLoadFailureAction({
  error,
  expectedState,
  activeState,
  activeRequestId,
  retryCount = 0,
  maxRetries = 2,
} = {}) {
  if (!isCurrentMediaPortalRequest(expectedState, activeState, activeRequestId)) return 'ignore-stale';
  if (isBenignMediaPortalNavigationError(error)) return 'wait-for-navigation';
  return Number(retryCount || 0) < Math.max(0, Number(maxRetries || 0)) ? 'retry' : 'fail';
}

function shouldRetryMediaPortalVideoAutomation(reason, recoveryCount = 0) {
  const normalizedReason = String(reason || '');
  const limits = {
    'automation-error': 2,
    'input-missing': 2,
    'parse-action-missing': 2,
    'parse-timeout': 1,
    'download-action-missing': 1,
  };
  const limit = Number(limits[normalizedReason] || 0);
  return limit > 0 && Number(recoveryCount || 0) < limit;
}

function shouldExtendMediaPortalVideoResultWait(reason, phase, waitCount = 0) {
  return String(reason || '') === 'parse-timeout'
    && String(phase || '') === 'result'
    && Number(waitCount || 0) < 1;
}

function parseMediaSizeBytes(label) {
  const match = String(label || '').replace(/,/g, '').match(/(\d+(?:\.\d+)?)\s*(KB|MB|GB|TB)\b/i);
  if (!match) return 0;
  const value = Number(match[1]);
  const unit = String(match[2] || '').toUpperCase();
  const multipliers = {
    KB: 1024,
    MB: 1024 ** 2,
    GB: 1024 ** 3,
    TB: 1024 ** 4,
  };
  return Number.isFinite(value) && value > 0 ? Math.round(value * multipliers[unit]) : 0;
}

function selectMediaPreviewOption(options, maxBytes = 0) {
  const limit = Math.max(0, Number(maxBytes || 0));
  const candidates = (Array.isArray(options) ? options : [])
    .map((option, index) => ({
      ...option,
      index,
      sizeBytes: parseMediaSizeBytes(option?.label),
    }));
  if (!limit) return candidates[0] || null;
  const known = candidates
    .filter((option) => option.sizeBytes > 0 && (!limit || option.sizeBytes <= limit))
    .sort((left, right) => left.sizeBytes - right.sizeBytes || right.index - left.index);
  if (known.length) return known[0];
  if (candidates.some((option) => option.sizeBytes > 0)) return null;
  return candidates.at(-1) || null;
}

function buildPortalScript({ mode, value = '', phase = '', timeoutMs = 30000, candidateIndex = 0, nativeSubmit = false } = {}, qualityScorer = () => -1) {
  const normalizedMode = String(mode || '');
  const normalizedPhase = String(phase || '');
  const deadlineMs = Math.max(1000, Math.min(90000, Number(timeoutMs) || 30000));
  const qualityScorerSource = typeof qualityScorer === 'function' ? qualityScorer.toString() : '(() => -1)';
  return `(() => new Promise((resolve) => {
    const mode = ${safeJson(normalizedMode)};
    const phase = ${safeJson(normalizedPhase)};
    const sourceValue = ${safeJson(String(value || ''))};
    const qualityScore = ${qualityScorerSource};
    const deadline = Date.now() + ${deadlineMs};
    const requestedCandidateIndex = ${Math.max(0, Math.floor(Number(candidateIndex) || 0))};
    const nativeSubmit = ${nativeSubmit ? 'true' : 'false'};
    const previewFallbackWaitMs = 2500;
    let previewFirstSeenAt = 0;
    let imageOnlyFirstSeenAt = 0;
    const pause = (fn, delay = 220) => setTimeout(fn, delay);
    const text = (element) => String(element?.innerText || element?.textContent || element?.value || element?.getAttribute?.('aria-label') || '').trim();
    const visible = (element) => {
      if (!element || element.disabled) return false;
      const rect = element.getBoundingClientRect();
      const style = getComputedStyle(element);
      return rect.width > 36 && rect.height > 16 && style.display !== 'none' && style.visibility !== 'hidden' && style.opacity !== '0';
    };
    const absoluteUrl = (value) => {
      const raw = String(value || '').trim();
      if (!raw) return '';
      try { return new URL(raw, location.href).href; } catch { return ''; }
    };
    const httpUrl = (value) => {
      const url = absoluteUrl(value);
      return /^https?:\\/\\//i.test(url) ? url : '';
    };
    const sameDocumentUrl = (value) => {
      try {
        const candidate = new URL(String(value || ''), location.href);
        const current = new URL(location.href);
        return candidate.origin === current.origin
          && candidate.pathname === current.pathname
          && candidate.search === current.search;
      } catch {
        return false;
      }
    };
    const portalToolNavigation = (value) => {
      try {
        const candidate = new URL(String(value || ''), location.href);
        const current = new URL(location.href);
        return candidate.hostname === current.hostname
          && candidate.pathname !== current.pathname
          && (/(?:^|\\.)dlpanda\\.com$/i.test(current.hostname)
            || /(?:downloader|download-[a-z0-9-]+|[a-z0-9-]+-download|\\/(?:vimeo|youtube|tiktok|douyin|facebook|instagram|twitter|xiaohongshu|bilibili|kuaishou)(?:\\/|$))/i.test(candidate.pathname));
      } catch {
        return false;
      }
    };
    const platformFor = (value) => {
      const normalized = String(value || '').toLowerCase();
      if (/(?:douyin|iesdouyin)/.test(normalized)) return 'douyin';
      if (/(?:bilibili|b23\.tv)/.test(normalized)) return 'bilibili';
      if (/(?:xiaohongshu|xhslink|rednote)/.test(normalized)) return 'xiaohongshu';
      if (/(?:kuaishou|gifshow|kwai)/.test(normalized)) return 'kuaishou';
      if (/tiktok/.test(normalized)) return 'tiktok';
      if (/instagram/.test(normalized)) return 'instagram';
      if (/youtube|youtu\.be/.test(normalized)) return 'youtube';
      if (/facebook/.test(normalized)) return 'facebook';
      if (/twitter|x\.com/.test(normalized)) return 'twitter';
      if (/vimeo/.test(normalized)) return 'vimeo';
      return '';
    };
    const sourcePlatform = platformFor(sourceValue);
    const mediaUrl = (value) => {
      const url = httpUrl(value);
      if (!url) return '';
      if (/\\.(?:mp4|m4s|m4v|mov|webm|mkv|mp3|m4a|wav|flac|aac|ogg)(?:[?#]|$)/i.test(url)) return url;
      try {
        const parsed = new URL(url);
        const host = parsed.hostname.toLowerCase().replace(/^www\\./, '');
        const mediaHost = ['douyinvod.com', 'douyinpic.com', 'snssdk.com', 'zjcdn.com', 'bytev.com', 'bilivideo.com', 'hdslb.com', 'xhscdn.com', 'xhscdn.net', 'kwimgs.com', 'ksapisrv.com', 'googlevideo.com', 'fbcdn.net', 'cdninstagram.com', 'akamaized.net']
          .some((domain) => host === domain || host.endsWith('.' + domain));
        const mediaSignal = /(?:^|[?&])(?:mime_type|mime|format|type)=(?:video|audio)(?:_|%2f|\\/)/i.test(parsed.search)
          || /\\/(?:video|audio)\\/(?:tos|play|download|stream)(?:\\/|$)/i.test(parsed.pathname)
          || /\\/aweme\\/v1\\/play(?:\\/|$)/i.test(parsed.pathname);
        return mediaHost && mediaSignal ? url : '';
      } catch {
        return '';
      }
    };
    const videoResultSignal = (value) => /(?:\\d+(?:\\.\\d+)?\\s*(?:gb|mb|kb)|\\b\\d{3,4}\\s*p\\b|原画|超清|高清|original|best|uhd|fhd|\\bhd\\b|(?:video|mp4)\\s*\\(?\\s*\\.?mp4\\s*\\)?|\\.mp4)/i.test(String(value || ''));
    const imageDownloadSignal = (value) => /(?:^|[^a-z0-9])(?:jpe?g|png|webp|gif|bmp|tiff?|avif)(?:[^a-z0-9]|$)|图片|封面|缩略图|image|thumbnail/i.test(String(value || ''));
    const audioDownloadSignal = (value) => /(?:^|[^a-z0-9])(?:mp3|m4a|weba|wav|flac|aac|ogg|opus)(?:[^a-z0-9]|$)|音频|音轨|audio/i.test(String(value || ''));
    const videoFormatSignal = (value) => /(?:^|[^a-z0-9])(?:mp4|m4v|mov|webm|mkv)(?:[^a-z0-9]|$)|视频|video/i.test(String(value || ''));
    const explicitVideoSignal = (value) => /(?:^|[^a-z0-9])(?:mp4|m4v|mov|webm|mkv)(?:[^a-z0-9]|$)|\\b\\d{3,4}\\s*p\\b|原画|超清|高清|original|best|uhd|fhd|\\bhd\\b|视频|video/i.test(String(value || ''));
    const imageUrl = (value) => {
      const url = httpUrl(value);
      if (!url || sameDocumentUrl(url)) return '';
      if (/\\/(?:[^/?#]*logo[^/?#]*|favicon|apple-touch-icon)(?:[./?#]|$)/i.test(url)) return '';
      try {
        if (new URL(url).origin === new URL(location.href).origin) return '';
      } catch {
        return '';
      }
      if (/\\.(?:jpe?g|png|webp|gif|bmp|tiff?|avif)(?:[?#]|$)/i.test(url)) return url;
      try {
        const host = new URL(url).hostname.toLowerCase().replace(/^www\\./, '');
        return ['douyinpic.com', 'byteimg.com', 'douyinstatic.com', 'tiktokcdn.com', 'tiktokcdn-us.com']
          .some((domain) => host === domain || host.endsWith('.' + domain)) ? url : '';
      } catch {
        return '';
      }
    };
    const installPopupGuards = () => {
      try { window.alert = () => undefined; } catch {}
      try { window.confirm = () => false; } catch {}
      try { window.prompt = () => null; } catch {}
    };
    installPopupGuards();
    const humanVerificationRequired = () => {
      const value = String(document.body?.innerText || '').slice(0, 5000);
      return /安全验证|验证您是真人|请验证您是真人|verify you are human|security verification|captcha|cloudflare/i.test(value);
    };
    const providerFailureReason = () => {
      const value = [...document.querySelectorAll('[role="alert"],[role="dialog"],.error,.alert,.toast,.modal,[class*="error-"],[class*="-error"],[class*="modal"],[class*="popup"]')]
        .filter(visible)
        .map(text)
        .join(' ')
        .slice(0, 3000);
      if (/(?:扫码|扫描).{0,18}(?:继续|下载|领取|验证)|(?:二维码|qr\\s*code).{0,18}(?:下载|继续|领取|验证)|scan.{0,18}(?:qr|code)/i.test(value)) return 'qr-code-required';
      if (/观看.{0,12}广告|广告.{0,12}(?:免费|次数|额度)|免费次数.{0,12}(?:用完|不足)|watch.{0,12}ad|free.{0,12}(?:quota|limit)/i.test(value)) return 'quota-or-ad-required';
      if (/受版权保护|私密内容|仅自己可见|禁止下载|private content|copyright protected|download prohibited/i.test(value)) return 'protected-or-private';
      if (/链接无效|视频不存在|内容不存在|视频已删除|invalid link|video unavailable|content unavailable|not found|has been removed/i.test(value)) return 'content-unavailable';
      if (/不支持.*链接|无法解析|解析失败|not supported|failed to parse/i.test(value)) return 'provider-rejected';
      return '';
    };
    const scoreInput = (input) => {
      const hint = [input.placeholder, input.name, input.id, input.getAttribute('aria-label')].filter(Boolean).join(' ').toLowerCase();
      let score = input.type === 'url' ? 12 : input.type === 'search' ? 6 : 2;
      if (/link|url|链接|粘贴|视频|地址|歌曲|歌手|搜索/.test(hint)) score += 20;
      if (/mail|phone|账号|password|密码/.test(hint)) score -= 40;
      return score;
    };
    const setInputValue = (input) => {
      const previousValue = String(input.value || '');
      const prototype = input.tagName === 'TEXTAREA' ? HTMLTextAreaElement.prototype : HTMLInputElement.prototype;
      const setter = Object.getOwnPropertyDescriptor(prototype, 'value')?.set;
      if (setter) setter.call(input, sourceValue); else input.value = sourceValue;
      try {
        const tracker = input._valueTracker;
        if (tracker) tracker.setValue(previousValue);
      } catch {}
      try {
        input.dispatchEvent(new InputEvent('beforeinput', { bubbles: true, cancelable: true, inputType: 'insertFromPaste', data: sourceValue }));
        input.dispatchEvent(new InputEvent('input', { bubbles: true, inputType: 'insertFromPaste', data: sourceValue }));
      } catch {
        input.dispatchEvent(new Event('input', { bubbles: true }));
      }
      input.dispatchEvent(new Event('change', { bubbles: true }));
      input.focus();
    };
    const findAction = (input) => {
      const scope = input?.closest('form') || input?.parentElement?.parentElement || document;
      const matcher = /下载|解析|获取|提取|开始|搜索|download|parse|extract|start|search/i;
      const local = [...scope.querySelectorAll('button,input[type="submit"],[role="button"]')].filter(visible);
      return local.find((element) => matcher.test(text(element)))
        || [...document.querySelectorAll('button,input[type="submit"],[role="button"]')].filter(visible).find((element) => matcher.test(text(element)));
    };
    const videoResultRoot = (element) => {
      const explicitCard = element?.closest('.media-item,article,li,tr,[class*="result-card"],[class*="quality-item"],[class*="resolution-item"],[class*="download-item"],[class*="format-item"],[class*="option-item"]');
      if (explicitCard) return explicitCard;
      let current = element?.parentElement;
      let fallback = element?.parentElement || null;
      for (let depth = 0; current && depth < 12 && current !== document.body; depth += 1, current = current.parentElement) {
        const value = text(current).replace(/\s+/g, ' ').trim();
        const actions = [...current.querySelectorAll('button,a,[role="button"]')]
          .filter(visible)
          .filter((item) => /(?:下载|download|保存|save)/i.test(text(item)));
        const mediaPreview = !!current.querySelector('img,video,source');
        if (videoResultSignal(value) && actions.length > 0 && actions.length <= 4 && value.length <= 2000) return current;
        if (mediaPreview && actions.length > 0 && actions.length <= 4 && value.length <= 1200) fallback = current;
      }
      return element?.closest('article,li,tr,[class*="result"],[class*="quality"],[class*="resolution"],[class*="download-item"],[class*="format"],[class*="option"],[class*="card"]') || fallback;
    };
    const cleanVideoTitle = (value) => String(value || '')
      .normalize('NFKC')
      .replace(/https?:\\/\\/\\S+/gi, ' ')
      .replace(/(?:download|下载|保存)\\s*/gi, ' ')
      .replace(/(?:\\b\\d{3,4}\\s*p\\b|\\b(?:original|best|uhd|fhd|hd)\\b|\\b\\d+(?:\\.\\d+)?\\s*(?:gb|mb|kb)\\b|\\[\\s*\\.?(?:mp4|mov|m4v|webm|mkv)\\s*\\])/gi, ' ')
      .replace(/\\s+/g, ' ')
      .trim()
      .slice(0, 180);
    const videoResultTitle = (root) => {
      if (!root) return '';
      const preferred = [...root.querySelectorAll('[class*="title"],[class*="caption"],[class*="description"],h1,h2,h3,p')]
        .filter(visible)
        .map((element) => cleanVideoTitle(text(element)))
        .find((value) => value.length > 2 && !/^(?:视频|video|mp4|立即|免费|原画|高清|超清)/i.test(value));
      if (preferred) return preferred;
      const clone = root.cloneNode(true);
      clone.querySelectorAll('button,a,svg,video,audio,source,img,[class*="actions"],[class*="quality"],[class*="format"],[class*="resolution"],[class*="size"]').forEach((element) => element.remove());
      return cleanVideoTitle(clone.textContent);
    };
    const videoCandidates = () => [...document.querySelectorAll('button,[role="button"],a')]
      .filter(visible)
      .map((element, index) => {
        const label = text(element);
        const href = httpUrl(element.href || element.getAttribute('href'));
        const resultRoot = videoResultRoot(element);
        const resultText = text(resultRoot);
        const ownQuality = qualityScore(label);
        const actionContext = text(element.parentElement).replace(/\\s+/g, ' ').trim().slice(0, 120);
        const descriptiveLabel = String(ownQuality >= 0 ? label : ([actionContext, resultText, label].filter(Boolean).join(' '))).replace(/\\s+/g, ' ').trim().slice(0, 240);
        const quality = qualityScore(descriptiveLabel);
        const parserAction = element.getAttribute('data-xuannian-parser-action') === 'true';
        const inertSamePageLink = element.tagName === 'A' && !!href && sameDocumentUrl(href);
        const toolNavigationLink = element.tagName === 'A' && !!href && portalToolNavigation(href);
        const candidatePlatform = platformFor(label + ' ' + href);
        const mismatchedPlatformLink = element.tagName === 'A' && !!candidatePlatform && !!sourcePlatform && candidatePlatform !== sourcePlatform;
        const nearbyInput = element.closest('form')?.querySelector('input,textarea')
          || element.parentElement?.querySelector?.('input,textarea');
        const repeatsSourceInput = !!sourceValue && String(nearbyInput?.value || '').trim() === sourceValue.trim();
        const structuredResultContainer = !!element.closest('article,li,tr,[class*="result"],[class*="quality"],[class*="resolution"],[class*="download-item"],[class*="format"],[class*="option"],[class*="card"]')
          || (!!resultRoot && resultRoot !== element.parentElement && resultText.length <= 2000);
        const technicalResultEvidence = !!mediaUrl(href)
          || element.hasAttribute('download')
          || !!resultRoot?.querySelector?.('video,audio,video source,audio source')
          || videoResultSignal(resultText);
        const genericDownloadAction = /^(?:立即|开始|免费)?\s*(?:下载|download)\s*$/i.test(label);
        const parserControl = !mediaUrl(href)
          && !element.hasAttribute('download')
          && /(?:解析|parse|提取|extract)/i.test(label)
          && [...document.querySelectorAll('input,textarea')].some((input) => String(input.value || '').trim() === sourceValue.trim());
        const oversizedPageControl = descriptiveLabel.length > 180
          && !mediaUrl(href)
          && !element.hasAttribute('download')
          && !explicitVideoSignal(label);
        const providerProgressControl = /preparing\s+the\s+media|cancel\s+this\s+request|正在准备|正在解析/i.test(label)
          && !mediaUrl(href);
        const marketingCopy = /多平台视频.*(?:免费|下载)|只需粘贴链接|粘贴.{0,40}(?:即可|就能).{0,80}(?:下载|保存)|极速.*无水印|一键保存精彩\\s*(?:reels?|视频)|使用\\s*seekin\\s*免费(?:下载|保存).{0,100}(?:立即开始|快速便捷)|由\\s*seekin\\s*提供|free.{0,100}(?:without watermark|lossless video quality)|require.{0,40}sessdata/i.test(resultText);
        const hasResultEvidence = technicalResultEvidence
          || (!!resultRoot?.querySelector?.('img') && structuredResultContainer && !marketingCopy);
        const hasDownloadAction = /(?:下载|保存|download|save)/i.test(label);
        const ownDownloadDescriptor = label + ' ' + href;
        const explicitImageDownload = imageDownloadSignal(ownDownloadDescriptor)
          && !explicitVideoSignal(ownDownloadDescriptor);
        const imageOnlyDownload = imageDownloadSignal(descriptiveLabel)
          && !explicitVideoSignal(descriptiveLabel)
          && !mediaUrl(href)
          && !resultRoot?.querySelector?.('video,video source');
        const explicitAudioDownload = audioDownloadSignal(ownDownloadDescriptor)
          && !videoFormatSignal(ownDownloadDescriptor);
        const audioOnlyDownload = explicitAudioDownload || (audioDownloadSignal(descriptiveLabel)
          && !videoFormatSignal(descriptiveLabel)
          && !resultRoot?.querySelector?.('video,video source'));
        const candidateKind = imageOnlyDownload || explicitImageDownload
          ? 'image'
          : (audioOnlyDownload ? 'audio' : 'video');
        const backupVideo = candidateKind === 'video'
          && /(?:备用|备选|备用线路|alternative|backup|mirror|second|线路\s*2)/i.test(descriptiveLabel);
        const disallowed = parserAction
          || inertSamePageLink
          || toolNavigationLink
          || mismatchedPlatformLink
          || repeatsSourceInput
          || parserControl
          || oversizedPageControl
          || providerProgressControl
          || marketingCopy
          || (genericDownloadAction && !technicalResultEvidence && !structuredResultContainer)
          || /(?:为什么|无法下载|下载失败|帮助|教程|常见问题|faq|how\s+to|support)/i.test(label)
          || /[?？]\s*$/.test(label);
        let score = quality >= 0 && (hasResultEvidence || hasDownloadAction) ? 100000 + quality : -1;
        if (disallowed) score = -1;
        if (!disallowed && hasResultEvidence && score < 0 && !/(?:复制|copy|解析|parse|搜索|search|应用|app)/i.test(label) && /(?:下载|download|保存|save)/i.test(label)) score = 20;
        if (!disallowed && score < 0 && (element.hasAttribute('download') || mediaUrl(href))) score = 10;
        return {
          element,
          index,
          label: descriptiveLabel || label,
          href,
          score,
          title: videoResultTitle(resultRoot),
          kind: candidateKind,
          backup: backupVideo,
        };
      })
      .filter((candidate) => candidate.score >= 0)
      .filter((candidate) => sourcePlatform === 'douyin' || candidate.kind === 'video')
      .sort((left, right) => right.score - left.score || left.index - right.index);
    const imageOnlyDownloadPresent = () => [...document.querySelectorAll('button,[role="button"],a')]
      .filter(visible)
      .some((element) => {
        const label = text(element);
        if (!/(?:下载|保存|download|save)/i.test(label)) return false;
        const root = videoResultRoot(element);
        const description = (label + ' ' + text(root)).replace(/\s+/g, ' ').trim();
        return imageDownloadSignal(description)
          && !explicitVideoSignal(description)
          && !!root?.querySelector?.('img')
          && !root?.querySelector?.('video,video source');
      });
    const previewUrl = () => {
      const video = [...document.querySelectorAll('video')].find(visible) || document.querySelector('video');
      const sources = [
        { value: video?.currentSrc, trusted: Number(video?.readyState || 0) >= 1 },
        { value: video?.src, trusted: Number(video?.readyState || 0) >= 1 },
        { value: video?.querySelector?.('source')?.src, trusted: Number(video?.readyState || 0) >= 1 },
        { value: document.querySelector('meta[property="og:video:url"]')?.content, trusted: false },
        { value: document.querySelector('meta[property="og:video"]')?.content, trusted: false },
        { value: document.querySelector('meta[name="twitter:player:stream"]')?.content, trusted: false },
      ];
      for (const candidate of sources) {
        const url = httpUrl(candidate.value);
        if (url && !sameDocumentUrl(url) && (candidate.trusted || !!mediaUrl(url))) return url;
      }
      return videoCandidates().map((candidate) => mediaUrl(candidate.href)).find(Boolean) || '';
    };
    const coverImageUrl = () => {
      const poster = [...document.querySelectorAll('video[poster]')].map((video) => imageUrl(video.poster)).find(Boolean);
      if (poster) return poster;
      return [...document.querySelectorAll('img')]
        .filter(visible)
        .sort((left, right) => (Number(right.naturalWidth || right.width || 0) * Number(right.naturalHeight || right.height || 0)) - (Number(left.naturalWidth || left.width || 0) * Number(left.naturalHeight || left.height || 0)))
        .map((image) => imageUrl(image.currentSrc || image.src || image.getAttribute('src')))
        .find(Boolean) || '';
    };
    const videoTitle = () => {
      const heading = [...document.querySelectorAll('h1,h2,h3')].filter(visible).map(text).find((value) => value.length > 1 && value.length < 160);
      return cleanVideoTitle(heading || document.title);
    };
    const parseMusicResults = () => {
      const unique = new Map();
      for (const link of document.querySelectorAll('a[href*="/music/"]')) {
        if (!visible(link)) continue;
        const url = httpUrl(link.href || link.getAttribute('href'));
        if (!/^https?:\\/\\/(?:www\\.)?gequbao\\.com\\/music\\/\\d+(?:[/?#]|$)/i.test(url)) continue;
        const ownLabel = text(link).replace(/(?:播放\\s*&?\\s*下载|播放|下载)$/i, '').trim();
        const rowLabel = text(link.closest('tr,li,article,.row,.item') || link.parentElement).replace(/(?:播放\\s*&?\\s*下载|播放|下载)/gi, ' ').replace(/\\s+/g, ' ').trim();
        const label = ownLabel.length > 1 ? ownLabel : rowLabel;
        if (!label || /^(?:播放|下载|播放\\s*&?\\s*下载)$/i.test(label)) continue;
        const previous = unique.get(url);
        if (!previous || label.length > previous.label.length) unique.set(url, { url, label: label.slice(0, 180) });
      }
      return [...unique.values()].slice(0, 60).map((item, index) => {
        const parts = item.label.split(/\\s+-\\s+/);
        return { id: String(index + 1), url: item.url, title: (parts.shift() || item.label).trim(), artist: parts.join(' - ').trim(), label: item.label };
      });
    };
    const musicDownloadCandidates = () => [...document.querySelectorAll('button,a,[role="button"]')]
      .filter(visible)
      .map((element, index) => {
        const label = text(element);
        const href = httpUrl(element.href || element.getAttribute('href'));
        const finalAction = /(?:下载)?(?:低品质|低音质|普通音质)\\s*mp3|备用线路/i.test(label);
        const score = finalAction ? 100 : (/下载歌曲|继续下载|download\\s*song|continue\\s*download/i.test(label) ? 20 : 10);
        return { element, index, label, href, finalAction, score };
      })
      .filter((candidate) => /(?:下载歌曲|继续下载|立即下载|普通下载|下载(?:低品质|低音质|普通音质)\\s*mp3|备用线路|download\\s*song|continue\\s*download)/i.test(candidate.label) && !/(?:歌词|lyric|无损|高清|高品质|推荐|网盘|夸克|阿里)/i.test(candidate.label))
      .sort((left, right) => {
        const directDifference = Number(!!mediaUrl(right.href)) - Number(!!mediaUrl(left.href));
        return directDifference || right.score - left.score || left.index - right.index;
      });
    const directAudioUrl = () => {
      const audio = document.querySelector('audio');
      const embedded = [audio?.currentSrc, audio?.src, audio?.querySelector?.('source')?.src]
        .map(httpUrl)
        .find(Boolean);
      if (embedded) return embedded;
      return [...document.querySelectorAll('a[href]')]
        .map((element) => mediaUrl(element.href))
        .find((url) => /\.(?:mp3|m4a|wav|flac|aac|ogg)(?:[?#]|$)/i.test(url)) || '';
    };

    const attemptVideoInput = () => {
      if (humanVerificationRequired()) { resolve({ ok: false, stage: 'input', reason: 'human-verification' }); return; }
      const inputs = [...document.querySelectorAll('input:not([type]),input[type="text"],input[type="url"],input[type="search"],textarea')]
        .filter((input) => !input.disabled && !input.readOnly && visible(input))
        .sort((left, right) => scoreInput(right) - scoreInput(left));
      const input = inputs[0];
      if (!input) {
        if (Date.now() < deadline) pause(attemptVideoInput); else resolve({ ok: false, stage: 'input', reason: 'input-missing' });
        return;
      }
      if (input.getAttribute('data-xuannian-input-ready') !== sourceValue) {
        setInputValue(input);
        input.setAttribute('data-xuannian-input-ready', sourceValue);
        pause(attemptVideoInput, 260);
        return;
      }
      if (String(input.value || '').trim() !== sourceValue.trim()) {
        input.removeAttribute('data-xuannian-input-ready');
        pause(attemptVideoInput, 80);
        return;
      }
      const action = findAction(input);
      if (!action) {
        if (Date.now() < deadline) pause(attemptVideoInput); else resolve({ ok: false, stage: 'input', reason: 'parse-action-missing' });
        return;
      }
      action.setAttribute('data-xuannian-parser-action', 'true');
      if (nativeSubmit) {
        action.scrollIntoView({ block: 'center', inline: 'center' });
        pause(() => {
          const rect = action.getBoundingClientRect();
          resolve({
            ok: true,
            stage: 'input',
            filled: true,
            submitted: false,
            nativeSubmitRequired: true,
            actionPoint: {
              x: Math.max(1, Math.round(rect.left + rect.width / 2)),
              y: Math.max(1, Math.round(rect.top + rect.height / 2)),
            },
            actionLabel: text(action).slice(0, 120),
            continueAutomation: true,
            nextPhase: 'result',
          });
        }, 80);
        return;
      }
      action.click();
      resolve({
        ok: true,
        stage: 'input',
        filled: true,
        submitted: true,
        actionLabel: text(action).slice(0, 120),
        continueAutomation: true,
        nextPhase: 'result',
      });
    };
    const attemptVideoResult = () => {
      if (humanVerificationRequired()) { resolve({ ok: false, stage: 'result', reason: 'human-verification' }); return; }
      const providerFailure = providerFailureReason();
      if (providerFailure) { resolve({ ok: false, stage: 'result', reason: providerFailure }); return; }
      const candidates = videoCandidates();
      const preview = previewUrl();
      if (candidates.length) {
        const videoCandidatesOnly = candidates.filter((candidate) => candidate.kind === 'video');
        const best = videoCandidatesOnly[0] || candidates[0];
        const directVideoCandidate = videoCandidatesOnly.find((candidate) => !!mediaUrl(candidate.href)) || null;
        const audioCandidate = candidates.find((candidate) => candidate.kind === 'audio');
        const imageCandidate = candidates.find((candidate) => candidate.kind === 'image');
        const coverHref = imageCandidate?.href || coverImageUrl();
        const backupCandidate = candidates.find((candidate) => candidate.kind === 'video' && candidate.backup)
          || videoCandidatesOnly.find((candidate) => candidate !== best);
        const candidatePosition = (candidate) => candidate ? candidates.indexOf(candidate) : -1;
        const mediaActions = [
          { id: 'video', label: '视频', href: String(directVideoCandidate?.href || (mediaUrl(preview) || '')), candidateIndex: candidatePosition(directVideoCandidate || best) },
          { id: 'backup', label: '备用下载', href: String(backupCandidate?.href || ''), candidateIndex: candidatePosition(backupCandidate) },
          { id: 'audio', label: '音频', href: String(audioCandidate?.href || ''), candidateIndex: candidatePosition(audioCandidate) },
          { id: 'image', label: '封面图片', href: String(coverHref || ''), candidateIndex: candidatePosition(imageCandidate) },
        ].map((action) => ({ ...action, href: httpUrl(action.href) }));
        const seenActionUrls = new Set();
        const uniqueActions = mediaActions.map((action) => {
          if (!action.href || !seenActionUrls.has(action.href)) {
            if (action.href) seenActionUrls.add(action.href);
            return action;
          }
          return { ...action, href: '', candidateIndex: -1 };
        });
        resolve({
          ok: true,
          stage: 'result',
          previewUrl: preview,
          title: best.title || videoTitle(),
          downloadReady: true,
          downloadActionReady: true,
          qualityLabel: String(best.label || ''),
          qualityHref: String(best.href || ''),
          candidateCount: candidates.length,
          qualityOptions: videoCandidatesOnly.slice(0, 8).map((candidate) => ({ label: String(candidate.label || ''), href: String(candidate.href || '') })),
          mediaActions: uniqueActions,
        });
        return;
      }
      if (preview) {
        if (!previewFirstSeenAt) previewFirstSeenAt = Date.now();
        if (Date.now() - previewFirstSeenAt >= previewFallbackWaitMs || Date.now() >= deadline) {
          resolve({ ok: true, stage: 'result', previewUrl: preview, title: videoTitle(), downloadReady: true, downloadActionReady: false, qualityLabel: '可直接下载当前预览', qualityHref: preview, mediaActions: [
            { id: 'video', label: '视频', href: preview, candidateIndex: -1 },
            { id: 'backup', label: '备用下载', href: '', candidateIndex: -1 },
          ] });
          return;
        }
      }
      if (imageOnlyDownloadPresent()) {
        if (!imageOnlyFirstSeenAt) imageOnlyFirstSeenAt = Date.now();
        if (Date.now() - imageOnlyFirstSeenAt >= 1500 || Date.now() >= deadline) {
          resolve({ ok: false, stage: 'result', reason: 'content-not-video' });
          return;
        }
      } else {
        imageOnlyFirstSeenAt = 0;
      }
      if (Date.now() < deadline) pause(attemptVideoResult, 260); else resolve({ ok: false, stage: 'result', reason: 'parse-timeout' });
    };
    const attemptMusicSearch = () => {
      if (humanVerificationRequired()) { resolve({ ok: false, stage: 'results', reason: 'human-verification', results: [] }); return; }
      const results = parseMusicResults();
      if (results.length) { resolve({ ok: true, stage: 'results', results }); return; }
      if (Date.now() < deadline) pause(attemptMusicSearch, 240); else resolve({ ok: false, stage: 'results', reason: 'search-timeout', results: [] });
    };
    const attemptMusicPreview = () => {
      if (humanVerificationRequired()) { resolve({ ok: false, stage: 'preview', reason: 'human-verification' }); return; }
      const providerFailure = providerFailureReason();
      if (providerFailure) { resolve({ ok: false, stage: 'preview', reason: providerFailure }); return; }
      const direct = directAudioUrl();
      if (direct) { resolve({ ok: true, stage: 'preview', previewUrl: direct }); return; }
      const play = [...document.querySelectorAll('button,a,[role="button"]')]
        .filter(visible)
        .find((element) => /^(?:试听|播放|play)$/i.test(text(element)) && !element.hasAttribute('data-xuannian-preview-clicked'));
      if (play) {
        play.setAttribute('data-xuannian-preview-clicked', 'true');
        play.click();
      }
      if (Date.now() < deadline) pause(attemptMusicPreview, 240); else resolve({ ok: false, stage: 'preview', reason: 'preview-unavailable' });
    };
    const attemptVideoDownload = () => {
      if (humanVerificationRequired()) { resolve({ ok: false, stage: 'download', reason: 'human-verification' }); return; }
      const candidates = videoCandidates();
      const candidate = candidates[requestedCandidateIndex];
      if (candidate) {
        if (candidate.href && !sameDocumentUrl(candidate.href) && !portalToolNavigation(candidate.href)) resolve({ ok: true, stage: 'download', href: candidate.href, label: candidate.label, clicked: false, candidateIndex: requestedCandidateIndex, candidateCount: candidates.length });
        else {
          document.querySelectorAll('[data-xuannian-download-action="true"]').forEach((element) => element.removeAttribute('data-xuannian-download-action'));
          candidate.element.setAttribute('data-xuannian-download-action', 'true');
          candidate.element.scrollIntoView({ block: 'center', inline: 'center' });
          pause(() => {
            const rect = candidate.element.getBoundingClientRect();
            resolve({
              ok: true,
              stage: 'download',
              href: '',
              label: candidate.label,
              clicked: false,
              nativeClickRequired: true,
              actionPoint: {
                x: Math.max(1, Math.round(rect.left + rect.width / 2)),
                y: Math.max(1, Math.round(rect.top + rect.height / 2)),
              },
              candidateIndex: requestedCandidateIndex,
              candidateCount: candidates.length,
            });
          }, 80);
        }
        return;
      }
      if (Date.now() < deadline) pause(attemptVideoDownload, 240); else resolve({ ok: false, stage: 'download', reason: 'download-action-missing' });
    };
    const attemptMusicDownload = () => {
      if (humanVerificationRequired()) { resolve({ ok: false, stage: 'download', reason: 'human-verification' }); return; }
      const direct = directAudioUrl();
      if (direct) { resolve({ ok: true, stage: 'download', href: direct, label: '普通音质', clicked: false }); return; }
      const clicked = new WeakSet();
      let actionStarted = false;
      const waitForDownload = () => {
        if (humanVerificationRequired()) { resolve({ ok: false, stage: 'download', reason: 'human-verification' }); return; }
        const readyUrl = directAudioUrl();
        if (readyUrl) { resolve({ ok: true, stage: 'download', href: readyUrl, label: '普通音质', clicked: actionStarted }); return; }
        const candidate = musicDownloadCandidates().find((item) => !clicked.has(item.element));
        if (candidate) {
          const directHref = mediaUrl(candidate.href);
          if (directHref) { resolve({ ok: true, stage: 'download', href: directHref, label: candidate.label, clicked: actionStarted }); return; }
          clicked.add(candidate.element);
          actionStarted = true;
          candidate.element.click();
          if (candidate.finalAction) {
            resolve({ ok: true, stage: 'download', href: '', label: candidate.label, clicked: true, pending: true });
            return;
          }
        }
        if (Date.now() < deadline) pause(waitForDownload, 240);
        else if (actionStarted) resolve({ ok: true, stage: 'download', href: '', label: '普通音质', clicked: true, pending: true });
        else resolve({ ok: false, stage: 'download', reason: 'download-action-missing' });
      };
      waitForDownload();
    };

    if (mode === 'video-parse') {
      if (phase === 'result') attemptVideoResult(); else attemptVideoInput();
    } else if (mode === 'video-download') attemptVideoDownload();
    else if (mode === 'music-search') attemptMusicSearch();
    else if (mode === 'music-preview') attemptMusicPreview();
    else if (mode === 'music-download') attemptMusicDownload();
    else resolve({ ok: false, reason: 'unsupported-mode' });
  }))()`;
}

module.exports = {
  buildPortalScript,
  classifyMediaPortalPopup,
  isBenignMediaPortalNavigationError,
  isCurrentMediaPortalRequest,
  isHttpUrl,
  isMediaUrl,
  mediaPortalLoadFailureAction,
  parseMediaSizeBytes,
  selectMediaPreviewOption,
  shouldExtendMediaPortalVideoResultWait,
  shouldRetryMediaPortalVideoAutomation,
};
