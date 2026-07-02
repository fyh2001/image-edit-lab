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


def _setup_cycles_quality(scene, render_cfg: Dict[str, Any]):
    """Cycles 真实感/降噪配置（安全、不破坏 before/after 对齐）。全部可 config 覆盖：

      light_paths: {max, diffuse, glossy, transmission, volume}  —— 反弹数（室内 diffuse 6 更亮更真）
      noise_threshold / min_samples                              —— 自适应采样（够干净就停，省时）
      clamp_indirect / clamp_direct                              —— 钳制亮样本，灭 fireflies 噪点
      caustics: false                                            —— 关焦散（主要噪声源）
    这些是渲染器全局设置，before/after 两帧一致 → 不影响"只有主体变"的对齐。
    """
    cy = scene.cycles
    lp = render_cfg.get("light_paths") or {}
    try:
        cy.max_bounces = int(lp.get("max", 12))
        cy.diffuse_bounces = int(lp.get("diffuse", 6))        # 室内多给一档，暗角更亮更真
        cy.glossy_bounces = int(lp.get("glossy", 4))
        cy.transmission_bounces = int(lp.get("transmission", 12))
        cy.volume_bounces = int(lp.get("volume", 0))
    except Exception as e:
        print(f"[cycles] 反弹设置跳过: {e}")
    try:
        nt = render_cfg.get("noise_threshold")
        if nt is not None:
            cy.use_adaptive_sampling = True
            cy.adaptive_threshold = float(nt)                 # 如 0.01
            cy.adaptive_min_samples = int(render_cfg.get("min_samples", 0))
    except Exception as e:
        print(f"[cycles] 自适应采样跳过: {e}")
    try:
        cy.sample_clamp_indirect = float(render_cfg.get("clamp_indirect", 10.0))  # 灭 fireflies
        cy.sample_clamp_direct = float(render_cfg.get("clamp_direct", 0.0))       # 0=不钳直接光
    except Exception as e:
        print(f"[cycles] 钳制设置跳过: {e}")
    try:
        if not bool(render_cfg.get("caustics", False)):
            cy.caustics_reflective = False
            cy.caustics_refractive = False
    except Exception:
        pass


def _set_color_management(render_cfg: Dict[str, Any]):
    """色彩管理：默认 AgX（Blender 4.x）——高光像真相机一样滚降，"少 CG 多照片"。

    可 config 覆盖：view_transform(AgX/Filmic/Standard)、look(对比度档)、exposure、gamma。
    BlenderProc 有时把 view_transform 留成 Standard/Raw → 高光死白、发假；这里显式设回 AgX。
    """
    try:
        import bpy
        vs = bpy.context.scene.view_settings
        vs.view_transform = str(render_cfg.get("view_transform", "AgX"))
        look = render_cfg.get("look")
        if look is not None:
            vs.look = str(look)                         # 如 "AgX - Medium Contrast"
        vs.exposure = float(render_cfg.get("exposure", 0.0))
        vs.gamma = float(render_cfg.get("gamma", 1.0))
    except Exception as e:
        print(f"[render] 色彩管理设置跳过: {e}")


def _common_setup(render_cfg: Dict[str, Any]):
    res = render_cfg.get("resolution", [768, 768])
    bproc.camera.set_resolution(res[0], res[1])
    _set_color_management(render_cfg)
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
        else:
            _setup_cycles_quality(scene, render_cfg)
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
