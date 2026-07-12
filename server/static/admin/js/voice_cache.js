// voice_cache.js - キャッシュ音声管理

let voiceCacheData = { pc_cache: [], stack_sd: [] };
let voiceCachePaths = { pc_cache_dir: '', stack_sd_dir: '' };

async function loadVoiceCache() {
  document.getElementById('voice-cache-loading').style.display = '';
  document.getElementById('voice-cache-content').style.display = 'none';
  try {
    const res = await adminFetch('/admin/api/voice_cache');
    const data = await res.json();
    voiceCacheData = data.catalog || data;
    voiceCachePaths = data.paths || { pc_cache_dir: '', stack_sd_dir: '' };
    renderVoiceCache();
  } catch (e) {
    toast('読み込み失敗: ' + e.message, 'err');
  }
}

function renderVoiceCache() {
  const pcNote = document.getElementById('pc-cache-dir-note');
  const sdNote = document.getElementById('stack-sd-dir-note');
  if (pcNote) {
    if (voiceCachePaths.pc_cache_dir_display) {
      pcNote.textContent = voiceCachePaths.pc_cache_is_default
        ? `現在の保存先: ${voiceCachePaths.pc_cache_dir_display}`
        : `現在の保存先: ${voiceCachePaths.pc_cache_dir_display}（推奨: ${voiceCachePaths.pc_cache_default_display}）`;
    } else {
      pcNote.textContent = '';
    }
  }
  if (sdNote) {
    if (voiceCachePaths.stack_sd_dir_display) {
      sdNote.textContent = voiceCachePaths.stack_sd_is_default
        ? `現在の保存先: ${voiceCachePaths.stack_sd_dir_display}`
        : `現在の保存先: ${voiceCachePaths.stack_sd_dir_display}（推奨: ${voiceCachePaths.stack_sd_default_display}）`;
    } else {
      sdNote.textContent = '';
    }
  }
  renderPcCacheTable();
  renderStackSdTable();
  document.getElementById('voice-cache-loading').style.display = 'none';
  document.getElementById('voice-cache-content').style.display = '';
}

function renderPcCacheTable() {
  const tbody = document.getElementById('pc-cache-tbody');
  tbody.innerHTML = (voiceCacheData.pc_cache || []).map((item, idx) => `
    <tr>
      <td><input type="text" value="${esc(item.text)}" oninput="voiceCacheData.pc_cache[${idx}].text=this.value"></td>
      <td><input type="text" value="${esc(item.filename)}" oninput="voiceCacheData.pc_cache[${idx}].filename=this.value"></td>
      <td style="text-align:center; display:flex; gap:6px; justify-content:center;">
        <button class="btn-secondary btn-sm" onclick="generatePcCache(${idx})">生成</button>
        <button class="btn-danger btn-sm" onclick="removePcCacheRow(${idx})">削除</button>
      </td>
    </tr>
  `).join('');
}

function renderStackSdTable() {
  const tbody = document.getElementById('stack-sd-tbody');
  tbody.innerHTML = (voiceCacheData.stack_sd || []).map((item, idx) => `
    <tr>
      <td><input type="text" value="${esc(item.text)}" oninput="voiceCacheData.stack_sd[${idx}].text=this.value"></td>
      <td><input type="text" value="${esc(item.filename)}" oninput="voiceCacheData.stack_sd[${idx}].filename=this.value"></td>
      <td><input type="text" value="${esc(item.endpoint)}" oninput="voiceCacheData.stack_sd[${idx}].endpoint=this.value"></td>
      <td style="text-align:center; display:flex; gap:6px; justify-content:center;">
        <button class="btn-secondary btn-sm" onclick="generateStackSd(${idx})">生成</button>
        <button class="btn-danger btn-sm" onclick="removeStackSdRow(${idx})">削除</button>
      </td>
    </tr>
  `).join('');
}

function addPcCacheRow() {
  voiceCacheData.pc_cache.push({ id: '', text: '', filename: '' });
  renderPcCacheTable();
}

function removePcCacheRow(idx) {
  voiceCacheData.pc_cache.splice(idx, 1);
  renderPcCacheTable();
}

function addStackSdRow() {
  voiceCacheData.stack_sd.push({ id: '', text: '', filename: '', endpoint: '' });
  renderStackSdTable();
}

function removeStackSdRow(idx) {
  voiceCacheData.stack_sd.splice(idx, 1);
  renderStackSdTable();
}

async function saveVoiceCache() {
  try {
    const res = await adminFetch('/admin/api/voice_cache', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify(voiceCacheData),
    });
    const data = await res.json();
    if (data.ok) {
      voiceCacheData = data.catalog;
      voiceCachePaths = data.paths || voiceCachePaths;
      renderVoiceCache();
      toast('✅ 保存しました');
    } else {
      toast('❌ 保存失敗: ' + data.error, 'err');
    }
  } catch (e) {
    toast('❌ 通信エラー: ' + e.message, 'err');
  }
}

async function _generateVoiceCache(kind, item) {
  const res = await adminFetch('/admin/api/voice_cache/generate', {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ kind, item }),
  });
  return await res.json();
}

async function generatePcCache(idx) {
  const item = voiceCacheData.pc_cache[idx];
  if (!item?.text || !item?.filename) {
    toast('❌ 内容テキストとファイル名が必要です', 'err');
    return;
  }
  const data = await _generateVoiceCache('pc_cache', item);
  if (data.ok) toast(`✅ 生成しました: ${data.path || item.filename + '.mp3'}`);
  else toast('❌ 生成失敗: ' + data.error, 'err');
}

async function generateStackSd(idx) {
  const item = voiceCacheData.stack_sd[idx];
  if (!item?.text || !item?.filename || !item?.endpoint) {
    toast('❌ 内容テキスト・ファイル名・エンドポイント名が必要です', 'err');
    return;
  }
  const data = await _generateVoiceCache('stack_sd', item);
  if (data.ok) toast(`✅ 生成しました: ${data.path || item.filename + '.mp3'}`);
  else toast('❌ 生成失敗: ' + data.error, 'err');
}

async function generateAllPcCache() {
  for (let i = 0; i < (voiceCacheData.pc_cache || []).length; i++) {
    const item = voiceCacheData.pc_cache[i];
    if (item?.text && item?.filename) {
      const data = await _generateVoiceCache('pc_cache', item);
      if (!data.ok) {
        toast(`❌ ${item.filename}: ${data.error}`, 'err');
        return;
      }
    }
  }
  toast('✅ PCキャッシュ音声を生成しました');
}

async function generateAllStackSd() {
  for (let i = 0; i < (voiceCacheData.stack_sd || []).length; i++) {
    const item = voiceCacheData.stack_sd[i];
    if (item?.text && item?.filename && item?.endpoint) {
      const data = await _generateVoiceCache('stack_sd', item);
      if (!data.ok) {
        toast(`❌ ${item.filename}: ${data.error}`, 'err');
        return;
      }
    }
  }
  toast('✅ Stack SD音声を書き出しました');
}
