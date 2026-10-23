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
        """10 个 Blueprint 全部注册。"""
        from app import app as flask_app
        endpoints = {rule.endpoint.split('.')[0] for rule in flask_app.url_map.iter_rules() if '.' in rule.endpoint}
        expected = {'admin', 'api', 'lifecycle', 'insurance', 'export',
                    'leave_cancel', 'mobile', 'user', 'backup', 'public'}
        assert expected.issubset(endpoints)

    def test_route_count(self, app):
        """路由总数约 148（144 Blueprint + 4 app.py）。"""
        from app import app as flask_app
        count = len(list(flask_app.url_map.iter_rules()))
        assert 145 <= count <= 155


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
