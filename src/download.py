#!/usr/bin/env python3
"""Descarga ERA5 (Climate Data Store) por bloques de años, reanudable.

Uso:
    # Modo síncrono: un bloque cada vez (por defecto)
    python src/download.py --start 1996 --end 2025 --block 3

    # Modo asíncrono: encola varios bloques a la vez y descarga según acaban
    python src/download.py --start 1996 --end 2025 --block 3 --mode async

    # Comprobar credenciales antes de una descarga larga
    python src/download.py --check-auth

    # Ver las peticiones sin llamar a la API
    python src/download.py --start 1996 --end 2025 --block 3 --dry-run

Cada bloque de años se descarga a un único fichero NetCDF en data/raw/.
Si el fichero ya existe (y no está vacío) se omite, así que interrumpir y
volver a lanzar el script es seguro.

En modo asíncrono, además, los `request_id` de los trabajos encolados se
guardan en `data/raw/.jobs.json`: al relanzar el script se retoma la espera
de los que siguen en cola en vez de volver a enviarlos al final de la fila.
Como las colas del CDS pueden ser de horas, ese modo evita esperar los
bloques de uno en uno y es el recomendado para descargar la serie completa.
"""

from __future__ import annotations

import argparse
import logging
import sys
import time
from pathlib import Path

import cds
from config import DATA_RAW_DIR, load_download_config
from jobs import JobState


def year_blocks(start: int, end: int, block: int) -> list[list[int]]:
    """Divide el rango [start, end] (inclusive) en bloques de `block` años."""
    if block < 1:
        raise ValueError(f"--block debe ser al menos 1 (recibido {block})")
    if start > end:
        raise ValueError(f"--start ({start}) no puede ser mayor que --end ({end})")
    years = list(range(start, end + 1))
    return [years[i : i + block] for i in range(0, len(years), block)]


def build_request(cfg: dict, years: list[int], variables: list[str]) -> dict:
    return {
        "product_type": [cfg["product_type"]],
        "variable": variables,
        "year": [str(y) for y in years],
        "month": [f"{m:02d}" for m in range(1, 13)],
        "day": [f"{d:02d}" for d in range(1, 32)],
        "time": cfg["hours"],
        "area": cfg["area"],
        "data_format": cfg["data_format"],
        "download_format": cfg["download_format"],
    }


def target_path(output_dir: Path, years: list[int], variables_group: str) -> Path:
    label = f"{years[0]}" if len(years) == 1 else f"{years[0]}_{years[-1]}"
    # El grupo de variables forma parte del nombre para que descargar
    # "prioritarias" y luego "complementarias" del mismo rango de años no
    # sobrescriba el fichero anterior ni se dé por reanudado por error.
    return output_dir / f"era5_{label}_{variables_group}.nc"


def already_downloaded(path: Path) -> bool:
    return path.exists() and path.stat().st_size > 0


def variables_for(cfg: dict, which: str) -> list[str]:
    all_vars = cfg["variables"]
    if which == "all":
        return [*all_vars["prioritarias"], *all_vars["complementarias"]]
    if which not in all_vars:
        raise ValueError(f"Grupo de variables desconocido: '{which}'")
    return list(all_vars[which])


def block_availability(years: list[int], last_available_year: int | None) -> str:
    """Compara los años de un bloque con el final de la cobertura del dataset.

    Devuelve "ok", "partial" (parte del bloque aún no existe) o "unavailable"
    (el bloque entero está fuera del rango publicado).
    """
    if last_available_year is None:
        return "ok"
    if years[0] > last_available_year:
        return "unavailable"
    if years[-1] > last_available_year:
        return "partial"
    return "ok"


