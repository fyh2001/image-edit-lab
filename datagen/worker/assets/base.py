"""AssetProvider：负责把 3D 资产/环境引入场景。"""
from __future__ import annotations
from abc import ABC, abstractmethod


class AssetProvider(ABC):
    def __init__(self, **params):
        self.params = params

    @abstractmethod
    def sample_object(self, ctx):
        """加载并返回一个 bproc MeshObject（用于主体或替换体）。"""
        raise NotImplementedError


class EnvironmentProvider(ABC):
    def __init__(self, **params):
        self.params = params

    @abstractmethod
    def apply(self, ctx):
        """设置世界环境（HDRI 光照 / 背景）。"""
        raise NotImplementedError
