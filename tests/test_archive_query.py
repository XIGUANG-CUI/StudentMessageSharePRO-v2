# -*- coding: utf-8 -*-
"""档案派遣查询与批次派遣明细导出测试。

覆盖：
1. /api/archive/search 跨县检索（姓名 / 拼音 / 身份证号 / 学号，筛选与排序）
2. /admin/archive/query 查询页面渲染
3. /admin/archive/export-detail/<batch_no> 派遣明细 Excel（表头 / 数据 / 汇总 / 空批次）
"""
import io
import os
import sys
from urllib.parse import quote

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import pytest
from openpyxl import load_workbook

CSRF = 'archive-query-token'

# 派遣明细表头（与 blueprints.archive.DETAIL_COLUMNS 对应）
DETAIL_HEADERS = [
    '学号', '姓名', '身份证号', '届', '年级', '班级', '县（区）', '批次号',
    '派遣状态', '派遣日期', '经办人', '接收机构', '机构地址', '联系电话',
    '机构编号', '学生电话', '家长电话', '生源地', '家庭住址',
]


def _login(client, role='admin'):
    with client.session_transaction() as sess:
        sess.clear()
        sess['user_id'] = 1
        sess['username'] = 'archive_tester'
        sess['role'] = role
        sess['school_visible'] = '*'
        sess['grade_visible'] = '*'
        sess['fields_visible'] = '["*"]'
        sess['csrf_token'] = CSRF


def _logout(client):
    with client.session_transaction() as sess:
        sess.clear()


def _insert_student(conn, no, name, pinyin, id_card, school=1, grade='2023级',
                    admin_class='1班', status='毕业', address='某街1号',
                    county=('230102', '道里区'), city=('230100', '哈尔滨市'),
                    prov=('230000', '黑龙江省')):
    cur = conn.execute(
        '''INSERT INTO students (
               student_no, id_card, name, name_pinyin, grade, admin_class, school,
               registration_status, address, student_phone, parent_phone,
               district_county_code, district_county_name,
               district_city_code, district_city_name,
               district_province_code, district_province_name
           ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)''',
        (no, id_card, name, pinyin, grade, admin_class, school, status, address,
         '13800000000', '13900000000',
         county[0], county[1], city[0], city[1], prov[0], prov[1])
    )
    return cur.lastrowid


def _insert_dispatch(conn, student_id, batch_no, status='已派遣', date='2026-06-01',
                     county=('230102', '道里区'), agency=('哈市道里区人才服务中心',
                                                       '道里区XX街1号', '0451-12345678', '23010201'),
                     operator='tester'):
    conn.execute(
        '''INSERT INTO archive_dispatch (
               batch_no, student_id, student_no, id_card, student_name, graduate_year,
               school, grade, admin_class, county_code, county_name, origin_place,
               student_phone, parent_phone, agency_no, agency_name, agency_address,
               agency_phone, dispatch_status, dispatch_date, operator
           ) SELECT ?, id, student_no, id_card, name, '2026', school, grade, admin_class,
                    ?, ?, ?, student_phone, parent_phone, ?, ?, ?, ?, ?, ?, ?
           FROM students WHERE id = ?''',
        (batch_no, county[0], county[1], county[1], agency[3], agency[0], agency[1],
         agency[2], status, date, operator, student_id)
    )


@pytest.fixture
def archive_students(db_conn):
    """构造覆盖各状态的档案数据，测试后清理学生与派遣记录。"""
    conn = db_conn
    ids = {}
    ids['zhangsan'] = _insert_student(
        conn, 'AQ001', '张三', 'ZhangSan', '230102200001010011')
    ids['lisi'] = _insert_student(
        conn, 'AQ002', '李四', 'LiSi', '230102200001010022')
    ids['wangwu'] = _insert_student(
        conn, 'AQ003', '王五', 'WangWu', '230103200001010033',
        school=2, grade='2024级', county=('230103', '南岗区'))
    ids['zhaoliu'] = _insert_student(
        conn, 'AQ004', '赵六', 'ZhaoLiu', '230102200001010044',
        status='在校')
    ids['zhangsanfeng'] = _insert_student(
        conn, 'AQ005', '张三丰', 'ZhangSanFeng', '230102200001010055')
    ids['non_graduate'] = _insert_student(
        conn, 'AQ006', '在校无档', 'ZaiXiaoWuDang', '230102200001010066',
        status='在校')

    # 张三：已派遣
    _insert_dispatch(conn, ids['zhangsan'], '2026-道里区-01',
                     status='已派遣', date='2026-06-01')
    # 王五：待派遣（校别 2 / 2024 级）
    _insert_dispatch(conn, ids['wangwu'], '2027-南岗区-01', status='待派遣',
                     date='2026-06-02', county=('230103', '南岗区'))
    # 赵六：非毕业但存在派遣记录（升学），应能查到
    _insert_dispatch(conn, ids['zhaoliu'], '2026-道里区-02', status='升学',
                     date='', operator='')
    conn.commit()

    yield ids

    placeholders = ','.join('?' for _ in ids)
    conn.execute(
        f'DELETE FROM archive_dispatch WHERE student_id IN ({placeholders})',
        list(ids.values())
    )
    conn.execute(f'DELETE FROM students WHERE id IN ({placeholders})', list(ids.values()))
    conn.commit()


