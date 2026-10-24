# -*- coding: utf-8 -*-
"""区划匹配 Blueprint（页面 + API）。

路由清单见 CHANGES.md；响应统一 {success, data|message}。
批量/导入为后台线程 + 内存进度表（单进程部署，重启即失效）。
"""
import json
import logging
import os
import threading
import time
import uuid

from flask import (Blueprint, current_app, jsonify, render_template, request,
                   send_file, session)

import auth
import config
from auth import _require_admin_json, _require_login_json
from models import district as district_model
from models import get_filter_values
from services.district_match import exporter, importer, runner
from services.district_match import pack as pack_mod

logger = logging.getLogger(__name__)

bp = Blueprint("district_match", __name__)

_ALLOWED_INPUT_EXT = (".xlsx", ".xlsm", ".csv")
_progress = {}
_progress_lock = threading.Lock()
_batch_lock = threading.Lock()
_import_lock = threading.Lock()
_PROGRESS_TTL = 3600


# ------------------------------------------------------------------ 工具
def _ok(data=None, **extra):
    payload = {"success": True}
    if data is not None:
        payload["data"] = data
    payload.update(extra)
    return jsonify(payload)


def _fail(message, status=400):
    return jsonify({"success": False, "message": message}), status


def _new_progress(kind):
    pid = uuid.uuid4().hex
    now = time.time()
    with _progress_lock:
        # 惰性清理超过 1 小时的进度
        for key in [k for k, v in _progress.items() if now - v.get("ts", now) > _PROGRESS_TTL]:
            _progress.pop(key, None)
        _progress[pid] = {"kind": kind, "status": "running", "phase": "",
                          "done": 0, "total": 0, "message": "", "ts": now}
    return pid


def _update_progress(pid, **fields):
    with _progress_lock:
        item = _progress.get(pid)
        if item is None:
            return
        item.update(fields)
        item["ts"] = time.time()


def _spawn(pid, target):
    """在后台线程执行 target(app, pid)；线程内独立 app context + 独立连接。"""
    app_obj = current_app._get_current_object()

    def _worker():
        with app_obj.app_context():
            try:
                target(app_obj, pid)
            except Exception as exc:  # 线程异常只落日志 + 进度，不打印堆栈到前端
                logger.exception("区划后台任务失败：%s", exc)
                _update_progress(pid, status="error", message=str(exc)[:300])

    thread = threading.Thread(target=_worker, daemon=True)
    thread.start()


def _upload_dir():
    path = os.path.join(config.UPLOAD_FOLDER, "district_match")
    os.makedirs(path, exist_ok=True)
    return path


def _save_upload(storage):
    ext = os.path.splitext(storage.filename or "")[1].lower()
    if ext not in _ALLOWED_INPUT_EXT:
        raise ValueError("仅支持 .xlsx / .xlsm / .csv 文件")
    name = uuid.uuid4().hex + ext
    path = os.path.join(_upload_dir(), name)
    storage.save(path)
    return path


def _result_payload(result):
    return {
        "district_province_name": result.province_name or "",
        "district_province_code": result.province_code or "",
        "district_city_name": result.city_name or "",
        "district_city_code": result.city_code or "",
        "district_county_name": result.district_name or "",
        "district_county_code": result.district_code or "",
        "district_match_status": result.match_status or "",
        "match_status": result.match_status or "",
        "engine1_detail": result.engine1_detail,
        "engine2_detail": result.engine2_detail,
        "decision_path": result.decision_path,
    }


# ------------------------------------------------------------------ 页面
@bp.route("/admin/district-match")
@auth.admin_required
def page():
    """区划匹配页面（仅 admin）。"""
    # 筛选下拉选项：包含毕业生，便于勾选「包含毕业学生」后按年级/班级筛选
    filter_values = get_filter_values(include_graduated=True)
    return render_template("admin/district_match.html", filter_values=filter_values)


# ------------------------------------------------------------------ 单条查询
@bp.route("/api/district-match/query", methods=["POST"])
def query():
    err = _require_admin_json()
    if err:
        return err
    data = request.get_json(silent=True) or {}
    id_card = (data.get("id_card") or "").strip()
    address = (data.get("address") or "").strip()
    if not id_card and not address:
        return _fail("请输入身份证号或地址")
    try:
        result = runner.run_match(district_model.DistrictRepository(),
                                  id_card, address, with_detail=True)
    except district_model.DistrictDbMissing:
        return _fail("区划字典尚未导入，请先在“字典状态”中导入数据包", 409)
    return _ok(_result_payload(result))


