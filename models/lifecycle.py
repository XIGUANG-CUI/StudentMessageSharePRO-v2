import sqlite3
import logging
from datetime import datetime
from models.db import get_db
from models.logs import update_sync_timestamp

logger = logging.getLogger(__name__)


def get_graduated_students(page=1, per_page=20, grade=None):
    """获取毕业学生列表（按学籍状态=毕业筛选），支持按年级过滤"""
    conn = get_db()
    cursor = conn.cursor()

    where_clauses = ["s.registration_status = '毕业'", "s.is_deleted = 0"]
    params = []
    if grade:
        where_clauses.append("s.grade = ?")
        params.append(grade)

    where_sql = ' AND '.join(where_clauses)

    count_query = f'SELECT COUNT(*) FROM students s WHERE {where_sql}'
    cursor.execute(count_query, params)
    total = cursor.fetchone()[0]

    offset = (page - 1) * per_page
    data_query = f'''
        SELECT s.*
        FROM students s
        WHERE {where_sql}
        ORDER BY s.grade DESC, s.admin_class, s.name
        LIMIT ? OFFSET ?
    '''
    cursor.execute(data_query, params + [per_page, offset])
    students = [dict(row) for row in cursor.fetchall()]
    conn.close()

    return {
        'students': students,
        'total': total,
        'page': page,
        'per_page': per_page,
        'pages': (total + per_page - 1) // per_page if total > 0 else 1,
    }


def get_graduated_students_all(grade=None):
    """获取所有毕业学生（用于导出），支持按年级过滤"""
    conn = get_db()
    cursor = conn.cursor()

    params = []
    where_clauses = ["s.registration_status = '毕业'", "s.is_deleted = 0"]
    if grade:
        where_clauses.append("s.grade = ?")
        params.append(grade)

    query = f'''
        SELECT s.*
        FROM students s
        WHERE {' AND '.join(where_clauses)}
        ORDER BY s.grade DESC, s.admin_class, s.name
    '''
    cursor.execute(query, params)
    students = [dict(row) for row in cursor.fetchall()]
    conn.close()
    return students


def get_graduated_list_grades():
    """获取已毕业学生的年级列表（用于列表页筛选）"""
    conn = get_db()
    cursor = conn.cursor()
    cursor.execute(
        '''SELECT DISTINCT grade FROM students
           WHERE registration_status = '毕业'
             AND is_deleted = 0
             AND grade IS NOT NULL AND grade != ''
           ORDER BY grade DESC'''
    )
    grades = [row['grade'] for row in cursor.fetchall()]
    conn.close()
    return grades

def get_graduation_eligible_students(grade, admin_class=None):
    """获取指定年级可进行毕业处理的学生（学籍状态非毕业且非退学办结）"""
    conn = get_db()
    cursor = conn.cursor()
    params = [grade]
    query = '''
        SELECT id, name, name_pinyin, student_no, id_card, admin_class,
               attendance_status, registration_status, remark, grade, major
        FROM students
        WHERE grade = ? AND is_deleted = 0
          AND (registration_status IS NULL OR registration_status != '毕业')
          AND attendance_status != '退学办结'
    '''
    if admin_class:
        query += ' AND admin_class = ?'
        params.append(admin_class)
    query += ' ORDER BY admin_class, name'
    cursor.execute(query, params)
    students = [dict(row) for row in cursor.fetchall()]
    conn.close()
    return students


def get_graduation_eligible_classes(grade):
    """获取指定年级可进行毕业处理的班级列表"""
    conn = get_db()
    cursor = conn.cursor()
    cursor.execute('''
        SELECT DISTINCT admin_class
        FROM students
        WHERE grade = ? AND is_deleted = 0
          AND (registration_status IS NULL OR registration_status != '毕业')
          AND attendance_status != '退学办结'
          AND admin_class IS NOT NULL
        ORDER BY admin_class
    ''', (grade,))
    classes = [row[0] for row in cursor.fetchall()]
    conn.close()
    return classes

