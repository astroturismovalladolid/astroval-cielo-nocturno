#!/usr/bin/env python3
"""Métricas nocturnas por emplazamiento a partir de los NetCDF de ERA5.

Uso:
    python src/analyze.py --site rello
    python src/analyze.py --all

Para cada noche (crepúsculo astronómico a crepúsculo astronómico) se
calculan las métricas definidas en config/thresholds.yaml, y se agregan
en tablas mensuales y anuales por emplazamiento, más una comparativa
conjunta. Todo se escribe en data/processed/.
"""

from __future__ import annotations

import argparse
import datetime as dt
import sys
from pathlib import Path

import numpy as np
import pandas as pd
import xarray as xr

from config import (
    DATA_PROCESSED_DIR,
    DATA_RAW_DIR,
    get_site,
    load_download_config,
    load_sites,
    load_thresholds,
    require_coordinates,
)
from twilight import clip_to_available_hours, date_range, night_hours

# Nombre corto que usa ERA5 en el NetCDF, indexado por variable larga del CDS.
SHORT_NAMES = {
    "total_cloud_cover": "tcc",
    "high_cloud_cover": "hcc",
    "medium_cloud_cover": "mcc",
    "low_cloud_cover": "lcc",
    "2m_temperature": "t2m",
    "2m_dewpoint_temperature": "d2m",
    "10m_u_component_of_wind": "u10",
    "10m_v_component_of_wind": "v10",
    "total_column_water_vapour": "tcwv",
    "snow_depth": "sd",
}

CLOUD_VARS = (
    "total_cloud_cover",
    "high_cloud_cover",
    "medium_cloud_cover",
    "low_cloud_cover",
)
TEMPERATURE_VARS = ("2m_temperature", "2m_dewpoint_temperature")


def open_dataset(raw_dir: Path) -> xr.Dataset:
    files = sorted(raw_dir.glob("*.nc"))
    if not files:
        raise FileNotFoundError(
            f"No hay ficheros .nc en {raw_dir}. Ejecuta antes 'python src/download.py'."
        )
    if len(files) == 1:
        return xr.open_dataset(files[0])
    return xr.open_mfdataset([str(f) for f in files], combine="by_coords")


def _latlon_names(ds: xr.Dataset) -> tuple[str, str]:
    if "latitude" in ds.coords and "longitude" in ds.coords:
        return "latitude", "longitude"
    if "lat" in ds.coords and "lon" in ds.coords:
        return "lat", "lon"
    raise KeyError("El dataset no tiene coordenadas latitude/longitude reconocibles.")


def _time_name(ds: xr.Dataset) -> str:
    if "valid_time" in ds.coords:
        return "valid_time"
    if "time" in ds.coords:
        return "time"
    raise KeyError("El dataset no tiene coordenada de tiempo reconocible.")


def _variable(ds: xr.Dataset, long_name: str):
    short = SHORT_NAMES[long_name]
    if short in ds.variables:
        return ds[short]
    if long_name in ds.variables:
        return ds[long_name]
    return None


def select_site_series(ds: xr.Dataset, lat: float, lon: float) -> tuple[pd.DataFrame, float, float]:
    """Extrae, para el punto de rejilla más cercano, las variables disponibles."""
    lat_name, lon_name = _latlon_names(ds)
    point = ds.sel({lat_name: lat, lon_name: lon}, method="nearest")
    used_lat = float(point[lat_name])
    used_lon = float(point[lon_name])

    time_name = _time_name(point)
    times = pd.to_datetime(point[time_name].values).tz_localize("UTC")

    data = {}
    for long_name in SHORT_NAMES:
        da = _variable(point, long_name)
        if da is not None:
            data[long_name] = np.asarray(da.values, dtype=float)

    df = pd.DataFrame(data, index=times)
    df.index.name = "time"
    df = df[~df.index.duplicated(keep="first")].sort_index()

    for col in CLOUD_VARS:
        if col in df.columns:
            df[col] = df[col] * 100.0  # fracción [0,1] -> porcentaje

    for col in TEMPERATURE_VARS:
        if col in df.columns:
            df[col] = df[col] - 273.15  # Kelvin -> Celsius

    return df, used_lat, used_lon


def longest_consecutive_run(mask: pd.Series) -> int:
    best = current = 0
    for value in mask.to_numpy():
        if value:
            current += 1
            best = max(best, current)
        else:
            current = 0
    return best


