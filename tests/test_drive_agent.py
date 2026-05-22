"""Tests para DriveAgent.

Cubre:
  - folder_for: crea carpeta correcta por fase, lanza para fase inválida
  - guardar_archivo: copia archivo, calcula sha256, registra en BD
  - Drive opcional: sin credenciales OAuth sube localmente, no crashea
  - Drive OAuth: _drive_enabled, _save_credentials, _load_credentials,
    authorize() errores, _upload_to_drive con mocks de googleapiclient
"""
from __future__ import annotations

import contextlib
import hashlib
import json
import secrets
import sqlite3
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

pytest.importorskip("win32cred", reason="pywin32 requerido (solo Windows)")

from src.agents.drive_agent import DriveAgent, FASES  # noqa: E402
from src.core.database import Database  # noqa: E402
from src.core.exceptions import AgentError, CredentialNotFoundError  # noqa: E402
from src.models.plano import TipoPlano  # noqa: E402


# ─────────────────────────────────────────────────────────────────────────────
# Infraestructura de test
# ─────────────────────────────────────────────────────────────────────────────

class TestDatabase(Database):
    @contextlib.contextmanager
    def connect(self):
        conn = sqlite3.connect(str(self.path), timeout=30.0)
        conn.row_factory = sqlite3.Row
        try:
            conn.execute("PRAGMA foreign_keys = ON")
            yield conn
        finally:
            conn.close()


class FakeCredentialManager:
    def __init__(self, *, with_oauth: bool = False):
        self._db_key = secrets.token_bytes(32)
        self._with_oauth = with_oauth
        self._secrets: dict[str, str] = {}

    def get_or_create_db_key(self) -> bytes:
        return self._db_key

    def get_db_key(self) -> bytes:
        return self._db_key

    def get_operators(self) -> set[str]:
        return set()

    def is_operator(self, phone: str) -> bool:
        return False

    def get_google_oauth(self) -> str:
        if self._with_oauth:
            return '{"client_id": "fake"}'
        raise CredentialNotFoundError("google-oauth")

    # ── Drive token ──
    def get_drive_token(self) -> dict:
        if "drive-token" not in self._secrets:
            raise CredentialNotFoundError("drive-token")
        return json.loads(self._secrets["drive-token"])

    def set_drive_token(self, data: dict) -> None:
        self._secrets["drive-token"] = json.dumps(data)


@pytest.fixture
def db(tmp_path):
    cm = FakeCredentialManager()
    database = TestDatabase(path=tmp_path / "test.db", credentials=cm)
    database.initialize_schema()
    return database


@pytest.fixture
def files_root(tmp_path):
    root = tmp_path / "files"
    root.mkdir()
    return root


@pytest.fixture
def agent(db, files_root):
    cm = FakeCredentialManager()
    return DriveAgent(db, cm, files_root=files_root)


def _crear_exp(db, numero: str = "EXP-TEST-0001") -> str:
    return db.crear_expediente(
        numero_expediente=numero,
        tipo_plano=TipoPlano.SEGREGACION.value,
        nombre_topografo="Topo Test",
        telefono_cliente="50688880001",
        nombre_cliente="Cliente Test",
        cedula_topografo="1-0001-0001",
        metadata={},
        actor="test",
    )


def _sha256(path: Path) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(64 * 1024), b""):
            h.update(chunk)
    return h.hexdigest()


# ─────────────────────────────────────────────────────────────────────────────
# folder_for
# ─────────────────────────────────────────────────────────────────────────────

class TestFolderFor:

    def test_crea_carpeta_para_fase_campo(self, db, agent, files_root):
        eid = _crear_exp(db)
        carpeta = agent.folder_for(eid, "campo")

        assert carpeta.is_dir()
        assert carpeta.name == "01_Campo"
        assert carpeta.parent.name == "EXP-TEST-0001"

    def test_crea_carpeta_para_todas_las_fases(self, db, agent):
        eid = _crear_exp(db)
        for fase in FASES:
            carpeta = agent.folder_for(eid, fase)
            assert carpeta.is_dir(), f"carpeta de fase {fase!r} no creada"

    def test_fase_invalida_lanza_agent_error(self, db, agent):
        eid = _crear_exp(db)
        with pytest.raises(AgentError, match="fase"):
            agent.folder_for(eid, "fase_inexistente")

    def test_expediente_inexistente_lanza_agent_error(self, db, agent):
        with pytest.raises(AgentError, match="no existe"):
            agent.folder_for("uuid-que-no-existe", "campo")

    def test_idempotente(self, db, agent):
        """Llamar dos veces no falla ni crea duplicados."""
        eid = _crear_exp(db)
        c1 = agent.folder_for(eid, "apt_ronda1")
        c2 = agent.folder_for(eid, "apt_ronda1")
        assert c1 == c2

    def test_carpeta_bajo_files_root(self, db, agent, files_root):
        eid = _crear_exp(db)
        carpeta = agent.folder_for(eid, "inscrito")
        assert carpeta.is_relative_to(files_root)


