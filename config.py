from sqlmodel import SQLModel
from charset_normalizer import from_bytes
from loguru import logger
import toml

class Config(SQLModel):
    admin_email: str
    """默认管理员用户"""

    admin_password: str
    """默认管理员密码"""

    jwt_secret: str
    """务必随机修改为256位随机字符串"""

    jwt_algorithm: str = "HS256"
    """一般情况下无需修改"""

    jwt_access_token_expire_minutes: int = 14 * 24 * 60
    """JWT Token 有效期，无需修改"""

    database_url: str = "sqlite+aiosqlite:///./data.db"
    """SQL 数据库 URL"""

    user_projects_base_dir: str = "user_projects"
    """用户项目文件夹存放的目录，一般无需修改"""

    runs_base_dir_name_in_project: str = "runs"
    """用户运行记录文件夹存放的目录（在项目目录里），一般无需修改"""

    debug: bool = True
    testing: bool = False

    max_allowed_table_view_limit: int = 20
    """查表最多允许返回多少行的内容"""

    simulation_max_timeout: int = 60 * 60 * 4
    """最大允许仿真运行的时间，默认是 4 小时 """

    max_concurrent_simulations: int = 5
    """一次最多可以同时运行的仿真数量，建议少于CPU核心数"""

    celery_broker_url: str = "redis://localhost:6379"
    celery_result_backend: str = "redis://localhost:6379"

    @staticmethod
    def load_from_file(path: str = "config.cfg") -> "Config":
        try:
            with open(path, "rb") as f:
                if guessed_str := from_bytes(f.read()).best():
                    _config = Config.model_validate(toml.loads(str(guessed_str)))
                    logger.info(f"已载入配置文件：{_config}")
                    return _config
                else:
                    raise ValueError("无法识别配置文件")
        except Exception as e:
            logger.exception(e)
            logger.error("配置文件有误")
            exit(-1)

if __name__ == "__main__":
    config = Config.load_from_file("../config.cfg")
    print(config)
else:
    config = Config.load_from_file()
