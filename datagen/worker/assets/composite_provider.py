"""组合资产 provider：把**多个物体数据集**按权重组合成一个，`sample_object` 时按权重挑一个源。

让"这次用 objaverse 的物体 + primitives 的物体，还能多选混合"变成配置——只在
config 里列 sources，worker 侧现有 objaverse/primitives 不用改。用于 add/replace/spawn。

    provider: composite
    params:
      sources:
        - {provider: objaverse,  weight: 3, params: {...}}
        - {provider: primitives, weight: 1, params: {...}}
"""
from __future__ import annotations
import numpy as np

from datagen.worker.assets.base import AssetProvider
from datagen.worker.registry import register_asset, build


@register_asset("composite")
class CompositeProvider(AssetProvider):
    def __init__(self, sources=None, **kw):
        super().__init__(**kw)
        self.specs = list(sources or [])
        if not self.specs:
            raise ValueError("composite provider 需要非空 sources")
        self._built = {}                              # 懒构建子 provider（首次用到才 build）
        w = np.array([float(s.get("weight", 1.0)) for s in self.specs], dtype=float)
        self._probs = w / w.sum()

    def _provider(self, i):
        if i not in self._built:
            s = self.specs[i]
            self._built[i] = build("asset", s["provider"], **s.get("params", {}))
        return self._built[i]

    def sample_object(self, ctx):
        i = int(ctx.rng.choice(len(self.specs), p=self._probs))   # 按权重挑数据集
        return self._provider(i).sample_object(ctx)