# ─────────────────────────────────────────────────────────────────────────────
# guardar_archivo
# ─────────────────────────────────────────────────────────────────────────────

class TestGuardarArchivo:

    def test_copia_archivo_y_devuelve_sha256(self, db, agent, tmp_path):
        eid = _crear_exp(db)
        origen = tmp_path / "anverso.pdf"
        origen.write_bytes(b"contenido de prueba del plano")

        resultado = agent.guardar_archivo(
            expediente_id=eid,
            fase="campo",
            archivo_origen=origen,
            tipo_archivo="anverso",
            actor="test",
        )

        assert resultado["sha256"] == _sha256(origen)
        assert Path(resultado["ruta_local"]).is_file()

    def test_archivo_destino_en_carpeta_correcta(self, db, agent, tmp_path, files_root):
        eid = _crear_exp(db)
        origen = tmp_path / "minuta.pdf"
        origen.write_bytes(b"minuta catastro")

        resultado = agent.guardar_archivo(
            expediente_id=eid,
            fase="apt_ronda1",
            archivo_origen=origen,
            tipo_archivo="minuta",
            actor="test",
        )

        destino = Path(resultado["ruta_local"])
        assert "03_APT_Ronda1" in str(destino)
        assert destino.is_relative_to(files_root)

    def test_registra_en_base_de_datos(self, db, agent, tmp_path):
        eid = _crear_exp(db)
        origen = tmp_path / "shape.zip"
        origen.write_bytes(b"PK fake zip")

        agent.guardar_archivo(
            expediente_id=eid,
            fase="campo",
            archivo_origen=origen,
            tipo_archivo="shape",
            actor="test",
        )

        archivos = db.archivos_de(eid, fase="campo")
        assert len(archivos) == 1
        assert archivos[0]["tipo_archivo"] == "shape"

    def test_devuelve_id_del_registro(self, db, agent, tmp_path):
        eid = _crear_exp(db)
        origen = tmp_path / "doc.pdf"
        origen.write_bytes(b"x")

        resultado = agent.guardar_archivo(
            expediente_id=eid,
            fase="municipalidad",
            archivo_origen=origen,
            tipo_archivo="comprobante_pago",
            actor="test",
        )

        assert resultado["id"] is not None
        assert isinstance(resultado["id"], str)

    def test_nombre_destino_personalizado(self, db, agent, tmp_path):
        eid = _crear_exp(db)
        origen = tmp_path / "archivo_original.pdf"
        origen.write_bytes(b"pdf content")

        resultado = agent.guardar_archivo(
            expediente_id=eid,
            fase="campo",
            archivo_origen=origen,
            tipo_archivo="anverso",
            nombre_destino="anverso_final.pdf",
            actor="test",
        )

        assert Path(resultado["ruta_local"]).name == "anverso_final.pdf"

    def test_archivo_no_existe_lanza_agent_error(self, db, agent, tmp_path):
        eid = _crear_exp(db)
        with pytest.raises(AgentError, match="no encontrado"):
            agent.guardar_archivo(
                expediente_id=eid,
                fase="campo",
                archivo_origen=tmp_path / "no_existe.pdf",
                tipo_archivo="anverso",
                actor="test",
            )

    def test_archivo_en_destino_correcto_no_se_duplica(self, db, agent, tmp_path):
        """Si el origen ya está en el destino correcto, no hace copia innecesaria."""
        eid = _crear_exp(db)
        # Pre-crear la carpeta y el archivo ahí
        carpeta = agent.folder_for(eid, "campo")
        origen = carpeta / "anverso.pdf"
        origen.write_bytes(b"ya en destino")

        resultado = agent.guardar_archivo(
            expediente_id=eid,
            fase="campo",
            archivo_origen=origen,
            tipo_archivo="anverso",
            actor="test",
        )

        assert Path(resultado["ruta_local"]).is_file()

    def test_sin_oauth_drive_file_id_es_none(self, db, agent, tmp_path):
        """Sin credenciales OAuth el drive_file_id debe ser None."""
        eid = _crear_exp(db)
        origen = tmp_path / "f.pdf"
        origen.write_bytes(b"pdf")

        resultado = agent.guardar_archivo(
            expediente_id=eid,
            fase="campo",
            archivo_origen=origen,
            tipo_archivo="anverso",
            actor="test",
        )

        assert resultado["drive_file_id"] is None


