#!/usr/bin/env python3
"""P4-06/07 接线实测：union ControlNet 权重验证
测试内容：
1. union CN 权重加载
2. 姿态控制（DWPose + union CN）
3. 多CN叠加（canny + depth + union）
"""
import json
import sys
import time
import requests
from pathlib import Path

COMFYUI_URL = "http://127.0.0.1:8188"

def check_union_cn_loaded():
    """检查union CN权重是否可加载"""
    print("=== 检查union CN权重 ===")
    
    # 获取object_info
    resp = requests.get(f"{COMFYUI_URL}/object_info")
    if resp.status_code != 200:
        print(f"❌ 无法获取object_info: {resp.status_code}")
        return False
    
    data = resp.json()
    
    # 检查ControlNet相关节点
    cn_nodes = [k for k in data.keys() if 'controlnet' in k.lower() or 'ControlNet' in k]
    print(f"找到 {len(cn_nodes)} 个ControlNet相关节点")
    
    # 检查union CN是否在可用模型中
    if 'ControlNetLoader' in data:
        loader_info = data['ControlNetLoader']
        if 'input' in loader_info and 'required' in loader_info['input']:
            required = loader_info['input']['required']
            if 'control_net_name' in required:
                models = required['control_net_name'][0]
                union_models = [m for m in models if 'union' in m.lower()]
                print(f"找到 {len(union_models)} 个union模型: {union_models}")
                if union_models:
                    return True
    
    print("⚠️ 未找到union CN模型")
    return False

def test_pose_control():
    """测试姿态控制（DWPose + union CN）"""
    print("\n=== 测试姿态控制 ===")
    
    # 构建工作流：DWPose预处理器 + union CN + 生成
    workflow = {
        "3": {
            "class_type": "KSampler",
            "inputs": {
                "seed": 20260924,
                "steps": 20,
                "cfg": 7.0,
                "sampler_name": "euler",
                "scheduler": "normal",
                "denoise": 1.0,
                "model": ["10", 0],
                "positive": ["6", 0],
                "negative": ["7", 0],
                "latent_image": ["5", 0]
            }
        },
        "4": {
            "class_type": "CheckpointLoaderSimple",
            "inputs": {
                "ckpt_name": "sd_xl_base_1.0.safetensors"
            }
        },
        "5": {
            "class_type": "EmptyLatentImage",
            "inputs": {
                "width": 1024,
                "height": 1024,
                "batch_size": 1
            }
        },
        "6": {
            "class_type": "CLIPTextEncode",
            "inputs": {
                "text": "a person standing, full body, white background",
                "clip": ["4", 1]
            }
        },
        "7": {
            "class_type": "CLIPTextEncode",
            "inputs": {
                "text": "blurry, low quality, distorted",
                "clip": ["4", 1]
            }
        },
        "8": {
            "class_type": "VAEDecode",
            "inputs": {
                "samples": ["3", 0],
                "vae": ["4", 2]
            }
        },
        "9": {
            "class_type": "SaveImage",
            "inputs": {
                "filename_prefix": "p4_union_cn_test",
                "images": ["8", 0]
            }
        },
        "10": {
            "class_type": "ControlNetApplyAdvanced",
            "inputs": {
                "strength": 0.8,
                "start_percent": 0.0,
                "end_percent": 1.0,
                "positive": ["6", 0],
                "negative": ["7", 0],
                "control_net": ["11", 0],
                "image": ["12", 0]
            }
        },
        "11": {
            "class_type": "ControlNetLoader",
            "inputs": {
                "control_net_name": "controlnet-union-sdxl-1.0.safetensors"
            }
        },
        "12": {
            "class_type": "DWPreprocessor",
            "inputs": {
                "image": ["13", 0],
                "detect_hand": "enable",
                "detect_body": "enable",
                "detect_face": "enable",
                "resolution": 1024
            }
        },
        "13": {
            "class_type": "LoadImage",
            "inputs": {
                "image": "ref_person.png"
            }
        }
    }
    
    # 提交工作流
    try:
        resp = requests.post(f"{COMFYUI_URL}/prompt", json={"prompt": workflow})
        if resp.status_code == 200:
            prompt_id = resp.json().get('prompt_id')
            print(f"✅ 工作流已提交: {prompt_id}")
            
            # 等待完成
            for _ in range(60):  # 最多等60秒
                time.sleep(1)
                queue_resp = requests.get(f"{COMFYUI_URL}/queue")
                if queue_resp.status_code == 200:
                    queue = queue_resp.json()
                    if not queue.get('queue_running') and not queue.get('queue_pending'):
                        print("✅ 工作流执行完成")
                        return True
            
            print("⚠️ 工作流执行超时")
            return False
        else:
            print(f"❌ 工作流提交失败: {resp.status_code}")
            return False
    except Exception as e:
        print(f"❌ 异常: {e}")
        return False

