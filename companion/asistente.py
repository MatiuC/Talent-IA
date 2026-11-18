"""TalentIA companion: only loopback, paired token, exact website origin."""
import argparse
import hmac
import json
import os
import secrets
import threading
import time
import urllib.request
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

MODEL = 'qwen3.5:2b'
parser = argparse.ArgumentParser()
parser.add_argument('--origin', default='http://localhost:8001')
args = parser.parse_args()
if not args.origin.startswith(('http://localhost:', 'http://127.0.0.1:', 'https://')) or args.origin.rstrip('/') != args.origin:
    raise SystemExit('Indica el origen exacto de TalentIA, sin ruta ni barra final.')
key_dir = Path(os.environ.get('LOCALAPPDATA', str(Path.home() / '.local' / 'share'))) / 'TalentIA'
key_dir.mkdir(parents=True, exist_ok=True)
key_file = key_dir / 'companion-key'
if not key_file.exists():
    key_file.write_text(secrets.token_urlsafe(32), encoding='utf-8')
KEY = key_file.read_text(encoding='utf-8').strip()
progress = {'running': False, 'status': 'Sin descarga activa', 'completed': 0, 'total': 0}
lock = threading.Lock()
inference = threading.Semaphore(1)

def ollama(path, data=None, timeout=600):
    request = urllib.request.Request('http://127.0.0.1:11434/api/'+path,
        data=json.dumps(data).encode() if data is not None else None,
        headers={'Content-Type': 'application/json'})
    return urllib.request.urlopen(request, timeout=timeout)

def lmstudio(path, data=None, timeout=600):
    request = urllib.request.Request('http://127.0.0.1:1234/v1/'+path,
        data=json.dumps(data).encode() if data is not None else None,
        headers={'Content-Type': 'application/json'})
    return urllib.request.urlopen(request, timeout=timeout)

def lm_model():
    try:
        with lmstudio('models', timeout=3) as r:
            names = [m['id'] for m in json.load(r).get('data', [])]
        return next((n for n in names if n == 'qwen3.5-2b'), None)
    except Exception:
        return None

def pull():
    try:
        local_model = lm_model()
        if local_model:
            progress.update(status='Qwen disponible en LM Studio. No necesita otra descarga.', completed=1, total=1, error=False)
            return
        with ollama('tags', timeout=5) as r:
            names = [m['name'] for m in json.load(r).get('models', [])]
        if MODEL not in names:
            with ollama('pull', {'model': MODEL, 'stream': True}, timeout=3600) as r:
                for line in r:
                    event = json.loads(line)
                    if event.get('error'):
                        raise RuntimeError(event['error'])
                    progress.update(status=event.get('status', ''), completed=event.get('completed', 0), total=event.get('total', 0))
        progress.update(status='IA lista. El modelo está guardado en este computador.', completed=1, total=1, error=False)
    except Exception as exc:
        progress.update(status='No se pudo preparar Qwen. Comprueba que Ollama esté abierto. '+str(exc), error=True)
    finally:
        progress['running'] = False