# ------------------------------------------------------------------ 批量：预览 / 启动
@bp.route("/api/district-match/preview", methods=["POST"])
def preview():
    err = _require_admin_json()
    if err:
        return err
    storage = request.files.get("file")
    if not storage or not storage.filename:
        return _fail("请选择上传文件")
    path = None
    try:
        path = _save_upload(storage)
        headers, rows = importer.read_input_rows(path, limit=10)
        if not headers:
            return _fail("文件为空或缺少表头")
        guessed = {"id_card": "", "address": ""}
        for key in headers:
            if "身份证" in key:
                guessed["id_card"] = key
            if "地址" in key or "住址" in key:
                guessed["address"] = key
        return _ok({"headers": headers, "rows": rows, "suggest": guessed,
                    "total_estimate": len(rows)})
    except ValueError as exc:
        return _fail(str(exc))
    finally:
        if path and os.path.exists(path):
            try:
                os.remove(path)
            except OSError:
                pass


@bp.route("/api/district-match/students", methods=["POST"])
def students_preview():
    """预览“取学生（可筛选）”的匹配范围（分页，只读，不写任何表）。"""
    err = _require_admin_json()
    if err:
        return err
    payload = request.get_json(silent=True) or {}
    raw_filters = payload.get("filters")
    filters = raw_filters if isinstance(raw_filters, dict) else {}
    include_graduated = bool(payload.get("include_graduated"))
    try:
        page_no = max(1, int(payload.get("page") or 1))
    except (TypeError, ValueError):
        page_no = 1
    try:
        size = int(payload.get("size") or 100)
    except (TypeError, ValueError):
        size = 100
    repo = district_model.DistrictRepository()
    return _ok(repo.list_students_page(filters, include_graduated, page_no, size))


@bp.route("/api/district-match/batch", methods=["POST"])
def batch():
    err = _require_admin_json()
    if err:
        return err
    if not _batch_lock.acquire(blocking=False):
        return _fail("已有批量任务在执行，请稍后再试", 409)

    staging = None
    try:
        source = "upload"
        student_ids = []
        filters = {}
        include_graduated = False
        id_card_col = address_col = None
        if request.files.get("file"):
            staging = _save_upload(request.files["file"])
            id_card_col = (request.form.get("id_card_col") or "").strip()
            address_col = (request.form.get("address_col") or "").strip()
            if not id_card_col and not address_col:
                raise ValueError("请先选择身份证列与地址列（可只选一个）")
        else:
            payload = request.get_json(silent=True) or {}
            source = payload.get("source") or "students"
            student_ids = payload.get("student_ids") or []
            raw_filters = payload.get("filters")
            filters = raw_filters if isinstance(raw_filters, dict) else {}
            include_graduated = bool(payload.get("include_graduated"))
            if source != "students":
                raise ValueError("缺少上传文件或 source 参数")
    except ValueError as exc:
        _batch_lock.release()
        return _fail(str(exc))

    pid = _new_progress("batch")
    created_by = session.get("username") or ""

    def _target(app_obj, progress_id):
        conn = district_model.get_db()
        district_model.warm_cache(conn)
        repo = district_model.DistrictRepository(conn)
        if source != "upload":
            students = repo.students_by_filter(filters, include_graduated,
                                               student_ids or None)
            total = len(students)
            if total == 0:
                raise ValueError("筛选条件下没有学生，请调整筛选范围后重试")
            batch_no = "B" + uuid.uuid4().hex[:8].upper()
            batch_id = repo.create_batch(batch_no, "students", total, created_by)
            _update_progress(progress_id, total=total, phase="匹配中")

            def on_progress(done, all_count, stats):
                _update_progress(progress_id, done=done, total=all_count,
                                 message="正在匹配第 %d/%d 条" % (done, all_count))

            try:
                stats = runner.process_batch(repo, students, batch_id,
                                             on_progress=on_progress,
                                             source="students")
                repo.update_batch(batch_id, status="done", total=total,
                                  stats_json=json.dumps(stats, ensure_ascii=False))
            except Exception:
                repo.update_batch(batch_id, status="failed")
                raise
            _update_progress(progress_id, status="done", done=total, total=total,
                             message="匹配完成", result={"batch_id": batch_id,
                                                     "batch_no": batch_no,
                                                     "stats": stats})
            return

        # 上传清单
        rows = list(importer.iter_input_rows(staging, id_card_col, address_col))
        total = len(rows)
        # 留存上传表单原始表头，导出时把匹配结果追加在这些列之后
        try:
            headers, _sample = importer.read_input_rows(staging, limit=1)
        except Exception:
            headers = []
        batch_no = "B" + uuid.uuid4().hex[:8].upper()
        batch_id = repo.create_batch(batch_no, "upload", total, created_by,
                                     source_headers=json.dumps(headers, ensure_ascii=False))
        _update_progress(progress_id, total=total, phase="匹配中")

        def on_progress(done, all_count, stats):
            _update_progress(progress_id, done=done, total=all_count,
                             message="正在匹配第 %d/%d 条" % (done, all_count))

        students = [{"student_id": None, "student_name": "",
                     "id_card": ic, "address": addr,
                     "raw_row": json.dumps(raw, ensure_ascii=False)}
                    for _n, ic, addr, raw in rows]
        stats = runner.process_batch(repo, students, batch_id,
                                     on_progress=on_progress, source="upload")
        repo.update_batch(batch_id, status="done", total=total,
                          stats_json=json.dumps(stats, ensure_ascii=False))
        _update_progress(progress_id, status="done", done=total, total=total,
                         message="匹配完成",
                         result={"batch_id": batch_id, "batch_no": batch_no,
                                 "stats": stats})
        try:
            os.remove(staging)
        except OSError:
            pass

    try:
        _spawn(pid, _target)
    except Exception:
        _batch_lock.release()
        raise

    # 线程结束时释放批量锁（放在 _target 尾部会漏掉异常分支，用看门狗线程）
    def _release_when_done():
        while True:
            time.sleep(0.5)
            with _progress_lock:
                item = _progress.get(pid)
            if item and item.get("status") != "running":
                break
        _batch_lock.release()

    threading.Thread(target=_release_when_done, daemon=True).start()
    return _ok({"progress_id": pid, "total": 0})


