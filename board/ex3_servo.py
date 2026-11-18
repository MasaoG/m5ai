# HO-3 改造課題のサンプル解答（課題1〜3）
#
# 接続: Grove 変換ケーブル → PORT.B（上面の左・黒いコネクター）
#       ENV Ⅲ → PORT.A（左側面の下・赤）※課題1 で使います
#
#   ★ サーボの電源はブレッドボード上で USB AC アダプターから取ります ★
#   Core2 から出ているのは 信号線（白 = G26）と GND（黒）の2本だけです。
#   赤(5V) は絶対に挿さないこと。挿すと Core2 が再起動します。
#
# ---------------------------------------------------------------------------
# 使いかた
#
#   ★ モードの切り替えは ボタン C（いちばん右）です ★
#
#     課題1  湿度を角度にマッピングして、湿度計メーターにする
#     課題2  step と wait_ms を変えて、動きの滑らかさを比べる
#     課題3  step=180（一気に動かす）と何が起きるか
#
#   ★ 課題3 は Core2 が再起動することがあります ★
#   それが観察したい現象そのものです。壊れてはいません。
# ---------------------------------------------------------------------------

import os, sys, io
import M5
from M5 import *
import m5ui
import lvgl as lv
from hardware import RGB
from machine import I2C, Pin, PWM
import time


# === 校正定数 ===============================================================
# 自分のサーボで両端を探して、この値を書き換えてください。
PULSE_MIN_US = 500
PULSE_MAX_US = 2400

N = 10
OFF = 0x000000

i2c = I2C(0, scl=Pin(33), sda=Pin(32), freq=100000)
ADDR_SHT30 = 0x44

page0 = None
lblMode = None
lblDeg = None
lblNote = None
rgb25 = None
servo = None

mode = 1
_current = 90
preset = 0          # 課題2 用。刻みの組み合わせを選ぶ
_last = 0


def read_sht30():
    i2c.writeto(ADDR_SHT30, b'\x2C\x06')
    time.sleep_ms(20)
    d = i2c.readfrom(ADDR_SHT30, 6)
    t = -45 + 175 * (d[0] << 8 | d[1]) / 65535
    h = 100 * (d[3] << 8 | d[4]) / 65535
    return t, h


def deg_to_us(deg):
    deg = max(0, min(180, deg))
    return PULSE_MIN_US + (PULSE_MAX_US - PULSE_MIN_US) * deg / 180


def led_gauge(deg):
    n = int(deg * N / 180 + 0.5)
    for i in range(N):
        rgb25.set_color(i, 0xff8c00 if i < n else OFF)


def _write(deg):
    # duty() ではなく duty_ns() を使うこと。
    # duty() は分解能が足りず、動きがカクつきます。
    servo.duty_ns(int(deg_to_us(deg) * 1000))


def angle(target, step=10, wait_ms=40):
    """段階的に動かす。step と wait_ms がこの課題の主役です。"""
    global _current
    target = max(0, min(180, target))
    d = step if target > _current else -step
    while abs(target - _current) > step:
        _current += d
        _write(_current)
        led_gauge(_current)
        lblDeg.set_text('{:.0f} °'.format(_current))
        time.sleep_ms(wait_ms)
    _current = target
    _write(_current)
    led_gauge(_current)
    lblDeg.set_text('{:.0f} °'.format(_current))


def setup():
    global page0, lblMode, lblDeg, lblNote, rgb25, servo

    M5.begin()
    Widgets.setRotation(1)
    m5ui.init()

    page0 = m5ui.M5Page(bg_c=0x000000)
    lblMode = m5ui.M5Label("課題1 湿度計メーター", x=20, y=22,
                           text_c=0x888888, bg_c=0x000000, bg_opa=0,
                           font=lv.AlibabaSans_JP24, parent=page0)
    lblDeg = m5ui.M5Label("-- °", x=20, y=80,
                          text_c=0xffffff, bg_c=0x000000, bg_opa=0,
                          font=lv.AlibabaSans_JP24, parent=page0)
    lblNote = m5ui.M5Label("ボタンC でモード切替", x=20, y=175,
                           text_c=0xff8c00, bg_c=0x000000, bg_opa=0,
                           font=lv.AlibabaSans_JP24, parent=page0)
    page0.screen_load()

    rgb25 = RGB(io=25, n=N, type="SK6812")
    rgb25.set_brightness(12)

    servo = PWM(Pin(26), freq=50)       # PORT.B の白（G26）、50Hz
    angle(90)
    print('=== HO-3 改造課題 サンプル解答 ===')


MODE_NAME = {
    1: "課題1 湿度計メーター",
    2: "課題2 刻みを変える",
    3: "課題3 一気に動かす",
}

# 課題2 で切り替える組み合わせ（step, wait_ms, 説明）
PRESETS = [
    (1,  10, "step=1  なめらか"),
    (10, 40, "step=10 標準"),
    (30, 60, "step=30 カクカク"),
]


