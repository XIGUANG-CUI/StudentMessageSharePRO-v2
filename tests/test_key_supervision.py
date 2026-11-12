# -*- coding: utf-8 -*-
"""重点监管学生筛选逻辑单元测试。

覆盖：
1. 外省并集判定（身份证非23 / 户籍省非黑龙江）、学籍非毕业、未删除、入学年份隔离
2. 命中类型多选组合（身份证非23 / 户籍非黑龙江 / 两者兼有）
3. 在校状态、关键字筛选
4. 动态筛选项（仅取重点监管集合）与年级→班级→专业级联
5. 页面路由、多值参数分页保留、Excel 导出
"""
import io
import os
import sys
from urllib.parse import quote

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import pytest
from openpyxl import load_workbook

import models

CSRF = 'key-supervision-token'
BASE_FILTER = {'enrollment_year': 2025}


def _insert_student(conn, no, name, id_card, province, year=2025, deleted=0,
                    grade='2025级', admin_class='1班', major='计算机',
                    attendance='在校', registration='正式学籍', school=1):
    conn.execute(
        '''INSERT INTO students (
               student_no, name, school, id_card, grade, admin_class, virtual_class,
               major, registration_status, attendance_status, is_deleted,
               enrollment_year, district_province_name
           ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)''',
        (no, name, school, id_card, grade, admin_class, '虚拟1班', major,
         registration, attendance, deleted, year, province)
    )


@pytest.fixture
def ks_students(db_conn):
    """构造覆盖各类判定的学生数据。"""
    conn = db_conn
    _insert_student(conn, 'KS001', '省内学生', '230101200001010011', '黑龙江省')
    _insert_student(conn, 'KS002', '证件命中', '110101200001010022', '黑龙江省')
    _insert_student(conn, 'KS003', '户籍命中', '230101200001010033', '吉林省',
                    admin_class='2班', major='护理')
    _insert_student(conn, 'KS004', '两者兼有', '110101200001010044', '吉林省',
                    grade='2024级', admin_class='3班', attendance='待销假')
    _insert_student(conn, 'KS005', '数据缺失', '', '')
    _insert_student(conn, 'KS006', '已删除', '110101200001010066', '吉林省', deleted=1)
    _insert_student(conn, 'KS007', '已毕业', '110101200001010077', '吉林省',
                    registration='毕业')
    _insert_student(conn, 'KS008', '其他年份', '110101200001010088', '吉林省',
                    year=2024, grade='2024级', admin_class='3班')
    # 省内非监管学生：用于验证筛选项不会混入非重点监管数据（4班/护理）
    _insert_student(conn, 'KS009', '省内非监管', '230101200001010099', '黑龙江省',
                    grade='2024级', admin_class='4班', major='护理')
    conn.commit()
    return conn


def _login(client, year=2025):
    with client.session_transaction() as sess:
        sess.clear()
        sess['user_id'] = 1
        sess['username'] = 'ks_tester'
        sess['role'] = 'admin'
        sess['school_visible'] = '*'
        sess['grade_visible'] = '*'
        sess['fields_visible'] = '["*"]'
        sess['csrf_token'] = CSRF
        sess['enrollment_year'] = year


def _nos(result):
    return [s['student_no'] for s in result['students']]


class TestKeySupervisionScope:
    """并集判定与基础范围。"""

    def test_union_scope_and_stats(self, app, db_conn, ks_students):
        result = models.get_key_supervision_students(BASE_FILTER)

        assert _nos(result) == ['KS002', 'KS003', 'KS004']
        assert result['total'] == 3
        assert result['stats'] == {
            'supervision_total': 3,
            'id_hits': 1,
            'district_hits': 1,
            'both_hits': 1,
        }
        # 缺失数据不纳入，仅统计人数
        assert result['missing'] == {'missing_id': 1, 'missing_district': 1}

    def test_excludes_deleted_graduated_and_other_year(self, app, db_conn, ks_students):
        nos = _nos(models.get_key_supervision_students(BASE_FILTER))
        assert 'KS001' not in nos  # 省内
        assert 'KS005' not in nos  # 数据缺失
        assert 'KS006' not in nos  # 已删除
        assert 'KS007' not in nos  # 已毕业
        assert 'KS008' not in nos  # 非当前入学年份
        assert 'KS009' not in nos  # 省内

    def test_hit_flag_label(self, app, db_conn, ks_students):
        flags = {s['student_no']: s['hit_type']
                 for s in models.get_key_supervision_students(BASE_FILTER)['students']}
        assert flags == {'KS002': 'id', 'KS003': 'district', 'KS004': 'both'}

    def test_pagination(self, app, db_conn, ks_students):
        page1 = models.get_key_supervision_students(BASE_FILTER, page=1, per_page=2)
        page2 = models.get_key_supervision_students(BASE_FILTER, page=2, per_page=2)
        assert _nos(page1) == ['KS002', 'KS003']
        assert _nos(page2) == ['KS004']
        assert page1['pages'] == 2


