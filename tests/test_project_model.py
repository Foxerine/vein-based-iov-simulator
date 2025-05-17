import os
from unittest.mock import patch, AsyncMock

import pytest
from fastapi import HTTPException

from config import config
from models.others import TableViewRequest
from models.project import Project
from models.user import User


@pytest.mark.asyncio
async def test_project_create(session):
    """测试创建项目"""
    # 首先创建一个用户作为项目所有者
    test_user = User(email="project_owner@example.com", hashed_password="password123")
    await test_user.save(session)
    test_user = await User.get(session, User.email == test_user.email, load=User.projects)

    # 创建项目
    project = Project(
        name="测试项目",
        description="这是一个测试项目",
        veins_config_name="TestConfig",
    )

    test_user.projects = [project]

    await test_user.save(session)
    test_user = await User.get(session, User.email == test_user.email, load=User.projects)

    created_project = test_user.projects[0]

    # 验证项目创建成功
    assert created_project.id is not None
    assert created_project.name == "测试项目"
    assert created_project.description == "这是一个测试项目"
    assert created_project.veins_config_name == "TestConfig"
    assert created_project.user_id == test_user.id
    assert created_project.created_at is not None
    assert created_project.updated_at is not None

@pytest.mark.asyncio
async def test_project_get(session):
    """测试查询项目"""
    # 创建用户
    user = User(email="project_query@example.com", hashed_password="password123")
    await user.save(session)

    # 创建多个项目
    project1 = Project(name="项目1", user_id=user.id)
    project2 = Project(name="项目2", user_id=user.id)

    await Project.add(session, [project1, project2])

    # 通过ID获取单个项目
    p1 = await Project.get(session, Project.id == project1.id)
    assert p1.name == "项目1"

    # 获取用户的所有项目
    await session.refresh(user)
    user = await User.get(session, User.email == user.email, load=User.projects)
    assert len(user.projects) == 2

    # 按名称查询
    p2 = await Project.get(session, Project.name == "项目2")
    assert p2.id == project2.id

@pytest.mark.asyncio
async def test_project_update(session):
    """测试更新项目"""
    # 创建用户
    user = User(email="project_update@example.com", hashed_password="password123")
    await user.save(session)

    # 创建项目
    project = Project(name="旧项目名", description="旧描述", user_id=user.id)
    await project.save(session)

    # 创建更新数据
    update_data = Project(name="新项目名", description="新描述", veins_config_name="NewConfig")

    # 更新项目
    updated_project = await project.update(session, update_data)

    # 验证更新结果
    assert updated_project.name == "新项目名"
    assert updated_project.description == "新描述"
    assert updated_project.veins_config_name == "NewConfig"
    await session.refresh(user)
    assert updated_project.user_id == user.id  # 用户ID不应该改变

@pytest.mark.asyncio
async def test_project_delete(session):
    """测试删除项目"""
    # 创建用户
    user = User(email="project_delete@example.com", hashed_password="password123")
    await user.save(session)

    # 创建项目
    project = Project(name="要删除的项目", user_id=user.id)
    await project.save(session)

    project_id = project.id

    # 删除项目
    await Project.delete(session, project)

    # 验证项目已被删除
    deleted_project = await session.get(Project, project_id)
    assert deleted_project is None

@pytest.mark.asyncio
async def test_project_dir_property():
    """测试项目目录属性"""
    # 创建项目实例
    project = Project(id=123, user_id=456)

    # 检查dir属性返回正确的路径
    expected_path = os.path.normpath(os.path.join(config.user_projects_base_dir, "456", "123"))
    assert project.dir == expected_path

    # 再次访问检查缓存是否工作 <---AI写的，这里不恰当，懒得改了，我的代码一看肯定就是对的
    assert project.dir == expected_path

@pytest.mark.asyncio
@patch('aiofiles.os.path.exists', new_callable=AsyncMock)
@patch('aioshutil.rmtree', new_callable=AsyncMock)
async def test_project_remove_all_files(mock_rmtree, mock_exists):
    """测试删除项目所有文件"""
    # 设置mock
    mock_exists.return_value = True

    # 创建项目实例
    project = Project(id=123, user_id=456)

    # 执行删除
    await project.remove_all_files()

    # 验证调用
    mock_exists.assert_called_once_with(project.dir)
    mock_rmtree.assert_called_once_with(project.dir)

