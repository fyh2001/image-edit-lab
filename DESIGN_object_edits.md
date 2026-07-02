# 物体编辑：物理有效性 + Metadata 规范

把 6 个物体编辑算子从"随机改"升级为"物理正确 + 可精确描述"。
本文先定标准，实现照此做。

---

## 一、坐标系与方向约定（一切的基础）

不定义绝对方向，metadata 就没法描述"往哪移/绕哪转"。定两套坐标系：

### 1. 世界坐标系（绝对，用于 metadata 的 ground truth）

- 采用 Blender 世界系：**+Z 朝上**（重力反方向），单位**米**。
- 地面在 `z = 0`（tabletop）或房间地板（front3d）。
- `+X / +Y` 为水平面两个正交方向，固定不变。
- **所有 metadata 里的位移/旋转都以世界系记录**——绝对、无歧义、可复现。

### 2. 相机坐标系（相对，用于生成指令）

关键洞察：编辑模型看的是**一张 2D 相机视图**，所以指令里的"左/右/上/下/远/近"
必须是**相机视角下**的方向，而不是世界系。否则"move left"在不同机位含义不同。

做法：把世界系位移向量投影到相机的 right / up / forward 三轴，分类成语义方向：

- right 分量 → "left" / "right"
- up 分量 → "up" / "down"
- forward 分量（沿视线）→ "closer" / "farther"（深度方向，2D 上变化小，需谨慎）

> **多机位的矛盾**：若一对数据渲多个机位，"move left"在各机位方向不同。
> 解决：要么**一对一机位**（允许相机相对指令），要么用**场景锚定**的措辞
> （"move onto the table" / "put it on the floor"），与机位无关。见决策区。

### 3. 同时记录两套

metadata 同时存：世界系 delta（绝对 ground truth）+ 相机系分解 + 语义方向词。

---

## 二、物理有效性基础设施（所有算子共用）

实现成一个 `physics/validity.py` 模块，提供下列检查；算子调用它做"拒绝采样"。

| 能力 | 作用 | 实现思路（API 需按 BlenderProc 版本核对） |
|---|---|---|
| **碰撞/穿模检测** | 主体网格是否与其他物体/墙/地相交 | 每个物体建 BVH (`mathutils.bvhtree.BVHTree`)，`bvh.overlap()`；或 BlenderProc `CollisionUtility` |
| **支撑/悬空检测** | 主体底部是否落在某个表面上（不悬空） | 从主体底部包围盒向 -Z 射线投射 (`scene.ray_cast`)，命中距离 ≤ ε 视为有支撑 |
| **重新落地 (reseat)** | 把物体竖直下落贴到最近支撑面 | 射线测下方表面，平移使底部接触（留 contact ε） |
| **边界/容器检测** | 不穿墙、不出房间、不出画面 | 房间包围盒 / 相机视锥检查 |
| **可见性检测** | 编辑后的变化在画面里**确实可见** | 见第四节，避免"无变化"配对 |
| **遮挡检测** | 主体未被其他物体**完全遮挡** | 相机向主体采样点射线，可见比例 < 阈值则拒（move 后藏到别人后面） |
| **屏占比检测** | 主体在画面里**不太小也不太大** | 主体投影包围框面积 / 图面积，越界则拒（缩小后几像素 / 放大到撑满） |
| **物理沉降（可选）** | 用物理引擎让物体自然静止、不穿模 | `bproc.object.simulate_physics_and_fix_final_poses(...)`，确定性需固定步长 |

**两种有效化策略**（决策区会问你选哪种）：

- **解析式**：射线 + BVH overlap，快、可控、可精确记录参数。适合 move/scale/rotate。
- **物理沉降式**：丢进物理引擎让它落稳，最鲁棒、最自然，但慢、参数不可直接指定。
  适合 add / replace / 复杂堆叠。
- **推荐：混合**——位姿类用解析式 + reseat，放置类（add/replace）用物理沉降。

**通用拒绝采样循环**：每个算子采样一个候选变换 → 跑有效性检查 → 不过就重采样
（上限 N 次）→ N 次都失败则**放弃该 job**（记录失败原因），绝不产出穿模/悬空数据。

**接触容差 ε**：定义统一的 contact epsilon（如 1mm）避免 z-fighting 和浮点抖动。

---

## 三、每个算子的物理规则

### 0. 前置：物体规范化（影响所有放置）

Objaverse 资产朝向/尺度任意，必须先：**摆正到 +Z 向上**（按资产 up-axis 或启发式），
**底部贴地**，**按类别归一到真实尺度**（椅子~1m、杯子~0.1m，而非统一 1m）。
否则一切支撑/碰撞判断都不成立。

