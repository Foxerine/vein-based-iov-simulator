# -*- coding: utf-8 -*-
"""回归测试：同一项目的多个任务同时运行时的编译竞争。

缺陷：同一项目的各次运行共用挂载进来的项目目录，旧入口脚本在其中执行 opp_makemake 与 make；
多个容器同时编译会并发改写 Makefile、out/ 与可执行文件链接，偶发"项目编译失败"
（T2c 诊断实验的第 2 轮平台批次中 5 个任务失败 1 个）。修复后改在容器内的私有副本中编译。

B0  命令行（不带 --result-dir）运行 seed-set 0：结果仍写入项目目录下的 results/，
    标量与修复前镜像的 T2 命令行结果逐行一致；
B1  ROUNDS 轮，每轮新建一个项目，同时提交 N 个任务（seed-set 0..4 循环），检查：
    全部成功、.sca 中的 seedset 正确、标量与同种子的修复前命令行结果逐行一致；
    运行结束后共享项目目录中没有出现 Makefile、out/ 或可执行文件。
结果写入 results/expBuildRace/buildrace.json。

用法：python exp_buildrace.py
"""

from pathlib import Path as _Path

HERE = _Path(__file__).resolve().parent   # 仓库中的 paper/ 目录
REPO = HERE.parent                        # 仓库根目录
import json
import re
import time
from pathlib import Path

import exp_timing_v6 as T
from exp_parallel_cli import scalars

OUT = (HERE / "results/expBuildRace")
PROJECTS = (REPO / "user_projects")
ROUNDS, N = 3, 10
BUILD_ARTIFACTS = ("Makefile", "out", "project")


def reference(seed):
    """修复前镜像的命令行结果（T2 W1 串行阶段）。"""
    return next((T.WORK / f"t2_W1_s{seed}" / "results").glob("*.sca"))


def seedset(sca):
    m = re.search(r"^attr seedset (\d+)", Path(sca).read_text(encoding="utf-8", errors="replace"), re.M)
    return int(m.group(1)) if m else None


def b0():
    r = T.cli_run(T.SCENARIO, "Default", 0, "buildrace_cli_s0")
    work = T.WORK / "buildrace_cli_s0"
    return {"returncode": r["returncode"], "sca_in_project_results": bool(r["sca"]),
            "identical_to_reference": bool(r["sca"]) and scalars(r["sca"]) == scalars(reference(0)),
            "build_artifacts_in_project": sorted(a for a in BUILD_ARTIFACTS if (work / a).exists())}


def b1_round(c, k):
    pid = T.create_project(c, f"buildrace-{k}-{int(time.time())}")
    seeds = [i % 5 for i in range(N)]
    rids = [T.submit(c, pid, s, f"buildrace-{k}-{i}") for i, s in enumerate(seeds)]
    t0 = time.monotonic()
    for rid in rids:
        c.post(f"/run/{rid}/execute").raise_for_status()
    done = T.wait_all(c, rids, t0)
    runs = []
    for s, rid in zip(seeds, rids):
        dest = OUT / f"round{k}" / f"run_{rid}"
        T.download_run(c, rid, dest)
        sca = next(dest.glob("*.sca"), None)
        runs.append({"run_id": rid, "seed": s, "status": done[rid][0],
                     "sca_seedset": seedset(sca) if sca else None,
                     "identical_to_reference": bool(sca) and scalars(sca) == scalars(reference(s))})
    project_dir = next(PROJECTS.glob(f"*/{pid}"))
    leftovers = sorted(a for a in BUILD_ARTIFACTS if (project_dir / a).exists())
    ok = all(r["status"] == "success" and r["sca_seedset"] == r["seed"] and r["identical_to_reference"]
             for r in runs) and not leftovers
    return {"round": k, "project_id": pid, "n": N, "total_s": round(max(v[1] for v in done.values()), 1),
            "all_ok": ok, "build_artifacts_in_shared_project_dir": leftovers, "runs": runs}


def main():
    OUT.mkdir(parents=True, exist_ok=True)
    res = {"timestamp": T.stamp(), "image": T.IMAGE}
    print(f"[{T.stamp()}] B0 command line", flush=True)
    res["b0_cli"] = b0()
    print("  ", res["b0_cli"], flush=True)
    c = T.client_with_token()
    res["b1_rounds"] = []
    for k in range(ROUNDS):
        print(f"[{T.stamp()}] B1 round {k}: {N} runs of one project at once", flush=True)
        r = b1_round(c, k)
        res["b1_rounds"].append(r)
        print(f"   ok={r['all_ok']} total={r['total_s']}s leftovers={r['build_artifacts_in_shared_project_dir']} "
              f"failed={[x['run_id'] for x in r['runs'] if x['status'] != 'success']}", flush=True)
    res["all_ok"] = (res["b0_cli"]["returncode"] == 0 and res["b0_cli"]["identical_to_reference"]
                     and not res["b0_cli"]["build_artifacts_in_project"]
                     and all(r["all_ok"] for r in res["b1_rounds"]))
    (OUT / "buildrace.json").write_text(json.dumps(res, indent=2, ensure_ascii=False), encoding="utf-8")
    print(f"[{T.stamp()}] all_ok={res['all_ok']}", flush=True)


if __name__ == "__main__":
    main()
