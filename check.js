/* ===========================================================================
   check.js — 環境診断
   ---------------------------------------------------------------------------
   実機検証と HO-0 で使います。
   手順書 付録C の空欄を埋めるための数値が、そのまま採れる構成です。
   =========================================================================== */

'use strict';

const $ = (id) => document.getElementById(id);
const R = {};                       // 結果を貯める

/* --- 表示ヘルパ ---------------------------------------------------------- */

function row(tblId, label, value, state) {
  const tb = $(tblId).querySelector('tbody');
  let tr = [...tb.rows].find((x) => x.dataset.k === label);
  if (!tr) {
    tr = tb.insertRow();
    tr.dataset.k = label;
    tr.insertCell();
    tr.insertCell();
    tr.cells[0].textContent = label;
  }
  const cls = { ok: 't-ok', ng: 't-ng', warn: 't-wn', info: 't-na' }[state] || 't-na';
  const tag = { ok: 'OK', ng: 'NG', warn: '注意', info: '参考' }[state] || '—';
  tr.cells[1].innerHTML = '';
  const b = document.createElement('span');
  b.className = 'tag ' + cls;
  b.textContent = tag;
  tr.cells[1].appendChild(b);
  tr.cells[1].appendChild(document.createTextNode(value));
}

function log(kind, text) {
  const el = $('log');
  const bottom = el.scrollHeight - el.scrollTop - el.clientHeight < 40;
  const d = document.createElement('div');
  d.className = kind;
  const t = new Date();
  d.textContent = [t.getHours(), t.getMinutes(), t.getSeconds()]
    .map((n) => String(n).padStart(2, '0')).join(':') + '  ' + text;
  el.appendChild(d);
  while (el.children.length > 300) el.removeChild(el.firstChild);
  if (bottom) el.scrollTop = el.scrollHeight;
}

/* ===========================================================================
   1. ブラウザーと端末
   =========================================================================== */

function checkEnv() {
  const ua = navigator.userAgent;
  const m = ua.match(/Chrome\/(\d+)/);
  const ver = m ? parseInt(m[1], 10) : 0;
  R.chrome = ver;
  row('tblEnv', 'Chrome バージョン', ver ? String(ver) : '不明',
      ver >= 148 ? 'ok' : 'ng');

  // Chromium 系の別ブラウザーを弾く
  const isEdge = /Edg\//.test(ua);
  const isBrave = !!navigator.brave;
  const isOpera = /OPR\//.test(ua);
  const other = isEdge ? 'Edge' : isBrave ? 'Brave' : isOpera ? 'Opera' : null;
  R.browser = other || 'Chrome';
  row('tblEnv', 'ブラウザー', other ? other + '（Chrome 公式ビルドが必要）' : 'Chrome',
      other ? 'ng' : 'ok');

  R.secure = isSecureContext;
  row('tblEnv', 'セキュアコンテキスト',
      location.protocol + '//' + location.host + (isSecureContext ? '' : ' ← https か localhost が必要'),
      isSecureContext ? 'ok' : 'ng');

  R.serialApi = 'serial' in navigator;
  row('tblEnv', 'Web Serial API', R.serialApi ? '利用できます' : '見つかりません',
      R.serialApi ? 'ok' : 'ng');

  R.lmApi = 'LanguageModel' in self;
  row('tblEnv', 'Prompt API', R.lmApi ? '利用できます' : '見つかりません',
      R.lmApi ? 'ok' : 'ng');

  R.cores = navigator.hardwareConcurrency || 0;
  row('tblEnv', '論理コア数', String(R.cores), R.cores >= 4 ? 'ok' : 'warn');

  // deviceMemory は概算値。Chrome は 8 で頭打ちになるため参考程度。
  const dm = navigator.deviceMemory;
  R.mem = dm || null;
  row('tblEnv', 'メモリ（概算）',
      (dm ? dm + 'GB 以上' : '取得できません') +
      ' — ブラウザーからは正確に取れません。端末の設定画面で確認してください', 'info');

  navigator.storage?.estimate?.().then((e) => {
    const gb = e.quota ? (e.quota / 1073741824).toFixed(1) : '?';
    R.quota = gb;
    row('tblEnv', 'ストレージ割当',
        gb + 'GB — 実際の空き容量とは異なります。エクスプローラで確認してください', 'info');
  }).catch(() => {});

  R.online = navigator.onLine;
  row('tblEnv', 'ネットワーク', R.online ? 'オンライン' : 'オフライン', 'info');
}

