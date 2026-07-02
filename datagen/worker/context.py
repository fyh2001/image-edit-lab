"""
JobSpec：一个渲染任务的完整、可序列化描述（orchestrator 生成，worker 消费）。
SceneContext：worker 运行期在各插件间传递的共享状态。

设计要点：JobSpec 是「生成配方」——只要 seed 和 spec 固定，产出的 (before, after)
就完全可复现。所以永久要备份的是这些 spec（很小），渲染图只是缓存。
"""
from __future__ import annotations
from dataclasses import dataclass, field, asdict
from typing import Any, Dict, List, Optional
import json


@dataclass
class JobSpec:
    job_id: str
    seed: int
    scene: Dict[str, Any]          # {"name": ..., "params": {...}}
    assets: Dict[str, Any]         # {"subject": {...}, "environment": {...}}
    edit: Dict[str, Any]           # {"name": ..., "params": {...}}
    render: Dict[str, Any]         # {"backend": ..., "resolution": [..], ...}
    output_dir: str
    instruction: Dict[str, Any] = field(default_factory=dict)  # {"frame": ...}
    # 摊销加载：一次建场景产多对时，每对从这里按权重重采一个算子（None 则都用 edit）
    edits_config: Dict[str, Any] = None

    def to_json(self) -> str:
        return json.dumps(asdict(self), ensure_ascii=False, indent=2)

    @classmethod
    def from_file(cls, path: str) -> "JobSpec":
        with open(path, "r", encoding="utf-8") as f:
            return cls(**json.load(f))

    def save(self, path: str) -> None:
        with open(path, "w", encoding="utf-8") as f:
            f.write(self.to_json())


class SceneContext:
    """worker 运行期共享状态，插件通过它互相协作。"""

    def __init__(self, spec: JobSpec, rng):
        self.spec = spec
        self.rng = rng                       # numpy Generator，所有随机都走它
        self.subject = None                  # 被编辑的主体 (bproc MeshObject)
        self.distractors: List[Any] = []     # 干扰物体
        self.all_objects: List[Any] = []     # 场景里全部加载的物体
        self.extras: Dict[str, Any] = {}     # 插件间临时挂载（如 add 算子预留的对象）

    def register_object(self, obj, is_subject: bool = False):
        self.all_objects.append(obj)
        if is_subject:
            self.subject = obj
        else:
            self.distractors.append(obj)
        return obj
