"""资源配额、VNC 会话令牌、任务容错与状态对账的测试（不依赖 Redis 与 Docker）。"""
import time
import uuid
from types import SimpleNamespace
from unittest.mock import MagicMock, patch, AsyncMock

import pytest
import requests
from docker.errors import APIError, DockerException, ImageNotFound
from kombu import Queue

import worker.worker as w
from config import config
from models.project import Project
from models.run import Run, RunStatus
from models.user import User
from utils import reconcile
from utils.auth import generate_vnc_token

TASK_ID = "abcdef12-0000-4000-8000-000000000000"


# ---------------------------------------------------------------- VNC 会话令牌

def test_vnc_token_is_random_uuid4():
    tokens = [generate_vnc_token() for _ in range(1000)]
    assert len(set(tokens)) == 1000
    for t in tokens[:50]:
        parsed = uuid.UUID(t)
        assert parsed.version == 4
        assert str(parsed) == t


@pytest.mark.asyncio
@patch('aioshutil.rmtree', new_callable=AsyncMock)
@patch('aiofiles.os.path.exists', new_callable=AsyncMock, return_value=False)
@patch('aiofiles.os.makedirs', new_callable=AsyncMock)
async def test_gui_execution_sends_fresh_random_token(_mk, _ex, _rm, session):
    """同一用户、同一项目的两次 GUI 运行得到互不相同的随机令牌，不能由 ID 推算。"""
    user = User(email="vnc_token@example.com", hashed_password="x")
    await user.save(session)
    project = Project(name="p", user_id=user.id)
    await project.save(session)
    project_id = project.id

    sent = []
    with patch('models.run.celery_app.send_task',
               side_effect=lambda *a, **k: sent.append(k['args']) or SimpleNamespace(id=str(uuid.uuid4()))):
        for _ in range(2):
            run = Run(project_id=project_id, use_gui=True)
            await run.save(session)
            run = await Run.get(session, Run.id == run.id, load=Run.project)
            await run.execute(session)

    tokens = [args[7] for args in sent]
    assert all(args[6] is True for args in sent)
    assert tokens[0] != tokens[1]
    assert all(uuid.UUID(t).version == 4 for t in tokens)


# ---------------------------------------------------------------- 资源配额

def test_container_resource_kwargs(monkeypatch):
    monkeypatch.setattr(config, "container_cpu_limit", 2.0)
    monkeypatch.setattr(config, "container_memory_limit", "4g")
    monkeypatch.setattr(config, "container_pids_limit", 1024)
    assert w.container_resource_kwargs() == {
        "nano_cpus": 2_000_000_000,
        "mem_limit": "4g",
        "memswap_limit": "4g",
        "pids_limit": 1024,
    }


def test_container_resource_kwargs_disabled(monkeypatch):
    monkeypatch.setattr(config, "container_cpu_limit", 0)
    monkeypatch.setattr(config, "container_memory_limit", "")
    monkeypatch.setattr(config, "container_pids_limit", 0)
    assert w.container_resource_kwargs() == {}


def test_container_isolation_headless_has_no_network(monkeypatch):
    monkeypatch.setattr(config, "container_isolate_network", True)
    assert w.container_isolation_kwargs(gui_mode=False) == {
        "security_opt": ["no-new-privileges:true"],
        "network_mode": "none",
    }


def test_container_isolation_gui_keeps_network_for_novnc(monkeypatch):
    monkeypatch.setattr(config, "container_isolate_network", True)
    assert w.container_isolation_kwargs(gui_mode=True) == {"security_opt": ["no-new-privileges:true"]}


def test_container_isolation_network_switch(monkeypatch):
    monkeypatch.setattr(config, "container_isolate_network", False)
    assert "network_mode" not in w.container_isolation_kwargs(gui_mode=False)


def test_concurrency_comes_from_config():
    assert w.celery_app.conf.worker_concurrency == config.max_concurrent_simulations
    assert w.celery_app.conf.worker_pool == config.worker_pool
    assert w.celery_app.conf.worker_prefetch_multiplier == 1
    assert w.celery_app.conf.task_acks_late is False


# ---------------------------------------------------------------- 错误分类

@pytest.mark.parametrize("exc, transient", [
    (DockerException("Error while fetching server API version"), True),
    (requests.exceptions.ConnectionError("refused"), True),
    (APIError("boom", response=SimpleNamespace(status_code=500, reason="x")), True),
    (APIError("conflict", response=SimpleNamespace(status_code=409, reason="x")), False),
    (ImageNotFound("no such image"), False),
    (ValueError("bad"), False),
])
def test_is_transient_docker_error(exc, transient):
    assert w.is_transient_docker_error(exc) is transient


