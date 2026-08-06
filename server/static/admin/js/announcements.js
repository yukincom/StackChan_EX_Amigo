// announcements.js - 定時コール
//
// 注意: 表示は時刻順に並べるが、data-idx / update / delete は
// 必ず annData の「元の添字」を使う（ソート後の idx で書くと別件を壊す）。

let annData = [];

async function loadAnnouncements() {
  document.getElementById('ann-loading').style.display = '';
  document.getElementById('ann-content').style.display = 'none';
  try {
    const res = await adminFetch('/admin/api/announcements');
    const data = await res.json();
    annData = Array.isArray(data) ? data : [];
    // 正規化（欠けたフラグを補う）
    annData = annData.map(normalizeAnnItem);
    renderAnnouncements();
  } catch (e) {
    toast('読み込み失敗: ' + e.message, 'err');
  }
}

function normalizeAnnItem(raw) {
  const item = raw && typeof raw === 'object' ? { ...raw } : {};
  item.hour = clampInt(item.hour, 0, 23, 8);
  item.minute = clampInt(item.minute, 0, 59, 0);
  item.message = item.message != null ? String(item.message) : '';
  item.with_weather = !!item.with_weather;

  // キー名は互換のため weekday_only / holiday_only のまま。
  // 意味は「のみ」ではなく「平日を対象」「祝日を対象」。
  // 旧仕様: 両方 false = 毎日 → 移行時は両方 true にする。
  const hasWeekdayKey = Object.prototype.hasOwnProperty.call(item, 'weekday_only');
  const hasHolidayKey = Object.prototype.hasOwnProperty.call(item, 'holiday_only');
  if (!hasWeekdayKey && !hasHolidayKey) {
    item.weekday_only = true;
    item.holiday_only = true;
  } else {
    item.weekday_only = !!item.weekday_only;
    item.holiday_only = !!item.holiday_only;
    // 旧データで両方 false（＝旧・毎日）は新仕様の「毎日」へ
    if (
      item._migrated_from_everyday !== true &&
      item.weekday_only === false &&
      item.holiday_only === false &&
      item._explicit_long_vacation !== true
    ) {
      // 明示的に長期休暇にした行は _explicit_long_vacation を付ける（UI）
      // ファイル上の旧「両方 false」は毎日扱いへ昇格
      item.weekday_only = true;
      item.holiday_only = true;
    }
  }

  if (item.weather_target !== 'tomorrow') {
    if (!item.with_weather) delete item.weather_target;
    else if (item.weather_target !== 'today') item.weather_target = item.weather_target || 'today';
  }
  return item;
}

function clampInt(v, min, max, fallback) {
  const n = Number.parseInt(v, 10);
  if (Number.isNaN(n)) return fallback;
  return Math.min(max, Math.max(min, n));
}

async function loadWeatherScheduleEnv() {
  document.getElementById('ann-weather-loading').style.display = '';
  document.getElementById('ann-weather-content').style.display = 'none';
  try {
    const res = await adminFetch('/admin/api/weather_schedule_env');
    const groups = await res.json();
    renderEnvGroups(groups, 'ann-weather-content');
    document.getElementById('ann-weather-loading').style.display = 'none';
    document.getElementById('ann-weather-content').style.display = '';
  } catch (e) {
    toast('読み込み失敗: ' + e.message, 'err');
  }
}

function renderAnnouncements() {
  const container = document.getElementById('ann-content');
  // 表示用に時刻順へ。origIdx は annData の本物の位置。
  const rows = annData.map((item, origIdx) => ({ item, origIdx }));
  rows.sort(
    (a, b) =>
      a.item.hour * 60 + a.item.minute - (b.item.hour * 60 + b.item.minute)
  );
  container.innerHTML = rows
    .map(({ item, origIdx }) => buildAnnCard(item, origIdx))
    .join('');
  document.getElementById('ann-loading').style.display = 'none';
  document.getElementById('ann-content').style.display = '';
}

