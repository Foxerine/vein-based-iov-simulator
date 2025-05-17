import os
import shutil
import tempfile
from io import BytesIO
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from fastapi import status

from config import config
from models import User, Project
from models.run import RunStatus, Run
from utils.auth import verify_password

@pytest.mark.asyncio
async def test_read_user_admin_all(client, admin_user, admin_user_token, normal_user):
    """测试管理员查看所有用户"""
    response = client.get(
        "/api/admin/user/",
        headers={"Authorization": f"Bearer {admin_user_token}"}
    )

    assert response.status_code == status.HTTP_200_OK
    users = response.json()
    assert isinstance(users, list)
    assert len(users) >= 2  # 至少包含管理员和普通用户

    # 验证返回的用户数据格式
    user_ids = [user["id"] for user in users]
    assert admin_user.id in user_ids
    assert normal_user.id in user_ids

@pytest.mark.asyncio
async def test_read_user_admin_specific(client, admin_user, admin_user_token, normal_user):
    """测试管理员查看特定用户"""
    response = client.get(
        f"/api/admin/user/{normal_user.id}",
        headers={"Authorization": f"Bearer {admin_user_token}"}
    )

    assert response.status_code == status.HTTP_200_OK
    user = response.json()
    assert user["id"] == normal_user.id
    assert user["email"] == normal_user.email

@pytest.mark.asyncio
async def test_read_user_admin_nonexistent(client, admin_user, admin_user_token):
    """测试管理员查看不存在的用户"""
    response = client.get(
        "/api/admin/user/9999",
        headers={"Authorization": f"Bearer {admin_user_token}"}
    )

    assert response.status_code == status.HTTP_404_NOT_FOUND

@pytest.mark.asyncio
async def test_update_user_admin(client, session, admin_user, admin_user_token, normal_user):
    """测试管理员更新用户信息"""
    response = client.patch(
        f"/api/admin/user/{normal_user.id}",
        headers={"Authorization": f"Bearer {admin_user_token}"},
        json={
            "email": "admin-updated@example.com",
            "password": "adminsetpass",
            "is_active": False,
            "is_admin": True
        }
    )

    assert response.status_code == status.HTTP_200_OK
    data = response.json()
    assert data["email"] == "admin-updated@example.com"
    assert data["is_active"] == False
    assert data["is_admin"] == True

    # 验证数据库中的更改
    updated_user = await User.get_exist_one(session, normal_user.id)
    assert updated_user.email == "admin-updated@example.com"
    assert updated_user.is_active == False
    assert updated_user.is_admin == True
    assert verify_password("adminsetpass", updated_user.hashed_password)

@pytest.mark.asyncio
async def test_delete_user_admin(client, session, admin_user, admin_user_token, normal_user):
    """测试管理员删除用户"""
    user_id = normal_user.id

    response = client.delete(
        f"/api/admin/user/{user_id}",
        headers={"Authorization": f"Bearer {admin_user_token}"}
    )

    assert response.status_code == status.HTTP_200_OK

    # 验证用户是否已从数据库中删除
    deleted_user = await session.get(User, user_id)
    assert deleted_user is None

@pytest.mark.asyncio
async def test_non_admin_access(client, normal_user_token):
    """测试普通用户访问管理员路由"""
    response = client.get(
        "/api/admin/user/",
        headers={"Authorization": f"Bearer {normal_user_token}"}
    )

    assert response.status_code == status.HTTP_403_FORBIDDEN

@pytest.mark.asyncio
async def test_update_user_admin_partial(client, session, admin_user, admin_user_token, normal_user):
    """测试管理员部分更新用户信息"""
    original_password = normal_user.hashed_password

    response = client.patch(
        f"/api/admin/user/{normal_user.id}",
        headers={"Authorization": f"Bearer {admin_user_token}"},
        json={
            "email": "partial-update@example.com",
            "is_active": False
        }
    )

    assert response.status_code == status.HTTP_200_OK
    data = response.json()
    assert data["email"] == "partial-update@example.com"
    assert data["is_active"] == False

    # 验证只有指定字段被更新
    updated_user = await User.get_exist_one(session, normal_user.id)
    assert updated_user.email == "partial-update@example.com"
    assert updated_user.is_active == False
    assert updated_user.is_admin == normal_user.is_admin
    assert updated_user.hashed_password == original_password