# ---------------------------------------------------------------- run_simulation（eager 执行）

def make_container(exit_code=0, oom=False, status="exited"):
    c = MagicMock()
    c.id = "c0ffee" * 10
    c.name = "sim"
    c.status = status
    c.ports = {}
    c.attrs = {"State": {"ExitCode": exit_code, "OOMKilled": oom}}
    c.logs.side_effect = lambda **kw: iter([b"simulating\n"]) if kw.get("stream") else b"done\n"
    c.wait.return_value = {"StatusCode": exit_code}
    return c


def run_task(tmp_path, client, gui=False, token=None):
    project_dir = tmp_path / "project"
    run_dir = tmp_path / "project" / "runs" / "3"
    project_dir.mkdir()
    args = [1, 2, 3, str(project_dir), str(run_dir), "Default"]
    args += [True, token] if gui else [False]
    with patch.object(w.run_simulation, "update_state"), \
            patch("worker.worker.docker.from_env", return_value=client) as from_env:
        result = w.run_simulation.apply(args=args, kwargs={"seed_set": 0}, task_id=TASK_ID).result
    return result, from_env, run_dir


def test_headless_run_applies_quotas_labels_and_removes_container(tmp_path):
    container = make_container()
    client = MagicMock()
    client.containers.list.return_value = []
    client.containers.run.return_value = container

    result, _, _ = run_task(tmp_path, client)

    assert result["status"] == w.RunStatus.SUCCESS
    kwargs = client.containers.run.call_args.kwargs
    for key, value in w.container_resource_kwargs().items():
        assert kwargs[key] == value
    for key, value in w.container_isolation_kwargs(gui_mode=False).items():
        assert kwargs[key] == value
    assert kwargs["remove"] is False
    assert kwargs["labels"][w.LABEL_TASK] == TASK_ID
    assert kwargs["labels"][w.LABEL_RUN] == "3"
    container.remove.assert_called_once_with(force=True)


def test_headless_run_reports_running_once_container_starts(tmp_path):
    client = MagicMock()
    client.containers.list.return_value = []
    client.containers.run.return_value = make_container()
    project_dir = tmp_path / "project"
    project_dir.mkdir()
    with patch.object(w.run_simulation, "update_state") as update_state, \
            patch("worker.worker.docker.from_env", return_value=client):
        w.run_simulation.apply(args=[1, 2, 3, str(project_dir), str(tmp_path / "project" / "runs" / "3"), "Default", False],
                               task_id=TASK_ID)
    reported = [call.kwargs["meta"]["status"] for call in update_state.call_args_list]
    assert reported == [w.RunStatus.STARTING, w.RunStatus.RUNNING]


def test_oom_killed_run_is_reported_as_failed(tmp_path):
    container = make_container(exit_code=137, oom=True)
    client = MagicMock()
    client.containers.list.return_value = []
    client.containers.run.return_value = container

    result, _, run_dir = run_task(tmp_path, client)

    assert result["status"] == w.RunStatus.FAILED
    assert "OOM" in result["error"]
    assert "OOM" in (run_dir / "simulation.log").read_text(encoding="utf-8")
    container.remove.assert_called_with(force=True)


def test_headless_run_enforces_max_runtime(tmp_path):
    container = make_container()
    container.wait.side_effect = requests.exceptions.ReadTimeout("timed out")
    client = MagicMock()
    client.containers.list.return_value = []
    client.containers.run.return_value = container

    result, _, _ = run_task(tmp_path, client)

    assert result["status"] == w.RunStatus.FAILED
    assert "最大运行时长" in result["error"]
    container.stop.assert_called()
    container.remove.assert_called_with(force=True)


def test_transient_docker_error_is_retried_with_backoff(tmp_path):
    client = MagicMock()
    with patch.object(w.run_simulation, "update_state"), \
            patch("worker.worker.docker.from_env", side_effect=DockerException("daemon unreachable")) as from_env:
        project_dir = tmp_path / "project"
        project_dir.mkdir()
        result = w.run_simulation.apply(
            args=[1, 2, 3, str(project_dir), str(tmp_path / "project" / "runs" / "3"), "Default", False],
            task_id=TASK_ID).result

    assert from_env.call_count == 4  # 首次 + 3 次重试
    assert result["status"] == w.RunStatus.FAILED
    log = (tmp_path / "project" / "runs" / "3" / "simulation.log").read_text(encoding="utf-8")
    for delay, n in ((10, 1), (20, 2), (40, 3)):
        assert f"{delay} 秒后重试（{n}/3）" in log