def preflight(
    backend, dataset: str, plan: list[tuple[Path, dict]]
) -> list[tuple[Path, dict]]:
    """Descarta bloques inviables antes de meterlos en la cola.

    Una petición fuera de rango o demasiado grande no falla al enviarla:
    falla al procesarse, es decir después de horas de cola. Comprobarlo por
    delante cuesta un par de llamadas rápidas y ahorra esa espera.

    Ante cualquier duda deja pasar el bloque: el preflight es una ayuda, no
    un portero. Solo descarta lo que puede afirmar con certeza.
    """
    try:
        _, end = backend.collection_window(dataset)
    except cds.UnsupportedOperation as exc:
        print(f"[preflight] omitido: {exc}", file=sys.stderr)
        return plan
    except Exception as exc:
        print(f"[preflight] no se pudo consultar el catálogo: {exc}", file=sys.stderr)
        return plan

    last_year = end.year if end is not None else None
    if last_year is not None:
        print(f"[preflight] el dataset publica datos hasta {end:%Y-%m-%d}")

    kept = []
    for target, request in plan:
        years = [int(y) for y in request["year"]]
        status = block_availability(years, last_year)

        if status == "unavailable":
            print(
                f"[preflight] {target.name} descartado: {years[0]}-{years[-1]} "
                f"está por encima del último año publicado ({last_year})",
                file=sys.stderr,
            )
            continue
        if status == "partial":
            print(
                f"[preflight] {target.name}: el bloque llega a {years[-1]} pero el "
                f"dataset acaba en {last_year}; se descargará lo disponible"
            )

        try:
            verdict, description = cds.interpret_costs(
                backend.estimate_costs(dataset, request)
            )
        except cds.UnsupportedOperation:
            verdict, description = "unknown", ""
        except Exception as exc:
            verdict, description = "unknown", f"no se pudo estimar ({exc})"

        if verdict == "exceeded":
            print(
                f"[preflight] {target.name} descartado: la petición supera el "
                f"límite del CDS ({description}). Prueba con --block más pequeño "
                "o --variables prioritarias.",
                file=sys.stderr,
            )
            continue
        if verdict == "ok":
            print(f"[preflight] {target.name}: {description}")

        kept.append((target, request))

    return kept


def _report_failure(target_name: str, exc: Exception) -> None:
    print(f"[fallo] {target_name}: {exc}", file=sys.stderr)
    if cds.looks_like_terms_error(str(exc)):
        print(f"         {cds.TERMS_HINT}", file=sys.stderr)


def run_sync(backend, dataset: str, plan: list[tuple[Path, dict]], force: bool) -> int:
    """Descarga bloque a bloque, esperando a cada uno antes del siguiente."""
    failures = 0
    for target, request in plan:
        if already_downloaded(target) and not force:
            print(f"[omitido] {target.name} ya existe")
            continue
        target.parent.mkdir(parents=True, exist_ok=True)
        print(f"[descargando] {target.name} ({', '.join(request['year'])}) ...")
        try:
            backend.retrieve(dataset, request, str(target))
        except Exception as exc:  # la API señala todos los fallos como excepción
            _report_failure(target.name, exc)
            failures += 1
            continue
        print(f"[completado] {target.name}")
    return failures


