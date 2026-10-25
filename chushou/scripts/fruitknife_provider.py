"""Headless Hongguo provider adapted from the user's fruitknife project.

Protocol source: wu798998264-crypto/fruitknife, f25f1a4e7367d3e9eb0aa1ab80d0cfc11742d13c.
Only source-provided playback material is used. No device registration/rotation,
browser credential collection, payment, or independent DRM licence acquisition.
"""
import ast
import base64
import hashlib
import html
import json
import os
from pathlib import Path
import re
import sys
import time
import urllib.parse

from download_video import DownloadError, extract_url, host_matches, safe_error, valid_url

ROOT = Path(__file__).resolve().parent
UPSTREAM = "f25f1a4e7367d3e9eb0aa1ab80d0cfc11742d13c"
HOSTS = ("novelquickapp.com", "hongguoduanju.com")
QUALITIES = ("360p", "480p", "540p", "720p", "1080p")
DESKTOP_UA = "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36"
MOBILE_UA = "Mozilla/5.0 (Linux; Android 9; 2509FPN0BC) AppleWebKit/537.36"


def is_hongguo_source(value):
    if re.fullmatch(r"\d{10,22}", str(value).strip()):
        return True
    try:
        host = urllib.parse.urlsplit(extract_url(value)).hostname or ""
        return any(host_matches(host.lower(), domain) for domain in HOSTS)
    except ValueError:
        return False


def series_id_from(value):
    """Parse IDs and nested share-redirect data without executing page scripts."""
    pending = [str(value).strip()]
    seen = set()
    for _ in range(20):
        if not pending:
            break
        item = pending.pop(0)
        if item in seen:
            continue
        seen.add(item)
        if re.fullmatch(r"\d{10,22}", item):
            return item  # Keep every digit; the GUI truncated some IDs to 18.
        try:
            data = json.loads(item)
        except (ValueError, TypeError):
            data = None
        if isinstance(data, dict):
            for key in ("series_id", "seriesId", "vid", "content_id"):
                if key in data:
                    pending.insert(0, str(data[key]))
            continue
        parsed = urllib.parse.urlsplit(item)
        match = re.search(r"/(?:player|series)/(\d{10,22})(?:/|$)", parsed.path)
        if match:
            return match[1]
        query = urllib.parse.parse_qs(parsed.query)
        for key in ("series_id", "seriesId", "vid", "content_id", "schemeParams", "report_params", "zlink"):
            pending.extend(query.get(key, []))
        decoded = urllib.parse.unquote(item)
        if decoded != item:
            pending.append(decoded)
    return ""


def _json_after(text, pattern):
    match = re.search(pattern, text)
    if not match:
        return None
    try:
        return json.JSONDecoder().raw_decode(text[match.end():].lstrip())[0]
    except ValueError:
        return None


def parse_series_page(text, series_id):
    """Port the GUI's player/SSR parsing; handle braces in strings and >200 eps."""
    router = _json_after(text, r"window\._ROUTER_DATA\s*=\s*")
    data = {}
    if isinstance(router, dict) and isinstance(router.get("loaderData"), dict):
        for loader in router["loaderData"].values():
            if isinstance(loader, dict):
                page = loader.get("pageData") or {}
                candidate = page.get("series_data") if isinstance(page, dict) else None
                if isinstance(candidate, dict) and candidate.get("chapter_ids"):
                    data = candidate
                    break
    ids = data.get("chapter_ids") or _json_after(text, r'["\']?vid_list["\']?\s*[:=]\s*')
    if not isinstance(ids, list) or not ids:
        raise DownloadError("Hongguo page returned no episode list; access or page format may have changed")
    if any(not re.fullmatch(r"[A-Za-z0-9_-]{1,128}", str(vid)) for vid in ids):
        raise DownloadError("Hongguo returned an invalid episode identifier")
    title = data.get("title") or _json_after(text, r'["\']?series_name["\']?\s*[:=]\s*')
    cover = data.get("series_cover") or _json_after(text, r'["\']?series_cover["\']?\s*[:=]\s*')
    return {"provider": "hongguo", "series_id": series_id,
            "title": str(title or ("剧集 " + series_id)),
            "cover_url": str(cover or ""), "total_episodes": len(ids),
            "episodes": [{"episode": i + 1, "video_id": str(vid)} for i, vid in enumerate(ids)],
            "source_url": "https://hongguoduanju.com/player/" + series_id,
            "upstream_commit": UPSTREAM}


