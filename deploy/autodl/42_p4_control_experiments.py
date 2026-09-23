#!/usr/bin/env python3
"""42_p4_control_experiments.py —— P4 控制体系真实 GPU 实验（走 ComfyUI HTTP API）。

设计纪律
  · **客观指标，不造人工分**：本脚本只产出机器可复算的数字（edge-F1 / SSIM / 耗时）。
    control_matrix.md 里「人工评分 ≥4/5」这类主观项一律不由此脚本填写。
  · **失败必须显形**：任一实验单元出错即记 `status=error` 与原始错误文本，
    绝不静默跳过、绝不拿旧结果顶替。
  · **同口径可比**：同 prompt / 同底模 / 同步数 / 同分辨率 / 同 seed 基线，
    只改被测变量。

实验
  preprocess   P4-01 预处理器评测：对同一参考图跑各预处理器，落控制图
  cn-scan      P4-02 ControlNet 权重扫描（strength 0.0→1.0）
  ipa-scan     P4-03 IP-Adapter 权重扫描
  joint        P4-07 两路 ControlNet 联合（canny + depth）权重配比
  consistency  P4-09/10 固定参考图与控制、仅变 seed，测结构一致性
  synth-person P4-06 前置：合成全身人物参考图（本机无人物素材；取骨架占比最高候选）
  pose         P4-01 **重评**：姿态预处理器对人物图 vs 商品图（后者即既有记录的「空转」对照）
  material     P4-09/10 材质一致性：canny-only vs canny+IP-Adapter，仅变 seed（含人工评分表）

用法（GPU 服务器，用 conda python 以拿到 numpy/cv2/skimage）：
    /root/miniconda3/bin/python 42_p4_control_experiments.py \
        --exp preprocess --exp cn-scan \
        --ref /root/autodl-tmp/l4_assets/ref_product.png \
        --outdir /root/autodl-tmp/p4_out

    # 补 P4 两条硬缺口（姿态预处理器重评 + 材质一致性）
    /root/miniconda3/bin/python 42_p4_control_experiments.py \
        --exp synth-person --exp pose --exp material --n 10 \
        --ref /root/autodl-tmp/l4_assets/ref_product.png \
        --outdir /root/autodl-tmp/p4_out --merge

退出码：0 = 全部单元成功；1 = 有单元失败（详见 metrics.json）
"""

from __future__ import annotations

import argparse
import json
import mimetypes
import os
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
import uuid
from pathlib import Path

import cv2
import numpy as np

HOST = os.environ.get("COMFY_HOST", "127.0.0.1")
PORT = int(os.environ.get("COMFY_PORT", "8188"))
BASE = f"http://{HOST}:{PORT}"

DEFAULT_PROMPT = (
    "professional e-commerce product photo, a matte ceramic perfume bottle on a "
    "clean seamless white background, soft studio softbox lighting, sharp focus, "
    "high detail, commercial advertising quality"
)
DEFAULT_NEGATIVE = (
    "blurry, low quality, jpeg artifacts, distorted, deformed, watermark, text, "
    "extra objects, cluttered background"
)

#: P4-01 预处理器清单（节点名 → 额外固定入参）
#: 有意不含 Zoe-DepthMapPreprocessor：其权重（ZoeD_M12_N.pt）在本机缺失，
#: 且节点会直连 huggingface.co 下载（本机不可达）⇒ 必失败单元，不白烧机时。
#: NormalMap 无对应节点，见 control_matrix.md 的说明。
PREPROCESSORS = {
    "CannyEdgePreprocessor": {"low_threshold": 100, "high_threshold": 200},
    "LineArtPreprocessor": {"coarse": "disable"},
    "HEDPreprocessor": {"safe": "enable"},
    "DepthAnythingV2Preprocessor": {"ckpt_name": "depth_anything_v2_vitl.pth"},
    "OpenposePreprocessor": {"detect_hand": "disable", "detect_face": "disable"},
    "DWPreprocessor": {"detect_hand": "disable", "detect_face": "disable"},
}

#: 每个控制类型配套的 SDXL ControlNet（缺模型时该单元会失败并显形）
PREPROC_FOR_CN = {
    "canny": "CannyEdgePreprocessor",
    "depth": "DepthAnythingV2Preprocessor",
    "lineart": "LineArtPreprocessor",
    "softedge": "HEDPreprocessor",
    "openpose": "OpenposePreprocessor",
    "dwpose": "DWPreprocessor",
}

#: P4-01 姿态预处理器重评用的人物参考图提示词。
#: 本机**没有**任何人物素材，用自家 t2i 合成可规避第三方素材的许可问题。
PERSON_PROMPT = (
    "full body studio photograph of a standing fashion model, head to toe visible, "
    "plain light gray seamless background, facing camera, arms held slightly away from body, "
    "wearing plain fitted neutral clothing, soft even lighting, sharp focus, "
    "professional e-commerce catalog photo"
)
PERSON_NEGATIVE = (
    "cropped, out of frame, head cut off, feet cut off, close-up, portrait crop, "
    "blurry, deformed, extra limbs, watermark, text, cluttered background"
)

#: 姿态预处理器输出的「非黑像素占比」下限 —— 低于此值判为**空图**（没检出人体）。
#: 参考：2026-09-23 对商品图（马克杯）跑 openpose/DW 得全黑，占比 ≈ 0。
SKELETON_NONBLACK_MIN = 0.005


