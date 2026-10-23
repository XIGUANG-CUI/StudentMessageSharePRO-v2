import os
import time
import zipfile
import base64
import re
import secrets
import logging
from datetime import datetime
from io import BytesIO
from urllib.parse import urlparse

from flask import (
    Blueprint, render_template, request, redirect, url_for,
    flash, session, jsonify, send_file, current_app,
)
from openpyxl import Workbook, load_workbook
from openpyxl.styles import Font, PatternFill, Alignment

import models
import auth
import utils
import config
import pinyin_utils
from services import import_service
from auth import _require_admin_json, _get_current_enrollment_year

logger = logging.getLogger(__name__)
bp = Blueprint('admin', __name__)

# 全局导入进度存储（随块迁移自 app.py）
import_progress = {}

def cleanup_temp_files(exclude_ids=None):
    """清理temp文件。 参数: exclude_ids"""
    exclude_ids = exclude_ids or []
    try:
        current_time = time.time()
        for filename in os.listdir(current_app.config['UPLOAD_FOLDER']):
            filepath = os.path.join(current_app.config['UPLOAD_FOLDER'], filename)
            if not os.path.isfile(filepath):
                continue
            file_age = current_time - os.path.getmtime(filepath)
            if file_age <= 3600:
                continue
            if (filename.startswith(('import_data_', 'batch_update_data_')) or 
                filename.endswith(('.xlsx', '.xls', '.csv'))):
                os.remove(filepath)
    except Exception as e:
        print(f"清理临时文件失败: {e}")


def _safe_str(value):
    """安全地将值转换为字符串，处理None和非字符串类型"""
    if value is None:
        return ''
    return str(value).strip() if isinstance(value, str) else str(value)

def _default_school_code():
    """未指定学校类别时的默认值：首个启用类别。"""
    types = models.get_school_types(active_only=True)
    return types[0]['code'] if types else 1

def _build_year_filter():
    """build年份筛选。"""
    filters = {}
    year = _get_current_enrollment_year()
    if year is not None:
        filters['enrollment_year'] = year
    return filters

def _get_filtered_students():
    """getfiltered学生。"""
    school_filter = request.args.get('school', '')
    filters = utils.parse_filters_from_request(request)
    has_photo_filter = filters.pop('has_photo', None)

    form_enrollment_year = request.args.get('enrollment_year', '').strip()
    if form_enrollment_year:
        filters['enrollment_year'] = int(form_enrollment_year)
    else:
        year_filter = _build_year_filter()
        filters.update(year_filter)

    if school_filter:
        filters['school'] = int(school_filter)

    enrollment_year = _get_current_enrollment_year()
    all_result = models.get_students(filters, page=1, per_page=999999)
    all_students = models.get_students_with_photo_status(all_result['students'], enrollment_year)

    if has_photo_filter == '1':
        all_students = [s for s in all_students if s.get('has_photo')]
    elif has_photo_filter == '0':
        all_students = [s for s in all_students if not s.get('has_photo')]

    return all_students, has_photo_filter, school_filter, enrollment_year, filters


def _students_back_url():
    """返回学生列表的来源地址，用于操作完成后保留筛选条件与页码"""
    candidate = request.values.get('next', '').strip()
    if not candidate:
        referrer = request.referrer or ''
        if referrer:
            parsed = urlparse(referrer)
            # 仅接受同源地址，避免把外部链接当作跳转目标
            if not parsed.netloc or parsed.netloc == request.host:
                candidate = parsed.path + (f'?{parsed.query}' if parsed.query else '')
    if candidate.startswith('/admin/students') and not candidate.startswith('/admin/students/delete'):
        return candidate
    return url_for('.admin_students')


@bp.route('/admin/year-select')
@auth.admin_required
def admin_year_select():
    """年份select相关接口。"""
    return redirect(url_for('.admin_dashboard', select_year='1'))

@bp.route('/admin/year-clear')
@auth.admin_required
def admin_year_clear():
    """年份clear相关接口。"""
    session.pop('enrollment_year', None)
    flash('已切换到全校数据视图', 'info')
    return redirect(url_for('.admin_dashboard'))

@bp.route('/api/enrollment-years')
def api_enrollment_years():
    """入学年份相关接口。"""
    auth_error = _require_admin_json()
    if auth_error:
        return auth_error
    years = models.get_enrollment_years()
    return jsonify({'years': years})

@bp.route('/api/year/select', methods=['POST'])
def api_year_select():
    """年份select相关接口。"""
    auth_error = _require_admin_json()
    if auth_error:
        return auth_error
    data = request.get_json()
    year = data.get('year')
    if year:
        try:
            existing = models.get_enrollment_years()
            if year not in existing:
                models.add_dict('enrollment_year', str(year))
        except Exception as e:
            logger.debug("年份选择-同步入学年份到字典失败 year=%s: %s", year, e, exc_info=True)
        session['enrollment_year'] = int(year)
        session.permanent = True
        return jsonify({'success': True, 'redirect': url_for('.admin_dashboard')})
    return jsonify({'success': False, 'message': '请选择年份'}), 400

@bp.route('/api/year/create', methods=['POST'])
def api_year_create():
    """年份创建相关接口。"""
    auth_error = _require_admin_json()
    if auth_error:
        return auth_error
    data = request.get_json()
    year = data.get('year')
    if not year or year < 2000 or year > 2100:
        return jsonify({'success': False, 'message': '请输入有效的年份（2000-2100）'}), 400

    existing = models.get_enrollment_years()
    if year in existing:
        session['enrollment_year'] = int(year)
        session.permanent = True
        return jsonify({'success': True, 'message': f'已切换到 {year} 年'})

    try:
        models.add_dict('enrollment_year', str(year))
    except Exception as e:
        logger.debug("年份创建-添加入学年份到字典失败 year=%s: %s", year, e, exc_info=True)

    session['enrollment_year'] = int(year)
    session.permanent = True
    return jsonify({'success': True, 'message': f'已创建并切换到 {year} 年'})


