# -*- coding: utf-8 -*-
"""T2 的补充基线：命令行方式一次并行启动 5 个容器（seed-set 0–4）。

回应"并行加速来自 CPU 而非平台"的质疑——用同样的 docker run 命令（与 T2 的命令行串行一致，不加配额）
同时启动 5 个仿真，与 T2 中的命令行串行、平台并行三者对比；并把每个种子的标量结果与 T2 平台并行
同种子的结果逐行比对。结果写入 results_v6/T2b_<负载>_cli_parallel.json，已完成的负载跳过。

用法：python exp_parallel_cli.py
"""

from pathlib import Path as _Path

HERE = _Path(__file__).resolve().parent   # 仓库中的 paper/ 目录
REPO = HERE.parent                        # 仓库根目录
import json
import time
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

import exp_timing_v6 as T


def scalars(path):
    return sorted(l for l in Path(path).read_text(encoding="utf-8", errors="replace").splitlines()
                  if l.startswith("scalar "))


def platform_results(wid):
    """T2 平台并行的 5 个任务按 run id 升序即 seed-set 0..4（依次提交）。"""
    dirs = sorted((T.ROOT / f"T2_{wid}_platform").glob("run_*"), key=lambda p: int(p.name[4:]))
    return [next(d.glob("*.sca")) for d in dirs]


def one(w, mhz):
    out = T.ROOT / f"T2b_{w['id']}_cli_parallel.json"
    if out.exists():
        return
    print(f"[{T.stamp()}] T2b {w['id']} command line, 5 containers at once", flush=True)
    fs = T.FreqSampler(T.ROOT / "freq" / f"{w['id']}_cli_parallel.csv")
    t0 = time.monotonic()
    with ThreadPoolExecutor(max_workers=5) as ex:
        runs = list(ex.map(lambda s: T.cli_run(w["scen"], w["config"], s, f"t2b_{w['id']}_s{s}"), range(5)))
    total = time.monotonic() - t0
    f_par = fs.stop()
    t2 = json.loads((T.ROOT / f"T2_{w['id']}.json").read_text(encoding="utf-8"))
    plat = platform_results(w["id"])
    identical = [bool(r["sca"]) and scalars(r["sca"]) == scalars(p) for r, p in zip(runs, plat)]
    T.save(out, {
        "timestamp": T.stamp(), "workload": w["id"],
        "cli_parallel_total_s": round(total, 1), "cli_parallel_runs": runs,
        "cli_parallel_returncodes_ok": all(r["returncode"] == 0 for r in runs),
        "serial_total_s": t2["serial_total_s"], "platform_parallel_total_s": t2["parallel_total_s"],
        "speedup_cli_parallel": round(t2["serial_total_s"] / total, 2),
        "speedup_platform_parallel": t2["speedup"],
        "platform_over_cli_parallel": round(t2["parallel_total_s"] / total, 3),
        "cpu_base_mhz": mhz, "cpu_perf_pct_cli_parallel": f_par,
        "scalars_identical_to_platform_same_seed": identical,
    })


def main():
    mhz = T.base_mhz()
    for w in T.workloads():
        one(w, mhz)
    print(f"[{T.stamp()}] T2b DONE", flush=True)


if __name__ == "__main__":
    main()