# ----------------------------------------------------------------------------
# ComfyUI HTTP 客户端
# ----------------------------------------------------------------------------
class Comfy:
    def __init__(self, base: str, timeout: float = 600.0) -> None:
        self.base = base.rstrip("/")
        self.timeout = timeout
        self.client_id = str(uuid.uuid4())

    def _get(self, path: str, timeout: float | None = None) -> bytes:
        req = urllib.request.Request(self.base + path)
        with urllib.request.urlopen(req, timeout=timeout or self.timeout) as r:
            return r.read()

    def _post_json(self, path: str, payload: dict) -> dict:
        body = json.dumps(payload).encode()
        req = urllib.request.Request(
            self.base + path,
            data=body,
            method="POST",
            headers={"Content-Type": "application/json"},
        )
        with urllib.request.urlopen(req, timeout=self.timeout) as r:
            return json.loads(r.read())

    def stats(self) -> dict:
        return json.loads(self._get("/system_stats", timeout=15))

    def upload(self, path: Path) -> str:
        boundary = "----p4" + uuid.uuid4().hex
        ctype = mimetypes.guess_type(path.name)[0] or "image/png"
        data = path.read_bytes()
        parts = []
        parts.append(
            f'--{boundary}\r\nContent-Disposition: form-data; name="image"; '
            f'filename="{path.name}"\r\nContent-Type: {ctype}\r\n\r\n'.encode()
        )
        parts.append(data)
        parts.append(
            f'\r\n--{boundary}\r\nContent-Disposition: form-data; name="type"\r\n\r\n'
            f"input\r\n--{boundary}--\r\n".encode()
        )
        body = b"".join(parts)
        req = urllib.request.Request(
            self.base + "/upload/image",
            data=body,
            method="POST",
            headers={"Content-Type": f"multipart/form-data; boundary={boundary}"},
        )
        with urllib.request.urlopen(req, timeout=120) as r:
            info = json.loads(r.read())
        sub = info.get("subfolder") or ""
        return f"{sub}/{info['name']}" if sub else info["name"]

    def run(self, workflow: dict, label: str, poll: float = 2.0) -> dict:
        """提交并等待；返回 {status, images, error, elapsed_s}。"""
        started = time.monotonic()
        try:
            resp = self._post_json("/prompt", {"prompt": workflow, "client_id": self.client_id})
        except urllib.error.HTTPError as exc:
            detail = exc.read().decode(errors="replace")
            return {"status": "error", "images": [], "error": f"HTTP {exc.code}: {detail[:1500]}",
                    "elapsed_s": 0.0}
        pid = resp.get("prompt_id")
        if not pid:
            return {"status": "error", "images": [], "error": f"no prompt_id: {resp}",
                    "elapsed_s": 0.0}

        deadline = started + self.timeout
        while time.monotonic() < deadline:
            time.sleep(poll)
            try:
                hist = json.loads(self._get(f"/history/{pid}", timeout=30))
            except Exception:  # noqa: BLE001
                continue
            if pid not in hist:
                continue
            entry = hist[pid]
            status = entry.get("status", {})
            if status.get("status_str") == "error" or status.get("completed") is False:
                msgs = status.get("messages", [])
                return {"status": "error", "images": [],
                        "error": json.dumps(msgs, ensure_ascii=False)[:1800],
                        "elapsed_s": round(time.monotonic() - started, 3)}
            images = []
            for out in entry.get("outputs", {}).values():
                images.extend(out.get("images", []))
            if images:
                return {"status": "ok", "images": images, "error": None,
                        "elapsed_s": round(time.monotonic() - started, 3)}
            return {"status": "error", "images": [], "error": "no images in outputs",
                    "elapsed_s": round(time.monotonic() - started, 3)}
        return {"status": "timeout", "images": [], "error": f"timeout {label}",
                "elapsed_s": round(time.monotonic() - started, 3)}

    def download(self, img: dict, dest: Path) -> Path:
        sub = img.get("subfolder", "")
        url = f"/view?filename={urllib.parse.quote(img['filename'])}&subfolder={urllib.parse.quote(sub)}&type=output"
        dest.parent.mkdir(parents=True, exist_ok=True)
        dest.write_bytes(self._get(url, timeout=120))
        return dest


# ----------------------------------------------------------------------------
# 工作流拼装
# ----------------------------------------------------------------------------
def wf_base(prompt: str, negative: str, seed: int, steps: int, cfg: float,
            width: int, height: int, prefix: str) -> dict:
    return {
        "4": {"class_type": "CheckpointLoaderSimple",
              "inputs": {"ckpt_name": "sd_xl_base_1.0.safetensors"}},
        "5": {"class_type": "EmptyLatentImage",
              "inputs": {"width": width, "height": height, "batch_size": 1}},
        "6": {"class_type": "CLIPTextEncode", "inputs": {"text": prompt, "clip": ["4", 1]}},
        "7": {"class_type": "CLIPTextEncode", "inputs": {"text": negative, "clip": ["4", 1]}},
        "3": {"class_type": "KSampler", "inputs": {
            "seed": seed, "steps": steps, "cfg": cfg, "sampler_name": "dpmpp_2m",
            "scheduler": "karras", "denoise": 1.0, "model": ["4", 0],
            "positive": ["6", 0], "negative": ["7", 0], "latent_image": ["5", 0]}},
        "8": {"class_type": "VAEDecode", "inputs": {"samples": ["3", 0], "vae": ["4", 2]}},
        "9": {"class_type": "SaveImage",
              "inputs": {"filename_prefix": prefix, "images": ["8", 0]}},
    }


def wf_preprocess(ref_name: str, node: str, extra: dict, prefix: str) -> dict:
    inputs = {"image": ["11", 0]}
    inputs.update(extra)
    return {
        "11": {"class_type": "LoadImage", "inputs": {"image": ref_name}},
        "12": {"class_type": node, "inputs": inputs},
        "9": {"class_type": "SaveImage",
              "inputs": {"filename_prefix": prefix, "images": ["12", 0]}},
    }


def _add_controlnet(wf: dict, node_id: str, cn_name: str, image_ref: list,
                    strength: float, pos: list, neg: list) -> tuple[list, list]:
    """给 wf 挂一路 ControlNet，返回新的 (positive, negative) 引用。"""
    ns = f"{node_id}_loader"
    na = f"{node_id}_apply"
    wf[ns] = {"class_type": "ControlNetLoader", "inputs": {"control_net_name": cn_name}}
    wf[na] = {"class_type": "ControlNetApplyAdvanced", "inputs": {
        "positive": pos, "negative": neg, "control_net": [ns, 0], "image": image_ref,
        "strength": strength, "start_percent": 0.0, "end_percent": 1.0}}
    return [na, 0], [na, 1]


def wf_controlnet(prompt: str, negative: str, ref_name: str, preproc: str,
                  preproc_extra: dict, cn_name: str, strength: float, seed: int,
                  steps: int, cfg: float, width: int, height: int, prefix: str) -> dict:
    wf = wf_base(prompt, negative, seed, steps, cfg, width, height, prefix)
    wf["11"] = {"class_type": "LoadImage", "inputs": {"image": ref_name}}
    pe = {"image": ["11", 0]}
    pe.update(preproc_extra)
    wf["12"] = {"class_type": preproc, "inputs": pe}
    pos, neg = _add_controlnet(wf, "20", cn_name, ["12", 0], strength, ["6", 0], ["7", 0])
    wf["3"]["inputs"]["positive"] = pos
    wf["3"]["inputs"]["negative"] = neg
    return wf


def add_ipadapter(wf: dict, ipa_name: str, clip_name: str, ref_name: str,
                  weight: float, weight_type: str, node_id: str = "20") -> dict:
    """把 IP-Adapter 挂到 wf 的 KSampler 模型链上。

    可就地叠加在已有的 ControlNet 之后（`wf["3"].inputs.model` 前插一层），
    用于「结构用 CN 锁 + 材质用 IP-Adapter 锁」的联合作业。
    """
    wf[node_id] = {"class_type": "IPAdapterModelLoader",
                   "inputs": {"ipadapter_file": ipa_name}}
    wf[f"{node_id}_cv"] = {"class_type": "CLIPVisionLoader", "inputs": {"clip_name": clip_name}}
    wf[f"{node_id}_img"] = {"class_type": "LoadImage", "inputs": {"image": ref_name}}
    wf[f"{node_id}_ipa"] = {"class_type": "IPAdapterAdvanced", "inputs": {
        "model": wf["3"]["inputs"]["model"], "ipadapter": [node_id, 0],
        "clip_vision": [f"{node_id}_cv", 0], "image": [f"{node_id}_img", 0],
        "weight": weight, "weight_type": weight_type, "combine_embeds": "concat",
        "start_at": 0.0, "end_at": 1.0, "embeds_scaling": "V only"}}
    wf["3"]["inputs"]["model"] = [f"{node_id}_ipa", 0]
    return wf


