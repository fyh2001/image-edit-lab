---
name: qa-review
description: 对一批渲染产物(before/after 编辑对)做质量初筛——先跑廉价启发式标出可疑对，再由 agent 看图做语义判断(物体消失/悬空/穿模/换了样/落点不合理/对齐漂移)，产出"确认有问题"的对的 HTML 清单，供人工精筛。当用户想筛查/审查/QA 一批生成的数据、或想找出"可能有问题、不合理"的编辑对时使用。
---

# QA 初筛：自动标出可疑编辑对，供人工精筛

目标：从一批 `(before, instruction, after)` 编辑对里，**自动找出可能有问题/不合理的**，
让人只需复核一个短清单，而不是肉眼扫全部。分两步：**启发式初筛** → **agent 看图精判**。

## 输入
- `raw_dir`：产物目录（每个子目录含 `before_*.png` / `after_*.png` / `sample.json`）。
  用户没给就问，或默认 `./out/debug_gallery`。

## 步骤

### 1. 跑启发式初筛（零模型，秒级）
```bash
python -m datagen.orchestrator.qa_screen <raw_dir>
```
产出 `<raw_dir>/qa_flagged.json`（可疑对 + flag + 原因 + 严重度）和 `qa_flagged.html`。
启发式能抓的：变化几乎看不见(imperceptible)、移动过远/落点离谱(far_move/odd_placement)、
缩放过猛(extreme_scale)、变化占比过大(huge_change)、编辑区外漂移(bg_drift)、过暗(dark)、
删除后承载物落地(dropped_objects)。

### 2. agent 看图精判（抓启发式漏的语义问题）
读 `qa_flagged.json`。**要看图的两组**：
- **全部 flagged 对**（确认启发式的判断，剔除误报）；
- **未 flagged 里随机抽 ~20%**（抓启发式抓不到的语义问题：物体换了样/朝向怪/不真实/
  指令与效果不符——如"sandwich 挪到地上后样子变了"这种）。

对每个要看的对：用 Read 打开它的 `before_*.png` 和 `after_*.png`，结合 `instruction`，判断：
- **是否干净合理**：after 是否**只有指令说的那个改动**、其余像素对齐；主体是否清晰可见、
  尺寸合理、落点自然、无穿模/悬空/消失/换样。
- 给判定：`ok` 或 `problem` + **类别**(disappeared/floating/clipping/imperceptible/
  wrong_object/odd_placement/misaligned/unrealistic/too_dark/other) + 一句中文 `note` + severity(1~3)。

对量大时**并行**：用多个子 agent（Agent 工具）各审一批，避免一个个串行。每个子 agent 返回
其批次的 `problem` 判定列表(JSON)。

### 3. 汇总成终审 HTML
把所有 `problem` 判定写成 `verdicts.json`（数组，每项 `{dir, category, note, severity}`，
`dir` 用子目录名如 `job_0000004_p00_move`），然后：
```bash
python -m datagen.orchestrator.qa_screen --review <raw_dir> <verdicts.json>
```
产出 `<raw_dir>/qa_review.html`——只含 agent 确认有问题的对，按类别标注，供人工最终过一遍。

### 4. 回报
- 一句话统计：总对数 / 启发式标出 / agent 确认有问题 / 各类别数量。
- 给出 `qa_review.html` 路径（人工精筛用）。
- 如果发现**某类问题反复出现**（如"move 落点频繁不合理"），点出来——那是该回去改管线的信号，
  不是靠筛查能解决的。

## 原则
- 宁可多标（false positive 让人快速划掉），别漏真问题。
- 类别要具体，方便回溯到管线哪一环（disappeared/odd_placement→move 放置；clipping→物理；
  wrong_object→资产池；misaligned→相机/渲染）。
- 这是**离线质检工具**，不改数据、不删文件，只产出清单。
