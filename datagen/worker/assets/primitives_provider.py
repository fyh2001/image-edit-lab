"""
原始体资产 provider —— 零下载、零依赖，专供本地冒烟测试。
返回随机的 Blender 内置几何体 + 随机颜色 PBR 材质。
"""
from __future__ import annotations
import blenderproc as bproc

from datagen.worker.assets.base import AssetProvider
from datagen.worker.registry import register_asset

_KINDS = ["CUBE", "SPHERE", "CYLINDER", "CONE", "MONKEY"]
_NOUN = {"CUBE": "cube", "SPHERE": "ball", "CYLINDER": "can",
         "CONE": "cone", "MONKEY": "head"}


@register_asset("primitives")
class PrimitivesProvider(AssetProvider):
    def sample_object(self, ctx):
        kinds = self.params.get("kinds", _KINDS)
        kind = str(ctx.rng.choice(kinds))
        obj = bproc.object.create_primitive(kind)

        # 随机颜色材质
        mat = bproc.material.create("rand_mat")
        c = ctx.rng.uniform(0.05, 0.95, size=3)
        mat.set_principled_shader_value(
            "Base Color", [float(c[0]), float(c[1]), float(c[2]), 1.0])
        try:
            obj.replace_materials(mat)
        except Exception:
            obj.add_material(mat)

        noun = _NOUN.get(kind, "object")
        obj.set_cp("category", noun)
        obj.set_cp("noun", noun)
        obj.set_cp("asset_uid", f"prim_{kind}")
        return obj