def process_graduation(student_ids, operator):
    """批量毕业处理（事务）：学籍状态→毕业，在校状态→非在校，原始在校状态→备注备份"""
    conn = get_db()
    cursor = conn.cursor()
    try:
        cursor.execute('BEGIN IMMEDIATE')
        processed = 0
        details = []

        for sid in student_ids:
            student = cursor.execute(
                'SELECT id, name, student_no, id_card, attendance_status, registration_status, remark FROM students WHERE id = ?',
                (sid,)
            ).fetchone()
            if not student:
                continue

            old_attendance = student['attendance_status'] or ''
            old_registration = student['registration_status'] or ''
            old_remark = student['remark'] or ''

            # 备份原始状态到备注（用于撤销恢复）
            backup_note = f'[毕业处理前：学籍={old_registration}，在校={old_attendance}]'
            new_remark = (old_remark + ' ' + backup_note).strip() if old_remark else backup_note

            cursor.execute(
                'UPDATE students SET registration_status = ?, attendance_status = ?, remark = ? WHERE id = ?',
                ('毕业', '非在校', new_remark, sid)
            )
            # 在事务内直接写 update_logs，避免 log_update() 另开连接导致锁冲突
            if old_registration != '毕业':
                cursor.execute(
                    'INSERT INTO update_logs (student_id, id_card, field_name, old_value, new_value, updated_by, created_at) VALUES (?, ?, ?, ?, ?, ?, datetime(\'now\', \'localtime\'))',
                    (sid, student['id_card'] or '', 'registration_status', old_registration, '毕业', operator)
                )
            if old_attendance != '非在校':
                cursor.execute(
                    'INSERT INTO update_logs (student_id, id_card, field_name, old_value, new_value, updated_by, created_at) VALUES (?, ?, ?, ?, ?, ?, datetime(\'now\', \'localtime\'))',
                    (sid, student['id_card'] or '', 'attendance_status', old_attendance, '非在校', operator)
                )

            processed += 1
            details.append(f"{student['name']}（学籍：{old_registration}→毕业，在校：{old_attendance}→非在校）")

        conn.commit()
        update_sync_timestamp()
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()
    return processed, details

def undo_graduation(student_ids, operator):
    """批量撤销毕业处理：从备注解析原始状态并恢复（事务）"""
    import re
    conn = get_db()
    cursor = conn.cursor()
    try:
        cursor.execute('BEGIN IMMEDIATE')
        processed = 0
        failed = 0
        details = []
        undone_ids = []

        for sid in student_ids:
            student = cursor.execute(
                'SELECT id, name, student_no, id_card, registration_status, attendance_status, remark FROM students WHERE id = ?',
                (sid,)
            ).fetchone()
            if not student:
                failed += 1
                details.append(f'ID={sid}：学生不存在')
                continue
            if student['registration_status'] != '毕业':
                failed += 1
                details.append(f"{student['name']}：当前不处于毕业状态，已跳过")
                continue

            remark = student['remark'] or ''
            m = re.search(r'\[毕业处理前：学籍=(.+?)，在校=(.+?)\]', remark)
            if not m:
                failed += 1
                details.append(f"{student['name']}：备注中未找到毕业前状态备份，已跳过")
                continue

            old_registration = m.group(1) or ''
            old_attendance = m.group(2) or ''
            new_remark = re.sub(r'\s?\[毕业处理前：学籍=.+?，在校=.+?\]', '', remark).strip()
            if new_remark == '':
                new_remark = None

            cursor.execute(
                'UPDATE students SET registration_status = ?, attendance_status = ?, remark = ? WHERE id = ?',
                (old_registration, old_attendance, new_remark, sid)
            )
            cursor.execute(
                'INSERT INTO update_logs (student_id, id_card, field_name, old_value, new_value, updated_by, created_at) VALUES (?, ?, ?, ?, ?, ?, datetime(\'now\', \'localtime\'))',
                (sid, student['id_card'] or '', 'registration_status', '毕业', old_registration, operator)
            )
            cursor.execute(
                'INSERT INTO update_logs (student_id, id_card, field_name, old_value, new_value, updated_by, created_at) VALUES (?, ?, ?, ?, ?, ?, datetime(\'now\', \'localtime\'))',
                (sid, student['id_card'] or '', 'attendance_status', '非在校', old_attendance, operator)
            )

            processed += 1
            undone_ids.append(sid)
            details.append(f"{student['name']}（学籍：毕业→{old_registration}，在校：非在校→{old_attendance}）")

        conn.commit()
        update_sync_timestamp()
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()
    if undone_ids:
        try:
            from models.archive import mark_dispatches_invalid
            mark_dispatches_invalid(undone_ids, operator)
        except Exception:
            logger.exception('撤销毕业联动派遣记录失效失败')
    return processed, failed, details

