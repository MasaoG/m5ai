# HO-6  多軸を制御する（Pan-Tilt / PCA9685）
#
# ---------------------------------------------------------------------------
# 接続（Grove ハブは使いません）
#
#   PORT.A（赤・本体上部）  G32=SDA / G33=SCL  →  ENV Ⅲ        … I2C バス0
#   PORT.C（青・ベース側面） G14=SDA / G13=SCL  →  Pan-Tilt 基板 … I2C バス1
#
#   ESP32 には I2C コントローラが2系統あるので、2つのポートを
#   独立したバスとして同時に使えます。
#
#   PORT.B（G26/G36）は I2C には使えません。
#   G36 が入力専用ピンで、双方向通信が必要な SDA を割り当てられないためです。
#   PORT.B はサーボの PWM 信号（HO-3）用にとっておきます。
#
# ---------------------------------------------------------------------------
# バスを分けると何が嬉しいか
#
#   ENV Ⅲ の気圧センサー QMP6988 は 0x70 です。
#   PCA9685 も初期状態で「All Call」という共通アドレス 0x70 に応答します。
#
#   同じバスに繋ぐと、0x70 宛の通信に2つのデバイスが応答して壊れます。
#   ところが別々のバスに分ければ、そもそも出会いません。
#
#   「同じ通りに同じ番地の家が2軒あると郵便が届かないが、
#     別の通りなら問題ない」ということです。
#
#   ただし ALLCALL は切っておきます。理由は下の初期化コードに書いてあります。
#
# ---------------------------------------------------------------------------
# ★接続する前に必ず測ること★
#
#   PORT.C に繋ぐ前に、Pan-Tilt 基板側の SDA / SCL のアイドル電圧を
#   テスタで測ってください。
#
#     3.3V → そのまま繋いでよい
#     5V   → I2C レベルシフタが必須
#
#   ESP32 は 5V トレラントではありません。直結すると壊れる可能性があります。
# ---------------------------------------------------------------------------

import os, sys, io
import M5
from M5 import *
import m5ui
import lvgl as lv
from machine import I2C, Pin
import math
import time


page0 = None
lblBus = None
lblPos = None
i2c_env = None      # バス0: ENV Ⅲ
i2c_pca = None      # バス1: Pan-Tilt
pca = None


# === PCA9685 ドライバ =======================================================

class PCA9685:
    """16チャンネル PWM ドライバ"""

    def __init__(self, i2c, addr=0x40):
        self.i2c = i2c
        self.addr = addr

        # MODE1(0x00) に 0x00 を書くと、SLEEP 解除と同時に ALLCALL が無効になる。
        #
        # バスを分けた今、0x70 の衝突は起きません。
        # それでも切っておくのは、
        #   ・将来このバスに別のデバイスを足すかもしれない
        #   ・scan() の結果に余計なアドレスが出ると紛らわしい
        # という理由です。「効いていないが害もない」設定は残しておきます。
        self._w(0x00, 0x00)
        time.sleep_ms(10)

    def _w(self, reg, val):
        self.i2c.writeto_mem(self.addr, reg, bytes([val]))

    def _r(self, reg):
        return self.i2c.readfrom_mem(self.addr, reg, 1)[0]

    def set_freq(self, hz=50):
        """PWM 周期を設定する。

        ★必ず呼ぶこと★
        初期状態の周期は 50Hz ではありません。
        呼び忘れるとサーボが正しく動きません。
        """
        prescale = int(round(25000000.0 / (4096 * hz))) - 1   # 50Hz → 121

        old = self._r(0x00)
        self._w(0x00, (old & 0x7F) | 0x10)   # SLEEP に入れないと変更できない
        self._w(0xFE, prescale)
        self._w(0x00, old)
        time.sleep_ms(5)
        self._w(0x00, 0xA0)                  # RESTART + オートインクリメント

    def set_us(self, ch, us):
        """チャンネル ch にパルス幅 us(µs) を出力する"""
        count = int(us * 4096 / 20000)       # 20ms を 4096 分割
        count = max(0, min(4095, count))
        self.i2c.writeto_mem(
            self.addr, 0x06 + 4 * ch,
            bytes([0, 0, count & 0xFF, count >> 8])
        )


# === 校正定数 ===============================================================
# すべて実機で確認した値に書き換えてください。

PULSE_MIN_US = 600
PULSE_MAX_US = 2400

PAN_CH = 0
TILT_CH = 1

# ★可動範囲のリミット★
# ブラケットの構造上、180度全域は回りません。
# 範囲外の指令を出し続けるとサーボが唸り、ギヤが傷みます。
PAN_LIMIT = (30, 150)
TILT_LIMIT = (70, 130)


def move(ch, deg, limit):
    deg = max(limit[0], min(limit[1], deg))
    us = PULSE_MIN_US + (PULSE_MAX_US - PULSE_MIN_US) * deg / 180
    pca.set_us(ch, us)


def look(pan, tilt):
    move(PAN_CH, pan, PAN_LIMIT)
    move(TILT_CH, tilt, TILT_LIMIT)
    lblPos.set_text(str('PAN {:.0f}°  TILT {:.0f}°'.format(pan, tilt)))


# === センサー（バス0） ======================================================

def read_sht30():
    i2c_env.writeto(0x44, b'\x2C\x06')
    time.sleep_ms(20)
    d = i2c_env.readfrom(0x44, 6)
    t = -45 + 175 * (d[0] << 8 | d[1]) / 65535
    h = 100 * (d[3] << 8 | d[4]) / 65535
    return t, h


