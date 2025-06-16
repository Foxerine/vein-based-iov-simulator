import os
from io import BytesIO
from unittest.mock import patch, MagicMock, AsyncMock

import pytest

from config import config
from models.run import Run, RunStatus
from utils.depends import SessionDep


@pytest.fixture(scope="function")
def setup_run_dirs():
    """设置测试运行目录并在测试后清理"""
    # 确保测试目录存在
    os.makedirs(config.user_projects_base_dir, exist_ok=True)

    yield

    # 测试完成后清理所有创建的项目目录
    for user_id in os.listdir(config.user_projects_base_dir):
        user_dir = os.path.join(config.user_projects_base_dir, user_id)
        if os.path.isdir(user_dir):
            for project_id in os.listdir(user_dir):
                project_dir = os.path.join(user_dir, project_id)
                if os.path.isdir(project_dir):
                    import shutil
                    shutil.rmtree(project_dir)

@pytest.fixture
def create_test_file():
    """创建测试结果文件"""
    async def _create_file(run_dir):
        os.makedirs(run_dir, exist_ok=True)
        with open(os.path.join(run_dir, "result.txt"), "w") as f:
            f.write("this is the test result")
    return _create_file

@pytest.mark.asyncio
@patch('models.run.Run._prepare_execution', new_callable=AsyncMock)
@patch('worker.worker.celery_app.send_task')
async def test_create_and_execute_run(mock_send_task, mock_prepare, client, session, normal_user, normal_user_token, setup_run_dirs):
    """测试创建和执行仿真运行"""
    # 设置mock
    mock_task = MagicMock()
    mock_task.id = "test-task-id"
    mock_send_task.return_value = mock_task

    # 首先创建一个项目
    project_response = client.post(
        "/api/project?name=仿真测试项目&description=测试描述&veins_config_name=TestConfig",
        files=[('files', ('test.txt', BytesIO(b'test content'), 'text/plain'))],
        headers={"Authorization": f"Bearer {normal_user_token}"}
    )
    project_id = project_response.json()["id"]

    # 创建仿真运行
    run_response = client.post(
        "/api/run",
        json={"project_id": project_id, "notes": "测试运行"},
        headers={"Authorization": f"Bearer {normal_user_token}"}
    )

    assert run_response.status_code == 200
    run_data = run_response.json()
    assert run_data["project_id"] == project_id
    assert run_data["notes"] == "测试运行"
    assert run_data["status"] == RunStatus.PENDING

    # 执行仿真
    execute_response = client.post(
        f"/api/run/{run_data['id']}/execute",
        headers={"Authorization": f"Bearer {normal_user_token}"}
    )

    assert execute_response.status_code == 200
    execute_data = execute_response.json()
    assert execute_data["id"] == run_data["id"]
    assert execute_data["status"] == RunStatus.STARTING
    assert execute_data["start_time"] is not None

    # 验证mock调用
    mock_prepare.assert_called_once()
    mock_send_task.assert_called_once()

@pytest.mark.asyncio
async def test_get_run_status(client, session, normal_user, normal_user_token, setup_run_dirs):
    """测试获取仿真运行状态"""
    # 首先创建一个项目
    project_response = client.post(
        "/api/project?name=状态测试项目&veins_config_name=TestConfig",
        files=[('files', ('test.txt', BytesIO(b'test content'), 'text/plain'))],
        headers={"Authorization": f"Bearer {normal_user_token}"}
    )
    project_id = project_response.json()["id"]

    # 创建仿真运行
    run_response = client.post(
        "/api/run",
        json={"project_id": project_id},
        headers={"Authorization": f"Bearer {normal_user_token}"}
    )
    run_id = run_response.json()["id"]

    # 手动设置运行状态和任务ID
    run = await Run.get_exist_one(session, run_id)
    run.status = RunStatus.RUNNING
    run.task_id = "test-task-id"
    await run.save(session)

    # 直接覆盖Run.get_status方法进行mock
    original_get_status = Run.get_status

    async def mock_get_status(self, session):
        """模拟状态更新"""
        self.status = RunStatus.RUNNING  # 固定返回RUNNING状态
        return self

    try:
        # 替换方法
        Run.get_status = mock_get_status

        # 获取状态
        status_response = client.get(
            f"/api/run/{run_id}",
            headers={"Authorization": f"Bearer {normal_user_token}"}
        )

        # 验证响应
        assert status_response.status_code == 200
        status_data = status_response.json()
        assert status_data["id"] == run_id
        assert status_data["status"] == RunStatus.RUNNING

    finally:
        # 恢复原始方法
        Run.get_status = original_get_status

