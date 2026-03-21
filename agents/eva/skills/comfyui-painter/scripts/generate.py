#!/usr/bin/env python3
"""ComfyUI 图片生成：构建 prompt → 提交 API → 等待完成 → 返回图片路径"""

import json
import time
import random
import urllib.request
from pathlib import Path

CONFIG = json.loads((Path(__file__).parent.parent / "config.json").read_text())
API_URL = CONFIG["comfyui"]["api_url"]
OUTPUT_DIR = Path(CONFIG["comfyui"]["output_dir_linux"])
MODELS = CONFIG["models"]
DEFAULTS = CONFIG["defaults"]
LAST_USED = Path(__file__).parent.parent / ".last_used"


def resolve_model(name: str) -> str:
    """模型别名 → checkpoint 文件名"""
    name = name.lower().strip()
    if name in MODELS:
        return MODELS[name]
    # 尝试模糊匹配
    for alias, ckpt in MODELS.items():
        if name in alias or name in ckpt.lower():
            return ckpt
    return MODELS[DEFAULTS["model"]]


def build_img2img_prompt(
    positive: str,
    image_path: str,
    negative: str = None,
    model: str = None,
    steps: int = None,
    cfg: float = None,
    sampler: str = None,
    scheduler: str = None,
    denoise: float = 0.6,
    batch_size: int = None,
    seed: int = None,
) -> dict:
    """构建 img2img ComfyUI API prompt"""
    
    ckpt = resolve_model(model or DEFAULTS["model"])
    neg = negative or DEFAULTS["negative_prompt"]
    st = steps or DEFAULTS["steps"]
    c = cfg or DEFAULTS["cfg"]
    sam = sampler or DEFAULTS["sampler"]
    sch = scheduler or DEFAULTS["scheduler"]
    bs = batch_size or 1
    s = seed or random.randint(1, 2**32 - 1)

    # Convert image path to Windows path for ComfyUI
    win_path = image_path
    if image_path.startswith("/mnt/"):
        # /mnt/c/... -> C:/...
        parts = image_path.split("/")
        drive = parts[2].upper()
        rest = "/".join(parts[3:])
        win_path = f"{drive}:/{rest}"

    prompt = {
        "1": {
            "class_type": "CheckpointLoaderSimple",
            "inputs": {"ckpt_name": ckpt},
        },
        "2": {
            "class_type": "CLIPTextEncode",
            "inputs": {"text": positive, "clip": ["1", 1]},
        },
        "3": {
            "class_type": "CLIPTextEncode",
            "inputs": {"text": neg, "clip": ["1", 1]},
        },
        "4": {
            "class_type": "LoadImage",
            "inputs": {"image": image_path.split("/")[-1]},
        },
        "5": {
            "class_type": "VAEEncode",
            "inputs": {"pixels": ["4", 0], "vae": ["1", 2]},
        },
        "6": {
            "class_type": "KSampler",
            "inputs": {
                "seed": s,
                "control_after_generate": "randomize",
                "steps": st,
                "cfg": c,
                "sampler_name": sam,
                "scheduler": sch,
                "denoise": denoise,
                "model": ["1", 0],
                "positive": ["2", 0],
                "negative": ["3", 0],
                "latent_image": ["5", 0],
            },
        },
        "7": {
            "class_type": "VAEDecode",
            "inputs": {"samples": ["6", 0], "vae": ["1", 2]},
        },
        "8": {
            "class_type": "SaveImage",
            "inputs": {"filename_prefix": "i2i", "images": ["7", 0]},
        },
    }

    return prompt


def build_prompt(
    positive: str,
    negative: str = None,
    model: str = None,
    width: int = None,
    height: int = None,
    steps: int = None,
    cfg: float = None,
    sampler: str = None,
    scheduler: str = None,
    batch_size: int = None,
    seed: int = None,
    loras: list = None,
) -> dict:
    """构建 ComfyUI API prompt"""
    
    ckpt = resolve_model(model or DEFAULTS["model"])
    neg = negative or DEFAULTS["negative_prompt"]
    w = width or DEFAULTS["width"]
    h = height or DEFAULTS["height"]
    st = steps or DEFAULTS["steps"]
    c = cfg or DEFAULTS["cfg"]
    sam = sampler or DEFAULTS["sampler"]
    sch = scheduler or DEFAULTS["scheduler"]
    bs = batch_size or DEFAULTS["batch_size"]
    s = seed or random.randint(1, 2**32 - 1)

    prompt = {
        "3": {
            "class_type": "KSampler",
            "inputs": {
                "seed": s,
                "control_after_generate": "randomize",
                "steps": st,
                "cfg": c,
                "sampler_name": sam,
                "scheduler": sch,
                "denoise": 1,
                "model": ["4", 0],
                "positive": ["6", 0],
                "negative": ["7", 0],
                "latent_image": ["16", 0],
            },
        },
        "4": {
            "class_type": "CheckpointLoaderSimple",
            "inputs": {"ckpt_name": ckpt},
        },
        "6": {
            "class_type": "CLIPTextEncode",
            "inputs": {"text": positive, "clip": ["4", 1]},
        },
        "7": {
            "class_type": "CLIPTextEncode",
            "inputs": {"text": neg, "clip": ["4", 1]},
        },
        "8": {
            "class_type": "VAEDecode",
            "inputs": {"samples": ["3", 0], "vae": ["4", 2]},
        },
        "9": {
            "class_type": "SaveImage",
            "inputs": {"filename_prefix": "sdi", "images": ["8", 0]},
        },
        "16": {
            "class_type": "EmptyLatentImage",
            "inputs": {"width": w, "height": h, "batch_size": bs},
        },
    }

    return prompt


