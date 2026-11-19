# Plan B  しるべナノ（AI指差しロボット） ／ ボード側
#
# このファイルを main.py として保存してください。
#
# ---------------------------------------------------------------------------
# 使いかた（順番を守ること）
#
#   1. このコードを main.py として保存する
#   2. ボードをリセットする
#   3. IDE の「Disconnect」を押す        ← ここを忘れると次で失敗します
#   4. ブラウザーのページで「ボードに接続」を押す
#
#   消すときは Web Terminal → File → /flash の main.py を Delete。
#   ★boot.py は消さないこと★
#
# ---------------------------------------------------------------------------
# プロトコル（1行に1つの JSON = JSON Lines）
#
#   PC → ボード:  {"pan":120,"tilt":95,"msg":"あちらです"}
#   ボード → PC:  {"pan":120,"tilt":95,"tp":150,"tt":90,"btn":0,"drop":0}
#
#   pan / tilt … いま実際に向いている角度（現在値）
#   tp  / tt   … いま目指している角度（指令値）
#   drop       … 受け取りそこねた行の累計。0 以外ならどこかが詰まっています
#
#   現在値と指令値の両方を返すので、ページ側で「指示」と「実際」を
#   並べて出せます。ズレていても、それが追従中なのか取りこぼしなのかが
#   すぐ分かります。
# ---------------------------------------------------------------------------

import os, sys, io
import M5
from M5 import *
import m5ui
import lvgl as lv
from hardware import RGB
from machine import Pin, PWM
import select
import json
import time


# ===========================================================================
# 校正値（2026/10/25 実測）
# ===========================================================================
#
#   planb_calib.py の出力をそのまま貼ってあります。

PAN_PIN = 26            # PORT.B の白
TILT_PIN = 14           # PORT.C の白  ★実測で確定★

PULSE_MIN_US = 600      # 0 度
PULSE_MAX_US = 2400     # 180 度

# ★可動範囲のリミット★
#
#   2026/10/25 に planb_range.py で確認済み。
#   組み上がった状態で 0〜180 度を往復させ、鳴きも引っかかりも
#   ありませんでした。つまりブラケットは全域を許容します。
#
#   それでも「使う範囲」を絞りたいときは、ここを狭めてください。
#   真下や真上を指しても意味がないので、デモでは絞るほうが見栄えがします。
#
#   ★狭めたら、ブラウザー側のスキーマの minimum / maximum も
#     同じ値にすること。片方だけでは意味がありません。★
PAN_LIMIT = (0, 180)
TILT_LIMIT = (0, 180)

HOME_PAN = 90
HOME_TILT = 90

SPEED_DPS = 125         # 動く速さ（度／秒）。大きいと速いが突入電流が増えます
                        #
                        # ★2026/10/28 に「1 周あたり何度」から
                        #   「1 秒あたり何度」に変えました★
                        #   1 周にかかる時間は、画面を描くかどうかで
                        #   10ms〜60ms と揺れます。1 周あたりで決めると
                        #   速さまで揺れてしまい、同じ指示でも到着時刻が
                        #   変わります。経過時間で決めれば一定になります。


page0 = None
lblMsg = None
lblPos = None
rgb25 = None
pan = None
tilt = None

_cur = {'pan': float(HOME_PAN), 'tilt': float(HOME_TILT)}
_tgt = {'pan': float(HOME_PAN), 'tilt': float(HOME_TILT)}


# === サーボ =================================================================

def deg_to_us(deg):
    return PULSE_MIN_US + (PULSE_MAX_US - PULSE_MIN_US) * deg / 180


def write_deg(servo, deg):
    servo.duty_ns(int(deg_to_us(deg) * 1000))


def clamp(deg, limit):
    return max(limit[0], min(limit[1], deg))


def set_target(p=None, t=None):
    """目標を決めるだけ。実際に動かすのは step_toward()。"""
    if p is not None:
        _tgt['pan'] = clamp(float(p), PAN_LIMIT)
    if t is not None:
        _tgt['tilt'] = clamp(float(t), TILT_LIMIT)


_last_step = 0


def step_toward():
    """目標に向かって、前回からの経過時間ぶんだけ動かす。

    ★ここが設計の要です★
    HO-3 では angle() の中で while ループを回して動かしきっていました。
    それだと動いている間シリアルを読めません。
    1 回の loop で少しだけ動かせば、通信が止まりません。
    """
    global _last_step
    now = time.ticks_ms()
    dt = time.ticks_diff(now, _last_step)
    _last_step = now
    if dt <= 0 or dt > 200:         # 初回や、長く止まっていた直後は飛ばす
        return False

    step = SPEED_DPS * dt / 1000.0

    moved = False
    for key, servo in (('pan', pan), ('tilt', tilt)):
        c = _cur[key]
        t = _tgt[key]
        d = t - c
        if abs(d) < 0.01:
            continue
        if abs(d) <= step:
            c = t
        else:
            c += step if d > 0 else -step
        _cur[key] = c
        write_deg(servo, c)
        moved = True
    return moved


