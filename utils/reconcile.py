"""后台状态对账：保证每个已提交的仿真任务最终收敛到终态。

需要它的原因有两个：
1. 任务状态原本只在有人请求 GET /run/{id} 时才从 Celery 刷新，无人查看的任务会一直停留在旧状态；
2. 任务在开始执行时即被确认（至多一次语义，见 worker/worker.py），执行它的 worker 中途崩溃后
   不会被重新投递，数据库里的状态会永远停在 starting/running。

对账在 API 进程内以后台协程运行，每个周期：
- 对所有未终结且已提交的任务调用 Run.get_status()，把 Celery 中已完成的结果同步到数据库；
- 任务已被某个 worker 开始执行（STARTED/PROGRESS），却连续多个周期不在任何 worker 的执行列表里，
  判定为 worker 丢失，标记失败；
- 任务开始执行后超过 simulation_max_timeout 仍未结束，标记失败（运行时长兜底）。
"""
import asyncio
import os
import time
from datetime import datetime

from celery.result import AsyncResult
from loguru import logger
from sqlmodel.ext.asyncio.session import AsyncSession

from config import config
from models.run import Run, RunStatus
from worker.worker import celery_app

TERMINAL = (RunStatus.SUCCESS, RunStatus.FAILED, RunStatus.CANCELLED)

LOST_AFTER_MISSES = 3
"""连续多少个扫描周期不在任何 worker 的执行列表里才判定丢失；单次 inspect 可能因超时漏报"""

DEADLINE_GRACE_S = 300
"""运行时长兜底在 simulation_max_timeout 之外再留出的余量（容器停止、结果搬运）"""

INSPECT_TIMEOUT_S = 2.0


def classify(celery_state: str, task_id: str, active_ids: set[str] | None,
             started_at: float | None, now: float, misses: int) -> tuple[str | None, int]:
    """判断一个未终结任务是否应被标记为失败。

    Args:
        celery_state: Celery 中的任务状态
        task_id: 任务ID
        active_ids: 所有 worker 正在执行的任务ID；None 表示没有任何 worker 应答
        started_at: worker 开始执行该任务的时间戳（来自任务 meta），未开始则为 None
        now: 当前时间戳
        misses: 此前连续缺席的周期数

    Returns:
        (失败原因，不应失败时为 None；更新后的连续缺席周期数)
    """
    if started_at is not None and now - started_at > config.simulation_max_timeout + DEADLINE_GRACE_S:
        return "超过最大运行时长仍未结束", misses

    # 只有已被 worker 开始执行的任务才可能"丢失"；排队中的任务（PENDING/RETRY）不在执行列表里是正常的
    if celery_state in ("STARTED", "PROGRESS"):
        if active_ids is None or task_id not in active_ids:
            misses += 1
            if misses >= LOST_AFTER_MISSES:
                return "执行该任务的 worker 已丢失", misses
            return None, misses
    return None, 0


def active_task_ids(timeout: float = INSPECT_TIMEOUT_S) -> set[str] | None:
    """向所有 worker 广播查询正在执行的任务（阻塞调用，应放到线程里执行）。"""
    replies = celery_app.control.inspect(timeout=timeout).active()
    if not replies:
        return None
    return {task["id"] for tasks in replies.values() for task in tasks}


async def _mark_failed(session: AsyncSession, run_id: int, task_id: str, log_path: str, reason: str):
    run = await Run.get(session, Run.id == run_id)
    run.status = RunStatus.FAILED
    run.end_time = datetime.now()
    await run.save(session)
    try:
        with open(log_path, "a", encoding="utf-8") as f:
            f.write(f"\n[{datetime.now().isoformat()}] 状态对账：{reason}，任务已标记为失败\n")
    except OSError:
        pass
    logger.warning(f"状态对账：运行 {run_id}（任务 {task_id}）{reason}，已标记为失败")


async def reconcile_once(session: AsyncSession, get_active_ids, misses: dict[int, int],
                         now: float | None = None) -> list[int]:
    """执行一次对账，返回本次被标记为失败的运行ID。

    Args:
        session: 数据库会话
        get_active_ids: 返回 active_task_ids() 结果的异步函数；只在确有执行中的任务时才调用
        misses: 运行ID -> 连续缺席周期数，跨周期保留
        now: 当前时间戳，测试时注入
    """
    now = time.time() if now is None else now
    runs = await Run.get(
        session,
        (Run.task_id != None) & (Run.status.not_in(TERMINAL)),  # noqa: E711
        fetch_mode="all",
        load=Run.project,
    )
    # 每次提交都会让会话中的实例过期，先在提交前取下后续要用的字段
    snapshot = [(run.id, run.task_id, os.path.join(run.dir, "simulation.log")) for run in runs]

    pending = []
    for run, (run_id, task_id, log_path) in zip(runs, snapshot):
        run = await run.get_status(session)
        if run.status in TERMINAL:
            continue
        result = AsyncResult(task_id, app=celery_app)
        state = result.state
        info = result.info if state == "PROGRESS" else None
        started_at = info.get("started_at") if isinstance(info, dict) else None
        pending.append((run_id, task_id, log_path, state, started_at))

    pending_ids = {run_id for run_id, *_ in pending}
    for run_id in list(misses):
        if run_id not in pending_ids:
            misses.pop(run_id)

    if not pending:
        return []

    executing = any(state in ("STARTED", "PROGRESS") for *_, state, _ in pending)
    active_ids = await get_active_ids() if executing else set()

    failed = []
    for run_id, task_id, log_path, state, started_at in pending:
        reason, misses[run_id] = classify(state, task_id, active_ids, started_at, now,
                                          misses.get(run_id, 0))
        if reason:
            await _mark_failed(session, run_id, task_id, log_path, reason)
            misses.pop(run_id, None)
            failed.append(run_id)
    return failed


async def reconcile_forever(engine, interval_s: int):
    """API 进程生命周期内的后台对账循环。"""
    misses: dict[int, int] = {}

    async def get_active_ids():
        return await asyncio.to_thread(active_task_ids)

    logger.info(f"状态对账已启动，间隔 {interval_s} 秒")
    while True:
        try:
            async with AsyncSession(engine) as session:
                await reconcile_once(session, get_active_ids, misses)
        except asyncio.CancelledError:
            raise
        except Exception as e:
            logger.exception(f"状态对账出错（下个周期重试）: {e}")
        await asyncio.sleep(interval_s)
