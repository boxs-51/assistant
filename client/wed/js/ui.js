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