from datetime import datetime, timedelta, timezone
import hashlib

import bcrypt
import jwt
from fastapi.security import OAuth2PasswordBearer

from config import config

oauth2_scheme = OAuth2PasswordBearer(tokenUrl="/api/auth/oauth2")

def verify_password(plain_password: str, hashed_password: str | bytes) -> bool:
    if isinstance(hashed_password, str):
        hashed_password = hashed_password.encode('utf-8')
    return bcrypt.checkpw(
        password=plain_password.encode('utf-8'),
        hashed_password=hashed_password
    )

def get_password_hash(password: str):
    return bcrypt.hashpw(password=password.encode('utf-8'), salt=bcrypt.gensalt())

def create_access_token(data: dict, expires_delta: timedelta | None = None) -> str:
    """
    创建访问令牌
    Args:
        data: 要编码到令牌中的数据
        expires_delta: 令牌过期时间间隔，若为None则使用配置的默认值
    Returns:
        JWT令牌字符串
    """
    to_encode = data.copy()
    expire = datetime.now(timezone.utc) + (expires_delta or timedelta(minutes=config.jwt_access_token_expire_minutes))
    to_encode.update({"exp": expire})
    encoded_jwt = jwt.encode(to_encode, config.jwt_secret, algorithm=config.jwt_algorithm)
    return encoded_jwt

def generate_vnc_uuid(user_id: str | int, project_id: str | int, run_id: str | int) -> str:
    """
    根据用户ID、项目ID、运行ID和JWT密钥生成可预测的VNC访问UUID

    Args:
        user_id: 用户ID
        project_id: 项目ID
        run_id: 运行ID

    Returns:
        str: 格式为UUID的字符串
    """
    user_id, project_id, run_id = map(str, [user_id, project_id, run_id])

    # 创建唯一字符串
    unique_string = f"{user_id}:{project_id}:{run_id}:{config.jwt_secret}"

    # 使用SHA-256生成哈希
    hash_obj = hashlib.sha256(unique_string.encode('utf-8'))
    hash_hex = hash_obj.hexdigest()

    # 将哈希值转换为UUID格式 (8-4-4-4-12)
    uuid_str = f"{hash_hex[:8]}-{hash_hex[8:12]}-{hash_hex[12:16]}-{hash_hex[16:20]}-{hash_hex[20:32]}"

    return uuid_str
