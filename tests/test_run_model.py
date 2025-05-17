import os
from datetime import datetime
from unittest.mock import patch, AsyncMock, MagicMock

import pytest
from fastapi import HTTPException

import models
from models.run import Run, RunStatus
from models.project import Project
from models.user import User
from config import config


@pytest.mark.asyncio
async def test_run_create(session):
    """测试创建仿真运行"""
    # 创建用户和项目
    user = User(email="run_test@example.com", hashed_password="password123")
    await user.save(session)

    project = Project(name="测试项目", user_id=user.id)
    await project.save(session)

    # 创建运行
    run = Run(project_id=project.id, notes="测试运行")
    run = await run.save(session)

    # 验证创建成功
    assert run.id is not None
    await session.refresh(project)
    assert run.project_id == project.id
    assert run.notes == "测试运行"
    assert run.status == RunStatus.PENDING
    assert run.task_id is None
    assert run.start_time is None
    assert run.end_time is None


@pytest.mark.asyncio
async def test_run_get(session):
    """测试获取仿真运行"""
    # 创建用户和项目
    user = User(email="run_get@example.com", hashed_password="password123")
    await user.save(session)

    project = Project(name="测试项目", user_id=user.id)
    await project.save(session)

    # 创建多个运行
    run1 = Run(project_id=project.id, notes="运行1")
    run2 = Run(project_id=project.id, notes="运行2")

    await Run.add(session, [run1, run2])

    # 通过ID查询
    r1 = await Run.get(session, Run.id == run1.id)
    assert r1.notes == "运行1"

    # 获取项目的所有运行
    await session.refresh(project)
    runs = await Run.get(session, Run.project_id == project.id, fetch_mode="all")
    assert len(runs) == 2


@pytest.mark.asyncio
async def test_run_get_exist_one_with_user_id(session):
    """测试带用户权限检查的获取仿真运行"""
    # 创建两个用户
    user1 = User(email="run_owner@example.com", hashed_password="password123")
    user2 = User(email="run_other@example.com", hashed_password="password123")
    await User.add(session, [user1, user2])

    # 创建项目和运行
    project = Project(name="测试项目", user_id=user1.id)
    await project.save(session)

    run = Run(project_id=project.id)
    await run.save(session)

    # 验证所有者可以获取
    await session.refresh(user1)
    run1 = await Run.get_exist_one(session, run.id, user_id=user1.id)
    assert run1.id == run.id

    # 验证非所有者无法获取
    with pytest.raises(HTTPException) as e:
        await session.refresh(user2)
        await Run.get_exist_one(session, run.id, user_id=user2.id)
    assert e.value.status_code == 404


@pytest.mark.asyncio
async def test_run_dir_property(session):
    """测试运行目录属性"""
    # 创建用户和项目
    user = User(email="run_dir@example.com", hashed_password="password123")
    await user.save(session)

    project = Project(name="测试项目", user_id=user.id)
    await project.save(session)

    # 创建运行
    run = Run(project_id=project.id, id=123)
    await run.save(session)

    run = await Run.get(session, Run.id == run.id, load=Run.project)

    # 检查dir属性
    expected_path = os.path.normpath(os.path.join(
        project.dir,
        config.runs_base_dir_name_in_project,
        "123"
    ))
    assert run.dir == expected_path


@pytest.mark.asyncio
@patch('aioshutil.rmtree', new_callable=AsyncMock)
@patch('aiofiles.os.path.exists', new_callable=AsyncMock)
@patch('aiofiles.os.makedirs', new_callable=AsyncMock)
async def test_prepare_execution(mock_makedirs, mock_exists, mock_rmtree, session):
    """测试准备执行环境"""
    # 设置mock返回值
    mock_exists.return_value = True

    # 创建用户、项目和运行
    user = User(email="run_prep@example.com", hashed_password="password123")
    await user.save(session)

    project = Project(name="测试项目", user_id=user.id, id=1)
    await project.save(session)

    run = Run(project_id=project.id, id=1)
    run.project = project

    # 执行准备
    await run._prepare_execution()

    # 验证调用
    assert mock_makedirs.call_count >= 1
    assert mock_exists.call_count == 2
    assert mock_rmtree.call_count == 2


@pytest.mark.asyncio
@patch('worker.worker.celery_app.send_task')
async def test_execute_run(mock_send_task, session):
    """测试执行仿真运行"""
    # 设置mock
    mock_task = MagicMock()
    mock_task.id = "test-task-id"
    mock_send_task.return_value = mock_task

    # 创建用户、项目和运行
    user = User(email="run_exec@example.com", hashed_password="password123")
    await user.save(session)

    project = Project(name="测试项目", user_id=user.id, veins_config_name="TestConfig")
    await project.save(session)

    run = Run(project_id=project.id)
    await run.save(session)

    # 添加一个mock
    run._prepare_execution = AsyncMock()

    # 执行仿真
    run = await Run.get(session, Run.id == run.id, load=Run.project)
    result = await run.execute(session)

    # 验证状态更新
    assert result.status == RunStatus.STARTING
    assert result.task_id == "test-task-id"
    assert result.start_time is not None
    assert result.end_time is None

    # 验证准备和任务发送
    run._prepare_execution.assert_called_once()
    mock_send_task.assert_called_once_with(
        'veins_simulation.run',
        args=[project.dir, run.dir, "TestConfig"]
    )

