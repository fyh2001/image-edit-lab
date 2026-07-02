"""SceneBuilder：搭建一个场景（地面/相机/光照/主体+干扰物），供编辑算子作用。"""
from __future__ import annotations
from abc import ABC, abstractmethod


class SceneBuilder(ABC):
    def __init__(self, **params):
        self.params = params

    @abstractmethod
    def build(self, ctx) -> None:
        """填充场景：放置资产、相机机位、光照/环境。

        约定：结束时 ctx.subject 必须指向被编辑的主体物体；
        相机机位需通过 bproc.camera.add_camera_pose 注册（before/after 共用）。
        """
        raise NotImplementedError
