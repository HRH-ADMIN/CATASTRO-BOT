"""Tests del LoteManager — orquestador de lotes."""
from __future__ import annotations

import pytest

from src.core.lote_manager import (
    LoteManager, LoteValidationError, LoteNotFoundError, LoteStateError,
    ACCIONES_PERMITIDAS, ACCIONES_BLOQUEADAS, MAX_ITEMS_POR_LOTE,
    LOTE_PENDIENTE_2FA, LOTE_EJECUTANDO, LOTE_COMPLETO, LOTE_CANCELADO,
    ITEM_OK, ITEM_FALLO, ITEM_CANCELADO, ITEM_PENDIENTE,
)
from src.utils.two_factor_auth import TwoFactorAuth
from tests.conftest import FakeCredentialManager, TestDatabase


# ── Fixtures ────────────────────────────────────────────────────────────

@pytest.fixture
def db(tmp_path):
    creds = FakeCredentialManager()
    d = TestDatabase(path=tmp_path / "lote_test.db", credentials=creds)
    d.initialize_schema()
    return d


@pytest.fixture
def auth():
    return TwoFactorAuth(ttl_segundos=300, max_intentos=5)


@pytest.fixture
def lm(db, auth):
    return LoteManager(db=db, two_factor_auth=auth)


def _crear_exp(db, *, numero: str, estado: str = "enteros_pagados",
               tipo: str = "segregacion") -> str:
    """Helper para crear un expediente en estado deseado."""
    eid = db.crear_expediente(
        numero_expediente=numero,
        tipo_plano=tipo,
        nombre_topografo="Test Topografo",
        telefono_cliente="+50688880000",
    )
    if estado != "recibido":
        db.cambiar_estado(eid, estado, actor="test")
    return eid


# ── Validación de creación ─────────────────────────────────────────────

class TestCrearLoteValidaciones:
    def test_lista_vacia_falla(self, lm):
        with pytest.raises(LoteValidationError, match="vacía"):
            lm.crear_lote(accion="apt-crear",
                          expedientes_ids=[], creado_por="+506x")

    def test_un_solo_item_falla(self, db, lm):
        eid = _crear_exp(db, numero="SEG-001")
        with pytest.raises(LoteValidationError, match="al menos 2"):
            lm.crear_lote(accion="apt-crear",
                          expedientes_ids=[eid], creado_por="+506x")

    def test_exceso_de_items_falla(self, db, lm):
        ids = []
        for i in range(MAX_ITEMS_POR_LOTE + 1):
            ids.append(_crear_exp(db, numero=f"SEG-{i:03d}"))
        with pytest.raises(LoteValidationError, match="tope"):
            lm.crear_lote(accion="apt-crear",
                          expedientes_ids=ids, creado_por="+506x")

    def test_accion_bloqueada_falla(self, db, lm):
        a = _crear_exp(db, numero="SEG-001")
        b = _crear_exp(db, numero="SEG-002")
        with pytest.raises(LoteValidationError, match="bloqueada"):
            lm.crear_lote(accion="apt-guardar",
                          expedientes_ids=[a, b], creado_por="+506x")

    def test_accion_desconocida_falla(self, db, lm):
        a = _crear_exp(db, numero="SEG-001")
        b = _crear_exp(db, numero="SEG-002")
        with pytest.raises(LoteValidationError, match="no soportada"):
            lm.crear_lote(accion="hackear",
                          expedientes_ids=[a, b], creado_por="+506x")

    def test_expediente_inexistente_falla(self, db, lm):
        a = _crear_exp(db, numero="SEG-001")
        with pytest.raises(LoteValidationError) as e:
            lm.crear_lote(accion="apt-crear",
                          expedientes_ids=[a, "EXP-INEXISTENTE"],
                          creado_por="+506x")
        assert "EXP-INEXISTENTE" in e.value.detalles_por_exp
        assert e.value.detalles_por_exp["EXP-INEXISTENTE"]["razon"] == "no existe"

    def test_estado_invalido_falla(self, db, lm):
        # presentado_apt_r1 NO admite apt-crear (es un estado posterior)
        a = _crear_exp(db, numero="SEG-001", estado="enteros_pagados")
        b = _crear_exp(db, numero="SEG-002", estado="presentado_apt_r1")
        with pytest.raises(LoteValidationError) as e:
            lm.crear_lote(accion="apt-crear",
                          expedientes_ids=[a, b], creado_por="+506x")
        razon = e.value.detalles_por_exp[b]["razon"]
        assert "no admite" in razon

    def test_expediente_completado_falla(self, db, lm):
        """Un expediente entregado no puede entrar al lote."""
        a = _crear_exp(db, numero="SEG-001")
        b = _crear_exp(db, numero="SEG-002", estado="entregado")
        with pytest.raises(LoteValidationError) as e:
            lm.crear_lote(accion="apt-crear",
                          expedientes_ids=[a, b], creado_por="+506x")
        razon = e.value.detalles_por_exp[b]["razon"]
        assert razon in ("completado", "cancelado") or "no admite" in razon

    def test_si_uno_invalido_no_crea_lote(self, db, lm):
        """Validación atómica: o todos válidos o ninguno."""
        a = _crear_exp(db, numero="SEG-001")
        b = _crear_exp(db, numero="SEG-002", estado="entregado")
        with pytest.raises(LoteValidationError):
            lm.crear_lote(accion="apt-crear",
                          expedientes_ids=[a, b], creado_por="+506x")
        # No debe quedar ningún lote en BD
        assert lm.listar_lotes() == []

    def test_dedup_de_ids(self, db, lm):
        """Si el operador repite un id, se considera uno solo."""
        a = _crear_exp(db, numero="SEG-001")
        b = _crear_exp(db, numero="SEG-002")
        # 3 ids pero 2 únicos — debe pasar la validación min=2
        lote = lm.crear_lote(accion="apt-crear",
                             expedientes_ids=[a, a, b],
                             creado_por="+506x")
        assert len(lote.items) == 2


