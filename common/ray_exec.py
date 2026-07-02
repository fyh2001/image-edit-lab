"""通用 Ray 流式任务执行器（**任务类型无关**）。

不绑死 Blender——这是通用编排能力，以后接训练 / RL / VLM 打标等都用它。
三个核心能力：
  1) **任务类型可插拔**：`@register_task("name")` 注册一个 runner；任务 = {type, profile, payload}。
  2) **每档资源可配**：`profile` → {num_cpus, num_gpus}，提交时用 `.options()` 动态设，无需为每档建 remote。
  3) **流式处理**：有界在飞窗口（max_in_flight）+ `ray.wait`，**一个完成就提交下一个**，
     吃 generator（可无限/超大流）、内存有界。

任务流是 generator，所以"用什么数据集的物体×场景""摊销还是逐算子"都在**生成任务流那一层**决定，
执行器只管调度。Blender 渲染只是众多任务类型之一（见 orchestrator/tasks/）。
"""
from __future__ import annotations
import time
import importlib

TASK_REGISTRY = {}


def register_task(name):
    """注册一个任务 runner：`fn(payload) -> result`。runner 必须是模块级函数（Ray 要能 import）。"""
    def deco(fn):
        TASK_REGISTRY[name] = fn
        return fn
    return deco


def _run_task(task, task_modules):
    """Ray worker 里执行：先 import 任务模块（触发注册），再按 type 分发。"""
    for m in (task_modules or []):
        importlib.import_module(m)
    fn = TASK_REGISTRY.get(task["type"])
    if fn is None:
        raise RuntimeError(f"未注册的任务类型: {task['type']}（task_modules={task_modules}）")
    return fn(task.get("payload", {}))


def _res_for(profile, resources):
    r = resources.get(profile) or resources.get("default") or {}
    return {"num_cpus": float(r.get("num_cpus", 1)), "num_gpus": float(r.get("num_gpus", 0))}


def run_stream(tasks, resources=None, max_in_flight=8, task_modules=None,
               on_result=None, log_every=1):
    """流式跑一串任务。

    tasks: 可迭代（建议 generator）of {type, profile, payload}。
    resources: {profile: {num_cpus, num_gpus}}，缺省用 'default'。
    max_in_flight: 最多同时在飞的任务数（有界窗口 = 背压）。
    task_modules: 需在 worker 里 import 以注册任务类型的模块名列表（如 ['datagen.orchestrator.tasks']）。
    on_result(task, ok, result): 每个任务完成回调。
    返回 {done, ok, fail}。
    """
    import ray
    resources = resources or {}
    remote = ray.remote(_run_task)

    it = iter(tasks)
    inflight = {}

    def submit_next():
        try:
            task = next(it)
        except StopIteration:
            return False
        r = _res_for(task.get("profile", "default"), resources)
        fut = remote.options(num_cpus=r["num_cpus"], num_gpus=r["num_gpus"]).remote(task, task_modules)
        inflight[fut] = task
        return True

    for _ in range(max(1, int(max_in_flight))):        # 预热窗口
        if not submit_next():
            break

    n_done = n_ok = n_fail = 0
    while inflight:
        done, _ = ray.wait(list(inflight.keys()), num_returns=1)
        fut = done[0]
        task = inflight.pop(fut)
        try:
            res = ray.get(fut)
            ok = True
        except Exception as e:
            res, ok = repr(e), False
        n_done += 1
        n_ok += int(ok)
        n_fail += int(not ok)
        if on_result:
            try:
                on_result(task, ok, res)
            except Exception:
                pass
        if log_every and n_done % log_every == 0:
            print(f"[ray_exec] 完成 {n_done}（成功 {n_ok} / 失败 {n_fail}），在飞 {len(inflight)}")
        submit_next()                                  # 一个完成 → 补一个（流式）

    return {"done": n_done, "ok": n_ok, "fail": n_fail}


