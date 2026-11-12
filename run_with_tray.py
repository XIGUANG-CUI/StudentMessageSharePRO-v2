import os
import sys
import ctypes
import threading
import logging
import logging.handlers
import tkinter as tk
from tkinter import messagebox

import webbrowser
import time
import subprocess
import signal

from pystray import Icon, Menu, MenuItem as item
from PIL import Image, ImageDraw
from flask import request

logger = logging.getLogger(__name__)

# ====================== 数据目录分离逻辑 ======================
# 检测是否为 PyInstaller 打包环境
_is_frozen = getattr(sys, 'frozen', False)

if _is_frozen:
    # 打包环境：EXE 同级的 user_data 文件夹作为数据目录
    _exe_dir = os.path.dirname(os.path.abspath(sys.executable))
    _user_data_dir = os.path.join(_exe_dir, 'user_data')
    os.environ['USER_DATA_DIR'] = _user_data_dir
    os.makedirs(_user_data_dir, exist_ok=True)
    # 确保 user_data 下的子目录存在
    os.makedirs(os.path.join(_user_data_dir, 'data'), exist_ok=True)
    os.makedirs(os.path.join(_user_data_dir, 'static', 'photos'), exist_ok=True)
    os.makedirs(os.path.join(_user_data_dir, 'databackup'), exist_ok=True)
    os.makedirs(os.path.join(_user_data_dir, 'uploads'), exist_ok=True)
    os.makedirs(os.path.join(_user_data_dir, 'logs'), exist_ok=True)
    BASE_DIR = _user_data_dir
else:
    # 开发环境：使用程序所在目录
    BASE_DIR = os.path.abspath(os.path.dirname(__file__))

sys.path.insert(0, BASE_DIR)

# ====================== 日志配置 ======================
def setup_logging():
    """初始化日志：写入 <数据目录>/logs/app.log，按 5MB 轮转保留 5 份。
    必须在导入 config/app 之前调用，确保启动早期异常也能落盘。"""
    log_dir = os.path.join(BASE_DIR, 'logs')
    try:
        os.makedirs(log_dir, exist_ok=True)
    except OSError as e:
        print(f"[日志] 创建日志目录失败: {e}")
        return

    root = logging.getLogger()
    if root.handlers:
        return
    root.setLevel(logging.INFO)
    formatter = logging.Formatter('%(asctime)s [%(levelname)s] %(name)s: %(message)s')

    try:
        file_handler = logging.handlers.RotatingFileHandler(
            os.path.join(log_dir, 'app.log'),
            maxBytes=5 * 1024 * 1024,
            backupCount=5,
            encoding='utf-8',
        )
        file_handler.setFormatter(formatter)
        root.addHandler(file_handler)
    except OSError as e:
        print(f"[日志] 无法创建日志文件: {e}")

    # 有控制台时同步输出一份便于开发调试；打包窗口模式下 stderr 为 None，跳过
    if sys.stderr is not None:
        console_handler = logging.StreamHandler()
        console_handler.setFormatter(formatter)
        root.addHandler(console_handler)

    # 静态资源/逐请求访问日志噪声过大，只保留告警及以上
    logging.getLogger('werkzeug').setLevel(logging.WARNING)


setup_logging()

# ====================== 单实例锁（Windows 命名互斥体） ======================
def check_already_running():
    """通过 Windows 命名互斥体检测是否已有实例在运行。返回 True 表示已在运行。"""
    mutex_name = r'Global\StudentMessageSharePRO_SingleInstance'
    ctypes.windll.kernel32.CreateMutexW(None, False, mutex_name)
    error = ctypes.windll.kernel32.GetLastError()
    # ERROR_ALREADY_EXISTS = 183
    return error == 183


def show_already_running_message():
    """弹出提示窗口告知用户程序已在运行中"""
    try:
        root = tk.Tk()
        root.withdraw()
        messagebox.showinfo(
            '学生数据管理系统',
            '应用程序已在运行中，请勿重复启动。\n\n可在系统托盘中找到程序图标。'
        )
        root.destroy()
    except Exception as e:
        logger.debug("显示已在运行弹窗失败: %s", e, exc_info=True)


if check_already_running():
    show_already_running_message()
    sys.exit(0)

from config import PORT

stop_event = threading.Event()
flask_server = None
flask_thread = None
tray_icon = None
is_shutting_down = False
_admin_first_password = None

from utils import get_lan_ip


