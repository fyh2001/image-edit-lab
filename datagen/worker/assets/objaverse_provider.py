"""
Objaverse 资产 provider。

重要：实际的 .glb 下载放在 orchestrator 侧预先做好（见 orchestrator/prefetch.py），
worker 这里只从本地缓存按 uid 加载，避免每个渲染进程都去联网下载。

API 说明：BlenderProc 用 bproc.loader.load_obj 加载多种格式（含 .glb）。
不同版本返回单个或多个 MeshObject，这里统一成「合并为一个主体」。
※ 请按你安装的 BlenderProc 版本核对 load_obj 对 glb 的支持与返回类型。
"""
from __future__ import annotations
import os
import blenderproc as bproc

from datagen.worker.assets.base import AssetProvider
from datagen.worker.assets.categories import (load_category_map, resolve_noun,
                                      load_meta_map, resolve_description, best_noun,
                                      category_target_size, DEFAULT_CATEGORY_SIZES)
from datagen.worker.registry import register_asset


@register_asset("objaverse")
class ObjaverseProvider(AssetProvider):
    def __init__(self, uid_list: str = None, local_cache: str = "./assets/objaverse",
                 category_map: str = "./assets/objaverse_categories.json",
                 meta_map: str = "./assets/objaverse_meta.json",
                 target_size: float = 1.0, category_sizes=None,
                 clean_noun: bool = False, indoor_only: bool = True, **kw):
        """
        target_size: 统一目标尺寸（最长边，米）。tabletop 用它把各资产归一到相近大小，取景稳定。
        category_sizes: 按类别归一到真实尺度（room 级场景用）。
            - None      → 统一 target_size（默认，适合 tabletop）；
            - "default" → 用 categories.DEFAULT_CATEGORY_SIZES；
            - dict      → 自定义 {category: 米}。
        """
        super().__init__(**kw)
        self.local_cache = local_cache
        self.uids = self._load_uids(uid_list)
        # uid -> 真实类别（LVIS）。文件缺失时为空，noun 自动回退 "object"。
        self.category_map = load_category_map(category_map)
        # 只保留**室内家居**类别的物体（防"熊猫/驴/车"进房间）。过滤后为空则回退全池（避免卡死）。
        if indoor_only and self.category_map:
            from datagen.worker.assets.indoor_categories import is_indoor
            kept = [u for u in self.uids if is_indoor(self.category_map.get(u))]
            if kept:
                dropped = len(self.uids) - len(kept)
                if dropped:
                    print(f"[objaverse] 室内白名单：保留 {len(kept)}/{len(self.uids)} 个物体"
                          f"（滤掉 {dropped} 个非室内类：动物/车辆/户外等）")
                self.uids = kept
            else:
                print("[objaverse] 警告：室内白名单过滤后为空，回退全池（检查类别是否匹配白名单）")
        # uid -> 富标注（name/tags/license），供 metadata 的物体描述。缺失则无描述。
        self.meta_map = load_meta_map(meta_map)
        self.target_size = float(target_size)
        self.clean_noun = bool(clean_noun)            # 实验性：用 name/tags 校正名词（默认关，见下）
        if category_sizes == "default":
            self.category_sizes = DEFAULT_CATEGORY_SIZES
        elif isinstance(category_sizes, dict):
            self.category_sizes = category_sizes
        else:
            self.category_sizes = None

    @staticmethod
    def _load_uids(uid_list):
        if uid_list and os.path.exists(uid_list):
            with open(uid_list) as f:
                return [ln.strip() for ln in f if ln.strip()]
        return []

    def _glb_path(self, uid: str) -> str:
        # 约定缓存布局：<local_cache>/<uid>.glb（由 prefetch 落地）
        return os.path.join(self.local_cache, f"{uid}.glb")

    def sample_object(self, ctx):
        if not self.uids:
            raise RuntimeError(
                "Objaverse uid 列表为空。请先用 orchestrator/prefetch.py 预下载并生成 uid 列表。"
            )
        uid = str(ctx.rng.choice(self.uids))
        path = self._glb_path(uid)
        if not os.path.exists(path):
            raise FileNotFoundError(f"未在缓存找到 {path}，请先预下载。")

        loaded = bproc.loader.load_obj(path)          # -> List[MeshObject]
        objs = loaded if isinstance(loaded, list) else [loaded]
        # Objaverse .glb 往往是层级结构：根/空 transform 节点 + 若干 mesh 子节点。
        # 不能盲取 objs[0]（可能是空节点 → "Object does not have geometry data"）。
        # 只留有网格的 mesh 物体，多 part 合并成一个主体；其余空节点删掉保持场景干净。
        meshes = [o for o in objs if _has_geometry(o)]
        if not meshes:
            raise RuntimeError(f"{uid}: load_obj 没返回任何带网格的物体（可能是纯空节点 glb）")
        # 先删非网格节点（空 transform 等），它们独立、可安全删除。
        # 注意：必须在 join 之前删，且只删非网格——join_with_other_objects 会消费 meshes[1:]，
        # 之后再对它们 delete() 会触发 Blender 内部 double-free（idtype.cc unreachable 崩溃）。
        mesh_ids = {id(o) for o in meshes}
        for o in objs:
            if id(o) not in mesh_ids:
                try:
                    o.delete()
                except Exception:
                    pass
        obj = meshes[0]
        if len(meshes) > 1:
            obj.join_with_other_objects(meshes[1:])   # 合并多 part（内部已删除被并入的对象）
        obj.set_cp("asset_uid", uid)                  # custom property，写进 meta 备查
        # 接真实名词。默认用 LVIS category（干净的单名词，虽偶有误标）。
        # clean_noun=True 时改用 name/tags 启发式校正——但实测在 LVIS/标题噪声下**净负**
        # （品牌/采集 app/外文词会混进来，如 date→polycam、bowl→patrimonio），故默认关闭；
        # 真要清洗名词建议用 LLM 跑一遍（metadata 里已存 description/tags 供其使用）。
        category, cat_noun = resolve_noun(uid, self.category_map)
        desc = resolve_description(uid, self.meta_map)
        noun = best_noun(category, desc.get("description"), desc.get("tags")) \
            if self.clean_noun else cat_noun
        obj.set_cp("category", category)
        obj.set_cp("noun", noun)
        # 物体描述/标签/许可（Sketchfab 原始名称等），写进 metadata 备查
        if desc.get("description"):
            obj.set_cp("description", str(desc["description"]))
        if desc.get("license"):
            obj.set_cp("license", str(desc["license"]))
        if desc.get("tags"):
            obj.set_cp("tags", ",".join(desc["tags"]))     # cp 存逗号串，metadata 再拆回 list
        # 记录本 job 实际用到的 uid（供「已用账本」过滤）
        ctx.extras.setdefault("used_objaverse_uids", []).append(uid)
        # 归一化：摆正原点 + 按类别/统一尺寸缩放，避免不同资产尺度/枢轴差异
        size = category_target_size(category, self.category_sizes, default=self.target_size)
        _normalize(obj, target=size)
        return obj