def submit(prompt: dict) -> str:
    """提交 prompt 到 ComfyUI，返回 prompt_id"""
    data = json.dumps({"prompt": prompt}).encode()
    req = urllib.request.Request(
        f"{API_URL}/api/prompt",
        data=data,
        headers={"Content-Type": "application/json"},
    )
    resp = urllib.request.urlopen(req, timeout=15)
    result = json.loads(resp.read())
    
    if result.get("node_errors"):
        raise RuntimeError(f"节点错误: {json.dumps(result['node_errors'])}")
    
    return result["prompt_id"]


def wait_for_completion(timeout=300, poll_interval=3) -> bool:
    """等待队列清空"""
    for _ in range(timeout // poll_interval):
        time.sleep(poll_interval)
        try:
            resp = urllib.request.urlopen(f"{API_URL}/api/queue", timeout=5)
            q = json.loads(resp.read())
            running = len(q.get("queue_running", []))
            pending = len(q.get("queue_pending", []))
            if running == 0 and pending == 0:
                return True
        except Exception:
            pass
    return False


def get_latest_images(count: int = 4, prefix: str = "sdi") -> list:
    """获取最新生成的图片路径"""
    pattern = f"{prefix}_*.png"
    files = sorted(OUTPUT_DIR.glob(pattern), key=lambda f: f.stat().st_mtime, reverse=True)
    return [str(f) for f in files[:count]]


def generate(
    positive: str,
    negative: str = None,
    model: str = None,
    width: int = None,
    height: int = None,
    steps: int = None,
    cfg: float = None,
    sampler: str = None,
    scheduler: str = None,
    batch_size: int = None,
    seed: int = None,
) -> dict:
    """完整生成流程：构建 → 提交 → 等待 → 返回图片"""
    
    # 更新最后使用时间
    LAST_USED.write_text(str(time.time()))
    
    bs = batch_size or DEFAULTS["batch_size"]
    prompt = build_prompt(
        positive=positive,
        negative=negative,
        model=model,
        width=width,
        height=height,
        steps=steps,
        cfg=cfg,
        sampler=sampler,
        scheduler=scheduler,
        batch_size=bs,
        seed=seed,
    )
    
    prompt_id = submit(prompt)
    success = wait_for_completion()
    
    if not success:
        return {"ok": False, "error": "生成超时"}
    
    images = get_latest_images(count=bs)
    
    # 更新最后使用时间
    LAST_USED.write_text(str(time.time()))
    
    return {"ok": True, "prompt_id": prompt_id, "images": images}


def upload_image(image_path: str, filename: str = None) -> str:
    """上传图片到 ComfyUI input 目录，返回文件名"""
    import shutil
    
    input_dir = Path(CONFIG["comfyui"]["output_dir_linux"]).parent / "input"
    input_dir.mkdir(exist_ok=True)
    
    fname = filename or Path(image_path).name
    dest = input_dir / fname
    shutil.copy2(image_path, dest)
    return fname


def img2img(
    positive: str,
    image_path: str,
    negative: str = None,
    model: str = None,
    steps: int = None,
    cfg: float = None,
    sampler: str = None,
    scheduler: str = None,
    denoise: float = 0.6,
    batch_size: int = None,
    seed: int = None,
) -> dict:
    """图生图：输入图片 + 提示词 → 生成新图片
    
    Args:
        positive: 正面提示词
        image_path: 输入图片的 Linux 路径
        denoise: 去噪强度（0.0=完全保留原图, 1.0=完全重绘，推荐 0.4-0.7）
        其他参数同 generate()
    
    Returns:
        {"ok": True, "images": [...]}
    """
    LAST_USED.write_text(str(time.time()))
    
    # Upload image to ComfyUI input dir
    filename = upload_image(image_path)
    
    bs = batch_size or 1
    prompt = build_img2img_prompt(
        positive=positive,
        image_path=filename,
        negative=negative,
        model=model,
        steps=steps,
        cfg=cfg,
        sampler=sampler,
        scheduler=scheduler,
        denoise=denoise,
        batch_size=bs,
        seed=seed,
    )
    
    # Fix: LoadImage uses just filename, not full path
    prompt["4"]["inputs"]["image"] = filename
    
    prompt_id = submit(prompt)
    success = wait_for_completion()
    
    if not success:
        return {"ok": False, "error": "生成超时"}
    
    images = get_latest_images(count=bs, prefix="i2i")
    LAST_USED.write_text(str(time.time()))
    
    return {"ok": True, "prompt_id": prompt_id, "images": images}


if __name__ == "__main__":
    import sys
    if len(sys.argv) < 2:
        print("用法: generate.py <positive_prompt> [--model noobv4] [--batch 4]")
        sys.exit(1)
    
    result = generate(positive=sys.argv[1])
    print(json.dumps(result, indent=2))
