# HO-0  自己診断スクリプト
#
# ---------------------------------------------------------------------------
# 用途
#
#   ボードとセンサーとサーボが正しく繋がっているかを、順番に確認します。
#   ボタン A で次のテストへ進みます。
#
#   ・運営: 実機検証で、各項目の実測値を記録するために使います
#   ・参加者: HO-0 で機材が正常かを確認するために使います
#
#   結果はシリアルにも出力されるので、IDE のターミナルからコピーして
#   記録用紙に貼り付けられます。
# ---------------------------------------------------------------------------
#
# 操作
#
#   ボタン A （左）  次のテストへ進む
#   ボタン B （中）  いまのテストをやり直す
#   ボタン C （右）  このテストを飛ばす
#
# ---------------------------------------------------------------------------

import os, sys, io
import M5
from M5 import *
import m5ui
import lvgl as lv
from hardware import RGB
from machine import I2C, Pin, PWM
import time


page0 = None
lblNo = None
lblTitle = None
lblResult = None
lblHint = None
rgb25 = None
i2c = None
servo = None

results = []          # (テスト名, 結果) を貯めておく


# === 画面まわり =============================================================

def draw(no, total, title, result="", hint=""):
    lblNo.set_text(str('[{}/{}]'.format(no, total)))
    lblTitle.set_text(str(title))
    lblResult.set_text(str(result))
    lblHint.set_text(str(hint))


def say(text):
    """シリアルに出す。IDE のターミナルからコピーできる"""
    print(text)


# === ボタン待ち =============================================================
#
# 戻り値: 'next' / 'again' / 'skip'

def wait_button():
    while True:
        M5.update()
        if M5.BtnA.wasPressed():
            return 'next'
        if M5.BtnB.wasPressed():
            return 'again'
        if M5.BtnC.wasPressed():
            return 'skip'
        time.sleep_ms(30)


# === テスト1  LED バー ======================================================

def test_led():
    draw(1, 6, "LED バー", "赤→緑→青→白", "10個すべて光りますか")
    say('--- [1] LED バー (G25 / SK6812 x10) ---')

    for name, color in [('赤', 0xff0000), ('緑', 0x00ff00),
                        ('青', 0x0000ff), ('白', 0xffffff)]:
        rgb25.fill_color(color)
        say('    ' + name)
        time.sleep_ms(700)

    # 1個ずつ流す
    for i in range(10):
        for j in range(10):
            rgb25.set_color(j, 0xff8c00 if j == i else 0x000000)
        time.sleep_ms(70)
    rgb25.fill_color(0x000000)

    draw(1, 6, "LED バー", "確認できましたか", "A:次へ  B:もう一度")
    say('    → 10個すべて点灯し、1個ずつ流れれば OK')
    return wait_button()


# === テスト2  明るさと発熱 ==================================================
#
# 白100%は電流が大きいので、確認のあとすぐ戻します。

def test_brightness():
    draw(2, 6, "明るさ", "12% → 50% → 100%", "本体が熱くなりませんか")
    say('--- [2] 明るさと発熱 (白100%は約600mA) ---')

    for pct in (12, 50, 100):
        rgb25.set_brightness(pct)
        rgb25.fill_color(0xffffff)
        draw(2, 6, "明るさ", "白 {} %".format(pct), "熱ければ B で中断")
        say('    白 {} %'.format(pct))
        time.sleep_ms(2500)

    rgb25.set_brightness(12)
    rgb25.fill_color(0x000000)

    draw(2, 6, "明るさ", "12% に戻しました", "A:次へ  B:もう一度")
    say('    → 再起動しなければ OK。発熱の程度を記録すること')
    return wait_button()


# === テスト3  I2C スキャン ==================================================

def test_i2c():
    draw(3, 6, "I2C スキャン", "PORT.A を確認中", "")
    say('--- [3] I2C スキャン (PORT.A / G32=SDA G33=SCL) ---')

    try:
        found = i2c.scan()
    except Exception as e:
        draw(3, 6, "I2C スキャン", "エラー", "配線を確認してください")
        say('    ERROR: ' + str(e))
        results.append(('I2C scan', 'ERROR'))
        return wait_button()

    hexes = [hex(a) for a in found]
    say('    検出: ' + str(hexes))
    results.append(('I2C scan', str(hexes)))

    ok44 = 0x44 in found
    ok70 = 0x70 in found

    msg = "0x44 {}   0x70 {}".format('OK' if ok44 else 'NG',
                                     'OK' if ok70 else 'NG')
    draw(3, 6, "I2C スキャン", msg, "A:次へ  B:もう一度")

    say('    0x44 SHT30   : ' + ('OK' if ok44 else 'NG ← ENV III を確認'))
    say('    0x70 QMP6988 : ' + ('OK' if ok70 else 'NG'))
    if not ok44:
        say('    → PORT.A に挿さっていますか。PORT.B ではありませんか')
    return wait_button()


