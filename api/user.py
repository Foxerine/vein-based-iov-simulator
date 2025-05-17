from typing import Literal

from fastapi import APIRouter, HTTPException

from models.user import UserInfoResponse, UserUpdateRequest, User
from utils.auth import get_password_hash
from utils.depends import CurrentActiveUserDep, SessionDep

router = APIRouter(prefix="/user", tags=["用户"])

@router.get("", response_model=UserInfoResponse)
async def read_user(current_user: CurrentActiveUserDep):
    return current_user

@router.patch("", response_model=UserInfoResponse)
async def update_user(session: SessionDep, current_user: CurrentActiveUserDep, update_data: UserUpdateRequest):
    extra_data = {}
    if password := update_data.model_dump(exclude_unset=True).get("password"):
        extra_data["hashed_password"] = get_password_hash(password)

    return await current_user.update(session, update_data, extra_data)

@router.delete("", response_model=Literal[True])
async def delete_user(current_user: CurrentActiveUserDep, session: SessionDep):
    if current_user.is_admin:
        raise HTTPException(
            status_code=400,
            detail="管理员用户无法被删除"
        )

    await User.delete(session, current_user)
    return True