@pytest.mark.asyncio
@patch('worker.worker.celery_app.control.revoke')
async def test_cancel_run(mock_revoke, client, session, normal_user, normal_user_token, setup_run_dirs):
    """测试取消仿真运行"""
    # 首先创建一个项目
    project_response = client.post(
        "/api/project?name=取消测试项目&veins_config_name=TestConfig",
        files=[('files', ('test.txt', BytesIO(b'test content'), 'text/plain'))],
        headers={"Authorization": f"Bearer {normal_user_token}"}
    )
    project_id = project_response.json()["id"]

    # 创建仿真运行
    run_response = client.post(
        "/api/run",
        json={"project_id": project_id},
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
        f"/api/run/{run_id}/cancel",
        headers={"Authorization": f"Bearer {normal_user_token}"}
    )

    assert cancel_response.status_code == 200
    cancel_data = cancel_response.json()
    assert cancel_data["id"] == run_id
    assert cancel_data["status"] == RunStatus.CANCELLED


    # 测试取消已成功完成的任务
    run.status = RunStatus.SUCCESS
    await run.save(session)

    invalid_cancel_response = client.post(
        f"/api/run/{run_id}/cancel",
        headers={"Authorization": f"Bearer {normal_user_token}"}
    )

    assert invalid_cancel_response.status_code == 400

@pytest.mark.asyncio
async def test_get_run_file(client, session, normal_user, normal_user_token, setup_run_dirs, create_test_file):
    """测试获取仿真结果文件"""
    # 首先创建一个项目
    project_response = client.post(
        "/api/project?name=文件测试项目&veins_config_name=TestConfig",
        files=[('files', ('test.txt', BytesIO(b'test content'), 'text/plain'))],
        headers={"Authorization": f"Bearer {normal_user_token}"}
    )
    project_id = project_response.json()["id"]

    # 创建仿真运行
    run_response = client.post(
        "/api/run",
        json={"project_id": project_id},
        headers={"Authorization": f"Bearer {normal_user_token}"}
    )

    run_id = run_response.json()["id"]

    # 获取Run实例并准备测试文件
    run = await Run.get_exist_one(session, run_id)
    run.project_id = project_id
    await run.save(session)

    # 创建结果文件
    run = await Run.get(session, Run.id == run.id, load=Run.project)
    await create_test_file(run.dir)

    # 获取文件
    file_response = client.get(
        f"/api/run/{run_id}/files/result.txt",
        headers={"Authorization": f"Bearer {normal_user_token}"}
    )

    assert file_response.status_code == 200
    assert file_response.content == b"this is the test result"

@pytest.mark.asyncio
async def test_permission_checks(client, session, normal_user, normal_user_token, admin_user, admin_user_token, setup_run_dirs):
    """测试权限检查"""
    # 管理员创建项目
    admin_project_response = client.post(
        "/api/project?name=管理员项目&veins_config_name=TestConfig",
        files=[('files', ('test.txt', BytesIO(b'admin content'), 'text/plain'))],
        headers={"Authorization": f"Bearer {admin_user_token}"}
    )
    admin_project_id = admin_project_response.json()["id"]

    # 管理员创建运行
    admin_run_response = client.post(
        "/api/run",
        json={"project_id": admin_project_id},
        headers={"Authorization": f"Bearer {admin_user_token}"}
    )
    admin_run_id = admin_run_response.json()["id"]

    # 普通用户尝试访问管理员的运行
    invalid_response = client.get(
        f"/api/run/{admin_run_id}",
        headers={"Authorization": f"Bearer {normal_user_token}"}
    )

    assert invalid_response.status_code == 404

    # 普通用户尝试执行管理员的运行
    invalid_execute_response = client.post(
        f"/api/run/{admin_run_id}/execute",
        headers={"Authorization": f"Bearer {normal_user_token}"}
    )

    assert invalid_execute_response.status_code == 404

    # 普通用户尝试取消管理员的运行
    invalid_cancel_response = client.post(
        f"/api/run/{admin_run_id}/cancel",
        headers={"Authorization": f"Bearer {normal_user_token}"}
    )

    assert invalid_cancel_response.status_code == 404

