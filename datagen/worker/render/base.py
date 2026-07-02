"""RenderBackend：封装渲染引擎/设备，让「调试用快后端、生产用 Cycles GPU」可一键切换。"""
from __future__ import annotations
from abc import ABC, abstractmethod
from typing import Dict, Any, List
import numpy as np


class RenderBackend(ABC):
    def __init__(self, **params):
        self.params = params

    @abstractmethod
    def setup(self, render_cfg: Dict[str, Any]) -> None:
        """配置引擎、分辨率、采样、设备（CPU/GPU/OptiX）等。"""
        raise NotImplementedError

    @abstractmethod
    def render(self) -> List[np.ndarray]:
        """渲染当前已注册的相机机位，返回 RGB 图像列表（每个机位一张）。"""
        raise NotImplementedError
