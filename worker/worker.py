"""
Veins 仿真 Celery Worker
支持无头和GUI两种模式，GUI模式带UUID验证
"""
import os
import shutil
import platform
from loguru import logger
from datetime import datetime
import time
from enum import Enum

from celery import Celery
import docker
from docker.errors import NotFound as DockerNotFound

from config import config

class RunStatus(str, Enum):
    PENDING = "pending"
    STARTING = "starting"
    RUNNING = "running"
    SUCCESS = "success"
    FAILED = "failed"
    CANCELLING = "cancelling"
    CANCELLED = "cancelled"

# 初始化Celery
celery_app = Celery(
    'veins_simulation',
    broker=config.celery_broker_url,
    backend=config.celery_result_backend
)

celery_app.conf.update(
    worker_prefetch_multiplier=config.max_concurrent_simulations,
    task_time_limit=config.simulation_max_timeout + 60,
    task_track_started=True,
    task_acks_late=True,
    task_reject_on_worker_lost=True,
)

# 内存中维护 task_id -> container_id 的映射
_task_container_mapping = {}

def normalize_path_for_docker(path):
    """处理路径，使其适用于Docker挂载"""
    path = os.path.normpath(path).replace('\\', '/')
    if "microsoft" in platform.uname().release.lower():
        if not os.path.isabs(path):
            path = os.path.abspath(path)
        if path.startswith('/mnt/'):
            return path
        else:
            cwd = os.getcwd().replace('\\', '/')
            return os.path.join(cwd, path).replace('\\', '/')
    return path

def register_task_container(task_id: str, container_id: str):
    """注册task_id和container_id的映射"""
    _task_container_mapping[task_id] = container_id
    logger.debug(f"注册映射: {task_id} -> {container_id}")

def get_container_id(task_id: str) -> str:
    """根据task_id获取container_id"""
    container_id = _task_container_mapping.get(task_id)
    logger.debug(f"查询映射: {task_id} -> {container_id}")
    return container_id

def unregister_task_container(task_id: str):
    """移除task_id和container_id的映射"""
    container_id = _task_container_mapping.pop(task_id, None)
    if container_id:
        logger.debug(f"移除映射: {task_id} -> {container_id}")
    return container_id

def move_results(results_dir, run_dir, log_file=None):
    moved_files = []
    if os.path.exists(results_dir):
        if log_file:
            log_file.write(f"[{datetime.now().isoformat()}] 移动结果文件...\n")
        for filename in os.listdir(results_dir):
            src = os.path.join(results_dir, filename)
            dst = os.path.join(run_dir, filename)
            try:
                shutil.move(src, dst)
                moved_files.append(filename)
                if log_file:
                    log_file.write(f"  移动: {filename}\n")
            except Exception as e:
                if log_file:
                    log_file.write(f"  移动失败 {filename}: {str(e)}\n")
        try:
            shutil.rmtree(results_dir)
        except Exception:
            pass
    return moved_files

