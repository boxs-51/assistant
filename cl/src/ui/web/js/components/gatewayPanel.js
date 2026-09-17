const state = {
  snapshot: null,
  activeTab: 'overview',
  selectedTool: null,
  authMode: 'login',
};
let agentPollTimer = null;

const escapeHtml = (value = '') => String(value)
  .replaceAll('&', '&amp;')
  .replaceAll('<', '&lt;')
  .replaceAll('>', '&gt;')
  .replaceAll('"', '&quot;')
  .replaceAll("'", '&#039;');

function resultText(value) {
  return typeof value === 'string' ? value : JSON.stringify(value, null, 2);
}

function setOutput(panel, value, error = false) {
  const output = panel.querySelector('#studio-output');
  output.textContent = resultText(value);
  output.classList.toggle('error', error);
}

async function callBridge(method, ...args) {
  const target = window.pywebview?.api?.[method];
  if (!target) throw new Error(`Bridge method '${method}' chưa sẵn sàng.`);
  const response = await target.apply(window.pywebview.api, args);
  if (response?.success === false) throw new Error(response.error || 'Thao tác thất bại.');
  return response?.data ?? response;
}

function capabilityItems(kind) {
  return (state.snapshot?.capabilities || []).filter(item => item.kind === kind);
}

function renderOverview(panel) {
  const view = panel.querySelector('[data-view="overview"]');
  const gateway = state.snapshot?.gateway || {};
  const preferences = state.snapshot?.preferences || {};
  const connected = Boolean(gateway.connected);
  const principalLabel = gateway.principal_type === 'guest' ? 'Guest' : 'User';
  view.innerHTML = `
    <div class="studio-card status-card">
      <div><span class="studio-kicker">GATEWAY</span><h3>${connected ? `Connected · ${principalLabel}` : 'Disconnected'}</h3></div>
      <span class="studio-dot ${connected ? 'online' : ''}"></span>
    </div>
    ${gateway.error ? `<div class="studio-alert">${escapeHtml(gateway.error)}</div>` : ''}
    ${connected && gateway.principal_type === 'user' ? '<button id="studio-logout" class="gateway-button secondary wide">Sign out and continue as guest</button>' : ''}
    <div class="studio-card">
      <span class="studio-kicker">CHAT RUNTIME</span>
      <label>Provider</label>
      <select id="studio-provider">
        ${['gemini', 'openai', 'ollama', 'mock'].map(name => `<option ${preferences.provider === name ? 'selected' : ''}>${name}</option>`).join('')}
      </select>
      <label>Model</label>
      <div class="studio-inline">
        <input id="studio-model" list="studio-model-options" value="${escapeHtml(preferences.model || '')}" placeholder="Model ID">
        <datalist id="studio-model-options"></datalist>
        <button id="studio-load-models" class="gateway-button secondary">Load</button>
      </div>
      <button id="studio-save-runtime" class="gateway-button primary wide">Use for chat & agents</button>
    </div>
    <div class="studio-card studio-summary">
      <div><strong>${state.snapshot?.skills?.length || 0}</strong><span>Skills</span></div>
      <div><strong>${state.snapshot?.tools?.length || 0}</strong><span>Tools</span></div>
      <div><strong>${state.snapshot?.agents?.length || 0}</strong><span>Agents</span></div>
    </div>
  `;

  view.querySelector('#studio-logout')?.addEventListener('click', async () => {
    try {
      await callBridge('logout');
      await refresh(panel, 'overview');
      setOutput(panel, { message: 'Signed out. A guest session is now active.' });
    } catch (error) {
      setOutput(panel, error.message, true);
    }
  });

  view.querySelector('#studio-load-models').addEventListener('click', async () => {
    try {
      const provider = view.querySelector('#studio-provider').value;
      const response = await callBridge('list_models', provider);
      const models = Array.isArray(response) ? response : (response?.data || []);
      view.querySelector('#studio-model-options').innerHTML = models
        .map(item => `<option value="${escapeHtml(item.id || item.name || '')}"></option>`)
        .join('');
      setOutput(panel, { loaded_models: models.length, provider });
    } catch (error) {
      setOutput(panel, error.message, true);
    }
  });

  view.querySelector('#studio-save-runtime').addEventListener('click', async () => {
    try {
      const data = await callBridge('set_chat_preferences', {
        provider: view.querySelector('#studio-provider').value,
        model: view.querySelector('#studio-model').value,
      });
      state.snapshot.preferences = data;
      setOutput(panel, { message: 'Chat runtime updated', ...data });
    } catch (error) {
      setOutput(panel, error.message, true);
    }
  });
}

