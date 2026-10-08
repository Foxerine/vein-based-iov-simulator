# -*- coding: utf-8 -*-
"""T4 安全性实验：越权访问测试 + VNC 会话令牌测试。

A. 越权访问（user A 持有效令牌访问 user B 的资源）
   - 覆盖全部 14 个带资源 ID 的用户端点（项目 7 个、任务 7 个，含 PATCH/DELETE 等写操作），
     循环 72 轮，共 1008 次请求；期望全部 404；
   - 同样的请求打到不存在的 ID，对比状态码与响应体是否完全一致（存在性不可观测）；
   - 攻击结束后核对 B 的项目元数据与全部文件哈希未被改动。
B. 身份认证：无令牌 / 伪造签名 / 已过期 / alg=none 令牌访问同一组端点，期望 401；
   普通用户访问管理员端点，期望 403。
C. VNC 会话令牌：GUI 任务的令牌为 uuid4；正确令牌可打开 noVNC 页面，
   按旧算法（SHA-256 派生）算出的令牌、随机令牌、篡改令牌均返回 404；
   同一项目两次执行令牌不同；取消任务后会话不可达、容器已回收。

用法：python exp_security.py
"""

from pathlib import Path as _Path

HERE = _Path(__file__).resolve().parent   # 仓库中的 paper/ 目录
REPO = HERE.parent                        # 仓库根目录
import base64
import hashlib
import json
import re
import subprocess
import sys
import time
import uuid
from datetime import datetime, timedelta, timezone
from pathlib import Path

import httpx
import jwt

sys.path.insert(0, str(Path(__file__).parent))
from exp_platform import BASE, SCENARIO, RESULTS, create_project, wait_run

OUT = RESULTS / "expSecurity"
CFG = (REPO / "config.cfg").read_text(encoding="utf-8")
JWT_SECRET = re.search(r'jwt_secret = "([^"]+)"', CFG).group(1)
ROUNDS = 72
NONEXISTENT = 10 ** 9


def register(email, password="SecurityTest#2026"):
    with httpx.Client(base_url=BASE, timeout=60) as c:
        r = c.post("/auth/register", json={"email": email, "password": password})
        r.raise_for_status()
        return r.json()["access_token"]


def client(token=None):
    c = httpx.Client(base_url=BASE, timeout=120)
    if token:
        c.headers["Authorization"] = f"Bearer {token}"
    return c


def endpoint_matrix(pid, rid, pfile, rfile):
    return [
        ("GET", f"/project/{pid}", {}),
        ("PATCH", f"/project/{pid}", {"params": {"name": "tampered"}}),
        ("DELETE", f"/project/{pid}", {}),
        ("GET", f"/project/{pid}/files", {}),
        ("GET", f"/project/{pid}/files/{pfile}", {}),
        ("DELETE", f"/project/{pid}/files/{pfile}", {}),
        ("GET", f"/project/{pid}/runs", {}),
        ("POST", "/run", {"json": {"project_id": pid}}),
        ("GET", f"/run/{rid}", {}),
        ("POST", f"/run/{rid}/execute", {}),
        ("POST", f"/run/{rid}/cancel", {}),
        ("GET", f"/run/{rid}/files", {}),
        ("GET", f"/run/{rid}/files/{rfile}", {}),
        ("GET", f"/run/{rid}/analysis", {}),
    ]


def snapshot(c, pid, rid):
    """B 视角下资源的完整快照：项目元数据 + 全部项目文件与结果文件的 SHA-256。"""
    proj = c.get(f"/project/{pid}").json()
    run = c.get(f"/run/{rid}").json()
    files = {}
    for name, _ in sorted(proj["files"]):
        files[f"project/{name}"] = hashlib.sha256(c.get(f"/project/{pid}/files/{name}").content).hexdigest()
    for name, _ in sorted(run["files"]):
        files[f"run/{name}"] = hashlib.sha256(c.get(f"/run/{rid}/files/{name}").content).hexdigest()
    return {"name": proj["name"], "veins_config_name": proj["veins_config_name"],
            "run_status": run["status"], "files": files}


