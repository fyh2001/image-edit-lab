# TROUBLESHOOTING —— 踩坑与解决记录

本文档按类别记录项目从冒烟测试到规模化产出过程中**遇到的问题、根因、解决方法**，供排障与新人上手。
格式：**问题**（症状）→ **原因**（根因）→ **解决**（做法）。相关代码位置随文标注。

> 总览见 `README.md`，设计见 `DESIGN_object_edits.md`，进度见 `NEXT_STEPS.md`，本机导览见 `CLAUDE.md`。

---

## 1. BlenderProc / Blender API 踩坑

### 1.1 `import blenderproc` 必须第一行
- **问题**：worker 脚本报 BlenderProc 初始化相关的怪异错误。
- **原因**：BlenderProc 要求 `import blenderproc` 在任何其它 import 之前（它要接管 Blender 启动）。
- **解决**：`worker/run_job.py` 把 `import blenderproc as bproc` 放在文件最顶（去掉了 docstring / `from __future__`）。

### 1.2 BVHTree 碰撞检测"所有物体都碰撞"
- **问题**：`collides()` 恒为 True，或报 "Object does not have geometry data"。
- **原因**：BVHTree 用的是**局部坐标**，直接 overlap 会把所有物体当成在各自原点 → 恒判碰撞。
- **解决**：`validity._bvh_of` 用 bmesh **烘进 `matrix_world`**（世界系）再建 BVH。

### 1.3 Eevee-Next 无显示器下不重评估可见性
- **问题**：`object_add`（先隐藏后显示）产出的 before/after 完全相同，add 失效。
- **原因**：Eevee-Next 在 headless 下不重新评估可见性变化。
- **解决**：冒烟/生产都用 **`cycles_gpu`** 后端；并在 `_common_setup` 里关掉 `use_persistent_data`
  （持久化缓存不会因 `hide_render` 变化失效 → 两次渲染相同）。

### 1.4 `hide()` 后渲染不生效
- **问题**：改了 `hide_render` 但下一次 render 仍是旧状态。
- **原因**：两次 render 之间 depsgraph 不自动重评估可见性（尤其"先藏后显"）。
- **解决**：`_common.hide()` 改完标志后强制 `bpy.context.view_layer.update()`，并同步 `hide_viewport`。

---

## 2. macOS 本机环境

### 2.1 首次下载的 Blender.app 被 App Management 保护
- **问题**：BlenderProc 往 Blender bundle 里装包失败（权限）。
- **原因**：macOS App Management 保护新下载的 `Blender.app`。
- **解决**：`xattr -dr com.apple.provenance <Blender.app>` 去掉隔离属性。

### 2.2 SSL 证书
- **问题**：下载 Objaverse/HDRI/HSSD 报 SSL 证书错误。
- **解决**：跑任何联网前 `export SSL_CERT_FILE=$(python -c "import certifi;print(certifi.where())")`；
  `smoke.py` 已自动设置。

### 2.3 GPU 后端回退
- **现象（非 bug）**：本机 M5 Pro 无 OPTIX，`cycles_gpu` 自动回退 **Metal GPU**（~0.85s/帧）。
  OPTIX 仅 8×H100 真机有。

---

## 3. 资产处理（Objaverse / HSSD glb / 贴图）

### 3.1 Objaverse glb 是层级结构，盲取 objs[0] 崩溃
- **问题**："Object does not have geometry data"；或 join 后 `delete()` 触发 Blender 内部 double-free
  （idtype.cc unreachable 崩溃）。
- **原因**：Objaverse .glb 常是"空 transform 根节点 + 若干 mesh 子节点"的层级；`join_with_other_objects`
  会**消费**被并入对象，之后再 delete 它们就是二次释放。
- **解决**：`objaverse_provider` 只留有网格的 mesh，**先删非网格空节点、再 join**（顺序关键）。

### 3.2 资产原点/尺度混乱
- **问题**：旋转绕偏远轴公转、缩放锚点乱、不同资产尺寸差异巨大。
- **原因**：Objaverse 建模原点随意、尺度不一。
- **解决**：`_normalize` 把原点归到几何中心（`origin_set BOUNDS`）+ 最长边缩放到 `target_size`
  或按类别 `category_sizes`。