class TestKeySupervisionFilters:
    """命中类型多选、在校状态与关键字筛选。"""

    @pytest.mark.parametrize('hit_types, expected', [
        (['id'], ['KS002']),
        (['district'], ['KS003']),
        (['both'], ['KS004']),
        (['id', 'district'], ['KS002', 'KS003']),
        (['id', 'both'], ['KS002', 'KS004']),
        (['id', 'district', 'both'], ['KS002', 'KS003', 'KS004']),
        ([], ['KS002', 'KS003', 'KS004']),
        (['unknown'], ['KS002', 'KS003', 'KS004']),
    ])
    def test_hit_types_combination(self, app, db_conn, ks_students, hit_types, expected):
        filters = dict(BASE_FILTER, hit_types=hit_types)
        assert _nos(models.get_key_supervision_students(filters)) == expected

    def test_single_category_excludes_both(self, app, db_conn, ks_students):
        """单独筛选单项命中时，不应包含“两者兼有”学生。"""
        assert _nos(models.get_key_supervision_students(
            dict(BASE_FILTER, hit_types=['id']))) == ['KS002']
        assert _nos(models.get_key_supervision_students(
            dict(BASE_FILTER, hit_types=['district']))) == ['KS003']

    def test_attendance_status_filter(self, app, db_conn, ks_students):
        onsite = models.get_key_supervision_students(
            dict(BASE_FILTER, attendance_status='在校'))
        pending = models.get_key_supervision_students(
            dict(BASE_FILTER, attendance_status='待销假'))
        assert _nos(onsite) == ['KS002', 'KS003']
        assert _nos(pending) == ['KS004']

    def test_search_filter(self, app, db_conn, ks_students):
        assert _nos(models.get_key_supervision_students(
            dict(BASE_FILTER, search='户籍'))) == ['KS003']
        assert _nos(models.get_key_supervision_students(
            dict(BASE_FILTER, search='KS002'))) == ['KS002']
        assert _nos(models.get_key_supervision_students(
            dict(BASE_FILTER, search='110101'))) == ['KS002', 'KS004']

    def test_combined_filters(self, app, db_conn, ks_students):
        filters = dict(BASE_FILTER, hit_types=['id', 'district'],
                       attendance_status='在校', grade='2025级')
        assert _nos(models.get_key_supervision_students(filters)) == ['KS002', 'KS003']


class TestKeySupervisionFilterOptions:
    """动态筛选项仅取重点监管集合，并支持级联。"""

    def test_options_from_supervision_scope(self, app, db_conn, ks_students):
        opts = models.get_key_supervision_filter_values(BASE_FILTER)

        assert opts['grades'] == ['2024级', '2025级']
        assert opts['classes'] == ['1班', '2班', '3班']  # 不含省内生的 4班
        assert opts['majors'] == ['护理', '计算机']
        assert opts['schools'] == [1]
        assert opts['attendance_statuses'] == ['在校', '待销假']

    def test_class_cascade_by_grade(self, app, db_conn, ks_students):
        opts = models.get_key_supervision_filter_values(
            dict(BASE_FILTER, grade='2025级'))
        assert opts['classes'] == ['1班', '2班']
        assert opts['majors'] == ['护理', '计算机']

    def test_major_cascade_by_grade_and_class(self, app, db_conn, ks_students):
        opts = models.get_key_supervision_filter_values(
            dict(BASE_FILTER, grade='2025级', admin_class='1班'))
        assert opts['classes'] == ['1班', '2班']
        assert opts['majors'] == ['计算机']

    def test_selected_value_is_retained(self, app, db_conn, ks_students):
        opts = models.get_key_supervision_filter_values(
            dict(BASE_FILTER, grade='2099级'))
        assert '2099级' in opts['grades']


