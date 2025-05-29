#!/bin/bash
set -e

# --- 基本变量定义 ---
VEINS_VERSION="veins-5.3"
OPP_ENV_DIR="/opp_env_inst"
VEINS_DIR="${OPP_ENV_DIR}/${VEINS_VERSION}"
VEINS_LAUNCHD="${VEINS_DIR}/bin/veins_launchd"
PROJECT_DIR="/simulation/project"
RESULTS_DIR="/simulation/results"
SUMO_CMD=$(which sumo)

# 进程PID跟踪
VEINS_LAUNCHD_PID=""
XVFB_PID=""
VNC_PID=""
WEBSOCKIFY_PID=""
NGINX_PID=""

# 获取参数
GUI_MODE=false
UI_MODE="Cmdenv"
VNC_UUID=""
CONFIG_NAME=""

# --- 解析参数 ---
while [[ $# -gt 0 ]]; do
    case $1 in
        --gui-mode)
            GUI_MODE=true
            shift
            ;;
        --vnc-uuid)
            VNC_UUID="$2"
            shift 2
            ;;
        --config-name)
            CONFIG_NAME="$2"
            shift 2
            ;;
        -u)
            if [ "$2" == "Qtenv" ]; then
                GUI_MODE=true
                UI_MODE="Qtenv"
            else
                UI_MODE="$2"
            fi
            shift 2
            ;;
        --help)
            echo "Veins仿真Docker容器"
            echo ""
            echo "参数:"
            echo "  --gui-mode              启用GUI模式"
            echo "  --vnc-uuid UUID         VNC访问的UUID验证"
            echo "  --config-name NAME      仿真配置名称"
            echo "  -u MODE                 UI模式 (Cmdenv/Qtenv)"
            echo ""
            echo "其他参数将传递给 opp_run"
            echo ""
            echo "请确保 omnetpp.ini 中设置 result-dir = /simulation/results"
            exit 0
            ;;
        *)
            # 保存其他参数给opp_run
            OPP_ARGS+=("$1")
            shift
            ;;
    esac
done

# --- 初始化基本环境 ---
source /root/.nix-profile/etc/profile.d/nix.sh
source /opt/venv/bin/activate

echo ">>> 仿真配置:"
echo "    配置名称: ${CONFIG_NAME:-未指定}"
echo "    GUI模式: $GUI_MODE"
echo "    UI模式: $UI_MODE"
if [ "$GUI_MODE" = true ] && [ -n "$VNC_UUID" ]; then
    echo "    VNC UUID: ${VNC_UUID:0:8}****"
fi

# --- 清理函数 ---
cleanup() {
    echo ">>> 开始清理进程..."

    # 停止nginx
    if [ -n "$NGINX_PID" ] && kill -0 "$NGINX_PID" 2>/dev/null; then
        echo ">>> 停止 nginx (PID: $NGINX_PID)..."
        kill "$NGINX_PID" 2>/dev/null || true
    fi

    # 停止websockify
    if [ -n "$WEBSOCKIFY_PID" ] && kill -0 "$WEBSOCKIFY_PID" 2>/dev/null; then
        echo ">>> 停止 websockify (PID: $WEBSOCKIFY_PID)..."
        kill "$WEBSOCKIFY_PID" 2>/dev/null || true
    fi

    # 停止VNC
    if [ -n "$VNC_PID" ] && kill -0 "$VNC_PID" 2>/dev/null; then
        echo ">>> 停止 x11vnc (PID: $VNC_PID)..."
        kill "$VNC_PID" 2>/dev/null || true
    fi

    # 停止Xvfb
    if [ -n "$XVFB_PID" ] && kill -0 "$XVFB_PID" 2>/dev/null; then
        echo ">>> 停止 Xvfb (PID: $XVFB_PID)..."
        kill "$XVFB_PID" 2>/dev/null || true
    fi

    # 停止veins_launchd
    if [ -n "$VEINS_LAUNCHD_PID" ] && kill -0 "$VEINS_LAUNCHD_PID" 2>/dev/null; then
        echo ">>> 停止 veins_launchd (PID: $VEINS_LAUNCHD_PID)..."
        kill "$VEINS_LAUNCHD_PID" 2>/dev/null || true
    fi

    echo ">>> 清理完成"
}

