export const UIRenderer = {
    // Sử dụng thư viện marked.js để phân tích Markdown
    // This object is stateful because of how marked.js is configured.
    parseMarkdown(text) {
        // Cấu hình marked để tương thích với highlight.js
        marked.setOptions({
            highlight: function(code, lang) {
                const language = hljs.getLanguage(lang) ? lang : 'plaintext';
                return hljs.highlight(code, { language }).value;
            },
            langPrefix: 'hljs language-', // class prefix for syntax highlighting
        });
        return marked.parse(text);
    },

    // Append thêm tin nhắn mới vào khung chat
    appendMessage(text, role, isStreaming = false) {
        const chatMessages = document.getElementById('chat-messages');
        const msgDiv = document.createElement('div');
        msgDiv.className = `message ${role}`;
        msgDiv.innerHTML = text; // Initial raw text

        // Phân tích Markdown cho tin nhắn hoàn chỉnh, không phải streaming
        if (!isStreaming) {
            msgDiv.innerHTML = this.parseMarkdown(text);
            // Áp dụng highlight cho các khối mã sau khi thêm vào DOM
            msgDiv.querySelectorAll('pre code').forEach((block) => {
                hljs.highlightElement(block); // Use highlightElement for safety
            });
        } else {
            msgDiv.innerHTML = text; // Sẽ được cập nhật bởi stream handler
        }

        chatMessages.appendChild(msgDiv);
        chatMessages.scrollTop = chatMessages.scrollHeight;
        return msgDiv;
    },

    // Cập nhật thanh hiển thị file ghim
    renderContextBar(attachedFiles) {
        const bar = document.getElementById('active-context-bar');
        const span = document.getElementById('active-context-files');
        const fileNames = attachedFiles.map(f => f.name);

        if (fileNames.length > 0) {
            bar.style.display = "block";
            span.textContent = fileNames.join(", ");
        } else {
            bar.style.display = "none";
        }
    },

    // Cập nhật giao diện dựa trên trạng thái đăng nhập
    updateLoginState(isLoggedIn, email = '') {
        const loginModal = document.getElementById('login-modal-overlay');
        const accountSection = document.getElementById('account-section');
        const apiKeysSection = document.getElementById('api-keys-section');
        const authForm = document.getElementById('auth-form');
        const otpForm = document.getElementById('otp-form');
        const userEmailSpan = document.getElementById('user-email');

        if (isLoggedIn) {
            loginModal.style.display = 'none';
            accountSection.style.display = 'block';
            apiKeysSection.style.display = 'block';
            userEmailSpan.textContent = email;
            authForm.style.display = 'flex';
            otpForm.style.display = 'none';
        } else {
            loginModal.style.display = 'flex';
            accountSection.style.display = 'none';
            apiKeysSection.style.display = 'none';
            authForm.style.display = 'flex';
            otpForm.style.display = 'none';
        }
    },

    showOtpView(show = true, email = '') {
        document.getElementById('auth-form').style.display = show ? 'none' : 'flex';
        document.getElementById('otp-form').style.display = show ? 'flex' : 'none';
        if (show) {
            document.getElementById('otp-email-display').textContent = email;
        }
    },

    // Render danh sách API Keys
    renderApiKeyList(keys, onRevoke) {
        const listDiv = document.getElementById('api-key-list');
        listDiv.innerHTML = "";

        if (!keys || keys.length === 0) {
            listDiv.innerHTML = "<div style='font-size:12px; color:var(--text-muted);'>Chưa có API key nào.</div>";
            return;
        }

        keys.forEach(key => {
            const item = document.createElement('div');
            item.className = 'api-key-item';
            item.innerHTML = `
                <span>${key.name} (${key.prefix}...)</span>
                <span class="del" data-id="${key.id}" title="Thu hồi key">🗑️</span>
            `;
            item.querySelector('.del').addEventListener('click', (e) => {
                onRevoke(e.target.dataset.id);
            });
            listDiv.appendChild(item);
        });
    },

    // Render khu vực danh sách file bên sidebar
    renderFileList(files, onFileDelete) {
        const fileListDiv = document.getElementById('file-list');
        fileListDiv.innerHTML = "";

        if (files.length === 0) {
            fileListDiv.innerHTML = "<div style='font-size:12px; color:var(--text-muted);'>Chưa có tệp nào.</div>";
            return;
        }

        files.forEach((f, index) => {
            const item = document.createElement('div');
            item.className = `file-item pinned`; // All attached files are "pinned"
            item.innerHTML = `
                <span title="${f.name}">${f.name.substring(0, 20)}${f.name.length > 20 ? '...' : ''}</span>
                <div class="file-actions">
                    <span class="del" data-index="${index}" title="Gỡ tệp">🗑️</span>
                </div>
            `;
            
            // Đóng gói Event Listener gọn gàng
            item.querySelector('.del').addEventListener('click', () => onFileDelete(index));
            
            fileListDiv.appendChild(item);
        });
    }
};

export const StreamManager = {
    currentStreamElement: null,
    fullText: '',

    startStream(element) {
        this.currentStreamElement = element;
        this.fullText = '';
        // Xóa nội dung cũ và thêm con trỏ
        this.currentStreamElement.innerHTML = '<span class="streaming-cursor"></span>';
    },

    updateStream(chunk) {
        if (!this.currentStreamElement) return;
        this.fullText += chunk;
        // Cập nhật nội dung đã parse + con trỏ ở cuối
        this.currentStreamElement.innerHTML = UIRenderer.parseMarkdown(this.fullText) + '<span class="streaming-cursor"></span>';
        // Highlight code blocks on the fly
        this.currentStreamElement.querySelectorAll('pre code:not(.hljs)').forEach(hljs.highlightElement);
        // Cuộn xuống dưới
        const chatMessages = document.getElementById('chat-messages');
        chatMessages.scrollTop = chatMessages.scrollHeight;
    },

    endStream() {
        if (!this.currentStreamElement) return;
        // Hiển thị nội dung cuối cùng không có con trỏ
        this.currentStreamElement.innerHTML = UIRenderer.parseMarkdown(this.fullText);
        // Highlight lại lần cuối để đảm bảo
        this.currentStreamElement.querySelectorAll('pre code').forEach(hljs.highlightElement);
        this.currentStreamElement = null;
        this.fullText = '';
    },

    renderImage(base64Data, mimeType) {
        const img = document.createElement('img');
        img.src = `data:${mimeType};base64,${base64Data}`;
        
        // Hiển thị ở cả 2 nơi: trong tin nhắn và ở khung ảnh riêng
        if (this.currentStreamElement) {
            this.currentStreamElement.appendChild(img.cloneNode());
        }
        document.getElementById('image-label').innerHTML = '';
        document.getElementById('image-label').appendChild(img);
    }
};