@bp.route('/admin')
@auth.admin_required
def admin_dashboard():
    """dashboard相关接口。"""
    school_filter = request.args.get('school', '')
    registration_filter = request.args.get('registration_status', '')

    filters = _build_year_filter()
    if school_filter:
        filters['school'] = int(school_filter)

    if registration_filter in ('正式学籍', '未注册'):
        filters['registration_status'] = registration_filter
    else:
        registration_filter = ''

    stats = models.get_stats(filters)
    attendance_dates = models.get_attendance_dates(10)

    # 公开页面完整链接（含访问口令，供管理员复制分发）
    public_pending_leave_url = url_for('public.public_pending_leave', k=config.PUBLIC_ACCESS_KEY, _external=True)
    public_roster_url = url_for('public.public_roster', k=config.PUBLIC_ACCESS_KEY, _external=True)

    return render_template('admin/dashboard.html', stats=stats, current_school=school_filter, current_registration=registration_filter, attendance_dates=attendance_dates, public_pending_leave_url=public_pending_leave_url, public_roster_url=public_roster_url)

@bp.route('/admin/students')
@auth.admin_required
def admin_students():
    """学生。"""
    page = int(request.args.get('page', 1))
    attendance_date = request.args.get('attendance_date', datetime.now().strftime('%Y-%m-%d'))

    all_students, has_photo_filter, school_filter, enrollment_year, filters = _get_filtered_students()

    # 也需要出勤数据：单独查询带 attendance 的分页结果
    # 先获取不含 has_photo 的基础筛选条件（已在 _get_filtered_students 中 pop 过）
    # 直接对 all_students 做内存分页
    total = len(all_students)
    photo_count = sum(1 for s in all_students if s.get('has_photo'))
    per_page = 20
    pages = (total + per_page - 1) // per_page if total > 0 else 1
    # 删除/筛选后当前页码可能越界，收缩到最后一页，避免展示空白页
    page = max(1, min(page, pages))
    start = (page - 1) * per_page
    end = start + per_page
    page_students = all_students[start:end]

    # 为分页学生附加出勤状态
    for s in page_students:
        s['today_attendance'] = None
    if page_students:
        conn = models.get_db()
        try:
            id_cards = [s['id_card'] for s in page_students if s.get('id_card')]
            placeholders = ','.join(['?' for _ in id_cards])
            rows = conn.execute(
                f'''SELECT ar.id_card, ar.status FROM attendance_records ar
                    INNER JOIN students s ON s.id_card = ar.id_card
                    WHERE ar.attendance_date = ? AND s.id_card IN ({placeholders})''',
                [attendance_date] + id_cards
            ).fetchall()
            att_map = {r['id_card']: r['status'] for r in rows}
            for s in page_students:
                s['today_attendance'] = att_map.get(s['id_card'])
        except Exception as e:
            logger.debug("查询今日出勤失败，降级不显示 today_attendance: %s", e, exc_info=True)
        finally:
            conn.close()

    result = {
        'students': page_students,
        'total': total,
        'page': page,
        'per_page': per_page,
        'pages': pages
    }

    filters['has_photo'] = has_photo_filter
    filter_values = models.get_filter_values()

    return render_template('admin/students.html',
                          students=result['students'],
                          pagination=result,
                          filter_values=filter_values,
                          current_school=school_filter,
                          current_filters=filters or {},
                          current_year=enrollment_year,
                          photo_count=photo_count,
                          today=datetime.now().strftime('%Y-%m-%d'),
                          attendance_date=attendance_date)

@bp.route('/admin/students/export/photos')
@auth.admin_required
def admin_students_export_photos():
    """学生导出照片。"""
    all_students, has_photo_filter, _, enrollment_year, _ = _get_filtered_students()

    if not all_students:
        flash('没有符合条件的学生照片可导出', 'warning')
        return redirect(_students_back_url())

    photos_dir = config.PHOTOS_DIR

    output = BytesIO()
    count = 0
    seen = set()

    with zipfile.ZipFile(output, 'w', zipfile.ZIP_DEFLATED) as zf:
        for student in all_students:
            id_card = student.get('id_card', '')
            if not id_card or id_card in seen:
                continue
            safe_id = ''.join(c for c in str(id_card) if c.isalnum())
            found = False

            student_year = enrollment_year or student.get('enrollment_year')
            if student_year:
                year_dir = os.path.join(photos_dir, str(student_year))
                for ext in ['.jpg', '.jpeg', '.png', '.bmp']:
                    photo_path = os.path.join(year_dir, f'{safe_id}{ext}')
                    if os.path.exists(photo_path):
                        zf.write(photo_path, f'{student_year}/{safe_id}{ext}')
                        count += 1
                        found = True
                        seen.add(id_card)
                        break

            if not found:
                for ext in ['.jpg', '.jpeg', '.png', '.bmp']:
                    photo_path = os.path.join(photos_dir, f'{safe_id}{ext}')
                    if os.path.exists(photo_path):
                        zf.write(photo_path, f'{safe_id}{ext}')
                        count += 1
                        seen.add(id_card)
                        break

    if count == 0:
        flash('没有找到匹配的照片文件', 'warning')
        return redirect(_students_back_url())

    output.seek(0)
    return send_file(output, mimetype='application/zip',
                     download_name=f'学生照片_{datetime.now().strftime("%Y%m%d_%H%M%S")}.zip',
                     as_attachment=True)

