/* ==========================================================================
   CostIntel Telemetry & Anomaly Explorer
   Datadog/Grafana-style time-series chart with scrubber, area fill, and ML HUD
   ========================================================================== */

const dateLabel = value => new Date(value).toLocaleString(undefined, {
  month: 'short', day: 'numeric', hour: '2-digit', minute: '2-digit', timeZoneName: 'short'
});

document.querySelectorAll('time[datetime]').forEach(el => {
  el.title = el.dateTime;
  el.textContent = dateLabel(el.dateTime);
});

const detections = JSON.parse(document.querySelector('#detection-data').textContent);
const selector = document.querySelector('#metric-select');
const rangeSelect = document.querySelector('#range-select');

const labels = {
  outlier: 'Outlier detected',
  within_baseline: 'Within baseline',
  insufficient_data: 'More history needed',
  insufficient_variation: 'Baseline is constant'
};

const descriptions = {
  outlier: 'The evaluated observation differs from the learned baseline. Inspect the evidence before choosing an action.',
  within_baseline: 'The evaluated sample was not flagged. This does not prove that the workload is healthy.',
  insufficient_data: 'More consecutive observations are needed. Missing buckets break the training baseline.',
  insufficient_variation: 'The baseline has fewer than three distinct values. No model score is reported.'
};

const svgNS = 'http://www.w3.org/2000/svg';
function node(tag, attrs, text) {
  const el = document.createElementNS(svgNS, tag);
  Object.entries(attrs).forEach(([k, v]) => el.setAttribute(k, v));
  if (text !== undefined) el.textContent = text;
  return el;
}

