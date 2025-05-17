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
VEINS_LAUNCHD_PID=""

# --- 初始化基本环境 ---
source /root/.nix-profile/etc/profile.d/nix.sh
source /opt/venv/bin/activate

# --- 处理退出清理 ---
cleanup() {
    if [ -n "$VEINS_LAUNCHD_PID" ] && ps -p "$VEINS_LAUNCHD_PID" > /dev/null; then
        echo ">>> 停止 veins_launchd (PID: $VEINS_LAUNCHD_PID)..."
        kill "$VEINS_LAUNCHD_PID" 2>/dev/null || true
        wait "$VEINS_LAUNCHD_PID" 2>/dev/null || true
    fi
}
trap cleanup EXIT INT TERM

# --- 帮助信息 ---
if [ "$1" == "--help" ] || [ $# -eq 0 ]; then
    echo "用法: docker run --rm -v <主机项目路径>:/simulation/project \\"
    echo "                 -v <主机结果路径>:/simulation/results \\"
    echo "       <镜像名称> [opp_run 参数]"
    echo ""
    echo "示例: docker run --rm -v \`pwd\`/项目:/simulation/project -v \`pwd\`/结果:/simulation/results veins-image \\"
    echo "       -u Cmdenv -c Default -r 0 \\"
    echo "       -n .:/opp_env_inst/veins-5.3/src/veins:/opp_env_inst/inet-4.5.4/src \\"
    echo "       -l /opp_env_inst/inet-4.5.4/src/INET -l /opp_env_inst/veins-5.3/src/veins \\"
    echo "       omnetpp.ini"
    echo ""
    echo "确保 omnetpp.ini 中设置了 'result-dir = /simulation/results'"
    exit 0
fi

# --- 启动 veins_launchd (作为独立的Python脚本) ---
echo ">>> 启动 veins_launchd..."

# 检查脚本是否存在
if [ ! -f "$VEINS_LAUNCHD" ]; then
    echo "错误: veins_launchd 脚本未找到: $VEINS_LAUNCHD"
    echo "检查路径是否正确，脚本是否存在"
    exit 1
fi

# 直接执行 veins_launchd 脚本
"$VEINS_LAUNCHD" -vv -c "$SUMO_CMD" &
VEINS_LAUNCHD_PID=$!
sleep 2

if ! ps -p $VEINS_LAUNCHD_PID > /dev/null; then
    echo "错误: veins_launchd 启动失败。"
    exit 1
fi
echo ">>> veins_launchd 启动成功 (PID: $VEINS_LAUNCHD_PID)"

# --- 保存所有参数，构建命令行 ---
CMD_ARGS=""
for arg in "$@"; do
    CMD_ARGS="${CMD_ARGS} \"${arg}\""
done

# --- 在opp_env shell中执行命令序列 ---
cd ${OPP_ENV_DIR}

# 使用here-document将命令传递给opp_env shell
opp_env shell ${VEINS_VERSION} << EOF
echo ">>> 进入opp_env shell环境"
echo ">>> PATH=\$PATH"

# 进入项目目录
cd ${PROJECT_DIR}
echo ">>> 当前目录: \$(pwd)"

# 检查是否有必要的文件
if [ -f "package.ned" ] || [ -f "omnetpp.ini" ]; then
    echo ">>> 编译项目..."
    opp_makemake -f --deep
    make MODE=release -j\$(nproc)
    echo ">>> 项目编译完成"
else
    echo ">>> 项目目录中没有找到package.ned或omnetpp.ini，跳过编译"
fi

# 执行仿真
echo ">>> 开始仿真执行..."
echo ">>> 执行: opp_run ${CMD_ARGS}"
echo "---------------------------------------------"
opp_run ${CMD_ARGS}
SIM_EXIT_CODE=\$?
echo "---------------------------------------------"
if [ \$SIM_EXIT_CODE -eq 0 ]; then
    echo "仿真成功完成"
else
    echo "仿真以退出代码 \$SIM_EXIT_CODE 结束"
fi
exit \$SIM_EXIT_CODE
EOF

# 获取opp_env shell的退出码
SIM_EXIT_CODE=$?
exit $SIM_EXIT_CODE
