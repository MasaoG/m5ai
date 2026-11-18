# HO-4 改造課題のサンプル解答（課題1〜3）
#
# このファイルは main.py として保存してください。
#
# ---------------------------------------------------------------------------
# 使いかた（順番を守ること）
#
#   1. このコードを main.py として保存する
#   2. ボードをリセットする
#   3. IDE の「Disconnect」を押す        ← ここを忘れると次で失敗します
#   4. ブラウザーのページで「ボードに接続」を押す
#
#   シリアルポートは 1つのアプリしか掴めません。
#
# ---------------------------------------------------------------------------
# 課題と操作
#
#     課題1  ボタン A の状態も JSON に含めて送る   → 常に送っています
#     課題2  送信間隔を 100ms にする               → ボタン C で切替
#     課題3  わざと壊れた JSON を送る              → ボタン B で1回送る
#
#   ★ ボタン A は課題1 で使うので、切替には使いません ★
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


PULSE_MIN_US = 500
PULSE_MAX_US = 2400
N = 10
OFF = 0x000000

i2c = I2C(0, scl=Pin(33), sda=Pin(32), freq=100000)
ADDR_SHT30 = 0x44

page0 = None
lblEnv = None
lblMsg = None
lblRate = None
rgb25 = None
servo = None

_current = 90
_last_send = 0
_sent = 0
_bad = 0

# ---- 課題2 の答え ---------------------------------------------------------
#   送信間隔をリストで持ち、ボタン C で切り替えます。
RATES = [1000, 500, 100]
rate_i = 0


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


def _write(deg):
    servo.duty_ns(int(deg_to_us(deg) * 1000))


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


def led_bar(percent, color=0x0078ff):
    p = max(0, min(100, percent))
    n = int(p * N / 100 + 0.5)
    for i in range(N):
        rgb25.set_color(i, color if i < n else OFF)


def setup():
    global page0, lblEnv, lblMsg, lblRate, rgb25, servo

    M5.begin()
    Widgets.setRotation(1)
    m5ui.init()

    page0 = m5ui.M5Page(bg_c=0x000000)
    lblEnv = m5ui.M5Label("--.- ℃   --.- %", x=20, y=24,
                          text_c=0x5fd3f3, bg_c=0x000000, bg_opa=0,
                          font=lv.AlibabaSans_JP24, parent=page0)
    lblMsg = m5ui.M5Label("接続を待っています", x=20, y=84,
                          text_c=0xffffff, bg_c=0x000000, bg_opa=0,
                          font=lv.AlibabaSans_JP24, parent=page0)
    lblRate = m5ui.M5Label("1000ms ごと  C:間隔 B:壊れたJSON", x=20, y=180,
                           text_c=0x888888, bg_c=0x000000, bg_opa=0,
                           font=lv.AlibabaSans_JP24, parent=page0)
    page0.screen_load()

    rgb25 = RGB(io=25, n=N, type="SK6812")
    rgb25.set_brightness(12)

    servo = PWM(Pin(26), freq=50)
    angle(90)


poller = select.poll()
poller.register(sys.stdin, select.POLLIN)