/* ===========================================================================
   2. Gemini Nano の状態
   =========================================================================== */

async function checkAI(startDownload) {
  if (!('LanguageModel' in self)) {
    row('tblAI', 'availability()', 'Prompt API がありません', 'ng');
    return;
  }

  let a;
  try {
    a = await LanguageModel.availability();
  } catch (e) {
    row('tblAI', 'availability()', 'エラー: ' + e.message, 'ng');
    return;
  }

  R.availability = a;
  row('tblAI', 'availability()', a,
      a === 'available' ? 'ok' : a === 'unavailable' ? 'ng' : 'warn');

  if (a === 'unavailable') {
    row('tblAI', '判定', 'この端末では使えません。交換してください', 'ng');
    return;
  }
  if (!startDownload && a !== 'available') {
    row('tblAI', '判定',
        '「ダウンロードを開始」を押してください（約4GB・数分かかります）', 'warn');
    row('tblAI', '補足',
        'Chrome やモデルの更新後は downloadable に戻ることがあります', 'info');
    return;
  }

  try {
    const t0 = performance.now();
    const s = await LanguageModel.create({
      monitor(mo) {
        mo.addEventListener('downloadprogress', (e) => {
          const pct = Math.round(e.loaded * 100);
          // 100% になったら「注意」ではなく OK にする
          row('tblAI', 'ダウンロード', pct + '%',
              pct >= 100 ? 'ok' : 'warn');
        });
      }
    });
    const ms = Math.round(performance.now() - t0);
    R.createMs = ms;
    row('tblAI', 'セッション作成', ms + ' ms', 'ok');

    // 使えるパラメーターの上限を控えておく
    if (typeof s.inputQuota === 'number') {
      R.inputQuota = s.inputQuota;
      row('tblAI', 'inputQuota', String(s.inputQuota), 'na');
    }
    try {
      const p = await LanguageModel.params();
      R.params = p;
      row('tblAI', 'temperature 上限', String(p.maxTemperature ?? '—'), 'na');
      row('tblAI', 'topK 上限', String(p.maxTopK ?? '—'), 'na');
    } catch {}

    s.destroy();

    // ダウンロード後は状態が変わっているので取り直す。
    // これをしないと上の行が downloadable のまま残ります。
    try {
      const after = await LanguageModel.availability();
      R.availability = after;
      row('tblAI', 'availability()', after,
          after === 'available' ? 'ok' : 'warn');
      row('tblAI', '判定',
          after === 'available' ? '準備できています'
                                : '「ダウンロードを開始」をもう一度押してください',
          after === 'available' ? 'ok' : 'warn');
    } catch {
      row('tblAI', '判定', '準備できています', 'ok');
    }
  } catch (e) {
    row('tblAI', 'セッション作成', 'エラー: ' + e.message, 'ng');
  }
}

/* ===========================================================================
   3. 推論の速さ
   =========================================================================== */

const SPEED_SYSTEM = 'あなたは室内環境アドバイザーです。簡潔に答えてください。';
const SPEED_SCHEMA = {
  type: 'object',
  properties: {
    servo: { type: 'integer', minimum: 0, maximum: 180 },
    msg: { type: 'string', maxLength: 24 }
  },
  required: ['servo', 'msg'],
  additionalProperties: false
};

async function checkSpeed() {
  if (!('LanguageModel' in self)) return;
  $('btnSpeed').disabled = true;
  row('tblSpeed', '計測', '実行中…', 'na');

  try {
    const base = await LanguageModel.create({
      initialPrompts: [{ role: 'system', content: SPEED_SYSTEM }],
      temperature: 0.2, topK: 3
    });

    const times = [];
    for (let i = 0; i < 3; i++) {
      const s = await base.clone();
      const t0 = performance.now();
      await s.prompt(`温度 ${24 + i}℃ 湿度 ${45 + i * 7}%`,
                     { responseConstraint: SPEED_SCHEMA });
      times.push(Math.round(performance.now() - t0));
      s.destroy();
      row('tblSpeed', '計測', `${i + 1} / 3 回目`, 'na');
    }
    base.destroy();

    const avg = Math.round(times.reduce((a, b) => a + b) / times.length);
    R.speed = times;
    R.speedAvg = avg;

    row('tblSpeed', '計測', '完了', 'ok');
    row('tblSpeed', '各回', times.join(' / ') + ' ms', 'na');
    row('tblSpeed', '平均', avg + ' ms', avg < 3000 ? 'ok' : 'warn');
    row('tblSpeed', '1秒ごとに呼べるか',
        avg < 1000 ? '可能だが負荷が高い' : '無理。呼ぶ頻度を落とす設計が必要',
        avg < 1000 ? 'warn' : 'ng');
  } catch (e) {
    row('tblSpeed', '計測', 'エラー: ' + e.message, 'ng');
  }
  $('btnSpeed').disabled = false;
}