# ─────────────────────────────────────────────────────────────────────────────
# run (compatibilidad BaseAgent)
# ─────────────────────────────────────────────────────────────────────────────

class TestRun:

    def test_run_crea_carpetas_de_todas_las_fases(self, db, agent, files_root):
        eid = _crear_exp(db)
        agent.run(eid)

        exp_folder = files_root / "EXP-TEST-0001"
        assert exp_folder.is_dir()
        for nombre_carpeta in FASES.values():
            assert (exp_folder / nombre_carpeta).is_dir()


# ─────────────────────────────────────────────────────────────────────────────
# _drive_enabled
# ─────────────────────────────────────────────────────────────────────────────

class TestDriveEnabled:

    def test_disabled_sin_token(self, db, files_root):
        agent = DriveAgent(db, FakeCredentialManager(), files_root=files_root)
        assert not agent._drive_enabled()

    def test_enabled_con_token(self, db, files_root):
        cm = FakeCredentialManager()
        cm.set_drive_token({"token": "tok", "refresh_token": "ref"})
        agent = DriveAgent(db, cm, files_root=files_root)
        assert agent._drive_enabled()

    def test_guardar_archivo_sin_drive_devuelve_none_en_drive_id(
        self, db, files_root, tmp_path
    ):
        agent = DriveAgent(db, FakeCredentialManager(), files_root=files_root)
        eid = _crear_exp(db)
        src = tmp_path / "test.pdf"
        src.write_bytes(b"pdf data")
        result = agent.guardar_archivo(
            expediente_id=eid, fase="campo",
            archivo_origen=src, tipo_archivo="anverso",
        )
        assert result["drive_file_id"] is None

    def test_guardar_archivo_llama_upload_cuando_drive_habilitado(
        self, db, files_root, tmp_path
    ):
        cm = FakeCredentialManager()
        cm.set_drive_token({"token": "t", "refresh_token": "r"})
        agent = DriveAgent(db, cm, files_root=files_root)
        eid = _crear_exp(db)
        src = tmp_path / "test.pdf"
        src.write_bytes(b"pdf data")

        with patch.object(agent, "_upload_to_drive", return_value="drv-id-abc") as mu:
            result = agent.guardar_archivo(
                expediente_id=eid, fase="campo",
                archivo_origen=src, tipo_archivo="anverso",
            )
        mu.assert_called_once()
        assert result["drive_file_id"] == "drv-id-abc"

    def test_guardar_archivo_continua_si_upload_falla(
        self, db, files_root, tmp_path
    ):
        """Drive falla → archivo local persiste, drive_file_id=None (sin excepción)."""
        cm = FakeCredentialManager()
        cm.set_drive_token({"token": "t", "refresh_token": "r"})
        agent = DriveAgent(db, cm, files_root=files_root)
        eid = _crear_exp(db)
        src = tmp_path / "test.pdf"
        src.write_bytes(b"pdf data")

        with patch.object(
            agent, "_upload_to_drive", side_effect=Exception("network error")
        ):
            result = agent.guardar_archivo(
                expediente_id=eid, fase="campo",
                archivo_origen=src, tipo_archivo="anverso",
            )
        assert Path(result["ruta_local"]).is_file()
        assert result["drive_file_id"] is None


# ─────────────────────────────────────────────────────────────────────────────
# _save_credentials / get_drive_token round-trip
# ─────────────────────────────────────────────────────────────────────────────

class TestSaveCredentials:

    def test_save_persiste_campos_clave(self, db, files_root):
        cm = FakeCredentialManager()
        agent = DriveAgent(db, cm, files_root=files_root)

        fake = MagicMock()
        fake.token = "tok-abc"
        fake.refresh_token = "ref-xyz"
        fake.token_uri = "https://oauth2.googleapis.com/token"
        fake.client_id = "my-client"
        fake.client_secret = "my-secret"
        fake.scopes = ["https://www.googleapis.com/auth/drive.file"]

        agent._save_credentials(fake)

        stored = cm.get_drive_token()
        assert stored["token"] == "tok-abc"
        assert stored["refresh_token"] == "ref-xyz"
        assert stored["client_id"] == "my-client"
        assert stored["client_secret"] == "my-secret"

    def test_save_sobrescribe_token_anterior(self, db, files_root):
        cm = FakeCredentialManager()
        cm.set_drive_token({"token": "viejo", "refresh_token": "ref-viejo"})
        agent = DriveAgent(db, cm, files_root=files_root)

        fake = MagicMock()
        fake.token = "nuevo"
        fake.refresh_token = "ref-nuevo"
        fake.token_uri = "https://oauth2.googleapis.com/token"
        fake.client_id = "c"
        fake.client_secret = "s"
        fake.scopes = []

        agent._save_credentials(fake)
        assert cm.get_drive_token()["token"] == "nuevo"


