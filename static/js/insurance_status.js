/*
 * insurance_status.js — 与 config.py 的 InsuranceStatus 镜像，登记/审批/待办三页共用。
 * 单一事实来源：所有 supervisor_flag 的存储值、年份感知格式化都从这里取，
 * 不再在前端散落硬编码中文（消除"拼错字就从待办/审批消失"的隐性 bug）。
 *
 * 注意：年份前缀统一为 {YYYY}年度（4 位年），与后端 config.InsuranceStatus 保持一致。
 * 办结态年份由 window.CURRENT_SCHOOL_YEAR 提供（由后端 render_template 注入），
 * 不取浏览器本地 new Date().getFullYear()，避免跨年办结被误标。
 */
window.InsuranceStatus = {
    UNREGISTERED: '',
    PENDING_XUEPING: '按计划办理学平险',
    PENDING_INTERN: '按计划办理实习就业险',
    EXEMPT: '不参保',
    EXTERNAL_PAY_MARKER: '新生默认首保',

    is_external_pay: function (v) {
        return v === this.EXTERNAL_PAY_MARKER;
    },

    yyyy: function (y) {
        return String(y);
    },

    done_xueping: function (y) {
        return this.yyyy(y) + '年度学平险办结';
    },

    done_intern: function (y) {
        return this.yyyy(y) + '年度实习就业险办结';
    },

    parse_flag_year: function (flag) {
        if (!flag) return null;
        var m = /(\d{4})年度/.exec(flag);
        return m ? parseInt(m[1], 10) : null;
    },

    is_done: function (flag) {
        return !!flag && flag.indexOf('办结') >= 0;
    },

    done_type: function (flag) {
        if (!flag) return null;
        if (flag.indexOf('实习就业险') >= 0 && flag.indexOf('办结') >= 0) return 'intern';
        if (flag.indexOf('学平险') >= 0 && flag.indexOf('办结') >= 0) return 'xueping';
        return null;
    }
};
