#!/bin/bash
set -e # 如果命令失败，立即退出

# --- 配置 ---
VEINS_VERSION="veins-5.3"
OPP_ENV_INSTALL_DIR="/opp_env_inst"
VEINS_DIR="${OPP_ENV_INSTALL_DIR}/${VEINS_VERSION}"
VEINS_LAUNCHD_PATH="${VEINS_DIR}/bin/veins_launchd"
SUMO_CMD=$(which sumo)
RESULTS_DIR="/simulation/results"
PROJECT_DIR="/simulation/project"
VEINS_LAUNCHD_PID="" # 初始化 PID 变量

# --- 函数定义 ---
activate_env() {
    # 加载 Nix 环境和 Python 虚拟环境
    # shellcheck source=/dev/null
    . /root/.nix-profile/etc/profile.d/nix.sh
    # shellcheck source=/dev/null
    . /opt/venv/bin/activate
}

start_veins_launchd() {
    echo ">>> 正在启动 veins_launchd..."
    activate_env

    if [ ! -x "$VEINS_LAUNCHD_PATH" ]; then
        echo "错误: 在 ${VEINS_LAUNCHD_PATH} 未找到 veins_launchd 或不可执行"
        exit 1
    fi
    if [ -z "$SUMO_CMD" ] || [ ! -x "$SUMO_CMD" ]; then
        echo "错误: 在 PATH 中未找到 sumo 可执行文件或不可执行"
        exit 1
    fi

    # 使用 opp_env shell 确保环境正确，并在后台启动 veins_launchd
    opp_env shell "${VEINS_VERSION}" -- "${VEINS_LAUNCHD_PATH}" -vv -c "${SUMO_CMD}" &
    VEINS_LAUNCHD_PID=$!
    echo "veins_launchd 已启动，PID: ${VEINS_LAUNCHD_PID}"
    sleep 3 # 等待 veins_launchd 启动并监听

    if ! ps -p $VEINS_LAUNCHD_PID > /dev/null; then
        echo "错误: veins_launchd 启动失败或意外终止。"
        exit 1
    fi
}

stop_veins_launchd() {
    if [ ! -z "$VEINS_LAUNCHD_PID" ] && ps -p "$VEINS_LAUNCHD_PID" > /dev/null; then
        echo ">>> 正在停止 veins_launchd (PID: $VEINS_LAUNCHD_PID)..."
        kill "$VEINS_LAUNCHD_PID"
        # 等待进程结束，忽略可能出现的 "No such process" 错误
        wait "$VEINS_LAUNCHD_PID" 2>/dev/null || true
        echo "veins_launchd 已停止。"
    fi
    VEINS_LAUNCHD_PID="" # 重置 PID
}

compile_project() {
    echo ">>> 正在尝试编译项目代码 (在 ${PROJECT_DIR})..."
    activate_env
    cd "${PROJECT_DIR}" # 进入项目目录

    # 使用 opp_env shell 执行编译命令
    # opp_makemake 生成 Makefile，make 进行编译 (通常使用 release 模式)
    if ! opp_env shell "${VEINS_VERSION}" -- bash -c 'opp_makemake -f --deep && make MODE=release -j$(nproc)'; then
        echo "错误: 项目编译失败。请检查项目代码和 Makefile。"
        exit 1
    fi
    echo ">>> 项目编译成功。"
    # 返回到原始目录（虽然在这里可能不是必须的，但作为好习惯）
    cd - > /dev/null
}

# --- 主逻辑 ---

# 设置 trap，确保脚本退出时（正常或异常）尝试停止 veins_launchd
trap stop_veins_launchd EXIT SIGINT SIGTERM

# 显示帮助信息
if [ "$1" == "--help" ] || [ $# -eq 0 ]; then
    echo "用法: docker run --rm -v <宿主机项目路径>:/simulation/project \\"
    echo "                   -v <宿主机结果路径>:/simulation/results \\"
    echo "       <镜像名称> [传递给 opp_run 的参数]"
    echo ""
    echo "功能:"
    echo " 1. 启动 veins_launchd (SUMO TraCI 服务器)."
    echo " 2. 尝试在 /simulation/project 目录编译项目 (opp_makemake && make)."
    echo " 3. 使用 Cmdenv 执行 Veins/OMNeT++ 仿真."
    echo " 4. 将结果输出到 /simulation/results 目录."
    echo " 5. 仿真结束后自动停止 veins_launchd."
    echo ""
    echo "示例:"
    echo "  docker run --rm \\"
    echo "    -v \`pwd\`/my_custom_veins_project:/simulation/project \\"
    echo "    -v \`pwd\`/results:/simulation/results \\"
    echo "    my-veins-compiler-runner \\"
    echo "    -u Cmdenv -c MyConfig -r 0 \\"
    echo "    -n \".:/opp_env_inst/veins-5.3/src/veins:/opp_env_inst/inet-4.5.4/src\" \\"
    echo "    -l \"/opp_env_inst/inet-4.5.4/src/INET\" \\"
    echo "    -l \"/opp_env_inst/veins-5.3/src/veins\" \\"
    echo "    omnetpp.ini"
    echo ""
    echo "重要提示:"
    echo " - 宿主机项目路径 <宿主机项目路径> 需要包含:"
    echo "    - omnetpp.ini 文件"
    echo "    - 所有 .ned 文件"
    echo "    - 所有自定义 C++ 源文件 (.cc, .h)"
    echo "    - 可能需要的 Makefile (如果 opp_makemake 不能自动生成合适的)"
    echo "    - 所有 SUMO 相关文件 (如 .net.xml, .rou.xml, .sumocfg) 并确保 omnetpp.ini 正确引用它们"
    echo " - 确保 omnetpp.ini 配置使用 Cmdenv，并将结果目录设置为 'result-dir = /simulation/results'."
    echo " - 提供的 opp_run 参数中，路径必须是容器内部路径 (例如 '.', '/opp_env_inst/...')."
    exit 0
fi

# 启动 veins_launchd
start_veins_launchd

# 编译项目代码
compile_project

# 进入项目工作目录
cd "${PROJECT_DIR}"

echo ">>> 准备执行仿真命令:"
echo "opp_env shell ${VEINS_VERSION} -- opp_run $*"
echo "---------------------------------------------"

# 激活环境并执行用户传入的 opp_run 命令
# 使用 opp_env shell 来设置 OMNeT++/INET/Veins 的环境
# "$@" 会将所有传递给 entrypoint.sh 的参数原样传递给 opp_run
# shellcheck disable=SC2068 # 我们希望这里进行单词分割
activate_env # 确保环境已激活
if ! opp_env shell "${VEINS_VERSION}" -- opp_run "$@"; then
    SIM_EXIT_CODE=$?
    echo "---------------------------------------------"
    echo "错误: 仿真执行失败，退出码: ${SIM_EXIT_CODE}"
    # trap 会负责调用 stop_veins_launchd
    exit $SIM_EXIT_CODE
fi

# 记录 opp_run 的退出状态
SIM_EXIT_CODE=$?

echo "---------------------------------------------"
echo "仿真成功完成，退出码: ${SIM_EXIT_CODE}"

# stop_veins_launchd 会在脚本退出时通过 trap 自动调用

# 以 opp_run 的退出状态退出脚本
exit $SIM_EXIT_CODE