def run_flask():
    """Flask 服务线程主函数：启动 Werkzeug 服务器。"""
    global flask_server

    os.makedirs(os.path.join(BASE_DIR, 'data'), exist_ok=True)
    os.makedirs(os.path.join(BASE_DIR, 'uploads'), exist_ok=True)
    os.makedirs(os.path.join(BASE_DIR, 'static', 'photos'), exist_ok=True)
    os.makedirs(os.path.join(BASE_DIR, 'databackup'), exist_ok=True)

    from app import app
    import app as _app_module
    global _admin_first_password
    _admin_first_password = getattr(_app_module, '_admin_initial_password', None)

    local_ip = get_lan_ip()
    print("=" * 60)
    print("学生数据管理系统")
    print("=" * 60)
    print("系统正在启动...")
    print(f"本地访问地址: http://127.0.0.1:{PORT}")
    print(f"局域网访问地址: http://{local_ip}:{PORT}/login")
    print(f"数据库路径: {os.path.join(BASE_DIR, 'data', 'student.db')}")
    # P0-NEW-2: 启动时输出安全配置状态，便于运维发现 SESSION_COOKIE_SECURE=False 的风险
    from config import Config as _Cfg
    _cookie_secure = 'True（HTTPS 安全）' if _Cfg.SESSION_COOKIE_SECURE else 'False（HTTP，如启用 HTTPS 请设环境变量 SESSION_COOKIE_SECURE=1）'
    print(f"Session Cookie 安全: {_cookie_secure}")
    print("=" * 60)

    @app.route('/shutdown', methods=['POST'])
    def shutdown():
        """/shutdown 路由：触发 Werkzeug 服务器关闭。"""
        func = request.environ.get('werkzeug.server.shutdown')
        if func:
            func()
        return "Server shutting down..."

    from werkzeug.serving import make_server
    flask_server = make_server('0.0.0.0', PORT, app, threaded=True)

    try:
        while not stop_event.is_set():
            flask_server.handle_request()
            time.sleep(0.1)
    except Exception as e:
        if not is_shutting_down:
            print(f"Flask服务器异常: {e}")
    finally:
        if flask_server:
            try:
                flask_server.server_close()
                print("Flask服务器已关闭")
            except Exception as e:
                print(f"关闭Flask服务器时出错: {e}")


def stop_flask():
    """停止 Flask 服务（先请求 /shutdown，失败则直接关闭 server）。"""
    global flask_server, flask_thread, is_shutting_down

    is_shutting_down = True
    stop_event.set()

    if flask_server:
        try:
            import requests
            requests.post(f'http://127.0.0.1:{PORT}/shutdown', timeout=1)
        except Exception as e:
            logger.debug("请求 /shutdown 端点失败，尝试直接关闭 server: %s", e, exc_info=True)
            try:
                flask_server.server_close()
            except Exception as e2:
                logger.debug("server_close 失败（已忽略）: %s", e2, exc_info=True)

    if flask_thread and flask_thread.is_alive():
        flask_thread.join(timeout=2)
        if flask_thread.is_alive():
            print("警告: Flask线程未正常结束")


def create_tray_icon_image():
    """加载托盘图标，优先使用 favicon.ico，找不到时回退到动态绘制。"""
    # 按环境构建候选路径
    possible_paths = []
    if getattr(sys, 'frozen', False):
        # PyInstaller onedir: _internal/static/favicon.ico
        possible_paths.append(os.path.join(sys._MEIPASS, 'static', 'favicon.ico'))
    else:
        # 开发环境：项目根目录 + static/ 子目录
        _script_dir = os.path.dirname(os.path.abspath(__file__))
        possible_paths.append(os.path.join(_script_dir, 'favicon.ico'))
        possible_paths.append(os.path.join(_script_dir, 'static', 'favicon.ico'))

    for path in possible_paths:
        if os.path.exists(path):
            return Image.open(path)

    # 回退：找不到图标文件时动态绘制
    width = 64
    height = 64
    image = Image.new('RGB', (width, height), (52, 131, 235))
    dc = ImageDraw.Draw(image)
    dc.rectangle([10, 15, 54, 49], fill=(255, 255, 255), outline=(30, 80, 150), width=2)
    dc.line([10, 28, 54, 28], fill=(52, 131, 235), width=2)
    dc.line([20, 15, 20, 28], fill=(52, 131, 235), width=1)
    dc.line([30, 15, 30, 28], fill=(52, 131, 235), width=1)
    dc.line([40, 15, 40, 28], fill=(52, 131, 235), width=1)
    dc.line([25, 35, 39, 35], fill=(52, 131, 235), width=1)
    dc.line([25, 42, 39, 42], fill=(52, 131, 235), width=1)
    return image


