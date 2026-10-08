# -*- coding: utf-8 -*-
"""T1 参数扫描案例研究：IEEE 802.11p 周期信标在不同车辆密度下的信道饱和行为。

实验设计（3x3 全因子，每组合 3 个随机种子，共 27 次 run）：
    因子 A  信标频率  f ∈ {1, 5, 10} Hz      -> omnetpp.ini  *.appl.beaconInterval
    因子 B  车辆数    N ∈ {50, 300, 600}     -> erlangen.rou.xml（多入口随机行程）
    重复    seed-set 0..2                     -> --seed-set

每次仿真固定 200 s。原示例场景只有一条从单车道居民区道路出发的路线，
实测其插入能力在 200 s 内封顶约 95 辆，无法形成 300/600 两档密度；
因此改用 SUMO randomTrips.py 在全路网生成多入口随机行程（前 100 s 内发车、
行程不短于 1000 m），路由文件为 calib/routes_{N}.rou.xml，生成命令见 calib/gen_routes.sh。
单独运行 SUMO 实测：三档在 200 s 内分别插入 50/299/587 辆。

全部 27 个任务经平台 API 提交（worker 并发 5），既产出网络层结论，
也顺带演示平台的批量参数扫描能力。

用法：
    python exp_sweep.py submit      # 生成场景、提交全部 run、等待完成、下载结果
    python exp_sweep.py analyze     # 解析 .sca，汇总 PDR / 信道繁忙占比
"""

from pathlib import Path as _Path

HERE = _Path(__file__).resolve().parent   # 仓库中的 paper/ 目录
REPO = HERE.parent                        # 仓库根目录
import json
import re
import shutil
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))
from exp_platform import (client_with_token, create_project, create_and_execute_run,
                          wait_run, download_run, RESULTS)
from parse_sca import parse, aggregate

SCEN_SRC = (HERE / "scenario_long")
BUILD = (HERE / "sweep_scenarios")
OUT = RESULTS / "expSweep"
CFG = "WithBeaconing"

FREQS_HZ = [1, 5, 10]
VEHICLES = [50, 300, 600]
SEEDS = [0, 1, 2]
SIM_TIME_S = 200
ROUTES = (HERE / "calib")
PLAYGROUND_M = (3000, 3500)


def build_scenario(freq_hz, n_veh):
    """按 (信标频率, 车辆数) 生成一个场景目录。"""
    dst = BUILD / f"f{freq_hz}_n{n_veh}"
    if dst.exists():
        shutil.rmtree(dst)
    shutil.copytree(SCEN_SRC, dst)

    ini = dst / "omnetpp.ini"
    txt = ini.read_text(encoding="utf-8")
    interval = 1.0 / freq_hz
    txt = re.sub(r"^sim-time-limit\s*=.*$", f"sim-time-limit = {SIM_TIME_S}s",
                 txt, flags=re.M)
    txt = re.sub(r"^(\*\.(?:rsu|node)\[\*\]\.appl\.beaconInterval\s*=).*$",
                 rf"\1 {interval:g}s", txt, flags=re.M)
    # 随机行程会用到整张 Erlangen 路网（2607 m x 3010 m），示例配置的 2500 m x 2500 m
    # 仿真区域只够容纳原示例那一条路线；车辆驶出区域时 Veins 会报错终止
    txt = re.sub(r"^\*\.playgroundSizeX\s*=.*$", f"*.playgroundSizeX = {PLAYGROUND_M[0]}m", txt, flags=re.M)
    txt = re.sub(r"^\*\.playgroundSizeY\s*=.*$", f"*.playgroundSizeY = {PLAYGROUND_M[1]}m", txt, flags=re.M)
    ini.write_text(txt, encoding="utf-8")

    rou = dst / "erlangen.rou.xml"
    shutil.copyfile(ROUTES / f"routes_{n_veh}.rou.xml", rou)

    # 回读校验，避免"以为改了其实没改"
    got_iv = re.findall(r"appl\.beaconInterval\s*=\s*(\S+)", ini.read_text(encoding="utf-8"))
    got_n = len(re.findall(r"<vehicle ", rou.read_text(encoding="utf-8")))
    got_t = re.search(r"sim-time-limit\s*=\s*(\S+)", ini.read_text(encoding="utf-8")).group(1)
    assert all(v == f"{interval:g}s" for v in got_iv), f"beaconInterval 未写入: {got_iv}"
    assert abs(got_n - n_veh) <= n_veh * 0.02, f"路由车辆数不符: {got_n}"
    assert got_t == f"{SIM_TIME_S}s", f"sim-time-limit 未写入: {got_t}"
    return dst


