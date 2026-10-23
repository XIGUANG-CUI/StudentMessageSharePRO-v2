from flask import Blueprint, request, redirect, url_for, render_template, session, flash
from datetime import datetime
import auth
import models

bp = Blueprint('mobile', __name__)


@bp.route('/mobile/login', methods=['GET', 'POST'])
def mobile_login():
    """login相关接口。"""
    if request.method == 'POST':
        username = request.form.get('username', '').strip()
        password = request.form.get('password', '')

        user = auth.authenticate_user(username, password)
        if user and user['role'] in ('admin', 'attendance'):
            auth.create_session(user)
            return redirect(url_for('.mobile_grades'))
        else:
            return render_template('mobile/login.html', error='用户名或密码错误，或无权限访问')

    return render_template('mobile/login.html', error=None)


@bp.route('/mobile/grades')
@auth.login_required
def mobile_grades():
    """年级。"""
    grade_visible = session.get('grade_visible', '*') if session.get('role') != 'admin' else None
    classes = models.get_all_classes_with_counts(grade_visible)
    today = datetime.now().strftime('%Y-%m-%d')
    today_records = {r['id_card']: r['status'] for r in models.get_attendance_records_by_date(today)}

    class_list = []
    total_confirmed = 0
    for cls in classes:
        students = models.get_students_by_class(cls['admin_class'])
        if not students:
            continue
        checkable_students = [s for s in students if s.get('attendance_status') != '托管']
        if checkable_students:
            all_recorded = all(s.get('id_card', s['student_no']) in today_records for s in checkable_students)
        else:
            all_recorded = True
        class_list.append({
            'name': cls['admin_class'],
            'confirmed': all_recorded,
            'count': cls['total'],
            'grades': cls['grades'] or ''
        })
        if all_recorded:
            total_confirmed += 1

    return render_template('mobile/grades.html', classes=class_list,
                           total=len(class_list), confirmed=total_confirmed)


@bp.route('/mobile/grades/<grade>/classes')
@auth.login_required
def mobile_classes(grade):
    """班级。 参数: grade"""
    return redirect(url_for('.mobile_grades'))


@bp.route('/mobile/classes/<grade>/<admin_class>/attendance')
@auth.attendance_required
def mobile_attendance_legacy(grade, admin_class):
    """出勤legacy。 参数: grade, admin_class"""
    return redirect(url_for('.mobile_attendance', admin_class=admin_class))


@bp.route('/mobile/classes/<admin_class>/attendance', methods=['GET', 'POST'])
@auth.attendance_required
def mobile_attendance(admin_class):
    """出勤。 参数: admin_class"""
    students = models.get_students_by_class(admin_class)
    today = datetime.now().strftime('%Y-%m-%d')

    if request.method == 'POST':
        records = []
        for key in request.form:
            if not key.startswith('status_'):
                continue
            status_str = request.form.get(key)
            if status_str == '-1':
                continue
            id_card = key[7:]
            records.append({
                'id_card': id_card,
                'attendance_date': today,
                'status': '出勤' if status_str == '1' else '缺勤'
            })

        try:
            if records:
                models.batch_upsert_attendance(records)
                flash(f'{admin_class} {len(records)} 人出勤记录已保存', 'success')
            else:
                deleted = models.delete_attendance_for_class(admin_class, today)
                if deleted > 0:
                    flash(f'{admin_class} 已重置，清除了 {deleted} 条出勤记录', 'success')
                else:
                    flash(f'{admin_class} 未操作任何学生，未保存记录', 'warning')
        except Exception as e:
            flash(f'保存失败: {str(e)}', 'danger')

        return redirect(url_for('.mobile_grades'))

    today_records = {r['id_card']: r['status'] for r in models.get_attendance_records_by_date(today)}

    student_list = []
    tuoguan_count = 0
    for s in students:
        if s.get('attendance_status') == '托管':
            default_status = 1
            tuoguan_count += 1
        elif s['id_card'] in today_records:
            default_status = today_records[s['id_card']]
        else:
            default_status = -1
        student_list.append({
            'student_no': s['student_no'],
            'id_card': s['id_card'],
            'name': s['name'],
            'name_pinyin': s.get('name_pinyin', ''),
            'status': default_status,
            'is_tuoguan': s.get('attendance_status') == '托管'
        })

    return render_template('mobile/attendance.html', admin_class=admin_class,
                           students=student_list,
                           tuoguan_count=tuoguan_count)
