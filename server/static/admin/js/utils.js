// utils.js - 共通ユーティリティ

const ADMIN_TOKEN_STORAGE_KEY = 'stackchan_admin_token';

function showPage(name, el) {
  document.querySelectorAll('.page').forEach(p => p.classList.remove('active'));
  document.querySelectorAll('nav a').forEach(a => a.classList.remove('active'));
  document.getElementById('page-' + name).classList.add('active');
  el.classList.add('active');
}

function toast(msg, type = 'ok') {
  const el = document.getElementById('toast');
  el.textContent = msg;
  el.className = type;
  setTimeout(() => { el.className = ''; el.textContent = ''; }, 3000);
}

function esc(s) {
  return String(s ?? '').replace(/&/g,'&amp;').replace(/</g,'&lt;').replace(/>/g,'&gt;').replace(/"/g,'&quot;');
}

// ===== Admin auth =====

function getAdminToken() {
  try {
    return sessionStorage.getItem(ADMIN_TOKEN_STORAGE_KEY) || '';
  } catch (_) {
    return '';
  }
}

function setAdminToken(token) {
  const value = String(token || '').trim();
  try {
    if (value) sessionStorage.setItem(ADMIN_TOKEN_STORAGE_KEY, value);
    else sessionStorage.removeItem(ADMIN_TOKEN_STORAGE_KEY);
  } catch (_) { /* ignore */ }
}

function clearAdminToken() {
  setAdminToken('');
}

function adminAuthHeaders(extra = {}) {
  const headers = { ...extra };
  const token = getAdminToken();
  if (token) {
    headers['Authorization'] = 'Bearer ' + token;
  }
  return headers;
}

/**
 * admin API 用 fetch。Bearer を付与し、401 ならトークン入力を促す。
 */
async function adminFetch(url, options = {}) {
  const opts = { ...options };
  const baseHeaders = opts.headers || {};
  opts.headers = adminAuthHeaders(
    baseHeaders instanceof Headers
      ? Object.fromEntries(baseHeaders.entries())
      : { ...baseHeaders }
  );

  const res = await fetch(url, opts);
  if (res.status === 401) {
    showAdminAuthGate(true);
    const err = new Error('認証が必要です。ADMIN_TOKEN を入力してください。');
    err.status = 401;
    throw err;
  }
  return res;
}

function showAdminAuthGate(visible) {
  const gate = document.getElementById('admin-auth-gate');
  if (!gate) return;
  gate.style.display = visible ? 'flex' : 'none';
  if (visible) {
    const input = document.getElementById('admin-token-input');
    if (input) {
      input.value = getAdminToken();
      setTimeout(() => input.focus(), 50);
    }
  }
}

async function submitAdminToken() {
  const input = document.getElementById('admin-token-input');
  const token = (input?.value || '').trim();
  if (!token) {
    toast('トークンを入力してください', 'err');
    return false;
  }
  setAdminToken(token);
  try {
    const res = await adminFetch('/admin/api/auth/check');
    if (!res.ok) throw new Error('unauthorized');
    showAdminAuthGate(false);
    toast('認証しました');
    if (typeof window.onAdminAuthenticated === 'function') {
      window.onAdminAuthenticated();
    }
    return true;
  } catch (e) {
    clearAdminToken();
    toast('トークンが正しくないか、ADMIN_TOKEN が未設定です', 'err');
    return false;
  }
}

function logoutAdminToken() {
  clearAdminToken();
  showAdminAuthGate(true);
  toast('トークンをクリアしました');
}

/**
 * 起動時: トークンなし or 保存済みトークンで認可確認。
 * 成功なら true。401 ならゲート表示して false。
 */
async function ensureAdminAuth() {
  try {
    const res = await adminFetch('/admin/api/auth/check');
    if (res.ok) {
      showAdminAuthGate(false);
      return true;
    }
  } catch (_) {
    /* gate already shown by adminFetch */
  }
  return false;
}

// ===== envレンダリング共通関数 =====
function renderEnvGroups(groups, containerId) {
  const container = document.getElementById(containerId);
  container.innerHTML = '';

  for (const group of groups) {
    const section = document.createElement('div');
    section.className = 'env-group';
    section.innerHTML = `<div class="env-group-title">${esc(group.group)}</div>`;

    const grid = document.createElement('div');
    grid.className = 'grid-2';

    for (const item of group.items) {
      const field = document.createElement('div');
      field.className = 'field';

      if (item.type === 'empty') {
        // 空のdivでグリッドの穴埋め
      } else if (item.type === 'datalist') {
        const isVoicevox = item.key === 'VOICEVOX_SPEAKER_ID';
        const optionsHtml = (item.options || []).map(o => {
          const val = isVoicevox ? o.split(':')[0].trim() : o;
          const selected = val === item.value ? 'selected' : '';
          return `<option value="${esc(val)}" ${selected}>${esc(o)}</option>`;
        }).join('');
        field.innerHTML = `
          <label>${esc(item.label)} <small style="color:#aaa">${esc(item.key)}</small></label>
          <select data-key="${esc(item.key)}">${optionsHtml}</select>
        `;
      } else if (item.type === 'select') {
        const optionsHtml = (item.options || []).map(o => {
          const [val, label] = o.split(':');
          const selected = val === item.value ? 'selected' : '';
          return `<option value="${esc(val)}" ${selected}>${esc(label ?? val)}</option>`;
        }).join('');
        field.innerHTML = `
          <label>${esc(item.label)} <small style="color:#aaa">${esc(item.key)}</small></label>
          <select data-key="${esc(item.key)}">${optionsHtml}</select>
        `;
      } else if (item.type === 'textarea') {
        field.style.gridColumn = '1 / -1';
        field.innerHTML = `
          <label>${esc(item.label)} <small style="color:#aaa">${esc(item.key)}</small></label>
          <textarea data-key="${esc(item.key)}">${esc(item.value)}</textarea>
        `;
      } else {
        const isPassword = item.type === 'password';
        const placeholder = isPassword ? (item.placeholder || '') : '';
        const setHint = isPassword && item.is_set
          ? '<div style="font-size:11px;color:var(--muted);margin-top:4px;">設定済み。空のまま保存すると変更しません。</div>'
          : '';
        field.innerHTML = `
          <label>${esc(item.label)} <small style="color:#aaa">${esc(item.key)}</small></label>
          <input type="${esc(item.type)}" data-key="${esc(item.key)}" value="${esc(item.value || '')}"
            ${isPassword ? `autocomplete="new-password" placeholder="${esc(placeholder)}" data-secret="1"` : ''}>
          ${setHint}
        `;
      }
      grid.appendChild(field);
    }
    section.appendChild(grid);
    container.appendChild(section);
  }
}

function collectEnvValues(containerId) {
  const updates = {};
  document.querySelectorAll(`#${containerId} [data-key]`).forEach(el => {
    updates[el.dataset.key] = el.value;
  });
  return updates;
}
