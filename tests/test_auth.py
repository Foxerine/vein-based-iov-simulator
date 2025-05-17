import pytest
from fastapi import status
from httpx import AsyncClient

from models import User
from utils.auth import verify_password

@pytest.mark.asyncio
async def test_register(client, session):
    """测试用户注册端点"""
    response = client.post(
        "/api/auth/register",
        json={"email": "newuser@example.com", "password": "securepass"}
    )

    assert response.status_code == status.HTTP_200_OK
    data = response.json()
    assert "access_token" in data
    assert data["token_type"] == "bearer"

    # 验证用户是否实际创建到数据库
    db_user = await User.get(session, User.email == "newuser@example.com")
    assert db_user is not None
    assert db_user.email == "newuser@example.com"
    assert verify_password("securepass", db_user.hashed_password)

@pytest.mark.asyncio
async def test_register_duplicate_email(client, normal_user):
    """测试使用已存在的邮箱注册"""
    response = client.post(
        "/api/auth/register",
        json={"email": "normal@example.com", "password": "newpassword"}
    )

    assert response.status_code == status.HTTP_400_BAD_REQUEST
    assert "该邮箱已被注册" in response.json()["detail"]

@pytest.mark.asyncio
async def test_login_success(client, normal_user):
    """测试成功登录"""
    response = client.post(
        "/api/auth/login",
        json={"email": "normal@example.com", "password": "password123"}
    )

    assert response.status_code == status.HTTP_200_OK
    data = response.json()
    assert "access_token" in data
    assert data["token_type"] == "bearer"

@pytest.mark.asyncio
async def test_login_wrong_password(client, normal_user):
    """测试错误密码登录"""
    response = client.post(
        "/api/auth/login",
        json={"email": "normal@example.com", "password": "wrongpassword"}
    )

    assert response.status_code == status.HTTP_401_UNAUTHORIZED
    assert "邮箱或密码不正确" in response.json()["detail"]

@pytest.mark.asyncio
async def test_login_nonexistent_user(client):
    """测试不存在的用户登录"""
    response = client.post(
        "/api/auth/login",
        json={"email": "nonexistent@example.com", "password": "anypassword"}
    )

    assert response.status_code == status.HTTP_401_UNAUTHORIZED
    assert "邮箱或密码不正确" in response.json()["detail"]