# === setup ==================================================================

def setup():
    global page0, lblBus, lblPos, i2c_env, i2c_pca, pca

    M5.begin()
    Widgets.setRotation(1)
    m5ui.init()

    page0 = m5ui.M5Page(bg_c=0x000000)
    lblBus = m5ui.M5Label("スキャン中", x=15, y=40,
                          text_c=0xffffff, bg_c=0x000000, bg_opa=0,
                          font=lv.AlibabaSans_JP24, parent=page0)
    lblPos = m5ui.M5Label("-- ", x=15, y=130,
                          text_c=0xff8c00, bg_c=0x000000, bg_opa=0,
                          font=lv.AlibabaSans_JP24, parent=page0)
    page0.screen_load()

    # --- バス0: PORT.A（ENV Ⅲ） ---
    i2c_env = I2C(0, scl=Pin(33), sda=Pin(32), freq=100000)

    # --- バス1: PORT.C（Pan-Tilt） ---
    # PORT.C の 1番ピン（黄）= G14 を SDA、2番ピン（白）= G13 を SCL と想定。
    # 実機で入れ替わっていたら、下の2行を交換してください。
    i2c_pca = I2C(1, scl=Pin(13), sda=Pin(14), freq=100000)

    # --- 両方のバスを確認する ---
    env_found = i2c_env.scan()
    pca_found = i2c_pca.scan()

    print('バス0 (PORT.A):', [hex(a) for a in env_found])
    print('バス1 (PORT.C):', [hex(a) for a in pca_found])

    # 同じ 0x70 が両方に出ますが、別のバスなので問題ありません
    print('0x44 SHT30   :', 'OK' if 0x44 in env_found else 'NG')
    print('0x40 PCA9685 :', 'OK' if 0x40 in pca_found else 'NG')

    lblBus.set_text(str('バス0 {}件 / バス1 {}件'.format(
        len(env_found), len(pca_found))))

    if 0x40 not in pca_found:
        lblBus.set_text(str('PCA9685 が見つかりません'))
        print('→ PORT.C の配線、SDA/SCL の向き、電源を確認してください')
        return

    pca = PCA9685(i2c_pca)
    pca.set_freq(50)
    look(90, 100)


# === loop ===================================================================
#
# 課題: パンとチルトを協調させて円を描く

_t = 0.0


def loop():
    global _t

    M5.update()

    if pca is None:
        time.sleep_ms(500)
        return

    pan = 90 + 40 * math.cos(_t)
    tilt = 100 + 20 * math.sin(_t)
    look(pan, tilt)

    # 別バスのセンサーも同時に読める
    if int(_t * 10) % 50 == 0:
        try:
            t, h = read_sht30()
            print('{:.1f}C {:.1f}%'.format(t, h))
        except Exception:
            pass

    _t += 0.1
    time.sleep_ms(50)


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
# HO-3 との違い
# ===========================================================================
#
#   HO-3（生 PWM）    duty_ns() でパルス幅を直接指定 / GPIO 1本で1軸
#   HO-6（PCA9685）   レジスタにカウント値を書く    / I2C 2本で16軸まで
#
#   やっていることは同じで、パルスを作る担当が CPU から専用 IC に
#   移っただけです。1軸だけなら生 PWM のほうが部品も概念も少なくて済みます。
#
# ===========================================================================
# 3つのポートを同時に使う
# ===========================================================================
#
#   PORT.A  I2C(0)  ENV Ⅲ              温湿度
#   PORT.B  PWM     SG92R              1軸サーボ（HO-3）
#   PORT.C  I2C(1)  Pan-Tilt 制御基板   2軸サーボ
#
#   この3つは互いに干渉しません。全部同時に動かせます。
#
# ===========================================================================
# 改造課題
# ===========================================================================
#
# 1. 湿度に応じてパンの角度を変える（バス0とバス1を繋ぐ）
#
#      t, h = read_sht30()
#      look(30 + h * 120 / 100, 100)
#
# 2. HO-5 のスキーマを {pan, tilt, msg} に変えて、AI に方向を決めさせる
#
#      pan:  { type: 'integer', minimum: 30, maximum: 150 },
#      tilt: { type: 'integer', minimum: 70, maximum: 130 }
#
#    minimum / maximum には必ず実機で測ったリミット値を入れること。
#    「機械の安全範囲を AI の出力制約として書く」設計パターンです。
#
# 3. 8 の字を描く
#
# 考えてみよう:
#   もし PORT.C が使えず、1つのバスに両方繋ぐしかなかったら、
#   0x70 の衝突をどう回避しますか。
#
# ===========================================================================
# つまずいたら
# ===========================================================================
#
#   唸る                 → すぐ電源を切る。PAN_LIMIT / TILT_LIMIT を見直す
#   動かない             → set_freq(50) を呼んだか / 外部電源が入っているか
#   バス1に何も出ない    → PORT.C の配線。SDA/SCL が逆かもしれない
#                          （Pin(13) と Pin(14) を入れ替えて試す）
#   バス1で ETIMEDOUT    → プルアップ不足、またはレベルシフタが必要
#   バス0が壊れた        → PORT.C に5Vバスを直結した可能性。要テスタ確認
#   画面が真っ黒         → page0.screen_load() を呼んだか
# ---------------------------------------------------------------------------