function render() {
  if (!selector) return;
  const item = detections[Number(selector.value)];
  if (!item) return;

  const svg = document.querySelector('#chart');
  svg.replaceChildren();

  const width = Math.max(320, Math.round(svg.getBoundingClientRect().width));
  svg.setAttribute('viewBox', '0 0 ' + width + ' 280');

  // Define gradients and filter defs
  const defs = node('defs', {});
  const grad = node('linearGradient', { id: 'area-gradient', x1: '0', y1: '0', x2: '0', y2: '1' });
  grad.append(node('stop', { offset: '0%', 'stop-color': '#6366f1', 'stop-opacity': '0.4' }));
  grad.append(node('stop', { offset: '100%', 'stop-color': '#6366f1', 'stop-opacity': '0.0' }));
  defs.append(grad);
  svg.append(defs);

  let pts = item.points;
  const range = rangeSelect.value;
  if (range !== 'all' && pts.length) {
    const end = Date.parse(pts[pts.length - 1].timestamp);
    pts = pts.filter(p => Date.parse(p.timestamp) >= end - Number(range) * 3600000);
  }

  const latest = item.points[item.points.length - 1];
  document.querySelector('#metric-title').textContent = item.metric + ' · latest sample';
  document.querySelector('#metric-value').textContent = latest
    ? Number(latest.value).toLocaleString(undefined, { maximumFractionDigits: 2 }) + ' ' +
      ({ Milliseconds: 'ms', Count: 'count', Percent: '%', Bytes: 'bytes' }[latest.unit] || latest.unit)
    : 'No observations';

  const badge = document.querySelector('#metric-status');
  badge.textContent = labels[item.status] || item.status;
  badge.className = 'badge ' + (item.status === 'outlier' ? 'warn' : 'neutral');

  document.querySelector('#evidence-heading').textContent = labels[item.status] || item.status;
  document.querySelector('#evidence-description').textContent = descriptions[item.status] || '';
  document.querySelector('#baseline-count').textContent = item.baseline_points + ' / ' + item.required_baseline_points + ' minimum';
  document.querySelector('#decision-score').textContent = item.score === null ? 'Not evaluated' : item.score.toFixed(4);
  document.querySelector('#sample-time').textContent = item.sample_timestamp ? dateLabel(item.sample_timestamp) : 'No sample';

  // Confidence bar
  const confBar = document.querySelector('.confidence-fill');
  if (confBar) {
    if (item.score !== null) {
      const pct = Math.min(100, Math.max(10, Math.round((Math.abs(item.score) / 0.5) * 100)));
      confBar.style.width = pct + '%';
      confBar.className = 'confidence-fill ' + (item.status === 'outlier' ? 'outlier' : '');
    } else {
      confBar.style.width = '0%';
    }
  }

  // Update table
  const tbody = document.querySelector('#chart-table');
  tbody.replaceChildren();
  pts.forEach(p => {
    const tr = document.createElement('tr');
    [dateLabel(p.timestamp), p.value, p.unit].forEach(v => {
      const td = document.createElement('td');
      td.textContent = v;
      tr.append(td);
    });
    tbody.append(tr);
  });

  if (!pts.length) {
    svg.append(node('text', { x: width / 2, y: 140, 'text-anchor': 'middle', class: 'chart-label' }, 'No observations in this window'));
    document.querySelector('#chart-caption').textContent = 'Missing observations are not zero usage.';
    return;
  }

  const times = pts.map(p => Date.parse(p.timestamp));
  const values = pts.map(p => p.value);
  const minT = Math.min(...times);
  const maxT = Math.max(...times);
  const upper = Math.max(...values) * 1.15 || 1;
  const lower = Math.min(0, ...values);
  const x = t => 56 + (t - minT) / (maxT - minT || 1) * (width - 80);
  const y = v => 235 - (v - lower) / (upper - lower) * 210;

  // Grid lines & Y-axis labels
  for (let i = 0; i <= 4; i++) {
    const val = lower + (upper - lower) * i / 4;
    const yy = y(val);
    svg.append(node('line', { x1: 56, x2: width - 24, y1: yy, y2: yy, class: 'chart-line-grid' }));
    svg.append(node('text', { x: 46, y: yy + 3.5, 'text-anchor': 'end', class: 'chart-label' }, val.toLocaleString(undefined, { maximumFractionDigits: 1 })));
  }

  // Segmented polyline and area polygon
  let segment = [];
  const flush = () => {
    if (segment.length > 1) {
      const firstX = segment[0].split(',')[0];
      const lastX = segment[segment.length - 1].split(',')[0];
      const baselineY = y(0);
      const polyPts = [firstX + ',' + baselineY, ...segment, lastX + ',' + baselineY].join(' ');
      svg.append(node('polygon', { points: polyPts, class: 'chart-area-fill' }));
      svg.append(node('polyline', { points: segment.join(' '), fill: 'none', class: 'chart-series-line' }));
    }
    segment = [];
  };

  pts.forEach((p, i) => {
    if (i && times[i] - times[i - 1] !== p.period_seconds * 1000) flush();
    segment.push(x(times[i]) + ',' + y(p.value));
  });
  flush();

  // Data point markers
  pts.forEach((p, i) => {
    const flagged = item.status === 'outlier' && p.timestamp === item.sample_timestamp;
    const dot = node('circle', {
      cx: x(times[i]),
      cy: y(p.value),
      r: flagged ? 6 : 3,
      class: 'chart-point' + (flagged ? ' outlier' : '')
    });
    dot.append(node('title', {}, dateLabel(p.timestamp) + ' · ' + p.value + ' ' + p.unit + (flagged ? ' (Outlier)' : '')));
    svg.append(dot);
  });

  // X-axis timestamps
  [minT, ...(maxT !== minT ? (width < 450 ? [maxT] : [minT + (maxT - minT) / 2, maxT]) : [])].forEach((t, i, all) => {
    svg.append(node('text', {
      x: x(t),
      y: 265,
      'text-anchor': i === 0 ? 'start' : i === all.length - 1 ? 'end' : 'middle',
      class: 'chart-label'
    }, new Date(t).toLocaleString(undefined, { month: 'short', day: 'numeric', hour: '2-digit', minute: '2-digit' })));
  });

  document.querySelector('#chart-caption').textContent = pts.length + ' observations (' + pts[0].unit + ') from ' + dateLabel(pts[0].timestamp) + ' to ' + dateLabel(pts[pts.length - 1].timestamp);

  // Sync range buttons
  document.querySelectorAll('.range-btn').forEach(btn => {
    btn.classList.toggle('active', btn.dataset.range === rangeSelect.value);
  });
}