def submit():
    OUT.mkdir(parents=True, exist_ok=True)
    c = client_with_token()
    jobs = []
    stamp = int(time.time())
    for f in FREQS_HZ:
        for n in VEHICLES:
            scen = build_scenario(f, n)
            pid = create_project(c, f"sweep-{stamp}-f{f}n{n}", config_name=CFG,
                                 scenario_dir=scen)
            for s in SEEDS:
                jobs.append({"freq_hz": f, "vehicles": n, "seed": s, "project_id": pid})
    print(f"built {len(FREQS_HZ) * len(VEHICLES)} scenarios, submitting {len(jobs)} runs",
          flush=True)

    t0 = time.monotonic()
    for j in jobs:
        j["run_id"] = create_and_execute_run(
            c, j["project_id"], f"sweep-f{j['freq_hz']}-n{j['vehicles']}-s{j['seed']}",
            seed_set=j["seed"])
    print("submitted:", [j["run_id"] for j in jobs], flush=True)

    for j in jobs:
        info = wait_run(c, j["run_id"], timeout_s=14400, poll=15)
        j["status"] = info["status"]
        j["start_time"] = info.get("start_time")
        j["end_time"] = info.get("end_time")
        dest = OUT / f"f{j['freq_hz']}_n{j['vehicles']}_s{j['seed']}"
        j["files"] = download_run(c, j["run_id"], dest) if info["status"] == "success" else []
        print(f"  f={j['freq_hz']}Hz n={j['vehicles']} seed={j['seed']}: {j['status']}",
              flush=True)
    total = time.monotonic() - t0

    data = {"design": {"freqs_hz": FREQS_HZ, "vehicles": VEHICLES, "seeds": SEEDS,
                       "sim_time_s": SIM_TIME_S,
                       "routes": "calib/routes_{N}.rou.xml (SUMO randomTrips, seed 42)",
                       "config": CFG},
            "total_wall_clock_s": round(total, 1),
            "n_runs": len(jobs), "runs": jobs}
    (OUT / "sweep.json").write_text(json.dumps(data, indent=2, ensure_ascii=False),
                                    encoding="utf-8")
    ok = sum(1 for j in jobs if j["status"] == "success")
    print(f"DONE {ok}/{len(jobs)} success, total {total:.0f}s -> {OUT}", flush=True)


def rerun(freq_hz, n_veh, seed, note):
    """补跑单个格子（原 run 未成功时），并在 sweep.json 中替换该条记录、写明原因。"""
    c = client_with_token()
    scen = build_scenario(freq_hz, n_veh)
    pid = create_project(c, f"sweep-rerun-{int(time.time())}-f{freq_hz}n{n_veh}", config_name=CFG,
                         scenario_dir=scen)
    rid = create_and_execute_run(c, pid, f"sweep-f{freq_hz}-n{n_veh}-s{seed}-rerun", seed_set=seed)
    info = wait_run(c, rid, timeout_s=14400, poll=10)
    dest = OUT / f"f{freq_hz}_n{n_veh}_s{seed}"
    files = download_run(c, rid, dest) if info["status"] == "success" else []
    data = json.loads((OUT / "sweep.json").read_text(encoding="utf-8"))
    for j in data["runs"]:
        if (j["freq_hz"], j["vehicles"], j["seed"]) == (freq_hz, n_veh, seed):
            j["original"] = {k: j[k] for k in ("run_id", "status")}
            j.update({"run_id": rid, "project_id": pid, "status": info["status"],
                      "start_time": info.get("start_time"), "end_time": info.get("end_time"),
                      "files": files, "rerun_note": note})
    (OUT / "sweep.json").write_text(json.dumps(data, indent=2, ensure_ascii=False), encoding="utf-8")
    print(f"rerun f={freq_hz} n={n_veh} seed={seed}: {info['status']}")