### 1. object_move（统一的"放置模式"模型，支持 X/Y/Z 三向）

**核心模型（按用户修正）**：吸附/悬挂**不是单独的编辑类型**，而是 move 的不同"落点"。
默认场景里每个物体都**搁置在某个支撑上**（地/桌/另一物体顶面）；move 就是把主体
重新放到某种**放置模式（placement mode）**：

| 放置模式 | 含义 | 物理约束 | 典型指令 |
|---|---|---|---|
| `support_surface` | 落到地面/桌面/另一物体顶 | 底部接触支撑、不穿模 | "move onto the table" |
| `object_top` | 叠到另一个物体上面 | 底部接触该物体顶、不超出 | "put it on the box" |
| `ceiling` | 贴/挂到天花板 | 顶部接触天花板下表面 | "hang it on the ceiling" |
| `wall` | 贴到墙面 | 一侧面接触墙、朝向贴合 | "stick it on the wall" |
| `floating` | 悬空（故意） | 不要求支撑，但要在边界内、可见 | "make it float in the air" |

- 采样：按 config 权重选一个放置模式 → 采样该模式下的目标位姿 → 跑对应有效性检查
  （搁置/叠放/天花板/墙要过接触检查；floating 跳过支撑检查）→ 碰撞 + 边界 + 可见性。
- 失败重采样，N 次失败放弃该 job。
- 记录：放置模式 + 世界系位移 + 相机系分解 + 语义方向，见 schema 的 `move`。

### 2. object_scale（放大）

- **锚点定在底部中心**，只向上/四周长，避免穿地。
- 放大后检查：与地/墙/其他物体碰撞。侧向撞到邻居 → **拒绝重采样**（不允许推开邻居，
  那会破坏"只动主体"）。撞地 → 锚底已避免；仍越界则缩小系数。

### 3. object_scale（缩小）

- 缩小后物体可能**悬空**（绕中心缩，底部抬高）→ **必做 reseat** 落回支撑面。
- 记录是否 reseat。

### 4. object_rotate（X/Y/Z，每对只转一个轴）

- 每对数据**随机选一个轴**（X/Y/Z 之一），只绕该轴转。
- 绕 Z（偏航）在平面上安全；绕 X/Y（俯仰/横滚）会改变接触、可能穿地或悬空
  → 旋转后 **reseat + 碰撞检查**（物体可能"翻倒"，需重新落稳）。
- 记录：轴、带符号角度、旋转约定（世界系 vs 物体局部系、欧拉序）。

### 5. object_add

- 采样**物理合法的放置**：在支撑面上、不穿模、不悬空、在边界内、且对相机可见。
- 用物理沉降或解析式 reseat 保证落稳。失败重采样。

### 6. object_replace

- 新物体放到旧物体位置，但因尺寸/形状不同**必须 reseat + 碰撞检查**。
- 新物体尺度归一到与旧物**可比的占地**，避免"沙发换成纽扣"的荒诞替换。
- 记录新旧 asset id、类别、各自变换。

---

## 四、我补充的细节（你没列但会坑的）

1. **相机相对 vs 世界绝对方向**（已在第一节）——指令方向必须跟随视角。
2. **变化可见性**：编辑后画面里**必须看得出变化**。否则会产出"看似没变"的废对：
   - 沿视线深度方向小幅移动 → 2D 上几乎无变化；
   - 旋转对称物体（球、圆柱）→ 看不出转了；
   - 把物体移到被别的物体完全遮挡处。
   → 加**像素级变化阈值**：渲完 before/after 比对主体区域像素/轮廓差，过小则丢弃。
3. **阴影 / 全局光照会变**：移动/增删物体会改变它的投影、接触阴影，甚至 GI 色彩外溢到
   邻居。这是物理正确的、对模型有益，但意味着"严格只有主体区域变"不成立（阴影区也变）。
   → 需决策：保留完整真实感（推荐）还是关 GI 求"纯净差异"。
4. **遮挡区自动补全**：删物体后，它原先挡住的背景/墙/地会被正确渲染出来——
   这是 3D 方案相对 2D inpainting 的巨大优势，无需"猜"被遮挡内容。
5. **指令指代消歧**：场景有 2 把椅子时，"move the chair"有歧义。
   → 主体要么是场景内**该类别唯一**，要么指令带区分属性（"the red chair" / "左边的椅子"）。
6. **编辑幅度上下限**：太小看不见、太大出画面。→ 把变化投影到像素，约束在 [min, max]。
7. **可复现性**：若用物理沉降，需固定随机种子 + 步长保证确定性，或把最终位姿写进
   metadata 以便重放。
