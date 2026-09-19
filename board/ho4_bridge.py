# HO-4  ブラウザーとボードを繋ぐ
#
# このファイルは main.py として保存してください。
# 電源投入時に自動実行されます。
#
# ---------------------------------------------------------------------------
# 使いかた（順番を守ること）
#
#   1. このコードを main.py として保存する
#   2. ボードをリセットする
#   3. IDE の「Disconnect」を押す        ← ここを忘れると次で失敗します
#   4. ブラウザーのページで「ボードに接続」を押す
#
#   シリアルポートは 1 つのアプリしか掴めません。
#   IDE が繋がったままだと、ページから接続できません。
# ---------------------------------------------------------------------------
#
# プロトコル（1行に1つの JSON = JSON Lines）
#
#   ボード → PC:  {"t":25.3,"h":48.1,"btn":0}
#   PC → ボード:  {"servo":120,"status":"HUMID","msg":"換気しましょう"}
#
#   改行が「1件の終わり」を意味します。
#   これを決めておかないと、途中まで届いたデータと次のデータが混ざります。
#
# ---------------------------------------------------------------------------
# ★ status と msg の違い★
#
#   msg    … 人間が読む日本語。画面にそのまま出すだけ。
#   status … 機械が分岐に使う値。STATUS_COLOR の辞書を引きます。
#
#   status が「決められた選択肢のどれか」でなければならないのは、
#   辞書を引くキーだからです。AI が「やや多湿ぎみ」「humid」「HUMID」を
#   混ぜて返してくると、色が引けません。
#
#   だから prompt.js の JSON Schema では enum で固定しています。
#
#     数値は minimum / maximum で範囲を縛る
#     機械が分岐に使う文字列は enum で集合を縛る
#
#   msg のほうは人間が読むだけなので、自由な文章で構いません。
# ---------------------------------------------------------------------------

import os, sys, io
import M5
from M5 import *
import m5ui
import lvgl as lv
from hardware import RGB
from machine import I2C, Pin, PWM
import select
import json
import time


page0 = None
lblStatus = None
lblMsg = None
lblEnv = None
rgb25 = None
servo = None
i2c = None


# === センサー（HO-2 の再利用） ==============================================

def read_sht30():
    i2c.writeto(0x44, b'\x2C\x06')
    time.sleep_ms(20)
    d = i2c.readfrom(0x44, 6)
    t = -45 + 175 * (d[0] << 8 | d[1]) / 65535
    h = 100 * (d[3] << 8 | d[4]) / 65535
    return t, h


# === サーボ（HO-3 の再利用） ================================================

PULSE_MIN_US = 500      # ← HO-3 で校正した自分の値に
PULSE_MAX_US = 2400
_current = 90


def _write(deg):
    deg = max(0, min(180, deg))
    us = PULSE_MIN_US + (PULSE_MAX_US - PULSE_MIN_US) * deg / 180
    servo.duty_ns(int(us * 1000))


def angle(target, step=10, wait_ms=40):
    global _current
    target = max(0, min(180, target))
    d = step if target > _current else -step
    while abs(target - _current) > step:
        _current += d
        _write(_current)
        time.sleep_ms(wait_ms)
    _current = target
    _write(_current)


# === LED バー（HO-1 の再利用） ==============================================

def led_bar(percent, color=0x0078ff):
    """0〜100 を光る個数 0〜10 で表す"""
    n = int(max(0, min(100, percent)) * 10 / 100 + 0.5)
    for i in range(10):
        rgb25.set_color(i, color if i < n else 0x000000)


# === 画面 ===================================================================
#
# status を色に対応づける辞書。
# 知らない値が来たら灰色にしておけば、表示が壊れません。

STATUS_COLOR = {
    'GOOD':  0x33ff33,
    'DRY':   0xffcc00,
    'HUMID': 0x0078ff,
    'HOT':   0xff3300,
    'COLD':  0x00ccff,
}


def show_env(t, h):
    lblEnv.set_text(str('{:.1f} ℃   {:.1f} %'.format(t, h)))


def show_result(status, msg):
    color = STATUS_COLOR.get(status, 0x888888)
    lblStatus.set_text(str(status))
    lblStatus.set_text_color(color, 255, 0)
    if msg:
        lblMsg.set_text(str(msg))


# === setup ==================================================================

