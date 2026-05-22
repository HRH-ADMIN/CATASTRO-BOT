"""Backup completo del bot — un solo ZIP con todo lo crítico.

Incluye:
  - BD principal (catastro.db) — usando VACUUM INTO (online-safe)
  - .env (configuración local)
  - config/*.yaml (configuración estática)
  - logs/catastro-bot.log (último log)
  - data/files/ (archivos de los expedientes — opcional con --con-archivos)
  - dump de apt_memoria_operador (reglas aprendidas)
  - manifest.json (metadata del backup: fecha, versión, contenido)

NO incluye:
  - data/temp/ (perfiles Chrome, caché)
  - .venv/ (dependencias Python)
  - __pycache__/ y .pyc
  - .git/
  - data/backups/ (no backup recursivo)

USO:
    from src.utils.backup_completo import generar_backup
    path = generar_backup()
    print(f"Backup en: {path}")

CLI:
    python -m src.utils.backup_completo                  # backup rápido (sin archivos)
    python -m src.utils.backup_completo --con-archivos   # con data/files/ (puede ser GB)
    python -m src.utils.backup_completo --listar         # ver backups existentes
"""
from __future__ import annotations
import argparse
import io
import json
import logging
import shutil
import sqlite3
import sys
import zipfile
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional

log = logging.getLogger("catastro.backup")

ROOT = Path(__file__).resolve().parents[2]
BACKUP_DIR = ROOT / "data" / "backups" / "completos"
RETENTION_DAYS = 30  # días que mantenemos backups antes de purgar


# ── Helpers ────────────────────────────────────────────────────────────

def _dump_reglas(zf: zipfile.ZipFile) -> int:
    """Dumpea apt_memoria_operador como JSON dentro del zip."""
    db_path = ROOT / "data" / "catastro.db"
    if not db_path.exists():
        return 0
    try:
        conn = sqlite3.connect(db_path)
        conn.row_factory = sqlite3.Row
        rows = conn.execute(
            "SELECT * FROM apt_memoria_operador WHERE activa=1"
        ).fetchall()
        conn.close()
        data = [dict(r) for r in rows]
        zf.writestr(
            "reglas_aprendidas.json",
            json.dumps(data, ensure_ascii=False, indent=2, default=str),
        )
        return len(data)
    except Exception as exc:
        log.warning("No se pudo dumpear reglas: %s", exc)
        return 0


def _bd_vacuum_into(temp_path: Path) -> bool:
    """Crea copia consistente de la BD usando VACUUM INTO (online-safe)."""
    db_path = ROOT / "data" / "catastro.db"
    if not db_path.exists():
        return False
    try:
        conn = sqlite3.connect(db_path)
        # VACUUM INTO produce una copia limpia y consistente sin bloquear lectores
        conn.execute(f"VACUUM INTO '{temp_path}'")
        conn.close()
        return True
    except Exception as exc:
        log.warning("VACUUM INTO falló (%s), uso copia simple", exc)
        try:
            shutil.copy2(db_path, temp_path)
            return True
        except Exception as exc2:
            log.error("Copia de BD falló: %s", exc2)
            return False


def _agregar_dir_al_zip(zf: zipfile.ZipFile, dir_path: Path, arc_prefix: str,
                       excluir: Optional[set[str]] = None) -> int:
    """Agrega recursivamente un directorio al ZIP. Devuelve cantidad de archivos."""
    excluir = excluir or set()
    n = 0
    if not dir_path.exists():
        return 0
    for f in dir_path.rglob("*"):
        if f.is_dir():
            continue
        # Excluir patrones
        if any(p in str(f) for p in excluir):
            continue
        try:
            arc_name = f"{arc_prefix}/{f.relative_to(dir_path)}".replace("\\", "/")
            zf.write(f, arcname=arc_name)
            n += 1
        except Exception as exc:
            log.warning("No pude agregar %s: %s", f, exc)
    return n


# ── Backup principal ───────────────────────────────────────────────────

