"""导入/批量更新业务服务层（P1-5）。

从 blueprints/admin.py 的 admin_batch_update_confirm 抽取的后台批量更新逻辑。
路由层保留 request 解析+响应，业务逻辑（DB更新+进度）移到本模块。
import_progress 字典由调用方持有并作为 ctx['progress_dict'] 传入，
全局状态结构不变，仅业务逻辑位置迁移。
"""
import os
import models
import pinyin_utils


def _safe_str(value):
    """安全地将值转换为字符串，处理None和非字符串类型"""
    if value is None:
        return ''
    return str(value).strip() if isinstance(value, str) else str(value)


def run_batch_update(ctx):
    """后台执行批量更新（service 层，由 admin_batch_update_confirm 调用）。 参数: ctx"""
    pending_results = ctx['pending_results']
    progress_id = ctx['progress_id']
    total_count = ctx['total_count']
    attendance_date = ctx['attendance_date']
    operator_username = ctx['operator_username']
    batch_update_data_file = ctx['batch_update_data_file']
    original_filename = ctx['original_filename']
    progress_dict = ctx['progress_dict']
    upload_folder = ctx['upload_folder']
    import uuid
    batch_id = uuid.uuid4().hex[:12]
    
    success_count = 0
    no_change_count = 0
    not_found_count = 0
    new_record_count = 0
    error_count = 0
    errors = []
    
    allowed_fields = ['student_no', 'national_no',
                      'registration_status', 'attendance_status', 'admin_class',
                      'virtual_class', 'major', 'grade', 'student_phone',
                      'parent_phone', 'parent_relation', 'backup_phone_1',
                      'backup_phone_2', 'address', 'gender', 'ethnicity',
                      'parent_name', 'parent_id_card', 'school',
                      'major_direction', 'household_type', 'junior_school',
                      'admission_type', 'junior_graduation_time',
                      'issue_authority', 'valid_start', 'valid_end',
                      'change_info', 'remark', 'enrollment_year', '_attendance']

    # 后台线程中创建 1 条数据库连接，循环内复用
    conn = models.get_db()
    try:
        for idx, result in enumerate(pending_results, 1):
            cursor = conn.cursor()
            
            try:
                id_card = result['data'].get('身份证号', '').strip()
                
                cursor.execute('SELECT * FROM students WHERE id_card = ? AND is_deleted = 0', (id_card,))
                student = cursor.fetchone()
                
                if not student:
                    if result.get('status') == 'new_record':
                        student_data = {
                            'id_card': id_card,
                            'name': result.get('name', _safe_str(result['data'].get('姓名', ''))),
                            'name_pinyin': pinyin_utils.name_to_pinyin(result.get('name', _safe_str(result['data'].get('姓名', '')))),
                        }
                        for change in result['changes']:
                            field = change['db_field']
                            new_value = change['new_value']
                            if field in ('school', 'enrollment_year'):
                                try:
                                    student_data[field] = int(float(new_value))
                                except (ValueError, TypeError):
                                    student_data[field] = 0
                            elif field == '_attendance':
                                continue
                            else:
                                student_data[field] = new_value
                        try:
                            models.create_student_with_conn(conn, student_data)
                            new_record_count += 1
                            progress_dict[progress_id]['new_record'] = new_record_count
                            progress_dict[progress_id]['current'] = idx
                            progress_dict[progress_id]['message'] = f'正在处理第 {idx}/{total_count} 条（新增）...'
                        except Exception as e:
                            error_msg = f"第{result['row']}行(新增): {str(e)}"
                            errors.append(error_msg)
                            error_count += 1
                            progress_dict[progress_id]['error'] = error_count
                            progress_dict[progress_id]['errors'].append(error_msg)
                    else:
                        not_found_count += 1
                        progress_dict[progress_id]['not_found'] = not_found_count
                        progress_dict[progress_id]['current'] = idx
                        progress_dict[progress_id]['message'] = f'正在处理第 {idx}/{total_count} 条...'
                    continue
                
                student_dict = dict(student)
                actual_updates = {}
                
                for change in result['changes']:
                    field = change['db_field']
                    new_value = change['new_value']
                    
                    if field not in allowed_fields:
                        continue
                    
                    if not new_value or str(new_value).strip() == '':
                        continue
                    
                    if field == '_attendance':
                        old_value = change['old_value']
                    else:
                        old_value = student_dict.get(field, '')
                    
                    if str(new_value) == str(old_value):
                        continue
                    
                    actual_updates[field] = (old_value, new_value)
                
                if not actual_updates:
                    no_change_count += 1
                    progress_dict[progress_id]['no_change'] = no_change_count
                    progress_dict[progress_id]['current'] = idx
                    progress_dict[progress_id]['message'] = f'正在处理第 {idx}/{total_count} 条...'
                    continue
                
                for field, (old_val, new_val) in actual_updates.items():
                    if field == '_attendance':
                        id_card = student_dict.get('id_card')
                        if not id_card:
                            continue
                        att_status = '出勤' if new_val == '出勤' else '缺勤'
                        cursor.execute('''INSERT INTO attendance_records (id_card, attendance_date, status, update_time)
                                          VALUES (?, ?, ?, datetime('now', 'localtime'))
                                          ON CONFLICT(id_card, attendance_date) DO UPDATE SET
                                          status = excluded.status, update_time = datetime('now', 'localtime')''',
                                       (id_card, attendance_date, att_status))
                    else:
                        cursor.execute(f'UPDATE students SET {field} = ?, updated_at = CURRENT_TIMESTAMP WHERE id = ?',
                                     (new_val, student_dict['id']))
                    
                    cursor.execute('''
                        INSERT INTO update_logs (student_id, id_card, field_name, old_value, new_value, updated_by, batch_id)
                        VALUES (?, ?, ?, ?, ?, ?, ?)
                    ''', (student_dict['id'], id_card, field, old_val, new_val, operator_username, batch_id))
                
                success_count += 1
                progress_dict[progress_id]['success'] = success_count
                progress_dict[progress_id]['current'] = idx
                progress_dict[progress_id]['message'] = f'正在处理第 {idx}/{total_count} 条...'
            except Exception as e:
                conn.rollback()
                error_msg = f"第{result['row']}行: {str(e)}"
                errors.append(error_msg)
                error_count += 1
                progress_dict[progress_id]['error'] = error_count
                progress_dict[progress_id]['errors'].append(error_msg)

            # 每 50 条 commit 一次，减少磁盘同步频率
            if idx % 50 == 0:
                conn.commit()
        
        # 提交剩余不足 50 条的记录
        if total_count % 50 != 0:
            conn.commit()

        progress_dict[progress_id]['status'] = 'completed'
        progress_dict[progress_id]['message'] = '批量更新完成！'
        progress_dict[progress_id]['success'] = success_count
        progress_dict[progress_id]['no_change'] = no_change_count
        progress_dict[progress_id]['not_found'] = not_found_count
        progress_dict[progress_id]['new_record'] = new_record_count
        progress_dict[progress_id]['error'] = error_count
        progress_dict[progress_id]['errors'] = errors[:10]

        models.update_sync_timestamp()
    except Exception as e:
        progress_dict[progress_id]['status'] = 'error'
        progress_dict[progress_id]['message'] = f'更新失败: {str(e)}'
        progress_dict[progress_id]['error'] = error_count + 1
        progress_dict[progress_id]['errors'] = (errors + [str(e)])[:10]
    finally:
        conn.close()
    
    if os.path.exists(batch_update_data_file):
        os.remove(batch_update_data_file)
    if original_filename:
        filepath = os.path.join(upload_folder, original_filename)
        if os.path.exists(filepath):
            os.remove(filepath)


