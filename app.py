from flask import Flask, render_template, request, redirect, url_for, flash, session, jsonify, make_response, send_file, g
from werkzeug.utils import secure_filename
import os
import secrets
import logging
import models
import auth
from auth import _require_admin_json, _require_login_json, _get_current_enrollment_year  # P1-1: 辅助函数公共化
import utils
import config
from config import Config
import pinyin_utils
from openpyxl import Workbook, load_workbook
from openpyxl.styles import Font, PatternFill, Alignment
from datetime import datetime
from io import BytesIO
import time
import zipfile
import base64
import re

logger = logging.getLogger(__name__)

app = Flask(__name__)
app.config.from_object(Config)
app.config['SEND_FILE_MAX_AGE_DEFAULT'] = 0

# P0-NEW-2: 删除死代码——Config.SECRET_KEY 已持久化设置（config.py:81），
# app.secret_key 永远非空，原 `if not app.secret_key: app.secret_key = secrets.token_hex(16)` 永不执行。

os.makedirs(app.config['UPLOAD_FOLDER'], exist_ok=True)
os.makedirs(app.config['LOG_FOLDER'], exist_ok=True)
os.makedirs(config.PHOTOS_DIR, exist_ok=True)

models.init_db()

# 注册 Blueprint（P1-1：路由模块化拆分）
from blueprints.mobile import bp as mobile_bp
from blueprints.public import bp as public_bp
from blueprints.backup import bp as backup_bp
app.register_blueprint(mobile_bp)
app.register_blueprint(public_bp)
app.register_blueprint(backup_bp)
from blueprints.export import bp as export_bp
app.register_blueprint(export_bp)
from blueprints.insurance import bp as insurance_bp
app.register_blueprint(insurance_bp)
from blueprints.user import bp as user_bp
app.register_blueprint(user_bp)
from blueprints.admin import bp as admin_bp
app.register_blueprint(admin_bp)
from blueprints.leave_cancel import bp as leave_cancel_bp
from blueprints.lifecycle import bp as lifecycle_bp
app.register_blueprint(leave_cancel_bp)
app.register_blueprint(lifecycle_bp)
from blueprints.api import bp as api_bp
app.register_blueprint(api_bp)

# ========== 安全防护 ==========

# --- CSRF 令牌初始化 ---
@app.before_request
def _ensure_csrf_token():
    """确保 Session 中始终存在 CSRF Token"""
    if 'csrf_token' not in session:
        session['csrf_token'] = secrets.token_hex(32)


# --- CSRF 校验 ---
@app.before_request
def _csrf_protect():
    """校验 POST/PUT/DELETE/PATCH 请求的 CSRF Token"""
    if request.method in ('POST', 'PUT', 'DELETE', 'PATCH'):
        exempt_paths = ('/login', '/mobile/login')
        if request.path in exempt_paths:
            return None
        req_token = request.form.get('csrf_token') or request.headers.get('X-CSRF-Token')
        token = session.get('csrf_token', '')
        if not req_token or not secrets.compare_digest(token, req_token):
            if request.path.startswith('/api/') or request.path.startswith('/admin/'):
                return jsonify({'success': False, 'message': '安全校验失败，请刷新页面后重试'}), 403
            flash('安全校验失败，请刷新页面后重试', 'danger')
            return redirect(request.url)


@app.context_processor
def _inject_csrf_token():
    """context_processor：注入 CSRF Token 到模板。"""
    return {'csrf_token': session.get('csrf_token', '')}


@app.context_processor
def _inject_school_filter_url():
    """为学生列表模板提供学校类别筛选按钮 URL 辅助函数"""
    from urllib.parse import urlencode

    def school_filter_url(school_value):
        """模板辅助函数：生成学校筛选 URL。 参数: school_value"""
        args = request.args.copy()
        args['school'] = str(school_value)
        return url_for('admin.admin_students') + '?' + urlencode(args)

    def clear_school_url():
        """模板辅助函数：生成清除学校筛选的 URL。"""
        args = request.args.copy()
        args.pop('school', None)
        return url_for('admin.admin_students') + '?' + urlencode(args)

    return {'school_filter_url': school_filter_url, 'clear_school_url': clear_school_url}