def wf_ipadapter(prompt: str, negative: str, ref_name: str, ipa_name: str, clip_name: str,
                 weight: float, weight_type: str, seed: int, steps: int, cfg: float,
                 width: int, height: int, prefix: str) -> dict:
    wf = wf_base(prompt, negative, seed, steps, cfg, width, height, prefix)
    return add_ipadapter(wf, ipa_name, clip_name, ref_name, weight, weight_type, node_id="20")


def wf_joint(prompt: str, negative: str, ref_name: str, cn1: tuple, cn2: tuple,
             seed: int, steps: int, cfg: float, width: int, height: int,
             prefix: str) -> dict:
    """cn1/cn2 = (preproc_node, extra, cn_model, strength)"""
    wf = wf_base(prompt, negative, seed, steps, cfg, width, height, prefix)
    wf["11"] = {"class_type": "LoadImage", "inputs": {"image": ref_name}}
    pe1 = {"image": ["11", 0]}
    pe1.update(cn1[1])
    wf["12"] = {"class_type": cn1[0], "inputs": pe1}
    pe2 = {"image": ["11", 0]}
    pe2.update(cn2[1])
    wf["13"] = {"class_type": cn2[0], "inputs": pe2}
    pos, neg = _add_controlnet(wf, "20", cn1[2], ["12", 0], cn1[3], ["6", 0], ["7", 0])
    pos, neg = _add_controlnet(wf, "21", cn2[2], ["13", 0], cn2[3], pos, neg)
    wf["3"]["inputs"]["positive"] = pos
    wf["3"]["inputs"]["negative"] = neg
    return wf


# ----------------------------------------------------------------------------
# 客观指标
# ----------------------------------------------------------------------------
def read_gray(path: Path) -> np.ndarray:
    img = cv2.imread(str(path), cv2.IMREAD_GRAYSCALE)
    if img is None:
        raise ValueError(f"读不出图：{path}")
    return img


def edges(gray: np.ndarray, lo: int = 100, hi: int = 200) -> np.ndarray:
    return cv2.Canny(gray, lo, hi)


def _match_shape(a: np.ndarray, b: np.ndarray) -> tuple[np.ndarray, np.ndarray]:
    """把 a 缩放到与 b 同尺寸。

    预处理器默认 resolution=512，出图是 1024 ⇒ 直接逐像素比会广播失败
    （2026-09-23 首次跑就因此中断，见项目进展）。
    """
    if a.shape != b.shape:
        a = cv2.resize(a, (b.shape[1], b.shape[0]), interpolation=cv2.INTER_NEAREST)
    return a, b


def edge_f1(control_gray: np.ndarray, out_gray: np.ndarray, tol: int = 2) -> float:
    """控制图边缘 vs 产物边缘的 F1（对产物边缘做 tol 像素膨胀以容忍轻微位移）。"""
    control_gray, out_gray = _match_shape(control_gray, out_gray)
    a = edges(control_gray) > 0
    b = edges(out_gray) > 0
    if a.sum() == 0:
        return float("nan")
    k = np.ones((2 * tol + 1, 2 * tol + 1), np.uint8)
    b_dil = cv2.dilate(b.astype(np.uint8), k) > 0
    tp = float((a & b_dil).sum())
    prec = tp / max(1.0, float(b_dil.sum()))
    rec = tp / max(1.0, float(a.sum()))
    if prec + rec == 0:
        return 0.0
    return round(2 * prec * rec / (prec + rec), 4)


def ssim(a_gray: np.ndarray, b_gray: np.ndarray) -> float:
    from skimage.metrics import structural_similarity as _ssim
    a_gray, b_gray = _match_shape(a_gray, b_gray)
    return round(float(_ssim(a_gray, b_gray, data_range=255)), 4)


def mean_abs_diff(a_gray: np.ndarray, b_gray: np.ndarray) -> float:
    a_gray, b_gray = _match_shape(a_gray, b_gray)
    return round(float(np.abs(a_gray.astype(np.int16) - b_gray.astype(np.int16)).mean()), 2)


def count_failures(node) -> int:
    """递归统计所有形如 {"status": ...} 且 status != "ok" 的单元数。"""
    n = 0
    if isinstance(node, dict):
        st = node.get("status")
        if isinstance(st, str):
            for v in node.values():
                n += count_failures(v)
            return n + (0 if st == "ok" else 1)
        for v in node.values():
            n += count_failures(v)
    elif isinstance(node, list):
        for v in node:
            n += count_failures(v)
    return n


def contact_sheet(paths: list[Path], labels: list[str], dest: Path,
                  cell: int = 384) -> None:
    tiles = []
    for p, lab in zip(paths, labels, strict=False):
        img = cv2.imread(str(p), cv2.IMREAD_COLOR)
        if img is None:
            continue
        img = cv2.resize(img, (cell, cell), interpolation=cv2.INTER_AREA)
        cv2.rectangle(img, (0, 0), (cell - 1, 26), (0, 0, 0), -1)
        cv2.putText(img, lab, (6, 19), cv2.FONT_HERSHEY_SIMPLEX, 0.55, (255, 255, 255), 1,
                    cv2.LINE_AA)
        tiles.append(img)
    if not tiles:
        return
    rows = []
    per_row = 5
    for i in range(0, len(tiles), per_row):
        chunk = tiles[i:i + per_row]
        while len(chunk) < per_row:
            chunk.append(np.zeros_like(tiles[0]))
        rows.append(cv2.hconcat(chunk))
    dest.parent.mkdir(parents=True, exist_ok=True)
    cv2.imwrite(str(dest), cv2.vconcat(rows))


# ----------------------------------------------------------------------------
# 材质 / 颜色一致性指标（P4-09/10）
# ----------------------------------------------------------------------------
def subject_mask(bgr: np.ndarray, dist_thr: float = 30.0, min_area: float = 0.02,
                 max_area: float = 0.90):
    """取**主体（前景）区域**掩码：以图像边框的中位色为背景色，取与其色距超阈的像素。

    为什么不用「灰度 < 245 即主体」的白底阈值法：本目录的产物背景**不是纯白**
    （提示词给的是 seamless white，SDXL 实际出的是带渐变的浅色背景），
    实测白底法退化成整图掩码（面积比 ≈1.0），等于没有掩码 —— 2026-09-23 自测时发现。
    改按边框估背景色后，掩码与背景色无关，白底/灰底/渐变色背景都适用。

    ⚠️ 这是**粗前景**而非语义分割：主体若贴到画框边缘、或背景本身是渐变，
    掩码会不干净。因此这里**同时记录面积比**，并把退化解（面积 > max_area 或 < min_area）
    显式判为失败（返回 None），让它显形而不是被静默采用。
    """
    h, w = bgr.shape[:2]
    b = max(4, int(0.05 * min(h, w)))
    border = np.concatenate([
        bgr[:b].reshape(-1, 3), bgr[-b:].reshape(-1, 3),
        bgr[:, :b].reshape(-1, 3), bgr[:, -b:].reshape(-1, 3),
    ])
    bg = np.median(border, axis=0)
    diff = np.linalg.norm(bgr.astype(np.float32) - bg.astype(np.float32), axis=2)
    m = (diff > dist_thr).astype(np.uint8)
    m = cv2.morphologyEx(m, cv2.MORPH_OPEN, np.ones((5, 5), np.uint8))
    m = cv2.morphologyEx(m, cv2.MORPH_CLOSE, np.ones((25, 25), np.uint8))
    n, labels, stats, _ = cv2.connectedComponentsWithStats(m, 8)
    if n <= 1:
        return None
    idx = 1 + int(np.argmax(stats[1:, cv2.CC_STAT_AREA]))
    mask = labels == idx
    ratio = float(mask.sum()) / float(mask.size)
    if ratio < min_area or ratio > max_area:
        return None
    return mask