# ── Creación exitosa ───────────────────────────────────────────────────

class TestCrearLoteOK:
    def test_crea_lote_con_2fa(self, db, lm):
        a = _crear_exp(db, numero="SEG-001")
        b = _crear_exp(db, numero="SEG-002")
        lote = lm.crear_lote(accion="apt-crear",
                             expedientes_ids=[a, b],
                             creado_por="+506888")
        assert lote.estado == LOTE_PENDIENTE_2FA
        assert lote.codigo_2fa is not None
        assert len(lote.codigo_2fa) == 6
        assert lote.codigo_2fa.isdigit()
        assert len(lote.items) == 2
        # Items en orden recibido
        assert lote.items[0]["expediente_id"] == a
        assert lote.items[1]["expediente_id"] == b

    def test_sin_2fa_va_directo_a_ejecutando(self, db, lm):
        a = _crear_exp(db, numero="SEG-001")
        b = _crear_exp(db, numero="SEG-002")
        lote = lm.crear_lote(accion="apt-crear",
                             expedientes_ids=[a, b],
                             creado_por="+506x",
                             exigir_2fa=False)
        assert lote.estado == LOTE_EJECUTANDO
        assert lote.codigo_2fa is None

    def test_lote_persiste_en_bd(self, db, lm):
        a = _crear_exp(db, numero="SEG-001")
        b = _crear_exp(db, numero="SEG-002")
        lote = lm.crear_lote(accion="apt-crear",
                             expedientes_ids=[a, b], creado_por="+506x")
        # Releer desde BD
        recuperado = lm.estado_lote(lote.id)
        assert recuperado["accion"] == "apt-crear"
        assert recuperado["creado_por"] == "+506x"
        items = lm.listar_items(lote.id)
        assert len(items) == 2
        assert all(it["estado"] == ITEM_PENDIENTE for it in items)


