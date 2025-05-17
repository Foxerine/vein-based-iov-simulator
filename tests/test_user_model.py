import pytest

from models import User

@pytest.mark.asyncio
async def test_user_create(session):
    """测试创建用户"""
    # 创建测试用户
    test_user = User(email="test@example.com", hashed_password="hashedpassword123", is_admin=False)

    # 使用模型中定义的add方法
    created_user = await User.add(session, test_user)

    # 检查是否成功创建并返回了ID
    assert created_user.id is not None
    assert created_user.email == "test@example.com"
    assert created_user.hashed_password == "hashedpassword123"
    assert created_user.is_admin == False
    assert created_user.is_active == True  # 默认值检查
    assert created_user.created_at is not None
    assert created_user.updated_at is not None

@pytest.mark.asyncio
async def test_user_get(session):
    """测试查询用户"""
    # 先创建一个用户
    user = User(email="get_test@example.com", hashed_password="password123")
    await user.save(session)

    # 通过邮箱查询
    retrieved_user = await User.get(session, User.email == "get_test@example.com")

    # 验证查询结果
    assert retrieved_user is not None
    assert retrieved_user.email == "get_test@example.com"

    # 测试get_exist_one方法
    existing_user = await User.get_exist_one(session, retrieved_user.id)
    assert existing_user.id == retrieved_user.id

    # 测试获取所有用户
    all_users = await User.get(session, condition=None, fetch_mode="all")
    assert len(all_users) >= 1

    # 测试分页
    limited_users = await User.get(session, condition=None, limit=1, fetch_mode="all")
    assert len(limited_users) == 1

@pytest.mark.asyncio
async def test_user_update(session):
    """测试更新用户"""
    # 创建用户
    user = User(email="update_test@example.com", hashed_password="oldpassword")
    await user.save(session)

    # 创建更新数据
    update_data = User(email="updated@example.com", hashed_password="newpassword", is_admin=True)

    # 更新用户
    updated_user = await user.update(session, update_data)

    # 验证更新结果
    assert updated_user.email == "updated@example.com"
    assert updated_user.hashed_password == "newpassword"
    assert updated_user.is_admin == True

@pytest.mark.asyncio
async def test_user_delete(session):
    """测试删除用户"""
    # 创建用户
    user = User(email="delete_test@example.com", hashed_password="password123")
    await user.save(session)

    # 获取用户ID
    user_id = user.id

    # 删除用户
    await User.delete(session, user)

    # 尝试查询已删除的用户
    deleted_user = await session.get(User, user_id)
    assert deleted_user is None
