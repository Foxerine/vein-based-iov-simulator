# -*- coding: utf-8 -*-
"""验证两处新修复：同项目并发运行的结果隔离；执行槽占满时的取消时延。

V1  在同一个项目里并发提交 seed-set 0..4 共 5 个任务（短任务），检查：
      - 每个任务的运行目录里都有自己的 .sca；
      - .sca 头部的 seedset 与提交时的 seed-set 一致；
      - 与同一镜像、同一 seed 的命令行运行逐行比对全部标量，完全一致。
V2  用 5 个长任务占满全部执行槽，取消其中一个，测量从发出取消到其容器消失的时间；
    随后取消其余任务。修复前停止请求要排队等到某个仿真结束才会执行。

用法：python exp_fixes.py
"""

from pathlib import Path as _Path

HERE = _Path(__file__).resolve().parent   # 仓库中的 paper/ 目录
REPO = HERE.parent                        # 仓库根目录
import json
import re
import shutil
import subprocess
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))
from exp_platform import client_with_token, create_project, wait_run, download_run, RESULTS, SCENARIO

OUT = RESULTS / "expFixes"
SCEN_LONG = (HERE / "scenario_long")
CLI_WORK = (HERE / "calib/cli_ref")
OPP = ["-u", "Cmdenv", "-r", "0", "-n", ".:/opp_env_inst/veins-5.3/src/veins:/opp_env_inst/inet-4.5.4/src",
       "-l", "/opp_env_inst/inet-4.5.4/src/INET", "-l", "/opp_env_inst/veins-5.3/src/veins", "omnetpp.ini"]


def scalars(path):
    return [l.rstrip() for l in open(path, encoding="utf-8", errors="replace") if l.startswith("scalar ")]


def seedset(path):
    m = re.search(r"^attr seedset (\d+)", open(path, encoding="utf-8", errors="replace").read(), re.M)
    return int(m.group(1)) if m else None


def cli_reference(seed):
    work = CLI_WORK / f"s{seed}"
    if work.exists():
        shutil.rmtree(work)
    shutil.copytree(SCENARIO, work)
    subprocess.run(["docker", "run", "--rm", "-v", f"{work}:/simulation/project", "veins-worker-test-gui",
                    "--config-name", "Default", "-c", "Default", f"--seed-set={seed}"] + OPP,
                   capture_output=True, text=True, timeout=600)
    return next((work / "results").glob("*.sca"))


def v1(c):
    pid = create_project(c, f"fix-sameproject-{int(time.time())}")
    rids = []
    for s in range(5):
        r = c.post("/run", json={"project_id": pid, "use_gui": False, "seed_set": s, "notes": f"same-project seed {s}"})
        rids.append(r.json()["id"])
    for rid in rids:
        c.post(f"/run/{rid}/execute").raise_for_status()
    rows = []
    for s, rid in enumerate(rids):
        info = wait_run(c, rid, timeout_s=900, poll=5)
        dest = OUT / "v1" / f"seed{s}_run{rid}"
        files = download_run(c, rid, dest)
        sca = next(dest.glob("*.sca"), None)
        ref = cli_reference(s)
        a, b = (scalars(sca), scalars(ref)) if sca else ([], scalars(ref))
        rows.append({"seed": s, "run_id": rid, "status": info["status"], "files": files,
                     "sca_seedset": seedset(sca) if sca else None,
                     "scalars": len(a), "differing_vs_cli_same_seed": sum(x != y for x, y in zip(a, b)) + abs(len(a) - len(b))})
        print(rows[-1], flush=True)
    distinct = len({tuple(scalars(next((OUT / "v1" / f"seed{r['seed']}_run{r['run_id']}").glob('*.sca'))))
                    for r in rows if r["files"]})
    return {"project_id": pid, "runs": rows, "distinct_result_sets": distinct,
            "all_ok": all(r["status"] == "success" and r["sca_seedset"] == r["seed"]
                          and r["differing_vs_cli_same_seed"] == 0 for r in rows)}


def containers_of(rid):
    out = subprocess.run(["docker", "ps", "-a", "--filter", f"label=veins.run_id={rid}", "--format", "{{.Names}}"],
                         capture_output=True, text=True).stdout.split()
    return out


def v2(c):
    rids = []
    for i in range(5):
        pid = create_project(c, f"fix-cancel-{int(time.time())}-{i}", config_name="WithBeaconing", scenario_dir=SCEN_LONG)
        rid = c.post("/run", json={"project_id": pid, "use_gui": False, "seed_set": i}).json()["id"]
        c.post(f"/run/{rid}/execute").raise_for_status()
        rids.append(rid)
    t0 = time.monotonic()
    while sum(c.get(f"/run/{r}").json()["status"] == "running" for r in rids) < 5:
        if time.monotonic() - t0 > 300:
            raise TimeoutError("runs did not all start")
        time.sleep(2)
    time.sleep(20)
    target = rids[2]
    before = containers_of(target)
    t_cancel = time.monotonic()
    resp = c.post(f"/run/{target}/cancel")
    t_api = time.monotonic() - t_cancel
    while containers_of(target) and time.monotonic() - t_cancel < 1800:
        time.sleep(0.5)
    t_gone = time.monotonic() - t_cancel
    others_running = sum(bool(containers_of(r)) for r in rids if r != target)
    for r in rids:
        if r != target:
            c.post(f"/run/{r}/cancel")
    time.sleep(5)
    leftover = sum(bool(containers_of(r)) for r in rids)
    return {"slots_busy": 5, "cancelled_run": target, "container_before": before,
            "api_status": resp.json().get("status"), "seconds_api_response": round(t_api, 2),
            "seconds_until_container_gone": round(t_gone, 2),
            "other_runs_still_running_at_that_moment": others_running,
            "containers_left_after_cancelling_all": leftover}


def main():
    OUT.mkdir(parents=True, exist_ok=True)
    c = client_with_token()
    res = {"V1_same_project_concurrent_seeds": v1(c)}
    print("V1 all_ok =", res["V1_same_project_concurrent_seeds"]["all_ok"],
          "| distinct result sets =", res["V1_same_project_concurrent_seeds"]["distinct_result_sets"], flush=True)
    res["V2_cancel_under_saturation"] = v2(c)
    print("V2:", json.dumps(res["V2_cancel_under_saturation"], ensure_ascii=False), flush=True)
    (OUT / "fixes.json").write_text(json.dumps(res, indent=2, ensure_ascii=False), encoding="utf-8")
    print("->", OUT / "fixes.json")


if __name__ == "__main__":
    main()