### 3.3 HSSD 贴图读不了（basisu / KTX2）
- **问题**：HSSD 家具 glb 用 Basis Universal(KTX2) 压缩，Blender 4.2 glTF 导入器不支持，
  且写在 `extensionsRequired` → 直接导入报错；剥掉后家具是平涂中性色、"塑料样板间"感。
- **原因**：`KHR_texture_basisu` 扩展 Blender 读不了。
- **解决**：两条路——① `strip_basisu` 把该扩展从 required/used 移除（降级，家具走中性色）；
  ② 用 KTX-Software 的 **`ktx` CLI 把内嵌 KTX2 解成 PNG 并重写进 glb**
  （`orchestrator/restore_hssd_textures.py`，二进制在 `tools/ktx/`，prefetch 下完自动调）。
  实测 561/677 glb 还原真实木纹/藤编/地毯，从"塑料"变"实拍感"。
- **注**：`ktx` 是 macOS-arm64 版；`install_name_tool` 改依赖会破坏代码签名（静默被杀）→ 需
  `codesign -s - -f` 重新 ad-hoc 签名。Linux/H100 换对应平台二进制。
- **弯路**：`pyktx` pip 装不上（`KeyError LIBKTX_VERSION`，要自己编 libktx）→ 改用预编译 CLI。

### 3.4 objaverse 名词噪声
- **现象（待办）**：LVIS 类别偶有误标/外文词（"solar array"、"domestic ass"、"truffle_(chocolate)"）。
- **现状**：`clean_noun` 启发式实测**净负**（品牌/采集 app 词混入），默认关闭；metadata 已存
  description/tags，留给 LLM 标注模块清洗。

### 3.5 批量下载 168 个 HSSD 房间的三连坑
- **① 单场景失败拖垮整批**：`prefetch_hssd.main` 原来 `for sid: fetch_scene(...)` 无 try，
  一个 scene json 拉取失败抛 RuntimeError → 整个 chunk 剩余全丢。→ 加 per-scene try/except。
- **② 复合 ID 场景需鉴权**：168 里 33 个简单数字 ID 公开可拉，135 个**复合 ID**（`103997403_171030405`）
  是 gated，裸 urllib 无 token → 403/失败。→ `_get` 带上 `Authorization: Bearer <HF token>`（`get_token()`）。
- **③ 并行下载被限流**：4 进程并行猛拉 → HTTP **429**，`_get` 当失败跳过（一轮丢 83 个）。
  → `_get` 加 **429 退避重试**（6s×attempt）+ 降到 2 进程。三坑修完 **168/168 全下齐**（8.5GB）。

### 3.6 ⚠️ HSSD 房间物体数差异巨大 → 本机吞吐崩
- **现象**：全 168 池渲染 ~0.5 对/分钟（原 7 间小房间时 ~5-6 对/分钟）。
- **原因**：原来那 7 间只有 ~40-150 物体；全 168 池**中位 276、均值 315、最大 1410 物体/房间**。
  加载几百个 glb + 渲复杂场景在本机**单 Metal GPU（4 worker 抢）**上极慢。
- **结论（非 bug）**：本机是开发/验证机，**大规模生产搬 8×H100**（OPTIX + Ray 铺 8 卡，快一两个数量级）。
  服务器上可 `pairs_per_scene` 提到 20-30 摊薄大房间加载、或加 `max_scene_objects` 过滤超大房间。

---

## 4. 场景（HSSD 室内）

### 4.1 Habitat Y-up → Blender Z-up 双重旋转
- **问题**：HSSD 家具与房间外壳错开一个坐标系（家具躺倒/穿墙）。
- **原因**：stage(房间外壳) 的 glTF 导入器已做 Y-up→Z-up；若再对 stage 乘转换矩阵 C 就**二次旋转 90°**。
- **解决**：stage 保持导入态（identity）；只对 object_instances 按 `matrix_world = C·M·C⁻¹` 放置，
  其中 `C = [[1,0,0,0],[0,0,-1,0],[0,1,0,0],[0,0,0,1]]`。