# ─────────────────────────────────────────────────────────────────────────────
# _load_credentials
# ─────────────────────────────────────────────────────────────────────────────

class TestLoadCredentials:

    def test_sin_token_lanza_credential_not_found(self, db, files_root):
        agent = DriveAgent(db, FakeCredentialManager(), files_root=files_root)
        with pytest.raises(CredentialNotFoundError):
            agent._load_credentials()

    def test_no_refresh_si_no_expirado(self, db, files_root):
        cm = FakeCredentialManager()
        cm.set_drive_token({
            "token": "tok",
            "refresh_token": "ref",
            "token_uri": "https://oauth2.googleapis.com/token",
            "client_id": "c",
            "client_secret": "s",
            "scopes": [],
        })
        agent = DriveAgent(db, cm, files_root=files_root)

        mock_creds = MagicMock()
        mock_creds.expired = False

        with patch("google.oauth2.credentials.Credentials", return_value=mock_creds):
            result = agent._load_credentials()

        mock_creds.refresh.assert_not_called()
        assert result is mock_creds

    def test_refresh_si_expirado(self, db, files_root):
        cm = FakeCredentialManager()
        cm.set_drive_token({
            "token": "tok",
            "refresh_token": "ref",
            "token_uri": "https://oauth2.googleapis.com/token",
            "client_id": "c",
            "client_secret": "s",
            "scopes": [],
        })
        agent = DriveAgent(db, cm, files_root=files_root)

        mock_creds = MagicMock()
        mock_creds.expired = True
        mock_creds.refresh_token = "ref"

        with patch("google.oauth2.credentials.Credentials", return_value=mock_creds):
            with patch("google.auth.transport.requests.Request"):
                with patch.object(agent, "_save_credentials"):
                    result = agent._load_credentials()

        mock_creds.refresh.assert_called_once()


# ─────────────────────────────────────────────────────────────────────────────
# authorize() — errores de configuración
# ─────────────────────────────────────────────────────────────────────────────

class TestAuthorize:

    def test_sin_client_secret_lanza_credential_not_found(self, db, files_root):
        """Si no hay CRED_GOOGLE_OAUTH en Credential Manager → CredentialNotFoundError."""
        agent = DriveAgent(db, FakeCredentialManager(), files_root=files_root)
        with pytest.raises(CredentialNotFoundError):
            agent.authorize()

    def test_client_secret_json_invalido_lanza_agent_error(self, db, files_root):
        """JSON malformado en CRED_GOOGLE_OAUTH → AgentError."""
        cm = FakeCredentialManager(with_oauth=True)
        # Sobrescribir con JSON inválido
        cm._secrets = {}
        cm._with_oauth = False
        # Acceder directamente al dict de secrets para forzar JSON inválido
        cm._secrets["google-oauth"] = "NO ES JSON"

        def _patched_get_google_oauth():
            return cm._secrets["google-oauth"]

        agent = DriveAgent(db, cm, files_root=files_root)
        agent.credentials.get_google_oauth = _patched_get_google_oauth
        with pytest.raises(AgentError, match="JSON"):
            agent.authorize()


# ─────────────────────────────────────────────────────────────────────────────
# _upload_to_drive (con mock de googleapiclient)
# ─────────────────────────────────────────────────────────────────────────────