def run_async(
    backend,
    dataset: str,
    plan: list[tuple[Path, dict]],
    force: bool,
    output_dir: Path,
    max_parallel: int,
    poll_seconds: int,
) -> int:
    """Encola hasta `max_parallel` bloques a la vez y descarga según acaban.

    El CDS procesa los trabajos encolados en paralelo, así que enviarlos
    todos por delante convierte una espera secuencial de N colas en una
    sola espera solapada.
    """
    if not backend.supports_async:
        raise cds.UnsupportedOperation(
            f"El backend '{backend.name}' no soporta el modo asíncrono."
        )

    state = JobState.load(output_dir)
    pending: dict[str, tuple[Path, dict]] = {}
    queue = list(plan)
    failures = 0

    def submit_next() -> None:
        """Rellena el hueco de trabajos en vuelo hasta max_parallel."""
        nonlocal failures
        while queue and len(pending) < max_parallel:
            target, request = queue.pop(0)
            if already_downloaded(target) and not force:
                print(f"[omitido] {target.name} ya existe")
                continue

            known = state.get(target.name)
            if known:
                # Ya estaba encolado en una ejecución anterior: se retoma
                # en vez de volver a enviarlo al final de la cola.
                try:
                    job = backend.get_job(known)
                except Exception as exc:
                    print(
                        f"[aviso] no se pudo retomar {target.name} ({exc}); se reenvía",
                        file=sys.stderr,
                    )
                    state.forget(target.name)
                    queue.insert(0, (target, request))
                    continue
                print(f"[retomado] {target.name} (request {known})")
            else:
                try:
                    job = backend.submit(dataset, request)
                except Exception as exc:
                    _report_failure(target.name, exc)
                    failures += 1
                    continue
                state.record(target.name, job.request_id, dataset)
                state.save()
                print(f"[encolado] {target.name} (request {job.request_id})")

            pending[target.name] = (target, job)

    submit_next()

    while pending:
        for target_name in list(pending):
            target, job = pending[target_name]
            try:
                ready = job.ready()
            except Exception as exc:
                # El trabajo ha fallado o ha sido rechazado. Se olvida para
                # que la siguiente ejecución lo reenvíe desde cero.
                _report_failure(target_name, exc)
                state.forget(target_name)
                state.save()
                del pending[target_name]
                failures += 1
                continue

            if not ready:
                continue

            target.parent.mkdir(parents=True, exist_ok=True)
            print(f"[descargando] {target.name} ...")
            try:
                job.download(str(target))
            except Exception as exc:
                _report_failure(target_name, exc)
                failures += 1
            else:
                print(f"[completado] {target.name}")
            state.forget(target_name)
            state.save()
            del pending[target_name]

        submit_next()
        if pending:
            time.sleep(poll_seconds)

    return failures


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--start", type=int, help="Primer año (inclusive)")
    parser.add_argument("--end", type=int, help="Último año (inclusive)")
    parser.add_argument(
        "--block", type=int, default=3, help="Tamaño del bloque de años (default: 3)"
    )
    parser.add_argument(
        "--mode",
        choices=["sync", "async"],
        default="sync",
        help="sync: un bloque cada vez. async: encola varios a la vez (default: sync)",
    )
    parser.add_argument(
        "--backend",
        choices=cds.BACKENDS,
        default=cds.DATASTORES,
        help=f"Cliente del CDS a usar (default: {cds.DATASTORES})",
    )
    parser.add_argument(
        "--variables",
        choices=["all", "prioritarias", "complementarias"],
        default="all",
        help="Grupo de variables a descargar (default: all)",
    )
    parser.add_argument(
        "--max-parallel",
        type=int,
        default=4,
        help="Trabajos encolados a la vez en modo async (default: 4)",
    )
    parser.add_argument(
        "--poll-seconds",
        type=int,
        default=60,
        help="Segundos entre comprobaciones de estado en modo async (default: 60)",
    )
    parser.add_argument(
        "--output-dir", type=Path, default=None, help="Directorio de salida (default: data/raw)"
    )
    parser.add_argument("--config", type=Path, default=None, help="Ruta a config/download.yaml")
    parser.add_argument(
        "--force", action="store_true", help="Vuelve a descargar aunque el fichero ya exista"
    )
    parser.add_argument(
        "--check-auth",
        action="store_true",
        help="Comprueba las credenciales contra la API y termina",
    )
    parser.add_argument(
        "--list-jobs",
        action="store_true",
        help="Lista los trabajos encolados en el servidor y termina",
    )
    parser.add_argument(
        "--no-preflight",
        action="store_true",
        help="No comprobar cobertura ni coste antes de encolar",
    )
    parser.add_argument(
        "--verbose",
        action="store_true",
        help="Muestra los mensajes del cliente (estado de cola, reintentos)",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Muestra las peticiones sin llamar a la API del CDS",
    )
    args = parser.parse_args(argv)

    solo_info = args.check_auth or args.list_jobs
    if not solo_info and (args.start is None or args.end is None):
        parser.error(
            "--start y --end son obligatorios salvo con --check-auth o --list-jobs"
        )
    if args.max_parallel < 1:
        parser.error("--max-parallel debe ser al menos 1")

    return args


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv)

    if args.verbose:
        # El cliente informa por logging del estado en cola y de los
        # reintentos; en esperas de horas es la única señal de avance.
        logging.basicConfig(level="INFO", format="%(asctime)s %(levelname)s %(message)s")

    if args.list_jobs:
        try:
            backend = cds.get_backend(args.backend)
            request_ids = backend.list_jobs()
        except (ImportError, cds.UnsupportedOperation) as exc:
            print(exc, file=sys.stderr)
            return 1
        except Exception as exc:
            print(f"No se pudo consultar la lista de trabajos: {exc}", file=sys.stderr)
            return 1
        if not request_ids:
            print("No hay trabajos registrados en el servidor.")
        else:
            print(f"{len(request_ids)} trabajo(s), del más reciente al más antiguo:")
            for request_id in request_ids:
                print(f"  {request_id}")
        return 0

    if args.check_auth:
        try:
            backend = cds.get_backend(args.backend)
            backend.check_authentication()
        except (ImportError, cds.UnsupportedOperation) as exc:
            print(exc, file=sys.stderr)
            return 1
        except FileNotFoundError as exc:
            print(
                f"No hay fichero de credenciales ({exc.filename}). Créalo con el "
                "token personal que muestra el CDS al estar logueado; ver la "
                "sección 'Configuración' del README.",
                file=sys.stderr,
            )
            return 1
        except Exception as exc:
            print(f"Credenciales rechazadas: {exc}", file=sys.stderr)
            if cds.looks_like_terms_error(str(exc)):
                print(cds.TERMS_HINT, file=sys.stderr)
            return 1
        print("Credenciales correctas.")
        return 0

    cfg = load_download_config(args.config)
    output_dir = args.output_dir or DATA_RAW_DIR
    try:
        variables = variables_for(cfg, args.variables)
        blocks = year_blocks(args.start, args.end, args.block)
    except ValueError as exc:
        print(exc, file=sys.stderr)
        return 1

    plan = [
        (target_path(output_dir, years, args.variables), build_request(cfg, years, variables))
        for years in blocks
    ]

    if args.dry_run:
        for target, request in plan:
            print(f"[dry-run] {target} <- {request}")
        return 0

    try:
        backend = cds.get_backend(args.backend)
    except ImportError as exc:
        print(exc, file=sys.stderr)
        return 1

    if not args.no_preflight:
        # Solo se comprueban los bloques que realmente se van a pedir.
        pendientes = [(t, r) for t, r in plan if not already_downloaded(t) or args.force]
        if pendientes:
            viables = {t.name for t, _ in preflight(backend, cfg["dataset"], pendientes)}
            descartados = {t.name for t, _ in pendientes} - viables
            plan = [(t, r) for t, r in plan if t.name not in descartados]
            if not plan:
                print("No queda ningún bloque que descargar.", file=sys.stderr)
                return 1

    try:
        if args.mode == "async":
            failures = run_async(
                backend,
                cfg["dataset"],
                plan,
                args.force,
                output_dir,
                args.max_parallel,
                args.poll_seconds,
            )
        else:
            failures = run_sync(backend, cfg["dataset"], plan, args.force)
    except cds.UnsupportedOperation as exc:
        print(exc, file=sys.stderr)
        return 1
    except KeyboardInterrupt:
        print(
            "\nInterrumpido. Los trabajos ya encolados siguen en la cola del CDS; "
            "relanza el script para retomarlos.",
            file=sys.stderr,
        )
        return 130

    return 1 if failures else 0


if __name__ == "__main__":
    sys.exit(main())
