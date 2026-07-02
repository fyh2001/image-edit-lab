"""Blender 渲染任务类型（注册到通用执行器 `ray_exec`）。

一个 task = 在子进程里 `blenderproc run worker/run_job.py -- <spec.json>`（BlenderProc 硬性要求：
必须在 Blender 自带 Python 里跑，不能在 Ray worker 进程直接 import）。Ray 通过资源档分配的
GPU 已放进 CUDA_VISIBLE_DEVICES，子进程继承即可。

payload: {"spec": <JobSpec dict>, "proj_root": <项目根>, "timeout": <秒>}
返回:    {"job_id", "ok", "tail"}
"""
from __future__ import annotations
import os
import json
import tempfile
import subprocess

from common.ray_exec import register_task, register_stream_task


@register_stream_task("blender_render_stream")
def run_incremental(payload):
    """流式版：边跑子进程边读 stdout，每读到 `##PAIR##` 就 yield 这一对 → driver 实时消费。"""
    spec = payload["spec"]
    proj_root = payload.get("proj_root") or os.getcwd()
    spec_path = os.path.join(tempfile.gettempdir(), f"{spec['job_id']}.json")
    with open(spec_path, "w", encoding="utf-8") as f:
        json.dump(spec, f, ensure_ascii=False)
    proc = None
    try:
        cmd = ["blenderproc", "run",
               os.path.join(proj_root, "datagen", "worker", "run_job.py"), "--", spec_path]
        proc = subprocess.Popen(cmd, cwd=proj_root, env=dict(os.environ),
                                stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
                                text=True, bufsize=1)
        for line in proc.stdout:                          # 逐行读子进程输出
            if line.startswith("##PAIR## "):
                try:
                    pair = json.loads(line[len("##PAIR## "):])
                    pair["job_id"] = spec["job_id"]
                    yield pair                            # ← 一对产出立刻流出去
                except Exception:
                    pass
        proc.wait()
    finally:
        if proc and proc.poll() is None:
            proc.kill()
        try:
            os.remove(spec_path)
        except OSError:
            pass


@register_task("blender_render")
def run(payload):
    spec = payload["spec"]
    proj_root = payload.get("proj_root") or os.getcwd()
    timeout = int(payload.get("timeout", 1800))

    spec_path = os.path.join(tempfile.gettempdir(), f"{spec['job_id']}.json")
    with open(spec_path, "w", encoding="utf-8") as f:
        json.dump(spec, f, ensure_ascii=False)
    try:
        cmd = ["blenderproc", "run",
               os.path.join(proj_root, "datagen", "worker", "run_job.py"), "--", spec_path]
        r = subprocess.run(cmd, cwd=proj_root, env=dict(os.environ),
                           capture_output=True, text=True, timeout=timeout)
        ok = (r.returncode == 0)
        tail = (r.stdout or "")[-400:] + (r.stderr or "")[-400:]
        return {"job_id": spec["job_id"], "ok": ok, "tail": tail}
    except Exception as e:
        return {"job_id": spec["job_id"], "ok": False, "tail": f"EXC: {e}"}
    finally:
        try:
            os.remove(spec_path)
        except OSError:
            pass
