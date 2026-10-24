import sqlite3
import re
import logging
from datetime import datetime, timedelta
from models.db import get_db, _exclude_graduated_clause
from models.district import fill_district_fields, DISTRICT_FIELDS
from models.logs import update_sync_timestamp
from models.school_types import get_school_type_map

logger = logging.getLogger(__name__)


def _to_null_if_empty(value):
    """将空字符串转换为None（NULL），用于唯一约束字段"""
    if value is None or (isinstance(value, str) and value.strip() == ''):
        return None
    return value.strip() if isinstance(value, str) else value

def create_student(student_data):
    """创建学生。 参数: student_data"""
    conn = get_db()
    cursor = conn.cursor()
    try:
        student_no = _to_null_if_empty(student_data.get('student_no'))
        national_no = _to_null_if_empty(student_data.get('national_no'))
        id_card = _to_null_if_empty(student_data.get('id_card'))
        
        if student_no:
            existing = get_student_by_student_no(student_no)
            if existing:
                raise ValueError(f'学号 {student_no} 已存在')
        
        if national_no:
            existing = get_student_by_national_no(national_no)
            if existing:
                raise ValueError(f'国网学籍号 {national_no} 已存在')
        
        if id_card:
            existing = get_student_by_id_card(id_card)
            if existing:
                raise ValueError(f'身份证号 {id_card} 已存在')

        # 区划联动：就地补 7 键（纯内存、不抛异常；事务仍由本函数控制）
        fill_district_fields(student_data, conn)

        cursor.execute('''
            INSERT INTO students (student_no, national_no, id_card, name, gender, birth_date,
                                  ethnicity, address, major, admin_class, virtual_class,
                                  parent_name, parent_id_card, parent_phone, student_phone,
                                  grade, school, registration_status, attendance_status,
                                  issue_authority, valid_start, valid_end, major_direction,
                                  household_type, junior_school, admission_type,
                                  junior_graduation_time, change_info, remark, enrollment_year,
                                  name_pinyin, district_province_name, district_province_code,
                                  district_city_name, district_city_code, district_county_name,
                                  district_county_code, district_match_status, created_at)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?,
                    ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?,
                    datetime('now', 'localtime'))
        ''', (
            student_no,
            national_no,
            id_card,
            student_data.get('name'),
            student_data.get('gender'),
            student_data.get('birth_date'),
            _to_null_if_empty(student_data.get('ethnicity')),
            _to_null_if_empty(student_data.get('address')),
            _to_null_if_empty(student_data.get('major')),
            _to_null_if_empty(student_data.get('admin_class')),
            _to_null_if_empty(student_data.get('virtual_class')),
            _to_null_if_empty(student_data.get('parent_name')),
            _to_null_if_empty(student_data.get('parent_id_card')),
            _to_null_if_empty(student_data.get('parent_phone')),
            _to_null_if_empty(student_data.get('student_phone')),
            _to_null_if_empty(student_data.get('grade')),
            student_data.get('school'),
            student_data.get('registration_status', '正式学籍'),
            student_data.get('attendance_status', '在校'),
            _to_null_if_empty(student_data.get('issue_authority')),
            _to_null_if_empty(student_data.get('valid_start')),
            _to_null_if_empty(student_data.get('valid_end')),
            _to_null_if_empty(student_data.get('major_direction')),
            _to_null_if_empty(student_data.get('household_type')),
            _to_null_if_empty(student_data.get('junior_school')),
            _to_null_if_empty(student_data.get('admission_type')),
            _to_null_if_empty(student_data.get('junior_graduation_time')),
            _to_null_if_empty(student_data.get('change_info')),
            _to_null_if_empty(student_data.get('remark')),
            student_data.get('enrollment_year', 0),
            student_data.get('name_pinyin', ''),
            student_data.get('district_province_name', ''),
            student_data.get('district_province_code', ''),
            student_data.get('district_city_name', ''),
            student_data.get('district_city_code', ''),
            student_data.get('district_county_name', ''),
            student_data.get('district_county_code', ''),
            student_data.get('district_match_status', '')
        ))
        conn.commit()
        update_sync_timestamp()
        return cursor.lastrowid
    except ValueError:
        raise
    except sqlite3.IntegrityError as e:
        conn.rollback()
        error_msg = str(e)
        if 'student_no' in error_msg:
            raise ValueError(f'学号 {student_no} 已存在')
        elif 'national_no' in error_msg:
            raise ValueError(f'国网学籍号 {national_no} 已存在')
        elif 'id_card' in error_msg:
            raise ValueError(f'身份证号 {id_card} 已存在')
        else:
            raise ValueError('数据冲突，请检查输入')
    finally:
        conn.close()

