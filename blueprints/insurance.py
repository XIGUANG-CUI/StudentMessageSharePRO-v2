from flask import (Blueprint, request, jsonify, render_template, send_file,
                   Response, session, abort, redirect, url_for)
from datetime import datetime, timedelta, date
from io import BytesIO, StringIO
from functools import wraps
import csv
import re
import secrets
import threading
from urllib.parse import quote
from openpyxl import Workbook
from openpyxl.styles import Font, PatternFill, Alignment
from openpyxl.utils import get_column_letter

import models
import auth
import utils
import config
from models import insurance as insurance_domain
from models.insurance import current_insurance_batch
from services import insurance_service

bp = Blueprint('insurance', __name__)

# insurance 专用导入进度存储（随块迁移自 app.py）
insurance_init_progress = {}

@bp.route('/admin/insurance-query')
@auth.login_required
def admin_insurance_query():
    """保险查询相关接口。"""
    return render_template('admin/insurance_query.html')


@bp.route('/api/insurance/query')
@auth.login_required
def api_insurance_query():
    """保险查询相关接口。"""
    keyword = request.args.get('keyword', '').strip()

    conn = models.get_db()
    base_where = ["s.is_deleted = 0"]

    # 不排除毕业学生：USER 可能需要查看已毕业学生的保单信息
    # 但排除退学办结
    base_where.append("s.attendance_status != '退学办结'")

    params = []
    if keyword:
        # 判断是否为纯数字（身份证号精确匹配）
        if keyword.isdigit() and len(keyword) == 18:
            base_where.append('s.id_card = ?')
            params.append(keyword)
        else:
            base_where.append('(s.name LIKE ? OR s.name_pinyin LIKE ?)')
            params.append(f'%{keyword}%')
            params.append(f'%{keyword}%')

    where = ' AND '.join(base_where)

    rows = conn.execute(
        f'''SELECT s.id, s.name, s.id_card, s.admin_class, s.grade,
                   s.student_insurance_no, s.student_insurance_expiry,
                   s.student_insurance_pay_date, s.supervisor_flag
            FROM students s
            WHERE {where}
            ORDER BY CASE WHEN s.student_insurance_expiry IS NULL OR s.student_insurance_expiry = '' THEN 1 ELSE 0 END,
                     s.student_insurance_expiry ASC,
                     s.grade DESC, s.admin_class, s.name
            LIMIT 200''',
        params
    ).fetchall()
    conn.close()

    students = [dict(r) for r in rows]
    return jsonify({'students': students, 'count': len(students)})


@bp.route('/insurance/register')
@auth.login_required
def insurance_register():
    """旧版缴费登记页已下线，统一重定向到新版保费催缴页。"""
    return redirect(url_for('insurance.insurance_collection'))


