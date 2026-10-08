# -*- coding: utf-8 -*-
"""T4 补充：worker 崩溃后的状态收敛与孤儿容器回收。

R1  提交一个长任务，确认其进入 running 后强杀仿真 worker 进程：
      - 测量后台状态对账把该任务标记为 failed 所用的时间；
      - 确认此时仿真容器仍在运行（孤儿）；
      - 以相同节点名重启 worker，测量孤儿容器被启动钩子回收所用的时间；
      - 确认任务日志里写入了对账说明，且重启后新任务可正常完成。
R2  提交一个短任务且全程不调用任何查询接口，直接读数据库确认状态已自行收敛为 success。

用法：python exp_reconcile.py   （系统栈需已启动；会改写 stack_logs/pids.json）
"""

from pathlib import Path as _Path

HERE = _Path(__file__).resolve().parent   # 仓库中的 paper/ 目录
REPO = HERE.parent                        # 仓库根目录
import json
import sqlite3
import subprocess
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))
from exp_platform import client_with_token, create_project, wait_run, RESULTS

REPO = REPO
PY = REPO / ".venv" / "Scripts" / "python.exe"
LOGS = (HERE / "stack_logs")
PIDS = LOGS / "pids.json"
DB = REPO / "data.db"
SCEN_LONG = (HERE / "scenario_long")
OUT = RESULTS / "expReconcile"


def sim_containers(run_id):
    out = subprocess.run(["docker", "ps", "-a", "--filter", f"label=veins.run_id={run_id}",
                          "--format", "{{.Names}}|{{.State}}"], capture_output=True, text=True).stdout
    return [line.split("|") for line in out.split()]


def start_sim_worker():
    log = open(LOGS / "simworker.restart.log", "ab")
    p = subprocess.Popen(
        [str(PY), "-m", "celery", "-A", "worker.worker.celery_app", "worker",
         "--loglevel=info", "-n", "veins-worker@%h"],
        cwd=REPO, stdout=log, stderr=log,
        creationflags=subprocess.DETACHED_PROCESS | subprocess.CREATE_NEW_PROCESS_GROUP)
    pids = json.loads(PIDS.read_text(encoding="utf-8-sig"))
    pids["simworker"] = p.pid
    PIDS.write_text(json.dumps(pids, indent=2), encoding="utf-8")
    return p.pid


def db_status(run_id):
    with sqlite3.connect(DB) as con:
        return con.execute("SELECT status FROM run WHERE id = ?", (run_id,)).fetchone()[0]


def r1(c):
    pid = create_project(c, f"reconcile-{int(time.time())}", config_name="WithBeaconing",
                         scenario_dir=SCEN_LONG)
    rid = c.post("/run", json={"project_id": pid, "use_gui": False, "notes": "worker-loss"}).json()["id"]
    c.post(f"/run/{rid}/execute").raise_for_status()
    while c.get(f"/run/{rid}").json()["status"] != "running":
        time.sleep(2)
    time.sleep(10)
    before = sim_containers(rid)

    worker_pid = json.loads(PIDS.read_text(encoding="utf-8-sig"))["simworker"]
    subprocess.run(["taskkill", "/F", "/PID", str(worker_pid)], capture_output=True)
    t_kill = time.monotonic()
    print(f"killed sim worker pid={worker_pid}; container before kill: {before}", flush=True)

    status = None
    while time.monotonic() - t_kill < 600:
        status = c.get(f"/run/{rid}").json()["status"]
        if status == "failed":
            break
        time.sleep(2)
    t_failed = time.monotonic() - t_kill
    orphan = sim_containers(rid)
    print(f"run marked {status} after {t_failed:.0f}s; containers now: {orphan}", flush=True)

    new_pid = start_sim_worker()
    t_restart = time.monotonic()
    while sim_containers(rid) and time.monotonic() - t_restart < 180:
        time.sleep(1)
    t_cleaned = time.monotonic() - t_restart
    left = sim_containers(rid)
    print(f"restarted worker pid={new_pid}; orphan removed after {t_cleaned:.0f}s; left: {left}", flush=True)

    log = c.get(f"/run/{rid}/files/simulation.log").text
    note = [line for line in log.splitlines() if "状态对账" in line]

    # 重启后系统健康：新任务可完成
    pid2 = create_project(c, f"reconcile-after-{int(time.time())}")
    rid2 = c.post("/run", json={"project_id": pid2, "use_gui": False}).json()["id"]
    c.post(f"/run/{rid2}/execute").raise_for_status()
    after = wait_run(c, rid2, timeout_s=600, poll=5)["status"]

    return {"run_id": rid, "container_before_kill": before,
            "final_status": status, "seconds_kill_to_failed": round(t_failed, 1),
            "container_after_marked_failed": orphan,
            "seconds_restart_to_orphan_removed": round(t_cleaned, 1),
            "containers_left": left or "none",
            "reconciler_log_note": note,
            "new_run_after_restart": after}


def r2(c):
    pid = create_project(c, f"nopoll-{int(time.time())}")
    rid = c.post("/run", json={"project_id": pid, "use_gui": False, "notes": "no-poll"}).json()["id"]
    c.post(f"/run/{rid}/execute").raise_for_status()
    t0 = time.monotonic()
    trace = []
    # 只读数据库，不调用任何会触发刷新的查询接口
    while time.monotonic() - t0 < 300:
        s = db_status(rid)
        trace.append((round(time.monotonic() - t0), s))
        if s.lower() == "success":
            break
        time.sleep(5)
    compact = [trace[0]] + [t for i, t in enumerate(trace[1:], 1) if t[1] != trace[i - 1][1]]
    return {"run_id": rid, "final_db_status": trace[-1][1].lower(),
            "seconds_to_success_in_db": trace[-1][0], "status_transitions": compact}


def main():
    OUT.mkdir(parents=True, exist_ok=True)
    c = client_with_token()
    res = {"R2_converges_without_polling": r2(c)}
    print("R2:", json.dumps(res["R2_converges_without_polling"], ensure_ascii=False), flush=True)
    res["R1_worker_loss"] = r1(c)
    print("R1:", json.dumps(res["R1_worker_loss"], ensure_ascii=False, indent=1), flush=True)
    (OUT / "reconcile.json").write_text(json.dumps(res, indent=2, ensure_ascii=False), encoding="utf-8")
    print("->", OUT / "reconcile.json")


if __name__ == "__main__":
    main()
