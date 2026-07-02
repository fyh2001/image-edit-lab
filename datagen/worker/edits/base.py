"""
EditOperator：一次 (before -> after) 编辑的抽象。这是系统最主要的扩展点。

生命周期（worker 主流程调用顺序）：
    build scene  ->  editor.prepare(ctx)  ->  render BEFORE
                 ->  editor.apply(ctx)    ->  render AFTER

- prepare(): 在「改之前」渲染前调整初始状态。多数算子为空；
             但像 object_add 需要在 before 时先把待加入物体藏起来。
- apply():   产生「改之后」状态，并返回 (instruction, change_meta)。
             instruction 是给图像编辑模型的训练指令文本。
"""
from __future__ import annotations
from abc import ABC, abstractmethod
from typing import Tuple, Dict, Any


class EditInvalid(Exception):
    """物理有效化失败（多次拒绝采样仍无合法位姿）→ 放弃该 job，不产出穿模/悬空数据。"""


class EditOperator(ABC):
    def __init__(self, **params):
        self.params = params

    def prepare(self, ctx) -> None:
        """默认无操作。需要在 before 前改状态的算子覆写它。"""
        return None

    @abstractmethod
    def apply(self, ctx) -> Tuple[str, Dict[str, Any]]:
        """把场景从 before 状态变到 after 状态。

        Returns:
            instruction: 自然语言编辑指令（可含多种改写，见下）
            meta: 结构化变更记录（改了哪个物体、参数多少），用于审计/过滤
        """
        raise NotImplementedError

    # 子类提供指令模板，这里统一做轻量改写以增加语言多样性
    @staticmethod
    def phrase(templates, rng) -> str:
        return str(rng.choice(templates))
