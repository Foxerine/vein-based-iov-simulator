# -*- coding: utf-8 -*-
"""v6 计时实验批次：在修复后的平台（预热镜像、结果目录隔离、新 worker）上重测全部计时数据。

E1  表 3：短任务命令行 n=10 与平台 n=10 顺序执行；复核 20 次运行全部标量逐行一致；
    由 simulation.log 时间戳把平台单次耗时拆成排队、创建容器、容器运行、收尾、状态同步。
E2  并发曲线：短任务 N=1/2/5/10 同时提交。
E4  noVNC：GUI 任务从提交到会话可用的时间、页面加载与 WebSocket 握手，n=5。
T2  多负载并行评估：四档负载各做"命令行串行 5 次"与"平台并行 5 任务"（seed-set 0..4），
    两个阶段都以 2 s 间隔采样宿主机 CPU 实际频率（% Processor Performance）。

平台计时一律从发出执行请求起、以 1 s 间隔轮询到终态为止（v5 使用 10 s 轮询，标准差被放大）。
每项完成即写结果文件，重跑时跳过已完成项。

用法：python exp_timing_v6.py [e1|e2|e4|t2|all]
"""

from pathlib import Path as _Path

HERE = _Path(__file__).resolve().parent   # 仓库中的 paper/ 目录
REPO = HERE.parent                        # 仓库根目录
import json
import re
import shutil
import statistics as st
import subprocess
import sys
import time
from datetime import datetime
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))
import exp_platform as ep
from exp_platform import client_with_token, create_project, download_run, SCENARIO
import exp_sweep

ROOT = (HERE / "results_v6")
WORK = (HERE / "cli_work_v6")
SCEN_LONG = (HERE / "scenario_long")
IMAGE = "veins-worker-test-gui"
OPP_TAIL = ["-u", "Cmdenv", "-r", "0", "-n", ".:/opp_env_inst/veins-5.3/src/veins:/opp_env_inst/inet-4.5.4/src",
            "-l", "/opp_env_inst/inet-4.5.4/src/INET", "-l", "/opp_env_inst/veins-5.3/src/veins", "omnetpp.ini"]
POLL = 1.0


def stamp():
    return datetime.now().isoformat(timespec="seconds")


def save(path, data):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data, indent=2, ensure_ascii=False), encoding="utf-8")
    print(f"[{stamp()}] saved {path}", flush=True)


def ms(xs):
    return {"mean": round(st.mean(xs), 2), "sd": round(st.stdev(xs), 2) if len(xs) > 1 else 0.0,
            "min": round(min(xs), 2), "max": round(max(xs), 2), "n": len(xs)}


# ---------------------------------------------------------------- CPU 频率采样

class FreqSampler:
    """宿主机 CPU 实际频率占标称频率的百分比，2 s 一个样本。"""

    def __init__(self, path):
        self.path = path
        path.parent.mkdir(parents=True, exist_ok=True)
        ps = ("$c='\\Processor Information(_Total)\\% Processor Performance';"
              "while($true){$v=(Get-Counter -Counter $c).CounterSamples[0].CookedValue;"
              "\"$([DateTimeOffset]::Now.ToUnixTimeMilliseconds()),$v\" | "
              f"Out-File -Append -Encoding ascii '{path}'; Start-Sleep -Seconds 1}}")
        self.p = subprocess.Popen(["powershell", "-NoProfile", "-Command", ps],
                                  stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)

    def stop(self):
        self.p.kill()
        vals = []
        if self.path.exists():
            for line in self.path.read_text(encoding="ascii", errors="ignore").splitlines():
                try:
                    vals.append(float(line.split(",")[1]))
                except (IndexError, ValueError):
                    pass
        return ms(vals) if len(vals) > 1 else None


def base_mhz():
    out = subprocess.run(["powershell", "-NoProfile", "-Command",
                          "(Get-CimInstance Win32_Processor | Select-Object -First 1).MaxClockSpeed"],
                         capture_output=True, text=True).stdout.strip()
    return int(out) if out.isdigit() else None


# ---------------------------------------------------------------- 命令行与平台执行

def cli_run(scen_dir, config, seed, tag):
    work = WORK / tag
    if work.exists():
        shutil.rmtree(work)
    shutil.copytree(scen_dir, work)
    cmd = ["docker", "run", "--rm", "-v", f"{work}:/simulation/project", IMAGE,
           "--config-name", config, "-c", config, f"--seed-set={seed}"] + OPP_TAIL
    t0 = time.monotonic()
    proc = subprocess.run(cmd, capture_output=True, text=True, timeout=4 * 3600)
    wall = time.monotonic() - t0
    sca = next((work / "results").glob("*.sca"), None)
    return {"seed": seed, "returncode": proc.returncode, "wall_s": round(wall, 2),
            "sca": str(sca) if sca else None}