@pytest.fixture(scope="function")
def setup_project_dir():
    """设置测试项目目录并在测试后清理"""
    # 确保测试目录存在
    os.makedirs(config.user_projects_base_dir, exist_ok=True)

    yield

    # 测试完成后清理所有创建的项目目录
    for user_id in os.listdir(config.user_projects_base_dir):
        user_dir = os.path.join(config.user_projects_base_dir, user_id)
        if os.path.isdir(user_dir):
            shutil.rmtree(user_dir)

@pytest.fixture
def test_file():
    """创建测试文件"""
    return ('files', ('test_file.txt', BytesIO(b'test_file_content'), 'text/plain'))

@pytest.mark.asyncio
async def test_list_projects_admin(client, session, admin_user, admin_user_token, normal_user, normal_user_token, setup_project_dir):
    """测试管理员查看所有项目"""
    # 先为普通用户和管理员各创建一个测试项目
    admin_project = client.post(
        "/api/project?name=管理员项目&veins_config_name=AdminConfig",
        files=[('files', ('admin.txt', BytesIO(b'admin content'), 'text/plain'))],
        headers={"Authorization": f"Bearer {admin_user_token}"}
    ).json()

    normal_project = client.post(
        "/api/project?name=普通用户项目&veins_config_name=UserConfig",
        files=[('files', ('user.txt', BytesIO(b'user content'), 'text/plain'))],
        headers={"Authorization": f"Bearer {normal_user_token}"}
    ).json()

    # 管理员查看所有项目
    response = client.get(
        "/api/admin/project",
        headers={"Authorization": f"Bearer {admin_user_token}"}
    )

    assert response.status_code == status.HTTP_200_OK
    projects = response.json()
    assert isinstance(projects, list)
    assert len(projects) >= 2

    project_ids = [proj["id"] for proj in projects]
    assert admin_project["id"] in project_ids
    assert normal_project["id"] in project_ids

@pytest.mark.asyncio
async def test_list_projects_by_user_admin(
        client,
        session,
        admin_user,
        admin_user_token,
        normal_user,
        normal_user_token,
        setup_project_dir
):
    """测试管理员查看特定用户的项目"""
    # 先为普通用户创建测试项目
    client.post(
        "/api/project?name=用户专属项目&veins_config_name=UserConfig",
        files=[('files', ('user.txt', BytesIO(b'user content'), 'text/plain'))],
        headers={"Authorization": f"Bearer {normal_user_token}"}
    )

    await session.refresh(normal_user)

    # 管理员查看普通用户的项目
    response = client.get(
        f"/api/admin/user/{normal_user.id}/projects",
        headers={"Authorization": f"Bearer {admin_user_token}"}
    )

    assert response.status_code == status.HTTP_200_OK
    projects = response.json()
    for project in projects:
        assert project["user_id"] == normal_user.id

@pytest.mark.asyncio
async def test_get_project_admin(client, admin_user_token, normal_user_token, setup_project_dir):
    """测试管理员查看特定项目详情"""
    # 创建测试项目
    project = client.post(
        "/api/project?name=测试详情项目&veins_config_name=TestConfig",
        files=[('files', ('test.txt', BytesIO(b'test content'), 'text/plain'))],
        headers={"Authorization": f"Bearer {normal_user_token}"}
    ).json()

    # 查看项目详情
    response = client.get(
        f"/api/admin/project/{project['id']}",
        headers={"Authorization": f"Bearer {admin_user_token}"}
    )

    assert response.status_code == status.HTTP_200_OK
    project_info = response.json()
    assert project_info["name"] == "测试详情项目"
    assert project_info["veins_config_name"] == "TestConfig"
    assert any(file[0] == "test.txt" for file in project_info["files"])

