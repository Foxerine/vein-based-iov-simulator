# -*- coding: utf-8 -*-
"""T3 资源画像采样器：在其他实验运行期间后台采集仿真容器的资源占用。

- 每 INTERVAL 秒对所有带 veins.run_id 标签的容器调用 Docker stats API，
  记录 CPU 占用（以单核 100% 计）、内存占用、进程/线程数，逐行写入 samples.jsonl；
- 每发现一个新容器，就启动一个 `docker logs -t -f` 子进程把带时间戳的容器输出写入
  logs/<容器名>.log，用于事后拆解冷启动各阶段耗时；
- 首次运行时记录镜像体积。

用法（须用后端 venv）：
    python exp_profile.py <输出目录名>    # 运行直到收到 Ctrl+C / 被终止
"""

from pathlib import Path as _Path

HERE = _Path(__file__).resolve().parent   # 仓库中的 paper/ 目录
REPO = HERE.parent                        # 仓库根目录
import json
import subprocess
import sys
import threading
import time
from pathlib import Path

import docker

INTERVAL = 2.0
ROOT = (HERE / "results/expProfile")


def cpu_pct(s):
    cpu = s["cpu_stats"]["cpu_usage"]["total_usage"] - s["precpu_stats"]["cpu_usage"].get("total_usage", 0)
    system = s["cpu_stats"].get("system_cpu_usage", 0) - s["precpu_stats"].get("system_cpu_usage", 0)
    ncpu = s["cpu_stats"].get("online_cpus") or 1
    return cpu / system * ncpu * 100 if system > 0 else None


def main():
    out = ROOT / (sys.argv[1] if len(sys.argv) > 1 else "default")
    (out / "logs").mkdir(parents=True, exist_ok=True)
    client = docker.from_env()
    image = client.images.get("veins-worker-test-gui")
    (out / "image.json").write_text(json.dumps({
        "tags": image.tags, "size_bytes": image.attrs.get("Size"),
        "virtual_size_bytes": image.attrs.get("VirtualSize")}, indent=2), encoding="utf-8")

    followers = {}
    lock = threading.Lock()
    samples = open(out / "samples.jsonl", "a", encoding="utf-8")

    def sample(c):
        try:
            s = c.stats(stream=False)
        except Exception:
            return
        mem = s.get("memory_stats", {})
        usage = mem.get("usage")
        # cgroup v2 下 inactive_file 为页缓存，与 docker stats 的口径一致需减去
        cache = mem.get("stats", {}).get("inactive_file", 0)
        rec = {"t": time.time(), "name": c.name, "run_id": c.labels.get("veins.run_id"),
               "cpu_pct": cpu_pct(s),
               "mem_bytes": (usage - cache) if usage is not None else None,
               "mem_limit": mem.get("limit"),
               "pids": s.get("pids_stats", {}).get("current")}
        with lock:
            samples.write(json.dumps(rec) + "\n")
            samples.flush()

    print(f"profiling -> {out}", flush=True)
    while True:
        t0 = time.time()
        try:
            running = client.containers.list(filters={"label": "veins.run_id"})
        except Exception:
            running = []
        for c in running:
            if c.name not in followers:
                f = open(out / "logs" / f"{c.name}.log", "wb")
                followers[c.name] = subprocess.Popen(["docker", "logs", "-t", "-f", c.name],
                                                     stdout=f, stderr=subprocess.STDOUT)
        threads = [threading.Thread(target=sample, args=(c,)) for c in running]
        for th in threads:
            th.start()
        for th in threads:
            th.join()
        time.sleep(max(0.0, INTERVAL - (time.time() - t0)))


if __name__ == "__main__":
    main()