def wait_all(c, rids, t0, timeout_s=6 * 3600):
    done = {}
    while len(done) < len(rids):
        if time.monotonic() - t0 > timeout_s:
            raise TimeoutError(f"runs not finished: {set(rids) - set(done)}")
        for rid in rids:
            if rid in done:
                continue
            st_ = c.get(f"/run/{rid}").json()["status"]
            if st_ in ("success", "failed", "cancelled"):
                done[rid] = (st_, time.monotonic() - t0, time.time())
        time.sleep(POLL)
    return done


def submit(c, pid, seed, note):
    r = c.post("/run", json={"project_id": pid, "use_gui": False, "seed_set": seed, "notes": note})
    r.raise_for_status()
    return r.json()["id"]


LOG_MARKS = {"task_start": "仿真任务开始", "container_started": "容器启动成功",
             "container_done": "容器执行完成", "task_done": "无头模式仿真完成"}


def log_breakdown(log_text, submit_epoch, observed_epoch):
    t = {}
    for key, mark in LOG_MARKS.items():
        m = re.search(r"^\[(\d{4}-\d\d-\d\dT[\d:.]+)\] " + mark, log_text, re.M)
        if m:
            t[key] = datetime.fromisoformat(m.group(1)).timestamp()
    if len(t) < 4:
        return None
    return {"queue_s": round(t["task_start"] - submit_epoch, 2),
            "create_container_s": round(t["container_started"] - t["task_start"], 2),
            "container_run_s": round(t["container_done"] - t["container_started"], 2),
            "finalize_s": round(t["task_done"] - t["container_done"], 2),
            "status_sync_s": round(observed_epoch - t["task_done"], 2)}


def scalar_lines(path):
    return [l.rstrip() for l in open(path, encoding="utf-8", errors="replace") if l.startswith("scalar ")]


# ---------------------------------------------------------------- E1

def e1():
    out = ROOT / "E1_consistency_timing.json"
    if out.exists():
        return
    cli = [cli_run(SCENARIO, "Default", 0, f"e1_cli_{i}") for i in range(10)]
    for r in cli:
        print("cli", r, flush=True)
    c = client_with_token()
    pid = create_project(c, f"v6-e1-{int(time.time())}")
    plat = []
    for i in range(10):
        rid = submit(c, pid, 0, f"v6-e1-{i}")
        t0 = time.monotonic()
        sub_epoch = time.time()
        c.post(f"/run/{rid}/execute").raise_for_status()
        status, wall, obs_epoch = wait_all(c, [rid], t0)[rid]
        dest = ROOT / "E1_platform" / f"run_{rid}"
        files = download_run(c, rid, dest)
        bd = log_breakdown((dest / "simulation.log").read_text(encoding="utf-8"), sub_epoch, obs_epoch)
        sca = next(dest.glob("*.sca"), None)
        plat.append({"run_id": rid, "status": status, "wall_s": round(wall, 2), "breakdown": bd,
                     "sca": str(sca) if sca else None})
        print("platform", plat[-1], flush=True)
    ref = scalar_lines(cli[0]["sca"])
    all_sca = [r["sca"] for r in cli + plat]
    diffs = [sum(a != b for a, b in zip(ref, scalar_lines(s))) + abs(len(ref) - len(scalar_lines(s)))
             for s in all_sca]
    bds = [p["breakdown"] for p in plat if p["breakdown"]]
    save(out, {
        "timestamp": stamp(), "scenario": "RSUExampleScenario Default, 200 s, seed-set 0",
        "cli_wall_s": ms([r["wall_s"] for r in cli]),
        "platform_wall_s": ms([r["wall_s"] for r in plat]),
        "overhead_mean_s": round(st.mean(r["wall_s"] for r in plat) - st.mean(r["wall_s"] for r in cli), 2),
        "platform_breakdown_mean_s": {k: round(st.mean(b[k] for b in bds), 2) for k in bds[0]} if bds else None,
        "scalars_per_run": len(ref), "runs_compared": len(all_sca),
        "differing_scalar_lines_total": sum(diffs),
        "cli": cli, "platform": plat})


# ---------------------------------------------------------------- E2

def e2():
    out = ROOT / "E2_concurrency.json"
    if out.exists():
        return
    c = client_with_token()
    rows = []
    for n in (1, 2, 5, 10):
        pid = create_project(c, f"v6-e2-n{n}-{int(time.time())}")
        rids = [submit(c, pid, i, f"v6-e2-n{n}-{i}") for i in range(n)]
        t0 = time.monotonic()
        for rid in rids:
            c.post(f"/run/{rid}/execute").raise_for_status()
        done = wait_all(c, rids, t0)
        rows.append({"n": n, "total_wall_s": round(max(v[1] for v in done.values()), 2),
                     "success": sum(v[0] == "success" for v in done.values()),
                     "per_run_finish_s": sorted(round(v[1], 2) for v in done.values())})
        print(rows[-1], flush=True)
        time.sleep(5)
    save(out, {"timestamp": stamp(), "scenario": "short task (Default)", "rows": rows})