# ── Confirmación 2FA ───────────────────────────────────────────────────

class TestConfirmarLote:
    def test_codigo_valido_pasa_a_ejecutando(self, db, lm):
        a = _crear_exp(db, numero="SEG-001")
        b = _crear_exp(db, numero="SEG-002")
        lote = lm.crear_lote(accion="apt-crear",
                             expedientes_ids=[a, b],
                             creado_por="+506x")
        ok = lm.confirmar_lote(lote_id=lote.id,
                               codigo=lote.codigo_2fa,
                               actor="+506x")
        assert ok is True
        assert lm.estado_lote(lote.id)["estado"] == LOTE_EJECUTANDO

    def test_codigo_invalido_no_avanza(self, db, lm):
        a = _crear_exp(db, numero="SEG-001")
        b = _crear_exp(db, numero="SEG-002")
        lote = lm.crear_lote(accion="apt-crear",
                             expedientes_ids=[a, b],
                             creado_por="+506x")
        ok = lm.confirmar_lote(lote_id=lote.id,
                               codigo="000000", actor="+506x")
        assert ok is False
        assert lm.estado_lote(lote.id)["estado"] == LOTE_PENDIENTE_2FA

    def test_actor_distinto_no_puede_confirmar(self, db, lm):
        a = _crear_exp(db, numero="SEG-001")
        b = _crear_exp(db, numero="SEG-002")
        lote = lm.crear_lote(accion="apt-crear",
                             expedientes_ids=[a, b],
                             creado_por="+506AAA")
        ok = lm.confirmar_lote(lote_id=lote.id,
                               codigo=lote.codigo_2fa,
                               actor="+506BBB")
        assert ok is False

    def test_lote_inexistente_lanza(self, lm):
        with pytest.raises(LoteNotFoundError):
            lm.confirmar_lote(lote_id="lote-xxx",
                              codigo="000000", actor="+506x")

    def test_lote_ya_ejecutando_no_se_reconfirma(self, db, lm):
        a = _crear_exp(db, numero="SEG-001")
        b = _crear_exp(db, numero="SEG-002")
        lote = lm.crear_lote(accion="apt-crear",
                             expedientes_ids=[a, b], creado_por="+506x")
        lm.confirmar_lote(lote_id=lote.id,
                          codigo=lote.codigo_2fa, actor="+506x")
        with pytest.raises(LoteStateError):
            lm.confirmar_lote(lote_id=lote.id,
                              codigo="111111", actor="+506x")


# ── Ejecución ──────────────────────────────────────────────────────────

