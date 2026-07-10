import { GatewayAPI } from './api.js';
import { AppState } from './state.js';
import { UIRenderer } from './ui.js';

export const AuthManager = {
    _pendingRegistrationEmail: null,

    async login(email, password) {
        const authError = document.getElementById('auth-error');
        try {
            const response = await GatewayAPI.login(email, password);
            if (!response.ok) {
                const errorData = await response.json();
                throw new Error(errorData.detail || 'Đăng nhập thất bại');
            }
            const data = await response.json();
            AppState.saveTokens(data.access_token, data.refreshToken);
            await this.fetchCurrentUser();
            authError.textContent = '';
        } catch (error) {
            authError.textContent = error.message;
        }
    },

    async initiateRegistration(email, password) {
        const authError = document.getElementById('auth-error');
        try {
            const response = await GatewayAPI.initiateRegistration(email, password);
            const data = await response.json();
            if (!response.ok) {
                throw new Error(data.message || 'Không thể bắt đầu đăng ký.');
            }
            this._pendingRegistrationEmail = email;
            UIRenderer.showOtpView(true, email);
            authError.textContent = '';
        } catch (error) {
            authError.textContent = error.message;
        }
    },

    async verifyOtp(otp) {
        const otpError = document.getElementById('otp-error');
        if (!this._pendingRegistrationEmail) {
            otpError.textContent = 'Lỗi: Không tìm thấy email đăng ký.';
            return;
        }
        try {
            const response = await GatewayAPI.verifyOtp(this._pendingRegistrationEmail, otp);
            const data = await response.json();
            if (!response.ok) {
                throw new Error(data.detail.message || 'Mã OTP không hợp lệ hoặc đã hết hạn.');
            }
            AppState.saveTokens(data.access_token, data.refreshToken);
            await this.fetchCurrentUser();
            this._pendingRegistrationEmail = null;
            otpError.textContent = '';
        } catch (error) {
            otpError.textContent = error.message;
        }
    },

    async fetchCurrentUser() {
        try {
            const response = await GatewayAPI.getMe();
            if (!response.ok) throw new Error('Phiên đăng nhập hết hạn.');
            const user = await response.json();
            AppState.userEmail = user.email;
            UIRenderer.updateLoginState(true, user.email);
            return true;
        } catch (error) {
            this.logout();
            return false;
        }
    },

    logout() {
        AppState.isAuthenticated = false;
        AppState.accessToken = null;
        AppState.refreshToken = null;
        AppState.userEmail = null;
        AppState.clearTokens();
        UIRenderer.updateLoginState(false);
    },

    async checkLoginStatus() {
        AppState.accessToken = localStorage.getItem('accessToken');
        AppState.refreshToken = localStorage.getItem('refreshToken');

        if (AppState.accessToken) {
            AppState.isAuthenticated = true;
            const loggedIn = await this.fetchCurrentUser();
            if (!loggedIn) {
                this.logout();
            }
        } else {
            this.logout();
        }
    }
};