# === テスト4  温湿度センサー ================================================

def read_sht30():
    i2c.writeto(0x44, b'\x2C\x06')
    time.sleep_ms(20)
    d = i2c.readfrom(0x44, 6)
    t = -45 + 175 * (d[0] << 8 | d[1]) / 65535
    h = 100 * (d[3] << 8 | d[4]) / 65535
    return t, h


def test_sensor():
    draw(4, 6, "温湿度センサー", "測定中", "息を吹きかけてみてください")
    say('--- [4] 温湿度センサー (SHT30 / 0x44) ---')

    first = None
    last = None
    t0 = time.ticks_ms()

    # 10秒間、値を見せ続ける。息を吹きかけると湿度が上がる。
    while time.ticks_diff(time.ticks_ms(), t0) < 10000:
        try:
            t, h = read_sht30()
        except Exception as e:
            draw(4, 6, "温湿度センサー", "読み取り失敗", str(e)[:24])
            say('    ERROR: ' + str(e))
            results.append(('Sensor', 'ERROR'))
            return wait_button()

        if first is None:
            first = (t, h)
        last = (t, h)

        draw(4, 6, "温湿度センサー",
             "{:.1f} ℃  {:.1f} %".format(t, h),
             "息を吹きかけて湿度が上がるか")

        # 湿度を LED バーにも出す
        n = int(h * 10 / 100 + 0.5)
        for i in range(10):
            rgb25.set_color(i, 0x0078ff if i < n else 0x000000)

        say('    {:.1f} C  {:.1f} %'.format(t, h))
        time.sleep_ms(1000)

    rgb25.fill_color(0x000000)
    results.append(('Sensor', '{:.1f}C {:.1f}%'.format(last[0], last[1])))

    draw(4, 6, "温湿度センサー", "{:.1f} ℃  {:.1f} %".format(last[0], last[1]),
         "A:次へ  B:もう一度")
    say('    → 息で湿度が上がれば OK。値だけ出ていても動作の証拠にならない')
    return wait_button()


# === テスト5  サーボ ========================================================
#
# ★注意★ サーボの電源はブレッドボードから外部供給されているか、
#         先に確認してください。M5Stack から取ると再起動します。

PULSE_MIN_US = 500
PULSE_MAX_US = 2400


def servo_us(us):
    servo.duty_ns(int(us * 1000))


def servo_deg(deg):
    deg = max(0, min(180, deg))
    servo_us(PULSE_MIN_US + (PULSE_MAX_US - PULSE_MIN_US) * deg / 180)


def test_servo():
    draw(5, 6, "サーボ", "90° に移動", "C で飛ばせます")
    say('--- [5] サーボ (PORT.B / G26) ---')
    say('    電源はブレッドボードから外部供給されていますか')

    servo_deg(90)
    time.sleep_ms(600)

    # ゆっくり往復させる。画面と LED に角度を出す。
    for target in (0, 180, 90):
        cur = 90 if target == 0 else (0 if target == 180 else 180)
        step = 10 if target > cur else -10
        for deg in range(cur, target + step, step):
            servo_deg(deg)
            n = int(deg * 10 / 180 + 0.5)
            for i in range(10):
                rgb25.set_color(i, 0xff8c00 if i < n else 0x000000)
            draw(5, 6, "サーボ",
                 "{} °   {:.0f} µs".format(deg, PULSE_MIN_US +
                                           (PULSE_MAX_US - PULSE_MIN_US) * deg / 180),
                 "画面が消えたら電源系のミス")
            time.sleep_ms(60)
        time.sleep_ms(400)

    rgb25.fill_color(0x000000)
    results.append(('Servo', '{}-{}us'.format(PULSE_MIN_US, PULSE_MAX_US)))

    draw(5, 6, "サーボ", "往復しましたか", "A:次へ  B:もう一度")
    say('    → 画面が消えなければ電源 OK')
    say('    → 両端で唸る場合は PULSE_MIN_US / PULSE_MAX_US を校正すること')
    return wait_button()


