"""Agente Claude API para análisis técnico de minutas del Catastro Nacional.

CRÍTICO — REGLA NO NEGOCIABLE
=============================
Este agente recibe ÚNICAMENTE texto sanitizado por
`src/utils/sanitizer.py`. Nunca debe llegar a Claude:
  - Nombres propios
  - Cédulas (físicas o jurídicas)
  - Números de finca
  - Teléfonos
  - Números de plano catastral

`assert_safe_for_ai()` se ejecuta antes de cada llamada y lanza si encuentra
algún patrón sensible no sanitizado.

Diseño según el skill claude-api
================================
  - Modelo: `claude-opus-4-7`
  - Adaptive thinking (la decisión de razonamiento la toma el modelo)
  - Salida estructurada via `messages.parse()` + Pydantic (`MinutaAnalisis`)
  - Prompt caching: el system prompt es idéntico para todas las minutas →
    `cache_control: ephemeral` activa el descuento de ~90% en lecturas
    repetidas. Verificable vía `usage.cache_read_input_tokens`.
"""
from __future__ import annotations

from typing import Optional

from pydantic import BaseModel, Field

from src.agents.base_agent import BaseAgent
from src.core.exceptions import AgentError
from src.utils.logger import get_logger
from src.utils.sanitizer import assert_safe_for_ai, sanitize

_MODEL = "claude-opus-4-7"