def lab_mean(bgr: np.ndarray, mask) -> np.ndarray:
    """掩码内 CIELAB 均值（L/a/b）。"""
    lab = cv2.cvtColor(bgr, cv2.COLOR_BGR2LAB)
    return lab[mask].mean(axis=0)


def hue_hist(bgr: np.ndarray, mask, bins: int = 32) -> np.ndarray:
    """掩码内色调直方图（归一化，位置无关）。"""
    hsv = cv2.cvtColor(bgr, cv2.COLOR_BGR2HSV)
    h = hsv[:, :, 0][mask]
    hist, _ = np.histogram(h, bins=bins, range=(0, 180))
    hist = hist.astype(np.float64)
    total = hist.sum()
    return hist / total if total > 0 else hist


def hist_hellinger(a: np.ndarray, b: np.ndarray) -> float:
    """两个归一化直方图的平方 Hellinger 距离（0 = 完全相同，1 = 不重叠）。"""
    return round(float(0.5 * np.sum((np.sqrt(a) - np.sqrt(b)) ** 2)), 5)


def material_stats(paths: list[Path]) -> dict:
    """结构（SSIM）+ 材质/颜色（掩码内 Lab 漂移、色调直方图距离）双口径。

    ⚠️ **这两个口径都不是画质分**：SSIM 测结构贴合，Lab/色调漂移测「跨 seed 的商品外观稳定性」。
    真正的人工评分（DoD 的 ≥4/5）不在此脚本能力范围内。
    """
    imgs = [cv2.imread(str(p), cv2.IMREAD_COLOR) for p in paths]
    imgs = [im for im in imgs if im is not None]
    if len(imgs) < 2:
        return {"n": len(imgs), "error": "可用图不足 2 张，无法算成对指标"}

    grays = [cv2.cvtColor(im, cv2.COLOR_BGR2GRAY) for im in imgs]
    pair = [ssim(grays[i], grays[j])
            for i in range(len(grays)) for j in range(i + 1, len(grays))]

    labs, hues, areas = [], [], []
    for im in imgs:
        mask = subject_mask(im)
        if mask is None:
            labs.append(None)
            hues.append(None)
            areas.append(0.0)
            continue
        areas.append(round(float(mask.sum()) / float(mask.size), 4))
        labs.append(lab_mean(im, mask))
        hues.append(hue_hist(im, mask))

    ok_labs = [x for x in labs if x is not None]
    lab_std, color_drift = None, None
    if len(ok_labs) >= 2:
        arr = np.asarray(ok_labs, dtype=np.float64)
        std = arr.std(axis=0)
        lab_std = [round(float(x), 2) for x in std]          # [dL, da, db]
        color_drift = round(float(np.linalg.norm(std)), 2)   # ΔE 形式的综合漂移，越低越稳

    ok_hues = [x for x in hues if x is not None]
    hue_pairs = [hist_hellinger(ok_hues[i], ok_hues[j])
                 for i in range(len(ok_hues)) for j in range(i + 1, len(ok_hues))]

    return {
        "n": len(imgs),
        "mask_failures": sum(1 for x in labs if x is None),
        "subject_area_ratio": areas,
        "ssim_pairs": len(pair),
        "ssim_mean": round(float(np.mean(pair)), 4),
        "ssim_min": round(float(np.min(pair)), 4),
        "ssim_max": round(float(np.max(pair)), 4),
        "ssim_std": round(float(np.std(pair)), 4),
        "lab_std_per_channel": lab_std,
        "color_drift_deltaE": color_drift,
        "hue_hist_mean_hellinger": round(float(np.mean(hue_pairs)), 4) if hue_pairs else None,
    }


def score_sheet_html(arms: dict[str, list[Path]], dest: Path, note: str = "") -> None:
    """生成**人工评分表**（P4 DoD 的直接判据）。

    本文件只负责把待评产物并列摆出来，**分数由人填**（见 `docs/sop/quality_rubric.md`）。
    脚本绝不预填任何分数。
    """
    rows = []
    for arm, paths in arms.items():
        cells = "".join(
            f'<td><img src="{p.name}" width="170"><div class="cap">{p.stem}</div></td>'
            for p in paths
        )
        rows.append(f'<tr><th class="arm">{arm}</th>{cells}</tr>')
    dest.write_text(
        "<!doctype html><meta charset='utf-8'><title>P4 一致性人工评分表</title>"
        "<style>body{font:13px/1.5 system-ui,sans-serif;margin:16px}"
        "table{border-collapse:collapse}th,td{border:1px solid #ccc;padding:4px;vertical-align:top}"
        ".arm{background:#f5f5f5;width:110px;text-align:left}"
        ".cap{font-size:11px;color:#666;text-align:center}"
        ".todo{background:#fffbe6}code{background:#f0f0f0;padding:1px 3px}</style>"
        "<h2>P4 一致性实验 · 人工评分表</h2>"
        f"<p>{note}</p>"
        "<p>打分口径见 <code>docs/sop/quality_rubric.md</code>：D1 技术缺陷 / D2 构图 / D3 一致性 / D4 商用可用度，"
        "各 1–5 分，<b>不做加权</b>；命中任一 <code>hit</code> 直接判废（1 分）。"
        "DoD 判据是「同一商品 10 次生成<b>结构一致性</b>人工评分 ≥ 4/5」。</p>"
        "<table><tr><th>组</th><th colspan='10'>产物（逐个打分）</th></tr>"
        + "".join(rows) +
        "</table>"
        "<h3>评分记录（请手工填写后另存/提交）</h3>"
        "<table><tr><th>组</th><th>D1</th><th>D2</th><th>D3</th><th>D4</th>"
        "<th>命中的 neg-xxx</th><th>评分者</th><th>时间</th></tr>"
        + "".join(
            f'<tr class="todo"><th>{arm}</th><td></td><td></td><td></td><td></td>'
            f'<td></td><td></td><td></td></tr>'
            for arm in arms
        )
        + "</table>"
        "<p>⚠️ 本表由脚本生成，<b>分数一律为空</b> —— 脚本不产生任何主观分。</p>",
        encoding="utf-8",
    )


