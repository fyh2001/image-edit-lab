# NEXT_STEPS — 后续工作 backlog（交接给 Claude Code）

按优先级排列。每完成一项，请更新本文件与 CLAUDE.md，保持上下文新鲜。

## P0 — 先把基本盘跑实

- [x] **冒烟测试全绿**：`scripts/smoke.py` 6 个算子稳定产出一对（cycles + primitives），
      对齐干净、无穿模/悬空、主体清晰、metadata 完整。详见 §"冒烟测试现状/已修"。
- [x] **metadata schema 补齐**（对照 DESIGN §5 的缺口）：相机内参(fx/fy/cx/cy/resolution)+rotation_euler、
      subject.init_transform/rotation_quat/support_before、move.final_transform、rotate.delta_quat、
      scale.per_axis、validity.penetration_depth/floating_gap 全部落地，对齐 DESIGN §5。
- [x] **接上真实名词**：`worker/assets/objaverse_provider.py` 加载 `category_map`，把 LVIS
      类别写进物体 `category`(规范串) + `noun`(下划线转空格的显示名)。纯逻辑抽到
      `worker/assets/categories.py`（`load_category_map / display_noun / resolve_noun`，
      `tests/test_categories.py` 7 测）。**用合成 .glb 端到端验证过**：指令变成
      "move the flower vase ...""，metadata `category=flower_vase`——顺带验证了
      `bproc.loader.load_obj` 对 .glb 的加载（原属待核对 API）。
- [x] **真实资产小跑**：`prefetch --n 30` 下了 30 个 Objaverse 资产 + 类别映射；
      `scripts/run_local.py`（新增，不依赖 Ray 的本地顺序 runner）跑 `configs/default.yaml`
      14 个 job → **14/14 全产出，6 算子覆盖到 5 个（delete 未被随机采到），metadata 全齐，
      真实名词生效**（"rotate the Bible" / "change the Band Aid into a Bible"）。
      本机 cycles 走 **Metal GPU**（M5 Pro），~0.85s/帧。**跑通时修了 3 个真实资产 bug，见下。**

### 真实资产小跑 — 修的 bug / 发现的缺口

修了（已落地）：
1. `objaverse_provider`：load_obj 返回的是**层级结构（空 transform 节点 + 多 mesh 子节点）**，
   不能盲取 `objs[0]`（空节点→"Object does not have geometry data"）。改为过滤出 mesh、
   多 part `join_with_other_objects` 合并、删空节点。
2. 同上的**删除顺序**：join 会消费 meshes[1:]，之后再 `delete()` 它们会触发 Blender
   内部 double-free（`idtype.cc ... unreachable` 崩溃）。改为 join 前只删非 mesh 节点。
3. `tabletop` 相机距离下限 10→3：真实资产归一到 1m（半径~0.6），下限 10 会把它们框得太小
   →编辑变化低于阈值被丢。下限 3 自适应（primitives 半径~2.2，`5×半径≈11`>3，冒烟取景不变）。

资产/质量缺口（4 个已修，2026-07-01）：
- [x] **#4 prefetch 类别多样性**：`sample_diverse_uids` 跨类别 round-robin 采样
  （`orchestrator/prefetch.py`，`tests/test_prefetch.py`）。实测 `--n 24` → **24 个不同类别**
  （bowl/giant_panda/dirt_bike/painting/vulture…），旧实现只有 2 类。
- [x] **#3 HDRI 链路**：下了 2 张 Poly Haven 1k HDRI 验证；`set_world_background_hdr_img` OK，
  `sample.json.hdri` 记录用了哪张，画面有真实室内背景+环境光（冒烟 6/6 仍绿）。
  注：smoke/default 的 `hdri_dir` 指向 `./assets/haven/hdris`——有就用、没有自动退平光。
- [x] **#1 资产原点/尺度归一**：`objaverse_provider._normalize` = 烘变换 + 原点归几何中心
  （`origin_set BOUNDS`，让 rotate 原地自转/scale 锚点可预测）+ 最长边缩放。新增
  `target_size`(统一，默认 1m，tabletop 用) 与 `category_sizes`(按类别真实尺度，room 级 opt-in，
  `categories.DEFAULT_CATEGORY_SIZES` + `category_target_size`，有单测)。
