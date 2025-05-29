#!/bin/bash

echo "启动 Veins IOV Simulator 后端..."

# 检查数据卷是否存在
if [ ! -f "config.cfg" ]; then
    echo "错误：找不到config.cfg文件！"
    echo "请确保在当前目录中有正确配置的config.cfg文件。"
    exit 1
fi

# 检查是否有运行中的同名容器
if docker ps -a | grep -q "veins-simulator"; then
    echo "已在运行..."
    exit 1
fi

echo "启动容器..."
docker run -d \
  -p 8000:8000 \
  -v "$(pwd)/data.db:/app/data.db" \
  -v "$(pwd)/config.cfg:/app/config.cfg" \
  -v "$(pwd)/user_projects:/app/user_projects" \
  --privileged \
  --name veins-simulator \
  veins-iov-simulator-backend

if [ $? -eq 0 ]; then
    echo ""
    echo "服务启动成功！"
    echo "API现在可以通过 http://localhost:8000 访问。"
    echo ""
else
    echo ""
    echo "服务启动失败，请检查错误信息。"
    echo ""
fi