def classify_night(
    df_night: pd.DataFrame, expected_hours: list[dt.datetime], thresholds: dict
) -> dict:
    """Calcula las métricas de una noche a partir de sus horas horarias."""
    df_night = df_night.reindex(expected_hours)
    n_horas_noche = len(expected_hours)
    has_cloud = "total_cloud_cover" in df_night.columns
    n_horas_con_dato = int(df_night["total_cloud_cover"].notna().sum()) if has_cloud else 0

    result = {"n_horas_noche": n_horas_noche, "n_horas_con_dato": n_horas_con_dato}

    if not has_cloud or n_horas_con_dato == 0:
        result.update(
            cobertura_media_pct=np.nan,
            noche_aprovechable=False,
            noche_despejada=False,
            noche_fotometrica=False,
            noche_perdida=False,
        )
    else:
        tcc = df_night["total_cloud_cover"]
        cobertura_media = tcc.mean()

        aprov_cfg = thresholds["noche_aprovechable"]
        below_threshold = (tcc < aprov_cfg["cobertura_total_max_pct"]).fillna(False)
        max_run = longest_consecutive_run(below_threshold)

        result["cobertura_media_pct"] = cobertura_media
        result["noche_aprovechable"] = max_run >= aprov_cfg["horas_consecutivas_min"]
        result["noche_perdida"] = not bool(below_threshold.any())
        result["noche_despejada"] = bool(
            cobertura_media < thresholds["noche_despejada"]["cobertura_media_max_pct"]
        )

        if "high_cloud_cover" in df_night.columns:
            nube_alta_media = df_night["high_cloud_cover"].mean()
            foto_cfg = thresholds["noche_fotometrica"]
            result["noche_fotometrica"] = bool(
                cobertura_media < foto_cfg["cobertura_media_max_pct"]
                and nube_alta_media < foto_cfg["nube_alta_max_pct"]
            )
        else:
            result["noche_fotometrica"] = False

    if {"2m_temperature", "2m_dewpoint_temperature"} <= set(df_night.columns):
        depresion = df_night["2m_temperature"] - df_night["2m_dewpoint_temperature"]
        umbral = thresholds["riesgo_rocio"]["depresion_punto_rocio_max_c"]
        result["riesgo_rocio"] = bool((depresion < umbral).any())
    else:
        result["riesgo_rocio"] = None

    if {"10m_u_component_of_wind", "10m_v_component_of_wind"} <= set(df_night.columns):
        # Proxy de racha: velocidad instantánea horaria, no hay variable de
        # racha máxima entre las descargadas (ver README, variables pendientes).
        velocidad_kmh = np.hypot(
            df_night["10m_u_component_of_wind"], df_night["10m_v_component_of_wind"]
        ) * 3.6
        umbral = thresholds["cierre_por_viento"]["racha_sostenida_min_kmh"]
        result["cierre_por_viento"] = bool((velocidad_kmh > umbral).any())
    else:
        result["cierre_por_viento"] = None

    return result


def analyze_site(
    site_id: str,
    raw_dir: Path | None = None,
    thresholds: dict | None = None,
    sites: list[dict] | None = None,
) -> pd.DataFrame:
    """Devuelve una tabla con una fila por noche para el emplazamiento dado."""
    raw_dir = raw_dir or DATA_RAW_DIR
    thresholds = thresholds or load_thresholds()
    site = get_site(site_id, sites)
    require_coordinates(site)

    ds = open_dataset(raw_dir)
    df, used_lat, used_lon = select_site_series(ds, site["lat"], site["lon"])
    ds.close()

    if df.empty:
        return pd.DataFrame()

    download_cfg = load_download_config()
    available_hours = {int(h.split(":")[0]) for h in download_cfg["hours"]}

    start_date = (df.index.min() - dt.timedelta(days=1)).date()
    end_date = df.index.max().date()

    records = []
    for date in date_range(start_date, end_date):
        hours = night_hours(site["lat"], site["lon"], date, elevation=site.get("altitude_m") or 0.0)
        hours = clip_to_available_hours(hours, available_hours)
        if not hours:
            continue
        df_night = df.reindex(hours)
        metrics = classify_night(df_night, hours, thresholds)
        if metrics["n_horas_con_dato"] == 0:
            continue
        metrics["fecha"] = date.isoformat()
        records.append(metrics)

    nightly = pd.DataFrame.from_records(records)
    if not nightly.empty:
        nightly = nightly.set_index("fecha")
    nightly.attrs["site_id"] = site_id
    nightly.attrs["lat_usada"] = used_lat
    nightly.attrs["lon_usada"] = used_lon
    return nightly