def loop():
    global _last_send, rate_i, _sent, _bad

    M5.update()

    # =======================================================================
    # 課題2　送信間隔を 100ms にしてみる。ページの表示はどうなるか
    # =======================================================================
    #
    #   ★ ボタン C で 1000 → 500 → 100ms と切り替わります ★
    #
    #   ■ 何が起きるか
    #
    #     100ms にすると、ページのログが一瞬で流れていきます。
    #     温度・湿度の表示はせわしなく動きますが、読めません。
    #
    #   ■ なぜ速くしても嬉しくないのか
    #
    #     1. 室温や湿度は 0.1秒では変わりません。
    #        意味のない桁が揺れているだけです。
    #     2. 行が増えるぶん、ブラウザー側の処理が増えます。
    #     3. AI を呼ぶ設計なら、呼ぶ回数がそのまま増えます。
    #
    #     「速く送れば良い」ではなく「変化が分かる最低限の速さ」を選ぶ。
    #     アイデアソンの制約カード「AI を呼ぶのは1分に1回まで」は、
    #     これを強制的に体験させるためのカードです。
    #
    #   逆に速さが要るのは、ボタンや加速度のように「一瞬の出来事」を
    #   取りこぼしたくないときです。何を測るかで適切な間隔が決まります。
    #
    if M5.BtnC.wasPressed():
        rate_i = (rate_i + 1) % len(RATES)
        lblRate.set_text("{}ms ごと  C:間隔 B:壊れたJSON".format(RATES[rate_i]))
        print('# 送信間隔を', RATES[rate_i], 'ms にしました')
        time.sleep_ms(200)

    # =======================================================================
    # 課題3　わざと壊れた JSON を送って、try/except が効くことを確認する
    # =======================================================================
    #
    #   ★ ボタン B を押すと、壊れた行を1つ送ります ★
    #
    #   ページ側の受信処理は try/except（JavaScript では try/catch）で
    #   囲ってあるので、壊れた行は捨てられ、次の行から何事もなく続きます。
    #   ボードも止まりません。
    #
    #   ■ なぜ「1行1JSON」にしてあるのか
    #
    #     改行が「1件の終わり」を意味すると決めておくと、
    #     壊れた行をその1行だけ捨てて、次から立て直せます。
    #
    #     もし改行で区切らずに流し続けると、どこからどこまでが
    #     1件なのか分からなくなり、1度壊れたら以降ずっと壊れたままです。
    #
    #     通信では、途中で切れたデータが届くのは異常ではなく日常です。
    #     「壊れることを前提に、壊れてもすぐ復帰できる形にしておく」
    #     — これがプロトコル設計のいちばん基本的な考え方です。
    #
    if M5.BtnB.wasPressed():
        _bad += 1
        print('{"t":25.3,"h":48.1,')        # ← わざと途中で終わらせる
        lblMsg.set_text("壊れた JSON を送信 {}回".format(_bad))
        time.sleep_ms(200)

    # --- PC からのコマンドを受け取る ---------------------------------------
    if poller.poll(0):
        try:
            cmd = json.loads(sys.stdin.readline())

            if 'servo' in cmd:
                deg = cmd['servo']
                angle(deg)
                led_bar(deg * 100 / 180,
                        0xff2800 if deg > 120 else 0x00a0ff)

            if 'status' in cmd or 'msg' in cmd:
                lblMsg.set_text(cmd.get('msg', ''))

        except Exception:
            # ボード側も同じように守っておきます。
            # ページから壊れた行が来ても、ここで止まりません。
            pass

    # --- センサー値を定期的に送る ------------------------------------------
    if time.ticks_diff(time.ticks_ms(), _last_send) > RATES[rate_i]:
        _last_send = time.ticks_ms()
        try:
            t, h = read_sht30()
            lblEnv.set_text('{:.1f} ℃   {:.1f} %'.format(t, h))

            # ===============================================================
            # 課題1　ボタン A の状態も JSON に含めて送る
            # ===============================================================
            #
            #   ★ これは HO-4 本編のコードに最初から入っています ★
            #
            #   課題の本番は「送ること」ではなく「使うこと」です。
            #   ページ側で btn が 1 のときだけ AI を呼ぶようにすると、
            #   アイデアソンの制約カード「操作はボタン1つだけ」と
            #   「AI を呼ぶのは1分に1回まで」の両方が同時に満たせます。
            #
            #   ■ なぜ True / False ではなく 1 / 0 なのか
            #
            #     JSON には真偽値もありますが、数値にしておくと
            #     グラフにそのまま載せられます。
            #     「あとで何に使うか」で型を選ぶ、という設計の話です。
            #
            payload = {
                't': round(t, 1),
                'h': round(h, 1),
                'btn': 1 if M5.BtnA.isPressed() else 0,
            }
            print(json.dumps(payload))      # print() がそのまま送信になります
            _sent += 1

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
# ページ側で確かめること
# ===========================================================================
#
#   課題1  ボタン A を押しながらログを見る → btn が 1 になる
#   課題2  ボタン C で 100ms にする       → ログが一気に流れる
#   課題3  ボタン B を押す                → 1行だけ飛ばされ、次から戻る
#
#   課題3 のとき、ページが固まったりエラーで止まったりしないことが
#   確認できれば成功です。「エラーが出ない」ことを確認する作業は
#   地味ですが、通信を扱うときはここが一番大事です。
#
# ===========================================================================
# つまずいたら
# ===========================================================================
#
#   ページから接続できない
#     → IDE の Disconnect を押しましたか。
#       シリアルポートは 1つのアプリしか掴めません。
#       UIFlow2 のタブも閉じてください。
#
#   ログに何も出ない
#     → print() が送信そのものです。print を消していませんか。
#       また、このファイルを main.py として保存してリセットしましたか。
#
#   壊れた JSON を送ったら本当に止まった
#     → ページ側の受信処理が try/catch で囲まれているか確認してください。
#       囲んでいないコードに書き換えていませんか。
# ---------------------------------------------------------------------------