class TestUploadToDrive:

    @pytest.fixture
    def agent_con_token(self, db, files_root):
        cm = FakeCredentialManager()
        cm.set_drive_token({
            "token": "tok",
            "refresh_token": "ref",
            "token_uri": "https://oauth2.googleapis.com/token",
            "client_id": "c",
            "client_secret": "s",
            "scopes": ["https://www.googleapis.com/auth/drive.file"],
        })
        return DriveAgent(db, cm, files_root=files_root)

    def _mock_service(self, *, folder_id="folder-123", file_id="file-xyz"):
        """Crea un mock del servicio Drive API."""
        service = MagicMock()
        service.files().list().execute.return_value = {"files": []}
        create_mock = MagicMock()
        create_mock.execute.return_value = {"id": folder_id}
        service.files().create.return_value = create_mock
        # El último create() devuelve el file_id
        file_create_mock = MagicMock()
        file_create_mock.execute.return_value = {"id": file_id}
        service.files().create.side_effect = [
            create_mock,   # root folder
            create_mock,   # exp folder
            create_mock,   # fase folder
            file_create_mock,  # file upload
        ]
        return service

    def test_upload_devuelve_file_id(self, db, files_root, agent_con_token, tmp_path):
        agent = agent_con_token
        eid = _crear_exp(db)
        src = tmp_path / "plano.pdf"
        src.write_bytes(b"PDF content")
        folder = agent.folder_for(eid, "campo")
        import shutil
        dst = folder / src.name
        shutil.copy2(src, dst)

        # Mock simplificado: las carpetas se crean o se encuentran, el archivo
        # devuelve "file-xyz-123"
        mock_svc = MagicMock()
        file_create = MagicMock()
        file_create.execute.return_value = {"id": "file-xyz-123"}
        mock_svc.files().create.return_value = file_create

        with patch("googleapiclient.http.MediaFileUpload"):
            with patch.object(agent, "_get_service", return_value=mock_svc):
                with patch.object(
                    agent, "_get_root_folder_id", return_value="root-id"
                ):
                    with patch.object(
                        agent, "_get_or_create_folder", return_value="folder-id"
                    ):
                        file_id = agent._upload_to_drive(dst, eid, "campo")

        assert file_id == "file-xyz-123"

    def test_upload_sin_token_lanza_credential_error(
        self, db, files_root, tmp_path
    ):
        """Sin drive-token, _load_credentials lanza CredentialNotFoundError."""
        agent = DriveAgent(db, FakeCredentialManager(), files_root=files_root)
        eid = _crear_exp(db)
        src = tmp_path / "t.pdf"
        src.write_bytes(b"x")
        folder = agent.folder_for(eid, "campo")
        import shutil
        dst = folder / src.name
        shutil.copy2(src, dst)

        with pytest.raises(CredentialNotFoundError):
            agent._upload_to_drive(dst, eid, "campo")

    def test_upload_llama_create_del_archivo(
        self, db, files_root, agent_con_token, tmp_path
    ):
        agent = agent_con_token
        eid = _crear_exp(db)
        src = tmp_path / "plano.pdf"
        src.write_bytes(b"PDF")
        folder = agent.folder_for(eid, "campo")
        import shutil
        dst = folder / src.name
        shutil.copy2(src, dst)

        mock_svc = MagicMock()
        mock_svc.files().list().execute.return_value = {"files": []}
        create_mock = MagicMock()
        create_mock.execute.return_value = {"id": "some-id"}
        mock_svc.files().create.return_value = create_mock

        with patch("googleapiclient.http.MediaFileUpload"):
            with patch.object(agent, "_get_service", return_value=mock_svc):
                with patch.object(
                    agent, "_get_root_folder_id", return_value="root-id"
                ):
                    agent._upload_to_drive(dst, eid, "campo")

        # files().create() debe haberse llamado para carpetas + archivo
        assert mock_svc.files().create.called


# ─────────────────────────────────────────────────────────────────────────────
# _get_or_create_folder
# ─────────────────────────────────────────────────────────────────────────────

class TestGetOrCreateFolder:

    @pytest.fixture
    def agent_setup(self, db, files_root):
        return DriveAgent(db, FakeCredentialManager(), files_root=files_root)

    def test_retorna_id_existente(self, agent_setup):
        agent = agent_setup
        mock_svc = MagicMock()
        mock_svc.files().list().execute.return_value = {
            "files": [{"id": "folder-existing"}]
        }
        result = agent._get_or_create_folder(mock_svc, "MiCarpeta", "parent-id")
        assert result == "folder-existing"
        # No debería llamar create
        mock_svc.files().create.assert_not_called()

    def test_crea_carpeta_si_no_existe(self, agent_setup):
        agent = agent_setup
        mock_svc = MagicMock()
        mock_svc.files().list().execute.return_value = {"files": []}
        mock_create = MagicMock()
        mock_create.execute.return_value = {"id": "new-folder"}
        mock_svc.files().create.return_value = mock_create

        result = agent._get_or_create_folder(mock_svc, "NuevaCarpeta", "parent-id")
        assert result == "new-folder"
        mock_svc.files().create.assert_called_once()
