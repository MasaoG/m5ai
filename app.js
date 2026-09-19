/* ===========================================================================
   app.js — Web Serial と Prompt API をつなぐ本体
   ---------------------------------------------------------------------------
   HO-4 と HO-5 の内容がここに入っています。
   読むときは下の順番がおすすめです。

     1. connect()      ボードにつなぐ
     2. readLoop()     ボードから 1 行ずつ受け取る
     3. initAI()       AI を用意する
     4. decide()       推論して JSON を受け取る
     5. onTelemetry()  受け取った値をどう扱うか決める（← 改造しどころ）
   =========================================================================== */

'use strict';

/* ---------------------------------------------------------------------------
   設定
   --------------------------------------------------------------------------- */

// 実機がないときに true にすると、ダミーのセンサー値で動作確認できます。
// Pan-Tilt 待ちのあいだも開発を止めずに済みます。
const FAKE_BOARD = false;

const BAUD_RATE = 115200;

// サーボのパルス幅。HO-3 で校正した自分の値に書き換えてください。
const PULSE_MIN_US = 500;
const PULSE_MAX_US = 2400;


/* ---------------------------------------------------------------------------
   状態
   --------------------------------------------------------------------------- */

const S = {
  port: null, reader: null, writer: null,
  buf: '',
  connected: false,
  ai: null,               // ベースセッション。使うときは clone する
  aiReady: false,
  latest: { t: null, h: null },
  lastAsked: null,
  askCount: 0,
  busy: false,
  angle: 90,
  intervalId: null,
  sys: '', schema: null, opts: {}, noSchema: false,
  fnAsk: null, fnCmd: null, fnView: null,   // 画面で編集されたロジック
  lastAI: null
};

/* 画面上で編集できるロジックの初期値 */
const DEFAULT_ASK = `// 湿度が5%以上動いたときだけ呼ぶ
if (last === null) return true;
return Math.abs(d.h - last.h) >= 5;`;

const DEFAULT_VIEW = `// box に HTML を入れると、画面に出ます。
// 空文字を入れると何も表示されません。
if (!d) { box.innerHTML = ''; return; }

const level = r ? r.servo : 0;
const bars  = '\u2588'.repeat(Math.round(level / 20));

box.innerHTML = \`
  <div style="font-family:var(--mono);font-size:12px;color:#5b6167;
              border:1px solid #dfe3e1;border-radius:6px;padding:10px 12px;
              margin-top:11px">
    <div>温度 \${d.t} ℃ ／ 湿度 \${d.h} %</div>
    <div style="color:#b45309;letter-spacing:2px">\${bars}</div>
  </div>\`;`;

const DEFAULT_CMD = `// AI の返事をボードへのコマンドに変換する
const cmd = {};
if (typeof r.servo === 'number')  cmd.servo  = Math.round(r.servo);
if (typeof r.status === 'string') cmd.status = r.status;
if (typeof r.msg === 'string')    cmd.msg    = r.msg;
return cmd;`;

const $ = (id) => document.getElementById(id);


/* ===========================================================================
   1. ボードにつなぐ
   =========================================================================== */

async function connect(preferSaved = true) {
  if (FAKE_BOARD) return startFakeBoard();

  if (!('serial' in navigator)) {
    log('er', 'このブラウザは Web Serial に対応していません');
    return;
  }

  try {
    // 一度許可したポートは getPorts() で取り出せます。
    // 選択ダイアログが出ないので、リロードのたびに選び直さずに済みます。
    // （Live Server は保存のたびにリロードするため、これがないと大変です）
    let picked = null;
    if (preferSaved) {
      const known = await navigator.serial.getPorts();
      if (known.length === 1) picked = known[0];
    }

    // 許可済みがなければ選択ダイアログを出します。
    // requestPort() はクリックなどのユーザー操作からしか呼べません。
    // ページ読み込み時の自動接続はブラウザが許可しません。
    S.port = picked || await navigator.serial.requestPort();
    if (picked) log('sys', '前回のポートに再接続します');

    await S.port.open({ baudRate: BAUD_RATE });

    // DTR / RTS はボードのリセット回路につながっていることがあります。
    // 落としておかないと、接続した瞬間にボードが再起動します。
    await S.port.setSignals({ dataTerminalReady: false, requestToSend: false });

    const enc = new TextEncoderStream();
    enc.readable.pipeTo(S.port.writable).catch(() => {});
    S.writer = enc.writable.getWriter();

    const dec = new TextDecoderStream();
    S.port.readable.pipeTo(dec.writable).catch(() => {});
    S.reader = dec.readable.getReader();

    setConnected(true);
    log('sys', '接続しました');
    readLoop();

  } catch (e) {
    if (e.name === 'NotFoundError') {
      log('sys', 'ポートの選択がキャンセルされました');
    } else {
      log('er', '接続できません: ' + e.message);
      log('sys', 'IDE を切断しましたか？ ポートは 1 つのアプリしか掴めません');
    }
  }
}

