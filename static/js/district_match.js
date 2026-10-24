/* 区划匹配页面脚本（外链，CSP 友好；不使用内联事件） */
(function () {
    'use strict';

    var app = document.getElementById('dm-app');
    if (!app) return;

    var URLS = {
        query: app.dataset.queryUrl,
        preview: app.dataset.previewUrl,
        batch: app.dataset.batchUrl,
        progress: app.dataset.progressUrl,
        batches: app.dataset.batchesUrl,
        students: app.dataset.studentsUrl,
        results: app.dataset.resultsUrl,
        exportUrl: app.dataset.exportUrl,
        deleteUrl: app.dataset.deleteUrl,
        dictionary: app.dataset.dictionaryUrl,
        dictImport: app.dataset.dictImportUrl,
        history: app.dataset.historyUrl,
        historyCreate: app.dataset.historyCreateUrl,
        historyUpdate: app.dataset.historyUpdateUrl,
        historyDelete: app.dataset.historyDeleteUrl,
        historyImport: app.dataset.historyImportUrl,
        backfill: app.dataset.backfillUrl
    };

    var $ = function (id) { return document.getElementById(id); };
    var statusClass = {
        '正确': 'success',
        '已解析': 'primary',
        '已解析(仅证件)': 'info',
        '需复核': 'warning',
        '部分': 'warning',
        '无匹配': 'secondary',
        '异常': 'danger'
    };

    function withId(template, id) {
        return template.replace('/0/', '/' + id + '/');
    }

    function requestJSON(url, options) {
        return fetch(url, options).then(function (resp) {
            return safeJSON(resp).then(function (body) {
                if (!resp.ok || body.success === false) {
                    var msg = (body && body.message) || ('请求失败（HTTP ' + resp.status + '）');
                    throw new Error(msg);
                }
                return body;
            });
        });
    }

    function cell(text) {
        var td = document.createElement('td');
        td.textContent = text === null || text === undefined ? '' : String(text);
        return td;
    }

    function badge(status) {
        var span = document.createElement('span');
        span.className = 'badge text-bg-' + (statusClass[status] || 'secondary');
        span.textContent = status || '未计算';
        return span;
    }

    function setBusy(button, busy, text) {
        if (!button) return;
        button.disabled = busy;
        if (busy) {
            button.dataset.original = button.textContent;
            button.textContent = text || '处理中…';
        } else if (button.dataset.original) {
            button.textContent = button.dataset.original;
        }
    }

    /* ---------------------------------------------------------- 1. 单条查询 */
    $('dm-query-form').addEventListener('submit', function (event) {
        event.preventDefault();
        var idCard = $('dm-id-card').value.trim();
        var address = $('dm-address').value.trim();
        if (!idCard && !address) {
            showToast('请输入身份证号或地址', 'warning');
            return;
        }
        var btn = $('dm-query-btn');
        setBusy(btn, true, '查询中…');
        requestJSON(URLS.query, {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({ id_card: idCard, address: address })
        }).then(function (body) {
            var data = body.data || {};
            var fields = $('dm-query-fields').querySelectorAll('[data-key]');
            Array.prototype.forEach.call(fields, function (dd) {
                dd.textContent = data[dd.dataset.key] || '-';
            });
            var summary = $('dm-query-summary');
            summary.textContent = '匹配状态：' + (data.match_status || '-') +
                '　说明：' + (data.decision_path || '-');
            $('dm-detail').textContent =
                '【引擎①】\n' + (data.engine1_detail || '-') +
                '\n\n【引擎②】\n' + (data.engine2_detail || '-');
        }).catch(function (err) {
            showToast(err.message, 'danger');
        }).finally(function () {
            setBusy(btn, false);
        });
    });

    /* ---------------------------------------------------------- 2. 批量匹配 */
    var headers = [];

    function fillSelect(select, list, selected) {
        select.innerHTML = '';
        var empty = document.createElement('option');
        empty.value = '';
        empty.textContent = '（不选择）';
        select.appendChild(empty);
        list.forEach(function (name) {
            var option = document.createElement('option');
            option.value = name;
            option.textContent = name;
            if (name === selected) option.selected = true;
            select.appendChild(option);
        });
    }

    $('dm-source-upload').addEventListener('change', toggleSource);
    $('dm-source-students').addEventListener('change', toggleSource);

    function toggleSource() {
        var isUpload = $('dm-source-upload').checked;
        $('dm-upload-panel').classList.toggle('d-none', !isUpload);
        $('dm-students-panel').classList.toggle('d-none', isUpload);
    }

    // 学生口径筛选（字段与模板导出引擎一致）
    function collectStudentFilters() {
        var map = {
            school: 'dm-f-school',
            grade: 'dm-f-grade',
            major: 'dm-f-major',
            admin_class: 'dm-f-class',
            registration_status: 'dm-f-regstatus',
            attendance_status: 'dm-f-attstatus',
            name: 'dm-f-name',
            search: 'dm-f-search'
        };
        var filters = {};
        Object.keys(map).forEach(function (key) {
            var el = $(map[key]);
            var val = el ? (el.value || '').trim() : '';
            if (val) filters[key] = val;
        });
        return filters;
    }

    /* 取学生筛选范围预览（只读分页，不影响匹配结果） */
    var stuPage = 1;
    var stuSize = 100;
    var stuPages = 1;

    function schoolName(code) {
        var sel = $('dm-f-school');
        if (!sel) return code;
        for (var i = 0; i < sel.options.length; i++) {
            if (sel.options[i].value === String(code)) return sel.options[i].textContent;
        }
        return code;
    }

    function loadStudentsPreview(page) {
        var btn = $('dm-stu-preview-btn');
        setBusy(btn, true, '加载中…');
        requestJSON(URLS.students, {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({
                page: page,
                size: stuSize,
                include_graduated: $('dm-f-graduated').checked,
                filters: collectStudentFilters()
            })
        }).then(function (body) {
            var data = body.data || {};
            var total = data.total || 0;
            stuPage = data.page || 1;
            stuPages = Math.max(1, Math.ceil(total / stuSize));
            var tbody = $('dm-stu-preview-table').querySelector('tbody');
            tbody.innerHTML = '';
            if (!total) {
                var tr = document.createElement('tr');
                var td = cell('该筛选条件下没有学生');
                td.colSpan = 9;
                td.className = 'text-center text-muted';
                tr.appendChild(td);
                tbody.appendChild(tr);
            } else {
                (data.items || []).forEach(function (item) {
                    var line = document.createElement('tr');
                    line.appendChild(cell(item.student_name));
                    line.appendChild(cell(item.student_no));
                    line.appendChild(cell(item.id_card));
                    line.appendChild(cell(schoolName(item.school)));
                    line.appendChild(cell(item.grade));
                    line.appendChild(cell(item.admin_class));
                    line.appendChild(cell(item.registration_status));
                    line.appendChild(cell(item.attendance_status));
                    line.appendChild(cell(item.address));
                    tbody.appendChild(line);
                });
            }
            $('dm-stu-page-info').textContent = total
                ? '共 ' + total + ' 条，第 ' + stuPage + '/' + stuPages + ' 页'
                : '共 0 条';
            $('dm-stu-prev').disabled = stuPage <= 1;
            $('dm-stu-next').disabled = stuPage >= stuPages;
            $('dm-stu-preview-wrap').classList.remove('d-none');
        }).catch(function (err) {
            showToast(err.message, 'danger');
        }).finally(function () {
            setBusy(btn, false);
        });
    }

    $('dm-stu-preview-btn').addEventListener('click', function () {
        loadStudentsPreview(1);
    });
    $('dm-stu-prev').addEventListener('click', function () {
        if (stuPage > 1) loadStudentsPreview(stuPage - 1);
    });
    $('dm-stu-next').addEventListener('click', function () {
        if (stuPage < stuPages) loadStudentsPreview(stuPage + 1);
    });

    $('dm-preview-btn').addEventListener('click', function () {
        var file = $('dm-file').files[0];
        if (!file) {
            showToast('请先选择文件', 'warning');
            return;
        }
        var form = new FormData();
        form.append('file', file);
        var btn = $('dm-preview-btn');
        setBusy(btn, true, '解析中…');
        requestJSON(URLS.preview, { method: 'POST', body: form }).then(function (body) {
            var data = body.data || {};
            headers = data.headers || [];
            fillSelect($('dm-col-id'), headers, data.suggest && data.suggest.id_card);
            fillSelect($('dm-col-addr'), headers, data.suggest && data.suggest.address);
            renderPreview(headers, data.rows || []);
            $('dm-preview-hint').textContent = '共读取 ' + (data.rows || []).length + ' 行预览';
        }).catch(function (err) {
            showToast(err.message, 'danger');
        }).finally(function () {
            setBusy(btn, false);
        });
    });

    function renderPreview(cols, rows) {
        var table = $('dm-preview-table');
        var thead = table.querySelector('thead');
        var tbody = table.querySelector('tbody');
        thead.innerHTML = '';
        tbody.innerHTML = '';
        var tr = document.createElement('tr');
        cols.forEach(function (name) { tr.appendChild(cell(name)); });
        thead.appendChild(tr);
        rows.forEach(function (row) {
            var line = document.createElement('tr');
            cols.forEach(function (name) { line.appendChild(cell(row[name])); });
            tbody.appendChild(line);
        });
        $('dm-preview-wrap').classList.remove('d-none');
    }

    $('dm-batch-btn').addEventListener('click', function () {
        var isUpload = $('dm-source-upload').checked;
        var btn = $('dm-batch-btn');
        var options;
        if (isUpload) {
            var file = $('dm-file').files[0];
            if (!file) {
                showToast('请先选择文件', 'warning');
                return;
            }
            var idCol = $('dm-col-id').value;
            var addrCol = $('dm-col-addr').value;
            if (!idCol && !addrCol) {
                showToast('请选择身份证列或地址列', 'warning');
                return;
            }
            var form = new FormData();
            form.append('file', file);
            form.append('id_card_col', idCol);
            form.append('address_col', addrCol);
            options = { method: 'POST', body: form };
        } else {
            options = {
                method: 'POST',
                headers: { 'Content-Type': 'application/json' },
                body: JSON.stringify({
                    source: 'students',
                    include_graduated: $('dm-f-graduated').checked,
                    filters: collectStudentFilters()
                })
            };
        }
        setBusy(btn, true, '启动中…');
        requestJSON(URLS.batch, options).then(function (body) {
            pollProgress(body.data.progress_id, function (item) {
                $('dm-progress-wrap').classList.remove('d-none');
                updateProgress(item);
            }, function (item) {
                $('dm-progress-wrap').classList.remove('d-none');
                updateProgress(item);
                if (item.status === 'error') {
                    showToast(item.message || '批量匹配失败', 'danger');
                    return;
                }
                showToast('批量匹配完成', 'success');
                if (item.result && item.result.batch_id) {
                    loadResults(item.result.batch_id);
                }
                loadBatches();
            });
        }).catch(function (err) {
            showToast(err.message, 'danger');
        }).finally(function () {
            setBusy(btn, false);
        });
    });

    function updateProgress(item) {
        var percent = item.total ? Math.round(item.done * 100 / item.total) : 0;
        var bar = $('dm-progress-bar');
        bar.style.width = percent + '%';
        bar.textContent = percent + '%';
        $('dm-progress-text').textContent =
            (item.phase ? '[' + item.phase + '] ' : '') +
            (item.message || '') + '（' + item.done + '/' + item.total + '）';
    }

    function pollProgress(progressId, onTick, onDone) {
        var url = URLS.progress.replace('__PID__', progressId);
        var timer = setInterval(function () {
            requestJSON(url, { method: 'GET' }).then(function (body) {
                var item = body.data || {};
                onTick(item);
                if (item.status !== 'running') {
                    clearInterval(timer);
                    onDone(item);
                }
            }).catch(function (err) {
                clearInterval(timer);
                showToast(err.message, 'danger');
            });
        }, 1000);
    }

    function loadResults(batchId) {
        requestJSON(withId(URLS.results, batchId) + '?size=100', { method: 'GET' })
            .then(function (body) {
                var data = body.data || {};
                var batch = data.batch || {};
                var total = data.total || 0;
                var tbody = $('dm-result-table').querySelector('tbody');
                tbody.innerHTML = '';
                if (!total) {
                    var trEmpty = document.createElement('tr');
                    var tdEmpty = cell('该批次结果已导出并清除，无剩余记录');
                    tdEmpty.colSpan = 8;
                    tdEmpty.className = 'text-center text-muted';
                    trEmpty.appendChild(tdEmpty);
                    tbody.appendChild(trEmpty);
                    $('dm-result-title').textContent = '批次 ' + (batch.batch_no || batchId) + '，结果已清除';
                    $('dm-export-link').classList.add('d-none');
                    $('dm-result-wrap').classList.remove('d-none');
                    return;
                }
                (data.items || []).forEach(function (item) {
                    var tr = document.createElement('tr');
                    tr.appendChild(cell(item.student_name));
                    tr.appendChild(cell(item.id_card));
                    tr.appendChild(cell(item.address));
                    tr.appendChild(cell(item.province_name));
                    tr.appendChild(cell(item.city_name));
                    tr.appendChild(cell(item.district_name));
                    var td = document.createElement('td');
                    td.appendChild(badge(item.match_status));
                    tr.appendChild(td);
                    tr.appendChild(cell(item.decision_path));
                    tbody.appendChild(tr);
                });
                $('dm-result-title').textContent =
                    '批次 ' + (batch.batch_no || batchId) + '，共 ' + total + ' 条，当前显示前 100 条';
                var link = $('dm-export-link');
                link.href = withId(URLS.exportUrl, batchId) + '?format=xlsx';
                link.dataset.batchId = batchId;
                link.classList.remove('d-none');
                $('dm-result-wrap').classList.remove('d-none');
            }).catch(function (err) {
                showToast(err.message, 'danger');
            });
    }

    // 导出成功后批次记录与结果均被清除：收起结果面板并刷新列表
    function afterExport() {
        $('dm-result-wrap').classList.add('d-none');
        loadBatches();
        showToast('导出完成，批次记录已清除', 'info');
    }

    $('dm-export-link').addEventListener('click', function () {
        if (!this.dataset.batchId) return;
        setTimeout(afterExport, 1500);
    });

    /* ---------------------------------------------------------- 3. 批次记录 */
    var BATCH_STATUS_TEXT = {
        running: '进行中',
        done: '已完成',
        failed: '失败'
    };
    var BATCH_SOURCE_TEXT = {
        students: '学生筛选',
        upload: '上传清单'
    };

    function loadBatches() {
        requestJSON(URLS.batches + '?size=20', { method: 'GET' }).then(function (body) {
            var data = body.data || {};
            var tbody = $('dm-batch-table').querySelector('tbody');
            tbody.innerHTML = '';
            (data.items || []).forEach(function (item) {
                var tr = document.createElement('tr');
                tr.appendChild(cell(item.batch_no));
                tr.appendChild(cell(BATCH_SOURCE_TEXT[item.source] || item.source));
                tr.appendChild(cell(item.total));
                var tdStatus = document.createElement('td');
                tdStatus.textContent = BATCH_STATUS_TEXT[item.status] || item.status || '';
                tr.appendChild(tdStatus);
                tr.appendChild(cell(item.created_by));
                tr.appendChild(cell(item.created_at));

                var td = document.createElement('td');
                td.className = 'text-end';
                var view = document.createElement('button');
                view.type = 'button';
                view.className = 'btn btn-outline-primary btn-sm me-1';
                view.textContent = '查看';
                view.addEventListener('click', function () { loadResults(item.id); });
                td.appendChild(view);

                var exportBtn = document.createElement('a');
                exportBtn.className = 'btn btn-outline-success btn-sm me-1';
                exportBtn.textContent = '导出';
                exportBtn.href = withId(URLS.exportUrl, item.id) + '?format=xlsx';
                // 导出即清除：批次记录与结果都会删除，导出后刷新列表
                exportBtn.addEventListener('click', function () {
                    setTimeout(function () {
                        afterExport();
                    }, 1500);
                });
                td.appendChild(exportBtn);

                var del = document.createElement('button');
                del.type = 'button';
                del.className = 'btn btn-outline-danger btn-sm';
                del.textContent = '删除';
                del.addEventListener('click', function () {
                    showConfirm('确认删除批次 ' + item.batch_no + '？结果将软删除。', 'danger')
                        .then(function (ok) {
                            if (!ok) return;
                            requestJSON(withId(URLS.deleteUrl, item.id), { method: 'POST' })
                                .then(function () {
                                    showToast('已删除', 'success');
                                    loadBatches();
                                    $('dm-result-wrap').classList.add('d-none');
                                }).catch(function (err) { showToast(err.message, 'danger'); });
                        });
                });
                td.appendChild(del);
                tr.appendChild(td);
                tbody.appendChild(tr);
            });
        }).catch(function (err) {
            showToast(err.message, 'danger');
        });
    }

    $('dm-batches-refresh').addEventListener('click', loadBatches);

    /* ---------------------------------------------------------- 4. 字典状态 */
    function loadDictionary(verify) {
        var url = URLS.dictionary + (verify ? '?verify=1' : '');
        requestJSON(url, { method: 'GET' }).then(function (body) {
            var data = body.data || {};
            renderDictionary(data);
        }).catch(function (err) {
            showToast(err.message, 'danger');
        });
    }

    function line(label, value) {
        var row = document.createElement('div');
        row.className = 'd-flex justify-content-between border-bottom py-1';
        var left = document.createElement('span');
        left.className = 'text-muted';
        left.textContent = label;
        var right = document.createElement('span');
        right.textContent = value;
        row.appendChild(left);
        row.appendChild(right);
        return row;
    }

    function renderDictionary(data) {
        var box = $('dm-dict-body');
        box.innerHTML = '';
        var byLevel = data.by_level || {};
        var levelText = Object.keys(byLevel).sort().map(function (key) {
            return 'L' + key + ' ' + byLevel[key];
        }).join(' / ') || '-';
        box.appendChild(line('字典库', data.db_exists ? '已就绪' : '未导入'));
        box.appendChild(line('总行数', String(data.total || 0)));
        box.appendChild(line('各级行数', levelText));
        box.appendChild(line('库文件大小', ((data.db_size || 0) / 1024 / 1024).toFixed(1) + ' MB'));
        box.appendChild(line('更新时间', data.updated_at || '-'));
        var cache = data.cache || {};
        box.appendChild(line('内存缓存', cache.ready ? (cache.rows + ' 行（L1–L4）') : '未加载'));
        var pack = data.pack || {};
        box.appendChild(line('数据包', pack.exists ? ((pack.size / 1024 / 1024).toFixed(2) + ' MB') : '缺失'));
        if (pack.sha256) {
            box.appendChild(line('sha256', pack.sha256.slice(0, 16) + '…'));
        }
        if (pack.verify_ok !== undefined) {
            box.appendChild(line('数据包校验', pack.verify_ok ? '通过（行数一致）' : '不一致'));
        }
    }

    $('dm-refresh-dict').addEventListener('click', function () { loadDictionary(true); });

    $('dm-dict-import-btn').addEventListener('click', function () {
        showConfirm('导入会清空并重灌字典表（运行期只读库），确认继续？', 'warn')
            .then(function (ok) {
                if (!ok) return;
                var btn = $('dm-dict-import-btn');
                setBusy(btn, true, '启动中…');
                requestJSON(URLS.dictImport, { method: 'POST' })
                    .then(function (body) {
                        $('dm-progress-wrap').classList.remove('d-none');
                        pollProgress(body.data.progress_id, updateProgress, function (item) {
                            updateProgress(item);
                            setBusy(btn, false);
                            if (item.status === 'error') {
                                showToast(item.message || '导入失败', 'danger');
                            } else {
                                showToast('字典导入完成', 'success');
                                loadDictionary(true);
                            }
                        });
                    }).catch(function (err) {
                        setBusy(btn, false);
                        showToast(err.message, 'danger');
                    });
            });
    });

    /* ---------------------------------------------------------- 历史映射维护 */
    var historyModal = null;

    function getHistoryModal() {
        if (!historyModal && window.bootstrap) {
            historyModal = new window.bootstrap.Modal($('dm-history-modal'));
        }
        return historyModal;
    }

    function historyKeyword() {
        return $('dm-history-search').value.trim();
    }

    function loadHistory(keyword) {
        var url = URLS.history;
        if (keyword) url += '?search=' + encodeURIComponent(keyword);
        requestJSON(url, { method: 'GET' }).then(function (body) {
            var tbody = $('dm-history-table').querySelector('tbody');
            tbody.innerHTML = '';
            var items = (body.data || {}).items || [];
            if (!items.length) {
                var empty = document.createElement('tr');
                var td = cell(keyword ? '无匹配记录' : '暂无历史映射');
                td.colSpan = 6;
                td.className = 'text-muted text-center py-3';
                empty.appendChild(td);
                tbody.appendChild(empty);
                return;
            }
            items.forEach(function (item) {
                var tr = document.createElement('tr');
                tr.appendChild(cell(item.old_code));
                tr.appendChild(cell(item.old_name));
                tr.appendChild(cell(item.new_code));
                tr.appendChild(cell(item.new_name));
                tr.appendChild(cell(item.change_type));

                var td = document.createElement('td');
                td.className = 'text-end text-nowrap';
                var edit = document.createElement('button');
                edit.type = 'button';
                edit.className = 'btn btn-outline-primary btn-sm me-1';
                edit.textContent = '编辑';
                edit.addEventListener('click', function () { openHistoryModal(item); });
                td.appendChild(edit);

                var del = document.createElement('button');
                del.type = 'button';
                del.className = 'btn btn-outline-danger btn-sm';
                del.textContent = '删除';
                del.addEventListener('click', function () {
                    var label = item.old_name || item.old_code || ('#' + item.id);
                    showConfirm('确认删除历史映射「' + label + '」？删除后不再参与匹配。', 'danger')
                        .then(function (ok) {
                            if (!ok) return;
                            requestJSON(withId(URLS.historyDelete, item.id), { method: 'POST' })
                                .then(function () {
                                    showToast('已删除', 'success');
                                    loadHistory(historyKeyword());
                                }).catch(function (err) { showToast(err.message, 'danger'); });
                        });
                });
                td.appendChild(del);
                tr.appendChild(td);
                tbody.appendChild(tr);
            });
        }).catch(function (err) {
            showToast(err.message, 'danger');
        });
    }

    function openHistoryModal(item) {
        item = item || {};
        $('dm-h-id').value = item.id || '';
        $('dm-h-old-code').value = item.old_code || '';
        $('dm-h-new-code').value = item.new_code || '';
        $('dm-h-old-name').value = item.old_name || '';
        $('dm-h-new-name').value = item.new_name || '';
        $('dm-h-change-type').value = item.change_type || '';
        $('dm-h-change-date').value = item.change_date || '';
        $('dm-h-remark').value = item.remark || '';
        $('dm-history-modal-title').textContent = item.id ? '编辑历史映射' : '新增历史映射';
        var modal = getHistoryModal();
        if (modal) modal.show();
    }

    $('dm-history-add-btn').addEventListener('click', function () { openHistoryModal(null); });
    $('dm-history-search-btn').addEventListener('click', function () { loadHistory(historyKeyword()); });
    $('dm-history-search').addEventListener('keydown', function (event) {
        if (event.key === 'Enter') {
            event.preventDefault();
            loadHistory(historyKeyword());
        }
    });

    $('dm-history-save-btn').addEventListener('click', function () {
        var id = $('dm-h-id').value;
        var payload = {
            old_code: $('dm-h-old-code').value.trim(),
            new_code: $('dm-h-new-code').value.trim(),
            old_name: $('dm-h-old-name').value.trim(),
            new_name: $('dm-h-new-name').value.trim(),
            change_type: $('dm-h-change-type').value.trim(),
            change_date: $('dm-h-change-date').value.trim(),
            remark: $('dm-h-remark').value.trim()
        };
        if (!((payload.old_code && payload.new_code) ||
              (payload.old_name && payload.new_name))) {
            showToast('旧码/新码 或 旧名/新名 需成对填写', 'warning');
            return;
        }
        var btn = $('dm-history-save-btn');
        setBusy(btn, true, '保存中…');
        requestJSON(id ? withId(URLS.historyUpdate, id) : URLS.historyCreate, {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify(payload)
        }).then(function () {
            showToast('已保存', 'success');
            var modal = getHistoryModal();
            if (modal) modal.hide();
            loadHistory(historyKeyword());
        }).catch(function (err) {
            showToast(err.message, 'danger');
        }).finally(function () {
            setBusy(btn, false);
        });
    });

    $('dm-history-import-btn').addEventListener('click', function () { $('dm-history-file').click(); });

    $('dm-history-file').addEventListener('change', function () {
        var file = this.files[0];
        if (!file) return;
        var form = new FormData();
        form.append('file', file);
        var btn = $('dm-history-import-btn');
        setBusy(btn, true, '导入中…');
        requestJSON(URLS.historyImport, { method: 'POST', body: form }).then(function (body) {
            var data = body.data || {};
            showToast('导入完成：新增 ' + (data.created || 0) + ' 条，更新 ' +
                (data.updated || 0) + ' 条，跳过 ' + (data.skipped || 0) + ' 条', 'success');
            loadHistory(historyKeyword());
        }).catch(function (err) {
            showToast(err.message, 'danger');
        }).finally(function () {
            setBusy(btn, false);
            $('dm-history-file').value = '';
        });
    });

    /* ---------------------------------------------------------- 批量补全 */
    var backfillCursor = 0;   // 续跑游标：本次处理到的最大 id，下次从它之后继续
    $('dm-backfill-btn').addEventListener('click', function () {
        showConfirm('将为“区划信息不完整”的学生补全 7 列（单次最多 5000 条，自动按游标续跑，不改更新时间）。确认继续？', 'warn')
            .then(function (ok) {
                if (!ok) return;
                var btn = $('dm-backfill-btn');
                setBusy(btn, true, '启动中…');
                requestJSON(URLS.backfill, {
                    method: 'POST',
                    headers: { 'Content-Type': 'application/json' },
                    body: JSON.stringify({ only_empty: true, limit: 5000, after_id: backfillCursor })
                }).then(function (body) {
                    $('dm-progress-wrap').classList.remove('d-none');
                    pollProgress(body.data.progress_id, updateProgress, function (item) {
                        updateProgress(item);
                        setBusy(btn, false);
                        if (item.status === 'error') {
                            showToast(item.message || '回填失败', 'danger');
                            return;
                        }
                        var res = item.result || {};
                        var pending = res.pending_total || 0;
                        var text = '本次处理 ' + (res.total || 0) + ' 条，更新 ' +
                            (res.updated || 0) + ' 条';
                        backfillCursor = res.last_id || 0;
                        if (res.remaining > 0) {
                            text += '，剩余 ' + pending + ' 条待补全，可再次点击继续';
                        } else if (pending > 0) {
                            backfillCursor = 0;   // 本轮已扫完，重置游标
                            text += '；本轮已扫描完，仍有 ' + pending +
                                ' 条无法自动匹配（地址/证件缺失），可重试';
                        } else {
                            backfillCursor = 0;
                            text += '，区划信息已全部补全';
                        }
                        $('dm-progress-text').textContent = text;
                        showToast(text, 'success');
                    });
                }).catch(function (err) {
                    setBusy(btn, false);
                    showToast(err.message, 'danger');
                });
            });
    });

    loadBatches();
    loadDictionary(false);
    loadHistory();
})();
