from flask import Blueprint, render_template, request, jsonify, session
from datetime import datetime
import models
import auth
from auth import _require_admin_json

bp = Blueprint('leave_cancel', __name__)


@bp.route('/api/admin/trash/students')
def api_trash_students():
    """回收站学生。"""
    auth_error = _require_admin_json()
    if auth_error:
        return auth_error
    students = models.get_deleted_students()
    return jsonify({'students': students, 'count': len(students)})

@bp.route('/api/admin/trash/restore/<int:student_id>', methods=['POST'])
def api_trash_restore(student_id):
    """回收站恢复。 参数: student_id"""
    auth_error = _require_admin_json()
    if auth_error:
        return auth_error
    affected = models.restore_student(student_id)
    if affected:
        return jsonify({'success': True, 'message': '已恢复'})
    return jsonify({'success': False, 'message': '恢复失败，学生可能已被恢复或不存在'}), 404

@bp.route('/api/admin/trash/delete/<int:student_id>', methods=['POST'])
def api_trash_delete(student_id):
    """回收站删除。 参数: student_id"""
    auth_error = _require_admin_json()
    if auth_error:
        return auth_error
    affected = models.permanently_delete_student(student_id)
    if affected:
        return jsonify({'success': True, 'message': '已永久删除'})
    return jsonify({'success': False, 'message': '删除失败'}), 404

@bp.route('/api/admin/trash/restore-all', methods=['POST'])
def api_trash_restore_all():
    """回收站恢复全部。"""
    auth_error = _require_admin_json()
    if auth_error:
        return auth_error
    count = models.restore_all_deleted_students()
    return jsonify({'success': True, 'message': f'已恢复 {count} 名学生', 'count': count})

@bp.route('/api/admin/trash/delete-all', methods=['POST'])
def api_trash_delete_all():
    """回收站删除全部。"""
    auth_error = _require_admin_json()
    if auth_error:
        return auth_error
    count = models.permanently_delete_all_deleted_students()
    return jsonify({'success': True, 'message': f'已永久删除 {count} 名学生', 'count': count})

@bp.route('/admin/leave-cancel', methods=['GET', 'POST'])
@auth.admin_required
def admin_leave_cancel():
    """销假取消。"""
    today = datetime.now().strftime('%Y-%m-%d')
    attendance_date = request.args.get('attendance_date', today)
    search_result = None
    error_message = None
    success_message = None

    if request.method == 'POST':
        action = request.form.get('action')
        if action == 'search':
            keyword = request.form.get('keyword', '').strip()
            if not keyword:
                error_message = '请输入学号、身份证号或姓名'
            else:
                student = None
                if len(keyword) >= 10 and keyword.isdigit():
                    student = models.get_student_by_id_card(keyword)
                if not student:
                    student = models.get_student_by_student_no(keyword)
                if not student:
                    students = models.get_students(filters={'name': keyword}, page=1, per_page=10)
                    if students and len(students) == 1:
                        student = students[0]
                    elif students and len(students) > 1:
                        error_message = f'找到多条同名记录，请输入学号或身份证号'
                if not student and not error_message:
                    students = models.get_students(filters={'id_card_partial': keyword}, page=1, per_page=10)
                    if students and len(students) == 1:
                        student = students[0]
                    elif students and len(students) > 1:
                        error_message = f'找到多条匹配记录，请输入更完整的信息'
                if student:
                    record = models.get_attendance_record(student['id_card'], attendance_date)
                    search_result = {
                        'student': student,
                        'attendance_record': record,
                        'attendance_date': attendance_date
                    }
                elif not error_message:
                    error_message = '未找到匹配的学生'
        elif action == 'cancel_leave':
            id_card = request.form.get('id_card', '').strip()
            att_date = request.form.get('attendance_date', '')
            if id_card and att_date:
                try:
                    models.batch_upsert_attendance([{
                        'id_card': id_card,
                        'attendance_date': att_date,
                        'status': '出勤'
                    }])
                    models.update_sync_timestamp()
                    success_message = f'学生 {id_card} {att_date} 出勤状态已更新为"出勤"'
                    search_result = None
                except Exception as e:
                    error_message = f'更新失败：{str(e)}'

    return render_template('admin/leave_cancel.html',
                         today=today,
                         attendance_date=attendance_date,
                         search_result=search_result,
                         error_message=error_message,
                         success_message=success_message)

