
---
license: openrail++
base_model: stabilityai/stable-diffusion-xl-base-1.0
tags:
- stable-diffusion-xl
- stable-diffusion-xl-diffusers
- text-to-image
- diffusers
- controlnet
inference: false
---
    
# SDXL-controlnet: Depth

These are controlnet weights trained on stabilityai/stable-diffusion-xl-base-1.0 with depth conditioning. You can find some example images in the following. 

prompt: spiderman lecture, photorealistic
![images_0)](./spiderman.png)

## Usage

Make sure to first install the libraries:

```bash
pip install accelerate transformers safetensors diffusers
```

And then we're ready to go:

```python
import torch
import numpy as np
from PIL import Image

from transformers import DPTFeatureExtractor, DPTForDepthEstimation
from diffusers import ControlNetModel, StableDiffusionXLControlNetPipeline, AutoencoderKL
from diffusers.utils import load_image


depth_estimator = DPTForDepthEstimation.from_pretrained("Intel/dpt-hybrid-midas").to("cuda")
feature_extractor = DPTFeatureExtractor.from_pretrained("Intel/dpt-hybrid-midas")
controlnet = ControlNetModel.from_pretrained(
    "diffusers/controlnet-depth-sdxl-1.0",
    variant="fp16",
    use_safetensors=True,
    torch_dtype=torch.float16,
)
vae = AutoencoderKL.from_pretrained("madebyollin/sdxl-vae-fp16-fix", torch_dtype=torch.float16)
pipe = StableDiffusionXLControlNetPipeline.from_pretrained(
    "stabilityai/stable-diffusion-xl-base-1.0",
    controlnet=controlnet,
    vae=vae,
    variant="fp16",
    use_safetensors=True,
    torch_dtype=torch.float16,
)
pipe.enable_model_cpu_offload()

def get_depth_map(image):
    image = feature_extractor(images=image, return_tensors="pt").pixel_values.to("cuda")
    with torch.no_grad(), torch.autocast("cuda"):
        depth_map = depth_estimator(image).predicted_depth

    depth_map = torch.nn.functional.interpolate(
        depth_map.unsqueeze(1),
        size=(1024, 1024),
        mode="bicubic",
        align_corners=False,
    )
    depth_min = torch.amin(depth_map, dim=[1, 2, 3], keepdim=True)
    depth_max = torch.amax(depth_map, dim=[1, 2, 3], keepdim=True)
    depth_map = (depth_map - depth_min) / (depth_max - depth_min)
    image = torch.cat([depth_map] * 3, dim=1)

    image = image.permute(0, 2, 3, 1).cpu().numpy()[0]
    image = Image.fromarray((image * 255.0).clip(0, 255).astype(np.uint8))
    return image


prompt = "stormtrooper lecture, photorealistic"
image = load_image("https://huggingface.co/lllyasviel/sd-controlnet-depth/resolve/main/images/stormtrooper.png")
controlnet_conditioning_scale = 0.5  # recommended for good generalization

depth_image = get_depth_map(image)

images = pipe(
    prompt, image=depth_image, num_inference_steps=30, controlnet_conditioning_scale=controlnet_conditioning_scale,
).images
images[0]

images[0].save(f"stormtrooper.png")
```

For more details, check out the official documentation of [`StableDiffusionXLControlNetPipeline`](https://huggingface.co/docs/diffusers/main/en/api/pipelines/controlnet_sdxl).

### Training

Our training script was built on top of the official training script that we provide [here](https://github.com/huggingface/diffusers/blob/main/examples/controlnet/README_sdxl.md). 

#### Training data and Compute
The model is trained on 3M image-text pairs from LAION-Aesthetics V2. The model is trained for 700 GPU hours on 80GB A100 GPUs.

#### Batch size
Data parallel with a single GPU batch size of 8 for a total batch size of 256.

#### Hyper Parameters
The constant learning rate of 1e-5.

#### Mixed precision
fp16