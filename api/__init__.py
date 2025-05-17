from fastapi import APIRouter
from api.auth import router as auth_router
from api.user import router as users_router
from api.admin import router as admin_router
from api.project import router as project_router
from api.run import router as run_router

root_router = APIRouter(prefix="/api")

# 包含各种子路由
root_router.include_router(auth_router)
root_router.include_router(users_router)
root_router.include_router(admin_router)
root_router.include_router(project_router)
root_router.include_router(run_router)