async function disconnect() {
  if (S.intervalId) { clearInterval(S.intervalId); S.intervalId = null; }
  try { if (S.reader) { await S.reader.cancel(); S.reader = null; } } catch {}
  try { if (S.writer) { await S.writer.close(); S.writer = null; } } catch {}
  try { if (S.port)   { await S.port.close();   S.port = null;   } } catch {}
  setConnected(false);
  log('sys', '切断しました');
}


/* ===========================================================================
   2. 1 行ずつ受け取る

   シリアル通信では、送った 1 行がそのまま 1 回で届くとは限りません。
   途中で切れたり、2 行まとめて届いたりします。
   だから「改行が来るまで溜める」処理が必要です。
   =========================================================================== */

async function readLoop() {
  try {
    for (;;) {
      const { value, done } = await S.reader.read();
      if (done) break;

      S.buf += value;

      let i;
      while ((i = S.buf.indexOf('\n')) >= 0) {
        const line = S.buf.slice(0, i).trim();
        S.buf = S.buf.slice(i + 1);
        if (!line) continue;

        if ($('chkRaw').checked) log('sys', '  ' + line);

        if (line.startsWith('{')) {
          try {
            onTelemetry(JSON.parse(line));
          } catch {
            log('er', 'JSON として読めません: ' + line.slice(0, 60));
          }
        }
      }
    }
  } catch (e) {
    log('er', '受信が止まりました: ' + e.message);
  }
  setConnected(false);
}

/** ボードへ 1 行送る */
async function send(obj) {
  const line = JSON.stringify(obj);
  if (FAKE_BOARD) { log('tx', '→ ' + line); applyLocalAngle(obj.servo); return; }
  if (!S.writer) return;
  await S.writer.write(line + '\n');
  log('tx', '→ ' + line);
  applyLocalAngle(obj.servo);
}


/* ===========================================================================
   3. AI を用意する
   =========================================================================== */

async function initAI() {
  if (!('LanguageModel' in self)) {
    setAIStatus('err', 'API なし');
    log('er', 'Prompt API が見つかりません。Chrome のバージョンを確認してください');
    log('sys', 'file:// で開いていませんか？ https か localhost が必要です');
    return;
  }

  let availability;
  try {
    availability = await LanguageModel.availability();
  } catch (e) {
    setAIStatus('err', 'エラー');
    log('er', 'availability() に失敗: ' + e.message);
    return;
  }

  if (availability === 'unavailable') {
    setAIStatus('err', '利用不可');
    log('er', 'この端末では Gemini Nano を使えません。運営に連絡してください');
    return;
  }

  if (availability !== 'available') {
    setAIStatus('', availability);
    log('sys', 'モデルを準備しています（' + availability + '）。数分かかることがあります');
  }

  try {
    S.ai = await LanguageModel.create({
      initialPrompts: [{ role: 'system', content: S.sys }],
      temperature: S.opts.temperature,
      topK: S.opts.topK,
      monitor(m) {
        m.addEventListener('downloadprogress', (e) => {
          setAIStatus('', 'DL ' + Math.round(e.loaded * 100) + '%');
        });
      }
    });
    S.aiReady = true;
    setAIStatus('on', '準備完了');
    $('btnAsk').disabled = false;
    log('sys', 'AI の準備ができました');
  } catch (e) {
    setAIStatus('err', '作成失敗');
    log('er', 'セッションを作れません: ' + e.message);
  }
}


/* ===========================================================================
   4. 推論する

   毎回 clone() して使い捨てているのは、Gemini Nano の文脈が
   短いためです。センサー値を投げ続けると、すぐに溢れます。
   clone() は「システムプロンプトだけ設定済みの、まっさらなセッション」を
   作ってくれます。
   =========================================================================== */

