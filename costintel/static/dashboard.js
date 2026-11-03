/* ==========================================================================
   CostIntel Autonomous FinOps Console
   Telemetry Explorer, Retractable Layout, Guarded Execution, & Verified Rollback
   ========================================================================== */

const dateLabel = value => {
  if (!value) return '—';
  return new Date(value).toLocaleString(undefined, {
    month: 'short',
    day: 'numeric',
    hour: '2-digit',
    minute: '2-digit',
    timeZoneName: 'short'
  });
};

document.querySelectorAll('time[datetime]').forEach(el => {
  if (el.dateTime) {
    el.title = el.dateTime;
    el.textContent = dateLabel(el.dateTime);
  }
});

const detectionsDataEl = document.querySelector('#detection-data');
const detections = detectionsDataEl ? JSON.parse(detectionsDataEl.textContent || '[]') : [];
const selector = document.querySelector('#metric-select');
const rangeSelect = document.querySelector('#range-select');

const labels = {
  outlier: 'Outlier detected',
  within_baseline: 'Within baseline',
  insufficient_data: 'More history needed',
  insufficient_variation: 'Baseline is constant'
};

const descriptions = {
  outlier: 'The evaluated observation differs significantly from the learned baseline. Inspect the evidence before choosing an action.',
  within_baseline: 'The evaluated sample is within learned baseline tolerances. Workload runtime bounds are stable.',
  insufficient_data: 'More consecutive observations are required to formulate a robust isolation forest baseline.',
  insufficient_variation: 'Baseline variance is zero or constant. Model requires dynamic variation before calculating score.'
};

const svgNS = 'http://www.w3.org/2000/svg';
function node(tag, attrs, text) {
  const el = document.createElementNS(svgNS, tag);
  Object.entries(attrs).forEach(([k, v]) => el.setAttribute(k, v));
  if (text !== undefined) el.textContent = text;
  return el;
}

/* ==========================================================================
   Interactive Time-Series SVG Chart
   ========================================================================== */

