"""Tests de memoria operativa: APT REGLA / APT IGNORA + filtrado de discrepancias."""
from __future__ import annotations
import pytest
from unittest.mock import MagicMock

pytest.importorskip("win32cred", reason="solo Windows")

from src.utils.memoria_operador import (
    discrepancia_silenciada, filtrar_discrepancias_silenciadas,
    listar_reglas_legibles,
)
from tests.conftest import FakeCredentialManager, TestDatabase  # noqa: E402


def _make_real_db(tmp_path):
    """Crea Database real (sqlite3) para testear schema + queries."""
    creds = FakeCredentialManager()
    db = TestDatabase(path=tmp_path / "t.db", credentials=creds)
    db.initialize_schema()
    return db


class TestAgregarMemoria:
    def test_agregar_regla(self, tmp_path):
        db = _make_real_db(tmp_path)
        mid = db.agregar_memoria_operador(
            tipo="regla", patron="Para FELIPETIOS verificar visado muni",
            operador="50688887310",
        )
        assert mid > 0
        reglas = db.listar_memoria_operador(tipo="regla")
        assert len(reglas) == 1
        assert reglas[0]["patron"].startswith("Para FELIPETIOS")

    def test_agregar_ignora(self, tmp_path):
        db = _make_real_db(tmp_path)
        mid = db.agregar_memoria_operador(
            tipo="ignora", patron="2-0440-0388",
        )
        assert mid > 0
        ignoras = db.listar_memoria_operador(tipo="ignora")
        assert ignoras[0]["patron"] == "2-0440-0388"

    def test_tipo_invalido(self, tmp_path):
        from src.core.exceptions import DatabaseError
        db = _make_real_db(tmp_path)
        with pytest.raises(DatabaseError):
            db.agregar_memoria_operador(tipo="basura", patron="x")

    def test_desactivar(self, tmp_path):
        db = _make_real_db(tmp_path)
        mid = db.agregar_memoria_operador(tipo="ignora", patron="X")
        assert db.desactivar_memoria_operador(mid) is True
        activas = db.listar_memoria_operador(tipo="ignora", activa=True)
        assert len(activas) == 0
        inactivas = db.listar_memoria_operador(tipo="ignora", activa=False)
        assert len(inactivas) == 1

    def test_listar_filtra_por_activa(self, tmp_path):
        db = _make_real_db(tmp_path)
        a = db.agregar_memoria_operador(tipo="regla", patron="R1")
        b = db.agregar_memoria_operador(tipo="regla", patron="R2")
        db.desactivar_memoria_operador(a)
        activas = db.listar_memoria_operador(tipo="regla", activa=True)
        assert len(activas) == 1
        assert activas[0]["patron"] == "R2"


class TestDiscrepanciaSilenciada:
    def test_match_por_cedula(self, tmp_path):
        db = _make_real_db(tmp_path)
        db.agregar_memoria_operador(tipo="ignora", patron="2-0440-0388")
        disc = {
            "tipo": "rnp_tse_mismatch",
            "cedula": "2-0440-0388",
            "tse_nombre": "AMALIA",
            "registro_nombre": "GRACE",
        }
        assert discrepancia_silenciada(db, disc) is True

    def test_match_por_tipo_con_prefijo(self, tmp_path):
        db = _make_real_db(tmp_path)
        db.agregar_memoria_operador(
            tipo="ignora", patron="tipo:protocolo_diferente_al_activo",
        )
        disc = {
            "tipo": "protocolo_diferente_al_activo",
            "valor": "19245",
            "valor_esperado": "24162",
        }
        assert discrepancia_silenciada(db, disc) is True
        # Otro tipo no se silencia
        disc2 = {"tipo": "rnp_tse_mismatch", "cedula": "1-1111-1111"}
        assert discrepancia_silenciada(db, disc2) is False

    def test_no_match_no_silencia(self, tmp_path):
        db = _make_real_db(tmp_path)
        db.agregar_memoria_operador(tipo="ignora", patron="2-0440-0388")
        disc = {"tipo": "rnp_tse_mismatch", "cedula": "9-9999-9999"}
        assert discrepancia_silenciada(db, disc) is False

    def test_ignora_desactivada_no_silencia(self, tmp_path):
        db = _make_real_db(tmp_path)
        mid = db.agregar_memoria_operador(tipo="ignora", patron="2-0440-0388")
        db.desactivar_memoria_operador(mid)
        disc = {"tipo": "rnp_tse_mismatch", "cedula": "2-0440-0388"}
        assert discrepancia_silenciada(db, disc) is False

    def test_reglas_no_silencian(self, tmp_path):
        """Sólo 'ignora' silencia. Las 'regla' no afectan filtrado."""
        db = _make_real_db(tmp_path)
        db.agregar_memoria_operador(tipo="regla", patron="2-0440-0388")
        disc = {"tipo": "rnp_tse_mismatch", "cedula": "2-0440-0388"}
        assert discrepancia_silenciada(db, disc) is False

    def test_db_none_no_silencia(self):
        assert discrepancia_silenciada(None, {"tipo": "x"}) is False

    def test_disc_vacio_no_silencia(self, tmp_path):
        db = _make_real_db(tmp_path)
        db.agregar_memoria_operador(tipo="ignora", patron="x")
        assert discrepancia_silenciada(db, {}) is False
        assert discrepancia_silenciada(db, None) is False