# ---------------------------------------------------------------- E4

def e4():
    out = ROOT / "E4_novnc.json"
    if out.exists():
        return
    import exp_novnc
    c = client_with_token()
    recs = []
    for i in range(5):
        rec = exp_novnc.measure_once(c, i)
        rec.pop("vnc_url", None)
        recs.append(rec)
        print(rec, flush=True)
        time.sleep(5)
    save(out, {"timestamp": stamp(), "n": len(recs),
               "vnc_ready_s": ms([r["vnc_ready_s"] for r in recs]),
               "page_load_ms": ms([r["page_load_s"] * 1000 for r in recs]),
               "ws_handshake_ms": ms([r["ws_handshake_s"] * 1000 for r in recs]),
               "all_ok": all(r["page_ok"] and r["ws_ok"] for r in recs), "runs": recs})


# ---------------------------------------------------------------- T2

def workloads():
    w2 = exp_sweep.build_scenario(1, 300)
    w4 = exp_sweep.build_scenario(5, 600)
    return [
        {"id": "W1", "desc": "原示例短任务：单入口，约 67 辆，200 s，无周期信标", "scen": SCENARIO, "config": "Default"},
        {"id": "W2", "desc": "多入口随机行程 300 辆，1 Hz 信标，200 s", "scen": w2, "config": "WithBeaconing"},
        {"id": "W3", "desc": "原长任务：单入口（实际进入 304–425 辆），10 Hz 信标，900 s", "scen": SCEN_LONG,
         "config": "WithBeaconing"},
        {"id": "W4", "desc": "多入口随机行程 600 辆，5 Hz 信标，200 s", "scen": w4, "config": "WithBeaconing"},
    ]


def t2_one(w, mhz):
    out = ROOT / f"T2_{w['id']}.json"
    if out.exists():
        return
    print(f"[{stamp()}] T2 {w['id']} serial", flush=True)
    fs = FreqSampler(ROOT / "freq" / f"{w['id']}_serial.csv")
    t0 = time.monotonic()
    serial = [cli_run(w["scen"], w["config"], s, f"t2_{w['id']}_s{s}") for s in range(5)]
    serial_total = time.monotonic() - t0
    f_serial = fs.stop()
    print(f"[{stamp()}] T2 {w['id']} serial total {serial_total:.1f}s", flush=True)

    c = client_with_token()
    pid = create_project(c, f"v6-t2-{w['id']}-{int(time.time())}", config_name=w["config"], scenario_dir=w["scen"])
    rids = [submit(c, pid, s, f"v6-t2-{w['id']}-s{s}") for s in range(5)]
    fs = FreqSampler(ROOT / "freq" / f"{w['id']}_parallel.csv")
    t0 = time.monotonic()
    for rid in rids:
        c.post(f"/run/{rid}/execute").raise_for_status()
    done = wait_all(c, rids, t0)
    parallel_total = max(v[1] for v in done.values())
    f_par = fs.stop()
    per_task = []
    for rid in rids:
        dest = ROOT / f"T2_{w['id']}_platform" / f"run_{rid}"
        download_run(c, rid, dest)
        m = re.findall(r"^\[(\d{4}-\d\d-\d\dT[\d:.]+)\] (容器启动成功|容器执行完成)",
                       (dest / "simulation.log").read_text(encoding="utf-8"), re.M)
        ts = {k: datetime.fromisoformat(v).timestamp() for v, k in m}
        if len(ts) == 2:
            per_task.append(round(ts["容器执行完成"] - ts["容器启动成功"], 2))
    serial_mean = st.mean(r["wall_s"] for r in serial)
    save(out, {
        "timestamp": stamp(), "workload": {k: str(v) for k, v in w.items()},
        "serial_total_s": round(serial_total, 1), "serial_runs": serial,
        "serial_single_mean_s": round(serial_mean, 2),
        "parallel_total_s": round(parallel_total, 1),
        "parallel_success": sum(v[0] == "success" for v in done.values()),
        "parallel_container_run_s": per_task,
        "speedup": round(serial_total / parallel_total, 2),
        "contention_factor": round(st.mean(per_task) / serial_mean, 2) if per_task else None,
        "cpu_base_mhz": mhz,
        "cpu_perf_pct_serial": f_serial, "cpu_perf_pct_parallel": f_par})


def t2():
    mhz = base_mhz()
    for w in workloads():
        t2_one(w, mhz)


if __name__ == "__main__":
    ROOT.mkdir(parents=True, exist_ok=True)
    which = sys.argv[1] if len(sys.argv) > 1 else "all"
    steps = {"e1": [e1], "e2": [e2], "e4": [e4], "t2": [t2], "all": [e1, e2, e4, t2]}[which]
    for fn in steps:
        print(f"[{stamp()}] === {fn.__name__} ===", flush=True)
        fn()
    print(f"[{stamp()}] ALL DONE", flush=True)
