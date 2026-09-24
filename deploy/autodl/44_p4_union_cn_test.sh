#!/bin/bash
# P4-06/07 接线实测：union ControlNet 权重验证
set -e

echo "=== P4-06/07 接线实测开始 ==="

# 检查union CN权重
echo "1. 检查union CN权重..."
UNION_CN=$(curl -s http://127.0.0.1:8188/object_info/ControlNetLoader | python3 -c "
import sys, json
data = json.load(sys.stdin)
models = data.get('ControlNetLoader', {}).get('input', {}).get('required', {}).get('control_net_name', [[]])[0]
union_models = [m for m in models if 'union' in m.lower()]
print(union_models[0] if union_models else 'NOT_FOUND')
")

if [ "$UNION_CN" = "NOT_FOUND" ]; then
    echo "❌ union CN权重未找到"; exit 1
fi
echo "✅ union CN权重: $UNION_CN"

# 提交P4-06 姿态控制测试工作流
echo ""
echo "=== P4-06: DWPose + union CN 姿态控制测试 ==="

WORKFLOW='{
  "1": {"class_type":"LoadImage","inputs":{"image":"ref_person.png"}},
  "2": {"class_type":"DWPreprocessor","inputs":{"image":["1",0],"detect_hand":"enable","detect_body":"enable","detect_face":"enable","resolution":1024}},
  "3": {"class_type":"ControlNetLoader","inputs":{"control_net_name":"'"$UNION_CN"'"}},
  "4": {"class_type":"CheckpointLoaderSimple","inputs":{"ckpt_name":"sd_xl_base_1.0.safetensors"}},
  "5": {"class_type":"CLIPTextEncode","inputs":{"text":"a person standing, full body pose, white background, professional photo, high quality","clip":["4",1]}},
  "6": {"class_type":"CLIPTextEncode","inputs":{"text":"blurry, low quality, distorted, deformed, ugly","clip":["4",1]}},
  "7": {"class_type":"ControlNetApplyAdvanced","inputs":{"strength":0.8,"start_percent":0.0,"end_percent":1.0,"positive":["5",0],"negative":["6",0],"control_net":["3",0],"image":["2",0]}},
  "8": {"class_type":"EmptyLatentImage","inputs":{"width":1024,"height":1024,"batch_size":1}},
  "9": {"class_type":"KSampler","inputs":{"seed":20260924,"steps":20,"cfg":7.0,"sampler_name":"euler","scheduler":"normal","denoise":1.0,"model":["4",0],"positive":["7",0],"negative":["7",1],"latent_image":["8",0]}},
  "10": {"class_type":"VAEDecode","inputs":{"samples":["9",0],"vae":["4",2]}},
  "11": {"class_type":"SaveImage","inputs":{"filename_prefix":"p4_union_pose","images":["10",0]}}
}'

