from typing import Annotated

from fastapi import APIRouter, HTTPException, status, Depends
from fastapi.security import OAuth2PasswordRequestForm

from utils.depends import SessionDep
from models.user import User, UserRegisterRequest, UserLoginRequest
from models.token import TokenResponse
from utils.auth import verify_password, get_password_hash, create_access_token

router = APIRouter(prefix="/auth", tags=["认证"])

@router.post("/register", response_model=TokenResponse)
async def register(user: UserRegisterRequest, session: SessionDep):
    """用户注册"""
    # 检查邮箱是否已存在
    db_user = await User.get(session, User.email == user.email)
    if db_user:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="该邮箱已被注册"
        )

    # 创建新用户
    await User(email=user.email, hashed_password=get_password_hash(user.password)).save(session)

    # 生成访问令牌
    access_token = create_access_token(data={"sub": user.email})
    return TokenResponse(access_token=access_token, token_type="bearer")

@router.post("/login", response_model=TokenResponse)
async def login(user: UserLoginRequest, session: SessionDep):
    """用户登录"""
    # 验证用户
    db_user = await User.get(session, User.email==user.email)
    if not db_user or not verify_password(user.password, db_user.hashed_password):
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="邮箱或密码不正确",
            headers={"WWW-Authenticate": "Bearer"},
        )

    # 生成访问令牌
    access_token = create_access_token(data={"sub": user.email})
    return TokenResponse(access_token=access_token, token_type="bearer")

@router.post("/oauth2", response_model=TokenResponse)
async def get_token(form: Annotated[OAuth2PasswordRequestForm, Depends()], session: SessionDep):
    """token产生"""
    # 验证用户
    db_user = await User.get(session, User.email==form.username)
    if not db_user or not verify_password(form.password, db_user.hashed_password):
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="邮箱或密码不正确",
            headers={"WWW-Authenticate": "Bearer"},
        )

    # 生成访问令牌
    access_token = create_access_token(data={"sub": form.username})
    return TokenResponse(access_token=access_token, token_type="bearer")
