import pytest
from fastapi import status

from models import User
from utils.auth import verify_password

@pytest.mark.asyncio
async def test_read_user(client, normal_user, normal_user_token):
    """测试读取当前用户信息"""
    response = client.get(
        "/api/user",
        headers={"Authorization": f"Bearer {normal_user_token}"}
    )

    assert response.status_code == status.HTTP_200_OK
    data = response.json()
    assert data["email"] == normal_user.email
    assert data["id"] == normal_user.id
    assert data["is_active"] == normal_user.is_active
    assert data["is_admin"] == normal_user.is_admin

@pytest.mark.asyncio
async def test_update_user(client, session, normal_user, normal_user_token):
    """测试更新用户信息"""
    response = client.patch(
        "/api/user",
        headers={"Authorization": f"Bearer {normal_user_token}"},
        json={"email": "updated@example.com", "password": "newpassword"}
    )

    assert response.status_code == status.HTTP_200_OK
    data = response.json()
    assert data["email"] == "updated@example.com"

    # 验证数据库中的更改
    updated_user = await User.get_exist_one(session, normal_user.id)
    assert updated_user.email == "updated@example.com"
    assert verify_password("newpassword", updated_user.hashed_password)

@pytest.mark.asyncio
async def test_update_user_email_only(client, session, normal_user, normal_user_token):
    """测试只更新用户邮箱"""
    original_password = normal_user.hashed_password

    response = client.patch(
        "/api/user",
        headers={"Authorization": f"Bearer {normal_user_token}"},
        json={"email": "emailonly@example.com"}
    )

    assert response.status_code == status.HTTP_200_OK
    data = response.json()
    assert data["email"] == "emailonly@example.com"

    # 验证密码没有更改
    updated_user = await User.get_exist_one(session, normal_user.id)
    assert updated_user.hashed_password == original_password

@pytest.mark.asyncio
async def test_delete_user(client, session, normal_user, normal_user_token):
    """测试删除用户"""
    response = client.delete(
        "/api/user",
        headers={"Authorization": f"Bearer {normal_user_token}"}
    )

    assert response.status_code == status.HTTP_200_OK

    # 验证用户是否已从数据库中删除
    deleted_user = await session.get(User, normal_user.id)
    assert deleted_user is None

@pytest.mark.asyncio
async def test_unauthorized_access(client):
    """测试未授权访问"""
    response = client.get("/api/user")
    assert response.status_code == status.HTTP_401_UNAUTHORIZED

    response = client.patch(
        "/api/user",
        json={"email": "hacker@example.com"}
    )
    assert response.status_code == status.HTTP_401_UNAUTHORIZED

    response = client.delete("/api/user")
    assert response.status_code == status.HTTP_401_UNAUTHORIZED

@pytest.mark.asyncio
async def test_inactive_user_access(client, inactive_user_token):
    """测试非激活用户访问"""
    response = client.get(
        "/api/user",
        headers={"Authorization": f"Bearer {inactive_user_token}"}
    )
    assert response.status_code == status.HTTP_401_UNAUTHORIZED