def create_student_with_conn(conn, student_data):
    """复用外部连接创建学生，调用方负责 commit 和 close。
       适用于 daemon 线程批量导入场景，避免每条记录重复创建连接。"""
    cursor = conn.cursor()
    try:
        student_no = _to_null_if_empty(student_data.get('student_no'))
        national_no = _to_null_if_empty(student_data.get('national_no'))
        id_card = _to_null_if_empty(student_data.get('id_card'))

        if student_no:
            cursor.execute('SELECT * FROM students WHERE student_no = ? AND is_deleted = 0', (student_no,))
            if cursor.fetchone():
                raise ValueError(f'学号 {student_no} 已存在')

        if national_no:
            cursor.execute('SELECT * FROM students WHERE national_no = ? AND is_deleted = 0', (national_no,))
            if cursor.fetchone():
                raise ValueError(f'国网学籍号 {national_no} 已存在')

        if id_card:
            cursor.execute('SELECT * FROM students WHERE id_card = ? AND is_deleted = 0', (id_card,))
            if cursor.fetchone():
                raise ValueError(f'身份证号 {id_card} 已存在')

        # 区划联动：批量导入逐行补 7 键（>500 行请改用 fill_district_fields_many）
        fill_district_fields(student_data, conn)

        cursor.execute('''
            INSERT INTO students (student_no, national_no, id_card, name, gender, birth_date,
                                  ethnicity, address, major, admin_class, virtual_class,
                                  parent_name, parent_id_card, parent_phone, student_phone,
                                  grade, school, registration_status, attendance_status,
                                  issue_authority, valid_start, valid_end, major_direction,
                                  household_type, junior_school, admission_type,
                                  junior_graduation_time, change_info, remark, enrollment_year,
                                  name_pinyin, district_province_name, district_province_code,
                                  district_city_name, district_city_code, district_county_name,
                                  district_county_code, district_match_status, created_at)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?,
                    ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?,
                    datetime('now', 'localtime'))
        ''', (
            student_no,
            national_no,
            id_card,
            student_data.get('name'),
            student_data.get('gender'),
            student_data.get('birth_date'),
            _to_null_if_empty(student_data.get('ethnicity')),
            _to_null_if_empty(student_data.get('address')),
            _to_null_if_empty(student_data.get('major')),
            _to_null_if_empty(student_data.get('admin_class')),
            _to_null_if_empty(student_data.get('virtual_class')),
            _to_null_if_empty(student_data.get('parent_name')),
            _to_null_if_empty(student_data.get('parent_id_card')),
            _to_null_if_empty(student_data.get('parent_phone')),
            _to_null_if_empty(student_data.get('student_phone')),
            _to_null_if_empty(student_data.get('grade')),
            student_data.get('school'),
            student_data.get('registration_status', '正式学籍'),
            student_data.get('attendance_status', '在校'),
            _to_null_if_empty(student_data.get('issue_authority')),
            _to_null_if_empty(student_data.get('valid_start')),
            _to_null_if_empty(student_data.get('valid_end')),
            _to_null_if_empty(student_data.get('major_direction')),
            _to_null_if_empty(student_data.get('household_type')),
            _to_null_if_empty(student_data.get('junior_school')),
            _to_null_if_empty(student_data.get('admission_type')),
            _to_null_if_empty(student_data.get('junior_graduation_time')),
            _to_null_if_empty(student_data.get('change_info')),
            _to_null_if_empty(student_data.get('remark')),
            student_data.get('enrollment_year', 0),
            student_data.get('name_pinyin', ''),
            student_data.get('district_province_name', ''),
            student_data.get('district_province_code', ''),
            student_data.get('district_city_name', ''),
            student_data.get('district_city_code', ''),
            student_data.get('district_county_name', ''),
            student_data.get('district_county_code', ''),
            student_data.get('district_match_status', '')
        ))
        return cursor.lastrowid
    except ValueError:
        raise
    except sqlite3.IntegrityError as e:
        error_msg = str(e)
        if 'student_no' in error_msg:
            raise ValueError(f'学号 {student_no} 已存在')
        elif 'national_no' in error_msg:
            raise ValueError(f'国网学籍号 {national_no} 已存在')
        elif 'id_card' in error_msg:
            raise ValueError(f'身份证号 {id_card} 已存在')
        else:
            raise ValueError('数据冲突，请检查输入')

def get_student_by_id(student_id):
    """获取学生byid。 参数: student_id"""
    conn = get_db()
    cursor = conn.cursor()
    cursor.execute('SELECT * FROM students WHERE id = ? AND is_deleted = 0', (student_id,))
    student = cursor.fetchone()
    conn.close()
    return dict(student) if student else None

