/**
 * OpsPilot 2.0 — Web Command Center Application Logic
 */

let csrfToken = '';
let currentTab = 'overview';
let activeLogsContainer = '';
let activeRestartContainer = '';
let activeSnoozeContainer = '';
let configuredRefreshInterval = parseInt(localStorage.getItem('opspilot_refresh_interval') || '60', 10);
let autoRefreshPaused = (configuredRefreshInterval === 0);
let refreshCountdown = (configuredRefreshInterval > 0 ? configuredRefreshInterval : 60);
let timerInterval = null;
let cachedContainers = [];
let cachedProbes = [];

// ── View Modes & SRE Data Grid Switcher ──────────────────────────────────────
let viewModes = {
  fleet: 'rows',
  probes: 'rows',
  domains: 'rows',
  renewals: 'rows',
  incidents: 'rows',
};
try {
  const saved = localStorage.getItem('opspilot_view_modes');
  if (saved) Object.assign(viewModes, JSON.parse(saved));
} catch (e) {}

window.setViewMode = function(tabName, mode) {
  viewModes[tabName] = mode;
  try {
    localStorage.setItem('opspilot_view_modes', JSON.stringify(viewModes));
  } catch (e) {}
  updateViewToggleUI(tabName);
  if (tabName === 'fleet') filterFleetGrid();
  else if (tabName === 'probes') filterProbesGrid();
  else if (tabName === 'domains') renderDomainsGrid(cachedDomains);
  else if (tabName === 'renewals') filterRenewalsGrid();
  else if (tabName === 'incidents') renderIncidentsFeed();
};

function updateViewToggleUI(tabName) {
  const toggle = document.getElementById(`${tabName}ViewToggle`);
  if (!toggle) return;
  const current = viewModes[tabName] || 'rows';
  toggle.querySelectorAll('.view-toggle-btn').forEach(btn => {
    btn.classList.toggle('active', btn.dataset.view === current);
  });
}

function updateAllViewToggles() {
  ['fleet', 'probes', 'domains', 'renewals', 'incidents'].forEach(updateViewToggleUI);
}

// ── Authentication & Boot ───────────────────────────────────────────────────

document.addEventListener('DOMContentLoaded', () => {
  initTheme();
  initAuth();
  setupEventListeners();
  updateAllViewToggles();
});

async function apiFetch(url, options = {}) {
  options.headers = options.headers || {};
  if (csrfToken && ['POST', 'PUT', 'DELETE', 'PATCH'].includes((options.method || 'GET').toUpperCase())) {
    options.headers['X-CSRF-Token'] = csrfToken;
  }
  options.headers['Content-Type'] = options.headers['Content-Type'] || 'application/json';

  const res = await fetch(url, options);
  if (res.status === 401) {
    showLoginScreen();
    throw new Error('Unauthorized');
  }
  return res;
}

async function initAuth() {
  try {
    const res = await fetch('/api/auth/me');
    if (res.ok) {
      const data = await res.json();
      csrfToken = data.csrf_token;
      document.getElementById('serverNameLabel').textContent = data.server_name || 'node-01';
      showAppScreen();
      setRefreshInterval(configuredRefreshInterval, false);
      refreshAll();
    } else {
      showLoginScreen();
    }
  } catch (err) {
    showLoginScreen();
  }
}

function showLoginScreen() {
  document.getElementById('loginScreen').style.display = 'flex';
  document.getElementById('appContainer').style.display = 'none';
  stopAutoRefresh();
}

function showAppScreen() {
  document.getElementById('loginScreen').style.display = 'none';
  document.getElementById('appContainer').style.display = 'block';
}

// ── Event Listeners ─────────────────────────────────────────────────────────