@bp.route("/api/district-match/progress/<progress_id>")
def progress(progress_id):
    err = _require_login_json()
    if err:
        return err
    with _progress_lock:
        item = _progress.get(progress_id)
        item = dict(item) if item else None
    if not item:
        return _fail("任务已失效，请重新发起", 404)
    return _ok(item)


@bp.route("/api/district-match/batches")
def batches():
    err = _require_login_json()
    if err:
        return err
    page_no = max(1, int(request.args.get("page") or 1))
    size = min(100, max(1, int(request.args.get("size") or 20)))
    repo = district_model.DistrictRepository()
    items = repo.list_batches(limit=size, offset=(page_no - 1) * size)
    for item in items:
        if item.get("stats_json"):
            try:
                item["stats"] = json.loads(item["stats_json"])
            except (TypeError, ValueError):
                item["stats"] = {}
    return _ok({"items": items, "total": repo.count_batches(), "page": page_no})


@bp.route("/api/district-match/batches/<int:batch_id>/results")
def results(batch_id):
    err = _require_login_json()
    if err:
        return err
    repo = district_model.DistrictRepository()
    batch = repo.get_batch(batch_id=batch_id)
    if not batch:
        return _fail("批次不存在", 404)
    page_no = max(1, int(request.args.get("page") or 1))
    size = min(200, max(1, int(request.args.get("size") or 50)))
    status = (request.args.get("status") or "").strip() or None
    items = repo.list_results(batch_id, status=status, limit=size,
                              offset=(page_no - 1) * size)
    return _ok({
        "batch": batch,
        "items": items,
        "total": repo.count_results(batch_id, status=status),
        "stats": repo.result_stats(batch_id),
        "page": page_no,
    })


@bp.route("/api/district-match/batches/<int:batch_id>/delete", methods=["POST"])
def delete_batch(batch_id):
    err = _require_admin_json()
    if err:
        return err
    repo = district_model.DistrictRepository()
    if not repo.get_batch(batch_id=batch_id):
        return _fail("批次不存在", 404)
    repo.soft_delete_batch(batch_id)
    return _ok({"batch_id": batch_id})