/* ===========================================================================
   4. スキーマ制約の効果

   HO-5 の教材として最も効く比較です。
   「制約なし」の実際の出力を控えておいてください。
   =========================================================================== */

async function checkSchema() {
  if (!('LanguageModel' in self)) return;
  $('btnSchema').disabled = true;
  $('outFree').textContent = '実行中…';
  $('outCons').textContent = '待機中';

  const sys = `あなたは室内環境アドバイザーです。
温度と湿度から換気の必要度を判断し、
必要度を 0（不要）〜180（至急）の整数 servo で表し、
24文字以内の日本語コメントを msg に入れてください。`;

  try {
    const base = await LanguageModel.create({
      initialPrompts: [{ role: 'system', content: sys }],
      temperature: 0.2, topK: 3
    });
    const q = '温度 28℃ 湿度 72%';

    // --- 制約なし ---
    const s1 = await base.clone();
    const free = await s1.prompt(q);
    s1.destroy();
    R.free = free;

    let parseOk = true;
    try { JSON.parse(free); } catch { parseOk = false; }
    R.freeParse = parseOk;
    $('outFree').textContent =
      free + '\n\n--- JSON.parse: ' + (parseOk ? '成功' : '失敗 ←') + ' ---';

    // --- 制約あり ---
    $('outCons').textContent = '実行中…';
    const s2 = await base.clone();
    const cons = await s2.prompt(q, { responseConstraint: SPEED_SCHEMA });
    s2.destroy();
    R.cons = cons;

    let ok2 = true, parsed = null;
    try { parsed = JSON.parse(cons); } catch { ok2 = false; }
    R.consParse = ok2;
    $('outCons').textContent =
      cons + '\n\n--- JSON.parse: ' + (ok2 ? '成功' : '失敗') + ' ---' +
      (parsed ? `\nservo = ${parsed.servo}（0〜180 の範囲内）` : '');

    base.destroy();
  } catch (e) {
    $('outFree').textContent = 'エラー: ' + e.message;
  }
  $('btnSchema').disabled = false;
}

/* ===========================================================================
   5. 文脈があふれるまでの回数
   =========================================================================== */

async function checkContext() {
  if (!('LanguageModel' in self)) return;
  $('btnCtx').disabled = true;
  row('tblCtx', '結果', '実行中…', 'na');

  const MAX = 30;
  try {
    const s = await LanguageModel.create({
      initialPrompts: [{ role: 'system', content: SPEED_SYSTEM }],
      temperature: 0.2, topK: 3
    });

    let n = 0, err = null;
    for (; n < MAX; n++) {
      $('ctxProg').textContent = `${n + 1} / ${MAX} 回目`;
      try {
        await s.prompt(`温度 ${20 + (n % 10)}℃ 湿度 ${40 + (n % 30)}%`,
                       { responseConstraint: SPEED_SCHEMA });
      } catch (e) {
        err = e.message;
        break;
      }
      if (typeof s.inputUsage === 'number' && typeof s.inputQuota === 'number') {
        row('tblCtx', '使用量', `${s.inputUsage} / ${s.inputQuota}`, 'na');
      }
    }

    R.ctxCount = n;
    R.ctxErr = err;
    $('ctxProg').textContent = '';

    if (err) {
      row('tblCtx', '結果', `${n + 1} 回目で失敗`, 'warn');
      row('tblCtx', 'エラー', err, 'na');
      row('tblCtx', '教材への反映', 'この回数を HO-5 の説明で使えます', 'ok');
    } else {
      row('tblCtx', '結果', `${MAX} 回とも成功（打ち切り）`, 'ok');
      row('tblCtx', '教材への反映',
          '短いプロンプトなら余裕あり。長文を投げる設計だと溢れます', 'na');
    }
    s.destroy();
  } catch (e) {
    row('tblCtx', '結果', 'エラー: ' + e.message, 'ng');
    $('ctxProg').textContent = '';
  }
  $('btnCtx').disabled = false;
}

