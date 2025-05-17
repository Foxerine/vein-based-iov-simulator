#!/bin/bash

# 定义宿主机上的项目和结果目录路径 (根据你的实际情况修改)
HOST_PROJECT_PATH="$(pwd)/test_veins_project"
VEINS_CONFIG_NAME="Default"

# 说明：以下是传递给 entrypoint.sh -> opp_run 的参数
# -u Cmdenv                使用命令行环境
# -c ${VEINS_CONFIG_NAME}  指定 omnetpp.ini 中的配置名
# -r 0                     指定运行编号 (run number)
# -n ".:/opp_env_inst/veins-5.3/src/veins:/opp_env_inst/inet-4.5.4/src"  NED 路径
# -l "/opp_env_inst/inet-4.5.4/src/INET"  链接 INET 库
# -l "/opp_env_inst/veins-5.3/src/veins"  链接 Veins 库
# omnetpp.ini              指定配置文件

# 运行 Docker 容器
docker run --rm \
    -v "${HOST_PROJECT_PATH}:/simulation/project" \
    veins-worker \
    -u Cmdenv \
    -c "${VEINS_CONFIG_NAME}" \
    -r 0 \
    -n ".:/opp_env_inst/veins-5.3/src/veins:/opp_env_inst/inet-4.5.4/src" \
    -l "/opp_env_inst/inet-4.5.4/src/INET" \
    -l "/opp_env_inst/veins-5.3/src/veins" \
    omnetpp.ini

# 注意：如果项目编译后产生了库 (例如在 bin/ 或 src/ 下)，可能也需要用 -l 链接
# 示例：在上面的 docker run 命令中添加 -l "./bin/myprojectlib"
