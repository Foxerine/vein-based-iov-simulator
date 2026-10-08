"""Drive the platform API for the paper experiments.

Subcommands:
    smoke                 - create project + 1 headless run, wait, download results
    expA --repeat N       - N sequential platform runs (consistency/perf, Table 3)
    expC --parallel N     - N runs submitted at once (parallelism, Table 5)

Outputs land in paper/results/<name>/...
Each run's wall-clock timing is recorded to timing.json alongside the
platform-reported start/end times and downloaded result files.
"""

from pathlib import Path as _Path

HERE = _Path(__file__).resolve().parent   # 仓库中的 paper/ 目录
REPO = HERE.parent                        # 仓库根目录
import argparse
import io
import json
import time
import zipfile
from pathlib import Path

import httpx

BASE = "http://127.0.0.1:8000/api"
def _admin_password():
    import re
    cfg = (REPO / "config.cfg").read_text(encoding="utf-8")
    return re.search(r'admin_password = "([^"]+)"', cfg).group(1)

ADMIN = {"email": "admin@example.com", "password": _admin_password()}
SCENARIO = (HERE / "scenario")
RESULTS = (HERE / "results")


def client_with_token():
    c = httpx.Client(base_url=BASE, timeout=120)
    r = c.post("/auth/login", json=ADMIN)
    r.raise_for_status()
    c.headers["Authorization"] = f"Bearer {r.json()['access_token']}"
    return c


def create_project(c, name, config_name="Default", scenario_dir=None):
    src = scenario_dir or SCENARIO
    files = [
        ("files", (p.name, p.read_bytes(), "application/octet-stream"))
        for p in sorted(src.iterdir()) if p.is_file()
    ]
    r = c.post(
        "/project",
        params={"name": name, "veins_config_name": config_name,
                "description": "paper experiment (Erlangen RSUExampleScenario)"},
        files=files,
    )
    r.raise_for_status()
    return r.json()["id"]


def create_and_execute_run(c, project_id, notes, seed_set=0):
    r = c.post("/run", json={"project_id": project_id, "use_gui": False,
                             "notes": notes, "seed_set": seed_set})
    r.raise_for_status()
    run_id = r.json()["id"]
    r = c.post(f"/run/{run_id}/execute")
    r.raise_for_status()
    return run_id


def wait_run(c, run_id, timeout_s=3600, poll=10):
    t0 = time.monotonic()
    while time.monotonic() - t0 < timeout_s:
        r = c.get(f"/run/{run_id}")
        r.raise_for_status()
        info = r.json()
        if info["status"] in ("success", "failed", "cancelled"):
            return info
        time.sleep(poll)
    raise TimeoutError(f"run {run_id} not finished in {timeout_s}s")


def download_run(c, run_id, dest: Path):
    dest.mkdir(parents=True, exist_ok=True)
    r = c.get(f"/run/{run_id}/files")
    r.raise_for_status()
    zipfile.ZipFile(io.BytesIO(r.content)).extractall(dest)
    return sorted(p.name for p in dest.iterdir())


def run_once(c, project_id, tag, outdir):
    t_submit = time.monotonic()
    wall_start = time.time()
    run_id = create_and_execute_run(c, project_id, tag)
    info = wait_run(c, run_id)
    wall = time.monotonic() - t_submit
    files = download_run(c, run_id, outdir / f"run_{run_id}")
    return {
        "run_id": run_id, "tag": tag, "status": info["status"],
        "wall_clock_s": round(wall, 1), "wall_start": wall_start,
        "start_time": info.get("start_time"), "end_time": info.get("end_time"),
        "files": files,
    }


def cmd_smoke(args):
    c = client_with_token()
    pid = create_project(c, f"smoke-{int(time.time())}")
    out = RESULTS / "smoke"
    rec = run_once(c, pid, "smoke", out)
    print(json.dumps(rec, indent=2, ensure_ascii=False))


def cmd_expA(args):
    c = client_with_token()
    pid = create_project(c, f"expA-{int(time.time())}")
    out = RESULTS / "expA_platform"
    records = []
    for i in range(args.repeat):
        rec = run_once(c, pid, f"expA-rep{i}", out)
        print(f"rep {i}: {rec['status']} wall={rec['wall_clock_s']}s")
        records.append(rec)
    (out / "timing.json").write_text(json.dumps(records, indent=2))
    print("done ->", out)


def cmd_expC(args):
    c = client_with_token()
    # one project per run: concurrent containers must not share a results dir
    pids = [create_project(c, f"expC-{int(time.time())}-{i}") for i in range(args.parallel)]
    out = RESULTS / "expC_parallel"
    t0 = time.monotonic()
    # 每个并行任务使用不同 seed-set（0..N-1），构成真实的参数扫描
    run_ids = [create_and_execute_run(c, pid, f"expC-seed{i}", seed_set=i)
               for i, pid in enumerate(pids)]
    print("submitted:", run_ids)
    infos = [wait_run(c, rid) for rid in run_ids]
    total_wall = time.monotonic() - t0
    records = []
    for rid, info in zip(run_ids, infos):
        files = download_run(c, rid, out / f"run_{rid}")
        records.append({"run_id": rid, "status": info["status"],
                        "start_time": info.get("start_time"),
                        "end_time": info.get("end_time"), "files": files})
    summary = {"total_wall_clock_s": round(total_wall, 1), "n": args.parallel,
               "runs": records}
    (out / "timing.json").write_text(json.dumps(summary, indent=2))
    print(json.dumps(summary, indent=2)[:1500])


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    sub = ap.add_subparsers(dest="cmd", required=True)
    sub.add_parser("smoke").set_defaults(fn=cmd_smoke)
    a = sub.add_parser("expA"); a.add_argument("--repeat", type=int, default=3); a.set_defaults(fn=cmd_expA)
    cph = sub.add_parser("expC"); cph.add_argument("--parallel", type=int, default=5); cph.set_defaults(fn=cmd_expC)
    args = ap.parse_args()
    args.fn(args)