@pytest.mark.asyncio
async def test_update_project_admin(client, admin_user_token, normal_user_token, setup_project_dir):
    """测试管理员更新项目"""
    # 创建测试项目
    project = client.post(
        "/api/project?name=原始项目名&veins_config_name=OldConfig",
        files=[('files', ('test.txt', BytesIO(b'test content'), 'text/plain'))],
        headers={"Authorization": f"Bearer {normal_user_token}"}
    ).json()

    # 更新项目
    response = client.patch(
        f"/api/admin/project/{project['id']}?name=更新后的项目名&veins_config_name=NewConfig",
        headers={"Authorization": f"Bearer {admin_user_token}"}
    )

    assert response.status_code == status.HTTP_200_OK
    updated_project = response.json()
    assert updated_project["name"] == "更新后的项目名"
    assert updated_project["veins_config_name"] == "NewConfig"

@pytest.mark.asyncio
async def test_delete_file_admin(client, admin_user_token, normal_user_token, setup_project_dir):
    """测试管理员删除项目文件"""
    # 创建测试项目
    project = client.post(
        "/api/project?name=文件测试项目&veins_config_name=Default",
        files=[('files', ('test.txt', BytesIO(b'test content'), 'text/plain'))],
        headers={"Authorization": f"Bearer {normal_user_token}"}
    ).json()

    # 删除文件
    response = client.delete(
        f"/api/admin/project/{project['id']}/files/test.txt",
        headers={"Authorization": f"Bearer {admin_user_token}"}
    )

    assert response.status_code == status.HTTP_200_OK
    updated_project = response.json()
    assert not any(file[0] == "test.txt" for file in updated_project["files"])

@pytest.mark.asyncio
async def test_delete_project_admin(client, session, admin_user_token, normal_user_token, setup_project_dir):
    """测试管理员删除项目"""
    # 创建测试项目
    project = client.post(
        "/api/project?name=要删除的项目&veins_config_name=Default",
        files=[('files', ('test.txt', BytesIO(b'delete me'), 'text/plain'))],
        headers={"Authorization": f"Bearer {normal_user_token}"}
    ).json()

    project_id = project["id"]

    # 删除项目
    response = client.delete(
        f"/api/admin/project/{project_id}",
        headers={"Authorization": f"Bearer {admin_user_token}"}
    )

    assert response.status_code == status.HTTP_200_OK
    assert response.json() is True

    # 验证项目是否已从数据库中删除
    deleted_project = await session.get(Project, project_id)
    assert deleted_project is None

@pytest.mark.asyncio
@patch('utils.files.create_zip_archive')
async def test_download_project_zip_admin(mock_create_zip, client, admin_user_token, normal_user_token, setup_project_dir):
    """测试管理员下载项目ZIP"""
    # 模拟ZIP创建结果
    temp_file = tempfile.NamedTemporaryFile(delete=False, suffix='.zip')
    temp_file.write(b'fake zip content')
    temp_file.close()
    mock_create_zip.return_value = (temp_file.name, "project.zip")

    # 创建测试项目
    project = client.post(
        "/api/project?name=ZIP测试项目&veins_config_name=Default",
        files=[('files', ('test.txt', BytesIO(b'zip me'), 'text/plain'))],
        headers={"Authorization": f"Bearer {normal_user_token}"}
    ).json()

    # 下载ZIP
    response = client.get(
        f"/api/admin/project/{project['id']}/files",
        headers={"Authorization": f"Bearer {admin_user_token}"}
    )

    assert "filename*=utf-8''" in response.headers["content-disposition"]
    assert ".zip" in response.headers["content-disposition"]
    assert "attachment;" in response.headers["content-disposition"]

    # 清理临时文件
    os.unlink(temp_file.name)

# ==================== 仿真运行管理测试 ====================

