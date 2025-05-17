import pytest
from fastapi import status

from models import User
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
