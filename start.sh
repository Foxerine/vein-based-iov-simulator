#!/bin/sh
# 启动Docker服务
/usr/bin/dockerd &

# 等待Docker服务就绪
while ! docker info > /dev/null 2>&1; do
  echo "等待Docker服务就绪..."
  sleep 1
done

# 导入预构建的worker镜像
echo "正在加载预构建的veins-worker镜像..."
docker load -i /tmp/veins-worker-imported.tar
rm -f /tmp/veins-worker-imported.tar
echo "已删除临时镜像文件"

# 启动Redis服务
redis-server --daemonize yes

# 启动Worker
/app/worker_venv/bin/celery -A worker.worker.celery_app worker --loglevel=info -n veins-worker@%h &

# 启动FastAPI应用
/app/backend_venv/bin/uvicorn main:app --host 0.0.0.0 --port ${PORT:-8000}