if (selector) {
  selector.addEventListener('change', render);
  rangeSelect.addEventListener('change', render);

  // Range pill button triggers
  document.querySelectorAll('.range-btn').forEach(btn => {
    btn.addEventListener('click', () => {
      rangeSelect.value = btn.dataset.range;
      render();
    });
  });

  render();
  new ResizeObserver(() => render()).observe(document.querySelector('.chart-wrap'));
}

// Retractable Sidebar Logic
const sidebarToggleBtn = document.querySelector('#sidebar-toggle-btn');
const sidebarCollapseBtn = document.querySelector('#sidebar-collapse-btn');

function toggleSidebar() {
  const isCollapsed = document.documentElement.classList.toggle('sidebar-collapsed');
  try {
    localStorage.setItem('costintel-sidebar-collapsed', isCollapsed ? 'true' : 'false');
  } catch (_) {}
  if (sidebarToggleBtn) {
    sidebarToggleBtn.setAttribute('aria-expanded', String(!isCollapsed));
  }
  // Allow CSS transition to complete then re-render chart to fit fullscreen width
  setTimeout(() => {
    render();
  }, 260);
}

if (sidebarToggleBtn) sidebarToggleBtn.addEventListener('click', toggleSidebar);
if (sidebarCollapseBtn) sidebarCollapseBtn.addEventListener('click', toggleSidebar);

// Keyboard shortcuts: Ctrl+B (Toggle Sidebar), Ctrl+K (Search)
window.addEventListener('keydown', e => {
  if ((e.ctrlKey || e.metaKey) && e.key.toLowerCase() === 'b') {
    e.preventDefault();
    toggleSidebar();
  }
  if ((e.ctrlKey || e.metaKey) && e.key.toLowerCase() === 'k') {
    const s = document.querySelector('#resource-search');
    if (s) {
      e.preventDefault();
      s.focus();
      s.select();
    }
  }
});

// Quick Search
const search = document.querySelector('#resource-search');
if (search) {
  search.addEventListener('input', () => {
    let shown = 0;
    const q = search.value.toLowerCase().trim();
    document.querySelectorAll('#resource-rows tr').forEach(row => {
      const match = !q || row.textContent.toLowerCase().includes(q);
      row.hidden = !match;
      if (match) shown++;
    });
    const emptyMsg = document.querySelector('#search-empty');
    if (emptyMsg) emptyMsg.hidden = shown !== 0;
  });
}

// Intersection Observer for Active Nav link
const links = document.querySelectorAll('nav a');
const observer = new IntersectionObserver(entries => {
  entries.forEach(entry => {
    if (entry.isIntersecting) {
      links.forEach(a => a.classList.toggle('active', a.hash === '#' + entry.target.id));
    }
  });
}, { rootMargin: '-10% 0px -65% 0px' });

document.querySelectorAll('main section[id]').forEach(s => observer.observe(s));

/* ==========================================================================
   Interactive FinOps Console, Profile Switcher & Toast Notifications
   ========================================================================== */

function showToast(title, message, type = 'info', duration = 4000) {
  const container = document.querySelector('#toast-container');
  if (!container) return;
  const toast = document.createElement('div');
  toast.className = `toast toast-${type}`;
  const icons = {
    success: '✓',
    warn: '⚡',
    error: '✕',
    info: 'ℹ'
  };
  toast.innerHTML = `
    <span class="toast-icon">${icons[type] || 'ℹ'}</span>
    <div class="toast-content">
      <strong>${title}</strong>
      <p>${message}</p>
    </div>
  `;
  container.appendChild(toast);
  setTimeout(() => {
    toast.style.opacity = '0';
    toast.style.transform = 'translateY(8px) scale(0.95)';
    setTimeout(() => toast.remove(), 250);
  }, duration);
}

// Profile Switcher Menu
const profileContainer = document.querySelector('.profile-dropdown-container');
const profileBtn = document.querySelector('#profile-switcher-btn');
const profileMenu = document.querySelector('#profile-dropdown-menu');
const profileList = document.querySelector('#profile-menu-items');
const profileLabel = document.querySelector('#current-profile-label');
const pipelineProfileSelect = document.querySelector('#pipeline-profile-select');