def _search(client, keyword='', **filters):
    params = {'keyword': keyword}
    params.update({k: v for k, v in filters.items() if v})
    resp = client.get('/api/archive/search?' + '&'.join(
        f'{k}={quote(str(v))}' for k, v in params.items()))
    assert resp.status_code == 200
    return resp.get_json()


class TestDispatchQuery:
    """跨县检索接口测试。"""

    @pytest.fixture(autouse=True)
    def _auth(self, client):
        _login(client)
        yield
        _logout(client)

    def test_page_renders(self, client, archive_students):
        resp = client.get('/admin/archive/query')
        assert resp.status_code == 200
        html = resp.get_data(as_text=True)
        assert '档案派遣查询' in html
        assert '查询结果' in html

    def test_empty_keyword_returns_nothing(self, client, archive_students):
        data = _search(client, '')
        assert data['count'] == 0

    def test_search_by_name(self, client, archive_students):
        data = _search(client, '张三')
        names = {s['name'] for s in data['students']}
        assert names == {'张三', '张三丰'}
        # 姓名精确匹配排在前面
        assert data['students'][0]['student_id'] == archive_students['zhangsan']

    def test_search_by_pinyin_case_insensitive(self, client, archive_students):
        lower = _search(client, 'zhangsan')
        upper = _search(client, 'ZHANGSAN')
        assert lower['count'] == 2
        assert upper['count'] == 2
        assert lower['students'][0]['student_id'] == archive_students['zhangsan']
        assert lower['students'][0]['name_pinyin'] == 'ZhangSan'

    def test_search_by_student_no(self, client, archive_students):
        data = _search(client, 'AQ002')
        assert data['count'] == 1
        assert data['students'][0]['name'] == '李四'

    def test_search_by_id_card(self, client, archive_students):
        data = _search(client, '230102200001010044')
        assert data['count'] == 1
        assert data['students'][0]['name'] == '赵六'

    def test_result_contains_dispatch_detail(self, client, archive_students):
        data = _search(client, 'AQ001')
        row = data['students'][0]
        assert row['status_label'] == '已派遣'
        assert row['dispatch_date'] == '2026-06-01'
        assert row['batch_no'] == '2026-道里区-01'
        assert row['agency_name'] == '哈市道里区人才服务中心'
        assert row['agency_address'] == '道里区XX街1号'
        assert row['agency_phone'] == '0451-12345678'
        assert row['county_code'] == '230102'
        assert row['county_name'] == '道里区'
        assert row['graduate_year'] == '2026'
        assert row['address'] == '某街1号'

    def test_graduate_without_dispatch_shows_not_generated(self, client, archive_students):
        data = _search(client, 'AQ002')
        row = data['students'][0]
        assert row['dispatch_status'] == ''
        assert row['status_label'] == '未生成'
        assert row['dispatch_date'] == ''

    def test_non_graduate_with_dispatch_is_included(self, client, archive_students):
        data = _search(client, 'AQ004')
        assert data['count'] == 1
        assert data['students'][0]['status_label'] == '升学'

    def test_non_graduate_without_dispatch_is_excluded(self, client, archive_students):
        data = _search(client, 'AQ006')
        assert data['count'] == 0

    def test_filter_by_school_and_grade(self, client, archive_students):
        all_rows = _search(client, 'AQ00')
        assert all_rows['count'] == 5
        only_school2 = _search(client, 'AQ00', school='2')
        assert [s['student_no'] for s in only_school2['students']] == ['AQ003']
        only_grade = _search(client, 'AQ00', grade='2024级')
        assert [s['student_no'] for s in only_grade['students']] == ['AQ003']

    def test_unauthorized(self, client, archive_students):
        with client.session_transaction() as sess:
            sess.clear()
        resp = client.get('/api/archive/search?keyword=AQ001')
        assert resp.status_code in (301, 302, 401)


