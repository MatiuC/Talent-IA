"""Per-recruiter Google OAuth. Credentials remain in the ignored local database."""
import base64
import hashlib
import json
import os
import secrets
import time
from email.message import EmailMessage
from urllib.parse import urlencode

import httpx
from fastapi import HTTPException, Request
from fastapi.responses import RedirectResponse
from backend import database as db
from backend.config import settings
from backend.google_services import GoogleServices, SCOPES


def init():
    with db.conexion() as c:
        c.executescript('''CREATE TABLE IF NOT EXISTS google_accounts (
            user_id INTEGER PRIMARY KEY, token TEXT NOT NULL);
            CREATE TABLE IF NOT EXISTS google_oauth_states (
            state TEXT PRIMARY KEY, user_id INTEGER NOT NULL, verifier TEXT NOT NULL, expires REAL NOT NULL);''')


def config():
    try:
        data = json.loads(settings.google_credentials_path.read_text(encoding='utf-8'))['web']
        redirect = os.environ.get('GOOGLE_REDIRECT_URI', 'http://localhost:8001/google/callback')
        if redirect not in data.get('redirect_uris', []):
            raise ValueError()
        return data, redirect
    except (OSError, ValueError, KeyError):
        raise HTTPException(409, 'Configura credentials.json de tipo Aplicación web y su URL de retorno. Consulta GOOGLE.md.')


def account(user_id):
    init()
    with db.conexion() as c:
        row = c.execute('SELECT token FROM google_accounts WHERE user_id=?', (user_id,)).fetchone()
    return json.loads(row['token']) if row else None


def save(user_id, token):
    with db.conexion() as c:
        c.execute('INSERT OR REPLACE INTO google_accounts VALUES (?,?)', (user_id, json.dumps(token)))


def access_token(user_id):
    token = account(user_id)
    if not token:
        raise ValueError('Conecta tu cuenta de Google en Configuración.')
    if token.get('expires_at', 0) < time.time() + 60:
        cfg, _ = config()
        r = httpx.post('https://oauth2.googleapis.com/token', data={
            'grant_type': 'refresh_token', 'refresh_token': token.get('refresh_token', ''),
            'client_id': cfg['client_id'], 'client_secret': cfg['client_secret']}, timeout=20)
        if r.status_code != 200:
            raise ValueError('Tu autorización de Google venció. Vuelve a conectar tu cuenta.')
        token.update(r.json()); token['expires_at'] = time.time() + token.get('expires_in', 3600)
        save(user_id, token)
    return token['access_token']


class RecruiterGoogle(GoogleServices):
    def __init__(self, user_id):
        super().__init__(); self.user_id = user_id

    @property
    def modo(self):
        return 'real' if account(self.user_id) else 'simulacion'

    @property
    def motivo_simulacion(self):
        return 'Conecta tu cuenta de Google para crear eventos y enviar invitaciones.'

    def estado(self):
        try:
            config(); configured = True
        except HTTPException:
            configured = False
        return {'modo': self.modo, 'configurado': configured, 'motivo': self.motivo_simulacion}

    def agendar_entrevista(self, nombre_candidato, email_candidato, titulo_vacante,
                          fecha_hora, duracion_minutos, modalidad='Videollamada', notas=''):
        payload = self.construir_payloads(nombre_candidato, email_candidato, titulo_vacante,
                                         fecha_hora, duracion_minutos, modalidad, notas)
        result = dict(modo=self.modo, calendar_ok=False, gmail_ok=False, evento_id='',
                      enlace_evento='', mensaje='', payload=payload)
        if result['modo'] == 'simulacion':
            result['mensaje'] = 'Entrevista simulada: conecta tu cuenta de Google para enviar.'
            return result
        try:
            token = access_token(self.user_id)
            with httpx.Client(headers={'Authorization': 'Bearer '+token}, timeout=30) as client:
                event = payload['calendar_event']
                video = any(word in modalidad.lower() for word in ('video', 'meet', 'virtual'))
                if video:
                    event['conferenceData'] = {'createRequest': {'requestId': secrets.token_hex(16),
                        'conferenceSolutionKey': {'type': 'hangoutsMeet'}}}
                response = client.post('https://www.googleapis.com/calendar/v3/calendars/primary/events',
                    params={'sendUpdates': 'all', 'conferenceDataVersion': 1}, json=event)
                response.raise_for_status(); created = response.json()
                result.update(calendar_ok=True, evento_id=created['id'], enlace_evento=created.get('htmlLink', ''))
                meet = created.get('hangoutLink', '')
                if meet:
                    payload['meet_url'] = meet
                body = payload['email']['cuerpo']
                if meet:
                    body += '\nVideollamada: '+meet+'\n'
                if result['enlace_evento']:
                    body += '\nEvento en Google Calendar: '+result['enlace_evento']+'\n'
                payload['email']['cuerpo'] = body
                message = EmailMessage()
                message['To'] = email_candidato
                message['Subject'] = payload['email']['asunto']
                message.set_content(body)
                response = client.post('https://gmail.googleapis.com/gmail/v1/users/me/messages/send',
                    json={'raw': base64.urlsafe_b64encode(message.as_bytes()).decode()})
                response.raise_for_status()
                result.update(gmail_ok=True, mensaje='Evento creado en tu Calendar e invitación enviada desde tu Gmail.')
        except Exception:
            result['mensaje'] = ('El evento se creó, pero Gmail no confirmó el envío. Revisa Calendar antes de reintentar.'
                if result['calendar_ok'] else 'Google no confirmó la creación del evento. Revisa tu conexión y permisos.')
        return result


