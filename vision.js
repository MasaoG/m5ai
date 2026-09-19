/* ===========================================================================
   vision.js — 発展課題: カメラの映像を AI に見せる
   ---------------------------------------------------------------------------
   中間レビューを通過したチームだけ着手してください。
   MVP が動く前に手を出すと、まず間に合いません。

   注意: Pan-Tilt に付属しているカメラは Raspberry Pi 用（CSI 接続）で、
         Core2 では使えません。ここでは PC の内蔵 Web カメラを使います。

   この構成には大きな意味があります。
   カメラの映像が一切ネットワークに出ません。
   「なぜクラウドではなく端末内の AI なのか」に対する、
   いちばん強い答えになります。

   ---------------------------------------------------------------------------
   使いかた

     index.html の </body> の直前に 1 行足します。

       <script src="vision.js"></script>

     そのあと、ブラウザーのコンソールで試せます。

       await Vision.start();              // カメラを起動
       await Vision.ask('何が見えますか'); // 1 フレーム撮って AI に渡す
       Vision.stop();                     // カメラを止める
   =========================================================================== */

'use strict';

const Vision = (() => {

  let stream = null;
  let video = null;
  let session = null;

  /* -------------------------------------------------------------------------
     出力スキーマ

     画像でも、返り値を JSON に固定できます。
     考え方は HO-5 とまったく同じです。
     ------------------------------------------------------------------------- */

  const VISION_SCHEMA = {
    type: 'object',
    properties: {
      present: { type: 'boolean' },                              // 人がいるか
      count:   { type: 'integer', minimum: 0, maximum: 10 },      // 何人か
      msg:     { type: 'string',  maxLength: 24 }
    },
    required: ['present', 'msg'],
    additionalProperties: false
  };

  const VISION_SYSTEM = `あなたは画像を見て状況を判断するアシスタントです。
見えたものを簡潔に日本語で答えてください。
推測できない場合は present を false にしてください。`;


  /* -------------------------------------------------------------------------
     カメラを起動する

     getUserMedia() もクリックなどのユーザー操作が起点でないと
     許可されません。Web Serial と同じ権限モデルです。
     ------------------------------------------------------------------------- */

  async function start() {
    if (stream) return;

    stream = await navigator.mediaDevices.getUserMedia({
      video: { width: 640, height: 480 },
      audio: false
    });

    video = document.createElement('video');
    video.srcObject = stream;
    video.playsInline = true;
    video.muted = true;
    await video.play();

    console.log('カメラを起動しました');
  }

  function stop() {
    if (stream) {
      stream.getTracks().forEach((t) => t.stop());
      stream = null;
      video = null;
      console.log('カメラを止めました');
    }
    if (session) { session.destroy(); session = null; }
  }


  /* -------------------------------------------------------------------------
     1 フレームを取り出す

     video からそのままは渡せないので、canvas に描いてから
     Blob にします。
     ------------------------------------------------------------------------- */

  async function capture() {
    if (!video) throw new Error('先に Vision.start() を呼んでください');

    const cv = document.createElement('canvas');
    cv.width = video.videoWidth;
    cv.height = video.videoHeight;
    cv.getContext('2d').drawImage(video, 0, 0);

    return new Promise((resolve) => {
      cv.toBlob(resolve, 'image/jpeg', 0.85);
    });
  }


  /* -------------------------------------------------------------------------
     AI に画像を見せる

     expectedInputs で「画像を渡します」と宣言してから
     セッションを作るのがポイントです。
     ------------------------------------------------------------------------- */

  async function ask(question = 'この画像に人はいますか') {
    if (!('LanguageModel' in self)) throw new Error('Prompt API がありません');

    if (!session) {
      session = await LanguageModel.create({
        initialPrompts: [{ role: 'system', content: VISION_SYSTEM }],
        expectedInputs: [{ type: 'image' }],
        temperature: 0.2,
        topK: 3
      });
    }

    const blob = await capture();
    const s = await session.clone();

    try {
      const raw = await s.prompt(
        [{
          role: 'user',
          content: [
            { type: 'image', value: blob },
            { type: 'text',  value: question }
          ]
        }],
        { responseConstraint: VISION_SCHEMA }
      );

      const result = JSON.parse(raw);
      console.log(result);
      return result;

    } finally {
      s.destroy();
    }
  }


  return { start, stop, capture, ask };

})();


/* ===========================================================================
   組み合わせのアイデア

   ■ 人がいたら Pan-Tilt をそちらに向ける
     画像から方向を判断させ、pan / tilt を返させます。
     スキーマの minimum / maximum に、実機で測ったリミット値を
     入れるのを忘れないでください。

   ■ 人がいなくなったら省エネモードにする
     カメラを常時回すのではなく、一定間隔で 1 枚だけ撮ります。
     推論の頻度を落とす設計（HO-5）がそのまま効きます。

   ■ プライバシーを説明の軸にする
     発表で「なぜクラウドではないのか」を問われたとき、
     カメラ映像が端末から出ない構成は、それ自体が答えになります。

   注意点

   ・画像の推論はテキストより時間がかかります。毎秒は無理です。
   ・Gemini Nano は細かい識別が得意ではありません。
     「誰か」ではなく「人がいるか」くらいの粒度で設計してください。
   ・カメラの許可ダイアログは 1 回で済みます。拒否した場合は
     アドレスバーの左側から権限を戻せます。
   =========================================================================== */