function render() {
  if (!selector || !detections.length) return;
  const item = detections[Number(selector.value)];
  if (!item) return;

  const svg = document.querySelector('#chart');
  if (!svg) return;
  svg.replaceChildren();

  const width = Math.max(320, Math.round(svg.getBoundingClientRect().width) || 720);
  svg.setAttribute('viewBox', '0 0 ' + width + ' 250');

  // SVG Area Fill Gradient
  const defs = node('defs', {});
  const grad = node('linearGradient', { id: 'area-gradient', x1: '0', y1: '0', x2: '0', y2: '1' });
  grad.append(node('stop', { offset: '0%', 'stop-color': 'var(--accent)', 'stop-opacity': '0.35' }));
  grad.append(node('stop', { offset: '100%', 'stop-color': 'var(--accent)', 'stop-opacity': '0.0' }));
  defs.append(grad);
  svg.append(defs);

  let pts = item.points || [];
  const range = rangeSelect ? rangeSelect.value : 'all';
  if (range !== 'all' && pts.length) {
    const end = Date.parse(pts[pts.length - 1].timestamp);
    pts = pts.filter(p => Date.parse(p.timestamp) >= end - Number(range) * 3600000);
  }

  const latest = item.points && item.points.length ? item.points[item.points.length - 1] : null;
  const metricTitle = document.querySelector('#metric-title');
  const metricVal = document.querySelector('#metric-value');
  const badge = document.querySelector('#metric-status');

  if (metricTitle) metricTitle.textContent = item.metric + ' · latest sample';
  if (metricVal) {
    metricVal.textContent = latest
      ? Number(latest.value).toLocaleString(undefined, { maximumFractionDigits: 2 }) + ' ' +
        ({ Milliseconds: 'ms', Count: 'reqs', Percent: '%', Bytes: 'B' }[latest.unit] || latest.unit)
      : 'No observations';
  }

  if (badge) {
    badge.textContent = labels[item.status] || item.status;
    badge.className = 'status-badge ' + (item.status === 'outlier' ? 'warn' : 'good');
  }

  const evHeading = document.querySelector('#evidence-heading');
  const evDesc = document.querySelector('#evidence-description');
  const baseCount = document.querySelector('#baseline-count');
  const decScore = document.querySelector('#decision-score');
  const sampTime = document.querySelector('#sample-time');

  if (evHeading) evHeading.textContent = labels[item.status] || item.status;
  if (evDesc) evDesc.textContent = latest ? (descriptions[item.status] || '')
    : 'CloudWatch returned no datapoints for this metric. Some metrics publish only after resource activity; missing data is not zero usage.';
  if (baseCount) baseCount.textContent = (item.baseline_points || 0) + ' / ' + (item.required_baseline_points || 6) + ' points';
  if (decScore) decScore.textContent = item.score === null ? 'Not evaluated' : item.score.toFixed(4);
  if (sampTime) sampTime.textContent = item.sample_timestamp ? dateLabel(item.sample_timestamp) : 'No sample';

  // Table population
  const tbody = document.querySelector('#chart-table');
  if (tbody) {
    tbody.replaceChildren();
    pts.forEach(p => {
      const tr = document.createElement('tr');
      [dateLabel(p.timestamp), Number(p.value).toFixed(2), p.unit].forEach(v => {
        const td = document.createElement('td');
        td.textContent = v;
        tr.append(td);
      });
      tbody.append(tr);
    });
  }

  if (!pts.length) {
    svg.append(node('text', { x: width / 2, y: 125, 'text-anchor': 'middle', class: 'chart-label' }, 'No CloudWatch datapoints in this window'));
    const cap = document.querySelector('#chart-caption');
    if (cap) cap.textContent = 'Missing observations are not treated as zero usage.';
    return;
  }

  const times = pts.map(p => Date.parse(p.timestamp));
  const values = pts.map(p => p.value);
  const minT = Math.min(...times);
  const maxT = Math.max(...times);
  const upper = Math.max(...values) * 1.15 || 1;
  const lower = Math.min(0, ...values);
  const x = t => 56 + (t - minT) / (maxT - minT || 1) * (width - 80);
  const y = v => 210 - (v - lower) / (upper - lower) * 180;

  // Horizontal Grid Lines & Y-Axis Labels
  for (let i = 0; i <= 4; i++) {
    const val = lower + (upper - lower) * i / 4;
    const yy = y(val);
    svg.append(node('line', { x1: 56, x2: width - 24, y1: yy, y2: yy, class: 'chart-line-grid' }));
    svg.append(node('text', { x: 46, y: yy + 3.5, 'text-anchor': 'end' }, val.toLocaleString(undefined, { maximumFractionDigits: 1 })));
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
      svg.append(node('polyline', { points: segment.join(' '), class: 'chart-series-line' }));
    }
    segment = [];
  };

  pts.forEach((p, i) => {
    if (i && times[i] - times[i - 1] !== (p.period_seconds || 300) * 1000) flush();
    segment.push(x(times[i]) + ',' + y(p.value));
  });
  flush();

  // Data point markers
  pts.forEach((p, i) => {
    const flagged = item.status === 'outlier' && p.timestamp === item.sample_timestamp;
    const dot = node('circle', {
      cx: x(times[i]),
      cy: y(p.value),
      r: flagged ? 5.5 : 3,
      class: 'chart-point' + (flagged ? ' outlier' : '')
    });
    dot.append(node('title', {}, dateLabel(p.timestamp) + ' · ' + p.value + ' ' + p.unit + (flagged ? ' (Outlier)' : '')));
    svg.append(dot);
  });

  // X-axis timestamps
  [minT, ...(maxT !== minT ? [minT + (maxT - minT) / 2, maxT] : [])].forEach((t, i, all) => {
    svg.append(node('text', {
      x: x(t),
      y: 236,
      'text-anchor': i === 0 ? 'start' : i === all.length - 1 ? 'end' : 'middle'
    }, new Date(t).toLocaleString(undefined, { month: 'short', day: 'numeric', hour: '2-digit', minute: '2-digit' })));
  });

  const cap = document.querySelector('#chart-caption');
  if (cap) {
    cap.textContent = pts.length + ' observations (' + pts[0].unit + ') from ' + dateLabel(pts[0].timestamp) + ' to ' + dateLabel(pts[pts.length - 1].timestamp);
  }
}

if (selector) {
  selector.addEventListener('change', render);
  if (rangeSelect) rangeSelect.addEventListener('change', render);
  render();
  const wrap = document.querySelector('.chart-wrap');
  if (wrap) new ResizeObserver(() => render()).observe(wrap);
}