# ============ 流式（增量）：一个任务边跑边吐多个结果，driver 逐个实时消费 ============
STREAM_REGISTRY = {}


def register_stream_task(name):
    """注册一个**流式** runner：`fn(payload)` 是 generator，`yield` 每个中间结果（如每产一对）。"""
    def deco(fn):
        STREAM_REGISTRY[name] = fn
        return fn
    return deco


def _run_stream_task(task, task_modules):
    """Ray worker 里执行流式任务：本身是 generator → Ray 自动包成 streaming ObjectRefGenerator。"""
    for m in (task_modules or []):
        importlib.import_module(m)
    fn = STREAM_REGISTRY.get(task["type"])
    if fn is None:
        raise RuntimeError(f"未注册的流式任务类型: {task['type']}")
    for item in fn(task.get("payload", {})):
        yield item


def run_stream_incremental(tasks, resources=None, max_in_flight=8, task_modules=None,
                           on_pair=None, log_every=50):
    """**增量流式**跑任务：任务级仍是有界窗口（max_in_flight），但结果按**每一项**实时回调。

    每个任务是 Ray streaming generator（边跑边 yield）。用 asyncio 起 max_in_flight 个 worker 协程，
    每个 worker 拉一个任务、`async for` 驱动它的 generator，产一项就 `on_pair(task, item)` 一项。
    on_pair 里可实时入库/打包/喂训练/监控。返回 {tasks, pairs, fail}。
    """
    import ray
    import asyncio
    resources = resources or {}
    remote = ray.remote(_run_stream_task)

    async def _main():
        it = iter(tasks)
        lock = asyncio.Lock()
        stats = {"tasks": 0, "pairs": 0, "fail": 0}

        async def worker():
            while True:
                async with lock:
                    try:
                        task = next(it)
                    except StopIteration:
                        return
                r = _res_for(task.get("profile", "default"), resources)
                gen = remote.options(num_cpus=r["num_cpus"],
                                     num_gpus=r["num_gpus"]).remote(task, task_modules)
                stats["tasks"] += 1
                try:
                    async for ref in gen:                 # 每 yield 一项 → 一个 ref
                        item = await ref                  # 取值（await = 异步 ray.get）
                        stats["pairs"] += 1
                        if on_pair:
                            on_pair(task, item)
                        if log_every and stats["pairs"] % log_every == 0:
                            print(f"[ray_exec] 实时消费 {stats['pairs']} 项"
                                  f"（任务 {stats['tasks']}，失败 {stats['fail']}）")
                except Exception as e:
                    stats["fail"] += 1
                    if on_pair:
                        on_pair(task, {"__error__": repr(e)})

        await asyncio.gather(*[worker() for _ in range(max(1, int(max_in_flight)))])
        return stats

    return asyncio.run(_main())


# ============ 多阶段 pipeline（stage A → B → C …，每阶段是一个注册的流式任务类型）============
_DONE = object()


def run_pipeline(items, stages, mode="staged", resources=None, max_in_flight=8,
                 task_modules=None, on_output=None):
    """把数据流过多个处理阶段（如 渲染→VLM打标→过滤）。每阶段 = 注册的**流式**任务类型。

    items: 初始输入项的可迭代（喂 stage[0] 的 payload，如各 job 的 spec）。
    stages: [{type, profile}, ...]，每个 type 是 register_stream_task 注册的（payload→yield 输出项）。
    mode:
      - "staged"   ：**批式between,流式within** —— A 全跑完(barrier)才开 B；每阶段内部有界窗口流式。稳、资源不跨阶段争。
      - "streaming"：**完全流式** —— 一项过完 A 立刻进 B，阶段重叠、单项延迟最低；每阶段独立资源档+窗口。
    on_output(item): 末阶段每产一项回调。
    """
    if mode == "streaming":
        return _pipeline_streaming(items, stages, resources or {}, max_in_flight, task_modules, on_output)
    return _pipeline_staged(items, stages, resources or {}, max_in_flight, task_modules, on_output)