def get_graduation_distinct_grades():
    """获取所有可毕业处理年级（排除学籍已毕业和退学办结）"""
    conn = get_db()
    cursor = conn.cursor()
    cursor.execute(
        '''SELECT DISTINCT grade FROM students
           WHERE is_deleted = 0
             AND (registration_status IS NULL OR registration_status != '毕业')
             AND attendance_status != '退学办结'
             AND grade IS NOT NULL AND grade != ''
           ORDER BY grade DESC'''
    )
    grades = [row['grade'] for row in cursor.fetchall()]
    conn.close()
    return grades

def get_internship_eligible_students(grade, admin_class=None):
    """获取指定年级可进行实习处理的学生（在校状态非退学办结、非毕业，备注不含实习标记）"""
    conn = get_db()
    cursor = conn.cursor()
    query = '''
        SELECT id, name, name_pinyin, student_no, id_card, admin_class,
               attendance_status, registration_status, remark, grade, major
        FROM students
        WHERE grade = ? AND is_deleted = 0
          AND attendance_status != '退学办结'
          AND (registration_status IS NULL OR registration_status != '毕业')
          AND (remark IS NULL OR remark NOT LIKE '%[实习处理：%')
    '''
    params = [grade]
    if admin_class:
        query += ' AND admin_class = ?'
        params.append(admin_class)
    query += ' ORDER BY admin_class, name'
    cursor.execute(query, params)
    students = [dict(row) for row in cursor.fetchall()]
    conn.close()
    return students


def get_internship_eligible_classes(grade):
    """获取指定年级可实习处理的班级列表"""
    conn = get_db()
    cursor = conn.cursor()
    cursor.execute(
        '''SELECT DISTINCT admin_class FROM students
           WHERE grade = ? AND is_deleted = 0
             AND attendance_status != '退学办结'
             AND (registration_status IS NULL OR registration_status != '毕业')
             AND (remark IS NULL OR remark NOT LIKE '%[实习处理：%')
             AND admin_class IS NOT NULL AND admin_class != ''
           ORDER BY admin_class''',
        (grade,)
    )
    classes = [row['admin_class'] for row in cursor.fetchall()]
    conn.close()
    return classes

def process_internship(student_ids, operator):
    """批量实习处理（事务）：在校状态→非在校，备注追加[实习处理]"""
    conn = get_db()
    cursor = conn.cursor()
    try:
        cursor.execute('BEGIN IMMEDIATE')
        processed = 0
        details = []

        for sid in student_ids:
            student = cursor.execute(
                'SELECT id, name, student_no, id_card, attendance_status, remark FROM students WHERE id = ?',
                (sid,)
            ).fetchone()
            if not student:
                continue

            old_attendance = student['attendance_status'] or '在校'
            old_remark = student['remark'] or ''

            # 备份原始在校状态到备注（用于撤销恢复）
            backup_note = f'[实习处理：原始在校状态={old_attendance}]'
            new_remark = (old_remark + ' ' + backup_note).strip() if old_remark else backup_note

            cursor.execute(
                'UPDATE students SET attendance_status = ?, remark = ? WHERE id = ?',
                ('非在校', new_remark, sid)
            )
            # 事务内直接写日志
            if old_attendance != '非在校':
                cursor.execute(
                    'INSERT INTO update_logs (student_id, id_card, field_name, old_value, new_value, updated_by, created_at) VALUES (?, ?, ?, ?, ?, ?, datetime(\'now\', \'localtime\'))',
                    (sid, student['id_card'] or '', 'attendance_status', old_attendance, '非在校', operator)
                )
            cursor.execute(
                'INSERT INTO update_logs (student_id, id_card, field_name, old_value, new_value, updated_by, created_at) VALUES (?, ?, ?, ?, ?, ?, datetime(\'now\', \'localtime\'))',
                (sid, student['id_card'] or '', 'remark', old_remark, new_remark, operator)
            )

            processed += 1
            details.append(f"{student['name']}（在校：{old_attendance}→非在校，备注：已追加实习标记）")

        conn.commit()
        update_sync_timestamp()
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()
    return processed, details