function buildAnnCard(item, origIdx) {
  const hh = String(item.hour).padStart(2, '0');
  const mm = String(item.minute).padStart(2, '0');
  const wOnly = item.weekday_only ? 'on' : '';
  const holOnly = item.holiday_only ? 'on' : '';
  const weather = item.with_weather ? 'on' : '';
  const wtTarget = item.weather_target === 'tomorrow' ? 'on' : '';

  return `
    <div class="ann-card" data-idx="${origIdx}">
      <div class="ann-card-head">
        <span class="ann-time-badge">🕐 ${hh}:${mm}</span>
        <button class="btn-danger btn-sm" onclick="removeAnnouncement(${origIdx})">削除</button>
      </div>
      <div class="grid-2" style="margin-bottom:10px">
        <div class="field">
          <label>時（0〜23）</label>
          <input type="number" min="0" max="23" value="${esc(item.hour)}"
            oninput="updateAnn(${origIdx}, 'hour', +this.value); refreshAnnTimeBadge(this, ${origIdx})">
        </div>
        <div class="field">
          <label>分（0〜59）</label>
          <input type="number" min="0" max="59" value="${esc(item.minute)}"
            oninput="updateAnn(${origIdx}, 'minute', +this.value); refreshAnnTimeBadge(this, ${origIdx})">
        </div>
      </div>
      <div class="field">
        <label>メッセージ</label>
        <textarea oninput="updateAnn(${origIdx}, 'message', this.value)">${esc(item.message)}</textarea>
      </div>
      <div class="ann-flags">
        <span class="ann-flag ${wOnly}"   onclick="toggleAnnFlag(this, ${origIdx}, 'weekday_only')" title="ON=平日にアナウンス">📅 平日</span>
        <span class="ann-flag ${holOnly}" onclick="toggleAnnFlag(this, ${origIdx}, 'holiday_only')" title="ON=土日・祝日にアナウンス">🎌 祝日</span>
        <span class="ann-flag ${weather}" onclick="toggleAnnWeather(this, ${origIdx})">☀️ 天気を追加</span>
        <span class="ann-flag ${wtTarget}" id="ann-tomorrow-${origIdx}"
          style="display:${weather ? 'inline-flex' : 'none'}"
          onclick="toggleAnnFlag(this, ${origIdx}, 'weather_target', 'tomorrow', '')">
          🌅 明日の天気
        </span>
      </div>
      <p style="margin:8px 0 0; font-size:11px; color:var(--muted);">
        平日・祝日とも OFF のときは「長期休暇」扱いでスキップします。両方 ON で毎日。
      </p>
    </div>
  `;
}

function updateAnn(origIdx, key, value) {
  if (!annData[origIdx]) return;
  if (key === 'hour') value = clampInt(value, 0, 23, 0);
  if (key === 'minute') value = clampInt(value, 0, 59, 0);
  annData[origIdx][key] = value;
}

function refreshAnnTimeBadge(input, origIdx) {
  const badge = input.closest('.ann-card').querySelector('.ann-time-badge');
  const hh = String(annData[origIdx].hour ?? 0).padStart(2, '0');
  const mm = String(annData[origIdx].minute ?? 0).padStart(2, '0');
  badge.textContent = `🕐 ${hh}:${mm}`;
}

function toggleAnnFlag(el, origIdx, key, onVal = true, offVal = false) {
  if (!annData[origIdx]) return;
  annData[origIdx][key] = el.classList.toggle('on') ? onVal : offVal;
  // 平日・祝日を両方 OFF にしたら「長期休暇」を明示（旧データの毎日扱いと区別）
  if (key === 'weekday_only' || key === 'holiday_only') {
    const w = !!annData[origIdx].weekday_only;
    const h = !!annData[origIdx].holiday_only;
    if (!w && !h) annData[origIdx]._explicit_long_vacation = true;
    else delete annData[origIdx]._explicit_long_vacation;
  }
}

function toggleAnnWeather(el, origIdx) {
  if (!annData[origIdx]) return;
  const isOn = el.classList.toggle('on');
  annData[origIdx].with_weather = isOn;
  const tb = document.getElementById(`ann-tomorrow-${origIdx}`);
  if (tb) tb.style.display = isOn ? 'inline-flex' : 'none';
  if (!isOn) {
    delete annData[origIdx].weather_target;
    if (tb) tb.classList.remove('on');
  }
}

function addAnnouncement() {
  annData.push(
    normalizeAnnItem({
      hour: 8,
      minute: 0,
      message: '',
      with_weather: false,
      weekday_only: true,
      holiday_only: true,
    })
  );
  renderAnnouncements();
  // 新規は末尾に push したので、表示上は時刻順でどこかに入る。
  // 該当カードへスクロール（data-idx = 新要素の origIdx）
  const newIdx = annData.length - 1;
  const card = document.querySelector(`.ann-card[data-idx="${newIdx}"]`);
  if (card) card.scrollIntoView({ behavior: 'smooth', block: 'center' });
}

function removeAnnouncement(origIdx) {
  if (!annData[origIdx]) return;
  if (!confirm('この定時コールを削除しますか？')) return;
  annData.splice(origIdx, 1);
  renderAnnouncements();
}

async function saveAnnouncements() {
  try {
    // 保存前に正規化（空メッセージは残す＝ユーザ判断）
    const payload = annData.map(normalizeAnnItem);
    const res = await adminFetch('/admin/api/announcements', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify(payload),
    });
    const data = await res.json();
    if (data.ok) {
      toast('✅ 保存しました（反映はサーバー再起動後）');
      // サーバが返した配列があればそれで同期
      if (Array.isArray(data.announcements)) {
        annData = data.announcements.map(normalizeAnnItem);
      } else {
        annData = payload;
      }
      renderAnnouncements();
    } else {
      toast('❌ 保存失敗: ' + data.error, 'err');
    }
  } catch (e) {
    toast('❌ 通信エラー: ' + e.message, 'err');
  }
}

async function saveWeatherScheduleEnv() {
  try {
    const res = await adminFetch('/admin/api/weather_schedule_env', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify(collectEnvValues('ann-weather-content')),
    });
    const data = await res.json();
    if (data.ok) toast('✅ 保存しました（サーバー再起動で反映）');
    else toast('❌ 保存失敗: ' + data.error, 'err');
  } catch (e) {
    toast('❌ 通信エラー: ' + e.message, 'err');
  }
}