# ----------------------------------------------------------------------------
# 实验实现
# ----------------------------------------------------------------------------
class Ctx:
    def __init__(self, args) -> None:
        self.a = args
        self.client = Comfy(BASE, timeout=args.timeout)
        self.out = args.outdir
        self.out.mkdir(parents=True, exist_ok=True)
        self.ref = args.ref
        self.ref_name = self.client.upload(args.ref)
        self.person_ref_name: str | None = None
        self.results: dict = {}
        self.failures = 0

    def note(self, key: str, value) -> None:
        self.results[key] = value


def exp_preprocess(ctx: Ctx) -> dict:
    print("\n=== P4-01 预处理器评测 ===")
    rows, paths, labels = [], [], []
    only = ctx.a.preproc or list(PREPROCESSORS)
    for node in only:
        extra = PREPROCESSORS.get(node)
        if extra is None:
            print(f"  [SKIP] 未知预处理器：{node}")
            rows.append({"preprocessor": node, "status": "unknown", "error": "不在清单内"})
            continue
        prefix = f"p4_pre_{node}"
        wf = wf_preprocess(ctx.ref_name, node, extra, prefix)
        r = ctx.client.run(wf, node)
        if r["status"] != "ok":
            print(f"  [FAIL] {node}: {str(r.get('error'))[:220]}")
            rows.append({"preprocessor": node, "status": r["status"],
                         "error": str(r.get("error"))[:400]})
            ctx.failures += 1
            continue
        dest = ctx.out / "preprocess" / f"{node}.png"
        for img in r["images"]:
            ctx.client.download(img, dest)
            break
        rows.append({"preprocessor": node, "status": "ok", "elapsed_s": r["elapsed_s"],
                     "file": str(dest)})
        paths.append(dest)
        labels.append(node.replace("Preprocessor", ""))
        print(f"  [ OK ] {node} -> {dest.name} ({r['elapsed_s']}s)")
    if paths:
        contact_sheet(paths, labels, ctx.out / "preprocess" / "_sheet.png")
    ctx.note("p4_01_preprocess", rows)
    return {"units": rows}


def exp_cn_scan(ctx: Ctx) -> dict:
    all_rows: dict[str, list] = {}
    for kind in ctx.a.cn_kind:
        if kind not in PREPROC_FOR_CN:
            print(f"  [SKIP] 未知控制类型：{kind}")
            all_rows[kind] = [{"status": "unknown", "error": "不在清单内"}]
            continue
        cn = ctx.a.cn_model_map.get(kind)
        if not cn:
            print(f"  [SKIP] {kind}：本机无对应 ControlNet 模型")
            all_rows[kind] = [{"status": "skipped", "error": "无对应 ControlNet 模型文件"}]
            continue
        all_rows[kind] = _cn_scan_one(ctx, kind, cn)
    ctx.note("p4_02_cn_scan", all_rows)
    return {"units": all_rows}


def _cn_scan_one(ctx: Ctx, kind: str, cn: str) -> list:
    preproc = PREPROC_FOR_CN[kind]
    extra = PREPROCESSORS[preproc]
    print(f"\n=== P4-02 ControlNet 权重扫描（{kind} / {cn}） ===")
    # strength=0 作为基线（控制不生效，仅作 pixel-delta 参考）
    weights = [0.0] + list(ctx.a.weights)
    rows, paths, labels = [], [], []
    for w in weights:
        prefix = f"p4_cn_{kind}_w{str(w).replace('.', '_')}"
        wf = wf_controlnet(ctx.a.prompt, ctx.a.negative, ctx.ref_name, preproc, extra, cn,
                           w, ctx.a.seed, ctx.a.steps, ctx.a.cfg, ctx.a.size, ctx.a.size, prefix)
        r = ctx.client.run(wf, f"{kind}@{w}")
        if r["status"] != "ok":
            print(f"  [FAIL] w={w}: {str(r.get('error'))[:220]}")
            rows.append({"strength": w, "status": r["status"],
                         "error": str(r.get("error"))[:400]})
            ctx.failures += 1
            continue
        dest = ctx.out / "cn_scan" / f"{kind}_w{str(w).replace('.', '_')}.png"
        for img in r["images"]:
            ctx.client.download(img, dest)
            break
        rows.append({"strength": w, "status": "ok", "elapsed_s": r["elapsed_s"],
                     "file": str(dest), "edge_f1": None, "delta_vs_strength0": None})
        paths.append(dest)
        labels.append(f"w={w}")
        print(f"  [ OK ] w={w} ({r['elapsed_s']}s)")

    # 指标
    ok = [r for r in rows if r["status"] == "ok"]
    if len(ok) >= 2:
        ctrl_path = ctx.out / "preprocess" / f"{preproc}.png"
        base = read_gray(Path(ok[0]["file"]))
        ctrl_gray = read_gray(ctrl_path) if ctrl_path.exists() else None
        if ctrl_gray is None and kind == "canny":
            print(f"  [warn] 缺控制图 {ctrl_path}，edge_f1 记为 null")
        for r in ok:
            g = read_gray(Path(r["file"]))
            r["delta_vs_strength0"] = mean_abs_diff(base, g)
            if ctrl_gray is not None:
                r["edge_f1"] = edge_f1(ctrl_gray, g)
        contact_sheet(paths, labels, ctx.out / "cn_scan" / f"_sheet_{kind}.png")
    return rows


def exp_ipa_scan(ctx: Ctx) -> dict:
    print(f"\n=== P4-03 IP-Adapter 权重扫描（{ctx.a.ipadapter}） ===")
    rows, paths, labels = [], [], []
    for w in ctx.a.weights:
        prefix = f"p4_ipa_w{str(w).replace('.', '_')}"
        wf = wf_ipadapter(ctx.a.prompt, ctx.a.negative, ctx.ref_name, ctx.a.ipadapter,
                          ctx.a.clip, w, ctx.a.ipa_weight_type, ctx.a.seed, ctx.a.steps,
                          ctx.a.cfg, ctx.a.size, ctx.a.size, prefix)
        r = ctx.client.run(wf, f"ipa@{w}")
        if r["status"] != "ok":
            print(f"  [FAIL] w={w}: {str(r.get('error'))[:220]}")
            rows.append({"weight": w, "status": r["status"],
                         "error": str(r.get("error"))[:400]})
            ctx.failures += 1
            continue
        dest = ctx.out / "ipa_scan" / f"w{str(w).replace('.', '_')}.png"
        for img in r["images"]:
            ctx.client.download(img, dest)
            break
        rows.append({"weight": w, "status": "ok", "elapsed_s": r["elapsed_s"],
                     "file": str(dest)})
        paths.append(dest)
        labels.append(f"w={w}")
        print(f"  [ OK ] w={w} ({r['elapsed_s']}s)")
    if paths:
        contact_sheet(paths, labels, ctx.out / "ipa_scan" / "_sheet.png")
        # 与参考图的相似度（风格/内容贴合度的粗代理）
        ref_gray = read_gray(ctx.ref)
        for r in rows:
            if r["status"] == "ok":
                r["ssim_vs_ref"] = ssim(ref_gray, read_gray(Path(r["file"])))
    ctx.note("p4_03_ipa_scan", rows)
    return {"units": rows}