- [x] **#2 replace 尺度归一**：`presence_edits._match_size` 把新物体最长边对齐旧物，
  替换前后占地可比（实测 bowl→solar array 不再放大成 1m）。
- 仍保留的限制（**不算 bug，规模化前再议**）：
  - **up-axis 朝向**仍沿用 glb 导入朝向（glTF Y-up→Z-up），不做强制摆正——多数资产 OK，
    个别建模成侧躺/recumbent 的（如那只 panda）保持原样；真要摆正需 per-asset 元数据或启发式。
  - **类别真实尺度**默认只在 room 级场景开（tabletop 用统一尺寸，否则杯子/创可贴会小到看不清）。
- delete 算子两批都没被随机采到（冒烟已验证；real 资产下逻辑同 add，低风险）。

### 冒烟测试现状 / 已修（本机 macOS arm64 + Blender 4.2.1）

跑通做的关键修正（按影响排序）：
1. `run_job.py`：`import blenderproc` 必须是第一条 import（BlenderProc 硬性要求）。
2. `physics/validity.py::_bvh_of`：BVH 必须烘到**世界系**（原 `FromObject` 是局部系→全员误判碰撞）。
3. `scene/tabletop.py`：干扰物放远(r2.4–3.4)+碰撞拒绝采样；加太阳光；相机看主体中心、
   距离 `max(10, 5.0×半径)`、俯仰 22–45°；存 `ctx.extras["ground"]` 供物理沉降当碰撞体。
4. `render/backends.py`：关 `use_persistent_data`；**Eevee-Next 在 macOS 无显示器下两次
   render 间不重评估可见性 → 冒烟改用 `cycles_gpu`**（本机无 OptiX 自动回退 CPU/Metal+OIDN）。
5. `edits/_common.py::hide`：切换可见性后 `view_layer.update()`。
6. `assets/haven_provider.py::_flat_world`：调亮兜底天光（否则 cycles 近黑）。
7. 编辑可见性兜底：move/scale/rotate 都采"有意义"幅值（避开近恒等）；rotate 用 X/Y 倾倒轴。
8. `presence_edits.py` replace：物理沉降把地面纳入被动碰撞体（否则新物体穿地消失）。
9. `scripts/smoke.py`：自动设 `SSL_CERT_FILE`(certifi)；每算子最多重试 3 次（换种子）兜住
   罕见 transient / 内容彩票丢弃。

**环境一次性操作**（不在代码里）：首次下载的 `~/blender/.../Blender.app` 被 macOS App
Management 保护，需 `xattr -dr com.apple.provenance <Blender.app>` 解保护，BlenderProc 才能
往 bundle 内装包。

**纯 Python 单测**：`tests/`（frames/validity/categories/prefetch/quality），
`python -m pytest tests/ -q`（45 passed，不依赖 Blender）。

## P1 — 数据质量

- [x] **QualityFilter（廉价像素度量）**：`worker/quality/`（`metrics.py` 纯 numpy + `filter.py`），
      在 `run_job` 可见性过滤之后跑：sharpness(拉普拉斯方差)、background_diff(编辑区**外**平均差，
      catch 整体错位)、change_ratio 上限。分数始终写进 `sample.json.edit.validity.quality`，
      不过门槛则丢弃。阈值在 config `render.quality`，对干净数据留足余量（实测 6/6 + 12/12 全过）。
      9 个单测。**指令-效果一致性（CLIP/VLM）需模型，留作打包前独立阶段**（接口已规划，未接入）。
- [x] **指令消歧**：`tabletop` 采样干扰物时**保证主体类别唯一**（同类就换一个资产，
      凑不出异类就少放一个），所以 "move the {subject}" 不会指代不清。干扰物类别写进
      `sample.json.distractor_categories` 供审计（实测主体类别从不出现在其中）。
      开关 `scene.params.ensure_unique_subject_category`（默认 True）。
- [x] **方向语义校验**：`frames.direction_consistency`（纯函数，有单测）把主体编辑前后的
      世界坐标投影到画面，核对实际像素位移方向与语义词（左右上下）是否一致；run_job 对 move
      跑此校验，结果写进 `validity.direction_check`（含 pixel_delta）。实测 14 个 move 样本
      **up 5/5、left 6/6、right 3/3 全一致，0 反例**——确认了世界系→相机系→像素的方向映射正确。
      （closer/farther 是深度方向，不在 xy 判定，记 None。）
