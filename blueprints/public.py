from flask import Blueprint, request, jsonify, render_template, session
import secrets
import time
import config
import models

bp = Blueprint('public', __name__)

_PUBLIC_RATE_LIMIT = {}

# 口令校验失败时返回的极简提示页（无动态内容，不涉及转义）
_ACCESS_DENIED_HTML = '''<!DOCTYPE html>
<html lang="zh-CN">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1.0">
<meta name="robots" content="noindex, nofollow">
<title>无访问权限</title>
<style>
body{margin:0;height:100vh;display:flex;align-items:center;justify-content:center;
background:#f8fafc;font-family:-apple-system,'PingFang SC','Microsoft YaHei',sans-serif;color:#1e293b;}
.box{text-align:center;padding:32px 24px;max-width:420px;}
.box .icon{font-size:44px;margin-bottom:12px;}
.box h1{font-size:18px;margin:0 0 8px;}
.box p{font-size:13px;color:#64748b;line-height:1.6;margin:0;}
</style>
</head>
<body>
<div class="box">
    <div class="icon">🔒</div>
    <h1>无访问权限</h1>
    <p>该页面需要完整访问链接。请联系管理员获取带访问口令的地址。</p>
</div>
</body>
</html>'''


def _check_public_rate_limit(ip, scope, max_requests=30, window_seconds=60):
    """简单的内存级 IP 频率限制，防止滥用。

    scope 用于区分不同公开页面，避免多页共用同一计数额度。
    """
    now = time.time()
    key = f'{scope}_{ip}'
    if key not in _PUBLIC_RATE_LIMIT:
        _PUBLIC_RATE_LIMIT[key] = {'count': 0, 'reset_at': now + window_seconds}
    entry = _PUBLIC_RATE_LIMIT[key]
    if now > entry['reset_at']:
        entry['count'] = 0
        entry['reset_at'] = now + window_seconds
    entry['count'] += 1
    # 条目过多时仅清理已过期的，避免误清其他页面的有效计数
    if len(_PUBLIC_RATE_LIMIT) > 200:
        for k in [k for k, v in _PUBLIC_RATE_LIMIT.items() if now > v['reset_at']]:
            _PUBLIC_RATE_LIMIT.pop(k, None)
    return entry['count'] <= max_requests


def _client_ip():
    """获取客户端 IP（反向代理场景优先取 X-Forwarded-For 首段）。"""
    return request.headers.get('X-Forwarded-For', request.remote_addr or 'unknown').split(',')[0].strip()


@bp.before_request
def _require_public_access():
    """公开页面口令校验：/public/* 与 /api/public/* 均需携带有效口令。

    通过条件（满足其一）：
      1) session 中已标记通过（首次用 ?k= 访问后写入）
      2) URL 参数 k 与配置口令一致
      3) 请求头 X-Public-Key 与配置口令一致
    """
    if session.get('public_access_ok'):
        return None
    supplied = request.args.get('k') or request.headers.get('X-Public-Key') or ''
    if supplied and secrets.compare_digest(supplied, config.PUBLIC_ACCESS_KEY):
        session['public_access_ok'] = True
        return None
    if request.path.startswith('/api/'):
        return jsonify({'error': '无访问权限，请使用完整链接访问'}), 403
    return _ACCESS_DENIED_HTML, 403


@bp.route('/public/pending-leave')
def public_pending_leave():
    """待处理销假。"""
    return render_template('public/pending_leave.html')


@bp.route('/api/public/pending-leave')
def api_public_pending_leave():
    """公开待处理销假相关接口。"""
    if not _check_public_rate_limit(_client_ip(), 'pending_leave'):
        return jsonify({'error': '请求过于频繁，请稍后重试'}), 429

    data = models.get_pending_leave_students()
    return jsonify(data)


@bp.route('/public/roster')
def public_roster():
    """公开班级花名册（按行政班级展示在册学生姓名与学籍状态）。"""
    return render_template('public/roster.html')


@bp.route('/api/public/roster')
def api_public_roster():
    """公开班级花名册接口。"""
    if not _check_public_rate_limit(_client_ip(), 'roster'):
        return jsonify({'error': '请求过于频繁，请稍后重试'}), 429

    data = models.get_public_roster()
    return jsonify(data)
