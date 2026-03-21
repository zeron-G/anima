#!/usr/bin/env python3
"""E-Hentai / ExHentai 工具 — 搜索、元数据、下载完整画廊"""

import json
import os
import re
import sys
import time
import urllib.parse
import urllib.request
from pathlib import Path

CONFIG_PATH = Path(__file__).parent.parent / "credentials" / "ehentai.json"
DOWNLOAD_DIR = Path("/mnt/d/data/ehentai")
RATE_LIMIT = 1.5

def load_cookies() -> dict:
    if CONFIG_PATH.exists():
        return json.loads(CONFIG_PATH.read_text())
    return {}

def save_cookies(cookies: dict):
    CONFIG_PATH.parent.mkdir(parents=True, exist_ok=True)
    CONFIG_PATH.write_text(json.dumps(cookies, indent=2))
    os.chmod(str(CONFIG_PATH), 0o600)

def setup_login(ipb_member_id: str, ipb_pass_hash: str, igneous: str = "", sk: str = ""):
    cookies = {"ipb_member_id": ipb_member_id, "ipb_pass_hash": ipb_pass_hash}
    if igneous: cookies["igneous"] = igneous
    if sk: cookies["sk"] = sk
    save_cookies(cookies)
    print(f"✅ 凭证已保存到 {CONFIG_PATH}")
    if test_exhentai(cookies):
        print("✅ ExHentai（里站）访问成功！")
    else:
        print("⚠️ ExHentai 访问失败，可能需要 igneous cookie。表站仍可使用。")

def test_exhentai(cookies: dict = None) -> bool:
    if cookies is None: cookies = load_cookies()
    try:
        resp = _fetch("https://exhentai.org/", cookies=cookies, allow_redirect=False)
        return b"sad panda" not in resp[:500].lower() and len(resp) > 1000
    except Exception:
        return False

def _cookie_header(cookies: dict) -> str:
    return "; ".join(f"{k}={v}" for k, v in cookies.items())

def _fetch(url: str, cookies: dict = None, allow_redirect=True, referer: str = None) -> bytes:
    req = urllib.request.Request(url)
    req.add_header("User-Agent", "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36")
    if cookies: req.add_header("Cookie", _cookie_header(cookies))
    if referer: req.add_header("Referer", referer)
    if not allow_redirect:
        import urllib.request as ur
        class NoRedirect(ur.HTTPRedirectHandler):
            def redirect_request(self, *a, **kw): return None
        resp = ur.build_opener(NoRedirect).open(req, timeout=30)
    else:
        resp = urllib.request.urlopen(req, timeout=30)
    return resp.read()

def _api_request(data: dict) -> dict:
    body = json.dumps(data).encode()
    req = urllib.request.Request("https://api.e-hentai.org/api.php", data=body)
    req.add_header("Content-Type", "application/json")
    req.add_header("User-Agent", "Mozilla/5.0")
    cookies = load_cookies()
    if cookies: req.add_header("Cookie", _cookie_header(cookies))
    return json.loads(urllib.request.urlopen(req, timeout=30).read())

def search(query: str, page: int = 0, exhentai: bool = False, category: int = None) -> list[dict]:
    cookies = load_cookies()
    base = "https://exhentai.org" if exhentai else "https://e-hentai.org"
    params = {"f_search": query, "page": page}
    if category is not None: params["f_cats"] = category
    html = _fetch(f"{base}/?{urllib.parse.urlencode(params)}", cookies=cookies).decode("utf-8", errors="replace")
    results = _parse_search_results(html, base)
    if results:
        gidlist = [[r["gid"], r["token"]] for r in results[:25]]
        try:
            meta = _api_request({"method": "gdata", "gidlist": gidlist, "namespace": 1})
            meta_map = {m["gid"]: m for m in meta.get("gmetadata", []) if "error" not in m}
            for r in results:
                if r["gid"] in meta_map:
                    m = meta_map[r["gid"]]
                    r.update({k: m.get(k, "") for k in ("title_jpn", "category", "rating", "filecount", "torrentcount")})
                    r["tags"] = m.get("tags", [])
                    r["filesize"] = m.get("filesize", 0)
        except Exception as e:
            print(f"⚠️ API 元数据获取失败: {e}", file=sys.stderr)
    return results

def _parse_search_results(html: str, base: str) -> list[dict]:
    pattern = re.compile(rf'{re.escape(base)}/g/(\d+)/([a-f0-9]+)/')
    title_pattern = re.compile(r'class="glink">([^<]+)<')
    links = pattern.findall(html)
    titles = title_pattern.findall(html)
    results, seen = [], set()
    for gid, token in links:
        gid = int(gid)
        if gid in seen: continue
        seen.add(gid)
        title = titles[len(seen)-1] if len(seen)-1 < len(titles) else "Unknown"
        results.append({"gid": gid, "token": token, "title": title, "url": f"{base}/g/{gid}/{token}/"})
    return results

def gallery_info(url: str) -> dict:
    gid, token = _parse_gallery_url(url)
    resp = _api_request({"method": "gdata", "gidlist": [[gid, token]], "namespace": 1})
    return resp["gmetadata"][0] if resp.get("gmetadata") else {"error": "not found"}