def part_a_and_b(ca, cb, cadmin_like, b_email):
    pid = create_project(cb, f"secB-{int(time.time())}")
    r = cb.post("/run", json={"project_id": pid, "use_gui": False, "notes": "victim"})
    r.raise_for_status()
    rid = r.json()["id"]
    cb.post(f"/run/{rid}/execute").raise_for_status()
    info = wait_run(cb, rid, timeout_s=900, poll=5)
    assert info["status"] == "success", info
    proj = cb.get(f"/project/{pid}").json()
    pfile = sorted(n for n, _ in proj["files"])[0]
    rfile = sorted(n for n, _ in cb.get(f"/run/{rid}").json()["files"])[0]

    before = snapshot(cb, pid, rid)

    # ---- A. 越权矩阵
    matrix = endpoint_matrix(pid, rid, pfile, rfile)
    ghost = endpoint_matrix(NONEXISTENT, NONEXISTENT, pfile, rfile)
    per_ep = {}
    leaks = []
    t0 = time.monotonic()
    for _ in range(ROUNDS):
        for (m, path, kw), (_, gpath, gkw) in zip(matrix, ghost):
            resp = ca.request(m, path, **kw)
            key = f"{m} {re.sub(r'/' + str(pid) + r'(?=/|$)', '/{pid}', re.sub(r'/' + str(rid) + r'(?=/|$)', '/{rid}', path))}"
            per_ep.setdefault(key, {"n": 0, "codes": {}})
            per_ep[key]["n"] += 1
            per_ep[key]["codes"][resp.status_code] = per_ep[key]["codes"].get(resp.status_code, 0) + 1
            if resp.status_code != 404:
                leaks.append({"endpoint": key, "status": resp.status_code, "body": resp.text[:200]})
    elapsed = time.monotonic() - t0

    # 与不存在 ID 的响应逐字节比较
    oracle = []
    for (m, path, kw), (_, gpath, gkw) in zip(matrix, ghost):
        r1, r2 = ca.request(m, path, **kw), ca.request(m, gpath, **gkw)
        oracle.append({"endpoint": f"{m} {path}", "victim": [r1.status_code, r1.text],
                       "nonexistent": [r2.status_code, r2.text],
                       "identical": r1.status_code == r2.status_code and r1.text == r2.text})

    after = snapshot(cb, pid, rid)
    total = sum(v["n"] for v in per_ep.values())

    # ---- B. 身份认证
    now = datetime.now(timezone.utc)
    forged = jwt.encode({"sub": b_email, "exp": now + timedelta(hours=1)}, "not-the-secret", algorithm="HS256")
    expired = jwt.encode({"sub": b_email, "exp": now - timedelta(minutes=1)}, JWT_SECRET, algorithm="HS256")
    hdr = base64.urlsafe_b64encode(b'{"alg":"none","typ":"JWT"}').rstrip(b"=").decode()
    body = base64.urlsafe_b64encode(json.dumps({"sub": b_email}).encode()).rstrip(b"=").decode()
    alg_none = f"{hdr}.{body}."
    auth_cases = {"no_token": None, "forged_signature": forged, "expired": expired, "alg_none": alg_none}
    auth = {}
    for name, tok in auth_cases.items():
        with client(tok) as c:
            codes = [c.request(m, path, **kw).status_code for m, path, kw in matrix]
        auth[name] = {"n": len(codes), "codes": {str(k): codes.count(k) for k in set(codes)}}
    admin_eps = ["/admin/user", "/admin/project", "/admin/run", f"/admin/project/{pid}", f"/admin/run/{rid}"]
    admin_codes = [ca.get(p).status_code for p in admin_eps]
    auth["normal_user_on_admin_endpoints"] = {"n": len(admin_codes),
                                              "codes": {str(k): admin_codes.count(k) for k in set(admin_codes)}}

    return {
        "victim": {"project_id": pid, "run_id": rid},
        "cross_user": {"total_requests": total, "rounds": ROUNDS, "endpoints": len(matrix),
                       "elapsed_s": round(elapsed, 1),
                       "all_404": not leaks, "non_404": leaks[:20], "per_endpoint": per_ep},
        "existence_oracle": {"all_identical": all(o["identical"] for o in oracle), "cases": oracle},
        "integrity": {"unchanged": before == after, "files_checked": len(before["files"]),
                      "before": before, "after": after},
        "authentication": auth,
    }


