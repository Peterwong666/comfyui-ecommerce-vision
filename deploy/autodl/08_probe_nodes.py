#!/usr/bin/env python3
# ============================================================
# 08_probe_nodes.py —— 查询运行中 ComfyUI 的可用节点与入参
#
# 用途：在跑工作流之前先确认节点类型存在、入参名正确。
#       本项目 flux2 支持为半成品（见 项目进展.md #13），
#       盲跑工作流会浪费 GPU 机时，故先探测再提交。
#
# 用法：
#   python 08_probe_nodes.py [关键字]        # 列出匹配的节点类名
#   python 08_probe_nodes.py -d <类名>       # 打印该节点的完整入参 schema
# ============================================================
import json
import sys
import urllib.request

BASE = "http://127.0.0.1:8188"


def fetch_object_info():
    with urllib.request.urlopen(f"{BASE}/object_info", timeout=120) as r:
        return json.loads(r.read().decode("utf-8"))


def main():
    info = fetch_object_info()
    print(f"[info] 共 {len(info)} 个节点类")

    if len(sys.argv) >= 3 and sys.argv[1] == "-d":
        name = sys.argv[2]
        if name not in info:
            print(f"[缺失] 节点 {name} 不存在")
            return 1
        node = info[name]
        req = node.get("input", {}).get("required", {})
        opt = node.get("input", {}).get("optional", {})
        print(f"\n=== {name} ===")
        print(f"category: {node.get('category')}  output: {node.get('output')}")
        for label, group in (("required", req), ("optional", opt)):
            if not group:
                continue
            print(f"-- {label} --")
            for k, v in group.items():
                t = v[0] if v else "?"
                if isinstance(t, list):
                    t = f"可选{len(t)}项: {t[:6]}{'...' if len(t) > 6 else ''}"
                extra = v[1] if len(v) > 1 else {}
                default = extra.get("default") if isinstance(extra, dict) else None
                print(f"   {k:20s} {t}   default={default}")
        return 0

    pattern = sys.argv[1] if len(sys.argv) > 1 else ""
    hits = [k for k in sorted(info) if pattern.lower() in k.lower()]
    print(f"[match '{pattern}'] {len(hits)} 个：")
    for k in hits:
        print("  ", k)
    return 0


if __name__ == "__main__":
    sys.exit(main())
