from flask import Blueprint, request, jsonify, render_template, send_file, Response
from datetime import datetime, timedelta
from io import BytesIO, StringIO
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
@auth.finance_required
def insurance_register():
    """登记。"""
    filters = utils.parse_filters_from_request(request)
    search = filters.get('search', '')
    grade = filters.get('grade', '')
    admin_class = filters.get('admin_class', '')
    major = filters.get('major', '')
    filter_values = models.get_filter_values()
    return render_template('insurance/register.html',
                           search=search, grade=grade,
                           admin_class=admin_class, major=major,
                           filter_values=filter_values,
                           current_school_year=config.CURRENT_SCHOOL_YEAR)


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
    """保险登记single相关接口。"""
    data = request.get_json()
    if not data:
        return jsonify({'error': '无效数据'}), 400

    student_id = data.get('student_id')
    field = data.get('field')
    value = data.get('value', '')

    if not student_id or not field:
        return jsonify({'error': '缺少参数'}), 400

    allowed = set(config.INSURANCE_BATCH_FIELDS)
    if field not in allowed:
        return jsonify({'error': '无效字段'}), 400

    try:
        models.update_student_insurance(student_id, {field: value})
        return jsonify({'success': True})
    except Exception as e:
        return jsonify({'error': str(e)}), 500


@bp.route('/api/insurance/register-batch', methods=['POST'])
@auth.finance_required
def api_insurance_register_batch():
    """保险登记批量相关接口。"""
    data = request.get_json()
    if not data:
        return jsonify({'error': '无效数据'}), 400

    student_ids = data.get('student_ids', [])
    fields = data.get('fields', {})

    if not student_ids:
        return jsonify({'error': '请选择学生'}), 400

    allowed = set(config.INSURANCE_BATCH_FIELDS)
    update_data = {}
    for key, val in fields.items():
        if key in allowed:
            update_data[key] = val

    if not update_data:
        return jsonify({'error': '无有效更新字段'}), 400

    try:
        models.batch_update_student_insurance(student_ids, update_data)
        return jsonify({'success': True, 'count': len(student_ids)})
    except Exception as e:
        return jsonify({'error': str(e)}), 500


@bp.route('/insurance/approve')
@auth.finance_chief_required
def insurance_approve():
    """审批。"""
    return render_template('insurance/approve.html', current_school_year=config.CURRENT_SCHOOL_YEAR)


def _is_blank(v):
    """判断缴费日期等字段是否为空（None / 空串 / 纯空白）。"""
    return v is None or (isinstance(v, str) and v.strip() == '')


def compute_insurance_batch(grade, enrollment_year, current_year=None):
    """计算学生保险批次（第几年），返回 1~3 的整数或 None（无法判定 / 超出 3 年制）。

    先判定学生类别，再计算，保证 普通 / 转入 / 特殊 三种情况任何时刻都正确：
      - 普通：grade == enrollment_year           -> 起始年 = grade
      - 转入：grade <  enrollment_year           -> 起始年 = grade（按本校年级进度）
      - 特殊：grade >  enrollment_year           -> 年级由初中毕业时间推算，可能偏高：
            * 若 grade 超出当前学年（被算飞到未来），以 enrollment_year 为准；
            * 否则以 grade 为准（年级合法、入学年被压低）。
    batch = current_year − 起始年 + 1；仅 1~3 显示。
    """
    if current_year is None:
        current_year = config.CURRENT_SCHOOL_YEAR
    try:
        g = int(grade)
    except (TypeError, ValueError):
        return None
    try:
        en = int(enrollment_year)
    except (TypeError, ValueError):
        en = None
    if g is None:
        return None
    if en is None:
        b = current_year - g + 1
        return b if 1 <= b <= 3 else None
    if g == en:
        start = g                       # 普通
    elif g < en:
        start = g                       # 转入：按本校年级进度
    else:  # g > en -> 特殊
        start = en if g > current_year else g
    b = current_year - start + 1
    return b if 1 <= b <= 3 else None


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
    """保险审批single相关接口。"""
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

    by_school = {1: {'students': [], 'interns': [], 'total': 0}, 2: {'students': [], 'interns': [], 'total': 0}}

    for r in rows:
        school = r[0] or 1
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
        'grand_total': sum(by_school[s]['total'] for s in [1, 2])
    })


@bp.route('/api/insurance/approve', methods=['POST'])
@auth.finance_chief_required
def api_insurance_approve():
    """保险审批相关接口。"""
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
@auth.insurance_officer_required
def insurance_todo():
    """待办。"""
    return render_template('insurance/todo.html', current_school_year=config.CURRENT_SCHOOL_YEAR)


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

    schools = {1: [], 2: []}
    for s in students:
        school = s.get('school') or 1
        if school not in schools:
            schools[school] = []
        schools[school].append(s)

    return jsonify({'students_by_school': schools})


@bp.route('/api/insurance/complete', methods=['POST'])
@auth.insurance_officer_required
def api_insurance_complete():
    """保险完成相关接口。

    支持两种模式：
    - 统一模式：传 student_ids + 可选的 student_insurance_no / student_insurance_expiry（全批套用）。
    - 逐人模式：传 items=[{student_id, student_insurance_no, student_insurance_expiry}]（实习/学平常每人一单）。
    实习就业险学生（supervisor_flag=按计划办理实习就业险）无论哪种模式都不写保单号/到期。
    """
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
@auth.insurance_officer_required
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
@auth.insurance_officer_required
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
@auth.insurance_officer_required
def insurance_init_progress_check(progress_id):
    """初始化进度检查。 参数: progress_id"""
    if progress_id not in insurance_init_progress:
        return jsonify({'status': 'not_found', 'message': '进度ID不存在'}), 404
    return jsonify(insurance_init_progress[progress_id])


@bp.route('/api/insurance/init/errors/<progress_id>/csv')
@auth.insurance_officer_required
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
@auth.insurance_officer_required
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
