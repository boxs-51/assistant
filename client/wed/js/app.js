import { AppState } from './config.js';
import { GatewayAPI } from './api.js';
import { UIRenderer } from './ui.js';

// --- LUỒNG QUẢN LÝ TỆP ---
async function loadFileList() {
    try {
        const response = await GatewayAPI.listFiles(AppState.getProvider());
        if (response.ok) {
            const files = await response.json();
            UIRenderer.renderFileList(files, AppState.activeContextFiles, togglePinFile, deleteFile);
        }
    } catch (err) {
        console.error("Lỗi đồng bộ danh sách file:", err);
    }
}

function togglePinFile(id, filename) {
    if (AppState.activeContextFiles[id]) {
        delete AppState.activeContextFiles[id];
    } else {
        AppState.activeContextFiles[id] = filename;
    }
    UIRenderer.renderContextBar(AppState.activeContextFiles);
    loadFileList();
}

async function deleteFile(id) {
    if (!confirm("Xóa file này khỏi hệ thống Cloud Provider?")) return;
    try {
        const res = await GatewayAPI.deleteFile(id, AppState.getProvider());
        if (res.ok) {
            delete AppState.activeContextFiles[id];
            UIRenderer.renderContextBar(AppState.activeContextFiles);
            loadFileList();
        }
    } catch (err) {
        alert("Lỗi kết nối khi xóa file.");
    }
}

async function handleFileUpload(e) {
    const file = e.target.files[0];
    if (!file) return;

    try {
        const res = await GatewayAPI.uploadFile(file, AppState.getProvider());
        if (res.ok) {
            const data = await res.json();
            const fileId = data.id || data.name;
            AppState.activeContextFiles[fileId] = file.name;
            UIRenderer.renderContextBar(AppState.activeContextFiles);
            loadFileList();
        }
    } catch (err) {
        alert("Upload file thất bại.");
    }
}

// --- LUỒNG CHAT STREAM MULTIMODAL ---
async function sendMessage() {
    const input = document.getElementById('message-input');
    const promptText = input.value.trim();
    if (!promptText) return;

    UIRenderer.appendMessage(promptText, 'user');
    input.value = "";

    // Đóng gói cấu trúc payload đa phương tiện gửi đi
    let currentMsg = { "role": "user" };
    const fileIds = Object.keys(AppState.activeContextFiles);

    if (fileIds.length > 0) {
        let contentParts = [{ "type": "text", "text": promptText }];
        fileIds.forEach(id => {
            contentParts.push({
                "type": "file",
                "file": { "id": id, "mime_type": "application/octet-stream" }
            });
        });
        currentMsg["content"] = contentParts;
    } else {
        currentMsg["content"] = promptText;
    }

    AppState.conversationHistory.push(currentMsg);
    const aiBubble = UIRenderer.appendMessage("...", 'assistant');

    try {
        const chatPayload = {
            "model": AppState.getModel(),
            "provider": AppState.getProvider(),
            "messages": AppState.conversationHistory,
            "config": { "stream": true }
        };

        const response = await GatewayAPI.postChatCompletion(chatPayload);
        if (!response.ok) {
            aiBubble.textContent = `❌ Lỗi (${response.status})`;
            AppState.conversationHistory.pop();
            return;
        }

        const reader = response.body.getReader();
        const decoder = new TextDecoder("utf-8");
        let aiFullReply = "";

        while (true) {
            const { value, done } = await reader.read();
            if (done) break;

            const chunk = decoder.decode(value, { stream: true });
            const lines = chunk.split("\n");

            for (let line of lines) {
                line = line.trim();
                if (!line || !line.startsWith("data:")) continue;

                const dataContent = line.replace("data:", "").trim();
                if (dataContent === "[DONE]") break;

                try {
                    const chunkJson = JSON.parse(dataContent);
                    const content = chunkJson.choices?.[0]?.delta?.content || "";
                    if (content) {
                        aiFullReply += content;
                        aiBubble.innerHTML = UIRenderer.parseMarkdownTable(aiFullReply);
                    }
                } catch (e) { }
            }
        }
        AppState.conversationHistory.push({ "role": "assistant", "content": aiFullReply });
    } catch (err) {
        aiBubble.textContent = `❌ Lỗi kết nối: ${err.message}`;
        AppState.conversationHistory.pop();
    }
}

// --- KHỞI TẠO BẮT SỰ KIỆN KHI APP READY ---
document.addEventListener("DOMContentLoaded", () => {
    loadFileList();

    document.getElementById('send-btn').addEventListener('click', sendMessage);
    document.getElementById('file-uploader').addEventListener('change', handleFileUpload);
    document.getElementById('provider-select').addEventListener('change', loadFileList);

    document.getElementById('message-input').addEventListener('keydown', (e) => {
        if (e.key === 'Enter' && !e.shiftKey) {
            e.preventDefault();
            sendMessage();
        }
    });

    window.viewModelDetails = async () => {
        const data = await GatewayAPI.getModelDetails(AppState.getModel(), AppState.getProvider());
        alert(JSON.stringify(data, null, 2));
    };

    window.clearConversation = () => {
        AppState.clearHistory();
        UIRenderer.renderContextBar(AppState.activeContextFiles);
        loadFileList();
        document.getElementById('chat-messages').innerHTML = `<div class="message assistant">🧹 Đã làm sạch ngữ cảnh.</div>`;
    };

    window.loadFileList = loadFileList;
});