@bp.route("/api/district-match/batches/<int:batch_id>/export")
def export_batch(batch_id):
    err = _require_login_json()
    if err:
        return err
    repo = district_model.DistrictRepository()
    batch = repo.get_batch(batch_id=batch_id)
    if not batch:
        return _fail("批次不存在", 404)
    fmt = (request.args.get("format") or "xlsx").lower()
    if fmt not in ("xlsx", "csv"):
        return _fail("仅支持 xlsx / csv")
    rows = repo.list_results(batch_id, limit=100000, offset=0)
    if not rows:
        return _fail("该批次无可导出结果（结果可能已随上次导出清除）", 400)
    # 导出目录与 uploads 同级（即 _USER_DATA_DIR/exports），不引用 config 私有变量
    out_dir = os.path.join(os.path.dirname(current_app.config["UPLOAD_FOLDER"]), "exports")
    os.makedirs(out_dir, exist_ok=True)
    out_path = os.path.join(out_dir,
                            "district_match_%s.%s" % (batch.get("batch_no") or batch_id, fmt))
    # 上传清单批次：按原始表头输出，匹配结果追加在最后几列
    headers = []
    if batch.get("source_headers"):
        try:
            parsed = json.loads(batch["source_headers"])
            headers = parsed if isinstance(parsed, list) else []
        except (TypeError, ValueError):
            headers = []
    exporter.export_results(rows, out_path, fmt=fmt, headers=headers)
    # 导出即清除：批次记录只保留到导出前，导出完成后连同结果一并物理删除，
    # 避免 dm_match_batch / dm_match_result 无限膨胀
    repo.purge_batch(batch_id)
    return send_file(out_path, as_attachment=True,
                     download_name="区划匹配_%s.%s" % (batch.get("batch_no") or batch_id, fmt))


# ------------------------------------------------------------------ 字典
@bp.route("/api/district-match/dictionary")
def dictionary():
    err = _require_login_json()
    if err:
        return err
    data = district_model.dictionary_status()
    if request.args.get("verify") in ("1", "true"):
        path = data["pack"]["path"]
        if data["pack"]["exists"]:
            ok, detail = pack_mod.verify_pack(path)
            data["pack"]["verify_ok"] = ok
            data["pack"]["sha256"] = detail.get("sha256")
            data["pack"]["counts"] = {"total": detail.get("total"),
                                      "by_level": detail.get("by_level")}
        else:
            data["pack"]["verify_ok"] = False
    return _ok(data)


@bp.route("/api/district-match/dictionary/import", methods=["POST"])
def dictionary_import():
    err = _require_admin_json()
    if err:
        return err
    if not _import_lock.acquire(blocking=False):
        return _fail("字典导入正在进行中，请稍后再试", 409)
    pid = _new_progress("dict_import")
    pack_file = district_model.pack_path()

    def _target(app_obj, progress_id):
        _update_progress(progress_id, phase="导入中", message="正在读取数据包...")
        try:
            result = district_model.import_dictionary(
                pack_file,
                on_progress=lambda n: _update_progress(progress_id, done=n,
                                                       message="已导入 %d 条" % n))
            _update_progress(progress_id, status="done", message="导入完成",
                             result=result)
        finally:
            _import_lock.release()

    try:
        _spawn(pid, _target)
    except Exception:
        _import_lock.release()
        raise
    return _ok({"progress_id": pid, "pack": pack_file})


# ------------------------------------------------------------------ 历史映射维护
_HISTORY_HEADER_ALIASES = {
    "旧码": "old_code", "原代码": "old_code", "旧区划代码": "old_code",
    "新码": "new_code", "新区划代码": "new_code",
    "旧名": "old_name", "原名": "old_name", "旧区划名称": "old_name",
    "新名": "new_name", "新名称": "new_name", "新区划名称": "new_name",
    "变更类型": "change_type", "类型": "change_type",
    "变更日期": "change_date", "日期": "change_date",
    "备注": "remark",
}
_HISTORY_KEYS = ("old_code", "new_code", "old_name", "new_name",
                 "change_type", "change_date", "remark")


def _normalize_history_rows(rows):
    """把导入行的表头（中文或英文）归一化为历史映射字段名。"""
    out = []
    for row in rows:
        item = {}
        for key, value in (row or {}).items():
            name = str(key or "").strip()
            target = _HISTORY_HEADER_ALIASES.get(name)
            if target is None:
                lower = name.lower()
                if lower in _HISTORY_KEYS:
                    target = lower
            if target:
                item[target] = value
        out.append(item)
    return out


@bp.route("/api/district-match/history")
def history():
    """历史映射列表（登录可读，支持 search 关键字）。"""
    err = _require_login_json()
    if err:
        return err
    repo = district_model.DistrictRepository()
    items = repo.list_history(search=(request.args.get("search") or "").strip())
    return _ok({"items": items, "readonly": False})


@bp.route("/api/district-match/history", methods=["POST"])
def create_history():
    err = _require_admin_json()
    if err:
        return err
    payload = request.get_json(silent=True) or {}
    repo = district_model.DistrictRepository()
    try:
        history_id = repo.create_history(payload)
    except ValueError as exc:
        return _fail(str(exc))
    return _ok({"id": history_id})