8. **多机位一致性**：一个 3D 编辑要在所有机位都满足有效性；且方向语义因机位而异
   （见第一节多机位矛盾）。
9. **尺度真实性**：物体相对场景的真实尺寸要合理（房间里的椅子~1m），需类别感知归一化。
10. **悬挂/吸附的朝向**：挂灯泡要朝下垂、挂画要贴墙——需按类别定吸附朝向，建议先支持
    一个子集（灯具→天花板、画框→墙）。

---

## 五、Metadata Schema（先定义，再详细记录）

每个样本 `sample.json` 增加 `coordinate_frame` 与结构化 `edit`。

### 通用（每个样本一份）

```jsonc
{
  "coordinate_frame": {
    "world": {"up_axis": "Z", "units": "meter", "ground_z": 0.0},
    "convention": "blender_world_right_handed"
  },
  "cameras": [
    {                       // 每个机位存外参，便于把世界 delta 反投到相机系
      "view": 0,
      "location": [x, y, z],
      "rotation_euler": [rx, ry, rz],
      "right": [..], "up": [..], "forward": [..],
      "intrinsics": {"fx":.., "fy":.., "cx":.., "cy":.., "resolution":[W,H]}
    }
  ],
  "subject": {
    "asset_uid": "....", "category": "chair",
    "init_transform": {
      "location": [x,y,z],
      "rotation_euler": [rx,ry,rz], "rotation_quat": [w,x,y,z],
      "scale": [sx,sy,sz]
    },
    "bbox_dims": [dx,dy,dz],          // 摆正后的世界系包围盒尺寸（米）
    "support_before": "ground"        // ground / table:<id> / ceiling / wall / object:<id>
  },
  "validity": {
    "strategy": "analytic|physics|hybrid",
    "num_attempts": 3,
    "collision_free": true,
    "penetration_depth": 0.0,         // ≈0
    "floating_gap": 0.0,              // ≈0（reseat 后）
    "reseated": false,
    "change_visible": true,
    "min_pixel_change_ratio": 0.04
  }
}
```

### 各算子专属 `edit` 字段

```jsonc
// object_move
{ "op":"object_move",
  "translation_world": [dx,dy,dz],            // 绝对，米
  "translation_camera": {"right":.., "up":.., "forward":..},  // 相机系分解
  "semantic_direction": ["left","up"],        // 给指令用
  "support_after": "ceiling",                 // 换支撑面时
  "final_transform": {...} }

// object_rotate
{ "op":"object_rotate",
  "axis": "X|Y|Z",
  "axis_world_vector": [..],
  "degrees": -37.5,                           // 带符号
  "rotation_space": "world|local",
  "euler_order": "XYZ",
  "delta_quat": [w,x,y,z],
  "reseated": true }

// object_scale
{ "op":"object_scale",
  "factor": 1.6,                              // 标量；或 per_axis
  "per_axis": [1.6,1.6,1.6],
  "uniform": true,
  "anchor": "bottom_center",
  "reseated": false }                         // 缩小时常为 true

// object_delete / object_add
{ "op":"object_delete",
  "asset_uid":"..","category":"lamp",
  "transform": {...}, "support":"table:3" }

// object_replace
{ "op":"object_replace",
  "from": {"asset_uid":"..","category":"chair","transform":{...}},
  "to":   {"asset_uid":"..","category":"stool","transform":{...}},
  "support":"ground" }
```

---

## 六、已锁定的决策

1. **有效化策略 = 混合**：位姿类（move/scale/rotate）用解析式（射线 + BVH 碰撞 + reseat），
   放置类（add/replace）用物理沉降。validity 模块两条路径都提供，按算子选。
2. **机位数 = 可配置**：`render.views_per_pair` 控制。方向类编辑（move/rotate）默认走
   **单机位**以便用相机相对指令；其余可多机位。
3. **指令措辞 = 可配置（两种都要）**：`instruction.frame` ∈ {`camera_relative`, `scene_anchored`, `both`}。
   - camera_relative：用相机系语义方向（左右上下远近）；
   - scene_anchored：用放置模式锚定（放到桌上/地上/天花板/墙上/悬空）；
   - both：随机或并列生成。
   多机位时强制 scene_anchored（视角相对方向会矛盾）。
4. **GI/阴影 = 可配置**：`render.global_illumination` ∈ {`full`, `off`}。
   full 保留真实阴影/色彩外溢（默认，推荐）；off 求纯净差异。
5. **放置模式（取代原"吸附范围"）**：吸附不单列为编辑类型，而是 move 的放置模式之一
   （见 §3.1）。默认物体搁置在支撑上，move 可把它挪到 support/object_top/ceiling/wall/floating。
   各模式权重由 `edits.params.object_move.placement_weights` 配置。