@pytest.mark.asyncio
@patch('models.run.Run._prepare_execution', new_callable=AsyncMock)
@patch('worker.worker.celery_app.send_task')
async def test_list_runs_admin(mock_send_task, mock_prepare, client, session, admin_user_token, normal_user_token, setup_project_dir):
    """测试管理员查看所有仿真运行"""
    # 设置mock
    mock_task = MagicMock()
    mock_task.id = "test-task-id"
    mock_send_task.return_value = mock_task

    # 创建项目
    resp = client.post(
        "/api/project?name=仿真测试项目&veins_config_name=Default",
        files=[('files', ('test.txt', BytesIO(b'test content'), 'text/plain'))],
        headers={"Authorization": f"Bearer {normal_user_token}"}
    )

    assert resp.status_code == status.HTTP_200_OK

    project = resp.json()

    # 创建两个仿真运行
    run1 = client.post(
        "/api/run",
        json={"project_id": project["id"], "notes": "运行1"},
        headers={"Authorization": f"Bearer {normal_user_token}"}
    ).json()

    run2 = client.post(
        "/api/run",
        json={"project_id": project["id"], "notes": "运行2"},
        headers={"Authorization": f"Bearer {normal_user_token}"}
    ).json()

    # 执行一个仿真
    client.post(
        f"/api/run/{run1['id']}/execute",
        headers={"Authorization": f"Bearer {normal_user_token}"}
    )

    # 保存原始方法
    original_get_status = Run.get_status

    # 创建模拟方法
    async def mock_get_status(self, session):
        """模拟状态更新"""
        if hasattr(self, 'task_id') and self.task_id:
            if self.id == run1['id']:
                self.status = RunStatus.RUNNING
        return self

    try:
        # 替换方法
        Run.get_status = mock_get_status

        # 管理员查看所有仿真
        response = client.get(
            "/api/admin/run",
            headers={"Authorization": f"Bearer {admin_user_token}"}
        )

        assert response.status_code == status.HTTP_200_OK
        runs = response.json()
        assert len(runs) >= 2

        run_ids = [run["id"] for run in runs]
        assert run1["id"] in run_ids
        assert run2["id"] in run_ids

        # 验证状态被正确更新
        for run in runs:
            if run["id"] == run1["id"]:
                assert run["status"] == RunStatus.RUNNING

    finally:
        # 恢复原始方法
        Run.get_status = original_get_status

@pytest.mark.asyncio
@patch('models.run.Run._prepare_execution', new_callable=AsyncMock)
@patch('worker.worker.celery_app.send_task')
async def test_list_runs_by_user_admin(mock_send_task, mock_prepare, client, session, admin_user, admin_user_token, normal_user, normal_user_token, setup_project_dir):
    """测试管理员查看特定用户的仿真运行"""
    # 设置mock
    mock_task = MagicMock()
    mock_task.id = "test-task-id"
    mock_send_task.return_value = mock_task

    # 为普通用户创建项目和运行
    normal_user_project = client.post(
        "/api/project?name=用户项目&veins_config_name=Default",
        files=[('files', ('user.txt', BytesIO(b'user content'), 'text/plain'))],
        headers={"Authorization": f"Bearer {normal_user_token}"}
    ).json()

    normal_user_run = client.post(
        "/api/run",
        json={"project_id": normal_user_project["id"], "notes": "用户运行"},
        headers={"Authorization": f"Bearer {normal_user_token}"}
    ).json()

    # 为管理员创建项目和运行
    admin_project = client.post(
        "/api/project?name=管理员项目&veins_config_name=Default",
        files=[('files', ('admin.txt', BytesIO(b'admin content'), 'text/plain'))],
        headers={"Authorization": f"Bearer {admin_user_token}"}
    ).json()

    admin_run = client.post(
        "/api/run",
        json={"project_id": admin_project["id"], "notes": "管理员运行"},
        headers={"Authorization": f"Bearer {admin_user_token}"}
    ).json()

    # 执行仿真
    client.post(
        f"/api/run/{normal_user_run['id']}/execute",
        headers={"Authorization": f"Bearer {admin_user_token}"}
    )

    # 管理员查看普通用户的仿真运行
    await session.refresh(normal_user)
    response = client.get(
        f"/api/admin/user/{normal_user.id}/runs",
        headers={"Authorization": f"Bearer {admin_user_token}"}
    )

    assert response.status_code == status.HTTP_200_OK
    runs = response.json()
    assert len(runs) > 0

    # 验证只返回普通用户的运行
    run_ids = [run["id"] for run in runs]
    assert normal_user_run["id"] in run_ids
    assert admin_run["id"] not in run_ids