class TestDispatchDetailExport:
    """按派遣日期范围全局导出派遣明细的测试。"""

    @pytest.fixture(autouse=True)
    def _auth(self, client):
        _login(client)
        yield
        _logout(client)

    def _download(self, client, query=''):
        url = '/admin/archive/export-detail'
        if query:
            url += '?' + query
        resp = client.get(url)
        assert resp.status_code == 200
        assert 'spreadsheetml' in resp.headers.get('Content-Type', '')
        assert 'attachment' in resp.headers.get('Content-Disposition', '')
        assert resp.data[:2] == b'PK'
        return load_workbook(io.BytesIO(resp.data)).active

    @staticmethod
    def _data_rows(ws):
        """按学号收集数据行（跳过末尾汇总区），返回 {学号: 行数据}。"""
        rows = {}
        for r in range(2, ws.max_row + 1):
            no = ws.cell(row=r, column=1).value
            if not no or not str(no).startswith('AQ'):
                continue
            rows[no] = {
                'name': ws.cell(row=r, column=2).value,
                'batch': ws.cell(row=r, column=8).value,
                'status': ws.cell(row=r, column=9).value,
                'date': ws.cell(row=r, column=10).value,
                'county': ws.cell(row=r, column=7).value,
                'agency': ws.cell(row=r, column=12).value,
                'address': ws.cell(row=r, column=13).value,
                'phone': ws.cell(row=r, column=14).value,
                'home': ws.cell(row=r, column=19).value,
            }
        return rows

    def test_headers(self, client, archive_students):
        ws = self._download(client, 'start_date=2026-06-01&end_date=2026-06-30')
        assert [ws.cell(row=1, column=i).value for i in range(1, len(DETAIL_HEADERS) + 1)] \
            == DETAIL_HEADERS
        assert ws.freeze_panes == 'A2'

    def test_cross_batch_and_county(self, client, archive_students):
        # 张三（道里区·批次 01）与王五（南岗区·批次 01）属于不同批次/区县，
        # 只要派遣日期落在范围内就应一起导出；升学记录无派遣日期，不纳入。
        ws = self._download(client, 'start_date=2026-06-01&end_date=2026-06-30')
        rows = self._data_rows(ws)
        assert set(rows) == {'AQ001', 'AQ003'}
        assert rows['AQ001']['batch'] == '2026-道里区-01'
        assert rows['AQ001']['county'] == '道里区'
        assert rows['AQ003']['batch'] == '2027-南岗区-01'
        assert rows['AQ003']['county'] == '南岗区'

    def test_row_values(self, client, archive_students):
        ws = self._download(client, 'start_date=2026-06-01&end_date=2026-06-30')
        row = self._data_rows(ws)['AQ001']
        assert row['name'] == '张三'
        assert row['status'] == '已派遣'
        assert row['date'] == '2026-06-01'
        assert row['agency'] == '哈市道里区人才服务中心'
        assert row['address'] == '道里区XX街1号'
        assert row['phone'] == '0451-12345678'
        assert row['home'] == '某街1号'

    def test_narrow_date_range(self, client, archive_students):
        ws = self._download(client, 'start_date=2026-06-02&end_date=2026-06-02')
        assert set(self._data_rows(ws)) == {'AQ003'}

    def test_school_filter(self, client, archive_students):
        ws = self._download(client, 'start_date=2026-06-01&end_date=2026-06-30&school=2')
        assert set(self._data_rows(ws)) == {'AQ003'}

    def test_without_dates_exports_all_dated_records(self, client, archive_students):
        rows = self._data_rows(self._download(client))
        assert set(rows) == {'AQ001', 'AQ003'}

    def test_summary_rows(self, client, archive_students):
        ws = self._download(client, 'start_date=2026-06-01&end_date=2026-06-30')
        summary = {}
        for r in range(2, ws.max_row + 1):
            key, value = ws.cell(row=r, column=1).value, ws.cell(row=r, column=2).value
            if key and not str(key).startswith('AQ'):
                summary[key] = value
        assert summary['状态汇总'] == '人数'
        assert summary['待派遣'] == 1
        assert summary['已派遣'] == 1
        assert summary['合计'] == 2

    def test_no_rows_returns_404(self, client, archive_students):
        resp = client.get('/admin/archive/export-detail'
                          '?start_date=2027-01-01&end_date=2027-01-31')
        assert resp.status_code == 404
        assert resp.get_json()['success'] is False

    def test_invalid_date_returns_400(self, client, archive_students):
        resp = client.get('/admin/archive/export-detail?start_date=2026/06/01')
        assert resp.status_code == 400
        assert resp.get_json()['success'] is False

    def test_reversed_range_returns_400(self, client, archive_students):
        resp = client.get('/admin/archive/export-detail'
                          '?start_date=2026-06-30&end_date=2026-06-01')
        assert resp.status_code == 400
        assert resp.get_json()['success'] is False

    def test_unauthorized(self, client, archive_students):
        with client.session_transaction() as sess:
            sess.clear()
        resp = client.get('/admin/archive/export-detail?start_date=2026-06-01')
        assert resp.status_code in (301, 302, 401)


