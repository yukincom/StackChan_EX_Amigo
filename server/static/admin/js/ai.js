// ai.js - AI設定

async function loadAiEnv() {
  document.getElementById('ai-loading').style.display = '';
  document.getElementById('ai-content').style.display = 'none';
  try {
    const res = await adminFetch('/admin/api/ai_env');
    const groups = await res.json();
    renderEnvGroups(groups, 'ai-content');
    _setupSearchSettings();
    document.getElementById('ai-loading').style.display = 'none';
    document.getElementById('ai-content').style.display = '';
    // モデルフィールドにチェックUIを注入
    _injectModelCheckers();
  } catch(e) { toast('読み込み失敗: ' + e.message, 'err'); }
}

async function saveAiEnv() {
  try {
    const res = await adminFetch('/admin/api/ai_env', {
      method: 'POST', headers: {'Content-Type': 'application/json'},
      body: JSON.stringify(collectEnvValues('ai-content'))
    });
    const data = await res.json();
    if (data.ok) {
      toast('✅ 保存しました（サーバー再起動で反映）');
      const status = document.getElementById('search-save-status');
      if (status) status.textContent = '保存済み。サーバー再起動後に反映されます。';
    } else toast('❌ 保存失敗: ' + data.error, 'err');
  } catch(e) { toast('❌ 通信エラー: ' + e.message, 'err'); }
}

// ── MLXモデルチェック UI の注入 ──────────────────────────

function _setupSearchSettings() {
  const container = document.getElementById('ai-content');
  const input = key => container.querySelector(`[data-key="${key}"]`);
  const provider = input('AI_SEARCH_PROVIDER');
  if (!provider) return;
  const panel = document.createElement('div');
  panel.className = 'env-group';
  panel.style.cssText = 'border:2px solid var(--accent, #6384dd);padding:16px;border-radius:12px;';
  panel.innerHTML = '<div class="env-group-title">🔍 ネット検索</div>' +
    '<p id="search-provider-status" role="status" style="font-weight:bold"></p>' +
    '<p id="search-provider-help"></p>' +
    '<div class="grid-2"></div>' +
    '<p id="search-save-status" role="status">起動時に読み込んだ設定です。変更は保存・サーバー再起動後に反映されます。</p>';
  const grid = panel.querySelector('.grid-2');
  const keys = ['AI_SEARCH_PROVIDER', 'AI_SEARCH_MODEL', 'AI_SEARCH_TEMPERATURE',
    'AI_SEARCH_RECENT_TURNS', 'FIRECRAWL_API_KEY', 'SEARCH_KEYWORDS'];
  keys.forEach(key => {
    const field = input(key)?.closest('.field');
    if (field) grid.appendChild(field);
  });
  container.prepend(panel);
  container.querySelectorAll('.env-group').forEach(group => {
    if (group !== panel && !group.querySelector('[data-key]')) group.remove();
  });
  const update = () => {
    const firecrawl = provider.value === 'firecrawl';
    document.getElementById('search-provider-status').textContent =
      firecrawl ? '選択中：Firecrawl' : '選択中：Gemini / Google検索';
    document.getElementById('search-provider-help').textContent = firecrawl
      ? `会話モデル（${input('AI_CHAT_MODEL')?.value || '会話設定を参照'}）が直前文脈から検索語を作成 → Firecrawlで検索 → 同じ会話モデルで回答。Gemini検索モデルは使用しません。検索はクレジットを消費します。`
      : 'GeminiのGoogle検索を使用します。下の検索モデルと、APIキー欄のGeminiキーを使います。Firecrawlは使用しません。';
    ['AI_SEARCH_MODEL', 'AI_SEARCH_TEMPERATURE'].forEach(key => {
      const field = input(key)?.closest('.field');
      if (field) field.style.display = firecrawl ? 'none' : '';
    });
    const keyField = input('FIRECRAWL_API_KEY')?.closest('.field');
    if (keyField) keyField.style.display = firecrawl ? '' : 'none';
    // Hidden inputs remain in the form so switching back preserves their values.
  };
  container.oninput = () => {
    update();
    document.getElementById('search-save-status').textContent = '未保存の変更があります。保存後、サーバーを再起動してください。';
  };
  update();
}