@pytest.mark.asyncio
@patch('models.run.Run._prepare_execution', new_callable=AsyncMock)
@patch('worker.worker.celery_app.send_task')
async def test_list_runs_by_project_admin(mock_send_task, mock_prepare, client, session, admin_user_token, normal_user_token, setup_project_dir):
    """测试管理员查看特定项目的仿真运行"""
    # 设置mock
    mock_task = MagicMock()
    mock_task.id = "test-task-id"
    mock_send_task.return_value = mock_task

    # 创建两个项目
    project1 = client.post(
        "/api/project?name=项目1&veins_config_name=Default",
        files=[('files', ('test1.txt', BytesIO(b'content1'), 'text/plain'))],
        headers={"Authorization": f"Bearer {normal_user_token}"}
    )

    assert project1.status_code == status.HTTP_200_OK
    project1 = project1.json()

    project2 = client.post(
        "/api/project?name=项目2&veins_config_name=Default",
        files=[('files', ('test2.txt', BytesIO(b'content2'), 'text/plain'))],
        headers={"Authorization": f"Bearer {normal_user_token}"}
    )

    assert project2.status_code == status.HTTP_200_OK
    project2 = project2.json()

    # 为每个项目创建仿真运行
    run1 = client.post(
        "/api/run",
        json={"project_id": project1["id"], "notes": "项目1的运行"},
        headers={"Authorization": f"Bearer {normal_user_token}"}
    ).json()

    run2 = client.post(
        "/api/run",
        json={"project_id": project2["id"], "notes": "项目2的运行"},
        headers={"Authorization": f"Bearer {normal_user_token}"}
    ).json()

    # 管理员查看项目1的仿真
    response = client.get(
        f"/api/admin/project/{project1['id']}/runs",
        headers={"Authorization": f"Bearer {admin_user_token}"}
    )

    assert response.status_code == status.HTTP_200_OK
    runs = response.json()

    # 验证只返回项目1的运行
    run_ids = [run["id"] for run in runs]
    assert run1["id"] in run_ids
    assert run2["id"] not in run_ids

@pytest.mark.asyncio
@patch('models.run.Run._prepare_execution', new_callable=AsyncMock)
@patch('worker.worker.celery_app.send_task')
async def test_get_run_admin(mock_send_task, mock_prepare, client, session, admin_user_token, normal_user_token, setup_project_dir):
    """测试管理员查看特定仿真运行详情"""
    # 设置mock
    mock_task = MagicMock()
    mock_task.id = "test-task-id"
    mock_send_task.return_value = mock_task

    # 创建项目和运行
    project = client.post(
        "/api/project?name=详情测试项目&veins_config_name=Default",
        files=[('files', ('test.txt', BytesIO(b'test content'), 'text/plain'))],
        headers={"Authorization": f"Bearer {normal_user_token}"}
    )
    assert project.status_code == status.HTTP_200_OK
    project = project.json()

    run = client.post(
        "/api/run",
        json={"project_id": project["id"], "notes": "测试运行"},
        headers={"Authorization": f"Bearer {normal_user_token}"}
    ).json()

    # 执行仿真
    client.post(
        f"/api/run/{run['id']}/execute",
        headers={"Authorization": f"Bearer {normal_user_token}"}
    )

    # 保存原始方法
    original_get_status = Run.get_status

    # 创建模拟方法
    async def mock_get_status(self, session):
        """模拟状态更新"""
        self.status = RunStatus.RUNNING  # 固定返回RUNNING状态
        return self

    try:
        # 替换方法
        Run.get_status = mock_get_status

        # 查看运行详情
        response = client.get(
            f"/api/admin/run/{run['id']}",
            headers={"Authorization": f"Bearer {admin_user_token}"}
        )

        assert response.status_code == status.HTTP_200_OK
        run_info = response.json()
        assert run_info["id"] == run["id"]
        assert run_info["notes"] == "测试运行"
        assert run_info["status"] == RunStatus.RUNNING  # 现在是确定的状态
        assert run_info["project_id"] == project["id"]

    finally:
        # 恢复原始方法
        Run.get_status = original_get_status