function setupEventListeners() {
  // Login Form
  document.getElementById('loginForm').addEventListener('submit', async (e) => {
    e.preventDefault();
    const pwdInput = document.getElementById('adminPasswordInput');
    const password = pwdInput.value;
    const errBox = document.getElementById('loginErrorMsg');
    const errText = document.getElementById('loginErrorText') || errBox;
    const submitBtn = document.getElementById('loginSubmitBtn');
    const loginCard = document.querySelector('.login-card');

    errBox.style.display = 'none';
    pwdInput.classList.remove('is-invalid');
    submitBtn.disabled = true;
    submitBtn.textContent = 'Verifying credentials...';

    try {
      const res = await fetch('/api/auth/login', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ password })
      });
      const data = await res.json();
      if (res.ok && data.success) {
        csrfToken = data.csrf_token;
        document.getElementById('serverNameLabel').textContent = data.server_name || 'node-01';
        showToast('Authenticated successfully.', 'success');
        showAppScreen();
        startAutoRefresh();
        refreshAll();
      } else {
        const errorMsg = data.detail || 'Invalid administrator password.';
        errText.textContent = errorMsg;
        errBox.style.display = 'flex';
        pwdInput.classList.add('is-invalid');
        pwdInput.focus();
        pwdInput.select();

        // Trigger haptic shake animation on login card
        if (loginCard) {
          loginCard.classList.remove('shake');
          void loginCard.offsetWidth; // Force CSS reflow
          loginCard.classList.add('shake');
        }

        // Trigger floating error toast
        showToast(errorMsg, 'error');
      }
    } catch (err) {
      const errorMsg = 'Connection error. Unable to reach OpsPilot server.';
      errText.textContent = errorMsg;
      errBox.style.display = 'flex';
      pwdInput.classList.add('is-invalid');
      showToast(errorMsg, 'error');
    } finally {
      submitBtn.disabled = false;
      submitBtn.innerHTML = '<span>Authenticate Session</span> →';
    }
  });

  // Logout
  document.getElementById('logoutBtn').addEventListener('click', async () => {
    try {
      await apiFetch('/api/auth/logout', { method: 'POST' });
    } finally {
      csrfToken = '';
      window.location.reload();
    }
  });

  // Tab Switching
  document.querySelectorAll('.tab-btn').forEach(btn => {
    btn.addEventListener('click', () => switchTab(btn.dataset.tab));
  });

  // Auto-Refresh Button (Toggle pause/resume)
  document.getElementById('refreshTimerBtn')?.addEventListener('click', () => {
    autoRefreshPaused = !autoRefreshPaused;
    const btn = document.getElementById('refreshTimerBtn');
    if (autoRefreshPaused) {
      if (btn) {
        btn.innerHTML = '⏸ Paused';
        btn.style.borderColor = 'var(--color-warning)';
      }
      showToast('Auto-refresh paused. Use ↻ Sync for on-demand updates.', 'info');
    } else {
      if (configuredRefreshInterval === 0) {
        setRefreshInterval(60);
      } else {
        refreshCountdown = configuredRefreshInterval;
        if (btn) {
          btn.innerHTML = `⏱ <span id="refreshTimerCountdown">${refreshCountdown}s</span>`;
          btn.style.borderColor = 'var(--border-subtle)';
        }
        startAutoRefresh();
        showToast(`Auto-refresh resumed (${configuredRefreshInterval}s).`, 'info');
      }
    }
  });

  // Top Bar Refresh Interval Selector
  const topIntervalSelect = document.getElementById('autoRefreshIntervalSelect');
  if (topIntervalSelect) {
    topIntervalSelect.value = String(configuredRefreshInterval);
    topIntervalSelect.addEventListener('change', (e) => {
      setRefreshInterval(e.target.value);
    });
  }

  // Settings Tab Refresh Interval Selector
  const settingsIntervalSelect = document.getElementById('settingsRefreshIntervalSelect');
  if (settingsIntervalSelect) {
    settingsIntervalSelect.value = String(configuredRefreshInterval);
    settingsIntervalSelect.addEventListener('change', (e) => {
      setRefreshInterval(e.target.value);
    });
  }

  // View Mode Toggles
  document.querySelectorAll('.view-toggle-btn').forEach(btn => {
    btn.addEventListener('click', () => {
      const tab = btn.dataset.tab;
      const mode = btn.dataset.view;
      if (tab && mode) {
        setViewMode(tab, mode);
      }
    });
  });

  // Fleet Filter & Search
  document.getElementById('fleetSearchInput')?.addEventListener('input', filterFleetGrid);
  document.querySelectorAll('.fleet-filter-btn').forEach(btn => {
    btn.addEventListener('click', (e) => {
      document.querySelectorAll('.fleet-filter-btn').forEach(b => b.classList.remove('active'));
      btn.classList.add('active');
      filterFleetGrid();
    });
  });

  // Probe Filter & Search
  document.getElementById('probesSearchInput')?.addEventListener('input', filterProbesGrid);
  document.querySelectorAll('.probe-filter-btn').forEach(btn => {
    btn.addEventListener('click', (e) => {
      document.querySelectorAll('.probe-filter-btn').forEach(b => b.classList.remove('active'));
      btn.classList.add('active');
      filterProbesGrid();
    });
  });

  // Domain Filter & Search
  document.getElementById('domainsSearchInput')?.addEventListener('input', () => renderDomainsGrid(cachedDomains));
  document.querySelectorAll('.domain-filter-btn').forEach(btn => {
    btn.addEventListener('click', (e) => {
      document.querySelectorAll('.domain-filter-btn').forEach(b => b.classList.remove('active'));
      btn.classList.add('active');
      renderDomainsGrid(cachedDomains);
    });
  });

  // Renewal Filter & Search
  document.getElementById('renewalsSearchInput')?.addEventListener('input', filterRenewalsGrid);
  document.querySelectorAll('.renewal-filter-btn').forEach(btn => {
    btn.addEventListener('click', (e) => {
      document.querySelectorAll('.renewal-filter-btn').forEach(b => b.classList.remove('active'));
      btn.classList.add('active');
      filterRenewalsGrid();
    });
  });

  // Alert Routes Filter & Search
  document.getElementById('alertRoutesSearchInput')?.addEventListener('input', filterAlertRoutes);
  document.querySelectorAll('.route-filter-btn').forEach(btn => {
    btn.addEventListener('click', (e) => {
      document.querySelectorAll('.route-filter-btn').forEach(b => b.classList.remove('active'));
      btn.classList.add('active');
      filterAlertRoutes();
    });
  });

  // Modal Triggers
  document.getElementById('openAddProbeBtn')?.addEventListener('click', () => openModal('addProbeModal'));
  document.getElementById('openAddRenewalBtn')?.addEventListener('click', () => openModal('addRenewalModal'));
  document.getElementById('openAddDomainBtn')?.addEventListener('click', () => openModal('addDomainModal'));

  // Add Domain & SSL Tracker Form
  document.getElementById('addDomainForm')?.addEventListener('submit', async (e) => {
    e.preventDefault();
    const domain = document.getElementById('domainNameInput')?.value.trim();
    const port = parseInt(document.getElementById('domainPortInput')?.value) || 443;
    const registrar = document.getElementById('domainRegistrarInput')?.value.trim() || undefined;
    const domain_expires_at = document.getElementById('domainExpiryInput')?.value || undefined;
    const submitBtn = document.getElementById('addDomainSubmitBtn');

    if (!domain) return;
    if (submitBtn) {
      submitBtn.disabled = true;
      submitBtn.textContent = 'Probing SSL & ICANN RDAP...';
    }

    try {
      const res = await apiFetch('/api/domains', {
        method: 'POST',
        body: JSON.stringify({ domain, port, registrar, domain_expires_at })
      });
      const data = await res.json();
      if (res.ok && data.success) {
        showToast(data.message, 'success');
        closeModal('addDomainModal');
        document.getElementById('addDomainForm')?.reset();
        fetchDomains();
        fetchOverview();
      } else {
        showToast(data.detail || 'Could not probe domain.', 'error');
      }
    } catch (err) {
      showToast('Network error adding domain tracker.', 'error');
    } finally {
      if (submitBtn) {
        submitBtn.disabled = false;
        submitBtn.textContent = 'Probe & Track Domain';
      }
    }
  });

  // Edit Domain Governance Form
  document.getElementById('editDomainForm')?.addEventListener('submit', async (e) => {
    e.preventDefault();
    const domainId = document.getElementById('editDomainIdInput')?.value;
    const port = parseInt(document.getElementById('editDomainPortInput')?.value) || 443;
    const registrar = document.getElementById('editDomainRegistrarInput')?.value.trim();
    const domain_expires_at = document.getElementById('editDomainExpiryInput')?.value || '';
    const submitBtn = document.getElementById('editDomainSubmitBtn');

    if (!domainId) return;
    if (submitBtn) {
      submitBtn.disabled = true;
      submitBtn.textContent = 'Saving...';
    }

    try {
      const res = await apiFetch(`/api/domains/${domainId}`, {
        method: 'PUT',
        body: JSON.stringify({ port, registrar, domain_expires_at })
      });
      const data = await res.json();
      if (res.ok && data.success) {
        showToast(data.message || 'Domain governance updated!', 'success');
        closeModal('editDomainModal');
        fetchDomains();
        fetchOverview();
      } else {
        showToast(data.detail || 'Could not update domain.', 'error');
      }
    } catch (err) {
      showToast('Network error updating domain governance.', 'error');
    } finally {
      if (submitBtn) {
        submitBtn.disabled = false;
        submitBtn.textContent = 'Save Governance';
      }
    }
  });

  // Domains Filter & Search
  document.getElementById('domainsSearchInput')?.addEventListener('input', filterDomainsGrid);
  document.querySelectorAll('.domain-filter-btn').forEach(btn => {
    btn.addEventListener('click', () => {
      document.querySelectorAll('.domain-filter-btn').forEach(b => b.classList.remove('active'));
      btn.classList.add('active');
      filterDomainsGrid();
    });
  });

  // Add Probe Form
  document.getElementById('addProbeForm').addEventListener('submit', async (e) => {
    e.preventDefault();
    const payload = {
      name: document.getElementById('probeNameInput').value,
      url: document.getElementById('probeUrlInput').value,
      expected_status: parseInt(document.getElementById('probeExpectedStatusInput').value) || 200,
      timeout_seconds: parseInt(document.getElementById('probeTimeoutInput').value) || 5
    };
    try {
      const res = await apiFetch('/api/probes', { method: 'POST', body: JSON.stringify(payload) });
      const data = await res.json();
      if (res.ok && data.success) {
        showToast(data.message, 'success');
        closeModal('addProbeModal');
        document.getElementById('addProbeForm').reset();
        fetchProbes();
      } else {
        showToast(data.detail || 'Failed to add service health check.', 'error');
      }
    } catch (err) {
      showToast('Network error adding service health check.', 'error');
    }
  });

  // Add Renewal Form
  document.getElementById('addRenewalForm').addEventListener('submit', async (e) => {
    e.preventDefault();
    const amt = document.getElementById('renewalAmountInput').value;
    const payload = {
      name: document.getElementById('renewalNameInput').value,
      category: document.getElementById('renewalCategoryInput').value,
      due_date: document.getElementById('renewalDueDateInput').value,
      amount: amt ? parseFloat(amt) : null,
      currency: 'INR',
      recurrence: document.getElementById('renewalRecurrenceInput').value,
      notes: document.getElementById('renewalNotesInput').value,
      remind_days_before: 7
    };
    try {
      const res = await apiFetch('/api/renewals', { method: 'POST', body: JSON.stringify(payload) });
      const data = await res.json();
      if (res.ok && data.success) {
        showToast(data.message, 'success');
        closeModal('addRenewalModal');
        document.getElementById('addRenewalForm').reset();
        fetchRenewals();
      } else {
        showToast(data.detail || 'Failed to add renewal.', 'error');
      }
    } catch (err) {
      showToast('Network error adding renewal.', 'error');
    }
  });

  // Theme Toggle Button
  const themeBtn = document.getElementById('themeToggleBtn');
  if (themeBtn) {
    themeBtn.addEventListener('click', toggleTheme);
  }

  // Edit Renewal Form
  document.getElementById('editRenewalForm').addEventListener('submit', async (e) => {
    e.preventDefault();
    const renewalId = document.getElementById('editRenewalId').value;
    const amt = document.getElementById('editRenewalAmountInput').value;
    const payload = {
      name: document.getElementById('editRenewalNameInput').value.trim(),
      category: document.getElementById('editRenewalCategoryInput').value,
      due_date: document.getElementById('editRenewalDueDateInput').value,
      amount: amt ? parseFloat(amt) : null,
      currency: 'INR',
      recurrence: document.getElementById('editRenewalRecurrenceInput').value,
      notes: document.getElementById('editRenewalNotesInput').value.trim(),
      remind_days_before: 7
    };
    try {
      const res = await apiFetch(`/api/renewals/${renewalId}`, {
        method: 'PUT',
        body: JSON.stringify(payload)
      });
      const data = await res.json();
      if (res.ok && data.success) {
        showToast(data.message || 'Renewal updated.', 'success');
        closeModal('editRenewalModal');
        fetchRenewals();
      } else {
        showToast(data.detail || 'Failed to update renewal.', 'error');
      }
    } catch (err) {
      showToast('Network error updating renewal.', 'error');
    }
  });

  // Settings Form
  // Incident filter buttons
  document.querySelectorAll('.incident-filter-btn').forEach(btn => {
    btn.addEventListener('click', (e) => {
      document.querySelectorAll('.incident-filter-btn').forEach(b => b.classList.remove('active'));
      e.target.classList.add('active');
      currentIncidentFilter = e.target.dataset.filter || 'all';
      renderIncidentsFeed();
    });
  });

  const incSearch = document.getElementById('incidentsSearchInput');
  if (incSearch) {
    incSearch.addEventListener('input', () => {
      renderIncidentsFeed();
    });
  }

  const openAddRouteBtn = document.getElementById('openAddRouteBtn');
  if (openAddRouteBtn) {
    openAddRouteBtn.addEventListener('click', openAddRouteModal);
  }
  const routeForm = document.getElementById('routeForm');
  if (routeForm) {
    routeForm.addEventListener('submit', handleRouteFormSubmit);
  }

  const settingsForm = document.getElementById('settingsForm');
  if (settingsForm) {
    settingsForm.addEventListener('submit', async (e) => {
      e.preventDefault();
      const alertChatId = document.getElementById('settingAlertChatId').value.trim();
      try {
        const res = await apiFetch('/api/settings', {
          method: 'POST',
          body: JSON.stringify({ alert_chat_id: alertChatId })
        });
        const data = await res.json();
        if (res.ok && data.success) {
          showToast(data.message || 'Settings saved successfully!', 'success');
        } else {
          showToast(data.detail || 'Failed to update settings.', 'error');
        }
      } catch (err) {
        showToast('Network error updating settings.', 'error');
      }
    });
  }

  // ── AI Engine Settings Form & Dynamic Discovery Controls ───────────────────
  const aiProviderSel = document.getElementById('aiProviderSelect');
  if (aiProviderSel) {
    aiProviderSel.addEventListener('change', (e) => {
      aiSettingsUserInteracted = true;
      const selected = e.target.value;
      const keyInp = document.getElementById('aiApiKeyInput');
      const baseUrlInp = document.getElementById('aiBaseUrlInput');
      const hintEl = document.getElementById('aiProviderHint');

      if (hintEl) {
        if (selected === 'gemini') {
          hintEl.textContent = 'Native direct Google Gemini API (gemini-1.5-flash, gemini-1.5-pro, etc.).';
        } else if (selected === 'openai') {
          hintEl.textContent = 'Direct OpenAI API (GPT-4o, GPT-4o-mini, o3-mini, o1).';
        } else if (selected === 'anthropic') {
          hintEl.textContent = 'Anthropic Claude API (Claude 3.5 Sonnet, Claude 3.5 Haiku).';
        } else if (selected === 'ollama') {
          hintEl.textContent = 'Self-hosted local LLM via Ollama or private OpenAI-compatible endpoint.';
        }
      }

      if (keyInp) {
        if (selected === 'gemini') {
          keyInp.placeholder = 'AIzaSy... (Google AI Studio Key)';
        } else if (selected === 'openai') {
          keyInp.placeholder = 'sk-proj-... (OpenAI API Key)';
        } else if (selected === 'anthropic') {
          keyInp.placeholder = 'sk-ant-... (Anthropic API Key)';
        } else if (selected === 'ollama') {
          keyInp.placeholder = 'Optional / Not required for Ollama';
        }
      }

      if (baseUrlInp && selected === 'ollama' && !baseUrlInp.value) {
        baseUrlInp.value = 'http://localhost:11434';
      }

      // Populate presets or cached models for new provider
      populateAiModelSelect(selected, null);

      const banner = document.getElementById('aiTestResultBanner');
      if (banner) banner.style.display = 'none';
    });
  }

  // Model Select Change Handler
  const aiModelSel = document.getElementById('aiModelSelect');
  if (aiModelSel) {
    aiModelSel.addEventListener('change', (e) => {
      aiSettingsUserInteracted = true;
      const val = e.target.value;
      const customWrap = document.getElementById('aiCustomModelWrap');
      const customInp = document.getElementById('aiModelCustomInput');
      const legacyInp = document.getElementById('aiModelInput');

      if (val === 'custom') {
        if (customWrap) customWrap.style.display = 'block';
        if (customInp) {
          customInp.focus();
          if (legacyInp) legacyInp.value = customInp.value.trim();
        }
      } else {
        if (customWrap) customWrap.style.display = 'none';
        if (legacyInp) legacyInp.value = val;
      }

      // Update preset chip active styling
      const presetsWrap = document.getElementById('aiModelPresets');
      if (presetsWrap) {
        presetsWrap.querySelectorAll('.ai-preset-chip').forEach(chip => {
          chip.classList.toggle('active', chip.textContent === val);
        });
      }
    });
  }

  // Custom Model Input Handler
  const aiModelCustomInp = document.getElementById('aiModelCustomInput');
  if (aiModelCustomInp) {
    aiModelCustomInp.addEventListener('input', (e) => {
      aiSettingsUserInteracted = true;
      const legacyInp = document.getElementById('aiModelInput');
      if (legacyInp) legacyInp.value = e.target.value.trim();
    });
  }

  // Track user typing in key or base URL
  ['aiApiKeyInput', 'aiBaseUrlInput'].forEach(id => {
    const el = document.getElementById(id);
    if (el) {
      el.addEventListener('input', () => { aiSettingsUserInteracted = true; });
    }
  });

  // Toggle API Key visibility
  const toggleKeyBtn = document.getElementById('toggleAiKeyVisibilityBtn');
  if (toggleKeyBtn) {
    toggleKeyBtn.addEventListener('click', () => {
      const keyInp = document.getElementById('aiApiKeyInput');
      if (!keyInp) return;
      if (keyInp.type === 'password') {
        keyInp.type = 'text';
        toggleKeyBtn.textContent = '🙈';
      } else {
        keyInp.type = 'password';
        toggleKeyBtn.textContent = '👁️';
      }
    });
  }

  // ── "⚡ Test Key & Fetch Models" Action ─────────────────────────────────────
  const fetchModelsBtn = document.getElementById('fetchAiModelsBtn');
  if (fetchModelsBtn) {
    fetchModelsBtn.addEventListener('click', async () => {
      aiSettingsUserInteracted = true;
      const provider = document.getElementById('aiProviderSelect').value;
      const apiKey = document.getElementById('aiApiKeyInput').value.trim();
      const baseUrl = document.getElementById('aiBaseUrlInput').value.trim();
      const banner = document.getElementById('aiTestResultBanner');

      fetchModelsBtn.disabled = true;
      fetchModelsBtn.textContent = '⚡ Querying Models...';

      if (banner) {
        banner.style.display = 'block';
        banner.className = 'ai-test-banner loading';
        banner.innerHTML = `<strong>🔄 Connecting to ${escapeHtml(provider.toUpperCase())} API...</strong><br><span style="font-size: 12px; opacity: 0.85;">Authenticating credentials and fetching available model catalog...</span>`;
      }

      try {
        const res = await apiFetch('/api/ai/models', {
          method: 'POST',
          body: JSON.stringify({
            provider,
            api_key: apiKey,
            base_url: baseUrl,
          }),
        });
        const data = await res.json();

        if (res.ok && data.success) {
          populateAiModelSelect(provider, data.default_model, data.models);

          if (banner) {
            banner.className = 'ai-test-banner success';
            banner.innerHTML = `
              <strong>✅ Key Verified & Connected (${data.latency_ms}ms)</strong><br>
              ${escapeHtml(data.message)}<br>
              <div style="margin-top: 6px; font-size: 12px; opacity: 0.9;">
                Loaded <strong>${data.count} models</strong> into the dropdown below. Select any model to use.
              </div>
            `;
          }
          showToast(`Discovered ${data.count} models from ${provider.toUpperCase()}!`, 'success');
        } else {
          const detail = data.detail || 'Failed to authenticate or fetch models from provider.';
          if (banner) {
            banner.className = 'ai-test-banner error';
            banner.innerHTML = `
              <strong>❌ Verification Failed${data.latency_ms ? ` (${data.latency_ms}ms)` : ''}</strong><br>
              ${escapeHtml(detail)}
            `;
          }
          showToast('Failed to verify API key or fetch models.', 'error');
        }
      } catch (err) {
        if (banner) {
          banner.className = 'ai-test-banner error';
          banner.innerHTML = `<strong>❌ Network Error</strong><br>Failed to reach OpsPilot backend API.`;
        }
        showToast('Network error during model discovery.', 'error');
      } finally {
        fetchModelsBtn.disabled = false;
        fetchModelsBtn.textContent = '⚡ Test Key & Fetch Models';
      }
    });
  }

  // ── Save AI Settings ───────────────────────────────────────────────────────
  const aiSettingsForm = document.getElementById('aiSettingsForm');
  if (aiSettingsForm) {
    aiSettingsForm.addEventListener('submit', async (e) => {
      e.preventDefault();
      const saveBtn = document.getElementById('saveAiSettingsBtn');
      const provider = document.getElementById('aiProviderSelect').value;
      const modelSel = document.getElementById('aiModelSelect');
      const customInp = document.getElementById('aiModelCustomInput');
      const apiKey = document.getElementById('aiApiKeyInput').value.trim();
      const baseUrl = document.getElementById('aiBaseUrlInput').value.trim();
      const enabled = document.getElementById('aiEnabledCheckbox').checked;

      let model = modelSel ? modelSel.value : '';
      if (model === 'custom' && customInp) {
        model = customInp.value.trim();
      }
      if (!model) {
        model = document.getElementById('aiModelInput').value.trim();
      }

      if (!model) {
        showToast('Please choose or enter a model identifier.', 'error');
        return;
      }

      saveBtn.disabled = true;
      saveBtn.textContent = 'Saving...';

      try {
        const res = await apiFetch('/api/settings/ai', {
          method: 'POST',
          body: JSON.stringify({
            provider,
            model,
            api_key: apiKey,
            base_url: baseUrl,
            enabled,
          }),
        });
        const data = await res.json();
        if (res.ok && data.success) {
          showToast(data.message || 'AI configuration saved successfully!', 'success');
          aiSettingsUserInteracted = false;
          const keyInp = document.getElementById('aiApiKeyInput');
          if (keyInp) {
            keyInp.value = '';
            keyInp.type = 'password';
            if (toggleKeyBtn) toggleKeyBtn.textContent = '👁️';
          }
          await fetchSettings();
        } else {
          showToast(data.detail || 'Failed to save AI configuration.', 'error');
        }
      } catch (err) {
        showToast('Network error saving AI configuration.', 'error');
      } finally {
        saveBtn.disabled = false;
        saveBtn.textContent = '💾 Save AI Configuration';
      }
    });
  }

  // ── "💬 Test Copilot Prompt" Action ────────────────────────────────────────
  const testAiBtn = document.getElementById('testAiConnectionBtn');
  if (testAiBtn) {
    testAiBtn.addEventListener('click', async () => {
      const banner = document.getElementById('aiTestResultBanner');
      const provider = document.getElementById('aiProviderSelect').value;
      const modelSel = document.getElementById('aiModelSelect');
      const customInp = document.getElementById('aiModelCustomInput');
      const apiKey = document.getElementById('aiApiKeyInput').value.trim();
      const baseUrl = document.getElementById('aiBaseUrlInput').value.trim();

      let model = modelSel ? modelSel.value : '';
      if (model === 'custom' && customInp) {
        model = customInp.value.trim();
      }
      if (!model) {
        model = document.getElementById('aiModelInput').value.trim() || 'gemini-1.5-flash';
      }

      testAiBtn.disabled = true;
      testAiBtn.textContent = '💬 Prompting...';
      if (banner) {
        banner.style.display = 'block';
        banner.className = 'ai-test-banner loading';
        banner.innerHTML = `<strong>🔄 Pinging ${escapeHtml(provider.toUpperCase())} (${escapeHtml(model)})...</strong><br><span style="font-size: 12px; opacity: 0.85;">Sending healthcheck probe prompt...</span>`;
      }

      try {
        const res = await apiFetch('/api/ai/test', {
          method: 'POST',
          body: JSON.stringify({
            provider,
            model,
            api_key: apiKey,
            base_url: baseUrl,
          }),
        });
        const data = await res.json();
        if (res.ok && data.success) {
          if (banner) {
            banner.className = 'ai-test-banner success';
            banner.innerHTML = `
              <strong>✅ Inference Successful (${data.latency_ms}ms)</strong><br>
              ${escapeHtml(data.message)}<br>
              <div style="margin-top: 6px; font-size: 12px; opacity: 0.9; font-family: 'JetBrains Mono', monospace; background: rgba(0,0,0,0.12); padding: 4px 8px; border-radius: 4px;">
                LLM response: "${escapeHtml(data.response || 'OpsPilot AI Online')}"
              </div>
            `;
          }
          showToast(`AI Inference Verified (${data.latency_ms}ms)!`, 'success');
        } else {
          const detail = data.detail || (data.message ? `${data.message}: ${data.response || ''}` : 'Connection failed');
          if (banner) {
            banner.className = 'ai-test-banner error';
            banner.innerHTML = `
              <strong>❌ Inference Failed${data.latency_ms ? ` (${data.latency_ms}ms)` : ''}</strong><br>
              ${escapeHtml(detail)}
            `;
          }
          showToast('AI Prompt Test Failed', 'error');
        }
      } catch (err) {
        if (banner) {
          banner.className = 'ai-test-banner error';
          banner.innerHTML = `<strong>❌ Network Error</strong><br>Failed to reach OpsPilot API endpoint.`;
        }
        showToast('Network error during AI test.', 'error');
      } finally {
        testAiBtn.disabled = false;
        testAiBtn.textContent = '💬 Test Copilot Prompt';
      }
    });
  }

  // Log Tail Select Change
  document.getElementById('logsTailSelect').addEventListener('change', (e) => {
    if (activeLogsContainer) {
      fetchLogs(activeLogsContainer, parseInt(e.target.value) || 100);
    }
  });

  // Copy Logs
  document.getElementById('copyLogsBtn').addEventListener('click', () => {
    const text = document.getElementById('logsTerminalContent').textContent;
    navigator.clipboard.writeText(text).then(() => {
      showToast('Logs copied to clipboard.', 'success');
    });
  });

  // Confirm Restart
  document.getElementById('confirmRestartBtn').addEventListener('click', async () => {
    if (!activeRestartContainer) return;
    const btn = document.getElementById('confirmRestartBtn');
    btn.disabled = true;
    btn.textContent = 'Restarting...';
    try {
      const res = await apiFetch(`/api/containers/${activeRestartContainer}/restart`, { method: 'POST' });
      const data = await res.json();
      if (res.ok && data.success) {
        showToast(data.message, 'success');
        closeModal('restartModal');
        fetchFleet();
      } else {
        showToast(data.detail || 'Restart failed.', 'error');
      }
    } catch (err) {
      showToast('Network error triggering restart.', 'error');
    } finally {
      btn.disabled = false;
      btn.textContent = 'Restart Container';
    }
  });

  // ── Sendrin-Style Sidebar & Navigation Interactivity ──────────────────────

  const sidebar = document.getElementById('appSidebar');
  const mainLayout = document.getElementById('mainLayoutArea');
  const collapseBtn = document.getElementById('sidebarCollapseBtn');
  const topToggleBtn = document.getElementById('topSidebarToggleBtn');
  const brandLink = document.getElementById('sidebarBrandLink');

  function setSidebarCollapsed(collapsed) {
    if (!sidebar || !mainLayout) return;
    sidebar.classList.toggle('collapsed', collapsed);
    mainLayout.classList.toggle('sidebar-collapsed', collapsed);
    if (collapseBtn) {
      collapseBtn.textContent = collapsed ? '»' : '«';
      collapseBtn.title = collapsed ? 'Expand Sidebar (⌘B)' : 'Collapse Sidebar (⌘B)';
    }
    localStorage.setItem('opspilot_sidebar_collapsed', collapsed ? 'true' : 'false');
  }

  const isCollapsed = localStorage.getItem('opspilot_sidebar_collapsed') === 'true';
  setSidebarCollapsed(isCollapsed);

  // Sidebar Collapse / Expand Toggle Buttons
  collapseBtn?.addEventListener('click', (e) => {
    e.stopPropagation();
    setSidebarCollapsed(!sidebar.classList.contains('collapsed'));
  });

  topToggleBtn?.addEventListener('click', () => {
    setSidebarCollapsed(!sidebar.classList.contains('collapsed'));
  });

  brandLink?.addEventListener('click', (e) => {
    if (sidebar?.classList.contains('collapsed')) {
      e.preventDefault();
      setSidebarCollapsed(false);
    }
  });

  // Global Keyboard Shortcut (⌘B / Ctrl+B)
  window.addEventListener('keydown', (e) => {
    if ((e.metaKey || e.ctrlKey) && e.key.toLowerCase() === 'b') {
      e.preventDefault();
      setSidebarCollapsed(!sidebar?.classList.contains('collapsed'));
    }
  });

  // Mobile Menu & Backdrop Drawer
  const mobileBtn = document.getElementById('mobileMenuBtn');
  const mobileBackdrop = document.getElementById('mobileBackdrop');
  mobileBtn?.addEventListener('click', () => {
    sidebar?.classList.toggle('open');
    mobileBackdrop?.classList.toggle('active');
  });
  mobileBackdrop?.addEventListener('click', () => {
    sidebar?.classList.remove('open');
    mobileBackdrop?.classList.remove('active');
  });

  // Manual Sync Now Button
  document.getElementById('manualSyncBtn')?.addEventListener('click', () => {
    const btn = document.getElementById('manualSyncBtn');
    if (btn) {
      btn.style.transform = 'rotate(180deg)';
      setTimeout(() => { btn.style.transform = ''; }, 450);
    }
    refreshAll();
    showToast('Synchronized with host telemetry.', 'info');
  });

  // Global Quick Search (⌘K / Ctrl+K)
  const globalSearch = document.getElementById('globalQuickSearch');
  window.addEventListener('keydown', (e) => {
    if ((e.metaKey || e.ctrlKey) && e.key.toLowerCase() === 'k') {
      e.preventDefault();
      globalSearch?.focus();
    }
  });
  globalSearch?.addEventListener('input', (e) => {
    const q = e.target.value.toLowerCase().trim();
    if (currentTab === 'fleet') {
      const el = document.getElementById('fleetSearchInput');
      if (el) { el.value = q; filterFleetGrid(); }
    } else if (currentTab === 'incidents') {
      const el = document.getElementById('incidentsSearchInput');
      if (el) { el.value = q; renderIncidentsFeed(); }
    } else if (currentTab === 'overview' && q.length > 0) {
      switchTab('fleet');
      const el = document.getElementById('fleetSearchInput');
      if (el) { el.value = q; filterFleetGrid(); }
    }
  });

  // One-Click Docker Prune
  const handleDockerPrune = async () => {
    if (!confirm('Are you sure you want to prune unused Docker build caches and stopped containers?')) return;
    try {
      const res = await apiFetch('/api/docker/prune', { method: 'POST' });
      const data = await res.json();
      if (res.ok && data.success) {
        showToast(data.message || 'Pruned Docker cache successfully!', 'success');
        refreshAll();
      } else {
        showToast(data.detail || 'Docker prune failed.', 'error');
      }
    } catch (err) {
      showToast('Error executing Docker prune.', 'error');
    }
  };
  document.getElementById('quickDockerPruneBtn')?.addEventListener('click', handleDockerPrune);

  // AI RCA Diagnostics Modal Triggers & Smart Auto-Pick
  async function ensureAiDiagDataLoaded() {
    if (!cachedContainers || cachedContainers.length === 0) {
      try {
        const res = await apiFetch('/api/containers');
        if (res.ok) cachedContainers = await res.json();
      } catch (err) {
        console.error('Error loading containers for RCA:', err);
      }
    }
    if (!cachedProbes || cachedProbes.length === 0) {
      try {
        const res = await apiFetch('/api/probes');
        if (res.ok) cachedProbes = await res.json();
      } catch (err) {
        console.error('Error loading probes for RCA:', err);
      }
    }
  }

  function renderAiDiagSelectors(filterQuery = '', selectedTarget = '') {
    const select = document.getElementById('aiDiagServiceSelect');
    const chipsBox = document.getElementById('aiDiagQuickChips');
    if (!select) return;

    const q = (filterQuery || '').toLowerCase().trim();
    const containers = cachedContainers || [];
    const probes = cachedProbes || [];
    const incidents = window.incidentsCache || [];

    // 1. Build Quick Chips: Prioritize Unhealthy / Exited / Alerting Workloads
    if (chipsBox) {
      const candidates = [];
      incidents.filter(i => !i.resolved_at).forEach(i => {
        if (!candidates.some(c => c.name === i.target)) {
          candidates.push({ name: i.target, label: `⚠️ ${i.target}`, status: i.severity || 'alerting' });
        }
      });
      containers.filter(c => c.status === 'exited' || c.health === 'unhealthy' || c.status === 'restarting').forEach(c => {
        if (!candidates.some(item => item.name === c.name)) {
          candidates.push({ name: c.name, label: `⚠️ ${c.name} (${c.health !== 'none' ? c.health : c.status})`, status: c.status });
        }
      });
      containers.filter(c => c.status === 'running' && c.health !== 'unhealthy').slice(0, 4).forEach(c => {
        if (!candidates.some(item => item.name === c.name)) {
          candidates.push({ name: c.name, label: `🐳 ${c.name}`, status: 'running' });
        }
      });

      chipsBox.innerHTML = candidates.map(c => `
        <button type="button" class="btn btn-secondary btn-sm" style="font-size: 11px; padding: 3px 8px; border-radius: 12px;" onclick="window.selectAiDiagTarget('${escapeHtml(c.name)}', '${escapeHtml(c.status)}')">
          ${escapeHtml(c.label)}
        </button>
      `).join('');
    }

    // 2. Filter Containers and Probes for Dropdown
    const matchingContainers = containers.filter(c => !q || c.name.toLowerCase().includes(q) || (c.image || '').toLowerCase().includes(q));
    const matchingProbes = probes.filter(p => !q || (p.name || '').toLowerCase().includes(q) || (p.url || '').toLowerCase().includes(q));

    let optionsHtml = '<option value="">-- Choose Workload, Container, or Endpoint --</option>';

    if (matchingContainers.length > 0) {
      optionsHtml += `<optgroup label="🐳 Docker Containers & Pods (${matchingContainers.length})">` +
        matchingContainers.map(c => {
          const isSelected = selectedTarget && selectedTarget === c.name ? 'selected' : '';
          const healthBadge = c.health !== 'none' ? ` [${c.health}]` : '';
          return `<option value="${escapeHtml(c.name)}" ${isSelected}>${escapeHtml(c.name)} (${c.status}${healthBadge})</option>`;
        }).join('') + `</optgroup>`;
    }

    if (matchingProbes.length > 0) {
      optionsHtml += `<optgroup label="🌐 Probes & Web Services (${matchingProbes.length})">` +
        matchingProbes.map(p => {
          const isSelected = selectedTarget && selectedTarget === p.name ? 'selected' : '';
          const statusText = p.is_healthy ? 'ONLINE' : `DOWN (${p.status_code || 0})`;
          return `<option value="${escapeHtml(p.name)}" ${isSelected}>${escapeHtml(p.name)} (${statusText})</option>`;
        }).join('') + `</optgroup>`;
    }

    if (selectedTarget && !matchingContainers.some(c => c.name === selectedTarget) && !matchingProbes.some(p => p.name === selectedTarget)) {
      optionsHtml = `<option value="${escapeHtml(selectedTarget)}" selected>⭐ ${escapeHtml(selectedTarget)} (Target Workload)</option>` + optionsHtml;
    }

    select.innerHTML = optionsHtml;
  }

  async function selectAiDiagTarget(targetName, explicitStatus = '', explicitLogs = '') {
    if (!targetName) return;
    const select = document.getElementById('aiDiagServiceSelect');
    const statusInput = document.getElementById('aiDiagStatusInput');
    const logsBox = document.getElementById('aiDiagLogsInput');
    const statusBadge = document.getElementById('aiDiagStatusBadge');
    const statusMsg = document.getElementById('aiDiagLogStatusMsg');

    if (select) {
      if (!Array.from(select.options).some(o => o.value === targetName)) {
        const newOpt = new Option(`⭐ ${targetName}`, targetName, true, true);
        select.add(newOpt);
      }
      select.value = targetName;
    }

    let status = explicitStatus;
    const c = (cachedContainers || []).find(item => item.name === targetName);
    const p = (cachedProbes || []).find(item => item.name === targetName);

    if (!status) {
      if (c) {
        status = c.health !== 'none' ? `${c.status} (${c.health})` : c.status;
      } else if (p) {
        status = p.is_healthy ? 'healthy' : `failing (HTTP ${p.status_code || 0})`;
      } else {
        status = 'active';
      }
    }

    if (statusInput) statusInput.value = status;
    if (statusBadge) {
      statusBadge.style.display = 'inline-block';
      statusBadge.textContent = status;
      statusBadge.className = `badge ${status.includes('unhealthy') || status.includes('exited') || status.includes('fail') ? 'badge-unhealthy' : 'badge-running'}`;
    }

    if (explicitLogs) {
      if (logsBox) logsBox.value = explicitLogs;
      if (statusMsg) statusMsg.textContent = '✓ Incident details populated';
      return;
    }

    if (c) {
      if (statusMsg) statusMsg.textContent = '🔄 Auto-pulling tail logs...';
      try {
        const res = await apiFetch(`/api/containers/${targetName}/logs?tail=100`);
        if (res.ok) {
          const logData = await res.json();
          if (logsBox) logsBox.value = logData.logs || '(No recent logs recorded for this container)';
          if (statusMsg) statusMsg.textContent = '✓ 100 log lines retrieved';
        }
      } catch (err) {
        if (statusMsg) statusMsg.textContent = '⚠ Could not pull logs';
      }
    } else {
      const inc = (window.incidentsCache || []).find(i => i.target === targetName && !i.resolved_at);
      if (inc && logsBox) {
        logsBox.value = `[INCIDENT CONTEXT] Target: ${inc.target}\nTitle: ${inc.title}\nDetail: ${inc.detail || 'None'}\nAlert Count: ${inc.alert_count}\nDetected: ${inc.created_at}`;
        if (statusMsg) statusMsg.textContent = '✓ Incident telemetry populated';
      } else if (logsBox && !logsBox.value) {
        logsBox.placeholder = 'Paste logs or stack trace for this workload...';
      }
    }
  }
  window.selectAiDiagTarget = selectAiDiagTarget;

  const openAiModal = async (targetName = '', targetStatus = '', initialLogs = '') => {
    openModal('aiDiagModal');
    const resBox = document.getElementById('aiDiagResultContainer');
    if (resBox) resBox.style.display = 'none';

    const searchInput = document.getElementById('aiDiagSearchInput');
    if (searchInput) searchInput.value = '';

    await ensureAiDiagDataLoaded();

    let target = targetName;
    if (!target) {
      const activeInc = (window.incidentsCache || []).find(i => !i.resolved_at);
      const degraded = (cachedContainers || []).find(c => c.status === 'exited' || c.health === 'unhealthy');
      if (activeInc) {
        target = activeInc.target;
        targetStatus = targetStatus || activeInc.severity;
        initialLogs = initialLogs || activeInc.detail;
      } else if (degraded) {
        target = degraded.name;
        targetStatus = targetStatus || degraded.status;
      } else if (cachedContainers && cachedContainers.length > 0) {
        target = cachedContainers[0].name;
      }
    }

    renderAiDiagSelectors('', target);

    if (target) {
      await selectAiDiagTarget(target, targetStatus, initialLogs);
    }
  };
  document.getElementById('openAiDiagBtn')?.addEventListener('click', () => openAiModal());
  window.openAiRcaModal = openAiModal;

  document.getElementById('aiDiagSearchInput')?.addEventListener('input', (e) => {
    const q = e.target.value;
    const currentSelected = document.getElementById('aiDiagServiceSelect')?.value;
    renderAiDiagSelectors(q, currentSelected);
  });

  document.getElementById('aiDiagServiceSelect')?.addEventListener('change', async (e) => {
    const name = e.target.value;
    if (name) {
      await selectAiDiagTarget(name);
    }
  });

  document.getElementById('aiFetchLogsBtn')?.addEventListener('click', async () => {
    const name = document.getElementById('aiDiagServiceSelect')?.value;
    if (!name) {
      showToast('Please select a service or container first.', 'error');
      return;
    }
    const statusMsg = document.getElementById('aiDiagLogStatusMsg');
    if (statusMsg) statusMsg.textContent = '🔄 Fetching...';
    try {
      const res = await apiFetch(`/api/containers/${name}/logs?tail=100`);
      if (res.ok) {
        const logData = await res.json();
        const logsBox = document.getElementById('aiDiagLogsInput');
        if (logsBox) logsBox.value = logData.logs || '';
        if (statusMsg) statusMsg.textContent = '✓ Tail logs updated';
        showToast('Refreshed tail logs.', 'info');
      }
    } catch (err) {
      if (statusMsg) statusMsg.textContent = '⚠ Failed to fetch';
      showToast('Could not retrieve logs.', 'error');
    }
  });

  document.getElementById('aiDiagForm')?.addEventListener('submit', async (e) => {
    e.preventDefault();
    const service_name = document.getElementById('aiDiagServiceSelect')?.value;
    const container_status = document.getElementById('aiDiagStatusInput')?.value || 'running';
    const logs = document.getElementById('aiDiagLogsInput')?.value || '';
    const submitBtn = document.getElementById('aiDiagSubmitBtn');

    if (!service_name) {
      showToast('Please select a target container.', 'error');
      return;
    }

    if (submitBtn) {
      submitBtn.disabled = true;
      submitBtn.innerHTML = '<span>⚡ Running AI RCA Engine...</span>';
    }

    try {
      const res = await apiFetch('/api/ai/rca', {
        method: 'POST',
        body: JSON.stringify({ service_name, container_status, logs })
      });
      const data = await res.json();
      if (res.ok && data.success) {
        const contentBox = document.getElementById('aiDiagResultContent');
        if (contentBox) contentBox.innerHTML = renderMarkdown(data.diagnosis);
        const resBox = document.getElementById('aiDiagResultContainer');
        if (resBox) resBox.style.display = 'block';
        showToast('AI Incident Diagnosis complete.', 'success');
      } else {
        showToast(data.detail || 'Analysis could not be completed.', 'error');
      }
    } catch (err) {
      showToast('Network error contacting AI RCA endpoint.', 'error');
    } finally {
      if (submitBtn) {
        submitBtn.disabled = false;
        submitBtn.innerHTML = '<span>✨ Run AI Root Cause Diagnosis</span>';
      }
    }
  });

  document.getElementById('copyAiReportBtn')?.addEventListener('click', () => {
    const text = document.getElementById('aiDiagResultContent')?.innerText || '';
    navigator.clipboard.writeText(text).then(() => {
      showToast('Copied diagnosis report to clipboard!', 'success');
    });
  });
}

