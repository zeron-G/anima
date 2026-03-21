---
name: doujin
description: "Search and download doujinshi/manga from E-Hentai, ExHentai, and 禁漫天堂 (JMComic/18comic). Use when: user asks to search/download 本子/同人志/doujinshi/manga/hentai, mentions ehentai/exhentai/里站/禁漫/jmcomic/18comic/JM号, or wants to browse/fetch adult manga content. NOT for: general image search, non-manga content, or AI image generation (use comfyui-painter)."
---

# Doujin — 本子搜索下载工具

Unified tool for searching and downloading from **E-Hentai/ExHentai** and **禁漫天堂 (JMComic)**.

## Source Selection

| Source | Best for | Script |
|--------|----------|--------|
| **E-Hentai** | Japanese doujinshi, CG sets, cosplay, Western | `scripts/ehentai.py` |
| **ExHentai** | Same + restricted content (needs login) | `scripts/ehentai.py --ex` |
| **禁漫天堂** | Chinese translations, popular manga, fast download | `scripts/jm.py` |

**Default strategy:** Search both sources unless user specifies one. 禁漫 for Chinese content; E-Hentai for Japanese/English/tags.

## E-Hentai / ExHentai

### ExHentai login (optional)
```python
from scripts.ehentai import setup_login
setup_login("ipb_member_id", "ipb_pass_hash", "igneous")
```

### Search
```python
from scripts.ehentai import search, print_search_results
results = search("azur lane", exhentai=False)
print_search_results(results)
```

### Download
```python
from scripts.ehentai import download
path = download("https://e-hentai.org/g/12345/abcdef/")
```

### CLI
```bash
python3 scripts/ehentai.py search "keyword"
python3 scripts/ehentai.py search "keyword" --ex
python3 scripts/ehentai.py download "https://e-hentai.org/g/xxx/xxx/"
python3 scripts/ehentai.py pack /mnt/d/data/ehentai/xxx/
python3 scripts/ehentai.py login <member_id> <pass_hash> [igneous]
```

## 禁漫天堂 (JMComic)

Requires: `pip install jmcomic`

### Search
```python
from scripts.jm import search, print_results
results = search("碧蓝航线")
print_results(results)
```

### Download
```python
from scripts.jm import download
path = download("1252206")  # JM号
```

### CLI
```bash
python3 scripts/jm.py search "关键词"
python3 scripts/jm.py info 1252206
python3 scripts/jm.py download 1252206
python3 scripts/jm.py pack /mnt/d/data/jmcomic/xxx/
```

## Workflow

1. User gives keyword → search on appropriate source(s) → show results
2. User picks one → `download()` → saves to `/mnt/d/data/{ehentai,jmcomic}/`
3. Copy image(s) to workspace → send via `message` tool (Discord 25MB limit per file)
4. Large galleries → `pack()` → ZIP

## Download Dirs

- E-Hentai: `/mnt/d/data/ehentai/`
- JMComic: `/mnt/d/data/jmcomic/`

## Notes

- E-Hentai: rate limited ~1.5s/request, supports resume
- JMComic: multi-threaded, images auto-descrambled, no login required
- ExHentai needs cookies (ipb_member_id + ipb_pass_hash + igneous)
