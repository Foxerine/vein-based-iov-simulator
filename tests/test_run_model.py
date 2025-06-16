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
