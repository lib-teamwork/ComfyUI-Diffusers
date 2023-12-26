import json
import os
from .utils import SCHEDULERS, token_auto_concat_embeds, vae_pt_to_vae_diffuser
import numpy as np
import torch
from comfy.model_management import get_torch_device, get_torch_device_name
import folder_paths
from diffusers import StableDiffusionPipeline, AutoencoderKL
from comfy.cli_args import args
from PIL import Image, ImageOps, ImageSequence
from PIL.PngImagePlugin import PngInfo


class DiffusersPipelineLoader:
    def __init__(self):
        self.tmp_dir = folder_paths.get_temp_directory()
        self.dtype = torch.float32
    
    @classmethod
    def INPUT_TYPES(s):
        return {"required": { "ckpt_name": (folder_paths.get_filename_list("checkpoints"), ), }}

    RETURN_TYPES = ("PIPELINE", "AUTOENCODER", "SCHEDULER",)

    FUNCTION = "create_pipeline"

    CATEGORY = "Diffusers"

    def create_pipeline(self, ckpt_name):
        ckpt_cache_path = os.path.join(self.tmp_dir, ckpt_name)
        
        StableDiffusionPipeline.from_single_file(
            pretrained_model_link_or_path=folder_paths.get_full_path("checkpoints", ckpt_name),
            torch_dtype=self.dtype,
            cache_dir=self.tmp_dir,
        ).save_pretrained(ckpt_cache_path, safe_serialization=True)
        
        pipe = StableDiffusionPipeline.from_pretrained(
            pretrained_model_name_or_path=ckpt_cache_path,
            torch_dtype=self.dtype,
            cache_dir=self.tmp_dir,
        )
        return ((pipe, ckpt_cache_path), pipe.vae, pipe.scheduler)

class DiffusersVaeLoader:
    def __init__(self):
        self.tmp_dir = folder_paths.get_temp_directory()
        self.dtype = torch.float32
    
    @classmethod
    def INPUT_TYPES(s):
        return {"required": { "vae_name": (folder_paths.get_filename_list("vae"), ), }}

    RETURN_TYPES = ("AUTOENCODER",)

    FUNCTION = "create_pipeline"

    CATEGORY = "Diffusers"

    def create_pipeline(self, vae_name):
        ckpt_cache_path = os.path.join(self.tmp_dir, vae_name)
        vae_pt_to_vae_diffuser(folder_paths.get_full_path("vae", vae_name), ckpt_cache_path)

        vae = AutoencoderKL.from_pretrained(
            pretrained_model_name_or_path=ckpt_cache_path,
            torch_dtype=self.dtype,
            cache_dir=self.tmp_dir,
        )
        
        return (vae,)

class DiffusersSchedulerLoader:
    def __init__(self):
        self.tmp_dir = folder_paths.get_temp_directory()
        self.dtype = torch.float32
    
    @classmethod
    def INPUT_TYPES(s):
        return {
            "required": {
                "pipeline": ("PIPELINE", ),
                "scheduler_name": (list(SCHEDULERS.keys()), ), 
            }
        }

    RETURN_TYPES = ("SCHEDULER",)

    FUNCTION = "load_scheduler"

    CATEGORY = "Diffusers"

    def load_scheduler(self, pipeline, scheduler_name):
        scheduler = SCHEDULERS[scheduler_name].from_pretrained(
            pretrained_model_name_or_path=pipeline[1],
            torch_dtype=self.dtype,
            cache_dir=self.tmp_dir,
            subfolder='scheduler'
        )
        return (scheduler,)

class DiffusersModelMakeup:
    def __init__(self):
        self.torch_device = get_torch_device()
    
    @classmethod
    def INPUT_TYPES(s):
        return {
            "required": {
                "pipeline": ("PIPELINE", ), 
                "autoencoder": ("AUTOENCODER", ),
                "scheduler": ("SCHEDULER", ),
            }, 
        }

    RETURN_TYPES = ("MAKED_PIPELINE",)

    FUNCTION = "makeup_pipeline"

    CATEGORY = "Diffusers"

    def makeup_pipeline(self, pipeline, autoencoder, scheduler):
        pipeline = pipeline[0]
        pipeline.vae = autoencoder
        pipeline.scheduler = scheduler
        pipeline.safety_checker = None if pipeline.safety_checker is None else lambda images, **kwargs: (images, [False])
        pipeline.enable_attention_slicing()
        pipeline = pipeline.to(self.torch_device)
        return (pipeline,)