# === LED バー ===============================================================
#
# パンの向きを光の位置で表します。
# 画面を見なくても、どちらを向いているかが分かります。

def led_pan(deg):
    span = PAN_LIMIT[1] - PAN_LIMIT[0]
    if span <= 0:
        return
    pos = int((deg - PAN_LIMIT[0]) * 9 / span + 0.5)
    pos = max(0, min(9, pos))
    for i in range(10):
        rgb25.set_color(i, 0x00a0ff if i == pos else 0x000000)


# === 画面 ===================================================================

def show_msg(msg):
    if msg:
        lblMsg.set_text(str(msg))


def show_pos():
    lblPos.set_text(str('PAN {:.0f}   TILT {:.0f}'.format(
        _cur['pan'], _cur['tilt'])))


# === setup ==================================================================

def setup():
    global page0, lblMsg, lblPos, rgb25, pan, tilt

    M5.begin()
    Widgets.setRotation(1)
    m5ui.init()

    page0 = m5ui.M5Page(bg_c=0x000000)

    # 日本語が使えるのは AlibabaSans_JP24 のみ（サイズ24固定）。
    # 画面幅の都合で、14文字くらいまでが安全です。
    lblMsg = m5ui.M5Label("せつぞくしてね", x=15, y=60,
                          text_c=0xffffff, bg_c=0x000000, bg_opa=0,
                          font=lv.AlibabaSans_JP24, parent=page0)
    lblPos = m5ui.M5Label("PAN --   TILT --", x=15, y=150,
                          text_c=0xff8c00, bg_c=0x000000, bg_opa=0,
                          font=lv.AlibabaSans_JP24, parent=page0)

    # ★これを呼ばないと画面に何も表示されません★
    page0.screen_load()

    rgb25 = RGB(io=25, n=10, type="SK6812")
    rgb25.set_brightness(12)
    rgb25.fill_color(0x003c00)

    pan = PWM(Pin(PAN_PIN), freq=50)
    tilt = PWM(Pin(TILT_PIN), freq=50)

    # まず正面を向く
    write_deg(pan, HOME_PAN)
    write_deg(tilt, HOME_TILT)
    show_pos()
    led_pan(HOME_PAN)


# === シリアル受信 ===========================================================
#
# ★ここは 2026/10/28 に作り直しました★
#
# 前の版は sys.stdin.readline() を直接呼んでいました。穴が2つありました。
#
#   穴1 ... readline() は改行が来るまで「待つ」
#           日本語の msg が入ると1行が 70 バイトを超えます。USB の 1 回の
#           転送は 64 バイトなので、行が2つに割れて届きます。後半が届くまで
#           loop 全体が止まり、その間サーボも画面も報告も止まります。
#
#   穴2 ... 1 周で 1 行しか読まない
#           1 周は 20ms、つまり 1 秒に 50 行しか捌けません。ページ側の
#           スライダーは 1 秒に 60 行以上送ってきます。送るほうが速いので
#           受信バッファが溢れ、溢れた分はバイト単位で黙って消えます。
#           消えた行は JSON として壊れるので except に吸い込まれ、
#           ボードは古い目標のまま、ページは新しい値を表示したままになります。
#           ★「画面の数字と実際の向きが違う」のは、これが原因です★
#
# 直しかた ... 1 文字ずつ「いま届いている分だけ」読んで、改行まで自分で
#              溜めます。待たないので止まりません。溜まった行はその周の
#              うちに全部処理します。

poller = select.poll()
poller.register(sys.stdin, select.POLLIN)

_rx = ''                # 改行までの途中の行
_dropped = 0            # 読めなかった行の累計（ページに報告します）

MAX_READ_PER_LOOP = 1024    # 1 周で読む文字数の上限（残りは次の周で読む）


def read_lines():
    """いま届いている分だけ読んで、できあがった行のリストを返す。待たない。"""
    global _rx
    out = []
    for _ in range(MAX_READ_PER_LOOP):
        if not poller.poll(0):          # 0 = 待たずにすぐ返る
            break
        ch = sys.stdin.read(1)
        if not ch:
            break
        if ch == '\n':
            line = _rx.strip()
            _rx = ''
            if line:
                out.append(line)
        elif ch != '\r':
            _rx += ch
            if len(_rx) > 400:          # 改行が来ないまま伸び続けたら捨てる
                _rx = ''
    return out


# === メインループ ===========================================================

_last_send = 0
_last_draw = 0
_moving = False
_report_now = False

SEND_INTERVAL_MS = 500      # 動いていなくても、この間隔では必ず報告する
DRAW_INTERVAL_MS = 120      # 画面と LED の書き換え間隔（下のコメント参照）


