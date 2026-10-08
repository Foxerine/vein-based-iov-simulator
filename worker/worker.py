"""
Veins 仿真 Celery Worker
支持无头和GUI两种模式，GUI模式带随机会话令牌验证
"""
import os
import platform
import threading
from loguru import logger
from datetime import datetime
import time
from enum import Enum

import requests
from celery import Celery
from celery.exceptions import Retry
from celery.signals import worker_ready
from celery.worker.control import control_command
import docker
from docker.errors import APIError, DockerException, NotFound as DockerNotFound

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
    worker_pool=config.worker_pool,
    worker_concurrency=config.max_concurrent_simulations,
    # 仿真是长任务：每个执行槽只预取 1 条，其余任务留在 broker 里，多 worker 部署时可被空闲节点领取
    worker_prefetch_multiplier=1,
    task_time_limit=config.simulation_max_timeout + 60,
    task_track_started=True,
    # 至多一次语义：任务开始执行即确认。仿真可运行数小时，若延迟确认，Redis broker 会在
    # visibility_timeout（默认 1 小时）后把仍在运行的任务重新投递，造成重复执行。
    # worker 中途丢失的任务由 API 侧的状态对账判定为失败（见 utils/reconcile.py）。
    task_acks_late=False,
)

SIM_IMAGE = "veins-worker-test-gui"
LABEL_WORKER = "veins.worker"
LABEL_TASK = "veins.task_id"
LABEL_RUN = "veins.run_id"

# 容器启动前的基础设施错误（Docker 守护进程暂不可达、服务端 5xx）按指数退避重试；
# 仿真本身的失败不重试——固定随机种子下它必然复现。
INFRA_MAX_RETRIES = 3
INFRA_RETRY_BASE_S = 10

# 内存中维护 task_id -> container_id 的映射
_task_container_mapping = {}

def mask_token(token):
    """日志中只保留会话令牌前 8 位。"""
    return f"{token[:8]}****" if token else "None"

def container_resource_kwargs() -> dict:
    """由配置生成仿真容器的资源配额参数，防止单个用户的任务耗尽宿主机资源。"""
    kwargs = {}
    if config.container_cpu_limit and config.container_cpu_limit > 0:
        kwargs["nano_cpus"] = int(config.container_cpu_limit * 1_000_000_000)
    if config.container_memory_limit:
        kwargs["mem_limit"] = config.container_memory_limit
        # 与 mem_limit 相同即禁止在内存上限之外再使用 swap
        kwargs["memswap_limit"] = config.container_memory_limit
    if config.container_pids_limit and config.container_pids_limit > 0:
        kwargs["pids_limit"] = config.container_pids_limit
    return kwargs

def container_isolation_kwargs(gui_mode: bool) -> dict:
    """仿真容器的隔离参数。用户的项目会在容器内编译并运行，因此容器内的代码不可信：
    一律禁止通过 setuid 等方式提权；无头任务不接入任何网络，无法访问宿主机上的 Redis、后端
    或其他容器。GUI 任务需要发布 noVNC 端口，仍接入默认网桥（由会话令牌保护）。"""
    kwargs = {"security_opt": ["no-new-privileges:true"]}
    if config.container_isolate_network and not gui_mode:
        kwargs["network_mode"] = "none"
    return kwargs

def is_transient_docker_error(e: Exception) -> bool:
    """容器启动前的错误是否值得重试：连接失败与服务端 5xx 是暂时性的，
    镜像不存在、参数错误等客户端 4xx 是永久性的。"""
    if isinstance(e, APIError):
        return e.is_server_error()
    return isinstance(e, (DockerException, requests.exceptions.ConnectionError))

def remove_container(container):
    try:
        container.remove(force=True)
    except DockerNotFound:
        pass
    except Exception as e:
        logger.warning(f"移除容器 {container.id[:12]} 失败: {e}")

def remove_stale_container(client, name: str):
    """同名容器只可能是同一任务此前中断时遗留的，启动前清除以免名称冲突。"""
    for c in client.containers.list(all=True, filters={"name": name}):
        if c.name == name:
            logger.warning(f"清除遗留的同名容器 {name}")
            remove_container(c)