@pytest.mark.asyncio
async def test_error_handling(client, normal_user_token, setup_run_dirs):
    """测试错误处理"""
    # 尝试访问不存在的运行
    not_found_response = client.get(
        "/api/run/99999",
        headers={"Authorization": f"Bearer {normal_user_token}"}
    )
    assert not_found_response.status_code == 404

    # 尝试执行不存在的运行
    not_found_execute_response = client.post(
        "/api/run/99999/execute",
        headers={"Authorization": f"Bearer {normal_user_token}"}
    )
    assert not_found_execute_response.status_code == 404

    # 尝试取消不存在的运行
    not_found_cancel_response = client.post(
        "/api/run/99999/cancel",
        headers={"Authorization": f"Bearer {normal_user_token}"}
    )
    assert not_found_cancel_response.status_code == 404

    # 尝试获取不存在的运行文件
    not_found_file_response = client.get(
        "/api/run/99999/files/result.txt",
        headers={"Authorization": f"Bearer {normal_user_token}"}
    )
    assert not_found_file_response.status_code == 404

    # 未认证访问
    unauthorized_response = client.get("/api/run/1")
    assert unauthorized_response.status_code == 401

@pytest.mark.asyncio
@patch('utils.files.create_zip_archive')
async def test_download_run_results_zip(mock_create_zip, client, session, normal_user_token, setup_run_dirs):
    """测试下载运行结果ZIP包"""
    # 设置mock
    import tempfile
    temp_file = tempfile.NamedTemporaryFile(delete=False, suffix='.zip')
    temp_file.write(b'fake run zip content')
    temp_file.close()
    mock_create_zip.return_value = (temp_file.name, "run.zip")

    # 创建项目
    project_response = client.post(
        "/api/project?name=ZIP下载测试&veins_config_name=TestConfig",
        files=[('files', ('test.txt', BytesIO(b'test content'), 'text/plain'))],
        headers={"Authorization": f"Bearer {normal_user_token}"}
    )
    project_id = project_response.json()["id"]

    # 创建运行
    run_response = client.post(
        "/api/run",
        json={"project_id": project_id, "notes": "ZIP测试运行"},
        headers={"Authorization": f"Bearer {normal_user_token}"}
    )
    run_id = run_response.json()["id"]

    # 下载运行结果ZIP
    download_response = client.get(
        f"/api/run/{run_id}/files",
        headers={"Authorization": f"Bearer {normal_user_token}"}
    )

    # 验证响应
    assert "filename*=utf-8''" in download_response.headers["content-disposition"]
    assert ".zip" in download_response.headers["content-disposition"]
    assert "attachment;" in download_response.headers["content-disposition"]

    # 清理
    import os
    os.unlink(temp_file.name)

@pytest.mark.asyncio
@patch('utils.files.create_zip_archive')
async def test_download_nonexistent_run_results_zip(mock_create_zip, client, normal_user_token):
    """测试下载不存在的运行结果ZIP"""
    # 尝试下载不存在的运行结果ZIP
    download_response = client.get(
        "/api/run/99999/files",
        headers={"Authorization": f"Bearer {normal_user_token}"}
    )

    # 验证响应
    assert download_response.status_code == 404

    # 确保mock没有被调用
    mock_create_zip.assert_not_called()

@pytest.mark.asyncio
@patch('utils.files.create_zip_archive')
async def test_unauthorized_run_results_zip_download(
        mock_create_zip, client, normal_user_token, admin_user_token, setup_run_dirs
):
    """测试未授权下载运行结果ZIP"""
    # 管理员创建项目
    admin_project = client.post(
        "/api/project?name=管理员ZIP项目&veins_config_name=AdminConfig",
        files=[('files', ('admin.txt', BytesIO(b'admin content'), 'text/plain'))],
        headers={"Authorization": f"Bearer {admin_user_token}"}
    ).json()

    # 管理员创建运行
    admin_run = client.post(
        "/api/run",
        json={"project_id": admin_project["id"], "notes": "管理员运行"},
        headers={"Authorization": f"Bearer {admin_user_token}"}
    ).json()

    # 普通用户尝试下载管理员运行结果ZIP
    download_response = client.get(
        f"/api/run/{admin_run['id']}/files",
        headers={"Authorization": f"Bearer {normal_user_token}"}
    )

    # 验证响应 (应该是404，因为获取Run时已经检查了权限)
    assert download_response.status_code == 404

    # 确保mock没有被调用
    mock_create_zip.assert_not_called()