class TestFiltrarDiscrepanciasSilenciadas:
    def test_separa_correctamente(self, tmp_path):
        db = _make_real_db(tmp_path)
        db.agregar_memoria_operador(tipo="ignora", patron="2-0440-0388")
        discrepancias = [
            {"tipo": "rnp_tse_mismatch", "cedula": "2-0440-0388"},  # silenciar
            {"tipo": "rnp_tse_mismatch", "cedula": "9-9999-9999"},  # mantener
            {"tipo": "protocolo_diferente_al_activo", "valor": "19245"},  # mantener
        ]
        a_notif, silenciadas = filtrar_discrepancias_silenciadas(db, discrepancias)
        assert len(a_notif) == 2
        assert len(silenciadas) == 1
        assert silenciadas[0]["cedula"] == "2-0440-0388"

    def test_lista_vacia(self, tmp_path):
        db = _make_real_db(tmp_path)
        a, s = filtrar_discrepancias_silenciadas(db, [])
        assert a == []
        assert s == []


class TestListarReglasLegibles:
    def test_devuelve_descripciones(self, tmp_path):
        db = _make_real_db(tmp_path)
        db.agregar_memoria_operador(tipo="regla", patron="r1",
                                     descripcion="Regla número 1")
        db.agregar_memoria_operador(tipo="regla", patron="r2",
                                     descripcion="Regla número 2")
        reglas = listar_reglas_legibles(db)
        assert "Regla número 1" in reglas
        assert "Regla número 2" in reglas

    def test_solo_activas(self, tmp_path):
        db = _make_real_db(tmp_path)
        mid = db.agregar_memoria_operador(tipo="regla", patron="r1",
                                          descripcion="Activa")
        db.agregar_memoria_operador(tipo="regla", patron="r2",
                                     descripcion="Otra activa")
        db.desactivar_memoria_operador(mid)
        reglas = listar_reglas_legibles(db)
        assert "Activa" not in reglas
        assert "Otra activa" in reglas

    def test_no_devuelve_ignoras(self, tmp_path):
        db = _make_real_db(tmp_path)
        db.agregar_memoria_operador(tipo="ignora", patron="X")
        assert listar_reglas_legibles(db) == []


class TestIntegracionConDiscrepanciaHandler:
    """Verifica que `procesar_discrepancias` filtra silenciadas."""

    def test_silenciada_no_se_notifica(self, tmp_path):
        from src.agents.apt_discrepancia_handler import procesar_discrepancias
        # DB real para que listar_memoria funcione
        db = _make_real_db(tmp_path)
        # Crear expediente mínimo
        eid = db.crear_expediente(
            numero_expediente="RDF-2026-TEST",
            tipo_plano="segregacion",
            nombre_topografo="X",
            telefono_cliente="50688887310",
            actor="test",
        )
        db.agregar_memoria_operador(tipo="ignora", patron="2-0440-0388")

        send_fn = MagicMock()
        discrepancias = [
            {"tipo": "rnp_tse_mismatch", "cedula": "2-0440-0388",
             "tse_nombre": "AMALIA", "registro_nombre": "GRACE",
             "contexto": "propietario"},
        ]
        res = procesar_discrepancias(
            db=db, expediente_id=eid, numero_expediente="RDF-2026-TEST",
            discrepancias=discrepancias,
            whatsapp_send_fn=send_fn,
            admins_phones=["+506111"],
            topografo_phone="+506222",
        )
        # Silenciada → no se envía nada
        assert send_fn.call_count == 0
        assert res["silenciadas"] == 1
        assert res["notificados_admin"] == 0
        assert res["notificado_topografo"] is False
        # Pero SÍ se persistió en metadata para auditoría
        exp = db.obtener_expediente(eid)
        import json
        meta = json.loads(exp["metadata_json"])
        assert len(meta.get("apt_discrepancias_rnp", [])) == 1
