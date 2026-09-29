/**
 * OpsPilot 2.0 — Web Command Center Application Logic
 */

let csrfToken = '';
let currentTab = 'overview';
let activeLogsContainer = '';
let activeRestartContainer = '';
let activeSnoozeContainer = '';
let autoRefreshPaused = false;
let refreshCountdown = 5;
let timerInterval = null;

// ── Authentication & Boot ───────────────────────────────────────────────────

document.addEventListener('DOMContentLoaded', () => {
  initTheme();
  initAuth();
  setupEventListeners();
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
      startAutoRefresh();
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
    const password = document.getElementById('adminPasswordInput').value;
    const errBox = document.getElementById('loginErrorMsg');
    const submitBtn = document.getElementById('loginSubmitBtn');

    errBox.style.display = 'none';
    submitBtn.disabled = true;
    submitBtn.textContent = 'Verifying...';

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
        errBox.textContent = data.detail || 'Authentication failed.';
        errBox.style.display = 'block';
      }
    } catch (err) {
      errBox.textContent = 'Connection error. Please try again.';
      errBox.style.display = 'block';
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
  document.getElementById('refreshTimerBtn').addEventListener('click', () => {
    autoRefreshPaused = !autoRefreshPaused;
    const btn = document.getElementById('refreshTimerBtn');
    if (autoRefreshPaused) {
      btn.innerHTML = '⏸ Paused';
      btn.style.borderColor = 'var(--color-warning)';
    } else {
      refreshCountdown = 5;
      btn.innerHTML = '⏱ <span id="refreshTimerCountdown">5s</span>';
      btn.style.borderColor = 'var(--border-subtle)';
      refreshAll();
    }
  });

  // Fleet Filter & Search
  document.getElementById('fleetSearchInput').addEventListener('input', filterFleetGrid);
  document.querySelectorAll('.fleet-filter-btn').forEach(btn => {
    btn.addEventListener('click', (e) => {
      document.querySelectorAll('.fleet-filter-btn').forEach(b => b.classList.remove('active'));
      btn.classList.add('active');
      filterFleetGrid();
    });
  });

  // Modal Triggers
  document.getElementById('openAddProbeBtn').addEventListener('click', () => openModal('addProbeModal'));
  document.getElementById('openAddRenewalBtn').addEventListener('click', () => openModal('addRenewalModal'));

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
        showToast(data.detail || 'Failed to add probe.', 'error');
      }
    } catch (err) {
      showToast('Network error adding probe.', 'error');
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

  document.getElementById('settingsForm').addEventListener('submit', async (e) => {
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
}

function switchTab(tabId) {
  currentTab = tabId;
  document.querySelectorAll('.tab-btn').forEach(btn => {
    btn.classList.toggle('active', btn.dataset.tab === tabId);
  });
  document.querySelectorAll('.tab-content').forEach(section => {
    section.classList.toggle('active', section.id === `tab-${tabId}`);
  });

  if (tabId === 'overview') fetchOverview();
  else if (tabId === 'fleet') fetchFleet();
  else if (tabId === 'probes') fetchProbes();
  else if (tabId === 'renewals') fetchRenewals();
  else if (tabId === 'incidents') fetchIncidents();
  else if (tabId === 'settings') fetchSettings();
}

// ── Polling & Auto-Refresh ──────────────────────────────────────────────────

function startAutoRefresh() {
  stopAutoRefresh();
  refreshCountdown = 5;
  timerInterval = setInterval(() => {
    if (autoRefreshPaused) return;
    refreshCountdown--;
    const el = document.getElementById('refreshTimerCountdown');
    if (el) el.textContent = `${refreshCountdown}s`;

    if (refreshCountdown <= 0) {
      refreshCountdown = 5;
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
    document.getElementById('cpuProgressBar').style.width = `${m.cpu_percent || 0}%`;
    document.getElementById('loadAvgVal').textContent = `Load: ${(m.load_avg || []).slice(0, 2).join(', ')}`;

    document.getElementById('ramMetricVal').textContent = `${m.ram_percent || 0}%`;
    document.getElementById('ramProgressBar').style.width = `${m.ram_percent || 0}%`;
    document.getElementById('ramUsedTotalVal').textContent = `${m.ram_used_gb || 0} GB / ${m.ram_total_gb || 0} GB`;

    document.getElementById('diskMetricVal').textContent = `${m.disk_percent || 0}%`;
    document.getElementById('diskProgressBar').style.width = `${m.disk_percent || 0}%`;
    document.getElementById('diskFreeVal').textContent = `Free: ${m.disk_free_gb || 0} GB`;

    document.getElementById('uptimeVal').textContent = `Uptime: ${m.uptime_human || '--'}`;

    // Counts & Badges
    const c = data.counts || {};
    document.getElementById('navFleetCount').textContent = c.containers_total || 0;
    document.getElementById('navProbesCount').textContent = c.endpoints_total || 0;
    document.getElementById('navRenewalsCount').textContent = c.renewals_pending || 0;
    document.getElementById('navIncidentsCount').textContent = c.incidents_open || 0;

    // Global Status Dot
    const dot = document.getElementById('globalStatusDot');
    if (c.incidents_open > 0 || c.containers_unhealthy > 0) {
      dot.className = 'status-dot red';
    } else {
      dot.className = 'status-dot green';
    }

    // Recent Incidents list in Overview
    const incBox = document.getElementById('overviewIncidentsContainer');
    const recent = data.recent_incidents || [];
    if (recent.length === 0) {
      incBox.innerHTML = '<div style="color: var(--text-muted); font-size: 14px; text-align: center; padding: 24px 0;">✅ No active incidents. All systems are operating smoothly.</div>';
    } else {
      incBox.innerHTML = recent.map(inc => `
        <div style="display: flex; justify-content: space-between; align-items: center; padding: 12px 0; border-bottom: 1px solid var(--border-subtle);">
          <div>
            <strong style="color: var(--color-danger);">${escapeHtml(inc.target)}</strong> — ${escapeHtml(inc.title)}
            <div style="font-size: 12px; color: var(--text-muted);">${escapeHtml(inc.detail || '')} · Alert #${inc.alert_count}</div>
          </div>
          <button class="btn btn-secondary btn-sm" onclick="resolveIncident(${inc.id})">Mark Resolved</button>
        </div>
      `).join('');
    }
  } catch (err) {
    console.error('Error fetching overview:', err);
  }
}

// ── Tab 2: Docker Fleet ─────────────────────────────────────────────────────

let cachedContainers = [];

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
  const query = (document.getElementById('fleetSearchInput').value || '').toLowerCase();
  const filterBtn = document.querySelector('.fleet-filter-btn.active');
  const filter = filterBtn ? filterBtn.dataset.filter : 'all';

  const filtered = cachedContainers.filter(c => {
    const matchesQuery = c.name.toLowerCase().includes(query) || (c.image || '').toLowerCase().includes(query);
    if (!matchesQuery) return false;

    if (filter === 'running') return c.status === 'running' && c.health !== 'unhealthy';
    if (filter === 'exited') return c.status === 'exited';
    if (filter === 'snoozed') return c.is_snoozed;
    return true;
  });

  const grid = document.getElementById('fleetCardsGrid');
  if (filtered.length === 0) {
    grid.innerHTML = '<div style="grid-column: 1/-1; text-align: center; color: var(--text-muted); padding: 40px;">No containers match your search.</div>';
    return;
  }

  grid.innerHTML = filtered.map(c => {
    let badgeClass = 'badge-running';
    let statusText = c.status;
    if (c.health === 'unhealthy') {
      badgeClass = 'badge-unhealthy';
      statusText = 'unhealthy';
    } else if (c.status === 'exited') {
      badgeClass = 'badge-exited';
      statusText = 'exited';
    }

    return `
      <div class="glass-panel container-card">
        <div>
          <div class="container-card-header">
            <div>
              <div class="container-name">${escapeHtml(c.name)}</div>
              <div class="container-image">${escapeHtml(c.image || 'docker-image')}</div>
            </div>
            <div style="display: flex; gap: 6px; flex-direction: column; align-items: flex-end;">
              <span class="badge ${badgeClass}">${statusText}</span>
              ${c.is_snoozed ? '<span class="badge badge-snoozed">⏰ Snoozed</span>' : ''}
            </div>
          </div>
        </div>

        <div class="card-actions">
          <button class="btn btn-secondary btn-sm" onclick="openLogsModal('${escapeHtml(c.name)}')">📋 Logs</button>
          <button class="btn btn-secondary btn-sm" onclick="openRestartModal('${escapeHtml(c.name)}')">🔄 Restart</button>
          ${c.is_snoozed 
            ? `<button class="btn btn-secondary btn-sm" onclick="unsnoozeContainer('${escapeHtml(c.name)}')">🔔 Unsnooze</button>`
            : `<button class="btn btn-secondary btn-sm" onclick="openSnoozeModal('${escapeHtml(c.name)}')">⏰ Snooze</button>`
          }
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
    const probes = await res.json();
    document.getElementById('navProbesCount').textContent = probes.length;

    const list = document.getElementById('probesListContainer');
    if (probes.length === 0) {
      list.innerHTML = '<div style="text-align: center; color: var(--text-muted); padding: 40px; grid-column: 1/-1;">No HTTP probes configured yet. Click "+ Add HTTP Probe" to start monitoring.</div>';
      return;
    }

    list.innerHTML = probes.map(p => {
      let latencyClass = 'latency-normal';
      if (p.latency_ms < 150) latencyClass = 'latency-fast';
      else if (p.latency_ms > 600) latencyClass = 'latency-slow';

      const isHealthy = p.is_healthy;
      const statusBadge = isHealthy
        ? `<span class="badge badge-running">HTTP ${p.status_code || 200}</span>`
        : `<span class="badge badge-exited">FAIL ${p.status_code ? 'HTTP ' + p.status_code : 'DOWN'}</span>`;

      // 10 micro-status spark bars (BetterStack style)
      const sparkBars = Array.from({ length: 10 }, (_, i) => {
        let barClass = 'uptime-spark-bar';
        if (!isHealthy && i >= 8) barClass += ' down';
        else if (p.latency_ms > 400 && i >= 7) barClass += ' degraded';
        return `<span class="${barClass}"></span>`;
      }).join('');

      return `
        <div class="glass-panel probe-item">
          <div class="probe-header">
            <div class="probe-title-group">
              <span class="status-dot ${isHealthy ? 'green' : 'red'}"></span>
              <div>
                <div class="probe-name">${escapeHtml(p.name)}</div>
                <a href="${escapeHtml(p.url)}" target="_blank" rel="noopener noreferrer" class="probe-url-link">${escapeHtml(p.url)}</a>
              </div>
            </div>
            <div>${statusBadge}</div>
          </div>

          <div class="probe-metrics-strip">
            <div style="display: flex; flex-direction: column; gap: 3px;">
              <span style="font-size: 10px; color: var(--text-muted); text-transform: uppercase; letter-spacing: 0.05em; font-weight: 600;">Latency</span>
              <span class="latency-pill ${latencyClass}">⚡ ${p.latency_ms || 0} ms</span>
            </div>
            <div style="display: flex; flex-direction: column; align-items: flex-end; gap: 3px;">
              <span style="font-size: 10px; color: var(--text-muted); text-transform: uppercase; letter-spacing: 0.05em; font-weight: 600;">Status Pulse</span>
              <div class="uptime-spark-bars">${sparkBars}</div>
            </div>
          </div>

          <div class="probe-actions-footer">
            <span style="font-size: 12px; color: var(--text-muted);">
              ${p.enabled === 1 ? '<span style="color: var(--color-success);">● Active</span>' : '<span style="color: var(--text-muted);">○ Paused</span>'}
            </span>
            <div style="display: flex; gap: 8px;">
              <button class="btn btn-secondary btn-sm" onclick="toggleProbe(${p.id}, ${p.enabled === 1 ? 'false' : 'true'})">
                ${p.enabled === 1 ? 'Pause' : 'Resume'}
              </button>
              <button class="btn btn-danger btn-sm" onclick="deleteProbe(${p.id})">🗑</button>
            </div>
          </div>
        </div>
      `;
    }).join('');
  } catch (err) {
    console.error('Error fetching probes:', err);
  }
}

async function toggleProbe(probeId, newEnabledState) {
  try {
    const res = await apiFetch(`/api/probes/${probeId}/toggle`, {
      method: 'POST',
      body: JSON.stringify({ enabled: newEnabledState })
    });
    if (res.ok) {
      showToast('Probe state updated.', 'success');
      fetchProbes();
    }
  } catch (err) {
    showToast('Failed to toggle probe.', 'error');
  }
}

async function deleteProbe(probeId) {
  if (!confirm('Remove this HTTP probe?')) return;
  try {
    const res = await apiFetch(`/api/probes/${probeId}`, { method: 'DELETE' });
    if (res.ok) {
      showToast('Probe removed.', 'success');
      fetchProbes();
    }
  } catch (err) {
    showToast('Failed to delete probe.', 'error');
  }
}

// ── Tab 4: Renewals & Billing ───────────────────────────────────────────────

async function fetchRenewals() {
  try {
    const res = await apiFetch('/api/renewals');
    if (!res.ok) return;
    const renewals = await res.json();
    window.renewalsCache = renewals;
    document.getElementById('navRenewalsCount').textContent = renewals.filter(r => r.status === 'pending').length;

    const grid = document.getElementById('renewalsGridContainer');
    if (renewals.length === 0) {
      grid.innerHTML = '<div style="grid-column: 1/-1; text-align: center; color: var(--text-muted); padding: 40px;">No upcoming renewals or client billing items registered.</div>';
      return;
    }

    grid.innerHTML = renewals.map(r => {
      const isPaid = r.status === 'paid';
      const isCancelled = r.status === 'cancelled';
      let dueBadge = `<span class="renewal-due-badge normal">Due: ${escapeHtml(r.due_date)}</span>`;

      // Check if due soon (within 7 days)
      const dueDate = new Date(r.due_date);
      const diffDays = Math.ceil((dueDate - new Date()) / (1000 * 60 * 60 * 24));
      if (!isPaid && !isCancelled && diffDays < 0) {
        dueBadge = `<span class="renewal-due-badge overdue">⚠️ Overdue (${Math.abs(diffDays)}d)</span>`;
      } else if (!isPaid && !isCancelled && diffDays <= 7) {
        dueBadge = `<span class="renewal-due-badge soon">⏳ Due in ${diffDays} day${diffDays === 1 ? '' : 's'}</span>`;
      } else if (isPaid) {
        dueBadge = `<span class="badge badge-running">✅ Paid</span>`;
      }

      // Initials avatar
      const initials = (r.name || 'CR')
        .split(' ')
        .map(w => w[0])
        .slice(0, 2)
        .join('')
        .toUpperCase();

      return `
        <div class="glass-panel renewal-card" style="${isPaid ? 'opacity: 0.6;' : ''}">
          <div>
            <div class="renewal-header">
              <div style="display: flex; gap: 12px; align-items: center;">
                <div class="renewal-avatar">${initials}</div>
                <div>
                  <h3 style="font-size: 16px; font-weight: 600; color: var(--text-primary); margin-bottom: 2px;">${escapeHtml(r.name)}</h3>
                  <span class="badge badge-snoozed" style="font-size: 10px; text-transform: uppercase;">${escapeHtml(r.category || 'client_billing')}</span>
                </div>
              </div>
              ${dueBadge}
            </div>
            
            <div style="margin: 16px 0;">
              <div class="renewal-amount-display">
                ₹${Number(r.amount || 0).toLocaleString('en-IN')}
                <span style="font-size: 13px; font-weight: normal; color: var(--text-muted); margin-left: 4px;">/ ${r.recurrence || 'monthly'}</span>
              </div>
              ${r.notes ? `<p style="font-size: 12px; color: var(--text-muted); margin-top: 4px; line-height: 1.4;">${escapeHtml(r.notes)}</p>` : ''}
            </div>
          </div>

          <div class="card-actions" style="justify-content: flex-end; gap: 6px;">
            ${!isPaid ? `
              <button class="btn btn-success btn-sm" onclick="markRenewalPaid(${r.id})">✅ Paid</button>
              <button class="btn btn-secondary btn-sm" onclick="snoozeRenewal(${r.id})">⏰ Snooze</button>
              <button class="btn btn-secondary btn-sm" onclick="openEditRenewalModal(${r.id})">✏️ Edit</button>
            ` : ''}
            <button class="btn btn-danger btn-sm" onclick="deleteRenewal(${r.id})">🗑</button>
          </div>
        </div>
      `;
    }).join('');
  } catch (err) {
    console.error('Error fetching renewals:', err);
  }
}

async function markRenewalPaid(renewalId) {
  try {
    const res = await apiFetch(`/api/renewals/${renewalId}/pay`, { method: 'POST' });
    const data = await res.json();
    if (res.ok && data.success) {
      showToast(data.message, 'success');
      fetchRenewals();
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
    }
  } catch (err) {
    showToast('Failed to snooze renewal.', 'error');
  }
}

async function deleteRenewal(renewalId) {
  if (!confirm('Cancel / delete this renewal?')) return;
  try {
    const res = await apiFetch(`/api/renewals/${renewalId}`, { method: 'DELETE' });
    if (res.ok) {
      showToast('Renewal deleted.', 'success');
      fetchRenewals();
    }
  } catch (err) {
    showToast('Failed to delete renewal.', 'error');
  }
}

// ── Tab 5: Incidents ────────────────────────────────────────────────────────

window.incidentsCache = [];
let currentIncidentFilter = 'all';

async function fetchIncidents() {
  try {
    const res = await apiFetch('/api/incidents');
    if (!res.ok) return;
    const incidents = await res.json();
    window.incidentsCache = incidents;

    const total = incidents.length;
    const active = incidents.filter(i => !i.resolved_at).length;
    const resolved = incidents.filter(i => Boolean(i.resolved_at)).length;

    // Update KPI Counters
    const activeEl = document.getElementById('activeIncidentsCountVal');
    if (activeEl) {
      activeEl.innerHTML = active > 0
        ? `<span class="status-dot red"></span> ${active} Open`
        : `<span class="status-dot green"></span> 0 Open`;
    }
    const totalEl = document.getElementById('totalIncidentsCountVal');
    if (totalEl) totalEl.textContent = total;
    const rateEl = document.getElementById('autoResolvedRateVal');
    if (rateEl) {
      rateEl.textContent = total > 0 ? `${Math.round((resolved / total) * 100)}%` : '100%';
    }

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
        <div style="font-size: 32px; margin-bottom: 8px;">🛡️</div>
        <div style="font-size: 15px; font-weight: 600; color: var(--text-primary); margin-bottom: 4px;">No Incidents Match Filter</div>
        <div style="font-size: 13px;">All infrastructure endpoints and services are operating normally.</div>
      </div>
    `;
    return;
  }

  container.innerHTML = filtered.map(inc => {
    const isResolved = Boolean(inc.resolved_at);
    const sourceIcon = inc.source === 'http_probe' ? '🌐 HTTP Probe' : (inc.source === 'docker' ? '🐳 Docker Container' : '🔒 SSL Expiry');
    const severityBadge = inc.severity === 'critical' 
      ? '<span class="badge-severity-critical">● CRITICAL</span>'
      : '<span class="badge-severity-warning">● WARNING</span>';
    const statusBadge = isResolved
      ? '<span class="badge-status-resolved">✓ RESOLVED</span>'
      : '<span class="badge-status-active">⚠️ ACTIVE INCIDENT</span>';

    return `
      <div class="incident-card ${!isResolved ? 'active-incident' : ''}">
        <div class="incident-card-header">
          <div>
            <div class="incident-target-title">
              <span class="status-dot ${isResolved ? 'green' : 'red'}"></span>
              <span>${escapeHtml(inc.title)}</span>
            </div>
            <div style="margin-top: 6px; display: flex; gap: 8px; align-items: center; flex-wrap: wrap;">
              <span class="badge-source">${sourceIcon}</span>
              ${severityBadge}
              <span style="font-size: 12px; color: var(--accent-cyan); font-family: ui-monospace, SFMono-Regular, monospace;">${escapeHtml(inc.target)}</span>
            </div>
          </div>
          <div>${statusBadge}</div>
        </div>

        <div class="incident-code-box">
          <span style="opacity: 0.6; font-weight: 600;">Diagnostics:</span>
          <span>${escapeHtml(inc.detail || 'Incident registered by autonomous health probe.')}</span>
        </div>

        <div class="incident-footer-meta">
          <div style="display: flex; gap: 14px; align-items: center; flex-wrap: wrap;">
            <span>🔔 <strong>${inc.alert_count || 1}</strong> alerts dispatched</span>
            <span>🕒 Detected: <strong>${escapeHtml(inc.created_at || 'Just now')}</strong></span>
            ${isResolved ? `<span>⚡ Auto-Resolved: <strong>${escapeHtml(inc.resolved_at)}</strong></span>` : ''}
          </div>
          <div>
            ${!isResolved 
              ? `<button class="btn btn-primary btn-sm" onclick="resolveIncident(${inc.id})">Mark Resolved</button>` 
              : '<span style="font-size: 11px; color: var(--color-success); font-weight: 500;">✓ Audited & Closed</span>'}
          </div>
        </div>
      </div>
    `;
  }).join('');
}

async function resolveIncident(incId) {
  try {
    const res = await apiFetch(`/api/incidents/${incId}/resolve`, { method: 'POST' });
    if (res.ok) {
      showToast('Incident marked resolved.', 'success');
      fetchIncidents();
      fetchOverview();
    }
  } catch (err) {
    showToast('Failed to resolve incident.', 'error');
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
    fetchAlertRoutes();
  } catch (err) {
    console.error('Error fetching settings:', err);
  }
}

// ── Theme Management ─────────────────────────────────────────────────────────

function initTheme() {
  const saved = localStorage.getItem('opspilot_theme') || 'dark';
  applyTheme(saved);
}

function applyTheme(theme) {
  document.documentElement.setAttribute('data-theme', theme);
  localStorage.setItem('opspilot_theme', theme);
  const icon = document.getElementById('themeIcon');
  if (icon) {
    icon.textContent = theme === 'light' ? '🌙' : '☀️';
  }
}

function toggleTheme() {
  const current = document.documentElement.getAttribute('data-theme') || 'dark';
  const next = current === 'dark' ? 'light' : 'dark';
  applyTheme(next);
  showToast(`Switched to ${next === 'light' ? 'Light' : 'Dark'} theme`, 'info');
}

// ── Multi-Channel Alert Routing Matrix ───────────────────────────────────────

window.alertRoutesCache = [];

async function fetchAlertRoutes() {
  try {
    const res = await apiFetch('/api/alert-routes');
    if (!res.ok) return;
    const routes = await res.json();
    window.alertRoutesCache = routes;

    const container = document.getElementById('alertRoutesContainer');
    if (!container) return;

    if (routes.length === 0) {
      container.innerHTML = `
        <div style="text-align: center; color: var(--text-muted); padding: 30px; grid-column: 1/-1;">
          No custom alert routes configured yet. Click "+ Add Alert Route" to direct alerts to specific Telegram groups.
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
        <div class="route-card">
          <div>
            <div class="route-header">
              <span class="route-label">${escapeHtml(r.label)}</span>
              <span class="badge ${r.enabled ? 'badge-running' : 'badge-snoozed'}">
                ${r.enabled ? 'Active' : 'Paused'}
              </span>
            </div>
            <div class="route-chatid">💬 ${escapeHtml(r.chat_id)}</div>
            <div class="route-tags">
              ${catBadges}
              ${evBadges}
            </div>
          </div>

          <div style="display: flex; justify-content: space-between; align-items: center; margin-top: 10px; border-top: 1px solid var(--border-subtle); padding-top: 10px;">
            <button class="btn btn-secondary btn-sm" onclick="testAlertRoute('${escapeHtml(r.chat_id)}')">
              🧪 Test Alert
            </button>
            <div style="display: flex; gap: 6px;">
              <button class="btn btn-secondary btn-sm" onclick="openEditRouteModal(${r.id})">✏️ Edit</button>
              <button class="btn btn-danger btn-sm" onclick="deleteAlertRoute(${r.id})">🗑</button>
            </div>
          </div>
        </div>
      `;
    }).join('');
  } catch (err) {
    console.error('Error fetching alert routes:', err);
  }
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
    const data = await res.json();
    if (res.ok && data.success) {
      showToast('Alert route deleted.', 'success');
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
