from flask import Blueprint, request, jsonify
from auth import _require_admin_json

bp = Blueprint('backup', __name__)


def _get_bs():
    """延迟导入 _get_backup_service，避免与 app.py 的模块级循环导入。"""
    from app import _get_backup_service
    return _get_backup_service()


@bp.route('/api/backup/status')
def api_backup_status():
    """备份状态相关接口。"""
    auth_error = _require_admin_json()
    if auth_error:
        return auth_error
    bs = _get_bs()
    return jsonify(bs.get_backup_status())


@bp.route('/api/backup/trigger', methods=['POST'])
def api_backup_trigger():
    """备份触发相关接口。"""
    auth_error = _require_admin_json()
    if auth_error:
        return auth_error
    bs = _get_bs()
    success = bs.silent_backup()
    return jsonify(bs.get_backup_status())


@bp.route('/api/backup/countdown')
def api_backup_countdown():
    """备份倒计时相关接口。"""
    auth_error = _require_admin_json()
    if auth_error:
        return auth_error
    bs = _get_bs()
    return jsonify(bs.get_countdown_info())


@bp.route('/api/backup/interval', methods=['GET', 'POST'])
def api_backup_interval():
    """备份间隔相关接口。"""
    auth_error = _require_admin_json()
    if auth_error:
        return auth_error
    bs = _get_bs()
    if request.method == 'POST':
        data = request.get_json(silent=True) or {}
        seconds = data.get('interval_seconds', 0)
        if not seconds:
            return jsonify({'success': False, 'message': '请提供有效的间隔秒数'}), 400
        result = bs.set_interval(seconds)
        return jsonify({'success': True, **result})
    return jsonify(bs.get_interval())
