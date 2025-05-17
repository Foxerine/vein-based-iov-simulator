from loguru import logger
from sqlalchemy import NullPool, AsyncAdaptedQueuePool
from sqlalchemy.ext.asyncio import create_async_engine
from sqlmodel import SQLModel
from sqlmodel.ext.asyncio.session import AsyncSession
from sqlalchemy.orm import sessionmaker

from config import config
from utils.auth import get_password_hash

from .user import User

engine = create_async_engine(
    config.database_url,
    echo=config.debug,
    poolclass=NullPool
    if config.testing
    else AsyncAdaptedQueuePool,  # Asyncio pytest works with NullPool
    connect_args={"check_same_thread": False} if config.database_url.startswith("sqlite") else None,
    future=True,
    # pool_size=POOL_SIZE,
    # max_overflow=64,
)

_async_session_factory = sessionmaker(engine, class_=AsyncSession)

async def get_session() -> AsyncSession:
    async with _async_session_factory() as session:
        yield session

async def init_db():
    """初始化数据库"""
    # 创建所有表
    async with engine.begin() as conn:
        await conn.run_sync(SQLModel.metadata.create_all)

    async with (AsyncSession(engine) as session):
        # 创建管理员用户（如果不存在）
        if not await User.get(session=session, condition=(User.email==config.admin_email)):
            await User(
                email=config.admin_email,
                hashed_password=get_password_hash(config.admin_password),
                is_admin=True
            ).save(session=session)
            logger.info("管理员用户创建成功")
        else:
            logger.info("管理员用户已存在")