def get_student_by_id_card(id_card):
    """获取学生byid身份证。 参数: id_card"""
    conn = get_db()
    cursor = conn.cursor()
    cursor.execute('SELECT * FROM students WHERE id_card = ? AND is_deleted = 0', (id_card,))
    student = cursor.fetchone()
    conn.close()
    return dict(student) if student else None

def get_student_by_student_no(student_no):
    """获取学生by学生编号。 参数: student_no"""
    conn = get_db()
    cursor = conn.cursor()
    cursor.execute('SELECT * FROM students WHERE student_no = ? AND is_deleted = 0', (student_no,))
    student = cursor.fetchone()
    conn.close()
    return dict(student) if student else None

def get_student_by_national_no(national_no):
    """获取学生by国网编号。 参数: national_no"""
    conn = get_db()
    cursor = conn.cursor()
    cursor.execute('SELECT * FROM students WHERE national_no = ? AND is_deleted = 0', (national_no,))
    student = cursor.fetchone()
    conn.close()
    return dict(student) if student else None

def get_public_roster():
    """获取公开班级花名册：按行政班级列出在册学生姓名与学籍状态。

    仅公开 姓名/学籍状态/行政班级 三个字段；排除软删除、毕业、退学办结及未分班学生。
    """
    conn = get_db()
    cursor = conn.cursor()
    cursor.execute(f'''
        SELECT s.name, s.registration_status, s.admin_class
        FROM students s
        WHERE s.is_deleted = 0
          AND {_exclude_graduated_clause('s')}
          AND (s.attendance_status IS NULL OR s.attendance_status != '退学办结')
          AND s.admin_class IS NOT NULL AND s.admin_class != ''
        ORDER BY s.admin_class, s.name
    ''')
    rows = [dict(r) for r in cursor.fetchall()]
    conn.close()

    classes_set = {}
    for r in rows:
        cls = r['admin_class']
        if cls not in classes_set:
            classes_set[cls] = {'name': cls, 'count': 0}
        classes_set[cls]['count'] += 1

    classes = sorted(classes_set.values(), key=lambda c: c['name'])
    return {'classes': classes, 'students': rows, 'total': len(rows)}

def update_student(student_id, student_data):
    """更新学生。 参数: student_id, student_data"""
    conn = get_db()
    cursor = conn.cursor()
    
    allowed_fields = ['student_no', 'national_no', 'id_card', 'name', 'gender', 'birth_date',
                      'ethnicity', 'address', 'major', 'admin_class', 'virtual_class',
                      'parent_name', 'parent_id_card', 'parent_phone', 'student_phone',
                      'grade', 'school', 'registration_status', 'attendance_status', 'photo_url',
                      'issue_authority', 'valid_start', 'valid_end', 'major_direction',
                      'household_type', 'junior_school', 'admission_type',
                      'junior_graduation_time', 'change_info', 'remark',
                      'name_pinyin']
    
    try:
        student_no = _to_null_if_empty(student_data.get('student_no'))
        national_no = _to_null_if_empty(student_data.get('national_no'))
        id_card = _to_null_if_empty(student_data.get('id_card'))
        
        if student_no:
            existing = get_student_by_student_no(student_no)
            if existing and existing.get('id') != student_id:
                raise ValueError(f'学号 {student_no} 已存在')
        
        if national_no:
            existing = get_student_by_national_no(national_no)
            if existing and existing.get('id') != student_id:
                raise ValueError(f'国网学籍号 {national_no} 已存在')
        
        if id_card:
            existing = get_student_by_id_card(id_card)
            if existing and existing.get('id') != student_id:
                raise ValueError(f'身份证号 {id_card} 已存在')

        # 区划联动：仅当身份证/地址发生变化时重算 7 列（失败留空，不阻断更新）
        try:
            current = get_student_by_id(student_id) or {}
            id_changed = ('id_card' in student_data and
                          _to_null_if_empty(student_data.get('id_card'))
                          != _to_null_if_empty(current.get('id_card')))
            addr_changed = ('address' in student_data and
                            _to_null_if_empty(student_data.get('address'))
                            != _to_null_if_empty(current.get('address')))
            if id_changed or addr_changed:
                source = {
                    'id_card': student_data.get('id_card', current.get('id_card')),
                    'address': student_data.get('address', current.get('address')),
                }
                fill_district_fields(source, conn)
                for key in DISTRICT_FIELDS:
                    student_data[key] = source.get(key, '')
                allowed_fields = allowed_fields + list(DISTRICT_FIELDS)
        except Exception as exc:
            logger.warning('区划联动失败，本次更新跳过区划列：%s', exc)

        updates = []
        values = []
        for field, value in student_data.items():
            if field in allowed_fields:
                updates.append(f'{field} = ?')
                if field in ['student_no', 'national_no', 'id_card', 'ethnicity', 'address', 
                             'major', 'admin_class', 'virtual_class', 'parent_name', 
                             'parent_id_card', 'parent_phone', 'student_phone', 'grade', 'photo_url',
                             'issue_authority', 'valid_start', 'valid_end', 'major_direction',
                             'household_type', 'junior_school', 'admission_type',
                             'junior_graduation_time', 'change_info', 'remark']:
                    values.append(_to_null_if_empty(value))
                else:
                    values.append(value)
        
        if updates:
            values.append(student_id)
            cursor.execute(f'UPDATE students SET {", ".join(updates)}, updated_at = CURRENT_TIMESTAMP WHERE id = ?', values)
            conn.commit()
            update_sync_timestamp()
    except ValueError:
        raise
    except sqlite3.IntegrityError as e:
        conn.rollback()
        error_msg = str(e)
        if 'student_no' in error_msg:
            raise ValueError(f'学号 {student_no} 已存在')
        elif 'national_no' in error_msg:
            raise ValueError(f'国网学籍号 {national_no} 已存在')
        elif 'id_card' in error_msg:
            raise ValueError(f'身份证号 {id_card} 已存在')
        else:
            raise ValueError('数据冲突，请检查输入')
    finally:
        conn.close()