function _injectModelCheckers() {
  // MLXモデルチェックが必要なフィールド
  ['AI_CHAT_MODEL', 'AI_SUMMARY_MODEL'].forEach(key => {
    const input = document.querySelector(`[data-key="${key}"]`);
    if (!input) return;

    // ラッパーをflexにしてボタンを横に並べる
    const field = input.closest('.field');
    input.style.flex = '1';

    const wrap = document.createElement('div');
    wrap.style.cssText = 'display:flex; gap:8px; align-items:center;';
    input.parentNode.insertBefore(wrap, input);
    wrap.appendChild(input);

    // ステータス表示
    const status = document.createElement('span');
    status.id = `mlx-status-${key}`;
    status.style.cssText = 'font-size:13px; white-space:nowrap; min-width:24px;';
    wrap.appendChild(status);

    // ダウンロードボタン
    const btn = document.createElement('button');
    btn.id = `mlx-btn-${key}`;
    btn.className = 'btn-secondary btn-sm';
    btn.textContent = '📥 DL';
    btn.style.display = 'none';
    btn.onclick = () => _startDownload(key);
    wrap.appendChild(btn);

    // 入力変更時に再チェック
    input.addEventListener('change', () => _checkModel(key));
    input.addEventListener('blur',   () => _checkModel(key));

    // 初回チェック
    _checkModel(key);
  });
}

async function _checkModel(key) {
  const input  = document.querySelector(`[data-key="${key}"]`);
  const status = document.getElementById(`mlx-status-${key}`);
  const btn    = document.getElementById(`mlx-btn-${key}`);
  if (!input || !status) return;

  const model = input.value.trim();
  if (!model) { status.textContent = ''; btn.style.display = 'none'; return; }

  status.textContent = '⏳';
  try {
    const res  = await adminFetch(`/admin/api/mlx/check?model=${encodeURIComponent(model)}`);
    const data = await res.json();

    if (data.status === 'downloading') {
      status.textContent = '⏳ DL中...';
      btn.style.display = 'none';
      // ポーリング継続
      setTimeout(() => _checkModel(key), 3000);
    } else if (data.cached) {
      status.textContent = '✅';
      btn.style.display = 'none';
    } else {
      status.innerHTML = '<span style="color:var(--danger)">❌ 未DL</span>';
      btn.style.display = '';
    }
  } catch(e) {
    status.textContent = '⚠️';
  }
}

async function _startDownload(key) {
  const input  = document.querySelector(`[data-key="${key}"]`);
  const status = document.getElementById(`mlx-status-${key}`);
  const btn    = document.getElementById(`mlx-btn-${key}`);
  const model  = input?.value.trim();
  if (!model) return;

  btn.style.display = 'none';
  status.textContent = '⏳ DL中...';

  try {
    await adminFetch('/admin/api/mlx/download', {
      method: 'POST', headers: {'Content-Type': 'application/json'},
      body: JSON.stringify({ model })
    });
    // 3秒ごとにポーリング
    const poll = setInterval(async () => {
      const res  = await adminFetch(`/admin/api/mlx/check?model=${encodeURIComponent(model)}`);
      const data = await res.json();
      if (data.cached) {
        status.textContent = '✅';
        btn.style.display = 'none';
        clearInterval(poll);
        toast('✅ ダウンロード完了: ' + model);
      } else if (data.status === 'error') {
        status.innerHTML = '<span style="color:var(--danger)">❌ DL失敗</span>';
        btn.style.display = '';
        clearInterval(poll);
        toast('❌ ダウンロード失敗', 'err');
      }
    }, 3000);
  } catch(e) {
    toast('❌ 通信エラー: ' + e.message, 'err');
  }
}