- [x] **指令/描述打磨**：① HSSD 接 `semantics/objects.csv` → 真实名词（"delete the table/stool"
      而非 "the object"），并优先挑有语义类别的家具当主体；② replace 禁止换成同类别（不再
      "change the ball into a ball"）；③ `display_noun` 去掉 LVIS 括号注释（"date_(fruit)"→"date"）；
      ④ **metadata 加物体描述**：`subject.description/tags/license`——objaverse 取 Sketchfab
      原始 name/tags/license（`assets/objaverse_meta.json`，prefetch 产出），HSSD 取物体原始名。
      意外收获：描述暴露了 **LVIS 自动标注的噪声**（被标 "sweatband" 的其实是射灯、"turnip"
      其实是核桃贝果）。
- [试过但默认关] **用 name/tags 校正名词**：写了启发式 `categories.best_noun`（category 在
      name/tags 里有呼应就信它，否则取干净 tag/词），但**实测净负**——品牌/采集 app/外文词会
      混进来（date→polycam、bowl→patrimonio、dalmatian→disney）。只有少数变好（sweatband→
      spotlights、domestic_ass→donkey）。**结论：噪声标题下 category 反而是更安全的默认**。
      故保留为 opt-in（provider `clean_noun=True`，默认 False），并把 description/tags 落进
      metadata；**真要清洗名词应上 LLM**（用户自带 key，离线跑一遍缓存进 meta 即可）。
      指令仍是模板拼接，自然度/多样性（LLM 改写）一并留作后续。

## P2 — 规模化与场景

- [x] **房间级放置模式（ceiling/wall/floating）已验证**：新增**合成 `room` 场景**
      （`worker/scene/room.py`：地面+4 墙+天花板+室内灯，零下载，`configs/room.yaml`），
      不依赖 3D-FRONT 就能跑通房间编辑。实测 16 个 room move：ceiling(顶部贴天花板 z=2.5)/
      wall(贴墙)/floating(悬空) 都正确落位、**不再掉到地上**，15/16 可见；8 算子混批 8/8。
      修了关键 bug：`MoveEdit` 原来对**任何非 floating 支撑都向下 reseat** → 会把 ceiling/wall
      放置拽回地面；改成只有 ground/object_top 才 reseat。room 还把主体归一到家具尺度(~1m)、
      用墙角机位取景（否则大块顶满画面 / 边角放置看不到）。
- [x] **HSSD 真实室内场景接通**（替代 gated 的 3D-FRONT）：新增 `worker/scene/hssd.py` +
      `orchestrator/prefetch_hssd.py` + `configs/hssd.yaml`。从 HuggingFace `hssd/hssd-hab`
      直链下载(当前免登可拉)，下了 1 个场景（stage + 41/67 家具，~54MB）。实测渲出**真实卧室/
      厨房**，家具摆放正确、可编辑（delete the bed / shrink → 干净对齐、删后墙地正确补全）。
      跑通修的点：① **Habitat Y-up→Blender Z-up**：家具世界矩阵 = `C·M·C⁻¹`，stage 是 identity
      实例**保持导入态**(别再乘 C，否则二次旋转和家具错开)；② HSSD 家具贴图是 **basisu(KTX2)**，
      Blender 4.2 导入器不支持且在 extensionsRequired → 报错，`prefetch_hssd` 自动**剥掉该扩展**
      （降级为可选，导入几何、跳贴图），loader 再给中性材质避免品红。
      局限：cluttered 真房间里 move/rotate/replace 拒绝率高（碰撞/越界），delete/scale 较稳；
      HSSD 物体没接语义名 → 指令是 "the object"（后续可从 `semantics/` 映射真实名词）。
- [ ] **3D-FRONT（可选，gated）**：`front3d.py` 代码已审过，要三件套数据集（数十 GB，需申请）。
      HSSD 已覆盖"真实室内"需求；3D-FRONT 等有数据再接。
- [ ] **ProcTHOR：本管线不可行**：场景是 JSON + AI2-THOR **Unity 模拟器**运行时加载，资产不以
      glb 分发，没有纯 Blender 路径（要整套 Unity + 显示）。如需其程序化房间，得走 Objaverse
      家具自己合成（类似 room 场景）或 Holodeck/Objathor 那套，不在当前管线范围内。