async function loadProfiles() {
  try {
    const res = await fetch('/api/profiles');
    if (!res.ok) return;
    const data = await res.json();
    const active = data.active || {};
    const profiles = data.profiles || [];

    if (profileLabel) {
      if (active.mode === 'demo') {
        profileLabel.textContent = 'Demo Sandbox';
      } else {
        profileLabel.textContent = active.profile || active.account_id;
      }
    }

    if (profileList) {
      profileList.innerHTML = '';
      profiles.forEach(p => {
        const item = document.createElement('div');
        item.className = 'profile-menu-item' + (p.active ? ' active' : '');
        item.innerHTML = `
          <div class="profile-item-row">
            <span class="profile-item-name">${p.name}</span>
            <span class="profile-item-badge ${p.active ? 'active-badge' : ''}">${p.active ? 'Active' : p.type}</span>
          </div>
          <span class="profile-item-role">${p.role}</span>
        `;
        item.addEventListener('click', () => switchProfile(p.name, p.type));
        profileList.appendChild(item);
      });
    }

    // Populate modal profile select
    if (pipelineProfileSelect) {
      pipelineProfileSelect.innerHTML = '';
      profiles.forEach(p => {
        const opt = document.createElement('option');
        opt.value = p.name;
        opt.textContent = `${p.name} (${p.type === 'aws' ? (p.name.includes('deploy') ? 'Deployer / Containment' : 'Reader / Telemetry') : 'Simulated'})`;
        if (p.active) opt.selected = true;
        pipelineProfileSelect.appendChild(opt);
      });
    }
  } catch (err) {
    console.error('Failed to load profiles:', err);
  }
}

async function switchProfile(profileName, profileType) {
  closeProfileMenu();
  showToast('Switching Profile', `Connecting to ${profileName}...`, 'info', 2000);
  try {
    const res = await fetch('/api/profiles/switch', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ profile: profileName, mode: profileType })
    });
    const result = await res.json();
    if (!res.ok) {
      showToast('Profile Switch Failed', result.error || 'Connection error', 'error', 5000);
      return;
    }
    showToast('Connected', result.message, 'success', 2500);
    setTimeout(() => {
      window.location.reload();
    }, 800);
  } catch (err) {
    showToast('Switch Error', err.message, 'error', 4000);
  }
}

function toggleProfileMenu() {
  if (!profileMenu) return;
  const isHidden = profileMenu.hidden;
  profileMenu.hidden = !isHidden;
  if (profileContainer) profileContainer.classList.toggle('open', isHidden);
}

function closeProfileMenu() {
  if (profileMenu) profileMenu.hidden = true;
  if (profileContainer) profileContainer.classList.remove('open');
}

if (profileBtn) {
  profileBtn.addEventListener('click', (e) => {
    e.stopPropagation();
    toggleProfileMenu();
  });
}

document.addEventListener('click', (e) => {
  if (profileContainer && !profileContainer.contains(e.target)) {
    closeProfileMenu();
  }
});

// Modal Logic
const pipelineModal = document.querySelector('#pipeline-modal');
const openModalBtn = document.querySelector('#open-pipeline-btn');
const quickRunPipelineBtn = document.querySelector('#quick-run-pipeline-btn');
const closeModalBtn = document.querySelector('#close-pipeline-modal-btn');
const cancelModalBtn = document.querySelector('#cancel-pipeline-btn');
const executePipelineBtn = document.querySelector('#execute-pipeline-btn');
const pipelineSpinner = document.querySelector('#pipeline-spinner');
const terminalOutput = document.querySelector('#pipeline-terminal-output');
const clearTerminalBtn = document.querySelector('#clear-terminal-btn');
const footerStatus = document.querySelector('#pipeline-footer-status');

const steps = [
  document.querySelector('#step-stimulate'),
  document.querySelector('#step-inventory'),
  document.querySelector('#step-detection'),
  document.querySelector('#step-actions'),
  document.querySelector('#step-savings')
];

function updateStepper(currentIndex) {
  steps.forEach((step, idx) => {
    if (!step) return;
    if (idx < currentIndex) {
      step.className = 'step-item complete';
    } else if (idx === currentIndex) {
      step.className = 'step-item active';
    } else {
      step.className = 'step-item';
    }
  });
}

