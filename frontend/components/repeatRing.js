/**
 * RxTrail — Δακτύλιος Επανάληψης (Repeat Ring)
 * Spec §3.3: SVG ring 40×40, stroke 4px, progress χρώμα ανάλογα με ημέρες.
 */
export function renderRepeatRing(daysElapsed, durationDays, options = {}) {
  const total = durationDays || 30;
  const remaining = Math.max(0, total - daysElapsed);
  const progress = Math.min(daysElapsed / total, 1);
  const circumference = 2 * Math.PI * 14; // r=14, stroke=4
  const offset = circumference * (1 - progress);

  // Color logic: 0-24 green, 25-29 orange, 30+ red + pulse
  let color = 'var(--st-active)';
  let pulse = false;
  if (daysElapsed >= 30) {
    color = 'var(--danger)';
    pulse = true;
  } else if (daysElapsed >= 25) {
    color = 'var(--st-atrisk)';
  }

  const label = daysElapsed >= total ? '!' : String(remaining);
  const size = options.size || 40;

  return `
    <svg class="repeat-ring ${pulse ? 'ring-pulse' : ''}" 
         width="${size}" height="${size}" viewBox="0 0 40 40"
         style="flex-shrink:0;">
      <circle class="track" cx="20" cy="20" r="14"/>
      <circle class="progress" cx="20" cy="20" r="14"
              stroke="${color}"
              stroke-dasharray="${circumference}"
              stroke-dashoffset="${offset}"/>
      <text x="20" y="20"
            font-family="var(--font-data)"
            font-size="var(--fs-clock)"
            font-weight="var(--w-clock)"
            fill="var(--ink)"
            text-anchor="middle" dominant-baseline="central">${label}</text>
    </svg>
  `;
}

/**
 * Create a status chip HTML string.
 * Spec §3.2: pill, 26px, 12% opacity bg, full color text.
 */
export function statusChip(status) {
  const labels = {
    NEW: 'ΝΕΟ', ASSESSED: 'ΑΞΙΟΛΟΓΗΘΗΚΕ', AT_DOCTOR: 'ΣΕ ΓΙΑΤΡΟ',
    PRESCRIBED: 'ΓΡΑΦΤΗΚΕ', ACTIVE: 'ΕΝΕΡΓΟ', AT_RISK: 'ΣΕ ΚΙΝΔΥΝΟ',
    LOST: 'ΧΑΘΗΚΕ', CLOSED: 'ΕΚΛΕΙΣΕ',
  };
  const s = (status || '').toUpperCase();
  return `<span class="status-chip status-${s}">${labels[s] || status}</span>`;
}

/**
 * Queue card — Spec §3.4
 */
export function queueCard(item, options = {}) {
  const status = (item.status || '').toUpperCase();
  const statusClass = `qc-${status.toLowerCase()}`;
  const icon = kindIcons[item.kind] || '';
  const why = item.why || '';
  const title = item.alias || item.case_id || '—';
  const meta = [
    options.sourceName || '',
    options.area || '',
  ].filter(Boolean).join(' · ');

  const ring = (status === 'ACTIVE' && item.days_elapsed !== undefined)
    ? renderRepeatRing(item.days_elapsed || 0, item.duration_days || 30)
    : `<span style="font-size:24px;">${icon}</span>`;

  return `
    <div class="queue-card ${statusClass}" data-case-id="${item.case_id || ''}">
      <div class="qc-header">
        <span class="qc-title">${es(title)}</span>
        ${ring}
      </div>
      ${meta ? `<div class="qc-meta">${statusChip(status)} · ${es(meta)}</div>` : ''}
      ${why ? `<div class="qc-why">«${es(why)}»</div>` : ''}
      <div class="qc-actions">
        ${item.case_id ? `<button class="btn-text btn-sm open-case-btn" data-case-id="${item.case_id}">📋 Άνοιγμα →</button>` : ''}
        ${options.onCall ? `<button class="btn-text btn-sm" onclick="event.stopPropagation();${options.onCall}">📞 Κλήση</button>` : ''}
      </div>
    </div>
  `;
}

const kindIcons = { AT_RISK: '🚨', REPEAT_DUE: '🔁', SLA_NEW: '🆕', SLA_ASSESSED: '📋', SLA_PRESCRIBED: '📝', ACTION_DUE: '📌', VISIT_SUGGEST: '👋' };
function es(s) { return String(s||'').replace(/&/g,'&amp;').replace(/</g,'&lt;').replace(/>/g,'&gt;'); }
