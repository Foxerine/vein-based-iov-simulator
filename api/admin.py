from typing import Literal

from fastapi import APIRouter, Query, Depends, HTTPException
from starlette import status

from api.user import delete_user
from models.user import User, UserInfoResponse, AdminUserUpdateRequest
from models.project import ProjectInfoResponse, Project, ProjectFileType
from utils.auth import get_password_hash
from utils.depends import AdminUserDep, SessionDep, TableViewRequestDep, ProjectUpdateRequestDep, AdminUserDepAnnotated

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

router.include_router(admin_user_router)

# 项目管理部分
admin_project_router = APIRouter(prefix="/project", tags=["项目管理"])

@admin_project_router.get("/user/{user_id}", response_model=list[ProjectInfoResponse])
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


router.include_router(admin_project_router)

# runs, etc.
