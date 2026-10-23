# -*- coding: utf-8 -*-
"""auth.py 权限和认证测试。"""
import os
import sys
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import pytest
from flask import session
from auth import (
    hash_password, verify_password, authenticate_user,
    create_session, clear_session, check_field_permission,
    check_school_permission, _is_insurance_role,
    apply_permission_filters, get_visible_fields,
    effective_school_visible,
)


class TestPasswordHashing:
    """密码哈希测试。"""

    def test_hash_and_verify(self):
        """哈希密码后验证。"""
        password = 'test_password_123'
        hashed = hash_password(password)
        assert hashed != password
        assert verify_password(hashed, password) is True

    def test_wrong_password(self):
        """错误密码验证失败。"""
        hashed = hash_password('correct_password')
        assert verify_password(hashed, 'wrong_password') is False

    def test_hash_uniqueness(self):
        """相同密码两次哈希结果不同（盐值）。"""
        h1 = hash_password('same_password')
        h2 = hash_password('same_password')
        assert h1 != h2


class TestSessionManagement:
    """会话管理测试。"""

    def test_create_and_clear_session(self, app):
        """创建并清除 session。"""
        with app.test_request_context():
            user = {
                'id': 1, 'username': 'testadmin', 'role': 'admin',
                'display_name': '管理员', 'school_visible': '1,2',
                'fields_visible': '["*"]', 'grade_visible': '*',
            }
            create_session(user)
            assert session.get('user_id') == 1
            assert session.get('username') == 'testadmin'
            assert session.get('role') == 'admin'
            assert session.get('enrollment_year') is not None  # admin 设置当前年份

            clear_session()
            assert 'user_id' not in session
            assert 'username' not in session


class TestFieldPermission:
    """字段权限测试。"""

    def test_admin_all_fields(self, app):
        """admin 角色可访问所有字段。"""
        with app.test_request_context():
            session['role'] = 'admin'
            assert check_field_permission('id_card') is True
            assert check_field_permission('student_phone') is True

    def test_non_admin_wildcard(self, app):
        """非 admin 角色带通配符可访问所有字段。"""
        with app.test_request_context():
            session['role'] = 'attendance'
            session['fields_visible'] = '["*"]'
            assert check_field_permission('id_card') is True

    def test_non_admin_restricted(self, app):
        """非 admin 角色仅可访问列表中的字段。"""
        with app.test_request_context():
            session['role'] = 'attendance'
            session['fields_visible'] = '["学号", "学生姓名"]'
            assert check_field_permission('学号') is True
            assert check_field_permission('学生姓名') is True
            assert check_field_permission('学生身份证号') is False

    def test_get_visible_fields_admin(self, app):
        """admin 获取可见字段列表。"""
        with app.test_request_context():
            session['role'] = 'admin'
            fields = get_visible_fields()
            assert fields == ['*']


class TestSchoolPermission:
    """学校权限测试。"""

    def test_admin_all_schools(self, app):
        with app.test_request_context():
            session['role'] = 'admin'
            assert check_school_permission(1) is True
            assert check_school_permission(2) is True

    def test_restricted_schools(self, app):
        with app.test_request_context():
            session['role'] = 'attendance'
            session['school_visible'] = '1'
            assert check_school_permission(1) is True
            assert check_school_permission(2) is False

    def test_insurance_role_all(self, app):
        """保险角色可访问所有学校。"""
        with app.test_request_context():
            session['role'] = 'finance_chief'
            assert check_school_permission(1) is True
            assert check_school_permission(2) is True
            assert _is_insurance_role() is True

    def test_new_school_3_denied_without_grant(self, app):
        """school_visible 不含 3 时无权访问 3 的数据。"""
        with app.test_request_context():
            session['role'] = 'attendance'
            session['school_visible'] = '1,2'
            assert check_school_permission(3) is False

    def test_new_school_3_allowed_after_grant(self, app):
        """授予 3 后可访问 3 的数据。"""
        with app.test_request_context():
            session['role'] = 'attendance'
            session['school_visible'] = '1,2,3'
            assert check_school_permission(3) is True


class TestApplyPermissionFilters:
    """权限过滤测试。"""

    def test_admin_no_filter_added(self, app):
        """admin 不追加过滤条件。"""
        with app.test_request_context():
            session['role'] = 'admin'
            filters = {'major': '计算机'}
            result = apply_permission_filters(filters)
            assert result == filters

    def test_non_admin_school_added(self, app):
        """非 admin 自动追加 school 过滤。"""
        with app.test_request_context():
            session['role'] = 'attendance'
            session['school_visible'] = '1'
            session['grade_visible'] = '*'
            result = apply_permission_filters({})
            assert 'school' in result
            assert result['school'] == 1

    def test_requested_school_3_blocked_when_not_visible(self, app):
        """越权请求 school=3 时取交集为空，返回空集。"""
        with app.test_request_context():
            session['role'] = 'attendance'
            session['school_visible'] = '1,2'
            session['grade_visible'] = '*'
            result = apply_permission_filters({'school': '3'})
            assert result['school'] == [-1]

    def test_requested_school_3_allowed_when_visible(self, app):
        """school_visible 含 3 时保留请求的 3。"""
        with app.test_request_context():
            session['role'] = 'attendance'
            session['school_visible'] = '1,2,3'
            session['grade_visible'] = '*'
            result = apply_permission_filters({'school': '3'})
            assert result['school'] == 3


class TestEffectiveSchoolVisible:
    """下拉学校范围计算测试。"""

    def test_admin_passthrough(self, app):
        with app.test_request_context():
            session['role'] = 'admin'
            assert effective_school_visible('3') == '3'
            assert effective_school_visible() is None

    def test_granted_school_3(self, app):
        with app.test_request_context():
            session['role'] = 'attendance'
            session['school_visible'] = '1,2,3'
            assert effective_school_visible('3') == '3'
            assert effective_school_visible() == '1,2,3'

    def test_requested_school_3_falls_back(self, app):
        with app.test_request_context():
            session['role'] = 'attendance'
            session['school_visible'] = '1,2'
            assert effective_school_visible('3') == '1,2'

    def test_empty_visibility_unrestricted(self, app):
        with app.test_request_context():
            session['role'] = 'attendance'
            session['school_visible'] = ''
            assert effective_school_visible('3') == '3'