def soft_delete_student(student_id, updated_by='system'):
    """软删除学生（标记 is_deleted=1，不实际删除）。 参数: student_id, updated_by"""
    conn = get_db()
    cursor = conn.cursor()
    # 先获取学生的 id_card 用于日志记录
    cursor.execute('SELECT id_card, is_deleted FROM students WHERE id = ?', (student_id,))
    row = cursor.fetchone()
    if not row:
        conn.close()
        return
    id_card = row['id_card']
    old_is_deleted = str(row['is_deleted'])

    cursor.execute('UPDATE students SET is_deleted = 1, updated_at = CURRENT_TIMESTAMP WHERE id = ?', (student_id,))
    conn.commit()

    # 写入 update_logs，使退学列表的 process_date（处理日期）可查询
    cursor.execute('''
        INSERT INTO update_logs (student_id, id_card, field_name, old_value, new_value, updated_by, created_at)
        VALUES (?, ?, 'is_deleted', ?, '1', ?, datetime('now', 'localtime'))
    ''', (student_id, id_card, old_is_deleted, updated_by))
    conn.commit()
    update_sync_timestamp()
    conn.close()

def clear_all_students():
    """clear全部学生。"""
    conn = get_db()
    cursor = conn.cursor()
    cursor.execute('SELECT COUNT(*) FROM students')
    count = cursor.fetchone()[0]
    cursor.execute('DELETE FROM students')
    conn.commit()
    update_sync_timestamp()
    conn.close()
    return count

def get_deleted_students():
    """获取已删除学生。"""
    conn = get_db()
    cursor = conn.cursor()
    cursor.execute('''
        SELECT s.*
        FROM students s
        WHERE s.is_deleted = 1
        ORDER BY s.updated_at DESC
    ''')
    rows = [dict(r) for r in cursor.fetchall()]
    conn.close()
    school_names = get_school_type_map()
    for r in rows:
        r['school_name'] = school_names.get(r.get('school'), '')
    return rows

def get_deleted_student_count():
    """获取已删除学生数量。"""
    conn = get_db()
    cursor = conn.cursor()
    cursor.execute('SELECT COUNT(*) FROM students WHERE is_deleted = 1')
    count = cursor.fetchone()[0]
    conn.close()
    return count

def restore_student(student_id):
    """恢复学生（从回收站还原）。 参数: student_id"""
    conn = get_db()
    cursor = conn.cursor()
    cursor.execute('UPDATE students SET is_deleted = 0, updated_at = CURRENT_TIMESTAMP WHERE id = ? AND is_deleted = 1', (student_id,))
    affected = cursor.rowcount
    conn.commit()
    update_sync_timestamp()
    conn.close()
    return affected

def permanently_delete_student(student_id):
    """永久删除学生（物理删除，不可恢复）。 参数: student_id"""
    conn = get_db()
    cursor = conn.cursor()
    cursor.execute("DELETE FROM attendance_records WHERE id_card = (SELECT id_card FROM students WHERE id = ?)", (student_id,))
    cursor.execute('DELETE FROM students WHERE id = ? AND is_deleted = 1', (student_id,))
    affected = cursor.rowcount
    conn.commit()
    update_sync_timestamp()
    conn.close()
    return affected

