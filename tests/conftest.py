# -*- coding: utf-8 -*-
"""pytest 配置文件 — 测试基础设施。
提供 Flask app、测试客户端、初始化数据库等 fixture。"""
import os
import sys
import tempfile
import shutil

import pytest

# 设置临时数据目录（在导入 config 之前）
_tmp_dir = tempfile.mkdtemp(prefix="sms_test_")
os.environ['USER_DATA_DIR'] = _tmp_dir

# 将项目根目录加入 sys.path
ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

# 创建必要的子目录
for sub in ['data', 'uploads', 'logs', 'databackup', 'static', 'static' + os.sep + 'photos']:
    os.makedirs(os.path.join(_tmp_dir, sub), exist_ok=True)


@pytest.fixture(scope="session")
def app():
    """创建 Flask 应用实例（session 级，所有测试共享）。"""
    from app import app as _app
    _app.config.update({
        'TESTING': True,
        'WTF_CSRF_ENABLED': False,  # 测试时禁用 CSRF
    })
    return _app


@pytest.fixture(scope="session")
def client(app):
    """Flask 测试客户端。"""
    return app.test_client()


@pytest.fixture(scope="session")
def db_conn():
    """已初始化的数据库连接（session 级）。
    使用临时文件数据库，测试结束后自动清理。"""
    from models.db import init_db, get_db
    from app import app
    with app.app_context():
        init_db()
        conn = get_db()
        yield conn


@pytest.fixture(autouse=True)
def _cleanup_db(db_conn):
    """每个测试后清理数据（保留表结构）。"""
    yield
    # 测试后清理所有业务表数据（保留 schema_version）
    tables_to_clean = [
        'attendance_records', 'attendance_dates', 'update_logs',
        'export_history', 'export_field_mappings', 'export_templates',
        'dicts', 'students', 'users',
        # 区划匹配结果表（幂等清空；字典历史映射种子不动）
        'dm_match_result', 'dm_match_batch',
    ]
    try:
        for table in tables_to_clean:
            db_conn.execute(f'DELETE FROM {table}')
        db_conn.commit()
    except Exception:
        pass


@pytest.fixture
def school_type_3(app, db_conn):
    """临时新增学校类别 3（技师学院），测试结束清理，避免影响其他用例。"""
    from models.school_types import create_school_type
    from models.db import get_db

    create_school_type(3, '技师学院')
    yield 3
    conn = get_db()
    try:
        conn.execute('DELETE FROM school_types WHERE code = 3')
        conn.commit()
    finally:
        conn.close()


@pytest.fixture(scope="session", autouse=True)
def _final_cleanup():
    """session 结束时清理临时目录。"""
    yield
    try:
        shutil.rmtree(_tmp_dir)
    except Exception:
        pass