### 4.2 相机取景太局限 / 太模糊
- **问题**：只拍到房间一角、贴着墙、小物体糊。
- **原因**：默认相机站太近/贴墙。
- **解决**：`set_wide_fov`(~75° FOV) + `_sample_camera` 重写——**站房间内部朝主体拍**、离主体≥1.5m、
  要求主体在视锥内；主体偏向大件家具（`min_subject_size`）。

### 4.3 房间太暗 / 暗角发黑
- **问题**：大开间厨房几乎全黑（casserole 那张）。
- **原因**：HSSD stage 不带灯，原来只补**一盏** 300W 面光在天花板中央 → 照不匀、暗角黑。
- **解决**：`_add_fill_light` 改成**天花板自适应格网面光**（每~3m 一盏、能量随格子面积 350-1500W）
  + `_set_world_ambient(0.5)` 世界环境光抬暗部。同一暗厨房 mean 亮度近黑→191，过曝率≤2%。

---

## 5. 物理有效性 / 放置

### 5.1 放置穿模/悬空
- **问题**：物体穿地、悬空、穿模。
- **解决**：混合策略——解析式 `reseat`（向下射线贴支撑面）+ `collides`（世界系 BVH）+ `in_bounds`；
  add/replace 用 `settle_physics`（Bullet 沉降）+ 兜底 reseat。

### 5.2 稠密场景变换被"误杀"（yield 低）
- **问题**：真实房间家具本来就挨着，一放大/旋转/replace 就判碰撞被拒，物理丢弃占多数。
- **原因**：`collides` 零容差（BVH overlap 即拒），把**原场景就存在的接触**也算无效。
- **解决**：`validity.contacts()` 编辑**前**记下基线接触，move/scale/rotate/replace 都
  `collides(..., ignore=baseline)`——只在撞**新**邻居时才拒。同种子批 yield **26%→58%**。

### 5.3 wall/floating 放置崩溃 `ValueError: high - low < 0`
- **问题**：正式批渲染中途崩，`rng.uniform(low>high)`。
- **原因**：`placement.sample_move_target` 的 wall/floating z 范围
  `uniform(ground+half_h, ceiling-half_h)`，当**物体比房间还高**（如 replace 匹配到高家具的大物体）时
  low>high。老隐患，被 spawn/replace 带来的大尺寸主体触发。
- **解决**：加 `_safe_uniform`（low≥high 退化为中点，合法性交给 check() 把关），wall/floating/`_rand_xy`
  全用它。重跑后 0 崩溃。

---

## 6. 数据质量 / 标签正确性

### 6.1 指令指代不清（多个同类物体）
- **问题**：真实房间有多把椅子，"delete the chair" 指代不清——before/after 只删一把却教模型任选。
- **原因**：`ensure_unique_subject_category` 只在 tabletop/room 有，**HSSD 没有**。
- **解决**：`worker/edits/_reference.py:subject_phrase`——只对**画面里可见的同类**（视锥内+未被挡）消歧：
  没有可见同类就用 "chair"；有就加唯一区分的空间词（on the left/right、nearest/farthest，
  投影屏幕坐标+到相机距离取极值）；都无法区分就抛 EditInvalid 丢弃。接进 delete/move/scale/rotate/replace，
  move 在**挪动前**算（指的是 before 位置）。实测 76/199 指令带消歧。

### 6.2 场景内重复编辑同一物体
- **问题**：摊销 12 对有放回随机选，同一物体+同一算子可能出两次（近重复）。
- **解决**：`run_job` 维护 `seen` 集合，同 `(op, subject_name)` 只产一次（同物体换**不同**算子仍欢迎）；
  spawn/replace 模式主体是新外部物体、天然不重复、不参与。判在渲染前不浪费渲染。

### 6.3 广角下小编辑"变化不可见"
- **问题**：加个 0.22m 小物体在全景里只占~200px（<阈值 0.0025），全被判不可见丢弃。
- **解决**：① add 物体放大到 0.30 + **近景相机**(`_closeup_camera` 站 1.2-1.9m 框物)；
  ② 阈值降到 0.0012；③ move/scale 加 `projected_change_ratio` 预判（<0.001 提前重采，零额外渲染）。