def analyze():
    """解析每次 run 的 .sca，按 (f, N) 汇总 mean±SD。"""
    import statistics as st
    cells = {}
    for d in sorted(OUT.glob("f*_n*_s*")):
        m = re.match(r"f(\d+)_n(\d+)_s(\d+)$", d.name)
        if not m:
            continue
        f, n, s = int(m.group(1)), int(m.group(2)), int(m.group(3))
        sca = next(iter(d.rglob("*.sca")), None)
        if sca is None:
            print("  MISSING .sca:", d.name)
            continue
        a = aggregate(parse(str(sca)))
        cells.setdefault((f, n), []).append({
            "seed": s,
            "vehicles_seen": int(a["modules_with_generatedBSMs"]),
            "generatedBSMs": a["generatedBSMs_total"],
            "receivedBSMs": a["receivedBSMs_total"],
            "PDR": a["PDR_total"],
            "PDR_rxtx_only": a["PDR_broadcast"],
            "receivers_per_bsm": (a["receivedBSMs_total"] / a["generatedBSMs_total"]
                                  if a["generatedBSMs_total"] else 0.0),
            "snir_lost": a["SNIRLostPackets_total"],
            "channel_busy": a["channelBusy_timeavg_mean"],
            "collisions": a["collisions_total"],
            "RXTXLost": a["RXTXLostPackets_total"],
        })

    def ms(vals):
        return {"mean": st.mean(vals),
                "sd": st.stdev(vals) if len(vals) > 1 else 0.0, "n": len(vals)}

    summary = []
    for (f, n), rows in sorted(cells.items()):
        summary.append({
            "freq_hz": f, "vehicles": n,
            "vehicles_seen": ms([r["vehicles_seen"] for r in rows]),
            "generatedBSMs": ms([r["generatedBSMs"] for r in rows]),
            "PDR_pct": ms([r["PDR"] * 100 for r in rows]),
            "channel_busy_pct": ms([r["channel_busy"] * 100 for r in rows]),
            "collisions": ms([r["collisions"] for r in rows]),
            "receivers_per_bsm": ms([r["receivers_per_bsm"] for r in rows]),
            "snir_lost": ms([r["snir_lost"] for r in rows]),
            "runs": rows,
        })
    (OUT / "sweep_metrics.json").write_text(
        json.dumps(summary, indent=2, ensure_ascii=False), encoding="utf-8")

    print(f"{'f(Hz)':>6} {'N':>5} {'seen':>7} {'BSMs':>12} "
          f"{'PDR%':>16} {'busy%':>16}")
    for r in summary:
        print(f"{r['freq_hz']:>6} {r['vehicles']:>5} "
              f"{r['vehicles_seen']['mean']:>7.0f} "
              f"{r['generatedBSMs']['mean']:>12.0f} "
              f"{r['PDR_pct']['mean']:>8.2f}±{r['PDR_pct']['sd']:<7.2f} "
              f"{r['channel_busy_pct']['mean']:>8.3f}±{r['channel_busy_pct']['sd']:<7.3f}")
    print("->", OUT / "sweep_metrics.json")


if __name__ == "__main__":
    cmd = sys.argv[1] if len(sys.argv) > 1 else "submit"
    if cmd == "rerun":
        rerun(int(sys.argv[2]), int(sys.argv[3]), int(sys.argv[4]), sys.argv[5])
    else:
        {"submit": submit, "analyze": analyze}[cmd]()
