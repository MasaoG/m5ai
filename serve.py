"""ローカル検証用の簡易 Web サーバー

python -m http.server との違いは2点です。

  1. テキストファイルに charset=utf-8 を付けて返す
     → .py や .md をブラウザーで開いても文字化けしません

  2. キャッシュを無効にする
     → ファイルを書き換えたのに反映されない、という混乱を防げます

使いかた:

    cd C:\\starter
    python serve.py

    → http://localhost:8000/ が開けるようになります

ポートを変えたいときは引数で渡します:

    python serve.py 8080
"""

import http.server
import socketserver
import sys
import webbrowser

PORT = int(sys.argv[1]) if len(sys.argv) > 1 else 8000
HOST = '127.0.0.1'          # ローカルのみ。社内ネットワークには公開しない


class Handler(http.server.SimpleHTTPRequestHandler):

    # 拡張子と Content-Type の対応。
    # guess_type() はこの表を最優先で見るので、ここで charset を付けられます。
    extensions_map = {
        **http.server.SimpleHTTPRequestHandler.extensions_map,
        '.html': 'text/html; charset=utf-8',
        '.js':   'text/javascript; charset=utf-8',
        '.css':  'text/css; charset=utf-8',
        '.json': 'application/json; charset=utf-8',
        '.svg':  'image/svg+xml; charset=utf-8',
        # .py と .md はブラウザーで「読む」ことが目的なので text/plain にする。
        # text/x-python のままだとダウンロード扱いになる環境があります。
        '.py':   'text/plain; charset=utf-8',
        '.md':   'text/plain; charset=utf-8',
        '.txt':  'text/plain; charset=utf-8',
        '':      'application/octet-stream',
    }

    def end_headers(self):
        # 検証中はキャッシュを効かせない
        self.send_header('Cache-Control', 'no-store, must-revalidate')
        self.send_header('Pragma', 'no-cache')
        self.send_header('Expires', '0')
        super().end_headers()

    def log_message(self, fmt, *args):
        # 404 とエラーだけ出す。200 の羅列で画面が埋まらないように。
        status = str(args[1]) if len(args) > 1 else ''
        if not status.startswith('2'):
            super().log_message(fmt, *args)


def main():
    socketserver.TCPServer.allow_reuse_address = True
    with socketserver.TCPServer((HOST, PORT), Handler) as httpd:
        url = f'http://localhost:{PORT}/'
        print()
        print('=' * 52)
        print('  ローカルサーバーを起動しました')
        print('=' * 52)
        print(f'  トップ      {url}')
        print(f'  環境診断    {url}check.html')
        print(f'  事前準備    {url}setup.html')
        print()
        print('  停止するには Ctrl + C')
        print('=' * 52)
        print()
        try:
            webbrowser.open(url)
        except Exception:
            pass
        try:
            httpd.serve_forever()
        except KeyboardInterrupt:
            print('\n停止しました')


if __name__ == '__main__':
    main()