def _parse_gallery_url(url: str) -> tuple[int, str]:
    m = re.search(r'/g/(\d+)/([a-f0-9]+)', url)
    if not m: raise ValueError(f"无法解析画廊 URL: {url}")
    return int(m.group(1)), m.group(2)

def download(url: str, output_dir: str = None, exhentai: bool = False) -> str:
    cookies = load_cookies()
    gid, token = _parse_gallery_url(url)
    meta = _api_request({"method": "gdata", "gidlist": [[gid, token]], "namespace": 1})
    if not meta.get("gmetadata") or "error" in meta["gmetadata"][0]:
        raise RuntimeError("画廊不存在或无法访问")
    info = meta["gmetadata"][0]
    title = re.sub(r'[<>:"/\\|?*]', '_', info["title"])[:100]
    filecount = int(info["filecount"])
    base = "https://exhentai.org" if exhentai else "https://e-hentai.org"
    gallery_url = f"{base}/g/{gid}/{token}/"
    dest = Path(output_dir) if output_dir else DOWNLOAD_DIR / f"{gid}_{title}"
    dest.mkdir(parents=True, exist_ok=True)
    (dest / "metadata.json").write_text(json.dumps(info, ensure_ascii=False, indent=2))
    print(f"📥 开始下载: {info['title']}")
    print(f"   共 {filecount} 页 → {dest}")
    page_urls = _get_all_page_urls(gallery_url, filecount, cookies)
    downloaded = 0
    for i, page_url in enumerate(page_urls):
        fname = f"{i+1:04d}"
        if list(dest.glob(f"{fname}.*")):
            downloaded += 1; continue
        try:
            img_url, ext = _get_image_url(page_url, cookies)
            img_data = _fetch(img_url, cookies=cookies, referer=page_url)
            (dest / f"{fname}.{ext}").write_bytes(img_data)
            downloaded += 1
            if downloaded % 10 == 0 or downloaded == filecount:
                print(f"   [{downloaded}/{filecount}] ✓")
            time.sleep(RATE_LIMIT)
        except Exception as e:
            print(f"   ⚠️ 第 {i+1} 页下载失败: {e}")
            time.sleep(RATE_LIMIT * 2)
    print(f"✅ 下载完成: {downloaded}/{filecount} 页 → {dest}")
    return str(dest)

def _get_all_page_urls(gallery_url: str, filecount: int, cookies: dict) -> list[str]:
    page_urls = []
    for p in range((filecount + 39) // 40):
        url = f"{gallery_url}?p={p}" if p > 0 else gallery_url
        html = _fetch(url, cookies=cookies).decode("utf-8", errors="replace")
        pattern = re.compile(r'https?://(?:e-hentai|exhentai)\.org/s/([a-f0-9]+)/(\d+)-(\d+)')
        seen = set()
        for pt, gid, pg in pattern.findall(html):
            key = f"{gid}-{pg}"
            if key not in seen:
                seen.add(key)
                base = "exhentai.org" if "exhentai" in gallery_url else "e-hentai.org"
                page_urls.append(f"https://{base}/s/{pt}/{gid}-{pg}")
        if p < (filecount + 39) // 40 - 1: time.sleep(RATE_LIMIT)
    return page_urls

def _get_image_url(page_url: str, cookies: dict) -> tuple[str, str]:
    html = _fetch(page_url, cookies=cookies).decode("utf-8", errors="replace")
    img_match = re.search(r'<img[^>]+id="img"[^>]+src="([^"]+)"', html)
    if img_match:
        img_url = img_match.group(1)
    else:
        nl_match = re.search(r'href="([^"]+)">Download original', html)
        if nl_match: img_url = nl_match.group(1).replace("&amp;", "&")
        else: raise RuntimeError("找不到图片 URL")
    ext_match = re.search(r'\.(\w{3,4})(?:\?|$)', img_url)
    return img_url, ext_match.group(1) if ext_match else "jpg"

def pack_gallery(gallery_dir: str) -> str:
    import zipfile
    d = Path(gallery_dir)
    zip_path = d.with_suffix(".zip")
    with zipfile.ZipFile(zip_path, "w", zipfile.ZIP_STORED) as zf:
        for f in sorted(d.iterdir()):
            if f.is_file(): zf.write(f, f.name)
    print(f"📦 已打包: {zip_path} ({zip_path.stat().st_size/1024/1024:.1f} MB)")
    return str(zip_path)

def _format_size(n: int) -> str:
    if n > 1024**3: return f"{n/1024**3:.1f} GB"
    if n > 1024**2: return f"{n/1024**2:.1f} MB"
    return f"{n/1024:.0f} KB"

def print_search_results(results: list[dict]):
    for i, r in enumerate(results, 1):
        title = r.get("title_jpn") or r["title"]
        cat, rating, pages = r.get("category","?"), r.get("rating","?"), r.get("filecount","?")
        size = _format_size(r["filesize"]) if r.get("filesize") else "?"
        tags_short = ", ".join(r.get("tags", [])[:5])
        print(f"\n{'='*60}")
        print(f"[{i}] {title}")
        if r.get("title_jpn") and r["title"] != r.get("title_jpn"):
            print(f"    EN: {r['title']}")
        print(f"    📁 {cat} | ⭐ {rating} | 📄 {pages}P | 💾 {size}")
        if tags_short: print(f"    🏷️  {tags_short}")
        print(f"    🔗 {r['url']}")