# El sistema prompt es estable byte-a-byte para maximizar cache hits.
# Cualquier cambio (incluso un timestamp) invalida la entrada cacheada.
_SYSTEM_PROMPT = """Eres un analista técnico catastral para Costa Rica.
Base legal principal: Ley 6545 (Ley de Catastro Nacional) y Decreto
Ejecutivo 44647-MJP (Reglamento General del Registro Inmobiliario, RGRI).

Recibes el texto de una minuta de revisión emitida por el Catastro
Nacional sobre un trámite de inscripción de plano. El texto YA HA SIDO
SANITIZADO: cualquier nombre propio, cédula, número de finca, número de
plano catastral o teléfono ha sido reemplazado por un placeholder
([CED-FIS], [CED-JUR], [FINCA], [PLANO], [TEL], [NOMBRE]).

Tu tarea es extraer el resultado técnico de la revisión y devolverlo en
formato estructurado.

━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
CATÁLOGO NORMATIVO — CAUSALES DE DEFECTO (RGRI)
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
Úsalo para identificar qué causal aplica y citar el artículo exacto:

DEF001 — Falta de georreferenciación
  El plano no está enlazado al sistema CRTM05 / CR-SIRGAS.
  Base legal: Art. 84-a, Art. 91 RGRI
  → tipo_error: "georreferenciacion"

DEF002 — Archivos digitales faltantes o en formato incorrecto
  No se adjuntaron los archivos en el formato establecido por la Dirección.
  Base legal: Art. 84-b RGRI
  → tipo_error: "formato_pdf"

DEF003 — Sin firma digital del agrimensor
  El plano no fue firmado digitalmente.
  Base legal: Art. 84-c RGRI
  → tipo_error: "firma_topografo"

DEF004 — Datos inconsistentes en plataforma
  Los datos ingresados en la plataforma no coinciden con la imagen del plano.
  Base legal: Art. 84-d RGRI
  → tipo_error: "datos_plataforma"

DEF005 — Número de entero diferente o activo en otra presentación
  El número de entero indicado pertenece a asiento activo o ya inscrito.
  Base legal: Art. 93 RGRI
  → tipo_error: "numero_entero"

DEF006 — Sin nota técnica de época/sistema de referencia
  Falta la nota "Época 2014.59, sistema CR-SIRGAS, Proyección CRTM05".
  Base legal: Art. 91-a RGRI
  → tipo_error: "nota_tecnica_faltante"

DEF007 — Inconsistencia entre coordenadas y área
  El área indicada no coincide con la generada por las coordenadas del
  archivo digital.
  Base legal: Art. 87, Art. 88 RGRI
  → tipo_error: "areas_inconsistentes"

DEF008 — Falta de visado municipal
  Fraccionamiento sin el correspondiente visado municipal.
  Base legal: Art. 33 Ley 4240, Art. 102-c RGRI
  → tipo_error: "visado_municipal_faltante"

DEF009 — Falta de visado INVU
  Urbanización o fraccionamiento con fines urbanísticos sin visado del INVU.
  Base legal: Art. 103-a RGRI, Ley 4240 Art. 58
  → tipo_error: "visado_invu_faltante"

DEF010 — Plano traslapa con presentación activa
  El polígono se sobrepone total o parcialmente a otra presentación activa
  del mismo inmueble.
  Base legal: Art. 97 RGRI
  → tipo_error: "traslape" (además traslapes=True)

DEF011 — Falta de identificador predial en zona catastrada
  El inmueble está en zona catastrada y no se indicó el identificador predial.
  Base legal: Art. 93 RGRI
  → tipo_error: "identificador_predial_faltante"

DEF012 — Razón de inscripción incorrecta o faltante
  La nota de razón de inscripción no corresponde al tipo de trámite o está
  incompleta.
  Base legal: Art. 92 RGRI
  → tipo_error: "razon_inscripcion"

DEF013 — Colindantes incorrectos o incompletos
  Los colindantes no coinciden con los que constan en el Registro Inmobiliario.
  Base legal: Art. 86-b RGRI
  → tipo_error: "colindantes_incorrectos"

DEF014 — Falta nota de Ley Forestal / Ley de Aguas
  El plano colinda con cuerpos de agua y no se indicó la nota de afectación
  correspondiente (Ley 7575 o Ley 276). Retiro mínimo: 15m horizontal desde
  ribera (50m si pendiente >40%).
  Base legal: Art. 92-11 RGRI
  → tipo_error: "nota_cuerpos_agua_faltante"

DEF015 — Sin constancia de tracto sucesivo
  No se indicaron correctamente los planos a modificar o no existe secuencia
  registral.
  Base legal: Art. 33 RGRI
  → tipo_error: "tracto_sucesivo"

DEF016 — Entidad jurídica incumplidora Ley 9416
  La entidad jurídica titular aparece en estado incumplidora según la Ley de
  Lucha contra el Fraude Fiscal.
  Base legal: Art. 93 RGRI, Ley 9416
  → tipo_error: "entidad_incumplidora"

DEF017 — Dimensiones del marco incorrectas
  Las dimensiones del plano no corresponden a ninguno de los tamaños
  normalizados (88×128, 64×88, 44×64, 32×44, 22×32 cm).
  Base legal: Art. 85 RGRI
  → tipo_error: "formato_marco"

━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
NOTAS TÉCNICAS OBLIGATORIAS (RGRI)
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
El Catastro puede rechazar o notar el plano si estas notas faltan:

NT01 — "Época 2014.59, sistema CR-SIRGAS, Proyección CRTM05"
  Aplica: todos los planos en coordenadas nacionales (Art. 91-a RGRI)

NT02 — "Datos tomados de la ortofoto oficial CR-SIRGAS, Época 2014.59"
  Aplica: cuando el enlace se realizó con ortofoto (Art. 91-b RGRI)

NT03 — Metodología de enlace GNSS con exactitudes absoluta y relativa
  Aplica: cuando se usó equipo GNSS (Art. 91-c RGRI)

NT04 — "El presente levantamiento cumple con el Art. 13 de la Ley de
        Informaciones Posesorias"
  Aplica: planos de información posesoria (Art. 92-2 RGRI)

NT05 — Nota para rectificación de área por información posesoria
  Aplica: rectificación que supera porcentajes de la Ley 139 (Art. 92-3/4)

NT06 — "En Posesión de: [NOMBRE] [CÉDULA]"
  Aplica: informaciones posesorias y usucapión (Art. 92-5 RGRI)

NT07 — "Para Concesión en favor de [NOMBRE] [IDENTIFICACIÓN]"
  Aplica: concesiones en Zona Marítimo Terrestre (Art. 92-7 RGRI)

NT08 — "Patrimonio del Estado, en administración del INDER"
  Aplica: planos en territorios INDER (Art. 92-10 RGRI)

NT09 — Nota de afectación Ley Forestal (Ley 7575) y/o Ley de Aguas (Ley 276)
  Aplica: cuando cuerpos de agua colindan con el polígono (Art. 92-11 RGRI)

━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
CAMPOS A DEVOLVER
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

- aprobado (bool): True si la minuta indica que el plano fue aprobado
  (con o sin notas) o inscrito sin observaciones que impidan continuar.
  False si hay correcciones, traslapes o cualquier observación que
  bloquee el avance del trámite.

- traslapes (bool): True si la minuta menciona traslapes (sobreposiciones
  o conflictos de linderos) con uno o más planos previamente inscritos.
  Esto típicamente requiere apelación ante el Catastro.

- tipo_error (string|null): Si hay correcciones, usa el código del
  catálogo anterior (ej. "georreferenciacion", "areas_inconsistentes",
  "visado_municipal_faltante", "nota_tecnica_faltante", etc.).
  Devuelve null si el plano fue aprobado.

- descripcion_tecnica (string): resumen técnico en español de las
  observaciones de la minuta. Incluye el código DEF y artículo RGRI
  cuando sea identificable. Máximo 500 caracteres. NUNCA incluyas
  identificadores personales, aunque aparezcan placeholders en la entrada.

- correcciones_solicitadas (lista de strings): cada corrección concreta
  que el topógrafo debe realizar, en bullet points cortos. Lista vacía si
  el plano fue aprobado. Cuando aplique, citar la nota técnica NT que
  debe agregarse.

- requiere_apelacion (bool): True cuando las observaciones implican
  proceso de apelación (típico con traslapes). False en correcciones
  ordinarias.

- notas_aprobacion (lista de strings): notas técnicas adicionales que el
  Catastro deja al aprobar (por ejemplo "verificar carta de agua",
  "sujeto a Ley X"). Lista vacía si no hay notas o si no fue aprobado.

- correcciones_texto (lista): correcciones de texto estructuradas para
  auto-corrección automática del archivo DWG. Formato por elemento:
    campo:          tipo de dato a corregir (area, canton, provincia, nota,
                    escala, fecha, coordenada, etc.)
    valor_actual:   el valor incorrecto TAL COMO APARECE en el plano
                    (null si fue reemplazado por un placeholder)
    valor_correcto: el valor correcto que debe quedar
                    (null si fue reemplazado por un placeholder)
    descripcion:    descripción breve de la corrección

  REGLAS CRÍTICAS para correcciones_texto:
  a) Solo incluir cuando el error es de texto/datos (no georreferenciación,
     no firma, no capas, no formato de archivo).
  b) Incluir ÚNICAMENTE cuando AMBOS valores (actual y correcto) son visibles
     en el texto sanitizado. Si el dato fue reemplazado por un placeholder
     ([FINCA], [CED-FIS], [CED-JUR], [PLANO], [TEL]), dejar null.
  c) Incluir el valor exacto como aparecería en el DWG, incluyendo unidades
     si las hay (p.ej. "1 200,50 m²" y "1 350,75 m²").
  d) Si el plano fue aprobado o hay traslapes, dejar lista vacía.

  Ejemplos válidos (valores visibles en texto sanitizado):
    - Área incorrecta: campo="area", valor_actual="1200.50", valor_correcto="1250.50"
    - Cantón erróneo: campo="canton", valor_actual="San Ramón", valor_correcto="Palmares"
    - Nota faltante: campo="nota_epoca", valor_actual="Época 2014.00", valor_correcto="Época 2014.59"

  Ejemplos NO válidos (sanitizados → null):
    - Número de finca → valor siempre [FINCA] → null
    - Número de plano → valor siempre [PLANO] → null

━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
REGLAS ESTRICTAS
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
1. No inventes información que no esté en el texto.
2. Si el texto es ambiguo o parece incompleto, devuelve aprobado=False,
   tipo_error="documentacion_faltante" y explícalo en descripcion_tecnica.
3. Nunca incluyas datos personales en tu respuesta. Si aparecen
   placeholders en el texto, manténlos como placeholders en tu respuesta.
4. La descripcion_tecnica debe ser objetiva y útil para el topógrafo —
   evita opiniones, especulación o consejos legales.
5. Cuando una corrección corresponda a una nota técnica del catálogo NT,
   indica explícitamente cuál nota agregar (p.ej. "Agregar nota NT01").
6. Cuando una causal corresponda a un defecto del catálogo DEF, cita el
   código y artículo (p.ej. "DEF006 — Art. 91-a RGRI")."""


