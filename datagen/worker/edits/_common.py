"""编辑算子共用的小工具。"""
from __future__ import annotations


def noun(obj) -> str:
    """取物体的人类可读名词。优先用资产元数据里的类别，否则退回 'object'。

    建议：用 Objaverse-LVIS 的 uid->category 映射在 prefetch 阶段写入
    custom property 'noun'，这样指令里就有真实名词（如 'a wooden chair'）。
    """
    for key in ("noun", "category"):
        try:
            v = obj.get_cp(key)
            if v:
                return str(v)
        except Exception:
            pass
    return "object"


def transform_dict(obj) -> dict:
    """物体世界系位姿 → {location, rotation_euler, rotation_quat[w,x,y,z], scale}。

    rotation_quat 用 Blender 自带的欧拉约定（XYZ）换算，保证与渲染一致。
    供各算子 metadata 与样本级 subject.init_transform 复用。
    """
    eul = [float(x) for x in obj.get_rotation_euler()]
    quat = [1.0, 0.0, 0.0, 0.0]
    try:
        import mathutils
        q = mathutils.Euler(eul, "XYZ").to_quaternion()
        quat = [float(q.w), float(q.x), float(q.y), float(q.z)]
    except Exception:
        pass
    return {
        "location": [round(float(x), 4) for x in obj.get_location()],
        "rotation_euler": [round(x, 4) for x in eul],
        "rotation_quat": [round(x, 6) for x in quat],
        "scale": [round(float(x), 4) for x in obj.get_scale()],
    }


def copy_transform(src, dst) -> None:
    """把 src 的位姿/缩放复制到 dst（用于 replace 时新物体对齐旧物体）。"""
    dst.set_location(src.get_location())
    dst.set_rotation_euler(src.get_rotation_euler())
    dst.set_scale(src.get_scale())


def hide(obj, do_hide: bool = True) -> None:
    """从渲染中隐藏/显示物体。不同版本 API 名称略有差异，做了兼容。

    关键：只改 `hide_render` 标志，两次 render() 之间 Blender 的 depsgraph 不会自动
    重新评估可见性——尤其是「先隐藏、后显示」（object_add）时，第二次渲染会沿用旧状态，
    导致 before/after 完全相同。因此改完标志必须强制刷新 view_layer / depsgraph。
    """
    try:
        obj.hide(do_hide)                       # 多数 BlenderProc 版本
    except Exception:
        try:
            obj.blender_obj.hide_render = do_hide
        except Exception:
            pass
    # 同步 hide_viewport，并强制刷新依赖图，保证下一次 render 反映新可见性
    try:
        import bpy
        try:
            obj.blender_obj.hide_viewport = do_hide
        except Exception:
            pass
        bpy.context.view_layer.update()
    except Exception:
        pass


def camera_basis(view: int = 0):
    """取某机位的 right/up/forward（世界系单位向量），用于把世界位移分解到相机系。

    返回 (right, up, forward)；取不到时返回一组默认基（不致命）。
    """
    import numpy as np
    from datagen.worker.geometry.frames import camera_basis_from_matrix
    try:
        import blenderproc as bproc
        cam2world = bproc.camera.get_camera_pose(frame=view)
        return camera_basis_from_matrix(cam2world)
    except Exception:
        return (np.array([1., 0, 0]), np.array([0, 0, 1.]), np.array([0, 1., 0]))