- [x] **WebDataset 打包链路（`collector`）已本机验证**：`orchestrator/collector.py` 重构——
      `iter_samples` 抽成纯函数（可单测，4 个测），`import webdataset` 挪进 `main`；打包时带
      **完整 metadata**（instruction/edit/validity+quality/subject/cameras/...，去冗余 views），
      下游可按 quality 分数/算子二次筛。实测 out/smoke_raw 6 样本 → 2 个 tar 分片，
      webdataset **读回 512×512 图 + metadata 全对**。CLI 支持 `--raw-dir/--shard-dir/--shard-size`。
- [x] **PostgreSQL 元数据库已上线**（服务器 `root@130.94.66.57`，见 `SETUP_DB.md`）：
      `orchestrator/db.py`（SQLAlchemy 模型 samples/assets/asset_usage，Postgres 用 JSONB）+
      `orchestrator/ingest.py`（读 sample.json 幂等入库，纯抽取函数可单测）。**worker 不碰 DB**，
      离线 ingest 解耦。质量/有效性数值拍平成列 → 可直接 SQL 筛（"sharp 且对齐好的 move"）。
      Postgres 按用户要求**对公网开放 5432**，加固为**强制 SSL(hostssl)+scram+28 位强密码**
      （非 SSL 连接被拒；实测直连 sslmode=require 通、明文被拒）。**建议后续改 IP 白名单**（见 SETUP_DB.md）。
      实测：远程库入 9 样本 / 31 资产 / 9 账本行,SQL 过滤与 JSONB 深查都通。
      无 DB 时自动回退 sqlite（单测走 sqlite，5 个测）。
      `asset_usage` 表即新版"已用账本"（比 `usage_ledger.py` 的 JSON 更好查/去重）。
- [x] **生产闭环已打通（软件层）**：prefetch(`--exclude-db` 读 `asset_usage` 跨批去重) →
      render(worker 写文件) → `ingest`(文件→DB) → `collector`(打包 + **回填 `samples.shard_path`**) →
      上传 HF。实测远程库：shard_path 正确回填(前 4 样本 edit-000000.tar、后 2 edit-000001.tar)，
      DB 去重查询返回已用 uid。**能按 SQL 筛样本 → 直接定位到 tar 分片**。
- [ ] **8×H100 上跑大批数据（本机太慢，已决定放服务器）**：管线全就绪，只差**规模化 RUN**。
      - **场景池已备**：168 个 HSSD 房间全下到 `assets/hssd`（8.5GB）。服务器上重下即可（`prefetch_hssd`
        已加 HF token 鉴权 + 429 退避重试 + per-scene 容错）。
      - **⚠️ 本机慢的根因**：168 池**中位 276 物体/房间、最大 1410**（原来那 7 间只有 ~40-150）。
        本机 Metal GPU 被 4 worker 抢，加载几百 glb + 渲复杂场景 → ~0.5 对/分钟（2000 对要 ~27h）。
        **8×H100 上**：OPTIX 单帧快数倍 + **Ray 把 job 铺到 8 张卡**（本机是单 GPU 串行）→ 快一两个数量级。
      - **服务器调优建议**：① Ray 已装并做成**通用流式执行器**（见下条），集群上 `max_in_flight=8` 一卡一路；
        ② 大房间加载贵，可把 `pairs_per_scene` 提到 20-30 摊得更薄，或加 `max_scene_objects` 过滤超大房间(可选小改)；
        ③ `ktx` 换 Linux 二进制。
      - **产出后**：`export_hf`（全 train Parquet）或 `collector`（WebDataset）→ `hf upload`（用户已登录 fyh2001）。
      - **本机已验证**：均衡生成/消歧/去重/溯源/表面放置/三模式/Parquet 导出/HF 上传全部跑通，
        只有大规模 RUN 因单 GPU 太慢而搬到服务器。