def parse_episodes(value, total, all_episodes=False):
    if all_episodes:
        if value:
            raise DownloadError("Use either an episode selection or all episodes")
        return list(range(1, total + 1))
    if not value:
        raise DownloadError("Specify --episodes (for example 1,3-5) or --all-episodes; an entire series is never implicit")
    selected = set()
    for part in str(value).replace("，", ",").split(","):
        match = re.fullmatch(r"\s*(\d+)(?:\s*-\s*(\d+))?\s*", part)
        if not match:
            raise DownloadError("Invalid episode range; use 1,3-5")
        start, end = int(match[1]), int(match[2] or match[1])
        if start < 1 or end < start or end > total:
            raise DownloadError(f"Episode range must be between 1 and {total}")
        selected.update(range(start, end + 1))
    return sorted(selected)


def load_libraries():
    from fruitknife_runtime import ensure_runtime
    ensure_runtime()
    try:
        import requests
        from Crypto.Cipher import AES
        vendor = ROOT / "fruitknife_vendor"
        if str(vendor) not in sys.path:
            sys.path.insert(0, str(vendor))
        from flurl.core import core_sixgod
        return requests, AES, core_sixgod
    except ImportError as exc:
        raise DownloadError("Fruitknife runtime is incomplete; run python scripts/fruitknife_runtime.py --install") from exc


def b64decode(value):
    value = str(value).strip()
    try:
        return base64.b64decode(value + "=" * (-len(value) % 4), altchars=b"-_", validate=True)
    except ValueError as exc:
        raise DownloadError("Invalid playback material returned by Hongguo") from exc


def playback_key(value):
    """Decode a source-provided playback key; never request/guess a licence."""
    raw = b64decode(value)
    if len(raw) < 34:
        raise DownloadError("Hongguo playback key is incomplete")
    size = len(raw) - (raw[0] ^ raw[1] ^ raw[2]) + 47
    if size < 33 or size > len(raw) - 1:
        raise DownloadError("Hongguo playback key has invalid length")
    data = bytearray(raw[1:1 + size])
    a, b = 85, 246
    for i in range(size):
        if i & 1:
            previous, a = a, data[i]
        else:
            previous, b = b, data[i]
        data[i] = (-21 - i.bit_count() + (previous ^ data[i])) & 255
    try:
        key = bytes.fromhex(bytes(data[1:33]).decode("ascii"))
    except (ValueError, UnicodeError) as exc:
        raise DownloadError("Hongguo returned invalid playback key material") from exc
    if len(key) != 16:
        raise DownloadError("Hongguo returned an unsupported playback key")
    return key


def playback_url(value, key_seed, aes):
    if str(value).startswith(("http://", "https://")):
        return valid_url(str(value))
    raw = b64decode(value)
    if raw.startswith((b"http://", b"https://")):
        return valid_url(raw.decode("utf-8"))
    ciphertext = raw[4:]
    if not key_seed or not ciphertext or len(ciphertext) % 16:
        raise DownloadError("Hongguo returned incomplete playback URL material")
    salt = bytes.fromhex("4dd4c2e6b83162090e52b3c7a6733ba41cb2462b829ab58a196b39db57177524f49baf7f08e8d68d26a72e37c1a95a2f1f05a51892aef2949732b62a38aadd58")
    seed = hashlib.sha512(hashlib.sha512(b64decode(key_seed)).digest() + salt).digest()
    plain = aes.new(seed[:16], aes.MODE_CBC, iv=seed[16:32]).decrypt(ciphertext)
    pad = plain[-1]
    if 1 <= pad <= 16 and plain[-pad:] == bytes([pad]) * pad:
        plain = plain[:-pad]
    try:
        return valid_url(plain.rstrip(b"\0").decode("utf-8"))
    except UnicodeError as exc:
        raise DownloadError("Hongguo returned an invalid playback URL") from exc


def choose_quality(video_list, requested):
    if requested not in QUALITIES or not isinstance(video_list, dict):
        raise DownloadError("Invalid quality or empty Hongguo format list")
    preferred = QUALITIES.index(requested)
    order = list(range(preferred, -1, -1)) + list(range(preferred + 1, len(QUALITIES)))
    for index in order:
        item = video_list.get("video_" + str(index + 1))
        if isinstance(item, dict) and item.get("main_url"):
            return QUALITIES[index], item
    raise DownloadError("No downloadable Hongguo video format was returned")


