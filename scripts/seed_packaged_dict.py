# -*- coding: utf-8 -*-
"""把区划字典预置进打包产物，供出厂包开箱即用。

用法：
    python scripts/seed_packaged_dict.py              # 自动定位 dist 下唯一产物目录
    python scripts/seed_packaged_dict.py <产物目录>    # 指定含 exe 的那一层目录

只生成 user_data/data/district.db，不创建 student.db 与 admin 账号；
首次启动仍由程序自行初始化管理员与业务库。
"""
import os
import sqlite3
import sys

REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def resolve_target(argv):
    """解析产物目录：优先命令行参数，否则取 dist 下唯一目录。"""
    if len(argv) > 1:
        return os.path.abspath(argv[1])
    dist = os.path.join(REPO, 'dist')
    if not os.path.isdir(dist):
        raise SystemExit('未找到 dist 目录，请先执行 pyinstaller run_with_tray.spec')
    names = [d for d in os.listdir(dist) if os.path.isdir(os.path.join(dist, d))]
    if len(names) != 1:
        raise SystemExit('dist 下产物目录不唯一，请显式指定：%s' % names)
    return os.path.join(dist, names[0])


def main():
    """校验数据包并把字典预置到 user_data/data/district.db。"""
    target = resolve_target(sys.argv)
    if not os.path.exists(os.path.join(target, '_internal')):
        raise SystemExit('目标目录不含 _internal，疑似不是 onedir 产物：%s' % target)

    user_data = os.path.join(target, 'user_data')
    os.makedirs(os.path.join(user_data, 'data'), exist_ok=True)
    os.environ['USER_DATA_DIR'] = user_data

    sys.path.insert(0, REPO)
    import models.district as district_model
    from services.district_match import pack as pack_mod

    # 优先用产物内自带的数据包，保证预置的字典与随包数据包完全一致
    bundled = os.path.join(target, '_internal', 'data', 'district_pack.tsv.gz')
    pack_path = bundled if os.path.exists(bundled) else district_model.pack_path()
    ok, detail = pack_mod.verify_pack(pack_path)
    print('数据包：%s' % pack_path)
    print('校验：%s total=%s levels=%s' % (ok, detail.get('total'), detail.get('by_level')))
    if not ok:
        raise SystemExit('数据包校验失败，已中止预置')

    print('导入中...')
    result = district_model.import_dictionary(pack_path)
    print('导入完成：%s' % result)

    # 收敛 WAL 并删除侧车文件，交付目录只保留单个 district.db
    db_path = district_model.DISTRICT_DB_PATH
    conn = sqlite3.connect(db_path, timeout=30)
    try:
        conn.execute('PRAGMA wal_checkpoint(TRUNCATE)')
    finally:
        conn.close()
    for suffix in ('-wal', '-shm'):
        sidecar = db_path + suffix
        if os.path.exists(sidecar):
            os.remove(sidecar)

    # 导入 config 时顺带生成了 secret.key / public_access.key，必须删掉：
    # 否则所有安装包共用同一份会话密钥，存在伪造 session 与跨校串号风险
    for name in ('secret.key', 'public_access.key'):
        stale = os.path.join(user_data, 'data', name)
        if os.path.exists(stale):
            os.remove(stale)

    print('字典库：%s（%d 字节）' % (db_path, os.path.getsize(db_path)))
    print('检查：user_data 下只应有 data/district.db，student.db 与密钥由首次启动生成')


if __name__ == '__main__':
    main()
