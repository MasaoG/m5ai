# HO-3  サーボを動かす（SG92R）
#
# 接続: Grove 変換ケーブル → PORT.B（ベース側面・黒）
#       PORT.B の 1番ピン = G26 が信号線です。
#       G36 は入力専用なので信号には使えません。
#
# ★重要★ サーボの電源は M5Stack から取っていません。
#   ブレッドボード上で USB AC アダプタから別に供給しています。
#   M5Stack から出ているのは「信号線」と「GND」の2本だけです。
#
#   ・電源を分ける理由 → サーボの突入電流で M5Stack が再起動するため
#   ・GND だけ繋ぐ理由 → PWM は GND を基準にした電圧変化で情報を伝えるため
#
#   HO-1 で LED の明るさを絞ったのと同じ「電流の話」です。今日 2 回目です。

import os, sys, io
import M5
from M5 import *
import m5ui
import lvgl as lv
from hardware import RGB
from machine import Pin, PWM
import time


page0 = None
lblTitle = None
lblDeg = None
lblUs = None
rgb25 = None
servo = None


# === 校正定数 ===============================================================
# データシート上は 500〜2400µs ですが、個体差でずれます。
# 自分のサーボで両端を探して、この値を書き換えてください。

PULSE_MIN_US = 500
PULSE_MAX_US = 2400

_current = 90


def deg_to_us(deg):
    """角度(0〜180) をパルス幅(µs) に変換する"""
    deg = max(0, min(180, deg))
    return PULSE_MIN_US + (PULSE_MAX_US - PULSE_MIN_US) * deg / 180


def led_gauge(deg):
    """LED バーを角度計として使う。

    HO-1 のバーグラフ課題がそのまま使えます。
    サーボ実機の順番待ちのあいだも、角度の変化を目で追えます。
    """
    n = int(deg * 10 / 180 + 0.5)
    for i in range(10):
        rgb25.set_color(i, 0xff8c00 if i < n else 0x000000)


def show(deg, us):
    """画面に角度とパルス幅を表示する"""
    lblDeg.set_text(str('{:.0f} °'.format(deg)))
    lblUs.set_text(str('パルス幅 {:.0f} µs'.format(us)))


def _write(deg):
    # duty() ではなく duty_ns() を使うこと。
    # duty() は分解能が足りず、サーボの動きがカクつきます。
    servo.duty_ns(int(deg_to_us(deg) * 1000))


def angle(target, step=10, wait_ms=40):
    """段階的に動かす。

    一気に動かすと突入電流が大きくなります。
    10 度ずつ刻むことで電流のピークを抑えます。
    """
    global _current
    target = max(0, min(180, target))
    d = step if target > _current else -step
    while abs(target - _current) > step:
        _current += d
        _write(_current)
        led_gauge(_current)
        show(_current, deg_to_us(_current))
        time.sleep_ms(wait_ms)
    _current = target
    _write(_current)
    led_gauge(_current)
    show(_current, deg_to_us(_current))


def setup():
    global page0, lblTitle, lblDeg, lblUs, rgb25, servo

    M5.begin()
    Widgets.setRotation(1)
    m5ui.init()

    page0 = m5ui.M5Page(bg_c=0x000000)

    # 日本語を表示するときは AlibabaSans_JP 系のフォントを使います。
    # font_montserrat_* は欧文専用で、日本語が □ になります。
    # 日本語が使えるのは AlibabaSans_JP24 のみです（サイズは24固定）。
    # font_montserrat_* は欧文専用で、日本語が □ になります。
    lblTitle = m5ui.M5Label("サーボ角度", x=20, y=25,
                            text_c=0x888888, bg_c=0x000000, bg_opa=0,
                            font=lv.AlibabaSans_JP24, parent=page0)
    lblDeg = m5ui.M5Label("-- °", x=20, y=85,
                          text_c=0xffffff, bg_c=0x000000, bg_opa=0,
                          font=lv.AlibabaSans_JP24, parent=page0)
    lblUs = m5ui.M5Label("パルス幅 -- µs", x=20, y=155,
                         text_c=0xff8c00, bg_c=0x000000, bg_opa=0,
                         font=lv.AlibabaSans_JP24, parent=page0)

    # ★これを呼ばないと画面に何も表示されません★
    page0.screen_load()

    rgb25 = RGB(io=25, n=10, type="SK6812")
    rgb25.set_brightness(12)        # 0〜100 のパーセント。12 で十分明るい

    servo = PWM(Pin(26), freq=50)   # PORT.B の1番ピン、50Hz = 周期 20ms

    angle(90)


def loop():
    M5.update()
    for deg in range(0, 181, 10):
        angle(deg)
        time.sleep_ms(120)
    for deg in range(180, -1, -10):
        angle(deg)
        time.sleep_ms(120)


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
# パルス幅と角度の関係
# ===========================================================================
#
#   PWM 周期 20ms（50Hz）のなかで、正パルスの「幅」だけで角度が決まります。
#
#      0.5 ms →   0°
#     1.45 ms →  90°
#      2.4 ms → 180°
#
#   周期は常に 20ms のまま変わりません。変わるのは幅だけです。
#
# ===========================================================================
# サーボが手元にないとき
# ===========================================================================
#
#   実機はチームに 1 セットしかありません。順番待ちのあいだは、
#   servo に関する行をコメントアウトすれば、画面と LED だけで動きます。
#   角度計算とパルス幅の理解は、これで完結します。
#
# ===========================================================================
# 校正のしかた
# ===========================================================================
#
#   1. _write() を直接呼んで、パルス幅を少しずつ変える
#   2. サーボが「これ以上回らない」位置に来たら、そこが端
#   3. その値を PULSE_MIN_US / PULSE_MAX_US に書く
#
#   ★注意★ 端を超えて指令を出し続けると「ジー」と鳴り続けます。
#           この音が聞こえたらすぐ止めてください。ギヤが傷みます。
#
# ===========================================================================
# 改造課題
# ===========================================================================
#
# 1. 湿度を角度にマッピングして湿度計にする（HO-2 と合体）
#
#      def humidity_to_deg(h):
#          return h * 180 / 100
#
# 2. step と wait_ms を変えて、動きの滑らかさがどう変わるか試す
# 3. step=180（一気に動かす）にすると何が起きるか観察する
# 4. 角度に応じて文字色を変える
#
#      lblDeg.set_text_color(0xff3300 if deg > 120 else 0xffffff, 255, 0)
#
# 考えてみよう: なぜ段階的に動かすのでしょうか。
#
# ===========================================================================
# つまずいたら
# ===========================================================================
#
#   M5Stack が再起動する   → Grove の赤線がブレッドボードに刺さっていないか
#   サーボが不規則に暴れる → M5Stack の GND が − レールに繋がっているか
#   全く動かない           → AC アダプタの通電、信号が G26 か
#   ジーと鳴り続ける       → 可動範囲を超えている。すぐ止めて校正する
#   画面が真っ黒のまま     → page0.screen_load() を呼んだか
#   日本語が □ になる      → font に font_montserrat_* を指定している
#                            日本語は lv.AlibabaSans_JP24 のみ（サイズ固定）
# ---------------------------------------------------------------------------