class TestEjecutarLote:
    def test_todos_exitosos(self, db, lm):
        a = _crear_exp(db, numero="SEG-001")
        b = _crear_exp(db, numero="SEG-002")
        lote = lm.crear_lote(accion="apt-crear",
                             expedientes_ids=[a, b],
                             creado_por="+506x", exigir_2fa=False)
        ejecutados = []
        def executor(item):
            ejecutados.append(item["expediente_id"])
        resumen = lm.ejecutar_lote(lote_id=lote.id, executor_fn=executor)
        assert resumen["ok"] == [a, b]
        assert resumen["fallos"] == []
        assert len(ejecutados) == 2
        assert lm.estado_lote(lote.id)["estado"] == LOTE_COMPLETO

    def test_fallo_aislado_continua(self, db, lm):
        a = _crear_exp(db, numero="SEG-001")
        b = _crear_exp(db, numero="SEG-002")
        c = _crear_exp(db, numero="SEG-003")
        lote = lm.crear_lote(accion="apt-crear",
                             expedientes_ids=[a, b, c],
                             creado_por="+506x", exigir_2fa=False)
        def executor(item):
            if item["expediente_id"] == b:
                raise RuntimeError("traslape detectado")
        resumen = lm.ejecutar_lote(lote_id=lote.id, executor_fn=executor)
        assert a in resumen["ok"]
        assert c in resumen["ok"]
        assert any(f["exp"] == b for f in resumen["fallos"])
        assert "traslape" in resumen["fallos"][0]["error"]

    def test_seguir_si_falla_false_cancela_pendientes(self, db, lm):
        a = _crear_exp(db, numero="SEG-001")
        b = _crear_exp(db, numero="SEG-002")
        c = _crear_exp(db, numero="SEG-003")
        lote = lm.crear_lote(accion="apt-crear",
                             expedientes_ids=[a, b, c],
                             creado_por="+506x", exigir_2fa=False)
        def executor(item):
            if item["expediente_id"] == b:
                raise RuntimeError("error")
        resumen = lm.ejecutar_lote(lote_id=lote.id,
                                   executor_fn=executor,
                                   seguir_si_falla=False)
        assert a in resumen["ok"]
        assert any(f["exp"] == b for f in resumen["fallos"])
        # c debe estar cancelado
        assert c in resumen["cancelados"]

    def test_no_ejecutable_si_pendiente_2fa(self, db, lm):
        a = _crear_exp(db, numero="SEG-001")
        b = _crear_exp(db, numero="SEG-002")
        lote = lm.crear_lote(accion="apt-crear",
                             expedientes_ids=[a, b],
                             creado_por="+506x")  # con 2FA
        with pytest.raises(LoteStateError):
            lm.ejecutar_lote(lote_id=lote.id, executor_fn=lambda i: None)

    def test_items_se_marcan_correctamente(self, db, lm):
        a = _crear_exp(db, numero="SEG-001")
        b = _crear_exp(db, numero="SEG-002")
        lote = lm.crear_lote(accion="apt-crear",
                             expedientes_ids=[a, b],
                             creado_por="+506x", exigir_2fa=False)
        def executor(item):
            if item["expediente_id"] == b:
                raise RuntimeError("boom")
        lm.ejecutar_lote(lote_id=lote.id, executor_fn=executor)
        items = lm.listar_items(lote.id)
        items_by_exp = {it["expediente_id"]: it for it in items}
        assert items_by_exp[a]["estado"] == ITEM_OK
        assert items_by_exp[b]["estado"] == ITEM_FALLO
        assert "boom" in items_by_exp[b]["error"]
        assert items_by_exp[a]["ts_inicio"]
        assert items_by_exp[a]["ts_fin"]

    def test_resumen_se_persiste(self, db, lm):
        a = _crear_exp(db, numero="SEG-001")
        b = _crear_exp(db, numero="SEG-002")
        lote = lm.crear_lote(accion="apt-crear",
                             expedientes_ids=[a, b],
                             creado_por="+506x", exigir_2fa=False)
        lm.ejecutar_lote(lote_id=lote.id, executor_fn=lambda i: None)
        recuperado = lm.estado_lote(lote.id)
        assert "resumen" in recuperado
        assert recuperado["resumen"]["total"] == 2


# ── Cancelación ────────────────────────────────────────────────────────

class TestCancelarLote:
    def test_cancela_pendientes(self, db, lm):
        a = _crear_exp(db, numero="SEG-001")
        b = _crear_exp(db, numero="SEG-002")
        c = _crear_exp(db, numero="SEG-003")
        lote = lm.crear_lote(accion="apt-crear",
                             expedientes_ids=[a, b, c],
                             creado_por="+506x", exigir_2fa=False)
        res = lm.cancelar_lote(lote_id=lote.id, actor="+506x")
        assert set(res["cancelados"]) == {a, b, c}
        assert lm.estado_lote(lote.id)["estado"] == LOTE_CANCELADO

    def test_no_se_puede_cancelar_completo(self, db, lm):
        a = _crear_exp(db, numero="SEG-001")
        b = _crear_exp(db, numero="SEG-002")
        lote = lm.crear_lote(accion="apt-crear",
                             expedientes_ids=[a, b],
                             creado_por="+506x", exigir_2fa=False)
        lm.ejecutar_lote(lote_id=lote.id, executor_fn=lambda i: None)
        with pytest.raises(LoteStateError):
            lm.cancelar_lote(lote_id=lote.id, actor="+506x")


