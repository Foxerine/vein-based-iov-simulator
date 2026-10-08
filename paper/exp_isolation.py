# -*- coding: utf-8 -*-
"""隔离检查（表2 中"网络隔离"与"任务队列认证"两行）。

直接调用 worker.worker.container_isolation_kwargs()——即平台启动仿真容器时实际下发的隔离参数——
在仿真镜像里检查：
1. 无头任务的容器：NoNewPrivs 标志、能否解析宿主机、能否连上宿主机上的 Redis 端口；
2. GUI 任务的容器（需发布 noVNC 端口，仍接入默认网桥）：同样的检查；
3. 在默认网桥上不带密码访问 Redis（即 GUI 容器内的代码能做到的最多的事）。

须用后端 venv 运行（需要 worker 模块与 docker SDK）：
    python exp_isolation.py
"""

from pathlib import Path as _Path

HERE = _Path(__file__).resolve().parent   # 仓库中的 paper/ 目录
REPO = HERE.parent                        # 仓库根目录
import json
import os
import sys
from datetime import datetime
from pathlib import Path

REPO = REPO
os.chdir(REPO)
sys.path.insert(0, str(REPO))

import docker  # noqa: E402
from worker.worker import SIM_IMAGE, container_isolation_kwargs  # noqa: E402

OUT = (HERE / "results/expIsolation")
client = docker.from_env()

PROBE = ("grep NoNewPrivs /proc/self/status; "
         "getent hosts host.docker.internal >/dev/null && echo resolve=ok || echo resolve=fail; "
         "(exec 3<>/dev/tcp/host.docker.internal/6379) 2>/dev/null && echo redis_port=open || echo redis_port=blocked")


def run(image, cmd, entrypoint=None, **kwargs):
    c = client.containers.run(image, cmd, entrypoint=entrypoint, detach=True, **kwargs)
    try:
        c.wait(timeout=120)
        return c.logs().decode("utf-8", "replace").strip()
    finally:
        c.remove(force=True)


def parse(out):
    kv = dict(l.split("=", 1) for l in out.splitlines() if "=" in l and not l.startswith("NoNewPrivs"))
    nnp = next((l.split()[-1] for l in out.splitlines() if l.startswith("NoNewPrivs")), None)
    return {"no_new_privs": nnp == "1", "resolve_host": kv.get("resolve") == "ok",
            "redis_port": kv.get("redis_port"), "raw": out}


def main():
    headless_kwargs = container_isolation_kwargs(gui_mode=False)
    gui_kwargs = container_isolation_kwargs(gui_mode=True)
    headless = parse(run(SIM_IMAGE, ["-c", PROBE], entrypoint="bash", **headless_kwargs))
    gui = parse(run(SIM_IMAGE, ["-c", PROBE], entrypoint="bash", **gui_kwargs))
    redis_noauth = run("redis:7", ["redis-cli", "-h", "host.docker.internal", "-p", "6379", "ping"], **gui_kwargs)
    result = {
        "timestamp": datetime.now().isoformat(timespec="seconds"),
        "headless_kwargs": headless_kwargs, "gui_kwargs": gui_kwargs,
        "headless": headless, "gui": gui,
        "gui_network_redis_without_password": redis_noauth,
    }
    OUT.mkdir(parents=True, exist_ok=True)
    (OUT / "isolation.json").write_text(json.dumps(result, indent=2, ensure_ascii=False), encoding="utf-8")
    print(json.dumps({k: v for k, v in result.items() if k not in ("headless", "gui")}, indent=1))
    print("headless:", {k: v for k, v in headless.items() if k != "raw"})
    print("gui:", {k: v for k, v in gui.items() if k != "raw"})


if __name__ == "__main__":
    main()
