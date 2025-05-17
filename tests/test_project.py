import os
import shutil
from io import BytesIO

import pytest

from config import config


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
async def test_create_project(session, client, normal_user, normal_user_token, test_file, setup_project_dir):
    """测试创建项目"""
    # 准备文件
    files = [test_file]

    # 发送请求
    response = client.post(
        "/api/project?name=测试项目&description=测试描述&veins_config_name=Default",
        files=files,
        headers={"Authorization": f"Bearer {normal_user_token}"}
    )

    # 验证响应
    assert response.status_code == 200
    assert response.json()["name"] == "测试项目"
    assert response.json()["description"] == "测试描述"
    await session.refresh(normal_user)
    assert response.json()["user_id"] == normal_user.id

    # 验证文件是否已创建
    project_id = response.json()["id"]
    project_dir = os.path.normpath(os.path.join(config.user_projects_base_dir, str(normal_user.id), str(project_id)))
    file_path = os.path.join(project_dir, "test_file.txt")
    assert os.path.exists(file_path)

    # 验证文件内容
    with open(file_path, 'rb') as f:
        content = f.read()
        assert content == b'test_file_content'

@pytest.mark.asyncio
async def test_create_project_without_files(client, normal_user_token, setup_project_dir):
    """测试创建项目但不提供文件"""
    # 发送请求，参数作为查询参数
    response = client.post(
        "/api/project?name=测试项目&description=测试描述&veins_config_name=Default",
        headers={"Authorization": f"Bearer {normal_user_token}"}
    )

    # 验证响应
    assert response.status_code == 422

@pytest.mark.asyncio
async def test_flow_create_list_get_update_delete(client, normal_user, normal_user_token, test_file, setup_project_dir):
    """测试完整的项目流程：创建、列表、获取、更新、删除"""
    # 1. 创建项目
    files = [test_file]
    response = client.post(
        "/api/project?name=原始项目名&description=原始描述&veins_config_name=Default",
        files=files,
        headers={"Authorization": f"Bearer {normal_user_token}"}
    )
    assert response.status_code == 200
    project_id = response.json()["id"]

    # 2. 获取项目列表
    response = client.get(
        "/api/project",
        headers={"Authorization": f"Bearer {normal_user_token}"}
    )
    assert response.status_code == 200
    projects = response.json()
    assert isinstance(projects, list)
    assert len(projects) >= 1
    assert any(p["id"] == project_id for p in projects)

    # 3. 获取单个项目
    response = client.get(
        f"/api/project/{project_id}",
        headers={"Authorization": f"Bearer {normal_user_token}"}
    )
    assert response.status_code == 200
    assert response.json()["id"] == project_id
    assert response.json()["name"] == "原始项目名"

    # 4. 更新项目
    response = client.patch(
        f"/api/project/{project_id}?name=更新项目名&description=更新描述&veins_config_name=NewConfig",
        headers={"Authorization": f"Bearer {normal_user_token}"}
    )
    assert response.status_code == 200
    assert response.json()["name"] == "更新项目名"
    assert response.json()["description"] == "更新描述"
    assert response.json()["veins_config_name"] == "NewConfig"

    # 5. 删除文件
    response = client.delete(
        f"/api/project/{project_id}/files/test_file.txt",
        headers={"Authorization": f"Bearer {normal_user_token}"}
    )
    assert response.status_code == 200

    # 验证文件是否已删除
    project_dir = os.path.normpath(os.path.join(config.user_projects_base_dir, str(normal_user.id), str(project_id)))
    file_path = os.path.join(project_dir, "test_file.txt")
    assert not os.path.exists(file_path)

    # 6. 尝试删除不存在的文件
    response = client.delete(
        f"/api/project/{project_id}/files/nonexistent_file.txt",
        headers={"Authorization": f"Bearer {normal_user_token}"}
    )
    assert response.status_code == 404
    assert "文件不存在" in response.json()["detail"]

    # 7. 尝试删除runs目录
    response = client.delete(
        f"/api/project/{project_id}/files/{config.runs_base_dir_name_in_project}",
        headers={"Authorization": f"Bearer {normal_user_token}"}
    )
    assert response.status_code == 403
    assert "无法移除该文件夹" in response.json()["detail"]

    # 8. 删除项目
    response = client.delete(
        f"/api/project/{project_id}",
        headers={"Authorization": f"Bearer {normal_user_token}"}
    )
    assert response.status_code == 200
    assert response.json() is True

    # 验证项目目录是否已删除
    assert not os.path.exists(project_dir)

@pytest.mark.asyncio
async def test_get_nonexistent_project(client, normal_user_token):
    """测试获取不存在的项目"""
    # 使用一个非常大的ID，确保项目不存在
    response = client.get(
        "/api/project/99999",
        headers={"Authorization": f"Bearer {normal_user_token}"}
    )

    # 验证响应
    assert response.status_code == 404

@pytest.mark.asyncio
async def test_authentication_required(client):
    """测试未认证的访问应被拒绝"""
    # 尝试无token访问
    endpoints = [
        ("/api/project", "GET"),
        ("/api/project", "POST"),
        ("/api/project/1", "GET"),
        ("/api/project/1", "PATCH"),
        ("/api/project/1", "DELETE"),
        ("/api/project/1/files/test.txt", "DELETE")
    ]

    for endpoint, method in endpoints:
        if method == "GET":
            response = client.get(endpoint)
        elif method == "POST":
            response = client.post(endpoint)
        elif method == "PATCH":
            response = client.patch(endpoint)
        elif method == "DELETE":
            response = client.delete(endpoint)

        assert response.status_code in (401, 403), f"{method} {endpoint} 应该要求认证"
