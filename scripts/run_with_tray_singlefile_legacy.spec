# -*- mode: python ; coding: utf-8 -*-

import os
import sys
from PyInstaller.utils.hooks import collect_data_files, collect_submodules

# ====================== 基础配置 ======================
# spec 文件位于项目根目录下
if '__file__' not in locals():
    spec_file = os.path.abspath(sys.argv[0])
else:
    spec_file = os.path.abspath(__file__)
base_path = os.path.dirname(spec_file)
block_cipher = None

# ====================== 版本信息配置 ======================
# version.txt 包含 PyInstaller 所需的 VSVersionInfo 结构
version_file = os.path.join(base_path, 'version.txt')

# 从 version.txt 动态读取产品名称和版本号
import re
exe_name = '学生数据管理系统'
if os.path.exists(version_file):
    with open(version_file, 'r', encoding='utf-8') as f:
        version_content = f.read()
    # 提取 ProductName
    match_name = re.search(r"StringStruct\(u'ProductName',\s*u'([^']+)'\)", version_content)
    if match_name:
        exe_name = match_name.group(1)
    # 提取 ProductVersion
    match_version = re.search(r"StringStruct\(u'ProductVersion',\s*u'([^']+)'\)", version_content)
    if match_version:
        exe_name += match_version.group(1)

# ====================== 收集 DLL 二进制文件 ======================
binaries = []

# SDK 目录下的身份证读卡器 DLL（ctypes 运行时动态加载）
sdk_dir = os.path.join(base_path, 'sdk')
if os.path.exists(sdk_dir):
    for file in os.listdir(sdk_dir):
        if file.lower().endswith(('.dll', '.so', '.dylib')):
            binaries.append((os.path.join(sdk_dir, file), 'sdk'))

# ====================== 收集数据文件 ======================
# 原则：只打包只读资源（模板/静态文件/SDK/图标/版本号）。
# 可写运行时数据不打包，由 run_with_tray.py 启动时自动创建：
#   user_data/data/         → init_db() 首次建库
#   user_data/databackup/   → backup_service 轮换备份
#   user_data/uploads/      → 用户上传文件
#   user_data/static/photos/ → 读卡器拍照
datas = []

# 1. Jinja2 模板文件（保持子目录结构）
templates_dir = os.path.join(base_path, 'templates')
if os.path.exists(templates_dir):
    for root, dirs, files in os.walk(templates_dir):
        for file in files:
            src = os.path.join(root, file)
            rel = os.path.relpath(root, base_path)
            datas.append((src, rel))

# 2. 静态资源（CSS / JS，排除运行时创建的 photos/ 子目录）
static_dir = os.path.join(base_path, 'static')
if os.path.exists(static_dir):
    for root, dirs, files in os.walk(static_dir):
        if os.path.basename(root) == 'photos':
            continue
        for file in files:
            src = os.path.join(root, file)
            rel = os.path.relpath(root, base_path)
            datas.append((src, rel))

# 3. SDK 文件（读卡器 DLL + license.dat）
if os.path.exists(sdk_dir):
    for file in os.listdir(sdk_dir):
        datas.append((os.path.join(sdk_dir, file), 'sdk'))

# 4. 图标文件（收集到 static/ 子目录，供 Flask url_for('static') 访问）
for icon_name in ['favicon.ico', 'favicon.png', 'icon.ico', 'icon.png']:
    icon_path = os.path.join(base_path, icon_name)
    if os.path.exists(icon_path):
        datas.append((icon_path, 'static'))

# 6. openpyxl 和 qrcode 的隐式依赖数据
datas += collect_data_files('openpyxl')
datas += collect_data_files('qrcode')

# ====================== 隐藏导入（防止分析遗漏） ======================
hiddenimports = []

# openpyxl 全部子模块（Excel 读写需要，约 189 个子模块）
hiddenimports += collect_submodules('openpyxl')

# qrcode 二维码生成
hiddenimports += [
    'qrcode',
    'qrcode.image.pil',
]

# PIL/Pillow（托盘图标绘制 + 二维码图片生成）
hiddenimports += [
    'PIL',
    'PIL.Image',
    'PIL.ImageFile',
    'PIL.ImageDraw',
    'PIL.ImageFont',
    'PIL._imaging',
]

# pystray 系统托盘（Windows 平台 _win32 后端）
hiddenimports += [
    'pystray',
    'pystray._win32',
]

# tkinter（启动完成弹窗）
hiddenimports += [
    'tkinter',
    'tkinter.messagebox',
]

# 项目 services 子模块
hiddenimports += [
    'services',
    'services.backup_service',
    'services.card_reader',
]

# 其他必要模块
hiddenimports += [
    'ipaddress',
    'threading',
    'datetime',
    'shutil',
    'sqlite3',
    'logging.handlers',
]

# ====================== 排除模块（减小体积） ======================
excludes = [
    'pandas',
    'matplotlib',
    'numpy',
    'scipy',
    'PyQt5',
    'wx',
    'tensorflow',
    'torch',
    'sklearn',
    'notebook',
    'ipython',
    'prompt_toolkit',
    'PIL.ImageQt',
    'PIL.ImageTk',
    'PIL._webp',
    'tkinter.test',
    'tkinter.ttk',
    'unittest',
    'test',
    'pydoc',
    'distutils',
    'setuptools',
]

# ====================== Analysis 分析配置 ======================
a = Analysis(
    [os.path.join(base_path, 'run_with_tray.py')],
    pathex=[base_path],
    binaries=binaries,
    datas=datas,
    hiddenimports=hiddenimports,
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    excludes=excludes,
    noarchive=False,
    optimize=0,
)
pyz = PYZ(a.pure)

# ====================== EXE 生成配置 ======================
exe = EXE(
    pyz,
    a.scripts,
    a.binaries,
    a.datas,
    [],
    name=exe_name,
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=True,
    upx_exclude=[],
    runtime_tmpdir=None,
    console=False,
    disable_windowed_traceback=False,
    argv_emulation=False,
    version=version_file,        # 从 version.txt 读取版本信息
    target_arch=None,
    codesign_identity=None,
    entitlements_file=None,
    icon=os.path.join(base_path, 'favicon.ico'),
)