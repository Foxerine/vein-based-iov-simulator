import os as sync_os
from typing import Literal

from aiofiles import os
from fastapi import APIRouter, HTTPException
from fastapi.responses import FileResponse
from starlette import status
from starlette.background import BackgroundTask

from api.user import delete_user
from models.project import ProjectInfoResponse, Project, ProjectFileType
from models.run import Run, RunStatus, RunInfoResponse
from models.user import User, UserInfoResponse, AdminUserUpdateRequest
from utils.auth import get_password_hash
from utils.depends import AdminUserDep, SessionDep, TableViewRequestDep, ProjectUpdateRequestDep, AdminUserDepAnnotated
from utils.files import ensure_file_path_valid, create_zip_archive

router = APIRouter(prefix="/admin", tags=["管理员"], dependencies=[AdminUserDep])

admin_user_router = APIRouter(prefix="/user", tags=["用户管理"])

@admin_user_router.get("", response_model=list[UserInfoResponse])
async def read_user_admin(session: SessionDep, table_view_args: TableViewRequestDep):
    return await User.get(
        session,
        None,
        offset=table_view_args.offset,
        limit=table_view_args.limit,
        fetch_mode="all",
        order_by=[table_view_args.clause(User)]
    )

@admin_user_router.get("/{id}", response_model=UserInfoResponse)
async def read_user_admin(session: SessionDep, id: int):
    return await User.get_exist_one(session, id)

@admin_user_router.patch("/{id}", response_model=UserInfoResponse)
async def update_user_admin(
        session: SessionDep,
        id: int,
        admin: AdminUserDepAnnotated,
        update_data: AdminUserUpdateRequest
):
    user = await User.get_exist_one(session, id)
    extra_data = {}
    update_data_dict = update_data.model_dump(exclude_unset=True)
    if password := update_data_dict.get("password"):
        extra_data["hashed_password"] = get_password_hash(password)

    if update_data_dict.get("is_active") is False or update_data_dict.get("is_admin") is False:
        if id == admin.id:
            raise HTTPException(
                status_code=400,
                detail="无法封禁自身或移除自身的权限"
            )

    return await user.update(session, update_data, extra_data)

@admin_user_router.delete("/{id}", response_model=Literal[True])
async def delete_user_admin(id: int, session: SessionDep):
    return await delete_user(await User.get_exist_one(session, id), session)


@admin_user_router.get("/{user_id}/runs", response_model=list[RunInfoResponse])
async def list_runs_by_user_admin(
        user_id: int,
        session: SessionDep,
        table_view_args: TableViewRequestDep
):
    """获取指定用户的所有仿真运行（管理员）"""
    # 首先验证用户是否存在
    user = await User.get_exist_one(session, user_id)

    # 找到用户的所有项目
    projects = await Project.get(
        session,
        Project.user_id == user_id,
        fetch_mode="all"
    )

    if not projects:
        return []

    # 获取这些项目的所有运行
    project_ids = [project.id for project in projects]
    runs = await Run.get(
        session,
        Run.project_id.in_(project_ids),
        offset=table_view_args.offset,
        limit=table_view_args.limit,
        fetch_mode="all",
        order_by=[table_view_args.clause(Run)]
    )

    # 更新每个Run的状态
    for run in runs:
        await run.get_status(session)

    return await RunInfoResponse.from_run(runs)

@admin_user_router.get("/{user_id}/projects", response_model=list[ProjectInfoResponse])
async def list_projects_by_user_admin(
        user_id: int,
        session: SessionDep,
        table_view_args: TableViewRequestDep
):
    """获取指定用户的项目列表（管理员）"""
    # 首先验证用户是否存在
    user = await User.get_exist_one(session, user_id)

    projects = await Project.get(
        session,
        Project.user_id == user_id,
        offset=table_view_args.offset,
        limit=table_view_args.limit,
        fetch_mode="all",
        order_by=[table_view_args.clause(Project)]
    )

    return await ProjectInfoResponse.from_project(projects)

router.include_router(admin_user_router)

# 项目管理部分
admin_project_router = APIRouter(prefix="/project", tags=["项目管理"])

@admin_project_router.get("", response_model=list[ProjectInfoResponse])
async def list_projects_admin(session: SessionDep, table_view_args: TableViewRequestDep):
    """获取所有项目列表（管理员）"""
    projects = await Project.get(
        session,
        None,  # 不设条件，获取所有项目
        offset=table_view_args.offset,
        limit=table_view_args.limit,
        fetch_mode="all",
        order_by=[table_view_args.clause(Project)]
    )

    return await ProjectInfoResponse.from_project(projects)

@admin_project_router.get("/{id}", response_model=ProjectInfoResponse)
async def get_project_admin(id: int, session: SessionDep):
    """获取特定项目的详情（管理员）"""
    project = await Project.get_exist_one(session=session, id=id)
    return await ProjectInfoResponse.from_project(project)

@admin_project_router.patch("/{id}", response_model=ProjectInfoResponse)
async def update_project_admin(
        id: int,
        update_data: ProjectUpdateRequestDep,
        session: SessionDep,
        files: ProjectFileType | None = None,
):
    """更新项目（管理员）"""
    project = await Project.get_exist_one(session=session, id=id)
    await project.update(session, update_data, files=files)
    return await ProjectInfoResponse.from_project(project)