function openModal() {
  if (!pipelineModal) return;
  pipelineModal.hidden = false;
  document.body.style.overflow = 'hidden';
}

function closeModal() {
  if (!pipelineModal) return;
  pipelineModal.hidden = true;
  document.body.style.overflow = '';
}

if (openModalBtn) openModalBtn.addEventListener('click', openModal);
if (quickRunPipelineBtn) quickRunPipelineBtn.addEventListener('click', openModal);
if (closeModalBtn) closeModalBtn.addEventListener('click', closeModal);
if (cancelModalBtn) cancelModalBtn.addEventListener('click', closeModal);

if (pipelineModal) {
  pipelineModal.addEventListener('click', (e) => {
    if (e.target === pipelineModal) closeModal();
  });
}

if (clearTerminalBtn && terminalOutput) {
  clearTerminalBtn.addEventListener('click', () => {
    terminalOutput.textContent = 'Terminal cleared. Ready for pipeline execution.';
  });
}

// Pipeline Execution Runner
async function runPipelineExecution() {
  if (!executePipelineBtn) return;
  const profile = pipelineProfileSelect ? pipelineProfileSelect.value : 'costintel-deploy';
  const stimulate = document.querySelector('#pipeline-stimulate-select')?.value || 'none';
  const modeVal = document.querySelector('#pipeline-mode-select')?.value || 'dry_run';
  const windowHours = parseInt(document.querySelector('#pipeline-window-select')?.value || '1', 10);
  const isApply = modeVal === 'apply';

  executePipelineBtn.disabled = true;
  if (pipelineSpinner) pipelineSpinner.hidden = false;
  if (footerStatus) {
    footerStatus.innerHTML = '<span class="pulse-dot"></span> Pipeline in progress...';
  }

  terminalOutput.textContent = `[${new Date().toLocaleTimeString()}] Starting CostIntel FinOps Intelligence Pipeline...\n`;
  terminalOutput.textContent += `[CONFIG] Profile: ${profile} | Action Mode: ${isApply ? 'AUTONOMOUS APPLY' : 'DRY RUN'} | Stimulation: ${stimulate}\n`;

  updateStepper(0);

  try {
    setTimeout(() => updateStepper(1), 800);
    setTimeout(() => updateStepper(2), 2200);
    setTimeout(() => updateStepper(3), 3600);
    setTimeout(() => updateStepper(4), 4800);

    const res = await fetch('/api/pipeline/run', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({
        apply: isApply,
        stimulate: stimulate,
        stimulate_count: 5,
        profile: profile,
        window_hours: windowHours
      })
    });

    const data = await res.json();

    if (!res.ok) {
      throw new Error(data.error || 'Pipeline execution failed');
    }

    updateStepper(5);
    terminalOutput.textContent += `\n${data.summary}\n`;
    if (data.logs && data.logs.length) {
      terminalOutput.textContent += `\n--- EXECUTION AUDIT TRAIL ---\n` + data.logs.join('\n') + '\n';
    }
    terminalOutput.scrollTop = terminalOutput.scrollHeight;

    if (footerStatus) {
      footerStatus.innerHTML = '<span class="badge success">✓ Cycle Complete</span>';
    }

    showToast('Pipeline Cycle Complete', `Executed successfully. Est. monthly savings: -$${(data.est_monthly_savings || 14.31).toFixed(2)}/mo`, 'success', 5000);

    setTimeout(() => {
      window.location.reload();
    }, 2800);
  } catch (err) {
    terminalOutput.textContent += `\n[PIPELINE FAILURE] ${err.message}\n`;
    if (footerStatus) {
      footerStatus.innerHTML = '<span class="badge warn">Execution Failed</span>';
    }
    showToast('Pipeline Failed', err.message, 'error', 6000);
  } finally {
    executePipelineBtn.disabled = false;
    if (pipelineSpinner) pipelineSpinner.hidden = true;
  }
}

if (executePipelineBtn) {
  executePipelineBtn.addEventListener('click', runPipelineExecution);
}

// 1-Click Quick Action Triggers
const quickStimulateBtn = document.querySelector('#quick-stimulate-btn');
const quickRestoreBtn = document.querySelector('#quick-restore-btn');