def legacy_token(user_id, project_id, run_id):
    """修复前的令牌算法，用来证明旧令牌已不可用。"""
    h = hashlib.sha256(f"{user_id}:{project_id}:{run_id}:{JWT_SECRET}".encode()).hexdigest()
    return f"{h[:8]}-{h[8:12]}-{h[12:16]}-{h[16:20]}-{h[20:32]}"


def gui_run(cb, pid):
    r = cb.post("/run", json={"project_id": pid, "use_gui": True, "notes": "vnc-token-test"})
    r.raise_for_status()
    rid = r.json()["id"]
    t0 = time.monotonic()
    cb.post(f"/run/{rid}/execute").raise_for_status()
    url = None
    while time.monotonic() - t0 < 180:
        info = cb.get(f"/run/{rid}").json()
        url = info.get("vnc_url")
        if url:
            try:
                if httpx.get(url, timeout=5).status_code == 200:
                    break
            except httpx.HTTPError:
                pass
        time.sleep(1)
    return rid, url, round(time.monotonic() - t0, 1)


def part_c(cb, b_user_id):
    pid = create_project(cb, f"secVNC-{int(time.time())}")
    rid1, url1, ready1 = gui_run(cb, pid)
    tok1 = re.search(r"/vnc/([^/]+)/", url1).group(1)
    base1 = url1.split("/vnc/")[0]

    def probe(token):
        try:
            return httpx.get(f"{base1}/vnc/{token}/vnc.html", timeout=5).status_code
        except httpx.HTTPError as e:
            return f"unreachable ({type(e).__name__})"

    tampered = tok1[:-1] + ("0" if tok1[-1] != "0" else "1")
    probes = {
        "correct_token": probe(tok1),
        "legacy_sha256_token": probe(legacy_token(b_user_id, pid, rid1)),
        "random_uuid4": probe(str(uuid.uuid4())),
        "last_char_tampered": probe(tampered),
        "root_path": (lambda: httpx.get(f"{base1}/", timeout=5).status_code)(),
    }

    rid2, url2, ready2 = gui_run(cb, pid)
    tok2 = re.search(r"/vnc/([^/]+)/", url2).group(1)

    for rid in (rid1, rid2):
        cb.post(f"/run/{rid}/cancel")
    time.sleep(3)
    after_cancel = probe(tok1)
    leftovers = subprocess.run(
        ["docker", "ps", "-a", "--filter", f"label=veins.run_id={rid1}", "--format", "{{.Names}}"],
        capture_output=True, text=True).stdout.strip()

    return {
        "token_1": {"uuid_version": uuid.UUID(tok1).version, "ready_s": ready1},
        "token_2": {"uuid_version": uuid.UUID(tok2).version, "ready_s": ready2},
        "tokens_differ_same_project": tok1 != tok2,
        "legacy_token_differs": legacy_token(b_user_id, pid, rid1) != tok1,
        "probes_while_running": probes,
        "probe_after_cancel": after_cancel,
        "containers_left_after_cancel": leftovers or "none",
    }


def main():
    OUT.mkdir(parents=True, exist_ok=True)
    stamp = int(time.time())
    a_email, b_email = f"attacker{stamp}@example.com", f"victim{stamp}@example.com"
    ca, cb = client(register(a_email)), client(register(b_email))
    b_user_id = cb.get("/user").json()["id"]

    result = {"timestamp": datetime.now().isoformat(timespec="seconds")}
    result.update(part_a_and_b(ca, cb, None, b_email))
    print("cross-user:", result["cross_user"]["total_requests"], "requests, all 404 =",
          result["cross_user"]["all_404"], "| oracle identical =", result["existence_oracle"]["all_identical"],
          "| integrity =", result["integrity"]["unchanged"], flush=True)
    print("auth:", json.dumps(result["authentication"], ensure_ascii=False), flush=True)

    result["vnc_token"] = part_c(cb, b_user_id)
    print("vnc:", json.dumps(result["vnc_token"], ensure_ascii=False, indent=1), flush=True)

    (OUT / "security.json").write_text(json.dumps(result, indent=2, ensure_ascii=False), encoding="utf-8")
    print("->", OUT / "security.json")


if __name__ == "__main__":
    main()