function renderSkills(panel) {
  const view = panel.querySelector('[data-view="skills"]');
  const skills = state.snapshot?.skills || [];
  view.innerHTML = `
    <div class="studio-section-head"><div><span class="studio-kicker">WORKFLOWS</span><h3>Skills</h3></div><span>${skills.length} discovered</span></div>
    <div class="studio-list">
      ${skills.length ? skills.map(skill => `
        <article class="studio-card resource-card">
          <div class="resource-title"><strong>${escapeHtml(skill.name)}</strong><span class="risk ${escapeHtml((skill.base_risk || 'MEDIUM').toLowerCase())}">${escapeHtml(skill.base_risk || 'MEDIUM')}</span></div>
          <p>${escapeHtml(skill.description || '')}</p>
          <button class="gateway-button ${skill.loaded ? 'secondary' : 'primary'}" data-skill="${escapeHtml(skill.name)}" data-action="${skill.loaded ? 'deactivate' : 'activate'}">
            ${skill.loaded ? 'Unload' : 'Activate'}
          </button>
        </article>`).join('') : '<div class="studio-empty">Không tìm thấy Skill.</div>'}
    </div>
  `;
  view.querySelectorAll('[data-skill]').forEach(button => {
    button.addEventListener('click', async () => {
      button.disabled = true;
      try {
        const method = button.dataset.action === 'activate' ? 'activate_skill' : 'deactivate_skill';
        await callBridge(method, button.dataset.skill);
        await refresh(panel, 'skills');
        setOutput(panel, `${button.dataset.skill}: ${button.dataset.action} thành công.`);
      } catch (error) {
        button.disabled = false;
        setOutput(panel, error.message, true);
      }
    });
  });
}

function mergedTools() {
  const byName = new Map();
  (state.snapshot?.tools || []).forEach(tool => byName.set(tool.name, tool));
  capabilityItems('TOOL').forEach(item => {
    const definition = item.definition || {};
    const name = item.capability_id || definition.name;
    byName.set(name, {
      ...(byName.get(name) || {}),
      name,
      description: definition.description || byName.get(name)?.description,
      parameters: definition.parameters || definition.input_schema || byName.get(name)?.parameters || { type: 'object' },
      source: definition.source || byName.get(name)?.source || 'SERVER',
      available: (item.implementations || []).some(impl => ['ENABLED', 'DEGRADED'].includes(impl.state)),
    });
  });
  return [...byName.values()];
}

function schemaInputs(schema = {}) {
  const properties = schema.properties || {};
  const required = new Set(schema.required || []);
  if (!Object.keys(properties).length) {
    return '<div class="studio-empty">Tool không yêu cầu tham số.</div>';
  }
  return Object.entries(properties).map(([name, spec]) => `
    <label>${escapeHtml(spec.title || name)}${required.has(name) ? ' *' : ''}</label>
    <input data-arg="${escapeHtml(name)}" data-type="${escapeHtml(spec.type || 'string')}" placeholder="${escapeHtml(spec.description || spec.type || '')}">
  `).join('');
}