async function decide(telemetry) {
  if (!S.aiReady || S.busy) return;
  S.busy = true;
  $('btnAsk').disabled = true;

  const started = performance.now();
  let session = null;

  try {
    session = await S.ai.clone();

    const userPrompt = buildUserPrompt(telemetry);
    const options = S.noSchema ? {} : { responseConstraint: S.schema };

    const raw = await session.prompt(userPrompt, options);

    S.askCount++;
    $('txtRate').textContent = S.askCount + ' 回';
    $('tagLat').textContent = Math.round(performance.now() - started) + ' ms';

    let result;
    try {
      result = JSON.parse(raw);
    } catch (e) {
      // スキーマ制約を外すと、ここに来ます。
      // これが responseConstraint を使う理由です。
      log('er', 'JSON として読めません。制約を外していませんか？');
      log('ai', raw.slice(0, 200));
      $('aiJson').textContent = raw.slice(0, 400);
      return;
    }

    renderAI(result);
    log('ai', 'AI: ' + JSON.stringify(result));

    let cmd = {};
    try {
      cmd = S.fnCmd(result) || {};
    } catch (e) {
      log('er', 'ロジックのエラー: ' + e.message);
    }
    if (Object.keys(cmd).length) await send(cmd);

  } catch (e) {
    log('er', '推論に失敗: ' + e.message);
    if (/quota|context|token/i.test(e.message)) {
      log('sys', '文脈が溢れた可能性があります。clone() しているか確認してください');
    }
  } finally {
    if (session) session.destroy();
    S.busy = false;
    $('btnAsk').disabled = !S.aiReady;
  }
}


/* ===========================================================================
   5. 受け取った値をどう扱うか ← 改造しどころ

   ここで「いつ AI を呼ぶか」を決めています。
   推論は端末の CPU / GPU を使うので、毎秒呼ぶと PC が重くなり発熱します。
   呼ぶ頻度を設計するのは、実務でもそのまま使う判断です。
   =========================================================================== */

function onTelemetry(d) {
  if (typeof d.t === 'number') { S.latest.t = d.t; $('vT').textContent = d.t.toFixed(1); }
  if (typeof d.h === 'number') { S.latest.h = d.h; $('vH').textContent = d.h.toFixed(1); }

  log('rx', '← ' + JSON.stringify(d));
  renderCustom();

  if (S.latest.t === null || S.latest.h === null) return;

  const mode = $('selMode').value;

  if (mode === 'threshold') {
    // 呼ぶ条件は画面の「ロジック」タブで編集できます
    let ask = false;
    try {
      ask = !!S.fnAsk(S.latest, S.lastAsked);
    } catch (e) {
      log('er', 'ロジックのエラー: ' + e.message);
    }
    if (ask) {
      S.lastAsked = { ...S.latest };
      decide(S.latest);
    }
  } else if (mode === 'always') {
    decide(S.latest);
  }
  // manual と interval は、ここでは呼びません
}

function setupIntervalMode() {
  if (S.intervalId) { clearInterval(S.intervalId); S.intervalId = null; }
  if ($('selMode').value === 'interval') {
    S.intervalId = setInterval(() => {
      if (S.latest.t !== null) decide(S.latest);
    }, 30000);
  }
}


/* ===========================================================================
   画面を描く
   =========================================================================== */

function renderAI(r) {
  const box = $('aiOut');
  box.innerHTML = '';

  if (r.status) {
    const st = document.createElement('div');
    st.style.cssText = 'font-family:var(--mono);font-size:11px;letter-spacing:.08em;color:var(--ai);margin-bottom:2px';
    st.textContent = r.status + ' — ボードはこの値で色を決めます';
    box.appendChild(st);
  }

  const msg = document.createElement('div');
  msg.className = 'msg';
  msg.textContent = r.msg ?? '(msg なし)';
  box.appendChild(msg);

  if (r.reason) {
    const why = document.createElement('div');
    why.className = 'why';
    why.textContent = r.reason;
    box.appendChild(why);
  }

  $('aiJson').textContent = JSON.stringify(r, null, 2);
  S.lastAI = r;
  renderCustom();
}

/** 「表示」タブで書いた関数を呼ぶ */
function renderCustom() {
  const box = $('custom');
  if (!S.fnView) return;
  try {
    S.fnView(box, S.latest.t === null ? null : S.latest, S.lastAI);
    box.style.display = box.innerHTML.trim() ? '' : 'none';
    $('viewErr').style.display = 'none';
  } catch (e) {
    $('viewErr').textContent = '表示のエラー: ' + e.message;
    $('viewErr').style.display = '';
  }
}

