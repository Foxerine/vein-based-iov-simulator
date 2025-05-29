import os as sync_os

from aiofiles import os
from fastapi import APIRouter, HTTPException, status
from starlette.background import BackgroundTask

from models import Project
from models.run import Run, RunCreateRequest, RunInfoResponse, RunStatus
from utils.depends import CurrentActiveUserDep, SessionDep
from fastapi.responses import FileResponse

from utils.files import ensure_file_path_valid, create_zip_archive

router = APIRouter(prefix="/run", tags=["仿真运行"])

@router.post("", response_model=RunInfoResponse)
async def create_run(
        run_data: RunCreateRequest,
        session: SessionDep,
        current_user: CurrentActiveUserDep,
):
    """创建新的仿真运行"""
    # 获取项目
    project = await Project.get_exist_one(session, run_data.project_id, user_id=current_user.id)

    # 创建Run实例
    run = Run.model_validate(run_data)
    await run.save(session)
    run = await Run.get(session, Run.id == run.id, load=Run.project)
    return await RunInfoResponse.from_run(run)

@router.post("/{id}/execute", response_model=RunInfoResponse)
async def execute_run(
        id: int,
        session: SessionDep,
        current_user: CurrentActiveUserDep
):
    """开始执行仿真"""
    # 获取Run
    run = await Run.get_exist_one(session, id, user_id=current_user.id, load=Run.project)

    # 检查状态
    if run.status != RunStatus.PENDING:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="仿真已经被执行过")

    # 执行仿真
    await run.execute(session)

    return await RunInfoResponse.from_run(run)

@router.get("/{run_id}", response_model=RunInfoResponse)
async def get_run(
        run_id: int,
        session: SessionDep,
        current_user: CurrentActiveUserDep
):
    """获取仿真详情和状态"""
    # 获取Run
    run = await Run.get_exist_one(session, run_id, current_user.id, load=Run.project)

    # 更新状态
    await run.get_status(session)

    return await RunInfoResponse.from_run(run)

@router.post("/{run_id}/cancel", response_model=RunInfoResponse)
async def cancel_run(
        run_id: int,
        session: SessionDep,
        current_user: CurrentActiveUserDep
):
    """取消运行中的仿真"""
    # 获取Run
    run = await Run.get_exist_one(session, run_id, current_user.id, load=Run.project)

    # 取消仿真
    try:
        await run.cancel(session)
        run = await Run.get(session, Run.id == run.id, load=Run.project)
    except RuntimeError as re:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=str(re)
        )
    return await RunInfoResponse.from_run(run)

@router.get("/{run_id}/files/{file_name}", response_class=FileResponse)
async def get_run_file(
        run_id: int,
        file_name: str,
        session: SessionDep,
        current_user: CurrentActiveUserDep
):
    """下载仿真结果文件"""
    # 获取Run
    run = await Run.get_exist_one(session, run_id, current_user.id, load=Run.project)

    return FileResponse(path=await ensure_file_path_valid(run.dir, file_name), filename=file_name)

@router.get("/{run_id}/files", response_class=FileResponse)
async def download_run_results_zip(
        run_id: int,
        session: SessionDep,
        current_user: CurrentActiveUserDep
):
    """将运行结果打包成zip下载"""
    # 获取Run并验证权限
    run = await Run.get_exist_one(session, run_id, current_user.id, load=Run.project)

    # 创建zip文件
    try:
        # 确保结果目录存在
        await os.makedirs(run.dir, exist_ok=True)

        # 创建zip归档
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