def open_browser(icon, item):
    """用默认浏览器打开系统首页。 参数: icon, item"""
    webbrowser.open(f'http://127.0.0.1:{PORT}')


def restart_server(icon, item):
    """重启 Flask 服务。 参数: icon, item"""
    icon.stop()
    subprocess.Popen([sys.executable] + sys.argv)
    os._exit(0)


def exit_app(icon, item):
    """退出应用程序（停止 Flask + 清理托盘）。 参数: icon, item"""
    global is_shutting_down

    if is_shutting_down:
        return

    is_shutting_down = True
    print("正在关闭系统...")

    try:
        if icon:
            icon.stop()
            print("托盘图标已停止")
    except Exception as e:
        print(f"停止托盘图标时出错: {e}")

    stop_flask()

    time.sleep(0.3)
    print("程序退出")
    os._exit(0)


def copy_to_clipboard(text):
    """写入 Windows 系统剪贴板。不用 Tk 剪贴板，避免窗口销毁后粘贴内容丢失。"""
    try:
        CF_UNICODETEXT = 13
        GMEM_MOVEABLE = 0x0002
        kernel32 = ctypes.windll.kernel32
        user32 = ctypes.windll.user32
        kernel32.GlobalAlloc.restype = ctypes.c_void_p
        kernel32.GlobalAlloc.argtypes = [ctypes.c_uint, ctypes.c_size_t]
        kernel32.GlobalLock.restype = ctypes.c_void_p
        kernel32.GlobalLock.argtypes = [ctypes.c_void_p]
        kernel32.GlobalUnlock.argtypes = [ctypes.c_void_p]
        kernel32.GlobalFree.argtypes = [ctypes.c_void_p]
        user32.SetClipboardData.restype = ctypes.c_void_p
        user32.SetClipboardData.argtypes = [ctypes.c_uint, ctypes.c_void_p]

        data = text.encode('utf-16-le') + b'\x00\x00'
        handle = kernel32.GlobalAlloc(GMEM_MOVEABLE, len(data))
        if not handle:
            return False
        ptr = kernel32.GlobalLock(handle)
        if not ptr:
            kernel32.GlobalFree(handle)
            return False
        ctypes.memmove(ptr, data, len(data))
        kernel32.GlobalUnlock(handle)
        if not user32.OpenClipboard(None):
            kernel32.GlobalFree(handle)
            return False
        try:
            user32.EmptyClipboard()
            return bool(user32.SetClipboardData(CF_UNICODETEXT, handle))
        finally:
            user32.CloseClipboard()
    except Exception as e:
        logger.debug("写入剪贴板失败: %s", e, exc_info=True)
        return False