class TestKeySupervisionRoutes:
    """页面路由、分页多值参数保留与导出。"""

    @pytest.fixture(autouse=True)
    def _logout(self, client):
        """每个用例后清理登录态，避免污染其他路由测试。"""
        yield
        with client.session_transaction() as sess:
            sess.clear()

    def test_page_multi_hit_and_attendance(self, app, client, db_conn, ks_students):
        _login(client)
        resp = client.get('/admin/key-supervision'
                          '?hit_types=id&hit_types=district&attendance_status=' +
                          quote('在校'))
        assert resp.status_code == 200
        html = resp.get_data(as_text=True)

        assert 'KS002' in html and 'KS003' in html
        assert 'KS004' not in html  # 待销假被过滤
        assert 'name="hit_types"' in html
        assert 'checked' in html
        assert 'value="4班"' not in html  # 选项不混入非监管学生

    def test_default_hit_types_excludes_district_only(self, app, client, db_conn, ks_students):
        """默认勾选 身份证非23 + 两者兼有，仅户籍命中的学生不显示。"""
        _login(client)
        html = client.get('/admin/key-supervision').get_data(as_text=True)
        assert 'KS002' in html and 'KS004' in html
        assert 'KS003' not in html
        assert html.count('checked') == 2

    def test_explicit_empty_hit_selection_shows_all(self, app, client, db_conn, ks_students):
        """表单提交时全部取消勾选，则不做命中类型限制。"""
        _login(client)
        html = client.get('/admin/key-supervision?hit_filtered=1').get_data(as_text=True)
        for no in ('KS002', 'KS003', 'KS004'):
            assert no in html
        assert html.count('checked') == 0

    def test_export_default_hit_types(self, app, client, db_conn, ks_students):
        """导出未带命中参数时，与页面默认口径一致。"""
        _login(client)
        resp = client.get('/admin/key-supervision/export')
        assert resp.status_code == 200
        wb = load_workbook(io.BytesIO(resp.data))
        rows = [[cell.value for cell in row] for row in wb.active.iter_rows(min_row=2)]
        assert [row[0] for row in rows] == ['KS002', 'KS004']

    def test_newly_added_student_appears(self, app, client, db_conn, ks_students):
        """通过新增学生表单录入后，默认筛选下应能在重点监管页面展示。"""
        _login(client)
        payload = {
            'student_no': 'KSADD01',
            'name': '新录入学生',
            'id_card': '110101199003070011',
            'gender': '男',
            'school': '1',
            'grade': '2025级',
            'admin_class': '1班',
            'major': '计算机',
            'registration_status': '正式学籍',
            'attendance_status': '在校',
            'address': '黑龙江省哈尔滨市',
            'csrf_token': CSRF,
        }
        resp = client.post('/admin/students/add', data=payload)
        assert resp.status_code == 302

        student = models.get_student_by_student_no('KSADD01')
        assert student is not None
        assert student['enrollment_year'] == 2025
        assert student['is_deleted'] == 0

        html = client.get('/admin/key-supervision').get_data(as_text=True)
        assert '新录入学生' in html

    def test_page_options_cascade(self, app, client, db_conn, ks_students):
        _login(client)
        resp = client.get('/admin/key-supervision?grade=' + quote('2025级') +
                          '&admin_class=' + quote('1班'))
        assert resp.status_code == 200
        html = resp.get_data(as_text=True)
        assert 'option value="计算机"' in html
        assert 'option value="护理"' not in html

    def test_pagination_keeps_multi_values(self, app, client, db_conn, ks_students):
        _login(client)
        for i in range(25):
            _insert_student(db_conn, f'KP{i:03d}', f'分页{i:03d}',
                            f'11010120000201{i:04d}', '吉林省',
                            admin_class='5班')
        db_conn.commit()

        resp = client.get('/admin/key-supervision'
                          '?hit_types=id&hit_types=district&page=1')
        assert resp.status_code == 200
        html = resp.get_data(as_text=True)
        assert 'hit_types=id&amp;hit_types=district' in html

    def test_export_respects_multi_hit_types(self, app, client, db_conn, ks_students):
        _login(client)
        resp = client.get('/admin/key-supervision/export'
                          '?hit_types=id&hit_types=both')
        assert resp.status_code == 200

        wb = load_workbook(io.BytesIO(resp.data))
        ws = wb.active
        rows = [[cell.value for cell in row] for row in ws.iter_rows(min_row=2)]
        assert [row[0] for row in rows] == ['KS002', 'KS004']
        assert [row[-1] for row in rows] == ['身份证非23', '两者兼有']
