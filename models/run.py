import os as sync_os
from datetime import datetime
from enum import Enum
from typing import override, Any, Union

import aioshutil
from aiofiles import os
from celery.result import AsyncResult
from fastapi import HTTPException
from sqlalchemy import exc
from sqlmodel import Field, SQLModel, Relationship
from sqlmodel.ext.asyncio.session import AsyncSession

from config import config
from utils.files import list_result_files
from utils.auth import generate_vnc_uuid
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
    use_gui: bool = Field(default=False)

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
        self._uuid = None
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

    async def uuid(self) -> str:
        """获取run uuid"""
        if not self.__dict__.get('_uuid'):
            #todo 似乎与pydantic冲突 不知道有没有更优雅的解法
            self._uuid = generate_vnc_uuid(self.project.user_id, self.project.id, self.id)
        return self._uuid

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

        # 准备任务参数
        task_args = [
            self.project.user_id,  # user_id
            self.project.id,       # project_id
            self.id,               # run_id
            self.project.dir,      # project_dir
            self.dir,              # run_dir
            self.project.veins_config_name  # config_name
        ]

        # GUI模式需要传入UUID
        if self.use_gui:
            vnc_uuid = await self.uuid()
            task_args.extend([True, vnc_uuid])  # gui_mode=True, vnc_uuid
        else:
            task_args.append(False)  # gui_mode=False

        # 启动Celery任务
        task = celery_app.send_task(
            'veins_simulation.run',
            args=task_args
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
        task_result = AsyncResult(self.task_id, app=celery_app)

        # 根据Celery状态更新Run状态
        if task_result.state == 'PENDING':
            self.status = RunStatus.PENDING
        elif task_result.state == 'PROGRESS':
            # 从任务meta中获取详细状态
            try:
                meta = task_result.info
                if isinstance(meta, dict) and 'status' in meta:
                    self.status = RunStatus(meta['status'])
                else:
                    self.status = RunStatus.RUNNING
            except Exception:
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
                self.end_time = datetime.now()

        # 保存状态
        load = None
        try:
            self.project
        except exc.MissingGreenlet:
            load = Run.project
            # todo 测试为什么这么写不正确
        return await self.save(session, load=load)

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

        # 发送停止任务
        stop_task = celery_app.send_task(
            'veins_simulation.stop',
            args=[self.task_id]
        )

        # 等待停止任务完成（可选，或者异步处理）
        try:
            stop_result = stop_task.get(timeout=30)
            if isinstance(stop_result, dict) and stop_result.get('status') == RunStatus.CANCELLED:
                self.status = RunStatus.CANCELLED
                self.end_time = datetime.now()
                await self.save(session)
        except Exception as e:
            # 如果停止任务失败，仍然标记为已取消
            self.status = RunStatus.CANCELLED
            self.end_time = datetime.now()
            await self.save(session)

        return self

    async def get_vnc_url(self) -> str | None:
        """获取VNC访问URL（仅GUI模式）"""
        if not self.use_gui or not self.task_id:
            return None

        if self.status not in [RunStatus.RUNNING]:
            return None

        try:
            # 从任务结果中获取VNC URL
            task_result = AsyncResult(self.task_id, app=celery_app)
            if task_result.state == 'PROGRESS':
                meta = task_result.info
                if isinstance(meta, dict):
                    return meta.get('vnc_url')
            elif task_result.state == 'SUCCESS':
                result = task_result.result
                if isinstance(result, dict):
                    return result.get('vnc_url')
        except Exception:
            pass

        return None


class RunCreateRequest(RunBase):
    pass


class RunInfoResponse(RunBase):
    id: int
    status: RunStatus
    start_time: datetime | None
    end_time: datetime | None

    vnc_url: str | None = None

    files: set[tuple[str, int]]
    """格式: path, size"""

    created_at: datetime
    updated_at: datetime

    @classmethod
    async def __from_run(cls, run: Run) -> "RunInfoResponse":
        await os.makedirs(run.dir, exist_ok=True)

        # 获取VNC URL（如果是GUI模式）
        vnc_url = None
        if run.use_gui:
            vnc_url = await run.get_vnc_url()

        return cls.model_validate(
            run,
            update={
                "files": await list_result_files(run.dir),
                "vnc_url": vnc_url
            }
        )

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
