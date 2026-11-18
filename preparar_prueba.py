"""Creates an isolated test workspace, never modifies the personal database."""
import os
import secrets
from pathlib import Path

ROOT = Path(__file__).resolve().parent
os.environ['DATABASE_PATH'] = str(ROOT / 'data/pruebas.db')
os.environ['UPLOADS_DIR'] = str(ROOT / 'data/pruebas-uploads')
os.environ['FORZAR_SIMULACION'] = 'true'
from backend import database as db, auth

def prepare():
    db.init_db()
    auth.init_auth()
    access = ROOT / 'data/acceso-pruebas.txt'
    if auth.setup_required():
        password = secrets.token_urlsafe(15)
        auth._create_user('Equipo de pruebas', 'prueba@talentia.local', password, bootstrap=True)
        access.write_text('Correo: prueba@talentia.local\nContraseña: '+password+'\n', encoding='utf-8')
    for title, description, skills in [
        ('Analista de datos junior', 'Preparar consultas y reportes de datos.', ['SQL', 'Excel']),
        ('Soporte técnico', 'Diagnóstico de sistemas, hardware y redes.', ['Linux', 'Hardware', 'Redes'])]:
        if not any(v['titulo'] == title for v in db.listar_vacantes()):
            db.crear_vacante(title, description, skills, 0)
    print(access.read_text(encoding='utf-8') if access.exists() else 'El usuario de pruebas ya existe; usa su contraseña original.')

if __name__ == '__main__':
    prepare()
