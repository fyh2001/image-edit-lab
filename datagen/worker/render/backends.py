"""
两个渲染后端，通过 config 的 render.backend 一键切换：
  - cycles_gpu : 路径追踪 + OptiX，真实感最好，跑在 H100 上（生产）
  - eevee_fast : 光栅化，秒级出图，用于本地把整条 pipeline 逻辑跑通（调试）

二者实现同一 RenderBackend 接口，主流程对后端无感知。
"""
from __future__ import annotations
from typing import Dict, Any, List
import numpy as np
import blenderproc as bproc

from datagen.worker.render.base import RenderBackend
from datagen.worker.registry import register_backend


def _common_setup(render_cfg: Dict[str, Any]):
    res = render_cfg.get("resolution", [768, 768])
    bproc.camera.set_resolution(res[0], res[1])
    # BlenderProc 在 init 时设了 render.use_persistent_data=True（缓存渲染数据库以提速）。
    # 但本管线在两次 render() 之间切换物体可见性（object_add 先隐藏再显示），持久化缓存
    # 不会因 hide_render 变化而失效 → before/after 完全相同。这里关掉它，保证每次渲染都
    # 重新评估场景（可见性/位姿变化都能正确反映）。
    try:
        import bpy
        bpy.context.scene.render.use_persistent_data = False
    except Exception:
        pass
    if render_cfg.get("transparent_bg", False):
        bproc.renderer.set_output_format(enable_transparency=True)
    # 只要 RGB（编辑配对不需要 depth/normal，省时间）
    try:
        bproc.renderer.set_max_amount_of_samples(int(render_cfg.get("samples", 64)))
    except Exception:
        pass


def _render_rgb() -> List[np.ndarray]:
    data = bproc.renderer.render()
    # data["colors"] 是 list，每个相机机位一张 HxWx3/4 的 uint8/float
    colors = data.get("colors", [])
    out = []
    for img in colors:
        arr = np.asarray(img)
        if arr.dtype != np.uint8:
            arr = np.clip(arr, 0, 1) * 255 if arr.max() <= 1.0 else np.clip(arr, 0, 255)
            arr = arr.astype(np.uint8)
        out.append(arr[..., :3])
    return out


@register_backend("cycles_gpu")
class CyclesGPUBackend(RenderBackend):
    def setup(self, render_cfg: Dict[str, Any]) -> None:
        import bpy
        scene = bpy.context.scene
        scene.render.engine = "CYCLES"
        scene.cycles.device = "GPU"
        # 启用所有可用 GPU + OptiX（H100）
        prefs = bpy.context.preferences.addons["cycles"].preferences
        denoiser = render_cfg.get("denoiser", "OPTIX")
        try:
            prefs.compute_device_type = "OPTIX" if denoiser == "OPTIX" else "CUDA"
            prefs.get_devices()
            for dev in prefs.devices:
                dev.use = (dev.type in ("OPTIX", "CUDA"))
        except Exception as e:
            print(f"[cycles_gpu] 设备配置警告: {e}（将回退到 Blender 默认设备选择）")
        if denoiser and denoiser != "NONE":
            try:
                bproc.renderer.set_denoiser(denoiser)
            except Exception:
                scene.cycles.use_denoising = True
        # 全局光照开关：off 时把反弹设为 0（只留直接光，得到"纯净差异"）
        if render_cfg.get("global_illumination", "full") == "off":
            try:
                scene.cycles.max_bounces = 0
                scene.cycles.diffuse_bounces = 0
                scene.cycles.glossy_bounces = 0
            except Exception:
                pass
        _common_setup(render_cfg)

    def render(self):
        return _render_rgb()


@register_backend("eevee_fast")
class EeveeFastBackend(RenderBackend):
    def setup(self, render_cfg: Dict[str, Any]) -> None:
        import bpy
        # Blender 4.2+ 是 'BLENDER_EEVEE_NEXT'，老版本是 'BLENDER_EEVEE'
        engine = "BLENDER_EEVEE_NEXT"
        try:
            bpy.context.scene.render.engine = engine
        except TypeError:
            bpy.context.scene.render.engine = "BLENDER_EEVEE"
        _common_setup(render_cfg)

    def render(self):
        return _render_rgb()
