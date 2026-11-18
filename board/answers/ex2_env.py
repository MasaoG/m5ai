# HO-2 改造課題のサンプル解答（課題1〜4）
#
# 接続: ENV Ⅲ Unit → PORT.A（左側面の下・赤いコネクター）
#
# ---------------------------------------------------------------------------
# 使いかた
#
#   ★ モードの切り替えは ボタン C（いちばん右）です ★
#
#     課題1  26℃ 以上なら赤、未満なら青
#     課題2  ★おすすめ★ 湿度を LED バーグラフで表す
#     課題3  温度と湿度を画面に文字で出す
#     課題4  読む間隔を 1秒 / 0.1秒 で切り替えて違いを見る
#
#   どのモードでも、画面には温度と湿度が出ています。
# ---------------------------------------------------------------------------

import os, sys, io
import M5
from M5 import *
import m5ui
import lvgl as lv
from hardware import RGB
from machine import I2C, Pin
import time


i2c = I2C(0, scl=Pin(33), sda=Pin(32), freq=100000)
ADDR_SHT30 = 0x44

N = 10
OFF = 0x000000

page0 = None
lblMode = None
lblEnv = None
lblNote = None
rgb25 = None

mode = 1
fast = False            # 課題4 用。True で 0.1秒間隔
_last = 0


def read_sht30():
    """温度(℃) と湿度(%) を返す（HO-2 本編と同じ）"""
    i2c.writeto(ADDR_SHT30, b'\x2C\x06')
    time.sleep_ms(20)            # ★この待ちを削ると値が取れません★
    d = i2c.readfrom(ADDR_SHT30, 6)
    t_raw = d[0] << 8 | d[1]
    h_raw = d[3] << 8 | d[4]
    t = -45 + 175 * t_raw / 65535
    h = 100 * h_raw / 65535
    return t, h


def bar(percent, color=0x0078ff):
    """HO-1 課題5 で作ったバーグラフ。そのまま持ってきます。"""
    p = max(0, min(100, percent))
    n = int(p * N / 100 + 0.5)
    for i in range(N):
        rgb25.set_color(i, color if i < n else OFF)


def clear():
    for i in range(N):
        rgb25.set_color(i, OFF)


def setup():
    global page0, lblMode, lblEnv, lblNote, rgb25

    M5.begin()
    Widgets.setRotation(1)
    m5ui.init()

    page0 = m5ui.M5Page(bg_c=0x000000)
    lblMode = m5ui.M5Label("課題1 温度でLEDの色", x=20, y=22,
                           text_c=0x888888, bg_c=0x000000, bg_opa=0,
                           font=lv.AlibabaSans_JP24, parent=page0)

    # ---- 課題3 ここが「画面に文字で表示する」の答えです -------------------
    lblEnv = m5ui.M5Label("--.- ℃   --.- %", x=20, y=80,
                          text_c=0x5fd3f3, bg_c=0x000000, bg_opa=0,
                          font=lv.AlibabaSans_JP24, parent=page0)

    lblNote = m5ui.M5Label("ボタンC でモード切替", x=20, y=175,
                           text_c=0xff8c00, bg_c=0x000000, bg_opa=0,
                           font=lv.AlibabaSans_JP24, parent=page0)
    page0.screen_load()

    rgb25 = RGB(io=25, n=N, type="SK6812")
    rgb25.set_brightness(12)
    clear()

    print('scan:', [hex(a) for a in i2c.scan()])
    print('=== HO-2 改造課題 サンプル解答 ===')
    print('scan が [0x44, 0x70] でなければ、配線を見直してください')


MODE_NAME = {
    1: "課題1 温度でLEDの色",
    2: "課題2 湿度をバーグラフ",
    3: "課題3 画面に文字で",
    4: "課題4 読む間隔を変える",
}


