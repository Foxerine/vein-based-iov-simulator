import pytest

from models import User, Project, Run
from models.run import RunStatus

@pytest.mark.asyncio
async def test_project_run_relationship(session):
    """测试项目与运行之间的关系"""
    # 创建用户和项目
    user = User(email="run_rel@example.com", hashed_password="password123")
    await user.save(session)

    project = Project(name="运行关联项目", user_id=user.id)
    await project.save(session)

    # 为项目创建多个运行记录
    run1 = Run(notes="关联运行1", project_id=project.id, status=RunStatus.SUCCESS)
    run2 = Run(notes="关联运行2", project_id=project.id, status=RunStatus.FAILED)

    await Run.add(session, [run1, run2])

    # 查询项目并加载其运行记录
    await session.refresh(project)
    project_with_runs = await Project.get(session, Project.id == project.id, load=Project.runs)

    # 验证关系
    assert len(project_with_runs.runs) == 2
    run_notes = {r.notes for r in project_with_runs.runs}
    run_statuses = {r.status for r in project_with_runs.runs}

    assert "关联运行1" in run_notes
    assert "关联运行2" in run_notes
    assert RunStatus.SUCCESS in run_statuses
    assert RunStatus.FAILED in run_statuses

@pytest.mark.asyncio
async def test_cascade_delete(session):
    """测试级联删除"""
    # 创建用户
    user = User(email="cascade@example.com", hashed_password="password123")
    await user.save(session)
    await session.refresh(user)

    # 创建项目
    project = Project(name="级联删除项目", user_id=user.id)
    await project.save(session)

    # 创建运行记录
    run = Run(notes="级联删除运行", project_id=project.id)
    await run.save(session)

    # 记录ID
    await session.refresh(user)
    user_id = user.id
    await session.refresh(project)
    project_id = project.id
    run_id = run.id

    # 删除用户，应级联删除项目和运行记录
    await User.delete(session, user)

    # 验证项目和运行记录也被删除
    deleted_project = await session.get(Project, project_id)
    deleted_run = await session.get(Run, run_id)

    assert deleted_project is None
    assert deleted_run is None