@celery_app.task(name="veins_simulation.run", bind=True)
def run_simulation(self, user_id: str, project_id: str, run_id: str, project_dir: str,
                   run_dir: str, config_name: str, gui_mode: bool = False, vnc_uuid: str = None):
    """
    执行Veins仿真的工作函数

    参数:
        user_id: 用户ID
        project_id: 项目ID
        run_id: 运行ID
        project_dir: 项目文件夹路径
        run_dir: 结果存储路径
        config_name: 仿真配置名称
        gui_mode: 是否启用GUI模式
        vnc_uuid: VNC访问UUID（GUI模式时必须提供）
    """
    project_dir = normalize_path_for_docker(project_dir)
    run_dir = normalize_path_for_docker(run_dir)
    results_dir = os.path.join(project_dir, "results").replace('\\', '/')
    log_path = os.path.join(run_dir, "simulation.log").replace('\\', '/')
    container = None
    task_id = self.request.id

    # GUI模式下必须提供vnc_uuid
    if gui_mode and not vnc_uuid:
        error_msg = "GUI模式下必须提供vnc_uuid参数"
        logger.error(error_msg)
        return {
            'status': RunStatus.FAILED,
            'error': error_msg,
        }

    logger.info(f"开始仿真任务: 用户={user_id}, 项目={project_id}, 运行={run_id}, 配置={config_name}, GUI={gui_mode}")
    if gui_mode:
        logger.info(f"VNC UUID: {vnc_uuid}")

    os.makedirs(run_dir, exist_ok=True)

    # 更新任务状态：开始启动
    self.update_state(
        state='PROGRESS',
        meta={
            'status': RunStatus.STARTING,
            'vnc_uuid': vnc_uuid if gui_mode else None,
            'vnc_url': None
        }
    )

    try:
        with open(log_path, 'w', encoding='utf-8') as log_file:
            log_file.write(f"[{datetime.now().isoformat()}] 仿真任务开始\n")
            log_file.write(f"任务ID: {task_id}\n")
            log_file.write(f"用户ID: {user_id}\n")
            log_file.write(f"项目ID: {project_id}\n")
            log_file.write(f"运行ID: {run_id}\n")
            log_file.write(f"配置名称: {config_name}\n")
            log_file.write(f"GUI模式: {gui_mode}\n")
            if gui_mode and vnc_uuid:
                log_file.write(f"VNC UUID: {vnc_uuid}\n")
            log_file.write("\n")

            client = docker.from_env()
            container_working_dir = '/simulation/project'
            ui_mode = "Qtenv" if gui_mode else "Cmdenv"

            command = [
                "--config-name", config_name,
                "-u", ui_mode,
                "-c", config_name,
                "-r", "0",
                "-n", ".:/opp_env_inst/veins-5.3/src/veins:/opp_env_inst/inet-4.5.4/src",
                "-l", "/opp_env_inst/inet-4.5.4/src/INET",
                "-l", "/opp_env_inst/veins-5.3/src/veins",
                "omnetpp.ini"
            ]

            if gui_mode:
                command = ["--gui-mode", "--vnc-uuid", vnc_uuid] + command

            log_file.write(f"Docker命令: {' '.join(command)}\n\n")

            container = client.containers.run(
                "veins-worker-test-gui",
                command=command,
                mounts=[{
                    'source': normalize_path_for_docker(project_dir),
                    'target': container_working_dir,
                    'type': 'bind',
                    'read_only': False
                }],
                ports={'8080/tcp': None} if gui_mode else None,
                detach=True,
                remove=not gui_mode,
                stdin_open=gui_mode,
                tty=gui_mode,
                name=f"veins-sim-u{user_id}-p{project_id}-r{run_id}-{task_id[:8]}"
            )

            container_id = container.id
            log_file.write(f"[{datetime.now().isoformat()}] 容器启动成功\n")
            log_file.write(f"容器ID: {container_id}\n")
            register_task_container(task_id, container_id)

            # GUI模式：生命周期与容器一致
            if gui_mode:
                start_time = time.time()
                while True:
                    container.reload()
                    status = container.status

                    # 获取VNC端口和URL
                    port_mappings = container.ports
                    if '8080/tcp' in port_mappings and port_mappings['8080/tcp']:
                        host_port = port_mappings['8080/tcp'][0]['HostPort']
                        vnc_url = f"http://localhost:{host_port}/vnc/{vnc_uuid}/vnc.html?path=/vnc/{vnc_uuid}/websockify"
                    else:
                        vnc_url = None

                    # 定期更新Celery状态
                    self.update_state(
                        state="PROGRESS",
                        meta={
                            "status": RunStatus.RUNNING,
                            "vnc_uuid": vnc_uuid,
                            "vnc_url": vnc_url
                        }
                    )

                    # 判断容器是否退出
                    if status == 'exited':
                        exit_code = container.attrs['State']['ExitCode']
                        log_file.write(f"[{datetime.now().isoformat()}] 容器已退出，退出代码: {exit_code}\n")
                        break

                    # 超时保护
                    if time.time() - start_time > config.simulation_max_timeout:
                        log_file.write(f"[{datetime.now().isoformat()}] 仿真超时，强制停止容器\n")
                        container.stop(timeout=30)
                        exit_code = 1
                        break

                    time.sleep(3)

                # 记录所有日志
                try:
                    container_logs = container.logs().decode('utf-8', errors='replace')
                    log_file.write(f"[{datetime.now().isoformat()}] 容器日志:\n{container_logs}\n")
                except Exception:
                    pass

                # 复制结果
                move_results(results_dir, run_dir, log_file)

                unregister_task_container(task_id)

                if exit_code == 0:
                    return {
                        'status': RunStatus.SUCCESS,
                        'vnc_url': vnc_url,
                        'exit_code': exit_code,
                    }
                else:
                    return {
                        'status': RunStatus.FAILED,
                        'vnc_url': vnc_url,
                        'exit_code': exit_code,
                    }
            else:
                # 无头模式：等待执行完成
                log_file.write(f"[{datetime.now().isoformat()}] 无头模式，等待执行完成...\n")
                log_file.flush()

                for line in container.logs(stream=True):
                    decoded_line = line.decode('utf-8', errors='replace')
                    log_file.write(decoded_line)
                    log_file.flush()

                result = container.wait()
                exit_code = result['StatusCode']
                log_file.write(f"\n[{datetime.now().isoformat()}] 容器执行完成，退出代码: {exit_code}\n")

                unregister_task_container(task_id)

                if exit_code != 0:
                    raise RuntimeError(f"仿真失败，退出代码: {exit_code}")

                move_results(results_dir, run_dir, log_file)
                log_file.write(f"[{datetime.now().isoformat()}] 无头模式仿真完成\n")

                return {
                    'status': RunStatus.SUCCESS,
                    'exit_code': exit_code,
                }

    except Exception as e:
        logger.exception(f"仿真执行失败: {str(e)}")
        unregister_task_container(task_id)
        try:
            with open(log_path, 'a', encoding='utf-8') as log_file:
                log_file.write(f"\n[{datetime.now().isoformat()}] 任务执行异常: {str(e)}\n")
        except:
            pass
        if container:
            try:
                container.stop(timeout=30)
                if gui_mode:
                    container.remove()
                logger.info(f"异常处理：容器 {container.id} 已清理")
            except Exception as cleanup_error:
                logger.error(f"清理容器失败: {str(cleanup_error)}")
        return {
            'status': RunStatus.FAILED,
            'error': str(e),
        }

