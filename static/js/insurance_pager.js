/* 保险模块通用前端分页组件：所有保险页面学生/记录列表统一每页 20 条。 */
(function () {
    var PAGE_SIZE = 20;

    // 计算当前页边界：total=总条数，page=期望页码
    function clamp(total, page) {
        var pages = Math.max(1, Math.ceil(total / PAGE_SIZE));
        var cur = Math.min(Math.max(1, page || 1), pages);
        return { page: cur, pages: pages, start: (cur - 1) * PAGE_SIZE, end: cur * PAGE_SIZE };
    }

    // 页码窗口：首页、末页、当前页 ±2，其余折叠为省略号
    function pageWindow(page, pages) {
        var nums = [1, pages];
        for (var p = page - 2; p <= page + 2; p += 1) {
            if (p >= 1 && p <= pages) { nums.push(p); }
        }
        nums = nums.filter(function (v, i, arr) { return arr.indexOf(v) === i; })
                   .sort(function (a, b) { return a - b; });
        var result = [];
        for (var i = 0; i < nums.length; i += 1) {
            if (i > 0 && nums[i] - nums[i - 1] > 1) { result.push('...'); }
            result.push(nums[i]);
        }
        return result;
    }

    function pagerHtml(page, pages) {
        var out = '<li class="page-item' + (page <= 1 ? ' disabled' : '') + '">'
            + '<a class="page-link" href="#" data-p="' + (page - 1) + '">上一页</a></li>';
        pageWindow(page, pages).forEach(function (p) {
            if (p === '...') {
                out += '<li class="page-item disabled"><span class="page-link">…</span></li>';
            } else if (p === page) {
                out += '<li class="page-item active"><span class="page-link">' + p + '</span></li>';
            } else {
                out += '<li class="page-item"><a class="page-link" href="#" data-p="' + p + '">'
                    + p + '</a></li>';
            }
        });
        out += '<li class="page-item' + (page >= pages ? ' disabled' : '') + '">'
            + '<a class="page-link" href="#" data-p="' + (page + 1) + '">下一页</a></li>';
        return out;
    }

    // 渲染分页栏：el 为 <ul class="pagination">，onGo 接收目标页码
    function mount(el, page, pages, onGo) {
        if (!el) { return; }
        if (pages <= 1) {
            el.innerHTML = '';
            el.classList.add('d-none');
            return;
        }
        el.classList.remove('d-none');
        el.innerHTML = pagerHtml(page, pages);
        el.onclick = function (event) {
            var link = event.target.closest('a.page-link');
            if (!link) { return; }
            event.preventDefault();
            var target = parseInt(link.getAttribute('data-p'), 10);
            if (!target || target === page) { return; }
            onGo(target);
        };
    }

    window.InsurancePager = {
        PAGE_SIZE: PAGE_SIZE,
        clamp: clamp,
        mount: mount,
        pageWindow: pageWindow
    };
})();
