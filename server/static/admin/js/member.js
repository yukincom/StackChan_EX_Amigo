// member.js - メンバー管理

let memberData = null;

function emptyMember({ master = false, friend = false } = {}) {
  const base = {
    name: '',
    notes: '',
    interests: [],
    line_user_id: '',
    discord_user_id: '',
  };
  if (!master) {
    base.call = '';
    base.speech_patterns = [];
  }
  if (friend) {
    delete base.notes;
    delete base.interests;
    delete base.line_user_id;
    delete base.discord_user_id;
  }
  return base;
}

function normalizeMember(member, { master = false, friend = false } = {}) {
  const normalized = { ...emptyMember({ master, friend }), ...(member && typeof member === 'object' ? member : {}) };
  if (!master) {
    normalized.speech_patterns = Array.isArray(normalized.speech_patterns) ? normalized.speech_patterns : [];
  }
  if (!friend) {
    normalized.interests = Array.isArray(normalized.interests)
      ? normalized.interests
      : (normalized.interests ? [normalized.interests] : []);
  }
  return normalized;
}

function normalizeMemberData(raw) {
  const rawMaster = raw?.master && typeof raw.master === 'object' && !Array.isArray(raw.master)
    ? raw.master
    : emptyMember({ master: true });

  return {
    master: normalizeMember(rawMaster, { master: true }),
    family: Array.isArray(raw?.family) ? raw.family.map(member => normalizeMember(member)) : [],
    friends: Array.isArray(raw?.friends) ? raw.friends.map(member => normalizeMember(member, { friend: true })) : [],
  };
}

function getMembers(category) {
  if (category === 'master') return [memberData.master];
  return memberData[category] ?? [];
}

function getMemberRef(category, idx) {
  return category === 'master' ? memberData.master : memberData[category][idx];
}

async function loadMember() {
  document.getElementById('member-loading').style.display = '';
  document.getElementById('member-content').style.display = 'none';
  try {
    const res = await fetch('/admin/api/member');
    memberData = normalizeMemberData(await res.json());
    renderMember();
  } catch(e) {
    toast('読み込み失敗: ' + e.message, 'err');
  }
}

function renderMember() {
  const container = document.getElementById('member-content');
  container.innerHTML = '';
  const sections = [
    { key: 'master',  label: 'マスター', badge: 'master', icon: '⭐', canAdd: false, canRemove: false },
    { key: 'family',  label: '家族',     badge: 'family', icon: '👨‍👩‍👦', canAdd: true,  canRemove: true  },
    { key: 'friends', label: 'フレンド', badge: 'friend', icon: '🤝', canAdd: true,  canRemove: true  },
  ];
  for (const sec of sections) {
    const members = getMembers(sec.key);
    const sh = document.createElement('div');
    sh.style.cssText = 'display:flex; align-items:center; justify-content:space-between; margin:20px 0 8px;';
    sh.innerHTML = `
      <div class="section-label" style="margin:0">${sec.icon} ${sec.label}</div>
      ${sec.canAdd ? `<button class="btn-secondary btn-sm" onclick="addMember('${sec.key}')">＋ 追加</button>` : '<span></span>'}
    `;
    container.appendChild(sh);
    if (sec.key !== 'master' && members.length === 0) {
      const empty = document.createElement('p');
      empty.style.cssText = 'color:var(--muted); font-size:13px; padding:8px 0;';
      empty.textContent = 'メンバーがいません';
      container.appendChild(empty);
    }
    members.forEach((m, idx) => container.appendChild(buildMemberCard(sec.key, idx, m, sec.badge, sec.canRemove)));
  }
  document.getElementById('member-loading').style.display = 'none';
  document.getElementById('member-content').style.display = '';
}