def test_permanent_docker_error_is_not_retried(tmp_path):
    client = MagicMock()
    client.containers.list.return_value = []
    client.containers.run.side_effect = ImageNotFound("no such image")

    result, from_env, _ = run_task(tmp_path, client)

    assert from_env.call_count == 1
    assert result["status"] == w.RunStatus.FAILED


def test_stale_container_with_same_name_is_removed_before_start(tmp_path):
    stale = MagicMock()
    stale.name = f"veins-sim-u1-p2-r3-{TASK_ID[:8]}"
    unrelated = MagicMock()
    unrelated.name = stale.name + "-other"
    client = MagicMock()
    client.containers.list.return_value = [stale, unrelated]
    client.containers.run.return_value = make_container()

    run_task(tmp_path, client)

    stale.remove.assert_called_once_with(force=True)
    unrelated.remove.assert_not_called()


def test_gui_run_masks_token_and_removes_container(tmp_path):
    token = generate_vnc_token()
    container = make_container(status="exited")
    client = MagicMock()
    client.containers.list.return_value = []
    client.containers.run.return_value = container

    result, _, run_dir = run_task(tmp_path, client, gui=True, token=token)

    assert result["status"] == w.RunStatus.SUCCESS
    assert token not in (run_dir / "simulation.log").read_text(encoding="utf-8")
    container.remove.assert_called_once_with(force=True)


# ---------------------------------------------------------------- 取消与孤儿清理

def test_stop_command_finds_container_by_label_after_worker_restart():
    w._task_container_mapping.clear()
    container = MagicMock()
    client = MagicMock()
    client.containers.list.return_value = [container]
    with patch("worker.worker.docker.from_env", return_value=client):
        result = w.stop_sim_containers(None, TASK_ID)

    client.containers.list.assert_called_once_with(all=True, filters={"label": f"{w.LABEL_TASK}={TASK_ID}"})
    container.stop.assert_called_once()
    container.remove.assert_called_once_with(force=True)
    assert result == {"stopped": 1}


def test_stop_is_a_remote_control_command_not_a_queued_task():
    """停止请求不能排在仿真任务后面等执行槽。"""
    from celery.worker.control import Panel
    assert "stop_sim_containers" in Panel.data
    assert "veins_simulation.stop" not in w.celery_app.tasks


@pytest.mark.asyncio
async def test_cancel_stops_container_without_waiting_for_a_worker_slot(session):
    run_id = await make_running_run(session, "cancel@example.com", "cancel-task")
    run = await Run.get(session, Run.id == run_id, load=Run.project)
    with patch.object(w.celery_app.control, "revoke") as revoke, \
            patch.object(w.celery_app.control, "broadcast") as broadcast, \
            patch.object(w.celery_app, "send_task") as send_task:
        await run.cancel(session)

    revoke.assert_called_once_with("cancel-task")
    broadcast.assert_called_once()
    assert broadcast.call_args.args[0] == "stop_sim_containers"
    assert broadcast.call_args.kwargs["arguments"] == {"task_id": "cancel-task"}
    send_task.assert_not_called()
    assert (await Run.get(session, Run.id == run_id)).status == RunStatus.CANCELLED


def test_results_go_to_the_runs_own_directory(tmp_path):
    """同一项目的多次运行并发时，结果必须写入各自的运行目录，不能共用项目级 results 目录。"""
    client = MagicMock()
    client.containers.list.return_value = []
    client.containers.run.return_value = make_container()
    project_dir = tmp_path / "project"
    project_dir.mkdir()
    run_dir = project_dir / "runs" / "3"
    with patch.object(w.run_simulation, "update_state"), \
            patch("worker.worker.docker.from_env", return_value=client):
        w.run_simulation.apply(args=[1, 2, 3, str(project_dir), str(run_dir), "Default", False],
                               task_id=TASK_ID)
    command = client.containers.run.call_args.kwargs["command"]
    assert "--result-dir=/simulation/project/runs/3" in command


def test_worker_ready_removes_only_own_orphans():
    sender = SimpleNamespace(hostname="veins-worker@host",
                             task_consumer=SimpleNamespace(queues=[Queue("celery")]))
    orphan = MagicMock()
    client = MagicMock()
    client.containers.list.return_value = [orphan]
    with patch("worker.worker.docker.from_env", return_value=client):
        w.cleanup_orphan_containers(sender=sender)

    client.containers.list.assert_called_once_with(
        all=True, filters={"label": f"{w.LABEL_WORKER}=veins-worker@host"})
    orphan.remove.assert_called_once_with(force=True)


def test_worker_ready_skipped_for_analysis_worker():
    sender = SimpleNamespace(hostname="veins-analysis@host",
                             task_consumer=SimpleNamespace(queues=[Queue("analysis")]))
    with patch("worker.worker.docker.from_env") as from_env:
        w.cleanup_orphan_containers(sender=sender)
    from_env.assert_not_called()


