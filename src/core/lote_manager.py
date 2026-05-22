"""Orquestador de lotes — procesamiento serial de múltiples expedientes.

Permite ejecutar una acción sobre 2-10 expedientes como una sola unidad
lógica, con UN solo código 2FA inicial y reporte final consolidado.

CARACTERÍSTICAS:
  - Persistencia en BD (`lotes` + `lotes_items`) → resiste reinicios
  - Validación previa: aborta la creación si algún expediente no califica
  - Serial puro: un item a la vez (Chrome/APT solo permite una sesión)
  - Fallo aislado: si un item falla, los demás siguen
  - Reanudable: tras crash, retoma desde el siguiente pendiente
  - Audit log: cada inicio/fin/fallo se loguea
  - 2FA único por lote (no por cada item)

POLÍTICA DE SEGURIDAD:
  Solo se permiten acciones REVERSIBLES en lote:
    - `apt-crear`  → llena bC1-bC8 y PAUSA pre-guardar (no toca APT real)
    - `apt-plano`  → llena bP1-bP7 (modificable)

  Estas acciones quedan EXCLUIDAS del lote por ser irreversibles —
  cada una necesita su propio 2FA individual:
    - `apt-guardar` (click final del contrato)
    - `enviar-cfia` (envío de R1/R2)
    - Borrado / cancelación de expedientes

USO TÍPICO:
    lm = LoteManager(db=db, two_factor_auth=auth, log=log)

    # 1) Operador pide el lote
    lote = lm.crear_lote(
        accion="apt-crear",
        expedientes_ids=["SEG-001", "RDF-005", "SEG-007"],
        creado_por="+50688887310",
    )
    # → lote.id, lote.codigo_2fa

    # 2) Operador confirma con CONFIRMAR <codigo>
    lm.confirmar_lote(lote_id=lote.id, codigo="482917",
                      actor="+50688887310")

    # 3) Caller ejecuta el lote (en background o sync)
    def executor_fn(item_dict) -> None:
        # item_dict = {"expediente_id": "...", "orden": 1, ...}
        apt_agent.crear_contrato(item_dict["expediente_id"])
        # raise Exception si falla

    resumen = lm.ejecutar_lote(
        lote_id=lote.id, executor_fn=executor_fn,
    )
    # → {"ok": ["SEG-001", "SEG-007"], "fallos": [...], ...}
"""
from __future__ import annotations

import json
import logging
import uuid
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any, Callable, Optional

from src.core.exceptions import DatabaseError
from src.utils.two_factor_auth import (
    AccionPendiente, TwoFactorAuth,
)

log = logging.getLogger("catastro.lote_manager")


# ── Constantes públicas ─────────────────────────────────────────────────

# Acciones permitidas en lote (reversibles).
ACCIONES_PERMITIDAS = {"apt-crear", "apt-plano"}

# Acciones EXPLÍCITAMENTE bloqueadas en lote (irreversibles).
# Listadas aquí para dar un mensaje de error claro al operador.
ACCIONES_BLOQUEADAS = {
    "apt-guardar":  "el GUARDAR final requiere 2FA individual",
    "enviar-cfia":  "el envío al CFIA requiere 2FA individual",
    "rechazar":     "el rechazo de expediente requiere 2FA individual",
    "borrar":       "el borrado requiere 2FA individual",
}

# Estados del lote
LOTE_PENDIENTE_2FA = "pendiente_2fa"
LOTE_EJECUTANDO   = "ejecutando"
LOTE_COMPLETO     = "completo"
LOTE_CANCELADO    = "cancelado"

# Estados de un item
ITEM_PENDIENTE  = "pendiente"
ITEM_EJECUTANDO = "ejecutando"
ITEM_OK         = "ok"
ITEM_FALLO      = "fallo"
ITEM_CANCELADO  = "cancelado"

