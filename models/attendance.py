import sqlite3
import logging
from datetime import datetime
from models.db import get_db, _non_graduated_where
from models.logs import update_sync_timestamp

logger = logging.getLogger(__name__)


def record_attendance_date(attendance_date, total_present, total_absent):
    """记录出勤日期。 参数: attendance_date, total_present, total_absent"""
    conn = get_db()
    cursor = conn.cursor()
    cursor.execute('''
        INSERT OR REPLACE INTO attendance_dates (attendance_date, total_present, total_absent)
        VALUES (?, ?, ?)
    ''', (attendance_date, total_present, total_absent))
    conn.commit()
    conn.close()

def get_attendance_dates(limit=10):
    """获取出勤日期。 参数: limit"""
    conn = get_db()
    cursor = conn.cursor()
    cursor.execute('''
        SELECT * FROM attendance_dates
        ORDER BY attendance_date DESC
        LIMIT ?
    ''', (limit,))
    rows = cursor.fetchall()
    conn.close()
    return [dict(r) for r in rows]

def get_attendance_record(id_card, attendance_date):
    """获取出勤记录。 参数: id_card, attendance_date"""
    conn = get_db()
    cursor = conn.cursor()
    cursor.execute('SELECT * FROM attendance_records WHERE id_card = ? AND attendance_date = ?',
                   (id_card, attendance_date))
    row = cursor.fetchone()
    conn.close()
    return dict(row) if row else None

def batch_upsert_attendance(records):
    """批量插入或更新出勤。 参数: records"""
    conn = get_db()
    cursor = conn.cursor()
    cursor.execute('BEGIN IMMEDIATE')
    try:
        for r in records:
            cursor.execute('''INSERT INTO attendance_records (id_card, attendance_date, status, update_time)
                              VALUES (?, ?, ?, datetime('now', 'localtime'))
                              ON CONFLICT(id_card, attendance_date) DO UPDATE SET
                              status = excluded.status, update_time = datetime('now', 'localtime')''',
                           (r['id_card'], r['attendance_date'], r['status']))
        conn.commit()
        update_sync_timestamp()
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()

def get_attendance_records_by_date(attendance_date):
    """获取出勤记录by日期。 参数: attendance_date"""
    conn = get_db()
    cursor = conn.cursor()
    cursor.execute('SELECT * FROM attendance_records WHERE attendance_date = ?', (attendance_date,))
    rows = cursor.fetchall()
    conn.close()
    return [dict(r) for r in rows]

def get_student_absence_records(id_card):
    """获取学生缺勤记录。 参数: id_card"""
    conn = get_db()
    cursor = conn.cursor()
    cursor.execute('''
        SELECT * FROM attendance_records
        WHERE id_card = ? AND status = '缺勤'
        ORDER BY attendance_date DESC
    ''', (id_card,))
    rows = cursor.fetchall()
    conn.close()
    return [dict(r) for r in rows]

def get_student_latest_absence(id_card):
    """获取学生latest缺勤。 参数: id_card"""
    conn = get_db()
    cursor = conn.cursor()
    cursor.execute('''
        SELECT * FROM attendance_records
        WHERE id_card = ? AND status = '缺勤'
        ORDER BY attendance_date DESC
        LIMIT 1
    ''', (id_card,))
    row = cursor.fetchone()
    conn.close()
    return dict(row) if row else None

def get_available_attendance_dates():
    """获取可用出勤日期。"""
    conn = get_db()
    cursor = conn.cursor()
    cursor.execute('SELECT DISTINCT attendance_date FROM attendance_records ORDER BY attendance_date DESC')
    rows = cursor.fetchall()
    conn.close()
    return [r[0] for r in rows]

def get_attendance_pivot_data(start_date=None, end_date=None):
    """获取出勤透视数据。 参数: start_date, end_date"""
    conn = get_db()
    cursor = conn.cursor()

    params = []
    where_clause = ''
    if start_date and end_date:
        where_clause = 'WHERE attendance_date >= ? AND attendance_date <= ?'
        params = [start_date, end_date]
    elif start_date:
        where_clause = 'WHERE attendance_date >= ?'
        params = [start_date]
    elif end_date:
        where_clause = 'WHERE attendance_date <= ?'
        params = [end_date]

    cursor.execute(f'SELECT DISTINCT attendance_date FROM attendance_records {where_clause} ORDER BY attendance_date ASC', params)
    dates = [r[0] for r in cursor.fetchall()]

    cursor.execute(f'''
        SELECT s.name, s.student_no, s.id_card, s.admin_class, s.school, s.enrollment_year
        FROM students s
        WHERE {_non_graduated_where('s')} AND s.attendance_status != '非在校'
        ORDER BY s.admin_class, s.student_no
    ''')
    students = [dict(r) for r in cursor.fetchall()]

    records = {}
    for s in students:
        records[s['id_card']] = {'name': s['name'], 'admin_class': s['admin_class'], 'attendance': {}}

    if dates:
        placeholders = ','.join(['?' for _ in dates])
        cursor.execute(f'''
            SELECT ar.id_card, ar.attendance_date, ar.status
            FROM attendance_records ar
            INNER JOIN students s ON ar.id_card = s.id_card AND s.is_deleted = 0 AND (s.registration_status IS NULL OR s.registration_status != '毕业')
            WHERE ar.attendance_date IN ({placeholders})
        ''', dates)
        for r in cursor.fetchall():
            row = dict(r)
            if row['id_card'] in records:
                records[row['id_card']]['attendance'][row['attendance_date']] = row['status']

    conn.close()
    return {'dates': dates, 'students': students, 'records': records}