def restore_all_deleted_students():
    """恢复全部「注册前退学」已删除学生（非正式学籍，从回收站还原）。"""
    conn = get_db()
    cursor = conn.cursor()
    cursor.execute('''
        UPDATE students SET is_deleted = 0, updated_at = CURRENT_TIMESTAMP
        WHERE is_deleted = 1
          AND (registration_status IS NULL OR registration_status != '正式学籍')
    ''')
    affected = cursor.rowcount
    conn.commit()
    update_sync_timestamp()
    conn.close()
    return affected

def permanently_delete_all_deleted_students():
    """永久删除全部「注册前退学」已删除学生（非正式学籍，物理删除，不可恢复）。"""
    conn = get_db()
    cursor = conn.cursor()
    cursor.execute('''
        DELETE FROM attendance_records
        WHERE id_card IN (
            SELECT id_card FROM students
            WHERE is_deleted = 1
              AND (registration_status IS NULL OR registration_status != '正式学籍')
        )
    ''')
    cursor.execute('''
        DELETE FROM students
        WHERE is_deleted = 1
          AND (registration_status IS NULL OR registration_status != '正式学籍')
    ''')
    affected = cursor.rowcount
    conn.commit()
    update_sync_timestamp()
    conn.close()
    return affected

def _build_student_where_clause(filters):
    """根据筛选条件构建 students 查询的 WHERE 子句列表与参数（表别名 's'）。
    返回 (where_clauses, params)；含基础条件 is_deleted=0 与排除毕业。"""
    where_clauses = ['s.is_deleted = 0', _exclude_graduated_clause('s')]
    params = []
    if filters:
        if filters.get('enrollment_year'):
            where_clauses.append('s.enrollment_year = ?')
            params.append(filters['enrollment_year'])
        
        if filters.get('school'):
            if isinstance(filters['school'], list):
                placeholders = ','.join(['?' for _ in filters['school']])
                where_clauses.append(f's.school IN ({placeholders})')
                params.extend(filters['school'])
            else:
                where_clauses.append('s.school = ?')
                params.append(filters['school'])
        
        if filters.get('grade'):
            if isinstance(filters['grade'], list):
                placeholders = ','.join(['?' for _ in filters['grade']])
                where_clauses.append(f's.grade IN ({placeholders})')
                params.extend(filters['grade'])
            else:
                where_clauses.append('s.grade = ?')
                params.append(filters['grade'])
        
        if filters.get('major'):
            if isinstance(filters['major'], list):
                placeholders = ','.join(['?' for _ in filters['major']])
                where_clauses.append(f's.major IN ({placeholders})')
                params.extend(filters['major'])
            else:
                where_clauses.append('s.major = ?')
                params.append(filters['major'])
        
        if filters.get('admin_class'):
            if isinstance(filters['admin_class'], list):
                placeholders = ','.join(['?' for _ in filters['admin_class']])
                where_clauses.append(f's.admin_class IN ({placeholders})')
                params.extend(filters['admin_class'])
            else:
                where_clauses.append('s.admin_class = ?')
                params.append(filters['admin_class'])
        
        if filters.get('virtual_class'):
            if isinstance(filters['virtual_class'], list):
                placeholders = ','.join(['?' for _ in filters['virtual_class']])
                where_clauses.append(f's.virtual_class IN ({placeholders})')
                params.extend(filters['virtual_class'])
            else:
                where_clauses.append('s.virtual_class = ?')
                params.append(filters['virtual_class'])
        
        if filters.get('registration_status'):
            where_clauses.append('s.registration_status = ?')
            params.append(filters['registration_status'])
        
        if filters.get('attendance_status'):
            where_clauses.append('s.attendance_status = ?')
            params.append(filters['attendance_status'])
        
        if filters.get('gender'):
            where_clauses.append('s.gender = ?')
            params.append(filters['gender'])

        if filters.get('ethnicity'):
            where_clauses.append('s.ethnicity = ?')
            params.append(filters['ethnicity'])

        if filters.get('search'):
            search_term = f'%{filters["search"]}%'
            where_clauses.append('(s.name LIKE ? OR s.student_no LIKE ? OR s.name_pinyin LIKE ? OR s.id_card LIKE ?)')
            params.extend([search_term, search_term, search_term, search_term])

        if filters.get('student_no'):
            where_clauses.append('s.student_no = ?')
            params.append(filters['student_no'])

        if filters.get('name'):
            name_val = filters['name'].replace('%', r'\%').replace('_', r'\_')
            if len(name_val) >= 2:
                where_clauses.append('s.name LIKE ?')
                params.append(f'%{name_val}%')

        if filters.get('id_card_partial'):
            search_term = f'%{filters["id_card_partial"]}'
            where_clauses.append('s.id_card LIKE ?')
            params.append(search_term)

        if filters.get('min_age') or filters.get('max_age'):
            today = datetime.today()
            if filters.get('min_age'):
                try:
                    min_age = int(filters['min_age'])
                    youngest = today.replace(year=today.year - min_age)
                    where_clauses.append('s.birth_date <= ?')
                    params.append(youngest.strftime('%Y-%m-%d'))
                except (ValueError, TypeError):
                    pass
            if filters.get('max_age'):
                try:
                    max_age = int(filters['max_age'])
                    oldest = today.replace(year=today.year - max_age - 1) + timedelta(days=1)
                    where_clauses.append('s.birth_date >= ?')
                    params.append(oldest.strftime('%Y-%m-%d'))
                except (ValueError, TypeError):
                    pass

        if filters.get('major_direction'):
            if isinstance(filters['major_direction'], list):
                placeholders = ','.join(['?' for _ in filters['major_direction']])
                where_clauses.append(f's.major_direction IN ({placeholders})')
                params.extend(filters['major_direction'])
            else:
                where_clauses.append('s.major_direction = ?')
                params.append(filters['major_direction'])

        if filters.get('household_type'):
            if isinstance(filters['household_type'], list):
                placeholders = ','.join(['?' for _ in filters['household_type']])
                where_clauses.append(f's.household_type IN ({placeholders})')
                params.extend(filters['household_type'])
            else:
                where_clauses.append('s.household_type = ?')
                params.append(filters['household_type'])

        if filters.get('admission_type'):
            if isinstance(filters['admission_type'], list):
                placeholders = ','.join(['?' for _ in filters['admission_type']])
                where_clauses.append(f's.admission_type IN ({placeholders})')
                params.extend(filters['admission_type'])
            else:
                where_clauses.append('s.admission_type = ?')
                params.append(filters['admission_type'])

        if filters.get('change_info'):
            where_clauses.append('s.change_info LIKE ?')
            params.append('%' + filters['change_info'] + '%')

        if filters.get('created_from'):
            where_clauses.append('s.created_at >= ?')
            params.append(filters['created_from'] + ' 00:00:00')

        if filters.get('created_to'):
            where_clauses.append('s.created_at <= ?')
            params.append(filters['created_to'] + ' 23:59:59')
    
    return where_clauses, params