# Estados de expediente válidos para cada acción del lote
_ESTADOS_VALIDOS_POR_ACCION: dict[str, set[str]] = {
    "apt-crear": {
        # Si el expediente está en uno de estos estados, se puede crear APT.
        # Liberal a propósito: el preflight individual del agente decide.
        "recibido", "enteros_pagados", "carta_agua_ok",
        "apt_correcciones",  # para R2
    },
    "apt-plano": {
        # apt-plano se hace después de tener apt_tramite creado
        "presentado_apt_r1", "apt_r1_respondio",
    },
}

# Tope absoluto para evitar lotes ingobernables
MAX_ITEMS_POR_LOTE = 10


# ── Excepciones ─────────────────────────────────────────────────────────

class LoteError(Exception):
    """Error genérico al manipular un lote."""


class LoteValidationError(LoteError):
    """La validación previa falló — el lote no se creó."""

    def __init__(self, message: str, detalles_por_exp: Optional[dict] = None):
        super().__init__(message)
        self.detalles_por_exp = detalles_por_exp or {}


class LoteNotFoundError(LoteError):
    """No existe el lote con el id dado."""


class LoteStateError(LoteError):
    """El lote no está en el estado correcto para la operación."""


# ── Dataclasses ─────────────────────────────────────────────────────────

@dataclass
class Lote:
    """Resultado de crear_lote — incluye el código 2FA si aplica."""
    id:           str
    accion:       str
    creado_por:   str
    estado:       str
    ts_creado:    str
    items:        list[dict] = field(default_factory=list)
    codigo_2fa:   Optional[str] = None
    ts_completado: Optional[str] = None
    resumen:      Optional[dict] = None


# ── Utilidades ──────────────────────────────────────────────────────────

def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


# ── Manager principal ──────────────────────────────────────────────────

