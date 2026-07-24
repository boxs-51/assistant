import { AuthManager } from './auth.js';
import { UIRenderer, StreamManager } from './ui.js';
import { AppState } from './state.js';
import { GatewayAPI } from './api.js';

export function setupEventHandlers() {
    // Auth Handlers
    document.getElementById('login-btn').addEventListener('click', async () => {
        const email = document.getElementById('auth-email').value;
        const password = document.getElementById('auth-password').value;
        await AuthManager.login(email, password);
    });

    document.getElementById('show-register-btn').addEventListener('click', async () => {
        const email = document.getElementById('auth-email').value;
        const password = document.getElementById('auth-password').value;
        await AuthManager.initiateRegistration(email, password);
    });

    document.getElementById('verify-otp-btn').addEventListener('click', async () => {
        const otp = document.getElementById('auth-otp').value;
        await AuthManager.verifyOtp(otp);
    });

    document.getElementById('cancel-otp-btn').addEventListener('click', () => {
        UIRenderer.showOtpView(false);
    });

    document.getElementById('logout-btn').addEventListener('click', () => {
        AuthManager.logout();
    });

    document.getElementById('guest-login-btn').addEventListener('click', () => {
        UIRenderer.enterGuestMode();
    });

    document.getElementById('google-login-btn').addEventListener('click', () => {
        // Logic chuyển hướng đến endpoint OAuth của server
        window.location.href = 'http://localhost:8000/auth/oauth/login/google';
    });

    // Chat Handlers
    document.getElementById('send-btn').addEventListener('click', handleSendMessage);
    document.getElementById('message-input').addEventListener('keydown', (e) => {
        if (e.key === 'Enter' && !e.shiftKey) {
            e.preventDefault();
            handleSendMessage();
        }
    });

    // File and other UI Handlers
    document.getElementById('upload-file-btn').addEventListener('click', () => {
        document.getElementById('file-uploader').click();
    });

    document.getElementById('file-uploader').addEventListener('change', handleUploadFile);

    document.getElementById('clear-conversation-btn').addEventListener('click', () => {
        AppState.clearHistory();
        UIRenderer.renderFileList(AppState.attachedFiles, handleRemoveFile);
        UIRenderer.renderContextBar(AppState.attachedFiles);
        const chatMessages = document.getElementById('chat-messages');
        chatMessages.innerHTML = '<div class="message assistant">Hội thoại đã được làm mới.</div>';
    });

    // Placeholder for future implementation
    document.getElementById('create-api-key-btn').addEventListener('click', () => alert('Chức năng tạo API Key sẽ được phát triển.'));
    document.getElementById('view-model-details-btn').addEventListener('click', () => alert('Chức năng xem chi tiết Model sẽ được phát triển.'));

}

export function handleOauthCallback() {
    const urlParams = new URLSearchParams(window.location.search);
    const accessToken = urlParams.get('access_token');
    const refreshToken = urlParams.get('refresh_token');

    if (accessToken && refreshToken) {
        localStorage.setItem('accessToken', accessToken);
        localStorage.setItem('refreshToken', refreshToken);
        window.history.replaceState({}, document.title, "/"); // Clean URL
    }
}

async function handleUploadFile(event) {
    const files = event.target.files;
    if (!files.length) return;

    for (const file of files) {
        try {
            const base64String = await toBase64(file);
            AppState.attachedFiles.push({
                name: file.name,
                type: file.type,
                data: base64String
            });
        } catch (error) {
            console.error("Error converting file to base64:", error);
            alert(`Lỗi khi xử lý tệp: ${file.name}`);
        }
    }

    UIRenderer.renderFileList(AppState.attachedFiles, handleRemoveFile);
    UIRenderer.renderContextBar(AppState.attachedFiles);

    // Reset the input so the same file can be selected again
    event.target.value = '';
}


async function handleSendMessage() {
    const input = document.getElementById('message-input');
    const message = input.value.trim();
    if (!message) return;

    UIRenderer.appendMessage(message, 'user');

    // Build content array with text and images
    const contentParts = [];
    if (message) {
        contentParts.push({ type: 'text', text: message });
    }
    AppState.attachedFiles.forEach(file => {
        contentParts.push({ type: 'image', source: { type: 'base64', media_type: file.type, data: file.data } });
    });

    AppState.conversationHistory.push({ role: 'user', content: contentParts });
    AppState.attachedFiles = []; // Clear attached files after sending

    input.value = '';
    input.disabled = true;
    document.getElementById('send-btn').disabled = true;

    const assistantMsgDiv = UIRenderer.appendMessage('', 'assistant', true);
    StreamManager.startStream(assistantMsgDiv);

    UIRenderer.renderFileList(AppState.attachedFiles, handleRemoveFile);
    UIRenderer.renderContextBar(AppState.attachedFiles);

    try {
        const payload = {
            messages: AppState.conversationHistory,
            model: AppState.getModel(),
            config: {
                provider: AppState.getProvider(),
                stream: true,
            }
        };

        const response = await GatewayAPI.postChatCompletion(payload);
        const reader = response.body.getReader();
        const decoder = new TextDecoder();
        let assistantResponse = '';

        while (true) {
            const { value, done } = await reader.read();
            if (done) break;

            const chunk = decoder.decode(value, { stream: true });
            const lines = chunk.split('\n\n');

            for (const line of lines) {
                if (line.startsWith('data: ')) {
                    const data = line.substring(6);
                    if (data.trim() === '[DONE]') {
                        break;
                    }
                    try {
                        const json = JSON.parse(data);
                        const delta = json.choices?.[0]?.delta;
                        if (delta?.content) {
                            assistantResponse += delta.content;
                            StreamManager.updateStream(delta.content);
                        }
                        // Handle tool calls for images (Gemini specific)
                        const toolCall = delta?.tool_calls?.[0]?.function?.call;
                        if (toolCall?.image) {
                            const { data, mime_type } = toolCall.image;
                            StreamManager.renderImage(data, mime_type);
                        }
                    } catch (e) { /* Ignore parsing errors for now */ }
                }
            }
        }
        AppState.conversationHistory.push({ role: 'assistant', content: assistantResponse });
    } catch (error) {
        StreamManager.updateStream(`\n**Lỗi:** ${error.message}`);
    } finally {
        StreamManager.endStream();
        input.disabled = false;
        document.getElementById('send-btn').disabled = false;
        input.focus();
    }
}

function handleRemoveFile(index) {
    AppState.attachedFiles.splice(index, 1);
    UIRenderer.renderFileList(AppState.attachedFiles, handleRemoveFile);
    UIRenderer.renderContextBar(AppState.attachedFiles);
}

// Helper to convert file to base64
function toBase64(file) {
    return new Promise((resolve, reject) => {
        const reader = new FileReader();
        reader.readAsDataURL(file);
        reader.onload = () => resolve(reader.result.split(',')[1]);
        reader.onerror = error => reject(error);
    });
}