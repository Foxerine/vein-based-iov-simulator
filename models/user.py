from datetime import datetime
import os as sync_os

import aioshutil
from aiofiles import os
from sqlmodel import Field, SQLModel, Relationship

from typing import TYPE_CHECKING, override, Union, Any

from sqlmodel.ext.asyncio.session import AsyncSession

from config import config

if TYPE_CHECKING:
    from .project import Project

from .table_base import TableBase

class UserBase(SQLModel):
    """用户基本字段"""
    email: str = Field(
        unique=True,
        index=True,
        min_length=5,
        max_length=50
    )

class User(UserBase, TableBase, table=True):
    """
    数据库用户模型，
    本项目里我们定义纯名词如 `User` 代表数据库类，
    其他类依据特定的用途将会有特定的后缀
    比如 `UserLoginRequest`
    """
    id: int | None = Field(default=None, primary_key=True)

    hashed_password: str
    is_active: bool = True
    is_admin: bool = False

    projects: list["Project"] = Relationship(
        back_populates="owner",
        cascade_delete=True,
    )
    def __init__(self, **data: Any):
        self._dir = None
        super().__init__(**data)

    @property
    def dir(self) -> str:
        """获取用户文件夹路径"""
        if not self.__dict__.get('_dir'):
            #todo 似乎与pydantic冲突 不知道有没有更优雅的解法
            path = sync_os.path.join(config.user_projects_base_dir, str(self.id))
            self._dir = sync_os.path.normpath(path)
        return self._dir

    async def remove_all_files(self):
        if await os.path.exists(self.dir):
            await aioshutil.rmtree(self.dir)

    @classmethod
    @override
    async def delete(cls: "User", session: AsyncSession, instances: Union["User", list["User"]]) -> None:
        if isinstance(instances, User):
            await instances.remove_all_files()
        else:
            for instance in instances:
                await instance.delete_all_files()
        await super().delete(session, instances)

class UserLoginRequest(UserBase):
    password: str = Field(min_length=8, max_length=64)

class UserRegisterRequest(UserBase):
    password: str = Field(min_length=8, max_length=64)

class UserUpdateRequest(UserBase):
    email: str | None = Field(
        default=None,
        unique=True,
        index=True,
        min_length=5,
        max_length=50
    )
    password: str | None = Field(default=None, min_length=8, max_length=64)

class AdminUserUpdateRequest(UserBase):
    email: str | None = Field(
        default=None,
        unique=True,
        index=True,
        min_length=5,
        max_length=50
    )
    password: str | None = Field(default=None, min_length=8, max_length=64)
    is_active: bool | None = None
    is_admin: bool | None = None

class UserInfoResponse(UserBase):
    id: int
    is_active: bool
    is_admin: bool

    created_at: datetime
    updated_at: datetime