function buildMemberCard(category, idx, m, badgeClass, canRemove) {
  const card = document.createElement('div');
  card.className = 'member-card';
  card.dataset.category = category;
  card.dataset.idx = idx;
  const fallbackName = badgeClass === 'master' ? 'マスター' : '新しいメンバー';
  const primaryName = Array.isArray(m.name) ? m.name[0] : m.name;
  const displayName = primaryName || fallbackName;
  const badgeLabel = { master: 'マスター', family: '家族', friend: 'フレンド' }[badgeClass] ?? '';
  card.innerHTML = `
    <div class="member-head" onclick="toggleCard(this)">
      <div class="member-title">
        <span class="member-badge badge-${badgeClass}">${badgeLabel}</span>
        <span class="name-display">${esc(displayName)}</span>
      </div>
      <div style="display:flex; gap:8px; align-items:center">
        ${canRemove ? `<button class="btn-danger btn-sm" onclick="event.stopPropagation(); removeMember('${category}', ${idx})">削除</button>` : ''}
        <span class="chevron">▼</span>
      </div>
    </div>
    <div class="member-body">${buildMemberForm(category, idx, m, badgeClass)}</div>
  `;
  return card;
}

function buildMemberForm(category, idx, m, badgeClass) {
  const isMaster = badgeClass === 'master';
  const isFriend = badgeClass === 'friend';
  const nameVal = Array.isArray(m.name) ? m.name.join(', ') : (m.name ?? '');
  const callVal = Array.isArray(m.call) ? m.call.join(', ') : (m.call ?? '');

  if (isMaster) {
    return `
      <div class="field">
        <label>名前（複数はカンマ区切り）</label>
        <input type="text" value="${esc(nameVal)}"
          oninput="updateMemberField('${category}', ${idx}, 'name', this.value)">
      </div>
      <div class="field">
        <label>備考（LLMへのヒント）</label>
        <input type="text" value="${esc(m.notes ?? '')}"
          oninput="updateMemberField('${category}', ${idx}, 'notes', this.value)">
      </div>
      <div class="field">
        <label>好きなこと（Enterで追加）</label>
        ${buildTagInput(category, idx, 'interests', m.interests ?? [])}
      </div>
      <div class="grid-2">
        <div class="field">
          <label>LINE ユーザーID</label>
          <input type="text" value="${esc(m.line_user_id ?? '')}"
            oninput="updateMemberField('${category}', ${idx}, 'line_user_id', this.value)">
        </div>
        <div class="field">
          <label>Discord ユーザーID</label>
          <input type="text" value="${esc(m.discord_user_id ?? '')}"
            oninput="updateMemberField('${category}', ${idx}, 'discord_user_id', this.value)">
        </div>
      </div>
    `;
  }

  let html = `
    <div class="grid-2">
      <div class="field">
        <label>名前（複数はカンマ区切り）</label>
        <input type="text" value="${esc(nameVal)}"
          oninput="updateMemberField('${category}', ${idx}, 'name', this.value)">
      </div>
      <div class="field">
        <label>呼び方（複数はカンマ区切り）</label>
        <input type="text" value="${esc(callVal)}"
          oninput="updateMemberField('${category}', ${idx}, 'call', this.value)">
      </div>
    </div>
    <div class="field">
      <label>発話パターン（Enterで追加）</label>
      ${buildTagInput(category, idx, 'speech_patterns', m.speech_patterns ?? [])}
    </div>
  `;

  if (!isFriend) {
    html += `
      <div class="field">
        <label>備考（LLMへのヒント）</label>
        <input type="text" value="${esc(m.notes ?? '')}"
          oninput="updateMemberField('${category}', ${idx}, 'notes', this.value)">
      </div>
      <div class="field">
        <label>好きなこと（Enterで追加）</label>
        ${buildTagInput(category, idx, 'interests', m.interests ?? [])}
      </div>
      <div class="grid-2">
        <div class="field">
          <label>LINE ユーザーID</label>
          <input type="text" value="${esc(m.line_user_id ?? '')}"
            oninput="updateMemberField('${category}', ${idx}, 'line_user_id', this.value)">
        </div>
        <div class="field">
          <label>Discord ユーザーID</label>
          <input type="text" value="${esc(m.discord_user_id ?? '')}"
            oninput="updateMemberField('${category}', ${idx}, 'discord_user_id', this.value)">
        </div>
      </div>
    `;
  }
  return html;
}

