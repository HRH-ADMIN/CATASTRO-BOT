"""Resuelve los datos APT del topógrafo responsable de un expediente.

Cada expediente tiene `nombre_topografo` + `cedula_topografo` en BD. Para
soportar oficinas con múltiples topógrafos (cada uno con su propio
protocolo activo, correo CFIA y cert BCR), el bot resuelve estos datos
desde la tabla `usuarios` extendida.

Estrategia de resolución (en orden de preferencia):
  1. Match por `cedula_topografo` del expediente → `usuarios.cedula`
  2. Match por `nombre_topografo` (normalizado) → `usuarios.nombre`
  3. Match por teléfono operador en `metadata.operador_telefono`
  4. Fallback: settings.PROTOCOLO_ACTIVO_TOPOGRAFO + correo por default

USO:
    from src.utils.topografo_resolver import resolver_topografo
    t = resolver_topografo(db, exp_dict)
    # t = {
    #     "nombre": "ROJAS HERRERA LUIS ALONSO",
    #     "telefono": "...",
    #     "cedula": "0205300432",
    #     "protocolo_activo": "24162",
    #     "correo_apt": "topografiahrh@gmail.com",
    #     "carne_cfia": "IT10676",
    #     "fuente": "match_cedula" | "match_nombre" | "match_telefono" | "default",
    # }
"""
from __future__ import annotations
import logging
import unicodedata
from typing import Optional

log = logging.getLogger("catastro.topografo_resolver")


# Defaults de oficina (cuando no hay match en usuarios)
CORREO_APT_DEFAULT = "topografiahrh@gmail.com"


def _normalizar(s: str) -> str:
    if not s:
        return ""
    s = unicodedata.normalize("NFD", s)
    s = "".join(c for c in s if unicodedata.category(c) != "Mn")
    s = s.upper().strip()
    s = " ".join(s.split())
    return s


def _build_result(usuario: dict | None, fuente: str,
                  *, protocolo_default: str = "") -> dict:
    if usuario:
        return {
            "nombre":           usuario.get("nombre", ""),
            "telefono":         usuario.get("telefono", ""),
            "cedula":           usuario.get("cedula") or "",
            "protocolo_activo": usuario.get("protocolo_activo") or protocolo_default,
            "correo_apt":       usuario.get("correo_apt") or CORREO_APT_DEFAULT,
            "carne_cfia":       usuario.get("carne_cfia") or "",
            "cert_bcr_fingerprint": usuario.get("cert_bcr_fingerprint") or "",
            "fuente":           fuente,
        }
    return {
        "nombre":           "",
        "telefono":         "",
        "cedula":           "",
        "protocolo_activo": protocolo_default,
        "correo_apt":       CORREO_APT_DEFAULT,
        "carne_cfia":       "",
        "cert_bcr_fingerprint": "",
        "fuente":           "default",
    }


def resolver_topografo(db, expediente: dict) -> dict:
    """Encuentra el topógrafo responsable del expediente y devuelve sus datos APT.

    Args:
        db: instancia de Database (debe tener `listar_usuarios`).
        expediente: dict del expediente (con `nombre_topografo`,
            `cedula_topografo`, opcionalmente `metadata_json` con
            `operador_telefono`).

    Returns dict con `nombre`, `telefono`, `cedula`, `protocolo_activo`,
    `correo_apt`, `carne_cfia`, `cert_bcr_fingerprint`, `fuente`.
    """
    # Default protocolo desde settings (override env var)
    try:
        from config.settings import PROTOCOLO_ACTIVO_TOPOGRAFO
        protocolo_default = str(PROTOCOLO_ACTIVO_TOPOGRAFO or "").strip()
    except Exception:
        protocolo_default = ""

    if not expediente:
        return _build_result(None, "default", protocolo_default=protocolo_default)

    cedula_exp = (expediente.get("cedula_topografo") or "").strip()
    nombre_exp = _normalizar(expediente.get("nombre_topografo") or "")

    # Cargar todos los topógrafos activos
    try:
        usuarios = db.listar_usuarios(rol="topografo", activo=True)
    except Exception as exc:
        log.warning("error listando topógrafos: %s", exc)
        usuarios = []

    # 1. Match por cédula (más confiable)
    if cedula_exp:
        for u in usuarios:
            uc = (u.get("cedula") or "").strip()
            if uc and uc == cedula_exp:
                return _build_result(u, "match_cedula",
                                     protocolo_default=protocolo_default)

    # 2. Match por nombre (case/accent insensitive)
    if nombre_exp:
        for u in usuarios:
            if _normalizar(u.get("nombre", "")) == nombre_exp:
                return _build_result(u, "match_nombre",
                                     protocolo_default=protocolo_default)

    # 3. Match por teléfono del operador (si está en metadata)
    import json
    try:
        meta_raw = expediente.get("metadata_json")
        if isinstance(meta_raw, str):
            meta = json.loads(meta_raw or "{}")
        elif isinstance(meta_raw, dict):
            meta = meta_raw
        else:
            meta = {}
        tel_op = (meta.get("operador_telefono") or "").strip()
        if tel_op:
            for u in usuarios:
                if (u.get("telefono") or "").strip() == tel_op.lstrip("+"):
                    return _build_result(u, "match_telefono",
                                         protocolo_default=protocolo_default)
    except Exception:
        pass

    # 4. Default — sin match
    log.info(
        "topógrafo no resuelto para exp '%s' / cédula '%s' / nombre '%s' — usando defaults",
        expediente.get("numero_expediente", "?"),
        cedula_exp, expediente.get("nombre_topografo", ""),
    )
    return _build_result(None, "default", protocolo_default=protocolo_default)


__all__ = ["resolver_topografo", "CORREO_APT_DEFAULT"]