@bp.route('/api/insurance/students')
@auth.finance_required
def api_insurance_students():
    """保险学生相关接口。"""
    page = request.args.get('page', 1, type=int)
    per_page = request.args.get('per_page', 20, type=int)
    search = request.args.get('search', '').strip()
    grade = request.args.get('grade', '').strip()
    admin_class = request.args.get('admin_class', '').strip()
    major = request.args.get('major', '').strip()
    supervisor_flag = request.args.get('supervisor_flag', '').strip()

    cursor = models.get_db().execute(f'''
        SELECT COUNT(*) FROM students WHERE {models._non_graduated_where()}
    ''')
    total = cursor.fetchone()[0]

    conditions = ['s.is_deleted = 0', "(s.registration_status IS NULL OR s.registration_status != '毕业')"]
    params = []

    if search:
        conditions.append('(s.name LIKE ? OR s.student_no LIKE ? OR s.id_card LIKE ? OR s.name_pinyin LIKE ?)')
        like = f'%{search}%'
        params.extend([like, like, like, like])
    if grade:
        conditions.append('s.grade = ?')
        params.append(grade)
    if admin_class:
        conditions.append('s.admin_class = ?')
        params.append(admin_class)
    if major:
        conditions.append('s.major = ?')
        params.append(major)

    columns = 's.id, s.name, s.student_no, s.id_card, s.grade, s.admin_class, s.major, s.supervisor_flag, s.student_insurance_no, s.student_insurance_pay_date, s.student_insurance_expiry, s.insurance_premium, s.internship_insurance_pay_date, s.declaration_date, s.insurance_update_date, s.tuition_status'
    where = ' AND '.join(conditions)

    if supervisor_flag == 'not_completed':
        conditions.append("(s.supervisor_flag IS NULL OR s.supervisor_flag = '' OR s.supervisor_flag NOT LIKE '%办结%')")

    where = ' AND '.join(conditions)

    count_cursor = models.get_db().execute(f'SELECT COUNT(*) FROM students s WHERE {where}', params)
    filtered_total = count_cursor.fetchone()[0]

    offset = (page - 1) * per_page
    rows = models.get_db().execute(
        f'SELECT {columns} FROM students s WHERE {where} ORDER BY s.grade, s.admin_class, s.name LIMIT ? OFFSET ?',
        params + [per_page, offset]
    ).fetchall()

    students = [dict(r) for r in rows]

    return jsonify({
        'students': students,
        'total': total,
        'filtered_total': filtered_total,
        'page': page,
        'per_page': per_page,
        'total_pages': max(1, (filtered_total + per_page - 1) // per_page)
    })


@bp.route('/api/insurance/register-tree')
@auth.finance_required
def api_insurance_register_tree():
    """保险登记tree相关接口。支持 search / grade / admin_class / major 筛选。"""
    search = request.args.get('search', '').strip()
    grade = request.args.get('grade', '').strip()
    admin_class = request.args.get('admin_class', '').strip()
    major = request.args.get('major', '').strip()

    conditions = [
        "s.is_deleted = 0",
        "s.attendance_status NOT IN ('托管', '退学办结')",
        "(s.registration_status IS NULL OR s.registration_status != '毕业')",
        "(s.supervisor_flag IS NULL OR (s.supervisor_flag NOT LIKE '%实习就业险%' AND s.supervisor_flag NOT IN ('{config.InsuranceStatus.PENDING_XUEPING}', '{config.InsuranceStatus.PENDING_INTERN}')))"
    ]
    params = []

    if grade:
        conditions.append('s.grade = ?')
        params.append(grade)
    if admin_class:
        conditions.append('s.admin_class = ?')
        params.append(admin_class)
    if major:
        conditions.append('s.major = ?')
        params.append(major)

    if search:
        conditions.append('(s.name LIKE ? OR s.student_no LIKE ? OR s.id_card LIKE ? OR s.name_pinyin LIKE ?)')
        like = f'%{search}%'
        params.extend([like, like, like, like])

    where = ' AND '.join(conditions)

    rows = models.get_db().execute(
        f'''SELECT s.id, s.name, s.student_no, s.id_card, s.grade, s.admin_class,
                   s.major, s.enrollment_year, s.school,
                   s.student_insurance_no, s.student_insurance_pay_date,
                   s.student_insurance_expiry, s.insurance_premium,
                   s.internship_insurance_pay_date, s.supervisor_flag,
                   s.declaration_date
            FROM students s
            WHERE {where}
            ORDER BY s.enrollment_year DESC, s.admin_class, s.name''',
        params
    ).fetchall()

    students = [dict(r) for r in rows]

    # batch=1 空标记学生默认走审批环节（外部参保），不显示在缴费登记页
    students = [s for s in students
                if not (_student_batch(s) == 1 and _is_blank(s.get('supervisor_flag')))]

    # 批次统一由后端计算并下发，模板只读取，避免前端维护第二份公式
    for s in students:
        s['batch'] = _student_batch(s)

    tree = {}
    for s in students:
        year = str(s.get('enrollment_year') or '未知年份')
        cls = s.get('admin_class') or '未分班'
        if year not in tree:
            tree[year] = {}
        if cls not in tree[year]:
            tree[year][cls] = []
        tree[year][cls].append(s)

    return jsonify({'tree': tree})


@bp.route('/api/insurance/register-single', methods=['POST'])
@auth.finance_required
def api_insurance_register_single():
    """保险登记single相关接口（旧版，已下线）。"""
    return jsonify({'success': False,
                    'message': '旧版保险登记接口已下线，请使用新版保费催缴接口'}), 410


@bp.route('/api/insurance/register-batch', methods=['POST'])
@auth.finance_required
def api_insurance_register_batch():
    """保险登记批量相关接口（旧版，已下线）。"""
    return jsonify({'success': False,
                    'message': '旧版保险登记接口已下线，请使用新版保费催缴接口'}), 410


@bp.route('/insurance/approve')
@auth.login_required
def insurance_approve():
    """旧版审批页已下线，统一重定向到新版新生首保页。"""
    return redirect(url_for('insurance.insurance_first'))


def _is_blank(v):
    """判断缴费日期等字段是否为空（None / 空串 / 纯空白）。"""
    return v is None or (isinstance(v, str) and v.strip() == '')


def compute_insurance_batch(grade, enrollment_year, current_year=None):
    """计算学生保险批次（第几年），委托 models.insurance 统一实现。

    保持原签名与返回值（1~3 或 None），供审批/待办/导入等旧逻辑继续调用。
    统一规则：偏移量 = 入学年 − 年级；0 普通、≥1 转学生、≤−1 推迟注册。
    """
    return current_insurance_batch(grade, enrollment_year, current_year)


# ============================================================
# P2：五个池的只读查询接口（不接页面，先验证口径）
# ============================================================
_INSURANCE_POOL_ROLES = ('admin', 'finance_chief', 'finance_staff', 'insurance_officer')


def _page_roles(*roles):
    """页面级多角色校验：未登录跳登录页，角色不符返回 403。"""
    def decorator(view):
        @wraps(view)
        def wrapper(*args, **kwargs):
            if not session.get('user_id'):
                return redirect(url_for('login'))
            if session.get('role') not in roles:
                abort(403)
            return view(*args, **kwargs)
        return wrapper
    return decorator


def _require_insurance_json():
    if 'user_id' not in session:
        return jsonify({'success': False, 'message': '请先登录'}), 401
    if session.get('role') not in _INSURANCE_POOL_ROLES:
        return jsonify({'success': False, 'message': '需要保险相关权限'}), 403
    return None


def _require_admin_json():
    if 'user_id' not in session:
        return jsonify({'success': False, 'message': '请先登录'}), 401
    if session.get('role') != 'admin':
        return jsonify({'success': False, 'message': '需要管理员权限'}), 403
    return None


def _require_roles_json(*roles):
    """JSON 接口多角色校验：未登录 401，角色不符 403。

    用于区分财务登记、主管审批、保险员办结等职责，避免"保险池角色全通"。
    """
    if 'user_id' not in session:
        return jsonify({'success': False, 'message': '请先登录'}), 401
    if session.get('role') not in roles:
        return jsonify({'success': False, 'message': '需要相应角色权限'}), 403
    return None


def _json_roles(*roles):
    """JSON 接口的角色校验装饰器（未登录 401 / 角色不符 403）。"""
    def decorator(view):
        @wraps(view)
        def wrapper(*args, **kwargs):
            err = _require_roles_json(*roles)
            if err:
                return err
            return view(*args, **kwargs)
        return wrapper
    return decorator


def _pool_filters():
    return {
        'search': (request.args.get('search') or '').strip(),
        'school': (request.args.get('school') or '').strip(),
        'grade': (request.args.get('grade') or '').strip(),
        'admin_class': (request.args.get('admin_class') or '').strip(),
        'major': (request.args.get('major') or '').strip(),
    }


def _pool_today():
    """可选 today=YYYY-MM-DD，便于验证 30 天窗口；缺省取服务器当天。"""
    raw = (request.args.get('today') or '').strip()
    if not raw:
        return date.today()
    return datetime.strptime(raw, '%Y-%m-%d').date()


def _pool_response(pool, items):
    return jsonify({'success': True, 'pool': pool, 'total': len(items), 'items': items})


@bp.route('/api/insurance/pools/summary')
def api_insurance_pools_summary():
    err = _require_insurance_json()
    if err:
        return err
    try:
        today = _pool_today()
    except ValueError:
        return jsonify({'success': False, 'message': 'today 需为 YYYY-MM-DD'}), 400
    summary = insurance_domain.pool_summary(models.get_db(), today=today)
    return jsonify({'success': True, 'summary': summary})


@bp.route('/api/insurance/pools/first')
def api_insurance_pool_first():
    err = _require_insurance_json()
    if err:
        return err
    items = insurance_domain.query_first_pool(models.get_db(), **_pool_filters())
    return _pool_response('first', items)


@bp.route('/api/insurance/pools/renewal')
def api_insurance_pool_renewal():
    err = _require_insurance_json()
    if err:
        return err
    try:
        today = _pool_today()
        window_days = int(request.args.get('window_days') or 30)
    except ValueError:
        return jsonify({'success': False, 'message': 'today/window_days 参数无效'}), 400
    items = insurance_domain.query_renewal_pool(
        models.get_db(), today=today, window_days=window_days, **_pool_filters()
    )
    return _pool_response('renewal', items)


@bp.route('/api/insurance/pools/collection')
def api_insurance_pool_collection():
    err = _require_insurance_json()
    if err:
        return err
    items = insurance_domain.query_collection_pool(models.get_db(), **_pool_filters())
    return _pool_response('collection', items)


@bp.route('/api/insurance/pools/push')
def api_insurance_pool_push():
    err = _require_insurance_json()
    if err:
        return err
    items = insurance_domain.query_push_pool(models.get_db(), **_pool_filters())
    return _pool_response('push', items)


@bp.route('/api/insurance/pools/intern')
def api_insurance_pool_intern():
    err = _require_insurance_json()
    if err:
        return err
    try:
        today = _pool_today()
    except ValueError:
        return jsonify({'success': False, 'message': 'today 需为 YYYY-MM-DD'}), 400
    items = insurance_domain.query_intern_pool(models.get_db(), today=today, **_pool_filters())
    return _pool_response('intern', items)


# ============================================================
# P5：独立推送页 + 实习就业险办结页
# ============================================================
def _build_export_workbook(conn, records, title, header_color):
    """生成参保/办结导出 Excel（姓名/身份证号/校别/班级/专业/学生电话）。"""
    student_ids = [r['student_id'] for r in records]
    placeholders = ','.join('?' * len(student_ids))
    rows = conn.execute(
        f'''SELECT id, name, id_card, school, admin_class, major, student_phone
            FROM students WHERE id IN ({placeholders})''',
        student_ids
    ).fetchall()
    info = {row['id']: dict(row) for row in rows}
    school_names = {st['code']: st['name'] for st in models.get_school_types()}

    wb = Workbook()
    ws = wb.active
    ws.title = title
    ws.append(['姓名', '身份证号', '校别', '班级', '专业', '学生电话'])
    fill = PatternFill('solid', fgColor=header_color)
    for cell in ws[1]:
        cell.font = Font(bold=True, color='FFFFFF')
        cell.fill = fill
        cell.alignment = Alignment(horizontal='center', vertical='center')

    for rec in records:
        student = info.get(rec['student_id']) or {}
        ws.append([
            student.get('name', '') or '',
            student.get('id_card', '') or '',
            school_names.get(student.get('school'), student.get('school') or ''),
            student.get('admin_class', '') or '',
            student.get('major', '') or '',
            student.get('student_phone', '') or '',
        ])

    for column, width in zip('ABCDEF', (14, 22, 16, 16, 18, 18)):
        ws.column_dimensions[column].width = width

    output = BytesIO()
    wb.save(output)
    output.seek(0)
    return output


@bp.route('/insurance/push')
@_page_roles('admin', 'finance_chief', 'insurance_officer')
def insurance_push():
    """独立推送页：把学平险全部办结的学生推入实习就业险。"""
    return render_template('insurance/push.html',
                           filter_values=models.get_filter_values(),
                           current_school_year=config.CURRENT_SCHOOL_YEAR)


@bp.route('/api/insurance/push/list')
def api_insurance_push_list():
    err = _require_roles_json('admin', 'finance_chief', 'insurance_officer')
    if err:
        return err
    conn = models.get_db()
    items = insurance_domain.query_push_pool(conn, **_pool_filters())
    return jsonify({'success': True, 'total': len(items), 'items': items})


@bp.route('/api/insurance/push/start', methods=['POST'])
def api_insurance_push_start():
    """推送：为选中学生创建实习就业险待缴费记录。"""
    err = _require_roles_json('admin', 'finance_chief', 'insurance_officer')
    if err:
        return err
    data = request.get_json(silent=True) or {}
    student_ids = data.get('student_ids') or []
    if not student_ids:
        return jsonify({'success': False, 'message': '请选择学生'}), 400

    conn = models.get_db()
    operator = session.get('username', '')
    try:
        result = insurance_domain.push_to_intern(
            conn, student_ids, operator=operator)
        conn.commit()
    except Exception as e:
        conn.rollback()
        return jsonify({'success': False, 'message': str(e)}), 500
    return jsonify({'success': True, 'count': result['created'],
                    'skipped': result['skipped']})


@bp.route('/api/insurance/push/renewal', methods=['POST'])
def api_insurance_push_renewal():
    """推送至续保：为推迟注册学生追加下一批学平险记录。"""
    err = _require_roles_json('admin', 'finance_chief', 'insurance_officer')
    if err:
        return err
    data = request.get_json(silent=True) or {}
    student_ids = data.get('student_ids') or []
    if not student_ids:
        return jsonify({'success': False, 'message': '请选择学生'}), 400

    conn = models.get_db()
    operator = session.get('username', '')
    try:
        result = insurance_domain.push_to_renewal(
            conn, student_ids, operator=operator)
        conn.commit()
    except Exception as e:
        conn.rollback()
        return jsonify({'success': False, 'message': str(e)}), 500
    return jsonify({'success': True, 'count': result['created'],
                    'skipped': result['skipped'], 'errors': result['errors']})


@bp.route('/insurance/intern')
@_page_roles('admin', 'finance_chief', 'insurance_officer')
def insurance_intern():
    """实习就业险办结页。"""
    return render_template('insurance/intern.html',
                           filter_values=models.get_filter_values(),
                           current_school_year=config.CURRENT_SCHOOL_YEAR)


@bp.route('/api/insurance/intern/list')
def api_insurance_intern_list():
    """实习办结列表：pending=待审批/已缴费/已审批，done=已办结。"""
    err = _require_roles_json('admin', 'finance_chief', 'insurance_officer')
    if err:
        return err

    s = insurance_domain.InsuranceStatus
    tab = (request.args.get('status') or 'pending').strip()
    conn = models.get_db()
    if tab == 'done':
        statuses = (s.DONE,)
    elif tab == 'exempt':
        statuses = (s.EXEMPT,)
    elif tab == 'all':
        statuses = None
    else:
        statuses = (s.PENDING_APPROVE, s.PAID, s.APPROVED)
    items = insurance_domain.query_intern_stage(
        conn, statuses=statuses, **_pool_filters())
    return jsonify({'success': True, 'total': len(items), 'items': items,
                    'summary': insurance_domain.intern_stage_counts(conn)})


@bp.route('/api/insurance/intern/approve', methods=['POST'])
def api_insurance_intern_approve():
    """实习险审批通过：pending_approve / paid → approved。"""
    err = _require_roles_json('admin', 'finance_chief')
    if err:
        return err
    data = request.get_json(silent=True) or {}
    record_ids = data.get('record_ids') or []
    if not record_ids:
        return jsonify({'success': False, 'message': '请选择学生'}), 400

    conn = models.get_db()
    operator = session.get('username', '')
    done = skipped = 0
    try:
        for rid in record_ids:
            try:
                insurance_domain.approve(conn, rid, operator=operator)
                done += 1
            except insurance_domain.InsuranceTransitionError:
                skipped += 1
        conn.commit()
    except Exception as e:
        conn.rollback()
        return jsonify({'success': False, 'message': str(e)}), 500
    return jsonify({'success': True, 'count': done, 'skipped': skipped})


@bp.route('/api/insurance/intern/export')
def api_insurance_intern_export():
    """导出并办结实习就业险：approved → done，写入导出批次。"""
    err = _require_roles_json('admin', 'insurance_officer')
    if err:
        return err

    raw = (request.args.get('record_ids') or '').strip()
    record_ids = [int(p) for p in raw.split(',') if p.strip().isdigit()]
    if not record_ids:
        return jsonify({'success': False, 'message': '请选择学生'}), 400

    conn = models.get_db()
    records = []
    for rid in record_ids:
        rec = insurance_domain.get_record(conn, rid)
        if (not rec
                or rec['insurance_type'] != insurance_domain.INSURANCE_TYPE_INTERN
                or rec['status'] != insurance_domain.InsuranceStatus.APPROVED):
            return jsonify({'success': False,
                            'message': '仅能导出已审批的实习就业险记录'}), 400
        records.append(rec)

    output = _build_export_workbook(conn, records, '实习就业险办结', '0B7285')
    timestamp = datetime.now().strftime('%Y%m%d_%H%M')
    filename = f'实习就业险办结_{timestamp}.xlsx'
    operator = session.get('username', '')
    try:
        insurance_domain.export_records(
            conn, record_ids, action='intern', operator=operator,
            file_name=filename)
        conn.commit()
    except insurance_domain.InsuranceTransitionError as e:
        conn.rollback()
        return jsonify({'success': False, 'message': str(e)}), 400
    except Exception as e:
        conn.rollback()
        return jsonify({'success': False, 'message': str(e)}), 500

    return send_file(
        output,
        mimetype='application/vnd.openxmlformats-officedocument.spreadsheetml.sheet',
        as_attachment=True, download_name=filename)


# ============================================================
# P4：续保页 + 保费催缴页
# ============================================================
@bp.route('/insurance/first')
@_page_roles('admin', 'finance_chief', 'insurance_officer')
def insurance_first():
    """新生首保页：计划批次为「首保」的学生办理、导出与补录保单。"""
    return render_template('insurance/first.html',
                           school_types=models.get_school_types(),
                           filter_values=models.get_filter_values(),
                           current_school_year=config.CURRENT_SCHOOL_YEAR)


def _first_tab_statuses():
    """首保页页签 → 记录状态集合；'' 代表尚未建档。"""
    s = insurance_domain.InsuranceStatus
    tabs = {
        '': ('', s.PENDING_APPROVE),
        'pending': ('', s.PENDING_APPROVE),
        'approved': (s.APPROVED,),
        'exported': (s.EXPORTED,),
        'done': (s.DONE,),
        'exempt': (s.EXEMPT,),
    }
    return tabs.get((request.args.get('status') or '').strip(), tabs[''])


def _first_step(conn, student_id):
    """返回学生首保计划步（batch_no/insurance_type），非首保学生返回 None。"""
    cur = conn.execute(
        'SELECT id, grade, enrollment_year FROM students '
        'WHERE id = ? AND COALESCE(is_deleted, 0) = 0', (student_id,))
    student = cur.fetchone()
    if not student:
        return None
    plan = insurance_domain.build_plan(
        student['enrollment_year'], student['grade'])
    if not plan or plan[0]['stage'] != insurance_domain.STAGE_FIRST:
        return None
    if insurance_domain.batch_now(
            student['enrollment_year'], student['grade']) > plan[0]['batch_no']:
        return None
    return plan[0]


@bp.route('/api/insurance/first/list')
def api_insurance_first_list():
    """首保页列表：按页签状态过滤，返回各页签计数。"""
    err = _require_roles_json('admin', 'finance_chief', 'insurance_officer')
    if err:
        return err

    filters = _pool_filters()
    conn = models.get_db()
    items = insurance_domain.query_first_stage(
        conn, statuses=_first_tab_statuses(), **filters)
    summary = insurance_domain.first_stage_counts(conn, **filters)
    return jsonify({'success': True, 'total': len(items), 'items': items,
                    'summary': summary})


@bp.route('/api/insurance/first/approve', methods=['POST'])
def api_insurance_first_approve():
    """办理首保：确保记录存在后审批通过（未建档 → pending_approve → approved）。"""
    err = _require_roles_json('admin', 'finance_chief')
    if err:
        return err
    data = request.get_json(silent=True) or {}
    student_ids = data.get('student_ids') or []
    if not student_ids:
        return jsonify({'success': False, 'message': '请选择学生'}), 400

    conn = models.get_db()
    operator = session.get('username', '')
    done = skipped = 0
    try:
        for sid in student_ids:
            step = _first_step(conn, sid)
            if not step:
                skipped += 1
                continue
            try:
                rec = insurance_domain.ensure_record(
                    conn, sid, step['batch_no'], step['insurance_type'],
                    operator=operator)
                insurance_domain.approve(conn, rec['id'], operator=operator)
                done += 1
            except insurance_domain.InsuranceTransitionError:
                skipped += 1
        conn.commit()
    except Exception as e:
        conn.rollback()
        return jsonify({'success': False, 'message': str(e)}), 500
    return jsonify({'success': True, 'count': done, 'skipped': skipped})


@bp.route('/api/insurance/first/exempt', methods=['POST'])
def api_insurance_first_exempt():
    """本年度不办理：确保记录存在后标记 exempt。"""
    err = _require_roles_json('admin', 'finance_chief')
    if err:
        return err
    data = request.get_json(silent=True) or {}
    student_ids = data.get('student_ids') or []
    if not student_ids:
        return jsonify({'success': False, 'message': '请选择学生'}), 400

    conn = models.get_db()
    operator = session.get('username', '')
    done = skipped = 0
    try:
        for sid in student_ids:
            step = _first_step(conn, sid)
            if not step:
                skipped += 1
                continue
            try:
                rec = insurance_domain.ensure_record(
                    conn, sid, step['batch_no'], step['insurance_type'],
                    operator=operator)
                insurance_domain.reject(conn, rec['id'], operator=operator)
                done += 1
            except insurance_domain.InsuranceTransitionError:
                skipped += 1
        conn.commit()
    except Exception as e:
        conn.rollback()
        return jsonify({'success': False, 'message': str(e)}), 500
    return jsonify({'success': True, 'count': done, 'skipped': skipped})


@bp.route('/api/insurance/first/export')
def api_insurance_first_export():
    """导出首保参保数据：approved → exported，并写入导出批次。"""
    err = _require_roles_json('admin', 'insurance_officer')
    if err:
        return err

    raw = (request.args.get('record_ids') or '').strip()
    record_ids = [int(p) for p in raw.split(',') if p.strip().isdigit()]
    if not record_ids:
        return jsonify({'success': False, 'message': '请选择学生'}), 400

    conn = models.get_db()
    records = []
    for rid in record_ids:
        rec = insurance_domain.get_record(conn, rid)
        if (not rec
                or rec['insurance_type'] != insurance_domain.INSURANCE_TYPE_XUEPING
                or rec['status'] != insurance_domain.InsuranceStatus.APPROVED):
            return jsonify({'success': False,
                            'message': '仅能导出已办理的首保记录'}), 400
        records.append(rec)

    output = _build_export_workbook(conn, records, '新生首保参保数据', '6F42C1')
    timestamp = datetime.now().strftime('%Y%m%d_%H%M')
    filename = f'新生首保参保数据_{timestamp}.xlsx'
    operator = session.get('username', '')
    try:
        insurance_domain.export_records(
            conn, record_ids, action='first', operator=operator,
            file_name=filename)
        conn.commit()
    except insurance_domain.InsuranceTransitionError as e:
        conn.rollback()
        return jsonify({'success': False, 'message': str(e)}), 400
    except Exception as e:
        conn.rollback()
        return jsonify({'success': False, 'message': str(e)}), 500

    return send_file(
        output,
        mimetype='application/vnd.openxmlformats-officedocument.spreadsheetml.sheet',
        as_attachment=True, download_name=filename)


@bp.route('/api/insurance/first/policy', methods=['POST'])
def api_insurance_first_policy():
    """补录保单：exported → done，写入保单号与到期日期。"""
    err = _require_roles_json('admin', 'insurance_officer')
    if err:
        return err
    data = request.get_json(silent=True) or {}
    record_ids = data.get('record_ids') or []
    policy_no = (data.get('policy_no') or '').strip()
    expiry_date = data.get('expiry_date') or ''
    if not record_ids:
        return jsonify({'success': False, 'message': '请选择学生'}), 400
    if not policy_no:
        return jsonify({'success': False, 'message': '请输入保单号'}), 400
    if not expiry_date:
        return jsonify({'success': False, 'message': '请选择到期日期'}), 400

    conn = models.get_db()
    operator = session.get('username', '')
    done = skipped = 0
    try:
        for rid in record_ids:
            try:
                insurance_domain.fill_policy(
                    conn, rid, policy_no, expiry_date, operator=operator)
                done += 1
            except insurance_domain.InsuranceTransitionError:
                skipped += 1
        conn.commit()
    except Exception as e:
        conn.rollback()
        return jsonify({'success': False, 'message': str(e)}), 500
    return jsonify({'success': True, 'count': done, 'skipped': skipped})


@bp.route('/insurance/renewal')
@_page_roles('admin', 'finance_chief', 'insurance_officer')
def insurance_renewal():
    """第二年续保页：到期前 30 天（及同月批次）学生列表。"""
    return render_template('insurance/renewal.html',
                           filter_values=models.get_filter_values(),
                           current_school_year=config.CURRENT_SCHOOL_YEAR)


@bp.route('/api/insurance/renewal/list')
def api_insurance_renewal_list():
    err = _require_roles_json('admin', 'finance_chief', 'insurance_officer')
    if err:
        return err

    try:
        window_days = int(request.args.get('window_days', 30))
    except (TypeError, ValueError):
        window_days = 30
    window_days = max(1, min(window_days, 365))

    today = _pool_today()
    conn = models.get_db()
    items = insurance_domain.query_renewal_pool(
        conn, today=today, window_days=window_days, **_pool_filters())
    summary = {
        insurance_domain.URGENCY_OVERDUE: 0,
        insurance_domain.URGENCY_DUE: 0,
        insurance_domain.URGENCY_MISSING_EXPIRY: 0,
        'total': len(items),
    }
    for item in items:
        key = item.get('urgency')
        if key in summary:
            summary[key] += 1
    return jsonify({'success': True, 'total': len(items), 'items': items,
                    'summary': summary, 'today': today.isoformat(),
                    'window_days': window_days})


@bp.route('/api/insurance/renewal/start', methods=['POST'])
def api_insurance_renewal_start():
    """进入参保流程：为选中学生创建下一批学平险待缴费记录。"""
    err = _require_roles_json('admin', 'finance_chief', 'insurance_officer')
    if err:
        return err
    data = request.get_json(silent=True) or {}
    student_ids = data.get('student_ids') or []
    if not student_ids:
        return jsonify({'success': False, 'message': '请选择学生'}), 400

    conn = models.get_db()
    operator = session.get('username', '')
    done = skipped = 0
    errors = []
    try:
        for sid in student_ids:
            try:
                insurance_domain.start_renewal(conn, sid, operator=operator)
                done += 1
            except insurance_domain.InsuranceTransitionError as e:
                skipped += 1
                errors.append({'student_id': sid, 'message': str(e)})
        conn.commit()
    except Exception as e:
        conn.rollback()
        return jsonify({'success': False, 'message': str(e)}), 500
    return jsonify({'success': True, 'count': done, 'skipped': skipped,
                    'errors': errors[:20]})


@bp.route('/api/insurance/renewal/exempt', methods=['POST'])
def api_insurance_renewal_exempt():
    """放弃本期续保：把下一个待续保批次标记为 exempt。

    仅 admin / 财务主管可操作；放弃只终结该批次，学生后续批次仍自动投保。
    """
    err = _require_roles_json('admin', 'finance_chief')
    if err:
        return err
    data = request.get_json(silent=True) or {}
    student_ids = data.get('student_ids') or []
    if not student_ids:
        return jsonify({'success': False, 'message': '请选择学生'}), 400

    conn = models.get_db()
    operator = session.get('username', '')
    done = skipped = 0
    errors = []
    try:
        for sid in student_ids:
            try:
                insurance_domain.exempt_renewal_batch(
                    conn, sid, operator=operator)
                done += 1
            except insurance_domain.InsuranceTransitionError as e:
                skipped += 1
                errors.append({'student_id': sid, 'message': str(e)})
        conn.commit()
    except Exception as e:
        conn.rollback()
        return jsonify({'success': False, 'message': str(e)}), 500
    return jsonify({'success': True, 'count': done, 'skipped': skipped,
                    'errors': errors[:20]})


@bp.route('/api/insurance/renewal/stage')
def api_insurance_renewal_stage():
    """续保环节：尚未走完流程的续保记录（待导出 / 待补录保单）。"""
    err = _require_roles_json('admin', 'finance_chief', 'insurance_officer')
    if err:
        return err

    s = insurance_domain.InsuranceStatus
    tabs = {
        'approved': (s.APPROVED,),
        'exported': (s.EXPORTED,),
    }
    tab = (request.args.get('status') or '').strip()
    statuses = tabs.get(tab, (s.APPROVED, s.EXPORTED))

    conn = models.get_db()
    items = insurance_domain.query_renewal_stage(
        conn, statuses=statuses, **_pool_filters())
    # summary 与列表同口径（同样携带搜索/筛选条件），避免人数虚高
    if tab:
        summary = {tab: len(items)}
    else:
        summary = {key: 0 for key in tabs}
        for item in items:
            for key, values in tabs.items():
                if item['source_status'] in values:
                    summary[key] += 1
    return jsonify({'success': True, 'total': len(items), 'items': items,
                    'summary': summary})


@bp.route('/api/insurance/renewal/export')
def api_insurance_renewal_export():
    """导出续保参保名单：approved → exported，等待保险公司返回保单。"""
    err = _require_roles_json('admin', 'finance_chief', 'insurance_officer')
    if err:
        return err

    raw = (request.args.get('record_ids') or '').strip()
    record_ids = [int(p) for p in raw.split(',') if p.strip().isdigit()]
    if not record_ids:
        return jsonify({'success': False, 'message': '请选择记录'}), 400

    conn = models.get_db()
    records = []
    for rid in record_ids:
        rec = insurance_domain.get_record(conn, rid)
        if (not rec
                or rec['insurance_type'] != insurance_domain.INSURANCE_TYPE_XUEPING
                or rec['status'] != insurance_domain.InsuranceStatus.APPROVED):
            return jsonify({'success': False,
                            'message': '仅能导出待导出的续保记录'}), 400
        records.append(rec)

    output = _build_export_workbook(conn, records, '学平险续保', '0D6EFD')
    timestamp = datetime.now().strftime('%Y%m%d_%H%M')
    filename = f'续保参保数据_{timestamp}.xlsx'
    operator = session.get('username', '')
    try:
        insurance_domain.export_records(
            conn, record_ids, action='renewal', operator=operator,
            file_name=filename)
        conn.commit()
    except insurance_domain.InsuranceTransitionError as e:
        conn.rollback()
        return jsonify({'success': False, 'message': str(e)}), 400
    except Exception as e:
        conn.rollback()
        return jsonify({'success': False, 'message': str(e)}), 500

    return send_file(
        output,
        mimetype='application/vnd.openxmlformats-officedocument.spreadsheetml.sheet',
        as_attachment=True, download_name=filename)


@bp.route('/api/insurance/renewal/policy', methods=['POST'])
def api_insurance_renewal_policy():
    """补录续保保单：exported → done，支持统一套用或逐人填写。"""
    err = _require_roles_json('admin', 'finance_chief', 'insurance_officer')
    if err:
        return err

    data = request.get_json(silent=True) or {}
    shared_no = (data.get('policy_no') or '').strip()
    shared_expiry = (data.get('expiry_date') or '').strip()

    pairs = []
    for item in data.get('items') or []:
        pairs.append((item.get('record_id'),
                      (item.get('policy_no') or shared_no),
                      (item.get('expiry_date') or shared_expiry)))
    if not pairs:
        for rid in data.get('record_ids') or []:
            pairs.append((rid, shared_no, shared_expiry))
    if not pairs:
        return jsonify({'success': False, 'message': '请选择记录'}), 400

    conn = models.get_db()
    operator = session.get('username', '')
    done = 0
    try:
        for rid, policy_no, expiry in pairs:
            insurance_domain.fill_policy(
                conn, rid, policy_no, expiry, operator=operator)
            done += 1
        conn.commit()
    except insurance_domain.InsuranceTransitionError as e:
        conn.rollback()
        return jsonify({'success': False, 'message': str(e)}), 400
    except Exception as e:
        conn.rollback()
        return jsonify({'success': False, 'message': str(e)}), 500
    return jsonify({'success': True, 'count': done})


@bp.route('/insurance/collection')
@_page_roles('admin', 'finance_chief', 'finance_staff')
def insurance_collection():
    """保费催缴页：录入学平险/实习险缴费日期。"""
    return render_template('insurance/collection.html',
                           filter_values=models.get_filter_values(),
                           current_school_year=config.CURRENT_SCHOOL_YEAR)


def _collection_counts(conn):
    return {
        'pending': len(insurance_domain.query_collection_stage(conn, paid=False)),
        'paid': len(insurance_domain.query_collection_stage(conn, paid=True)),
    }


@bp.route('/api/insurance/collection/list')
def api_insurance_collection_list():
    """催缴列表：pending=待缴费，paid=已缴费（只读）。

    催缴对象：学平险续保记录 + 已确认办理的实习就业险记录；
    首保的缴费在首保审批环节确定，不进入催缴。
    """
    err = _require_roles_json('admin', 'finance_chief', 'finance_staff')
    if err:
        return err

    tab = (request.args.get('status') or 'pending').strip()
    insurance_type = (request.args.get('insurance_type') or '').strip()
    types = None
    if insurance_type in (insurance_domain.INSURANCE_TYPE_XUEPING,
                          insurance_domain.INSURANCE_TYPE_INTERN):
        types = (insurance_type,)
    conn = models.get_db()
    items = insurance_domain.query_collection_stage(
        conn, paid=(tab == 'paid'), insurance_types=types, **_pool_filters())
    return jsonify({'success': True, 'total': len(items), 'items': items,
                    'summary': _collection_counts(conn)})


@bp.route('/api/insurance/collection/export')
def api_insurance_collection_export():
    """导出待缴费名单（只读，不改变记录状态）。"""
    err = _require_roles_json('admin', 'finance_chief', 'finance_staff')
    if err:
        return err

    insurance_type = (request.args.get('insurance_type') or '').strip()
    types = None
    if insurance_type in (insurance_domain.INSURANCE_TYPE_XUEPING,
                          insurance_domain.INSURANCE_TYPE_INTERN):
        types = (insurance_type,)

    conn = models.get_db()
    items = insurance_domain.query_collection_stage(
        conn, paid=False, insurance_types=types, **_pool_filters())
    if not items:
        return jsonify({'success': False, 'message': '没有待缴费记录可导出'}), 400

    school_names = {st['code']: st['name'] for st in models.get_school_types()}
    type_labels = {
        insurance_domain.INSURANCE_TYPE_XUEPING: '学平险',
        insurance_domain.INSURANCE_TYPE_INTERN: '实习就业险',
    }

    wb = Workbook()
    ws = wb.active
    ws.title = '待缴费名单'
    ws.append(['姓名', '学号', '身份证号', '校别', '班级', '专业', '年级', '险种', '批次', '状态'])
    fill = PatternFill('solid', fgColor='0D6EFD')
    for cell in ws[1]:
        cell.font = Font(bold=True, color='FFFFFF')
        cell.fill = fill
        cell.alignment = Alignment(horizontal='center', vertical='center')

    for item in items:
        ws.append([
            item.get('name') or '',
            item.get('student_no') or '',
            item.get('id_card') or '',
            school_names.get(item.get('school'), item.get('school') or ''),
            item.get('admin_class') or '',
            item.get('major') or '',
            item.get('grade') or '',
            type_labels.get(item.get('insurance_type'), item.get('insurance_type') or ''),
            f"第{item.get('batch_no')}年" if item.get('batch_no') else '',
            '待缴费',
        ])

    for column, width in zip('ABCDEFGHIJ', (12, 16, 22, 14, 14, 18, 10, 12, 10, 10)):
        ws.column_dimensions[column].width = width

    output = BytesIO()
    wb.save(output)
    output.seek(0)
    timestamp = datetime.now().strftime('%Y%m%d_%H%M')
    filename = f'待缴费名单_{timestamp}.xlsx'
    return send_file(
        output,
        mimetype='application/vnd.openxmlformats-officedocument.spreadsheetml.sheet',
        as_attachment=True, download_name=filename)


@bp.route('/api/insurance/collection/pay', methods=['POST'])
def api_insurance_collection_pay():
    """登记缴费日期（不改变保险状态）：支持统一日期或逐条日期。"""
    err = _require_roles_json('admin', 'finance_chief', 'finance_staff')
    if err:
        return err
    data = request.get_json(silent=True) or {}
    shared_date = (data.get('pay_date') or '').strip()
    shared_premium = data.get('premium')

    pairs = []
    for item in data.get('items') or []:
        pairs.append((item.get('record_id'),
                      item.get('pay_date') or shared_date,
                      item.get('premium', shared_premium)))
    if not pairs:
        for rid in data.get('record_ids') or []:
            pairs.append((rid, shared_date, shared_premium))
    if not pairs:
        return jsonify({'success': False, 'message': '请选择记录'}), 400

    conn = models.get_db()
    operator = session.get('username', '')
    done = skipped = 0
    try:
        for rid, pay_date, premium in pairs:
            try:
                insurance_domain.record_payment(
                    conn, rid, pay_date, operator=operator,
                    premium=premium if premium not in (None, '') else None)
                done += 1
            except insurance_domain.InsuranceTransitionError:
                skipped += 1
        conn.commit()
    except Exception as e:
        conn.rollback()
        return jsonify({'success': False, 'message': str(e)}), 500
    return jsonify({'success': True, 'count': done, 'skipped': skipped})


# ============================================================
# P6：记录查询 / 撤销中心
# ============================================================
@bp.route('/insurance/records')
@_page_roles(*_INSURANCE_POOL_ROLES)
def insurance_records():
    """保险记录查询（新查询页，数据源 insurance_records）。"""
    return render_template('insurance/records.html',
                           filter_values=models.get_filter_values(),
                           current_school_year=config.CURRENT_SCHOOL_YEAR)


@bp.route('/api/insurance/records/list')
def api_insurance_records_list():
    err = _require_insurance_json()
    if err:
        return err
    insurance_type = (request.args.get('insurance_type') or '').strip()
    status = (request.args.get('status') or '').strip()
    conn = models.get_db()
    items = insurance_domain.query_insurance_records(
        conn, insurance_type=insurance_type or None,
        status=status or None, **_pool_filters())
    return jsonify({'success': True, 'total': len(items), 'items': items})


@bp.route('/insurance/revert')
@auth.admin_required
def insurance_revert():
    """撤销中心：导出批次与操作日志。"""
    return render_template('insurance/revert.html')


@bp.route('/api/insurance/revert/batches')
def api_insurance_revert_batches():
    err = _require_admin_json()
    if err:
        return err
    try:
        limit = int(request.args.get('limit', 100))
    except (TypeError, ValueError):
        limit = 100
    items = insurance_domain.list_export_batches(models.get_db(),
                                                 limit=max(1, min(limit, 500)))
    return jsonify({'success': True, 'total': len(items), 'items': items})


@bp.route('/api/insurance/revert/batch', methods=['POST'])
def api_insurance_revert_batch():
    err = _require_admin_json()
    if err:
        return err
    data = request.get_json(silent=True) or {}
    batch_id = (data.get('export_batch_id') or '').strip()
    if not batch_id:
        return jsonify({'success': False, 'message': '缺少导出批次'}), 400

    conn = models.get_db()
    operator = session.get('username', '')
    try:
        count = insurance_domain.revert_export(conn, batch_id, operator=operator)
        conn.commit()
    except insurance_domain.InsuranceTransitionError as e:
        conn.rollback()
        return jsonify({'success': False, 'message': str(e)}), 400
    except Exception as e:
        conn.rollback()
        return jsonify({'success': False, 'message': str(e)}), 500
    return jsonify({'success': True, 'count': count})


@bp.route('/api/insurance/revert/students')
def api_insurance_revert_students():
    """按学生撤销：搜索学生及其最近一条保险记录。"""
    err = _require_admin_json()
    if err:
        return err
    search = (request.args.get('search') or '').strip()
    items = insurance_domain.search_student_last_records(models.get_db(), search)
    return jsonify({'success': True, 'total': len(items), 'items': items})


@bp.route('/api/insurance/revert/student', methods=['POST'])
def api_insurance_revert_student():
    """按学生撤销：删除最近一条保险记录，回到办理前状态（日志保留）。"""
    err = _require_admin_json()
    if err:
        return err
    data = request.get_json(silent=True) or {}
    try:
        student_id = int(data.get('student_id'))
    except (TypeError, ValueError):
        return jsonify({'success': False, 'message': '缺少学生ID'}), 400

    conn = models.get_db()
    try:
        rec = insurance_domain.revert_student(
            conn, student_id, operator=session.get('username', ''))
        conn.commit()
    except insurance_domain.InsuranceTransitionError as e:
        conn.rollback()
        return jsonify({'success': False, 'message': str(e)}), 400
    except Exception as e:
        conn.rollback()
        return jsonify({'success': False, 'message': str(e)}), 500
    return jsonify({
        'success': True,
        'record': {
            'batch_no': rec['batch_no'],
            'insurance_type': rec['insurance_type'],
            'status': rec['status'],
        },
    })


@bp.route('/api/insurance/revert/logs')
def api_insurance_revert_logs():
    err = _require_admin_json()
    if err:
        return err
    try:
        limit = int(request.args.get('limit', 200))
    except (TypeError, ValueError):
        limit = 200
    action = (request.args.get('action') or '').strip()
    search = (request.args.get('search') or '').strip()
    items = insurance_domain.list_operation_logs(
        models.get_db(), limit=limit, action=action, search=search)
    return jsonify({'success': True, 'total': len(items), 'items': items})


def _pay_date_year(pd):
    """从学平险缴费日期提取年份，兼容 'YYYY-MM-DD' 字符串 与 Excel 日期序列号（如 45893）。

    空值 / 外部占位标记('新生默认首保') / 无法解析 均返回 None。
    背景：导入时 Excel 日期常被读成 int 序列号存库（如 45893），前端 formatDate 会转成
    2025-08-24 显示，故锁定逻辑必须按同一规则还原年份，否则 pd[:4] 误读为 4589 导致年份比较失效。
    """
    if pd is None:
        return None
    s = str(pd).strip()
    if s == '' or s == config.InsuranceStatus.EXTERNAL_PAY_MARKER:
        return None
    # YYYY-MM-DD
    if len(s) >= 5 and s[:4].isdigit() and s[4] == '-':
        try:
            return int(s[:4])
        except ValueError:
            return None
    # Excel 日期序列号（与前端 formatDate 的 (num-25569)*86400000 等价）
    try:
        serial = float(s)
        if 10000 < serial < 100000:  # 合理序列号范围（约 1927~2137 年）
            d = datetime(1899, 12, 30) + timedelta(days=serial)
            return d.year
    except (ValueError, TypeError, OverflowError):
        pass
    return None


def _student_batch(s):
    """审批列表用：计算学生当前年级批次（第几年），返回 1~3 或 None。"""
    return compute_insurance_batch(s.get('grade'), s.get('enrollment_year'))


def _annotate_xueping_lock(s):
    """审批页：计算学生“按计划办理学平险”按钮是否应锁定，以及锁定原因提示。

    锁定条件（任一即锁）：
      1) 已交实习就业险（hasInternDate）——该生已走完第3年流程，无需再办学平险；
      2) 学平险缴费日期为「新生默认首保」占位标记（且实习未交）——提示先去登记页录第2期真实日期；
      3) 主管标记“YYYY年度学平险已办结”且 YYYY >= 学平险缴费日期年份——同年（或回溯跨边界）已办完，无需重复办理。
         仅当 标记年份 < 缴费日期年份（即已录入下一年真实日期）时才放行，允许办理第2年学平险。

    flag 年份 = CURRENT_SCHOOL_YEAR（日历年）；缴费日期年份取其前4位。
    年份既不相等也不小于（如回溯跨学年边界导致 标记年 > 缴费年）时按锁定处理（安全侧），避免误放行重复办理。
    后端为唯一真源，前端仅读取 xueping_locked / xueping_lock_hint，避免前后端解析不一致。
    """
    flag = s.get('supervisor_flag')
    pd = s.get('student_insurance_pay_date')
    has_intern = not _is_blank(s.get('internship_insurance_pay_date'))
    pay_external = (pd == config.InsuranceStatus.EXTERNAL_PAY_MARKER)
    # 同年（或标记年更大）学平险已办结
    flag_year = config.InsuranceStatus.parse_flag_year(flag)
    is_xp_done = config.InsuranceStatus.done_type(flag) == 'xueping'
    pay_year = _pay_date_year(pd)
    xp_done_lock = bool(
        is_xp_done and flag_year is not None and pay_year is not None and flag_year >= pay_year
    )
    locked = bool(has_intern or pay_external or xp_done_lock)
    if has_intern:
        hint = '该生已交实习就业险，无需再办理学平险'
    elif pay_external:
        hint = '请录入该生2期学平险缴费日期'
    elif xp_done_lock:
        hint = f'该生{flag_year}年度学平险已办结，无需重复办理'
    else:
        hint = None
    s['xueping_locked'] = locked
    s['xueping_lock_hint'] = hint


@bp.route('/api/insurance/approve-tree')
@auth.finance_chief_required
def api_insurance_approve_tree():
    """保险审批tree相关接口。"""
    search = request.args.get('search', '').strip()

    conditions = [
        "s.is_deleted = 0",
        "(s.registration_status IS NULL OR s.registration_status != '毕业')",
        "((s.student_insurance_pay_date IS NOT NULL AND s.student_insurance_pay_date != '') OR (s.internship_insurance_pay_date IS NOT NULL AND s.internship_insurance_pay_date != '') OR (s.supervisor_flag IS NULL OR s.supervisor_flag = ''))",
        f"(s.supervisor_flag IS NULL OR s.supervisor_flag = '' OR (s.supervisor_flag NOT IN ('{config.InsuranceStatus.PENDING_XUEPING}', '{config.InsuranceStatus.EXEMPT}', '{config.InsuranceStatus.PENDING_INTERN}') AND s.supervisor_flag NOT LIKE '%实习就业险%办结%'))"
    ]
    params = []

    if search:
        conditions.append('(s.name LIKE ? OR s.student_no LIKE ? OR s.id_card LIKE ? OR s.name_pinyin LIKE ?)')
        like = f'%{search}%'
        params.extend([like, like, like, like])

    where = ' AND '.join(conditions)

    rows = models.get_db().execute(
        f'''SELECT s.id, s.name, s.student_no, s.id_card, s.grade, s.admin_class,
                   s.major, s.enrollment_year, s.school,
                   s.student_insurance_pay_date, s.student_insurance_expiry,
                   s.student_insurance_no, s.insurance_premium,
                   s.internship_insurance_pay_date, s.supervisor_flag,
                   s.declaration_date
            FROM students s
            WHERE {where}
            ORDER BY
                CASE WHEN s.student_insurance_expiry IS NULL OR s.student_insurance_expiry = ''
                     THEN '9999-99-99'
                     ELSE s.student_insurance_expiry
                END ASC,
                s.admin_class, s.name''',
        params
    ).fetchall()

    students = [dict(r) for r in rows]

    # 第3年学生不再办学平险，只审批已缴纳实习就业险的；
    # 第2年提前办实习险的特殊学生不受影响（本过滤仅针对第3年）。
    # batch=1 空标记学生（外部参保）虽无缴费日期，也需在审批页显示，由主管确认。
    students = [s for s in students
                if (not (_student_batch(s) == 3 and _is_blank(s.get('internship_insurance_pay_date'))))
                and ((not _is_blank(s.get('student_insurance_pay_date')) or not _is_blank(s.get('internship_insurance_pay_date')))
                     or (_student_batch(s) == 1 and _is_blank(s.get('supervisor_flag'))))]

    # 后端计算“按计划办理学平险”按钮锁定状态与提示，随学生数据下发（前端仅读取）
    for s in students:
        _annotate_xueping_lock(s)
        s['batch'] = _student_batch(s)

    tree = {}
    for s in students:
        expiry = s.get('student_insurance_expiry') or '未设置到期时间'
        if expiry not in tree:
            tree[expiry] = []
        tree[expiry].append(s)

    return jsonify({'tree': tree})


@bp.route('/api/insurance/approve-single', methods=['POST'])
@auth.finance_chief_required
def api_insurance_approve_single():
    """保险审批single相关接口（旧版，已下线）。"""
    return jsonify({'success': False,
                    'message': '旧版保险审批接口已下线，请使用新版审批接口'}), 410
    data = request.get_json()
    if not data:
        return jsonify({'error': '无效数据'}), 400

    student_id = data.get('student_id')
    action = data.get('action', 'approve')

    if not student_id:
        return jsonify({'error': '缺少学生ID'}), 400
    if action not in ('approve', 'reject', 'internship'):
        return jsonify({'error': '操作无效'}), 400

    if action == 'approve':
        flag_value = config.InsuranceStatus.PENDING_XUEPING
    elif action == 'internship':
        flag_value = config.InsuranceStatus.PENDING_INTERN
    else:
        flag_value = config.InsuranceStatus.EXEMPT

    fields = {'supervisor_flag': flag_value}
    # batch=1 空标记学生经审批确认参保(外部)：补占位缴费日期，进入待办页（与正常流一致）
    if action == 'approve':
        cur = models.get_db().execute(
            'SELECT grade, enrollment_year, student_insurance_pay_date FROM students WHERE id = ?',
            (student_id,)
        ).fetchone()
        if cur and compute_insurance_batch(cur['grade'], cur['enrollment_year']) == 1 \
           and _is_blank(cur['student_insurance_pay_date']):
            fields['student_insurance_pay_date'] = config.InsuranceStatus.EXTERNAL_PAY_MARKER

    try:
        models.update_student_insurance(student_id, fields)
        return jsonify({'success': True})
    except Exception as e:
        return jsonify({'error': str(e)}), 500


@bp.route('/api/insurance/approve-summary')
@auth.finance_chief_required
def api_insurance_approve_summary():
    """保险审批汇总相关接口。"""
    conn = models.get_db()

    rows = conn.execute(
        '''SELECT s.school, s.supervisor_flag, COUNT(*) as cnt
           FROM students s
           WHERE s.is_deleted = 0
             AND s.supervisor_flag LIKE '%办结%'
           GROUP BY s.school, s.supervisor_flag
           ORDER BY s.school, s.supervisor_flag'''
    ).fetchall()

    # 按 school_types 动态初始化分组，新增学校类别也能显示汇总
    by_school = {st['code']: {'students': [], 'interns': [], 'total': 0}
                 for st in models.get_school_types()}

    for r in rows:
        school = r[0]
        if school not in by_school:
            by_school[school] = {'students': [], 'interns': [], 'total': 0}
        flag = r[1]
        count = r[2]
        kind = '学平险' if '学平险' in flag else '实习就业险'

        detail = {
            'id': r[1],
            'label': flag,
            'kind': kind,
            'count': count
        }

        if kind == '学平险':
            by_school[school]['students'].append(detail)
        else:
            by_school[school]['interns'].append(detail)
        by_school[school]['total'] += count

    return jsonify({
        'schools': {str(k): v for k, v in by_school.items()},
        'grand_total': sum(v['total'] for v in by_school.values())
    })


@bp.route('/api/insurance/approve', methods=['POST'])
@auth.finance_chief_required
def api_insurance_approve():
    """保险审批相关接口（旧版，已下线）。"""
    return jsonify({'success': False,
                    'message': '旧版保险审批接口已下线，请使用新版审批接口'}), 410
    data = request.get_json()
    if not data:
        return jsonify({'error': '无效数据'}), 400

    student_ids = data.get('student_ids', [])
    action = data.get('action', 'approve')

    if not student_ids:
        return jsonify({'error': '请选择学生'}), 400

    if action not in ('approve', 'reject', 'internship'):
        return jsonify({'error': '操作无效'}), 400

    if action == 'approve':
        flag_value = config.InsuranceStatus.PENDING_XUEPING
    elif action == 'internship':
        flag_value = config.InsuranceStatus.PENDING_INTERN
    else:
        flag_value = config.InsuranceStatus.EXEMPT

    try:
        if action == 'approve':
            # 逐人处理：batch=1 空标记补占位缴费日期
            done = 0
            for sid in student_ids:
                cur = models.get_db().execute(
                    'SELECT grade, enrollment_year, student_insurance_pay_date FROM students WHERE id = ?',
                    (sid,)
                ).fetchone()
                if not cur:
                    continue
                f = {'supervisor_flag': flag_value}
                if compute_insurance_batch(cur['grade'], cur['enrollment_year']) == 1 \
                   and _is_blank(cur['student_insurance_pay_date']):
                    f['student_insurance_pay_date'] = config.InsuranceStatus.EXTERNAL_PAY_MARKER
                models.update_student_insurance(sid, f)
                done += 1
            return jsonify({'success': True, 'count': done})
        else:
            models.batch_update_student_insurance(student_ids, {'supervisor_flag': flag_value})
            return jsonify({'success': True, 'count': len(student_ids)})
    except Exception as e:
        return jsonify({'error': str(e)}), 500


@bp.route('/insurance/todo')
@auth.login_required
def insurance_todo():
    """旧版待办页已下线，统一重定向到新版新生首保页。"""
    return redirect(url_for('insurance.insurance_first'))


@bp.route('/api/insurance/todo/export')
@auth.insurance_officer_required
def api_insurance_todo_export():
    """保险待办导出相关接口。尊重当前搜索筛选（与待办列表一致）。"""
    search = request.args.get('search', '').strip()

    conditions = [
        's.is_deleted = 0',
        "(s.registration_status IS NULL OR s.registration_status != '毕业')",
        f"s.supervisor_flag IN ('{config.InsuranceStatus.PENDING_XUEPING}', '{config.InsuranceStatus.PENDING_INTERN}')"
    ]
    params = []
    if search:
        conditions.append('(s.name LIKE ? OR s.student_no LIKE ? OR s.id_card LIKE ? OR s.name_pinyin LIKE ?)')
        like = f'%{search}%'
        params.extend([like, like, like, like])

    where = ' AND '.join(conditions)

    conn = models.get_db()
    rows = conn.execute(
        f'''SELECT s.* FROM students s
            WHERE {where}
            ORDER BY s.school, s.admin_class, s.name''',
        params
    ).fetchall()

    students = [dict(r) for r in rows]

    visible_fields = auth.get_visible_fields()
    export_fields = None if visible_fields == ['*'] else visible_fields

    for student in students:
        student['age'] = utils.calculate_age(student.get('birth_date'))

    wb = utils.export_students_to_excel(students, export_fields)
    output = BytesIO()
    wb.save(output)
    output.seek(0)

    timestamp = datetime.now().strftime('%Y%m%d_%H%M')
    filename = f'保险待办导出_{timestamp}.xlsx'

    return send_file(output, mimetype='application/vnd.openxmlformats-officedocument.spreadsheetml.sheet',
                     as_attachment=True, download_name=filename)


@bp.route('/api/insurance/todo-tree')
@auth.insurance_officer_required
def api_insurance_todo_tree():
    """保险待办tree相关接口。"""
    search = request.args.get('search', '').strip()

    conditions = [
        's.is_deleted = 0',
        "(s.registration_status IS NULL OR s.registration_status != '毕业')",
        f"s.supervisor_flag IN ('{config.InsuranceStatus.PENDING_XUEPING}', '{config.InsuranceStatus.PENDING_INTERN}')"
    ]
    params = []

    if search:
        conditions.append('(s.name LIKE ? OR s.student_no LIKE ? OR s.id_card LIKE ? OR s.name_pinyin LIKE ?)')
        like = f'%{search}%'
        params.extend([like, like, like, like])

    where = ' AND '.join(conditions)

    rows = models.get_db().execute(
        f'''SELECT s.id, s.name, s.student_no, s.id_card, s.grade, s.admin_class,
                   s.major, s.enrollment_year, s.school,
                   s.student_insurance_pay_date, s.student_insurance_expiry,
                   s.student_insurance_no, s.insurance_premium,
                   s.internship_insurance_pay_date, s.supervisor_flag,
                   s.declaration_date
            FROM students s
            WHERE {where}
            ORDER BY s.school, s.admin_class, s.name''',
        params
    ).fetchall()

    students = [dict(r) for r in rows]

    # 按 school_types 动态初始化分组，新增学校类别也能归入待办树
    schools = {st['code']: [] for st in models.get_school_types()}
    for s in students:
        school = s.get('school')
        if school not in schools:
            schools[school] = []
        schools[school].append(s)

    return jsonify({'students_by_school': schools})


@bp.route('/api/insurance/complete', methods=['POST'])
@auth.insurance_officer_required
def api_insurance_complete():
    """保险完成相关接口（旧版，已下线）。

    支持两种模式：
    - 统一模式：传 student_ids + 可选的 student_insurance_no / student_insurance_expiry（全批套用）。
    - 逐人模式：传 items=[{student_id, student_insurance_no, student_insurance_expiry}]（实习/学平常每人一单）。
    实习就业险学生（supervisor_flag=按计划办理实习就业险）无论哪种模式都不写保单号/到期。
    """
    return jsonify({'success': False,
                    'message': '旧版保险办结接口已下线，请使用新版办结接口'}), 410
    data = request.get_json()
    if not data:
        return jsonify({'error': '无效数据'}), 400

    student_ids = data.get('student_ids', [])
    items = data.get('items')  # 逐人模式可选
    student_insurance_no = data.get('student_insurance_no', '').strip()
    student_insurance_expiry = utils.normalize_date_string(data.get('student_insurance_expiry', '').strip())

    if not student_ids and not items:
        return jsonify({'error': '请选择学生'}), 400

    # 年份来源：取系统学年基准（config.CURRENT_SCHOOL_YEAR），而非点击当天的
    # datetime.now().year —— 避免 clerk 在跨年时把上一学年的批次误标成本年前缀。
    year = config.CURRENT_SCHOOL_YEAR

    try:
        if items:
            target_map = {}
            for it in items:
                sid = it.get('student_id')
                if sid is None:
                    continue
                target_map[sid] = it
            id_list = list(target_map.keys())
        else:
            id_list = student_ids
            target_map = None

        done = 0
        for sid in id_list:
            cur = models.get_db().execute(
                'SELECT supervisor_flag FROM students WHERE id = ?', (sid,)
            ).fetchone()
            if not cur:
                continue

            is_intern = (cur[0] == config.InsuranceStatus.PENDING_INTERN)
            flag_done = config.InsuranceStatus.done_intern(year) if is_intern else config.InsuranceStatus.done_xueping(year)
            fields = {'supervisor_flag': flag_done}

            # 实习就业险系统无保单号/到期字段，绝不写
            if not is_intern:
                if target_map:
                    it = target_map.get(sid, {})
                    no = (it.get('student_insurance_no') or '').strip()
                    ex = utils.normalize_date_string((it.get('student_insurance_expiry') or '').strip())
                else:
                    no, ex = student_insurance_no, student_insurance_expiry
                if no:
                    fields['student_insurance_no'] = no
                if ex:
                    fields['student_insurance_expiry'] = ex

            models.update_student_insurance(sid, fields)
            done += 1
        return jsonify({'success': True, 'count': done})
    except Exception as e:
        return jsonify({'error': str(e)}), 500


@bp.route('/api/insurance/init-preview', methods=['POST'])
@_json_roles('admin')
def insurance_init_preview():
    """初始化导入预览：后端用 openpyxl 解析上传的 Excel，返回预览行。"""
    f = request.files.get('file')
    if not f:
        return jsonify({'error': '未收到文件'}), 400
    rows, errors = insurance_service.parse_insurance_excel(f)
    if errors:
        return jsonify({'success': False, 'errors': errors}), 400
    preview = rows[:10]
    return jsonify({
        'success': True,
        'total': len(rows),
        'columns': insurance_service.IMPORT_COLUMNS,
        'preview': preview,
        'errors': [],
    })


@bp.route('/insurance/init', methods=['GET', 'POST'])
@_page_roles('admin')
def insurance_init():
    """初始化。GET 渲染页面；POST 接收 Excel 文件（或兼容旧 JSON rows）并启动后台导入。"""
    if request.method == 'POST':
        # 优先使用上传的文件（后端解析，去 CDN）
        f = request.files.get('file')
        if f:
            rows, errors = insurance_service.parse_insurance_excel(f)
            if errors:
                return jsonify({'error': '；'.join(errors)}), 400
        else:
            data = request.get_json(silent=True)
            if not data or 'rows' not in data:
                return jsonify({'error': '无效数据：未收到文件或 rows'}), 400
            rows = data['rows']
        progress_id, total_count = insurance_service.start_insurance_init_import(rows, insurance_init_progress)
        return jsonify({'success': True, 'progress_id': progress_id, 'total': total_count})
    return render_template('insurance/init.html')


@bp.route('/api/insurance/init-progress/<progress_id>')
@_json_roles('admin')
def insurance_init_progress_check(progress_id):
    """初始化进度检查。 参数: progress_id"""
    if progress_id not in insurance_init_progress:
        return jsonify({'status': 'not_found', 'message': '进度ID不存在'}), 404
    return jsonify(insurance_init_progress[progress_id])


@bp.route('/api/insurance/init/errors/<progress_id>/csv')
@_json_roles('admin')
def insurance_init_errors_csv(progress_id):
    """导出初始化导入失败清单 CSV（全量错误，不受轮询"最近 10 条"展示截断影响）。

    前端原实现在客户端基于轮询返回的 progress.errors（最多 10 条）拼 CSV，
    大量错误时其余丢失。改为后端基于 errors_full 生成全量 CSV。
    """
    p = insurance_init_progress.get(progress_id)
    if not p:
        return jsonify({'error': '进度ID不存在'}), 404
    errs = p.get('errors_full') or []

    # 与前端原逻辑一致：从错误文本解析行号 / 身份证号
    re_row = re.compile(r'第\s*(\d+)\s*行')
    re_id = re.compile(r'(?:身份证号\s*)?([0-9Xx]{6,18})')
    buf = StringIO()
    writer = csv.writer(buf)
    writer.writerow(['行号', '身份证号', '说明'])
    for e in errs:
        line = str(e)
        m_row = re_row.search(line)
        m_id = re_id.search(line)
        writer.writerow([
            m_row.group(1) if m_row else '',
            m_id.group(1) if m_id else '',
            line,
        ])

    filename = f'保险导入失败清单_{datetime.now().strftime("%Y-%m-%d")}.csv'
    return Response(
        ('\ufeff' + buf.getvalue()).encode('utf-8'),
        mimetype='text/csv;charset=utf-8',
        headers={'Content-Disposition': f"attachment; filename*=UTF-8''{quote(filename)}"},
    )


@bp.route('/insurance/init/template')
@_page_roles('admin')
def insurance_init_template():
    """初始化模板。"""
    from io import BytesIO
    from openpyxl.utils import get_column_letter
    wb = Workbook()
    ws = wb.active
    ws.title = '保险初始化导入模板'

    header_fill = PatternFill(start_color='4472C4', end_color='4472C4', fill_type='solid')
    header_font = Font(color='FFFFFF', bold=True, size=11)
    headers = ['身份证号', '学平险保单号', '学平险缴费日期', '学平险到期时间', '实习险缴费日期', '主管标记']
    col_widths = [22, 22, 16, 16, 16, 22]

    for col_idx, (header, width) in enumerate(zip(headers, col_widths), 1):
        cell = ws.cell(row=1, column=col_idx, value=header)
        cell.fill = header_fill
        cell.font = header_font
        cell.alignment = Alignment(horizontal='center', vertical='center')
        ws.column_dimensions[get_column_letter(col_idx)].width = width

    rows = models.get_db().execute(
        '''SELECT id_card, student_insurance_no, student_insurance_pay_date,
                  student_insurance_expiry, internship_insurance_pay_date,
                  supervisor_flag
           FROM students
           WHERE is_deleted = 0
           ORDER BY id_card'''
    ).fetchall()

    for row_idx, r in enumerate(rows, 2):
        id_card = r[0] or ''
        insurance_no = r[1] or ''
        pay_date = r[2] or ''
        expiry = r[3] or ''
        intern_pay_date = r[4] or ''
        flag = r[5] or ''

        vals = [id_card, insurance_no, pay_date, expiry, intern_pay_date, flag]
        for col_idx, val in enumerate(vals, 1):
            cell = ws.cell(row=row_idx, column=col_idx, value=val)
            cell.alignment = Alignment(horizontal='center')

    output = BytesIO()
    wb.save(output)
    output.seek(0)

    return send_file(
        output,
        mimetype='application/vnd.openxmlformats-officedocument.spreadsheetml.sheet',
        as_attachment=True,
        download_name='保险初始化导入模板.xlsx'
    )