@pytest.mark.asyncio
@patch('models.run.Run._prepare_execution', new_callable=AsyncMock)
@patch('worker.worker.celery_app.send_task')
async def test_execute_run_admin(mock_send_task, mock_prepare, client, admin_user_token, normal_user_token, setup_project_dir):
    """测试管理员执行仿真"""
    # 设置mock
    mock_task = MagicMock()
    mock_task.id = "test-task-id"
    mock_send_task.return_value = mock_task

    # 创建项目和运行
    project = client.post(
        "/api/project?name=执行测试项目&veins_config_name=Default",
        files=[('files', ('test.txt', BytesIO(b'test content'), 'text/plain'))],
        headers={"Authorization": f"Bearer {normal_user_token}"}
    ).json()

    run = client.post(
        "/api/run",
        json={"project_id": project["id"]},
        headers={"Authorization": f"Bearer {normal_user_token}"}
    ).json()

    # 管理员执行仿真
    execute_response = client.post(
        f"/api/admin/run/{run['id']}/execute",
        headers={"Authorization": f"Bearer {admin_user_token}"}
    )

    assert execute_response.status_code == status.HTTP_200_OK
    executed_run = execute_response.json()
    assert executed_run["id"] == run["id"]
    assert executed_run["status"] == RunStatus.STARTING
    assert executed_run["start_time"] is not None

    # 验证mock调用
    mock_prepare.assert_called_once()
    mock_send_task.assert_called_once()

    # 测试重复执行会失败
    repeat_response = client.post(
        f"/api/admin/run/{run['id']}/execute",
        headers={"Authorization": f"Bearer {admin_user_token}"}
    )
    assert repeat_response.status_code == status.HTTP_400_BAD_REQUEST

@pytest.mark.asyncio
@patch('worker.worker.celery_app.control.revoke')
async def test_cancel_run_admin(mock_revoke, client, session, admin_user_token, normal_user_token, setup_project_dir):
    """测试管理员取消运行中的仿真"""
    # 创建项目
    project = client.post(
        "/api/project?name=取消测试项目&veins_config_name=Default",
        files=[('files', ('test.txt', BytesIO(b'test content'), 'text/plain'))],
        headers={"Authorization": f"Bearer {normal_user_token}"}
    )
    assert project.status_code == status.HTTP_200_OK
    project = project.json()

    # 创建运行
    run_response = client.post(
        "/api/run",
        json={"project_id": project["id"]},
        headers={"Authorization": f"Bearer {normal_user_token}"}
    )
    run_id = run_response.json()["id"]

    # 手动设置运行状态和任务ID
    run = await Run.get_exist_one(session, run_id)
    run.status = RunStatus.RUNNING
    run.task_id = "task-to-cancel"
    await run.save(session)

    # 取消运行
    cancel_response = client.post(
        f"/api/admin/run/{run_id}/cancel",
        headers={"Authorization": f"Bearer {admin_user_token}"}
    )

    assert cancel_response.status_code == status.HTTP_200_OK
    cancel_data = cancel_response.json()
    assert cancel_data["id"] == run_id
    assert cancel_data["status"] == RunStatus.CANCELLING

    # 验证mock调用
    mock_revoke.assert_called_once_with(
        "task-to-cancel",
        terminate=True,
        signal='SIGTERM'
    )

@pytest.mark.asyncio
async def test_delete_run_admin(client, session, admin_user_token, normal_user_token, setup_project_dir):
    """测试管理员删除仿真运行"""
    # 创建项目
    project = client.post(
        "/api/project?name=删除运行测试&veins_config_name=Default",
        files=[('files', ('test.txt', BytesIO(b'test content'), 'text/plain'))],
        headers={"Authorization": f"Bearer {normal_user_token}"}
    ).json()

    # 创建运行
    run_response = client.post(
        "/api/run",
        json={"project_id": project["id"]},
        headers={"Authorization": f"Bearer {normal_user_token}"}
    )
    run_id = run_response.json()["id"]

    # 删除运行
    delete_response = client.delete(
        f"/api/admin/run/{run_id}",
        headers={"Authorization": f"Bearer {admin_user_token}"}
    )

    assert delete_response.status_code == status.HTTP_200_OK
    assert delete_response.json() is True

    # 验证运行是否已从数据库中删除
    deleted_run = await session.get(Run, run_id)
    assert deleted_run is None