@bp.route("/api/district-match/history/<int:history_id>/update", methods=["POST"])
def update_history(history_id):
    err = _require_admin_json()
    if err:
        return err
    payload = request.get_json(silent=True) or {}
    repo = district_model.DistrictRepository()
    try:
        repo.update_history(history_id, payload)
    except ValueError as exc:
        return _fail(str(exc), 404 if "不存在" in str(exc) else 400)
    return _ok({"id": history_id})


@bp.route("/api/district-match/history/<int:history_id>/delete", methods=["POST"])
def delete_history(history_id):
    err = _require_admin_json()
    if err:
        return err
    repo = district_model.DistrictRepository()
    try:
        repo.delete_history(history_id)
    except ValueError as exc:
        return _fail(str(exc), 404)
    return _ok({"id": history_id})


@bp.route("/api/district-match/history/import", methods=["POST"])
def import_history():
    """批量导入历史映射（xlsx / csv，中文或英文字段名）。"""
    err = _require_admin_json()
    if err:
        return err
    storage = request.files.get("file")
    if not storage or not storage.filename:
        return _fail("请选择要导入的文件")
    try:
        path = _save_upload(storage)
    except ValueError as exc:
        return _fail(str(exc))
    try:
        _headers, rows = importer.read_input_rows(path, limit=5000)
        result = district_model.DistrictRepository().bulk_import_history(
            _normalize_history_rows(rows))
    except ValueError as exc:
        return _fail(str(exc))
    finally:
        try:
            os.remove(path)
        except OSError:
            pass
    return _ok(result)


# ------------------------------------------------------------------ 4.8.1 回填 / 重新匹配
@bp.route("/api/district-match/backfill", methods=["POST"])
def backfill():
    err = _require_admin_json()
    if err:
        return err
    payload = request.get_json(silent=True) or {}
    only_empty = bool(payload.get("only_empty", True))
    try:
        limit = int(payload.get("limit") or 5000)
        after_id = int(payload.get("after_id") or 0)
    except (TypeError, ValueError):
        return _fail("limit / after_id 必须为整数")
    limit = max(1, min(5000, limit))
    after_id = max(0, after_id)
    if not _batch_lock.acquire(blocking=False):
        return _fail("已有批量任务在执行，请稍后再试", 409)
    pid = _new_progress("backfill")

    def _target(app_obj, progress_id):
        _update_progress(progress_id, phase="回填中")
        try:
            result = district_model.backfill_students(
                only_empty=only_empty, limit=limit, after_id=after_id,
                on_progress=lambda done, total: _update_progress(
                    progress_id, done=done, total=total,
                    message="已处理 %d/%d 条" % (done, total)))
            _update_progress(progress_id, status="done", message="回填完成",
                             result=result)
        finally:
            _batch_lock.release()

    try:
        _spawn(pid, _target)
    except Exception:
        _batch_lock.release()
        raise
    return _ok({"progress_id": pid, "limit": limit, "after_id": after_id,
                "only_empty": only_empty})


@bp.route("/api/district-match/pending")
def pending_students():
    """待补全学生明细：定位哪些学生的地址/证件缺失导致匹配不上。"""
    err = _require_admin_json()
    if err:
        return err
    try:
        limit = int(request.args.get("limit") or 200)
        after_id = int(request.args.get("after_id") or 0)
    except (TypeError, ValueError):
        return _fail("limit / after_id 必须为整数")
    limit = max(1, min(500, limit))
    data = district_model.list_pending_students(limit=limit,
                                                after_id=max(0, after_id))
    return _ok(data)


@bp.route("/api/district-match/students/<int:student_id>/rematch", methods=["POST"])
def rematch(student_id):
    """编辑弹窗「重新匹配」：只写 dm_match_result，不回写 students。"""
    err = _require_admin_json()
    if err:
        return err
    try:
        data = district_model.ensure_student_district(student_id, refresh=True)
    except district_model.DistrictDbMissing:
        return _fail("区划字典尚未导入，请先在“字典状态”中导入数据包", 409)
    if data is None:
        return _fail("学生不存在", 404)
    return _ok(data)


# ------------------------------------------------------------------ 编辑弹窗只读展示
@bp.route("/api/district-match/students/<int:student_id>")
def student_district(student_id):
    err = _require_login_json()
    if err:
        return err
    data = district_model.get_student_district(student_id=student_id)
    return _ok({"district": data})
