document.addEventListener('DOMContentLoaded', function() {
    var flashData = document.getElementById('flashMessagesData');
    if (flashData) {
        try {
            var messages = JSON.parse(flashData.textContent);
            messages.forEach(function(msg) {
                showToast(msg[1], msg[0]);
            });
            flashData.remove();
        } catch (e) {
            console.error('Flash messages parse error:', e);
        }
    }

    var forms = document.querySelectorAll('form[data-confirm]');
    forms.forEach(function(form) {
        form.addEventListener('submit', function(e) {
            e.preventDefault();
            var message = form.getAttribute('data-confirm');
            var self = this;
            showConfirm(message, 'warn').then(function(result) {
                if (result) {
                    self.submit();
                }
            });
        });
    });

    var deleteButtons = document.querySelectorAll('[data-delete-confirm]');
    deleteButtons.forEach(function(button) {
        button.addEventListener('click', function(e) {
            e.preventDefault();
            e.stopPropagation();
            var message = button.getAttribute('data-delete-confirm');
            if (message) {
                var self = this;
                showConfirm(message, 'danger').then(function(result) {
                    if (result) {
                        if (self.tagName === 'A') {
                            window.location.href = self.href;
                        } else {
                            self.click();
                        }
                    }
                });
            }
        });
    });
});

function showLoading() {
    var loadingHtml = '<div class="modal fade" id="loadingModal" data-bs-backdrop="static" data-bs-keyboard="false">' +
        '<div class="modal-dialog modal-dialog-centered">' +
        '<div class="modal-content text-center p-4">' +
        '<div class="spinner-border text-primary" role="status">' +
        '<span class="visually-hidden">加载中...</span></div>' +
        '<p class="mt-3 mb-0">处理中，请稍候...</p>' +
        '</div></div></div>';

    document.body.insertAdjacentHTML('beforeend', loadingHtml);
    var loadingModal = new bootstrap.Modal(document.getElementById('loadingModal'));
    loadingModal.show();

    return loadingModal;
}

function hideLoading(loadingModal) {
    if (loadingModal) {
        loadingModal.hide();
        setTimeout(function() {
            var modalElement = document.getElementById('loadingModal');
            if (modalElement) {
                modalElement.remove();
            }
        }, 200);
    }
}

function getToastContainer() {
    var container = document.getElementById('toastContainer');
    if (!container) {
        container = document.createElement('div');
        container.id = 'toastContainer';
        document.body.appendChild(container);
    }
    return container;
}

function showToast(message, type) {
    type = type || 'info';
    var iconMap = {
        success: 'bi-check-circle-fill',
        danger: 'bi-x-circle-fill',
        warning: 'bi-exclamation-triangle-fill',
        info: 'bi-info-circle-fill'
    };
    var icon = iconMap[type] || iconMap.info;

    var container = getToastContainer();

    var item = document.createElement('div');
    item.className = 'toast-item toast-' + type;
    item.innerHTML = '<i class="bi ' + icon + ' toast-icon"></i>' +
        '<span class="toast-msg">' + escapeHtml(message) + '</span>' +
        '<button class="toast-close">&times;</button>';

    var closeBtn = item.querySelector('.toast-close');
    closeBtn.addEventListener('click', function() {
        removeToast(item);
    });

    item.addEventListener('click', function(e) {
        if (e.target !== closeBtn) {
            removeToast(item);
        }
    });

    container.appendChild(item);

    var autoRemove = setTimeout(function() {
        removeToast(item);
    }, 5000);

    item._autoRemove = autoRemove;
}

function removeToast(item) {
    if (item._removing) return;
    item._removing = true;
    if (item._autoRemove) {
        clearTimeout(item._autoRemove);
    }
    item.classList.add('toast-removing');
    setTimeout(function() {
        if (item.parentNode) {
            item.parentNode.removeChild(item);
        }
    }, 300);
}

function showConfirm(message, type) {
    type = type || 'warn';
    var iconMap = {
        warn: 'bi-exclamation-triangle-fill warn',
        danger: 'bi-exclamation-octagon-fill danger'
    };
    var iconClass = iconMap[type] || iconMap.warn;

    var existingModal = document.getElementById('confirmModal');
    if (existingModal) {
        existingModal.remove();
    }

    var modalHtml = '<div class="modal fade" id="confirmModal" tabindex="-1">' +
        '<div class="modal-dialog modal-dialog-centered">' +
        '<div class="modal-content border-0 shadow">' +
        '<div class="modal-body text-center p-4">' +
        '<div class="confirm-icon ' + iconClass + '">' +
        '<i class="bi ' + iconClass.replace(' warn', '').replace(' danger', '') + '"></i>' +
        '</div>' +
        '<p class="confirm-message mt-2">' + escapeHtml(message) + '</p>' +
        '</div>' +
        '<div class="modal-footer justify-content-center border-0 pt-0">' +
        '<button type="button" class="btn btn-secondary px-4" id="confirmCancelBtn">取消</button>' +
        '<button type="button" class="btn btn-primary px-4" id="confirmOkBtn">确定</button>' +
        '</div>' +
        '</div></div></div>';

    document.body.insertAdjacentHTML('beforeend', modalHtml);

    var modalElement = document.getElementById('confirmModal');
    var modal = new bootstrap.Modal(modalElement, {
        backdrop: 'static',
        keyboard: false
    });

    return new Promise(function(resolve) {
        var resolved = false;

        function done(result) {
            if (resolved) return;
            resolved = true;
            modal.hide();
            setTimeout(function() {
                if (modalElement.parentNode) {
                    modalElement.remove();
                }
            }, 200);
            resolve(result);
        }

        document.getElementById('confirmOkBtn').addEventListener('click', function() {
            done(true);
        });

        document.getElementById('confirmCancelBtn').addEventListener('click', function() {
            done(false);
        });

        modalElement.addEventListener('hidden.bs.modal', function() {
            done(false);
        });

        modal.show();

        setTimeout(function() {
            document.getElementById('confirmOkBtn').focus();
        }, 300);
    });
}