@pytest.mark.asyncio
@patch('worker.worker.celery_app.control.revoke')
async def test_cancel_run(mock_revoke, session):
    """测试取消仿真运行"""
    # 创建用户、项目和运行
    user = User(email="run_cancel@example.com", hashed_password="password123")
    await user.save(session)

    project = Project(name="测试项目", user_id=user.id)
    await project.save(session)

    run = Run(
        project_id=project.id,
        status=RunStatus.RUNNING,
        task_id="task-to-cancel"
    )
    await run.save(session)

    # 取消运行
    result = await run.cancel(session)

    # 验证状态更新
    assert result.status == RunStatus.CANCELLING

    # 验证取消调用
    mock_revoke.assert_called_once_with(
        "task-to-cancel",
        terminate=True,
        signal='SIGTERM'
    )

    # 测试无效取消
    run.status = RunStatus.SUCCESS
    await run.save(session)

    with pytest.raises(RuntimeError):
        await run.cancel(session)


@pytest.mark.asyncio
@patch('celery.result.AsyncResult')
async def test_get_status_pending(mock_async_result, session):
    """测试PENDING状态"""
    # 创建测试环境
    user = User(email="run_pending@example.com", hashed_password="password123")
    await user.save(session)
    project = Project(name="测试项目", user_id=user.id)
    await project.save(session)

    # 配置mock
    mock_result = MagicMock()
    mock_result.state = 'PENDING'
    mock_async_result.return_value = mock_result

    # 创建运行
    run = Run(project_id=project.id, task_id="test-task", status=RunStatus.RUNNING)
    await run.save(session)

    # 获取状态
    updated_run = await run.get_status(session)

    # 验证
    assert updated_run.status == RunStatus.STARTING

@pytest.mark.asyncio
async def test_get_status_started(session):
    """测试 STARTED 状态"""
    # 创建测试环境
    user = User(email="run_started@example.com", hashed_password="password123")
    await user.save(session)
    project = Project(name="测试项目", user_id=user.id)
    await project.save(session)

    # 创建运行
    run = Run(project_id=project.id, task_id="test-task", status=RunStatus.STARTING)
    await run.save(session)

    # 直接替换 AsyncResult 类以确保 mock 正确应用
    original_async_result = models.run.AsyncResult

    try:
        # 创建具有特定状态的 mock AsyncResult 类
        mock_result = MagicMock()
        mock_result.state = 'STARTED'

        # 创建一个返回我们 mock 对象的函数来替代 AsyncResult
        def mock_async_result(task_id):
            return mock_result

        # 替换 AsyncResult
        models.run.AsyncResult = mock_async_result

        # 获取状态
        updated_run = await run.get_status(session)

        # 验证
        assert updated_run.status == RunStatus.RUNNING

    finally:
        # 恢复原始函数
        models.run.AsyncResult = original_async_result


@pytest.mark.asyncio
async def test_get_status_success(session):
    """测试 SUCCESS 状态"""
    # 创建测试环境
    user = User(email="run_success@example.com", hashed_password="password123")
    await user.save(session)
    project = Project(name="测试项目", user_id=user.id)
    await project.save(session)

    # 创建运行
    run = Run(project_id=project.id, task_id="test-task", status=RunStatus.RUNNING)
    await run.save(session)

    original_async_result = models.run.AsyncResult

    try:
        # 创建具有SUCCESS状态的mock
        mock_result = MagicMock()
        mock_result.state = 'SUCCESS'
        mock_result.get.return_value = {'time': datetime.now().isoformat()}

        def mock_async_result(task_id):
            return mock_result

        # 替换AsyncResult
        models.run.AsyncResult = mock_async_result

        # 获取状态
        updated_run = await run.get_status(session)

        # 验证
        assert updated_run.status == RunStatus.SUCCESS
        assert updated_run.end_time is not None

    finally:
        # 恢复原始函数
        models.run.AsyncResult = original_async_result

@pytest.mark.asyncio
async def test_get_status_no_task_id(session):
    """测试无任务ID的情况"""
    # 创建测试环境
    user = User(email="run_no_task@example.com", hashed_password="password123")
    await user.save(session)
    project = Project(name="测试项目", user_id=user.id)
    await project.save(session)

    # 创建无任务ID的运行
    run = Run(project_id=project.id, task_id=None, status=RunStatus.PENDING)
    await run.save(session)

    # 获取状态
    updated_run = await run.get_status(session)

    # 验证状态不变
    assert updated_run.status == RunStatus.PENDING
