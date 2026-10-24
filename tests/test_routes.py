# -*- coding: utf-8 -*-
"""Flask 路由集成测试 — 验证关键路由的 HTTP 响应。"""
import os
import sys
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import pytest


class TestPublicRoutes:
    """公开路由测试（未登录）。"""

    def test_root_redirect(self, client):
        """首页未登录重定向到 login。"""
        r = client.get('/')
        assert r.status_code == 302
        assert '/login' in r.headers.get('Location', '')

    def test_login_page(self, client):
        """登录页可访问。"""
        r = client.get('/login')
        assert r.status_code == 200

    def test_admin_redirect(self, client):
        """admin 路由未登录重定向。"""
        r = client.get('/admin/students')
        assert r.status_code in (301, 302)

    def test_api_unauthorized(self, client):
        """API 未登录返回 401。"""
        r = client.get('/api/students')
        assert r.status_code in (301, 302, 401)

        r = client.get('/api/dicts')
        assert r.status_code in (301, 302, 401)

        r = client.get('/api/stats')
        assert r.status_code in (301, 302, 401)


class TestBlueprintRegistration:
    """Blueprint 注册验证。"""

    def test_all_blueprints_registered(self, app):
        """12 个 Blueprint 全部注册。"""
        from app import app as flask_app
        endpoints = {rule.endpoint.split('.')[0] for rule in flask_app.url_map.iter_rules() if '.' in rule.endpoint}
        expected = {'admin', 'api', 'lifecycle', 'insurance', 'export',
                    'leave_cancel', 'mobile', 'user', 'backup', 'public',
                    'district_match', 'archive'}
        assert expected.issubset(endpoints)

    def test_route_count(self, app):
        """路由总数 197（含区划匹配 20 + 档案派遣 22）。"""
        from app import app as flask_app
        count = len(list(flask_app.url_map.iter_rules()))
        assert 180 <= count <= 210


class TestModelsPackageImport:
    """models 包导入兼容性测试。"""

    def test_import_from_models(self):
        """from models import xxx 仍可用（向后兼容）。"""
        from models import get_students, get_db, init_db, create_student
        assert callable(get_students)
        assert callable(get_db)
        assert callable(init_db)
        assert callable(create_student)

    def test_import_submodule(self):
        """直接导入子模块可用。"""
        from models.students import get_students as gs1
        from models.db import get_db as gdb1
        from models.users import create_user as cu1
        assert callable(gs1)
        assert callable(gdb1)
        assert callable(cu1)

    def test_models_package_logger(self):
        """models 包有 logger。"""
        import models
        assert hasattr(models, 'logger')


class TestSchoolTypeCode3Routes:
    """新增学校类别 3 后的页面与接口回归。"""

    @pytest.fixture(autouse=True)
    def _clear_session(self, client):
        """用例结束后清空 session，避免登录态污染其他测试。"""
        yield
        with client.session_transaction() as sess:
            sess.clear()

    @staticmethod
    def _login(client, role):
        with client.session_transaction() as sess:
            sess.clear()
            sess['user_id'] = 1
            sess['username'] = 'phase6_tester'
            sess['role'] = role
            sess['school_visible'] = '*'
            sess['grade_visible'] = '*'
            sess['fields_visible'] = '["*"]'
            sess['csrf_token'] = 'phase6test'

    def test_students_page_shows_school_3(self, client, school_type_3, db_conn):
        """学生列表筛选 school=3 时只显示 3 类学生，下拉含新类别。"""
        from models.students import create_student
        create_student({
            'student_no': 'P6S3001', 'name': '技师学院测试生',
            'id_card': '110101199003073001', 'gender': '男',
            'major': '电气自动化', 'admin_class': '26电气1班',
            'grade': '2026级', 'enrollment_year': 2026, 'school': 3,
        })
        self._login(client, 'admin')
        r = client.get('/admin/students?school=3')
        assert r.status_code == 200
        html = r.get_data(as_text=True)
        assert '技师学院' in html
        assert '技师学院测试生' in html

    def test_insurance_todo_tree_groups_school_3(self, client, school_type_3, db_conn):
        """保险待办树按 school_types 动态分组，3 类学生归入 3 组。"""
        from config import InsuranceStatus
        from models.students import create_student
        create_student({
            'student_no': 'P6S3002', 'name': '待办三类生',
            'id_card': '110101199003073002', 'gender': '女',
            'major': '电气自动化', 'admin_class': '26电气1班',
            'grade': '2026级', 'enrollment_year': 2026, 'school': 3,
        })
        db_conn.execute(
            'UPDATE students SET supervisor_flag = ? WHERE student_no = ?',
            (InsuranceStatus.PENDING_XUEPING, 'P6S3002')
        )
        db_conn.commit()

        self._login(client, 'insurance_officer')
        r = client.get('/api/insurance/todo-tree')
        assert r.status_code == 200
        groups = r.get_json()['students_by_school']
        assert {'1', '2', '3'}.issubset(set(groups))
        assert [s['student_no'] for s in groups['3']] == ['P6S3002']