def install(app):
    @app.get('/api/google/status')
    def status(request: Request):
        return RecruiterGoogle(request.state.user['id']).estado()

    @app.post('/api/google/connect')
    def connect(request: Request):
        cfg, redirect = config(); init()
        state = secrets.token_urlsafe(32); verifier = secrets.token_urlsafe(48)
        with db.conexion() as c:
            c.execute('DELETE FROM google_oauth_states WHERE expires < ?', (time.time(),))
            c.execute('INSERT INTO google_oauth_states VALUES (?,?,?,?)',
                (state, request.state.user['id'], verifier, time.time()+600))
        challenge = base64.urlsafe_b64encode(hashlib.sha256(verifier.encode()).digest()).decode().rstrip('=')
        from fastapi.responses import JSONResponse
        result = JSONResponse({'url': 'https://accounts.google.com/o/oauth2/v2/auth?'+urlencode({
            'client_id': cfg['client_id'], 'redirect_uri': redirect, 'response_type': 'code',
            'scope': ' '.join(SCOPES), 'state': state, 'access_type': 'offline',
            'prompt': 'consent select_account', 'code_challenge': challenge, 'code_challenge_method': 'S256'})})
        result.set_cookie('talentia_google_state', state, httponly=True, samesite='lax',
                          secure=redirect.startswith('https:'), max_age=600, path='/google/callback')
        return result

    @app.get('/google/callback')
    def callback(request: Request):
        init(); state = request.query_params.get('state', '')
        if not state or not secrets.compare_digest(state, request.cookies.get('talentia_google_state', '')):
            raise HTTPException(400, 'La autorización no corresponde a este navegador. Conecta de nuevo.')
        with db.transaccion() as c:
            row = c.execute('SELECT * FROM google_oauth_states WHERE state=? AND expires>?', (state,time.time())).fetchone()
            c.execute('DELETE FROM google_oauth_states WHERE state=?', (state,))
        if not row or request.query_params.get('error'):
            raise HTTPException(400, 'Autorización cancelada o vencida. Vuelve a Configuración.')
        cfg, redirect = config()
        try:
            response = httpx.post('https://oauth2.googleapis.com/token', data={
                'client_id': cfg['client_id'], 'client_secret': cfg['client_secret'],
                'grant_type': 'authorization_code', 'code': request.query_params.get('code', ''),
                'redirect_uri': redirect, 'code_verifier': row['verifier']}, timeout=20)
            response.raise_for_status(); token = response.json()
            if not set(SCOPES).issubset(set(token.get('scope','').split())):
                raise ValueError('missing scopes')
            token['expires_at'] = time.time()+token.get('expires_in',3600)
            save(row['user_id'], token)
        except Exception:
            raise HTTPException(400, 'No se pudo conectar Google. Autoriza Calendar y Gmail e inténtalo nuevamente.')
        result = RedirectResponse('/#settings', status_code=303)
        result.delete_cookie('talentia_google_state', path='/google/callback')
        result.headers['Referrer-Policy'] = 'no-referrer'
        return result

    @app.post('/api/google/disconnect')
    def disconnect(request: Request):
        init()
        with db.transaccion() as c:
            c.execute('DELETE FROM google_accounts WHERE user_id=?', (request.state.user['id'],))
            c.execute('DELETE FROM google_oauth_states WHERE user_id=?', (request.state.user['id'],))
        return {'ok': True}
