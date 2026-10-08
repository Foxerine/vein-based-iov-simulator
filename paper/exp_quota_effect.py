# -*- coding: utf-8 -*-
"""T2 的诊断实验：命令行并行为何比平台并行慢约 5%。

两种方式的仿真命令与镜像完全相同，系统性的差别只有平台给每个容器下发的资源配额
（worker.worker.container_resource_kwargs()）。在同一负载上交替测三种做法，各重复 REPEATS 轮：
    cli          命令行一次启动 5 个容器，不加配额（与 exp_parallel_cli.py 相同）
    cli_quota    命令行一次启动 5 个容器，加与平台相同的配额（--cpus/--memory/--memory-swap/--pids-limit）
    platform     经平台 API 提交 5 个任务（当前平台：配额 + 隔离参数）
每轮记录总耗时与每个种子的容器运行时间，并把标量结果与 T2 平台并行同种子的结果逐行比对。
结果写入 results_v6/T2c_<负载>_quota_effect.json。

须用后端 venv 运行（需要 worker 模块）：python exp_quota_effect.py [W2]
"""

from pathlib import Path as _Path

HERE = _Path(__file__).resolve().parent   # 仓库中的 paper/ 目录
REPO = HERE.parent                        # 仓库根目录
import json
import os
import re
import shutil
import subprocess
import sys
import time
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime
from pathlib import Path

import exp_timing_v6 as T
from exp_parallel_cli import platform_results, scalars

REPO = REPO
sys.path.insert(0, str(REPO))
_cwd = os.getcwd()
os.chdir(REPO)   # 后端配置从当前目录的 config.cfg 读取
from worker.worker import container_resource_kwargs  # noqa: E402
os.chdir(_cwd)

REPEATS = 3


def quota_flags():
    k = container_resource_kwargs()
    flags = []
    if "nano_cpus" in k:
        flags += ["--cpus", str(k["nano_cpus"] / 1e9)]
    if "mem_limit" in k:
        flags += ["--memory", k["mem_limit"], "--memory-swap", k["memswap_limit"]]
    if "pids_limit" in k:
        flags += ["--pids-limit", str(k["pids_limit"])]
    return flags


def cli_run(scen_dir, config, seed, tag, extra):
    work = T.WORK / tag
    if work.exists():
        shutil.rmtree(work)
    shutil.copytree(scen_dir, work)
    cmd = ["docker", "run", "--rm", *extra, "-v", f"{work}:/simulation/project", T.IMAGE,
           "--config-name", config, "-c", config, f"--seed-set={seed}"] + T.OPP_TAIL
    t0 = time.monotonic()
    proc = subprocess.run(cmd, capture_output=True, text=True, timeout=4 * 3600)
    wall = time.monotonic() - t0
    sca = next((work / "results").glob("*.sca"), None)
    return {"seed": seed, "returncode": proc.returncode, "wall_s": round(wall, 2), "sca": str(sca) if sca else None}


def cli_batch(w, label, extra, k):
    t0 = time.monotonic()
    with ThreadPoolExecutor(max_workers=5) as ex:
        runs = list(ex.map(lambda s: cli_run(w["scen"], w["config"], s, f"t2c_{w['id']}_{label}_{k}_s{s}", extra),
                           range(5)))
    return time.monotonic() - t0, runs


def platform_batch(w, k):
    c = T.client_with_token()
    pid = T.create_project(c, f"v6-t2c-{w['id']}-{k}-{int(time.time())}", config_name=w["config"],
                           scenario_dir=w["scen"])
    rids = [T.submit(c, pid, s, f"v6-t2c-{w['id']}-{k}-s{s}") for s in range(5)]
    t0 = time.monotonic()
    for rid in rids:
        c.post(f"/run/{rid}/execute").raise_for_status()
    done = T.wait_all(c, rids, t0)
    total = max(v[1] for v in done.values())
    runs = []
    for s, rid in enumerate(rids):
        dest = T.ROOT / f"T2c_{w['id']}_platform_{k}" / f"run_{rid}"
        T.download_run(c, rid, dest)
        log = (dest / "simulation.log").read_text(encoding="utf-8")
        m = dict((k2, v) for v, k2 in re.findall(
            r"^\[(\d{4}-\d\d-\d\dT[\d:.]+)\] (容器启动成功|容器执行完成)", log, re.M))
        run_s = None
        if len(m) == 2:
            run_s = round(datetime.fromisoformat(m["容器执行完成"]).timestamp()
                          - datetime.fromisoformat(m["容器启动成功"]).timestamp(), 2)
        sca = next(dest.glob("*.sca"), None)
        runs.append({"seed": s, "run_id": rid, "status": done[rid][0], "container_run_s": run_s,
                     "sca": str(sca) if sca else None})
    return total, runs


def main():
    wid = sys.argv[1] if len(sys.argv) > 1 else "W2"
    w = next(x for x in T.workloads() if x["id"] == wid)
    ref = platform_results(wid)
    flags = quota_flags()
    rounds = []
    for k in range(REPEATS):
        for label in ("cli", "cli_quota", "platform"):
            print(f"[{T.stamp()}] {wid} round {k} {label}", flush=True)
            if label == "platform":
                total, runs = platform_batch(w, k)
                ok = all(r["status"] == "success" for r in runs)
            else:
                total, runs = cli_batch(w, label, flags if label == "cli_quota" else [], k)
                ok = all(r["returncode"] == 0 for r in runs)
            same = [bool(r["sca"]) and scalars(r["sca"]) == scalars(p) for r, p in zip(runs, ref)]
            rounds.append({"round": k, "mode": label, "total_s": round(total, 1), "all_ok": ok,
                           "scalars_identical_to_T2_platform": same, "runs": runs})
            print(f"   total {total:.1f}s ok={ok} identical={all(same)}", flush=True)
    summary = {}
    for label in ("cli", "cli_quota", "platform"):
        tot = [r["total_s"] for r in rounds if r["mode"] == label]
        summary[label] = {"total_s": tot, "mean_total_s": round(sum(tot) / len(tot), 1)}
    T.save(T.ROOT / f"T2c_{wid}_quota_effect.json", {
        "timestamp": T.stamp(), "workload": wid, "quota_flags": flags, "repeats": REPEATS,
        "t2_platform_parallel_total_s": json.loads((T.ROOT / f"T2_{wid}.json").read_text(encoding="utf-8"))[
            "parallel_total_s"],
        "summary": summary, "rounds": rounds})
    print(json.dumps(summary, indent=1))


if __name__ == "__main__":
    main()