function renderTools(panel) {
  const view = panel.querySelector('[data-view="tools"]');
  const tools = mergedTools();
  view.innerHTML = `
    <div class="studio-section-head"><div><span class="studio-kicker">CAPABILITIES</span><h3>Tools</h3></div><span>${tools.length} available</span></div>
    <div class="studio-list tool-list">
      ${tools.map(tool => `
        <button class="studio-card tool-card" data-tool="${escapeHtml(tool.name)}">
          <span><strong>${escapeHtml(tool.name)}</strong><small>${escapeHtml(tool.source || 'TOOL')}</small></span>
          <p>${escapeHtml(tool.description || '')}</p>
        </button>`).join('') || '<div class="studio-empty">Không tìm thấy Tool.</div>'}
    </div>
    <div id="tool-runner" class="studio-card tool-runner hidden"></div>
  `;
  view.querySelectorAll('[data-tool]').forEach(button => {
    button.addEventListener('click', () => {
      state.selectedTool = tools.find(tool => tool.name === button.dataset.tool);
      const runner = view.querySelector('#tool-runner');
      runner.classList.remove('hidden');
      runner.innerHTML = `
        <div class="studio-section-head"><h3>Run ${escapeHtml(state.selectedTool.name)}</h3><span>${escapeHtml(state.selectedTool.base_risk || 'HITL')}</span></div>
        <div id="tool-args">${schemaInputs(state.selectedTool.parameters)}</div>
        <button id="run-selected-tool" class="gateway-button primary wide">Run tool</button>
      `;
      runner.querySelector('#run-selected-tool').addEventListener('click', async () => {
        try {
          const args = {};
          runner.querySelectorAll('[data-arg]').forEach(input => {
            if (!input.value) return;
            const type = input.dataset.type;
            if (type === 'number' || type === 'integer') args[input.dataset.arg] = Number(input.value);
            else if (type === 'boolean') args[input.dataset.arg] = ['true', '1', 'yes'].includes(input.value.toLowerCase());
            else if (type === 'object' || type === 'array') args[input.dataset.arg] = JSON.parse(input.value);
            else args[input.dataset.arg] = input.value;
          });
          const result = await callBridge('execute_tool', state.selectedTool.name, args);
          setOutput(panel, result);
          await refresh(panel, 'tools');
        } catch (error) {
          setOutput(panel, error.message, true);
        }
      });
    });
  });
}

function assignmentOptions(items, field) {
  return items.map(item => `
    <label class="studio-check"><input type="checkbox" data-assignment="${field}" value="${escapeHtml(item.name || item.capability_id)}"> ${escapeHtml(item.name || item.capability_id)}</label>
  `).join('') || '<span class="studio-empty">None</span>';
}

function monitorAgentTask(panel, taskId) {
  if (agentPollTimer) clearInterval(agentPollTimer);
  const monitor = panel.querySelector('#agent-monitor');
  if (!monitor) return;
  monitor.classList.remove('hidden');

  const paint = task => {
    monitor.innerHTML = `
      <div class="resource-title"><strong>Task ${escapeHtml(task.task_id)}</strong><span class="gateway-status running">${escapeHtml(task.status)}</span></div>
      ${task.output ? `<pre class="gateway-result">${escapeHtml(resultText(task.output))}</pre>` : '<p>Agent đang xử lý ở background…</p>'}
      ${['COMPLETED', 'FAILED', 'CANCELLED'].includes(task.status) ? '' : '<button id="cancel-running-agent" class="gateway-button secondary wide">Cancel task</button>'}
    `;
    monitor.querySelector('#cancel-running-agent')?.addEventListener('click', async () => {
      try {
        const cancelled = await callBridge('cancel_agent_task', taskId);
        paint(cancelled);
      } catch (error) {
        setOutput(panel, error.message, true);
      }
    });
  };

  paint({ task_id: taskId, status: 'RUNNING' });
  agentPollTimer = setInterval(async () => {
    try {
      const task = await callBridge('get_agent_task_status', taskId);
      paint(task);
      if (['COMPLETED', 'FAILED', 'CANCELLED'].includes(task.status)) {
        clearInterval(agentPollTimer);
        agentPollTimer = null;
        setOutput(panel, task, task.status === 'FAILED');
      }
    } catch (error) {
      clearInterval(agentPollTimer);
      agentPollTimer = null;
      setOutput(panel, error.message, true);
    }
  }, 1000);
}