// CSV Export
const exportBtn = document.querySelector('#export');
if (exportBtn && selector && detections.length) {
  exportBtn.addEventListener('click', () => {
    const item = detections[Number(selector.value)];
    if (!item || !item.points) return;
    const rows = [['timestamp', 'value', 'unit', 'statistic']];
    item.points.forEach(p => rows.push([p.timestamp, p.value, p.unit, p.statistic || 'Average']));
    const csvContent = 'data:text/csv;charset=utf-8,' + rows.map(e => e.join(',')).join('\n');
    const encodedUri = encodeURI(csvContent);
    const link = document.createElement('a');
    link.setAttribute('href', encodedUri);
    link.setAttribute('download', `costintel_${item.metric}_${item.resource_id.split(':').pop()}.csv`);
    document.body.appendChild(link);
    link.click();
    link.remove();
  });
}

/* ==========================================================================
   Retractable Sidebar Logic
   ========================================================================== */

const sidebarToggleBtn = document.querySelector('#sidebar-toggle-btn');
const sidebarCollapseBtn = document.querySelector('#sidebar-collapse-btn');

function toggleSidebar() {
  const isCollapsed = document.documentElement.classList.toggle('sidebar-collapsed');
  try {
    localStorage.setItem('costintel-sidebar-collapsed', isCollapsed ? 'true' : 'false');
  } catch (_) {}
  setTimeout(() => render(), 260);
}

if (sidebarToggleBtn) sidebarToggleBtn.addEventListener('click', toggleSidebar);
if (sidebarCollapseBtn) sidebarCollapseBtn.addEventListener('click', toggleSidebar);

// Keep navigation in sync with the section beneath the fixed top bar.
const sectionLinks = [...document.querySelectorAll('.sidebar nav a[href^="#"]')];
const trackedSections = sectionLinks.map(link => ({link, section: document.getElementById(link.hash.slice(1))})).filter(item => item.section);
const breadcrumbSection = document.querySelector('.breadcrumb strong');
let sectionScrollPending = false;
function updateActiveSection() {
  sectionScrollPending = false;
  if (!trackedSections.length) return;
  const topBar = document.querySelector('.topbar');
  const threshold = (topBar?.getBoundingClientRect().bottom || 0) + 32;
  let current = trackedSections[0];
  for (const item of trackedSections) {
    if (item.section.getBoundingClientRect().top <= threshold) current = item;
  }
  for (const item of trackedSections) {
    const active = item === current;
    item.link.classList.toggle('active', active);
    if (active) item.link.setAttribute('aria-current', 'location');
    else item.link.removeAttribute('aria-current');
  }
  if (breadcrumbSection) breadcrumbSection.textContent = current.link.textContent.replace(/\d+\s*$/, '').trim();
}
function scheduleActiveSection() {
  if (sectionScrollPending) return;
  sectionScrollPending = true;
  requestAnimationFrame(updateActiveSection);
}
sectionLinks.forEach(link => link.addEventListener('click', () => {
  sectionLinks.forEach(other => { other.classList.remove('active'); other.removeAttribute('aria-current'); });
  link.classList.add('active');
  link.setAttribute('aria-current', 'location');
  if (breadcrumbSection) breadcrumbSection.textContent = link.textContent.replace(/\d+\s*$/, '').trim();
  setTimeout(scheduleActiveSection, 350);
}));
window.addEventListener('scroll', scheduleActiveSection, {passive:true});
window.addEventListener('resize', scheduleActiveSection);
window.addEventListener('hashchange', () => setTimeout(scheduleActiveSection, 350));
scheduleActiveSection();

// Global Keyboard Shortcuts (Ctrl+B, Ctrl+K)
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

/* ==========================================================================
   Resource Search & Service Filter
   ========================================================================== */

const resourceSearch = document.querySelector('#resource-search');
const serviceFilter = document.querySelector('#service-filter');

function filterResources() {
  const q = (resourceSearch ? resourceSearch.value : '').toLowerCase().trim();
  const svc = serviceFilter ? serviceFilter.value : 'all';
  let matchCount = 0;

  document.querySelectorAll('#resource-rows tr').forEach(row => {
    const rowSvc = row.dataset.service || '';
    const matchesService = svc === 'all' || rowSvc === svc;
    const matchesQuery = !q || row.textContent.toLowerCase().includes(q);
    const visible = matchesService && matchesQuery;
    row.hidden = !visible;
    if (visible) matchCount++;
  });

  const emptyMsg = document.querySelector('#search-empty');
  if (emptyMsg) emptyMsg.hidden = matchCount > 0;
}

