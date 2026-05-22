"""Modelo dataclass del expediente catastral."""
from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from typing import Optional


@dataclass
class Expediente:
    id: str
    numero_expediente: str
    tipo_plano: str
    nombre_topografo: str
    telefono_cliente: str
    estado_actual: str
    fecha_creacion: datetime
    fecha_actualizacion: datetime
    cedula_topografo: Optional[str] = None
    nombre_cliente: Optional[str] = None
    municipalidad: str = "San Ramón"
    metadata_json: Optional[str] = None
    completado: bool = False
    cancelado: bool = False

    @classmethod
    def from_row(cls, row: dict) -> "Expediente":
        return cls(
            id=row["id"],
            numero_expediente=row["numero_expediente"],
            tipo_plano=row["tipo_plano"],
            nombre_topografo=row["nombre_topografo"],
            telefono_cliente=row["telefono_cliente"],
            estado_actual=row["estado_actual"],
            fecha_creacion=datetime.fromisoformat(row["fecha_creacion"]),
            fecha_actualizacion=datetime.fromisoformat(row["fecha_actualizacion"]),
            cedula_topografo=row.get("cedula_topografo"),
            nombre_cliente=row.get("nombre_cliente"),
            municipalidad=row.get("municipalidad", "San Ramón"),
            metadata_json=row.get("metadata_json"),
            completado=bool(row.get("completado", 0)),
            cancelado=bool(row.get("cancelado", 0)),
        )
