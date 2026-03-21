#!/usr/bin/env python3
"""禁漫天堂 (18comic/JMComic) 工具 — 搜索、详情、下载"""

import json
import sys
from pathlib import Path

DOWNLOAD_DIR = "/mnt/d/data/jmcomic"

def _get_client():
    import jmcomic
    option = jmcomic.JmOption.default()
    return option, option.new_jm_client()

def search(query: str, page: int = 1) -> list[dict]:
    _, cl = _get_client()
    sp = cl.search_site(query, page=page)
    return [{"album_id": aid, "title": title, "url": f"https://18comic.vip/album/{aid}/"} for aid, title in sp]

def info(album_id: str) -> dict:
    _, cl = _get_client()
    album_id = str(album_id).replace("JM", "").replace("jm", "")
    album = cl.get_album_detail(album_id)
    chapters = [{"photo_id": pid, "title": t} for pid, t in album.iter_id_title()]
    return {
        "album_id": album.album_id, "title": album.title,
        "author": getattr(album, "author", ""), "tags": getattr(album, "tags", []),
        "chapters": chapters, "chapter_count": len(chapters),
    }

def download(album_id: str, output_dir: str = None) -> str:
    import jmcomic
    album_id = str(album_id).replace("JM", "").replace("jm", "")
    dest = output_dir or DOWNLOAD_DIR
    option = jmcomic.JmOption.default()
    option.dir_rule = jmcomic.DirRule('Bd_Aid', dest)
    print(f"📥 开始下载 JM{album_id}...")
    jmcomic.download_album(album_id, option)
    for d in Path(dest).iterdir():
        if d.is_dir() and d.name.startswith(album_id):
            files = list(d.rglob("*.webp")) + list(d.rglob("*.jpg")) + list(d.rglob("*.png"))
            print(f"✅ 下载完成: JM{album_id} → {d} ({len(files)} 张图片)")
            return str(d)
    print(f"✅ 下载完成: JM{album_id} → {dest}")
    return dest

def pack(gallery_dir: str) -> str:
    import zipfile
    d = Path(gallery_dir)
    zip_path = d.with_suffix(".zip")
    with zipfile.ZipFile(zip_path, "w", zipfile.ZIP_STORED) as zf:
        for f in sorted(d.rglob("*")):
            if f.is_file(): zf.write(f, f.relative_to(d))
    print(f"📦 已打包: {zip_path} ({zip_path.stat().st_size/1024/1024:.1f} MB)")
    return str(zip_path)

def print_results(results: list[dict]):
    for i, r in enumerate(results, 1):
        print(f"[{i}] JM{r['album_id']} — {r['title']}")
        print(f"    🔗 {r['url']}")
