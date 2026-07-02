# 指令/caption 设计 — 真实用户表达谱系（labeling 模块的依据）

目标：把几何真值（"物体 X 绕 Z 转 87°"）翻译成**真实用户会说的自然意图**，且**多风格采样**
（同一编辑给 2-4 种说法），让模型学"意图→编辑"的多对一映射，而不是背模板。

**分工**：worker 侧输出**客观事实**（哪个物体、操作、方向/大小、支撑关系、相机相对视角）→
VLM captioner **看 before/after 图**把事实说成自然话（补视觉 grounding + front/back 命名）→
`direction_check`/`validity` **校验**caption 不与真值矛盾（说反了就丢）。

---

## 通用表达风格（每种编辑都可套）
- **直接命令**：delete/move/add the X …
- **礼貌请求**：can you …, please …, 帮我把…
- **意图/愿望**：I don't want …, I'd like …, 我想…, 这里应该有…
- **目标/结果**：make it look …, clear the table, 让房间清爽点
- **口语**：把那玩意儿弄走 / 这花瓶放冰箱上吧

## 引用/消歧方式（怎么指认是哪个物体）
- **空间**：on the left/right、nearest/farthest、by the window、next to the sofa、in the corner、on the table
- **属性**：the red/wooden/big/small one（需物体颜色/材质 → objaverse tags / HSSD name）
- **关系**：the X on the Y、the X near the Y

---

## 各任务的表达谱系 + 所需事实

### DELETE（移除）
- 直接：remove / delete / erase / take out the X
- 意图：**我不想看到这个 X**；get rid of the X；the X shouldn't be here
- 目标：clear the table；declutter；把架子腾空
- **需要**：物体身份 + 空间 grounding（support/位置/干扰物消歧）+ 属性 ✅ 已有

### ADD（增加）
- 直接：add / put / place a X
- **落位 grounding**：add a X **on the table / next to the couch / on top of the fridge / in the corner**
- 意图：**在桌上加一个照相机**；this table needs a X；it'd look nice with a X here
- **需要**：新物体类别/描述 + 支撑物身份 + 空间关系 ✅ 已有(support_noun)

