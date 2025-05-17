"""
Veins 仿真 Celery Worker
此文件设计为独立运行，只负责执行仿真任务
"""
import os
import shutil
import platform
from loguru import logger
from datetime import datetime

from celery import Celery
import docker

from config import config

# 初始化Celery
celery_app = Celery(
    'veins_simulation',
    broker=config.celery_broker_url,
    backend=config.celery_result_backend
)

celery_app.conf.update(
    worker_prefetch_multiplier=config.max_concurrent_simulations,
    task_time_limit=config.simulation_max_timeout + 60,  # 任务超时时间
    task_track_started=True,  # 跟踪任务启动状态
    task_acks_late=True,  # 任务完成后再确认
)

def normalize_path_for_docker(path):
    """处理路径，使其适用于Docker挂载

    在WSL环境下，需要将Windows路径转换为适合Docker使用的格式
    """
    # 标准化路径分隔符
    path = os.path.normpath(path).replace('\\', '/')

    # 检测是否在WSL环境下
    if "microsoft" in platform.uname().release.lower():
        # 如果是相对路径，转为绝对路径
        if not os.path.isabs(path):
            path = os.path.abspath(path)

        # 确保路径正确 (如果是 /mnt/驱动器/路径 格式，转为标准Linux路径)
        if path.startswith('/mnt/'):
            return path
        else:
            # 获取当前工作目录的绝对路径
            cwd = os.getcwd().replace('\\', '/')
            return os.path.join(cwd, path).replace('\\', '/')

    return path

@celery_app.task(name="veins_simulation.run")
def run_simulation(project_dir: str, run_dir: str, veins_config_name: str):
    """
    执行Veins仿真的工作函数

    参数:
        project_dir: 项目文件夹路径
        run_dir: 结果存储路径
        veins_config_name: omnetpp.ini中的配置名

    返回:
        dict: 包含执行结果的字典
    """
    # 标准化路径，处理Windows和Linux路径差异
    project_dir = normalize_path_for_docker(project_dir)
    run_dir = normalize_path_for_docker(run_dir)

    results_dir = os.path.join(project_dir, "results").replace('\\', '/')
    log_path = os.path.join(run_dir, "simulation.log").replace('\\', '/')
    container = None

    logger.info(f"开始仿真任务: project_dir={project_dir}, config={veins_config_name}")

    # 确保结果目录存在
    os.makedirs(run_dir, exist_ok=True)

    try:
        # 创建日志文件
        with open(log_path, 'w') as log_file:
            log_file.write(f"[{datetime.now().isoformat()}] 任务开始执行\n")
            log_file.write(f"项目目录: {project_dir}\n")
            log_file.write(f"结果目录: {run_dir}\n")

            # 使用Docker API创建容器
            client = docker.from_env()

            # 准备挂载点
            mount_path = os.path.abspath(project_dir)

            # Docker容器内工作目录
            container_working_dir = '/simulation/project'



            # 运行Docker容器
            container = client.containers.run(
                "veins-worker",
                command=[
                    "-u", "Cmdenv",
                    "-c", veins_config_name,
                    "-r", "0",
                    "-n", ".:/opp_env_inst/veins-5.3/src/veins:/opp_env_inst/inet-4.5.4/src",
                    "-l", "/opp_env_inst/inet-4.5.4/src/INET",
                    "-l", "/opp_env_inst/veins-5.3/src/veins",
                    "omnetpp.ini"
                ],
                mounts=[{
                    'source': normalize_path_for_docker(project_dir),
                    'target': container_working_dir,
                    'type': 'bind',
                    'read_only': False
                }],
                detach=True,
                remove=True
            )

            # 记录容器ID，方便追踪
            container_id = container.id
            log_file.write(f"[{datetime.now().isoformat()}] 容器启动，ID: {container_id}\n")
            log_file.write(f"挂载卷: {mount_path} → {container_working_dir}\n")

            # 读取输出并写入日志
            for line in container.logs(stream=True):
                log_file.write(line.decode('utf-8', errors='replace'))
                log_file.flush()

            # 等待容器执行完成
            result = container.wait()
            exit_code = result['StatusCode']
            log_file.write(f"\n[{datetime.now().isoformat()}] 容器退出，状态码: {exit_code}\n")
            if exit_code != 0:
                raise RuntimeError(f"返回码为{exit_code}，而不是 0。检查 log。")

            # 移动结果到run目录
            if os.path.exists(results_dir):
                for filename in os.listdir(results_dir):
                    src = os.path.join(results_dir, filename)
                    dst = os.path.join(run_dir, filename)
                    if os.path.isdir(src):
                        shutil.move(src, dst)
                    else:
                        shutil.move(src, dst)

                # 清理results目录
                if os.path.exists(results_dir):
                    shutil.rmtree(results_dir)

            # 返回结果
            return {
                'status': 'completed' if exit_code == 0 else 'failed',
                'exit_code': exit_code,
                'time': datetime.now().isoformat()
            }

    except Exception as e:
        logger.exception(f"仿真执行失败: {str(e)}")
        # 记录错误
        with open(log_path, 'a') as log_file:
            log_file.write(f"\n[{datetime.now().isoformat()}] 任务执行异常: {str(e)}\n")

        # 如果容器仍在运行，尝试停止它
        if container:
            try:
                container.stop()
                log_file.write(f"\n[{datetime.now().isoformat()}] 异常处理：容器已停止\n")
            except:
                pass

        return {
            'status': 'failed',
            'error': str(e),
            'time': datetime.now().isoformat()
        }

# 当作为独立程序运行时，启动worker
if __name__ == '__main__':
    argv = [
        'worker',
        '--loglevel=info',
        '-n=veins-worker@%h'  # 自动添加主机名
    ]
    celery_app.worker_main(argv)
