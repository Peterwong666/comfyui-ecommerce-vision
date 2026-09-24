---
license: openrail++
base_model: runwayml/stable-diffusion-v1-5
tags:
- stable-diffusion-xl
- stable-diffusion-xl-diffusers
- text-to-image
- diffusers
inference: false
---
    
# SDXL-controlnet: Canny

These are controlnet weights trained on [stabilityai/stable-diffusion-xl-base-1.0](https://huggingface.co/stabilityai/stable-diffusion-xl-base-1.0) with canny conditioning. You can find some example images in the following. 

prompt: a couple watching a romantic sunset, 4k photo
![images_0)](./out_couple.png)

prompt: ultrarealistic shot of a furry blue bird
![images_1)](./out_bird.png)

prompt: a woman, close up, detailed, beautiful, street photography, photorealistic, detailed, Kodak ektar 100, natural, candid shot
![images_2)](./out_women.png)

prompt: Cinematic, neoclassical table in the living room, cinematic, contour, lighting, highly detailed, winter, golden hour
![images_3)](./out_room.png)

prompt: a tornado hitting grass field, 1980's film grain. overcast, muted colors.
![images_0)](./out_tornado.png)

## Usage

Make sure to first install the libraries:

```bash
pip install accelerate transformers safetensors opencv-python diffusers
```

And then we're ready to go:

```python
from diffusers import ControlNetModel, StableDiffusionXLControlNetPipeline, AutoencoderKL
from diffusers.utils import load_image
from PIL import Image
import torch
import numpy as np
import cv2

prompt = "aerial view, a futuristic research complex in a bright foggy jungle, hard lighting"
negative_prompt = 'low quality, bad quality, sketches'

image = load_image("https://huggingface.co/datasets/hf-internal-testing/diffusers-images/resolve/main/sd_controlnet/hf-logo.png")

controlnet_conditioning_scale = 0.5  # recommended for good generalization

controlnet = ControlNetModel.from_pretrained(
    "diffusers/controlnet-canny-sdxl-1.0",
    torch_dtype=torch.float16
)
vae = AutoencoderKL.from_pretrained("madebyollin/sdxl-vae-fp16-fix", torch_dtype=torch.float16)
pipe = StableDiffusionXLControlNetPipeline.from_pretrained(
    "stabilityai/stable-diffusion-xl-base-1.0",
    controlnet=controlnet,
    vae=vae,
    torch_dtype=torch.float16,
)
pipe.enable_model_cpu_offload()

image = np.array(image)
image = cv2.Canny(image, 100, 200)
image = image[:, :, None]
image = np.concatenate([image, image, image], axis=2)
image = Image.fromarray(image)

images = pipe(
    prompt, negative_prompt=negative_prompt, image=image, controlnet_conditioning_scale=controlnet_conditioning_scale,
    ).images

images[0].save(f"hug_lab.png")
```

![images_10)](./out_hug_lab_7.png)

To more details, check out the official documentation of [`StableDiffusionXLControlNetPipeline`](https://huggingface.co/docs/diffusers/main/en/api/pipelines/controlnet_sdxl).

### Training

Our training script was built on top of the official training script that we provide [here](https://github.com/huggingface/diffusers/blob/main/examples/controlnet/README_sdxl.md). 

#### Training data
This checkpoint was first trained for 20,000 steps on laion 6a resized to a max minimum dimension of 384. 
It was then further trained for 20,000 steps on laion 6a resized to a max minimum dimension of 1024 and 
then filtered to contain only minimum 1024 images. We found the further high resolution finetuning was 
necessary for image quality.

#### Compute
one 8xA100 machine

#### Batch size
Data parallel with a single gpu batch size of 8 for a total batch size of 64.

#### Hyper Parameters
Constant learning rate of 1e-4 scaled by batch size for total learning rate of 64e-4

#### Mixed precision
fp16