def show_start_message():
    """启动完成后弹出提示窗口，访问地址支持鼠标点击复制到剪贴板。"""
    try:
        local_ip = get_lan_ip()
        local_url = f'http://127.0.0.1:{PORT}'
        lan_url = f'http://{local_ip}:{PORT}/login'
        db_path = os.path.join(BASE_DIR, 'data', 'student.db')

        root = tk.Tk()
        root.withdraw()
        root.attributes('-toolwindow', True)
        root.attributes('-alpha', 0.0)

        dlg = tk.Toplevel(root)
        dlg.title("启动成功")
        dlg.resizable(False, False)
        dlg.attributes('-toolwindow', True)
        dlg.attributes('-topmost', True)

        font_base = ('Microsoft YaHei UI', 10)
        font_small = ('Microsoft YaHei UI', 9)

        body = tk.Frame(dlg, padx=20, pady=16)
        body.pack(fill='both', expand=True)

        tk.Label(body, text="学生数据管理系统已运行！",
                 font=('Microsoft YaHei UI', 12, 'bold'), anchor='w').pack(fill='x', pady=(0, 10))

        status_var = tk.StringVar(value="")

        def copy_text(text):
            """点击地址后写入系统剪贴板并给出提示。"""
            if copy_to_clipboard(text):
                status_var.set(f"已复制：{text}")
            else:
                status_var.set("复制失败，请手动选中内容复制")

        def add_copy_row(label_text, url, link_font=('Microsoft YaHei UI', 10, 'underline'), link_fg='#0d6efd'):
            """添加一行「说明 + 可点击复制的值」。"""
            row = tk.Frame(body)
            row.pack(fill='x', pady=3)
            tk.Label(row, text=label_text, font=font_base, anchor='w').pack(side='left')
            link = tk.Label(row, text=url, fg=link_fg, cursor='hand2', font=link_font)
            link.pack(side='left')
            link.bind('<Button-1>', lambda _e, u=url: copy_text(u))

        tk.Label(body, text=f"本地访问：{local_url}", font=font_base, anchor='w').pack(fill='x', pady=3)
        add_copy_row("局域网访问：", lan_url)

        tk.Label(body, text="点击蓝色局域网地址或红色密码即可复制到剪贴板",
                 fg='#6c757d', font=font_small, anchor='w').pack(fill='x', pady=(2, 8))

        tk.Label(body, text=f"数据库路径：{db_path}", fg='#6c757d', font=font_small,
                 anchor='w', justify='left', wraplength=460).pack(fill='x')

        if _admin_first_password:
            tk.Frame(body, height=1, bg='#dee2e6').pack(fill='x', pady=10)
            tk.Label(body, text="首次启动，管理员账号：", font=font_base, anchor='w').pack(fill='x')
            tk.Label(body, text="用户名：admin", font=font_base, anchor='w').pack(fill='x')
            add_copy_row("密码：", _admin_first_password,
                         link_font=('Consolas', 10, 'bold', 'underline'), link_fg='#b02a37')
            tk.Label(body, text="请妥善保管，登录后可在用户管理中修改。",
                     fg='#6c757d', font=font_small, anchor='w').pack(fill='x')

        tk.Label(body, textvariable=status_var, fg='#198754',
                 font=font_small, anchor='w').pack(fill='x', pady=(10, 0))
        tk.Button(body, text="确定", width=10, command=dlg.destroy).pack(pady=(10, 0))

        dlg.update_idletasks()
        width, height = dlg.winfo_reqwidth(), dlg.winfo_reqheight()
        x = (dlg.winfo_screenwidth() - width) // 2
        y = (dlg.winfo_screenheight() - height) // 3
        dlg.geometry(f"+{max(x, 0)}+{max(y, 0)}")
        dlg.bind('<Escape>', lambda _e: dlg.destroy())
        dlg.bind('<Return>', lambda _e: dlg.destroy())
        dlg.protocol("WM_DELETE_WINDOW", dlg.destroy)
        dlg.focus_force()
        dlg.wait_window()

        root.destroy()
        webbrowser.open(f'http://127.0.0.1:{PORT}/login')
    except Exception as e:
        print(f"显示启动弹窗失败: {e}")


def setup_signal_handlers():
    """注册系统信号处理器（SIGINT/SIGTERM 优雅退出）。"""
    def signal_handler(signum, frame):
        """系统信号处理函数。 参数: signum, frame"""
        print(f"收到系统信号 {signum}，正在关闭...")
        exit_app(None, None)

    signal.signal(signal.SIGINT, signal_handler)
    signal.signal(signal.SIGTERM, signal_handler)

    if hasattr(signal, 'SIGBREAK'):
        signal.signal(signal.SIGBREAK, signal_handler)


def main():
    """程序入口：启动 Flask 线程 + 系统托盘主循环。"""
    global flask_thread, tray_icon

    setup_signal_handlers()

    if getattr(sys, 'frozen', False):
        os.chdir(os.path.dirname(sys.executable))

    stop_event.clear()
    global is_shutting_down
    is_shutting_down = False

    flask_thread = threading.Thread(target=run_flask, daemon=True)
    flask_thread.start()

    time.sleep(1.5)

    msg_thread = threading.Thread(target=show_start_message, daemon=True)
    msg_thread.start()

    # 双击检测：两次点击间隔 < 500ms 才触发打开系统
    _click_state = {'last': 0.0}
    _DCLICK_MS = 0.5

    def _on_tray_click(icon, _item):
        now = time.time()
        if now - _click_state['last'] < _DCLICK_MS:
            open_browser(icon, _item)
        _click_state['last'] = now

    tray_icon = Icon(
        '学生数据管理系统',
        create_tray_icon_image(),
        '学生数据管理系统',
        menu=Menu(
            item('打开系统', _on_tray_click, default=True),
            item('重启服务器', restart_server),
            item('退出系统', exit_app)
        )
    )

    tray_icon.run()


if __name__ == '__main__':
    try:
        main()
    except KeyboardInterrupt:
        print("收到键盘中断，正在关闭...")
        exit_app(None, None)
    except Exception as e:
        print(f"程序启动异常: {e}")
        import traceback
        traceback.print_exc()
        stop_flask()
        if not getattr(sys, 'frozen', False):
            input("按任意键退出...")
        sys.exit(1)