# ---------------------------------------------------------------- 状态对账：判定逻辑

NOW = 1_000_000.0


def test_classify_lost_after_consecutive_misses():
    misses = 0
    for i in range(1, reconcile.LOST_AFTER_MISSES):
        reason, misses = reconcile.classify("PROGRESS", "t", set(), NOW - 10, NOW, misses)
        assert reason is None and misses == i
    reason, misses = reconcile.classify("PROGRESS", "t", set(), NOW - 10, NOW, misses)
    assert reason is not None


def test_classify_active_task_resets_misses():
    reason, misses = reconcile.classify("PROGRESS", "t", {"t"}, NOW - 10, NOW, 2)
    assert reason is None and misses == 0


def test_classify_no_worker_replied_counts_as_miss():
    reason, misses = reconcile.classify("STARTED", "t", None, None, NOW, 0)
    assert reason is None and misses == 1


@pytest.mark.parametrize("state", ["PENDING", "RETRY"])
def test_classify_queued_task_is_never_lost(state):
    reason, misses = reconcile.classify(state, "t", set(), None, NOW, 5)
    assert reason is None and misses == 0


def test_classify_deadline_exceeded():
    started = NOW - config.simulation_max_timeout - reconcile.DEADLINE_GRACE_S - 1
    reason, _ = reconcile.classify("PROGRESS", "t", {"t"}, started, NOW, 0)
    assert reason is not None


# ---------------------------------------------------------------- 状态对账：数据库收敛

async def make_running_run(session, email, task_id):
    user = User(email=email, hashed_password="x")
    await user.save(session)
    project = Project(name="p", user_id=user.id)
    await project.save(session)
    run = Run(project_id=project.id, task_id=task_id, status=RunStatus.RUNNING)
    await run.save(session)
    return run.id


def celery_result(state, info=None, result=None):
    return MagicMock(state=state, info=info, result=result)


@pytest.mark.asyncio
async def test_reconcile_marks_lost_task_failed(session):
    run_id = await make_running_run(session, "lost@example.com", "lost-task")
    res = celery_result("PROGRESS", info={"status": "running", "started_at": time.time()})
    misses = {}

    async def no_active():
        return set()

    with patch("models.run.AsyncResult", return_value=res), \
            patch("utils.reconcile.AsyncResult", return_value=res):
        for _ in range(reconcile.LOST_AFTER_MISSES - 1):
            assert await reconcile.reconcile_once(session, no_active, misses) == []
        assert await reconcile.reconcile_once(session, no_active, misses) == [run_id]

    run = await Run.get(session, Run.id == run_id)
    assert run.status == RunStatus.FAILED
    assert run.end_time is not None
    assert misses == {}


@pytest.mark.asyncio
async def test_reconcile_keeps_task_that_is_still_executing(session):
    run_id = await make_running_run(session, "alive@example.com", "alive-task")
    res = celery_result("PROGRESS", info={"status": "running", "started_at": time.time()})

    async def active():
        return {"alive-task"}

    with patch("models.run.AsyncResult", return_value=res), \
            patch("utils.reconcile.AsyncResult", return_value=res):
        for _ in range(reconcile.LOST_AFTER_MISSES + 1):
            assert await reconcile.reconcile_once(session, active, {}) == []

    run = await Run.get(session, Run.id == run_id)
    assert run.status == RunStatus.RUNNING


@pytest.mark.asyncio
async def test_reconcile_syncs_finished_task_without_client_polling(session):
    run_id = await make_running_run(session, "done@example.com", "done-task")
    res = celery_result("SUCCESS", result={"status": "success", "exit_code": 0})
    get_active = AsyncMock()

    with patch("models.run.AsyncResult", return_value=res), \
            patch("utils.reconcile.AsyncResult", return_value=res):
        assert await reconcile.reconcile_once(session, get_active, {}) == []

    run = await Run.get(session, Run.id == run_id)
    assert run.status == RunStatus.SUCCESS
    get_active.assert_not_called()


# ---------------------------------------------------------------- 日志脱敏

def test_redact_url_credentials():
    from config import redact_url_credentials
    assert redact_url_credentials("redis://:s3cret@localhost:6379/0") == "redis://***@localhost:6379/0"
    assert redact_url_credentials("redis://user:pw@host:6379") == "redis://***@host:6379"
    assert redact_url_credentials("redis://localhost:6379") == "redis://localhost:6379"
    assert redact_url_credentials("sqlite+aiosqlite:///./data.db") == "sqlite+aiosqlite:///./data.db"