def delete_attendance_before_date(before_date):
    """删除出勤before日期。 参数: before_date"""
    conn = get_db()
    cursor = conn.cursor()
    cursor.execute('SELECT COUNT(*) FROM attendance_records WHERE attendance_date < ?', (before_date,))
    count = cursor.fetchone()[0]
    cursor.execute('DELETE FROM attendance_records WHERE attendance_date < ?', (before_date,))
    conn.commit()
    update_sync_timestamp()
    conn.close()
    return count

def delete_attendance_for_class(admin_class, attendance_date):
    """删除出勤for班级。 参数: admin_class, attendance_date"""
    conn = get_db()
    cursor = conn.cursor()
    cursor.execute(f'''
        DELETE FROM attendance_records
        WHERE attendance_date = ? AND id_card IN (
            SELECT id_card FROM students WHERE admin_class = ? AND {_non_graduated_where()}
        )
    ''', (attendance_date, admin_class))
    deleted = cursor.rowcount
    conn.commit()
    update_sync_timestamp()
    conn.close()
    return deleted

def get_pending_leave_students():
    """获取最近一次点名中出勤为「缺勤」的在校/待销假学生，按行政班级分组。
       仅返回公开可展示字段：学号、姓名、性别、行政班级。"""
    conn = get_db()
    cursor = conn.cursor()

    # 先取最近一次点名日期
    cursor.execute("SELECT MAX(attendance_date) FROM attendance_records")
    latest_date = cursor.fetchone()[0]

    if not latest_date:
        conn.close()
        return {'classes': [], 'students': [], 'date': None}

    cursor.execute(f'''
        SELECT s.student_no, s.name, s.gender, s.admin_class
        FROM students s
        INNER JOIN attendance_records ar ON s.id_card = ar.id_card
            AND ar.attendance_date = ?
            AND ar.status = '缺勤'
        WHERE {_non_graduated_where('s')}
          AND s.attendance_status IN ('在校', '待销假')
        ORDER BY s.admin_class, s.student_no
    ''', [latest_date])
    rows = [dict(r) for r in cursor.fetchall()]

    classes_set = {}
    for r in rows:
        cls = r['admin_class'] or '未分班'
        if cls not in classes_set:
            classes_set[cls] = {'name': cls, 'count': 0}
        classes_set[cls]['count'] += 1

    classes = sorted(classes_set.values(), key=lambda c: c['name'])

    conn.close()
    return {'classes': classes, 'students': rows, 'date': latest_date}

def update_attendance_status_from_latest_attendance():
    """更新出勤状态fromlatest出勤。"""
    conn = get_db()
    cursor = conn.cursor()
    cursor.execute('BEGIN IMMEDIATE')

    cursor.execute("SELECT MAX(attendance_date) FROM attendance_records")
    last_date_row = cursor.fetchone()
    if not last_date_row or not last_date_row[0]:
        conn.rollback()
        conn.close()
        return {'updated': 0, 'message': '无点名记录'}

    last_date = last_date_row[0]

    cursor.execute('''
        SELECT ar.id_card, ar.status, s.attendance_status
        FROM attendance_records ar
        INNER JOIN students s ON ar.id_card = s.id_card AND s.is_deleted = 0
        WHERE ar.attendance_date = ?
    ''', (last_date,))
    attendance_records = cursor.fetchall()

    total_present = 0
    total_absent = 0
    total_skipped = 0
    updated_count = 0
    for record in attendance_records:
        id_card = record['id_card']
        status = record['status']
        existing_attendance = record['attendance_status'] or ''

        if existing_attendance not in ('在校', '待销假'):
            total_skipped += 1
            if status == '出勤':
                total_present += 1
            elif status == '缺勤':
                total_absent += 1
            continue

        if status == '出勤':
            new_status = '在校'
            total_present += 1
        elif status == '缺勤':
            new_status = '待销假'
            total_absent += 1
        else:
            continue

        cursor.execute('''
            UPDATE students
            SET attendance_status = ?
            WHERE id_card = ?
        ''', (new_status, id_card))
        updated_count += 1

    cursor.execute('''
        INSERT OR REPLACE INTO attendance_dates (attendance_date, total_present, total_absent)
        VALUES (?, ?, ?)
    ''', (last_date, total_present, total_absent))

    conn.commit()
    update_sync_timestamp()
    conn.close()

    return {
        'date': str(last_date),
        'updated': updated_count,
        'present': total_present,
        'absent': total_absent,
        'skipped': total_skipped,
    }