def setup():
    global page0, lblStatus, lblMsg, lblEnv, rgb25, servo, i2c

    M5.begin()
    Widgets.setRotation(1)
    m5ui.init()

    page0 = m5ui.M5Page(bg_c=0x000000)

    # 日本語が使えるのは AlibabaSans_JP24 のみです（サイズは24固定）。
    lblStatus = m5ui.M5Label("READY", x=20, y=25,
                             text_c=0xffffff, bg_c=0x000000, bg_opa=0,
                             font=lv.AlibabaSans_JP24, parent=page0)
    lblMsg = m5ui.M5Label("接続してください", x=20, y=95,
                          text_c=0xffffff, bg_c=0x000000, bg_opa=0,
                          font=lv.AlibabaSans_JP24, parent=page0)
    lblEnv = m5ui.M5Label("--.- ℃   --.- %", x=20, y=165,
                          text_c=0xaaaaaa, bg_c=0x000000, bg_opa=0,
                          font=lv.AlibabaSans_JP24, parent=page0)

    # ★これを呼ばないと画面に何も表示されません★
    page0.screen_load()

    rgb25 = RGB(io=25, n=10, type="SK6812")
    rgb25.set_brightness(12)        # 0〜100 のパーセント。12 で十分明るい
    rgb25.fill_color(0x003c00)      # 起動できたことを LED でも示す

    i2c = I2C(0, scl=Pin(33), sda=Pin(32), freq=100000)   # PORT.A
    servo = PWM(Pin(26), freq=50)                          # PORT.B

    angle(90)


# === メインループ ===========================================================
#
# 標準入力に「読めるデータがあるか」を確認するための仕掛け。
# これを使うと、データが来ていないときに待たされずに済みます。

poller = select.poll()
poller.register(sys.stdin, select.POLLIN)

_last_send = 0
SEND_INTERVAL_MS = 1000


def loop():
    global _last_send

    M5.update()

    # --- PC からのコマンドを受け取る ---------------------------------------
    if poller.poll(0):                       # 0 = 待たずにすぐ返る
        try:
            cmd = json.loads(sys.stdin.readline())

            if 'servo' in cmd:
                deg = cmd['servo']
                angle(deg)
                # AI の判断（0〜180）を LED バーでも表す
                led_bar(deg * 100 / 180,
                        0xff2800 if deg > 120 else 0x00a0ff)

            if 'status' in cmd or 'msg' in cmd:
                show_result(cmd.get('status', ''), cmd.get('msg', ''))

        except Exception:
            # 壊れた JSON が来ても止まらないようにする。
            # 通信では途中で切れたデータが届くことが普通にあります。
            pass

    # --- センサー値を定期的に送る ------------------------------------------
    if time.ticks_diff(time.ticks_ms(), _last_send) > SEND_INTERVAL_MS:
        _last_send = time.ticks_ms()
        try:
            t, h = read_sht30()
            show_env(t, h)
            payload = {
                't': round(t, 1),
                'h': round(h, 1),
                'btn': 1 if M5.BtnA.isPressed() else 0,
            }
            # print() がそのままシリアル送信になります。
            print(json.dumps(payload))
        except Exception:
            pass

    time.sleep_ms(20)


if __name__ == '__main__':
    try:
        setup()
        while True:
            loop()
    except (Exception, KeyboardInterrupt) as e:
        try:
            m5ui.deinit()
            from utility import print_error_msg
            print_error_msg(e)
        except ImportError:
            print("please update to latest firmware")


# ===========================================================================
# 改造課題
# ===========================================================================
#
# 1. ボタン A を押したときだけ何かする（btn はすでに送っています）
# 2. SEND_INTERVAL_MS を 100 にしてみる。ページの表示はどうなるか
# 3. わざと壊れた JSON を送って、try/except が効くことを確認する
# 4. status に応じて画面の背景色も変える
#
#      page0.set_bg_color(STATUS_COLOR.get(status, 0x000000), 60, 0)
#
# 5. STATUS_COLOR に新しい状態を足す（prompt.js の enum も直すこと）
#    片方だけ直すとどうなるか、先に予想してから試してください。
#
# 考えてみよう: なぜ「1行1JSON」という決まりが必要なのでしょうか。
#
# ===========================================================================
# つまずいたら
# ===========================================================================
#
#   ページからポートが選べない
#     → IDE を Disconnect したか
#
#   接続した瞬間にボードが再起動する
#     → ページ側で setSignals(dataTerminalReady=false, requestToSend=false)
#       を呼んでいるか（app.js の connect() を参照）
#
#   数値が表示されない
#     → まず IDE のターミナルで {"t":...} が流れているか確認する
#
#   画面が真っ黒のまま
#     → page0.screen_load() を呼んだか
#
#   日本語が □ になる
#     → font に font_montserrat_* を指定している。
#       日本語は lv.AlibabaSans_JP24 のみ（サイズ24固定）
#
#   起動直後に再起動を繰り返す
#     → main.py の中でエラーが出ている。IDE から main.py を消して復旧する
# ---------------------------------------------------------------------------
