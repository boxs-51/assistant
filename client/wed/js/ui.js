export const UIRenderer = {
    // Biến đổi chuỗi bảng Markdown thô sang cấu trúc bảng HTML chuẩn
    parseMarkdownTable(text) {
        const lines = text.split("\n");
        let inTable = false;
        let html = "";
        
        for (let line of lines) {
            if (line.trim().startsWith("|")) {
                if (!inTable) {
                    inTable = true;
                    html += "<table>";
                }
                const cols = line.split("|").map(c => c.trim()).filter((c, i, a) => i > 0 && i < a.length - 1);
                if (line.includes("---")) continue;
                html += "<tr>" + cols.map(c => `<td>${c}</td>`).join("") + "</tr>";
            } else {
                if (inTable) {
                    inTable = false;
                    html += "</table>";
                }
                html += line + "<br>";
            }
        }
        if (inTable) html += "</table>";
        return html;
    },

    // Append thêm tin nhắn mới vào khung chat
    appendMessage(text, role) {
        const chatMessages = document.getElementById('chat-messages');
        const msgDiv = document.createElement('div');
        msgDiv.className = `message ${role}`;
        msgDiv.innerHTML = text;
        chatMessages.appendChild(msgDiv);
        chatMessages.scrollTop = chatMessages.scrollHeight;
        return msgDiv;
    },

    // Cập nhật thanh hiển thị file ghim
    renderContextBar(activeFiles) {
        const bar = document.getElementById('active-context-bar');
        const span = document.getElementById('active-context-files');
        const fileNames = Object.values(activeFiles);
        
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
        const userEmailSpan = document.getElementById('user-email');

        if (isLoggedIn) {
            loginModal.style.display = 'none';
            accountSection.style.display = 'block';
            apiKeysSection.style.display = 'block';
            userEmailSpan.textContent = email;
        } else {
            loginModal.style.display = 'flex';
            accountSection.style.display = 'none';
            apiKeysSection.style.display = 'none';
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
    renderFileList(files, activeFiles, onPinToggle, onFileDelete) {
        const fileListDiv = document.getElementById('file-list');
        fileListDiv.innerHTML = "";
        
        if (files.length === 0) {
            fileListDiv.innerHTML = "<div style='font-size:12px; color:var(--text-muted);'>Chưa có tệp nào.</div>";
            return;
        }

        files.forEach(f => {
            const isPinned = activeFiles[f.id] !== undefined;
            const item = document.createElement('div');
            item.className = `file-item ${isPinned ? 'pinned' : ''}`;
            item.innerHTML = `
                <span title="${f.filename}">${f.filename.substring(0, 15)}${f.filename.length > 15 ? '...' : ''}</span>
                <div class="file-actions">
                    <span class="pin" data-id="${f.id}" data-name="${f.filename}">${isPinned ? '📌' : '📎'}</span>
                    <span class="del" data-id="${f.id}">🗑️</span>
                </div>
            `;
            
            // Đóng gói Event Listener gọn gàng
            item.querySelector('.pin').addEventListener('click', () => onPinToggle(f.id, f.filename));
            item.querySelector('.del').addEventListener('click', () => onFileDelete(f.id));
            
            fileListDiv.appendChild(item);
        });
    }
};