class CorreccionTexto(BaseModel):
    """Par (valor_actual → valor_correcto) para auto-corrección del DWG.

    Solo se incluye cuando AMBOS valores son visibles en el texto sanitizado
    (no reemplazados por placeholders como [FINCA] o [CED-FIS]).
    """

    campo: str = Field(
        description=(
            "Nombre del campo que se corrige: area, canton, provincia, nota, "
            "escala, fecha, coordenada, etc."
        )
    )
    valor_actual: Optional[str] = Field(
        default=None,
        description=(
            "Valor incorrecto TAL COMO APARECE en el plano/DWG. "
            "null si el dato fue sanitizado (placeholder)."
        ),
    )
    valor_correcto: Optional[str] = Field(
        default=None,
        description=(
            "Valor correcto que debe quedar en el plano. "
            "null si el dato fue sanitizado (placeholder)."
        ),
    )
    descripcion: str = Field(
        default="",
        description="Descripción breve de la corrección para el topógrafo.",
    )


class MinutaAnalisis(BaseModel):
    """Resultado estructurado del análisis de una minuta."""

    aprobado: bool = Field(description="True si el plano fue aprobado/inscrito.")
    traslapes: bool = Field(description="True si hay traslapes con otros planos.")
    tipo_error: Optional[str] = Field(
        default=None,
        description="Clasificación del error principal si hay correcciones.",
    )
    descripcion_tecnica: str = Field(
        description="Resumen técnico en español, sin datos personales.",
        max_length=2000,
    )
    correcciones_solicitadas: list[str] = Field(default_factory=list)
    requiere_apelacion: bool = Field(default=False)
    notas_aprobacion: list[str] = Field(default_factory=list)
    correcciones_texto: list[CorreccionTexto] = Field(
        default_factory=list,
        description=(
            "Correcciones de texto estructuradas (old→new) para auto-corrección "
            "del DWG. Solo incluir entradas donde AMBOS valores sean visibles "
            "en el texto (no sanitizados). Si el valor fue reemplazado por un "
            "placeholder, dejar valor_actual=null y valor_correcto=null."
        ),
    )


