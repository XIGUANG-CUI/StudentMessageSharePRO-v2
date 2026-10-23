var HuashiCardReader = function() {
    this.ws = null;
    this.isConnected = false;
    this.callback = null;

    this.initConnection = function() {
        var self = this;
        if (self.ws && (self.ws.readyState === WebSocket.OPEN || self.ws.readyState === WebSocket.CONNECTING)) {
            return;
        }
        var wsUrl = (window.CARD_READER_WS_URL || 'ws://127.0.0.1:9001');
        console.log('华视WebSocket连接:', wsUrl);
        self.ws = new WebSocket(wsUrl);

        self.ws.onopen = function() {
            console.log('华视WebSocket已连接');
            self.isConnected = true;
        };

        self.ws.onmessage = function(event) {
            console.log('华视消息:', event.data);
            if (self.callback) {
                self.callback(event.data);
                self.callback = null;
            }
        };

        self.ws.onerror = function() {
            self.isConnected = false;
        };

        self.ws.onclose = function() {
            self.isConnected = false;
        };
    };

    this.readIDCard = function(timeout) {
        var self = this;
        timeout = timeout || 10000;
        return new Promise(function(resolve, reject) {
            if (!self.isConnected || self.ws.readyState !== WebSocket.OPEN) {
                self.initConnection();
                var checkInterval = setInterval(function() {
                    if (self.isConnected && self.ws.readyState === WebSocket.OPEN) {
                        clearInterval(checkInterval);
                        self._executeRead(timeout, resolve, reject);
                    }
                }, 100);
                setTimeout(function() {
                    clearInterval(checkInterval);
                    reject(new Error('WebSocket连接超时，请确保华视监控软件已运行'));
                }, 5000);
            } else {
                self._executeRead(timeout, resolve, reject);
            }
        });
    };

    this._executeRead = function(timeout, resolve, reject) {
        var self = this;
        self.callback = function(data) {
            if (data === 'failed to obtain ID card information') {
                reject(new Error('寻卡失败，请放置身份证到读卡器上'));
            } else if (data.indexOf('|') !== -1) {
                var cardData = self._parseCardData(data);
                if (cardData) {
                    resolve(cardData);
                } else {
                    reject(new Error('身份证数据解析失败'));
                }
            } else {
                reject(new Error('未知响应: ' + data));
            }
        };
        var command = '03?timeout=' + timeout;
        console.log('发送读卡指令:', command);
        self.ws.send(command);
        setTimeout(function() {
            if (self.callback) {
                self.callback = null;
                reject(new Error('读卡超时 (' + timeout + 'ms)，请检查身份证是否放置正确'));
            }
        }, timeout + 1000);
    };

    this._parseCardData = function(dataString) {
        var parts = dataString.split('|');
        if (parts.length < 17) {
            console.error('数据字段不足:', parts.length);
            return null;
        }
        return {
            name: parts[3] || '',
            gender: parts[4] || '',
            nation: parts[5] || '',
            birthday: parts[6] || '',
            address: parts[7] || '',
            id_card: parts[8] || '',
            issue_authority: parts[9] || '',
            valid_start: parts[10] || '',
            valid_end: parts[11] || '',
            photo_base64: parts[19] || ''
        };
    };

    this.disconnect = function() {
        if (this.ws) {
            this.ws.close();
            this.ws = null;
            this.isConnected = false;
        }
    };

    this.initConnection();
};