class TestMergedTransferExport:
    """多区县批次合并导出转递单 + 已导出标记测试。"""

    @pytest.fixture(autouse=True)
    def _auth(self, client):
        _login(client)
        yield
        _logout(client)

    @staticmethod
    def _export(client, batch_nos):
        query = '&'.join('batch_nos=' + quote(no) for no in batch_nos)
        return client.get('/admin/archive/export-merged?' + query)

    def test_merge_multiple_batches(self, client, archive_students, db_conn):
        resp = self._export(client, ['2026-道里区-01', '2027-南岗区-01'])
        assert resp.status_code == 200
        assert 'wordprocessingml' in resp.headers.get('Content-Type', '')
        assert 'attachment' in resp.headers.get('Content-Disposition', '')
        assert resp.data[:2] == b'PK'
        history = db_conn.execute(
            "SELECT scheme_name, row_count, template_name FROM export_history "
            "WHERE template_name = '档案转递单（合并）' ORDER BY id"
        ).fetchall()
        assert [(h['scheme_name'], h['row_count']) for h in history] == [
            ('2026-道里区-01', 1), ('2027-南岗区-01', 1),
        ]

    def test_duplicate_batch_numbers_are_deduped(self, client, archive_students, db_conn):
        resp = self._export(client, ['2026-道里区-01', '2026-道里区-01'])
        assert resp.status_code == 200
        count = db_conn.execute(
            "SELECT COUNT(*) FROM export_history WHERE template_name = '档案转递单（合并）'"
        ).fetchone()[0]
        assert count == 1

    def test_batches_without_printable_rows_are_skipped(self, client, archive_students, db_conn):
        # 2026-道里区-02 只有升学记录，不出转递单；仍应成功导出可打印的另一个批次
        resp = self._export(client, ['2026-道里区-02', '2026-道里区-01'])
        assert resp.status_code == 200
        names = [h['scheme_name'] for h in db_conn.execute(
            "SELECT scheme_name FROM export_history WHERE template_name = '档案转递单（合并）'"
        ).fetchall()]
        assert names == ['2026-道里区-01']

    def test_all_unprintable_returns_404(self, client, archive_students):
        resp = self._export(client, ['2026-道里区-02'])
        assert resp.status_code == 404
        assert resp.get_json()['success'] is False

    def test_no_batches_returns_400(self, client, archive_students):
        resp = client.get('/admin/archive/export-merged')
        assert resp.status_code == 400
        assert resp.get_json()['success'] is False

    def test_overview_marks_exported_batches(self, client, archive_students):
        before = client.get('/api/archive/overview').get_json()['batches']
        assert all(not b['exported_at'] for b in before if b['batch_no'] in (
            '2026-道里区-01', '2027-南岗区-01'))

        assert self._export(client, ['2026-道里区-01']).status_code == 200

        batches = client.get('/api/archive/overview').get_json()['batches']
        marks = {b['batch_no']: b for b in batches}
        assert marks['2026-道里区-01']['exported_at']
        assert marks['2026-道里区-01']['exported_by'] == 'archive_tester'
        # 未导出的批次不应被误标记
        assert marks['2027-南岗区-01']['exported_at'] == ''

    def test_unauthorized(self, client, archive_students):
        with client.session_transaction() as sess:
            sess.clear()
        resp = client.get('/admin/archive/export-merged?batch_nos=2026-道里区-01')
        assert resp.status_code in (301, 302, 401)
