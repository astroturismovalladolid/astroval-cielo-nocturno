#!/usr/bin/env python3
"""Métricas nocturnas sobre la rejilla completa, para el mapa regional.

Uso:
    python src/grid.py

`analyze.py` responde "¿qué tal este emplazamiento?" tomando la celda más
cercana a unas coordenadas. Este script responde "¿cómo se reparte el
cielo por la comunidad?" evaluando **todas** las celdas del recorte.

Es el uso que mejor le sienta a ERA5: una celda de ~25 km no distingue dos
parajes vecinos, pero sí resuelve con solvencia el gradiente regional entre
la vertiente atlántica y los páramos del interior.

La salida es `data/processed/rejilla_metricas.csv`, en formato largo (una
fila por celda), que `report.py` convierte en mapa.
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

import numpy as np
import pandas as pd

from analyze import _latlon_names, _time_name, _variable, open_dataset
from config import DATA_PROCESSED_DIR, DATA_RAW_DIR, load_download_config, load_thresholds
from solar import is_astronomical_night

# Horas que cubre una noche en la ventana descargada: 18:00-05:00 UTC.
NIGHT_SLOTS = 12
FIRST_SLOT_HOUR = 18


def night_dates(times: pd.DatetimeIndex) -> pd.DatetimeIndex:
    """Fechas de atardecer presentes en la serie.

    Una hora anterior a las 06:00 UTC pertenece a la noche que empezó el
    día anterior.
    """
    shifted = times - pd.Timedelta(hours=FIRST_SLOT_HOUR)
    return pd.DatetimeIndex(sorted(set(shifted.normalize())))


def slot_timestamps(dates: pd.DatetimeIndex, slot: int) -> pd.DatetimeIndex:
    """Instante que corresponde a un hueco horario de la noche."""
    return dates + pd.Timedelta(hours=FIRST_SLOT_HOUR + slot)


def longest_run(mask: np.ndarray, axis: int = 1) -> np.ndarray:
    """Racha consecutiva más larga de True a lo largo de `axis`.

    Equivale a `analyze.longest_consecutive_run`, pero sobre toda la
    rejilla de una vez. Solo hay 12 huecos horarios, así que el bucle es
    corto y cada paso es una operación de NumPy sobre el resto de ejes.
    """
    n = mask.shape[axis]
    current = np.zeros(np.delete(mask.shape, axis), dtype=np.int16)
    best = np.zeros_like(current)
    for i in range(n):
        step = np.take(mask, i, axis=axis)
        current = np.where(step, current + 1, 0)
        best = np.maximum(best, current)
    return best


def grid_metrics(
    raw_dir: Path | None = None,
    thresholds: dict | None = None,
    chunk_nights: int = 512,
) -> pd.DataFrame:
    """Porcentaje de noches de cada tipo en cada celda de la rejilla."""
    raw_dir = raw_dir or DATA_RAW_DIR
    thresholds = thresholds or load_thresholds()

    ds = open_dataset(raw_dir)
    lat_name, lon_name = _latlon_names(ds)
    time_name = _time_name(ds)

    lats = np.asarray(ds[lat_name].values, dtype=float)
    lons = np.asarray(ds[lon_name].values, dtype=float)
    times = pd.DatetimeIndex(pd.to_datetime(ds[time_name].values))

    tcc_da = _variable(ds, "total_cloud_cover")
    if tcc_da is None:
        ds.close()
        raise KeyError(
            "El dataset no trae 'total_cloud_cover'; sin nubosidad total no hay métricas."
        )
    hcc_da = _variable(ds, "high_cloud_cover")

    aprov_cfg = thresholds["noche_aprovechable"]
    umbral_tcc = aprov_cfg["cobertura_total_max_pct"]
    horas_min = aprov_cfg["horas_consecutivas_min"]
    umbral_despejada = thresholds["noche_despejada"]["cobertura_media_max_pct"]
    foto_cfg = thresholds["noche_fotometrica"]

    dates = night_dates(times)
    shape = (len(lats), len(lons))
    totals = {
        k: np.zeros(shape, dtype=np.int32)
        for k in ("noches", "aprovechable", "despejada", "fotometrica", "perdida")
    }

    for start in range(0, len(dates), chunk_nights):
        block = dates[start : start + chunk_nights]

        tcc = np.full((len(block), NIGHT_SLOTS, *shape), np.nan, dtype=np.float32)
        hcc = (
            np.full((len(block), NIGHT_SLOTS, *shape), np.nan, dtype=np.float32)
            if hcc_da is not None
            else None
        )
        stamps = np.empty((len(block), NIGHT_SLOTS), dtype="datetime64[ns]")

        for slot in range(NIGHT_SLOTS):
            wanted = slot_timestamps(block, slot)
            stamps[:, slot] = wanted.values
            found = times.get_indexer(wanted)
            ok = found >= 0
            if not ok.any():
                continue
            tcc[ok, slot] = np.asarray(tcc_da.values[found[ok]], dtype=np.float32)
            if hcc is not None:
                hcc[ok, slot] = np.asarray(hcc_da.values[found[ok]], dtype=np.float32)

        # ERA5 da la nubosidad como fracción [0,1]; las métricas van en %.
        tcc *= 100.0
        if hcc is not None:
            hcc *= 100.0

        noche = is_astronomical_night(stamps.reshape(-1), lats, lons).reshape(
            len(block), NIGHT_SLOTS, *shape
        )
        con_dato = noche & ~np.isnan(tcc)

        # Media de la noche solo sobre las horas con dato.
        with np.errstate(invalid="ignore"):
            suma = np.where(con_dato, tcc, 0.0).sum(axis=1)
            cuenta = con_dato.sum(axis=1)
            media = np.where(cuenta > 0, suma / np.maximum(cuenta, 1), np.nan)

        despejado = con_dato & (tcc < umbral_tcc)
        racha = longest_run(despejado, axis=1)

        # Cada máscara es (noches del bloque, lat, lon): se suman las noches.
        hay_noche = cuenta > 0
        totals["noches"] += hay_noche.sum(axis=0)
        totals["aprovechable"] += (hay_noche & (racha >= horas_min)).sum(axis=0)
        totals["perdida"] += (hay_noche & ~despejado.any(axis=1)).sum(axis=0)
        totals["despejada"] += (hay_noche & (media < umbral_despejada)).sum(axis=0)

        if hcc is not None:
            with np.errstate(invalid="ignore"):
                media_alta = np.where(
                    cuenta > 0,
                    np.where(con_dato, hcc, 0.0).sum(axis=1) / np.maximum(cuenta, 1),
                    np.nan,
                )
            totals["fotometrica"] += (
                hay_noche
                & (media < foto_cfg["cobertura_media_max_pct"])
                & (media_alta < foto_cfg["nube_alta_max_pct"])
            ).sum(axis=0)

    ds.close()

    noches = totals["noches"]
    lat_mesh, lon_mesh = np.meshgrid(lats, lons, indexing="ij")
    with np.errstate(invalid="ignore", divide="ignore"):
        def pct(key):
            return np.where(noches > 0, totals[key] / np.maximum(noches, 1) * 100.0, np.nan)

        frame = pd.DataFrame(
            {
                "lat": lat_mesh.ravel(),
                "lon": lon_mesh.ravel(),
                "n_noches": noches.ravel(),
                "pct_aprovechable": pct("aprovechable").ravel(),
                "pct_despejada": pct("despejada").ravel(),
                "pct_fotometrica": pct("fotometrica").ravel(),
                "pct_perdida": pct("perdida").ravel(),
            }
        )
    return frame.sort_values(["lat", "lon"], ascending=[False, True]).reset_index(drop=True)


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--raw-dir", type=Path, default=None)
    parser.add_argument("--output-dir", type=Path, default=None)
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv)
    output_dir = args.output_dir or DATA_PROCESSED_DIR
    output_dir.mkdir(parents=True, exist_ok=True)

    try:
        frame = grid_metrics(args.raw_dir)
    except (FileNotFoundError, KeyError) as exc:
        print(exc, file=sys.stderr)
        return 1

    path = output_dir / "rejilla_metricas.csv"
    frame.to_csv(path, index=False, float_format="%.3f")

    validas = frame["n_noches"] > 0
    print(f"{int(validas.sum())} celdas con datos de {len(frame)}")
    if validas.any():
        mejor = frame.loc[frame["pct_aprovechable"].idxmax()]
        print(
            f"Máximo de noches aprovechables: {mejor['pct_aprovechable']:.1f}% "
            f"en {mejor['lat']:.2f}, {mejor['lon']:.2f}"
        )
    print(f"Rejilla guardada en {path}")

    # Aviso honesto: sin la ventana horaria completa el recuento cojea.
    cfg = load_download_config()
    if len(cfg["hours"]) < NIGHT_SLOTS:
        print(
            "Aviso: la ventana horaria configurada es menor que la noche completa; "
            "algunas noches quedarán parcialmente cubiertas.",
            file=sys.stderr,
        )
    return 0


if __name__ == "__main__":
    sys.exit(main())