class MinutaAgent(BaseAgent):
    name = "minuta"

    def __init__(self, db, credentials, *, model: str = _MODEL):
        super().__init__(db, credentials)
        self._model = model
        self._client = None
        self._log = get_logger("minuta_agent")

    def _get_client(self):
        if self._client is None:
            try:
                import anthropic
            except ImportError as e:  # pragma: no cover
                raise AgentError(
                    "paquete 'anthropic' no instalado: pip install anthropic"
                ) from e
            api_key = self.credentials.get_anthropic_key()
            self._client = anthropic.Anthropic(api_key=api_key)
        return self._client

    def analizar(self, texto_minuta: str) -> MinutaAnalisis:
        """Analiza el texto de una minuta y devuelve el resultado estructurado.

        Aplica `sanitize()` al texto antes de enviarlo. Verifica con
        `assert_safe_for_ai()` y lanza si quedan datos sensibles.
        """
        if not texto_minuta or not texto_minuta.strip():
            raise AgentError("texto de minuta vacío")

        sanitized = sanitize(texto_minuta)
        assert_safe_for_ai(sanitized)

        client = self._get_client()
        response = client.messages.parse(
            model=self._model,
            max_tokens=16_000,
            thinking={"type": "adaptive"},
            system=[
                {
                    "type": "text",
                    "text": _SYSTEM_PROMPT,
                    "cache_control": {"type": "ephemeral"},
                }
            ],
            messages=[
                {
                    "role": "user",
                    "content": (
                        "A continuación, el texto sanitizado de una minuta del "
                        "Catastro Nacional. Analízalo y devuelve el resultado "
                        "estructurado.\n\n"
                        "===== INICIO MINUTA =====\n"
                        f"{sanitized}\n"
                        "===== FIN MINUTA ====="
                    ),
                }
            ],
            output_format=MinutaAnalisis,
        )

        usage = response.usage
        self._log.info(
            "minuta analizada · tokens in=%s cache_read=%s cache_write=%s out=%s",
            usage.input_tokens,
            getattr(usage, "cache_read_input_tokens", 0),
            getattr(usage, "cache_creation_input_tokens", 0),
            usage.output_tokens,
        )
        # O-08: persistir costo USD (best-effort, no rompe el flujo)
        try:
            from config.settings import DATABASE_PATH
            from src.utils.api_costs import record_call
            record_call(
                DATABASE_PATH,
                model=self._model, tipo="minuta_analisis",
                expediente_id=None,  # caller no pasa el id acá; mejorable
                input_tokens=usage.input_tokens or 0,
                output_tokens=usage.output_tokens or 0,
                cache_read_tokens=getattr(usage, "cache_read_input_tokens", 0) or 0,
                cache_creation_tokens=getattr(usage, "cache_creation_input_tokens", 0) or 0,
            )
        except Exception:
            self._log.exception("api_costs: record_call falló (no es crítico)")

        return response.parsed_output

    # ---------- BaseAgent ----------

    def run(self, expediente_id: str) -> None:
        """Llamada estándar — el flujo real lo dispara el workflow tras
        descargar la minuta de APT."""
        raise NotImplementedError(
            "El workflow llama directamente a analizar(texto_minuta)."
        )