def exp_joint(ctx: Ctx) -> dict:
    print("\n=== P4-07 两路 ControlNet 联合（canny + depth） ===")
    combos = []
    for wc in (0.6, 0.8):
        for wd in (0.3, 0.5):
            combos.append((wc, wd))
    rows, paths, labels = [], [], []
    for wc, wd in combos:
        prefix = f"p4_joint_c{str(wc).replace('.', '_')}_d{str(wd).replace('.', '_')}"
        cn1 = ("CannyEdgePreprocessor", PREPROCESSORS["CannyEdgePreprocessor"],
               ctx.a.cn_canny, wc)
        cn2 = ("DepthAnythingV2Preprocessor", PREPROCESSORS["DepthAnythingV2Preprocessor"],
               ctx.a.cn_depth, wd)
        wf = wf_joint(ctx.a.prompt, ctx.a.negative, ctx.ref_name, cn1, cn2,
                      ctx.a.seed, ctx.a.steps, ctx.a.cfg, ctx.a.size, ctx.a.size, prefix)
        label = f"c{wc}+d{wd}"
        r = ctx.client.run(wf, label)
        if r["status"] != "ok":
            print(f"  [FAIL] {label}: {str(r.get('error'))[:220]}")
            rows.append({"canny": wc, "depth": wd, "status": r["status"],
                         "error": str(r.get("error"))[:400]})
            ctx.failures += 1
            continue
        dest = ctx.out / "joint" / f"c{str(wc).replace('.', '_')}_d{str(wd).replace('.', '_')}.png"
        for img in r["images"]:
            ctx.client.download(img, dest)
            break
        rows.append({"canny": wc, "depth": wd, "status": "ok", "elapsed_s": r["elapsed_s"],
                     "file": str(dest)})
        paths.append(dest)
        labels.append(label)
        print(f"  [ OK ] {label} ({r['elapsed_s']}s)")
    if paths:
        contact_sheet(paths, labels, ctx.out / "joint" / "_sheet.png")
    ctx.note("p4_07_joint", rows)
    return {"units": rows}


def exp_consistency(ctx: Ctx) -> dict:
    """固定参考图 + 固定 canny 控制，仅变 seed：测结构一致性。"""
    print(f"\n=== P4-09/10 一致性（canny strength={ctx.a.consistency_strength}，N={ctx.a.n}） ===")
    preproc = "CannyEdgePreprocessor"
    outdir = ctx.out / "consistency"
    outdir.mkdir(parents=True, exist_ok=True)
    paths, rows = [], []
    for i in range(ctx.a.n):
        seed = ctx.a.seed + i
        prefix = f"p4_cons_seed{seed}"
        wf = wf_controlnet(ctx.a.prompt, ctx.a.negative, ctx.ref_name, preproc,
                           PREPROCESSORS[preproc], ctx.a.cn_canny, ctx.a.consistency_strength,
                           seed, ctx.a.steps, ctx.a.cfg, ctx.a.size, ctx.a.size, prefix)
        r = ctx.client.run(wf, f"cons@{seed}")
        if r["status"] != "ok":
            print(f"  [FAIL] seed={seed}: {str(r.get('error'))[:200]}")
            rows.append({"seed": seed, "status": r["status"],
                         "error": str(r.get("error"))[:300]})
            ctx.failures += 1
            continue
        dest = outdir / f"seed{seed}.png"
        for img in r["images"]:
            ctx.client.download(img, dest)
            break
        paths.append(dest)
        rows.append({"seed": seed, "status": "ok", "elapsed_s": r["elapsed_s"], "file": str(dest)})
        print(f"  [ OK ] seed={seed} ({r['elapsed_s']}s)")

    # 全对 SSIM：衡量"同商品多图结构一致性"的客观代理
    grays = [read_gray(p) for p in paths]
    pair = []
    for i in range(len(grays)):
        for j in range(i + 1, len(grays)):
            pair.append(ssim(grays[i], grays[j]))
    summary = {
        "n": len(grays),
        "pairs": len(pair),
        "ssim_mean": round(float(np.mean(pair)), 4) if pair else None,
        "ssim_min": round(float(np.min(pair)), 4) if pair else None,
        "ssim_max": round(float(np.max(pair)), 4) if pair else None,
        "ssim_std": round(float(np.std(pair)), 4) if pair else None,
    }
    if paths:
        contact_sheet(paths, [f"s={r['seed']}" for r in rows if r["status"] == "ok"],
                      outdir / "_sheet.png")
    print(f"  pairwise SSIM: mean={summary['ssim_mean']} min={summary['ssim_min']} "
          f"max={summary['ssim_max']} (n={summary['n']})")
    ctx.note("p4_09_consistency", {"summary": summary, "runs": rows})
    return {"units": rows, "summary": summary}


def exp_synth_person(ctx: Ctx) -> dict:
    """P4-06 前置：合成一张「全身人物」参考图。

    动机：本机**没有任何人物素材**（见 control_matrix.md P4-01 行），
    导致 openpose/DW 预处理器只能对着马克杯空转。用自家 t2i 合成可规避第三方素材许可问题。

    可靠性措施：SDXL 常把人物裁成半身 ⇒ 生成 N 个候选，**每个都跑一次 DW 预处理器**，
    取「骨架非黑像素占比」最高者作为人物参考图。选不中（全为空图）则显式失败，不静默兜底。
    """
    print(f"\n=== 合成人物参考图（{ctx.a.person_n} 候选，取骨架占比最高者）===")
    outdir = ctx.out / "person"
    outdir.mkdir(parents=True, exist_ok=True)
    rows, paths, labels = [], [], []
    best = None  # (ratio, cand_path, comfy_input_name)
    for i in range(ctx.a.person_n):
        seed = ctx.a.person_seed + i
        wf = wf_base(PERSON_PROMPT, PERSON_NEGATIVE, seed, ctx.a.person_steps, ctx.a.cfg,
                     ctx.a.person_w, ctx.a.person_h, f"p4_person_{seed}")
        r = ctx.client.run(wf, f"person@{seed}")
        if r["status"] != "ok":
            print(f"  [FAIL] seed={seed}: {str(r.get('error'))[:200]}")
            rows.append({"seed": seed, "status": r["status"],
                         "error": str(r.get("error"))[:400]})
            ctx.failures += 1
            continue
        cand = outdir / f"cand_{seed}.png"
        for img in r["images"]:
            ctx.client.download(img, cand)
            break
        name = ctx.client.upload(cand)
        rp = ctx.client.run(
            wf_preprocess(name, "DWPreprocessor", PREPROCESSORS["DWPreprocessor"],
                          f"p4_person_dw_{seed}"), f"person-dw@{seed}")
        ratio = None
        if rp["status"] == "ok":
            pose = outdir / f"cand_{seed}_dw.png"
            for img in rp["images"]:
                ctx.client.download(img, pose)
                break
            g = read_gray(pose)
            ratio = round(float((g > 16).sum()) / float(g.size), 5)
        rows.append({"seed": seed, "status": "ok", "file": str(cand),
                     "skeleton_nonblack_ratio": ratio, "elapsed_s": r["elapsed_s"],
                     "dw_error": None if rp["status"] == "ok" else str(rp.get("error"))[:300]})
        paths.append(cand)
        labels.append(f"s={seed} dw={ratio}")
        if ratio is not None and (best is None or ratio > best[0]):
            best = (ratio, cand, name)
        print(f"  [ OK ] seed={seed} skeleton_ratio={ratio} ({r['elapsed_s']}s)")
    if paths:
        contact_sheet(paths, labels, outdir / "_sheet_candidates.png")

    if best is None:
        print("  [FAIL] 没有候选检出骨架 ⇒ 合成人物图失败（不兜底）")
        ctx.note("p4_person", {"status": "error", "rows": rows})
        return {"units": rows, "status": "error"}

    ratio, cand, name = best
    ctx.a.person_ref.parent.mkdir(parents=True, exist_ok=True)
    ctx.a.person_ref.write_bytes(cand.read_bytes())
    ctx.person_ref_name = name
    print(f"  → 人物参考图 = {ctx.a.person_ref}（来自 {cand.name}，骨架占比 {ratio}）")
    ctx.note("p4_person", {"status": "ok", "picked_file": str(cand),
                           "picked_skeleton_ratio": ratio,
                           "saved_as": str(ctx.a.person_ref), "rows": rows})
    return {"units": rows, "status": "ok", "skeleton_nonblack_ratio": ratio}


