# -*- coding: utf-8 -*-
"""T4 补充：容器资源配额在内核层面是否真正生效。

直接调用 worker.worker.container_resource_kwargs()——即平台启动仿真容器时实际下发的参数——
在仿真镜像里分别制造内存、进程数、CPU 三种压力，并与不加配额的对照组比较。

须用后端 venv 运行（需要 worker 模块与 docker SDK）：
    python exp_quota.py
"""

from pathlib import Path as _Path

HERE = _Path(__file__).resolve().parent   # 仓库中的 paper/ 目录
REPO = HERE.parent                        # 仓库根目录
import json
import os
import sys
import threading
import time
from pathlib import Path

REPO = REPO
os.chdir(REPO)
sys.path.insert(0, str(REPO))

import docker  # noqa: E402
from worker.worker import SIM_IMAGE, container_resource_kwargs  # noqa: E402

OUT = (HERE / "results/expQuota")
client = docker.from_env()

MEM_PROBE = "x = bytearray(5 * 1024 ** 3); print('allocated 5 GiB')"

PID_PROBE = """
import os, time
n = 0
while n < 3000:
    try:
        pid = os.fork()
    except OSError as e:
        print(f'fork failed after {n} children: {type(e).__name__}')
        break
    if pid == 0:
        time.sleep(30)
        os._exit(0)
    n += 1
else:
    print(f'spawned {n} children')
"""

CPU_PROBE = "for i in $(seq 8); do (while :; do :; done) & done; sleep 25"


def run_probe(cmd, quota, entry="python3"):
    kw = container_resource_kwargs() if quota else {}
    c = client.containers.run(SIM_IMAGE, entrypoint=[entry, "-c"], command=[cmd],
                              detach=True, remove=False, **kw)
    return c


def finish(c, timeout=120):
    status = c.wait(timeout=timeout)
    c.reload()
    out = c.logs().decode(errors="replace").strip()
    state = c.attrs["State"]
    c.remove(force=True)
    return {"exit_code": status["StatusCode"], "oom_killed": state.get("OOMKilled", False),
            "output": out[-300:]}


def cpu_probe(quota):
    c = run_probe(CPU_PROBE, quota, entry="sh")
    samples = []
    time.sleep(5)
    for _ in range(8):
        s = c.stats(stream=False)
        cpu_delta = s["cpu_stats"]["cpu_usage"]["total_usage"] - s["precpu_stats"]["cpu_usage"]["total_usage"]
        sys_delta = s["cpu_stats"].get("system_cpu_usage", 0) - s["precpu_stats"].get("system_cpu_usage", 0)
        ncpu = s["cpu_stats"].get("online_cpus", 16)
        if sys_delta > 0:
            samples.append(cpu_delta / sys_delta * ncpu * 100)
        time.sleep(1)
    c.stop(timeout=5)
    c.remove(force=True)
    return {"cpu_pct_samples": [round(x) for x in samples],
            "cpu_pct_mean": round(sum(samples) / len(samples), 1),
            "busy_loops": 8}


def main():
    OUT.mkdir(parents=True, exist_ok=True)
    quota = container_resource_kwargs()
    print("quota under test:", quota, flush=True)
    res = {"quota_kwargs": quota, "image": SIM_IMAGE}

    res["memory"] = {"with_quota": finish(run_probe(MEM_PROBE, True)),
                     "without_quota": finish(run_probe(MEM_PROBE, False))}
    print("memory:", json.dumps(res["memory"], ensure_ascii=False), flush=True)

    res["pids"] = {"with_quota": finish(run_probe(PID_PROBE, True), timeout=180),
                   "without_quota": finish(run_probe(PID_PROBE, False), timeout=180)}
    print("pids:", json.dumps(res["pids"], ensure_ascii=False), flush=True)

    res["cpu"] = {"with_quota": cpu_probe(True), "without_quota": cpu_probe(False)}
    print("cpu:", json.dumps(res["cpu"], ensure_ascii=False), flush=True)

    (OUT / "quota.json").write_text(json.dumps(res, indent=2, ensure_ascii=False), encoding="utf-8")
    print("->", OUT / "quota.json")


if __name__ == "__main__":
    main()