def _pipeline_staged(items, stages, resources, max_in_flight, task_modules, on_output):
    cur = items                                          # stage0 吃 source；之后吃上阶段输出
    stats = {"stages": []}
    for stage in stages:
        outs = []
        prof = stage.get("profile", "default")
        tasks = ({"type": stage["type"], "profile": prof, "payload": it} for it in cur)
        s = run_stream_incremental(
            tasks, resources=resources, max_in_flight=max_in_flight, task_modules=task_modules,
            on_pair=lambda t, o, _o=outs: (None if isinstance(o, dict) and "__error__" in o else _o.append(o)))
        stats["stages"].append({"type": stage["type"], "in": s["tasks"], "out": len(outs)})
        cur = outs                                       # ← barrier：这阶段全好才喂下阶段
    if on_output:
        for o in cur:
            on_output(o)
    stats["final"] = len(list(cur)) if not isinstance(cur, list) else len(cur)
    return stats


def _pipeline_streaming(items, stages, resources, max_in_flight, task_modules, on_output):
    import ray
    import asyncio
    remote = ray.remote(_run_stream_task)
    n = len(stages)

    async def _main():
        queues = [asyncio.Queue(maxsize=2 * max_in_flight) for _ in range(n)]
        active = [max_in_flight] * n                     # 每阶段在岗 worker 数（用于 DONE 传播）
        produced = [0] * n

        async def source():
            for it in items:
                await queues[0].put(it)
            for _ in range(max_in_flight):               # 给 stage0 每个 worker 一个结束信号
                await queues[0].put(_DONE)

        async def worker(si):
            stage = stages[si]
            r = _res_for(stage.get("profile", "default"), resources)
            while True:
                item = await queues[si].get()
                if item is _DONE:
                    active[si] -= 1
                    if active[si] == 0 and si + 1 < n:   # 本阶段最后一个 worker → 关下阶段
                        for _ in range(max_in_flight):
                            await queues[si + 1].put(_DONE)
                    return
                gen = remote.options(num_cpus=r["num_cpus"], num_gpus=r["num_gpus"]).remote(
                    {"type": stage["type"], "payload": item}, task_modules)
                try:
                    async for ref in gen:
                        out = await ref
                        produced[si] += 1
                        if si + 1 < n:
                            await queues[si + 1].put(out)     # ← 立刻流向下一阶段
                        elif on_output:
                            on_output(out)
                except Exception:
                    pass

        coros = [asyncio.ensure_future(source())]
        for si in range(n):
            for _ in range(max_in_flight):
                coros.append(asyncio.ensure_future(worker(si)))
        await asyncio.gather(*coros)
        return {"produced_per_stage": produced}

    return asyncio.run(_main())


# ---- 内置 noop 任务，自测执行器（不需要 Blender/GPU）----
@register_task("noop")
def _noop(payload):
    time.sleep(float(payload.get("sleep", 0.05)))
    return {"i": payload.get("i"), "profile_ok": True}


@register_stream_task("noop_stream")
def _noop_stream(payload):
    """流式自测：一个任务吐 n 项，每项间隔 sleep，模拟"边产边出"。"""
    for k in range(int(payload.get("n", 3))):
        time.sleep(float(payload.get("sleep", 0.03)))
        yield {"task_i": payload.get("i"), "item_k": k}


@register_stream_task("_stage_incr")     # pipeline 自测阶段 A：v+1
def _stage_incr(payload):
    time.sleep(float(payload.get("sleep", 0.02)))
    yield {"v": payload.get("v", 0) + 1, "trace": payload.get("trace", "") + "A"}


@register_stream_task("_stage_x10")      # pipeline 自测阶段 B：v*10
def _stage_x10(payload):
    time.sleep(float(payload.get("sleep", 0.02)))
    yield {"v": payload.get("v", 0) * 10, "trace": payload.get("trace", "") + "B"}