- [x] **Ray 通用流式执行器 + 每档资源可配 + 多数据集组合**（本机装了 Ray 2.56 验证）：
      - **通用执行器** `orchestrator/ray_exec.py`：任务类型无关（`@register_task` 注册，未来接训练/RL/VLM
        打标同一套）；**流式**（有界 `max_in_flight` 窗口 + `ray.wait`，一个完成搞下一个，吃 generator）；
        **每档资源** `.options(num_cpus,num_gpus)` 动态设。noop + blender 任务都验证：3 blender task 流式
        `在飞 2/1/0` 全成。
      - **多数据集组合** `jobspec_gen`：`scenes: [{name,weight,params}]` 场景多选按权重、`object_sources:
        [{provider,weight,params}]` 物体多选（`composite` provider `worker/assets/composite_provider.py`
        自动注入 add/replace/spawn）。**任务粒度** `run.granularity: amortized|per_edit`（A1 摊销按场景档 /
        A2 逐算子按算子档）、`ray.profile_by: scene|edit`。示例 `configs/multi_dataset.yaml`。
        实测：hssd:tabletop 按权重 4:1、composite 2 源注入、per_edit pairs=1，均正确；63 单测 + smoke 无回归。
      - **逐对增量流式**（`run_stream_incremental`）：worker 每产一对打 `##PAIR##` stdout 标记，
        `blender_render_stream` 流式任务读 stdout 逐对 yield，driver 用 asyncio 多路复用（任务级有界窗口、
        按对 `on_pair` 实时回调）→ 边产边消费（入库/打包/喂训练）。`ray.streaming: true` 开启。
        实测 4 blender 对 +4.5/4.6s、+9.0/9.1s 逐个实时到达。流 metadata/路径不流图字节（共享盘）。
      - **多阶段 pipeline**（`run_pipeline`，stage=注册的流式任务如 渲染→VLM打标→过滤）：两模式——
        `staged`（批式between/流式within，A 全好才开 B、资源不跨阶段争、稳）| `streaming`（完全流式，
        一项过完 A 立刻进 B、阶段重叠、延迟低）。asyncio 队列 + 每阶段 worker 池 + DONE 传播。
        两模式 noop 阶段验证结果一致、streaming 更快（重叠）。
- [ ] **domain gap**：渲染→提真（img2img/扩散）后处理，对 before/after 用同一结构
      约束保持对齐；先在小批上验证真实照片迁移效果。

## 真实场景放量的关键优化（P2.5，做 HSSD 规模化前必须）

现状：`tabletop` 是"影棚"（白地板+HDRI），不够真实；`hssd` 是真实房间但放量有两个瓶颈：
- **一 job 一进程会把整间房（几十~150 个 glb）重载一遍** → ~13.5s/job，几百对要几小时；
- **相机取景对小物体常糊**（贴着墙拍），杂乱房间编辑拒绝率高（yield 16~30%）。

要做的（按收益）：
- [x] **"一间房 → 多个编辑对"摊销加载已实现**：`render.pairs_per_scene` > 1 时，worker 建场景一次，
      循环产 N 对——每对**复位场景(快照/还原+删新增+清 rigidbody)→ 换主体 → 重新框相机(reset_keyframes)→
      重采算子**。`run_job` 主循环重构，场景 builder 挂 `editable_subjects`+`reframe_camera` 到 extras。
      实测好房间 **10/20 对、76s ≈ 7.6s/对（vs 非摊销 ~40-67s/对，≈5-8×）**；已验证**无状态泄漏**
      （p17 删柜子，p18 的 before 仍是完整房间）+ 相机每对换角度。单对(pairs_per_scene=1)行为不变，冒烟 6/6。
      注：**yield 随房间波动大**(好房 50%、挤房 8%)，`replace` 现在会命名新物("replace the chair with a spice rack")。
- [x] **室内实拍式取景**（原来只框到墙角）：HSSD 相机改成**广角(~75° FOV) + 站房间内部朝主体拍**，
      把整间房带进画面（`set_wide_fov` + `_sample_camera` 重写：房间内部采位、要求主体在视锥内、
      离主体≥1.5m）；主体**偏向大件家具**（`min_subject_size`，小摆件广角下看不清）。实测出真实客厅
      全景 + 清晰编辑（"remove the carpet" → 露出木地板，沙发/柜/茶几全不动）。阈值相应放低。
- [x] **HSSD 家具真实贴图已还原**（原来 basisu 读不了 → 平涂中性色，发假/塑料感）：用
      KTX-Software 的 `ktx` CLI 把 glb 内嵌的 **Basis/KTX2 贴图解成 PNG 并重写进 glb**
      （`orchestrator/restore_hssd_textures.py`，`ktx` 二进制在 `tools/ktx/`，prefetch_hssd 下完自动调）。
      实测 561/677 个 glb 还原，Blender 加载真实**木纹地板/木雕柜/藤编篮/图案地毯** → 从"塑料样板间"
      变"真实室内照片感"。loader 里有贴图的用真材质、没有的才兜底中性色。
      注：`ktx` 是 macOS-arm64 版；Linux/8×H100 上从 KTX-Software release 换对应平台的二进制。