@bp.route('/admin/students/export/excel')
@auth.admin_required
def admin_students_export_excel():
    """学生导出Excel。"""
    all_students, _, _, _, _ = _get_filtered_students()

    if not all_students:
        flash('没有符合条件的学生数据可导出', 'warning')
        return redirect(_students_back_url())

    for student in all_students:
        student['age'] = utils.calculate_age(student.get('birth_date'))

    wb = utils.export_students_to_excel(all_students)
    output = BytesIO()
    wb.save(output)
    output.seek(0)

    timestamp = datetime.now().strftime('%Y%m%d_%H%M')
    return send_file(output, mimetype='application/vnd.openxmlformats-officedocument.spreadsheetml.sheet',
                     download_name=f'学生信息导出_{timestamp}.xlsx',
                     as_attachment=True)

def _build_form_student():
    """从 request.form 提取学生表单数据，构造模板渲染用的 dict"""
    return {
        'student_no': request.form.get('student_no', '').strip(),
        'national_no': request.form.get('national_no', '').strip(),
        'id_card': request.form.get('id_card', '').strip(),
        'name': request.form.get('name', '').strip(),
        'gender': request.form.get('gender', ''),
        'ethnicity': request.form.get('ethnicity', '').strip(),
        'address': request.form.get('address', '').strip(),
        'major': request.form.get('major', '').strip(),
        'admin_class': request.form.get('admin_class', '').strip(),
        'virtual_class': request.form.get('virtual_class', '').strip(),
        'parent_name': request.form.get('parent_name', '').strip(),
        'parent_id_card': request.form.get('parent_id_card', '').strip(),
        'parent_phone': request.form.get('parent_phone', '').strip(),
        'student_phone': request.form.get('student_phone', '').strip(),
        'grade': request.form.get('grade', '').strip(),
        'school': int(request.form.get('school') or _default_school_code()),
        'registration_status': request.form.get('registration_status', '正式学籍'),
        'attendance_status': request.form.get('attendance_status', '在校'),
        'issue_authority': request.form.get('issue_authority', '').strip(),
        'valid_start': request.form.get('valid_start', '').strip(),
        'valid_end': request.form.get('valid_end', '').strip(),
        'major_direction': request.form.get('major_direction', '').strip(),
        'household_type': request.form.get('household_type', '').strip(),
        'junior_school': request.form.get('junior_school', '').strip(),
        'admission_type': request.form.get('admission_type', '').strip(),
        'junior_graduation_time': request.form.get('junior_graduation_time', '').strip(),
        'change_info': request.form.get('change_info', '').strip(),
        'remark': request.form.get('remark', '').strip(),
    }


@bp.route('/admin/students/add', methods=['GET', 'POST'])
@auth.admin_required
def admin_student_add():
    """学生add相关接口。"""
    if request.method == 'POST':
        id_card = request.form.get('id_card', '').strip()
        if id_card:
            check = utils.validate_id_card_detailed(id_card)
            if not check['valid']:
                flash(check['message'], 'danger')
                return render_template('admin/student_form.html', student=_build_form_student(), filter_values=models.get_filter_values(), next_url=_students_back_url())

        parent_id_card = request.form.get('parent_id_card', '').strip()
        if parent_id_card and parent_id_card != id_card:
            pcheck = utils.validate_id_card_detailed(parent_id_card)
            if not pcheck['valid']:
                flash('家长' + pcheck['message'], 'danger')
                return render_template('admin/student_form.html', student=_build_form_student(), filter_values=models.get_filter_values(), next_url=_students_back_url())

        parent_phone = request.form.get('parent_phone', '').strip()
        if parent_phone and not utils.validate_phone(parent_phone):
            flash('家长手机号格式不正确', 'danger')
            return render_template('admin/student_form.html', student=_build_form_student(), filter_values=models.get_filter_values(), next_url=_students_back_url())

        student_phone = request.form.get('student_phone', '').strip()
        if student_phone and not utils.validate_phone(student_phone):
            flash('学生手机号格式不正确', 'danger')
            return render_template('admin/student_form.html', student=_build_form_student(), filter_values=models.get_filter_values(), next_url=_students_back_url())

        student_data = _build_form_student()
        student_data['name_pinyin'] = pinyin_utils.name_to_pinyin(student_data['name'])
        
        year = _get_current_enrollment_year()
        if year:
            student_data['enrollment_year'] = year
        
        if student_data['id_card']:
            student_data['birth_date'] = utils.extract_birth_date_from_id_card(student_data['id_card'])

        if student_data.get('admission_type') == '转学生' and student_data.get('grade', '').isdigit():
            student_data['grade'] = str(int(student_data['grade']) - 1)

        try:
            student_id = models.create_student(student_data)
            models.update_sync_timestamp()
            flash('学生添加成功', 'success')
            return redirect(_students_back_url())
        except Exception as e:
            flash(f'添加失败: {str(e)}', 'danger')
            return render_template('admin/student_form.html', student=_build_form_student(), filter_values=models.get_filter_values(), next_url=_students_back_url())
    
    filter_values = models.get_filter_values()
    return render_template('admin/student_form.html', filter_values=filter_values, next_url=_students_back_url())

