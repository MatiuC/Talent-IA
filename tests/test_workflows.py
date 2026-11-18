"""End-to-end API flows with disposable data and deterministic model output.

The PDF parser, persistence and HTTP routes run for real. Only external model
and Google calls are substituted. No files or records in the user's data
directory are touched.
"""

from __future__ import annotations

import tempfile
import unittest
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timedelta, timezone
from pathlib import Path
from unittest.mock import AsyncMock, Mock, patch

import pymupdf
from fastapi.testclient import TestClient

from backend import auth, database as db
from backend.config import settings
from backend.llm_agent import LLMError, MetricasInferencia
from backend.main import app
from backend.schemas import EvaluacionCV


class WorkflowTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix="talentia-workflow-")
        self.old_db, self.old_uploads = settings.database_path, settings.uploads_dir
        settings.database_path = Path(self.temp.name) / "test.db"
        settings.uploads_dir = Path(self.temp.name) / "uploads"
        settings.uploads_dir.mkdir()
        db.init_db()
        auth.init_auth()
        self.clients = []
        self.client = self.new_client()
        self.admin = {"name": "Test Admin", "email": "admin@example.test", "password": "test-password-123"}
        response = self.client.post("/api/auth/bootstrap", json=self.admin)
        self.assertEqual(response.status_code, 201, response.text)
        self.admin_id = response.json()["user"]["id"]

    def tearDown(self):
        for client in self.clients:
            client.close()
        settings.database_path, settings.uploads_dir = self.old_db, self.old_uploads
        self.temp.cleanup()

    def new_client(self):
        # No lifespan context: avoid starting live Ollama clients or monitors.
        client = TestClient(app, base_url="http://localhost", client=("127.0.0.1", 12345),
                            headers={"X-TalentIA-Request": "1"})
        self.clients.append(client)
        return client

    def vacancy(self, client=None, title="Desarrollo Python"):
        response = (client or self.client).post("/api/vacantes", json={
            "titulo": title, "descripcion": "Construir herramientas accesibles para el equipo.",
            "habilidades_obligatorias": ["Python", "SQL"], "anios_experiencia_min": 2,
        })
        self.assertEqual(response.status_code, 201, response.text)
        return response.json()["id"]

    def pdf(self, text=""):
        document = pymupdf.open()
        page = document.new_page()
        if text:
            page.insert_textbox(pymupdf.Rect(30, 30, 550, 750), text, fontsize=12)
        data = document.tobytes()
        document.close()
        return data

    def evaluation(self):
        return EvaluacionCV(
            nombre_candidato="Persona de prueba", email="candidate@example.test",
            anios_experiencia=3, habilidades_detectadas=["Python", "SQL"],
            puntaje=4, cumple_minimos=True,
            justificacion_evidencia="CV: 'Tres años desarrollando sistemas con Python y SQL.'",
        ), MetricasInferencia(tokens_generados=120, throughput_tokens_seg=24, modelo="test-local", intentos=1)

    def upload_evaluated(self):
        vacancy_id = self.vacancy()
        content = self.pdf("Persona de prueba\ncandidate@example.test\nTres años desarrollando sistemas con Python y SQL.\nExperiencia en documentación y pruebas de aplicaciones.")
        with patch("backend.main.evaluar_cv", new=AsyncMock(return_value=self.evaluation())):
            result = self.client.post(f"/api/vacantes/{vacancy_id}/analizar-lote",
                                      files=[("archivos", ("cv.pdf", content, "application/pdf"))])
        self.assertEqual(result.status_code, 200, result.text)
        return vacancy_id, result.json()["ranking"][0]["id"], content

    def test_pdf_batch_extracts_evidence_and_keeps_unreadable_for_manual_review(self):
        vacancy_id = self.vacancy()
        text_pdf = self.pdf("Persona de prueba\ncandidate@example.test\nTres años desarrollando sistemas con Python y SQL.\nExperiencia verificable en proyectos de software y trabajo en equipo.")
        blank_pdf = self.pdf()
        evaluator = AsyncMock(return_value=self.evaluation())
        with patch("backend.main.evaluar_cv", new=evaluator):
            response = self.client.post(f"/api/vacantes/{vacancy_id}/analizar-lote",
                data={"sesion": "spoofed-account"}, files=[
                    ("archivos", ("legible.pdf", text_pdf, "application/pdf")),
                    ("archivos", ("escaneado.pdf", blank_pdf, "application/pdf")),
                ])
        self.assertEqual(response.status_code, 200, response.text)
        result = response.json()
        self.assertEqual((result["total_recibidos"], result["procesados_ok"], result["revision_manual"], result["errores"]), (2, 1, 1, 0))
        evaluator.assert_awaited_once()
        model_args = evaluator.await_args.kwargs
        self.assertIn("Python", model_args["texto_cv"])
        self.assertEqual(model_args["habilidades_obligatorias"], ["Python", "SQL"])
        ranking = result["ranking"]
        evaluated = next(c for c in ranking if c["estado"] == "PENDIENTE_REVISION")
        manual = next(c for c in ranking if c["estado"] == "REVISION_MANUAL")
        self.assertEqual(evaluated["puntaje"], 4)
        self.assertIn("Python", evaluated["justificacion_evidencia"])
        self.assertIsNone(manual["puntaje"])
        self.assertEqual(len(self.client.get("/api/candidatos").json()), 2)
        self.assertEqual(self.client.get(f"/api/vacantes/{vacancy_id}/ranking").json(), ranking)
        self.assertEqual(self.client.get(f"/api/candidatos/{evaluated['id']}/pdf").content, text_pdf)
        anonymous = self.new_client()
        self.assertEqual(anonymous.get(f"/api/candidatos/{evaluated['id']}/pdf").status_code, 401)
        logs = self.client.get("/api/metricas").json()
        self.assertEqual(len(logs), 2)
        self.assertTrue(all("spoofed-account" not in log["usuario_o_sesion"] for log in logs))
        self.assertTrue(all(str(self.admin_id) in log["usuario_o_sesion"] for log in logs))
        summary = self.client.get("/api/metricas/resumen").json()
        self.assertEqual(summary["total_cvs_procesados"], 2)
        self.assertEqual(summary["total_exitos"], 1)
        self.assertEqual(summary["tokens_generados_total"], 120)

    def test_model_failure_is_recorded_without_fabricated_score(self):
        vacancy_id = self.vacancy()
        content = self.pdf("Persona de prueba. Experiencia profesional suficiente para extraer texto. Python, SQL, desarrollo de aplicaciones y documentación.")
        with patch("backend.main.evaluar_cv", new=AsyncMock(side_effect=LLMError("Modelo no disponible"))):
            response = self.client.post(f"/api/vacantes/{vacancy_id}/analizar-lote",
                                       files=[("archivos", ("cv.pdf", content, "application/pdf"))])
        self.assertEqual(response.status_code, 200, response.text)
        result = response.json()
        self.assertEqual(result["errores"], 1)
        self.assertEqual(result["ranking"][0]["estado"], "ERROR_PROCESAMIENTO")
        self.assertIsNone(result["ranking"][0]["puntaje"])
        self.assertEqual(self.client.get("/api/metricas").json()[0]["estado"], "ERROR")

    def test_human_rejection_is_audited_to_authenticated_identity(self):
        _, candidate_id, _ = self.upload_evaluated()
        response = self.client.post(f"/api/candidatos/{candidate_id}/rechazar", json={
            "motivo": "La vacante requiere experiencia adicional en el área.", "sesion": "impersonated",
        })
        self.assertEqual(response.status_code, 200, response.text)
        candidate = self.client.get(f"/api/candidatos/{candidate_id}").json()
        self.assertEqual(candidate["estado"], "RECHAZADO")
        self.assertIn("Test Admin", candidate["detalle"])
        self.assertNotIn("impersonated", candidate["detalle"])

    def test_simulated_interview_persists_payload_without_external_calls(self):
        vacancy_id, candidate_id, _ = self.upload_evaluated()
        appointment = (datetime.now(timezone(timedelta(hours=-5))) + timedelta(days=2)).replace(microsecond=0)
        google = Mock()
        google.modo = "simulacion"
        google.agendar_entrevista.return_value = {
            "modo": "simulacion", "calendar_ok": False, "gmail_ok": False,
            "evento_id": "", "enlace_evento": "", "mensaje": "Entrevista guardada en simulación.",
            "payload": {"calendar_event": {"summary": "Entrevista de prueba"}, "email": {"para": "candidate@example.test"}},
        }
        with patch("backend.main.google_services", google):
            response = self.client.post(f"/api/candidatos/{candidate_id}/aprobar", json={
                "fecha_hora_entrevista": appointment.isoformat(), "duracion_minutos": 45,
                "modalidad": "Videollamada", "sesion": "spoofed-session", "confirmar_envio_real": False,
            })
        self.assertEqual(response.status_code, 200, response.text)
        result = response.json()
        self.assertEqual(result["modo"], "simulacion")
        self.assertFalse(result["gmail_ok"])
        google.agendar_entrevista.assert_called_once()
        interviews = self.client.get(f"/api/entrevistas?vacante_id={vacancy_id}").json()
        self.assertEqual(len(interviews), 1)
        self.assertEqual(interviews[0]["payload"]["email"]["para"], "candidate@example.test")
        self.assertEqual(interviews[0]["modo"], "simulacion")
        self.assertEqual(self.client.get(f"/api/candidatos/{candidate_id}").json()["estado"], "ENTREVISTA_PROGRAMADA")

        with patch("backend.main.google_services", google):
            repeated = self.client.post(f"/api/candidatos/{candidate_id}/aprobar", json={
                "fecha_hora_entrevista": appointment.isoformat(),
            })
        self.assertEqual(repeated.status_code, 409)
        google.agendar_entrevista.assert_called_once()

    def test_real_invitation_requires_confirmation_and_valid_date(self):
        _, candidate_id, _ = self.upload_evaluated()
        google = Mock()
        google.modo = "real"
        future = datetime.now(timezone.utc) + timedelta(days=1)
        with patch("backend.main.google_services", google):
            unconfirmed = self.client.post(f"/api/candidatos/{candidate_id}/aprobar", json={
                "fecha_hora_entrevista": future.isoformat(),
            })
            past = self.client.post(f"/api/candidatos/{candidate_id}/aprobar", json={
                "fecha_hora_entrevista": (future - timedelta(days=2)).isoformat(),
                "confirmar_envio_real": True,
            })
        self.assertEqual(unconfirmed.status_code, 409)
        self.assertEqual(past.status_code, 422)
        google.agendar_entrevista.assert_not_called()
        self.assertEqual(self.client.get(f"/api/candidatos/{candidate_id}").json()["estado"], "PENDIENTE_REVISION")

    def test_partial_google_failure_is_not_reported_as_scheduled(self):
        _, candidate_id, _ = self.upload_evaluated()
        google = Mock()
        google.modo = "real"
        google.agendar_entrevista.return_value = {
            "modo": "real", "calendar_ok": True, "gmail_ok": False,
            "evento_id": "synthetic-event", "enlace_evento": "", "mensaje": "Fallo del correo de prueba.",
            "payload": {},
        }
        with patch("backend.main.google_services", google):
            response = self.client.post(f"/api/candidatos/{candidate_id}/aprobar", json={
                "fecha_hora_entrevista": (datetime.now(timezone.utc) + timedelta(days=1)).isoformat(),
                "confirmar_envio_real": True,
            })
        self.assertEqual(response.status_code, 200, response.text)
        self.assertEqual(response.json()["estado"], "APROBADO")
        self.assertFalse(response.json()["gmail_ok"])
        self.assertEqual(self.client.get(f"/api/candidatos/{candidate_id}").json()["estado"], "APROBADO")

    def test_client_inference_roundtrip_and_batch_limit(self):
        vacancy_id = self.vacancy()
        pdf = self.pdf("Test Person. Experience with Python SQL and software development. Three years building applications.")
        with patch("backend.main.evaluar_cv", new=AsyncMock()) as server_model:
            result = self.client.post(f"/api/vacantes/{vacancy_id}/preparar-local", files=[("archivos", ("cv.pdf", pdf, "application/pdf"))])
            self.assertEqual(result.status_code, 200, result.text)
            job = result.json()["jobs"][0]
            self.assertTrue(job['legible'])
            self.assertIn('messages', job)
            evaluation, _ = self.evaluation()
            saved = self.client.post(f"/api/candidatos/{job['id']}/resultado-local", json={"evaluacion": evaluation.model_dump(), "latencia": 2})
            self.assertEqual(saved.status_code, 200, saved.text)
            self.assertEqual(self.client.get(f"/api/candidatos/{job['id']}").json()['puntaje'], 4)
            server_model.assert_not_awaited()
            repeated = self.client.post(f"/api/candidatos/{job['id']}/resultado-local", json={"evaluacion": evaluation.model_dump()})
            self.assertEqual(repeated.status_code, 409)
        oversized = self.client.post(f"/api/vacantes/{vacancy_id}/preparar-local", files=[("archivos", (f"{n}.pdf", b"x", "application/pdf")) for n in range(101)])
        self.assertEqual(oversized.status_code, 400)

    def test_three_authenticated_users_can_write_concurrently(self):
        accounts = [self.admin]
        for n in (2, 3):
            account = {"name": f"Recruiter {n}", "email": f"staff{n}@example.test", "password": "test-password-123"}
            response = self.client.post("/api/auth/users", json=account)
            self.assertEqual(response.status_code, 201, response.text)
            accounts.append(account)
        workers = [self.new_client() for _ in accounts]
        for client, account in zip(workers, accounts):
            self.assertEqual(client.post("/api/auth/login", json=account).status_code, 200)
        def create(index):
            return workers[index].post("/api/vacantes", json={"titulo": f"Vacante concurrente {index + 1}"})
        with ThreadPoolExecutor(max_workers=3) as pool:
            responses = list(pool.map(create, range(3)))
        self.assertEqual([r.status_code for r in responses], [201, 201, 201])
        self.assertEqual(len({r.json()["id"] for r in responses}), 3)
        for client in workers:
            self.assertEqual(len(client.get("/api/vacantes").json()), 3)


if __name__ == "__main__":
    unittest.main()