def loop():
    global mode, preset, _last

    M5.update()

    if M5.BtnC.wasPressed():
        mode = mode % 3 + 1
        lblMode.set_text(MODE_NAME[mode])
        print('--- モード', mode, MODE_NAME[mode], '---')
        time.sleep_ms(200)
        return

    # =======================================================================
    # 課題1　湿度を角度にマッピングして、実際の湿度計メーターにする
    # =======================================================================
    #
    #   ★ マッピングとは「ある範囲を別の範囲に対応づける」ことです ★
    #
    #     湿度  0 〜 100 %
    #     角度  0 〜 180 °
    #
    #   なので  角度 = 湿度 × 180 / 100  です。
    #   HO-1 のバーグラフ（0〜100 を 0〜10個に写す）と同じ考え方で、
    #   出力先が LED の個数からサーボの角度に変わっただけです。
    #
    #   ■ なぜ 2秒ごとなのか
    #
    #     サーボを動かすたびに電流が流れます。
    #     湿度は秒単位では動かないので、速く回しても意味がありません。
    #     「動かす必要があるときだけ動かす」のが、電池で動く機器の基本です。
    #
    #   息を吹きかけると、針（紙を貼ると分かりやすい）が動きます。
    #
    if mode == 1:
        if time.ticks_diff(time.ticks_ms(), _last) > 2000:
            _last = time.ticks_ms()
            t, h = read_sht30()
            deg = h * 180 / 100
            angle(deg)
            lblNote.set_text("湿度 {:.1f}% → {:.0f}°".format(h, deg))
        time.sleep_ms(20)

    # =======================================================================
    # 課題2　step と wait_ms を変えて、滑らかさの違いを見る
    # =======================================================================
    #
    #   ★ ボタン A で 3つの組み合わせを切り替えます ★
    #
    #     step=1,  wait_ms=10  … なめらか。ただし 0→180 に 1.8秒かかる
    #     step=10, wait_ms=40  … 標準。HO-3 本編の値
    #     step=30, wait_ms=60  … 目に見えてカクカクする
    #
    #   ■ 2つの値は役割が違います
    #
    #     step    … 1回でどれだけ動かすか（空間の刻み）
    #     wait_ms … 次の1回までどれだけ待つか（時間の刻み）
    #
    #   全体の速さは「step ÷ wait_ms」で決まります。
    #   step を小さくして wait_ms も小さくすれば、
    #   なめらかなまま同じ速さにできます。
    #
    #   なめらかさと速さは、別々に調整できる — これが分かると、
    #   アイデアソンで「ゆっくり優しく動くもの」が作れるようになります。
    #
    elif mode == 2:
        if M5.BtnA.wasPressed():
            preset = (preset + 1) % len(PRESETS)
            time.sleep_ms(200)
        st, wt, name = PRESETS[preset]
        lblNote.set_text("A で切替: " + name)
        angle(180, step=st, wait_ms=wt)
        time.sleep_ms(300)
        angle(0, step=st, wait_ms=wt)
        time.sleep_ms(300)

    # =======================================================================
    # 課題3　step=180（一気に動かす）と何が起きるか
    # =======================================================================
    #
    #   ★ Core2 が再起動することがあります。それが観察対象です ★
    #
    #   ■ 何が起きているか
    #
    #     サーボは、目標と現在位置の差が大きいほど強く回ろうとします。
    #     0° から 180° へ一度に指示すると、最大のトルクを出そうとして
    #     大きな電流（突入電流）が一瞬流れます。
    #
    #     外部電源を使っていても、GND を共有しているので
    #     電圧の落ち込みが Core2 側に伝わることがあります。
    #     電源を Core2 から取っていたら、ほぼ確実に落ちます。
    #
    #   ■ だから段階的に動かす
    #
    #     10度ずつ刻めば、1回あたりの差が小さいので
    #     電流のピークが抑えられます。
    #     遠回りに見えますが、これが安定して動かすための遠回りです。
    #
    #   HO-1 の「LED の明るさを 12% にする」と同じ、電流の話です。
    #   今日 2回目です。部品を足すときは、いつも電流を気にしてください。
    #
    else:
        lblNote.set_text("一気に 0 ⇄ 180（観察用）")
        angle(180, step=180, wait_ms=0)
        time.sleep_ms(900)
        angle(0, step=180, wait_ms=0)
        time.sleep_ms(900)


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
# つまずいたら
# ===========================================================================
#
#   サーボが動かない
#     → 信号線は PORT.B の白（G26）ですか。黄ではありません。
#       変換ケーブルの製品によって色が違うので、疑わしければテスターで。
#       G36 は入力専用なので、そちらに繋いでも何も出ません。
#
#   サーボが不規則に暴れる
#     → GND（黒）が繋がっていません。
#       PWM は GND を基準にした電圧変化で情報を伝えています。
#       基準が共有されていないと、信号の意味が決まりません。
#
#   Core2 が再起動する（課題3 以外で）
#     → サーボの電源を Core2 から取っていませんか。
#       赤(5V) はブレッドボード側の AC アダプターから取ります。
#
#   両端まで回りきらない／唸る
#     → PULSE_MIN_US / PULSE_MAX_US を自分の機体で調整してください。
#       唸っているのは、行けない位置に行こうとしている音です。
#       そのままにすると発熱します。
# ---------------------------------------------------------------------------
