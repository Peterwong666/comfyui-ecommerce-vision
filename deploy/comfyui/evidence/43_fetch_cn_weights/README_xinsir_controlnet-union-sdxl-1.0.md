---
license: apache-2.0
tags:
- Text-to-Image
- ControlNet
- Diffusers
- Stable Diffusion
pipeline_tag: text-to-image
---

# **ControlNet++: All-in-one ControlNet for image generations and editing!**
## **ProMax Model has released!! 12 control + 5 advanced editing, just try it!!!**
![images_display](./images/masonry.webp)

## Network Arichitecture
![images](./images/ControlNet++.png)

## Advantages about the model
- Use bucket training like novelai, can generate high resolutions images of any aspect ratio
- Use large amount of high quality data(over 10000000 images), the dataset covers a diversity of situation
- Use re-captioned prompt like DALLE.3, use CogVLM to generate detailed description, good prompt following ability
- Use many useful tricks during training. Including but not limited to date augmentation, mutiple loss, multi resolution
- Use almost the same parameter compared with original ControlNet. No obvious increase in network parameter or computation.
- Support 10+ control conditions, no obvious performance drop on any single condition compared with training independently
- Support multi condition generation, condition fusion is learned during training. No need to set hyperparameter or design prompts.
- Compatible with other opensource SDXL models, such as BluePencilXL, CounterfeitXL. Compatible with other Lora models.


***We design a new architecture that can support 10+ control types in condition text-to-image generation and can generate high resolution images visually comparable with 
midjourney***. The network is based on the original ControlNet architecture, we propose two new modules to: 1 Extend the original ControlNet to support different image 
conditions using the same network parameter. 2 Support multiple conditions input without increasing computation offload, which is especially important for designers 
who want to edit image in detail, different conditions use the same condition encoder, without adding extra computations or parameters. We do thoroughly experiments 
on SDXL and achieve superior performance both in control ability and aesthetic score. We release the method and the model to the open source community to make everyone 
can enjoy it.  

Inference scripts and more details can found: https://github.com/xinsir6/ControlNetPlus/tree/main

**If you find it useful, please give me a star, thank you very much**

**SDXL ProMax version has been released!!!，Enjoy it!!!**  

**I am sorry that because of the project's revenue and expenditure are difficult to balance, the GPU resources are assigned to other projects that are more likely to be profitable, the SD3 trainging is stopped until I find enough GPU supprt, I will try my best to find GPUs to continue training. If this brings you inconvenience, I sincerely apologize for that. I want to thank everyone who likes this project, your support is what keeps me going**