# ── Progreso / Listado ─────────────────────────────────────────────────

class TestProgreso:
    def test_progreso_inicial_todos_pendientes(self, db, lm):
        a = _crear_exp(db, numero="SEG-001")
        b = _crear_exp(db, numero="SEG-002")
        lote = lm.crear_lote(accion="apt-crear",
                             expedientes_ids=[a, b],
                             creado_por="+506x")
        p = lm.progreso(lote.id)
        assert p["pendientes"] == 2
        assert p["ok"] == 0
        assert p["total"] == 2

    def test_progreso_post_ejecucion(self, db, lm):
        a = _crear_exp(db, numero="SEG-001")
        b = _crear_exp(db, numero="SEG-002")
        lote = lm.crear_lote(accion="apt-crear",
                             expedientes_ids=[a, b],
                             creado_por="+506x", exigir_2fa=False)
        lm.ejecutar_lote(lote_id=lote.id, executor_fn=lambda i: None)
        p = lm.progreso(lote.id)
        assert p["ok"] == 2
        assert p["pendientes"] == 0

    def test_listar_lotes_ordenado_desc(self, db, lm):
        a = _crear_exp(db, numero="SEG-001")
        b = _crear_exp(db, numero="SEG-002")
        l1 = lm.crear_lote(accion="apt-crear",
                           expedientes_ids=[a, b], creado_por="+506x")
        l2 = lm.crear_lote(accion="apt-crear",
                           expedientes_ids=[a, b], creado_por="+506x")
        lotes = lm.listar_lotes()
        # Más reciente primero
        assert lotes[0]["id"] == l2.id
        assert lotes[1]["id"] == l1.id


# ── Audit log ──────────────────────────────────────────────────────────

class TestAudit:
    def test_audit_fn_se_invoca_en_creacion(self, db):
        auth = TwoFactorAuth()
        log_entries = []
        lm = LoteManager(
            db=db, two_factor_auth=auth,
            audit_fn=lambda actor, acc, det: log_entries.append(
                (actor, acc, det),
            ),
        )
        a = _crear_exp(db, numero="SEG-001")
        b = _crear_exp(db, numero="SEG-002")
        lm.crear_lote(accion="apt-crear",
                      expedientes_ids=[a, b], creado_por="+506x")
        events = [e[1] for e in log_entries]
        assert "lote_creado" in events

    def test_audit_loguea_fallos_de_items(self, db):
        log_entries = []
        lm = LoteManager(
            db=db, two_factor_auth=None,
            audit_fn=lambda a, ac, d: log_entries.append((a, ac, d)),
        )
        a = _crear_exp(db, numero="SEG-001")
        b = _crear_exp(db, numero="SEG-002")
        lote = lm.crear_lote(accion="apt-crear",
                             expedientes_ids=[a, b],
                             creado_por="+506x", exigir_2fa=False)
        def executor(item):
            if item["expediente_id"] == b:
                raise RuntimeError("falla")
        lm.ejecutar_lote(lote_id=lote.id, executor_fn=executor)
        events = [e[1] for e in log_entries]
        assert "lote_item_ok" in events
        assert "lote_item_fallo" in events
        assert "lote_completado" in events


# ── Constantes públicas ────────────────────────────────────────────────

class TestConstantes:
    def test_acciones_bloqueadas_explicadas(self):
        assert "apt-guardar" in ACCIONES_BLOQUEADAS
        assert "enviar-cfia" in ACCIONES_BLOQUEADAS
        # Cada una tiene mensaje de explicación
        for accion, msg in ACCIONES_BLOQUEADAS.items():
            assert msg  # no vacío

    def test_acciones_no_overlapan(self):
        """Una acción no puede estar permitida y bloqueada a la vez."""
        assert ACCIONES_PERMITIDAS.isdisjoint(ACCIONES_BLOQUEADAS.keys())
