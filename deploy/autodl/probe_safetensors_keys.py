#!/usr/bin/env python3
# ============================================================
# probe_safetensors_keys.py —— 只读 safetensors 头部，识别模型包含哪些组件
# 用途：下载前判断是否含 VAE / text encoder，避免下完发现缺组件
# 代价：仅拉取前若干 MB，不下载整个权重
# ============================================================
import json
import struct
import sys
import urllib.request

PROBE_BYTES = 8 * 1024 * 1024  # safetensors 头通常远小于此


def probe(url):
    req = urllib.request.Request(url, headers={"Range": f"bytes=0-{PROBE_BYTES}"})
    with urllib.request.urlopen(req, timeout=90) as r:
        data = r.read()
    header_len = struct.unpack("<Q", data[:8])[0]
    header = json.loads(data[8 : 8 + header_len])
    header.pop("__metadata__", None)
    return header


def summarize(name, header):
    top = {}
    for k in header:
        seg = k.split(".")[0]
        top[seg] = top.get(seg, 0) + 1

    joined = " ".join(header.keys()).lower()
    has_vae = any(s in joined for s in ("vae", "decoder.conv", "post_quant_conv"))
    has_clip = "text_model" in joined or "clip" in joined or "encoder.block" in joined

    print(f"--- {name} ---")
    print(f"  tensor 数: {len(header)}")
    print("  主要前缀:", ", ".join(f"{k}({v})" for k, v in
                                 list(sorted(top.items(), key=lambda x: -x[1]))[:8]))
    print(f"  疑似含 VAE : {'是' if has_vae else '否'}")
    print(f"  疑似含文本编码器: {'是' if has_clip else '否'}")
    return has_vae, has_clip


if __name__ == "__main__":
    for url in sys.argv[1:]:
        name = url.rsplit("/", 1)[-1]
        try:
            summarize(name, probe(url))
        except Exception as e:
            print(f"--- {name} --- 探测失败: {type(e).__name__}: {e}")