trap cleanup EXIT INT TERM

# --- GUI环境启动函数 ---
start_gui_environment() {
    echo ">>> 启动GUI环境..."

    # 1. 启动虚拟X服务器
    echo ">>> 启动Xvfb虚拟显示器..."
    Xvfb $DISPLAY -screen 0 ${SCREEN_WIDTH}x${SCREEN_HEIGHT}x${SCREEN_DEPTH} -ac -pn -noreset &
    XVFB_PID=$!
    sleep 3

    if ! kill -0 $XVFB_PID 2>/dev/null; then
        echo "错误: Xvfb启动失败"
        exit 1
    fi
    echo ">>> Xvfb启动成功 (PID: $XVFB_PID)"

    # 2. 启动VNC服务器
    echo ">>> 启动VNC服务器..."
    x11vnc -display $DISPLAY -nopw -listen 127.0.0.1 -xkb -ncache 10 -ncache_cr -forever -shared -bg -o /tmp/x11vnc.log
    sleep 2

    VNC_PID=$(pgrep x11vnc)
    if [ -z "$VNC_PID" ]; then
        echo "错误: x11vnc启动失败"
        cat /tmp/x11vnc.log 2>/dev/null || true
        exit 1
    fi
    echo ">>> VNC服务器启动成功 (PID: $VNC_PID)"

    # 3. 启动WebSockify
    echo ">>> 启动WebSockify..."
    websockify --web=/usr/share/novnc/ --wrap-mode=ignore 6080 127.0.0.1:5900 &
    WEBSOCKIFY_PID=$!
    sleep 2

    if ! kill -0 $WEBSOCKIFY_PID 2>/dev/null; then
        echo "错误: WebSockify启动失败"
        exit 1
    fi
    echo ">>> WebSockify启动成功 (PID: $WEBSOCKIFY_PID)"

    # 4. 配置Nginx代理
    echo ">>> 配置Nginx代理..."
cat > /etc/nginx/sites-enabled/vnc-proxy.conf <<EOF
server {
    listen 8080;
    server_name localhost;

    location /vnc/${VNC_UUID}/ {
        proxy_pass http://127.0.0.1:6080;
        proxy_http_version 1.1;
        proxy_set_header Upgrade \$http_upgrade;
        proxy_set_header Connection "upgrade";
        proxy_set_header Host \$host;
        proxy_set_header X-Real-IP \$remote_addr;
        proxy_set_header X-Forwarded-For \$proxy_add_x_forwarded_for;
        proxy_set_header X-Forwarded-Proto \$scheme;
        proxy_read_timeout 86400;
        proxy_send_timeout 86400;
        rewrite ^/vnc/${VNC_UUID}/(.*)\$ /\$1 break;
    }

    location / {
        return 404;
    }
}
EOF

    sed -i "s/\${VNC_UUID}/${VNC_UUID}/g" /etc/nginx/sites-enabled/vnc-proxy.conf

    # 5. 启动Nginx
    echo ">>> 启动Nginx..."
    nginx -t
    nginx -g 'daemon off;' &
    NGINX_PID=$!
    sleep 2

    if ! kill -0 $NGINX_PID 2>/dev/null; then
        echo "错误: Nginx启动失败"
        nginx -T
        exit 1
    fi
    echo ">>> Nginx启动成功 (PID: $NGINX_PID)"
    # 获取宿主机端口（docker容器下）
    CONTAINER_ID=$(cat /proc/1/cpuset | awk -F/ '{print $3}')
    HOST_PORT=$(docker inspect --format='{{(index (index .NetworkSettings.Ports "8080/tcp") 0).HostPort}}' "$CONTAINER_ID" 2>/dev/null || true)
    LINK_SUFFIX="/vnc/${VNC_UUID}/vnc.html?path=/vnc/${VNC_UUID}/websockify"
    if [ -n "$HOST_PORT" ]; then
        echo ">>> VNC访问地址: http://localhost:${HOST_PORT}${LINK_SUFFIX}"
    else
        echo ">>> VNC访问地址: ${LINK_SUFFIX}"
    fi
}

