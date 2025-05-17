1. 在wsl或者其他linux环境里安装python3.12
2. 构建docker镜像
2. 创建venv，记得和主项目的venv文件夹分开
3. pip install -r ./requirements_worker.txt
4. celery -A worker.worker.celery_app worker --loglevel=info -n veins-worker@%h