def _has_geometry(o) -> bool:
    """o 是否是带顶点的 mesh 物体（排除空节点 / 灯 / 相机等）。"""
    try:
        b = o.blender_obj
        return b is not None and b.type == "MESH" and b.data is not None \
            and len(b.data.vertices) > 0
    except Exception:
        return False


def _recenter_origin(obj):
    """把物体原点设到包围盒几何中心。

    Objaverse 资产的原点常远离几何（建模随意）→ 直接旋转会绕一个偏远的轴公转、
    缩放锚点也乱。把原点归到几何中心后，rotate 原地自转、scale/placement 都可预测。
    """
    try:
        import bpy
        b = obj.blender_obj
        try:
            bpy.ops.object.mode_set(mode="OBJECT")
        except Exception:
            pass
        for o in list(bpy.context.selected_objects):
            o.select_set(False)
        bpy.context.view_layer.objects.active = b
        b.select_set(True)
        bpy.ops.object.origin_set(type="ORIGIN_GEOMETRY", center="BOUNDS")
        b.select_set(False)
    except Exception as e:
        print(f"[objaverse] 原点归中失败（跳过）: {e}")


def _normalize(obj, target=1.0):
    """摆正原点 + 把最长边缩放到 target 米，并移到世界原点（底部贴地交给场景）。"""
    import numpy as np
    try:
        obj.persist_transformation_into_mesh()        # 烘掉导入变换，bbox/raycast 世界系一致
    except Exception:
        pass
    _recenter_origin(obj)
    try:
        bbox = np.asarray(obj.get_bound_box())        # 8x3
        longest = float((bbox.max(axis=0) - bbox.min(axis=0)).max()) or 1.0
        s = target / longest
        cur = obj.get_scale()
        obj.set_scale([cur[0] * s, cur[1] * s, cur[2] * s])
        obj.set_location([0.0, 0.0, 0.0])             # xy 居中；底部贴地由 scene 的 drop 处理
    except Exception as e:
        print(f"[objaverse] 尺度归一失败（跳过）: {e}")