/* ===========================================================================
   6. ボードとの通信
   =========================================================================== */

let port = null, reader = null, writer = null, buf = '';
let rxLines = 0, rxJson = 0, firstRx = null;

async function connect() {
  if (!('serial' in navigator)) { log('er', 'Web Serial がありません'); return; }
  try {
    // 許可済みポートがあれば選択ダイアログを省略
    const known = await navigator.serial.getPorts();
    port = known.length === 1 ? known[0] : await navigator.serial.requestPort();
    await port.open({ baudRate: 115200 });
    await port.setSignals({ dataTerminalReady: false, requestToSend: false });

    const enc = new TextEncoderStream();
    enc.readable.pipeTo(port.writable).catch(() => {});
    writer = enc.writable.getWriter();

    const dec = new TextDecoderStream();
    port.readable.pipeTo(dec.writable).catch(() => {});
    reader = dec.readable.getReader();

    $('btnConn').disabled = true;
    $('btnDisc').disabled = false;
    $('btnPing').disabled = false;
    row('tblSer', '接続', '115200 baud', 'ok');
    log('sys', '接続しました');
    readLoop();
  } catch (e) {
    if (e.name === 'NotFoundError') { log('sys', 'キャンセルされました'); return; }
    log('er', e.message);
    log('sys', 'IDE を Disconnect しましたか');
    row('tblSer', '接続', e.message, 'ng');
  }
}

async function readLoop() {
  try {
    for (;;) {
      const { value, done } = await reader.read();
      if (done) break;
      buf += value;
      let i;
      while ((i = buf.indexOf('\n')) >= 0) {
        const line = buf.slice(0, i).trim();
        buf = buf.slice(i + 1);
        if (!line) continue;
        rxLines++;
        if (firstRx === null) {
          firstRx = performance.now();
          row('tblSer', '初回受信', '届いています', 'ok');
        }
        if (line.startsWith('{')) {
          try { JSON.parse(line); rxJson++; } catch {}
        }
        log('rx', '← ' + line);
        row('tblSer', '受信行数', `${rxLines} 行（うち JSON ${rxJson} 件）`, 'ok');
      }
    }
  } catch (e) {
    log('er', '受信停止: ' + e.message);
  }
}

async function disconnect() {
  try { await reader?.cancel(); } catch {}
  try { await writer?.close(); } catch {}
  try { await port?.close(); } catch {}
  reader = writer = port = null;
  $('btnConn').disabled = false;
  $('btnDisc').disabled = true;
  $('btnPing').disabled = true;
  log('sys', '切断しました');
}

async function ping() {
  if (!writer) return;
  for (const deg of [0, 90, 180, 90]) {
    const line = JSON.stringify({ servo: deg, status: 'GOOD', msg: 'テスト' });
    await writer.write(line + '\n');
    log('sys', '→ ' + line);
    await new Promise((r) => setTimeout(r, 700));
  }
  row('tblSer', '送信', '4件（サーボが動けば OK）', 'ok');
  R.txOk = true;
}

/* ===========================================================================
   7. 記録用の出力
   =========================================================================== */