@app.context_processor
def _inject_card_reader_url():
    """注入读卡器 WebSocket 地址到前端"""
    return {'card_reader_ws_url': utils._get_card_reader_ws_url()}


@app.context_processor
def _inject_field_help():
    """为导入/导出页面提供字段帮助信息，含字段级权限过滤数据"""
    user_role = session.get('role', '')
    visible_fields = auth.get_visible_fields()
    info = {
        'import_fields': list(config.ALL_STUDENT_FIELDS),
        'import_count': len(config.ALL_STUDENT_FIELDS),
        'required_fields': ['学生姓名', '学生身份证号', '学号'],
        'auto_fields': ['年龄', '姓名拼音'],
        'is_admin': user_role == 'admin',
        'insurance_fields': ['学平险保单号', '学平险缴费日期', '学平险到期时间',
                             '学平险保费金额', '实习险缴费日期', '主管标记',
                             '申报日期', '保单更新日期', '学费总体情况'],
        'visible_fields': visible_fields,
        'visible_fields_set': None if visible_fields == ['*'] else set(visible_fields),
        'field_mapping_keys': set(config.FIELD_MAPPING.keys()),
    }
    return {'field_help': info}


# --- 安全响应头 ---
@app.after_request
def _add_security_headers(response):
    """after_request 钩子：添加安全响应头（X-Content-Type-Options/CSP 等）。 参数: response"""
    response.headers['X-Content-Type-Options'] = 'nosniff'
    response.headers['X-Frame-Options'] = 'SAMEORIGIN'
    response.headers['Referrer-Policy'] = 'strict-origin-when-cross-origin'
    # CSP: 允许本站资源 + Bootstrap CDN + 任意 WebSocket（读卡器）
    csp = (
        "default-src 'self'; "
        "script-src 'self' 'unsafe-inline' https://cdn.jsdelivr.net; "
        "style-src 'self' 'unsafe-inline' https://cdn.jsdelivr.net; "
        "font-src 'self' https://cdn.jsdelivr.net; "
        "img-src 'self' data: blob:; "
        "connect-src 'self' ws:;"
        "frame-ancestors 'self'; "
        "base-uri 'self'"
    )
    response.headers['Content-Security-Policy'] = csp
    return response

# ========== 安全防护结束 ==========


# --- 请求结束：自动关闭数据库连接 ---
@app.teardown_appcontext
def _close_db_connection(exception=None):
    """teardown_appcontext 钩子：请求结束时关闭数据库连接。 参数: exception"""
    conn = g.pop('_db_conn', None)
    if conn is not None:
        try:
            conn._real_close()
        except Exception as e:
            logger.debug("关闭数据库连接失败（已忽略）: %s", e, exc_info=True)


_backup_service = None


def _get_backup_service():
    """获取备份服务实例（单例，延迟初始化）。"""
    global _backup_service
    if _backup_service is None:
        from services.backup_service import BackupService
        _backup_service = BackupService(
            config.DATABASE_PATH,
            backup_dir=os.path.join(config._USER_DATA_DIR, 'databackup')
        )
    return _backup_service


def _init_backup_on_startup():
    """启动时执行首次备份并启动定时备份。"""
    if not config.BACKUP_ENABLED or not config.BACKUP_ON_STARTUP:
        return
    try:
        bs = _get_backup_service()
        bs.silent_backup()
        if config.BACKUP_AUTO_ENABLED:
            bs.start_auto_backup(config.BACKUP_AUTO_INTERVAL, immediate=False)
    except Exception as e:
        import traceback
        print(f"[启动备份] 失败: {e}")
        traceback.print_exc()


_init_backup_on_startup()

