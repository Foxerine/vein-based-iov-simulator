# 基于Veins的车联网仿真系统 - 后端 (Vehicular Network Simulation System Based on Veins - Backend)

本项目是一个本科毕业设计——一个基于Web的Veins车联网仿真系统的后端部分。此 `README.md` 专注于后端服务的介绍。

项目旨在解决 Veins 仿真平台操作复杂、环境依赖性强、学习曲线陡峭的问题。通过提供一个现代化的Web界面（前端由NiceGUI构建，**不包含在此仓库**），后端系统负责处理用户管理、项目配置、任务调度和结果存储，将复杂的仿真流程封装为简单、直观的API调用。

## 系统架构

本系统后端采用前后端分离的现代化架构，并深度整合了异步任务处理与容器化技术，以实现高效、隔离且可扩展的仿真环境。



1.  **API服务 (FastAPI)**: 基于 Python 的高性能 FastAPI 框架，提供 RESTful API 接口，负责处理所有业务逻辑，包括用户认证(JWT)、项目管理和仿真任务的创建。
2.  **数据库 (SQLite)**: 使用轻量级的 SQLite 数据库，通过 SQLModel 进行数据建模与交互，存储用户、项目及仿真任务的元数据。
3.  **异步任务队列 (Celery & Redis)**: 对于耗时的 Veins 仿真任务，API服务将其作为任务发送到 Redis 消息代理。独立的 Celery Worker 进程负责消费这些任务。
4.  **容器化仿真环境 (Docker)**: Celery Worker 通过 Docker SDK 动态启动一个预先构建好的 Docker 容器来执行每一个仿真任务。该容器封装了完整的、标准化的 Veins、OMNeT++ 和 SUMO 环境，确保了仿真的一致性和隔离性。同时，通过 noVNC 技术栈实现了仿真过程的远程Web可视化。

## 主要功能

- **统一的用户认证**：基于 JWT 的安全用户注册、登录和权限管理。
- **项目管理**：支持用户创建仿真项目，上传和管理相关的配置文件（`.ini`, `.net.xml`, `.rou.xml` 等）。
- **异步仿真任务**：通过 Celery 实现仿真任务的异步执行，Web API 响应迅速，不被长时间运行的仿真阻塞。
- **容器化执行**：每个仿真任务都在独立的、干净的 Docker 容器中运行，彻底解决环境依赖和冲突问题。
- **远程过程可视化**：支持在GUI模式下运行仿真，并通过 noVNC 在浏览器中实时查看和交互 OMNeT++/Qtenv 界面。
- **全面的管理员功能**：管理员可以查看和管理系统中的所有用户、项目和仿真任务。
- **完善的API文档**：基于 FastAPI 自动生成交互式 OpenAPI (Swagger) 文档。

## 技术栈

| 类别 | 技术 |
| :--- | :--- |
| **后端框架** | FastAPI, Uvicorn |
| **数据模型** | SQLModel |
| **数据库** | SQLite |
| **异步任务** | Celery |
| **消息代理/结果后端** | Redis |
| **容器化** | Docker, Docker SDK for Python |
| **认证** | JWT (pyjwt), Passlib, Bcrypt |
| **测试** | Pytest |

## 环境准备与安装

本项目后端由三个核心部分组成，需要分别进行环境配置和启动：
1.  **FastAPI 主应用**
2.  **Celery 异步任务 Worker**
3.  **Veins 仿真环境 Docker 镜像**

**先决条件**:
- Git
- Python 3.12+
- Docker
- Redis（建议通过docker安装）

### 1. 克隆项目

```bash
git clone https://github.com/Foxerine/vein-based-iov-simulator.git
cd vein-based-iov-simulator
```

### 2. 配置文件

在项目根目录下，创建 `config.cfg`，然后根据您的环境修改其中的配置。

**`config.cfg` 示例**:
```toml
# 默认管理员用户
admin_email = "admin@example.com"
admin_password = "a_very_strong_password"

# 务必修改为256位随机字符串
jwt_secret = "YOUR_SUPER_SECRET_RANDOM_STRING_HERE"
jwt_algorithm = "HS256"
jwt_access_token_expire_minutes = 20160

# 数据库、项目文件和运行结果的存放位置
database_url = "sqlite+aiosqlite:///./data.db"
user_projects_base_dir = "user_projects"
runs_base_dir_name_in_project = "runs"

# 调试与测试配置
debug = false
testing = false

# Celery 配置
celery_broker_url = "redis://localhost:6379/0"
celery_result_backend = "redis://localhost:6379/1"

# 仿真相关配置
simulation_max_timeout = 14400  # 最大仿真时间 (秒)，默认4小时
max_concurrent_simulations = 4  # Celery Worker 并发数，建议小于CPU核心数
```

### 3. 配置 FastAPI 主应用环境

```bash
# 创建并激活 Python 虚拟环境
python -m venv venv_main
source venv_main/bin/activate  # on Windows: venv_main\Scripts\activate

# 安装依赖
pip install -r requirements.txt
```

