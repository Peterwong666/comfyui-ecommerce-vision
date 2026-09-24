---
license: apache-2.0
tags:
- comfyui
- diffusion-single-file
base_model:
- black-forest-labs/FLUX.2-klein-4B
- black-forest-labs/FLUX.2-klein-base-4B
---

# Flux Klein 4B

Repackaged model files for ComfyUI.

Original model repository:

- https://huggingface.co/black-forest-labs/FLUX.2-klein-4B
- https://huggingface.co/black-forest-labs/FLUX.2-klein-base-4B

Place the files in the following folders:

```
📂 ComfyUI/
├── 📂 models/
│   ├── 📂 diffusion_models/
│   │   ├── flux-2-klein-4b.safetensors
│   │   └── flux-2-klein-base-4b.safetensors
│   ├── 📂 text_encoders/
│   │   ├── qwen_3_4b.safetensors
│   │   └── qwen_3_4b_fp4_flux2.safetensors
│   ├── 📂 vae/
│   │   └── flux2-vae.safetensors
```