class LoteManager:
    """Coordina la vida de un lote: validar → 2FA → ejecutar → reportar.

    Args:
        db: instancia de Database (necesita acceso a conn / expedientes).
        two_factor_auth: instancia de TwoFactorAuth para generar/validar
            códigos. Si es None, no se exige 2FA (modo test / CLI dev).
        audit_fn: callable opcional `(actor, accion, detalles_dict) → None`
            para escribir audit log. Si es None, solo loggea.
    """

    def __init__(
        self,
        *,
        db,
        two_factor_auth: Optional[TwoFactorAuth] = None,
        audit_fn: Optional[Callable[[str, str, dict], None]] = None,
    ):
        if db is None:
            raise ValueError("db es obligatorio")
        self.db = db
        self.auth = two_factor_auth
        self._audit_fn = audit_fn

    # ── helpers ─────────────────────────────────────────────────────────

    def _audit(self, actor: str, accion: str, detalles: dict) -> None:
        if self._audit_fn is not None:
            try:
                self._audit_fn(actor, accion, detalles)
            except Exception:
                log.exception("audit_fn falló — continuando")
        log.info("LOTE [%s] %s — %s", actor, accion, detalles)

    def _validar_accion(self, accion: str) -> None:
        if accion in ACCIONES_BLOQUEADAS:
            raise LoteValidationError(
                f"acción {accion!r} bloqueada en lote: "
                f"{ACCIONES_BLOQUEADAS[accion]}"
            )
        if accion not in ACCIONES_PERMITIDAS:
            raise LoteValidationError(
                f"acción {accion!r} no soportada; "
                f"permitidas: {sorted(ACCIONES_PERMITIDAS)}"
            )

    def _obtener_expediente(self, exp_id: str) -> Optional[dict]:
        """Busca expediente por id O por numero_expediente."""
        with self.db.connect() as conn:
            row = conn.execute(
                "SELECT * FROM expedientes WHERE id = ? OR numero_expediente = ?",
                (exp_id, exp_id),
            ).fetchone()
        return dict(row) if row else None

    def _validar_items(
        self, accion: str, expedientes_ids: list[str],
    ) -> tuple[list[dict], dict]:
        """Valida que cada expediente exista y esté en estado correcto.

        Returns:
            (items_validos, detalles_por_exp)
            - items_validos: lista de dicts del expediente
            - detalles_por_exp: dict exp_id → {"ok": bool, "razon": str}
        """
        detalles: dict[str, dict] = {}
        validos: list[dict] = []
        estados_ok = _ESTADOS_VALIDOS_POR_ACCION.get(accion, set())

        for exp_id in expedientes_ids:
            exp = self._obtener_expediente(exp_id)
            if exp is None:
                detalles[exp_id] = {"ok": False, "razon": "no existe"}
                continue
            if exp.get("cancelado"):
                detalles[exp_id] = {"ok": False, "razon": "cancelado"}
                continue
            if exp.get("completado"):
                detalles[exp_id] = {"ok": False, "razon": "completado"}
                continue
            estado = exp.get("estado_actual", "")
            if estados_ok and estado not in estados_ok:
                detalles[exp_id] = {
                    "ok": False,
                    "razon": f"estado {estado!r} no admite {accion}",
                }
                continue
            detalles[exp_id] = {"ok": True, "razon": ""}
            validos.append(exp)
        return validos, detalles

    # ── API pública: crear ────────────────────────────────────────────

    def crear_lote(
        self,
        *,
        accion: str,
        expedientes_ids: list[str],
        creado_por: str,
        exigir_2fa: bool = True,
    ) -> Lote:
        """Crea un lote tras validar todos los expedientes.

        Si **alguno** no califica, NO crea el lote y lanza
        LoteValidationError con el desglose. Esto evita lotes a medias.

        Args:
            accion: una de ACCIONES_PERMITIDAS.
            expedientes_ids: 2-MAX_ITEMS_POR_LOTE ids o números de expediente.
            creado_por: teléfono del operador (para audit + 2FA).
            exigir_2fa: si False, el lote queda directo en EJECUTANDO
                (modo CLI / scripts internos).

        Raises:
            LoteValidationError: si la acción o los expedientes no califican.
        """
        self._validar_accion(accion)

        if not expedientes_ids:
            raise LoteValidationError("lista de expedientes vacía")
        if len(expedientes_ids) < 2:
            raise LoteValidationError(
                "un lote requiere al menos 2 expedientes "
                "(para 1 solo usa el comando individual)"
            )
        if len(expedientes_ids) > MAX_ITEMS_POR_LOTE:
            raise LoteValidationError(
                f"lote excede el tope ({MAX_ITEMS_POR_LOTE} items max)"
            )

        # Dedup conservando orden
        seen = set()
        unicos: list[str] = []
        for x in expedientes_ids:
            if x not in seen:
                seen.add(x)
                unicos.append(x)

        validos, detalles = self._validar_items(accion, unicos)
        invalidos = {k: v for k, v in detalles.items() if not v["ok"]}
        if invalidos:
            raise LoteValidationError(
                f"{len(invalidos)} expediente(s) no califican para {accion}",
                detalles_por_exp=detalles,
            )

        # Crear filas en BD
        lote_id = f"lote-{uuid.uuid4().hex[:12]}"
        ts = _now_iso()
        estado_inicial = LOTE_PENDIENTE_2FA if exigir_2fa else LOTE_EJECUTANDO

        with self.db.connect() as conn:
            conn.execute(
                "INSERT INTO lotes (id, accion, creado_por, estado, ts_creado) "
                "VALUES (?, ?, ?, ?, ?)",
                (lote_id, accion, creado_por, estado_inicial, ts),
            )
            for orden, exp in enumerate(validos, start=1):
                conn.execute(
                    "INSERT INTO lotes_items "
                    "(lote_id, expediente_id, orden, estado) "
                    "VALUES (?, ?, ?, ?)",
                    (lote_id, exp["id"], orden, ITEM_PENDIENTE),
                )
            conn.commit()

        # Generar código 2FA si aplica
        codigo = None
        if exigir_2fa and self.auth is not None:
            codigo = self.auth.crear_codigo(
                accion=AccionPendiente(
                    tipo="lote_confirmar",
                    actor=creado_por,
                    payload={"lote_id": lote_id, "accion": accion},
                ),
            )

        self._audit(creado_por, "lote_creado", {
            "lote_id": lote_id, "accion": accion,
            "n_items": len(validos),
        })

        return Lote(
            id=lote_id,
            accion=accion,
            creado_por=creado_por,
            estado=estado_inicial,
            ts_creado=ts,
            items=[{"expediente_id": exp["id"],
                    "numero_expediente": exp["numero_expediente"],
                    "orden": i}
                   for i, exp in enumerate(validos, start=1)],
            codigo_2fa=codigo,
        )

    # ── API pública: confirmar 2FA ─────────────────────────────────────

    def confirmar_lote(
        self, *, lote_id: str, codigo: str, actor: str,
    ) -> bool:
        """Valida el código 2FA y marca el lote como EJECUTANDO.

        Returns:
            True si se confirmó. False si el código es inválido.

        Raises:
            LoteNotFoundError: si el lote no existe.
            LoteStateError: si el lote no está en PENDIENTE_2FA.
        """
        lote = self.estado_lote(lote_id)
        if lote["estado"] != LOTE_PENDIENTE_2FA:
            raise LoteStateError(
                f"lote {lote_id} está en {lote['estado']}, "
                "no se puede confirmar"
            )
        if self.auth is None:
            # Modo dev — auto-confirmar
            self._marcar_estado_lote(lote_id, LOTE_EJECUTANDO)
            self._audit(actor, "lote_confirmado_sin_2fa",
                        {"lote_id": lote_id})
            return True

        accion_pend = self.auth.validar_codigo(codigo, actor=actor)
        if accion_pend is None:
            self._audit(actor, "lote_2fa_fallido", {"lote_id": lote_id})
            return False
        if accion_pend.payload.get("lote_id") != lote_id:
            self._audit(actor, "lote_2fa_mismatch", {
                "lote_id": lote_id,
                "payload_lote_id": accion_pend.payload.get("lote_id"),
            })
            return False

        self._marcar_estado_lote(lote_id, LOTE_EJECUTANDO)
        self._audit(actor, "lote_confirmado", {"lote_id": lote_id})
        return True

    # ── API pública: ejecutar ──────────────────────────────────────────

    def ejecutar_lote(
        self,
        *,
        lote_id: str,
        executor_fn: Callable[[dict], None],
        seguir_si_falla: bool = True,
    ) -> dict:
        """Ejecuta los items pendientes en orden, llamando a executor_fn.

        Args:
            lote_id: id del lote (debe estar en EJECUTANDO).
            executor_fn: callable que recibe el item (dict con
                expediente_id, numero_expediente, orden) y debe completar
                la acción o lanzar Exception.
            seguir_si_falla: si True (default), un fallo no aborta los
                siguientes. Si False, primer fallo cierra el lote.

        Returns:
            resumen dict con ok / fallos / cancelados.

        Raises:
            LoteStateError: si el lote no está en EJECUTANDO.
        """
        lote = self.estado_lote(lote_id)
        if lote["estado"] != LOTE_EJECUTANDO:
            raise LoteStateError(
                f"lote {lote_id} está en {lote['estado']}, "
                "no se puede ejecutar"
            )

        ok_list: list[str] = []
        fallo_list: list[dict] = []
        cancelado_list: list[str] = []

        items_pendientes = self._listar_items_pendientes(lote_id)
        for item in items_pendientes:
            exp_id = item["expediente_id"]
            self._marcar_item(lote_id, exp_id, ITEM_EJECUTANDO,
                              ts_inicio=_now_iso())
            self._audit(lote["creado_por"], "lote_item_inicio", {
                "lote_id": lote_id, "expediente_id": exp_id,
            })

            try:
                executor_fn(item)
                self._marcar_item(lote_id, exp_id, ITEM_OK,
                                  ts_fin=_now_iso())
                ok_list.append(exp_id)
                self._audit(lote["creado_por"], "lote_item_ok", {
                    "lote_id": lote_id, "expediente_id": exp_id,
                })
            except Exception as exc:
                error_msg = str(exc)[:500]
                self._marcar_item(lote_id, exp_id, ITEM_FALLO,
                                  ts_fin=_now_iso(), error=error_msg)
                fallo_list.append({"exp": exp_id, "error": error_msg})
                self._audit(lote["creado_por"], "lote_item_fallo", {
                    "lote_id": lote_id, "expediente_id": exp_id,
                    "error": error_msg,
                })
                if not seguir_si_falla:
                    # Marcar el resto como cancelado y terminar
                    cancelado_list = self._cancelar_pendientes(lote_id)
                    break

        resumen = {
            "ok":         ok_list,
            "fallos":     fallo_list,
            "cancelados": cancelado_list,
            "total":      len(ok_list) + len(fallo_list) + len(cancelado_list),
        }
        self._cerrar_lote(lote_id, resumen)
        self._audit(lote["creado_por"], "lote_completado", {
            "lote_id": lote_id, "resumen": resumen,
        })
        return resumen

    # ── API pública: cancelar ──────────────────────────────────────────

    def cancelar_lote(self, *, lote_id: str, actor: str) -> dict:
        """Cancela los items pendientes; preserva los ya procesados.

        Returns:
            {"cancelados": [...]} con los exp_ids que estaban pendientes.

        Raises:
            LoteStateError: si el lote ya está COMPLETO o CANCELADO.
        """
        lote = self.estado_lote(lote_id)
        if lote["estado"] in (LOTE_COMPLETO, LOTE_CANCELADO):
            raise LoteStateError(
                f"lote {lote_id} ya está {lote['estado']}"
            )

        cancelados = self._cancelar_pendientes(lote_id)
        # Conservar resumen previo si había items ya procesados
        items = self.listar_items(lote_id)
        resumen = {
            "ok":         [i["expediente_id"] for i in items
                           if i["estado"] == ITEM_OK],
            "fallos":     [{"exp": i["expediente_id"],
                            "error": i.get("error", "")}
                           for i in items if i["estado"] == ITEM_FALLO],
            "cancelados": cancelados,
            "total":      len(items),
        }
        with self.db.connect() as conn:
            conn.execute(
                "UPDATE lotes SET estado = ?, ts_completado = ?, "
                "resumen_json = ? WHERE id = ?",
                (LOTE_CANCELADO, _now_iso(),
                 json.dumps(resumen, ensure_ascii=False), lote_id),
            )
            conn.commit()
        self._audit(actor, "lote_cancelado", {
            "lote_id": lote_id, "n_cancelados": len(cancelados),
        })
        return {"cancelados": cancelados}

    # ── API pública: queries ──────────────────────────────────────────

    def estado_lote(self, lote_id: str) -> dict:
        """Devuelve el lote como dict (incluye resumen si existe)."""
        with self.db.connect() as conn:
            row = conn.execute(
                "SELECT * FROM lotes WHERE id = ?", (lote_id,),
            ).fetchone()
        if not row:
            raise LoteNotFoundError(f"lote {lote_id!r} no existe")
        d = dict(row)
        if d.get("resumen_json"):
            try:
                d["resumen"] = json.loads(d["resumen_json"])
            except (TypeError, ValueError):
                d["resumen"] = None
        return d

    def listar_items(self, lote_id: str) -> list[dict]:
        """Devuelve todos los items del lote en orden."""
        with self.db.connect() as conn:
            rows = conn.execute(
                "SELECT li.*, e.numero_expediente "
                "FROM lotes_items li "
                "LEFT JOIN expedientes e ON e.id = li.expediente_id "
                "WHERE li.lote_id = ? ORDER BY li.orden",
                (lote_id,),
            ).fetchall()
        return [dict(r) for r in rows]

    def listar_lotes(self, *, limit: int = 10) -> list[dict]:
        """Últimos N lotes (todos los estados).

        Orden: ts_creado DESC, rowid DESC (rowid es tiebreaker cuando dos
        lotes se crean en el mismo segundo).
        """
        with self.db.connect() as conn:
            rows = conn.execute(
                "SELECT * FROM lotes "
                "ORDER BY ts_creado DESC, rowid DESC LIMIT ?",
                (limit,),
            ).fetchall()
        return [dict(r) for r in rows]

    def progreso(self, lote_id: str) -> dict:
        """Snapshot del progreso para mostrarle al operador."""
        items = self.listar_items(lote_id)
        contador = {ITEM_PENDIENTE: 0, ITEM_EJECUTANDO: 0,
                    ITEM_OK: 0, ITEM_FALLO: 0, ITEM_CANCELADO: 0}
        for it in items:
            contador[it["estado"]] = contador.get(it["estado"], 0) + 1
        return {
            "lote_id":     lote_id,
            "total":       len(items),
            "ok":          contador[ITEM_OK],
            "fallos":      contador[ITEM_FALLO],
            "pendientes":  contador[ITEM_PENDIENTE],
            "ejecutando":  contador[ITEM_EJECUTANDO],
            "cancelados":  contador[ITEM_CANCELADO],
        }

    # ── internos ──────────────────────────────────────────────────────

    def _marcar_estado_lote(self, lote_id: str, nuevo: str) -> None:
        with self.db.connect() as conn:
            n = conn.execute(
                "UPDATE lotes SET estado = ? WHERE id = ?",
                (nuevo, lote_id),
            ).rowcount
            conn.commit()
        if n == 0:
            raise LoteNotFoundError(f"lote {lote_id!r} no existe")

    def _marcar_item(
        self, lote_id: str, exp_id: str, estado: str,
        *, ts_inicio: Optional[str] = None,
        ts_fin: Optional[str] = None, error: Optional[str] = None,
    ) -> None:
        sets = ["estado = ?"]
        args: list[Any] = [estado]
        if ts_inicio:
            sets.append("ts_inicio = ?"); args.append(ts_inicio)
        if ts_fin:
            sets.append("ts_fin = ?"); args.append(ts_fin)
        if error is not None:
            sets.append("error = ?"); args.append(error)
        args.extend([lote_id, exp_id])
        sql = (f"UPDATE lotes_items SET {', '.join(sets)} "
               f"WHERE lote_id = ? AND expediente_id = ?")
        with self.db.connect() as conn:
            conn.execute(sql, args)
            conn.commit()

    def _listar_items_pendientes(self, lote_id: str) -> list[dict]:
        with self.db.connect() as conn:
            rows = conn.execute(
                "SELECT li.*, e.numero_expediente "
                "FROM lotes_items li "
                "LEFT JOIN expedientes e ON e.id = li.expediente_id "
                "WHERE li.lote_id = ? "
                "  AND li.estado IN (?, ?) "
                "ORDER BY li.orden",
                (lote_id, ITEM_PENDIENTE, ITEM_EJECUTANDO),
            ).fetchall()
        return [dict(r) for r in rows]

    def _cancelar_pendientes(self, lote_id: str) -> list[str]:
        items = self._listar_items_pendientes(lote_id)
        cancelados: list[str] = []
        with self.db.connect() as conn:
            for it in items:
                conn.execute(
                    "UPDATE lotes_items SET estado = ?, ts_fin = ? "
                    "WHERE lote_id = ? AND expediente_id = ?",
                    (ITEM_CANCELADO, _now_iso(),
                     lote_id, it["expediente_id"]),
                )
                cancelados.append(it["expediente_id"])
            conn.commit()
        return cancelados

    def _cerrar_lote(self, lote_id: str, resumen: dict) -> None:
        with self.db.connect() as conn:
            conn.execute(
                "UPDATE lotes SET estado = ?, ts_completado = ?, "
                "resumen_json = ? WHERE id = ?",
                (LOTE_COMPLETO, _now_iso(),
                 json.dumps(resumen, ensure_ascii=False), lote_id),
            )
            conn.commit()


__all__ = [
    "LoteManager", "Lote",
    "LoteError", "LoteValidationError",
    "LoteNotFoundError", "LoteStateError",
    "ACCIONES_PERMITIDAS", "ACCIONES_BLOQUEADAS",
    "MAX_ITEMS_POR_LOTE",
    "LOTE_PENDIENTE_2FA", "LOTE_EJECUTANDO",
    "LOTE_COMPLETO", "LOTE_CANCELADO",
    "ITEM_PENDIENTE", "ITEM_EJECUTANDO",
    "ITEM_OK", "ITEM_FALLO", "ITEM_CANCELADO",
]