class DiffusersClipTextEncode:
    @classmethod
    def INPUT_TYPES(s):
        return {"required": {
            "maked_pipeline": ("MAKED_PIPELINE", ),
            "positive": ("STRING", {"multiline": True}),
            "negative": ("STRING", {"multiline": True}),
        }}

    RETURN_TYPES = ("EMBEDS", "EMBEDS", )
    RETURN_NAMES = ("positive_embeds", "negative_embeds", )

    FUNCTION = "concat_embeds"

    CATEGORY = "Diffusers"

    def concat_embeds(self, maked_pipeline, positive, negative):
        positive_embeds, negative_embeds = token_auto_concat_embeds(maked_pipeline, positive,negative)

        return (positive_embeds, negative_embeds, )

class DiffusersSampler:
    def __init__(self):
        self.torch_device = get_torch_device()
        
    @classmethod
    def INPUT_TYPES(s):
        return {"required": {
            "maked_pipeline": ("MAKED_PIPELINE", ),
            "positive_embeds": ("EMBEDS", ),
            "negative_embeds": ("EMBEDS", ),
            "width": ("INT", {"default": 512, "min": 1, "max": 8192, "step": 1}),
            "height": ("INT", {"default": 512, "min": 1, "max": 8192, "step": 1}),
            "steps": ("INT", {"default": 20, "min": 1, "max": 10000}),
            "cfg": ("FLOAT", {"default": 8.0, "min": 0.0, "max": 100.0, "step":0.1, "round": 0.01}),
            "seed": ("INT", {"default": 0, "min": 0, "max": 0xffffffffffffffff}),
        }}

    RETURN_TYPES = ("PIL_IMAGE",)

    FUNCTION = "sample"

    CATEGORY = "Diffusers"

    def sample(self, maked_pipeline, positive_embeds, negative_embeds, height, width, steps, cfg, seed):
        images = maked_pipeline(
            prompt_embeds=positive_embeds,
            height=height,
            width=width,
            num_inference_steps=steps,
            guidance_scale=cfg,
            negative_prompt_embeds=negative_embeds,
            generator=torch.Generator(self.torch_device).manual_seed(seed)
        ).images
        return (images,)

class DiffusersSaveImage:
    def __init__(self):
        self.output_dir = folder_paths.get_output_directory()
        self.type = "output"
        self.prefix_append = ""
        self.compress_level = 4

    @classmethod
    def INPUT_TYPES(s):
        return {"required": 
                    {"pil_images": ("PIL_IMAGE", ),
                     "filename_prefix": ("STRING", {"default": "ComfyUI"})},
                "hidden": {"prompt": "PROMPT", "extra_pnginfo": "EXTRA_PNGINFO"},
                }

    RETURN_TYPES = ()
    FUNCTION = "save_images"

    OUTPUT_NODE = True

    CATEGORY = "Diffusers"

    def save_images(self, pil_images, filename_prefix="ComfyUI", prompt=None, extra_pnginfo=None):
        filename_prefix += self.prefix_append
        width, height = pil_images[0].size
        full_output_folder, filename, counter, subfolder, filename_prefix = folder_paths.get_save_image_path(filename_prefix, self.output_dir, width, height)
        results = list()
        for image in pil_images:
            metadata = None
            if not args.disable_metadata:
                metadata = PngInfo()
                if prompt is not None:
                    metadata.add_text("prompt", json.dumps(prompt))
                if extra_pnginfo is not None:
                    for x in extra_pnginfo:
                        metadata.add_text(x, json.dumps(extra_pnginfo[x]))

            file = f"{filename}_{counter:05}_.png"
            image.save(os.path.join(full_output_folder, file), pnginfo=metadata, compress_level=self.compress_level)
            results.append({
                "filename": file,
                "subfolder": subfolder,
                "type": self.type
            })
            counter += 1

        return { "ui": { "images": results } }

NODE_CLASS_MAPPINGS = {
    "DiffusersPipelineLoader": DiffusersPipelineLoader,
    "DiffusersVaeLoader": DiffusersVaeLoader,
    "DiffusersSchedulerLoader": DiffusersSchedulerLoader,
    "DiffusersModelMakeup": DiffusersModelMakeup,
    "DiffusersClipTextEncode": DiffusersClipTextEncode,
    "DiffusersSampler": DiffusersSampler,
    "DiffusersSaveImage": DiffusersSaveImage
}

NODE_DISPLAY_NAME_MAPPINGS = {
    "DiffusersPipelineLoader": "Diffusers Pipeline Loader",
    "DiffusersVaeLoader": "Diffusers Vae Loader",
    "DiffusersSchedulerLoader": "Diffusers Scheduler Loader",
    "DiffusersModelMakeup": "Diffusers Model Makeup",
    "DiffusersClipTextEncode": "Diffusers Clip Text Encode",
    "DiffusersSampler": "Diffusers Sampler",
    "DiffusersSaveImage": "Diffusers Save Image"
}