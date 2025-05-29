# 主镜像构建
FROM python:3.12-alpine

# 安装必要依赖
RUN apk add --no-cache \
    docker docker-cli redis git bash curl procps \
    build-base gcc musl-dev linux-headers

# 创建工作目录
WORKDIR /app

# 复制并安装依赖
COPY requirements_worker.txt requirements.txt ./
RUN python -m venv /app/worker_venv && \
    /app/worker_venv/bin/pip install --no-cache-dir -U pip setuptools wheel && \
    /app/worker_venv/bin/pip install --no-cache-dir -r requirements_worker.txt && \
    python -m venv /app/backend_venv && \
    /app/backend_venv/bin/pip install --no-cache-dir -U pip setuptools wheel && \
    /app/backend_venv/bin/pip install --no-cache-dir -r requirements.txt

# 复制已导入的worker镜像
COPY veins-worker.tar /tmp/veins-worker-imported.tar

# 复制启动脚本
COPY start.sh /app/start.sh
RUN chmod +x /app/start.sh

# 复制项目代码
COPY . .

# 创建需要映射的目录
RUN mkdir -p /app/user_projects

# 设置卷映射
VOLUME ["/app/data.db", "/app/config.cfg", "/app/user_projects"]

# 开放端口（FastAPI后端）
EXPOSE 8000

# 设置启动命令
CMD ["/app/start.sh"]
