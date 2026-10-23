/*
 * insurance_common.js — 登记/审批/待办三页共用的「批量操作组件」。
 *
 * 提供 BatchController：行勾选状态管理（selection 在整棵 DOM 重渲染后依旧保留）
 * + 浮动批量工具条（根据已选人数显隐、渲染操作按钮）。各页按自己的字段/动作初始化即可，
 * 无需重复造轮子。
 *
 * 用法：
 *   var bc = InsuranceCommon.createBatchController({
 *     containerId: 'irTreeContainer',   // 承载学生行的滚动容器
 *     rowSelector: '.ir-student-row',    // 单行选择器
 *     getRowId: function(row){ return parseInt(row.dataset.studentId, 10); },
 *     toolbarId: 'irBatchBar',           // 浮动工具条元素 id（不存在则自动创建并挂到 body）
 *     actions: [ {label:'批量通过', cls:'btn-success', handler:function(ids){...}} ]
 *   });
 *   // 每次 renderTree 后调用 bc.syncCheckboxes() 以回显勾选态
 */
window.InsuranceCommon = (function () {
    function forEach(nodes, fn) {
        for (var i = 0; i < nodes.length; i++) fn(nodes[i], i);
    }

    function BatchController(cfg) {
        this.containerId = cfg.containerId;
        this.rowSelector = cfg.rowSelector || '.student-row';
        this.getRowId = cfg.getRowId || function (row) { return parseInt(row.dataset.studentId, 10); };
        this.toolbarId = cfg.toolbarId;
        this.actions = cfg.actions || [];
        this.selected = {};   // id -> true
        this._bindChange();
        this.renderToolbar();
    }

    // 行勾选变化（事件委托，兼容整棵 DOM 重渲染）
    BatchController.prototype._bindChange = function () {
        var self = this;
        var container = document.getElementById(this.containerId);
        container.addEventListener('change', function (e) {
            var cb = e.target.closest('input[type="checkbox"].ic-row-cb');
            if (!cb) return;
            var row = cb.closest(self.rowSelector);
            if (!row) return;
            var id = self.getRowId(row);
            if (cb.checked) self.selected[id] = true; else delete self.selected[id];
            self.renderToolbar();
        });
    };

    // 重新渲染树后，回显勾选态 + 重置全选框
    BatchController.prototype.syncCheckboxes = function () {
        var self = this;
        var container = document.getElementById(this.containerId);
        var boxes = container.querySelectorAll('input[type="checkbox"].ic-row-cb');
        forEach(boxes, function (cb) {
            var row = cb.closest(self.rowSelector);
            if (!row) return;
            cb.checked = !!self.selected[self.getRowId(row)];
        });
        var sels = container.querySelectorAll('input[type="checkbox"].ic-selectall');
        forEach(sels, function (cb) { cb.checked = false; });
    };

    BatchController.prototype.getSelectedIds = function () {
        var ids = [];
        for (var k in this.selected) if (this.selected.hasOwnProperty(k)) ids.push(parseInt(k, 10));
        return ids;
    };

    BatchController.prototype.clear = function () {
        this.selected = {};
        this.syncCheckboxes();
        this.renderToolbar();
    };

    // 全选/取消全选当前视图内所有可见行
    BatchController.prototype.selectAllVisible = function (on) {
        var self = this;
        var container = document.getElementById(this.containerId);
        var rows = container.querySelectorAll(this.rowSelector);
        forEach(rows, function (row) {
            var id = self.getRowId(row);
            if (on) self.selected[id] = true; else delete self.selected[id];
        });
        this.syncCheckboxes();
        this.renderToolbar();
    };

    BatchController.prototype.renderToolbar = function () {
        var self = this;
        var bar = document.getElementById(this.toolbarId);
        if (!bar) {
            bar = document.createElement('div');
            bar.id = this.toolbarId;
            bar.className = 'ic-batch-bar';
            document.body.appendChild(bar);
        }
        var ids = this.getSelectedIds();
        var html = '<div class="ic-batch-info">已选 <strong>' + ids.length + '</strong> 人</div><div class="ic-batch-actions">';
        forEach(this.actions, function (a, i) {
            var st = a.style ? ' style="' + a.style + '"' : '';
            html += '<button type="button" class="btn btn-sm ' + (a.cls || 'btn-primary') + '"' + st + ' data-act="' + i + '">' + a.label + '</button>';
        });
        html += '<button type="button" class="btn btn-sm btn-outline-light" data-act="clear">取消</button>';
        html += '</div>';
        bar.innerHTML = html;
        bar.style.display = ids.length ? 'flex' : 'none';

        var btns = bar.querySelectorAll('button[data-act]');
        forEach(btns, function (btn) {
            btn.addEventListener('click', function () {
                var act = btn.getAttribute('data-act');
                if (act === 'clear') { self.clear(); return; }
                var idx = parseInt(act, 10);
                var a = self.actions[idx];
                if (a && a.handler) a.handler(self.getSelectedIds());
            });
        });
    };

    function createBatchController(cfg) { return new BatchController(cfg); }

    return { createBatchController: createBatchController };
})();