def loop():
    global mode, fast, _last

    M5.update()

    if M5.BtnC.wasPressed():
        mode = mode % 4 + 1
        clear()
        lblMode.set_text(MODE_NAME[mode])
        print('--- モード', mode, MODE_NAME[mode], '---')
        time.sleep_ms(200)
        return

    # 課題4 のときだけ、ボタン A で速さを切り替える
    if mode == 4 and M5.BtnA.wasPressed():
        fast = not fast
        time.sleep_ms(200)

    interval = 100 if (mode == 4 and fast) else 1000
    if time.ticks_diff(time.ticks_ms(), _last) < interval:
        time.sleep_ms(10)
        return
    _last = time.ticks_ms()

    t, h = read_sht30()

    # ---- 課題3 の答え（全モードで常に表示しています）----------------------
    #
    #   set_text() に渡せるのは文字列だけです。
    #   数値をそのまま渡すとエラーになるので format() で文字列にします。
    #   {:.1f} は「小数第1位まで」という意味です。
    #
    lblEnv.set_text('{:.1f} ℃   {:.1f} %'.format(t, h))

    # =======================================================================
    # 課題1　温度が 26℃ 以上なら LED を赤、未満なら青
    # =======================================================================
    #
    #   HO-1 で作った fill_color() がそのまま使えます。
    #   新しく覚えることは何もありません。
    #   「前にやったことを部品として再利用する」のがこの課題の狙いです。
    #
    #   しきい値を 26 ちょうどにすると、25.9 と 26.0 を行き来したときに
    #   色がチカチカします。気になる人は、赤に変わるのは 26 以上、
    #   青に戻るのは 25.5 未満、のように幅を持たせてみてください
    #   （ヒステリシスといいます）。
    #
    if mode == 1:
        rgb25.fill_color(0xff2800 if t >= 26 else 0x0078ff)
        lblNote.set_text("26℃ 以上 → 赤" if t >= 26 else "26℃ 未満 → 青")

    # =======================================================================
    # 課題2　★おすすめ★ 湿度を LED バーグラフで表す
    # =======================================================================
    #
    #   HO-1 の課題5 と、この HO-2 を合体させたものです。
    #
    #   息を吹きかけてください。光る数が増えます。
    #   これで「画面を見なくても湿度が分かる出力」になりました。
    #
    #   湿度 70% 以上で色を赤に変えています。
    #   数だけでなく色でも状態を伝えると、遠目でも分かります。
    #
    elif mode == 2:
        bar(h, 0x0078ff if h < 70 else 0xff2800)
        lblNote.set_text("息を吹きかけてみてください")

    # =======================================================================
    # 課題3　温度と湿度を画面に文字で表示する
    # =======================================================================
    #
    #   上の lblEnv.set_text(...) が答えです。
    #   このモードでは LED を消して、画面だけにしています。
    #
    #   画面と LED の違いを考えてみてください。
    #     画面 … 正確な数値が分かる。近づかないと読めない
    #     LED  … 数値は分からない。離れていても状態が分かる
    #
    #   どちらが優れているかではなく、用途が違います。
    #
    elif mode == 3:
        clear()
        lblNote.set_text("LED は消しています")

    # =======================================================================
    # 課題4　time.sleep(1) を time.sleep(0.1) に変えるとどうなるか
    # =======================================================================
    #
    #   ★ ボタン A で 1秒 ⇄ 0.1秒 を切り替えます ★
    #
    #   0.1秒にすると、値がせわしなく動くのが分かります。
    #   センサー自体は壊れません。読みすぎても大丈夫です。
    #
    #   ■ では sleep_ms(20) のほうは消していいのか
    #
    #     こちらは消せません。意味がまったく違います。
    #
    #       time.sleep(1)     … 次に読むまでの間隔。好きに変えてよい
    #       time.sleep_ms(20) … センサーが測り終わるのを待つ時間。
    #                           データシートで決められている
    #
    #     20ms を削ると、測定が終わる前に読みに行くので、
    #     古い値が返ったりエラーになったりします。
    #
    #     「自分の都合で決めてよい待ち」と「相手の都合で決まる待ち」は
    #     別物です。組み込みでは何度も出てくる区別です。
    #
    else:
        bar(h)
        lblNote.set_text("A で切替: {}".format("0.1秒ごと" if fast else "1秒ごと"))

    print('{:.1f}C  {:.1f}%'.format(t, h))


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
#   scan が [] になる
#     → PORT.A（左側面の下・赤）に挿さっていますか。
#       上面の黒（B）・青（C）ではありません。
#       配線ミスはエラーではなく「何も見つからない」として出ます。
#
#   OSError: [Errno 19] ENODEV
#     → ケーブルが抜けているか、奥まで挿さっていません。
#
#   値が変わらない
#     → 息を吹きかけてみてください。室温は数秒では動きません。
#       湿度のほうが反応が速いので、動作確認には湿度を使います。
#
#   画面に □ が並ぶ
#     → フォントが AlibabaSans_JP24 になっているか確認してください。
#       font_montserrat_* は欧文専用で、日本語が出せません。
# ---------------------------------------------------------------------------