def get_students(filters=None, page=1, per_page=20, sort_field='id', sort_order='DESC', attendance_date=None):
    """获取学生。 参数: filters, page, per_page, sort_field, sort_order, ..."""
    conn = get_db()
    cursor = conn.cursor()
    
    if attendance_date is None:
        attendance_date = datetime.now().strftime('%Y-%m-%d')
    
    where_clauses, params = _build_student_where_clause(filters)
    where_sql = ' AND '.join(where_clauses)
    
    count_query = f'SELECT COUNT(*) FROM students s WHERE {where_sql}'
    cursor.execute(count_query, params)
    total = cursor.fetchone()[0]
    
    sort_field = sort_field if sort_field in ['id', 'student_no', 'name', 'grade', 'major', 'admin_class'] else 'id'
    sort_order = sort_order.upper() if sort_order.upper() in ['ASC', 'DESC'] else 'DESC'
    
    offset = (page - 1) * per_page
    data_query = f'''
        SELECT s.*, ar.status as today_attendance
        FROM students s
        LEFT JOIN attendance_records ar ON s.id_card = ar.id_card AND ar.attendance_date = ?
        WHERE {where_sql}
        ORDER BY s.{sort_field} {sort_order}
        LIMIT ? OFFSET ?
    '''
    cursor.execute(data_query, [attendance_date] + params + [per_page, offset])
    students = cursor.fetchall()
    
    conn.close()
    
    students_with_age = []
    for student in students:
        student_dict = dict(student)
        birth_date = student_dict.get('birth_date')
        if not birth_date:
            # 兜底：从身份证号提取出生日期
            id_card = student_dict.get('id_card', '')
            if id_card and len(id_card) == 18:
                try:
                    birth_date = f'{id_card[6:10]}-{id_card[10:12]}-{id_card[12:14]}'
                except (IndexError, ValueError):
                    pass
        if birth_date:
            try:
                birth = datetime.strptime(birth_date, '%Y-%m-%d')
                today = datetime.today()
                age = today.year - birth.year - ((today.month, today.day) < (birth.month, birth.day))
                student_dict['age'] = age
            except (ValueError, TypeError):
                student_dict['age'] = None
        else:
            student_dict['age'] = None
        students_with_age.append(student_dict)
    
    return {
        'students': students_with_age,
        'total': total,
        'page': page,
        'per_page': per_page,
        'pages': (total + per_page - 1) // per_page
    }

