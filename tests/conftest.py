import asyncio

import pytest
import pytest_asyncio
from fastapi.testclient import TestClient
from sqlalchemy.ext.asyncio import create_async_engine
from sqlalchemy.orm import sessionmaker
from sqlmodel import SQLModel
from sqlmodel.ext.asyncio.session import AsyncSession
from starlette.routing import _DefaultLifespan

from models import get_session
from models.user import User
from utils.auth import get_password_hash, create_access_token

# 使用内存数据库
TEST_DATABASE_URL = "sqlite+aiosqlite:///:memory:"

@pytest.fixture(scope="session")
def event_loop():
    """创建一个事件循环供所有测试使用"""
    loop = asyncio.get_event_loop_policy().new_event_loop()
    yield loop
    loop.close()

@pytest_asyncio.fixture(scope="function")  # 修改为function作用域，每个测试都创建新引擎
async def engine():
    """创建测试引擎"""
    test_engine = create_async_engine(
        TEST_DATABASE_URL,
        echo=False,
        poolclass=None,
        connect_args={"check_same_thread": False},
        future=True,
    )

    # 创建所有表
    async with test_engine.begin() as conn:
        await conn.run_sync(SQLModel.metadata.create_all)

    yield test_engine

    # 清理表
    async with test_engine.begin() as conn:
        await conn.run_sync(SQLModel.metadata.drop_all)

    await test_engine.dispose()

@pytest_asyncio.fixture
async def session(engine):
    """提供测试用会话"""
    async_session_factory = sessionmaker(engine, class_=AsyncSession)

    async with async_session_factory() as session:
        yield session
        # 确保测试后清理数据，先回滚未提交的内容
        await session.rollback()

@pytest_asyncio.fixture
async def normal_user(session):
    """创建普通测试用户"""
    user = User(
        email="normal@example.com",
        hashed_password=get_password_hash("password123"),
        is_active=True,
        is_admin=False
    )
    return await user.save(session)

@pytest_asyncio.fixture
async def admin_user(session):
    """创建管理员测试用户"""
    admin = User(
        email="admin@example.com",
        hashed_password=get_password_hash("adminpass"),
        is_active=True,
        is_admin=True
    )
    return await admin.save(session)

@pytest_asyncio.fixture
async def inactive_user(session):
    """创建非激活测试用户"""
    user = User(
        email="inactive@example.com",
        hashed_password=get_password_hash("password123"),
        is_active=False,
        is_admin=False
    )
    return await user.save(session)

@pytest.fixture
def normal_user_token(normal_user):
    """为普通用户创建有效的token"""
    return create_access_token(data={"sub": normal_user.email})

@pytest.fixture
def admin_user_token(admin_user):
    """为管理员用户创建有效的token"""
    return create_access_token(data={"sub": admin_user.email})

@pytest.fixture
def inactive_user_token(inactive_user):
    """为非激活用户创建有效的token"""
    return create_access_token(data={"sub": inactive_user.email})

@pytest.fixture
def client(session):
    """创建FastAPI测试客户端，并覆盖数据库会话依赖"""
    from main import app

    app.router.lifespan_context = _DefaultLifespan(app.router)

    # 创建一个返回测试会话的依赖替代函数
    async def get_test_session():
        yield session

    app.dependency_overrides[get_session] = get_test_session

    # 创建测试客户端
    with TestClient(app) as test_client:
        yield test_client