@bp.route('/api/leave-cancel/search/absences', methods=['GET'])
def api_leave_cancel_search_absences():
    """销假取消搜索缺勤记录。"""
    auth_error = _require_admin_json()
    if auth_error:
        return auth_error
    keyword = request.args.get('keyword', '').strip()
    if not keyword:
        return jsonify({'results': [], 'total': 0})

    results = []
    students_list = []

    if keyword.isdigit() and len(keyword) >= 6:
        student = models.get_student_by_student_no(keyword)
        if student:
            students_list = [student]
    else:
        result = models.get_students(filters={'search': keyword}, page=1, per_page=20)
        students_list = result['students'] if result else []

    if not students_list and keyword.isdigit() and len(keyword) <= 6:
        result = models.get_students(filters={'id_card_partial': keyword}, page=1, per_page=20)
        students_list = result['students'] if result else []

    if not students_list:
        return jsonify({'results': [], 'total': 0, 'error': '未找到该学生'})

    for student in students_list:
        absence = models.get_student_latest_absence(student['id_card'])
        results.append({
            'student': {
                'id': student['id'],
                'student_no': student['student_no'],
                'id_card': student['id_card'],
                'name': student['name'],
                'gender': student['gender'],
                'major': student['major'],
                'admin_class': student['admin_class']
            },
            'absence': absence,
            'has_absence': absence is not None
        })

    return jsonify({'results': results, 'total': len(results)})

@bp.route('/api/leave-cancel/search/realtime', methods=['GET'])
def api_leave_cancel_search_realtime():
    """销假取消搜索realtime。"""
    auth_error = _require_admin_json()
    if auth_error:
        return auth_error
    keyword = request.args.get('keyword', '').strip()
    attendance_date = request.args.get('attendance_date', datetime.now().strftime('%Y-%m-%d'))
    attendance_status_filter = request.args.get('status', '').strip()

    filters = {}
    has_keyword = False

    if keyword:
        has_keyword = True
        if keyword.isdigit() and len(keyword) >= 6:
            filters['student_no'] = keyword
        elif keyword.isdigit():
            filters['id_card_suffix'] = keyword
        else:
            filters['name_fuzzy'] = keyword

    if attendance_status_filter:
        filters['attendance_date'] = attendance_date
        filters['attendance_status_filter'] = attendance_status_filter

    if not has_keyword and not attendance_status_filter:
        return jsonify({'students': [], 'total': 0})

    conn = models.get_db()
    cursor = conn.cursor()

    base_where = ['s.is_deleted = 0']
    params = []

    if keyword:
        if keyword.isdigit() and len(keyword) >= 6:
            base_where.append('s.student_no = ?')
            params.append(keyword)
        elif keyword.isdigit():
            base_where.append('s.id_card LIKE ?')
            params.append('%' + keyword)
        else:
            base_where.append('(s.name LIKE ? OR s.name_pinyin LIKE ?)')
            params.append('%' + keyword + '%')
            params.append('%' + keyword + '%')

    where_sql = ' AND '.join(base_where)

    if attendance_status_filter:
        query = f'''
            SELECT s.*, ar.status as today_attendance, ar.attendance_date
            FROM students s
            LEFT JOIN attendance_records ar ON s.id_card = ar.id_card AND ar.attendance_date = ?
            WHERE {where_sql}
            ORDER BY s.student_no
            LIMIT 50
        '''
        params_inner = [attendance_date] + params
    else:
        query = f'''
            SELECT s.*, ar.status as today_attendance, ar.attendance_date
            FROM students s
            LEFT JOIN attendance_records ar ON s.id_card = ar.id_card AND ar.attendance_date = ?
            WHERE {where_sql}
            ORDER BY s.student_no
            LIMIT 50
        '''
        params_inner = [attendance_date] + params

    cursor.execute(query, params_inner)
    rows = cursor.fetchall()
    conn.close()

    students = []
    for row in rows:
        student = dict(row)
        record_status = student.get('today_attendance')
        if attendance_status_filter:
            if attendance_status_filter == '缺勤' and record_status != '缺勤':
                continue
            elif attendance_status_filter == '出勤' and record_status != '出勤':
                continue
        students.append({
            'id': student['id'],
            'student_no': student['student_no'] or '-',
            'name': student['name'] or '-',
            'gender': student['gender'] or '-',
            'id_card': student['id_card'] or '-',
            'major': student['major'] or '-',
            'admin_class': student['admin_class'] or '-',
            'attendance_status': student.get('attendance_status', '-'),
            'today_attendance': record_status or '-',
            'attendance_date': attendance_date
        })

    return jsonify({'students': students, 'total': len(students)})