@celery_app.task(name="veins_simulation.stop")
def stop_simulation(task_id: str):
    """
    停止仿真任务的专门task

    参数:
        task_id: 要停止的任务ID
    """
    logger.info(f"开始停止仿真任务: {task_id}")

    try:
        celery_app.control.revoke(task_id, terminate=True)
        logger.info(f"任务 {task_id} 已被revoke")
        container_id = get_container_id(task_id)
        if container_id:
            try:
                client = docker.from_env()
                container = client.containers.get(container_id)
                container.stop(timeout=30)
                container.remove()
                logger.info(f"容器 {container_id} 已停止并移除")
                unregister_task_container(task_id)
                return {
                    'status': RunStatus.CANCELLED,
                    'message': '任务和容器已成功停止'
                }
            except DockerNotFound:
                logger.warning(f"容器 {container_id} 未找到，可能已被清理")
                unregister_task_container(task_id)
                return {
                    'status': RunStatus.CANCELLED,
                    'message': '任务已停止，容器未找到'
                }
            except Exception as e:
                logger.error(f"停止容器失败: {str(e)}")
                return {
                    'status': RunStatus.FAILED,
                    'error': f'停止容器失败: {str(e)}'
                }
        else:
            logger.warning(f"未找到任务 {task_id} 对应的容器ID，只进行了任务revoke")
            return {
                'status': RunStatus.CANCELLED,
                'message': '任务已停止，但未找到对应容器'
            }
    except Exception as e:
        logger.exception(f"停止任务失败: {str(e)}")
        return {
            'status': RunStatus.FAILED,
            'error': str(e)
        }

if __name__ == '__main__':
    argv = [
        'worker',
        '--loglevel=info',
        '-n=veins-worker@%h'
    ]
    celery_app.worker_main(argv)
