const ENDPOINTS = [
  { value: 'health', label: 'Health', hint: 'GET /health' },
  { value: 'readiness', label: 'Readiness', hint: 'GET /ready' },
  { value: 'stats', label: 'Stats', hint: 'GET /stats' },
  { value: 'metrics', label: 'Metrics', hint: 'GET /metrics' },
  { value: 'login', label: 'Login', hint: 'POST /v1/auth/login' },
  { value: 'register', label: 'Register', hint: 'POST /v1/auth/register/initiate' },
  { value: 'verify_registration', label: 'Verify registration', hint: 'POST /v1/auth/register/verify' },
  { value: 'refresh_token', label: 'Refresh token', hint: 'POST /v1/auth/refresh' },
  { value: 'logout', label: 'Logout', hint: 'POST /v1/auth/logout' },
  { value: 'oauth_login', label: 'OAuth login', hint: 'POST /v1/auth/oauth/{provider}' },
  { value: 'current_user', label: 'Current user', hint: 'GET /v1/auth/me' },
  { value: 'embeddings', label: 'Embeddings', hint: 'POST /v1/embeddings' },
  { value: 'list_models', label: 'List models', hint: 'GET /v1/models/' },
  { value: 'model_details', label: 'Model details', hint: 'GET /v1/models/{model_id}' },
  { value: 'list_api_keys', label: 'List API keys', hint: 'GET /v1/auth/api-keys' },
  { value: 'create_api_key', label: 'Create API key', hint: 'POST /v1/auth/api-keys' },
  { value: 'revoke_api_key', label: 'Revoke API key', hint: 'DELETE /v1/auth/api-keys/{key_id}' },
  { value: 'register_agent', label: 'Register agent', hint: 'POST /v1/agents/' },
  { value: 'register_tool', label: 'Register tool', hint: 'POST /v1/tools/' },
  { value: 'create_agent_session', label: 'Create agent session', hint: 'POST /v1/multi-agent/sessions' },
  { value: 'add_agent_to_session', label: 'Add agent to session', hint: 'POST /v1/multi-agent/sessions/{id}/agents' },
  { value: 'list_agent_messages', label: 'List agent messages', hint: 'GET /v1/multi-agent/sessions/{id}/messages' },
  { value: 'send_agent_message', label: 'Send agent message', hint: 'POST /v1/multi-agent/messages' },
  { value: 'create_agent_task', label: 'Create agent task', hint: 'POST /v1/multi-agent/tasks' },
  { value: 'get_agent_task', label: 'Get agent task', hint: 'GET /v1/multi-agent/tasks/{id}' },
  { value: 'cancel_agent_task', label: 'Cancel agent task', hint: 'POST /v1/multi-agent/tasks/{id}/cancel' },
  { value: 'execute_agent_task', label: 'Execute agent task', hint: 'POST /v1/multi-agent/tasks/{id}/execute' },
  { value: 'close_agent_session', label: 'Close agent session', hint: 'POST /v1/multi-agent/sessions/{id}/close' },
  { value: 'get_agent_execution', label: 'Get agent execution', hint: 'GET /v1/multi-agent/executions/{id}' },
  { value: 'reload_routing', label: 'Reload routing', hint: 'POST /v1/admin/reload/routing' },
  { value: 'circuit_breakers_status', label: 'Circuit breakers', hint: 'GET /v1/admin/circuit-breakers/status' },
];

const EXAMPLES = {
  login: { email: '', password: '' },
  register: { email: '', password: '', name: '' },
  embeddings: { model: 'mock-embedding', input: ['hello'] },
  list_models: { provider_name: 'mock' },
  model_details: { provider_name: 'mock', model_id: 'mock-chat' },
  create_agent_session: { agent_ids: [] },
  send_agent_message: { session_id: '', sender_id: '', message_type: 'text', payload: {} },
  create_agent_task: { session_id: '', assigned_agent_id: '', input: {} },
  register_agent: { name: '', description: '', tools: [] },
  register_tool: { name: '', description: '', parameters: {} },
  verify_registration: { email: '', otp: '' },
  refresh_token: { refresh_token: '' },
  logout: { refresh_token: '' },
  oauth_login: { provider: 'github', email: '', provider_user_id: '', name: '' },
  create_api_key: { name: '' },
  revoke_api_key: { key_id: '' },
  add_agent_to_session: { session_id: '', agent_id: '' },
  list_agent_messages: { session_id: '' },
  get_agent_task: { task_id: '' },
  cancel_agent_task: { task_id: '' },
  execute_agent_task: { task_id: '' },
  close_agent_session: { session_id: '' },
  get_agent_execution: { execution_id: '' },
};

