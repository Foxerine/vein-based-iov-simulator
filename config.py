from sqlmodel import SQLModel
from charset_normalizer import from_bytes
from loguru import logger
import toml

def redact_url_credentials(value: str) -> str:
    """把 URL 中的用户名/密码（如 redis://:password@host）替换为 ***，用于日志输出。"""
    import re
    return re.sub(r"^([A-Za-z][A-Za-z0-9+.-]*://)[^@/]+@", r"\1***@", value)


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

    vnc_public_host: str = "localhost"
    """生成 VNC 访问链接时使用的对外主机名/IP；部署到服务器时必须改为服务器地址"""

    max_allowed_table_view_limit: int = 20
    """查表最多允许返回多少行的内容"""

    simulation_max_timeout: int = 60 * 60 * 4
    """最大允许仿真运行的时间，默认是 4 小时 """

    max_concurrent_simulations: int = 5
    """单个 worker 同时运行的仿真数量上限（即 Celery worker_concurrency），建议少于CPU核心数"""

    worker_pool: str = "threads"
    """Celery worker 池类型。仿真任务主要在等待容器，且 Windows 原生不支持 prefork，默认 threads"""

    container_cpu_limit: float = 2.0
    """每个仿真容器可用的 CPU 核数上限（opp_run 与 SUMO 各占一个线程），0 表示不限制"""

    container_memory_limit: str = "4g"
    """每个仿真容器的内存上限（同时作为内存+swap 上限，禁止额外使用 swap），空字符串表示不限制"""

    container_pids_limit: int = 1024
    """每个仿真容器的最大进程/线程数，防止 fork 炸弹，0 表示不限制"""

    container_isolate_network: bool = True
    """无头仿真容器不接入任何网络（OMNeT++ 与 SUMO 只经容器内回环地址通信），
    使用户上传并在容器内编译运行的代码无法访问 Redis、后端或其他用户的会话。
    GUI 任务需要对外发布 noVNC 端口，不受此项影响"""

    reconcile_interval_s: int = 30
    """后台状态对账的扫描间隔（秒），0 表示关闭"""

    celery_broker_url: str = "redis://localhost:6379"
    celery_result_backend: str = "redis://localhost:6379"

    @staticmethod
    def load_from_file(path: str = "config.cfg") -> "Config":
        try:
            with open(path, "rb") as f:
                if guessed_str := from_bytes(f.read()).best():
                    _config = Config.model_validate(toml.loads(str(guessed_str)))
                    # 日志中脱敏，凭据绝不能进入日志文件
                    _safe = _config.model_dump()
                    for _k in ("admin_password", "jwt_secret"):
                        if _safe.get(_k):
                            _safe[_k] = "***REDACTED***"
                    for _k, _v in _safe.items():
                        if isinstance(_v, str):
                            _safe[_k] = redact_url_credentials(_v)
                    logger.info(f"已载入配置文件：{_safe}")
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
