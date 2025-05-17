import os as sync_os
from datetime import datetime
from typing import Union, List, Any

import aioshutil
from aiofiles import os, open
from fastapi import HTTPException, UploadFile
from loguru import logger
from sqlmodel import Field, SQLModel, Relationship
from sqlmodel.ext.asyncio.session import AsyncSession
from typing_extensions import override

from utils.files import list_result_files, ensure_file_path_valid
from config import config
from .table_base import TableBase, M
from .user import User

from typing import TYPE_CHECKING
if TYPE_CHECKING:
    from .run import Run

class ProjectBase(SQLModel):
    name: str = Field(index=True, min_length=1, max_length=255)
    description: str | None = Field(default=None, max_length=1000)
    veins_config_name: str = Field(
        default="Default",
        min_length=1,
        max_length=100,
        description="Veins omnetpp.ini中的配置名"
    )

ProjectFileType = list[UploadFile] | UploadFile

class Project(ProjectBase, TableBase, table=True):
    """用户上传的veins项目文件的存储路径，默认在项目/user_projects/{user_id}/{project_id}/"""
    id: int | None = Field(default=None, primary_key=True)

    user_id: int = Field(foreign_key="user.id", index=True, ondelete="CASCADE")
    owner: User | None = Relationship(back_populates="projects")
    # 查询项目时注意避免产生无限循环
    # （比如：selectinload项目，然后由于定义自动selectinload用户，然后selectinload项目，导致无限循环）

    runs: list["Run"] = Relationship(back_populates="project", cascade_delete=True)

    @property
    def dir(self) -> str:
        """获取项目文件夹路径"""
        if not self.__dict__.get('_dir'):
            #todo 似乎与pydantic冲突 不知道有没有更优雅的解法
            path = sync_os.path.join(config.user_projects_base_dir, str(self.user_id), str(self.id))
            self._dir = sync_os.path.normpath(path)
        return self._dir

    async def remove_all_files(self):
        if await os.path.exists(self.dir):
            await aioshutil.rmtree(self.dir)

    async def remove_one_file(self, file_name: str):
        path = await ensure_file_path_valid(self.dir, file_name)

        if not await os.path.isfile(path):
            await aioshutil.rmtree(self.dir)
            return

        await os.remove(path)

    async def __save_files(self, files: ProjectFileType):
        if files is None:
            raise ValueError("没有提供文件")

        try:
            await os.makedirs(self.dir, exist_ok=True)
            if isinstance(files, list):
                for file in files:
                    async with open(sync_os.path.join(self.dir, file.filename), "wb") as f:
                        await f.write(await file.read())
            else:
                async with open(sync_os.path.join(self.dir, files.filename), "wb") as f:
                    await f.write(await files.read())
        except Exception as e:
            logger.exception(e)
            raise OSError(str(e))

    def __init__(self, **data: Any):
        self._dir = None
        super().__init__(**data)

    @override
    async def update(
            self: "Project",
            session: AsyncSession,
            other: M,
            extra_data: dict = None,
            exclude_unset: bool = True,
            files: ProjectFileType | None = None
    ) -> "Project":
        """
        更新记录
        :param session: 数据库会话
        :param other:
        :param extra_data:
        :param exclude_unset:
        :param files: 文件
        """
        await super().update(session, other, extra_data, exclude_unset)
        if files:
            await self.__save_files(files)

        return self

    @classmethod
    @override
    async def delete(cls: "Project", session: AsyncSession, instances: "Project" | List["Project"]) -> None:
        """
        删除一些记录
        :param session: 数据库会话
        :param instances:
        :return: None
        会一并删除项目文件

        usage:
        item1 = Item.get(...)
        item2 = Item.get(...)

        Item.delete(session, [item1, item2])

        """
        if isinstance(instances, list):
            for instance in instances:
                await session.delete(instance)
                await instance.remove_all_files()
        else:
            await session.delete(instances)
            await instances.remove_all_files()

        await session.commit()
        return

    @override
    async def save(self, session: AsyncSession, files: ProjectFileType | None = None) -> "Project":
        await super().save(session)
        if files:
            await self.__save_files(files)

        return self

    @classmethod
    @override
    async def get_exist_one(cls: "Project", session: AsyncSession, id: int, user_id: int | None = None) -> "Project":
        """此方法和 await session.get(cls, 主键)的区别就是当不存在时不返回None，
        而是会抛出fastapi 404 异常"""
        if not user_id:
            instance = await session.get(cls, id)
        else:
            instance = await cls.get(
                session,
                (Project.id == id) & (Project.user_id == user_id)
            )
        if not instance:
            raise HTTPException(status_code=404, detail="Not found")
        return instance

class ProjectCreateRequest(ProjectBase):
    pass

class ProjectUpdateRequest(ProjectBase):
    name: str | None = Field(default=None, min_length=1, max_length=255)
    veins_config_name: str | None = Field(
        default=None,
        min_length=1,
        max_length=100,
        description="Veins omnetpp.ini中的配置名"
    )

class ProjectInfoResponse(ProjectBase):
    id: int
    user_id: int
    files: set[tuple[str, int]]
    """格式: path, size。文件在fastapi端点上传到文件夹里，这里只需要当场读取然后传回文件夹里的所有的文件名"""

    created_at: datetime
    updated_at: datetime

    @classmethod
    async def __from_project(cls, project: Project) -> "ProjectInfoResponse":
        await os.makedirs(project.dir, exist_ok=True)
        return cls.model_validate(project, update={"files": await list_result_files(project.dir)})

    @classmethod
    async def from_project(cls, project: Project | list[Project]) -> Union["ProjectInfoResponse", list["ProjectInfoResponse"]]:
        """为项目添加文件列表和大小信息"""
        #todo 这部分的逻辑换成mixin，使得逻辑在run和project共用
        if isinstance(project, list):
            res = []
            for item in project:
                res.append(await cls.__from_project(item))
            return res
        return await cls.__from_project(project)