function formatNumber(num) {
    return num.toString().replace(/\B(?=(\d{3})+(?!\d))/g, ",");
}

function formatDate(dateString) {
    if (!dateString) return '-';
    var date = new Date(dateString);
    return date.toLocaleDateString('zh-CN');
}

function debounce(func, wait) {
    var timeout;
    return function executedFunction() {
        var args = arguments;
        var later = function() {
            clearTimeout(timeout);
            func.apply(null, args);
        };
        clearTimeout(timeout);
        timeout = setTimeout(later, wait);
    };
}

function escapeHtml(text) {
    var map = {
        '&': '&amp;',
        '<': '&lt;',
        '>': '&gt;',
        '"': '&quot;',
        "'": '&#039;'
    };
    return String(text).replace(/[&<>"']/g, function(m) { return map[m]; });
}

function validateIdCard(idCard) {
    if (!idCard || idCard.length !== 18) {
        return false;
    }

    var pattern = /^[1-9]\d{5}(18|19|20)\d{2}(0[1-9]|1[0-2])(0[1-9]|[12]\d|3[01])\d{3}[\dXx]$/;
    return pattern.test(idCard);
}

function validatePhone(phone) {
    if (!phone) return true;

    phone = phone.replace(/[\s\-]/g, '');

    if (!/^1\d{10}$/.test(phone)) {
        return false;
    }

    var validPrefixes = [
        '134', '135', '136', '137', '138', '139',
        '147', '148',
        '150', '151', '152', '157', '158', '159',
        '165',
        '172', '178',
        '182', '183', '184', '187', '188',
        '195', '198',
        '130', '131', '132',
        '145', '146',
        '155', '156',
        '166',
        '171', '175', '176',
        '185', '186',
        '196',
        '133',
        '149',
        '153',
        '162',
        '173', '174', '177',
        '180', '181', '189',
        '190', '191', '193', '199',
        '192',
        '170', '171'
    ];

    var prefix = phone.substring(0, 3);

    if (validPrefixes.indexOf(prefix) !== -1) {
        return true;
    }

    var secondDigit = phone.charAt(1);
    if (secondDigit >= '3' && secondDigit <= '9') {
        return true;
    }

    return false;
}

function copyToClipboard(text) {
    if (navigator.clipboard && navigator.clipboard.writeText) {
        navigator.clipboard.writeText(text).then(function() {
            showToast('已复制到剪贴板', 'success');
        }).catch(function(err) {
            console.error('复制失败:', err);
            showToast('复制失败', 'danger');
        });
    } else {
        var textArea = document.createElement('textarea');
        textArea.value = text;
        textArea.style.position = 'fixed';
        textArea.style.left = '-999999px';
        document.body.appendChild(textArea);
        textArea.select();

        try {
            document.execCommand('copy');
            showToast('已复制到剪贴板', 'success');
        } catch (err) {
            console.error('复制失败:', err);
            showToast('复制失败', 'danger');
        }

        document.body.removeChild(textArea);
    }
}

function downloadFile(url, filename) {
    var link = document.createElement('a');
    link.href = url;
    link.download = filename || '';
    document.body.appendChild(link);
    link.click();
    document.body.removeChild(link);
}

function printPage() {
    window.print();
}

function toggleFullScreen() {
    if (!document.fullscreenElement) {
        document.documentElement.requestFullscreen().catch(function(err) {
            console.error('全屏模式失败:', err);
            showToast('全屏模式失败', 'danger');
        });
    } else {
        if (document.exitFullscreen) {
            document.exitFullscreen();
        }
    }
}

function refreshPage() {
    location.reload();
}

function goBack() {
    if (window.history.length > 1) {
        window.history.back();
    } else {
        window.location.href = '/';
    }
}

function safeJSON(response) {
    var ct = response.headers.get('content-type') || '';
    if (ct.indexOf('application/json') !== -1) {
        return response.json();
    }
    return response.text().then(function(text) {
        throw new Error('服务器返回异常：会话可能已过期，请刷新页面后重试');
    });
}

function loadPhotoToElement(idCard, imgSelector, placeholderSelector) {
    if (!idCard) return;
    var img = typeof imgSelector === 'string' ? document.querySelector(imgSelector) : imgSelector;
    var placeholder = typeof placeholderSelector === 'string' ? document.querySelector(placeholderSelector) : placeholderSelector;

    if (!img) return;

    var photoUrl = '/api/photos/' + encodeURIComponent(idCard) + '?t=' + new Date().getTime();
    img.src = photoUrl;
    img.style.display = '';
    if (placeholder) placeholder.style.display = 'none';

    img.onerror = function() {
        img.style.display = 'none';
        if (placeholder) placeholder.style.display = '';
    };
}

function savePhotoToServer(idCard, photoBase64) {
    return fetch('/api/photos/save', {
        method: 'POST',
        headers: {'Content-Type': 'application/json'},
        body: JSON.stringify({ id_card: idCard, photo_base64: photoBase64 })
    }).then(function(r) { return r.json(); });
}

function deletePhotoFromServer(idCard) {
    return fetch('/api/photos/delete/' + encodeURIComponent(idCard), {
        method: 'DELETE'
    }).then(function(r) { return r.json(); });
}

function getPhotoCount() {
    return fetch('/api/photos/count').then(function(r) { return r.json(); });
}