### 6.4 surface-add 的 `subject` metadata 串味
- **问题**："add a spice rack" 的样本，`subject.category` 却是 "potted_plant"。
- **原因**：surface-add 的真主体是 `prepare()` 里现场 spawn 的，但主体快照拍在 `prepare()` **之前**
  （拍到的是重选来取景的物体）。
- **解决**：把 `_snapshot_subject` 挪到 `prepare()` **之后**（move/scale/rotate 的 prepare 是空操作，
  仍是编辑前状态，不受影响）。修后 subject.category 正确对上被编辑物体。

### 6.5 replace 出废指令 "change the tray into a tray"
- **问题**：同类替换时指令读起来像没变。
- **解决**：`same_category_prob` 命中同 noun 时，指令改成 "replace the X with a **different** X /
  swap for **another** X"。

---

## 7. 编辑算子 / 主体来源

### 7.1 表面感知放置（放到桌面/柜顶/冰箱上）
- **需求**：物体要能自然落在真实家具顶面。
- **实现**：`worker/physics/surfaces.py:find_support_point`——挑顶面够高够大的家具，footprint 内采点、
  向下射线确认法线朝上、**四角同高检查**（不悬边），底部贴面。参数含 `max_support_h`（顶面高度上限，
  避免衣柜顶近天花板拍不到）、`near`（只收相机框着那块区域附近的支撑面，大场景多房间必须）、
  `prefer_on_object`（偏向叠在桌上小物如笔记本电脑上）、`in_view`（视锥+遮挡射线）。
- **踩坑**：① 只用视锥不够——大场景视锥锥体延伸到别的房间，18m 外被墙挡的桌面也算"在锥内" →
  加 near 距离过滤 + 相机→落点遮挡射线；② 支撑面太高（衣柜顶 2.6m）相机拍不到 → `max_support_h` cap。

### 7.2 surface-aware ADD 忘 hide
- **问题**：spawn add 的 before/after 同图（ratio=0）。
- **原因**：spawn 路径忘了把新物体在 before 藏起来。
- **解决**：`_spawn.spawn_surface_subject(hide_after=True)`。

### 7.3 `prepare()` 抛 EditInvalid 没被捕获 → 整 job 崩
- **问题**：add 找不到表面抛 EditInvalid，run_job 只 try 了 apply()，没 try prepare() → 崩。
- **解决**：`_produce_pair` 把 prepare() 也包进 try（当丢弃处理）。

### 7.4 变换主体三来源（可配置混合）
- **需求**：变换既要能编辑场景已有物体（真实分布），也要能加/替换外部物体再操作（可控多样性）。
- **实现**：`subject_source` 加权字典（沿用 sampling_weights/placement_weights 惯例）——
  **scene** 直接编辑已有 / **spawn** 加外部物到空表面 / **replace** 用外部物替换已有物槽位（对齐尺寸）。
  共享 `worker/edits/_spawn.py`，`subject.origin` 记 scene/spawned/replaced 溯源。向后兼容旧 `spawn_subject_prob`。

---

## 8. 产率 / 摊销

### 8.1 一 job 一进程重载整间房太慢
- **问题**：每 job 重载几十~150 个 glb，~13.5s/job，几百对要几小时。
- **解决**：**摊销加载**——`pairs_per_scene>1` 时建场景一次、循环产 N 对，每对复位场景（快照/还原+删新增
  +清 rigidbody）→ 换主体 → 重新框相机 → 重采算子。好房间 ~7.6s/对（5-8×）。
- **验证无状态泄漏**：p17 删柜子，p18 的 before 仍是完整房间。

### 8.2 相机穿墙/主体被挡（yield 低、构图差）
- **问题**：前景一堵墙挡半屏，或主体被别的家具挡住。
- **解决**：`_subject_visible`——对主体包围盒 9 点各打相机→点射线，中途撞墙/家具就算被挡，
  可见占比不够就换机位。yield **26%→55%**、构图明显变好。

### 8.3 丢弃即浪费 slot
- **问题**：摊销循环跑固定 `pairs_per_scene` 次，一对被丢就损失产量。
- **解决**：每个 slot 最多重试 `pair_max_tries`(默认4) 次——被丢就复位换主体/算子重采。
  产出/slot **55%→90%**。关键：重试只是廉价渲染，昂贵的场景 load 只付一次 → 每对总成本反而降。