function renderAgents(panel) {
  const view = panel.querySelector('[data-view="agents"]');
  const agents = state.snapshot?.agents || [];
  const skills = (state.snapshot?.skills || []).filter(item => item.loaded);
  const tools = mergedTools();
  view.innerHTML = `
    <div class="studio-section-head"><div><span class="studio-kicker">AUTOMATION</span><h3>Agents</h3></div><span>${agents.length} registered</span></div>
    <details class="studio-card" open>
      <summary>Create agent</summary>
      <label>Name</label><input id="agent-name" placeholder="review-agent">
      <label>Goal</label><input id="agent-goal" placeholder="Review the project">
      <label>Instruction</label><textarea id="agent-instruction" placeholder="Act carefully and report findings."></textarea>
      <label>Skills</label><div class="studio-checks">${assignmentOptions(skills, 'skills')}</div>
      <label>Tools</label><div class="studio-checks">${assignmentOptions(tools, 'tools')}</div>
      <button id="save-agent" class="gateway-button primary wide">Save agent</button>
    </details>
    <div class="studio-card">
      <label>Run agent</label>
      <select id="run-agent-id"><option value="">Select agent</option>${agents.map(agent => `<option value="${escapeHtml(agent.name)}">${escapeHtml(agent.name)}</option>`).join('')}</select>
      <textarea id="run-agent-prompt" placeholder="Describe the task..."></textarea>
      <button id="run-agent" class="gateway-button primary wide">Start task</button>
    </div>
    <div id="agent-monitor" class="studio-card hidden"></div>
    <div class="studio-list">${agents.map(agent => `<article class="studio-card resource-card"><strong>${escapeHtml(agent.name)}</strong><p>${escapeHtml(agent.goal || '')}</p><small>${agent.skills?.length || 0} skills · ${agent.tools?.length || 0} tools</small></article>`).join('')}</div>
  `;
  view.querySelector('#save-agent').addEventListener('click', async () => {
    const selected = field => [...view.querySelectorAll(`[data-assignment="${field}"]:checked`)].map(input => input.value);
    const payload = {
      name: view.querySelector('#agent-name').value.trim(),
      goal: view.querySelector('#agent-goal').value.trim(),
      instruction: view.querySelector('#agent-instruction').value.trim(),
      skills: selected('skills'),
      tools: selected('tools'),
    };
    try {
      const result = await callBridge('save_agent', payload);
      setOutput(panel, result);
      await refresh(panel, 'agents');
    } catch (error) {
      setOutput(panel, error.message, true);
    }
  });
  view.querySelector('#run-agent').addEventListener('click', async () => {
    try {
      const result = await callBridge('run_agent', {
        agent_id: view.querySelector('#run-agent-id').value,
        prompt: view.querySelector('#run-agent-prompt').value,
      });
      setOutput(panel, result);
      monitorAgentTask(panel, result.task.task_id);
    } catch (error) {
      setOutput(panel, error.message, true);
    }
  });
}

function renderActivity(panel) {
  const view = panel.querySelector('[data-view="activity"]');
  const items = state.snapshot?.activity || [];
  view.innerHTML = `
    <div class="studio-section-head"><div><span class="studio-kicker">OBSERVABILITY</span><h3>Activity</h3></div><span>${items.length} events</span></div>
    <div class="studio-list">${items.map(item => `
      <article class="studio-card activity-item ${escapeHtml(item.status)}">
        <div><strong>${escapeHtml(item.action)}</strong><time>${new Date(item.created_at * 1000).toLocaleTimeString()}</time></div>
        <p>${escapeHtml(resultText(item.detail ?? ''))}</p>
      </article>`).join('') || '<div class="studio-empty">Chưa có hoạt động.</div>'}</div>
  `;
}

function showTab(panel, tab) {
  state.activeTab = tab;
  panel.querySelectorAll('[data-tab]').forEach(button => button.classList.toggle('active', button.dataset.tab === tab));
  panel.querySelectorAll('[data-view]').forEach(view => view.classList.toggle('active', view.dataset.view === tab));
}

function render(panel) {
  renderOverview(panel);
  renderSkills(panel);
  renderTools(panel);
  renderAgents(panel);
  renderActivity(panel);
  showTab(panel, state.activeTab);
  const connected = Boolean(state.snapshot?.gateway?.connected);
  const badge = panel.querySelector('#studio-connection');
  badge.textContent = connected ? 'Online' : 'Offline';
  badge.className = `gateway-status ${connected ? 'success' : 'idle'}`;
}