def init_admin_user():
    """首次启动时创建 admin 用户（随机密码）。"""
    admin = models.get_user_by_username('admin')
    if not admin:
        password = utils.generate_random_password()
        password_hash = auth.hash_password(password)
        models.create_user(
            username='admin',
            password_hash=password_hash,
            role='admin',
            display_name='系统管理员',
            school_visible='1,2',
            fields_visible='["*"]',
            grade_visible='*'
        )
        print('=' * 60)
        print('初始管理员账号已创建')
        print(f'用户名: admin')
        print(f'密码: {password}')
        print('=' * 60)
        return password
    else:
        print('管理员账号已存在')
        return None

_admin_initial_password = init_admin_user()

@app.context_processor
def inject_last_update():
    """context_processor：注入最后更新时间到模板。"""
    try:
        last_update = models.get_last_update_time()
        if last_update and last_update != '-':
            return {'last_update_time': last_update[:16]}
    except Exception as e:
        logger.debug("获取最后更新时间失败，降级显示暂无: %s", e, exc_info=True)
    return {'last_update_time': '暂无'}

@app.context_processor
def inject_css_version():
    """context_processor：注入 CSS 版本号（用于浏览器缓存刷新）。"""
    try:
        css_path = os.path.join(os.path.dirname(__file__), 'static', 'css', 'style.css')
        css_mtime = int(os.path.getmtime(css_path))
    except Exception:
        css_mtime = int(time.time())
    return {'css_version': css_mtime}

@app.context_processor
def inject_current_enrollment_year():
    """context_processor：注入当前入学年份和年份列表到模板。"""
    year = session.get('enrollment_year')
    is_admin = session.get('role') == 'admin'
    current_year = datetime.now().year
    show_year_modal = False
    if request.args.get('select_year') == '1':
        show_year_modal = True
    try:
        if is_admin:
            models.sync_enrollment_years_to_dicts()
        years = models.get_enrollment_years()
    except Exception:
        years = []
    return {
        'current_enrollment_year': int(year) if year else None,
        'enrollment_year_label': f'{year}年' if year else '全部年份',
        'existing_years': years,
        'show_year_modal': show_year_modal,
        'is_student_list_page': request.endpoint == 'admin.admin_students'
    }

@app.route('/')
def index():
    """首页路由：根据用户角色重定向到对应 dashboard。"""
    if 'user_id' in session:
        role = session.get('role')
        if role == 'admin':
            return redirect(url_for('admin.admin_dashboard'))
        elif role == 'attendance':
            return redirect(url_for('mobile.mobile_grades'))
        elif role in ('finance_chief', 'finance_staff'):
            return redirect(url_for('insurance.insurance_register'))
        elif role == 'insurance_officer':
            return redirect(url_for('insurance.insurance_todo'))
        else:
            return redirect(url_for('user.user_query'))
    return redirect(url_for('login'))

# ========== 登录限流（暴力破解防护）==========
import time as _time

_login_attempts = {}  # {username: [timestamp, ...]}
_LOGIN_MAX_ATTEMPTS = 5
_LOGIN_WINDOW_SECONDS = 60
_LOGIN_ATTEMPTS_INACTIVE_SECONDS = 3600  # 用户名键 1 小时不活跃后清理
_LOGIN_ATTEMPTS_CLEANUP_COOLDOWN = 300   # 全局清理间隔 5 分钟
_last_cleanup_time = 0


def _cleanup_login_attempts(now=None):
    """清理过期的登录尝试记录。
    1. 移除所有超过 60 秒的旧时间戳
    2. 移除超过 1 小时未活跃的用户名键
    3. 删除列表为空的键
    """
    global _last_cleanup_time
    if now is None:
        now = _time.time()
    to_delete = []
    for username, timestamps in list(_login_attempts.items()):
        # 清理超过 60 秒的旧记录
        valid = [t for t in timestamps if now - t < _LOGIN_WINDOW_SECONDS]
        if not valid:
            # 列表为空 → 检查是否超过 1 小时未活跃
            if timestamps and now - max(timestamps) > _LOGIN_ATTEMPTS_INACTIVE_SECONDS:
                to_delete.append(username)
            elif not timestamps:
                to_delete.append(username)
            else:
                _login_attempts[username] = []
        else:
            _login_attempts[username] = valid
    for username in to_delete:
        _login_attempts.pop(username, None)
    _last_cleanup_time = now


