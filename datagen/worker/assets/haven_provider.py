"""
Haven 环境 provider：用 HDRI 做世界光照 + 背景，提升真实感、增加光照多样性。

API：BlenderProc 提供 bproc.world.set_world_background_hdr_img(path)。
HDRI 文件请预先放到 hdri_dir（Poly Haven 免费下载 .hdr/.exr）。
"""
from __future__ import annotations
import os
import glob
import blenderproc as bproc

from datagen.worker.assets.base import EnvironmentProvider
from datagen.worker.registry import register_asset


@register_asset("haven")
class HavenEnvironmentProvider(EnvironmentProvider):
    def __init__(self, hdri_dir: str = "./assets/haven/hdris", strength: float = 1.0,
                 strength_jitter: float = 0.0, **kw):
        super().__init__(**kw)
        self.strength = float(strength)                 # enclosed 房间可调 >1 提亮
        self.strength_jitter = float(strength_jitter)   # 每场景强度抖动 → 明暗多样性
        self.hdris = sorted(
            glob.glob(os.path.join(hdri_dir, "**", "*.hdr"), recursive=True)
            + glob.glob(os.path.join(hdri_dir, "**", "*.exr"), recursive=True)
        )

    def apply(self, ctx):
        if not self.hdris:
            # 兜底：纯色世界光，保证 pipeline 仍能跑
            bproc.world.set_world_background_hdr_img  # noqa  (存在性提示)
            _flat_world(strength=2.5)
            ctx.extras["hdri"] = None
            return
        hdri = str(ctx.rng.choice(self.hdris))
        # 随机旋转环境，增加光照方向多样性（域随机化）
        rot_z = float(ctx.rng.uniform(0, 6.283))
        strength = self.strength
        if self.strength_jitter > 0:
            strength *= float(ctx.rng.uniform(1.0 - self.strength_jitter, 1.0 + self.strength_jitter))
        try:
            bproc.world.set_world_background_hdr_img(
                hdri, strength=strength, rotation_euler=[0, 0, rot_z])
        except TypeError:
            bproc.world.set_world_background_hdr_img(hdri)
        ctx.extras["hdri"] = os.path.basename(hdri)
        ctx.extras["hdri_strength"] = round(strength, 3)


def _flat_world(strength=2.5):
    """无 HDRI 时的兜底：把世界背景设成较亮的中性天光，保证场景被照亮。

    Blender 默认世界色是 0.05 灰、强度 1.0，几乎不发光 —— 在 Cycles 下会渲成近黑，
    导致 before/after 差异被淹没。这里调亮颜色 + 提高强度，使物体清晰可见。
    """
    import bpy
    world = bpy.context.scene.world
    world.use_nodes = True
    bg = world.node_tree.nodes.get("Background")
    if bg:
        bg.inputs["Strength"].default_value = strength
        try:
            bg.inputs["Color"].default_value = (0.65, 0.68, 0.72, 1.0)
        except Exception:
            pass
