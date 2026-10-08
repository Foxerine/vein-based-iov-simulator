# -*- coding: utf-8 -*-
"""v6 实验数据汇总：E1 网络指标（表 3）与 T3 资源画像（表 6）。

输入
  results_v6/E1_consistency_timing.json          E1 原始计时与 .sca 路径
  results_v6/T2_W*.json, T2_W*_platform/run_*/   T2 平台并行运行
  results/expProfile/timing_v6/samples.jsonl      采样器：每 2 s 一条容器资源记录
  results/expProfile/timing_v6/logs/*.log         采样器：带时间戳的容器输出
  results/expProfile/timing_v6/image.json         镜像体积
输出
  results_v6/E1_metrics.json, results_v6/T3_profile.json
"""

from pathlib import Path as _Path

HERE = _Path(__file__).resolve().parent   # 仓库中的 paper/ 目录
REPO = HERE.parent                        # 仓库根目录
import json
import re
import statistics as st
import sys
from collections import defaultdict
from datetime import datetime
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))
from parse_sca import parse, aggregate  # noqa: E402

EXP = HERE
R6 = EXP / "results_v6"
PROF = EXP / "results" / "expProfile" / "timing_v6"
WORKLOADS = ["W1", "W2", "W3", "W4"]


def e1_metrics():
    e1 = json.loads((R6 / "E1_consistency_timing.json").read_text(encoding="utf-8"))
    a = aggregate(parse(e1["cli"][0]["sca"]))
    out = {"sent": a["SentPackets_total"], "received": a["ReceivedBroadcasts_total"],
           "lost": a["TotalLostPackets_total"], "pdr_pct": a["PDR_total"] * 100,
           "busy_pct": a["channelBusy_timeavg_mean"] * 100}
    (R6 / "E1_metrics.json").write_text(json.dumps(out, indent=2), encoding="utf-8")
    print("E1 metrics:", out)
    return e1


def load_samples():
    by_run = defaultdict(list)
    for line in (PROF / "samples.jsonl").read_text(encoding="utf-8").splitlines():
        r = json.loads(line)
        if r.get("run_id"):
            by_run[int(r["run_id"])].append(r)
    return by_run


def run_ids(dirpath):
    return [int(p.name.split("_")[1]) for p in dirpath.glob("run_*")]


TS = re.compile(r"^(\d{4}-\d\d-\d\dT\d\d:\d\d:\d\d\.\d+)")


def ts(line):
    m = TS.match(line)
    return datetime.fromisoformat(m.group(1)[:26]).timestamp() if m else None


def startup_breakdown(log_path):
    """容器内冷启动分解：入口脚本首行 -> veins_launchd 就绪 -> 进入 opp_env -> 仿真初始化。"""
    lines = log_path.read_text(encoding="utf-8", errors="replace").splitlines()
    if not lines:
        return None
    t0 = ts(lines[0])

    def first(mark):
        return next((ts(l) for l in lines if mark in l), None)

    launchd = first("veins_launchd 启动成功")
    oppenv = first("进入opp_env shell环境")
    init = first("Initializing...")
    if None in (t0, launchd, oppenv, init):
        return None
    return {"launchd_s": launchd - t0, "oppenv_s": oppenv - launchd, "ned_s": init - oppenv,
            "startup_to_sim_s": init - t0}


def container_logs_for(rid):
    return sorted(PROF.glob(f"logs/veins-sim-*-r{rid}-*.log"))


def main():
    e1 = e1_metrics()
    samples = load_samples()
    image = json.loads((PROF / "image.json").read_text(encoding="utf-8"))

    per = {}
    for w in WORKLOADS:
        rids = run_ids(R6 / f"T2_{w}_platform")
        mem_peak, cpu_mean, pids_peak, startups = [], [], [], []
        for rid in rids:
            ss = samples.get(rid, [])
            mems = [s["mem_bytes"] for s in ss if s.get("mem_bytes")]
            cpus = [s["cpu_pct"] for s in ss if s.get("cpu_pct") is not None]
            pids = [s["pids"] for s in ss if s.get("pids")]
            if mems:
                mem_peak.append(max(mems) / 2 ** 20)
            if len(cpus) > 2:
                cpu_mean.append(st.mean(cpus[1:]))      # 首个样本的 precpu 基线不完整
            if pids:
                pids_peak.append(max(pids))
            for lp in container_logs_for(rid):
                b = startup_breakdown(lp)
                if b:
                    startups.append(b["startup_to_sim_s"])
        per[w] = {"runs": rids, "samples": sum(len(samples.get(r, [])) for r in rids),
                  "mem_peak_mib": st.mean(mem_peak) if mem_peak else None,
                  "mem_peak_max_mib": max(mem_peak) if mem_peak else None,
                  "cpu_mean_pct": st.mean(cpu_mean) if cpu_mean else None,
                  "pids_peak": max(pids_peak) if pids_peak else None,
                  "startup_s": st.mean(startups) if startups else None}
        print(w, per[w])

    # 冷启动分解取 E1 平台侧的 10 次顺序运行（无并发竞争）
    bds = []
    for p in e1["platform"]:
        for lp in container_logs_for(p["run_id"]):
            b = startup_breakdown(lp)
            if b:
                bds.append(b)
    sb = {k: st.mean(b[k] for b in bds) for k in bds[0]} if bds else {}

    t2_ok = [w for w in WORKLOADS if per[w]["mem_peak_mib"] is not None]
    plat_run = [p["breakdown"]["container_run_s"] for p in e1["platform"] if p.get("breakdown")]
    out = {
        "image_gib": image["size_bytes"] / 2 ** 30,
        "workload_ids": t2_ok,
        "per_workload": {w: per[w] for w in t2_ok},
        "mem_peak_min_mib": min(per[w]["mem_peak_mib"] for w in t2_ok),
        "mem_peak_max_mib": max(per[w]["mem_peak_max_mib"] for w in t2_ok),
        "cpu_mean_max_pct": max(per[w]["cpu_mean_pct"] for w in t2_ok),
        "startup_breakdown_runs": len(bds),
        **{k: v for k, v in sb.items()},
        "quota_not_binding_check": {
            "cli_wall_mean_s_no_quota": e1["cli_wall_s"]["mean"],
            "platform_container_run_mean_s_with_quota": st.mean(plat_run) if plat_run else None},
    }
    (R6 / "T3_profile.json").write_text(json.dumps(out, indent=2, ensure_ascii=False), encoding="utf-8")
    print("T3:", json.dumps({k: v for k, v in out.items() if k != "per_workload"}, ensure_ascii=False, indent=1))


if __name__ == "__main__":
    main()