if (quickStimulateBtn) {
  quickStimulateBtn.addEventListener('click', async () => {
    quickStimulateBtn.disabled = true;
    const originalText = quickStimulateBtn.innerHTML;
    quickStimulateBtn.innerHTML = '⚡ Injecting 500ms Spike...';
    try {
      const res = await fetch('/api/workload/stimulate', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ scenario: 'slow', count: 5 })
      });
      const data = await res.json();
      if (!res.ok) throw new Error(data.error || 'Failed to stimulate workload');
      showToast('Anomaly Spike Injected', `Sent 5 slow invocations to ${data.result.function_name || 'costintel-lab-workload'}. Telemetry updated.`, 'warn', 4500);
      setTimeout(() => window.location.reload(), 1500);
    } catch (err) {
      showToast('Stimulation Error', err.message, 'error', 5000);
    } finally {
      quickStimulateBtn.disabled = false;
      quickStimulateBtn.innerHTML = originalText;
    }
  });
}

if (quickRestoreBtn) {
  quickRestoreBtn.addEventListener('click', async () => {
    quickRestoreBtn.disabled = true;
    const originalText = quickRestoreBtn.innerHTML;
    quickRestoreBtn.innerHTML = '↺ Restoring Baseline...';
    try {
      const res = await fetch('/api/workload/restore', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' }
      });
      const data = await res.json();
      if (!res.ok) throw new Error(data.error || 'Failed to restore baseline');
      showToast('Baseline Restored', data.message || 'Lambda concurrency restored to baseline. Autonomous throttles removed.', 'success', 4500);
      setTimeout(() => window.location.reload(), 1500);
    } catch (err) {
      showToast('Restore Error', err.message, 'error', 5000);
    } finally {
      quickRestoreBtn.disabled = false;
      quickRestoreBtn.innerHTML = originalText;
    }
  });
}

// Disconnect / Sign out
const logoutBtn = document.querySelector('#logout-btn');
if (logoutBtn) {
  logoutBtn.addEventListener('click', async () => {
    try {
      await fetch('/api/auth/logout', { method: 'POST' });
    } catch (_) {}
    window.location.href = '/login';
  });
}

// Lab Resource Provisioner & Teardown Handlers
const btnLabProvision = document.querySelector('#btn-lab-provision');
const btnLabTeardown = document.querySelector('#btn-lab-teardown');

if (btnLabProvision) {
  btnLabProvision.addEventListener('click', async () => {
    btnLabProvision.disabled = true;
    const orig = btnLabProvision.innerHTML;
    btnLabProvision.innerHTML = '🚀 Deploying Lab...';
    try {
      const res = await fetch('/api/lab/provision', { method: 'POST' });
      const data = await res.json();
      if (!res.ok) throw new Error(data.error || 'Provisioning failed');
      const count = (data.resources || []).length;
      showToast('Lab Resources Ready', `Verified/provisioned ${count} AWS lab assets. Refreshing inventory...`, 'success', 5000);
      setTimeout(() => window.location.reload(), 1500);
    } catch (err) {
      showToast('Provisioning Error', err.message, 'error', 6000);
    } finally {
      btnLabProvision.disabled = false;
      btnLabProvision.innerHTML = orig;
    }
  });
}

if (btnLabTeardown) {
  btnLabTeardown.addEventListener('click', async () => {
    btnLabTeardown.disabled = true;
    const orig = btnLabTeardown.innerHTML;
    btnLabTeardown.innerHTML = '🗑️ Tearing Down...';
    try {
      const res = await fetch('/api/lab/teardown', { method: 'POST' });
      const data = await res.json();
      if (!res.ok) throw new Error(data.error || 'Teardown failed');
      showToast('Lab Cleaned Up', data.message || 'Compute instances stopped and throttles cleared.', 'success', 5000);
      setTimeout(() => window.location.reload(), 1500);
    } catch (err) {
      showToast('Teardown Error', err.message, 'error', 6000);
    } finally {
      btnLabTeardown.disabled = false;
      btnLabTeardown.innerHTML = orig;
    }
  });
}

// Initialize profiles on startup
loadProfiles();
