from typing import Annotated, Literal

import jwt
from fastapi import Depends, HTTPException, status, Query
from loguru import logger
from sqlmodel.ext.asyncio.session import AsyncSession

from config import config
from models import get_session
from models import User
from models.project import ProjectCreateRequest, ProjectUpdateRequest
from models.others import TableViewRequest
from utils.auth import oauth2_scheme

SessionDep = Annotated[AsyncSession, Depends(get_session)]

# --- Users ---

async def get_current_user(
        session: SessionDep,
        token: Annotated[str, Depends(oauth2_scheme)],
) -> User:
    """
    获取当前用户

    Args:
        :param session:
        :param token:

    Returns:
        当前认证的用户

    Raises:
        HTTPException: 认证失败时抛出401异常
    """
    credentials_exception = HTTPException(
        status_code=status.HTTP_401_UNAUTHORIZED,
        detail="认证凭据无效",
        headers={"WWW-Authenticate": "Bearer"},
    )

    try:
        payload = jwt.decode(token, config.jwt_secret, algorithms=[config.jwt_algorithm])
        email = payload.get("sub", None)
        if email is None:
            raise credentials_exception
    except jwt.PyJWTError:
        raise credentials_exception

    try:
        user = await User.get(session, User.email == email)
        if user is None:
            raise credentials_exception
        return user
    except Exception as e:
        logger.exception(e)
        raise credentials_exception

CurrentUserDep = Annotated[User, Depends(get_current_user)]

async def get_current_active_user(current_user: CurrentUserDep) -> User:
    """
    获取当前激活状态的用户

    Args:
        current_user: 当前认证的用户

    Returns:
        当前激活状态的用户

    Raises:
        HTTPException: 用户未激活时抛出401异常
    """
    if not current_user.is_active:
        raise HTTPException(status_code=401, detail="用户未激活")
    return current_user

CurrentActiveUserDep = Annotated[User, Depends(get_current_active_user)]

async def get_admin_user(current_user: CurrentActiveUserDep) -> User:
    """
    获取管理员用户

    Args:
        current_user: 当前激活状态的用户

    Returns:
        当前管理员用户

    Raises:
        HTTPException: 用户不是管理员时抛出403异常
    """
    if not current_user.is_admin:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="权限不足，需要管理员权限"
        )
    return current_user

AdminUserDep = Depends(get_admin_user)
AdminUserDepAnnotated = Annotated[User, AdminUserDep]

# --- Projects ---

def get_project_create_request(
        name: Annotated[str, Query(min_length=1, max_length=255)],
        veins_config_name: Annotated[str, Query(min_length=1, max_length=100)],
        description: Annotated[str| None, Query(max_length=1000)] = None,
) -> ProjectCreateRequest:
    """
    从查询参数创建项目创建请求

    Args:
        name: 项目名称
        description: 项目描述（可选）
        veins_config_name: Veins配置名称

    Returns:
        ProjectCreateRequest实例
    """
    return ProjectCreateRequest(
        name=name,
        description=description,
        veins_config_name=veins_config_name
    )

ProjectCreateRequestDep = Annotated[ProjectCreateRequest, Depends(get_project_create_request)]

def get_project_update_request(
        name: Annotated[str | None, Query(min_length=1, max_length=255)] = None,
        veins_config_name: Annotated[str | None, Query(min_length=1, max_length=100)] = None,
        description: Annotated[str | None, Query(max_length=1000)] = None,
) -> ProjectUpdateRequest:
    """
    从查询参数创建项目更新请求

    Args:
        name: 项目名称（可选）
        description: 项目描述（可选）
        veins_config_name: Veins配置名称（可选）

    Returns:
        ProjectUpdateRequest实例
    """
    data = {}
    if name is not None:
        data["name"] = name
    if description is not None:
        data["description"] = description
    if veins_config_name is not None:
        data["veins_config_name"] = veins_config_name
    return ProjectUpdateRequest(**data)

ProjectUpdateRequestDep = Annotated[ProjectUpdateRequest, Depends(get_project_update_request)]

# --- Others ---
def get_table_view_queries(
        offset: Annotated[int | None, Query()] = 0,
        limit: Annotated[int | None, Query(le=100)] = config.max_allowed_table_view_limit,
        desc: bool | None = True,
        order: Literal["created_at", "updated_at"] | None = "created_at",
):
    return TableViewRequest(offset=offset, limit=limit, desc=desc, order=order)

TableViewRequestDep = Annotated[TableViewRequest, Depends(get_table_view_queries)]