def get_dropout_students(page=1, per_page=20, date_from=None, date_to=None, name=None):
    """获取退学学生。 参数: page, per_page, date_from, date_to, name"""
    conn = get_db()
    cursor = conn.cursor()

    where_clauses = ["s.attendance_status = '退学办结'"]
    params = []

    # 姓名模糊查询：同时匹配中文姓名与姓名拼音
    if name:
        where_clauses.append('(s.name LIKE ? OR s.name_pinyin LIKE ?)')
        params.extend([f'%{name}%', f'%{name}%'])

    # 退学表提交日期筛选：基于 update_logs 中 attendance_status→退学办结 的时间
    if date_from:
        where_clauses.append('''(SELECT ul.created_at FROM update_logs ul
             WHERE ul.student_id = s.id AND ul.field_name = 'attendance_status'
               AND ul.new_value = '退学办结'
             ORDER BY ul.created_at DESC LIMIT 1) >= ?''')
        params.append(date_from + ' 00:00:00')
    if date_to:
        where_clauses.append('''(SELECT ul.created_at FROM update_logs ul
             WHERE ul.student_id = s.id AND ul.field_name = 'attendance_status'
               AND ul.new_value = '退学办结'
             ORDER BY ul.created_at DESC LIMIT 1) <= ?''')
        params.append(date_to + ' 23:59:59')

    where_sql = ' AND '.join(where_clauses)

    count_query = f'SELECT COUNT(*) FROM students s WHERE {where_sql}'
    cursor.execute(count_query, params)
    total = cursor.fetchone()[0]

    offset = (page - 1) * per_page
    data_query = f'''
        SELECT s.*,
            (SELECT ul.created_at FROM update_logs ul
             WHERE ul.student_id = s.id AND ul.field_name = 'attendance_status'
               AND ul.new_value = '退学办结'
             ORDER BY ul.created_at DESC LIMIT 1) as dropout_date,
            (SELECT ul.created_at FROM update_logs ul
             WHERE ul.student_id = s.id AND ul.field_name = 'is_deleted'
               AND ul.new_value = '1'
             ORDER BY ul.created_at DESC LIMIT 1) as process_date
        FROM students s
        WHERE {where_sql}
        ORDER BY dropout_date DESC
        LIMIT ? OFFSET ?
    '''
    cursor.execute(data_query, params + [per_page, offset])
    students = [dict(row) for row in cursor.fetchall()]

    cursor.execute(f'SELECT COUNT(*) FROM students s WHERE {where_sql} AND s.is_deleted = 0', params)
    pending_total = cursor.fetchone()[0]
    processed_total = total - pending_total

    conn.close()

    return {
        'students': students,
        'total': total,
        'page': page,
        'per_page': per_page,
        'pages': max((total + per_page - 1) // per_page, 1),
        'pending_total': pending_total,
        'processed_total': processed_total,
        'date_from': date_from,
        'date_to': date_to,
        'name': name
    }

def get_dropout_students_all(date_from=None, date_to=None, name=None):
    """获取退学学生全部。 参数: date_from, date_to, name"""
    conn = get_db()
    cursor = conn.cursor()

    where_clauses = ["s.attendance_status = '退学办结'"]
    params = []

    # 姓名模糊查询：同时匹配中文姓名与姓名拼音
    if name:
        where_clauses.append('(s.name LIKE ? OR s.name_pinyin LIKE ?)')
        params.extend([f'%{name}%', f'%{name}%'])

    if date_from:
        where_clauses.append('''(SELECT ul.created_at FROM update_logs ul
             WHERE ul.student_id = s.id AND ul.field_name = 'attendance_status'
               AND ul.new_value = '退学办结'
             ORDER BY ul.created_at DESC LIMIT 1) >= ?''')
        params.append(date_from + ' 00:00:00')
    if date_to:
        where_clauses.append('''(SELECT ul.created_at FROM update_logs ul
             WHERE ul.student_id = s.id AND ul.field_name = 'attendance_status'
               AND ul.new_value = '退学办结'
             ORDER BY ul.created_at DESC LIMIT 1) <= ?''')
        params.append(date_to + ' 23:59:59')

    where_sql = ' AND '.join(where_clauses)

    query = f'''
        SELECT s.*,
            (SELECT ul.created_at FROM update_logs ul
             WHERE ul.student_id = s.id AND ul.field_name = 'attendance_status'
               AND ul.new_value = '退学办结'
             ORDER BY ul.created_at DESC LIMIT 1) as dropout_date,
            (SELECT ul.created_at FROM update_logs ul
             WHERE ul.student_id = s.id AND ul.field_name = 'is_deleted'
               AND ul.new_value = '1'
             ORDER BY ul.created_at DESC LIMIT 1) as process_date
        FROM students s
        WHERE {where_sql}
        ORDER BY dropout_date DESC
    '''
    cursor.execute(query, params)
    students = [dict(row) for row in cursor.fetchall()]
    conn.close()
    return students

def update_student_insurance(student_id, fields):
    """更新学生保险字段（登记/审批/待办共用）。
    与 update_student() 分离原因：
    - 保险字段不在 update_student 的 allowed_fields 白名单
    - 保险使用 insurance_update_date 而非 updated_at
    - 保险日期字段需要 normalize_date_string 而非 _to_null_if_empty
    参数: student_id, fields(dict)"""
    from utils import normalize_date_string

    INSURANCE_FIELDS = {
        'student_insurance_no', 'student_insurance_pay_date',
        'student_insurance_expiry', 'insurance_premium',
        'internship_insurance_pay_date', 'supervisor_flag',
        'declaration_date', 'tuition_status'
    }
    DATE_FIELDS = {'student_insurance_pay_date', 'student_insurance_expiry',
                   'internship_insurance_pay_date'}

    conn = get_db()
    try:
        set_parts = []
        params = []
        for key, val in fields.items():
            if key not in INSURANCE_FIELDS:
                continue
            if key in DATE_FIELDS and val:
                val = normalize_date_string(val)
            set_parts.append(f'{key} = ?')
            params.append(val)

        if not set_parts:
            return 0

        set_parts.append("insurance_update_date = date('now')")
        params.append(student_id)
        conn.execute(
            f'UPDATE students SET {", ".join(set_parts)} WHERE id = ?',
            params
        )
        conn.commit()
        update_sync_timestamp()
        return conn.total_changes
    finally:
        conn.close()


def batch_update_student_insurance(student_ids, fields):
    """批量更新学生保险字段（所有学生应用相同 fields）。
    参数: student_ids(list), fields(dict)"""
    from utils import normalize_date_string

    INSURANCE_FIELDS = {
        'student_insurance_no', 'student_insurance_pay_date',
        'student_insurance_expiry', 'insurance_premium',
        'internship_insurance_pay_date', 'supervisor_flag',
        'declaration_date', 'tuition_status'
    }
    DATE_FIELDS = {'student_insurance_pay_date', 'student_insurance_expiry',
                   'internship_insurance_pay_date'}

    if not student_ids or not fields:
        return 0

    conn = get_db()
    try:
        set_parts = []
        params = []
        for key, val in fields.items():
            if key not in INSURANCE_FIELDS:
                continue
            if key in DATE_FIELDS and val:
                val = normalize_date_string(val)
            set_parts.append(f'{key} = ?')
            params.append(val)

        if not set_parts:
            return 0

        set_parts.append("insurance_update_date = date('now')")
        placeholders = ','.join('?' * len(student_ids))
        params.extend(student_ids)
        conn.execute(
            f'UPDATE students SET {", ".join(set_parts)} WHERE id IN ({placeholders})',
            params
        )
        conn.commit()
        update_sync_timestamp()
        return conn.total_changes
    finally:
        conn.close()


def get_transfer_pending_students(page=1, per_page=10):
    """获取转学待处理学生。 参数: page, per_page"""
    conn = get_db()
    cursor = conn.cursor()

    where_clauses = [
        "s.admission_type = '转学生'",
        "s.registration_status = '未注册'",
        "s.is_deleted = 0",
        "s.attendance_status != '退学办结'"
    ]
    where_sql = ' AND '.join(where_clauses)

    cursor.execute(f'SELECT COUNT(*) FROM students s WHERE {where_sql}')
    total = cursor.fetchone()[0]

    offset = (page - 1) * per_page
    cursor.execute(f'''
        SELECT s.id, s.name, s.id_card, s.admin_class, s.grade, s.enrollment_year,
               s.admission_type, s.registration_status, s.student_no,
               s.major, s.school, s.created_at
        FROM students s
        WHERE {where_sql}
        ORDER BY s.id DESC
        LIMIT ? OFFSET ?
    ''', [per_page, offset])
    students = [dict(row) for row in cursor.fetchall()]
    conn.close()

    return {
        'students': students,
        'total': total,
        'page': page,
        'per_page': per_page,
        'pages': max((total + per_page - 1) // per_page, 1)
    }