### 4. 配置 Celery Worker 环境

Worker 的依赖与主应用略有不同。建议为其创建独立的环境。

```bash
# 创建并激活 Worker 的虚拟环境
python -m venv venv_worker
source venv_worker/bin/activate # on Windows: venv_worker\Scripts\activate

# 安装 Worker 的依赖
pip install -r requirements_worker.txt
```

### 5. 构建 Veins 仿真环境 Docker 镜像

这是执行实际仿真的环境。

```bash
# 进入 worker 目录
cd worker

# 构建 Docker 镜像
# 注意：此过程会下载和编译大量组件，可能需要较长时间
docker build -t veins-simulation-worker:latest .

# 返回项目根目录
cd ..
```

## 运行项目

请确保 **Docker** 和 **Redis** 服务已经启动。

### 1. 启动 FastAPI 后端服务

在 **第一个** 终端窗口中，激活主应用虚拟环境并启动。

```bash
# 激活主应用虚拟环境
source venv_main/bin/activate

# 启动 FastAPI
fastapi run
```
服务启动后，数据库 `data.db` 会被自动创建，并会初始化 `config.cfg` 中配置的管理员账号。

### 2. 启动 Celery Worker

在 **第二个** 终端窗口中，激活 Worker 虚拟环境并启动。

```bash
# 激活 Worker 虚拟环境
source venv_worker/bin/activate

# 启动 Celery Worker
celery -A worker.worker.celery_app worker --loglevel=info
```

现在，整个后端系统已经准备就绪，可以接收来自前端或API工具的请求了。

## API 文档

项目启动后，FastAPI 会自动生成交互式的 API 文档。

- **Swagger UI**: [http://localhost:8000/docs](http://localhost:8000/docs)
- **ReDoc**: [http://localhost:8000/redoc](http://localhost:8000/redoc)

### API 端点概览

| 标签 (Tag) | 方法 (Method) | 路径 (Path) | 描述 |
| :--- | :--- | :--- | :--- |
| **认证** | `POST` | `/api/auth/register` | 用户注册 |
| | `POST` | `/api/auth/login` | 用户登录获取Token |
| **用户** | `GET` | `/api/user` | 获取当前用户信息 |
| | `PATCH` | `/api/user` | 更新当前用户信息 |
| | `DELETE` | `/api/user` | 删除当前用户 |
| **项目** | `POST` | `/api/project` | 创建新项目并上传文件 |
| | `GET` | `/api/project` | 获取当前用户的项目列表 |
| | `GET` | `/api/project/{id}` | 获取项目详情 |
| | `PATCH` | `/api/project/{id}` | 更新项目信息或文件 |
| | `DELETE`| `/api/project/{id}` | 删除项目 |
| | `GET` | `/api/project/{id}/files` | 下载项目文件(ZIP) |
| **仿真运行** | `POST` | `/api/run` | 基于项目创建仿真任务 |
| | `POST` | `/api/run/{id}/execute` | 执行一个待定的仿真任务 |
| | `GET` | `/api/run/{id}` | 获取仿真任务详情和状态 |
| | `POST` | `/api/run/{id}/cancel` | 取消一个运行中的任务 |
| | `GET` | `/api/run/{id}/files` | 下载仿真结果(ZIP) |
| **管理员** | *...* | `/api/admin/...` | 提供对所有用户、项目、运行的增删改查权限 |

## 项目结构

```
.
├── api/                # FastAPI 路由模块
│   ├── admin.py        # 管理员相关API
│   ├── auth.py         # 认证相关API
│   ├── project.py      # 项目管理API
│   ├── run.py          # 仿真运行管理API
│   └── user.py         # 普通用户API
├── models/             # SQLModel 数据模型
│   ├── database_connection.py # 数据库连接和初始化
│   ├── project.py      # 项目模型
│   ├── run.py          # 仿真运行模型
│   └── user.py         # 用户模型
├── tests/              # Pytest 单元测试和集成测试
├── utils/              # 辅助工具模块
│   ├── auth.py         # 密码哈希、JWT Token处理
│   ├── depends.py      # FastAPI 依赖注入
│   └── files.py        # 文件处理工具
├── worker/             # Celery Worker 和仿真环境相关
│   ├── dockerfile      # 用于构建Veins仿真环境的Dockerfile
│   ├── entrypoint.sh   # Docker容器的入口脚本
│   └── worker.py       # Celery任务定义
├── user_projects/      # 存储用户上传的项目文件和仿真结果 (运行时生成)
├── config.cfg          # 项目配置文件 (需手动创建)
├── main.py             # FastAPI 应用主入口
├── requirements.txt    # FastAPI 主应用依赖
└── requirements_worker.txt # Celery Worker 依赖
```

## 致谢

感谢我的指导老师湖南农业大学 **李博** 老师在整个项目设计和开发过程中的悉心指导和大力支持。他的专业知识和宝贵建议对本项目的成功至关重要。

## License

本项目采用 [MIT License](LICENSE)。