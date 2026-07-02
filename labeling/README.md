# labeling — VLM 打标（待建）

用 VLM 把模板指令改写得更自然/多样、清洗名词噪声、给样本打质量/属性标签。
注册为 common.ray_exec 的任务类型（如 `@register_task("vlm_label")`），复用通用流式执行器
+ 资源档 + pipeline。输入 datagen 产出的 (before, instruction, after)，输出增强后的标注。
