"""Authentication regression tests. All writes use a disposable database."""

from __future__ import annotations

import tempfile
import time
import unittest
from concurrent.futures import ThreadPoolExecutor
from contextlib import contextmanager
from pathlib import Path

from fastapi.testclient import TestClient

from backend import auth, database as db
from backend.config import settings
from backend.main import app


class AuthenticationTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix="talentia-auth-")
        self.old_db, self.old_uploads = settings.database_path, settings.uploads_dir
        settings.database_path = Path(self.temp.name) / "test.db"
        settings.uploads_dir = Path(self.temp.name) / "uploads"
        settings.uploads_dir.mkdir()
        db.init_db()
        auth.init_auth()
        self.client = self.new_client()
        self.account = {"name": "Recruiter Test", "email": "admin@example.test", "password": "test-password-123"}

    def tearDown(self):
        self.client.close()
        settings.database_path, settings.uploads_dir = self.old_db, self.old_uploads
        self.temp.cleanup()

    def new_client(self, host="127.0.0.1"):
        return TestClient(app, base_url="http://localhost", client=(host, 12345), headers={"X-TalentIA-Request": "1"})

    @contextmanager
    def client_context(self, host="127.0.0.1"):
        # The databases are initialized above. Do not start external model
        # clients or the process-global resource monitor for these API tests.
        client = self.new_client(host)
        try:
            yield client
        finally:
            client.close()

    def bootstrap(self):
        response = self.client.post("/api/auth/bootstrap", json=self.account)
        self.assertEqual(response.status_code, 201, response.text)
        return response

    def test_bootstrap_single_admin_and_no_unauthenticated_data(self):
        self.assertTrue(self.client.get("/api/auth/status").json()["setup_required"])
        for route in ("/api/vacantes", "/api/candidatos", "/api/entrevistas", "/api/metricas", "/api/health", "/docs", "/openapi.json"):
            self.assertEqual(self.client.get(route).status_code, 401, route)
        response = self.bootstrap()
        self.assertEqual(response.json()["user"]["role"], "admin")
        self.assertNotIn("password", response.text)
        self.assertFalse(self.client.get("/api/auth/status").json()["setup_required"])
        cookie = response.headers["set-cookie"].lower()
        self.assertIn("httponly", cookie)
        self.assertIn("samesite=strict", cookie)
        self.assertEqual(self.client.post("/api/auth/bootstrap", json=self.account).status_code, 409)
        created = self.client.post("/api/vacantes", json={"titulo": "Ingeniería de software"})
        self.assertEqual(created.status_code, 201, created.text)
        self.assertEqual(len(self.client.get("/api/vacantes").json()), 1)

    def test_bootstrap_rejects_remote_clients_and_concurrent_creation(self):
        with self.client_context("192.0.2.5") as remote:
            self.assertEqual(remote.post("/api/auth/bootstrap", json=self.account).status_code, 403)
        def create(number):
            with self.client_context() as client:
                return client.post("/api/auth/bootstrap", json={**self.account, "email": f"admin{number}@example.test"}).status_code
        with ThreadPoolExecutor(max_workers=2) as pool:
            results = list(pool.map(create, (1, 2)))
        self.assertEqual(sorted(results), [201, 409])

    def test_sessions_persist_revoke_and_expire(self):
        self.bootstrap()
        token = self.client.cookies.get(auth.COOKIE_NAME)
        with self.client_context() as second:
            second.cookies.set(auth.COOKIE_NAME, token)
            self.assertEqual(second.get("/api/auth/me").status_code, 200)
            self.assertEqual(self.client.post("/api/auth/logout").status_code, 200)
            self.assertEqual(second.get("/api/auth/me").status_code, 401)
        self.assertEqual(self.client.post("/api/auth/login", json={**self.account, "password": "wrong"}).status_code, 401)
        self.assertEqual(self.client.post("/api/auth/login", json=self.account).status_code, 200)
        self.assertNotEqual(self.client.cookies.get(auth.COOKIE_NAME), token)
        with db.conexion() as conn:
            conn.execute("UPDATE auth_sessions SET expires_at=?", (int(time.time()) - 1,))
        self.assertEqual(self.client.get("/api/auth/me").status_code, 401)
        with db.conexion() as conn:
            row = conn.execute("SELECT password_hash FROM auth_users").fetchone()
        self.assertNotIn(self.account["password"], row["password_hash"])

    def test_cross_origin_mutations_are_rejected(self):
        self.assertEqual(self.client.post("/api/auth/bootstrap", json=self.account,
                                         headers={"Origin": "https://other.example"}).status_code, 403)
        self.bootstrap()
        blocked = self.client.post("/api/vacantes", json={"titulo": "No crear"}, headers={"Origin": "https://other.example"})
        self.assertEqual(blocked.status_code, 403)
        self.assertEqual(self.client.get("/api/vacantes").json(), [])
        accepted = self.client.post("/api/vacantes", json={"titulo": "Sí crear"}, headers={"Origin": "http://localhost"})
        self.assertEqual(accepted.status_code, 201)
        with self.client_context() as no_header:
            no_header.headers.pop("X-TalentIA-Request")
            self.assertEqual(no_header.post("/api/auth/login", json=self.account).status_code, 403)

    def test_only_admin_can_add_recruiters(self):
        self.bootstrap()
        recruiter = {"name": "Second Recruiter", "email": "recruiter@example.test", "password": "another-password-123"}
        self.assertEqual(self.client.post("/api/auth/users", json=recruiter).status_code, 201)
        with self.client_context() as staff:
            response = staff.post("/api/auth/login", json=recruiter)
            self.assertEqual(response.status_code, 200)
            self.assertEqual(response.json()["user"]["role"], "recruiter")
            self.assertEqual(staff.get("/api/vacantes").status_code, 200)
            self.assertEqual(staff.get("/api/auth/users").status_code, 403)
            self.assertEqual(staff.post("/api/auth/users", json={**recruiter, "email": "third@example.test"}).status_code, 403)

    def test_login_rate_limit_survives_new_clients(self):
        self.bootstrap()
        self.client.post("/api/auth/logout")
        with self.client_context() as attacker:
            for _ in range(auth.LOGIN_LIMIT):
                self.assertEqual(attacker.post("/api/auth/login", json={**self.account, "password": "wrong"}).status_code, 401)
        with self.client_context() as attacker:
            blocked = attacker.post("/api/auth/login", json={**self.account, "password": "wrong"})
            self.assertEqual(blocked.status_code, 429)
            self.assertIn("retry-after", blocked.headers)

    def test_pdf_access_checks_auth_and_file_containment(self):
        vacancy = db.crear_vacante("Ingeniería", "", [], 0)
        pdf = settings.uploads_dir / "example.pdf"
        pdf.write_bytes(b"%PDF-1.4\nexample")
        candidate = db.crear_candidato(vacancy, "example.pdf", str(pdf), "PENDIENTE_REVISION", "test")
        route = f"/api/candidatos/{candidate}/pdf"
        self.assertEqual(self.client.get(route).status_code, 401)
        self.bootstrap()
        self.assertEqual(self.client.get(route).content, pdf.read_bytes())
        outside = Path(self.temp.name) / "outside.pdf"
        outside.write_bytes(b"private")
        with db.conexion() as conn:
            conn.execute("UPDATE candidatos SET ruta_archivo=? WHERE id=?", (str(outside), candidate))
        self.assertEqual(self.client.get(route).status_code, 404)


if __name__ == "__main__":
    unittest.main()