Note: we put the promax model with a promax suffix in the same [huggingface model repo](https://huggingface.co/xinsir/controlnet-union-sdxl-1.0), detailed instructions will be added later. 
## Advanced editing features in Promax Model
### Tile Deblur
![blur0](./images/100000_tile_blur_concat.webp)
![blur1](./images/100001_tile_blur_concat.webp)
![blur2](./images/100002_tile_blur_concat.webp)
![blur3](./images/100003_tile_blur_concat.webp)
![blur4](./images/100004_tile_blur_concat.webp)
![blur5](./images/100005_tile_blur_concat.webp)
### Tile variation
![var0](./images/100006_tile_var_concat.webp)
![var1](./images/100007_tile_var_concat.webp)
![var2](./images/100008_tile_var_concat.webp)
![var3](./images/100009_tile_var_concat.webp)
![var4](./images/100010_tile_var_concat.webp)
![var5](./images/100011_tile_var_concat.webp)

### Tile Super Resolution
Following example show from 1M resolution --> 9M resolution
<div style="display: flex; justify-content: space-between;">
  <img src="./images/tile_super1.webp" alt="Image 1" style="width: 49%; margin: 1%;">
  <img src="./images/tile_super1_9upscale.webp" alt="Image 2" style="width: 49%; margin: 1%;">
</div>

<div style="display: flex; justify-content: space-between;">
  <img src="./images/tile_super2.webp" alt="Image 1" style="width: 49%; margin: 1%;">
  <img src="./images/tile_super2_9upscale.webp" alt="Image 2" style="width: 49%; margin: 1%;">
</div>

### Image Inpainting
![inp0](./images/100018_inpainting_concat.webp)
![inp1](./images/100019_inpainting_concat.webp)
![inp2](./images/100020_inpainting_concat.webp)
![inp3](./images/100021_inpainting_concat.webp)
![inp4](./images/100022_inpainting_concat.webp)
![inp5](./images/100023_inpainting_concat.webp)

### Image Outpainting
![oup0](./images/100012_outpainting_concat.webp)
![oup1](./images/100013_outpainting_concat.webp)
![oup2](./images/100014_outpainting_concat.webp)
![oup3](./images/100015_outpainting_concat.webp)
![oup4](./images/100016_outpainting_concat.webp)
![oup5](./images/100017_outpainting_concat.webp)


## Visual Examples
### Openpose
![pose0](./images/000000_pose_concat.webp)
![pose1](./images/000001_pose_concat.webp)
![pose2](./images/000002_pose_concat.webp)
![pose3](./images/000003_pose_concat.webp)
![pose4](./images/000004_pose_concat.webp)
### Depth
![depth0](./images/000005_depth_concat.webp)
![depth1](./images/000006_depth_concat.webp)
![depth2](./images/000007_depth_concat.webp)
![depth3](./images/000008_depth_concat.webp)
![depth4](./images/000009_depth_concat.webp)
### Canny
![canny0](./images/000010_canny_concat.webp)
![canny1](./images/000011_canny_concat.webp)
![canny2](./images/000012_canny_concat.webp)
![canny3](./images/000013_canny_concat.webp)
![canny4](./images/000014_canny_concat.webp)
### Lineart
![lineart0](./images/000015_lineart_concat.webp)
![lineart1](./images/000016_lineart_concat.webp)
![lineart2](./images/000017_lineart_concat.webp)
![lineart3](./images/000018_lineart_concat.webp)
![lineart4](./images/000019_lineart_concat.webp)
### AnimeLineart
![animelineart0](./images/000020_anime_lineart_concat.webp)
![animelineart1](./images/000021_anime_lineart_concat.webp)
![animelineart2](./images/000022_anime_lineart_concat.webp)
![animelineart3](./images/000023_anime_lineart_concat.webp)
![animelineart4](./images/000024_anime_lineart_concat.webp)
### Mlsd
![mlsd0](./images/000025_mlsd_concat.webp)
![mlsd1](./images/000026_mlsd_concat.webp)
![mlsd2](./images/000027_mlsd_concat.webp)
![mlsd3](./images/000028_mlsd_concat.webp)
![mlsd4](./images/000029_mlsd_concat.webp)
### Scribble
![scribble0](./images/000030_scribble_concat.webp)
![scribble1](./images/000031_scribble_concat.webp)
![scribble2](./images/000032_scribble_concat.webp)
![scribble3](./images/000033_scribble_concat.webp)
![scribble4](./images/000034_scribble_concat.webp)
### Hed
![hed0](./images/000035_hed_concat.webp)
![hed1](./images/000036_hed_concat.webp)
![hed2](./images/000037_hed_concat.webp)
![hed3](./images/000038_hed_concat.webp)
![hed4](./images/000039_hed_concat.webp)
### Pidi(Softedge)
![pidi0](./images/000040_softedge_concat.webp)
![pidi1](./images/000041_softedge_concat.webp)
![pidi2](./images/000042_softedge_concat.webp)
![pidi3](./images/000043_softedge_concat.webp)
![pidi4](./images/000044_softedge_concat.webp)
### Teed
![ted0](./images/000045_ted_concat.webp)
![ted1](./images/000046_ted_concat.webp)
![ted2](./images/000047_ted_concat.webp)
![ted3](./images/000048_ted_concat.webp)
![ted4](./images/000049_ted_concat.webp)
### Segment
![segment0](./images/000050_seg_concat.webp)
![segment1](./images/000051_seg_concat.webp)
![segment2](./images/000052_seg_concat.webp)
![segment3](./images/000053_seg_concat.webp)
![segment4](./images/000054_seg_concat.webp)
### Normal
![normal0](./images/000055_normal_concat.webp)
![normal1](./images/000056_normal_concat.webp)
![normal2](./images/000057_normal_concat.webp)
![normal3](./images/000058_normal_concat.webp)
![normal4](./images/000059_normal_concat.webp)

## Multi Control Visual Examples
### Openpose + Canny
![pose_canny0](./images/000007_openpose_canny_concat.webp)
![pose_canny1](./images/000008_openpose_canny_concat.webp)
![pose_canny2](./images/000009_openpose_canny_concat.webp)
![pose_canny3](./images/000010_openpose_canny_concat.webp)
![pose_canny4](./images/000011_openpose_canny_concat.webp)
![pose_canny5](./images/000012_openpose_canny_concat.webp)

### Openpose + Depth
![pose_depth0](./images/000013_openpose_depth_concat.webp)
![pose_depth1](./images/000014_openpose_depth_concat.webp)
![pose_depth2](./images/000015_openpose_depth_concat.webp)
![pose_depth3](./images/000016_openpose_depth_concat.webp)
![pose_depth4](./images/000017_openpose_depth_concat.webp)
![pose_depth5](./images/000018_openpose_depth_concat.webp)

### Openpose + Scribble
![pose_scribble0](./images/000001_openpose_scribble_concat.webp)
![pose_scribble1](./images/000002_openpose_scribble_concat.webp)
![pose_scribble2](./images/000003_openpose_scribble_concat.webp)
![pose_scribble3](./images/000004_openpose_scribble_concat.webp)
![pose_scribble4](./images/000005_openpose_scribble_concat.webp)
![pose_scribble5](./images/000006_openpose_scribble_concat.webp)

### Openpose + Normal
![pose_normal0](./images/000019_openpose_normal_concat.webp)
![pose_normal1](./images/000020_openpose_normal_concat.webp)
![pose_normal2](./images/000021_openpose_normal_concat.webp)
![pose_normal3](./images/000022_openpose_normal_concat.webp)
![pose_normal4](./images/000023_openpose_normal_concat.webp)
![pose_normal5](./images/000024_openpose_normal_concat.webp)

### Openpose + Segment
![pose_segment0](./images/000025_openpose_sam_concat.webp)
![pose_segment1](./images/000026_openpose_sam_concat.webp)
![pose_segment2](./images/000027_openpose_sam_concat.webp)
![pose_segment3](./images/000028_openpose_sam_concat.webp)
![pose_segment4](./images/000029_openpose_sam_concat.webp)
![pose_segment5](./images/000030_openpose_sam_concat.webp)