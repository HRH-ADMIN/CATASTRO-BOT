"""Organización de archivos por expediente — local + Google Drive (opcional).

Estructura local:
    data/files/{numero_expediente}/{fase}/{nombre_archivo}

`fase` corresponde a las carpetas:
    01_Campo            paso 1: plano recibido del topógrafo
    02_Pago             pasos 2-4: comprobantes de pago
    03_APT_Ronda1       pasos 6-7: minuta + correcciones + imagen
    04_Municipalidad    pasos 11-15: formularios + comprobantes muni
    05_APT_Ronda2       paso 16: presentación final
    06_Inscrito         paso 18: plano inscrito

Implementación local-first: todo se guarda en disco con permisos
restrictivos (carpetas heredan ACLs de `data/`). El sync a Google Drive
es opcional y se activa cuando hay tokens OAuth en Windows Credential
Manager bajo `CRED_DRIVE_TOKEN`.

Para autorizar por primera vez:
    drive_agent.authorize()   # abre el browser; guarda tokens en Cred Mgr
"""
from __future__ import annotations

import hashlib
import json
import shutil
import threading
from pathlib import Path
from typing import Optional

from config.settings import FILES_DIR
from src.agents.base_agent import BaseAgent
from src.core.exceptions import AgentError, CredentialNotFoundError
from src.utils.logger import get_logger

# Mapping de fases canónicas a nombres de carpeta
FASES = {
    "campo": "01_Campo",
    "pago": "02_Pago",
    "apt_ronda1": "03_APT_Ronda1",
    "municipalidad": "04_Municipalidad",
    "apt_ronda2": "05_APT_Ronda2",
    "inscrito": "06_Inscrito",
}

# Google Drive — scopes mínimos (sólo archivos creados por este app)
_DRIVE_SCOPES = ["https://www.googleapis.com/auth/drive.file"]

# Nombre de la carpeta raíz en Google Drive
_DRIVE_ROOT_FOLDER = "catastro-bot"


def _sha256_of(path: Path) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(64 * 1024), b""):
            h.update(chunk)
    return h.hexdigest()