/** サーボに送っているパルスを描く（HO-3 の図4 と同じ内容） */
function drawScope() {
  const cv = $('scope');
  const g = cv.getContext('2d');
  const W = cv.width, H = cv.height;
  const L = 46, R = W - 16, T = 26, B = H - 34;

  g.clearRect(0, 0, W, H);

  // 目盛り: 20ms を 5ms ごとに
  g.font = '11px ui-monospace, Menlo, Consolas, monospace';
  g.textAlign = 'center';
  for (let ms = 0; ms <= 20; ms += 5) {
    const x = L + (R - L) * ms / 20;
    g.strokeStyle = '#eceeec';
    g.lineWidth = 1;
    g.beginPath(); g.moveTo(x, T); g.lineTo(x, B); g.stroke();
    g.fillStyle = '#8d949a';
    g.fillText(ms + 'ms', x, B + 20);
  }

  // 基線
  g.strokeStyle = '#dfe3e1';
  g.beginPath(); g.moveTo(L, B); g.lineTo(R, B); g.stroke();
  g.textAlign = 'right';
  g.fillStyle = '#8d949a';
  g.fillText('HIGH', L - 8, T + 5);
  g.fillText('LOW', L - 8, B + 4);

  const us = PULSE_MIN_US + (PULSE_MAX_US - PULSE_MIN_US) * S.angle / 180;
  const xEnd = L + (R - L) * (us / 1000) / 20;

  // パルス本体
  g.strokeStyle = '#b45309';
  g.lineWidth = 2.5;
  g.lineJoin = 'round';
  g.beginPath();
  g.moveTo(L, B); g.lineTo(L, T); g.lineTo(xEnd, T); g.lineTo(xEnd, B); g.lineTo(R, B);
  g.stroke();

  // 幅の塗り
  g.fillStyle = 'rgba(180,83,9,.09)';
  g.fillRect(L, T, xEnd - L, B - T);

  // 幅の寸法線
  g.strokeStyle = '#b45309';
  g.lineWidth = 1;
  g.setLineDash([3, 3]);
  g.beginPath(); g.moveTo(L, T - 12); g.lineTo(xEnd, T - 12); g.stroke();
  g.setLineDash([]);

  $('scopeUs').textContent = 'パルス幅 ' + Math.round(us) + ' µs';
  $('scopeDeg').textContent = S.angle + ' °';
}

function applyLocalAngle(deg) {
  if (typeof deg !== 'number') return;
  S.angle = Math.max(0, Math.min(180, Math.round(deg)));
  $('sld').value = S.angle;
  $('sldVal').textContent = S.angle + '°';
  drawScope();
}


/* ===========================================================================
   ログ
   =========================================================================== */

function log(kind, text) {
  const el = $('log');
  const atBottom = el.scrollHeight - el.scrollTop - el.clientHeight < 40;
  const d = document.createElement('div');
  d.className = kind;
  const t = new Date();
  const hh = String(t.getHours()).padStart(2, '0');
  const mm = String(t.getMinutes()).padStart(2, '0');
  const ss = String(t.getSeconds()).padStart(2, '0');
  d.textContent = `${hh}:${mm}:${ss}  ${text}`;
  el.appendChild(d);
  while (el.children.length > 400) el.removeChild(el.firstChild);
  if (atBottom) el.scrollTop = el.scrollHeight;
}


/* ===========================================================================
   状態表示
   =========================================================================== */

function setConnected(on) {
  S.connected = on;
  $('dotSerial').className = 'dot' + (on ? ' on' : '');
  $('txtSerial').textContent = on ? (FAKE_BOARD ? 'ダミー動作中' : BAUD_RATE + ' baud') : '未接続';
  $('btnConnect').disabled = on;
  $('btnDisconnect').disabled = !on;
  $('sld').disabled = !on;
}

function setAIStatus(cls, text) {
  $('dotAI').className = 'dot' + (cls ? ' ' + cls : '');
  $('txtAI').textContent = text;
}

function updateNet() {
  const on = navigator.onLine;
  $('dotNet').className = 'dot' + (on ? ' on' : '');
  $('txtNet').textContent = on ? 'オンライン' : 'オフライン — それでも動きます';
}


/* ===========================================================================
   設定の反映
   =========================================================================== */

function loadDefaults() {
  $('taSys').value = SYSTEM_PROMPT;
  $('taSch').value = JSON.stringify(RESPONSE_SCHEMA, null, 2);
  $('taAsk').value = DEFAULT_ASK;
  $('taCmd').value = DEFAULT_CMD;
  $('taView').value = DEFAULT_VIEW;
  $('rngTemp').value = AI_OPTIONS.temperature;
  $('rngTopK').value = AI_OPTIONS.topK;
  $('vTemp').textContent = AI_OPTIONS.temperature;
  $('vTopK').textContent = AI_OPTIONS.topK;
  $('chkNoSchema').checked = false;
  readSettings();
}