@pytest.mark.asyncio
async def test_get_run_file_admin(client, session, normal_user_token, admin_user_token, setup_project_dir):
    """测试管理员下载仿真结果文件"""
    # 创建项目
    project = client.post(
        "/api/project?name=文件下载测试&veins_config_name=Default",
        files=[('files', ('test.txt', BytesIO(b'test content'), 'text/plain'))],
        headers={"Authorization": f"Bearer {normal_user_token}"}
    ).json()

    # 创建运行
    run_response = client.post(
        "/api/run",
        json={"project_id": project["id"]},
        headers={"Authorization": f"Bearer {normal_user_token}"}
    )
    run_id = run_response.json()["id"]

    # 获取Run实例
    run = await Run.get_exist_one(session, run_id, load=Run.project)

    # 手动创建结果文件
    os.makedirs(run.dir, exist_ok=True)
    with open(os.path.join(run.dir, "result.txt"), "w") as f:
        f.write("仿真结果数据")

    # 下载文件
    file_response = client.get(
        f"/api/admin/run/{run_id}/files/result.txt",
        headers={"Authorization": f"Bearer {admin_user_token}"}
    )

    assert file_response.status_code == status.HTTP_200_OK
    assert file_response.content == b"\xe4\xbb\xbf\xe7\x9c\x9f\xe7\xbb\x93\xe6\x9e\x9c\xe6\x95\xb0\xe6\x8d\xae"  # "仿真结果数据"的UTF-8编码

@pytest.mark.asyncio
@patch('utils.files.create_zip_archive')
async def test_download_run_results_zip_admin(mock_create_zip, client, session, admin_user_token, setup_project_dir):
    """测试管理员下载仿真结果ZIP"""
    # 模拟ZIP创建结果
    temp_file = tempfile.NamedTemporaryFile(delete=False, suffix='.zip')
    temp_file.write(b'fake zip content')
    temp_file.close()
    mock_create_zip.return_value = (temp_file.name, "run.zip")

    # 创建项目
    project = client.post(
        "/api/project?name=结果ZIP测试&veins_config_name=Default",
        files=[('files', ('test.txt', BytesIO(b'test content'), 'text/plain'))],
        headers={"Authorization": f"Bearer {admin_user_token}"}
    )
    assert project.status_code == status.HTTP_200_OK
    project = project.json()

    # 创建运行
    run_response = client.post(
        "/api/run",
        json={"project_id": project["id"]},
        headers={"Authorization": f"Bearer {admin_user_token}"}
    )
    run_id = run_response.json()["id"]

    # 下载ZIP
    response = client.get(
        f"/api/admin/run/{run_id}/files",
        headers={"Authorization": f"Bearer {admin_user_token}"}
    )

    assert "filename*=utf-8''" in response.headers["content-disposition"]
    assert ".zip" in response.headers["content-disposition"]
    assert "attachment;" in response.headers["content-disposition"]

    # 清理临时文件
    os.unlink(temp_file.name)

@pytest.mark.asyncio
async def test_admin_access_requires_admin_role(client, normal_user_token):
    """测试非管理员不能访问管理员API"""
    endpoints = [
        ("/api/admin/user", "GET"),
        ("/api/admin/user/1", "GET"),
        ("/api/admin/user/1", "PATCH"),
        ("/api/admin/user/1", "DELETE"),
        ("/api/admin/project", "GET"),
        ("/api/admin/project/1", "GET"),
        ("/api/admin/project/1", "PATCH"),
        ("/api/admin/project/1", "DELETE"),
        ("/api/admin/run", "GET"),
        ("/api/admin/run/1", "GET"),
        ("/api/admin/run/1/execute", "POST"),
        ("/api/admin/run/1/cancel", "POST"),
        ("/api/admin/run/1", "DELETE"),
    ]

    for endpoint, method in endpoints:
        if method == "GET":
            response = client.get(endpoint, headers={"Authorization": f"Bearer {normal_user_token}"})
        elif method == "POST":
            response = client.post(endpoint, headers={"Authorization": f"Bearer {normal_user_token}"})
        elif method == "PATCH":
            response = client.patch(endpoint, headers={"Authorization": f"Bearer {normal_user_token}"})
        elif method == "DELETE":
            response = client.delete(endpoint, headers={"Authorization": f"Bearer {normal_user_token}"})

        assert response.status_code == status.HTTP_403_FORBIDDEN, f"{method} {endpoint} 应拒绝非管理员访问"

