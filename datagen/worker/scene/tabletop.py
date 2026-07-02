"""
tabletop 场景：地面 + HDRI 环境光 + 一个主体物体 + 若干干扰物体 + 环绕相机机位。
是最通用的「桌面级单主体」场景，适合做物体级编辑。换场景 = 新写一个 SceneBuilder。
"""
from __future__ import annotations
import numpy as np
import blenderproc as bproc

from datagen.worker.scene.base import SceneBuilder
from datagen.worker.registry import register_scene, build
from datagen.worker.physics import validity


@register_scene("tabletop")
class TabletopScene(SceneBuilder):
    def build(self, ctx):
        rng = ctx.rng
        spec = ctx.spec

        # 1) 地面
        ground = bproc.object.create_primitive("PLANE")
        ground.set_scale([4, 4, 1])
        ground.set_name("ground")
        # 存一份地面引用：物理沉降（object_replace/add）需要它当被动碰撞体，否则物体会
        # 直接穿过地面坠入虚空（地面不在 all_objects 里，不参与解析碰撞检查以免误判贴地）。
        ctx.extras["ground"] = ground

        # 场景几何描述（供 placement / validity 使用）：平地、无天花板/墙
        ctx.extras["scene_geom"] = {
            "ground_z": 0.0, "ceiling_z": None,
            "bounds_min": None, "bounds_max": None,
        }
        ctx.extras["subject_support"] = "ground"

        # 2) HDRI 环境（光照 + 背景）
        env_cfg = spec.assets.get("environment", {})
        if env_cfg:
            env = build("asset", env_cfg["provider"], **env_cfg.get("params", {}))
            env.apply(ctx)

        # 2b) 方向性主光（太阳）：HDRI 缺省时只有平光、物体发灰偏暗、随机深色物体几乎看不清。
        # 补一盏固定太阳光给方向性明暗与接触阴影，物体清晰可见；它在 before/after 不变，
        # 不影响"只有主体变"的对齐（主体移动带来的阴影变化是物理正确的）。
        sun = bproc.types.Light()
        sun.set_type("SUN")
        sun.set_energy(4.0)
        sun.set_location([3, -2, 6])
        sun.set_rotation_euler([0.5, 0.25, 0.3])

        # 3) 主体物体
        subj_cfg = spec.assets["subject"]
        provider = build("asset", subj_cfg["provider"], **subj_cfg.get("params", {}))
        subject = provider.sample_object(ctx)
        subject.set_location([0, 0, _drop_height(subject)])
        subject.set_rotation_euler([0, 0, float(rng.uniform(0, 6.283))])
        ctx.register_object(subject, is_subject=True)

        # 4) 干扰物体（让场景不至于太空，也检验编辑只动主体）
        # 注意：primitives 半径约 1.4（cube/cone 对角），距离太近会与主体/彼此穿模，
        # 导致初始场景就 collides=True（scale/rotate 主体留在原点会被永久判碰撞）。
        # 因此放得更远（r∈[2.4,3.4]）并对每个干扰物做碰撞拒绝采样，保证初始场景干净。
        # 指令消歧：干扰物不能与主体同类别，否则 "move the chair" 指代不清——同类就换一个资产。
        unique_cat = self.params.get("ensure_unique_subject_category", True)
        subj_cat = _category(subject)
        n_lo, n_hi = self.params.get("num_distractors", [1, 3])
        n_distract = int(rng.integers(n_lo, n_hi + 1))
        for _ in range(n_distract):
            d = None
            for _osample in range(8):         # 最多换 8 次资产以避开与主体同类
                cand = provider.sample_object(ctx)
                if not unique_cat or _category(cand) != subj_cat:
                    d = cand
                    break
                cand.delete()                 # 同类 → 丢弃换一个
            if d is None:
                continue                      # 资产太单一、凑不出异类 → 少放一个干扰物
            placed = False
            for _try in range(40):
                angle = rng.uniform(0, 6.283)
                r = rng.uniform(2.4, 3.4)
                d.set_location([float(np.cos(angle) * r), float(np.sin(angle) * r),
                                _drop_height(d)])
                d.set_rotation_euler([0, 0, float(rng.uniform(0, 6.283))])
                if not validity.collides(d, ctx.all_objects):
                    placed = True
                    break
            if placed:
                ctx.register_object(d, is_subject=False)
            else:
                d.delete()                    # 放不下就丢弃这个干扰物，不污染场景

        # 记录干扰物类别（供审计/消歧校验：主体类别不应出现在其中）
        ctx.extras["distractor_categories"] = [_category(o) for o in ctx.distractors]

        # 5) 相机机位（before/after 共用，保证对齐）
        # 稳定地把主体框在画面中央：看向主体包围盒中心、限定俯仰角、按主体大小定距离。
        # （旧实现看固定点 (0,0,0.5)、机位高低随机，偶尔出现低机位贴地平线/主体偏出框，
        #  例如 object_add 那一对几乎全黑。）
        sb = np.asarray(subject.get_bound_box())
        subj_center = (sb.min(axis=0) + sb.max(axis=0)) / 2.0
        subj_radius = float(np.linalg.norm(sb.max(axis=0) - sb.min(axis=0)) / 2.0)
        n_views = int(self.params.get("camera_views", 1))
        for _ in range(n_views):
            cam_pose = _sample_camera_pose(rng, subj_center, subj_radius)
            bproc.camera.add_camera_pose(cam_pose)

        # 摊销加载支持：候选主体 + 重新取景闭包（一次建场景产多对时，run_job 用它换主体/机位）
        ctx.extras["editable_subjects"] = [subject] + list(ctx.distractors)
        ctx.extras["reframe_camera"] = _make_reframe(rng)


