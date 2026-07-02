"""
极简插件注册表 —— 整套系统「可扩展模块化」的核心。

用法：
    from datagen.worker.registry import register_edit, build

    @register_edit("object_move")
    class MoveEdit(EditOperator):
        ...

    op = build("edit", "object_move", max_offset=0.6)

加一个新编辑类型 = 新写一个类 + @register_edit("名字")，
然后在 configs/*.yaml 里点名即可，主流程一行都不用改。
"""
from __future__ import annotations
from typing import Dict, Type, List

_REGISTRIES: Dict[str, Dict[str, Type]] = {}


def _table(kind: str) -> Dict[str, Type]:
    return _REGISTRIES.setdefault(kind, {})


def register(kind: str, name: str):
    def deco(cls):
        table = _table(kind)
        if name in table:
            raise ValueError(f"[registry] 重复注册 {kind}:{name}")
        table[name] = cls
        cls._plugin_kind = kind
        cls._plugin_name = name
        return cls
    return deco


def build(kind: str, name: str, **kwargs):
    table = _table(kind)
    if name not in table:
        raise KeyError(
            f"[registry] 未知 {kind}:{name}。已注册的有: {sorted(table)}"
        )
    return table[name](**kwargs)


def available(kind: str) -> List[str]:
    return sorted(_table(kind))


# 各类别的便捷装饰器
def register_scene(name: str):   return register("scene", name)
def register_asset(name: str):   return register("asset", name)
def register_edit(name: str):    return register("edit", name)
def register_backend(name: str): return register("backend", name)
