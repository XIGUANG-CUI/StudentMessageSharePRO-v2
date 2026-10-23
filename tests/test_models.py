# -*- coding: utf-8 -*-
"""models 层 CRUD 测试 — 用户和学生核心操作。"""
import os
import sys
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import pytest
from models.users import create_user, get_user_by_username, get_user_by_id, get_all_users, update_user, toggle_user_status
from models.students import (
    create_student, get_student_by_id_card, get_student_by_student_no,
    get_students, update_student, soft_delete_student,
    get_deleted_students, restore_student, _build_student_where_clause,
)
from models.dicts import add_dict, get_dicts, get_all_grades, get_enrollment_years


class TestUserCRUD:
    """用户 CRUD 测试。"""

    def test_create_and_get_user(self, app, db_conn):
        """创建用户并通过用户名查询。"""
        create_user('testadmin', 'hash_placeholder', 'admin', '测试管理员',
                    school_visible='1,2', fields_visible='["*"]', grade_visible='*')
        user = get_user_by_username('testadmin')
        assert user is not None
        assert user['username'] == 'testadmin'
        assert user['role'] == 'admin'
        assert user['display_name'] == '测试管理员'

    def test_get_user_by_id(self, app, db_conn):
        """通过 ID 查询用户。"""
        create_user('testuser2', 'hash2', 'attendance', '考勤员')
        user = get_user_by_username('testuser2')
        assert user is not None
        user_by_id = get_user_by_id(user['id'])
        assert user_by_id is not None
        assert user_by_id['username'] == 'testuser2'

    def test_get_all_users(self, app, db_conn):
        """查询全部用户。"""
        create_user('user_a', 'hash_a', 'admin', 'A')
        create_user('user_b', 'hash_b', 'attendance', 'B')
        users = get_all_users()
        assert len(users) >= 2

    def test_toggle_user_status(self, app, db_conn):
        """切换用户启用/禁用状态。"""
        create_user('toggle_user', 'hash', 'admin', '切换测试')
        user = get_user_by_username('toggle_user')
        assert user['is_active'] == 1
        toggle_user_status(user['id'])
        user_after = get_user_by_id(user['id'])
        assert user_after['is_active'] == 0


class TestStudentCRUD:
    """学生 CRUD 测试。"""

    _test_student = {
        'student_no': 'TEST001',
        'name': '张三',
        'id_card': '110101199003073456',
        'gender': '男',
        'major': '计算机应用',
        'admin_class': '24级1班',
        'grade': '24级',
        'enrollment_year': 2024,
        'school': 1,
    }

    def test_create_and_get_by_id_card(self, app, db_conn):
        """创建学生并通过身份证查询。"""
        create_student(dict(self._test_student))
        student = get_student_by_id_card('110101199003073456')
        assert student is not None
        assert student['name'] == '张三'
        assert student['student_no'] == 'TEST001'

    def test_get_by_student_no(self, app, db_conn):
        """通过学号查询。"""
        create_student(dict(self._test_student))
        student = get_student_by_student_no('TEST001')
        assert student is not None
        assert student['name'] == '张三'

    def test_update_student(self, app, db_conn):
        """更新学生信息。"""
        create_student(dict(self._test_student))
        student = get_student_by_id_card('110101199003073456')
        update_student(student['id'], {'name': '李四', 'major': '电子商务'})
        updated = get_student_by_id_card('110101199003073456')
        assert updated['name'] == '李四'
        assert updated['major'] == '电子商务'

    def test_soft_delete_and_restore(self, app, db_conn):
        """软删除后从回收站恢复。"""
        create_student(dict(self._test_student))
        student = get_student_by_id_card('110101199003073456')
        soft_delete_student(student['id'], 'test_admin')
        # 软删除后正常查询应查不到
        normal = get_student_by_id_card('110101199003073456')
        assert normal is None
        # 回收站应有记录
        deleted = get_deleted_students()
        assert any(s['id'] == student['id'] for s in deleted)
        # 恢复
        restore_student(student['id'])
        restored = get_student_by_id_card('110101199003073456')
        assert restored is not None
        assert restored['name'] == '张三'


class TestStudentQuery:
    """学生查询和筛选测试。"""

    def test_get_students_pagination(self, app, db_conn):
        """分页查询学生。"""
        # 创建多个学生
        for i in range(5):
            create_student({
                'student_no': f'PAGE{i:03d}',
                'name': f'学生{i}',
                'id_card': f'1101012000{i:02d}010734{i:02d}',
                'gender': '男',
                'major': '测试专业',
                'admin_class': '24级1班',
                'grade': '24级',
                'enrollment_year': 2024,
                'school': 1,
            })
        result = get_students(page=1, per_page=3)
        assert 'students' in result
        assert 'total' in result
        assert result['total'] >= 5
        assert len(result['students']) <= 3

    def test_build_where_clause_basic(self, app, db_conn):
        """_build_student_where_clause 基础条件。"""
        where_clauses, params = _build_student_where_clause(None)
        # 应包含基础条件 is_deleted=0 和排除毕业
        assert len(where_clauses) >= 2
        assert any('is_deleted' in c for c in where_clauses)

    def test_build_where_clause_with_filters(self, app, db_conn):
        """_build_student_where_clause 带筛选条件。"""
        where_clauses, params = _build_student_where_clause({
            'school': 1,
            'grade': '24级',
            'gender': '男',
        })
        # 应有更多条件
        assert len(where_clauses) > 2
        # 参数应与条件数匹配
        assert len(params) > 0


class TestDictOperations:
    """字典操作测试。"""

    def test_add_and_get_dict(self, app, db_conn):
        """添加字典项并查询。"""
        add_dict('test_type', 'test_value_001')
        dicts = get_dicts('test_type')
        assert any(d['dict_value'] == 'test_value_001' for d in dicts)

    def test_get_all_grades(self, app, db_conn):
        """获取年级列表（应返回 list）。"""
        grades = get_all_grades()
        assert isinstance(grades, list)

    def test_get_enrollment_years(self, app, db_conn):
        """获取入学年份列表。"""
        years = get_enrollment_years()
        assert isinstance(years, list)
