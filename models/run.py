import os as sync_os
from datetime import datetime
from enum import Enum
from typing import override, Any, Union

import aioshutil
from aiofiles import os
from celery.result import AsyncResult
from fastapi import HTTPException
from sqlmodel import Field, SQLModel, Relationship
from sqlmodel.ext.asyncio.session import AsyncSession

from config import config
from utils.files import list_result_files
from worker.worker import celery_app
from .project import Project
from .table_base import TableBase


class RunStatus(str, Enum):
    PENDING = "pending"
    STARTING = "starting"
    RUNNING = "running"
    SUCCESS = "success"
    FAILED = "failed"
    CANCELLING = "cancelling"
    CANCELLED = "cancelled"


class RunBase(SQLModel):
    notes: str | None = Field(default=None, max_length=500, description="User notes for this run")
    project_id: int = Field(foreign_key="project.id", index=True, ondelete="CASCADE")


class Run(RunBase, TableBase, table=True):
    id: int | None = Field(default=None, primary_key=True)

    status: RunStatus = Field(
        default=RunStatus.PENDING,
        description="仿真运行状态"
    )
    task_id: str | None = Field(default=None)

    start_time: datetime | None = None
    end_time: datetime | None = None

    project: Project = Relationship(back_populates="runs")

    def __init__(self, **data: Any):
        self._dir = None
        super().__init__(**data)

    @classmethod
    @override
    async def get_exist_one(
            cls: "Run",
            session: AsyncSession,
            id: int,
            user_id: int | None = None,
            load: Union[Relationship, None] = None
    ) -> "Run":
        """此方法和 await session.get(cls, 主键)的区别就是当不存在时不返回None，
        而是会抛出fastapi 404 异常。如果指定了user_id，会检查权限"""

        if user_id is None:
            # 不检查用户权限的情况
            instance = await super().get(session, Run.id == id, load=load)
            if not instance:
                raise HTTPException(status_code=404, detail="Not found")
            return instance

        # 需要检查用户权限的情况，使用联表查询
        instance = await cls.get(
            session,
            (cls.id == id) & (Project.user_id == user_id),
            join=(Project, cls.project_id == Project.id),
            load=load,
        )

        if not instance:
            raise HTTPException(status_code=404, detail="Not found")

        return instance

    @property
    def dir(self) -> str:
        """获取run结果目录"""
        if not self.__dict__.get('_dir'):
            #todo 似乎与pydantic冲突 不知道有没有更优雅的解法
            path = sync_os.path.join(
                self.project.dir,
                config.runs_base_dir_name_in_project,
                str(self.id)
            )
            self._dir = sync_os.path.normpath(path)
        return self._dir

    async def _prepare_execution(self) -> None:
        """准备执行环境"""
        # 确保目录存在
        await os.makedirs(sync_os.path.dirname(self.dir), exist_ok=True)

        # 清除旧的run文件夹和项目results目录
        results_dir = sync_os.path.join(self.project.dir, "results")

        if await os.path.exists(self.dir):
            await aioshutil.rmtree(self.dir)
        if await os.path.exists(results_dir):
            await aioshutil.rmtree(results_dir)

        # 创建新的run目录
        await os.makedirs(self.dir, exist_ok=True)

    async def execute(self, session) -> 'Run':
        """
        执行仿真，返回更新后的Run对象，要求self是已经载入Run.project的Run对象。
        （run = await Run.get(session, ..., load=Run.project)）
        """
        # 检查是否已有任务正在执行
        if self.task_id:
            return self

        # 准备环境
        await self._prepare_execution()

        # 启动Celery任务
        task = celery_app.send_task(
            'veins_simulation.run',
            args=[
                self.project.dir,
                self.dir,
                self.project.veins_config_name
            ]
        )

        # 更新状态
        self.status = RunStatus.STARTING
        self.start_time = datetime.now()
        self.end_time = None
        self.task_id = task.id
        await self.save(session)

        return self

    async def get_status(self, session) -> 'Run':
        """从Celery更新运行状态"""
        await session.refresh(self)
        if not self.task_id or self.status in [RunStatus.SUCCESS, RunStatus.FAILED, RunStatus.CANCELLED]:
            return self

        # 获取任务状态
        task_result = AsyncResult(self.task_id)

        # 根据Celery状态更新Run状态
        if task_result.state == 'PENDING':
            self.status = RunStatus.STARTING
        elif task_result.state == 'STARTED':
            self.status = RunStatus.RUNNING
        elif task_result.state in ('SUCCESS', 'FAILURE', 'REVOKED'):
            # 设置对应状态
            status_mapping = {
                'SUCCESS': RunStatus.SUCCESS,
                'REVOKED': RunStatus.CANCELLED,
                'FAILURE': RunStatus.FAILED
            }
            self.status = status_mapping[task_result.state]

            # 统一处理结束时间
            if not self.end_time:
                try:
                    # 尝试从结果中获取时间
                    timeout = 0.5 if task_result.state != 'SUCCESS' else None
                    result = task_result.get(timeout=timeout, propagate=False)

                    if isinstance(result, dict) and 'time' in result:
                        self.end_time = datetime.fromisoformat(result['time'])
                    else:
                        self.end_time = datetime.now()
                except Exception:
                    self.end_time = datetime.now()

        # 保存状态
        await self.save(session)
        return self

    async def cancel(self, session) -> 'Run':
        """
        取消运行中的仿真
        """
        if not self.task_id:
            return self

        if self.status not in [RunStatus.STARTING, RunStatus.RUNNING]:
            raise RuntimeError(f"项目的状态是{self.status}，取消操作无效")

        # 先更新状态为取消中
        self.status = RunStatus.CANCELLING
        await self.save(session)

        # 发送取消指令
        celery_app.control.revoke(self.task_id, terminate=True, signal='SIGTERM')

        return self


class RunCreateRequest(RunBase):
    pass


class RunInfoResponse(RunBase):
    id: int
    status: RunStatus
    start_time: datetime | None
    end_time: datetime | None

    files: set[tuple[str, int]]
    """格式: path, size"""


    created_at: datetime
    updated_at: datetime

    @classmethod
    async def __from_run(cls, run: Run) -> "RunInfoResponse":
        await os.makedirs(run.dir, exist_ok=True)
        return cls.model_validate(run, update={"files": await list_result_files(run.dir)})

    @classmethod
    async def from_run(cls, run: Run | list[Run]) -> Union["RunInfoResponse", list["RunInfoResponse"]]:
        """为run添加文件列表和大小信息"""
        #todo 这部分的逻辑换成mixin，使得逻辑在run和project共用
        if isinstance(run, list):
            res = []
            for item in run:
                res.append(await cls.__from_run(item))
            return res
        return await cls.__from_run(run)
