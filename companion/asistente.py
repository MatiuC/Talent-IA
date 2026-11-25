"""TalentIA companion: only loopback, paired token, exact website origin."""
import argparse
import hmac
import json
import os
import secrets
import threading
import time
import urllib.error
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
_lm_cache = {'details': None, 'ts': 0.0}
MAX_CONCURRENT = max(1, min(8, int(os.environ.get('TALENTIA_CONCURRENCY', '3'))))
inference = threading.Semaphore(MAX_CONCURRENT)

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

def lm_details(force=False):
    now = time.monotonic()
    if not force:
        with lock:
            if _lm_cache['details'] is not None and (now - _lm_cache['ts']) < 30.0:
                return dict(_lm_cache['details'])
    try:
        with urllib.request.urlopen('http://127.0.0.1:1234/api/v1/models', timeout=10) as r:
            models = json.load(r).get('models', [])
        loaded = [(m, i) for m in models if m.get('type') == 'llm' for i in m.get('loaded_instances', [])]
        preferred = os.environ.get('LM_STUDIO_MODEL', '')
        if preferred:
            loaded = [(m, i) for m, i in loaded if preferred in {m.get('key'), i.get('id')}]
        if not loaded:
            with lock:
                _lm_cache['details'] = None
                _lm_cache['ts'] = now
            return None
        m, instance = loaded[0]
        info = {
            'id': instance['id'],
            'reasoning_off': 'off' in m.get('capabilities', {}).get('reasoning', {}).get('allowed_options', []),
            'parallel': int(instance.get('config', {}).get('parallel') or 1),
            'context_length': int(instance.get('config', {}).get('context_length') or 8192),
        }
        with lock:
            _lm_cache['details'] = dict(info)
            _lm_cache['ts'] = time.monotonic()
        return info
    except Exception:
        if not force:
            with lock:
                if _lm_cache['details'] is not None and (now - _lm_cache['ts']) < 300.0:
                    return dict(_lm_cache['details'])
        return None


def lm_model(force=False):
    model = lm_details(force=force)
    return model['id'] if model else None

def pull():
    try:
        local_model = lm_model()
        if local_model:
            progress.update(status='Modelo disponible en LM Studio. No necesita otra descarga.', completed=1, total=1, error=False)
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

def _fit_messages_for_slot(messages, context_length, parallel, scale=1.0):
    """Ajusta el texto del CV al presupuesto real de tokens por ranura paralela."""
    slots = max(1, int(parallel or 1), MAX_CONCURRENT)
    slot_tokens = max(1024, int(context_length or 8192) // slots)
    max_cv_chars = max(1800, int((slot_tokens - 900) * 3.5 * scale))
    max_out_tokens = min(900, max(350, slot_tokens // 3))
    marker_start = '<<<INICIO_CV'
    marker_end = 'FIN_CV>>>'
    fitted = []
    for msg in messages:
        content = msg.get('content', '')
        if isinstance(content, str) and marker_start in content and marker_end in content:
            before, rest = content.split(marker_start, 1)
            cv_body, after = rest.split(marker_end, 1)
            cv_clean = cv_body.strip()
            if len(cv_clean) > max_cv_chars:
                head_len = int(max_cv_chars * 0.7)
                tail_len = max(0, max_cv_chars - head_len)
                note = '\n[... sección intermedia resumida por límite de contexto ...]\n'
                trimmed = (cv_clean[:head_len].rstrip() + note + cv_clean[-tail_len:].lstrip()) if tail_len else cv_clean[:head_len].rstrip()
                content = f"{before}{marker_start}\n{trimmed}\n{marker_end}{after}"
        fitted.append({**msg, 'content': content})
    return fitted, max_out_tokens


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
            self.respond(200, '<!doctype html><meta charset="utf-8"><title>Asistente TalentIA</title><body style="font:18px system-ui;max-width:650px;margin:80px auto"><h1>Conecta tu IA con TalentIA</h1><p>Copia esta clave en Configuración → Motor de este computador. No la compartas.</p><textarea readonly style="width:100%;padding:20px">'+KEY+'</textarea><p>Mantén el asistente y LM Studio (u Ollama, si lo usas) abiertos mientras analizas. El modelo se conserva al cerrar.</p></body>', True)
            return
        if not self.authenticated():
            self.respond(403, {'error': 'Origen o clave de vinculación no válidos.'}); return
        if self.path != '/status':
            self.respond(404, {}); return
        local_model = lm_model(force=True)
        if local_model:
            self.respond(200, {**progress, 'ready': True, 'model': local_model, 'provider': 'LM Studio', 'concurrency': MAX_CONCURRENT, 'status': 'Modelo disponible en LM Studio.'})
            return
        try:
            with ollama('tags', timeout=5) as r:
                ready = MODEL in [m['name'] for m in json.load(r).get('models', [])]
            self.respond(200, {**progress, 'ready': ready, 'model': MODEL})
        except Exception:
            self.respond(200, {**progress, 'ready': False, 'model': MODEL, 'status': 'Carga un modelo de conversación en LM Studio y activa su servidor local (puerto 1234).'})

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
                    details = lm_details(force=False)
                    local_model = details['id'] if details else None
                    if local_model:
                        started = time.monotonic()
                        extra_reasoning = {'reasoning': 'off', 'reasoning_effort': 'none'} if details['reasoning_off'] else {}
                        result = None
                        for scale in (1.0, 0.65):
                            fitted_messages, max_out = _fit_messages_for_slot(
                                data['messages'], details['context_length'], details['parallel'], scale=scale
                            )
                            try:
                                with lmstudio('chat/completions', {
                                    'model': local_model,
                                    'messages': fitted_messages,
                                    'response_format': {'type': 'json_schema', 'json_schema': {'name': 'talentia', 'strict': True, 'schema': data['format']}},
                                    'stream': False,
                                    'temperature': 0.1,
                                    'max_tokens': max_out,
                                    **extra_reasoning,
                                }) as r:
                                    result = json.load(r)
                                break
                            except urllib.error.HTTPError as err:
                                err_body = err.read().decode('utf-8', 'ignore')
                                if scale == 1.0 and 'context size' in err_body.lower():
                                    continue
                                raise ValueError(f'HTTP {err.code}: {err_body.strip()[:300] or err.reason}') from err
                            except urllib.error.URLError as err:
                                with lock:
                                    _lm_cache['details'] = None
                                    _lm_cache['ts'] = 0.0
                                raise ValueError(f'LM Studio no responde en 127.0.0.1:1234 ({err.reason}).') from err
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
        except urllib.error.HTTPError as exc:
            detail = ''
            try:
                detail = exc.read().decode('utf-8', 'ignore').strip()[:300]
            except Exception:
                pass
            self.respond(502, {'error': f'HTTP {exc.code}: {detail or exc.reason}'})
        except Exception as exc:
            self.respond(502, {'error': str(exc)})

print('Asistente TalentIA disponible en http://127.0.0.1:11435')
print('Web autorizada:', args.origin)
print('Concurrencia maxima:', MAX_CONCURRENT)
ThreadingHTTPServer(('127.0.0.1', 11435), Handler).serve_forever()