def loop():
    global _last_send, _last_draw, _moving, _report_now, _dropped

    M5.update()

    # --- 届いている分を、まとめて処理する ----------------------------------
    #
    # 連続して届いた pan / tilt は、最後の1つだけが意味を持ちます。
    # 途中の値に順番に追いつく必要はないので、畳んでしまいます。
    # これでスライダーを速く動かしても溜まりません。
    last_p = None
    last_t = None
    last_msg = None

    for line in read_lines():
        try:
            cmd = json.loads(line)
        except Exception:
            # 壊れた行が来ても止まらない。ただし黙って消さず、数えて報告する
            _dropped += 1
            _report_now = True
            continue

        if cmd.get('home'):                 # 正面に戻す
            last_p, last_t = HOME_PAN, HOME_TILT
        if 'pan' in cmd:
            last_p = cmd['pan']
        if 'tilt' in cmd:
            last_t = cmd['tilt']
        if 'msg' in cmd:
            last_msg = cmd['msg']

    if last_p is not None or last_t is not None:
        set_target(last_p, last_t)
        _report_now = True                  # 受け取ったことをすぐ返す
    if last_msg is not None:
        show_msg(last_msg)

    # --- 目標に向かって 1 段階だけ動かす -----------------------------------
    moving = step_toward()
    now = time.ticks_ms()

    # --- 画面と LED ---------------------------------------------------------
    #
    # ★この2つは重い★
    #   ラベルの再描画と SK6812 10 個の書き換えで、1 周 20ms のつもりが
    #   40〜60ms かかることがあります。その間シリアルを読めません。
    #   前の版はこれを「動いている間ずっと 20ms ごと」に呼んでいたので、
    #   サーボが動いている最中に次の指示を出すと取りこぼしていました。
    #   動いている間は間引き、止まった瞬間には必ず1回描きます。
    if moving:
        _moving = True
        if time.ticks_diff(now, _last_draw) > DRAW_INTERVAL_MS:
            _last_draw = now
            show_pos()
            led_pan(_cur['pan'])
    elif _moving:
        _moving = False                     # いま到着した
        _last_draw = now
        show_pos()
        led_pan(_cur['pan'])
        _report_now = True                  # 到着をすぐ知らせる

    # --- 現在位置と目標を送る ----------------------------------------------
    if _report_now or time.ticks_diff(now, _last_send) > SEND_INTERVAL_MS:
        _report_now = False
        _last_send = now
        try:
            print(json.dumps({
                'pan':  round(_cur['pan']),     # いま実際に向いている角度
                'tilt': round(_cur['tilt']),
                'tp':   round(_tgt['pan']),     # いま目指している角度
                'tt':   round(_tgt['tilt']),
                'btn':  1 if M5.BtnA.isPressed() else 0,
                'drop': _dropped,               # 読めなかった行の累計
            }))
        except Exception:
            pass

    time.sleep_ms(10)


if __name__ == '__main__':
    try:
        setup()
        while True:
            loop()
    except (Exception, KeyboardInterrupt) as e:
        try:
            # 止めるときは正面に戻してから
            if pan:
                write_deg(pan, HOME_PAN)
            if tilt:
                write_deg(tilt, HOME_TILT)
        except Exception:
            pass
        try:
            m5ui.deinit()
            from utility import print_error_msg
            print_error_msg(e)
        except ImportError:
            print("please update to latest firmware")


# ===========================================================================
# 動作確認（ブラウザーを使わずに）
# ===========================================================================
#
#   IDE のターミナルから 1 行ずつ貼っても動きます。
#   ただし main.py が走っているとポートが埋まるので、
#   確認は「保存せずに実行」した状態でやってください。
#
#     set_target(30, 90)
#     set_target(150, 90)
#     set_target(90, 60)
#
# ===========================================================================
# つまずいたら
# ===========================================================================
#
#   ページからポートが選べない
#     → IDE を Disconnect したか
#
#   サーボが「ジー」と鳴る
#     → 可動範囲外です。PAN_LIMIT / TILT_LIMIT を狭めてください。
#       ★ブラウザー側のスキーマの minimum / maximum も同じ値にすること★
#
#   動きがカクカクする
#     → SPEED_DPS を小さくする（125 → 80）
#
#   動きが遅い
#     → SPEED_DPS を大きくする。ただし突入電流が増えます
#
#   ページの数字と実際の向きが違う
#     → ページのログに「受け取れなかった行」が出ていないか見てください。
#       0 のままなら、ズレているのは追従の途中です（指示 ≠ 実際 は正常）。
#       増えていくなら、どこかが詰まっています。DRAW_INTERVAL_MS を
#       大きくするか、SEND_INTERVAL_MS を長くしてください。
#
#   2軸同時に動くと画面が消える
#     → 電圧降下です。電解コンデンサーを増やす
#
#   画面が真っ黒
#     → page0.screen_load() を呼んだか
#
#   日本語が □ になる
#     → font に lv.AlibabaSans_JP24 以外を指定している
# ---------------------------------------------------------------------------
