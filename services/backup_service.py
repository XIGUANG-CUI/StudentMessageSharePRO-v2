import os
import json
import shutil
import sqlite3
import time
import logging
import threading
from datetime import datetime, timedelta

logger = logging.getLogger(__name__)

INTERVAL_PRESETS = [
    {'label': '10 分钟', 'value': 600},
    {'label': '20 分钟', 'value': 1200},
    {'label': '30 分钟', 'value': 1800},
    {'label': '1 小时', 'value': 3600},
    {'label': '2 小时', 'value': 7200},
    {'label': '4 小时', 'value': 14400},
    {'label': '12 小时', 'value': 43200},
    {'label': '24 小时', 'value': 86400},
]


class BackupService:

    MAX_LOG_ENTRIES = 500
    MAX_LOG_AGE_DAYS = 30

    def __init__(self, db_path, backup_dir=None):
        self.db_path = db_path
        if backup_dir is None:
            backup_dir = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), 'databackup')
        self.backup_dir = backup_dir
        self.file_a = os.path.join(backup_dir, 'student_A.db')
        self.file_b = os.path.join(backup_dir, 'student_B.db')
        self.rotation_file = os.path.join(backup_dir, 'backup_rotation.txt')
        self.log_file = os.path.join(backup_dir, 'backup_log.txt')
        self._config_file = os.path.join(backup_dir, 'backup_config.json')
        self._log_cleanup_counter = 0
        self._last_result = None
        self._next_backup_at = None
        self._backup_interval = self._load_interval()
        self._backup_status = 'idle'
        self._status_lock = threading.Lock()
        self._timer_lock = threading.Lock()
        self._active_timer = None

        logger.info("备份服务初始化完成，备份目录: %s", self.backup_dir)
        print(f"[备份服务] 初始化完成")
        print(f"[备份服务] 备份目录: {self.backup_dir}")
        print(f"[备份服务] 备份间隔: {self._backup_interval}秒 ({self._backup_interval // 60}分钟)")

    def _load_interval(self):
        """从配置文件加载备份间隔。"""
        try:
            if os.path.exists(self._config_file):
                with open(self._config_file, 'r', encoding='utf-8') as f:
                    data = json.load(f)
                    interval = int(data.get('backup_interval', 1800))
                    if interval < 60:
                        interval = 60
                    if interval > 86400:
                        interval = 86400
                    return interval
        except Exception as e:
            logger.warning("加载备份配置失败，使用默认值: %s", e)
        return 1800

    def _save_interval(self, interval):
        """保存备份间隔到配置文件。 参数: interval"""
        try:
            os.makedirs(self.backup_dir, exist_ok=True)
            with open(self._config_file, 'w', encoding='utf-8') as f:
                json.dump({
                    'backup_interval': interval,
                    'updated_at': datetime.now().isoformat()
                }, f, ensure_ascii=False, indent=2)
            logger.info("备份间隔已持久化: %d秒", interval)
        except Exception as e:
            logger.error("保存备份配置失败: %s", e)

    def _cancel_timer(self):
        """取消当前定时备份计时器。"""
        with self._timer_lock:
            if self._active_timer is not None:
                self._active_timer.cancel()
                self._active_timer = None

    def _schedule_timer(self):
        """调度下一次定时备份。"""
        self._cancel_timer()
        interval = self._backup_interval
        with self._timer_lock:
            self._active_timer = threading.Timer(interval, self._auto_backup_task)
            self._active_timer.daemon = True
            self._active_timer.start()
        logger.info("备份定时器已调度，间隔: %d秒", interval)

    def start_auto_backup(self, interval=3600, immediate=True):
        """启动自动定时备份。 参数: interval, immediate"""
        try:
            self._backup_interval = interval
            now = datetime.now()
            self._next_backup_at = now + timedelta(seconds=interval)

            print(f"[定时备份] 已启动，间隔: {interval}秒 ({interval // 60}分钟)")
            logger.info("定时备份已启动，间隔: %d秒，立即执行: %s", interval, immediate)
            if immediate:
                print("[定时备份] 立即执行首次备份...")
                self._set_status('backing_up')
                try:
                    self.silent_backup()
                finally:
                    self._set_status('idle')
                self._next_backup_at = datetime.now() + timedelta(seconds=interval)
            self._schedule_timer()
        except Exception as e:
            print(f"[定时备份] 启动失败: {e}")
            logger.error("定时备份启动失败: %s", e, exc_info=True)

    def _auto_backup_task(self):
        """定时备份任务执行体（计时器回调）。"""
        try:
            print(f"\n[定时备份] 开始执行定时备份...")
            self._set_status('backing_up')
            try:
                self.silent_backup()
            finally:
                self._set_status('idle')
        except Exception as e:
            print(f"[定时备份] 任务异常: {e}")
            logger.error("定时备份任务异常: %s", e, exc_info=True)
        finally:
            self._next_backup_at = datetime.now() + timedelta(seconds=self._backup_interval)
            self._schedule_timer()

    def get_interval(self):
        """获取间隔。"""
        return {
            'interval_seconds': self._backup_interval,
            'interval_minutes': self._backup_interval // 60,
            'interval_hours': round(self._backup_interval / 3600, 1),
            'interval_label': self._format_interval(self._backup_interval),
            'presets': INTERVAL_PRESETS,
        }

    def set_interval(self, seconds):
        """设置备份间隔（秒）并持久化。 参数: seconds"""
        seconds = int(seconds)
        if seconds < 60:
            seconds = 60
        if seconds > 86400:
            seconds = 86400

        if seconds == self._backup_interval:
            return self.get_interval()

        self._backup_interval = seconds
        self._next_backup_at = datetime.now() + timedelta(seconds=seconds)
        self._save_interval(seconds)
        self._schedule_timer()

        print(f"[备份] 间隔已更新: {seconds}秒 ({seconds // 60}分钟)")
        logger.info("备份间隔已动态调整: %d秒", seconds)
        return self.get_interval()

    @staticmethod
    def _format_interval(seconds):
        """格式化备份间隔为人类可读文本。 参数: seconds"""
        if seconds < 60:
            return f'{seconds}秒'
        if seconds < 3600:
            return f'{seconds // 60}分钟'
        if seconds < 86400:
            h = seconds // 3600
            m = (seconds % 3600) // 60
            return f'{h}小时' + (f'{m}分钟' if m > 0 else '')
        return f'{seconds // 3600}小时'

    def _set_status(self, status):
        """更新备份状态。 参数: status"""
        with self._status_lock:
            self._backup_status = status

    def silent_backup(self):
        """静默执行一次备份（不触发 UI 倒计时）。"""
        try:
            print("[备份] 开始执行静默备份...")
            logger.info("开始执行静默备份，源数据库: %s", self.db_path)

            if not os.path.exists(self.db_path):
                msg = f"主数据库不存在: {self.db_path}"
                print(f"[备份] {msg}")
                logger.warning(msg)
                self._last_result = {'success': False, 'error': 'db_not_found', 'time': datetime.now().isoformat()}
                return False

            self._ensure_backup_dir()
            success, target_file, backup_type = self._perform_backup()

            self._last_result = {
                'success': success,
                'file': os.path.basename(target_file) if target_file else None,
                'type': backup_type,
                'time': datetime.now().isoformat(),
                'error': None if success else 'backup_failed'
            }

            if success:
                self._print_success_message(target_file, backup_type)
                self._log_backup(target_file, True, backup_type)
                logger.info("备份成功: %s", os.path.basename(target_file))
                return True
            else:
                print(f"[备份] 备份操作失败")
                logger.error("备份操作失败")
                return False

        except Exception as e:
            print(f"[备份] 异常: {str(e)}")
            import traceback
            traceback.print_exc()
            logger.error("备份异常: %s", e, exc_info=True)
            self._last_result = {'success': False, 'error': str(e), 'time': datetime.now().isoformat()}
            return False

    def _ensure_backup_dir(self):
        """确保备份目录存在。"""
        os.makedirs(self.backup_dir, exist_ok=True)

        if not os.path.exists(self.rotation_file):
            try:
                with open(self.rotation_file, 'w', encoding='utf-8') as f:
                    f.write('A')
            except Exception as e:
                print(f"[备份] 创建轮换文件失败: {e}")

        if not os.path.exists(self.log_file):
            try:
                with open(self.log_file, 'w', encoding='utf-8') as f:
                    f.write("=" * 50 + "\n")
                    f.write("学生数据管理系统 数据库备份日志\n")
                    f.write("创建时间: " + datetime.now().strftime("%Y-%m-%d %H:%M:%S") + "\n")
                    f.write("=" * 50 + "\n")
            except Exception as e:
                print(f"[备份] 创建日志文件失败: {e}")

    def _perform_backup(self):
        """执行备份核心流程（AB 轮换 + 完整性校验）。"""
        try:
            current_mark = self._read_rotation_mark()
            print(f"[备份] 当前轮换状态: '{current_mark}'")

            if current_mark == 'A':
                backup_type = 'B'
                target_file = self.file_b
                new_mark = 'B'
                backup_name = "B文件(student_B.db)"
            else:
                backup_type = 'A'
                target_file = self.file_a
                new_mark = 'A'
                backup_name = "A文件(student_A.db)"

            print(f"[备份] 决策: 备份{backup_name}")

            backup_success = self._execute_backup(target_file)

            if backup_success:
                self._update_rotation_file(new_mark)
                print(f"[备份] 轮换状态更新: '{current_mark}' -> '{new_mark}'")
                return True, target_file, backup_type
            else:
                print(f"[备份] 备份操作失败")
                return False, None, None

        except Exception as e:
            print(f"[备份] 备份逻辑异常: {e}")
            import traceback
            traceback.print_exc()
            return False, None, None

    def _read_rotation_mark(self):
        """读取当前 AB 轮换标记（A 或 B）。"""
        try:
            if os.path.exists(self.rotation_file):
                with open(self.rotation_file, 'r', encoding='utf-8') as f:
                    content = f.read().strip()
                    if content in ['A', 'B']:
                        return content
            return 'A'
        except OSError:
            return 'A'

    def _execute_backup(self, dst_db):
        """执行备份到目标文件（SQLite API 优先，文件复制降级）。 参数: dst_db"""
        if self._backup_with_sqlite(dst_db):
            return True

        print("[备份] SQLite备份失败，尝试文件复制...")
        return self._backup_with_file_copy(dst_db)

    def _backup_with_sqlite(self, dst_db):
        """使用 SQLite 原生 backup API 进行备份。 参数: dst_db"""
        src_conn = None
        dst_conn = None

        try:
            src_conn = sqlite3.connect(self.db_path)
            dst_conn = sqlite3.connect(dst_db)

            src_conn.execute("PRAGMA busy_timeout = 5000;")

            print(f"[备份] 源数据库: {os.path.basename(self.db_path)}")
            print(f"[备份] 目标备份: {os.path.basename(dst_db)}")

            print("[备份] 正在执行SQLite备份...")
            src_conn.backup(dst_conn)

            if self._verify_database(dst_db):
                print("[备份] SQLite原生备份成功")
                return True
            else:
                print("[备份] SQLite备份验证失败")
                return False

        except Exception as e:
            print(f"[备份] SQLite备份失败: {e}")
            return False

        finally:
            if src_conn:
                try:
                    src_conn.close()
                except Exception as e:
                    logger.debug("备份-关闭源数据库连接失败（已忽略）: %s", e, exc_info=True)
            if dst_conn:
                try:
                    dst_conn.close()
                except Exception as e:
                    logger.debug("备份-关闭目标数据库连接失败（已忽略）: %s", e, exc_info=True)

    def _backup_with_file_copy(self, dst_db):
        """使用文件复制进行备份（SQLite API 失败时的降级方案）。 参数: dst_db"""
        try:
            max_attempts = 3
            wal_file = self.db_path + '-wal'
            shm_file = self.db_path + '-shm'
            dst_wal = dst_db + '-wal'
            dst_shm = dst_db + '-shm'

            for attempt in range(max_attempts):
                try:
                    print(f"[备份] 文件复制尝试 {attempt + 1}/{max_attempts}...")

                    if not os.access(self.db_path, os.R_OK):
                        print(f"[备份] 源文件不可读: {self.db_path}")
                        time.sleep(0.5)
                        continue

                    shutil.copy2(self.db_path, dst_db)

                    if os.path.exists(wal_file):
                        shutil.copy2(wal_file, dst_wal)
                    if os.path.exists(shm_file):
                        shutil.copy2(shm_file, dst_shm)

                    if self._verify_database(dst_db):
                        print(f"[备份] 文件复制成功")
                        return True
                    else:
                        print(f"[备份] 文件验证失败")

                except PermissionError as e:
                    print(f"[备份] 权限错误: {e}")
                    if attempt < max_attempts - 1:
                        time.sleep(0.5)
                    continue
                except Exception as e:
                    print(f"[备份] 复制异常: {e}")
                    if attempt < max_attempts - 1:
                        time.sleep(0.5)
                    continue

            print("[备份] 文件复制最终失败")
            return False

        except Exception as e:
            print(f"[备份] 复制过程异常: {e}")
            return False

    def _verify_database(self, db_path):
        """验证备份数据库完整性（表数量 + 关键表）。 参数: db_path"""
        conn = None
        try:
            conn = sqlite3.connect(db_path)
            cursor = conn.cursor()

            cursor.execute("PRAGMA integrity_check;")
            integrity = cursor.fetchone()
            if integrity and integrity[0] != 'ok':
                print(f"[备份] 完整性检查失败: {integrity[0]}")
                logger.error("备份完整性检查失败: %s", integrity[0])
                return False

            cursor.execute("SELECT name FROM sqlite_master WHERE type='table';")
            tables = cursor.fetchall()

            table_count = len(tables)

            if table_count == 0:
                print("[备份] 验证失败: 数据库无表结构")
                return False

            table_names = [table[0].lower() for table in tables]
            has_users = 'users' in table_names
            has_students = 'students' in table_names

            verified_tables = []
            for t_name in table_names:
                if t_name.startswith('sqlite_'):
                    continue
                cursor.execute(f"SELECT COUNT(*) FROM [{t_name}];")
                row_count = cursor.fetchone()[0]
                verified_tables.append(f"{t_name}({row_count})")

            detail = ', '.join(verified_tables)
            print(f"[备份] 验证通过: {table_count}个表", end="")
            if has_users and has_students:
                print(f" (含users+students) [{detail}]")
            else:
                print(f" [{detail}]")

            return True

        except sqlite3.Error as e:
            print(f"[备份] 数据库验证错误: {e}")
            logger.error("数据库验证错误: %s", e)
            return False
        except Exception as e:
            print(f"[备份] 验证异常: {e}")
            logger.error("验证异常: %s", e)
            return False
        finally:
            if conn:
                try:
                    conn.close()
                except Exception as e:
                    logger.debug("备份-关闭数据库连接失败（已忽略）: %s", e, exc_info=True)

    def _update_rotation_file(self, next_mark):
        """更新 AB 轮换标记文件。 参数: next_mark"""
        try:
            with open(self.rotation_file, 'w', encoding='utf-8') as f:
                f.write(next_mark)
            return True
        except Exception as e:
            print(f"[备份] 更新轮换文件失败: {e}")
            return False

    def _print_success_message(self, target_file, backup_type):
        """输出备份成功信息。 参数: target_file, backup_type"""
        try:
            file_size = os.path.getsize(target_file)
            size_mb = file_size / 1024 / 1024

            mtime = os.path.getmtime(target_file)
            time_str = datetime.fromtimestamp(mtime).strftime("%Y-%m-%d %H:%M:%S")

            print("\n" + "=" * 60)
            print("  数据库备份成功完成")
            print("=" * 60)

            if backup_type == 'A':
                print("  备份位置: A文件 (student_A.db)")
            else:
                print("  备份位置: B文件 (student_B.db)")

            print(f"  文件大小: {size_mb:.2f} MB")
            print(f"  备份时间: {time_str}")
            print(f"  存储目录: {os.path.dirname(target_file)}")
            print("=" * 60 + "\n")

        except Exception as e:
            print(f"[备份] 生成成功信息失败: {e}")
            print("  [备份] 备份成功完成")

    def _log_backup(self, backup_file, success, backup_type):
        """记录备份日志到文件。 参数: backup_file, success, backup_type"""
        try:
            timestamp = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
            status = "成功" if success else "失败"
            file_name = os.path.basename(backup_file)

            file_size = 0
            file_mtime = ""
            if os.path.exists(backup_file):
                file_size = os.path.getsize(backup_file)
                mtime = os.path.getmtime(backup_file)
                file_mtime = datetime.fromtimestamp(mtime).strftime("%H:%M:%S")

            if file_size >= 1024 * 1024:
                size_str = f"{file_size / 1024 / 1024:.2f} MB"
            elif file_size >= 1024:
                size_str = f"{file_size / 1024:.2f} KB"
            else:
                size_str = f"{file_size} B"

            log_entry = (
                f"[{timestamp}] "
                f"文件: {file_name} | "
                f"类型: {backup_type} | "
                f"大小: {size_str} | "
                f"时间: {file_mtime} | "
                f"状态: {status}\n"
            )

            with open(self.log_file, 'a', encoding='utf-8') as f:
                f.write(log_entry)

            self._log_cleanup_counter += 1
            if self._log_cleanup_counter >= 10:
                self._log_cleanup_counter = 0
                self._cleanup_log()

        except Exception as e:
            print(f"[备份日志] 记录失败: {e}")

    def _cleanup_log(self):
        """清理过期备份日志。"""
        try:
            if not os.path.exists(self.log_file):
                return

            with open(self.log_file, 'r', encoding='utf-8') as f:
                lines = f.readlines()

            header_lines = []
            data_lines = []
            in_header = True
            for line in lines:
                if in_header:
                    if line.startswith('['):
                        in_header = False
                        data_lines.append(line)
                    else:
                        header_lines.append(line)
                else:
                    data_lines.append(line)

            if not data_lines:
                return

            cutoff_date = datetime.now().replace(hour=0, minute=0, second=0, microsecond=0)
            cutoff_date = cutoff_date - timedelta(days=self.MAX_LOG_AGE_DAYS)

            kept = []
            removed_count = 0
            for line in data_lines:
                try:
                    date_str = line[1:11]
                    entry_date = datetime.strptime(date_str, '%Y-%m-%d')
                    if entry_date >= cutoff_date:
                        kept.append(line)
                    else:
                        removed_count += 1
                except (ValueError, IndexError):
                    kept.append(line)

            if len(kept) > self.MAX_LOG_ENTRIES:
                excess = len(kept) - self.MAX_LOG_ENTRIES
                kept = kept[-self.MAX_LOG_ENTRIES:]
                removed_count += excess

            if removed_count > 0:
                with open(self.log_file, 'w', encoding='utf-8') as f:
                    for line in header_lines:
                        f.write(line)
                    for line in kept:
                        f.write(line)
                print(f"[日志清理] 移除了 {removed_count} 条过期/超量日志记录")

        except Exception as e:
            print(f"[日志清理] 清理异常: {e}")

    def purge_old_logs(self, keep_days=None, max_entries=None):
        """清除超过保留期限的旧备份。 参数: keep_days, max_entries"""
        keep_days = keep_days if keep_days is not None else self.MAX_LOG_AGE_DAYS
        max_entries = max_entries if max_entries is not None else self.MAX_LOG_ENTRIES

        saved_age, saved_entries = self.MAX_LOG_AGE_DAYS, self.MAX_LOG_ENTRIES
        self.MAX_LOG_AGE_DAYS = keep_days
        self.MAX_LOG_ENTRIES = max_entries

        try:
            self._cleanup_log()
        finally:
            self.MAX_LOG_AGE_DAYS = saved_age
            self.MAX_LOG_ENTRIES = saved_entries

    def get_countdown_info(self):
        """获取倒计时信息。"""
        with self._status_lock:
            status = self._backup_status

        if self._next_backup_at is None:
            return {
                'remaining_seconds': 0,
                'total_interval': self._backup_interval,
                'next_backup_at': None,
                'status': 'stopped',
                'status_label': '未启动',
                'percent': 0,
                'interval_info': self.get_interval(),
                'poll_config': {
                    'slow': 30000,
                    'medium': 10000,
                    'fast': 3000,
                    'threshold_fast': 60,
                    'threshold_medium': 300,
                }
            }

        now = datetime.now()
        remaining = (self._next_backup_at - now).total_seconds()
        if remaining < 0:
            remaining = 0
        total = self._backup_interval
        percent = round((1 - remaining / total) * 100, 1) if total > 0 else 0

        if status == 'backing_up':
            status_label = '备份中'
        elif remaining <= 0:
            status_label = '即将备份'
        else:
            status_label = '等待中'

        return {
            'remaining_seconds': max(0, int(remaining)),
            'total_interval': total,
            'next_backup_at': self._next_backup_at.isoformat() if self._next_backup_at else None,
            'status': status,
            'status_label': status_label,
            'percent': min(100, percent),
            'last_result': self.get_last_backup_info(),
            'interval_info': self.get_interval(),
            'poll_config': {
                'slow': 30000,
                'medium': 10000,
                'fast': 3000,
                'threshold_fast': 60,
                'threshold_medium': 300,
            }
        }

    def get_last_backup_info(self):
        """获取最后备份信息。"""
        if self._last_result is None:
            return {'success': None, 'message': '尚未执行备份'}
        return dict(self._last_result)

    def get_backup_status(self):
        """获取备份状态。"""
        info = {
            'db_path': self.db_path,
            'db_exists': os.path.exists(self.db_path),
            'backup_dir': self.backup_dir,
            'backup_dir_exists': os.path.exists(self.backup_dir),
            'file_a_exists': os.path.exists(self.file_a),
            'file_b_exists': os.path.exists(self.file_b),
            'last_result': self.get_last_backup_info(),
        }

        if info['db_exists']:
            info['db_size_mb'] = round(os.path.getsize(self.db_path) / (1024 * 1024), 2)
        if info['file_a_exists']:
            info['file_a_size_mb'] = round(os.path.getsize(self.file_a) / (1024 * 1024), 2)
            info['file_a_mtime'] = datetime.fromtimestamp(os.path.getmtime(self.file_a)).isoformat()
        if info['file_b_exists']:
            info['file_b_size_mb'] = round(os.path.getsize(self.file_b) / (1024 * 1024), 2)
            info['file_b_mtime'] = datetime.fromtimestamp(os.path.getmtime(self.file_b)).isoformat()

        info['verify'] = self._get_verify_detail(self.db_path if info['db_exists'] else None)
        info['interval_info'] = self.get_interval()

        return info

    def _get_verify_detail(self, db_path):
        """获取备份验证详情（各表行数）。 参数: db_path"""
        if not db_path or not os.path.exists(db_path):
            return {'ok': False, 'error': '数据库文件不存在'}
        conn = None
        try:
            conn = sqlite3.connect(db_path)
            cursor = conn.cursor()

            cursor.execute("PRAGMA integrity_check;")
            integrity = cursor.fetchone()
            integrity_ok = integrity and integrity[0] == 'ok'
            integrity_msg = integrity[0] if integrity else '未知'

            cursor.execute("SELECT name FROM sqlite_master WHERE type='table' AND name NOT LIKE 'sqlite_%';")
            tables = cursor.fetchall()

            tables_detail = []
            for (t_name,) in tables:
                cursor.execute(f"SELECT COUNT(*) FROM [{t_name}];")
                row_count = cursor.fetchone()[0]
                tables_detail.append({'name': t_name, 'rows': row_count})

            return {
                'ok': integrity_ok,
                'integrity': integrity_msg,
                'table_count': len(tables_detail),
                'tables': tables_detail
            }

        except Exception as e:
            logger.error("获取验证详情失败: %s", e)
            return {'ok': False, 'error': str(e)}
        finally:
            if conn:
                try:
                    conn.close()
                except Exception as e:
                    logger.debug("备份-关闭数据库连接失败（已忽略）: %s", e, exc_info=True)
