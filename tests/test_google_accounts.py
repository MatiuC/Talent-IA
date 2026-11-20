import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch, Mock
from fastapi.testclient import TestClient
from backend import auth, database as db
from backend.config import settings
from backend.main import app
from backend import google_accounts as google

class GoogleAccountTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.old_db, self.old_cfg = settings.database_path, settings.google_credentials_path
        settings.database_path = Path(self.tmp.name)/'test.db'
        settings.google_credentials_path = Path(self.tmp.name)/'credentials.json'
        settings.google_credentials_path.write_text(json.dumps({'web': {'client_id':'test', 'client_secret':'test',
            'redirect_uris':['http://localhost:8001/google/callback']}}))
        db.init_db(); auth.init_auth(); google.init()
        self.client = TestClient(app,base_url='http://localhost',client=('127.0.0.1',1234),headers={'Origin':'http://localhost','X-TalentIA-Request':'1'})
        self.client.post('/api/auth/bootstrap',json={'name':'Test','email':'test@example.com','password':'test-password-123'})
    def tearDown(self):
        self.client.close()
        settings.database_path, settings.google_credentials_path = self.old_db, self.old_cfg
        self.tmp.cleanup()
    def test_state_binding_callback_and_account_isolation(self):
        self.assertEqual(self.client.get('/api/google/status').json()['modo'],'simulacion')
        response=self.client.post('/api/google/connect')
        self.assertEqual(response.status_code,200)
        from urllib.parse import urlparse,parse_qs
        params=parse_qs(urlparse(response.json()['url']).query)
        self.assertIn('code_challenge',params)
        state=params['state'][0]
        self.assertEqual(self.client.get('/google/callback?state=wrong&code=fake').status_code,400)
        token=Mock(); token.json.return_value={'access_token':'fake','refresh_token':'fake','expires_in':3600,'scope':' '.join(google.SCOPES)}
        with patch('backend.google_accounts.httpx.post',return_value=token):
            result=self.client.get('/google/callback',params={'state':state,'code':'fake'},follow_redirects=False)
        self.assertEqual(result.status_code,303)
        self.assertIsNotNone(google.account(1))
        self.assertIsNone(google.account(2))
        self.assertEqual(self.client.get('/api/google/status').json()['modo'],'real')
        self.assertEqual(self.client.get('/google/callback',params={'state':state,'code':'fake'}).status_code,400)
        self.client.post('/api/google/disconnect')
        self.assertIsNone(google.account(1))
    def test_missing_config_and_no_unauthenticated_access(self):
        settings.google_credentials_path.unlink()
        self.assertEqual(self.client.post('/api/google/connect').status_code,409)
        self.client.post('/api/auth/logout')
        self.assertEqual(self.client.get('/api/google/status').status_code,401)

    def test_calendar_then_gmail_and_partial_failure(self):
        import time
        from datetime import datetime, timezone
        google.save(1, {'access_token':'fake','expires_at':time.time()+3600})
        event=Mock(); event.json.return_value={'id':'event1','htmlLink':'https://calendar.google.com/event', 'hangoutLink':'https://meet.google.com/test'}
        mail=Mock()
        client=Mock(); client.post.side_effect=[event,mail]
        with patch('backend.google_accounts.httpx.Client') as factory:
            factory.return_value.__enter__.return_value=client
            result=google.RecruiterGoogle(1).agendar_entrevista('Test','test@example.com','Job',datetime.now(timezone.utc),30)
        self.assertTrue(result['calendar_ok']); self.assertTrue(result['gmail_ok'])
        self.assertEqual(result['payload']['meet_url'],'https://meet.google.com/test')
        self.assertIn('/calendar/',client.post.call_args_list[0].args[0])
        self.assertIn('/gmail/',client.post.call_args_list[1].args[0])
        client.post.side_effect=[event,RuntimeError('mail failed')]
        with patch('backend.google_accounts.httpx.Client') as factory:
            factory.return_value.__enter__.return_value=client
            result=google.RecruiterGoogle(1).agendar_entrevista('Test','test@example.com','Job',datetime.now(timezone.utc),30)
        self.assertTrue(result['calendar_ok']); self.assertFalse(result['gmail_ok'])
        client.post.side_effect=RuntimeError('calendar failed')
        client.reset_mock()
        with patch('backend.google_accounts.httpx.Client') as factory:
            factory.return_value.__enter__.return_value=client
            result=google.RecruiterGoogle(1).agendar_entrevista('Test','test@example.com','Job',datetime.now(timezone.utc),30)
        self.assertFalse(result['calendar_ok']); self.assertFalse(result['gmail_ok'])
        self.assertEqual(client.post.call_count,1)