def aggregate_monthly(nightly: pd.DataFrame) -> pd.DataFrame:
    dates = pd.to_datetime(nightly.index)
    grouped = nightly.groupby(dates.month)
    summary = pd.DataFrame(
        {
            "n_noches": grouped.size(),
            "pct_aprovechable": grouped["noche_aprovechable"].mean() * 100,
            "pct_despejada": grouped["noche_despejada"].mean() * 100,
            "pct_fotometrica": grouped["noche_fotometrica"].mean() * 100,
            "pct_perdida": grouped["noche_perdida"].mean() * 100,
        }
    )
    summary.index.name = "mes"
    return summary.sort_index()


def aggregate_annual(nightly: pd.DataFrame) -> pd.DataFrame:
    dates = pd.to_datetime(nightly.index)
    grouped = nightly.groupby(dates.year)
    summary = pd.DataFrame(
        {
            "n_noches": grouped.size(),
            "pct_aprovechable": grouped["noche_aprovechable"].mean() * 100,
            "pct_despejada": grouped["noche_despejada"].mean() * 100,
            "pct_fotometrica": grouped["noche_fotometrica"].mean() * 100,
            "pct_perdida": grouped["noche_perdida"].mean() * 100,
        }
    )
    summary.index.name = "anyo"
    return summary.sort_index()


def summarize(nightly: pd.DataFrame) -> dict:
    return {
        "lat_usada": nightly.attrs.get("lat_usada"),
        "lon_usada": nightly.attrs.get("lon_usada"),
        "n_noches": len(nightly),
        "pct_aprovechable": nightly["noche_aprovechable"].mean() * 100,
        "pct_despejada": nightly["noche_despejada"].mean() * 100,
        "pct_fotometrica": nightly["noche_fotometrica"].mean() * 100,
        "pct_perdida": nightly["noche_perdida"].mean() * 100,
    }


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    group = parser.add_mutually_exclusive_group(required=True)
    group.add_argument("--site", help="Id del emplazamiento (ver config/sites.yaml)")
    group.add_argument(
        "--all", action="store_true", help="Analiza todos los emplazamientos con coordenadas"
    )
    parser.add_argument("--raw-dir", type=Path, default=None)
    parser.add_argument("--output-dir", type=Path, default=None)
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv)
    sites = load_sites()
    thresholds = load_thresholds()
    output_dir = args.output_dir or DATA_PROCESSED_DIR
    output_dir.mkdir(parents=True, exist_ok=True)

    if args.all:
        targets = [s["id"] for s in sites if s.get("lat") is not None]
        skipped = [s["id"] for s in sites if s.get("lat") is None]
        if skipped:
            print(
                f"Omitidos por falta de coordenadas verificadas: {', '.join(skipped)}",
                file=sys.stderr,
            )
    else:
        targets = [args.site]

    summaries = []
    for site_id in targets:
        try:
            nightly = analyze_site(site_id, args.raw_dir, thresholds, sites)
        except (FileNotFoundError, KeyError, ValueError) as exc:
            print(f"[{site_id}] {exc}", file=sys.stderr)
            continue

        if nightly.empty:
            print(f"[{site_id}] no se encontraron noches con datos en data/raw/", file=sys.stderr)
            continue

        nightly_path = output_dir / f"{site_id}_noches.csv"
        nightly.to_csv(nightly_path)

        monthly = aggregate_monthly(nightly)
        monthly_path = output_dir / f"{site_id}_mensual.csv"
        monthly.to_csv(monthly_path)

        annual = aggregate_annual(nightly)
        annual_path = output_dir / f"{site_id}_anual.csv"
        annual.to_csv(annual_path)

        overall = summarize(nightly)
        overall["site_id"] = site_id
        summaries.append(overall)

        print(
            f"[{site_id}] {len(nightly)} noches analizadas "
            f"({overall['pct_aprovechable']:.1f}% aprovechables) -> {nightly_path.name}"
        )

    if summaries:
        comparison = pd.DataFrame(summaries).set_index("site_id")
        comparison_path = output_dir / "comparativa_emplazamientos.csv"
        comparison.to_csv(comparison_path)
        print(f"Comparativa guardada en {comparison_path}")
    else:
        print("No se generó ninguna tabla: revisa los mensajes anteriores.", file=sys.stderr)
        return 1

    return 0


if __name__ == "__main__":
    sys.exit(main())