- **run_local 假象**：`run_local` 用 nohup 后台跑时，`&` 放进后台命令会让包装 bash 立即返回、
  貌似"1/96"，其实 detach 的 python 还在跑 → 用 `until ! pgrep` 等真结束。

---

## 9. 存储 / 数据库 / 溯源

### 9.1 数据库选型与部署
- **决策**：PostgreSQL，建在用户服务器 `130.94.66.57`。三表：`samples`（扁平质量/溯源列 + 完整 meta JSONB）、
  `assets`（资产目录）、`asset_usage`（已用账本，跨批次去重）。`_JSON = JSON().with_variant(JSONB,"postgresql")`。

### 9.2 SSH 隧道不跨 Bash 调用持久
- **问题**：分开的 Bash 调用里 SSH 隧道被沙箱清理，第二个调用连不上。
- **解决**：早期把隧道+ingest+query 放**同一个** Bash 调用；后来用户开了 5432 防火墙 → 改直连 SSL。

### 9.3 溯源不全（还原现场）
- **问题**：`scene.params.scene_id=null`（随机选），真正用哪间房丢了；生成配置/工具版本没存。
- **解决**：`_provenance()` 补全——`scene_source`(dataset+**解析后真实 scene_id**+data_dir+license)、
  `assets`(uid+license+hdri)、**完整生成 config**、`tooling`(blender/blenderproc/pipeline 版本)+seed。
  DB 加 `scene_id`/`source_dataset`/`pipeline_version` 扁平列，`_migrate_add_columns` 给老库**无缝加列**
  （`ADD COLUMN IF NOT EXISTS`）。HSSD 靠 scene_id+data_dir 即可重建整间房，不用存每个干扰物变换。

### 9.4 目录/分片/key 看不出任务类型
- **需求**：一眼看出是什么编辑任务。
- **解决**：算子短名注入三处——`write_pair` 拼目录名(`job_..._delete`)、`iter_samples` 拼 key
  (`..._delete_v0`，传导到分片文件名 + DB 主键)。`job_id` 字段保持干净。

### 9.5 破坏性 DB 操作被安全机制拦（正确行为）
- **现象**：`TRUNCATE` 被 auto-mode classifier 拦，理由"用户没明确要求清库"。
- **处理**：**没有绕过**；用 AskUserQuestion 明确让用户确认清空，得到明确授权后才执行。
- **教训**：从助手自己的措辞推断的"清库"意图不算用户明确请求，破坏性操作要显式确认。

---

## 10. 打包 / 上传

### 10.1 WebDataset 打包
- 同 key 三文件成组（`<key>.before.png` / `.after.png` / `.json`），可流式喂训练。
- `collector` 返回 {key:shard} 映射并回填 DB 的 `shard_path`。

### 10.2 上传只是提示、没实现
- **问题**：`collector` 只打印 `hf upload` 提示，没有真正上传/卡片/license 汇总。
- **解决**：写 `orchestrator/upload_hf.py`——从 DB 聚合统计 → 生成数据集卡片(README，含 license/溯源披露)
  → `create_repo(exist_ok=True)` + `upload_folder`（需 HF_TOKEN 或 `hf auth login`）。`--dry-run` 纯本地。

### 10.3 多批上传会互相覆盖
- **问题**：`collector` 每次从 `edit-000000.tar` 重编号，两批传同仓库根目录会覆盖同名分片 → 丢数据。
- **解决**：`--path-in-repo`（如 `data/batch-1`）让每批进不同子目录、仓库里累积；README 始终传根目录。
  仓库按 repo-id 复用（`exist_ok=True`），不是每次新建。

### 10.4 安全：token 不进对话
- **原则**：不让用户把 HF token 贴进对话（会进日志）；用户本地 `hf auth login` 或
  `export HF_TOKEN`，脚本自动拾取，助手全程不碰明文。

---

## 11. 性能与产率数据（本机 M5 Pro + Metal GPU，cycles_gpu ~0.85s/帧）