async function refresh(panel, keepTab = state.activeTab) {
  state.activeTab = keepTab;
  try {
    state.snapshot = await callBridge('get_app_snapshot');
    render(panel);
  } catch (error) {
    setOutput(panel, error.message, true);
  }
}

function showAuthMode(panel, mode) {
  state.authMode = mode;
  panel.querySelectorAll('[data-auth-mode]').forEach(button => {
    button.classList.toggle('active', button.dataset.authMode === mode);
  });
  panel.querySelectorAll('[data-auth-view]').forEach(view => {
    view.classList.toggle('active', view.dataset.authView === mode);
  });
}

function setAuthPending(form, pending) {
  form.querySelectorAll('button, input').forEach(control => {
    control.disabled = pending;
  });
}

function assertMatchingPasswords(password, confirmation) {
  if (password.length < 6) throw new Error('Password must contain at least 6 characters.');
  if (password !== confirmation) throw new Error('Passwords do not match.');
}

export function initGatewayPanel() {
  const panel = document.getElementById('gateway-panel');
  if (!panel) return;
  panel.innerHTML = `
    <div class="gateway-heading studio-heading">
      <div><span class="gateway-eyebrow">AGENT STUDIO</span><h2>Control Center</h2></div>
      <span id="studio-connection" class="gateway-status idle">Loading</span>
    </div>
    <section class="gateway-auth studio-card" aria-label="Gateway account">
      <div class="gateway-auth-tabs" role="tablist">
        <button type="button" data-auth-mode="login" class="active">Sign in</button>
        <button type="button" data-auth-mode="register">Register</button>
        <button type="button" data-auth-mode="reset">Forgot password</button>
      </div>
      <form class="gateway-auth-view active" data-auth-view="login">
        <input id="auth-email" type="email" autocomplete="username" placeholder="Email" required>
        <input id="auth-password" type="password" autocomplete="current-password" placeholder="Password" required>
        <button class="gateway-button primary wide" type="submit">Sign in</button>
      </form>
      <form class="gateway-auth-view" data-auth-view="register">
        <input id="register-name" autocomplete="name" placeholder="Full name">
        <input id="register-email" type="email" autocomplete="username" placeholder="Email" required>
        <input id="register-password" type="password" autocomplete="new-password" minlength="6" placeholder="Password (6+ characters)" required>
        <input id="register-password-confirm" type="password" autocomplete="new-password" minlength="6" placeholder="Confirm password" required>
        <div class="gateway-auth-verification" data-register-verification hidden>
          <input id="register-otp" inputmode="numeric" pattern="[0-9]{6}" maxlength="6" autocomplete="one-time-code" placeholder="6-digit verification code">
          <button id="register-verify" class="gateway-button primary wide" type="button">Verify and sign in</button>
        </div>
        <button id="register-send" class="gateway-button primary wide" type="submit">Send verification code</button>
      </form>
      <form class="gateway-auth-view" data-auth-view="reset">
        <input id="reset-email" type="email" autocomplete="username" placeholder="Account email" required>
        <div class="gateway-auth-verification" data-reset-verification hidden>
          <input id="reset-otp" inputmode="numeric" pattern="[0-9]{6}" maxlength="6" autocomplete="one-time-code" placeholder="6-digit reset code">
          <input id="reset-password" type="password" autocomplete="new-password" minlength="6" placeholder="New password">
          <input id="reset-password-confirm" type="password" autocomplete="new-password" minlength="6" placeholder="Confirm new password">
          <button id="reset-confirm" class="gateway-button primary wide" type="button">Set new password</button>
        </div>
        <button id="reset-send" class="gateway-button primary wide" type="submit">Send reset code</button>
      </form>
    </section>
    <nav class="studio-tabs">
      ${['overview', 'skills', 'tools', 'agents', 'activity'].map(tab => `<button data-tab="${tab}">${tab}</button>`).join('')}
    </nav>
    <div class="studio-views">
      ${['overview', 'skills', 'tools', 'agents', 'activity'].map(tab => `<section class="studio-view" data-view="${tab}"></section>`).join('')}
    </div>
    <div class="studio-output-wrap"><span class="studio-kicker">RESULT</span><pre id="studio-output" class="gateway-result">Ready.</pre></div>
  `;

  panel.querySelectorAll('[data-tab]').forEach(button => button.addEventListener('click', () => showTab(panel, button.dataset.tab)));
  panel.querySelectorAll('[data-auth-mode]').forEach(button => {
    button.addEventListener('click', () => showAuthMode(panel, button.dataset.authMode));
  });

  const loginForm = panel.querySelector('[data-auth-view="login"]');
  loginForm.addEventListener('submit', async event => {
    event.preventDefault();
    setAuthPending(loginForm, true);
    try {
      const response = await callBridge('login', {
        email: panel.querySelector('#auth-email').value.trim(),
        password: panel.querySelector('#auth-password').value,
      });
      setOutput(panel, response.user || response);
      await refresh(panel);
    } catch (error) {
      setOutput(panel, error.message, true);
    } finally {
      setAuthPending(loginForm, false);
    }
  });

  const registerForm = panel.querySelector('[data-auth-view="register"]');
  registerForm.addEventListener('submit', async event => {
    event.preventDefault();
    setAuthPending(registerForm, true);
    try {
      assertMatchingPasswords(
        panel.querySelector('#register-password').value,
        panel.querySelector('#register-password-confirm').value,
      );
      const response = await callBridge('register', {
        name: panel.querySelector('#register-name').value.trim() || null,
        email: panel.querySelector('#register-email').value.trim(),
        password: panel.querySelector('#register-password').value,
      });
      panel.querySelector('[data-register-verification]').hidden = false;
      panel.querySelector('#register-send').textContent = 'Resend verification code';
      setOutput(panel, response);
    } catch (error) {
      setOutput(panel, error.message, true);
    } finally {
      setAuthPending(registerForm, false);
    }
  });

  panel.querySelector('#register-verify').addEventListener('click', async () => {
    setAuthPending(registerForm, true);
    try {
      const response = await callBridge('verify_registration', {
        email: panel.querySelector('#register-email').value.trim(),
        otp: panel.querySelector('#register-otp').value.trim(),
      });
      setOutput(panel, response.user || response);
      await refresh(panel);
    } catch (error) {
      setOutput(panel, error.message, true);
    } finally {
      setAuthPending(registerForm, false);
    }
  });

  const resetForm = panel.querySelector('[data-auth-view="reset"]');
  resetForm.addEventListener('submit', async event => {
    event.preventDefault();
    setAuthPending(resetForm, true);
    try {
      const response = await callBridge('initiate_password_reset', {
        email: panel.querySelector('#reset-email').value.trim(),
      });
      panel.querySelector('[data-reset-verification]').hidden = false;
      panel.querySelector('#reset-send').textContent = 'Resend reset code';
      setOutput(panel, response);
    } catch (error) {
      setOutput(panel, error.message, true);
    } finally {
      setAuthPending(resetForm, false);
    }
  });

  panel.querySelector('#reset-confirm').addEventListener('click', async () => {
    setAuthPending(resetForm, true);
    try {
      assertMatchingPasswords(
        panel.querySelector('#reset-password').value,
        panel.querySelector('#reset-password-confirm').value,
      );
      const response = await callBridge('confirm_password_reset', {
        email: panel.querySelector('#reset-email').value.trim(),
        otp: panel.querySelector('#reset-otp').value.trim(),
        new_password: panel.querySelector('#reset-password').value,
      });
      resetForm.reset();
      panel.querySelector('[data-reset-verification]').hidden = true;
      panel.querySelector('#reset-send').textContent = 'Send reset code';
      showAuthMode(panel, 'login');
      setOutput(panel, response);
    } catch (error) {
      setOutput(panel, error.message, true);
    } finally {
      setAuthPending(resetForm, false);
    }
  });
  showAuthMode(panel, state.authMode);
  refresh(panel);
}
