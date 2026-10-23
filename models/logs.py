import sqlite3
import logging
from datetime import datetime
from models.db import get_db

logger = logging.getLogger(__name__)


def log_update(student_id, id_card, field_name, old_value, new_value, updated_by, batch_id=None):
    """记录更新日志。 参数: student_id, id_card, field_name, old_value, new_value, ..."""
    conn = get_db()
    cursor = conn.cursor()
    cursor.execute('''
        INSERT INTO update_logs (student_id, id_card, field_name, old_value, new_value, updated_by, batch_id, created_at)
        VALUES (?, ?, ?, ?, ?, ?, ?, datetime('now', 'localtime'))
    ''', (student_id, id_card, field_name, old_value, new_value, updated_by, batch_id))
    conn.commit()
    conn.close()

def get_update_logs(student_id=None, batch_id=None, limit=100):
    """获取更新日志。 参数: student_id, batch_id, limit"""
    conn = get_db()
    cursor = conn.cursor()
    
    where_clauses = []
    params = []
    
    if student_id:
        where_clauses.append('student_id = ?')
        params.append(student_id)
    
    if batch_id:
        where_clauses.append('batch_id = ?')
        params.append(batch_id)
    
    where_sql = ' AND '.join(where_clauses) if where_clauses else '1=1'
    
    cursor.execute(f'SELECT * FROM update_logs WHERE {where_sql} ORDER BY created_at DESC LIMIT ?', params + [limit])
    logs = cursor.fetchall()
    
    conn.close()
    return [dict(log) for log in logs]

def batch_update_students(updates, operator_username):
    """批量更新学生。 参数: updates, operator_username"""
    import uuid
    batch_id = uuid.uuid4().hex[:12]
    
    conn = get_db()
    cursor = conn.cursor()
    
    results = {
        'success': 0,
        'no_change': 0,
        'not_found': 0,
        'errors': []
    }
    
    allowed_fields = ['registration_status', 'attendance_status', 'admin_class', 
                      'virtual_class', 'major', 'grade', 'student_phone', 
                      'parent_phone', 'address']
    
    try:
        conn.execute('BEGIN TRANSACTION')
        
        for id_card, field_updates in updates.items():
            cursor.execute('SELECT * FROM students WHERE id_card = ? AND is_deleted = 0', (id_card,))
            student = cursor.fetchone()
            
            if not student:
                results['not_found'] += 1
                continue
            
            student_dict = dict(student)
            actual_updates = {}
            
            for field, new_value in field_updates.items():
                if field not in allowed_fields:
                    continue
                
                if not new_value or str(new_value).strip() == '':
                    continue
                
                old_value = student_dict.get(field, '')
                if str(new_value) == str(old_value):
                    continue
                
                actual_updates[field] = (old_value, new_value)
            
            if not actual_updates:
                results['no_change'] += 1
                continue
            
            for field, (old_val, new_val) in actual_updates.items():
                cursor.execute(f'UPDATE students SET {field} = ?, updated_at = CURRENT_TIMESTAMP WHERE id = ?', 
                             (new_val, student_dict['id']))
                
                log_update(student_dict['id'], id_card, field, old_val, new_val, operator_username, batch_id)
            
            results['success'] += 1
        
        conn.commit()
    except Exception as e:
        conn.rollback()
        results['errors'].append(str(e))
    finally:
        conn.close()
    
    return results

def update_sync_timestamp():
    """更新同步时间戳。"""
    conn = get_db()
    cursor = conn.cursor()
    cursor.execute('''
        CREATE TABLE IF NOT EXISTS sync_timestamp (
            id INTEGER PRIMARY KEY CHECK (id = 1),
            updated_at TIMESTAMP DEFAULT (datetime('now', 'localtime'))
        )
    ''')
    cursor.execute('''
        INSERT INTO sync_timestamp (id, updated_at) VALUES (1, datetime('now', 'localtime'))
        ON CONFLICT(id) DO UPDATE SET updated_at = datetime('now', 'localtime')
    ''')
    conn.commit()
    conn.close()

def get_last_update_time():
    """获取最后更新时间（从 sync_timestamp 表读取，所有数据写入操作均会更新此时间戳）。"""
    conn = get_db()
    cursor = conn.cursor()
    cursor.execute('SELECT updated_at FROM sync_timestamp WHERE id = 1')
    row = cursor.fetchone()
    conn.close()
    if row and row[0]:
        return row[0]
    return '-'

