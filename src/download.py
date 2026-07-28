#!/usr/bin/env python3
"""Descarga ERA5 (Climate Data Store) por bloques de años, reanudable.

Uso:
    python src/download.py --start 1996 --end 2025 --block 3
    python src/download.py --start 1996 --end 2025 --block 3 --dry-run

Cada bloque de años se descarga a un único fichero NetCDF en data/raw/.
Si el fichero ya existe (y no está vacío) se omite, así que interrumpir
y volver a lanzar el script es seguro.
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

from config import DATA_RAW_DIR, load_download_config


def year_blocks(start: int, end: int, block: int) -> list[list[int]]:
    """Divide el rango [start, end] (inclusive) en bloques de `block` años."""
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


def download_block(
    client, dataset: str, request: dict, target: Path, force: bool
) -> None:
    if already_downloaded(target) and not force:
        print(f"[omitido] {target.name} ya existe")
        return
    target.parent.mkdir(parents=True, exist_ok=True)
    print(f"[descargando] {target.name} ({', '.join(request['year'])}) ...")
    client.retrieve(dataset, request, str(target))
    print(f"[completado] {target.name}")


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--start", type=int, required=True, help="Primer año (inclusive)")
    parser.add_argument("--end", type=int, required=True, help="Último año (inclusive)")
    parser.add_argument(
        "--block", type=int, default=3, help="Tamaño del bloque de años (default: 3)"
    )
    parser.add_argument(
        "--variables",
        choices=["all", "prioritarias", "complementarias"],
        default="all",
        help="Grupo de variables a descargar (default: all)",
    )
    parser.add_argument(
        "--output-dir", type=Path, default=None, help="Directorio de salida (default: data/raw)"
    )
    parser.add_argument("--config", type=Path, default=None, help="Ruta a config/download.yaml")
    parser.add_argument(
        "--force", action="store_true", help="Vuelve a descargar aunque el fichero ya exista"
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Muestra las peticiones sin llamar a la API del CDS",
    )
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv)
    cfg = load_download_config(args.config)
    output_dir = args.output_dir or DATA_RAW_DIR
    variables = variables_for(cfg, args.variables)

    blocks = year_blocks(args.start, args.end, args.block)

    client = None
    if not args.dry_run:
        try:
            from ecmwf.datastores import Client
        except ImportError:
            print(
                "ecmwf-datastores-client no está instalado. Ejecuta "
                "'pip install ecmwf-datastores-client' o usa --dry-run para ver "
                "las peticiones sin descargar.",
                file=sys.stderr,
            )
            return 1
        client = Client()

    for years in blocks:
        request = build_request(cfg, years, variables)
        target = target_path(output_dir, years, args.variables)
        if args.dry_run:
            print(f"[dry-run] {target} <- {request}")
            continue
        download_block(client, cfg["dataset"], request, target, args.force)

    return 0


if __name__ == "__main__":
    sys.exit(main())