# === テスト6  シリアル送信 ==================================================

def test_serial():
    draw(6, 6, "シリアル送信", "JSON を10回送信", "IDE のターミナルを見てください")
    say('--- [6] シリアル送信 (JSON Lines) ---')
    say('    以下の10行がブラウザ側で受信できるか確認します')

    for i in range(10):
        try:
            t, h = read_sht30()
        except Exception:
            t, h = 0.0, 0.0
        payload = '{"t":%.1f,"h":%.1f,"n":%d}' % (t, h, i)
        print(payload)
        draw(6, 6, "シリアル送信", "{} / 10".format(i + 1), "")
        time.sleep_ms(500)

    results.append(('Serial', 'sent 10 lines'))
    draw(6, 6, "シリアル送信", "10行 送信しました", "A:結果へ")
    return wait_button()


# === 結果まとめ =============================================================

def show_summary():
    say('')
    say('=========================================')
    say('  自己診断の結果')
    say('=========================================')
    for name, value in results:
        say('  {:<12} {}'.format(name, value))
    say('=========================================')
    say('  この内容を記録用紙に貼り付けてください')
    say('')

    draw(0, 6, "診断おわり", "結果はターミナルに", "B で最初からやり直せます")
    rgb25.fill_color(0x003c00)

    while True:
        M5.update()
        if M5.BtnB.wasPressed():
            return
        time.sleep_ms(50)


# === setup ==================================================================

def setup():
    global page0, lblNo, lblTitle, lblResult, lblHint, rgb25, i2c, servo

    M5.begin()
    Widgets.setRotation(1)
    m5ui.init()

    page0 = m5ui.M5Page(bg_c=0x000000)
    lblNo = m5ui.M5Label("[0/6]", x=15, y=10,
                         text_c=0x888888, bg_c=0x000000, bg_opa=0,
                         font=lv.AlibabaSans_JP24, parent=page0)
    lblTitle = m5ui.M5Label("自己診断", x=15, y=55,
                            text_c=0xffffff, bg_c=0x000000, bg_opa=0,
                            font=lv.AlibabaSans_JP24, parent=page0)
    lblResult = m5ui.M5Label("", x=15, y=110,
                             text_c=0x33ff33, bg_c=0x000000, bg_opa=0,
                             font=lv.AlibabaSans_JP24, parent=page0)
    lblHint = m5ui.M5Label("A ボタンで開始", x=15, y=180,
                           text_c=0x888888, bg_c=0x000000, bg_opa=0,
                           font=lv.AlibabaSans_JP24, parent=page0)

    page0.screen_load()

    rgb25 = RGB(io=25, n=10, type="SK6812")
    rgb25.set_brightness(12)

    i2c = I2C(0, scl=Pin(33), sda=Pin(32), freq=100000)   # PORT.A
    servo = PWM(Pin(26), freq=50)                          # PORT.B

    say('')
    say('=========================================')
    say('  M5Stack Core2 for AWS  自己診断')
    say('  A:次へ  B:やり直し  C:飛ばす')
    say('=========================================')

    wait_button()


# === メイン =================================================================

TESTS = [test_led, test_brightness, test_i2c, test_sensor, test_servo, test_serial]


def run_all():
    i = 0
    while i < len(TESTS):
        action = TESTS[i]()
        if action == 'again':
            continue          # 同じテストをもう一度
        i += 1                # 'next' も 'skip' も次へ
    show_summary()


if __name__ == '__main__':
    try:
        setup()
        while True:
            results.clear()
            run_all()
    except (Exception, KeyboardInterrupt) as e:
        try:
            m5ui.deinit()
            from utility import print_error_msg
            print_error_msg(e)
        except ImportError:
            print("please update to latest firmware")


# ---------------------------------------------------------------------------
# 記録しておくこと（運営の実機検証時）
#
#   [1] LED     10個すべて点灯するか
#   [2] 明るさ  白100%で再起動しないか。発熱の程度
#   [3] I2C     scan() の実際の出力（そのままコピー）
#   [4] センサー 室温との差。息で湿度が上がるか
#   [5] サーボ  両端の実測パルス幅。動作中に画面が消えないか
#   [6] シリアル ブラウザ側で10行すべて受信できるか
# ---------------------------------------------------------------------------