- [x] **表面感知放置**（"把物体放到桌面/台面/柜顶/座面/冰箱顶"）：`worker/physics/surfaces.py`
      的 `find_support_point` 先挑"顶面够高够大能放下主体"的家具，在其顶面 footprint 内采点、
      向下射线确认（法线朝上 nrm.z≥0.75）、**四角同高检查**(不悬边/放得下)，把主体底部贴到面上；
      找不到合适支撑面就返回 None（move 回退到 support_surface）。接进 `move` 的 `object_top` 模式，
      指令按支撑家具类别改成"move the X **on top of the counter/bed**"。**加了 `in_view` 约束**：
      落点必须在广角相机视锥内，否则主体挪到画面外 after 里"凭空消失"像删除（是坏配对）。实测
      HSSD 厨房：书/地毯/盆栽稳稳落在中岛台面上、在画面内、且是唯一变化。hssd.yaml 把
      `object_top` 权重提到 1.5。**注**：几何机制对任意大小主体都work，但**语义自然度看主体大小**——
      小物件(书/杯/盆栽)放台面很自然，大家具(柜/长凳)放台面显怪。
- [x] **surface-aware ADD**（"往桌上/柜顶/冰箱上加一个原本没有的小物体"，甚至"叠到桌上的
      笔记本电脑上"）：`AddEdit` 加 `spawn=True` 模式——从 objaverse 取小物体(target_size 0.30)、
      `find_support_point` 放到附近可见表面、**把相机 reframe 成近景**(`_closeup_camera`：站 1.2-1.9m
      直视物体)、before 藏 after 显 → "add a casserole **on top of the counter**"/"place a bullhorn
      **on the fridge**"/"add a turnip **on top of the table runner**"(叠在桌上小物上)。指令按支撑物类别命名。
      **踩过的坑**：① spawn 路径忘了 hide → before/after 同图(ratio=0)；② `prepare()` 抛 EditInvalid
      没被 `_produce_pair` 捕获 → 整个 job 崩(已加 try)；③ 只用视锥判可见**不够**——大场景视锥锥体
      延伸到别的房间，18m 外被墙挡的桌面也算"在锥内" → 加了 **near 距离过滤**(只收框着的那块区域附近
      的支撑面)+ **相机→落点遮挡射线**；④ 广角全景里 0.22m 小物只占~200px(<阈值 0.0025)全被判不可见
      → 物体放大到 0.30 + 近景相机 + 阈值降到 0.0012。实测 yield 从 1/24→**6/24(~25%，与其他 HSSD 算子相当)**。
      hssd.yaml 开 `object_add: 1.5`(spawn/prefer_on_object/target_size 0.30)。**仍受 HSSD 单灯暗房影响**
      objaverse 名词偶有噪声("solar array"/"desk")。
- [x] **HSSD 照明修好**（原来单盏 300W 面光照不匀，大开间/暗角发黑，casserole 那张几乎全黑）：
      `_add_fill_light` 改成**天花板自适应格网面光**（每~3m 一盏、能量随格子面积 350-1500W）+
      `_set_world_ambient(0.5)` **世界环境光抬暗部**。同一暗厨房 mean 亮度从近黑→191，暗客厅→181，
      过曝率 ≤2%，不洗白。yield 也随之上升（暗房本来大量被 quality/可见性过滤掉）。
- [x] **相机遮挡检查（yield 26%→55%）**：`_subject_visible`——对主体包围盒 9 点各打一条相机→点
      射线，中途撞墙/家具就算被挡，可见点占比不够(sample 0.5 / closeup 0.6)就换机位。接进
      `_sample_camera` + `_closeup_camera`（都要求 `_in_frustum && _subject_visible`）。专治两类坏机位：
      ①相机卡墙里/家具后(前景一堵墙挡半屏)；②主体被别家具遮住。实测同规模批 yield **26%→55%**、构图明显变好。
