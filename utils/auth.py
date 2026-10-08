from datetime import datetime, timedelta, timezone
import uuid

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

def generate_vnc_token() -> str:
    """为一次 GUI 运行生成新的 VNC 会话令牌。

    取自 uuid4：由操作系统 CSPRNG（os.urandom）生成，含 122 位随机熵，
    每次执行都重新生成，无法由用户/项目/运行 ID 推算。
    令牌只作为容器内 nginx 的访问路径存在，容器停止即失效。
    保持 UUID 字面格式，因为容器入口脚本会把它代入 nginx location 与 sed 表达式。
    """
    return str(uuid.uuid4())