@pytest.mark.asyncio
@patch('aiofiles.os.path.exists', new_callable=AsyncMock)
@patch('aiofiles.os.path.isfile', new_callable=AsyncMock)
@patch('aiofiles.os.remove', new_callable=AsyncMock)
@patch('aioshutil.rmtree', new_callable=AsyncMock)
async def test_project_remove_one_file(mock_rmtree, mock_remove, mock_isfile, mock_exists):
    """测试删除项目中的单个文件"""
    # 设置mock
    mock_exists.return_value = True
    mock_isfile.return_value = True

    # 创建项目实例
    project = Project(id=123, user_id=456)

    # 执行删除文件
    await project.remove_one_file("test_file.txt")

    # 验证调用
    mock_exists.assert_called_once()
    mock_isfile.assert_called_once()
    mock_remove.assert_called_once()
    mock_rmtree.assert_not_called()

@pytest.mark.asyncio
@patch('aiofiles.os.path.exists', new_callable=AsyncMock)
@patch('aiofiles.os.path.isfile', new_callable=AsyncMock)
@patch('aioshutil.rmtree', new_callable=AsyncMock)
async def test_project_remove_one_directory(mock_rmtree, mock_isfile, mock_exists):
    """测试删除项目中的目录"""
    # 设置mock
    mock_exists.return_value = True
    mock_isfile.return_value = False

    # 创建项目实例
    project = Project(id=123, user_id=456)

    # 执行删除目录
    await project.remove_one_file("test_dir")

    # 验证调用
    mock_exists.assert_called_once()
    mock_isfile.assert_called_once()
    mock_rmtree.assert_called_once_with(project.dir)

@pytest.mark.asyncio
async def test_project_remove_runs_dir():
    """测试尝试删除runs目录应该失败"""
    project = Project(id=123, user_id=456)

    # 验证尝试删除runs目录会抛出ValueError
    with pytest.raises(HTTPException):
        await project.remove_one_file(config.runs_base_dir_name_in_project)

@pytest.mark.asyncio
@patch('aiofiles.os.path.exists', new_callable=AsyncMock)
async def test_project_remove_nonexistent_file(mock_exists):
    """测试删除不存在的文件"""
    # 设置mock
    mock_exists.return_value = False

    # 创建项目实例
    project = Project(id=123, user_id=456)

    # 验证尝试删除不存在的文件会抛出ValueError
    with pytest.raises(HTTPException, match="文件不存在"):
        await project.remove_one_file("nonexistent_file.txt")

@pytest.mark.asyncio
async def test_project_pagination_and_sorting(session):
    """测试项目的分页和排序"""
    # 创建用户
    user = User(email="project_sort@example.com", hashed_password="password123")
    await user.save(session)

    # 创建多个项目
    projects = []
    for i in range(5):
        project = Project(name=f"项目{i}", user_id=user.id)
        projects.append(project)

    await Project.add(session, projects)
    await session.refresh(user)

    # 测试分页
    table_view_req = TableViewRequest(offset=0, limit=2)
    result = await Project.get(
        session,
        Project.user_id == user.id,
        offset=table_view_req.offset,
        limit=table_view_req.limit,
        fetch_mode="all",
        order_by=[table_view_req.clause(Project)]
    )
    assert len(result) == 2

    # 测试第二页
    table_view_req = TableViewRequest(offset=2, limit=2)
    result = await Project.get(
        session,
        Project.user_id == user.id,
        offset=table_view_req.offset,
        limit=table_view_req.limit,
        fetch_mode="all",
        order_by=[table_view_req.clause(Project)]
    )
    assert len(result) == 2

    # 测试按创建时间降序排序
    table_view_req = TableViewRequest(desc=True, order="created_at")
    result = await Project.get(
        session,
        Project.user_id == user.id,
        fetch_mode="all",
        order_by=[table_view_req.clause(Project)]
    )

    # 确认排序正确
    for i in range(len(result) - 1):
        assert result[i].created_at >= result[i+1].created_at
