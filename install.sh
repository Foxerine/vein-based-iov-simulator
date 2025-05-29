#!/bin/bash

echo "开始安装 Veins IOV Simulator 后端..."

# 检查Docker是否已安装
if ! command -v docker &> /dev/null; then
    echo "Docker未安装，正在使用apt安装Docker..."

    # 更新软件包索引并安装依赖
    sudo apt-get update
    sudo apt-get install -y \
        apt-transport-https \
        ca-certificates \
        curl \
        gnupg \
        lsb-release

    # 添加Docker官方GPG密钥
    curl -fsSL https://download.docker.com/linux/$(lsb_release -is | tr '[:upper:]' '[:lower:]')/gpg | sudo gpg --dearmor -o /usr/share/keyrings/docker-archive-keyring.gpg

    # 设置Docker稳定版仓库
    echo \
      "deb [arch=$(dpkg --print-architecture) signed-by=/usr/share/keyrings/docker-archive-keyring.gpg] https://download.docker.com/linux/$(lsb_release -is | tr '[:upper:]' '[:lower:]') \
      $(lsb_release -cs) stable" | sudo tee /etc/apt/sources.list.d/docker.list > /dev/null

    # 安装Docker引擎
    sudo apt-get update
    sudo apt-get install -y docker-ce docker-ce-cli containerd.io

    # 将当前用户添加到docker组（需要重新登录才能生效）
    sudo usermod -aG docker $USER

    echo "Docker安装完成。您可能需要重新登录才能使用Docker而不需要sudo。"
    echo "如果要立即使用Docker而不重新登录，请运行: newgrp docker"

    # 启动Docker服务
    sudo systemctl enable docker
    sudo systemctl start docker
else
    echo "检测到Docker已安装，继续构建镜像..."
fi

# 等待Docker服务启动
echo "等待Docker服务启动..."
while ! docker info &>/dev/null; do
    echo "Docker服务尚未就绪，等待5秒..."
    sleep 5
done

# 构建或导出worker镜像
if docker images -q veins-worker > /dev/null 2>&1; then
    echo "检测到veins-worker镜像已存在，导出镜像..."
else
    echo "构建worker镜像..."
    cd ./worker
    docker build -t veins-worker .
    if [ $? -ne 0 ]; then
        echo "worker镜像构建失败，请检查错误信息。"
        cd ..
        exit 1
    fi
    cd ..
fi

# 导出worker镜像
echo "导出worker镜像为tar文件..."
docker save -o veins-worker.tar veins-worker
if [ $? -ne 0 ]; then
    echo "worker镜像导出失败，请检查错误信息。"
    exit 1
fi

echo "构建后端Docker镜像..."
docker build -t veins-iov-simulator-backend .

if [ $? -eq 0 ]; then
    echo ""
    echo "安装成功！您现在可以使用run.sh脚本运行应用。"
    echo ""

    # 清理临时文件
    if [ -f ./veins-worker.tar ]; then
        echo "清理临时文件..."
        rm -f ./veins-worker.tar
    fi
else
    echo ""
    echo "安装失败，请检查错误信息。"
    echo ""
fi