function buildTagInput(category, idx, field, tags) {
  return `
    <div class="tags-wrap" id="tags-${category}-${idx}-${field}">
      ${tags.map(t => `
        <span class="tag">${esc(t)}
          <span class="tag-del" onclick="removeTag('${category}', ${idx}, '${field}', '${esc(t)}')">×</span>
        </span>`).join('')}
      <input class="tag-input" type="text" placeholder="例：ただいま"
        onkeydown="handleTagKey(event, '${category}', ${idx}, '${field}', this)">
    </div>
  `;
}

function handleTagKey(e, category, idx, field, input) {
  if (e.key === 'Enter' || e.key === ',') {
    e.preventDefault();
    const val = input.value.trim().replace(/,/g, '');
    if (!val) return;
    const member = getMemberRef(category, idx);
    const arr = member[field] ?? [];
    if (!arr.includes(val)) {
      arr.push(val);
      member[field] = arr;
    }
    input.value = '';
    reRenderTagsWrap(category, idx, field);
  }
}

function removeTag(category, idx, field, tag) {
  const member = getMemberRef(category, idx);
  member[field] = (member[field] ?? []).filter(t => t !== tag);
  reRenderTagsWrap(category, idx, field);
}

function reRenderTagsWrap(category, idx, field) {
  const wrap = document.getElementById(`tags-${category}-${idx}-${field}`);
  if (!wrap) return;
  const tags = getMemberRef(category, idx)[field] ?? [];
  const savedVal = wrap.querySelector('.tag-input')?.value ?? '';
  wrap.innerHTML = tags.map(t => `
    <span class="tag">${esc(t)}
      <span class="tag-del" onclick="removeTag('${category}', ${idx}, '${field}', '${esc(t)}')">×</span>
    </span>`).join('') + `
    <input class="tag-input" type="text" placeholder="例：ただいま" value="${esc(savedVal)}"
      onkeydown="handleTagKey(event, '${category}', ${idx}, '${field}', this)">
  `;
}

function updateMemberField(category, idx, field, value) {
  const member = getMemberRef(category, idx);
  if (field === 'name' || field === 'call') {
    const parts = value.split(',').map(s => s.trim()).filter(Boolean);
    member[field] = parts.length === 1 ? parts[0] : parts;
    const card = document.querySelector(`.member-card[data-category="${category}"][data-idx="${idx}"]`);
    if (card) {
      const fallbackName = category === 'master' ? 'マスター' : '新しいメンバー';
      const primaryName = Array.isArray(member.name) ? member.name[0] : member.name;
      const newName = primaryName || fallbackName;
      const disp = card.querySelector('.name-display');
      if (disp) disp.textContent = newName;
    }
  } else {
    member[field] = value;
  }
}

function toggleCard(head) {
  head.nextElementSibling.classList.toggle('open');
  head.querySelector('.chevron').classList.toggle('open');
}

function addMember(category) {
  if (category === 'master') return;
  if (!memberData[category]) memberData[category] = [];
  const member = category === 'friends' ? emptyMember({ friend: true }) : emptyMember();
  member.name = '新しいメンバー';
  memberData[category].push(member);
  renderMember();
  const cards = document.querySelectorAll(`.member-card[data-category="${category}"]`);
  const last = cards[cards.length - 1];
  if (last) {
    toggleCard(last.querySelector('.member-head'));
    last.scrollIntoView({behavior:'smooth',block:'center'});
  }
}

function removeMember(category, idx) {
  if (!confirm('このメンバーを削除しますか？')) return;
  memberData[category].splice(idx, 1);
  renderMember();
}

async function saveMember() {
  try {
    const res = await fetch('/admin/api/member', {
      method: 'POST', headers: {'Content-Type': 'application/json'},
      body: JSON.stringify(normalizeMemberData(memberData))
    });
    const data = await res.json();
    if (data.ok) toast('✅ 保存しました');
    else toast('❌ 保存失敗: ' + data.error, 'err');
  } catch(e) { toast('❌ 通信エラー: ' + e.message, 'err'); }
}