def find_task_containers(client, task_id: str) -> list:
    """先查进程内映射；映射只在执行该任务的 worker 进程里有，且 worker 重启后丢失，
    所以再按容器标签查找。"""
    container_id = get_container_id(task_id)
    if container_id:
        try:
            return [client.containers.get(container_id)]
        except DockerNotFound:
            pass
    return client.containers.list(all=True, filters={"label": f"{LABEL_TASK}={task_id}"})

def normalize_path_for_docker(path):
    """处理路径，使其适用于Docker挂载"""
    path = os.path.normpath(path)
    if not os.path.isabs(path):
        # Docker bind mount 要求绝对路径；原生 Windows 下也需要绝对化
        path = os.path.abspath(path)
    path = path.replace('\\', '/')
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

@celery_app.task(name="veins_simulation.run", bind=True)
def run_simulation(self, user_id: str, project_id: str, run_id: str, project_dir: str,
                   run_dir: str, config_name: str, gui_mode: bool = False, vnc_uuid: str = None,
                   seed_set: int = 0):
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
        vnc_uuid: VNC会话令牌（GUI模式时必须提供）
    """
    project_dir = normalize_path_for_docker(project_dir)
    run_dir = normalize_path_for_docker(run_dir)
    log_path = os.path.join(run_dir, "simulation.log").replace('\\', '/')
    container = None
    task_id = self.request.id
    attempt = self.request.retries or 0

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
        logger.info(f"VNC 令牌: {mask_token(vnc_uuid)}")

    os.makedirs(run_dir, exist_ok=True)

    # started_at 供 API 侧状态对账计算运行时长上限
    started_at = time.time()
    self.update_state(
        state='PROGRESS',
        meta={
            'status': RunStatus.STARTING,
            'vnc_uuid': vnc_uuid if gui_mode else None,
            'vnc_url': None,
            'started_at': started_at,
        }
    )

    try:
        # 重试时追加写入，保留前几次启动失败的原因
        with open(log_path, 'a' if attempt else 'w', encoding='utf-8') as log_file:
            retry_note = f"（第 {attempt + 1} 次尝试）" if attempt else ""
            log_file.write(f"[{datetime.now().isoformat()}] 仿真任务开始{retry_note}\n")
            log_file.write(f"任务ID: {task_id}\n")
            log_file.write(f"用户ID: {user_id}\n")
            log_file.write(f"项目ID: {project_id}\n")
            log_file.write(f"运行ID: {run_id}\n")
            log_file.write(f"配置名称: {config_name}\n")
            log_file.write(f"GUI模式: {gui_mode}\n")
            if gui_mode and vnc_uuid:
                log_file.write(f"VNC 令牌: {mask_token(vnc_uuid)}\n")
            log_file.write("\n")

            container_working_dir = '/simulation/project'
            ui_mode = "Qtenv" if gui_mode else "Cmdenv"
            # 结果直接写入本次运行专属目录。若写到项目共享的 results 目录，同一项目的多次运行
            # 并发时会互相覆盖：各次运行的输出文件同名（如 WithBeaconing-#0.sca，不含 seed-set）。
            run_rel = os.path.relpath(run_dir, project_dir).replace(os.sep, '/')
            if run_rel.startswith('..'):
                raise ValueError(f"运行目录不在项目目录内: {run_dir}")
            result_dir_in_container = f"{container_working_dir}/{run_rel}"

            command = [
                "--config-name", config_name,
                "-u", ui_mode,
                "-c", config_name,
                "-r", "0",
                f"--seed-set={int(seed_set)}",
                f"--result-dir={result_dir_in_container}",
                "-n", ".:/opp_env_inst/veins-5.3/src/veins:/opp_env_inst/inet-4.5.4/src",
                "-l", "/opp_env_inst/inet-4.5.4/src/INET",
                "-l", "/opp_env_inst/veins-5.3/src/veins",
                "omnetpp.ini"
            ]

            if gui_mode:
                command = ["--gui-mode", "--vnc-uuid", vnc_uuid] + command

            printable = [mask_token(a) if a == vnc_uuid else a for a in command]
            log_file.write(f"Docker命令: {' '.join(printable)}\n")
            log_file.write(f"资源配额: {container_resource_kwargs() or '不限制'}\n\n")

            container_name = f"veins-sim-u{user_id}-p{project_id}-r{run_id}-{task_id[:8]}"
            try:
                client = docker.from_env()
                remove_stale_container(client, container_name)
                container = client.containers.run(
                    SIM_IMAGE,
                    command=command,
                    mounts=[{
                        'source': normalize_path_for_docker(project_dir),
                        'target': container_working_dir,
                        'type': 'bind',
                        'read_only': False
                    }],
                    ports={'8080/tcp': None} if gui_mode else None,
                    detach=True,
                    # 由本任务在读取退出码与 OOMKilled 标志后显式移除
                    remove=False,
                    stdin_open=gui_mode,
                    tty=gui_mode,
                    name=container_name,
                    labels={
                        LABEL_WORKER: self.request.hostname or "",
                        LABEL_TASK: task_id,
                        LABEL_RUN: str(run_id),
                    },
                    **container_resource_kwargs(),
                    **container_isolation_kwargs(gui_mode),
                )
            except Exception as e:
                if is_transient_docker_error(e) and attempt < INFRA_MAX_RETRIES:
                    delay = INFRA_RETRY_BASE_S * (2 ** attempt)
                    log_file.write(f"[{datetime.now().isoformat()}] 启动容器失败（{e}），"
                                   f"{delay} 秒后重试（{attempt + 1}/{INFRA_MAX_RETRIES}）\n")
                    logger.warning(f"启动容器失败，{delay} 秒后重试: {e}")
                    raise self.retry(exc=e, countdown=delay, max_retries=INFRA_MAX_RETRIES)
                raise

            container_id = container.id
            log_file.write(f"[{datetime.now().isoformat()}] 容器启动成功\n")
            log_file.write(f"容器ID: {container_id}\n")
            register_task_container(task_id, container_id)

            # GUI模式：生命周期与容器一致
            if gui_mode:
                start_time = time.time()
                vnc_url = None
                while True:
                    container.reload()
                    status = container.status

                    # 获取VNC端口和URL
                    port_mappings = container.ports
                    if '8080/tcp' in port_mappings and port_mappings['8080/tcp']:
                        host_port = port_mappings['8080/tcp'][0]['HostPort']
                        vnc_url = (f"http://{config.vnc_public_host}:{host_port}"
                                   f"/vnc/{vnc_uuid}/vnc.html?path=/vnc/{vnc_uuid}/websockify")

                    # 定期更新Celery状态
                    self.update_state(
                        state="PROGRESS",
                        meta={
                            "status": RunStatus.RUNNING,
                            "vnc_uuid": vnc_uuid,
                            "vnc_url": vnc_url,
                            "started_at": started_at,
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

                oom_killed = container.attrs.get('State', {}).get('OOMKilled', False)
                # 容器停止后 noVNC 会话随之失效；此处同时回收容器
                remove_container(container)
                unregister_task_container(task_id)

                if exit_code == 0:
                    return {
                        'status': RunStatus.SUCCESS,
                        'vnc_url': vnc_url,
                        'exit_code': exit_code,
                    }
                else:
                    result = {
                        'status': RunStatus.FAILED,
                        'vnc_url': vnc_url,
                        'exit_code': exit_code,
                    }
                    if oom_killed:
                        result['error'] = f"仿真容器超出内存上限 {config.container_memory_limit}，被系统终止（OOM）"
                    return result
            else:
                # 无头模式：后台线程持续写入容器输出，主线程带超时等待容器退出
                self.update_state(
                    state="PROGRESS",
                    meta={
                        "status": RunStatus.RUNNING,
                        "vnc_uuid": None,
                        "vnc_url": None,
                        "started_at": started_at,
                    }
                )
                log_file.write(f"[{datetime.now().isoformat()}] 无头模式，等待执行完成...\n")
                log_file.flush()

                def pump_logs():
                    try:
                        for line in container.logs(stream=True, follow=True):
                            log_file.write(line.decode('utf-8', errors='replace'))
                            log_file.flush()
                    except Exception:
                        pass

                pump = threading.Thread(target=pump_logs, daemon=True)
                pump.start()

                try:
                    exit_code = container.wait(timeout=config.simulation_max_timeout)['StatusCode']
                except requests.exceptions.RequestException:
                    # threads 池不执行 Celery 的 task_time_limit，运行时长上限在此保证
                    container.stop(timeout=30)
                    pump.join(timeout=30)
                    raise RuntimeError(f"仿真超过最大运行时长 {config.simulation_max_timeout} 秒，已强制停止")
                pump.join(timeout=30)
                log_file.write(f"\n[{datetime.now().isoformat()}] 容器执行完成，退出代码: {exit_code}\n")

                container.reload()
                oom_killed = container.attrs.get('State', {}).get('OOMKilled', False)
                remove_container(container)
                unregister_task_container(task_id)

                if oom_killed:
                    raise RuntimeError(f"仿真容器超出内存上限 {config.container_memory_limit}，被系统终止（OOM）")
                if exit_code != 0:
                    raise RuntimeError(f"仿真失败，退出代码: {exit_code}")

                log_file.write(f"[{datetime.now().isoformat()}] 无头模式仿真完成\n")

                return {
                    'status': RunStatus.SUCCESS,
                    'exit_code': exit_code,
                }

    except Retry:
        raise
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
            except Exception as cleanup_error:
                logger.error(f"停止容器失败: {str(cleanup_error)}")
            remove_container(container)
            logger.info(f"异常处理：容器 {container.id} 已清理")
        return {
            'status': RunStatus.FAILED,
            'error': str(e),
        }

@control_command(args=[("task_id", str)], signature="<task_id>")
def stop_sim_containers(state, task_id):
    """远程控制命令：停止并移除某个任务的仿真容器。

    由 worker 的控制线程立即执行，不占用也不等待任务执行槽。若作为普通任务投递，
    停止请求会排在仿真任务之后；执行槽被长仿真占满时要等其中一个结束才轮得到，
    而那恰恰是用户最需要取消的时候（GUI 会话也只能靠取消结束）。
    """
    try:
        client = docker.from_env()
        containers = find_task_containers(client, task_id)
    except Exception as e:
        logger.warning(f"停止任务 {task_id} 的容器失败: {e}")
        return {"error": str(e)}
    for container in containers:
        try:
            # 被取消的仿真结果直接丢弃，无需优雅退出；控制线程串行处理多个取消请求，等待越短越好
            container.stop(timeout=3)
        except DockerNotFound:
            pass
        except Exception as e:
            logger.warning(f"停止容器 {container.id[:12]} 失败: {e}")
        remove_container(container)
        logger.info(f"已停止并移除任务 {task_id} 的容器 {container.id[:12]}")
    unregister_task_container(task_id)
    return {"stopped": len(containers)}

@worker_ready.connect
def cleanup_orphan_containers(sender=None, **kwargs):
    """worker 启动时清理本节点上一次运行遗留的仿真容器。

    任务在开始执行时即被确认，worker 重启后不会再收到此前已开始的任务，
    因此带有本节点名标签的容器必然是孤儿。只由消费仿真队列的 worker 执行
    （analysis worker 不参与），且只处理带本节点名标签的容器。
    """
    try:
        queues = {q.name for q in sender.task_consumer.queues}
    except Exception:
        return
    if celery_app.conf.task_default_queue not in queues:
        return

    try:
        client = docker.from_env()
        orphans = client.containers.list(all=True, filters={"label": f"{LABEL_WORKER}={sender.hostname}"})
    except Exception as e:
        logger.warning(f"启动时检查孤儿容器失败: {e}")
        return
    for container in orphans:
        logger.warning(f"清理上次运行遗留的仿真容器: {container.name}")
        remove_container(container)

@celery_app.task(name="veins_simulation.analyze")
def analyze_results(run_dir: str) -> dict:
    """解析仿真结果（CPU密集），由专用 analysis 队列的独立进程worker执行。

    结果写入 run_dir/analysis.json 作为缓存，API 下次直接读缓存返回。
    """
    import json

    from utils.result_analysis import analyze_run_dir

    result = analyze_run_dir(run_dir)
    cache_path = os.path.join(run_dir, "analysis.json")
    try:
        with open(cache_path, "w", encoding="utf-8") as f:
            json.dump(result, f, ensure_ascii=False)
    except OSError as e:
        logger.warning(f"分析结果缓存写入失败（不影响本次返回）: {e}")
    return result

if __name__ == '__main__':
    argv = [
        'worker',
        '--loglevel=info',
        '-n=veins-worker@%h'
    ]
    celery_app.worker_main(argv)