@bp.route('/admin/students/edit/<int:student_id>', methods=['GET', 'POST'])
@auth.admin_required
def admin_student_edit(student_id):
    """学生edit相关接口。 参数: student_id"""
    student = models.get_student_by_id(student_id)
    if not student:
        flash('学生不存在', 'danger')
        return redirect(_students_back_url())
    
    if request.method == 'POST':
        id_card = request.form.get('id_card', '').strip()
        if id_card:
            check = utils.validate_id_card_detailed(id_card)
            if not check['valid']:
                flash(check['message'], 'danger')
                return render_template('admin/student_form.html', student={**student, **_build_form_student()}, filter_values=models.get_filter_values(), next_url=_students_back_url())

        parent_id_card = request.form.get('parent_id_card', '').strip()
        if parent_id_card and parent_id_card != id_card:
            pcheck = utils.validate_id_card_detailed(parent_id_card)
            if not pcheck['valid']:
                flash('家长' + pcheck['message'], 'danger')
                return render_template('admin/student_form.html', student={**student, **_build_form_student()}, filter_values=models.get_filter_values(), next_url=_students_back_url())

        parent_phone = request.form.get('parent_phone', '').strip()
        if parent_phone and not utils.validate_phone(parent_phone):
            flash('家长手机号格式不正确', 'danger')
            return render_template('admin/student_form.html', student={**student, **_build_form_student()}, filter_values=models.get_filter_values(), next_url=_students_back_url())

        student_phone = request.form.get('student_phone', '').strip()
        if student_phone and not utils.validate_phone(student_phone):
            flash('学生手机号格式不正确', 'danger')
            return render_template('admin/student_form.html', student={**student, **_build_form_student()}, filter_values=models.get_filter_values(), next_url=_students_back_url())

        student_data = _build_form_student()
        
        if student_data['id_card']:
            student_data['birth_date'] = utils.extract_birth_date_from_id_card(student_data['id_card'])
        
        try:
            models.update_student(student_id, student_data)
            models.update_sync_timestamp()
            flash('学生信息更新成功', 'success')
            return redirect(_students_back_url())
        except Exception as e:
            flash(f'更新失败: {str(e)}', 'danger')
    
    filter_values = models.get_filter_values()
    return render_template('admin/student_form.html', student=student, filter_values=filter_values, next_url=_students_back_url())

@bp.route('/admin/students/delete/<int:student_id>', methods=['POST'])
@auth.admin_required
def admin_student_delete(student_id):
    """学生删除相关接口。 参数: student_id"""
    student = models.get_student_by_id(student_id)
    if not student:
        flash('学生不存在', 'danger')
        return redirect(_students_back_url())

    attendance_status = student.get('attendance_status', '') or ''
    if attendance_status != '退学办结':
        flash('仅退学办结状态的学生方可删除，当前在校状态：' + (attendance_status or '无'), 'warning')
        return redirect(_students_back_url())

    models.soft_delete_student(student_id, updated_by=session.get('username', 'system'))
    models.update_sync_timestamp()
    flash('学生已删除', 'success')
    return redirect(_students_back_url())

@bp.route('/admin/students/clear', methods=['POST'])
@auth.admin_required
def admin_students_clear():
    """学生clear。"""
    try:
        count = models.clear_all_students()
        models.update_sync_timestamp()
        return jsonify({'success': True, 'message': f'成功清空 {count} 条学生数据'})
    except Exception as e:
        return jsonify({'success': False, 'message': str(e)})

@bp.route('/admin/import', methods=['GET', 'POST'])
@auth.admin_required
def admin_import():
    """导入。"""
    if request.method == 'POST':
        # 清理旧的临时文件
        cleanup_temp_files()
        
        if 'file' not in request.files:
            flash('请选择文件', 'danger')
            return redirect(request.url)
        
        file = request.files['file']
        if file.filename == '':
            flash('请选择文件', 'danger')
            return redirect(request.url)
        
        if not utils.allowed_file(file.filename):
            flash('文件格式不支持，请上传Excel文件', 'danger')
            return redirect(request.url)
        
        filename = utils.generate_unique_filename(file.filename)
        filepath = os.path.join(current_app.config['UPLOAD_FOLDER'], filename)
        file.save(filepath)
        
        try:
            data, error = utils.parse_import_file(filepath)
            if error:
                flash(f'文件解析失败: {error}', 'danger')
                return redirect(request.url)
            
            validation_results = utils.validate_import_data(data)
            
            # 将验证数据存储到服务器端文件，避免session过大
            import_id = secrets.token_hex(16)
            import_data_file = os.path.join(current_app.config['UPLOAD_FOLDER'], f'import_data_{import_id}.json')
            import json
            with open(import_data_file, 'w', encoding='utf-8') as f:
                json.dump({'validation_results': validation_results, 'filename': filename}, f)
            
            session['import_id'] = import_id
            
            valid_count = sum(1 for r in validation_results if r['valid'])
            invalid_count = len(validation_results) - valid_count
            
            flash(f'文件解析完成，共{len(data)}条数据，有效{valid_count}条，无效{invalid_count}条', 'info')
            return render_template('admin/import.html', validation_results=validation_results)
        
        except Exception as e:
            flash(f'处理失败: {str(e)}', 'danger')
            return redirect(request.url)
    
    return render_template('admin/import.html')

@bp.route('/admin/import/template')
@auth.admin_required
def admin_import_template():
    """导入模板。"""
    wb = utils.create_import_template()
    output = BytesIO()
    wb.save(output)
    output.seek(0)
    
    return send_file(output, mimetype='application/vnd.openxmlformats-officedocument.spreadsheetml.sheet',
                     as_attachment=True, download_name='学生导入模板.xlsx')