if (resourceSearch) resourceSearch.addEventListener('input', filterResources);
if (serviceFilter) serviceFilter.addEventListener('change', filterResources);

/* ==========================================================================
   Toast Notification System
   ========================================================================== */

function showToast(title, message, type = 'info', duration = 4000) {
  const container = document.querySelector('#toast-container');
  if (!container) return;
  const toast = document.createElement('div');
  toast.className = `toast toast-${type}`;
  const content=document.createElement('div');
  content.append(Object.assign(document.createElement('strong'),{textContent:title}), Object.assign(document.createElement('p'),{textContent:message}));
  toast.append(content);
  container.appendChild(toast);
  setTimeout(() => {
    toast.style.opacity = '0';
    toast.style.transform = 'translateY(8px)';
    toast.style.transition = 'all 0.2s ease';
    setTimeout(() => toast.remove(), 250);
  }, duration);
}

/* ==========================================================================
   Operation Modal (Collect & Preview Pipeline)
   ========================================================================== */

const dialog = document.querySelector('#operation-dialog');
const openDialogBtns = document.querySelectorAll('#collect, [data-collect]');
const closeDialogBtn = document.querySelector('#close-dialog');
const execOpBtn = document.querySelector('#execute-operation-btn');
const reloadViewBtn = document.querySelector('#reload-view');
const opResults = document.querySelector('#operation-results');

function openOperationDialog() {
  if (!dialog) return;
  if (opResults) opResults.replaceChildren();
  document.querySelector('#reviewed-plans')?.replaceChildren();
  if (reloadViewBtn) reloadViewBtn.hidden = true;
  if (execOpBtn) {
    execOpBtn.disabled = false;
    execOpBtn.textContent = 'Run Pipeline Cycle';
  }
  dialog.showModal();
}

function closeOperationDialog() {
  if (dialog) dialog.close();
}

openDialogBtns.forEach(btn => btn.addEventListener('click', openOperationDialog));
if (closeDialogBtn) closeDialogBtn.addEventListener('click', closeOperationDialog);
if (reloadViewBtn) reloadViewBtn.addEventListener('click', () => window.location.reload());

if (execOpBtn) {
  execOpBtn.addEventListener('click', async () => {
    execOpBtn.disabled = true;
    execOpBtn.textContent = 'Running Ingestion & Isolation Forest...';

    if (opResults) {
      opResults.replaceChildren(Object.assign(document.createElement('pre'), {
        textContent: document.documentElement.dataset.hosted === 'true'
          ? 'Collecting AWS observations and evaluating metric patterns. This does not change resources.'
          : '[PREVIEW] Collecting workspace observations and evaluating metrics. Demo workspaces make no AWS calls.'
      }));
    }

    try {
      const res = await fetch('/api/pipeline/run', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json', 'X-CSRF-Token': document.querySelector('meta[name=csrf-token]').content },
        body: JSON.stringify({
          apply: false,
          window_hours: document.documentElement.dataset.hosted === 'true' ? 6 : 48
        })
      });

      const data = await res.json();
      if (!res.ok) throw new Error(data.error || 'Pipeline run failed');

      if (opResults) {
        let text = data.summary || 'Pipeline cycle finished successfully.\n';
        if (data.logs && data.logs.length) {
          text += '\n--- EXECUTION AUDIT LOGS ---\n' + data.logs.join('\n');
        }
        opResults.replaceChildren(Object.assign(document.createElement('pre'), {textContent:text}));
      }

      showToast('Pipeline Cycle Complete', document.documentElement.dataset.hosted === 'true'
        ? 'Collection complete. Review the metric evidence; no AWS resources were changed.'
        : 'Collection complete. Review the generated plans; no AWS changes were applied.', 'success', 4500);
      if (reloadViewBtn) reloadViewBtn.hidden = false;
      execOpBtn.textContent = 'Cycle Finished';
      renderPlans(data);


    } catch (err) {
      if (opResults) {
        opResults.replaceChildren(Object.assign(document.createElement('pre'), {textContent:'[ERROR] '+err.message}));
      }
      showToast('Pipeline Execution Error', err.message, 'error', 5000);
      execOpBtn.disabled = false;
      execOpBtn.textContent = 'Retry Pipeline Cycle';
    }
  });
}