### MOVE（移动）
- 直接：move the X to Y
- **场景 grounding（源→目标）**：**把桌上的花瓶放到冰箱上**；move the lamp from the desk to the shelf
- **相机相对**：move the X left/right/closer/farther/**toward me/away**
- **地标相对**：next to the sofa / near the window / away from the door / to the center
- 意图：the X should be on the table；我想把 X 挪那边
- **需要**：source support + dest support（身份）+ 相机相对方向(semantic_direction) + 地标关系
  ✅ 大部分有；地标关系(near which object)可补

### SCALE（缩放）
- 直接：make the X bigger/smaller；enlarge/shrink
- **数值**：**放大1.5倍**；**缩小到一半**；twice as big；缩小到原来的0.75倍
- 定性/比较：the X is too small；make it the size of the Y；再大一点
- 意图：I want a bigger X
- **需要**：factor（精确）+ `factor_is_round`（是否敢报数）+ 定性档 slightly/moderately/much ✅ 已有
- **注**：数值 caption 要诚实——采样已**离散化**（`scale_choices` 整齐倍数为主 + `continuous_fraction`
  留连续）：整齐倍数才报"1.5倍"，连续的只说"明显放大"，绝不在 1.47× 的图上写"1.5倍"。

### ROTATE（旋转，表达最丰富）
- 直接：rotate / turn the X
- **数值**：**顺时针转90度**；rotate it 90° clockwise；逆时针180度
- **视角目标（相对观者）**：**我想看 X 的侧面 / 背面**；turn the X to face me；
  **把电视的背面对着我**；let me see the other side；转一下让我看看后面
- **语义朝向（相对他物）**：make the chair **face the table**；turn the sofa **toward the window**
- 意图：I want to see it from another angle；转个身
- **需要**：axis + angle + **相机相对视角变化**(view_change：opposite_side/side_face/partial) +
  **相机相对顺逆**(turn_direction) + `angle_is_round`；front/back/side 的**命名交给看图 VLM**
- **注**：
  - 采样已**离散化**（`angle_choices` 整齐角度为主）：整齐角度才报"90度"，连续的只说视角/定性。
  - **顺逆取决于视角**：只有旋转轴≈沿视线（正对你的钟面/画）时 turn_direction 才非空，能说"顺时针"；
    轴≈垂直视线（竖轴 yaw + 水平相机）时顺逆无意义 → 用 view_change 说"露侧面/背面对着我"。
  - 绕竖轴 180°→"露出相反的一面"→VLM 补"背面对着你"；90°→"露出侧面"→"看侧面"。

### REPLACE（替换）
- 直接：replace / swap / change X with/for/into Y
- 意图/偏好：I'd rather have a Y；instead of X, put a Y；**不喜欢这个 X,换成 Y**；把 X 变成 Y
- 同类换新：replace the X with a **different/nicer** one
- **需要**：旧物身份 + 新物类别 + 空间 grounding ✅ 已有

---

## 从谱系倒推的 metadata 缺口（worker 侧要补的事实）
1. **rotate 相机相对视角变化**（view_change）→ ✅ 已补（`_rotate_view_change`）
2. **rotate 相机相对顺逆**（turn_direction）+ **角度/倍数整齐标记**（angle_is_round/factor_is_round）
   + scale/rotate 采样**离散化**（整齐值为主）→ ✅ 已补
3. **move/add 的地标关系**（挨着哪个物体、靠哪面墙）→ 可补：主体最近的同框物体/结构件
4. **属性**（颜色/材质）→ 从 objaverse tags / HSSD name 提，供属性消歧
5. **朝向某物**（chair faces table）→ 需物体正面向量，较难；一期靠 VLM 看图推

## 语言：中文为主 + 英文少量（不必每条双语）
Qwen-Image-Edit 中英**共享文本空间**，编辑技能靠视觉 grounding、基本语言无关 → 中文学到的技能
能部分迁移到英文。所以**不必每条双语**（会摊薄每种语言覆盖）：**主语言集中 + 另一语言少量掺入**
（防微调时遗忘、保双语可用）。默认 `lang_weights: {zh: 0.8, en: 0.2}`，按亏空采样均衡，
`caption_lang` 写回元数据。改部署语言就调 `lang_weights`。VLM 直接按目标语言生成（无机翻噪声）。

## 一对几条 caption？→ **一对一条 + 数据集级风格均衡**（已定）
训练主集**每对只出一条**，captioner 按权重随机挑一种风格生成，并把 `style` 标签写进 metadata。
- **为什么不是一对多条**：视觉多样性充裕（能产 10 万+ 对），不需要靠文本复制凑量；一对多条会让
  同一 (before,after) 像素**重复 N 次**（过拟合风险 + split 泄漏 + N× VLM 花费），且措辞鲁棒性靠
  **整个数据集覆盖各风格**就能学到，不必同图重复。
- **风格均衡**：像算子均衡那样，按 `style` 亏空采样，保证 direct/spatial/intent/view/casual 各风格够。
- **一对多条留给**：① 措辞鲁棒性**评测集**(same-pair × N 说法，只进 eval 不进训练主集)；
  ② 补某个 (任务×风格) 稀缺片时做轻量文本增强。

## captioner 算子（labeling 模块）
`@register_task("vlm_caption")`：吃 (before_png, after_png, edit_metadata) → VLM → **一条**指令
（按 `style` 加权采样一种风格）→ 用 `direction_check`/`view_change`/support 校验一致性、说反就重生成/丢弃 →
写回样本（含 `style` 标签）。接在 `render → vlm_caption → pack` 的 pipeline 里，**不用重渲、随时可重标**。