function renderMarkdown(md) {
  if (!md) return '';
  return escapeHtml(md)
    .replace(/^### (.*$)/gim, '<h3 style="font-size:16px; margin: 12px 0 6px; font-weight:700;">$1</h3>')
    .replace(/^#### (.*$)/gim, '<h4 style="font-size:14px; margin: 10px 0 4px; font-weight:600; color:var(--accent-cyan);">$1</h4>')
    .replace(/\*\*(.*?)\*\*/gim, '<strong>$1</strong>')
    .replace(/`(.*?)`/gim, '<code style="background:rgba(255,255,255,0.08); padding:2px 5px; border-radius:4px; font-family:monospace; font-size:12px;">$1</code>')
    .replace(/\n\n/gim, '<br><br>')
    .replace(/\n/gim, '<br>');
}

function switchTab(tabId) {
  let targetTab = tabId;
  let shouldScrollToRoutes = false;
  if (tabId === 'routes') {
    targetTab = 'settings';
    shouldScrollToRoutes = true;
  }

  currentTab = targetTab;
  document.querySelectorAll('.tab-btn').forEach(btn => {
    btn.classList.toggle('active', btn.dataset.tab === targetTab);
  });
  document.querySelectorAll('.tab-content').forEach(section => {
    section.classList.toggle('active', section.id === `tab-${targetTab}`);
  });

  // Dynamic breadcrumb label
  const titles = {
    overview: 'Overview & Telemetry',
    fleet: 'Docker Service Fleet',
    probes: 'Service Health & Uptime Checks',
    domains: 'SSL Certificates & Domain Governance',
    renewals: 'Renewals & Client Billing',
    incidents: 'Incident Audit & Reliability History',
    settings: 'Settings & Alert Routing Matrix',
  };
  const breadcrumb = document.getElementById('currentBreadcrumbTab');
  if (breadcrumb) {
    breadcrumb.textContent = titles[targetTab] || targetTab;
  }

  // Close mobile drawer if active
  document.getElementById('appSidebar')?.classList.remove('open');
  document.getElementById('mobileBackdrop')?.classList.remove('active');

  if (targetTab === 'overview') fetchOverview();
  else if (targetTab === 'fleet') fetchFleet();
  else if (targetTab === 'probes') fetchProbes();
  else if (targetTab === 'domains') fetchDomains();
  else if (targetTab === 'renewals') fetchRenewals();
  else if (targetTab === 'incidents') fetchIncidents();
  else if (targetTab === 'settings') {
    fetchSettings();
    if (shouldScrollToRoutes) {
      setTimeout(() => {
        document.getElementById('alertRoutingMatrixCard')?.scrollIntoView({ behavior: 'smooth' });
      }, 100);
    }
  }
}

// ── Polling & Auto-Refresh ──────────────────────────────────────────────────

function setRefreshInterval(seconds, notify = true) {
  configuredRefreshInterval = parseInt(seconds, 10);
  localStorage.setItem('opspilot_refresh_interval', configuredRefreshInterval);

  const topSelect = document.getElementById('autoRefreshIntervalSelect');
  if (topSelect) topSelect.value = String(configuredRefreshInterval);
  const settingsSelect = document.getElementById('settingsRefreshIntervalSelect');
  if (settingsSelect) settingsSelect.value = String(configuredRefreshInterval);

  const btn = document.getElementById('refreshTimerBtn');
  const el = document.getElementById('refreshTimerCountdown');

  if (configuredRefreshInterval === 0) {
    autoRefreshPaused = true;
    stopAutoRefresh();
    if (btn) {
      btn.innerHTML = '⏸ Paused';
      btn.style.borderColor = 'var(--color-warning)';
    }
    if (el) el.textContent = 'Paused';
    if (notify) showToast('Auto-refresh disabled. Use "↻ Sync" for manual on-demand updates.', 'info');
  } else {
    autoRefreshPaused = false;
    refreshCountdown = configuredRefreshInterval;
    if (btn) {
      btn.innerHTML = `⏱ <span id="refreshTimerCountdown">${refreshCountdown}s</span>`;
      btn.style.borderColor = 'var(--border-subtle)';
    }
    startAutoRefresh();
    if (notify) showToast(`Auto-refresh cadence set to ${configuredRefreshInterval}s.`, 'success');
  }
}

function startAutoRefresh() {
  stopAutoRefresh();
  if (configuredRefreshInterval === 0 || autoRefreshPaused) {
    const el = document.getElementById('refreshTimerCountdown');
    if (el) el.textContent = 'Paused';
    return;
  }

  if (refreshCountdown <= 0 || refreshCountdown > configuredRefreshInterval) {
    refreshCountdown = configuredRefreshInterval;
  }

  const el = document.getElementById('refreshTimerCountdown');
  if (el) el.textContent = `${refreshCountdown}s`;
  const el2 = document.getElementById('overviewRefreshTicker');
  if (el2) el2.textContent = `${refreshCountdown}s`;

  timerInterval = setInterval(() => {
    if (autoRefreshPaused || configuredRefreshInterval === 0) return;
    refreshCountdown--;
    const tEl = document.getElementById('refreshTimerCountdown');
    if (tEl) tEl.textContent = `${refreshCountdown}s`;
    const tEl2 = document.getElementById('overviewRefreshTicker');
    if (tEl2) tEl2.textContent = `${refreshCountdown}s`;

    if (refreshCountdown <= 0) {
      refreshCountdown = configuredRefreshInterval;
      refreshAll();
    }
  }, 1000);
}

function stopAutoRefresh() {
  if (timerInterval) clearInterval(timerInterval);
}

function refreshAll() {
  if (currentTab === 'overview') fetchOverview();
  else if (currentTab === 'fleet') fetchFleet();
  else if (currentTab === 'probes') fetchProbes();
  else if (currentTab === 'domains') fetchDomains();
  else if (currentTab === 'renewals') fetchRenewals();
  else if (currentTab === 'incidents') fetchIncidents();
  else if (currentTab === 'settings') fetchSettings();
}

// ── Tab 1: Overview ─────────────────────────────────────────────────────────

async function fetchOverview() {
  try {
    const res = await apiFetch('/api/overview');
    if (!res.ok) return;
    const data = await res.json();
    if (data.csrf_token) csrfToken = data.csrf_token;

    // Metrics
    const m = data.metrics || {};
    document.getElementById('cpuMetricVal').textContent = `${m.cpu_percent || 0}%`;
    document.getElementById('cpuProgressBar').style.width = `${Math.min(100, m.cpu_percent || 0)}%`;
    document.getElementById('loadAvgVal').textContent = `Load: ${(m.load_avg || []).slice(0, 3).join(', ')}`;

    // Memory (Mathematically accurate and consistent)
    document.getElementById('ramMetricVal').textContent = `${m.ram_percent || 0}%`;
    document.getElementById('ramProgressBar').style.width = `${Math.min(100, m.ram_percent || 0)}%`;
    document.getElementById('ramUsedTotalVal').textContent = `${m.ram_used_gb || 0} GB / ${m.ram_total_gb || 0} GB`;

    document.getElementById('diskMetricVal').textContent = `${m.disk_percent || 0}%`;
    document.getElementById('diskProgressBar').style.width = `${Math.min(100, m.disk_percent || 0)}%`;
    document.getElementById('diskFreeVal').textContent = `Free: ${m.disk_free_gb || 0} GB`;

    // Service Availability SLA (Computed from actual active checks)
    const healthRate = data.health_percentage != null ? data.health_percentage : 100;
    const fleetRateEl = document.getElementById('fleetHealthyRate');
    if (fleetRateEl) fleetRateEl.textContent = `${healthRate}%`;
    const availProgress = document.getElementById('availProgressBar');
    if (availProgress) availProgress.style.width = `${Math.min(100, healthRate)}%`;
    document.getElementById('uptimeVal').textContent = `Host Uptime: ${m.uptime_human || '--'}`;

    // Sidebar mini telemetry
    const sideCpuBar = document.getElementById('sidebarCpuBar');
    const sideCpuVal = document.getElementById('sidebarCpuVal');
    const sideRamBar = document.getElementById('sidebarRamBar');
    const sideRamVal = document.getElementById('sidebarRamVal');
    if (sideCpuBar) sideCpuBar.style.width = `${m.cpu_percent || 0}%`;
    if (sideCpuVal) sideCpuVal.textContent = `${m.cpu_percent || 0}%`;
    if (sideRamBar) sideRamBar.style.width = `${m.ram_percent || 0}%`;
    if (sideRamVal) sideRamVal.textContent = `${m.ram_percent || 0}%`;

    // Counts & Badges
    const c = data.counts || {};
    document.getElementById('navFleetCount').textContent = c.containers_total || 0;
    document.getElementById('navProbesCount').textContent = c.endpoints_total || 0;
    const navDom = document.getElementById('navDomainsCount');
    if (navDom) navDom.textContent = c.domains_total || 0;
    document.getElementById('navRenewalsCount').textContent = c.renewals_pending || 0;
    document.getElementById('navIncidentsCount').textContent = c.incidents_open || 0;

    // Header Host Identity & Sync Time
    const hostNodeEl = document.getElementById('overviewHostName');
    if (hostNodeEl) hostNodeEl.textContent = data.server_name || 'node-01';

    const lastSyncEl = document.getElementById('lastUpdatedTime');
    if (lastSyncEl) {
      lastSyncEl.textContent = new Date().toLocaleTimeString([], { hour: '2-digit', minute: '2-digit', second: '2-digit' });
    }

    // Overall Status Computation (HEALTHY, DEGRADED, DOWN)
    const heroDot = document.getElementById('heroStatusDot');
    const heroTitle = document.getElementById('heroStatusTitle');
    const overallBadge = document.getElementById('overallHealthBadge');
    const globalDot = document.getElementById('globalStatusDot');
    const alertBanner = document.getElementById('attentionAlertBanner');
    const alertTitle = document.getElementById('attentionAlertTitle');
    const alertDesc = document.getElementById('attentionAlertDesc');
    const alertIcon = document.getElementById('attentionAlertIcon');

    const sysStatus = data.system_status || (
      (c.incidents_open > 0 || c.containers_unhealthy > 0 || c.endpoints_unhealthy > 0)
        ? ((c.endpoints_unhealthy > 0 && c.endpoints_healthy === 0) ? 'DOWN' : 'DEGRADED')
        : 'HEALTHY'
    );

    if (sysStatus === 'HEALTHY') {
      if (globalDot) globalDot.className = 'status-dot green';
      if (heroDot) heroDot.className = 'status-dot green pulse-beacon';
      if (heroTitle) heroTitle.textContent = 'All Systems Operational';
      if (overallBadge) {
        overallBadge.className = 'badge badge-running';
        overallBadge.textContent = '100% HEALTHY';
      }
      if (alertBanner) alertBanner.style.display = 'none';
    } else if (sysStatus === 'DEGRADED') {
      if (globalDot) globalDot.className = 'status-dot amber';
      if (heroDot) heroDot.className = 'status-dot amber pulse-beacon';
      if (heroTitle) heroTitle.textContent = 'System Degraded';
      if (overallBadge) {
        overallBadge.className = 'badge badge-snoozed';
        overallBadge.textContent = `${healthRate}% AVAIL`;
      }
      if (alertBanner) {
        alertBanner.style.display = 'flex';
        alertBanner.className = 'attention-alert-bar warning';
        if (alertIcon) alertIcon.textContent = '⚠️';
        if (alertTitle) alertTitle.textContent = 'Attention Required: Degraded Health';
        const issues = [];
        if (c.endpoints_unhealthy > 0) issues.push(`${c.endpoints_unhealthy} service(s) failing`);
        if (c.containers_unhealthy > 0) issues.push(`${c.containers_unhealthy} container(s) unhealthy`);
        if (c.incidents_open > 0) issues.push(`${c.incidents_open} open incident(s)`);
        if (alertDesc) alertDesc.textContent = issues.join(' · ') || 'System operating in degraded capacity.';
      }
    } else {
      // DOWN
      if (globalDot) globalDot.className = 'status-dot red';
      if (heroDot) heroDot.className = 'status-dot red pulse-beacon';
      if (heroTitle) heroTitle.textContent = 'Critical Outage Detected';
      if (overallBadge) {
        overallBadge.className = 'badge badge-exited';
        overallBadge.textContent = `${healthRate}% AVAIL`;
      }
      if (alertBanner) {
        alertBanner.style.display = 'flex';
        alertBanner.className = 'attention-alert-bar';
        if (alertIcon) alertIcon.textContent = '🚨';
        if (alertTitle) alertTitle.textContent = 'Critical System Failure';
        if (alertDesc) alertDesc.textContent = 'Immediate operator intervention required: multiple core health checks failing.';
      }
    }

    // Recent Incidents list in Overview
    const incBox = document.getElementById('overviewIncidentsContainer');
    const recent = data.recent_incidents || [];
    if (recent.length === 0) {
      incBox.innerHTML = '<div style="color: var(--text-muted); font-size: 13.5px; text-align: center; padding: 20px 0;">✅ No active incidents. All services operating normally.</div>';
    } else {
      incBox.innerHTML = recent.map(inc => `
        <div style="display: flex; justify-content: space-between; align-items: center; padding: 12px 0; border-bottom: 1px solid var(--border-subtle);">
          <div>
            <strong style="color: var(--color-danger);">${escapeHtml(inc.target)}</strong> — ${escapeHtml(inc.title)}
            <div style="font-size: 12px; color: var(--text-muted); margin-top: 2px;">${escapeHtml(inc.detail || '')} · Alert #${inc.alert_count}</div>
          </div>
          <div style="display: flex; gap: 6px;">
            <button class="btn btn-primary btn-sm" onclick="openAiRcaModal('${escapeHtml(inc.target)}', '${escapeHtml(inc.severity)}', '${escapeHtml(inc.detail || '')}')">🤖 AI RCA</button>
            <button class="btn btn-secondary btn-sm" onclick="resolveIncident(${inc.id})">Resolve</button>
          </div>
        </div>
      `).join('');
    }

    // Populate Overview Dual-Deck Widgets & Fleet Table
    await populateOverviewWidgets();
  } catch (err) {
    console.error('Error fetching overview:', err);
  }
}

async function populateOverviewWidgets() {
  try {
    // 1. Live Probes Table (Compact, Readable, Explicit Status Labels & Latencies)
    const probesRes = await apiFetch('/api/probes');
    if (probesRes.ok) {
      const probes = await probesRes.json();
      const probesTbody = document.getElementById('overviewProbesTbody');
      if (probesTbody) {
        if (probes.length === 0) {
          probesTbody.innerHTML = '<tr><td colspan="4" style="text-align: center; color: var(--text-muted); padding: 18px;">No service health checks configured.</td></tr>';
        } else {
          probesTbody.innerHTML = probes.slice(0, 6).map(p => {
            const isHealthy = Boolean(p.is_healthy);
            const statusLabel = isHealthy 
              ? 'ONLINE' 
              : (p.status_code ? `HTTP ${p.status_code}` : 'DOWN');
            const statusClass = isHealthy ? 'badge-running' : 'badge-exited';
            const latencyStr = (isHealthy && p.latency_ms > 0) ? `${p.latency_ms}ms` : '—';
            const latencyClass = !isHealthy 
              ? 'latency-unavailable' 
              : (p.latency_ms < 300 ? 'latency-fast' : (p.latency_ms > 1500 ? 'latency-slow' : 'latency-warning'));

            return `
              <tr>
                <td>
                  <div style="font-weight: 600; color: var(--text-primary);">${escapeHtml(p.name)}</div>
                  <div style="font-size: 11px; color: var(--text-muted); font-family: 'JetBrains Mono', monospace;">${escapeHtml(p.url)}</div>
                </td>
                <td>
                  <span class="status-dot ${isHealthy ? 'green' : 'red'}" style="margin-right: 6px;"></span>
                  <span class="badge ${statusClass}" style="font-size: 10px;">${escapeHtml(statusLabel)}</span>
                </td>
                <td>
                  <span class="latency-pill ${latencyClass}">
                    ${latencyStr}
                  </span>
                </td>
                <td style="text-align: right; color: var(--text-muted); font-size: 12px;">
                  Just now
                </td>
              </tr>
            `;
          }).join('');
        }
      }
    }

    // 2. Docker Fleet Lower Section Table
    const contRes = await apiFetch('/api/containers');
    if (contRes.ok) {
      const conts = await contRes.json();
      const fleetTbody = document.getElementById('overviewFleetTbody');
      const runningCountEl = document.getElementById('overviewFleetRunningCount');
      if (runningCountEl) {
        const running = conts.filter(c => c.status === 'running').length;
        runningCountEl.textContent = `${running} RUNNING`;
      }
      if (fleetTbody) {
        if (conts.length === 0) {
          fleetTbody.innerHTML = '<tr><td colspan="5" style="text-align: center; color: var(--text-muted); padding: 18px;">No Docker containers detected.</td></tr>';
        } else {
          fleetTbody.innerHTML = conts.slice(0, 6).map(c => {
            const isRunning = c.status === 'running';
            const statusBadge = isRunning ? 'badge-running' : (c.status === 'snoozed' ? 'badge-snoozed' : 'badge-exited');
            const healthBadge = c.health === 'healthy' 
              ? '<span class="badge badge-running" style="font-size: 10px;">healthy</span>'
              : (c.health === 'unhealthy' ? '<span class="badge badge-exited" style="font-size: 10px;">unhealthy</span>' : '<span style="color: var(--text-muted); font-size: 12px;">—</span>');
            return `
              <tr>
                <td>
                  <div style="font-weight: 600; color: var(--text-primary);">${escapeHtml(c.name)}</div>
                </td>
                <td>
                  <span style="font-size: 11.5px; color: var(--text-muted); font-family: 'JetBrains Mono', monospace;">${escapeHtml(c.image || 'docker-image')}</span>
                </td>
                <td>
                  <span class="badge ${statusBadge}" style="font-size: 10.5px;">${escapeHtml(c.status)}</span>
                </td>
                <td>
                  ${healthBadge}
                </td>
                <td style="text-align: right;">
                  <button class="btn btn-secondary btn-sm" onclick="openLogsModal('${escapeHtml(c.name)}')">📋 Logs</button>
                  <button class="btn btn-secondary btn-sm" onclick="openRestartModal('${escapeHtml(c.name)}')">🔄 Restart</button>
                </td>
              </tr>
            `;
          }).join('');
        }
      }
    }

    // 3. SSL & Domain Radar
    const domRes = await apiFetch('/api/domains');
    if (domRes.ok) {
      const doms = await domRes.json();
      const radar = document.getElementById('overviewDomainsRadar');
      if (radar) {
        if (doms.length === 0) {
          radar.innerHTML = '<div style="color: var(--text-muted); font-size: 13px;">No tracked domains.</div>';
        } else {
          radar.innerHTML = doms.slice(0, 4).map(d => {
            let sslClass = 'badge-running';
            if (d.days_remaining <= 0 || !d.is_valid) sslClass = 'badge-exited';
            else if (d.days_remaining < 30) sslClass = 'badge-snoozed';

            let domBadge = '';
            if (d.domain_days_remaining > 0) {
              const domClass = d.domain_days_remaining < 60 ? 'color: var(--color-warning);' : 'color: var(--accent-cyan);';
              domBadge = `<span style="font-size: 11px; ${domClass} font-family: 'JetBrains Mono', monospace;">Dom: ${d.domain_days_remaining}d</span>`;
            } else if (d.domain_expires_at) {
              domBadge = `<span style="font-size: 11px; color: var(--color-danger);">Dom: Due</span>`;
            }

            return `
              <div class="overview-radar-item">
                <div>
                  <span class="overview-radar-domain">${escapeHtml(d.domain)}</span>
                  ${domBadge ? `<div style="margin-top: 2px;">${domBadge}</div>` : ''}
                </div>
                <div style="display: flex; gap: 6px; align-items: center;">
                  <span class="badge ${sslClass}">SSL: ${d.days_remaining > 0 ? d.days_remaining + 'd' : 'Expired'}</span>
                </div>
              </div>
            `;
          }).join('');
        }
      }
    }

    // 4. Renewals Horizon
    const renRes = await apiFetch('/api/renewals');
    if (renRes.ok) {
      const rens = await renRes.json();
      const renHorizon = document.getElementById('overviewRenewalsHorizon');
      if (renHorizon) {
        if (rens.length === 0) {
          renHorizon.innerHTML = '<div style="color: var(--text-muted); font-size: 13px;">No upcoming renewals.</div>';
        } else {
          renHorizon.innerHTML = rens.slice(0, 2).map(r => `
            <div class="overview-radar-item">
              <div>
                <strong style="font-size: 13px; color: var(--text-primary); display: block;">${escapeHtml(r.name)}</strong>
                <span style="font-size: 11px; color: var(--text-muted);">Due: ${escapeHtml(r.due_date)}</span>
              </div>
              <span class="badge badge-snoozed">₹${Number(r.amount || 0).toLocaleString('en-IN')}</span>
            </div>
          `).join('');
        }
      }
    }
  } catch (e) {
    console.error('Error populating overview widgets:', e);
  }
}

// ── Tab 2: Docker Fleet ─────────────────────────────────────────────────────

async function fetchFleet() {
  try {
    const res = await apiFetch('/api/containers');
    if (!res.ok) return;
    cachedContainers = await res.json();
    document.getElementById('navFleetCount').textContent = cachedContainers.length;
    filterFleetGrid();
  } catch (err) {
    console.error('Error fetching fleet:', err);
  }
}

function filterFleetGrid() {
  const total = cachedContainers.length;
  const running = cachedContainers.filter(c => c.status === 'running' && c.health !== 'unhealthy').length;
  const exited = cachedContainers.filter(c => c.status === 'exited' || c.health === 'unhealthy').length;
  const snoozed = cachedContainers.filter(c => c.is_snoozed).length;

  const elTotal = document.getElementById('fleetKpiTotal');
  const elRunning = document.getElementById('fleetKpiRunning');
  const elExited = document.getElementById('fleetKpiExited');
  const elSnoozed = document.getElementById('fleetKpiSnoozed');
  const elBadge = document.getElementById('fleetRunningBadge');

  if (elTotal) elTotal.textContent = total;
  if (elRunning) elRunning.textContent = running;
  if (elExited) elExited.textContent = exited;
  if (elSnoozed) elSnoozed.textContent = snoozed;
  if (elBadge) elBadge.textContent = `${running} RUNNING`;

  const btnAll = document.getElementById('fleetFilterAll');
  const btnRun = document.getElementById('fleetFilterRunning');
  const btnExit = document.getElementById('fleetFilterExited');
  const btnSnooze = document.getElementById('fleetFilterSnoozed');
  if (btnAll) btnAll.innerHTML = `All (${total})`;
  if (btnRun) btnRun.innerHTML = `Running (${running})`;
  if (btnExit) btnExit.innerHTML = `Exited (${exited})`;
  if (btnSnooze) btnSnooze.innerHTML = `Snoozed (${snoozed})`;

  const query = (document.getElementById('fleetSearchInput')?.value || '').toLowerCase().trim();
  const filterBtn = document.querySelector('.fleet-filter-btn.active');
  const filter = filterBtn ? filterBtn.dataset.filter : 'all';

  const filtered = cachedContainers.filter(c => {
    const matchesQuery = !query || 
      (c.name || '').toLowerCase().includes(query) || 
      (c.image || '').toLowerCase().includes(query) ||
      (c.status || '').toLowerCase().includes(query) ||
      (c.id || '').toLowerCase().includes(query);
    if (!matchesQuery) return false;

    if (filter === 'running') return c.status === 'running' && c.health !== 'unhealthy';
    if (filter === 'exited') return c.status === 'exited' || c.health === 'unhealthy';
    if (filter === 'snoozed') return c.is_snoozed;
    return true;
  });

  const grid = document.getElementById('fleetCardsGrid');
  if (!grid) return;

  if (filtered.length === 0) {
    grid.innerHTML = `
      <div style="text-align: center; color: var(--text-muted); padding: 48px; background: var(--bg-card); border-radius: var(--radius-md); border: 1px solid var(--border-card); width: 100%;">
        <div style="font-size: 36px; margin-bottom: 10px;">🐳</div>
        <div style="font-size: 16px; font-weight: 600; color: var(--text-primary); margin-bottom: 6px;">No Containers Match Filter</div>
        <div style="font-size: 13px;">Try clearing your search query or switching tabs.</div>
      </div>
    `;
    return;
  }

  const isTable = (viewModes.fleet === 'table');

  if (isTable) {
    grid.innerHTML = `
      <div class="dense-table-wrapper">
        <table class="dense-data-table">
          <thead>
            <tr>
              <th style="width: 140px;">Status</th>
              <th>Container Name</th>
              <th>Image Repository</th>
              <th>State Details</th>
              <th style="text-align: right;">Quick Actions</th>
            </tr>
          </thead>
          <tbody>
            ${filtered.map(c => {
              const isRunning = c.status === 'running' && c.health !== 'unhealthy';
              const isExited = c.status === 'exited' || c.health === 'unhealthy';
              const dotClass = isRunning ? 'pulse-dot-green' : (isExited ? 'pulse-dot-red' : 'pulse-dot-amber');
              const statusPill = isRunning
                ? '<span class="badge badge-running">● RUNNING</span>'
                : (c.health === 'unhealthy' ? '<span class="badge badge-unhealthy">● UNHEALTHY</span>' : '<span class="badge badge-exited">● EXITED</span>');

              return `
                <tr>
                  <td>
                    <div style="display: flex; align-items: center; gap: 8px;">
                      <span class="${dotClass}"></span>
                      ${statusPill}
                    </div>
                  </td>
                  <td>
                    <strong style="color: var(--text-primary); font-size: 13.5px;">${escapeHtml(c.name)}</strong>
                    ${c.is_snoozed ? '<span class="badge badge-snoozed" style="font-size: 10px; margin-left: 6px;">⏰ Snoozed</span>' : ''}
                  </td>
                  <td class="dense-col-mono" style="color: var(--text-secondary); max-width: 280px; overflow: hidden; text-overflow: ellipsis; white-space: nowrap;">
                    ${escapeHtml(c.image || 'docker-image')}
                  </td>
                  <td style="color: var(--text-muted); font-size: 12px;">
                    ${escapeHtml(c.status || '')} ${c.health ? '· ' + escapeHtml(c.health) : ''}
                  </td>
                  <td class="dense-actions-cell">
                    <button class="btn btn-secondary btn-sm" onclick="openLogsModal('${escapeHtml(c.name)}')">📋 Logs</button>
                    <button class="btn btn-secondary btn-sm" onclick="openRestartModal('${escapeHtml(c.name)}')">🔄 Restart</button>
                    <button class="btn btn-primary btn-sm" onclick="openAiRcaModal('${escapeHtml(c.name)}', '${escapeHtml(c.status)}')">⚡ AI RCA</button>
                    ${c.is_snoozed 
                      ? `<button class="btn btn-secondary btn-sm" onclick="unsnoozeContainer('${escapeHtml(c.name)}')">🔔 Unsnooze</button>`
                      : `<button class="btn btn-secondary btn-sm" onclick="openSnoozeModal('${escapeHtml(c.name)}')">⏰ Snooze</button>`
                    }
                  </td>
                </tr>
              `;
            }).join('')}
          </tbody>
        </table>
      </div>
    `;
    return;
  }

  // Rows mode (matching Incident Audit pattern)
  grid.innerHTML = filtered.map(c => {
    const isRunning = c.status === 'running' && c.health !== 'unhealthy';
    const isExited = c.status === 'exited' || c.health === 'unhealthy';
    const rowClass = isRunning ? 'status-healthy' : (isExited ? 'status-danger' : 'status-warning');
    const dotClass = isRunning ? 'pulse-dot-green' : (isExited ? 'pulse-dot-red' : 'pulse-dot-amber');
    const statusPill = isRunning
      ? '<span class="badge badge-running">● RUNNING</span>'
      : (c.health === 'unhealthy' ? '<span class="badge badge-unhealthy">● UNHEALTHY</span>' : '<span class="badge badge-exited">● EXITED</span>');

    return `
      <div class="entity-row-card ${rowClass}">
        <div class="entity-row-header">
          <div class="entity-identity-block">
            <div class="entity-identity-row">
              <span class="${dotClass}"></span>
              <span class="entity-title-text">${escapeHtml(c.name)}</span>
              <span class="badge-source">🐳 Docker Container</span>
              ${c.is_snoozed ? '<span class="badge badge-snoozed">⏰ Alert Snoozed</span>' : ''}
            </div>
            <div class="entity-sub-identifier">${escapeHtml(c.image || 'docker-image')}</div>
          </div>
          <div>${statusPill}</div>
        </div>

        <div class="entity-details-box">
          <div class="entity-detail-item">
            <span class="entity-detail-label">CONTAINER STATE:</span>
            <span style="color: ${isRunning ? 'var(--color-success)' : 'var(--color-danger)'}; font-weight: 700;">
              ${escapeHtml(c.status || 'unknown')} ${c.health ? '(' + escapeHtml(c.health) + ')' : ''}
            </span>
          </div>
          <div class="entity-detail-item">
            <span class="entity-detail-label">SERVICE ID:</span>
            <span class="entity-detail-sub" style="font-family: 'JetBrains Mono', monospace;">
              ${escapeHtml((c.id || '').substring(0, 12) || 'auto')}
            </span>
          </div>
        </div>

        <div class="entity-row-footer">
          <div class="entity-meta-list">
            <span>🛡️ Monitored by Docker Daemon</span>
            <span>⚡ Automated Crash Recovery Active</span>
          </div>
          <div class="entity-actions-group">
            <button class="btn btn-secondary btn-sm" onclick="openLogsModal('${escapeHtml(c.name)}')">📋 Logs</button>
            <button class="btn btn-secondary btn-sm" onclick="openRestartModal('${escapeHtml(c.name)}')">🔄 Restart</button>
            <button class="btn btn-primary btn-sm" onclick="openAiRcaModal('${escapeHtml(c.name)}', '${escapeHtml(c.status)}')">⚡ AI RCA</button>
            ${c.is_snoozed 
              ? `<button class="btn btn-secondary btn-sm" onclick="unsnoozeContainer('${escapeHtml(c.name)}')">🔔 Unsnooze</button>`
              : `<button class="btn btn-secondary btn-sm" onclick="openSnoozeModal('${escapeHtml(c.name)}')">⏰ Snooze</button>`
            }
          </div>
        </div>
      </div>
    `;
  }).join('');
}

// ── Tab 3: Probes ───────────────────────────────────────────────────────────

async function fetchProbes() {
  try {
    const res = await apiFetch('/api/probes');
    if (!res.ok) return;
    cachedProbes = await res.json();
    document.getElementById('navProbesCount').textContent = cachedProbes.length;

    // Calculate Probe KPIs
    const total = cachedProbes.length;
    const healthy = cachedProbes.filter(p => p.is_healthy).length;
    const failing = total - healthy;
    const latencies = cachedProbes.filter(p => p.latency_ms > 0).map(p => p.latency_ms);
    const avgLatency = latencies.length ? Math.round(latencies.reduce((a, b) => a + b, 0) / latencies.length) : 0;

    const elTotal = document.getElementById('probesKpiTotal');
    const elHealthy = document.getElementById('probesKpiHealthy');
    const elFailing = document.getElementById('probesKpiFailing');
    const elLatency = document.getElementById('probesKpiLatency');

    if (elTotal) elTotal.textContent = `${total} Endpoints`;
    if (elHealthy) elHealthy.textContent = `${healthy} Healthy`;
    if (elFailing) elFailing.textContent = `${failing} Alert${failing === 1 ? '' : 's'}`;
    if (elLatency) elLatency.textContent = `${avgLatency} ms`;

    filterProbesGrid();
  } catch (err) {
    console.error('Error fetching probes:', err);
  }
}

function filterProbesGrid() {
  const total = cachedProbes.length;
  const healthy = cachedProbes.filter(p => p.enabled === 1 && Boolean(p.is_healthy)).length;
  const failing = cachedProbes.filter(p => p.enabled === 1 && !p.is_healthy).length;
  const paused = cachedProbes.filter(p => p.enabled === 0).length;

  const btnAll = document.getElementById('probeFilterAll');
  const btnHealth = document.getElementById('probeFilterHealthy');
  const btnFail = document.getElementById('probeFilterFailing');
  const btnPause = document.getElementById('probeFilterPaused');
  if (btnAll) btnAll.innerHTML = `All (${total})`;
  if (btnHealth) btnHealth.innerHTML = `Operational (${healthy})`;
  if (btnFail) btnFail.innerHTML = `Degraded (${failing})`;
  if (btnPause) btnPause.innerHTML = `Paused (${paused})`;

  const query = (document.getElementById('probesSearchInput')?.value || '').toLowerCase().trim();
  const filterBtn = document.querySelector('.probe-filter-btn.active');
  const filter = filterBtn ? filterBtn.dataset.filter : 'all';

  const filtered = cachedProbes.filter(p => {
    const matchesQuery = !query ||
      (p.name || '').toLowerCase().includes(query) ||
      (p.url || '').toLowerCase().includes(query) ||
      String(p.status_code || '').includes(query);
    if (!matchesQuery) return false;

    if (filter === 'healthy') return p.enabled === 1 && Boolean(p.is_healthy);
    if (filter === 'failing') return p.enabled === 1 && !p.is_healthy;
    if (filter === 'paused') return p.enabled === 0;
    return true;
  });

  const list = document.getElementById('probesListContainer');
  if (!list) return;

  if (filtered.length === 0) {
    list.innerHTML = `
      <div style="text-align: center; color: var(--text-muted); padding: 48px; background: var(--bg-card); border-radius: var(--radius-md); border: 1px solid var(--border-card); width: 100%;">
        <div style="font-size: 36px; margin-bottom: 10px;">🌐</div>
        <div style="font-size: 16px; font-weight: 600; color: var(--text-primary); margin-bottom: 6px;">No Service Health Checks Match Filter</div>
        <div style="font-size: 13px;">All endpoints operating within normal limits or adjust your search filter.</div>
      </div>
    `;
    return;
  }

  const isTable = (viewModes.probes === 'table');

  if (isTable) {
    list.innerHTML = `
      <div class="dense-table-wrapper">
        <table class="dense-data-table">
          <thead>
            <tr>
              <th style="width: 140px;">Status</th>
              <th>Service Name</th>
              <th>Target URL</th>
              <th>HTTP Code</th>
              <th>Latency</th>
              <th>State</th>
              <th style="text-align: right;">Quick Actions</th>
            </tr>
          </thead>
          <tbody>
            ${filtered.map(p => {
              const isHealthy = Boolean(p.is_healthy);
              const isPaused = (p.enabled === 0);
              const dotClass = isPaused ? 'status-dot' : (isHealthy ? 'pulse-dot-green' : 'pulse-dot-red');
              const latencyText = (isHealthy && p.latency_ms > 0) ? `${p.latency_ms} ms` : '—';
              const codeBadge = isHealthy
                ? `<span class="badge badge-running">HTTP ${p.status_code || 200}</span>`
                : (isPaused ? '<span class="badge badge-snoozed">PAUSED</span>' : `<span class="badge badge-exited">HTTP ${p.status_code || 'ERR'}</span>`);

              return `
                <tr>
                  <td>
                    <div style="display: flex; align-items: center; gap: 8px;">
                      <span class="${dotClass}"></span>
                      ${codeBadge}
                    </div>
                  </td>
                  <td><strong style="color: var(--text-primary); font-size: 13.5px;">${escapeHtml(p.name)}</strong></td>
                  <td class="dense-col-mono">
                    <a href="${escapeHtml(p.url)}" target="_blank" rel="noreferrer" style="color: var(--accent-cyan); text-decoration: none;">
                      ${escapeHtml(p.url)} ↗
                    </a>
                  </td>
                  <td><span class="dense-col-mono">${p.status_code || '—'}</span></td>
                  <td><span class="dense-col-mono" style="color: ${p.latency_ms < 300 ? 'var(--color-success)' : 'var(--color-warning)'};">${latencyText}</span></td>
                  <td>${p.enabled === 1 ? '<span style="color: var(--color-success); font-size: 12px; font-weight: 600;">Active</span>' : '<span style="color: var(--text-muted); font-size: 12px;">Paused</span>'}</td>
                  <td class="dense-actions-cell">
                    <button class="btn btn-secondary btn-sm" onclick="toggleProbe(${p.id}, ${p.enabled === 1 ? 'false' : 'true'})">
                      ${p.enabled === 1 ? 'Pause' : 'Resume'}
                    </button>
                    <button class="btn btn-primary btn-sm" onclick="openAiRcaModal('${escapeHtml(p.name)}', '${isHealthy ? 'healthy' : 'failing'}', 'Latency: ${p.latency_ms}ms URL: ${escapeHtml(p.url)}')">
                      ⚡ AI RCA
                    </button>
                    <button class="btn btn-danger btn-sm" onclick="deleteProbe(${p.id})" title="Delete check">🗑</button>
                  </td>
                </tr>
              `;
            }).join('')}
          </tbody>
        </table>
      </div>
    `;
    return;
  }

  // Rows mode (matching Incident Audit pattern)
  list.innerHTML = filtered.map(p => {
    const isHealthy = Boolean(p.is_healthy);
    const isPaused = (p.enabled === 0);
    const rowClass = isPaused ? 'status-muted' : (isHealthy ? 'status-healthy' : 'status-danger');
    const dotClass = isPaused ? 'status-dot' : (isHealthy ? 'pulse-dot-green' : 'pulse-dot-red');

    let latencyClass = 'latency-normal';
    let latencyDisplay = `${p.latency_ms} ms`;
    if (!isHealthy || p.latency_ms == null || p.latency_ms <= 0) {
      latencyClass = 'latency-unavailable';
      latencyDisplay = 'Unavailable (—)';
    } else if (p.latency_ms < 300) {
      latencyClass = 'latency-fast';
    } else if (p.latency_ms <= 1500) {
      latencyClass = 'latency-warning';
    } else {
      latencyClass = 'latency-slow';
    }

    const sparkBars = Array.from({ length: 12 }, (_, i) => {
      let barClass = 'uptime-spark-bar';
      if (!isHealthy && i >= 10) barClass += ' down';
      else if (p.latency_ms > 400 && i >= 9) barClass += ' degraded';
      return `<span class="${barClass}"></span>`;
    }).join('');

    return `
      <div class="entity-row-card ${rowClass}">
        <div class="entity-row-header">
          <div class="entity-identity-block">
            <div class="entity-identity-row">
              <span class="${dotClass}"></span>
              <span class="entity-title-text">${escapeHtml(p.name)}</span>
              <span class="badge-source">🌐 HTTP Probe</span>
              ${p.enabled === 0 ? '<span class="badge badge-snoozed">○ Paused</span>' : ''}
            </div>
            <div class="entity-sub-identifier">
              <a href="${escapeHtml(p.url)}" target="_blank" rel="noopener noreferrer" style="color: inherit; text-decoration: none;">
                ${escapeHtml(p.url)} ↗
              </a>
            </div>
          </div>
          <div>
            ${isHealthy 
              ? `<span class="badge badge-running">● ONLINE · HTTP ${p.status_code || 200}</span>`
              : (isPaused ? '<span class="badge badge-snoozed">○ PROBE PAUSED</span>' : `<span class="badge badge-exited">● DOWN ${p.status_code ? '· HTTP ' + p.status_code : '· UNREACHABLE'}</span>`)}
          </div>
        </div>

        <div class="entity-details-box">
          <div class="entity-detail-item">
            <span class="entity-detail-label">RESPONSE TIME:</span>
            <span class="latency-pill ${latencyClass}">${latencyDisplay}</span>
          </div>
          <div class="entity-detail-item">
            <span class="entity-detail-label">EXPECTED:</span>
            <span class="entity-detail-value">HTTP ${p.expected_status || 200} (timeout ${p.timeout_seconds || 5}s)</span>
          </div>
          <div class="entity-detail-item" style="align-items: center; gap: 6px;">
            <span class="entity-detail-label">STATUS PULSE:</span>
            <div class="uptime-spark-bars">${sparkBars}</div>
          </div>
        </div>

        <div class="entity-row-footer">
          <div class="entity-meta-list">
            <span>📡 Interval: 60s autonomous heartbeat</span>
            <span>${p.enabled === 1 ? '● Continuous verification' : '○ Paused by operator'}</span>
          </div>
          <div class="entity-actions-group">
            <button class="btn btn-secondary btn-sm" onclick="toggleProbe(${p.id}, ${p.enabled === 1 ? 'false' : 'true'})">
              ${p.enabled === 1 ? 'Pause' : 'Resume'}
            </button>
            <button class="btn btn-primary btn-sm" onclick="openAiRcaModal('${escapeHtml(p.name)}', '${isHealthy ? 'healthy' : 'failing'}', 'Latency: ${p.latency_ms}ms URL: ${escapeHtml(p.url)}')">
              ⚡ AI RCA
            </button>
            <button class="btn btn-danger btn-sm" onclick="deleteProbe(${p.id})" title="Delete check">🗑</button>
          </div>
        </div>
      </div>
    `;
  }).join('');
}

async function toggleProbe(probeId, newEnabledState) {
  try {
    const res = await apiFetch(`/api/probes/${probeId}/toggle`, {
      method: 'POST',
      body: JSON.stringify({ enabled: newEnabledState })
    });
    if (res.ok) {
      showToast('Service health check state updated.', 'success');
      fetchProbes();
    }
  } catch (err) {
    showToast('Failed to toggle service check.', 'error');
  }
}

async function deleteProbe(probeId) {
  if (!confirm('Permanently remove this service health check?')) return;
  try {
    const res = await apiFetch(`/api/probes/${probeId}`, { method: 'DELETE' });
    const data = await res.json().catch(() => ({}));
    if (res.ok) {
      showToast(data.message || 'Service health check removed.', 'success');
      fetchProbes();
      fetchOverview();
    } else {
      showToast(data.detail || 'Failed to delete service check.', 'error');
    }
  } catch (err) {
    showToast('Network error deleting service check.', 'error');
  }
}

// ── Tab 4: Renewals & Billing ───────────────────────────────────────────────

let cachedRenewals = [];

async function fetchRenewals() {
  try {
    const res = await apiFetch('/api/renewals');
    if (!res.ok) return;
    cachedRenewals = await res.json();
    window.renewalsCache = cachedRenewals;
    document.getElementById('navRenewalsCount').textContent = cachedRenewals.filter(r => r.status === 'pending').length;

    // Calculate Financial KPIs
    const active = cachedRenewals.filter(r => r.status === 'pending');
    let monthlyRunRate = 0;
    let dueSoonCount = 0;
    const now = new Date();

    active.forEach(r => {
      const amt = Number(r.amount || 0);
      const rec = (r.recurrence || 'monthly').toLowerCase();
      if (rec === 'monthly') monthlyRunRate += amt;
      else if (rec === 'yearly' || rec === 'annual') monthlyRunRate += (amt / 12);
      else if (rec === 'quarterly') monthlyRunRate += (amt / 3);

      const dueDate = new Date(r.due_date);
      const diffDays = Math.ceil((dueDate - now) / (1000 * 60 * 60 * 24));
      if (diffDays >= 0 && diffDays <= 30) dueSoonCount++;
    });

    const annualProjected = Math.round(monthlyRunRate * 12);

    const kpiActive = document.getElementById('renKpiActive');
    const kpiMonthly = document.getElementById('renKpiMonthly');
    const kpiUpcoming = document.getElementById('renKpiUpcoming');
    const kpiAnnual = document.getElementById('renKpiAnnual');

    if (kpiActive) kpiActive.textContent = `${active.length} Active`;
    if (kpiMonthly) kpiMonthly.textContent = `₹${Math.round(monthlyRunRate).toLocaleString('en-IN')} / mo`;
    if (kpiUpcoming) kpiUpcoming.textContent = `${dueSoonCount} Due Soon`;
    if (kpiAnnual) kpiAnnual.textContent = `₹${annualProjected.toLocaleString('en-IN')} / yr`;

    filterRenewalsGrid();
    renderRenewalsTimeline(cachedRenewals);
  } catch (err) {
    console.error('Error fetching renewals:', err);
  }
}

function filterRenewalsGrid() {
  const activeRenewals = (cachedRenewals || []).filter(r => r.status !== 'cancelled');
  const total = activeRenewals.length;
  const vpsCount = activeRenewals.filter(r => (r.category || '').toLowerCase().includes('vps')).length;
  const domainCount = activeRenewals.filter(r => (r.category || '').toLowerCase().includes('domain')).length;
  const sslCount = activeRenewals.filter(r => (r.category || '').toLowerCase().includes('ssl')).length;
  const saasCount = activeRenewals.filter(r => (r.category || '').toLowerCase().includes('saas') || (r.category || '').toLowerCase().includes('client')).length;

  const btnAll = document.getElementById('renewalFilterAll');
  const btnVps = document.getElementById('renewalFilterVps');
  const btnDom = document.getElementById('renewalFilterDomain');
  const btnSsl = document.getElementById('renewalFilterSsl');
  const btnSaas = document.getElementById('renewalFilterSaas');

  if (btnAll) btnAll.innerHTML = `All (${total})`;
  if (btnVps) btnVps.innerHTML = `Cloud VPS (${vpsCount})`;
  if (btnDom) btnDom.innerHTML = `Domains (${domainCount})`;
  if (btnSsl) btnSsl.innerHTML = `SSL Certs (${sslCount})`;
  if (btnSaas) btnSaas.innerHTML = `SaaS (${saasCount})`;

  const query = (document.getElementById('renewalsSearchInput')?.value || '').toLowerCase().trim();
  const filterBtn = document.querySelector('.renewal-filter-btn.active');
  const filter = filterBtn ? filterBtn.dataset.filter : 'all';

  const filtered = (cachedRenewals || []).filter(r => {
    if (r.status === 'cancelled') return false;
    const matchesQuery = !query || 
      (r.name || '').toLowerCase().includes(query) || 
      (r.category || '').toLowerCase().includes(query) || 
      (r.notes || '').toLowerCase().includes(query) ||
      (r.recurrence || '').toLowerCase().includes(query);
    if (!matchesQuery) return false;

    if (filter === 'vps') return (r.category || '').toLowerCase().includes('vps');
    if (filter === 'domain') return (r.category || '').toLowerCase().includes('domain');
    if (filter === 'ssl') return (r.category || '').toLowerCase().includes('ssl');
    if (filter === 'saas') return (r.category || '').toLowerCase().includes('saas') || (r.category || '').toLowerCase().includes('client');
    return true;
  });

  const grid = document.getElementById('renewalsGridContainer');
  if (!grid) return;

  if (filtered.length === 0) {
    grid.innerHTML = `
      <div style="text-align: center; color: var(--text-muted); padding: 48px; background: var(--bg-card); border-radius: var(--radius-md); border: 1px solid var(--border-card); width: 100%;">
        <div style="font-size: 36px; margin-bottom: 10px;">💳</div>
        <div style="font-size: 16px; font-weight: 600; color: var(--text-primary); margin-bottom: 6px;">No Renewals Match Filter</div>
        <div style="font-size: 13px;">Try adjusting search keywords or category filters.</div>
      </div>
    `;
    return;
  }

  const isTable = (viewModes.renewals === 'table');

  if (isTable) {
    grid.innerHTML = `
      <div class="dense-table-wrapper">
        <table class="dense-data-table">
          <thead>
            <tr>
              <th style="width: 150px;">Status / Due</th>
              <th>Subscription / Service</th>
              <th>Category</th>
              <th>Cost / Billing</th>
              <th>Notes & Context</th>
              <th style="text-align: right;">Quick Actions</th>
            </tr>
          </thead>
          <tbody>
            ${filtered.map(r => {
              const isPaid = r.status === 'paid';
              const dueDate = new Date(r.due_date);
              const diffDays = Math.ceil((dueDate - new Date()) / (1000 * 60 * 60 * 24));
              
              let dotClass = 'pulse-dot-green';
              let statusPill = `<span class="badge badge-running">● ${escapeHtml(r.due_date)}</span>`;

              if (isPaid) {
                statusPill = `<span class="badge badge-running">✅ PAID</span>`;
              } else if (diffDays < 0) {
                dotClass = 'pulse-dot-red';
                statusPill = `<span class="badge badge-exited">⚠️ OVERDUE (${Math.abs(diffDays)}d)</span>`;
              } else if (diffDays <= 7) {
                dotClass = 'pulse-dot-amber';
                statusPill = `<span class="badge badge-snoozed">⏳ ${diffDays}d LEFT</span>`;
              } else if (diffDays <= 30) {
                statusPill = `<span class="badge badge-snoozed" style="color: var(--accent-cyan);">📅 In ${diffDays}d</span>`;
              }

              let categoryIcon = '🖥️';
              const cat = (r.category || '').toLowerCase();
              if (cat.includes('domain')) categoryIcon = '🌐';
              else if (cat.includes('ssl')) categoryIcon = '🔒';
              else if (cat.includes('saas') || cat.includes('client')) categoryIcon = '⚡';

              const currencySymbol = r.currency === 'USD' ? '$' : (r.currency === 'EUR' ? '€' : '₹');

              return `
                <tr style="${isPaid ? 'opacity: 0.65;' : ''}">
                  <td>
                    <div style="display: flex; align-items: center; gap: 8px;">
                      <span class="${dotClass}"></span>
                      ${statusPill}
                    </div>
                  </td>
                  <td>
                    <strong style="color: var(--text-primary); font-size: 13.5px;">${categoryIcon} ${escapeHtml(r.name)}</strong>
                    <div style="font-size: 11px; color: var(--text-muted); margin-top: 2px;">Due: ${escapeHtml(r.due_date)}</div>
                  </td>
                  <td>
                    <span class="badge-source">${escapeHtml(r.category || 'other').toUpperCase()}</span>
                  </td>
                  <td class="dense-col-mono">
                    <span style="font-weight: 700; color: var(--text-primary);">
                      ${currencySymbol}${Number(r.amount || 0).toLocaleString()}
                    </span>
                    <span style="font-size: 11px; color: var(--text-muted); display: block;">
                      / ${escapeHtml(r.recurrence || 'monthly')}
                    </span>
                  </td>
                  <td style="color: var(--text-secondary); font-size: 12px; max-width: 250px; overflow: hidden; text-overflow: ellipsis; white-space: nowrap;">
                    ${escapeHtml(r.notes || '—')}
                  </td>
                  <td class="dense-actions-cell">
                    ${!isPaid ? `
                      <button class="btn btn-success btn-sm" onclick="markRenewalPaid(${r.id})" title="Mark Paid">✅ Paid</button>
                      <button class="btn btn-secondary btn-sm" onclick="snoozeRenewal(${r.id})" title="Snooze 7 Days">⏰ Snooze</button>
                      <button class="btn btn-secondary btn-sm" onclick="openEditRenewalModal(${r.id})" title="Edit Details">✏️ Edit</button>
                    ` : ''}
                    <button class="btn btn-danger btn-sm" onclick="deleteRenewal(${r.id})" title="Delete Renewal">🗑</button>
                  </td>
                </tr>
              `;
            }).join('')}
          </tbody>
        </table>
      </div>
    `;
    return;
  }

  // Rows mode (matching Incident Audit pattern)
  grid.innerHTML = filtered.map(r => {
    const isPaid = r.status === 'paid';
    const dueDate = new Date(r.due_date);
    const diffDays = Math.ceil((dueDate - new Date()) / (1000 * 60 * 60 * 24));

    let rowClass = 'status-healthy';
    let dotClass = 'pulse-dot-green';
    let statusPill = `<span class="badge badge-running">📅 DUE IN ${diffDays} DAYS</span>`;

    if (isPaid) {
      statusPill = `<span class="badge badge-running">✅ PAID & ACTIVE</span>`;
    } else if (diffDays < 0) {
      rowClass = 'status-danger';
      dotClass = 'pulse-dot-red';
      statusPill = `<span class="badge badge-exited">⚠️ OVERDUE (${Math.abs(diffDays)}d)</span>`;
    } else if (diffDays <= 7) {
      rowClass = 'status-warning';
      dotClass = 'pulse-dot-amber';
      statusPill = `<span class="badge badge-snoozed">⏳ DUE IN ${diffDays} DAYS</span>`;
    } else if (diffDays <= 30) {
      rowClass = 'status-warning';
      statusPill = `<span class="badge badge-snoozed" style="color: var(--accent-cyan);">📅 IN ${diffDays} DAYS</span>`;
    }

    let categoryIcon = '🖥️';
    const cat = (r.category || '').toLowerCase();
    if (cat.includes('domain')) categoryIcon = '🌐';
    else if (cat.includes('ssl')) categoryIcon = '🔒';
    else if (cat.includes('saas') || cat.includes('client')) categoryIcon = '⚡';

    const currencySymbol = r.currency === 'USD' ? '$' : (r.currency === 'EUR' ? '€' : '₹');

    return `
      <div class="entity-row-card ${rowClass}" style="${isPaid ? 'opacity: 0.65;' : ''}">
        <div class="entity-row-header">
          <div class="entity-identity-block">
            <div class="entity-identity-row">
              <span class="${dotClass}"></span>
              <span class="entity-title-text">${categoryIcon} ${escapeHtml(r.name)}</span>
              <span class="badge-source">${escapeHtml(r.category || 'other').toUpperCase()}</span>
              <span class="badge-source">Recurrence: ${escapeHtml(r.recurrence || 'monthly')}</span>
            </div>
            <div class="entity-sub-identifier">${escapeHtml(r.notes || 'Autonomous cloud infrastructure renewal tracking active.')}</div>
          </div>
          <div>${statusPill}</div>
        </div>

        <div class="entity-details-box">
          <div class="entity-detail-item">
            <span class="entity-detail-label">RECURRING AMOUNT:</span>
            <span style="color: var(--color-success); font-weight: 700; font-size: 13.5px;">
              ${currencySymbol}${Number(r.amount || 0).toLocaleString()} 
              <span class="entity-detail-sub">/ ${escapeHtml(r.recurrence || 'monthly')}</span>
            </span>
          </div>
          <div class="entity-detail-item">
            <span class="entity-detail-label">SCHEDULED DUE DATE:</span>
            <span class="entity-detail-value">📅 ${escapeHtml(r.due_date)}</span>
          </div>
          <div class="entity-detail-item">
            <span class="entity-detail-label">PAYMENT STATUS:</span>
            <span style="color: ${isPaid ? 'var(--color-success)' : (diffDays < 0 ? 'var(--color-danger)' : 'var(--color-warning)')}; font-weight: 700;">
              ${isPaid ? 'Settled' : (diffDays < 0 ? 'Overdue Action Required' : 'Pending')}
            </span>
          </div>
        </div>

        <div class="entity-row-footer">
          <div class="entity-meta-list">
            <span>💳 Renewal ID: #${r.id}</span>
            <span>🌐 Currency: ${escapeHtml(r.currency || 'INR')}</span>
            <span>🛡️ SRE Auto-Notification: 14d & 3d prior</span>
          </div>
          <div class="entity-actions-group">
            ${!isPaid ? `
              <button class="btn btn-success btn-sm" onclick="markRenewalPaid(${r.id})">✅ Paid</button>
              <button class="btn btn-secondary btn-sm" onclick="snoozeRenewal(${r.id})">⏰ Snooze</button>
              <button class="btn btn-secondary btn-sm" onclick="openEditRenewalModal(${r.id})">✏️ Edit</button>
            ` : ''}
            <button class="btn btn-danger btn-sm" onclick="deleteRenewal(${r.id})" title="Cancel and Delete Renewal">🗑 Delete</button>
          </div>
        </div>
      </div>
    `;
  }).join('');
}

function renderRenewalsTimeline(renewals) {
  const container = document.getElementById('renewalsTimelineContainer');
  if (!container) return;

  const activeRenewals = (renewals || []).filter(r => r.status !== 'cancelled');
  if (activeRenewals.length === 0) {
    container.innerHTML = '<div style="color: var(--text-muted); font-size: 12.5px;">No upcoming payments scheduled.</div>';
    return;
  }

  container.innerHTML = activeRenewals.slice(0, 5).map(r => {
    const cur = r.currency === 'USD' ? '$' : (r.currency === 'EUR' ? '€' : '₹');
    return `
      <div class="timeline-item">
        <div>
          <span class="timeline-item-title">${escapeHtml(r.name)}</span>
          <span style="display: block; font-size: 11px; color: var(--text-muted); margin-top: 2px;">Due: ${escapeHtml(r.due_date)}</span>
        </div>
        <span class="timeline-item-price">${cur}${Number(r.amount || 0).toLocaleString()}</span>
      </div>
    `;
  }).join('');
}

async function markRenewalPaid(renewalId) {
  try {
    const res = await apiFetch(`/api/renewals/${renewalId}/pay`, { method: 'POST' });
    const data = await res.json();
    if (res.ok && data.success) {
      showToast(data.message, 'success');
      fetchRenewals();
      fetchOverview();
    }
  } catch (err) {
    showToast('Failed to mark renewal as paid.', 'error');
  }
}

async function snoozeRenewal(renewalId) {
  try {
    const res = await apiFetch(`/api/renewals/${renewalId}/snooze`, {
      method: 'POST',
      body: JSON.stringify({ days: 7 })
    });
    if (res.ok) {
      showToast('Renewal snoozed for 7 days.', 'success');
      fetchRenewals();
      fetchOverview();
    }
  } catch (err) {
    showToast('Failed to snooze renewal.', 'error');
  }
}

async function deleteRenewal(renewalId) {
  if (!confirm('Permanently cancel / delete this renewal?')) return;
  try {
    const res = await apiFetch(`/api/renewals/${renewalId}`, { method: 'DELETE' });
    const data = await res.json().catch(() => ({}));
    if (res.ok) {
      showToast(data.message || 'Renewal deleted.', 'success');
      fetchRenewals();
      fetchOverview();
    } else {
      showToast(data.detail || 'Failed to delete renewal.', 'error');
    }
  } catch (err) {
    showToast('Network error deleting renewal.', 'error');
  }
}

// ── Tab 5: Incidents ────────────────────────────────────────────────────────

window.incidentsCache = [];
let currentIncidentFilter = 'all';

function formatIncidentDuration(startStr, endStr) {
  if (!startStr || !endStr) return '';
  try {
    const s = new Date(startStr.replace(' ', 'T')).getTime();
    const e = new Date(endStr.replace(' ', 'T')).getTime();
    if (isNaN(s) || isNaN(e)) return '';
    const diffSec = Math.max(0, Math.floor((e - s) / 1000));
    if (diffSec < 60) return `${diffSec}s`;
    const mins = Math.floor(diffSec / 60);
    const secs = diffSec % 60;
    if (mins < 60) return `${mins}m ${secs}s`;
    const hours = Math.floor(mins / 60);
    const remMins = mins % 60;
    return `${hours}h ${remMins}m`;
  } catch (_) {
    return '';
  }
}

async function fetchIncidents() {
  try {
    const res = await apiFetch('/api/incidents');
    if (!res.ok) return;
    const incidents = await res.json();
    window.incidentsCache = incidents;

    const total = incidents.length;
    const active = incidents.filter(i => !i.resolved_at).length;
    const resolved = incidents.filter(i => Boolean(i.resolved_at)).length;
    const criticalCount = incidents.filter(i => i.severity === 'critical').length;

    // Update KPI Counters
    const activeEl = document.getElementById('activeIncidentsCountVal');
    const activeSubEl = document.getElementById('activeIncidentsSubVal');
    if (activeEl) {
      activeEl.innerHTML = active > 0
        ? `<span class="pulse-dot-red"></span> ${active} Open`
        : `<span class="status-dot green"></span> 0 Open`;
    }
    if (activeSubEl) {
      if (active === 0) {
        activeSubEl.textContent = 'All monitored endpoints healthy';
        activeSubEl.style.color = 'var(--text-muted)';
      } else if (active === 1) {
        activeSubEl.textContent = '1 active anomaly requiring attention';
        activeSubEl.style.color = '#f87171';
      } else {
        activeSubEl.textContent = `${active} active anomalies requiring attention`;
        activeSubEl.style.color = '#f87171';
      }
    }

    const totalEl = document.getElementById('totalIncidentsCountVal');
    if (totalEl) totalEl.textContent = total;
    const rateEl = document.getElementById('autoResolvedRateVal');
    if (rateEl) {
      rateEl.textContent = total > 0 ? `${Math.round((resolved / total) * 100)}%` : '100%';
    }

    // Dynamic MTTR Calculation
    let totalSecs = 0;
    let resolvedWithDuration = 0;
    incidents.forEach(inc => {
      if (inc.created_at && inc.resolved_at) {
        try {
          const s = new Date(inc.created_at.replace(' ', 'T')).getTime();
          const e = new Date(inc.resolved_at.replace(' ', 'T')).getTime();
          if (!isNaN(s) && !isNaN(e)) {
            const diff = Math.floor((e - s) / 1000);
            if (diff > 0 && diff < 86400 * 30) {
              totalSecs += diff;
              resolvedWithDuration++;
            }
          }
        } catch (_) {}
      }
    });

    const mttrEl = document.getElementById('mttrVal');
    if (mttrEl) {
      if (resolvedWithDuration > 0) {
        const avgSecs = Math.round(totalSecs / resolvedWithDuration);
        if (avgSecs < 60) mttrEl.textContent = `~ ${avgSecs}s`;
        else if (avgSecs < 3600) mttrEl.textContent = `~ ${Math.round(avgSecs / 60)}m`;
        else mttrEl.textContent = `~ ${(avgSecs / 3600).toFixed(1)}h`;
      } else {
        mttrEl.textContent = '~ 58s';
      }
    }

    // Update filter button labels with counts
    const btnAll = document.getElementById('filterBtn-all');
    if (btnAll) btnAll.textContent = `All (${total})`;
    const btnActive = document.getElementById('filterBtn-active');
    if (btnActive) btnActive.textContent = `Active (${active})`;
    const btnResolved = document.getElementById('filterBtn-resolved');
    if (btnResolved) btnResolved.textContent = `Resolved (${resolved})`;
    const btnCrit = document.getElementById('filterBtn-critical');
    if (btnCrit) btnCrit.textContent = `Critical (${criticalCount})`;

    renderIncidentsFeed();
  } catch (err) {
    console.error('Error fetching incidents:', err);
  }
}

function renderIncidentsFeed() {
  const container = document.getElementById('incidentsFeedContainer');
  if (!container) return;

  const incidents = window.incidentsCache || [];
  const searchInput = document.getElementById('incidentsSearchInput');
  const search = (searchInput ? searchInput.value : '').toLowerCase().trim();

  const filtered = incidents.filter(inc => {
    // Filter by tab pill
    if (currentIncidentFilter === 'active' && inc.resolved_at) return false;
    if (currentIncidentFilter === 'resolved' && !inc.resolved_at) return false;
    if (currentIncidentFilter === 'critical' && inc.severity !== 'critical') return false;

    // Search query
    if (search) {
      const matchTarget = (inc.target || '').toLowerCase().includes(search);
      const matchTitle = (inc.title || '').toLowerCase().includes(search);
      const matchDetail = (inc.detail || '').toLowerCase().includes(search);
      return matchTarget || matchTitle || matchDetail;
    }
    return true;
  });

  if (filtered.length === 0) {
    container.innerHTML = `
      <div style="text-align: center; color: var(--text-muted); padding: 48px; background: var(--bg-card); border-radius: var(--radius-md); border: 1px solid var(--border-card);">
        <div style="font-size: 36px; margin-bottom: 10px;">🛡️</div>
        <div style="font-size: 16px; font-weight: 600; color: var(--text-primary); margin-bottom: 6px;">No Incidents Match Filter</div>
        <div style="font-size: 13px;">All infrastructure endpoints and services are operating normally.</div>
      </div>
    `;
    return;
  }

  const isTable = (viewModes.incidents === 'table');

  if (isTable) {
    container.innerHTML = `
      <div class="dense-table-wrapper">
        <table class="dense-data-table">
          <thead>
            <tr>
              <th style="width: 140px;">Status</th>
              <th>Incident Anomaly</th>
              <th>Target / Source</th>
              <th>Diagnostics & Detail</th>
              <th>Timeline & Alerts</th>
              <th style="text-align: right;">Quick Actions</th>
            </tr>
          </thead>
          <tbody>
            ${filtered.map(inc => {
              const isResolved = Boolean(inc.resolved_at);
              let sourceIcon = '🌐 HTTP';
              if (inc.source === 'docker') sourceIcon = '🐳 Docker';
              else if (inc.source === 'ssl') sourceIcon = '🔒 SSL';
              else if (inc.source === 'domain_renewal') sourceIcon = '🏷️ Domain';

              const dotClass = isResolved ? 'status-dot green' : (inc.severity === 'critical' ? 'pulse-dot-red' : 'pulse-dot-amber');
              const statusPill = isResolved
                ? '<span class="badge badge-running">✓ RESOLVED</span>'
                : (inc.severity === 'critical' ? '<span class="badge badge-exited">🔴 CRITICAL</span>' : '<span class="badge badge-snoozed">🟡 WARNING</span>');

              const durationText = formatIncidentDuration(inc.created_at, inc.resolved_at);

              return `
                <tr style="${isResolved ? 'opacity: 0.75;' : ''}">
                  <td>
                    <div style="display: flex; align-items: center; gap: 8px;">
                      <span class="${dotClass}"></span>
                      ${statusPill}
                    </div>
                  </td>
                  <td>
                    <strong style="color: var(--text-primary); font-size: 13.5px;">${escapeHtml(inc.title)}</strong>
                    <div style="font-size: 11px; color: var(--text-muted); margin-top: 2px;">Record #${inc.id}</div>
                  </td>
                  <td>
                    <span class="badge-source">${sourceIcon}</span>
                    <div class="dense-col-mono" style="color: var(--text-secondary); margin-top: 4px; max-width: 200px; overflow: hidden; text-overflow: ellipsis; white-space: nowrap;">
                      ${escapeHtml(inc.target)}
                    </div>
                  </td>
                  <td style="color: var(--text-secondary); font-size: 12px; max-width: 320px; overflow: hidden; text-overflow: ellipsis; white-space: nowrap;" title="${escapeHtml(inc.detail || '')}">
                    ${escapeHtml(inc.detail || 'Incident registered by autonomous health probe.')}
                  </td>
                  <td style="font-size: 11.5px; color: var(--text-muted); white-space: nowrap;">
                    <div>🔔 ${inc.alert_count || 1} alert${(inc.alert_count || 1) === 1 ? '' : 's'}</div>
                    <div>🕒 ${escapeHtml(inc.created_at || 'Just now')}</div>
                    ${durationText ? `<div style="color: var(--color-success); font-weight: 600;">⏱️ ${durationText}</div>` : ''}
                  </td>
                  <td class="dense-actions-cell">
                    ${!isResolved ? `
                      <button class="btn btn-primary btn-sm" onclick="openAiRcaModal('${escapeHtml(inc.target)}', '${escapeHtml(inc.severity)}', '${escapeHtml(inc.detail || '')}')" title="AI Root Cause Analysis">✨ RCA</button>
                      <button class="btn btn-success btn-sm" onclick="resolveIncident(${inc.id})" title="Mark Incident Resolved">✓ Resolve</button>
                    ` : ''}
                    <button class="btn btn-danger btn-sm" onclick="deleteIncident(${inc.id})" title="Permanently delete incident">🗑</button>
                  </td>
                </tr>
              `;
            }).join('')}
          </tbody>
        </table>
      </div>
    `;
    return;
  }

  // Rows mode (matching Incident Audit pattern)

  container.innerHTML = filtered.map(inc => {
    const isResolved = Boolean(inc.resolved_at);
    let sourceIcon = '🌐 HTTP Probe';
    if (inc.source === 'docker') sourceIcon = '🐳 Docker Container';
    else if (inc.source === 'ssl') sourceIcon = '🔒 SSL Certificate';
    else if (inc.source === 'domain_renewal') sourceIcon = '🏷️ Domain Registration';

    const cardClass = isResolved 
      ? 'is-resolved' 
      : (inc.severity === 'critical' ? 'active-critical' : 'active-warning');

    const durationText = formatIncidentDuration(inc.created_at, inc.resolved_at);

    return `
      <div class="incident-card ${cardClass}">
        <div class="incident-card-header">
          <div class="incident-title-block">
            <div class="incident-title-row">
              <span class="${isResolved ? 'status-dot green' : (inc.severity === 'critical' ? 'pulse-dot-red' : 'pulse-dot-amber')}"></span>
              <span class="incident-title-text">${escapeHtml(inc.title)}</span>
            </div>
            <div class="incident-badges-strip">
              <span class="badge-source">${sourceIcon}</span>
              <span class="badge-severity ${inc.severity === 'critical' ? 'critical' : 'warning'}">
                ● ${escapeHtml((inc.severity || 'info').toUpperCase())}
              </span>
              <span class="incident-target-tag">${escapeHtml(inc.target)}</span>
            </div>
          </div>
          <div>
            ${isResolved 
              ? '<span class="incident-status-pill resolved">✓ RESOLVED & AUDITED</span>'
              : '<span class="incident-status-pill active"><span class="pulse-dot-red"></span> ACTIVE ANOMALY</span>'}
          </div>
        </div>

        <div class="incident-diagnostics-box">
          <span class="diag-tag">DIAGNOSTICS</span>
          <span class="diag-msg">${escapeHtml(inc.detail || 'Incident registered by autonomous health probe.')}</span>
        </div>

        <div class="incident-card-footer">
          <div class="incident-meta-list">
            <span class="incident-meta-item">🔔 <strong class="incident-meta-highlight">${inc.alert_count || 1}</strong> alerts dispatched</span>
            <span class="incident-meta-item">🕒 Detected: <span class="incident-meta-highlight">${escapeHtml(inc.created_at || 'Just now')}</span></span>
            ${isResolved ? `<span class="incident-meta-item">⚡ Auto-Resolved: <span class="incident-meta-highlight">${escapeHtml(inc.resolved_at)}</span></span>` : ''}
            ${durationText ? `<span class="incident-meta-item">⏱️ Duration: <span class="incident-meta-highlight">${durationText}</span></span>` : ''}
          </div>
          <div class="incident-actions-group">
            ${!isResolved 
              ? `
                <button class="btn-ai-rca" onclick="openAiRcaModal('${escapeHtml(inc.target)}', '${escapeHtml(inc.severity)}', '${escapeHtml(inc.detail || '')}')" title="Run OpsPilot AI Root Cause Analysis">
                  ✨ AI RCA Copilot
                </button>
                <button class="btn-resolve-incident" onclick="resolveIncident(${inc.id})" title="Mark Incident Manually Resolved">
                  ✓ Mark Resolved
                </button>
              ` 
              : '<span class="resolved-audit-badge">🛡️ Verified by OpsPilot Engine</span>'}
            <button class="btn btn-secondary btn-sm" onclick="deleteIncident(${inc.id})" style="color: var(--color-danger); padding: 6px 10px;" title="Permanently delete incident record">
              🗑
            </button>
          </div>
        </div>
      </div>
    `;
  }).join('');
}

async function resolveIncident(incId) {
  try {
    const res = await apiFetch(`/api/incidents/${incId}/resolve`, { method: 'POST' });
    const data = await res.json().catch(() => ({}));
    if (res.ok) {
      showToast(data.message || 'Incident marked resolved.', 'success');
      fetchIncidents();
      fetchOverview();
    } else {
      showToast(data.detail || 'Failed to resolve incident.', 'error');
    }
  } catch (err) {
    showToast('Failed to resolve incident.', 'error');
  }
}

async function deleteIncident(incId) {
  if (!confirm('Permanently remove this incident from the audit ledger?')) return;
  try {
    const res = await apiFetch(`/api/incidents/${incId}`, { method: 'DELETE' });
    const data = await res.json().catch(() => ({}));
    if (res.ok) {
      showToast(data.message || 'Incident deleted.', 'success');
      fetchIncidents();
      fetchOverview();
    } else {
      showToast(data.detail || 'Failed to delete incident.', 'error');
    }
  } catch (err) {
    showToast('Network error deleting incident.', 'error');
  }
}

// ── Modals & Actions ────────────────────────────────────────────────────────

function openModal(modalId) {
  document.getElementById(modalId).classList.add('active');
}

function closeModal(modalId) {
  document.getElementById(modalId).classList.remove('active');
}

// Logs Modal
async function openLogsModal(containerName) {
  activeLogsContainer = containerName;
  document.getElementById('logsModalTitle').textContent = `Logs: ${containerName}`;
  document.getElementById('logsModalSubtitle').textContent = 'Fetching tail stream...';
  document.getElementById('logsTerminalContent').textContent = 'Loading...';
  openModal('logsModal');
  await fetchLogs(containerName, 100);
}

async function fetchLogs(containerName, tail = 100) {
  try {
    const res = await apiFetch(`/api/containers/${containerName}/logs?tail=${tail}`);
    const data = await res.json();
    const terminal = document.getElementById('logsTerminalContent');

    // STRICT XSS DEFENSE: render exclusively via textContent
    terminal.textContent = data.output || 'No output recorded.';
    terminal.scrollTop = terminal.scrollHeight;
    document.getElementById('logsModalSubtitle').textContent = `Showing last ${tail} lines · ${new Date().toLocaleTimeString()}`;
  } catch (err) {
    document.getElementById('logsTerminalContent').textContent = 'Error fetching container logs.';
  }
}

// Restart Modal
function openRestartModal(containerName) {
  activeRestartContainer = containerName;
  document.getElementById('restartContainerName').textContent = containerName;
  openModal('restartModal');
}

// Snooze Modal
function openSnoozeModal(containerName) {
  activeSnoozeContainer = containerName;
  document.getElementById('snoozeContainerName').textContent = containerName;
  openModal('snoozeModal');
}

async function submitSnooze(duration) {
  if (!activeSnoozeContainer) return;
  try {
    const res = await apiFetch(`/api/containers/${activeSnoozeContainer}/snooze`, {
      method: 'POST',
      body: JSON.stringify({ duration })
    });
    const data = await res.json();
    if (res.ok && data.success) {
      showToast(data.message, 'success');
      closeModal('snoozeModal');
      fetchFleet();
    }
  } catch (err) {
    showToast('Failed to snooze container.', 'error');
  }
}

async function unsnoozeContainer(containerName) {
  try {
    const res = await apiFetch(`/api/containers/${containerName}/unsnooze`, { method: 'POST' });
    const data = await res.json();
    if (res.ok && data.success) {
      showToast(data.message, 'success');
      fetchFleet();
    }
  } catch (err) {
    showToast('Failed to unsnooze container.', 'error');
  }
}

// ── Toasts & Utility ─────────────────────────────────────────────────────────

function showToast(message, type = 'success') {
  const container = document.getElementById('toastContainer');
  const toast = document.createElement('div');
  toast.className = `toast ${type === 'success' ? 'toast-success' : 'toast-error'}`;
  toast.innerHTML = `<span>${type === 'success' ? '✅' : '❌'}</span> <span>${escapeHtml(message)}</span>`;
  container.appendChild(toast);

  setTimeout(() => {
    toast.style.opacity = '0';
    toast.style.transform = 'translateY(10px)';
    toast.style.transition = 'all 0.3s ease';
    setTimeout(() => toast.remove(), 300);
  }, 3500);
}

function escapeHtml(str) {
  if (!str) return '';
  return String(str)
    .replace(/&/g, '&amp;')
    .replace(/</g, '&lt;')
    .replace(/>/g, '&gt;')
    .replace(/"/g, '&quot;')
    .replace(/'/g, '&#039;');
}


function openEditRenewalModal(renewalId) {
  const renewal = (window.renewalsCache || []).find(r => r.id === renewalId);
  if (!renewal) return;
  document.getElementById('editRenewalId').value = renewal.id;
  document.getElementById('editRenewalNameInput').value = renewal.name || '';
  document.getElementById('editRenewalCategoryInput').value = renewal.category || 'other';
  document.getElementById('editRenewalDueDateInput').value = renewal.due_date || '';
  document.getElementById('editRenewalAmountInput').value = renewal.amount != null ? renewal.amount : '';
  document.getElementById('editRenewalRecurrenceInput').value = renewal.recurrence || 'none';
  document.getElementById('editRenewalNotesInput').value = renewal.notes || '';
  openModal('editRenewalModal');
}

// ── AI Engine Settings & Dynamic Model Discovery ────────────────────────────

let aiSettingsUserInteracted = false;

function populateAiModelSelect(provider, selectedModel, modelsList = null) {
  const selectEl = document.getElementById('aiModelSelect');
  const customWrap = document.getElementById('aiCustomModelWrap');
  const customInp = document.getElementById('aiModelCustomInput');
  const legacyInp = document.getElementById('aiModelInput');
  const countBadge = document.getElementById('aiModelCountBadge');
  const presetsWrap = document.getElementById('aiModelPresets');
  if (!selectEl) return;

  selectEl.innerHTML = '';

  // If models were loaded live from the Provider API
  if (modelsList && modelsList.length > 0) {
    if (countBadge) {
      countBadge.textContent = `⚡ ${modelsList.length} Models from ${provider.toUpperCase()}`;
    }

    let hasMatch = false;
    modelsList.forEach(m => {
      const opt = document.createElement('option');
      opt.value = m.id;
      opt.textContent = m.name || m.id;
      if (selectedModel && selectedModel === m.id) {
        opt.selected = true;
        hasMatch = true;
      }
      selectEl.appendChild(opt);
    });

    // Custom option
    const customOpt = document.createElement('option');
    customOpt.value = 'custom';
    customOpt.textContent = '✏️ Custom Model Identifier...';
    selectEl.appendChild(customOpt);

    if (!hasMatch && selectedModel) {
      customOpt.selected = true;
      if (customWrap) customWrap.style.display = 'block';
      if (customInp) customInp.value = selectedModel;
      if (legacyInp) legacyInp.value = selectedModel;
    } else {
      if (!hasMatch && modelsList.length > 0) {
        selectEl.selectedIndex = 0;
        if (legacyInp) legacyInp.value = modelsList[0].id;
      } else if (hasMatch && legacyInp) {
        legacyInp.value = selectedModel;
      }
      if (customWrap) customWrap.style.display = 'none';
    }

    // Dynamic model quick-select chips created strictly from the API results
    if (presetsWrap) {
      presetsWrap.innerHTML = '';
      modelsList.slice(0, 4).forEach(m => {
        const chip = document.createElement('button');
        chip.type = 'button';
        const activeId = selectEl.value === 'custom' ? (customInp ? customInp.value : '') : selectEl.value;
        chip.className = `ai-preset-chip${activeId === m.id ? ' active' : ''}`;
        chip.textContent = m.id;
        chip.title = m.name || m.id;
        chip.onclick = () => {
          selectEl.value = m.id;
          if (customWrap) customWrap.style.display = 'none';
          if (legacyInp) legacyInp.value = m.id;
          presetsWrap.querySelectorAll('.ai-preset-chip').forEach(c => c.classList.remove('active'));
          chip.classList.add('active');
          aiSettingsUserInteracted = true;
        };
        presetsWrap.appendChild(chip);
      });
    }
  } else {
    // Models not yet queried from API
    if (presetsWrap) presetsWrap.innerHTML = '';

    if (selectedModel) {
      if (countBadge) countBadge.textContent = `Configured: ${selectedModel}`;
      const activeOpt = document.createElement('option');
      activeOpt.value = selectedModel;
      activeOpt.textContent = `${selectedModel} (Configured Model)`;
      activeOpt.selected = true;
      selectEl.appendChild(activeOpt);
      if (legacyInp) legacyInp.value = selectedModel;
    } else {
      if (countBadge) countBadge.textContent = 'Not Loaded';
      const promptOpt = document.createElement('option');
      promptOpt.value = '';
      promptOpt.textContent = `Click "⚡ Test Key & Fetch Models" to retrieve ${provider.toUpperCase()} models`;
      selectEl.appendChild(promptOpt);
    }

    const customOpt = document.createElement('option');
    customOpt.value = 'custom';
    customOpt.textContent = '✏️ Custom Model Identifier...';
    selectEl.appendChild(customOpt);
  }
}

async function fetchSettings() {
  try {
    const res = await apiFetch('/api/settings');
    if (!res.ok) return;
    const data = await res.json();
    const input = document.getElementById('settingAlertChatId');
    if (input && !input.matches(':focus')) {
      input.value = data.alert_chat_id || '';
    }
    const srv = document.getElementById('settingServerName');
    if (srv) {
      srv.value = `${data.server_name || 'node-01'} (${data.environment || 'production'})`;
    }

    // Populate AI Engine Settings
    if (data.ai) {
      const providerSel = document.getElementById('aiProviderSelect');
      const baseUrlInp = document.getElementById('aiBaseUrlInput');
      const enabledChk = document.getElementById('aiEnabledCheckbox');
      const keyInp = document.getElementById('aiApiKeyInput');
      const statusBadge = document.getElementById('aiStatusBadge');
      const keyHint = document.getElementById('aiKeyStatusHint');

      const provider = data.ai.provider || 'gemini';
      const model = data.ai.model || 'gemini-1.5-flash';

      // Only initialize inputs if user is not actively editing them
      if (!aiSettingsUserInteracted) {
        if (providerSel && !providerSel.matches(':focus')) {
          providerSel.value = provider;
        }
        if (baseUrlInp && !baseUrlInp.matches(':focus')) {
          baseUrlInp.value = data.ai.base_url || '';
        }
        if (enabledChk) {
          enabledChk.checked = Boolean(data.ai.enabled);
        }
        populateAiModelSelect(provider, model);
      }

      if (statusBadge) {
        if (data.ai.enabled && (data.ai.has_key || provider === 'ollama')) {
          statusBadge.className = 'badge badge-running';
          statusBadge.textContent = 'Active & Ready';
        } else if (!data.ai.enabled) {
          statusBadge.className = 'badge badge-snoozed';
          statusBadge.textContent = 'Disabled';
        } else {
          statusBadge.className = 'badge badge-exited';
          statusBadge.textContent = 'Key Required';
        }
      }

      if (keyHint) {
        if (data.ai.has_key) {
          keyHint.innerHTML = `<span style="color: var(--color-success); font-weight: 600;">✅ Saved (${escapeHtml(data.ai.masked_key)})</span>`;
          if (keyInp && !keyInp.matches(':focus') && !keyInp.value) {
            keyInp.placeholder = `${data.ai.masked_key} (Saved - leave empty to keep)`;
          }
        } else if (provider === 'ollama') {
          keyHint.innerHTML = `<span style="color: var(--text-muted);">ℹ️ Ollama does not require an API key</span>`;
          if (keyInp && !keyInp.matches(':focus') && !keyInp.value) {
            keyInp.placeholder = 'Optional / Not required for Ollama';
          }
        } else {
          keyHint.innerHTML = `<span style="color: var(--color-danger); font-weight: 600;">⚠️ No API Key Configured</span>`;
          if (keyInp && !keyInp.matches(':focus') && !keyInp.value) {
            keyInp.placeholder = `Enter API key for ${provider}...`;
          }
        }
      }
    }

    fetchAlertRoutes();
  } catch (err) {
    console.error('Error fetching settings:', err);
  }
}

// ── Theme Management ─────────────────────────────────────────────────────────

function initTheme() {
  let saved = localStorage.getItem('opspilot_theme_user_choice');
  if (!saved) {
    saved = 'light';
  }
  applyTheme(saved);
}

function applyTheme(theme) {
  document.documentElement.setAttribute('data-theme', theme);
  localStorage.setItem('opspilot_theme_user_choice', theme);
  localStorage.setItem('opspilot_theme', theme);
  const icon = document.getElementById('themeIcon');
  if (icon) {
    icon.textContent = theme === 'light' ? '🌙' : '☀️';
  }
}

function toggleTheme() {
  const current = document.documentElement.getAttribute('data-theme') || 'light';
  const next = current === 'dark' ? 'light' : 'dark';
  applyTheme(next);
  showToast(`Switched to ${next === 'light' ? 'Light' : 'Dark'} theme`, 'info');
}

// ── Tab: Domains & SSL Certificates ──────────────────────────────────────────

let cachedDomains = [];

async function fetchDomains() {
  try {
    const res = await apiFetch('/api/domains');
    if (!res.ok) return;
    cachedDomains = await res.json();

    // Update KPI metrics
    const totalEl = document.getElementById('totalDomainsVal');
    const validEl = document.getElementById('validCertsVal');
    const expEl = document.getElementById('expiringSoonCertsVal');
    const domRenEl = document.getElementById('domainRenewalDueVal');
    const navBadge = document.getElementById('navDomainsCount');

    if (navBadge) navBadge.textContent = cachedDomains.length;
    if (totalEl) totalEl.textContent = cachedDomains.length;

    const validCount = cachedDomains.filter(d => d.is_valid && d.days_remaining > 0).length;
    const sslExpCount = cachedDomains.filter(d => d.days_remaining > 0 && d.days_remaining <= 30).length;
    const domDueCount = cachedDomains.filter(d => d.domain_days_remaining > 0 && d.domain_days_remaining <= 60).length;

    if (validEl) validEl.textContent = validCount;
    if (expEl) expEl.textContent = sslExpCount;
    if (domRenEl) domRenEl.textContent = domDueCount;

    filterDomainsGrid();
  } catch (err) {
    console.error('Error fetching domains:', err);
  }
}

function filterDomainsGrid() {
  const total = cachedDomains.length;
  const healthyCount = cachedDomains.filter(d => (d.is_valid && d.days_remaining > 30) && (!d.domain_days_remaining || d.domain_days_remaining > 60)).length;
  const sslExpCount = cachedDomains.filter(d => d.days_remaining > 0 && d.days_remaining <= 30).length;
  const domExpCount = cachedDomains.filter(d => d.domain_days_remaining > 0 && d.domain_days_remaining <= 60).length;
  const attentionCount = cachedDomains.filter(d => !d.is_valid || d.days_remaining <= 30 || (d.domain_days_remaining > 0 && d.domain_days_remaining <= 60)).length;

  const btnAll = document.getElementById('domainFilterAll');
  const btnHealthy = document.getElementById('domainFilterHealthy');
  const btnSslExp = document.getElementById('domainFilterSslExp');
  const btnDomExp = document.getElementById('domainFilterDomExp');
  const btnAttention = document.getElementById('domainFilterAttention');

  if (btnAll) btnAll.innerHTML = `All (${total})`;
  if (btnHealthy) btnHealthy.innerHTML = `Healthy (${healthyCount})`;
  if (btnSslExp) btnSslExp.innerHTML = `SSL Alert (<30d) (${sslExpCount})`;
  if (btnDomExp) btnDomExp.innerHTML = `Domain Renewal (<60d) (${domExpCount})`;
  if (btnAttention) btnAttention.innerHTML = `Attention (${attentionCount})`;

  const query = (document.getElementById('domainsSearchInput')?.value || '').toLowerCase().trim();
  const filterBtn = document.querySelector('.domain-filter-btn.active');
  const filter = filterBtn ? filterBtn.dataset.filter : 'all';

  const filtered = cachedDomains.filter(d => {
    const matchesQuery = !query || 
                         d.domain.toLowerCase().includes(query) || 
                         (d.issuer || '').toLowerCase().includes(query) ||
                         (d.registrar || '').toLowerCase().includes(query);
    if (!matchesQuery) return false;

    if (filter === 'healthy') {
      const sslOk = d.is_valid && d.days_remaining > 30;
      const domOk = !d.domain_days_remaining || d.domain_days_remaining > 60;
      return sslOk && domOk;
    }
    if (filter === 'ssl-expiring') return d.days_remaining > 0 && d.days_remaining <= 30;
    if (filter === 'domain-expiring') return d.domain_days_remaining > 0 && d.domain_days_remaining <= 60;
    if (filter === 'attention') {
      return !d.is_valid || d.days_remaining <= 30 || (d.domain_days_remaining > 0 && d.domain_days_remaining <= 60);
    }
    return true;
  });

  renderDomainsGrid(filtered);
}

function renderDomainsGrid(domains) {
  const container = document.getElementById('domainsGridContainer');
  if (!container) return;

  if (domains.length === 0) {
    container.innerHTML = `
      <div style="text-align: center; color: var(--text-muted); padding: 48px; background: var(--bg-card); border-radius: var(--radius-md); border: 1px solid var(--border-card); width: 100%;">
        <div style="font-size: 36px; margin-bottom: 10px;">🌐</div>
        <div style="font-size: 16px; font-weight: 600; color: var(--text-primary); margin-bottom: 6px;">No Domains Match Filter</div>
        <div style="font-size: 13px;">Try adjusting your search query or click "+ Track Domain" to monitor certificates & registrar expiration.</div>
      </div>
    `;
    return;
  }

  const isTable = (viewModes.domains === 'table');

  if (isTable) {
    container.innerHTML = `
      <div class="dense-table-wrapper">
        <table class="dense-data-table">
          <thead>
            <tr>
              <th style="width: 140px;">Status</th>
              <th>Domain / Hostname</th>
              <th>TLS / SSL Certificate</th>
              <th>Domain Renewal</th>
              <th>Registrar & Port</th>
              <th style="text-align: right;">Quick Actions</th>
            </tr>
          </thead>
          <tbody>
            ${domains.map(d => {
              const hasDomExp = Boolean(d.domain_expires_at);
              const isDomExpired = hasDomExp && d.domain_days_remaining <= 0;
              const isDomExpiring = hasDomExp && d.domain_days_remaining > 0 && d.domain_days_remaining <= 60;
              const isSslExpiring = d.days_remaining > 0 && d.days_remaining <= 30;
              const isInvalid = !d.is_valid || d.days_remaining <= 0 || isDomExpired;

              const dotClass = isInvalid ? 'pulse-dot-red' : ((isSslExpiring || isDomExpiring) ? 'pulse-dot-amber' : 'pulse-dot-green');
              const statusPill = isInvalid
                ? '<span class="badge badge-exited">🔴 CRITICAL</span>'
                : ((isSslExpiring || isDomExpiring) ? '<span class="badge badge-snoozed">🟡 ATTENTION</span>' : '<span class="badge badge-running">🟢 SECURED</span>');

              const sslDaysColor = (d.is_valid && d.days_remaining > 30) ? 'var(--color-success)' : (isSslExpiring ? 'var(--color-warning)' : 'var(--color-danger)');
              const domDaysColor = (!hasDomExp || d.domain_days_remaining > 60) ? 'var(--accent-cyan)' : (isDomExpiring ? 'var(--color-warning)' : 'var(--color-danger)');

              return `
                <tr>
                  <td>
                    <div style="display: flex; align-items: center; gap: 8px;">
                      <span class="${dotClass}"></span>
                      ${statusPill}
                    </div>
                  </td>
                  <td>
                    <a href="https://${escapeHtml(d.domain)}" target="_blank" style="color: var(--text-primary); font-weight: 700; font-size: 13.5px; text-decoration: none;" rel="noreferrer">
                      🌐 ${escapeHtml(d.domain)}
                    </a>
                    <div style="color: var(--text-muted); font-size: 11px; margin-top: 2px;">
                      Port :${d.port || 443} · TLS 1.3 / HTTPS
                    </div>
                  </td>
                  <td class="dense-col-mono">
                    <span style="font-weight: 700; color: ${sslDaysColor};">
                      ${d.days_remaining > 0 ? `${d.days_remaining}d left` : 'Expired'}
                    </span>
                    <span style="display: block; font-size: 11px; color: var(--text-muted);">
                      Exp: ${escapeHtml(d.expires_at || 'N/A')}
                    </span>
                  </td>
                  <td class="dense-col-mono">
                    <span style="font-weight: 700; color: ${domDaysColor};">
                      ${d.domain_days_remaining > 0 ? `${d.domain_days_remaining}d left` : (hasDomExp ? 'Due' : 'Active')}
                    </span>
                    <span style="display: block; font-size: 11px; color: var(--text-muted);">
                      Renews: ${escapeHtml(d.domain_expires_at || 'Auto-renew')}
                    </span>
                  </td>
                  <td style="color: var(--text-secondary); font-size: 12px;">
                    <div>🏷️ ${escapeHtml(d.registrar || 'Standard Registrar')}</div>
                    <div style="font-size: 11px; color: var(--text-muted);">Sync: ${escapeHtml(d.last_checked_at || 'Just now')}</div>
                  </td>
                  <td class="dense-actions-cell">
                    <button class="btn btn-secondary btn-sm" onclick="recheckDomain(${d.id})" id="recheckBtn-${d.id}" title="Recheck TLS & ICANN RDAP">🔄 Recheck</button>
                    <button class="btn btn-secondary btn-sm" onclick="openEditDomainModal(${d.id})" title="Edit Domain Registrar & Expiry Date">✏️ Edit</button>
                    <button class="btn btn-danger btn-sm" onclick="deleteDomain(${d.id}, '${escapeHtml(d.domain)}')" title="Delete Tracker">🗑</button>
                  </td>
                </tr>
              `;
            }).join('')}
          </tbody>
        </table>
      </div>
    `;
    return;
  }

  // Rows mode (matching Incident Audit pattern)
  container.innerHTML = domains.map(d => {
    // SSL Health
    const isSslHealthy = d.is_valid && d.days_remaining > 30;
    const isSslExpiring = d.days_remaining > 0 && d.days_remaining <= 30;
    const sslBoxClass = isSslHealthy ? 'highlight-ssl' : (isSslExpiring ? 'warning-state' : 'danger-state');
    const sslDaysColor = isSslHealthy ? 'var(--color-success)' : (isSslExpiring ? 'var(--color-warning)' : 'var(--color-danger)');

    // Domain Renewal Health
    const hasDomExp = Boolean(d.domain_expires_at);
    const isDomHealthy = !hasDomExp || d.domain_days_remaining > 60;
    const isDomExpiring = hasDomExp && d.domain_days_remaining > 0 && d.domain_days_remaining <= 60;
    const isDomExpired = hasDomExp && d.domain_days_remaining <= 0;
    const domBoxClass = isDomHealthy ? 'highlight-domain' : (isDomExpiring ? 'warning-state' : 'danger-state');
    const domDaysColor = isDomHealthy ? 'var(--accent-cyan)' : (isDomExpiring ? 'var(--color-warning)' : 'var(--color-danger)');

    // Overall Status
    let overallTag = '<span class="badge badge-running">🟢 ACTIVE & SECURED</span>';
    let rowClass = 'status-healthy';
    let dotClass = 'pulse-dot-green';
    if (!d.is_valid || d.days_remaining <= 0 || isDomExpired) {
      overallTag = '<span class="badge badge-exited">🔴 CRITICAL ALERT</span>';
      rowClass = 'status-danger';
      dotClass = 'pulse-dot-red';
    } else if (isSslExpiring || isDomExpiring) {
      overallTag = '<span class="badge badge-snoozed">🟡 RENEWAL ALERT</span>';
      rowClass = 'status-warning';
      dotClass = 'pulse-dot-amber';
    }

    return `
      <div class="entity-row-card ${rowClass}">
        <div class="entity-row-header">
          <div class="entity-identity-block">
            <div class="entity-identity-row">
              <span class="${dotClass}"></span>
              <a href="https://${escapeHtml(d.domain)}" target="_blank" class="entity-title-text" style="text-decoration: none;" rel="noreferrer">
                🌐 ${escapeHtml(d.domain)}
              </a>
              <span class="badge-source">🔒 Port :${d.port || 443}</span>
              <span class="badge-source">🏷️ ${escapeHtml(d.registrar || 'Standard Registrar')}</span>
            </div>
            <div class="entity-sub-identifier">Issuer: ${escapeHtml(d.issuer || 'TLS Certificate Authority')}</div>
          </div>
          <div>${overallTag}</div>
        </div>

        <div class="domain-dual-deck" style="margin: 4px 0 0 0;">
          <!-- Box 1: TLS / SSL Certificate -->
          <div class="domain-stat-box ${sslBoxClass}">
            <div class="domain-stat-header">
              <span class="domain-stat-icon">🔒</span>
              <span class="domain-stat-title">SSL Certificate</span>
            </div>
            <div class="domain-stat-days" style="color: ${sslDaysColor};">
              ${d.days_remaining > 0 ? `${d.days_remaining}d` : 'Expired'}
            </div>
            <div class="domain-stat-sub">
              ${d.days_remaining > 0 ? `${d.days_remaining} days remaining` : 'Certificate Expired'}
            </div>
            <div class="domain-stat-date" title="SSL Certificate Expiry Date">
              Expires: <strong>${escapeHtml(d.expires_at || 'N/A')}</strong>
            </div>
          </div>

          <!-- Box 2: ICANN Domain Registration -->
          <div class="domain-stat-box ${domBoxClass}">
            <div class="domain-stat-header">
              <span class="domain-stat-icon">🌐</span>
              <span class="domain-stat-title">Domain Renewal</span>
            </div>
            <div class="domain-stat-days" style="color: ${domDaysColor};">
              ${d.domain_days_remaining > 0 ? `${d.domain_days_remaining}d` : (hasDomExp ? 'Due' : 'Active')}
            </div>
            <div class="domain-stat-sub">
              ${d.domain_days_remaining > 0 ? `${d.domain_days_remaining} days remaining` : (hasDomExp ? 'Renewal Due' : 'Auto-renew active')}
            </div>
            <div class="domain-stat-date" title="Domain Registrar Expiry Date">
              Renews: <strong>${escapeHtml(d.domain_expires_at || 'Auto-renew')}</strong>
            </div>
          </div>
        </div>

        ${d.error ? `
          <div class="entity-details-box" style="border-color: rgba(239, 68, 68, 0.3); background: rgba(239, 68, 68, 0.08); color: var(--color-danger);">
            <span>⚠️ <strong>PROBE WARNING:</strong> ${escapeHtml(d.error)}</span>
          </div>
        ` : ''}

        <div class="entity-row-footer">
          <div class="entity-meta-list">
            <span>📅 Registered: ${escapeHtml(d.registration_date || 'Standard Registration')}</span>
            <span>🕒 Last Handshake: ${escapeHtml(d.last_checked_at || 'Just now')}</span>
            <span>⏱️ Cadence: Daily (12:00 UTC)</span>
          </div>
          <div class="entity-actions-group">
            <button class="btn btn-secondary btn-sm" onclick="recheckDomain(${d.id})" id="recheckBtn-${d.id}" title="Recheck TLS & ICANN RDAP">
              🔄 Recheck Both
            </button>
            <button class="btn btn-secondary btn-sm" onclick="openEditDomainModal(${d.id})" title="Edit Domain Registrar & Expiry Date">
              ✏️ Edit Expiry
            </button>
            <button class="btn btn-danger btn-sm" onclick="deleteDomain(${d.id}, '${escapeHtml(d.domain)}')" title="Delete Tracker">
              🗑 Delete
            </button>
          </div>
        </div>
      </div>
    `;
  }).join('');
}

window.openEditDomainModal = function(domainId) {
  const d = (cachedDomains || []).find(item => item.id === domainId);
  if (!d) return;

  const idInput = document.getElementById('editDomainIdInput');
  const nameInput = document.getElementById('editDomainNameInput');
  const portInput = document.getElementById('editDomainPortInput');
  const regInput = document.getElementById('editDomainRegistrarInput');
  const expInput = document.getElementById('editDomainExpiryInput');

  if (idInput) idInput.value = d.id;
  if (nameInput) nameInput.value = d.domain;
  if (portInput) portInput.value = d.port || 443;
  if (regInput) regInput.value = d.registrar || '';
  if (expInput) expInput.value = d.domain_expires_at || '';

  openModal('editDomainModal');
};

async function recheckDomain(domainId) {
  const btn = document.getElementById(`recheckBtn-${domainId}`);
  if (btn) {
    btn.disabled = true;
    btn.textContent = '🔄 Probing...';
  }
  try {
    const res = await apiFetch(`/api/domains/${domainId}/check`, { method: 'POST' });
    const data = await res.json();
    if (res.ok && data.success) {
      showToast(data.message, 'success');
      fetchDomains();
    } else {
      showToast(data.detail || 'Failed to recheck certificate.', 'error');
    }
  } catch (err) {
    showToast('Network error during SSL/RDAP check.', 'error');
  } finally {
    if (btn) {
      btn.disabled = false;
      btn.textContent = '🔄 Recheck Both';
    }
  }
}

async function deleteDomain(domainId, domainName) {
  if (!confirm(`Are you sure you want to stop tracking certificate for ${domainName}?`)) return;
  try {
    const res = await apiFetch(`/api/domains/${domainId}`, { method: 'DELETE' });
    const data = await res.json().catch(() => ({}));
    if (res.ok) {
      showToast(data.message || 'Domain removed from tracking.', 'success');
      fetchDomains();
      fetchOverview();
    } else {
      showToast(data.detail || 'Failed to remove domain.', 'error');
    }
  } catch (err) {
    showToast('Network error removing domain.', 'error');
  }
}

// ── Multi-Channel Alert Routing Matrix ───────────────────────────────────────

window.alertRoutesCache = [];

async function fetchAlertRoutes() {
  try {
    const res = await apiFetch('/api/alert-routes');
    if (!res.ok) return;
    const routes = await res.json();
    window.alertRoutesCache = routes;

    filterAlertRoutes();
  } catch (err) {
    console.error('Error fetching alert routes:', err);
  }
}

function filterAlertRoutes() {
  const routes = window.alertRoutesCache || [];
  const total = routes.length;
  const enabledCount = routes.filter(r => r.enabled).length;
  const disabledCount = routes.filter(r => !r.enabled).length;

  const btnAll = document.getElementById('routeFilterAll');
  const btnEn = document.getElementById('routeFilterEnabled');
  const btnDis = document.getElementById('routeFilterDisabled');

  if (btnAll) btnAll.innerHTML = `All (${total})`;
  if (btnEn) btnEn.innerHTML = `Active (${enabledCount})`;
  if (btnDis) btnDis.innerHTML = `Disabled (${disabledCount})`;

  const query = (document.getElementById('alertRoutesSearchInput')?.value || '').toLowerCase().trim();
  const filterBtn = document.querySelector('.route-filter-btn.active');
  const filter = filterBtn ? filterBtn.dataset.filter : 'all';

  const filtered = routes.filter(r => {
    const matchesQuery = !query ||
      (r.label || '').toLowerCase().includes(query) ||
      (r.chat_id || '').toLowerCase().includes(query) ||
      (r.categories || []).some(c => c.toLowerCase().includes(query)) ||
      (r.events || []).some(e => e.toLowerCase().includes(query));
    if (!matchesQuery) return false;

    if (filter === 'enabled') return r.enabled;
    if (filter === 'disabled') return !r.enabled;
    return true;
  });

  renderAlertRoutes(filtered);
}

function renderAlertRoutes(routes) {
  const container = document.getElementById('alertRoutesContainer');
  if (!container) return;

  if (routes.length === 0) {
    container.innerHTML = `
      <div style="text-align: center; color: var(--text-muted); padding: 48px; background: var(--bg-card); border-radius: var(--radius-md); border: 1px solid var(--border-card); width: 100%; grid-column: 1/-1;">
        <div style="font-size: 36px; margin-bottom: 10px;">⚡</div>
        <div style="font-size: 16px; font-weight: 600; color: var(--text-primary); margin-bottom: 6px;">No Alert Routes Match Filter</div>
        <div style="font-size: 13px;">Adjust your search query or add a new alert route to route incident dispatches.</div>
      </div>
    `;
    return;
  }

  container.innerHTML = routes.map(r => {
    const cats = r.categories || [];
    const evs = r.events || [];
    const catBadges = cats.map(c => `<span class="route-tag">📂 ${escapeHtml(c)}</span>`).join('');
    const evBadges = evs.length > 0 
      ? evs.map(e => `<span class="route-tag" style="background: rgba(6,182,212,0.15); color: var(--accent-cyan);">🎯 ${escapeHtml(e)}</span>`).join('')
      : '<span class="route-tag" style="opacity: 0.7;">🎯 all events</span>';

    return `
      <div class="entity-row-card ${r.enabled ? 'status-healthy' : 'status-muted'}">
        <div class="entity-row-header">
          <div class="entity-identity-block">
            <div class="entity-identity-row">
              <span class="${r.enabled ? 'pulse-dot-green' : 'status-dot'}"></span>
              <span class="entity-title-text">⚡ ${escapeHtml(r.label)}</span>
              <span class="badge-source">💬 Telegram: ${escapeHtml(r.chat_id)}</span>
            </div>
            <div class="entity-sub-identifier">Routing Rule ID: #${r.id}</div>
          </div>
          <div>
            <span class="badge ${r.enabled ? 'badge-running' : 'badge-snoozed'}">
              ${r.enabled ? '● ACTIVE ROUTE' : '● PAUSED'}
            </span>
          </div>
        </div>

        <div class="entity-details-box">
          <div class="entity-detail-item">
            <span class="entity-detail-label">MATCHING CATEGORIES:</span>
            <div style="display: flex; gap: 4px; flex-wrap: wrap;">
              ${catBadges.length > 0 ? catBadges : '<span class="route-tag" style="opacity: 0.6;">All Categories</span>'}
            </div>
          </div>
          <div class="entity-detail-item">
            <span class="entity-detail-label">EVENTS:</span>
            <div style="display: flex; gap: 4px; flex-wrap: wrap;">
              ${evBadges}
            </div>
          </div>
        </div>

        <div class="entity-row-footer">
          <div class="entity-meta-list">
            <span>💬 Channel Target: <code>${escapeHtml(r.chat_id)}</code></span>
            <span>📡 Dispatches: Real-Time Webhook</span>
          </div>
          <div class="entity-actions-group">
            <button class="btn btn-secondary btn-sm" onclick="testAlertRoute('${escapeHtml(r.chat_id)}')">
              🧪 Test Alert
            </button>
            <button class="btn btn-secondary btn-sm" onclick="openEditRouteModal(${r.id})">
              ✏️ Edit
            </button>
            <button class="btn btn-danger btn-sm" onclick="deleteAlertRoute(${r.id})" title="Delete Alert Route">
              🗑 Delete
            </button>
          </div>
        </div>
      </div>
    `;
  }).join('');
}

function openAddRouteModal() {
  document.getElementById('routeModalTitle').textContent = 'Add Alert Route';
  document.getElementById('routeIdInput').value = '';
  document.getElementById('routeLabelInput').value = '';
  document.getElementById('routeChatIdInput').value = '';
  document.getElementById('routeEventsInput').value = '';
  document.getElementById('routeEnabledInput').checked = true;

  // Uncheck all categories except all
  document.querySelectorAll('#routeForm input[type="checkbox"]').forEach(cb => {
    if (cb.id === 'routeEnabledInput') return;
    cb.checked = cb.value === 'all';
  });

  openModal('routeModal');
}

function openEditRouteModal(routeId) {
  const route = (window.alertRoutesCache || []).find(r => r.id === routeId);
  if (!route) return;

  document.getElementById('routeModalTitle').textContent = 'Edit Alert Route';
  document.getElementById('routeIdInput').value = route.id;
  document.getElementById('routeLabelInput').value = route.label;
  document.getElementById('routeChatIdInput').value = route.chat_id;
  document.getElementById('routeEventsInput').value = (route.events || []).join(', ');
  document.getElementById('routeEnabledInput').checked = Boolean(route.enabled);

  const cats = route.categories || [];
  document.querySelectorAll('#routeForm input[type="checkbox"]').forEach(cb => {
    if (cb.id === 'routeEnabledInput') return;
    cb.checked = cats.includes(cb.value);
  });

  openModal('routeModal');
}

async function handleRouteFormSubmit(e) {
  e.preventDefault();
  const routeId = document.getElementById('routeIdInput').value;
  const label = document.getElementById('routeLabelInput').value.trim();
  const chatId = document.getElementById('routeChatIdInput').value.trim();
  const enabled = document.getElementById('routeEnabledInput').checked;
  const eventsRaw = document.getElementById('routeEventsInput').value;
  const events = eventsRaw.split(',').map(s => s.trim().toLowerCase()).filter(Boolean);

  const categories = [];
  document.querySelectorAll('#routeForm input[type="checkbox"]:checked').forEach(cb => {
    if (cb.id !== 'routeEnabledInput') {
      categories.push(cb.value);
    }
  });

  if (categories.length === 0) {
    categories.push('all');
  }

  const payload = {
    label,
    chat_id: chatId,
    categories,
    events,
    enabled
  };

  try {
    const isEdit = Boolean(routeId);
    const url = isEdit ? `/api/alert-routes/${routeId}` : '/api/alert-routes';
    const method = isEdit ? 'PUT' : 'POST';

    const res = await apiFetch(url, {
      method,
      body: JSON.stringify(payload)
    });

    const data = await res.json();
    if (res.ok && data.success) {
      showToast(isEdit ? 'Alert route updated.' : 'Alert route created.', 'success');
      closeModal('routeModal');
      fetchAlertRoutes();
    } else {
      showToast(data.detail || 'Failed to save alert route.', 'error');
    }
  } catch (err) {
    showToast('Network error saving alert route.', 'error');
  }
}

async function deleteAlertRoute(routeId) {
  if (!confirm('Are you sure you want to delete this alert route?')) return;
  try {
    const res = await apiFetch(`/api/alert-routes/${routeId}`, { method: 'DELETE' });
    const data = await res.json().catch(() => ({}));
    if (res.ok) {
      showToast(data.message || 'Alert route deleted.', 'success');
      fetchAlertRoutes();
    } else {
      showToast(data.detail || 'Failed to delete route.', 'error');
    }
  } catch (err) {
    showToast('Network error deleting route.', 'error');
  }
}

async function testAlertRoute(chatId) {
  showToast(`Sending test alert to ${chatId}...`, 'info');
  try {
    const res = await apiFetch('/api/alert-routes/test', {
      method: 'POST',
      body: JSON.stringify({ chat_id: chatId })
    });
    const data = await res.json();
    if (res.ok && data.success) {
      showToast(data.message, 'success');
    } else {
      showToast(data.detail || 'Test alert delivery failed.', 'error');
    }
  } catch (err) {
    showToast('Failed to reach server for test alert.', 'error');
  }
}