@bp.route('/api/leave-cancel/search', methods=['GET'])
def api_leave_cancel_search():
    """销假取消搜索。"""
    auth_error = _require_admin_json()
    if auth_error:
        return auth_error
    keyword = request.args.get('keyword', '').strip()
    attendance_date = request.args.get('attendance_date', datetime.now().strftime('%Y-%m-%d'))
    if not keyword:
        return jsonify({'error': '请输入学号、身份证号或姓名'}), 400

    student = None
    if len(keyword) >= 10 and keyword.isdigit():
        student = models.get_student_by_id_card(keyword)
    if not student:
        student = models.get_student_by_student_no(keyword)
    if not student:
        result = models.get_students(filters={'name': keyword}, page=1, per_page=10)
        name_students = result['students'] if result else []
        if len(name_students) == 1:
            student = name_students[0]
        elif len(name_students) > 1:
            return jsonify({'error': '找到多条同名记录，请输入学号或身份证号'}), 400
    # 按拼音查找
    if not student:
        result = models.get_students(filters={'search': keyword}, page=1, per_page=10)
        pinyin_students = result['students'] if result else []
        if len(pinyin_students) == 1:
            student = pinyin_students[0]
        elif len(pinyin_students) > 1:
            return jsonify({'error': '找到多条匹配记录，请输入学号或身份证号'}), 400
    if not student:
        result = models.get_students(filters={'id_card_partial': keyword}, page=1, per_page=10)
        id_students = result['students'] if result else []
        if len(id_students) == 1:
            student = id_students[0]
        elif len(id_students) > 1:
            return jsonify({'error': '找到多条匹配记录，请输入更完整的信息'}), 400

    if not student:
        return jsonify({'error': '未找到匹配的学生'}), 404

    record = models.get_attendance_record(student['id_card'], attendance_date)
    return jsonify({
        'student': {
            'id': student['id'],
            'student_no': student['student_no'],
            'name': student['name'],
            'id_card': student['id_card'],
            'gender': student['gender'],
            'major': student['major'],
            'admin_class': student['admin_class'],
            'attendance_status': student['attendance_status']
        },
        'attendance_record': record,
        'attendance_date': attendance_date
    })

@bp.route('/api/leave-cancel/confirm', methods=['POST'])
def api_leave_cancel_confirm():
    """销假取消确认。"""
    auth_error = _require_admin_json()
    if auth_error:
        return auth_error
    data = request.get_json()
    id_card = data.get('id_card', '').strip()
    attendance_date = data.get('attendance_date', '').strip()
    if not id_card or not attendance_date:
        return jsonify({'error': '参数不完整'}), 400
    try:
        models.batch_upsert_attendance([{
            'id_card': id_card,
            'attendance_date': attendance_date,
            'status': '出勤'
        }])
        models.update_sync_timestamp()
        return jsonify({'success': True, 'message': f'学生 {id_card} 出勤状态已更新'})
    except Exception as e:
        return jsonify({'error': f'更新失败：{str(e)}'}), 500