/* ==========================================================================
   Restoration / Rollback Buttons
   ========================================================================== */

document.querySelectorAll('.restore[data-id]').forEach(btn => {
  btn.addEventListener('click', async () => {
    const actionId = btn.dataset.id;
    const kind = btn.dataset.kind;
    if (!confirm('Restore the recorded prior state for action '+actionId+'?')) return;
    btn.disabled = true;
    const orig = btn.textContent;
    btn.textContent = 'Restoring...';

    try {
      const res = await fetch('/api/workload/restore', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json', 'X-CSRF-Token': document.querySelector('meta[name=csrf-token]').content },
        body: JSON.stringify({ action_id: actionId, kind: kind })
      });
      const data = await res.json();
      if (!res.ok) throw new Error(data.error || 'Failed to restore action');
      showToast('Baseline Restored', data.message || `Action ${actionId} verified and restored.`, 'success', 4000);
      setTimeout(() => window.location.reload(), 1200);
    } catch (err) {
      showToast('Restoration Failed', err.message, 'error', 5000);
      btn.disabled = false;
      btn.textContent = orig;
    }
  });
});

/* ==========================================================================
   Logout / Purge Session
   ========================================================================== */

const logoutBtns = document.querySelectorAll('#logout, #logout-top');
logoutBtns.forEach(btn => {
  btn.addEventListener('click', async () => {
    try {
      await fetch('/api/auth/logout', { method: 'POST', headers: {'Content-Type':'application/json','X-CSRF-Token':document.querySelector('meta[name=csrf-token]').content}, body:'{}' });
    } catch (_) {}
    window.location.href = '/login';
  });
});


// The browser submits IDs only. Plan details and AWS identity stay on the server.
function renderPlans(data) {
  const root = document.querySelector('#reviewed-plans');
  if (!root) return;
  root.replaceChildren();
  if (document.documentElement.dataset.hosted === 'true') {
    root.append(Object.assign(document.createElement('p'), {textContent:'Hosted monitoring is read-only. Review metric evidence above; cloud actions are unavailable here.'}));
    return;
  }
  const plans = data.report?.plans_generated || [];
  if (!plans.length) {
    root.append(Object.assign(document.createElement('p'), {textContent:'No eligible plans. Stale or missing observations cannot authorize a change.'}));
    return;
  }
  root.append(Object.assign(document.createElement('p'), {textContent:'Review each action before execution. This preview expires in five minutes; the connected AWS identity must have the required permissions and resource opt-in tags.'}));
  for (const plan of plans) {
    const card = document.createElement('article'); card.className='reviewed-plan';
    const title = document.createElement('h3'); title.textContent=plan.action.replaceAll('_',' ');
    const details = document.createElement('pre');
    details.textContent=JSON.stringify({account:plan.account,region:plan.region,resource:plan.resource,
      reason:plan.reason,effect:plan.effect || (plan.action==='stop_idle_instance'?'Stops this instance. Restart is available from action history.':'Adds a review tag. No measured savings.'),
      required_tags:plan.required_tags,evidence:plan.evidence},null,2);
    const button = document.createElement('button'); button.className='button secondary';button.textContent='Execute this plan';
    const status = document.createElement('p');status.setAttribute('role','status');
    button.addEventListener('click', async () => {
      if (!confirm('Execute '+plan.action+' on '+plan.resource+' in account '+plan.account+'? This changes a real AWS resource.')) return;
      button.disabled=true;status.textContent='Executing and verifying... Do not resubmit.';
      try {
        const response=await fetch('/api/plans/execute',{method:'POST',headers:{'Content-Type':'application/json','X-CSRF-Token':document.querySelector('meta[name=csrf-token]').content},body:JSON.stringify({preview_token:data.preview_token,plan_id:plan.id})});
        const result=await response.json();
        if(!response.ok) throw new Error(result.error || 'Result not verified');
        status.textContent='Recorded status: '+result.result.status+'. Action ID: '+result.result.id+'. Refresh the dashboard to inspect the audit or restore.';
      } catch(error) { status.textContent=error.message+' This selection will not be retried automatically.'; }
      if(reloadViewBtn) reloadViewBtn.hidden=false;
    });
    card.append(title,details,button,status);root.append(card);
  }
}