@bp.route('/admin/import/confirm', methods=['POST'])
def admin_import_confirm():
    """导入确认。"""
    auth_error = _require_admin_json()
    if auth_error:
        return auth_error
    import json
    import threading
    import traceback
    
    if 'import_id' not in session:
        return jsonify({'success': False, 'message': '请先上传文件'})
    
    import_id = session['import_id']
    import_data_file = os.path.join(current_app.config['UPLOAD_FOLDER'], f'import_data_{import_id}.json')
    
    if not os.path.exists(import_data_file):
        return jsonify({'success': False, 'message': '请先上传文件'})
    
    try:
        with open(import_data_file, 'r', encoding='utf-8') as f:
            import_data = json.load(f)
        
        validation_results = import_data['validation_results']
        original_filename = import_data.get('filename', '')
    except Exception as e:
        traceback.print_exc()
        return jsonify({'success': False, 'message': f'数据读取失败: {str(e)}'})
    
    # 只统计有效数据
    valid_results = [r for r in validation_results if r['valid']]
    total_count = len(valid_results)
    
    # 生成进度ID
    progress_id = secrets.token_hex(8)
    session['import_progress_id'] = progress_id
    
    # 初始化进度
    import_progress[progress_id] = {
        'total': total_count,
        'current': 0,
        'success': 0,
        'error': 0,
        'status': 'processing',
        'message': '开始导入...',
        'errors': []
    }
    
    # 后台线程退出请求上下文后无法访问 current_app，提前取出上传目录
    upload_folder = current_app.config['UPLOAD_FOLDER']

    def do_import():
        """后台执行导入的 worker 函数。"""
        success_count = 0
        error_count = 0
        errors = []

        # 后台线程中创建 1 条数据库连接，循环内复用，避免每条记录重复建连
        conn = models.get_db()
        try:
            for idx, result in enumerate(valid_results, 1):
                data = result['data']
                student_data = {
                    'student_no': _safe_str(data.get('学号', '')),
                    'national_no': _safe_str(data.get('国网学籍号', '')),
                    'id_card': _safe_str(data.get('学生身份证号', '')),
                    'name': _safe_str(data.get('学生姓名', '')),
                    'gender': _safe_str(data.get('性别', '')),
                    'ethnicity': _safe_str(data.get('民族', '')),
                    'address': _safe_str(data.get('住址', '')),
                    'major': _safe_str(data.get('专业', '')),
                    'admin_class': _safe_str(data.get('行政班级', '')),
                    'virtual_class': _safe_str(data.get('虚拟班级', '')),
                    'parent_name': _safe_str(data.get('家长姓名', '')),
                    'parent_id_card': _safe_str(data.get('家长身份证号', '')),
                    'parent_phone': _safe_str(data.get('家长电话', '')),
                    'student_phone': _safe_str(data.get('学生电话', '')),
                    'grade': _safe_str(data.get('年级', '')),
                    'school': int(data.get('学校类型', 1)),
                    'registration_status': _safe_str(data.get('学籍状态', '正式学籍')),
                    'attendance_status': _safe_str(data.get('在校状态', '在校')),
                    'issue_authority': _safe_str(data.get('签发机关', '')),
                    'valid_start': _safe_str(data.get('有效期开始', '')),
                    'valid_end': _safe_str(data.get('有效期结束', '')),
                    'major_direction': _safe_str(data.get('专业方向', '')),
                    'household_type': _safe_str(data.get('户口性质', '')),
                    'junior_school': _safe_str(data.get('初中毕业学校', '')),
                    'admission_type': _safe_str(data.get('入学形式', '')),
                    'junior_graduation_time': _safe_str(data.get('初中毕业时间', '')),
                    'change_info': _safe_str(data.get('异动信息', '')),
                    'remark': _safe_str(data.get('备注', '')),
                    'enrollment_year': int(data.get('入学年份', 0) or 0),
                    'name_pinyin': pinyin_utils.name_to_pinyin(_safe_str(data.get('学生姓名', '')))
                }

                id_card = student_data['id_card']
                if id_card:
                    birth_date = utils.extract_birth_date_from_id_card(id_card)
                    student_data['birth_date'] = birth_date

                try:
                    models.create_student_with_conn(conn, student_data)
                    success_count += 1
                    import_progress[progress_id]['success'] = success_count
                except Exception as e:
                    error_count += 1
                    error_msg = f"第{result['row']}行: {str(e)}"
                    errors.append(error_msg)
                    import_progress[progress_id]['errors'].append(error_msg)

                # 每 50 条 commit 一次，减少磁盘同步频率
                if idx % 50 == 0:
                    conn.commit()

                # 更新进度
                import_progress[progress_id]['current'] = idx
                import_progress[progress_id]['error'] = error_count
                import_progress[progress_id]['message'] = f'正在导入第 {idx}/{total_count} 条...'

            # 提交剩余不足 50 条的记录
            if total_count % 50 != 0:
                conn.commit()

            # 标记完成
            import_progress[progress_id]['status'] = 'completed'
            import_progress[progress_id]['message'] = '导入完成！'
            import_progress[progress_id]['success_count'] = success_count
            import_progress[progress_id]['error_count'] = error_count
            import_progress[progress_id]['errors'] = errors[:10]

            models.update_sync_timestamp()

            # 清理临时文件
            if os.path.exists(import_data_file):
                os.remove(import_data_file)
            if original_filename:
                filepath = os.path.join(upload_folder, original_filename)
                if os.path.exists(filepath):
                    os.remove(filepath)
        finally:
            conn.close()
    
    # 在后台线程执行导入
    thread = threading.Thread(target=do_import)
    thread.daemon = True
    thread.start()
    
    return jsonify({
        'success': True,
        'progress_id': progress_id
    })

@bp.route('/admin/import/progress')
@auth.admin_required
def admin_import_progress():
    """SSE接口：实时推送导入进度"""
    progress_id = request.args.get('id')
    
    if not progress_id or progress_id not in import_progress:
        return jsonify({'error': '无效的进度ID'}), 404
    
    def generate():
        """生成导出数据的生成器函数。"""
        while True:
            progress = import_progress.get(progress_id, {})
            if not progress:
                break
            
            data = {
                'total': progress.get('total', 0),
                'current': progress.get('current', 0),
                'success': progress.get('success', 0),
                'error': progress.get('error', 0),
                'status': progress.get('status', 'processing'),
                'message': progress.get('message', ''),
                'errors': progress.get('errors', [])
            }
            
            yield f"data: {json.dumps(data)}\n\n"
            
            # 如果导入完成，清理并停止
            if progress.get('status') == 'completed':
                # 延迟清理，让前端有机会获取最后状态
                import time
                time.sleep(2)
                if progress_id in import_progress:
                    del import_progress[progress_id]
                break
            
            import time
            time.sleep(0.5)  # 每500ms推送一次
    
    return Response(generate(), mimetype='text/event-stream')

