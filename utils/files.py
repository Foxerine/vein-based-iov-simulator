import os as sync_os
from aiofiles import os
from pathlib import Path
from fastapi import HTTPException, status
from pathvalidate import validate_filepath, ValidationError
from config import config  # 导入配置

async def list_result_files(path: str) -> set[tuple[str, int]]:
    # 获取项目文件列表
    try:
        files_list = [f for f in await os.listdir(path) if await os.path.isfile(sync_os.path.join(path, f))]
    except FileNotFoundError:
        files_list = []

    # 计算项目总大小
    files: set[tuple[str, int]] = set()
    for file_name in files_list:
        file_path = sync_os.path.join(path, file_name)
        if await os.path.exists(file_path):
            files.add((file_name, await os.path.getsize(file_path)))

    return files

async def ensure_file_path_valid(base_dir: str, relative_path: str, allow_protected_dirs: bool = False) -> str:
    """
    验证文件路径是否有效并存在，防止目录遍历攻击

    参数:
        base_dir: 基础目录（绝对路径）
        relative_path: 相对于基础目录的文件路径
        allow_protected_dirs: 是否允许操作受保护的目录，默认为False

    返回:
        str: 验证后的绝对文件路径

    异常:
        HTTPException: 如果路径无效或文件不存在
    """
    try:
        # 使用pathvalidate验证文件名部分
        validate_filepath(relative_path, platform="auto")

        # 创建Path对象并解析路径（跨平台）
        base_path = Path(base_dir).resolve()
        file_path = (base_path / relative_path).resolve()

        # 确保文件路径在基础目录内（防止目录遍历）
        if not str(file_path.parent).startswith(str(base_path)):
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="非法访问路径（目录遍历）"
            )

        # 检查是否为受保护的目录
        if not allow_protected_dirs:
            protected_dirs = [
                config.user_projects_base_dir,
                config.runs_base_dir_name_in_project
            ]

            # 检查路径是否匹配任何受保护目录
            rel_path = Path(relative_path)
            if rel_path.name in protected_dirs or str(rel_path) in protected_dirs:
                raise HTTPException(
                    status_code=status.HTTP_403_FORBIDDEN,
                    detail="无法移除该文件夹"
                )

        file_exists = await os.path.exists(str(file_path))
        if not file_exists:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail="文件不存在"
            )

        return str(file_path)

    except ValidationError as e:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"文件名无效: {str(e)}"
        )
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"路径验证出错: {str(e)}"
        )