def exp_pose(ctx: Ctx) -> dict:
    """P4-01 姿态预处理器**重评**：人物图 vs 商品图（后者是既有记录里的「空转」对照）。"""
    print("\n=== P4-01 姿态预处理器重评（人物图 vs 商品图）===")
    if not ctx.a.person_ref.exists():
        print(f"  [info] 缺人物参考图 {ctx.a.person_ref} ⇒ 先自动合成")
        exp_synth_person(ctx)
    if not ctx.a.person_ref.exists():
        print("  [FAIL] 人物参考图仍不存在，无法重评姿态预处理器")
        ctx.note("p4_01_pose", {"status": "error",
                                "error": f"缺人物参考图 {ctx.a.person_ref}"})
        ctx.failures += 1
        return {"units": [], "status": "error"}

    person_name = getattr(ctx, "person_ref_name", None) or ctx.client.upload(ctx.a.person_ref)
    outdir = ctx.out / "pose"
    outdir.mkdir(parents=True, exist_ok=True)
    rows = []
    for node in ("OpenposePreprocessor", "DWPreprocessor"):
        extra = PREPROCESSORS[node]
        for tag, ref_name in (("person", person_name), ("product", ctx.ref_name)):
            r = ctx.client.run(wf_preprocess(ref_name, node, extra, f"p4_pose_{node}_{tag}"),
                               f"{node}@{tag}")
            if r["status"] != "ok":
                print(f"  [FAIL] {node}/{tag}: {str(r.get('error'))[:200]}")
                rows.append({"preprocessor": node, "ref": tag, "status": r["status"],
                             "error": str(r.get("error"))[:400]})
                ctx.failures += 1
                continue
            dest = outdir / f"{node}__{tag}.png"
            for img in r["images"]:
                ctx.client.download(img, dest)
                break
            g = read_gray(dest)
            ratio = round(float((g > 16).sum()) / float(g.size), 5)
            rows.append({"preprocessor": node, "ref": tag, "status": "ok",
                         "nonblack_ratio": ratio,
                         "verdict": "skeleton" if ratio >= SKELETON_NONBLACK_MIN else "empty",
                         "elapsed_s": r["elapsed_s"], "file": str(dest)})
            print(f"  [ OK ] {node}/{tag} nonblack={ratio} "
                  f"({'检出骨架' if ratio >= SKELETON_NONBLACK_MIN else '空图'})")
    sheet = [p for p in sorted(outdir.glob("*.png")) if not p.name.startswith("_")]
    if sheet:
        contact_sheet(sheet, [p.stem for p in sheet], outdir / "_sheet.png")
    ctx.note("p4_01_pose", rows)
    return {"units": rows}


def _material_arm_paths(ctx: Ctx, arm: str, use_ipa: bool) -> tuple[list[Path], list[dict]]:
    """跑一组（arm）N 个 seed，返回 (产物路径, 逐次记录)。"""
    outdir = ctx.out / "material" / arm
    outdir.mkdir(parents=True, exist_ok=True)
    paths, rows = [], []
    for i in range(ctx.a.n):
        seed = ctx.a.seed + i
        prefix = f"p4_mat_{arm}_seed{seed}"
        wf = wf_controlnet(ctx.a.prompt, ctx.a.negative, ctx.ref_name, "CannyEdgePreprocessor",
                           PREPROCESSORS["CannyEdgePreprocessor"], ctx.a.cn_canny,
                           ctx.a.material_strength, seed, ctx.a.steps, ctx.a.cfg,
                           ctx.a.size, ctx.a.size, prefix)
        if use_ipa:
            add_ipadapter(wf, ctx.a.ipadapter, ctx.a.clip, ctx.ref_name,
                          ctx.a.material_ipa_weight, ctx.a.ipa_weight_type, node_id="30")
        r = ctx.client.run(wf, f"{arm}@{seed}")
        if r["status"] != "ok":
            print(f"  [FAIL] {arm} seed={seed}: {str(r.get('error'))[:200]}")
            rows.append({"seed": seed, "status": r["status"],
                         "error": str(r.get("error"))[:400]})
            ctx.failures += 1
            continue
        dest = outdir / f"seed{seed}.png"
        for img in r["images"]:
            ctx.client.download(img, dest)
            break
        paths.append(dest)
        rows.append({"seed": seed, "status": "ok", "elapsed_s": r["elapsed_s"], "file": str(dest)})
        print(f"  [ OK ] {arm} seed={seed} ({r['elapsed_s']}s)")
    return paths, rows


def exp_material(ctx: Ctx) -> dict:
    """P4-09/10 材质一致性：**canny-only** vs **canny + IP-Adapter**，仅变 seed。

    回答的问题：2026-09-23 发现「canny 锁得住结构、锁不住材质」，
    那么再叠一层 IP-Adapter（以参考图为材质锚）能不能把材质也锁住？
    两个 arm 的 seed 区间与既有 `p4_09_consistency` **完全一致**，可直接与 0.8333 对比。
    """
    strength = ctx.a.material_strength
    ipa_w = ctx.a.material_ipa_weight
    print(f"\n=== P4-09/10 材质一致性（canny@{strength} vs canny@{strength}+IPA@{ipa_w}, "
          f"N={ctx.a.n}）===")
    arms = {"canny_only": False, "canny_ipa": True}
    summary, sheets, all_rows = {}, {}, {}
    for arm, use_ipa in arms.items():
        print(f"  --- arm={arm} ---")
        paths, rows = _material_arm_paths(ctx, arm, use_ipa)
        all_rows[arm] = rows
        if paths:
            sheets[arm] = paths
            summary[arm] = material_stats(paths)
            contact_sheet(paths, [p.stem for p in paths],
                          ctx.out / "material" / arm / "_sheet.png")
            s = summary[arm]
            print(f"    SSIM mean={s.get('ssim_mean')} · 颜色漂移 ΔE={s.get('color_drift_deltaE')} "
                  f"· 色调距离={s.get('hue_hist_mean_hellinger')}")

    note = (f"canny@{strength} 单路 vs canny@{strength}+IP-Adapter@{ipa_w}；"
            f"seed {ctx.a.seed}…{ctx.a.seed + ctx.a.n - 1}；与 `p4_09_consistency` 同区间。")
    if sheets:
        score_sheet_html(sheets, ctx.out / "material" / "_score_sheet.html", note)
        print(f"  → 人工评分表：{ctx.out / 'material' / '_score_sheet.html'}（分数为空，须人填）")

    ctx.note("p4_09_10_material", {"note": note, "summary": summary, "runs": all_rows})
    return {"units": all_rows, "summary": summary}