### 11.1 产率（yield）演进 —— 每步优化的实测提升
> yield = 产出对数 / 尝试 slot 数。真实稠密室内编辑天然拒绝率高，逐步优化把它从 1/4 拉到 ~9/10。

| 阶段 | 关键改动 | yield | 说明 |
|---|---|---|---|
| 初版 HSSD | 广角相机 + 摊销 | **~26%** | 74/288（24 job，seed 200） |
| + 相机遮挡检查 | `_subject_visible` 砍穿墙/被挡机位 | **~55%** | 33/60（seed 777）；构图也明显变好 |
| + 基线碰撞 | `contacts` + `ignore=baseline` | **~58%** | 56/96（seed 200，同种子对比） |
| + 丢弃即重试 | `pair_max_tries=4` 换主体/算子重采 | **~90%** | 87/96 产出/slot（5/6 场景填满 12/12） |
| 正式批（20 job） | 全部叠加 + 三模式 + 消歧 | **~83%** | 199/240（move 拥挤房间产率天然低，拉低总数） |

**注**：产出/slot 90% 是"每场景近满"，但单次尝试成功率仍 ~52%——retry 用**廉价渲染**换满产量，
而昂贵的**场景 load 只付一次**，所以每对总成本反而下降。

### 11.2 耗时拆解
| 环节 | 耗时 | 备注 |
|---|---|---|
| 场景加载（100+ glb） | **~13s** | 每 job/场景只付一次（摊销的意义） |
| 单帧渲染 | ~0.85s | 512×512、48 samples、Metal GPU |
| 单次渲染对（2帧+编辑+过滤+复位） | **~3.3s** | before + after + validity/quality + restore |
| 非摊销单对 | ~40-67s/对 | 每对都重载场景（旧方式） |
| 摊销好房间 | **~7.6s/对** | load 一次产多对（5-8×） |
| 单 job（12 对、16 次渲染尝试） | **66s** | 实测，含 4 次重试 |
| 单场景最坏（12 slot × 4 次全失败） | ~173s（~2.9 min） | `pair_max_tries` 是硬上限 |
| 正式批（20 job × 12，4 workers） | **~30-40 min** wall | 含 retry + Bullet 沉降 |

**8×H100 真机预期**：OPTIX 单帧远快于 Metal 的 ~0.85s，整体快数倍；Ray 集群并行铺满 job。

### 11.3 贴图还原
- HSSD glb：**561/677** 个成功还原真实贴图（KTX2→PNG），其余走中性色兜底。

### 11.4 正式批（第一版干净数据集）质量画像
> 20 job，199 对，configs/hssd.yaml 权重。

- **场景**：7 间真实 HSSD 房间
- **算子分布**：add 72 / delete 41 / scale 39 / replace 21 / rotate 18 / move 8（add 偏多、move 偏少）
- **主体来源**：spawned 104 / scene 88 / replaced 7（三模式都产出）
- **空间消歧指令**：76 / 199（多同类物体时加了 left/right/nearest/farthest）
- **平均清晰度**：286（laplacian）
- **溯源列填充**：scene_id / source_dataset / pipeline_version **199/199 全满**
- **崩溃**：0（wall/floating `_safe_uniform` 修复后）

### 11.5 已知的产率/质量权衡（非 bug）
- **move 产率低**：拥挤真实房间里挪家具多半撞邻居，`sampling_weights` 已调低；想多可提 `pair_max_tries`。
- **replace run-to-run 抖动**：Bullet 沉降非确定性 → 精确到像素的复现不保证（场景状态可重建）。
- **广角下小编辑占比小**：真实图像编辑本就局部，属固有；地毯/大家具最明显。

---

## 附：一次性首次配置清单
1. `python3 -m venv .venv && source .venv/bin/activate && pip install blenderproc pyyaml imageio numpy certifi`
2. `export SSL_CERT_FILE=$(python -c "import certifi;print(certifi.where())")`
3. 首次 `python scripts/smoke.py` 自动下载 Blender；macOS 需 `xattr -dr com.apple.provenance <Blender.app>`
4. HSSD 贴图还原需 `tools/ktx/ktx`（对应平台二进制）
5. DB：`.env` 里 `DATABASE_URL`；上传：`pip install huggingface_hub` + `hf auth login`
