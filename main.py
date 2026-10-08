import asyncio
import contextlib
import pathlib
import uvicorn
from fastapi import FastAPI
from contextlib import asynccontextmanager
from loguru import logger as l

from config import config

from models import init_db
from models.database_connection import engine
from api import root_router
from utils.reconcile import reconcile_forever
# from worker.worker import celery_app

_reconciler: asyncio.Task | None = None

async def startup():
    global _reconciler
    l.info("Starting up...")
    await init_db()
    path_to_create = pathlib.Path(config.user_projects_base_dir)
    path_to_create.mkdir(parents=True, exist_ok=True)
    l.info(f"User projects base directory ensured: {path_to_create.resolve()}")
    if config.reconcile_interval_s > 0:
        _reconciler = asyncio.create_task(reconcile_forever(engine, config.reconcile_interval_s))

    # argv = [
    #     'worker',
    #     '--loglevel=info',
    #     '-n=veins-worker@%h'  # 自动添加主机名
    # ]
    # celery_app.worker_main(argv)

async def shutdown():
    l.info("Shutting down...")
    if _reconciler:
        _reconciler.cancel()
        with contextlib.suppress(asyncio.CancelledError):
            await _reconciler

@asynccontextmanager
async def lifespan(app: FastAPI):
    """
    Function that handles startup and shutdown events.
    To understand more, read https://fastapi.tiangolo.com/advanced/events/
    """
    await startup()
    yield
    await shutdown()

# 创建FastAPI应用
app = FastAPI(
    title="Veins Based IoV Simulator",
    version="0.0.1",
    lifespan=lifespan,
)

# 包含API路由
app.include_router(root_router)

if __name__ == "__main__":
    uvicorn.run("main:app", host="0.0.0.0", port=8000)