class FruitknifeClient:
    def __init__(self, timeout=30):
        self.requests, self.aes, self.sign = load_libraries()
        self.session = self.requests.Session()
        self.timeout = timeout

    def request(self, method, url, **kwargs):
        valid_url(url)
        try:
            response = self.session.request(method, url, timeout=self.timeout, **kwargs)
            if response.status_code in (401, 403, 402):
                raise DownloadError(f"Hongguo HTTP {response.status_code}: access/authorization required; no device rotation attempted")
            response.raise_for_status()
            return response
        except self.requests.RequestException as exc:
            raise DownloadError("Hongguo request failed: " + safe_error(exc)) from exc

    def signed_json(self, url, params, body, device):
        headers = {"User-Agent": device["ua"], "Accept": "application/json",
                   "Content-Type": "application/json; charset=UTF-8",
                   "x-xs-from-web": "0", "x-ss-req-ticket": str(int(time.time() * 1000)),
                   "sdk-version": "2", "passport-sdk-version": "50561"}
        signed_headers, signed_url = self.sign(surl=url, params=params, data=body or {},
                                               devices=device, header=headers, log=False)
        kwargs = {"headers": signed_headers}
        if body is not None:
            kwargs["data"] = json.dumps(body, ensure_ascii=False, separators=(",", ":")).encode()
        response = self.request("POST" if body is not None else "GET", signed_url, **kwargs)
        try:
            data = response.json()
        except ValueError as exc:
            raise DownloadError("Hongguo returned non-JSON data") from exc
        if not isinstance(data, dict):
            raise DownloadError("Hongguo returned an unexpected JSON response format")
        if str(data.get("code", 0)) not in ("0", "None"):
            raise DownloadError("Hongguo API rejected request: " + safe_error(str(data.get("message") or data.get("msg") or data.get("code"))))
        return data

    def inspect(self, source):
        sid = series_id_from(source)
        share_html = ""
        if not re.fullmatch(r"\d{10,22}", source.strip()):
            url = extract_url(source)
            if not is_hongguo_source(url):
                raise DownloadError("Not a fruitknife-supported Hongguo share link")
            for _ in range(6):
                response = self.request("GET", url, headers={"User-Agent": MOBILE_UA}, allow_redirects=False)
                sid = sid or series_id_from(url) or series_id_from(response.headers.get("Location", ""))
                if response.is_redirect:
                    target = urllib.parse.urljoin(url, response.headers.get("Location", ""))
                    if urllib.parse.urlsplit(target).scheme not in ("http", "https"):
                        break  # Parse a deep link as data; never launch it.
                    url = valid_url(target)
                    continue
                share_html = response.text
                break
        if not sid:
            raise DownloadError("Could not resolve series_id from this share link")
        if share_html:
            try:
                return parse_series_page(share_html, sid)
            except DownloadError:
                pass
        response = self.request("GET", "https://hongguoduanju.com/player/" + sid,
                                headers={"User-Agent": DESKTOP_UA, "Accept": "text/html"})
        return parse_series_page(response.text, sid)

    def search(self, keyword, cursor=""):
        if not keyword.strip():
            raise DownloadError("Search keyword is empty")
        did = os.environ.get("DUANJU_DEVICE_ID", "4368802900374539")
        iid = os.environ.get("DUANJU_INSTALL_ID", "4368802899821595")
        device = {"device_id": did, "iid": iid, "install_id": iid, "device_brand": "XIAOMI",
                  "device_model": "2509FPN0BC", "device_type": "2509FPN0BC", "device_manufacturer": "XIAOMI",
                  "os_version": "9", "version_name": "7.1.5.32", "version_code": "71532",
                  "ua": "com.phoenix.read/71532", "channel": "oppo_8662_64"}
        params = {"query": keyword.strip(), "tab_type": "11", "tab_name": "feed", "offset": cursor or "0",
                  "count": "0" if cursor else "20", "bookstore_tab": "16", "search_source": "1",
                  "only_feed": "false", "use_correct": "true", "aid": "8662", "device_platform": "android",
                  "version_code": "71532", "version_name": "7.1.5.32", "iid": iid, "device_id": did,
                  "ac": "wifi", "channel": "oppo_8662_64", "app_name": "novelread", "device_type": "2509FPN0BC",
                  "device_brand": "XIAOMI", "os_api": "28", "os_version": "9", "resolution": "1920*1080",
                  "dpi": "280", "host_abi": "arm64-v8a", "dragon_device_type": "pad", "language": "zh",
                  "manifest_version_code": "71532", "update_version_code": "71532", "user_is_login": "0",
                  "is_android_pad_screen": "1", "pv_player": "71532", "compliance_status": "0"}
        if cursor:
            params["passback"] = cursor
        data = self.signed_json("https://api5-normal-sinfonlinec.fqnovel.com/reading/bookapi/search/tab/v", params, None, device)
        results, next_cursor = [], ""
        for tab in data.get("search_tabs") or []:
            next_cursor = str(tab.get("passback") or next_cursor)
            for item in tab.get("data") or []:
                videos = item.get("video_data")
                if isinstance(videos, str):
                    try:
                        videos = ast.literal_eval(videos)
                    except (ValueError, SyntaxError):
                        continue
                if not isinstance(videos, list) or not videos or not isinstance(videos[0], dict):
                    continue
                video = videos[0]
                detail = item.get("video_detail") if isinstance(item.get("video_detail"), dict) else {}
                sid = str(detail.get("series_id") or video.get("series_id") or "")
                if sid:
                    results.append({"series_id": sid, "title": detail.get("series_title") or video.get("title") or "",
                                    "total_episodes": detail.get("episode_cnt") or video.get("episode_cnt") or 0,
                                    "heat": video.get("rec_text") or "", "intro": detail.get("series_intro") or "",
                                    "drama_type": "short" if detail else "manju"})
        def heat_value(item):
            match = re.search(r"([\d.]+)\s*([万亿]?)", item["heat"])
            return float(match[1]) * {"": 1, "万": 10000, "亿": 100000000}[match[2]] if match else 0
        results.sort(key=heat_value, reverse=True)
        return {"status": "search_results", "provider": "hongguo", "keyword": keyword,
                "results": results, "next_cursor": next_cursor, "upstream_commit": UPSTREAM}

    def resolve_episode(self, video_id, quality):
        did, iid = os.environ.get("DUANJU_DEVICE_ID", "11223344556677889900"), os.environ.get("DUANJU_INSTALL_ID", "99887766554433221100")
        device = {"device_id": did, "iid": iid, "install_id": iid, "device_brand": "Redmi", "device_model": "25053RT47C",
                  "device_type": "25053RT47C", "device_manufacturer": "Xiaomi", "os_version": "16",
                  "version_name": "7.1.3.32", "ua": "com.phoenix.read/71332"}
        params = {"iid": iid, "device_id": did, "ac": "wifi", "channel": "update_64", "aid": "8662",
                  "app_name": "novelread", "version_code": "71332", "version_name": "7.1.3.32",
                  "device_platform": "android", "os": "android", "ssmix": "a", "device_type": "25053RT47C",
                  "device_brand": "Redmi", "language": "zh", "os_api": "36", "os_version": "16",
                  "manifest_version_code": "71332", "resolution": "1280*2772", "dpi": "520",
                  "update_version_code": "71332", "host_abi": "arm64-v8a", "dragon_device_type": "phone",
                  "pv_player": "71332", "compliance_status": "0", "need_personal_recommend": "1", "player_so_load": "1", "is_android_pad_screen": "0"}
        body = {"biz_param": {"detail_page_version": 0, "device_level": 3, "disable_digg_stat": False,
                              "need_all_video_definition": True, "need_mp4_align": False, "use_os_player": False,
                              "use_server_dns": False, "video_platform": 1024}, "mixed_video_id_map": {"1004": [video_id]}}
        data = self.signed_json("https://api5-normal-sinfonlineb.fqnovel.com/novel/player/multi_video_model/v1/", params, body, device)
        records = data.get("data")
        record = records.get(video_id) if isinstance(records, dict) else None
        model = record.get("video_model") if isinstance(record, dict) else None
        if isinstance(model, str):
            model = json.loads(model)
        if not isinstance(model, dict) or not model.get("fallback_api"):
            raise DownloadError("Hongguo returned no authorized playback material for this episode")
        response = self.request("GET", valid_url(model["fallback_api"]), headers={"User-Agent": device["ua"]})
        fallback = response.json()
        video_info = fallback.get("video_info") if isinstance(fallback, dict) else None
        info = video_info.get("data") if isinstance(video_info, dict) else None
        if not isinstance(info, dict):
            raise DownloadError("Hongguo returned an unexpected playback response format")
        actual, item = choose_quality(info.get("video_list"), quality)
        if any(item.get(key) for key in ("license_url", "widevine", "playready", "fairplay", "need_purchase")):
            raise DownloadError("This episode requires separate access or DRM authorization; not downloaded")
        return {"url": playback_url(item["main_url"], info.get("key_seed"), self.aes),
                "key": playback_key(item["spade_a"]) if item.get("spade_a") else None,
                "actual_quality": actual, "reported_duration_seconds": item.get("duration") or info.get("duration")}