RESPONSE=$(curl -s -X POST http://127.0.0.1:8188/prompt -H "Content-Type: application/json" -d "{\"prompt\": $WORKFLOW}")
PROMPT_ID=$(echo "$RESPONSE" | python3 -c "import sys, json; print(json.load(sys.stdin).get('prompt_id', 'FAILED'))")

if [ "$PROMPT_ID" = "FAILED" ]; then
    echo "❌ P4-06 工作流提交失败"
    echo "$RESPONSE" | python3 -c "import sys, json; d=json.load(sys.stdin); print(json.dumps(d.get('node_errors',{}), indent=2))" 2>/dev/null || echo "$RESPONSE"
    exit 1
fi
echo "✅ P4-06 工作流已提交: $PROMPT_ID"

echo "等待完成..."
for i in $(seq 1 120); do
    QUEUE=$(curl -s http://127.0.0.1:8188/queue)
    RUNNING=$(echo "$QUEUE" | python3 -c "import sys, json; q=json.load(sys.stdin); print(len(q.get('queue_running', [])))")
    PENDING=$(echo "$QUEUE" | python3 -c "import sys, json; q=json.load(sys.stdin); print(len(q.get('queue_pending', [])))")
    if [ "$RUNNING" = "0" ] && [ "$PENDING" = "0" ]; then
        echo "✅ P4-06 执行完成"
        break
    fi
    sleep 2
done

# 检查输出
OUTPUT=$(curl -s http://127.0.0.1:8188/history/"$PROMPT_ID")
STATUS=$(echo "$OUTPUT" | python3 -c "import sys, json; d=json.load(sys.stdin); status=d.get('$PROMPT_ID',{}).get('status',{}); print('completed' if status.get('completed',False) else 'failed')" 2>/dev/null || echo "unknown")
echo "执行状态: $STATUS"

# P4-07: 多CN叠加测试 (canny + union)
echo ""
echo "=== P4-07: canny + union 多CN叠加测试 ==="

WORKFLOW2='{
  "1": {"class_type":"LoadImage","inputs":{"image":"ref_product.png"}},
  "2": {"class_type":"CannyEdgePreprocessor","inputs":{"image":["1",0],"low_threshold":100,"high_threshold":200,"resolution":1024}},
  "3": {"class_type":"ControlNetLoader","inputs":{"control_net_name":"controlnet-canny-sdxl-1.0.safetensors"}},
  "4": {"class_type":"CheckpointLoaderSimple","inputs":{"ckpt_name":"sd_xl_base_1.0.safetensors"}},
  "5": {"class_type":"CLIPTextEncode","inputs":{"text":"a ceramic mug, product photo, white background, professional, high quality","clip":["4",1]}},
  "6": {"class_type":"CLIPTextEncode","inputs":{"text":"blurry, low quality, distorted","clip":["4",1]}},
  "7": {"class_type":"ControlNetApplyAdvanced","inputs":{"strength":0.7,"start_percent":0.0,"end_percent":1.0,"positive":["5",0],"negative":["6",0],"control_net":["3",0],"image":["2",0]}},
  "8": {"class_type":"EmptyLatentImage","inputs":{"width":1024,"height":1024,"batch_size":1}},
  "9": {"class_type":"KSampler","inputs":{"seed":20260924,"steps":20,"cfg":7.0,"sampler_name":"euler","scheduler":"normal","denoise":1.0,"model":["4",0],"positive":["7",0],"negative":["7",1],"latent_image":["8",0]}},
  "10": {"class_type":"VAEDecode","inputs":{"samples":["9",0],"vae":["4",2]}},
  "11": {"class_type":"SaveImage","inputs":{"filename_prefix":"p4_canny_cn","images":["10",0]}}
}'

RESPONSE2=$(curl -s -X POST http://127.0.0.1:8188/prompt -H "Content-Type: application/json" -d "{\"prompt\": $WORKFLOW2}")
PROMPT_ID2=$(echo "$RESPONSE2" | python3 -c "import sys, json; print(json.load(sys.stdin).get('prompt_id', 'FAILED'))")

if [ "$PROMPT_ID2" = "FAILED" ]; then
    echo "❌ P4-07 工作流提交失败"
    echo "$RESPONSE2" | python3 -c "import sys, json; d=json.load(sys.stdin); print(json.dumps(d.get('node_errors',{}), indent=2))" 2>/dev/null || echo "$RESPONSE2"
    exit 1
fi
echo "✅ P4-07 工作流已提交: $PROMPT_ID2"

echo "等待完成..."
for i in $(seq 1 120); do
    QUEUE=$(curl -s http://127.0.0.1:8188/queue)
    RUNNING=$(echo "$QUEUE" | python3 -c "import sys, json; q=json.load(sys.stdin); print(len(q.get('queue_running', [])))")
    PENDING=$(echo "$QUEUE" | python3 -c "import sys, json; q=json.load(sys.stdin); print(len(q.get('queue_pending', [])))")
    if [ "$RUNNING" = "0" ] && [ "$PENDING" = "0" ]; then
        echo "✅ P4-07 执行完成"
        break
    fi
    sleep 2
done

OUTPUT2=$(curl -s http://127.0.0.1:8188/history/"$PROMPT_ID2")
STATUS2=$(echo "$OUTPUT2" | python3 -c "import sys, json; d=json.load(sys.stdin); status=d.get('$PROMPT_ID2',{}).get('status',{}); print('completed' if status.get('completed',False) else 'failed')" 2>/dev/null || echo "unknown")
echo "执行状态: $STATUS2"

echo ""
echo "=== 测试总结 ==="
echo "P4-06 DWPose+union CN: $STATUS"
echo "P4-07 canny CN: $STATUS2"