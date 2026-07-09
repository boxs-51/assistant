import { AppState } from './config.js';
import { GatewayAPI } from './api.js';
import { UIRenderer } from './ui.js';

async function completeLogin(accessToken, refreshToken) {
    // Lưu token tạm thời để gọi /me
    AppState.saveTokens(accessToken, refreshToken, '...');

    try {
        const meResponse = await GatewayAPI.getMe();
        if (meResponse.ok) {
            const userInfo = await meResponse.json();
            AppState.saveTokens(accessToken, refreshToken, userInfo.email); // Lưu lại với email chính xác
            UIRenderer.updateLoginState(true, userInfo.email);
            initializeAppState();
        }
    } catch (e) { console.error("Failed to fetch user info", e); }
}

// --- LUỒNG XÁC THỰC & QUẢN LÝ API KEY ---

async function handleLogin() {
    const email = document.getElementById('auth-email').value;
    const password = document.getElementById('auth-password').value;
    const errorP = document.getElementById('auth-error');
    errorP.textContent = "";

    try {
        const response = await GatewayAPI.login(email, password);
        const data = await response.json();
        if (response.ok) {
            await completeLogin(data.access_token, data.refresh_token);
        } else {
            errorP.textContent = data.detail || "Đăng nhập thất bại.";
        }
    } catch (err) {
        errorP.textContent = "Lỗi kết nối đến server.";
    }
}

async function handleRegister() {
    const email = document.getElementById('auth-email').value;
    const password = document.getElementById('auth-password').value;
    const errorP = document.getElementById('auth-error');
    errorP.textContent = "";

    try {
        const response = await GatewayAPI.register(email, password);
        const data = await response.json();
        if (response.ok) {
            await completeLogin(data.access_token, data.refresh_token);
        } else {
            errorP.textContent = data.detail || "Đăng ký thất bại.";
        }
    } catch (err) {
        errorP.textContent = "Lỗi kết nối đến server.";
    }
}

function handleGoogleLogin() {
    window.location.href = `${GatewayAPI.getAuthBaseUrl()}/oauth/login/google`;
}

function handleLogout() {
    AppState.logout();
    UIRenderer.updateLoginState(false);
    UIRenderer.renderApiKeyList([]);
    UIRenderer.renderFileList([]);
}

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

// --- LUỒNG QUẢN LÝ API KEY ---
async function loadApiKeys() {
    try {
        const response = await GatewayAPI.listApiKeys();
        if (response.ok) {
            const keys = await response.json();
            UIRenderer.renderApiKeyList(keys, revokeApiKey);
        }
    } catch (err) {
        console.error("Lỗi tải danh sách API key:", err);
    }
}

async function createNewApiKey() {
    const nameInput = document.getElementById('new-api-key-name');
    if (!nameInput.value) return;

    const response = await GatewayAPI.createApiKey(nameInput.value);
    if (response.ok) {
        const newKey = await response.json();
        alert(`Tạo key thành công! Vui lòng lưu lại key này:\n\n${newKey.full_key}\n\nĐây là lần duy nhất key đầy đủ được hiển thị.`);
        nameInput.value = "";
        loadApiKeys();
    } else {
        alert("Tạo API key thất bại.");
    }
}

async function revokeApiKey(keyId) {
    if (!confirm("Thu hồi API key này? Key sẽ không thể sử dụng được nữa.")) return;
    const response = await GatewayAPI.revokeApiKey(keyId);
    if (response.ok) {
        loadApiKeys();
    } else {
        alert("Thu hồi key thất bại.");
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

function initializeAppState() {
    if (AppState.isAuthenticated) {
        loadFileList();
        loadApiKeys();
    }
}

function handleOAuthCallback() {
    const params = new URLSearchParams(window.location.search);
    const accessToken = params.get('access_token');
    const refreshToken = params.get('refresh_token');

    if (accessToken && refreshToken) {
        // Xóa các tham số token khỏi URL để làm sạch
        window.history.replaceState({}, document.title, window.location.pathname);
        completeLogin(accessToken, refreshToken);
    }
}

// --- KHỞI TẠO BẮT SỰ KIỆN KHI APP READY ---
document.addEventListener("DOMContentLoaded", () => {
    handleOAuthCallback(); // Kiểm tra xem có phải là redirect từ OAuth không

    // Auth events
    document.getElementById('login-btn').addEventListener('click', handleLogin);
    document.getElementById('register-btn').addEventListener('click', handleRegister);
    document.getElementById('logout-btn').addEventListener('click', handleLogout);
    document.getElementById('google-login-btn').addEventListener('click', handleGoogleLogin);

    // Chat events
    document.getElementById('send-btn').addEventListener('click', sendMessage);
    document.getElementById('message-input').addEventListener('keydown', (e) => {
        if (e.key === 'Enter' && !e.shiftKey) {
            e.preventDefault();
            sendMessage();
        }
    });

    // File & API Key events
    document.getElementById('file-uploader').addEventListener('change', handleFileUpload);
    document.getElementById('provider-select').addEventListener('change', loadFileList);
    document.getElementById('create-api-key-btn').addEventListener('click', createNewApiKey);

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

    // Tải trạng thái ban đầu
    window.loadFileList = loadFileList;
    AppState.loadTokensFromStorage();
    UIRenderer.updateLoginState(AppState.isAuthenticated, AppState.userEmail);
    initializeAppState();
});