function readSettings() {
  S.sys = $('taSys').value;
  S.opts = {
    temperature: parseFloat($('rngTemp').value),
    topK: parseInt($('rngTopK').value, 10)
  };
  S.noSchema = $('chkNoSchema').checked;

  try {
    S.schema = JSON.parse($('taSch').value);
  } catch (e) {
    log('er', 'スキーマが JSON として読めません: ' + e.message);
    return false;
  }

  // 画面で書いたコードを関数にする。
  // 自分のページで自分が書いたコードを動かすだけなので、
  // ここでは new Function を使っています。
  const err = $('logicErr');
  try {
    S.fnAsk = new Function('d', 'last', $('taAsk').value);
    S.fnCmd = new Function('r', $('taCmd').value);
    S.fnView = new Function('box', 'd', 'r', $('taView').value);
    err.style.display = 'none';
  } catch (e) {
    err.textContent = 'ロジックの文法エラー: ' + e.message;
    err.style.display = '';
    log('er', 'ロジックの文法エラー: ' + e.message);
    return false;
  }

  return true;
}

async function applySettings() {
  if (!readSettings()) return;
  if (S.ai) { S.ai.destroy(); S.ai = null; }
  S.aiReady = false;
  setAIStatus('', '作り直し中');
  renderCustom();
  await initAI();
  log('sys', '設定を反映しました');
}


/* ===========================================================================
   ダミーボード（実機がないとき）
   =========================================================================== */

function startFakeBoard() {
  setConnected(true);
  $('tagSrc').textContent = 'ダミー';
  log('sys', 'ダミーモードです。実機は使っていません');
  let t = 24.0, h = 45.0;
  S.fakeId = setInterval(() => {
    t += (Math.random() - 0.5) * 0.4;
    h += (Math.random() - 0.48) * 2.2;
    h = Math.max(20, Math.min(90, h));
    onTelemetry({ t: Math.round(t * 10) / 10, h: Math.round(h * 10) / 10 });
  }, 1000);
}


/* ===========================================================================
   起動
   =========================================================================== */

function boot() {
  loadDefaults();
  drawScope();
  updateNet();
  addEventListener('online', updateNet);
  addEventListener('offline', updateNet);

  $('btnConnect').onclick = () => connect(true);
  $('btnDisconnect').onclick = disconnect;
  $('btnAsk').onclick = () => { if (S.latest.t !== null) decide(S.latest); };
  $('btnApply').onclick = applySettings;
  $('btnReset').onclick = () => { loadDefaults(); applySettings(); };
  $('btnClear').onclick = () => { $('log').innerHTML = ''; };

  $('sld').oninput = (e) => {
    const v = parseInt(e.target.value, 10);
    applyLocalAngle(v);
    send({ servo: v });
  };

  $('selMode').onchange = () => { S.lastAsked = null; setupIntervalMode(); };
  $('rngTemp').oninput = (e) => { $('vTemp').textContent = e.target.value; };
  $('rngTopK').oninput = (e) => { $('vTopK').textContent = e.target.value; };

  document.querySelectorAll('.tabs button').forEach((b) => {
    b.onclick = () => {
      document.querySelectorAll('.tabs button').forEach((x) => {
        x.setAttribute('aria-selected', String(x === b));
        $(x.dataset.pane).hidden = x !== b;
      });
    };
  });

  $('footMode').textContent = FAKE_BOARD ? 'FAKE_BOARD = true' : '';

  if (location.protocol === 'file:') {
    log('er', 'file:// で開いています。https か localhost で開き直してください');
  }

  initAI();
  tryAutoReconnect();
}

/**
 * 起動時、許可済みのポートが1つだけあれば自動で繋ぎます。
 *
 * requestPort() はユーザー操作が必要ですが、getPorts() は不要です。
 * すでに一度許可を出したポートだからです。
 * Live Server でリロードされても、接続がそのまま戻ります。
 */
async function tryAutoReconnect() {
  if (FAKE_BOARD || !('serial' in navigator)) {
    log('sys', '「ボードに接続」を押してください');
    return;
  }
  try {
    const known = await navigator.serial.getPorts();
    if (known.length === 1) {
      await connect(true);
    } else {
      log('sys', '「ボードに接続」を押してください');
    }
  } catch {
    log('sys', '「ボードに接続」を押してください');
  }
}

boot();