class DriveAgent(BaseAgent):
    name = "drive"

    def __init__(self, db, credentials, *, files_root: Path = FILES_DIR):
        super().__init__(db, credentials)
        self._root = Path(files_root)
        self._root.mkdir(parents=True, exist_ok=True)
        self._drive_service = None
        self._root_folder_id: Optional[str] = None  # caché del folder raíz en Drive
        self._log = get_logger("drive_agent")

    # ──────────────────────────────────────────────────────────────────────
    # estructura local
    # ──────────────────────────────────────────────────────────────────────

    def folder_for(self, expediente_id: str, fase: str) -> Path:
        if fase not in FASES:
            raise AgentError(
                f"fase {fase!r} desconocida — válidas: {sorted(FASES)}"
            )
        exp = self.db.obtener_expediente(expediente_id)
        if not exp:
            raise AgentError(f"expediente {expediente_id!r} no existe")
        folder = self._root / exp["numero_expediente"] / FASES[fase]
        folder.mkdir(parents=True, exist_ok=True)
        return folder

    def guardar_archivo(
        self,
        *,
        expediente_id: str,
        fase: str,
        archivo_origen: Path,
        tipo_archivo: str,
        nombre_destino: Optional[str] = None,
        actor: str = "drive",
    ) -> dict:
        """Copia el archivo al árbol del expediente, registra en BD, opcional
        sube a Drive. Devuelve {id, sha256, ruta_local, drive_file_id}."""
        archivo_origen = Path(archivo_origen)
        if not archivo_origen.is_file():
            raise AgentError(f"archivo no encontrado: {archivo_origen}")

        nombre = nombre_destino or archivo_origen.name
        destino = self.folder_for(expediente_id, fase) / nombre
        if destino.resolve() != archivo_origen.resolve():
            shutil.copy2(archivo_origen, destino)

        sha256 = _sha256_of(destino)
        size = destino.stat().st_size

        drive_file_id = None
        if self._drive_enabled():
            try:
                drive_file_id = self._upload_to_drive(destino, expediente_id, fase)
            except Exception:
                self._log.exception(
                    "upload a Drive falló (ruta local conserva el archivo)"
                )

        archivo_id = self.db.registrar_archivo(
            expediente_id=expediente_id,
            fase=fase,
            tipo_archivo=tipo_archivo,
            nombre_original=nombre,
            sha256=sha256,
            ruta_local=str(destino),
            drive_file_id=drive_file_id,
            tamano_bytes=size,
            actor=actor,
        )
        self._log.info(
            "archivo guardado %s (fase=%s, sha256=%s)", nombre, fase, sha256[:12]
        )
        return {
            "id": archivo_id,
            "sha256": sha256,
            "ruta_local": str(destino),
            "drive_file_id": drive_file_id,
        }

    # ──────────────────────────────────────────────────────────────────────
    # Google Drive — autorización OAuth
    # ──────────────────────────────────────────────────────────────────────

    def authorize(self) -> str:
        """Ejecuta el flujo OAuth y guarda tokens en Credential Manager.

        Abre el navegador local para que el operador autorice el acceso.
        Lanza CredentialNotFoundError si no hay client_secret guardado
        (ejecutar primero: python -m src.core.credential_manager → opción Google OAuth).

        Devuelve un string de confirmación.
        """
        try:
            from google_auth_oauthlib.flow import InstalledAppFlow
        except ImportError as e:
            raise AgentError(
                "google-auth-oauthlib no instalado — ejecutar: "
                "pip install google-auth-oauthlib"
            ) from e

        client_secret_json = self.credentials.get_google_oauth()
        try:
            client_config = json.loads(client_secret_json)
        except json.JSONDecodeError as e:
            raise AgentError(
                "CRED_GOOGLE_OAUTH no contiene JSON válido. "
                "Re-configure con el archivo client_secret de Google Cloud."
            ) from e

        flow = InstalledAppFlow.from_client_config(client_config, _DRIVE_SCOPES)
        creds = flow.run_local_server(port=0)
        self._save_credentials(creds)
        self._drive_service = None  # invalidar caché
        self._root_folder_id = None
        self._log.info("Drive autorizado correctamente, tokens guardados")
        return "Drive autorizado"

    # ──────────────────────────────────────────────────────────────────────
    # Google Drive — gestión de credenciales
    # ──────────────────────────────────────────────────────────────────────

    def _save_credentials(self, creds) -> None:
        """Persiste el token OAuth (access + refresh) en Credential Manager."""
        token_data = {
            "token": creds.token,
            "refresh_token": creds.refresh_token,
            "token_uri": creds.token_uri,
            "client_id": creds.client_id,
            "client_secret": creds.client_secret,
            "scopes": list(creds.scopes or _DRIVE_SCOPES),
        }
        self.credentials.set_drive_token(token_data)

    def _load_credentials(self):
        """Carga los tokens guardados y los refresca si han expirado.

        Lanza CredentialNotFoundError si no hay tokens guardados
        (usar authorize() primero).
        """
        try:
            from google.auth.transport.requests import Request
            from google.oauth2.credentials import Credentials
        except ImportError as e:
            raise AgentError(
                "google-auth no instalado — ejecutar: pip install google-auth"
            ) from e

        token_data = self.credentials.get_drive_token()
        creds = Credentials(
            token=token_data.get("token"),
            refresh_token=token_data.get("refresh_token"),
            token_uri=token_data.get(
                "token_uri", "https://oauth2.googleapis.com/token"
            ),
            client_id=token_data.get("client_id"),
            client_secret=token_data.get("client_secret"),
            scopes=token_data.get("scopes", _DRIVE_SCOPES),
        )

        if creds.expired and creds.refresh_token:
            self._log.debug("refrescando token Drive expirado")
            creds.refresh(Request())
            self._save_credentials(creds)

        return creds

    # ──────────────────────────────────────────────────────────────────────
    # Google Drive — operaciones de carpetas y archivos
    # ──────────────────────────────────────────────────────────────────────

    def _drive_enabled(self) -> bool:
        """True si hay tokens Drive guardados en Credential Manager."""
        try:
            self.credentials.get_drive_token()
            return True
        except CredentialNotFoundError:
            return False

    def _get_service(self):
        """Devuelve (o construye) el cliente de Google Drive API."""
        try:
            from googleapiclient.discovery import build
        except ImportError as e:
            raise AgentError(
                "google-api-python-client no instalado — "
                "ejecutar: pip install google-api-python-client"
            ) from e

        if self._drive_service is None:
            creds = self._load_credentials()
            self._drive_service = build("drive", "v3", credentials=creds)
        return self._drive_service

    def _get_or_create_folder(
        self, service, name: str, parent_id: str
    ) -> str:
        """Encuentra o crea una carpeta en Drive bajo el parent dado.
        Devuelve el folder_id."""
        safe_name = name.replace("'", "\\'")
        q = (
            f"name='{safe_name}' "
            f"and mimeType='application/vnd.google-apps.folder' "
            f"and '{parent_id}' in parents "
            f"and trashed=false"
        )
        results = (
            service.files()
            .list(q=q, fields="files(id)", pageSize=1)
            .execute()
        )
        items = results.get("files", [])
        if items:
            return items[0]["id"]

        folder = (
            service.files()
            .create(
                body={
                    "name": name,
                    "mimeType": "application/vnd.google-apps.folder",
                    "parents": [parent_id],
                },
                fields="id",
            )
            .execute()
        )
        return folder["id"]

    def _get_root_folder_id(self, service) -> str:
        """Devuelve (creando si no existe) la carpeta 'catastro-bot' en Drive.
        Usa caché de instancia para evitar llamadas repetidas."""
        if self._root_folder_id:
            return self._root_folder_id
        q = (
            f"name='{_DRIVE_ROOT_FOLDER}' "
            f"and mimeType='application/vnd.google-apps.folder' "
            f"and 'root' in parents "
            f"and trashed=false"
        )
        results = (
            service.files()
            .list(q=q, fields="files(id)", pageSize=1)
            .execute()
        )
        items = results.get("files", [])
        if items:
            fid = items[0]["id"]
        else:
            folder = (
                service.files()
                .create(
                    body={
                        "name": _DRIVE_ROOT_FOLDER,
                        "mimeType": "application/vnd.google-apps.folder",
                    },
                    fields="id",
                )
                .execute()
            )
            fid = folder["id"]
        self._root_folder_id = fid
        return fid

    def _upload_to_drive(
        self, ruta_local: Path, expediente_id: str, fase: str
    ) -> Optional[str]:
        """Sube el archivo a Google Drive y devuelve el file_id.

        Estructura en Drive:
            /catastro-bot/{numero_expediente}/{FASES[fase]}/{nombre}

        Lanza CredentialNotFoundError si no hay tokens (Drive no autorizado).
        """
        try:
            from googleapiclient.http import MediaFileUpload
        except ImportError as e:
            raise AgentError(
                "google-api-python-client no instalado"
            ) from e

        service = self._get_service()

        exp = self.db.obtener_expediente(expediente_id)
        numero = exp["numero_expediente"] if exp else expediente_id
        fase_nombre = FASES.get(fase, fase)

        root_id = self._get_root_folder_id(service)
        exp_folder_id = self._get_or_create_folder(service, numero, root_id)
        fase_folder_id = self._get_or_create_folder(
            service, fase_nombre, exp_folder_id
        )

        file_metadata = {
            "name": ruta_local.name,
            "parents": [fase_folder_id],
        }
        media = MediaFileUpload(str(ruta_local), resumable=True)
        file = (
            service.files()
            .create(body=file_metadata, media_body=media, fields="id")
            .execute()
        )
        drive_id = file["id"]
        self._log.info(
            "archivo subido a Drive: %s → %s", ruta_local.name, drive_id
        )
        return drive_id

    # ──────────────────────────────────────────────────────────────────────
    # Backup automático a Drive (carpeta separada)
    # ──────────────────────────────────────────────────────────────────────

    def subir_backup(self, zip_path: Path) -> dict:
        """Sube un ZIP de backup a /catastro-bot/backups/ en Drive.

        Args:
            zip_path: ruta al archivo ZIP local.

        Returns:
            dict {ok, file_id, web_link} o {ok=False, error}
        """
        try:
            from googleapiclient.http import MediaFileUpload
        except ImportError:
            return {"ok": False, "error": "google-api-python-client no instalado"}

        zip_path = Path(zip_path)
        if not zip_path.exists():
            return {"ok": False, "error": f"ZIP no existe: {zip_path}"}

        try:
            service = self._get_service()
            root_id = self._get_root_folder_id(service)
            backups_folder_id = self._get_or_create_folder(
                service, "backups", root_id,
            )

            file_metadata = {
                "name":    zip_path.name,
                "parents": [backups_folder_id],
            }
            media = MediaFileUpload(
                str(zip_path),
                mimetype="application/zip",
                resumable=True,
            )
            file = (
                service.files()
                .create(
                    body=file_metadata, media_body=media,
                    fields="id,webViewLink,size",
                )
                .execute()
            )
            size_mb = int(file.get("size", 0)) / (1024 * 1024)
            self._log.info(
                "backup subido a Drive: %s (%.2f MB) → %s",
                zip_path.name, size_mb, file["id"],
            )
            return {
                "ok":       True,
                "file_id":  file["id"],
                "web_link": file.get("webViewLink", ""),
                "size_mb":  round(size_mb, 2),
                "nombre":   zip_path.name,
            }
        except Exception as exc:
            self._log.exception("subir_backup falló")
            return {"ok": False, "error": str(exc)[:200]}

    # ──────────────────────────────────────────────────────────────────────
    # Replicación de hash root del audit_log (Sprint 2 / N-10)
    # ──────────────────────────────────────────────────────────────────────

    def subir_audit_root(self, linea: str) -> dict:
        """Append a `audit_roots.txt` en `/catastro-bot/audit-roots/`.

        Como Google Drive no soporta append nativo, el flujo es:
          1. Encontrar el archivo existente (o crearlo vacío).
          2. Descargar su contenido actual.
          3. Concatenar `linea` al final.
          4. Re-subir el contenido (sobrescribir el mismo file_id).

        El archivo es texto plano TSV: `iso_ts \\t audit_id \\t audit_ts \\t hash`.
        Cada noche se le agrega una línea — crece ~80 bytes/día.

        Args:
            linea: línea YA terminada en \\n.

        Returns:
            dict {ok: bool, file_id: str|None, lineas_total: int|None, error: str|None}

        Sprint 2 / N-10.
        """
        try:
            from googleapiclient.http import MediaInMemoryUpload
        except ImportError:
            return {"ok": False, "error": "google-api-python-client no instalado"}

        try:
            service = self._get_service()
            root_id = self._get_root_folder_id(service)
            audit_folder_id = self._get_or_create_folder(
                service, "audit-roots", root_id,
            )

            # Buscar archivo audit_roots.txt en esa carpeta
            q = (
                f"name='audit_roots.txt' "
                f"and '{audit_folder_id}' in parents "
                f"and trashed=false"
            )
            results = (
                service.files()
                .list(q=q, fields="files(id)", pageSize=1)
                .execute()
            )
            items = results.get("files", [])

            if items:
                file_id = items[0]["id"]
                # Descargar contenido existente
                contenido = service.files().get_media(fileId=file_id).execute()
                if isinstance(contenido, bytes):
                    contenido = contenido.decode("utf-8", errors="replace")
                else:
                    contenido = str(contenido)
            else:
                file_id = None
                contenido = ""

            nuevo = contenido + linea
            data_bytes = nuevo.encode("utf-8")
            media = MediaInMemoryUpload(
                data_bytes, mimetype="text/plain", resumable=False
            )

            if file_id:
                service.files().update(
                    fileId=file_id, media_body=media,
                ).execute()
            else:
                file_metadata = {
                    "name":     "audit_roots.txt",
                    "parents":  [audit_folder_id],
                    "mimeType": "text/plain",
                }
                created = (
                    service.files()
                    .create(body=file_metadata, media_body=media, fields="id")
                    .execute()
                )
                file_id = created["id"]

            lineas_total = nuevo.count("\n")
            self._log.info(
                "audit-root subido a Drive (%d líneas, %d bytes) → %s",
                lineas_total, len(data_bytes), file_id,
            )
            return {
                "ok":           True,
                "file_id":      file_id,
                "lineas_total": lineas_total,
                "bytes":        len(data_bytes),
            }
        except Exception as exc:
            self._log.exception("subir_audit_root falló")
            return {"ok": False, "error": str(exc)[:200]}

    def listar_backups_drive(self) -> list[dict]:
        """Lista los backups en /catastro-bot/backups/ ordenados por fecha desc."""
        try:
            service = self._get_service()
            root_id = self._get_root_folder_id(service)
            backups_folder_id = self._get_or_create_folder(
                service, "backups", root_id,
            )
            results = service.files().list(
                q=f"'{backups_folder_id}' in parents and trashed=false",
                fields="files(id,name,size,createdTime,webViewLink)",
                orderBy="createdTime desc",
                pageSize=50,
            ).execute()
            return results.get("files", [])
        except Exception as exc:
            self._log.exception("listar_backups_drive falló")
            return []

    def authorize_async(self, callback_fn) -> threading.Thread:
        """Ejecuta authorize() en un hilo; llama callback_fn(result, error)."""
        def _run():
            try:
                result = self.authorize()
                callback_fn(result, None)
            except Exception as e:
                callback_fn(None, e)
        t = threading.Thread(target=_run, daemon=True)
        t.start()
        return t

    # ──────────────────────────────────────────────────────────────────────
    # BaseAgent
    # ──────────────────────────────────────────────────────────────────────

    def run(self, expediente_id: str) -> None:
        """Garantiza que existan las carpetas locales del expediente."""
        for fase in FASES:
            self.folder_for(expediente_id, fase)
