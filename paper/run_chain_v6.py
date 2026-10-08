# -*- coding: utf-8 -*-
"""T1 扫描结束后自动接续：分析 T1 → 采样器切换输出目录 → 计时批次（E1/E2/E4/T2）。"""

from pathlib import Path as _Path

HERE = _Path(__file__).resolve().parent   # 仓库中的 paper/ 目录
REPO = HERE.parent                        # 仓库根目录
import sys
import json
import subprocess
import time
from datetime import datetime
from pathlib import Path

EXP = HERE
TOOLS_PY = EXP / ".venv_tools" / "Scripts" / "python.exe"
BACKEND_PY = Path(sys.executable)
PIDS = EXP / "stack_logs" / "pids.json"
LOG = open(EXP / "stack_logs" / "chain_v6.log", "a", encoding="utf-8")


def log(msg):
    LOG.write(f"[{datetime.now().isoformat(timespec='seconds')}] {msg}\n")
    LOG.flush()


def alive(pid):
    out = subprocess.run(["tasklist", "/FI", f"PID eq {pid}", "/NH"], capture_output=True, text=True).stdout
    return str(pid) in out


pids = json.loads(PIDS.read_text(encoding="utf-8-sig"))
log(f"waiting for sweep pid {pids['sweep']}")
while alive(pids["sweep"]):
    time.sleep(30)
log("sweep finished; analyzing T1")
subprocess.run([str(TOOLS_PY), "exp_sweep.py", "analyze"], cwd=EXP,
               stdout=open(EXP / "stack_logs" / "sweep_analyze.out", "w"), stderr=subprocess.STDOUT)

subprocess.run(["taskkill", "/F", "/T", "/PID", str(pids["profiler"])], capture_output=True)
f = open(EXP / "stack_logs" / "profile_timing.log", "ab")
p = subprocess.Popen([str(BACKEND_PY), "exp_profile.py", "timing_v6"], cwd=EXP, stdout=f, stderr=f,
                     creationflags=subprocess.DETACHED_PROCESS | subprocess.CREATE_NEW_PROCESS_GROUP)
pids["profiler"] = p.pid
PIDS.write_text(json.dumps(pids, indent=2), encoding="utf-8")
log(f"profiler restarted pid {p.pid}; starting timing batch")

rc = subprocess.run([str(TOOLS_PY), "-u", "exp_timing_v6.py", "all"], cwd=EXP,
                    stdout=open(EXP / "stack_logs" / "timing_v6.out", "a"), stderr=subprocess.STDOUT).returncode
log(f"timing batch exited rc={rc}")
(EXP / "stack_logs" / "CHAIN_DONE").write_text(datetime.now().isoformat(), encoding="utf-8")
