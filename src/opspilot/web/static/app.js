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
      list.innerHTML = '<div style="text-align: center; color: var(--text-muted); padding: 40px;">No HTTP probes configured yet. Click "+ Add HTTP Probe" to start monitoring.</div>';
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

      return `
        <div class="glass-panel probe-item">
          <div class="probe-main">
            <span class="status-dot ${isHealthy ? 'green' : 'red'}"></span>
            <div class="probe-url-col">
              <div class="probe-name">${escapeHtml(p.name)}</div>
              <a href="${escapeHtml(p.url)}" target="_blank" rel="noopener noreferrer" class="probe-url">${escapeHtml(p.url)}</a>
            </div>
          </div>

          <div class="probe-stats">
            <span class="latency-pill ${latencyClass}">⚡ ${p.latency_ms || 0}ms</span>
            ${statusBadge}
            <button class="btn btn-secondary btn-sm" onclick="toggleProbe(${p.id}, ${p.enabled === 1 ? 'false' : 'true'})">
              ${p.enabled === 1 ? 'Disable' : 'Enable'}
            </button>
            <button class="btn btn-danger btn-sm" onclick="deleteProbe(${p.id})">🗑</button>
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
    document.getElementById('navRenewalsCount').textContent = renewals.filter(r => r.status === 'pending').length;

    const grid = document.getElementById('renewalsGridContainer');
    if (renewals.length === 0) {
      grid.innerHTML = '<div style="grid-column: 1/-1; text-align: center; color: var(--text-muted); padding: 40px;">No upcoming renewals or client billing items registered.</div>';
      return;
    }

    grid.innerHTML = renewals.map(r => {
      const isPaid = r.status === 'paid';
      const isCancelled = r.status === 'cancelled';
      let dueBadge = `<span class="renewal-due-badge due-later">Due: ${escapeHtml(r.due_date)}</span>`;

      // Check if due soon (within 7 days)
      const dueDate = new Date(r.due_date);
      const diffDays = Math.ceil((dueDate - new Date()) / (1000 * 60 * 60 * 24));
      if (!isPaid && !isCancelled && diffDays <= 7) {
        dueBadge = `<span class="renewal-due-badge due-soon">⚠️ Due in ${diffDays} day${diffDays === 1 ? '' : 's'}</span>`;
      } else if (isPaid) {
        dueBadge = `<span class="badge badge-running">✅ Paid</span>`;
      }

      return `
        <div class="glass-panel renewal-card" style="${isPaid ? 'opacity: 0.6;' : ''}">
          <div>
            <div style="display: flex; justify-content: space-between; align-items: flex-start; margin-bottom: 12px;">
              <div>
                <span class="badge badge-snoozed" style="font-size: 10px; text-transform: uppercase;">${escapeHtml(r.category || 'other')}</span>
                <h3 style="font-size: 17px; margin-top: 6px;">${escapeHtml(r.name)}</h3>
              </div>
              ${dueBadge}
            </div>
            
            <div style="margin: 14px 0;">
              <div class="renewal-amount">₹${Number(r.amount || 0).toLocaleString('en-IN')} <span style="font-size: 14px; font-weight: normal; color: var(--text-muted);">${r.recurrence}</span></div>
              ${r.notes ? `<p style="font-size: 12px; color: var(--text-muted); margin-top: 4px;">${escapeHtml(r.notes)}</p>` : ''}
            </div>
          </div>

          <div class="card-actions" style="justify-content: flex-end;">
            ${!isPaid ? `
              <button class="btn btn-success btn-sm" onclick="markRenewalPaid(${r.id})">✅ Mark Paid</button>
              <button class="btn btn-secondary btn-sm" onclick="snoozeRenewal(${r.id})">⏰ Snooze 7d</button>
            ` : ''}
            <button class="btn btn-secondary btn-sm" onclick="deleteRenewal(${r.id})">🗑 Delete</button>
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

async function fetchIncidents() {
  try {
    const res = await apiFetch('/api/incidents');
    if (!res.ok) return;
    const incidents = await res.json();
    const tbody = document.getElementById('incidentsTableBody');

    if (incidents.length === 0) {
      tbody.innerHTML = '<tr><td colspan="7" style="text-align: center; color: var(--text-muted); padding: 40px;">No incidents recorded in audit log.</td></tr>';
      return;
    }

    tbody.innerHTML = incidents.map(inc => {
      const isResolved = !!inc.resolved_at;
      const statusBadge = isResolved
        ? '<span class="badge badge-running">Resolved</span>'
        : '<span class="badge badge-exited">Active</span>';

      return `
        <tr>
          <td><strong>${escapeHtml(inc.target)}</strong></td>
          <td><span class="badge badge-snoozed">${escapeHtml(inc.source)}</span></td>
          <td><span class="badge ${inc.severity === 'critical' ? 'badge-exited' : 'badge-unhealthy'}">${escapeHtml(inc.severity)}</span></td>
          <td>
            <div>${escapeHtml(inc.title)}</div>
            <div style="font-size: 12px; color: var(--text-muted);">${escapeHtml(inc.detail || '')}</div>
          </td>
          <td>${inc.alert_count}</td>
          <td>${statusBadge}</td>
          <td>
            ${!isResolved ? `<button class="btn btn-secondary btn-sm" onclick="resolveIncident(${inc.id})">Resolve</button>` : '-'}
          </td>
        </tr>
      `;
    }).join('');
  } catch (err) {
    console.error('Error fetching incidents:', err);
  }
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