@admin_project_router.delete("/{id}", response_model=Literal[True])
async def delete_project_admin(id: int, session: SessionDep):
    """删除项目（管理员）"""
    project = await Project.get_exist_one(session=session, id=id)
    await Project.delete(session, project)
    return True

@admin_project_router.delete("/{id}/files/{file_name}", response_model=ProjectInfoResponse)
async def delete_file_admin(id: int, file_name: str, session: SessionDep):
    """删除项目中的文件（管理员）"""
    project = await Project.get_exist_one(session=session, id=id)
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

@admin_project_router.get("/{id}/files", response_class=FileResponse)
async def download_project_zip_admin(id: int, session: SessionDep):
    """将项目文件打包成zip下载（管理员）"""
    # 获取项目
    project = await Project.get_exist_one(session=session, id=id)

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

@admin_project_router.get("/{project_id}/runs", response_model=list[RunInfoResponse])
async def list_runs_by_project_admin(
        project_id: int,
        session: SessionDep,
        table_view_args: TableViewRequestDep
):
    """获取指定项目的所有仿真运行（管理员）"""
    # 首先验证项目是否存在
    project = await Project.get_exist_one(session, project_id)

    # 获取项目的所有运行
    runs = await Run.get(
        session,
        Run.project_id == project_id,
        offset=table_view_args.offset,
        limit=table_view_args.limit,
        fetch_mode="all",
        order_by=[table_view_args.clause(Run)]
    )

    # 更新每个Run的状态
    for run in runs:
        await run.get_status(session)

    return await RunInfoResponse.from_run(runs)

router.include_router(admin_project_router)

# 仿真运行管理部分
admin_run_router = APIRouter(prefix="/run", tags=["运行管理"])

@admin_run_router.get("", response_model=list[RunInfoResponse])
async def list_runs_admin(session: SessionDep, table_view_args: TableViewRequestDep):
    """获取所有仿真运行列表（管理员）"""
    runs = await Run.get(
        session,
        None,  # 不设条件，获取所有运行
        offset=table_view_args.offset,
        limit=table_view_args.limit,
        fetch_mode="all",
        order_by=[table_view_args.clause(Run)],
        load=Run.project
    )

    # 更新每个Run的状态
    for run in runs:
        # await session.refresh(run)
        await run.get_status(session)

    return await RunInfoResponse.from_run(runs)

@admin_run_router.get("/{id}", response_model=RunInfoResponse)
async def get_run_admin(id: int, session: SessionDep):
    """获取特定仿真运行的详情（管理员）"""
    run = await Run.get_exist_one(session, id, load=Run.project)
    await run.get_status(session)
    return await RunInfoResponse.from_run(run)

@admin_run_router.post("/{id}/execute", response_model=RunInfoResponse)
async def execute_run_admin(id: int, session: SessionDep):
    """执行仿真（管理员）"""
    # 获取Run（需要加载project关系）
    run = await Run.get_exist_one(session, id, load=Run.project)

    # 检查状态
    if run.status != RunStatus.PENDING:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="仿真已经被执行过")

    # 执行仿真
    await run.execute(session)

    return await RunInfoResponse.from_run(run)

@admin_run_router.post("/{id}/cancel", response_model=RunInfoResponse)
async def cancel_run_admin(id: int, session: SessionDep):
    """取消运行中的仿真（管理员）"""
    # 获取Run
    run = await Run.get_exist_one(session, id, load=Run.project)

    # 取消仿真
    try:
        await run.cancel(session)
    except RuntimeError as re:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=str(re)
        )
    run = await Run.get(session, Run.id == run.id, load=Run.project)
    return await RunInfoResponse.from_run(run)

@admin_run_router.delete("/{id}", response_model=Literal[True])
async def delete_run_admin(id: int, session: SessionDep):
    """删除仿真运行（管理员）"""
    run = await Run.get_exist_one(session, id)
    await Run.delete(session, run)
    return True

@admin_run_router.get("/{run_id}/files/{file_name}", response_class=FileResponse)
async def get_run_file_admin(run_id: int, file_name: str, session: SessionDep):
    """下载仿真结果文件（管理员）"""
    # 获取Run
    run = await Run.get_exist_one(session, run_id, load=Run.project)

    return FileResponse(path=await ensure_file_path_valid(run.dir, file_name), filename=file_name)

@admin_run_router.get("/{run_id}/files", response_class=FileResponse)
async def download_run_results_zip_admin(run_id: int, session: SessionDep):
    """将运行结果打包成zip下载（管理员）"""
    # 获取Run
    run = await Run.get_exist_one(session, run_id, load=Run.project)

    try:
        await os.makedirs(run.dir, exist_ok=True)

        zip_path, filename = await create_zip_archive(run.dir)

        return FileResponse(
            path=zip_path,
            filename=f"仿真_{run.id}_结果.zip",
            media_type="application/zip",
            background=BackgroundTask(lambda: sync_os.unlink(zip_path))  # 下载完成后删除临时文件
        )
    except Exception as e:
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"创建ZIP文件失败: {str(e)}"
        )

router.include_router(admin_run_router)