- [x] **基线感知碰撞（yield 再翻倍到 58%）**：稠密真实室内里家具本来就贴着（椅塞桌下、沙发贴柜），
      原来 `collides` 零容差(BVH overlap 即拒)→ 放大/旋转/replace 一动就撞本就贴着的邻居被**误杀**
      （物理丢弃占 20/29）。新增 `validity.contacts(subject, others)`：编辑**前**记下基线接触，
      move/scale/rotate/replace 都改成 `collides(..., ignore=baseline)`——只在撞**新**邻居时才拒。
      同种子(200)批 yield **26%→58%**；抽查 shrink/rotate/move + 放大 1.46× 盆栽均无明显穿模。
- [x] **丢弃即重采（每场景产出 55%→90%）**：变换类算子本就用 `find_valid` 对碰撞/边界重采 30 次、
      相机对遮挡重采 60 机位——但一个"对"被丢弃后整个 slot 就浪费了。改成**摊销循环里每个 slot
      最多重试 `pair_max_tries`(默认4)次**：被丢弃就复位场景+换主体+重采算子再来，直到填满或用尽。
      实测 8 job：产出/slot **55%→90%**(5/6 场景填满 12/12)。**关键**：每次重试只是廉价渲染(~1-2s)，
      而昂贵的**场景加载(~13s，100+ glb)只付一次**——等于把 load 摊到满 12 对上，**每对总成本反而下降**。
      单对路径(pairs=1，如 smoke)不受影响。另加了 `projected_change_ratio` 预判(move/scale)当廉价早退，
      阈值取 0.001 略低于渲染像素下限，只提前拦明显看不见的、不误伤边界样本。
- [x] **溯源 / 还原现场**：`provenance` 块补全——`scene_source`(dataset + **解析后的真实 scene_id** +
      data_dir + license)、`assets`(objaverse uids + subject_uid + license + hdri)、**完整生成 config**
      (scene/render/edits 全部权重与参数)、`tooling`(blender/blenderproc/pipeline 版本) + `seed`。
      HSSD 靠 scene_id+data_dir 即可重建整间房(源 scene_instance.json 有全部摆放)，无需存每个干扰物变换。
      DB 加 `scene_id`/`source_dataset`/`pipeline_version` 扁平列(老库 `_migrate_add_columns` 无缝加列)，
      完整 provenance 也在 `meta` JSONB 里。目录/分片/key 已带算子短名。
      **注**：replace 用 Bullet 沉降有 run-to-run 抖动 → 精确到像素的复现不保证，但场景状态可重建。
- [x] **变换主体来源三模式（`subject_source` 加权字典）**：move/scale/rotate 的主体可来自三种来源，
      按权重混着产（形式沿用 sampling_weights/placement_weights 惯例，可扩展、per-算子可调）：
      **scene**=直接编辑场景已有物体（真实分布）；**spawn**=加外部小物体到空表面再操作（近景、~0.3m）；
      **replace**=用外部物体替换已有物体的**槽位**（占位+对齐尺寸、原机位框、家具级）再操作。
      `_spawn.spawn_surface_subject` / `_spawn.replace_subject_with_external`（add 也复用前者）。
      `subject.origin` 记 `scene`/`spawned`/`replaced` 溯源。hssd.yaml 设 {scene:.5, spawn:.25, replace:.25}。
      实测 12 对 5/5/2 三种都出、图验证干净；向后兼容旧 `spawn_subject_prob`；smoke 默认 scene 无回归。
- [x] **指令指代消歧（标签正确性）**：真实房间有多把椅子时 "delete the chair" 指代不清（before/after
      只改一个却教模型任选）。`worker/edits/_reference.py:subject_phrase`——只对**画面里可见的同类**
      （在视锥内 + 相机→中心视线没被挡）消歧：没有同类可见就用 "chair"；有就加能唯一区分的空间词
      （on the left / on the right / nearest / farthest，投影屏幕坐标 + 到相机距离取极值）；都无法区分
      就抛 EditInvalid 丢弃（宁丢不出歧义标签）。接进 delete/move/scale/rotate/replace(旧物侧)；move
      在**挪动前**算（指的是 before 的位置）。实测出 "make the chair on the left smaller"/"delete the
      nearest stool"/"erase the farthest stool"，唯一物体仍是 plain。add 用不定冠词"a"不涉歧义。
