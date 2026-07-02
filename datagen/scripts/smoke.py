"""
本地冒烟测试：为 6 个编辑算子各渲一对 (before, after)，零下载、不依赖 Ray。

    python scripts/smoke.py

每个算子单独跑一个 `blenderproc run worker/run_job.py`，产物在 out/smoke_raw/。
跑完肉眼检查：before/after 是否只有主体变、对齐是否干净、sample.json 的 metadata
是否正确（方向/角度/倍数/放置模式/validity）。
"""
from __future__ import annotations
import os
import sys
import json
import tempfile
import subprocess
from dataclasses import asdict

import yaml

PROJ = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, PROJ)
from datagen.worker.context import JobSpec

# 首次运行 blenderproc 会下载 Blender；macOS 上 python.org 自带的 Python 默认证书链不全，
# urllib 会报 CERTIFICATE_VERIFY_FAILED。这里把 SSL_CERT_FILE 指向 certifi 的根证书，
# 让子进程（blenderproc run）继承，避免下载失败。已设置则不覆盖。
if not os.environ.get("SSL_CERT_FILE"):
    try:
        import certifi
        os.environ["SSL_CERT_FILE"] = certifi.where()
        os.environ.setdefault("REQUESTS_CA_BUNDLE", certifi.where())
    except Exception:
        pass

EDITS = ["object_move", "object_scale", "object_rotate",
         "object_delete", "object_add", "object_replace"]


def main():
    cfg = yaml.safe_load(open(os.path.join(PROJ, "datagen", "configs", "smoke.yaml"), encoding="utf-8"))
    out = cfg["run"]["output_dir"]
    os.makedirs(os.path.join(PROJ, out), exist_ok=True)

    # 每个算子最多尝试 MAX_TRIES 次：覆盖两类"产不出一对"的情况——
    #   1) 罕见的进程级 transient（returncode != 0）：同种子重试即可；
    #   2) 内容彩票导致的"变化不可见"丢弃（returncode==0 但没有 sample.json）：
    #      换种子重采样（如恰好抽到对称物体绕对称轴旋转）。
    # 换种子用 base_seed + i + try_idx*1000，保证可复现。
    MAX_TRIES = 3
    base_seed = cfg["run"]["base_seed"]
    ok, fail = 0, 0
    for i, ename in enumerate(EDITS):
        print(f"\n=========== 渲染 {ename} ===========")
        produced = False
        for t in range(MAX_TRIES):
            spec = JobSpec(
                job_id=f"smoke_{ename}",
                seed=base_seed + i + t * 1000,
                scene=cfg["scene"],
                assets=cfg["assets"],
                edit={"name": ename, "params": cfg["edits"]["params"].get(ename, {})},
                render=cfg["render"],
                output_dir=out,
                instruction=cfg.get("instruction", {}),
            )
            spec_path = os.path.join(tempfile.gettempdir(), spec.job_id + ".json")
            with open(spec_path, "w", encoding="utf-8") as f:
                json.dump(asdict(spec), f)

            # 产物目录带算子短名后缀（write_pair: <job_id>_<op>）→ 用 glob 匹配，别写死路径
            import glob as _glob
            for _d in _glob.glob(os.path.join(PROJ, out, spec.job_id + "*")):
                _sj = os.path.join(_d, "sample.json")
                if os.path.exists(_sj):
                    os.remove(_sj)            # 清掉上次产物，避免误判

            r = subprocess.run(
                ["blenderproc", "run",
                 os.path.join(PROJ, "datagen", "worker", "run_job.py"), "--", spec_path],
                cwd=PROJ,
            )
            # 成功 = 进程正常退出 且 真的落地了一对（<job_id>* 目录下有 sample.json）
            import glob as _glob
            landed = any(os.path.exists(os.path.join(_d, "sample.json"))
                         for _d in _glob.glob(os.path.join(PROJ, out, spec.job_id + "*")))
            if r.returncode == 0 and landed:
                produced = True
                break
            reason = (f"returncode={r.returncode}" if r.returncode != 0
                      else "无产物（被物理/可见性过滤丢弃）")
            print(f"[smoke] {ename} 第 {t + 1}/{MAX_TRIES} 次未产出（{reason}），重试…")

        if produced:
            ok += 1
        else:
            fail += 1
            print(f"[smoke] {ename} 最终失败（{MAX_TRIES} 次都没产出一对）")

    print(f"\n冒烟完成：成功 {ok} / 失败 {fail}")
    print(f"产物目录：{os.path.join(PROJ, out)}")
    print("逐个看 out/smoke_raw/smoke_*/ 下的 before_v0.png / after_v0.png / sample.json")


if __name__ == "__main__":
    main()