# --- 启动GUI环境（如果需要） ---
if [ "$GUI_MODE" = true ]; then
    if [ -z "$VNC_UUID" ]; then
        echo "错误: GUI模式需要提供 --vnc-uuid 参数"
        exit 1
    fi
    start_gui_environment
fi

# --- 启动 veins_launchd ---
echo ">>> 启动 veins_launchd..."

if [ ! -f "$VEINS_LAUNCHD" ]; then
    echo "错误: veins_launchd 脚本未找到: $VEINS_LAUNCHD"
    exit 1
fi

"$VEINS_LAUNCHD" -vv -c "$SUMO_CMD" &
VEINS_LAUNCHD_PID=$!
sleep 2

if ! kill -0 $VEINS_LAUNCHD_PID 2>/dev/null; then
    echo "错误: veins_launchd 启动失败"
    exit 1
fi

echo ">>> veins_launchd 启动成功 (PID: $VEINS_LAUNCHD_PID)"

# --- 构建opp_run命令参数 ---
OPP_ARGS=("-u" "$UI_MODE" "${OPP_ARGS[@]}")
CMD_ARGS=""
for arg in "${OPP_ARGS[@]}"; do
    escaped_arg=$(printf '%q' "$arg")
    CMD_ARGS="${CMD_ARGS} ${escaped_arg}"
done

# --- 在opp_env shell中执行仿真 ---
echo ">>> 进入opp_env环境..."
cd ${OPP_ENV_DIR}

# 使用here-document将命令传递给opp_env shell
opp_env shell ${VEINS_VERSION} << EOF
echo ">>> 进入opp_env shell环境"

if [ "$GUI_MODE" = "true" ]; then
    export DISPLAY=:99
    echo ">>> GUI模式: DISPLAY=\$DISPLAY"

    if command -v xdpyinfo >/dev/null && xdpyinfo -display \$DISPLAY >/dev/null 2>&1; then
        echo ">>> X显示器连接正常"
    else
        echo ">>> 警告: 无法验证X显示器连接"
    fi
fi

cd ${PROJECT_DIR}
echo ">>> 当前工作目录: \$(pwd)"
echo ">>> 目录内容:"
ls -la

if [ -f "package.ned" ] || [ -f "omnetpp.ini" ]; then
    echo ">>> 编译项目..."
    opp_makemake -f --deep
    if make MODE=release -j\$(nproc); then
        echo ">>> 项目编译完成"
    else
        echo "错误: 项目编译失败"
        exit 1
    fi
else
    echo ">>> 未找到package.ned或omnetpp.ini文件，跳过编译"
fi

echo ">>> 开始执行仿真..."
echo ">>> 配置名称: ${CONFIG_NAME:-使用参数中的配置}"
echo ">>> 命令: opp_run${CMD_ARGS}"
echo "=============================================="

eval "opp_run${CMD_ARGS}" &
SIM_PID=\$!
wait \$SIM_PID
SIM_EXIT_CODE=\$?

echo "=============================================="
if [ \$SIM_EXIT_CODE -eq 0 ]; then
    echo ">>> 仿真成功完成"
else
    echo ">>> 仿真失败，退出代码: \$SIM_EXIT_CODE"
fi

exit \$SIM_EXIT_CODE
EOF

SIM_EXIT_CODE=$?
exit $SIM_EXIT_CODE