- [x] **场景内 (物体,算子) 去重**：摊销 12 对是有放回随机选，可能同一物体+同一算子出两次（近重复）。
      run_job 维护 `seen` 集合，同 `(op, subject_name)` 只产一次（同物体换**不同**算子仍欢迎）；
      spawn/replace 模式主体是新外部物体，天然不重复、不参与去重。早于渲染判、不浪费渲染。
- [x] **算子产出均衡（亏空采样）**：原来 `sampling_weights` 控"尝试概率"，但各算子 yield 差异巨大
      （add ~90%、move ~20%）→ 产出严重失衡（199 批 add 36% / move 4%，9× 差距）。改成
      `sampling_weights` = **目标产出占比**，`_sample_edit` 用**亏空采样**（挑当前最欠目标的算子 +eps）
      去命中。`run_job` 循环记 `produced_by_op`。hssd.yaml 设全 1.0（均衡）。实测 4 job：move 4%→15%(≈1/6)、
      整体 9×→~2× 差距，吞吐不掉（move 失败多是渲染前碰撞检查）。注：add 仍略高(23%)，yield 太高一选就中。
- [x] **HF 原生 Parquet 导出（比 WebDataset 更可浏览/可查）**：`orchestrator/export_hf.py`——
      用 `datasets` 建带 **Image 特征**的 Dataset（HF 网页 Viewer 能渲染缩略图 + `load_dataset` 一行读），
      列 schema 对齐标准编辑数据集（source_image / edit_instruction / target_image + edit_op/scene_id/
      subject_origin/quality/disambiguated/meta_json）。**默认全 `train`**——合成训练数据全用来训，
      真评测在外部真实 benchmark（MagicBrush/PIE-Bench 等）上做，在自己合成数据上留 test 量的是错的东西；
      想要过拟合探针用 `--holdout-scenes <id>` 把整间房留作 validation（按房间分、无泄漏）。`--dry-run`
      本地出 parquet、`--repo-id` push_to_hub。验证：parquet 读回 source_image 是 PIL 512×512、列齐全。
      WebDataset 打包(collector)保留作流式训练选项。
- [ ] **仍待优化**：① 广角下编辑占画面比例小是**固有**的（真实图像编辑本就局部），地毯/大家具最明显；
      ② 个别房间天花板灯具从下方看偏暗；③ 消歧的"可见同类"仅按中心点判，偶尔对**几乎出框**的同类
      也会加限定词（不算错、稍保守），可加最小投影面积门槛收紧。
- [x] **replace 替换域可配**（`same_category_prob`）：不写死同域——**跨域/不合常理的替换是有价值的
      OOD 训练样本**，保留为默认(=0 全换不同类)；想要合理替换就调高(=1 尽量同类"落地灯→台灯")，
      中间值按比例混。`_sample_replacement(same_cat)` 择优采样、永远拒绝换成同一资产、理想没抽到优雅回退。
      同类命中(同 noun)时指令改成"replace the X with a **different** X / swap for **another** X"，避免读成没变。
      hssd.yaml 设 0.3（objaverse 池小，同类命中率有限，多数会回退到不同类）。**待资产池变大后同类替换才明显。**
- [ ] **室外场景数据源**：目前没有。选项：Objaverse 户外环境模型（可分离性差）、Infinigen
      程序化自然（重）、或 ground+户外 HDRI（仍偏影棚）。需定方案。

## P3 — 增量编辑类型（验证扩展性）

- [ ] 换色/换材质算子（`@register_edit("object_recolor")`）。
- [ ] 风格/光照类编辑。

## 已知待核对的 BlenderProc API（实跑中遇到就地修正）

`load_obj`(glb)、`obj.hide()`、`set_world_background_hdr_img`、`BVHTree`、
`scene.ray_cast`、`bproc.camera.project_points`、`simulate_physics_and_fix_final_poses`、
Eevee 引擎名。调试单 job：`blenderproc debug worker/run_job.py -- <spec.json>`。

## 工作约定

- 随机走 `ctx.rng`（seed 固定保证可复现）。
- 加插件 = 类 + `@register_*` + `plugins.py` 加一行 import。
- 纯 Python 逻辑（frames、validity 的 change_is_visible/find_valid）写单测，不依赖 Blender。
- 每次较大改动后，更新 CLAUDE.md / DESIGN_object_edits.md / 本文件。