def generar_backup(
    con_archivos: bool = False,
    con_codigo: bool = True,         # NUEVO: incluir src/, tools/, tests/, docs/
    output_dir: Optional[Path] = None,
) -> Path:
    """Genera un ZIP con todo lo crítico del bot.

    Args:
        con_archivos: si True, incluye `data/files/` (PDFs de expedientes).
                      Default False — backup ligero solo de BD + config.
        con_codigo:   si True, incluye el código fuente del bot (src/, tools/,
                      tests/, docs/, requirements). Default True — sin código
                      no se puede restaurar la app si se pierde la PC.
        output_dir:   dónde guardar el ZIP. Default `data/backups/completos/`.

    Returns: Path al ZIP generado.
    """
    output_dir = Path(output_dir) if output_dir else BACKUP_DIR
    output_dir.mkdir(parents=True, exist_ok=True)

    ts = datetime.now(timezone.utc).strftime("%Y-%m-%d_%H%M%S")
    sufijo = "_completo" if con_archivos else "_basico"
    zip_path = output_dir / f"catastro-backup-{ts}{sufijo}.zip"

    # Manifest del backup (metadata)
    manifest = {
        "fecha":          datetime.now(timezone.utc).isoformat(),
        "version_bot":    "semana_9",
        "con_archivos":   con_archivos,
        "con_codigo":     con_codigo,
        "incluye": {
            "bd":            False,
            "env":           False,
            "config_yaml":   False,
            "logs":          False,
            "files":         False,
            "reglas_json":   False,
            "codigo_fuente": False,
        },
        "stats": {},
    }

    log.info("Generando backup → %s", zip_path)

    # Carpeta temporal para la BD limpia
    temp_db = output_dir / f".temp_db_{ts}.db"

    with zipfile.ZipFile(zip_path, "w", zipfile.ZIP_DEFLATED, compresslevel=6) as zf:
        # 1. BD (con VACUUM INTO)
        if _bd_vacuum_into(temp_db):
            zf.write(temp_db, arcname="data/catastro.db")
            temp_db.unlink(missing_ok=True)
            manifest["incluye"]["bd"] = True
            log.info("  ✅ BD incluida (VACUUM INTO)")

        # 2. .env
        env_file = ROOT / ".env"
        if env_file.exists():
            zf.write(env_file, arcname=".env")
            manifest["incluye"]["env"] = True
            log.info("  ✅ .env incluido")

        # 3. config/*.yaml + *.json
        config_dir = ROOT / "config"
        if config_dir.exists():
            n = _agregar_dir_al_zip(zf, config_dir, "config",
                                     excluir={"__pycache__", ".pyc"})
            manifest["incluye"]["config_yaml"] = True
            manifest["stats"]["archivos_config"] = n
            log.info("  ✅ config/ incluido (%d archivos)", n)

        # 4. Último log
        log_file = ROOT / "logs" / "catastro-bot.log"
        if log_file.exists():
            zf.write(log_file, arcname="logs/catastro-bot.log")
            manifest["incluye"]["logs"] = True
            log.info("  ✅ log incluido")

        # 5. data/files/ (PDFs de expedientes) — solo si --con-archivos
        if con_archivos:
            files_dir = ROOT / "data" / "files"
            if files_dir.exists():
                n = _agregar_dir_al_zip(
                    zf, files_dir, "data/files",
                    excluir={"__pycache__", ".pyc", "Thumbs.db"},
                )
                manifest["incluye"]["files"] = True
                manifest["stats"]["archivos_expedientes"] = n
                log.info("  ✅ data/files/ incluido (%d archivos)", n)

        # 6. Reglas aprendidas (dump JSON)
        n_reglas = _dump_reglas(zf)
        if n_reglas > 0:
            manifest["incluye"]["reglas_json"] = True
            manifest["stats"]["reglas_activas"] = n_reglas
            log.info("  ✅ %d reglas dumpeadas en reglas_aprendidas.json", n_reglas)

        # 7. Código fuente del bot (src/, tools/, tests/, docs/)
        if con_codigo:
            total_codigo = 0
            for sub in ("src", "tools", "tests", "docs"):
                d = ROOT / sub
                if d.exists():
                    n = _agregar_dir_al_zip(
                        zf, d, sub,
                        excluir={"__pycache__", ".pyc", ".pytest_cache",
                                 "node_modules", ".coverage"},
                    )
                    total_codigo += n
            # Archivos sueltos en raíz (requirements, README, etc.)
            for fname in ("requirements.txt", "requirements-dev.txt",
                          "pyproject.toml", "README.md", "CLAUDE.md",
                          "pytest.ini", "setup.py"):
                f = ROOT / fname
                if f.exists():
                    zf.write(f, arcname=fname)
                    total_codigo += 1
            if total_codigo > 0:
                manifest["incluye"]["codigo_fuente"] = True
                manifest["stats"]["archivos_codigo"] = total_codigo
                log.info("  ✅ código fuente incluido (%d archivos)", total_codigo)

        # 8. Instrucciones de restauración (README adentro del ZIP)
        readme_restore = """\
CATASTRO BOT — INSTRUCCIONES DE RESTAURACIÓN
=============================================

Si llegaste a este archivo es porque necesitás restaurar el bot.
Pasos:

1. INSTALAR PYTHON 3.13+
   https://www.python.org/downloads/

2. CREAR CARPETA Y DESCOMPRIMIR
   - Crear: C:\\catastro-bot\\
   - Descomprimir este ZIP ahí

3. CREAR ENTORNO VIRTUAL
   cd C:\\catastro-bot
   python -m venv .venv
   .venv\\Scripts\\activate

4. INSTALAR DEPENDENCIAS
   .venv\\Scripts\\pip install -r requirements.txt
   .venv\\Scripts\\playwright install chromium

5. CONFIGURAR CREDENCIALES (Windows Credential Manager)
   - Las credenciales NO están en este backup (por seguridad)
   - Hay que volver a setearlas:
     .venv\\Scripts\\python tools\\catastro_bot.py config set anthropic-api
     .venv\\Scripts\\python tools\\catastro_bot.py config set muni-san-ramon
     .venv\\Scripts\\python tools\\catastro_bot.py config set green-api
     .venv\\Scripts\\python tools\\catastro_bot.py config set apt-cfia
   - Para Drive: corré 'tools\\drive.py autorizar' otra vez

6. ARRANCAR EL BOT
   tools\\catastro_bot_autostart.bat
   (O agregarlo a la carpeta Startup de Windows para autostart)

7. VERIFICAR
   - Dashboard: http://localhost:9224
   - Listar expedientes: catastro-bot resumen
   - Las 89+ reglas aprendidas están en BD (data/catastro.db)
   - Si BD se corrompió: descomprimir reglas_aprendidas.json e
     importar manualmente a apt_memoria_operador

Para más info: docs/BOT_PLAYBOOK.md
"""
        zf.writestr("LEEME_RESTAURACION.txt", readme_restore)

        # 9. Manifest
        zf.writestr("manifest.json", json.dumps(manifest, ensure_ascii=False, indent=2))

    # Asegurar limpieza de temp
    temp_db.unlink(missing_ok=True)

    size_mb = zip_path.stat().st_size / (1024 * 1024)
    log.info("✅ Backup generado: %s (%.2f MB)", zip_path.name, size_mb)
    return zip_path


