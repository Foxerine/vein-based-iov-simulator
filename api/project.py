from typing import Literal
import os as sync_os

from fastapi import APIRouter, HTTPException, status
from starlette.background import BackgroundTask

from config import config
from models.project import (
    Project, ProjectInfoResponse, ProjectFileType
)
from models.run import RunInfoResponse, Run
from utils.depends import CurrentActiveUserDep, SessionDep, ProjectCreateRequestDep, ProjectUpdateRequestDep, \
    TableViewRequestDep

from utils.files import ensure_file_path_valid, create_zip_archive
from fastapi.responses import FileResponse

router = APIRouter(prefix="/project", tags=["项目"])

@router.post("", response_model=ProjectInfoResponse)
async def create_project(
        session: SessionDep,
        project_data: ProjectCreateRequestDep,
        current_user: CurrentActiveUserDep,
        files: ProjectFileType,
):
    if not files or len(files) == 0:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="必须上传至少一个文件"
        )

    project = Project.model_validate(project_data, update={"user_id": current_user.id})

    try:
        await project.save(session, files=files)
    except OSError:
        await Project.delete(session, project)
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"文件上传保存失败"
        )

    return await ProjectInfoResponse.from_project(project)

@router.get("", response_model=list[ProjectInfoResponse])
async def list_projects(
        session: SessionDep,
        current_user: CurrentActiveUserDep,
        table_view_args: TableViewRequestDep
):
    """获取当前用户的项目列表"""
    projects = await Project.get(
        session,
        Project.user_id == current_user.id,
        offset=table_view_args.offset,
        limit=table_view_args.limit,
        fetch_mode="all",
        order_by=[table_view_args.clause(Project)]
    )

    # 为每个项目添加文件列表和大小信息
    return await ProjectInfoResponse.from_project(projects)

@router.get("/{id}", response_model=ProjectInfoResponse)
async def get_project(
        id: int,
        session: SessionDep,
        current_user: CurrentActiveUserDep
):
    """获取特定项目的详情"""
    project = await Project.get_exist_one(session=session, id=id, user_id=current_user.id)

    return await ProjectInfoResponse.from_project(project)

@router.patch("/{id}", response_model=ProjectInfoResponse)
async def update_project(
        id: int,
        update_data: ProjectUpdateRequestDep,
        session: SessionDep,
        current_user: CurrentActiveUserDep,
        files: ProjectFileType | None = None,
):
    """更新项目"""
    project = await Project.get_exist_one(session=session, id=id, user_id=current_user.id)
    await project.update(session, update_data, files=files)

    return await ProjectInfoResponse.from_project(project)

@router.delete("/{id}", response_model=Literal[True])
async def delete_project(
        id: int,
        session: SessionDep,
        current_user: CurrentActiveUserDep
):
    """删除项目"""
    project = await Project.get_exist_one(session=session, id=id, user_id=current_user.id)
    await Project.delete(session, project)

    return True

@router.delete("/{id}/files/{file_name}", response_model=ProjectInfoResponse)
async def delete_file(
        id: int,
        file_name: str,
        session: SessionDep,
        current_user: CurrentActiveUserDep
):
    """删除项目中的文件"""
    project = await Project.get_exist_one(session=session, id=id, user_id=current_user.id)
    try:
        await project.remove_one_file(file_name)

    except ValueError as e:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=str(e)
        )
    except OSError:
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="服务器故障"
        )

    return await ProjectInfoResponse.from_project(project)

@router.get("/{project_id}/runs", response_model=list[RunInfoResponse])
async def list_runs(
        project_id: int,
        session: SessionDep,
        current_user: CurrentActiveUserDep,
        table_view_args: TableViewRequestDep
):
    """获取项目的所有仿真运行"""
    # 获取项目
    project = await Project.get_exist_one(session, project_id, user_id=current_user.id)

    # 获取仿真列表
    runs = await Run.get(
        session,
        Run.project_id == project_id,
        offset=table_view_args.offset,
        limit=table_view_args.limit,
        fetch_mode="all",
        order_by=[table_view_args.clause(Run)],
        load=Run.project
    )

    # 更新每个Run的状态
    for run in runs:
        await run.get_status(session)

    runs = await Run.get(
        session,
        Run.project_id == project_id,
        offset=table_view_args.offset,
        limit=table_view_args.limit,
        fetch_mode="all",
        order_by=[table_view_args.clause(Run)],
        load=Run.project
    )

    return await RunInfoResponse.from_run(runs)

@router.get("/{id}/files/{file_name}", response_class=FileResponse)
async def download_file(
        id: int,
        file_name: str,
        session: SessionDep,
        current_user: CurrentActiveUserDep
):
    """下载项目中的文件"""
    # 获取项目并验证权限
    project = await Project.get_exist_one(session=session, id=id, user_id=current_user.id)

    # 验证并获取文件路径
    file_path = await ensure_file_path_valid(project.dir, file_name)

    # 返回文件响应
    return FileResponse(path=file_path, filename=file_name)

@router.get("/{id}/files", response_class=FileResponse)
async def download_project_zip(
        id: int,
        session: SessionDep,
        current_user: CurrentActiveUserDep
):
    """将项目文件打包成zip下载"""
    # 获取项目并验证权限
    project = await Project.get_exist_one(session=session, id=id, user_id=current_user.id)

    # 创建zip文件
    try:
        # 排除runs目录
        zip_path, filename = await create_zip_archive(
            project.dir,
            # excludes=[config.runs_base_dir_name_in_project]
        )

        return FileResponse(
            path=zip_path,
            filename=f"项目_{project.name}.zip",
            media_type="application/zip",
            background=BackgroundTask(lambda: sync_os.unlink(zip_path))  # 下载完成后删除临时文件
        )
    except Exception as e:
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"创建ZIP文件失败: {str(e)}"
        )
