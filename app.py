import argparse
import json
import mimetypes
import secrets
import shutil
import threading
import webbrowser
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from http.client import HTTPConnection
from pathlib import Path
from datetime import datetime
from urllib.parse import urlsplit, parse_qs
from core import Database, apply, production_item, quote, report, shared_report

ROOT = Path(__file__).resolve().parent

def handler(db, origin):
    lock = threading.Lock()
    token = secrets.token_urlsafe(32)

    class Handler(BaseHTTPRequestHandler):
        def log_message(self, *_):
            pass

        def send(self, status, body, content_type):
            self.send_response(status)
            self.send_header('Content-Type', content_type)
            self.send_header('Content-Length', str(len(body)))
            self.send_header('Cache-Control', 'no-store')
            self.send_header('X-Content-Type-Options', 'nosniff')
            self.send_header('X-Frame-Options', 'DENY')
            self.send_header('Referrer-Policy', 'no-referrer')
            self.end_headers()
            self.wfile.write(body)

        def json(self, data, status=200):
            self.send(status, json.dumps(data, ensure_ascii=False).encode(), 'application/json; charset=utf-8')

        def allowed(self):
            return self.headers.get('Host') == origin.removeprefix('http://')

        def do_GET(self):
            if not self.allowed():
                return self.json({'erro': 'Endereço inválido.'}, 403)
            url = urlsplit(self.path)
            try:
                if url.path == '/':
                    page = (ROOT / 'interface.html').read_text(encoding='utf-8-sig')
                    return self.send(200, page.replace('__TOKEN__', token).encode(), 'text/html; charset=utf-8')
                if url.path.startswith('/assets/'):
                    asset = ROOT / 'assets' / url.path.removeprefix('/assets/')
                    if asset.parent != ROOT / 'assets' or asset.suffix not in ('.svg', '.png', '.ttf') or not asset.is_file():
                        return self.json({'erro': 'Arquivo não encontrado.'}, 404)
                    return self.send(200, asset.read_bytes(), mimetypes.guess_type(asset.name)[0] or 'application/octet-stream')
                args = parse_qs(url.query)
                params = [args.get(key, [''])[0] for key in ('medico', 'inicio', 'fim')]
                with lock:
                    s = db.read()
                if url.path == '/cadastros':
                    return self.json({k: v for k, v in s.items() if k not in ('Itens', 'Atendimentos', 'Pagamentos')})
                if url.path == '/cotacao':
                    return self.json(quote(s, {k: values[0] for k, values in args.items()}))
                if url.path == '/painel':
                    return self.json(report(s, *params))
                if url.path == '/lancamentos':
                    key = args.get('id', [''])[0]
                    if key:
                        item = production_item(s, key)
                        if not item:
                            raise ValueError('Este lançamento não pode ser editado neste formulário.')
                        row = next(r for r in s['Atendimentos'] if r['id'] == key)
                        return self.json(dict(row, servico=item['servico'], unitario=item['unitario'],
                                              clinica_unitario=item['clinica_unitario']))
                    rows = report(s, *params)['linhas']
                    return self.json(list(reversed(rows)))
                if url.path == '/relatorio':
                    return self.json(shared_report(s, *params))
                self.json({'erro': 'Página não encontrada.'}, 404)
            except (ValueError, KeyError, OSError) as e:
                self.json({'erro': str(e)}, 400)

        def do_POST(self):
            if not self.allowed() or self.headers.get('Origin') != origin or self.headers.get('X-Clinica') != token:
                return self.json({'erro': 'Reabra a página do sistema.'}, 403)
            if self.path != '/salvar':
                return self.json({'erro': 'Página não encontrada.'}, 404)
            try:
                size = int(self.headers.get('Content-Length', '0'))
                if not 0 < size <= 2_000_000:
                    raise ValueError('Tamanho do lote inválido.')
                p = json.loads(self.rfile.read(size))
                with lock:
                    s = db.read()
                    result = apply(s, p['tabela'], p['dados'])
                    if p['tabela'] in ('excluir_atendimento', 'excluir_cadastro') or (p['tabela'] == 'producao' and p['dados'].get('id')):
                        backups = db.path.parent / 'backups'
                        backups.mkdir(exist_ok=True)
                        stamp = datetime.now().strftime('%Y%m%d-%H%M%S-%f')
                        shutil.copyfile(db.path, backups / f'{db.path.stem}-{stamp}.xlsx')
                    db.save(s)
                self.json({'resultado': result})
            except PermissionError:
                self.json({'erro': 'Feche a base no Excel e tente novamente.'}, 409)
            except (ValueError, KeyError, TypeError, OSError) as e:
                self.json({'erro': str(e)}, 400)

    return Handler

def open_when_ready(port):
    connection = HTTPConnection('127.0.0.1', port, timeout=5)
    origin = f'http://127.0.0.1:{port}'
    try:
        connection.request('GET', '/')
        response = connection.getresponse()
        if response.status != 200:
            raise OSError(f'HTTP {response.status}')
        response.read()
    except OSError as error:
        print(f'O teste de conexão local falhou: {error}', flush=True)
        return
    finally:
        connection.close()
    if not webbrowser.open(origin):
        print(f'Abra manualmente no navegador: {origin}', flush=True)

def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--porta', type=int, default=0)
    parser.add_argument('--base', type=Path, default=ROOT / 'dados' / 'Clinica.xlsx')
    parser.add_argument('--sem-abrir', action='store_true')
    options = parser.parse_args()
    options.base.parent.mkdir(parents=True, exist_ok=True)
    db = Database(options.base)
    db.read()
    with ThreadingHTTPServer(('127.0.0.1', options.porta), BaseHTTPRequestHandler) as server:
        origin = f'http://127.0.0.1:{server.server_port}'
        server.RequestHandlerClass = handler(db, origin)
        print(f'Gestão da Clínica disponível em {origin}', flush=True)
        if not options.sem_abrir:
            threading.Thread(target=open_when_ready, args=(server.server_port,), daemon=True).start()
        try:
            server.serve_forever()
        except KeyboardInterrupt:
            pass

if __name__ == '__main__':
    main()