def purgar_backups_viejos(dias: int = RETENTION_DAYS) -> int:
    """Borra backups más viejos que N días. Devuelve cuántos borró."""
    if not BACKUP_DIR.exists():
        return 0
    cutoff = datetime.now(timezone.utc).timestamp() - (dias * 86400)
    borrados = 0
    for f in BACKUP_DIR.glob("catastro-backup-*.zip"):
        if f.stat().st_mtime < cutoff:
            f.unlink(missing_ok=True)
            borrados += 1
    if borrados:
        log.info("Purgados %d backups > %d días", borrados, dias)
    return borrados


def listar_backups() -> list[dict]:
    """Lista los backups existentes ordenados por fecha (más reciente primero)."""
    if not BACKUP_DIR.exists():
        return []
    out = []
    for f in sorted(BACKUP_DIR.glob("catastro-backup-*.zip"),
                    key=lambda p: p.stat().st_mtime, reverse=True):
        out.append({
            "nombre":     f.name,
            "fecha":      datetime.fromtimestamp(f.stat().st_mtime, tz=timezone.utc).isoformat(),
            "size_mb":    round(f.stat().st_size / (1024 * 1024), 2),
            "completo":   "_completo" in f.name,
            "path":       str(f),
        })
    return out


# ── CLI ────────────────────────────────────────────────────────────────

def main() -> int:
    # UTF-8 stdout para emojis
    if hasattr(sys.stdout, "buffer"):
        sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8",
                                       errors="replace")

    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)s %(message)s",
    )

    parser = argparse.ArgumentParser(description=__doc__,
                                      formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--con-archivos", action="store_true",
                        help="Incluir data/files/ (PDFs de expedientes — backup pesado)")
    parser.add_argument("--sin-codigo", action="store_true",
                        help="NO incluir código fuente (src/, tools/, tests/, docs/)")
    parser.add_argument("--listar", action="store_true",
                        help="Listar backups existentes (no genera nuevo)")
    parser.add_argument("--purgar", type=int, default=0,
                        help="Purgar backups más viejos que N días")
    args = parser.parse_args()

    if args.listar:
        backups = listar_backups()
        if not backups:
            print("(no hay backups en data/backups/completos/)")
            return 0
        print(f"\nBackups disponibles ({len(backups)}):\n")
        print(f"  {'Archivo':<46} {'Fecha':<22} {'Tamaño':<10} Tipo")
        print(f"  {'─'*45} {'─'*21} {'─'*9} {'─'*8}")
        for b in backups:
            tipo = "COMPLETO" if b["completo"] else "Básico"
            print(f"  {b['nombre']:<46} {b['fecha'][:19]:<22} {b['size_mb']:>7} MB {tipo}")
        return 0

    if args.purgar:
        n = purgar_backups_viejos(args.purgar)
        print(f"Purgados {n} backups > {args.purgar} días")
        return 0

    # Generar backup
    path = generar_backup(
        con_archivos=args.con_archivos,
        con_codigo=not args.sin_codigo,
    )
    print()
    print("=" * 70)
    print(f"  ✅ BACKUP GENERADO")
    print("=" * 70)
    print(f"  Archivo:  {path}")
    print(f"  Tamaño:   {path.stat().st_size / (1024*1024):.2f} MB")
    print(f"  Tipo:     {'COMPLETO (con archivos)' if args.con_archivos else 'Básico (sin PDFs)'}")
    print()
    print(f"  Para listar todos:    python -m src.utils.backup_completo --listar")
    print(f"  Para incluir PDFs:    python -m src.utils.backup_completo --con-archivos")
    print(f"  Para purgar viejos:   python -m src.utils.backup_completo --purgar 30")

    # Auto-purgar viejos
    purgar_backups_viejos(RETENTION_DAYS)
    return 0


if __name__ == "__main__":
    sys.exit(main())