def _make_reframe(rng):
    def reframe(subj):
        try:
            bproc.utility.reset_keyframes()
        except Exception:
            pass
        sb = np.asarray(subj.get_bound_box())
        c = (sb.min(axis=0) + sb.max(axis=0)) / 2.0
        r = float(np.linalg.norm(sb.max(axis=0) - sb.min(axis=0)) / 2.0)
        bproc.camera.add_camera_pose(_sample_camera_pose(rng, c, r))
    return reframe


def _category(obj) -> str:
    """读物体类别（custom property），用于消歧判重。取不到回退 'object'。"""
    for key in ("category", "noun"):
        try:
            v = obj.get_cp(key)
            if v:
                return str(v)
        except Exception:
            pass
    return "object"


def _drop_height(obj) -> float:
    """把物体底部贴到地面 z=0。"""
    try:
        bbox = obj.get_bound_box()
        return float(-bbox.min(axis=0)[2])
    except Exception:
        return 0.0


def _sample_camera_pose(rng, look_at, subj_radius):
    """在主体上方半球采一个**看向主体中心**的机位，俯仰角/距离受控以稳定取景。

    - 方位角随机绕一圈；俯仰角限定在 22°~45°（避免贴地平线的低机位）。
    - 距离随主体大小自适应，保证主体在画面里清晰、不太小也不撑满。
    """
    look_at = np.asarray(look_at, dtype=float)
    azimuth = rng.uniform(0, 2 * np.pi)
    elevation = rng.uniform(np.radians(22), np.radians(45))
    # 距离随主体大小自适应：5×包围球半径（primitives 半径~2.2 → ~11，与冒烟取景一致；
    # 真实资产归一到 1m、半径~0.6 → ~3，避免被框得太小看不出编辑）。下限 3 防贴脸。
    dist = max(3.0, 5.0 * subj_radius) * float(rng.uniform(1.0, 1.15))
    horiz = dist * np.cos(elevation)
    cam_loc = look_at + np.array([np.cos(azimuth) * horiz,
                                  np.sin(azimuth) * horiz,
                                  dist * np.sin(elevation)])
    forward = look_at - cam_loc
    rot = bproc.camera.rotation_from_forward_vec(forward)
    return bproc.math.build_transformation_mat(cam_loc, rot)
