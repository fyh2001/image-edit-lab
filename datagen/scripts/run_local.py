"""
本地顺序跑一个 config 的若干 job（不依赖 Ray），用于在单机验证整条链路。
生产仍走 orchestrator/ray_runner.py（Ray + 8×H100）；这个脚本只是开发期的轻量替代。

    python scripts/run_local.py --config configs/default.yaml --num-jobs 12 \
        --resolution 512 512 --samples 24

每个 job 用子进程跑 `blenderproc run worker/run_job.py`；产物落在 config 的 run.output_dir。
失败/被丢弃（无 sample.json）会换种子重试至多 --max-tries 次。
"""
from __future__ import annotations
import os
import sys
import json
import argparse
import tempfile
import subprocess
from dataclasses import asdict

import yaml

PROJ = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, PROJ)
from datagen.orchestrator.jobspec_gen import generate_jobspecs

# macOS python.org 证书链不全 → 子进程下载 Blender/资产会失败，这里补上
if not os.environ.get("SSL_CERT_FILE"):
    try:
        import certifi
        os.environ["SSL_CERT_FILE"] = certifi.where()
        os.environ.setdefault("REQUESTS_CA_BUNDLE", certifi.where())
    except Exception:
        pass


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--config", default="configs/default.yaml")
    ap.add_argument("--num-jobs", type=int, default=None, help="覆盖 run.num_jobs")
    ap.add_argument("--resolution", type=int, nargs=2, default=None, help="覆盖 render.resolution")
    ap.add_argument("--samples", type=int, default=None, help="覆盖 render.samples")
    ap.add_argument("--max-tries", type=int, default=2)
    ap.add_argument("--workers", type=int, default=1, help="并发跑几个 blenderproc（放量时提速）")
    ap.add_argument("--quiet", action="store_true", help="不打印每个 job 的渲染输出，只报进度")
    args = ap.parse_args()

    cfg = yaml.safe_load(open(os.path.join(PROJ, args.config), encoding="utf-8"))
    if args.num_jobs is not None:
        cfg["run"]["num_jobs"] = args.num_jobs
    if args.resolution is not None:
        cfg["render"]["resolution"] = list(args.resolution)
    if args.samples is not None:
        cfg["render"]["samples"] = args.samples

    out = cfg["run"]["output_dir"]
    os.makedirs(os.path.join(PROJ, out), exist_ok=True)
    specs = generate_jobspecs(cfg)

    import glob as _glob

    def _produced(job_id):
        # 摊销模式一场景产多对，落在 <job_id>_pNN/ 目录；单对则 <job_id>/。返回产出数。
        pat = os.path.join(PROJ, out, f"{job_id}*", "sample.json")
        return len(_glob.glob(pat))

    def run_one(spec):
        for t in range(args.max_tries):
            sd = asdict(spec)
            if t > 0:                                 # 重试换种子，躲内容彩票/罕见 transient
                sd["seed"] = spec.seed + t * 100000
            spec_path = os.path.join(tempfile.gettempdir(), sd["job_id"] + ".json")
            with open(spec_path, "w", encoding="utf-8") as f:
                json.dump(sd, f, ensure_ascii=False)
            for old in _glob.glob(os.path.join(PROJ, out, f"{sd['job_id']}*", "sample.json")):
                os.remove(old)
            r = subprocess.run(
                ["blenderproc", "run",
                 os.path.join(PROJ, "datagen", "worker", "run_job.py"), "--", spec_path],
                cwd=PROJ,
                stdout=(subprocess.DEVNULL if args.quiet else None),
                stderr=(subprocess.DEVNULL if args.quiet else None))
            n = _produced(sd["job_id"]) if r.returncode == 0 else 0
            if n > 0:
                return (sd["job_id"], spec.edit["name"], f"{n} 对", True)
        return (spec.job_id, spec.edit["name"], "<未产出>", False)

    from concurrent.futures import ThreadPoolExecutor, as_completed
    import time as _time
    ok, fail, summary, done, t0 = 0, 0, [], 0, _time.time()
    with ThreadPoolExecutor(max_workers=max(1, args.workers)) as ex:
        futs = [ex.submit(run_one, spec) for spec in specs]
        for fut in as_completed(futs):
            jid, edit, instr, produced = fut.result()
            done += 1
            ok += int(produced)
            fail += int(not produced)
            summary.append((jid, edit, instr))
            if done % 20 == 0 or done == len(specs):
                rate = done / max(1e-6, _time.time() - t0)
                print(f"进度 {done}/{len(specs)}  成功 {ok} 失败 {fail}  "
                      f"({rate:.2f} job/s)", flush=True)

    print(f"\n本地批量完成：成功 {ok} / 失败 {fail}")
    for jid, edit, instr in summary:
        print(f"  {jid}  [{edit}]  {instr}")


if __name__ == "__main__":
    main()