@bp.route('/admin/import/progress/<progress_id>')
def admin_import_progress_get(progress_id):
    """导入进度get。 参数: progress_id"""
    auth_error = _require_admin_json()
    if auth_error:
        return auth_error
    if progress_id not in import_progress:
        return jsonify({'error': '无效的进度ID'}), 404
    
    progress = import_progress[progress_id]
    return jsonify({
        'total': progress.get('total', 0),
        'current': progress.get('current', 0),
        'success': progress.get('success', 0),
        'error': progress.get('error', 0),
        'status': progress.get('status', 'processing'),
        'message': progress.get('message', ''),
        'errors': progress.get('errors', [])
    })

@bp.route('/admin/batch-update', methods=['GET', 'POST'])
@auth.admin_required
def admin_batch_update():
    """批量更新。"""
    if request.method == 'POST':
        cleanup_temp_files()
        
        if 'file' not in request.files:
            flash('请选择文件', 'danger')
            return redirect(request.url)
        
        file = request.files['file']
        if file.filename == '':
            flash('请选择文件', 'danger')
            return redirect(request.url)
        
        if not utils.allowed_file(file.filename):
            flash('文件格式不支持，请上传Excel文件', 'danger')
            return redirect(request.url)
        
        filename = utils.generate_unique_filename(file.filename)
        filepath = os.path.join(current_app.config['UPLOAD_FOLDER'], filename)
        file.save(filepath)
        
        try:
            data, error = utils.parse_batch_update_file(filepath)
            if error:
                flash(f'文件解析失败: {error}', 'danger')
                return redirect(request.url)
            
            attendance_date = request.form.get('attendance_date', datetime.now().strftime('%Y-%m-%d'))
            validation_results = utils.validate_batch_update_data(data, attendance_date)
            
            import json
            batch_update_id = secrets.token_hex(16)
            batch_update_data_file = os.path.join(current_app.config['UPLOAD_FOLDER'], f'batch_update_data_{batch_update_id}.json')
            with open(batch_update_data_file, 'w', encoding='utf-8') as f:
                json.dump({'validation_results': validation_results, 'filename': filename, 'attendance_date': attendance_date}, f)
            
            session['batch_update_id'] = batch_update_id
            
            pending_count = sum(1 for r in validation_results if r['status'] == 'pending')
            no_change_count = sum(1 for r in validation_results if r['status'] == 'no_change')
            not_found_count = sum(1 for r in validation_results if r['status'] == 'not_found')
            
            flash(f'文件解析完成，待更新{pending_count}条，无变更{no_change_count}条，未匹配{not_found_count}条', 'info')
            return render_template('admin/batch_update.html', validation_results=validation_results)
        
        except Exception as e:
            flash(f'处理失败: {str(e)}', 'danger')
            return redirect(request.url)
    
    return render_template('admin/batch_update.html')

@bp.route('/admin/batch-update/template')
@auth.admin_required
def admin_batch_update_template():
    """批量更新模板。"""
    attendance_date = request.args.get('attendance_date', datetime.now().strftime('%Y-%m-%d'))
    students = models.get_students(per_page=999999)['students']
    wb = utils.create_batch_update_template(students, attendance_date)
    output = BytesIO()
    wb.save(output)
    output.seek(0)
    
    return send_file(output, mimetype='application/vnd.openxmlformats-officedocument.spreadsheetml.sheet',
                     as_attachment=True, download_name='批量更新模板.xlsx')

@bp.route('/admin/batch-update/confirm', methods=['POST'])
def admin_batch_update_confirm():
    """批量更新确认。"""
    auth_error = _require_admin_json()
    if auth_error:
        return auth_error
    import json
    import threading
    import traceback
    
    if 'batch_update_id' not in session:
        return jsonify({'success': False, 'message': '请先上传文件'})
    
    batch_update_id = session['batch_update_id']
    batch_update_data_file = os.path.join(current_app.config['UPLOAD_FOLDER'], f'batch_update_data_{batch_update_id}.json')
    
    if not os.path.exists(batch_update_data_file):
        return jsonify({'success': False, 'message': '请先上传文件'})
    
    try:
        with open(batch_update_data_file, 'r', encoding='utf-8') as f:
            batch_update_data = json.load(f)
        
        validation_results = batch_update_data['validation_results']
        original_filename = batch_update_data.get('filename', '')
        attendance_date = batch_update_data.get('attendance_date', datetime.now().strftime('%Y-%m-%d'))
        operator_username = session.get('username', 'system')
    except Exception as e:
        traceback.print_exc()
        return jsonify({'success': False, 'message': f'数据读取失败: {str(e)}'})
    
    pending_results = [r for r in validation_results if r['status'] in ('pending', 'new_record')]
    total_count = len(pending_results)
    
    if total_count == 0:
        return jsonify({'success': False, 'message': '没有待更新或待新增的数据'})
    
    progress_id = secrets.token_hex(8)
    session['batch_update_progress_id'] = progress_id
    
    import_progress[progress_id] = {
        'total': total_count,
        'current': 0,
        'success': 0,
        'no_change': 0,
        'not_found': 0,
        'new_record': 0,
        'error': 0,
        'status': 'processing',
        'message': '开始批量更新...',
        'errors': []
    }
    
    ctx = {
        'pending_results': pending_results,
        'progress_id': progress_id,
        'total_count': total_count,
        'attendance_date': attendance_date,
        'operator_username': operator_username,
        'batch_update_data_file': batch_update_data_file,
        'original_filename': original_filename,
        'progress_dict': import_progress,
        'upload_folder': current_app.config['UPLOAD_FOLDER'],
    }
    thread = threading.Thread(target=import_service.run_batch_update, args=(ctx,), daemon=True)
    thread.start()
    
    return jsonify({
        'success': True,
        'progress_id': progress_id
    })