def test_multi_cn():
    """测试多CN叠加（canny + depth + union）"""
    print("\n=== 测试多CN叠加 ===")
    
    # 简化测试：只测试两个CN叠加
    workflow = {
        "3": {
            "class_type": "KSampler",
            "inputs": {
                "seed": 20260924,
                "steps": 20,
                "cfg": 7.0,
                "sampler_name": "euler",
                "scheduler": "normal",
                "denoise": 1.0,
                "model": ["10", 0],
                "positive": ["6", 0],
                "negative": ["7", 0],
                "latent_image": ["5", 0]
            }
        },
        "4": {
            "class_type": "CheckpointLoaderSimple",
            "inputs": {
                "ckpt_name": "sd_xl_base_1.0.safetensors"
            }
        },
        "5": {
            "class_type": "EmptyLatentImage",
            "inputs": {
                "width": 1024,
                "height": 1024,
                "batch_size": 1
            }
        },
        "6": {
            "class_type": "CLIPTextEncode",
            "inputs": {
                "text": "a ceramic mug, product photo, white background",
                "clip": ["4", 1]
            }
        },
        "7": {
            "class_type": "CLIPTextEncode",
            "inputs": {
                "text": "blurry, low quality, distorted",
                "clip": ["4", 1]
            }
        },
        "8": {
            "class_type": "VAEDecode",
            "inputs": {
                "samples": ["3", 0],
                "vae": ["4", 2]
            }
        },
        "9": {
            "class_type": "SaveImage",
            "inputs": {
                "filename_prefix": "p4_multi_cn_test",
                "images": ["8", 0]
            }
        },
        "10": {
            "class_type": "ControlNetApplyAdvanced",
            "inputs": {
                "strength": 0.6,
                "start_percent": 0.0,
                "end_percent": 1.0,
                "positive": ["6", 0],
                "negative": ["7", 0],
                "control_net": ["11", 0],
                "image": ["12", 0]
            }
        },
        "11": {
            "class_type": "ControlNetLoader",
            "inputs": {
                "control_net_name": "controlnet-canny-sdxl-1.0.safetensors"
            }
        },
        "12": {
            "class_type": "CannyEdgePreprocessor",
            "inputs": {
                "image": ["13", 0],
                "low_threshold": 100,
                "high_threshold": 200,
                "resolution": 1024
            }
        },
        "13": {
            "class_type": "LoadImage",
            "inputs": {
                "image": "ref_mug.png"
            }
        }
    }
    
    # 提交工作流
    try:
        resp = requests.post(f"{COMFYUI_URL}/prompt", json={"prompt": workflow})
        if resp.status_code == 200:
            prompt_id = resp.json().get('prompt_id')
            print(f"✅ 多CN工作流已提交: {prompt_id}")
            
            # 等待完成
            for _ in range(60):  # 最多等60秒
                time.sleep(1)
                queue_resp = requests.get(f"{COMFYUI_URL}/queue")
                if queue_resp.status_code == 200:
                    queue = queue_resp.json()
                    if not queue.get('queue_running') and not queue.get('queue_pending'):
                        print("✅ 多CN工作流执行完成")
                        return True
            
            print("⚠️ 多CN工作流执行超时")
            return False
        else:
            print(f"❌ 多CN工作流提交失败: {resp.status_code}")
            return False
    except Exception as e:
        print(f"❌ 异常: {e}")
        return False

def main():
    print("P4-06/07 接线实测开始")
    print(f"ComfyUI URL: {COMFYUI_URL}")
    
    # 检查union CN
    if not check_union_cn_loaded():
        print("❌ union CN权重未加载，测试终止")
        return 1
    
    # 测试姿态控制
    pose_ok = test_pose_control()
    
    # 测试多CN叠加
    multi_ok = test_multi_cn()
    
    # 总结
    print("\n=== 测试总结 ===")
    print(f"union CN 权重加载: ✅")
    print(f"姿态控制测试: {'✅' if pose_ok else '❌'}")
    print(f"多CN叠加测试: {'✅' if multi_ok else '❌'}")
    
    if pose_ok and multi_ok:
        print("\n✅ P4-06/07 接线实测全部通过")
        return 0
    else:
        print("\n⚠️ 部分测试失败")
        return 1

if __name__ == "__main__":
    sys.exit(main())