function formatResult(value) {
  return typeof value === 'string' ? value : JSON.stringify(value, null, 2);
}

export function initGatewayPanel() {
  const panel = document.getElementById('gateway-panel');
  if (!panel) return;

  panel.innerHTML = `
    <section class="gateway-auth">
      <div class="gateway-heading">
        <div>
          <span class="gateway-eyebrow">ACCESS</span>
          <h2>Sign in</h2>
        </div>
        <span id="auth-status" class="gateway-status idle">Signed out</span>
      </div>

      <input id="auth-email" type="email" placeholder="Email">
      <input id="auth-password" type="password" placeholder="Password">

      <button id="auth-login" class="gateway-button primary">
        Sign in
      </button>

      <pre id="auth-result" class="gateway-result"></pre>
    </section>

    <!-- Gateway console hiện tại đặt bên dưới -->
    
    <div class="gateway-heading">
      <div>
        <span class="gateway-eyebrow">CONTROL PLANE</span>
        <h2>Gateway Console</h2>
      </div>
      <span id="gateway-status" class="gateway-status idle">Ready</span>
    </div>
    <label class="gateway-label" for="gateway-endpoint">Endpoint</label>
    <select id="gateway-endpoint" class="gateway-select">
      ${ENDPOINTS.map(item => `<option value="${item.value}">${item.label} · ${item.hint}</option>`).join('')}
    </select>
    <label class="gateway-label" for="gateway-payload">JSON payload</label>
    <textarea id="gateway-payload" class="gateway-payload" spellcheck="false" placeholder="{}"></textarea>
    <div class="gateway-actions">
      <button id="gateway-reset" class="gateway-button secondary">Reset</button>
      <button id="gateway-run" class="gateway-button primary">Run endpoint</button>
    </div>
    <pre id="gateway-result" class="gateway-result">Chưa có response.</pre>
  `;

  const loginButton = panel.querySelector('#auth-login');
  const emailInput = panel.querySelector('#auth-email');
  const passwordInput = panel.querySelector('#auth-password');
  const authStatus = panel.querySelector('#auth-status');
  const authResult = panel.querySelector('#auth-result');

  loginButton.addEventListener('click', async () => {
    loginButton.disabled = true;
    authStatus.textContent = 'Signing in';
    authStatus.className = 'gateway-status running';

    try {
      const response = await window.pywebview.api.login({
        email: emailInput.value.trim(),
        password: passwordInput.value,
      });

      if (!response.success) {
        throw new Error(response.error);
      }

      authStatus.textContent = 'Signed in';
      authStatus.className = 'gateway-status success';
      authResult.textContent = JSON.stringify(
        response.data.user,
        null,
        2,
      );
    } catch (error) {
      authStatus.textContent = 'Failed';
      authStatus.className = 'gateway-status error';
      authResult.textContent = error.message;
    } finally {
      loginButton.disabled = false;
    }
  });

  const endpoint = panel.querySelector('#gateway-endpoint');
  const payload = panel.querySelector('#gateway-payload');
  const result = panel.querySelector('#gateway-result');
  const status = panel.querySelector('#gateway-status');
  const runButton = panel.querySelector('#gateway-run');

  const resetPayload = () => {
    payload.value = JSON.stringify(EXAMPLES[endpoint.value] || {}, null, 2);
    result.textContent = 'Chưa có response.';
    status.textContent = 'Ready';
    status.className = 'gateway-status idle';
  };

  endpoint.addEventListener('change', resetPayload);
  panel.querySelector('#gateway-reset').addEventListener('click', resetPayload);
  runButton.addEventListener('click', async () => {
    let body;
    try {
      body = payload.value.trim() ? JSON.parse(payload.value) : {};
    } catch (error) {
      status.textContent = 'Invalid JSON';
      status.className = 'gateway-status error';
      result.textContent = error.message;
      return;
    }

    if (!window.pywebview?.api?.execute_gateway_endpoint) {
      status.textContent = 'Bridge unavailable';
      status.className = 'gateway-status error';
      result.textContent = 'PyWebView API chưa sẵn sàng.';
      return;
    }

    runButton.disabled = true;
    status.textContent = 'Running';
    status.className = 'gateway-status running';
    result.textContent = 'Đang thực thi...';
    try {
      const response = await window.pywebview.api.execute_gateway_endpoint(endpoint.value, body);
      result.textContent = formatResult(response?.data ?? response);
      status.textContent = response?.success ? 'Success' : 'Failed';
      status.className = `gateway-status ${response?.success ? 'success' : 'error'}`;
    } catch (error) {
      status.textContent = 'Failed';
      status.className = 'gateway-status error';
      result.textContent = error.message;
    } finally {
      runButton.disabled = false;
    }
  });

  resetPayload();
}