function generate() {
  const d = new Date().toLocaleString('ja-JP');
  const L = [];
  L.push('## 環境診断の結果');
  L.push('');
  L.push('実施日時: ' + d);
  L.push('端末名  : ');
  L.push('');
  L.push('### ブラウザーと端末');
  L.push('');
  L.push('| 項目 | 値 |');
  L.push('|---|---|');
  L.push(`| Chrome バージョン | ${R.chrome ?? '—'} |`);
  L.push(`| ブラウザー | ${R.browser ?? '—'} |`);
  L.push(`| セキュアコンテキスト | ${R.secure ? 'はい' : 'いいえ'} |`);
  L.push(`| Web Serial | ${R.serialApi ? '利用可' : '不可'} |`);
  L.push(`| Prompt API | ${R.lmApi ? '利用可' : '不可'} |`);
  L.push(`| 論理コア数 | ${R.cores ?? '—'} |`);
  L.push(`| メモリ（概算） | ${R.mem ? R.mem + 'GB 以上' : '—'} |`);
  L.push('');
  L.push('### Gemini Nano');
  L.push('');
  L.push('| 項目 | 値 |');
  L.push('|---|---|');
  L.push(`| availability() | ${R.availability ?? '—'} |`);
  L.push(`| セッション作成 | ${R.createMs ? R.createMs + ' ms' : '—'} |`);
  L.push(`| inputQuota | ${R.inputQuota ?? '—'} |`);
  L.push(`| temperature 上限 | ${R.params?.maxTemperature ?? '—'} |`);
  L.push(`| topK 上限 | ${R.params?.maxTopK ?? '—'} |`);
  L.push('');
  L.push('### 推論の速さ');
  L.push('');
  L.push(`- 各回: ${R.speed ? R.speed.join(' / ') + ' ms' : '未計測'}`);
  L.push(`- 平均: ${R.speedAvg ? R.speedAvg + ' ms' : '未計測'}`);
  L.push('');
  L.push('### スキーマ制約の効果');
  L.push('');
  L.push('**制約なしの出力（HO-5 の説明で使う）**');
  L.push('');
  L.push('```');
  L.push(R.free ?? '未実行');
  L.push('```');
  L.push('');
  L.push(`JSON.parse: ${R.free ? (R.freeParse ? '成功' : '**失敗**') : '—'}`);
  L.push('');
  L.push('**制約ありの出力**');
  L.push('');
  L.push('```');
  L.push(R.cons ?? '未実行');
  L.push('```');
  L.push('');
  L.push(`JSON.parse: ${R.cons ? (R.consParse ? '成功' : '失敗') : '—'}`);
  L.push('');
  L.push('### 文脈があふれるまで');
  L.push('');
  if (R.ctxCount === undefined) L.push('- 未計測');
  else if (R.ctxErr) L.push(`- ${R.ctxCount + 1} 回目で失敗: ${R.ctxErr}`);
  else L.push('- 30回とも成功（打ち切り）');
  L.push('');
  L.push('### ボードとの通信');
  L.push('');
  L.push(`- 受信: ${rxLines} 行（うち JSON ${rxJson} 件）`);
  L.push(`- 送信: ${R.txOk ? 'サーボが応答' : '未実行'}`);
  L.push('');
  L.push('### 手順書 付録C へ転記する数値');
  L.push('');
  L.push('| 項目 | 実測値 |');
  L.push('|---|---|');
  L.push('| SG92R PULSE_MIN_US | |');
  L.push('| SG92R PULSE_MAX_US | |');
  L.push('| i2c.scan() の実出力 | |');
  L.push(`| 推論1回の平均 | ${R.speedAvg ? R.speedAvg + ' ms' : ''} |`);
  L.push(`| 文脈があふれる回数 | ${R.ctxErr ? R.ctxCount + 1 : '30回超'} |`);
  L.push('| LED 白100% の発熱 | |');
  L.push('');

  $('report').value = L.join('\n');
}

/* ===========================================================================
   起動
   =========================================================================== */

$('btnAI').onclick = () => checkAI(false);
$('btnDL').onclick = () => checkAI(true);
$('btnSpeed').onclick = checkSpeed;
$('btnSchema').onclick = checkSchema;
$('btnCtx').onclick = checkContext;
$('btnConn').onclick = connect;
$('btnDisc').onclick = disconnect;
$('btnPing').onclick = ping;
$('btnGen').onclick = generate;
$('btnCopy').onclick = async () => {
  if (!$('report').value) generate();
  try {
    await navigator.clipboard.writeText($('report').value);
    $('btnCopy').textContent = 'コピーしました';
    setTimeout(() => ($('btnCopy').textContent = 'コピー'), 1600);
  } catch {
    $('report').select();
  }
};

$('btnAll').onclick = async () => {
  $('btnAll').disabled = true;
  checkEnv();
  await checkAI(false);
  await checkSpeed();
  await checkSchema();
  await checkContext();
  generate();
  $('btnAll').disabled = false;
};

checkEnv();
log('sys', 'ボードを繋ぐ場合は、先に IDE を Disconnect してください');