@app.route('/login', methods=['GET', 'POST'])
def login():
    """登录路由：处理用户登录（含速率限制防暴力破解）。"""
    if request.method == 'POST':
        username = request.form.get('username', '').strip()
        password = request.form.get('password', '')

        # 定期清理所有用户名的过期记录
        now = _time.time()
        if now - _last_cleanup_time > _LOGIN_ATTEMPTS_CLEANUP_COOLDOWN:
            _cleanup_login_attempts(now)

        # 清理当前用户名的过期记录（确保未命中全局清理时也能清理自身）
        attempts = _login_attempts.get(username, [])
        attempts[:] = [t for t in attempts if now - t < _LOGIN_WINDOW_SECONDS]
        _login_attempts[username] = attempts
        if not attempts:
            _login_attempts.pop(username, None)

        # 速率限制检查
        if len(attempts) >= _LOGIN_MAX_ATTEMPTS:
            remaining = int(_LOGIN_WINDOW_SECONDS - (now - attempts[0]))
            flash(f'登录尝试过多，请 {remaining} 秒后重试', 'danger')
            return render_template('login.html')

        user = auth.authenticate_user(username, password)
        if user:
            _login_attempts.pop(username, None)  # 登录成功清除记录
            auth.create_session(user)
            flash('登录成功', 'success')
            if user['role'] == 'admin':
                return redirect(url_for('admin.admin_dashboard'))
            elif user['role'] == 'attendance':
                return redirect(url_for('mobile.mobile_grades'))
            elif user['role'] in ('finance_chief', 'finance_staff'):
                return redirect(url_for('insurance.insurance_register'))
            elif user['role'] == 'insurance_officer':
                return redirect(url_for('insurance.insurance_todo'))
            else:
                return redirect(url_for('user.user_query'))
        else:
            attempts.append(now)  # 登录失败记录
            _login_attempts[username] = attempts
            remaining = _LOGIN_MAX_ATTEMPTS - len(attempts)
            flash(f'用户名或密码错误，还剩 {remaining} 次尝试', 'danger')

    return render_template('login.html')

@app.route('/logout')
def logout():
    """登出路由：清除 session 并重定向到登录页。"""
    role = session.get('role', '')
    auth.clear_session()
    flash('已退出登录', 'info')
    if role == 'attendance':
        return redirect(url_for('mobile.mobile_login'))
    return redirect(url_for('login'))

# _get_current_enrollment_year 已移至 auth.py

@app.errorhandler(Exception)
def handle_api_exception(e):
    """全局异常处理：记录日志并返回友好错误信息（不泄露堆栈）。 参数: e"""
    from werkzeug.exceptions import HTTPException
    import logging
    if isinstance(e, HTTPException):
        return e
    # 记录完整错误到日志，但不泄露给客户端
    app.logger.error(f'Unhandled exception on {request.path}: {e}', exc_info=True)
    if request.path.startswith('/admin/') or request.path.startswith('/api/'):
        return jsonify({'success': False, 'message': '服务器内部错误，请稍后重试'}), 500
    raise e





# ============================================================
# 模板驱动数据导出引擎
# ============================================================

if __name__ == '__main__':
    utils.cleanup_old_files(app.config['UPLOAD_FOLDER'])
    utils.cleanup_old_files(config.EXPORT_OUTPUT_FOLDER, max_age_hours=1)
    app.run(host='0.0.0.0', port=config.PORT, debug=False)