@bp.route('/admin/batch-update/progress/<progress_id>')
def admin_batch_update_progress_get(progress_id):
    """批量更新进度get。 参数: progress_id"""
    auth_error = _require_admin_json()
    if auth_error:
        return auth_error
    if progress_id not in import_progress:
        return jsonify({'error': '无效的进度ID'}), 404
    
    progress = import_progress[progress_id]
    return jsonify({
        'total': progress.get('total', 0),
        'current': progress.get('current', 0),
        'success': progress.get('success', 0),
        'no_change': progress.get('no_change', 0),
        'not_found': progress.get('not_found', 0),
        'new_record': progress.get('new_record', 0),
        'error': progress.get('error', 0),
        'status': progress.get('status', 'processing'),
        'message': progress.get('message', ''),
        'errors': progress.get('errors', [])
    })

@bp.route('/admin/attendance/cleanup', methods=['POST'])
@auth.admin_required
def admin_attendance_cleanup():
    """出勤清理。"""
    before_date = request.form.get('before_date', '').strip()
    if not before_date:
        flash('请选择截止日期', 'danger')
        return redirect(url_for('.admin_dashboard'))
    
    count = models.delete_attendance_before_date(before_date)
    flash(f'已清理 {count} 条 {before_date} 之前的出勤记录', 'success')
    return redirect(url_for('.admin_dashboard'))

@bp.route('/admin/update-attendance-status', methods=['POST'])
@auth.admin_required
def admin_update_attendance_status():
    """更新出勤状态。"""
    result = models.update_attendance_status_from_latest_attendance()
    if result.get('message'):
        flash(result['message'], 'warning')
    else:
        msg = f"已根据 {result['date']} 点名更新 {result['updated']} 名学生状态（出勤→在校 {result['present']} 人，缺勤→待销假 {result['absent']} 人）"
        skipped = result.get('skipped', 0)
        if skipped:
            msg += f"，跳过 {skipped} 人（原有状态为托管/非在校等，不予覆盖）"
        flash(msg, 'success')
    return redirect(url_for('.admin_dashboard'))


@bp.route('/admin/api/mobile-qr')
@auth.admin_required
def api_mobile_qr():
    """移动端二维码。"""
    import qrcode

    manual_url = request.args.get('url', '').strip()
    if manual_url:
        mobile_url = manual_url
    else:
        lan_ip = utils.get_lan_ip()
        mobile_url = f'http://{lan_ip}:{config.PORT}/mobile/login'

    qr = qrcode.QRCode(
        version=1,
        error_correction=qrcode.constants.ERROR_CORRECT_M,
        box_size=10,
        border=4,
    )
    qr.add_data(mobile_url)
    qr.make(fit=True)

    img = qr.make_image(fill_color='black', back_color='white')
    buf = BytesIO()
    img.save(buf, format='PNG')
    buf.seek(0)

    return send_file(buf, mimetype='image/png')


@bp.route('/admin/api/mobile-url')
@auth.admin_required
def api_mobile_url():
    """移动端URL。"""
    lan_ip = utils.get_lan_ip()
    return jsonify({
        'url': f'http://{lan_ip}:{config.PORT}/mobile/login',
        'ip': lan_ip
    })