def undo_internship(student_ids, operator):
    """批量撤销实习处理：从备注解析原始在校状态并恢复"""
    import re
    conn = get_db()
    cursor = conn.cursor()
    try:
        cursor.execute('BEGIN IMMEDIATE')
        processed = 0
        failed = 0
        details = []

        for sid in student_ids:
            student = cursor.execute(
                'SELECT id, name, student_no, id_card, attendance_status, remark FROM students WHERE id = ?',
                (sid,)
            ).fetchone()
            if not student:
                failed += 1
                details.append(f'ID={sid}：学生不存在')
                continue
            if student['attendance_status'] != '非在校':
                failed += 1
                details.append(f"{student['name']}：当前在校状态非「非在校」，已跳过")
                continue

            remark = student['remark'] or ''
            m = re.search(r'\[实习处理：原始在校状态=(.+?)\]', remark)
            if not m:
                failed += 1
                details.append(f"{student['name']}：备注中未找到实习前状态备份，已跳过")
                continue

            old_attendance = m.group(1) or '在校'
            new_remark = re.sub(r'\s?\[实习处理：原始在校状态=.+?\]', '', remark).strip()
            if new_remark == '':
                new_remark = None

            cursor.execute(
                'UPDATE students SET attendance_status = ?, remark = ? WHERE id = ?',
                (old_attendance, new_remark, sid)
            )
            cursor.execute(
                'INSERT INTO update_logs (student_id, id_card, field_name, old_value, new_value, updated_by, created_at) VALUES (?, ?, ?, ?, ?, ?, datetime(\'now\', \'localtime\'))',
                (sid, student['id_card'] or '', 'attendance_status', '非在校', old_attendance, operator)
            )

            processed += 1
            details.append(f"{student['name']}（在校：非在校→{old_attendance}）")

        conn.commit()
        update_sync_timestamp()
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()
    return processed, failed, details

def get_internship_grades():
    """获取所有可实习处理年级（在校状态为非退学办结、非毕业）"""
    conn = get_db()
    cursor = conn.cursor()
    cursor.execute(
        '''SELECT DISTINCT grade FROM students
           WHERE is_deleted = 0
             AND attendance_status != '退学办结'
             AND (registration_status IS NULL OR registration_status != '毕业')
             AND (remark IS NULL OR remark NOT LIKE '%[实习处理：%')
             AND grade IS NOT NULL AND grade != ''
           ORDER BY grade DESC'''
    )
    grades = [row['grade'] for row in cursor.fetchall()]
    conn.close()
    return grades


def get_internship_processed_students(grade):
    """获取指定年级已实习处理的学生（在校状态=非在校 且 备注含实习标记，用于撤销）"""
    conn = get_db()
    cursor = conn.cursor()
    query = '''
        SELECT id, name, name_pinyin, student_no, id_card, admin_class,
               attendance_status, registration_status, remark, grade, major
        FROM students
        WHERE grade = ? AND is_deleted = 0
          AND attendance_status = '非在校'
          AND remark LIKE '%[实习处理：%'
          AND (registration_status IS NULL OR registration_status != '毕业')
        ORDER BY admin_class, name
    '''
    cursor.execute(query, (grade,))
    students = [dict(row) for row in cursor.fetchall()]
    conn.close()
    return students


def get_internship_processed_grades():
    """获取所有有已实习处理学生的年级"""
    conn = get_db()
    cursor = conn.cursor()
    cursor.execute(
        '''SELECT DISTINCT grade FROM students
           WHERE is_deleted = 0
             AND attendance_status = '非在校'
             AND remark LIKE '%[实习处理：%'
             AND grade IS NOT NULL AND grade != ''
           ORDER BY grade DESC'''
    )
    grades = [row['grade'] for row in cursor.fetchall()]
    conn.close()
    return grades