class Handler(BaseHTTPRequestHandler):
    def log_message(self, *_):
        pass  # Never log CVs or pairing credentials.

    def respond(self, status, data, html=False):
        self.send_response(status)
        origin = self.headers.get('Origin')
        if origin == args.origin:
            self.send_header('Access-Control-Allow-Origin', origin)
            self.send_header('Access-Control-Allow-Headers', 'Authorization, Content-Type')
            self.send_header('Access-Control-Allow-Methods', 'GET, POST, OPTIONS')
            self.send_header('Access-Control-Allow-Private-Network', 'true')
        self.send_header('Cache-Control', 'no-store')
        self.send_header('X-Frame-Options', 'DENY')
        self.send_header('Content-Type', 'text/html; charset=utf-8' if html else 'application/json')
        self.end_headers()
        self.wfile.write(data.encode() if html else json.dumps(data).encode())

    def allowed(self):
        return self.headers.get('Host') in {'127.0.0.1:11435', 'localhost:11435'}

    def authenticated(self):
        return self.allowed() and self.headers.get('Origin') in {None, args.origin} and hmac.compare_digest(self.headers.get('Authorization', ''), 'Bearer '+KEY)

    def do_OPTIONS(self):
        self.respond(204 if self.allowed() and self.headers.get('Origin') == args.origin else 403, {})

    def do_GET(self):
        if self.path == '/' and self.allowed() and not self.headers.get('Origin') and (self.headers.get('Sec-Fetch-Site', 'none') in {'none', 'same-origin'} or self.headers.get('Sec-Fetch-Mode') == 'navigate'):
            self.respond(200, '<!doctype html><meta charset="utf-8"><title>Asistente TalentIA</title><body style="font:18px system-ui;max-width:650px;margin:80px auto"><h1>Conecta tu IA con TalentIA</h1><p>Copia esta clave en Configuración → Motor de este computador. No la compartas.</p><textarea readonly style="width:100%;padding:20px">'+KEY+'</textarea><p>Mantén el asistente y Ollama abiertos mientras analizas. El modelo se conserva al cerrar.</p></body>', True)
            return
        if not self.authenticated():
            self.respond(403, {'error': 'Origen o clave de vinculación no válidos.'}); return
        if self.path != '/status':
            self.respond(404, {}); return
        local_model = lm_model()
        if local_model:
            self.respond(200, {**progress, 'ready': True, 'model': local_model, 'provider': 'LM Studio', 'status': 'Qwen disponible en LM Studio.'})
            return
        try:
            with ollama('tags', timeout=5) as r:
                ready = MODEL in [m['name'] for m in json.load(r).get('models', [])]
            self.respond(200, {**progress, 'ready': ready, 'model': MODEL})
        except Exception:
            self.respond(200, {**progress, 'ready': False, 'model': MODEL, 'status': 'Abre Ollama para continuar.'})

    def do_POST(self):
        if not self.authenticated():
            self.respond(403, {'error': 'Origen o clave de vinculación no válidos.'}); return
        try:
            size = int(self.headers.get('Content-Length', '0'))
            if not 0 <= size <= 2000000:
                raise ValueError('Solicitud demasiado grande.')
            data = json.loads(self.rfile.read(size) or '{}')
            if self.path == '/pull':
                with lock:
                    if not progress['running']:
                        progress.update(running=True, error=False, status='Preparando Qwen…')
                        threading.Thread(target=pull, daemon=True).start()
                self.respond(200, progress)
            elif self.path == '/chat':
                with inference:
                    local_model = lm_model()
                    if local_model:
                        started = time.monotonic()
                        with lmstudio('chat/completions', {'model': local_model, 'messages': data['messages'],
                                'response_format': {'type': 'json_schema', 'json_schema': {'name': 'talentia', 'strict': True, 'schema': data['format']}},
                                'stream': False, 'temperature': 0.1, 'max_tokens': 4096,
                                'reasoning': 'off', 'reasoning_effort': 'none'}) as r:
                            result = json.load(r)
                        if not result['choices'][0]['message'].get('content'):
                            raise ValueError('LM Studio devolvió una respuesta vacía. Desactiva Thinking para este modelo y vuelve a intentar.')
                        self.respond(200, {'message': result['choices'][0]['message'],
                            'eval_count': result.get('usage', {}).get('completion_tokens', 0),
                            'eval_duration': int((time.monotonic()-started)*1e9), 'provider': 'LM Studio', 'model': local_model})
                        return
                    with ollama('chat', {'model': MODEL, 'messages': data['messages'], 'format': data['format'],
                            'stream': False, 'think': False, 'options': {'temperature': 0.1, 'num_ctx': 8192}}) as r:
                        self.respond(200, json.load(r))
            else:
                self.respond(404, {})
        except Exception as exc:
            self.respond(502, {'error': str(exc)})

print('Asistente TalentIA disponible en http://127.0.0.1:11435')
print('Web autorizada:', args.origin)
ThreadingHTTPServer(('127.0.0.1', 11435), Handler).serve_forever()