# ----------------------------------------------------------------------------
def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--exp", action="append", required=True,
                    choices=["preprocess", "cn-scan", "ipa-scan", "joint", "consistency",
                             "synth-person", "pose", "material", "all"])
    ap.add_argument("--ref", default="/root/autodl-tmp/l4_assets/ref_product.png")
    ap.add_argument("--outdir", default="/root/autodl-tmp/p4_out")
    ap.add_argument("--prompt", default=DEFAULT_PROMPT)
    ap.add_argument("--negative", default=DEFAULT_NEGATIVE)
    ap.add_argument("--seed", type=int, default=20260923)
    ap.add_argument("--steps", type=int, default=20)
    ap.add_argument("--cfg", type=float, default=6.5)
    ap.add_argument("--size", type=int, default=1024)
    ap.add_argument("--weights", default="0.2,0.4,0.6,0.8,1.0")
    ap.add_argument("--n", type=int, default=10)
    ap.add_argument("--preproc", action="append",
                    help="只跑指定预处理器（可重复）；缺省跑全部")
    ap.add_argument("--timeout", type=float, default=600.0)
    ap.add_argument("--cn-kind", default="canny,depth",
                    help="逗号分隔；仅 canny/depth 有对应模型")
    ap.add_argument("--cn-model", default="",
                    help="（兼容旧用法，等价于 --cn-canny）")
    ap.add_argument("--cn-canny", default="controlnet-canny-sdxl-1.0.safetensors")
    ap.add_argument("--cn-depth", default="controlnet-depth-sdxl-1.0.safetensors")
    ap.add_argument("--ipadapter", default="ip-adapter-plus_sdxl_vit-h.safetensors")
    ap.add_argument("--clip", default="CLIP-ViT-H-14-laion2B-s32B-b79K.safetensors")
    ap.add_argument("--ipa-weight-type", default="linear")
    ap.add_argument("--consistency-strength", type=float, default=0.7)
    # --- P4-01 姿态重评（合成人物参考图）---
    ap.add_argument("--person-ref", default="/root/autodl-tmp/l4_assets/ref_person.png",
                    help="人物参考图路径；不存在时由 synth-person 现场合成并写入此处")
    ap.add_argument("--person-n", type=int, default=4, help="合成候选数（取骨架占比最高者）")
    ap.add_argument("--person-seed", type=int, default=20260923)
    ap.add_argument("--person-steps", type=int, default=24)
    ap.add_argument("--person-w", type=int, default=832)
    ap.add_argument("--person-h", type=int, default=1216)
    # --- P4-09/10 材质一致性（canny-only vs canny+IPA）---
    ap.add_argument("--material-strength", type=float, default=0.7, help="两组共用的 canny 权重")
    ap.add_argument("--material-ipa-weight", type=float, default=0.6,
                    help="第二组的 IP-Adapter 权重（第一组为 0，即只有 canny）")
    ap.add_argument("--merge", action="store_true",
                    help="把本次结果并入已有 metrics.json（仅覆盖本次跑的实验）")
    args = ap.parse_args()
    args.weights = [float(x) for x in args.weights.split(",") if x.strip()]
    args.cn_kind = [k.strip() for k in args.cn_kind.split(",") if k.strip()]
    if args.cn_model:
        args.cn_canny = args.cn_model
    args.cn_model_map = {"canny": args.cn_canny, "depth": args.cn_depth}
    args.ref = Path(args.ref)
    args.outdir = Path(args.outdir)
    args.person_ref = Path(args.person_ref)

    if not args.ref.exists():
        print(f"[fatal] 参考图不存在：{args.ref}", file=sys.stderr)
        return 1

    ctx = Ctx(args)
    try:
        st = ctx.client.stats()
        dev = (st.get("devices") or [{}])[0]
        print(f"引擎 {st.get('system', {}).get('comfyui_version')} · {dev.get('name')} · "
              f"vram {(dev.get('vram_total') or 0) / 1024**3:.1f}GiB")
    except Exception as exc:  # noqa: BLE001
        print(f"[fatal] 连不上 ComfyUI {BASE}: {exc}", file=sys.stderr)
        return 1
    print(f"参考图已上传为 ComfyUI 输入：{ctx.ref_name}")

    exps = args.exp
    if "all" in exps:
        exps = ["preprocess", "cn-scan", "ipa-scan", "joint", "consistency",
                "synth-person", "pose", "material"]
    dispatch = {"preprocess": exp_preprocess, "cn-scan": exp_cn_scan,
                "ipa-scan": exp_ipa_scan, "joint": exp_joint,
                "consistency": exp_consistency, "synth-person": exp_synth_person,
                "pose": exp_pose, "material": exp_material}
    for e in exps:
        try:
            dispatch[e](ctx)
        except Exception as exc:  # noqa: BLE001
            print(f"  [FAIL] 实验 {e} 抛异常：{exc}")
            ctx.results[f"{e}__exception"] = repr(exc)

    summary_path = ctx.out / "metrics.json"
    merged = ctx.results
    if args.merge and summary_path.exists():
        old = json.loads(summary_path.read_text())
        merged = old.get("results", {})
        # 本次真正跑过的实验：用新结果覆盖旧键，并清掉对应的旧异常记录
        run_norm = {e.replace("-", "_") for e in exps}
        for k in list(merged):
            is_exc = k.endswith("__exception") and k[: -len("__exception")].replace("-", "_") in run_norm
            if k in ctx.results or is_exc:
                merged.pop(k)
        merged.update(ctx.results)
        print(f"  [merge] 合并旧 metrics.json：{len(old.get('results', {}))} 项 → {len(merged)} 项")
    failures = count_failures(merged)
    summary_path.write_text(json.dumps({
        "ran_at": time.strftime("%Y-%m-%dT%H:%M:%S%z"),
        "ref": str(args.ref), "seed": args.seed, "steps": args.steps, "cfg": args.cfg,
        "size": args.size, "failures": failures, "results": merged,
    }, ensure_ascii=False, indent=2))
    ctx.failures = failures
    print(f"\n=== 指标写入 {summary_path} · 失败单元 {failures} ===")
    return 0 if failures == 0 else 1


if __name__ == "__main__":
    raise SystemExit(main())