@bp.route('/admin/leave-cancel/export-attendance')
@auth.admin_required
def export_attendance_excel():
    """导出出勤Excel。"""
    from openpyxl.styles import Font, PatternFill, Alignment, Border, Side
    from openpyxl.utils import get_column_letter

    start_date = request.args.get('start_date', '').strip()
    end_date = request.args.get('end_date', '').strip()

    data = models.get_attendance_pivot_data(
        start_date=start_date or None,
        end_date=end_date or None
    )
    dates = data['dates']
    students = data['students']
    records = data['records']

    wb = Workbook()
    ws = wb.active
    ws.title = '出勤明细'

    header_font = Font(name='微软雅黑', bold=True, size=11, color='FFFFFF')
    header_fill = PatternFill(start_color='4361EE', end_color='4361EE', fill_type='solid')
    header_align = Alignment(horizontal='center', vertical='center', wrap_text=True)
    cell_align = Alignment(horizontal='center', vertical='center')
    thin_border = Border(
        left=Side(style='thin', color='D1D5DB'),
        right=Side(style='thin', color='D1D5DB'),
        top=Side(style='thin', color='D1D5DB'),
        bottom=Side(style='thin', color='D1D5DB'),
    )
    present_fill = PatternFill(start_color='ECFDF5', end_color='ECFDF5', fill_type='solid')
    present_font = Font(name='微软雅黑', color='10B981')
    absent_fill = PatternFill(start_color='FEF2F2', end_color='FEF2F2', fill_type='solid')
    absent_font = Font(name='微软雅黑', color='EF4444')

    ws.cell(row=1, column=1, value='学号')
    ws.cell(row=1, column=2, value='姓名')
    ws.cell(row=1, column=3, value='行政班级')
    ws.cell(row=1, column=4, value='身份证号')
    ws.cell(row=1, column=5, value='校别')
    ws.cell(row=1, column=6, value='入学年')
    info_cols = 6
    for idx, dt in enumerate(dates):
        ws.cell(row=1, column=info_cols + 1 + idx, value=dt)

    for col_idx in range(1, info_cols + 1 + len(dates)):
        cell = ws.cell(row=1, column=col_idx)
        cell.font = header_font
        cell.fill = header_fill
        cell.alignment = header_align
        cell.border = thin_border

    ws.column_dimensions['A'].width = 12
    ws.column_dimensions['B'].width = 10
    ws.column_dimensions['C'].width = 14
    ws.column_dimensions['D'].width = 21
    ws.column_dimensions['E'].width = 8
    ws.column_dimensions['F'].width = 9
    for idx in range(len(dates)):
        ws.column_dimensions[get_column_letter(info_cols + 1 + idx)].width = 12

    school_names = models.get_school_type_map()

    row_num = 2
    for student in students:
        school_name = school_names.get(student['school'], '') if student['school'] else ''
        ws.cell(row=row_num, column=1, value=student['student_no'] or '').alignment = cell_align
        ws.cell(row=row_num, column=1).border = thin_border
        ws.cell(row=row_num, column=2, value=student['name']).alignment = cell_align
        ws.cell(row=row_num, column=2).border = thin_border
        ws.cell(row=row_num, column=3, value=student['admin_class'] or '').alignment = cell_align
        ws.cell(row=row_num, column=3).border = thin_border
        ws.cell(row=row_num, column=4, value=student['id_card'] or '').alignment = cell_align
        ws.cell(row=row_num, column=4).border = thin_border
        ws.cell(row=row_num, column=5, value=school_name).alignment = cell_align
        ws.cell(row=row_num, column=5).border = thin_border
        ws.cell(row=row_num, column=6, value=student['enrollment_year'] or '').alignment = cell_align
        ws.cell(row=row_num, column=6).border = thin_border

        att = records.get(student['id_card'], {}).get('attendance', {})
        for idx, dt in enumerate(dates):
            status = att.get(dt, '-')
            cell = ws.cell(row=row_num, column=info_cols + 1 + idx, value=status)
            cell.alignment = cell_align
            cell.border = thin_border
            if status == '出勤':
                cell.fill = present_fill
                cell.font = present_font
            elif status == '缺勤':
                cell.fill = absent_fill
                cell.font = absent_font

        row_num += 1

    ws.freeze_panes = get_column_letter(info_cols + 1) + '2'
    ws.auto_filter.ref = ws.dimensions

    output = BytesIO()
    wb.save(output)
    output.seek(0)

    timestamp = datetime.now().strftime('%Y%m%d_%H%M')
    name_suffix = ''
    if start_date and end_date:
        name_suffix = f'_{start_date}_至_{end_date}'
    elif start_date:
        name_suffix = f'_自{start_date}'
    elif end_date:
        name_suffix = f'_至{end_date}'
    return send_file(
        output,
        mimetype='application/vnd.openxmlformats-officedocument.spreadsheetml.sheet',
        download_name=f'出勤明细{name_suffix}_{timestamp}.xlsx',
        as_attachment=True
    )


@bp.route('/admin/users', methods=['GET', 'POST'])
@auth.admin_required
def admin_users():
    """用户。"""
    if request.method == 'POST':
        action = request.form.get('action')
        
        if action == 'add':
            username = request.form.get('username', '').strip()
            password = request.form.get('password', '')
            display_name = request.form.get('display_name', '').strip()
            role = request.form.get('role', 'user')
            school_visible = ','.join(request.form.getlist('school_visible'))
            grade_visible = request.form.get('grade_visible', '*')
            fields_visible = request.form.get('fields_visible', '[]')
            
            if not school_visible:
                flash('请至少选择一个可见学校类型', 'danger')
                return redirect(url_for('.admin_users'))
            if len(password) < 6:
                flash('密码长度不能少于6位', 'danger')
                return redirect(url_for('.admin_users'))
            
            password_hash = auth.hash_password(password)
            user_id = models.create_user(
                username=username,
                password_hash=password_hash,
                role=role,
                display_name=display_name,
                school_visible=school_visible,
                fields_visible=fields_visible,
                grade_visible=grade_visible
            )
            
            if user_id:
                flash('用户创建成功', 'success')
            else:
                flash('用户名已存在', 'danger')
        
        elif action == 'edit':
            user_id = int(request.form.get('user_id'))
            display_name = request.form.get('display_name', '').strip()
            role = request.form.get('role', 'user')
            school_visible = ','.join(request.form.getlist('school_visible'))
            grade_visible = request.form.get('grade_visible', '*')
            fields_visible = request.form.get('fields_visible', '[]')
            
            if not school_visible:
                flash('请至少选择一个可见学校类型', 'danger')
                return redirect(url_for('.admin_users'))

            models.update_user(user_id, display_name=display_name, role=role,
                              school_visible=school_visible, grade_visible=grade_visible,
                              fields_visible=fields_visible)
            flash('用户更新成功', 'success')
        
        elif action == 'toggle':
            user_id = int(request.form.get('user_id'))
            models.toggle_user_status(user_id)
            flash('用户状态已更新', 'success')
        
        elif action == 'reset_password':
            user_id = int(request.form.get('user_id'))
            new_password = utils.generate_random_password()
            password_hash = auth.hash_password(new_password)
            models.update_user(user_id, password_hash=password_hash)
            flash(f'密码已重置: {new_password}', 'success')
        
        return redirect(url_for('.admin_users'))
    
    users = models.get_all_users()
    return render_template('admin/users.html', users=users, all_student_fields=config.ALL_STUDENT_FIELDS)

@bp.route('/admin/dicts')
@auth.admin_required
def admin_dicts():
    """字典。"""
    return render_template('admin/dicts.html')


@bp.route('/admin/download/card-reader')
@auth.admin_required
def download_card_reader():
    """下载读卡器控件安装包（华视电子证件读取监控软件.exe）"""
    exe_path = os.path.join(config.SDK_DIR, '华视电子证件读取监控软件.exe')
    if not os.path.exists(exe_path):
        flash('读卡器控件文件不存在，请联系管理员', 'danger')
        return redirect(url_for('.admin_students'))
    return send_file(exe_path,
                     mimetype='application/octet-stream',
                     as_attachment=True,
                     download_name='华视电子证件读取监控软件.exe')
