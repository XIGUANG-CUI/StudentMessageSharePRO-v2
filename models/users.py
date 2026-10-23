import sqlite3
import logging
from models.db import get_db

logger = logging.getLogger(__name__)


def create_user(username, password_hash, role='user', display_name=None, 
                school_visible='1,2', fields_visible='[]', grade_visible='*'):
    """创建用户。 参数: username, password_hash, role, display_name, school_visible, ..."""
    conn = get_db()
    cursor = conn.cursor()
    try:
        cursor.execute('''
            INSERT INTO users (username, password_hash, role, display_name, school_visible, fields_visible, grade_visible)
            VALUES (?, ?, ?, ?, ?, ?, ?)
        ''', (username, password_hash, role, display_name, school_visible, fields_visible, grade_visible))
        conn.commit()
        return cursor.lastrowid
    except sqlite3.IntegrityError:
        return None
    finally:
        conn.close()

def get_user_by_username(username):
    """获取用户byusername。 参数: username"""
    conn = get_db()
    cursor = conn.cursor()
    cursor.execute('SELECT * FROM users WHERE username = ?', (username,))
    user = cursor.fetchone()
    conn.close()
    return dict(user) if user else None

def get_user_by_id(user_id):
    """获取用户byid。 参数: user_id"""
    conn = get_db()
    cursor = conn.cursor()
    cursor.execute('SELECT * FROM users WHERE id = ?', (user_id,))
    user = cursor.fetchone()
    conn.close()
    return dict(user) if user else None

def get_all_users():
    """获取全部用户。"""
    conn = get_db()
    cursor = conn.cursor()
    cursor.execute('SELECT * FROM users ORDER BY id')
    users = cursor.fetchall()
    conn.close()
    return [dict(user) for user in users]

def update_user(user_id, **kwargs):
    """更新用户。 参数: user_id"""
    conn = get_db()
    cursor = conn.cursor()
    
    allowed_fields = ['password_hash', 'role', 'display_name', 'school_visible', 
                      'fields_visible', 'grade_visible', 'is_active']
    
    updates = []
    values = []
    for field, value in kwargs.items():
        if field in allowed_fields:
            updates.append(f'{field} = ?')
            values.append(value)
    
    if updates:
        values.append(user_id)
        cursor.execute(f'UPDATE users SET {", ".join(updates)} WHERE id = ?', values)
        conn.commit()
    
    conn.close()

def toggle_user_status(user_id):
    """toggle用户状态。 参数: user_id"""
    conn = get_db()
    cursor = conn.cursor()
    cursor.execute('UPDATE users SET is_active = NOT is_active WHERE id = ?', (user_id,))
    conn.commit()
    conn.close()

