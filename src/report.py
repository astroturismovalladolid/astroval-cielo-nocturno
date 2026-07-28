#!/usr/bin/env python3
"""Tablas y gráficos a partir de las tablas generadas por analyze.py.

Uso:
    python src/report.py

Lee data/processed/comparativa_emplazamientos.csv y los *_mensual.csv de
cada emplazamiento, y genera en outputs/:

- tables/comparativa_emplazamientos.md   tabla comparativa en Markdown
- figures/comparativa_aprovechables.png  barras: % noches aprovechables
- figures/<site_id>_estacional.png       distribución mensual por sitio
"""

from __future__ import annotations

import argparse
import math
import sys
from pathlib import Path

import matplotlib

matplotlib.use("Agg")
import matplotlib.patheffects as path_effects
import matplotlib.pyplot as plt
import numpy as np
import pandas as pd
from matplotlib.colors import LinearSegmentedColormap
from mpl_toolkits.axes_grid1 import make_axes_locatable

from config import DATA_PROCESSED_DIR, OUTPUTS_DIR, load_reference_cities, load_sites

MESES = [
    "ene", "feb", "mar", "abr", "may", "jun",
    "jul", "ago", "sep", "oct", "nov", "dic",
]

# Superficie y tintas de texto. El texto nunca lleva el color de la serie:
# la identidad la aporta la marca, no la letra.
SURFACE = "#fcfcfb"
INK_PRIMARY = "#0b0b0b"
INK_SECONDARY = "#52514e"
GRIDLINE = "#e1e0d9"

# Rampa secuencial de un solo tono (azul), claro -> oscuro. Para magnitud
# continua se usa un único tono: una rampa multicolor tipo arcoíris rompe
# la lectura de "más es más".
SEQUENTIAL_BLUE = [
    "#cde2fb", "#b7d3f6", "#9ec5f4", "#86b6ef", "#6da7ec", "#5598e7",
    "#3987e5", "#2a78d6", "#256abf", "#1c5cab", "#184f95", "#104281", "#0d366b",
]
CMAP_NOCHES = LinearSegmentedColormap.from_list("noches", SEQUENTIAL_BLUE)

SERIES_BLUE = "#2a78d6"
SERIES_ORANGE = "#eb6834"


def load_comparison(processed_dir: Path) -> pd.DataFrame:
    path = processed_dir / "comparativa_emplazamientos.csv"
    if not path.exists():
        raise FileNotFoundError(
            f"No existe {path}. Ejecuta antes 'python src/analyze.py --all'."
        )
    return pd.read_csv(path, index_col="site_id")


def write_comparison_table(comparison: pd.DataFrame, tables_dir: Path) -> Path:
    tables_dir.mkdir(parents=True, exist_ok=True)
    path = tables_dir / "comparativa_emplazamientos.md"
    ordered = comparison.sort_values("pct_aprovechable", ascending=False)
    with open(path, "w", encoding="utf-8") as f:
        f.write("| Emplazamiento | Noches | % Aprovechable | % Despejada | % Fotométrica | % Perdida |\n")
        f.write("| --- | --- | --- | --- | --- | --- |\n")
        for site_id, row in ordered.iterrows():
            f.write(
                f"| {site_id} | {int(row['n_noches'])} "
                f"| {row['pct_aprovechable']:.1f} | {row['pct_despejada']:.1f} "
                f"| {row['pct_fotometrica']:.1f} | {row['pct_perdida']:.1f} |\n"
            )
    return path


def plot_comparison(comparison: pd.DataFrame, figures_dir: Path) -> Path:
    figures_dir.mkdir(parents=True, exist_ok=True)
    ordered = comparison.sort_values("pct_aprovechable", ascending=False)

    fig, ax = plt.subplots(figsize=(8, 4.5))
    ax.bar(ordered.index, ordered["pct_aprovechable"], color="#2b4a6f")
    ax.set_ylabel("% de noches aprovechables")
    ax.set_title("Comparativa de emplazamientos — noches astronómicamente aprovechables")
    ax.set_ylim(0, 100)
    ax.tick_params(axis="x", rotation=30)
    fig.tight_layout()

    path = figures_dir / "comparativa_aprovechables.png"
    fig.savefig(path, dpi=150)
    plt.close(fig)
    return path


def plot_seasonal(site_id: str, monthly: pd.DataFrame, figures_dir: Path) -> Path:
    figures_dir.mkdir(parents=True, exist_ok=True)
    monthly = monthly.set_index("mes").reindex(range(1, 13))

    fig, ax = plt.subplots(figsize=(8, 4.5))
    ax.bar(range(1, 13), monthly["pct_aprovechable"], color="#2b4a6f", label="Aprovechable")
    ax.plot(range(1, 13), monthly["pct_fotometrica"], color="#e0a800", marker="o", label="Fotométrica")
    ax.set_xticks(range(1, 13))
    ax.set_xticklabels(MESES)
    ax.set_ylabel("% de noches")
    ax.set_ylim(0, 100)
    ax.set_title(f"Distribución estacional — {site_id}")
    ax.legend()
    fig.tight_layout()

    path = figures_dir / f"{site_id}_estacional.png"
    fig.savefig(path, dpi=150)
    plt.close(fig)
    return path


def _halo(size: int, weight: str = "normal", color: str = INK_PRIMARY) -> dict:
    """Estilo de etiqueta legible sobre cualquier celda del mapa."""
    return {
        "fontsize": size,
        "color": color,
        "fontweight": weight,
        "path_effects": [path_effects.withStroke(linewidth=2.5, foreground=SURFACE)],
    }


def resolver_colisiones(fig, prioritarias: list, secundarias: list) -> int:
    """Aparta las etiquetas secundarias que pisen a las prioritarias.

    Los nombres de los emplazamientos son largos y su caja de texto se
    solapa con la de alguna capital cercana. Se prueban colocaciones
    alternativas alrededor del punto y, si ninguna queda libre, se oculta
    el rótulo de la ciudad: su punto sigue en el mapa, que es lo que
    aporta la referencia geográfica.

    Devuelve cuántas etiquetas se han acabado ocultando.
    """
    fig.canvas.draw()
    renderer = fig.canvas.get_renderer()

    def caja(artista):
        return artista.get_window_extent(renderer=renderer).expanded(1.05, 1.15)

    fijas = [caja(a) for a in prioritarias]
    alternativas = [(6, -3), (6, 8), (-6, -3), (-6, 8), (6, -14), (-6, -14)]
    ocultas = 0

    for etiqueta in secundarias:
        original_xy = etiqueta.get_position()
        original_ha = etiqueta.get_ha()
        for dx, dy in alternativas:
            etiqueta.set_position((dx, dy))
            etiqueta.set_ha("right" if dx < 0 else "left")
            fig.canvas.draw()
            if not any(caja(etiqueta).overlaps(otra) for otra in fijas):
                break
        else:
            etiqueta.set_position(original_xy)
            etiqueta.set_ha(original_ha)
            etiqueta.set_visible(False)
            ocultas += 1

    return ocultas


def plot_map(
    grid: pd.DataFrame,
    figures_dir: Path,
    metric: str = "pct_aprovechable",
    titulo: str = "Noches astronómicamente aprovechables",
) -> Path:
    """Mapa de calor de la métrica sobre la rejilla completa."""
    figures_dir.mkdir(parents=True, exist_ok=True)

    lats = np.sort(grid["lat"].unique())[::-1]
    lons = np.sort(grid["lon"].unique())
    valores = (
        grid.pivot(index="lat", columns="lon", values=metric)
        .reindex(index=lats, columns=lons)
        .to_numpy()
    )

    # Bordes de celda: ERA5 da el centro, la celda ocupa medio paso a cada lado.
    paso_lat = abs(lats[1] - lats[0]) if len(lats) > 1 else 0.25
    paso_lon = abs(lons[1] - lons[0]) if len(lons) > 1 else 0.25
    bordes_lat = np.append(lats + paso_lat / 2, lats[-1] - paso_lat / 2)
    bordes_lon = np.append(lons - paso_lon / 2, lons[-1] + paso_lon / 2)

    fig, ax = plt.subplots(figsize=(10, 7.5))
    fig.patch.set_facecolor(SURFACE)
    ax.set_facecolor(SURFACE)

    malla = ax.pcolormesh(
        bordes_lon, bordes_lat, valores, cmap=CMAP_NOCHES, shading="flat"
    )

    # Proyección plana: se corrige la relación de aspecto para que un grado
    # de longitud no aparente lo mismo que uno de latitud.
    ax.set_aspect(1.0 / math.cos(math.radians(float(np.mean(lats)))))

    # La barra se ancla a los ejes para que comparta su altura: con la
    # relación de aspecto fijada, un colorbar de figura queda descolgado.
    divider = make_axes_locatable(ax)
    cax = divider.append_axes("right", size="3%", pad=0.15, axes_class=plt.Axes)
    cbar = fig.colorbar(malla, cax=cax)
    cbar.set_label(f"% de noches ({metric.replace('pct_', '')})", color=INK_SECONDARY)
    cbar.ax.tick_params(colors=INK_SECONDARY, labelsize=8, length=0)
    cbar.outline.set_visible(False)

    etiquetas_ciudad = []
    for ciudad in load_reference_cities():
        if not (bordes_lon[0] <= ciudad["lon"] <= bordes_lon[-1]):
            continue
        if not (bordes_lat[-1] <= ciudad["lat"] <= bordes_lat[0]):
            continue
        ax.plot(
            ciudad["lon"], ciudad["lat"], "o",
            markersize=4, markerfacecolor=SURFACE,
            markeredgecolor=INK_SECONDARY, markeredgewidth=1.2, zorder=3,
        )
        etiquetas_ciudad.append(
            ax.annotate(
                ciudad["nombre"], (ciudad["lon"], ciudad["lat"]),
                textcoords="offset points", xytext=(6, -3),
                **_halo(8, color=INK_SECONDARY),
            )
        )

    etiquetas_sitio = []
    for sitio in load_sites():
        if sitio.get("lat") is None or sitio.get("lon") is None:
            continue
        if not (bordes_lon[0] <= sitio["lon"] <= bordes_lon[-1]):
            continue
        if not (bordes_lat[-1] <= sitio["lat"] <= bordes_lat[0]):
            continue
        ax.plot(
            sitio["lon"], sitio["lat"], "^",
            markersize=9, markerfacecolor=SERIES_ORANGE,
            markeredgecolor=SURFACE, markeredgewidth=1.6, zorder=4,
        )
        # Cerca del borde derecho la etiqueta se vuelca hacia dentro, o se
        # sale del gráfico: los nombres largos son la norma aquí.
        cerca_del_borde = sitio["lon"] > bordes_lon[0] + 0.72 * (
            bordes_lon[-1] - bordes_lon[0]
        )
        etiquetas_sitio.append(
            ax.annotate(
                sitio["name"], (sitio["lon"], sitio["lat"]),
                textcoords="offset points",
                xytext=(-8, 5) if cerca_del_borde else (8, 5),
                ha="right" if cerca_del_borde else "left",
                **_halo(9, weight="bold"),
            )
        )

    resolver_colisiones(fig, etiquetas_sitio, etiquetas_ciudad)

    ax.set_title(titulo, color=INK_PRIMARY, fontsize=13, pad=12)
    ax.set_xlabel("Longitud", color=INK_SECONDARY, fontsize=9)
    ax.set_ylabel("Latitud", color=INK_SECONDARY, fontsize=9)
    ax.tick_params(colors=INK_SECONDARY, labelsize=8, length=0)
    for lado in ax.spines.values():
        lado.set_visible(False)
    # Sin rejilla: sobre un mapa de celdas se dibujaría encima del dato y
    # compite con él. La propia malla ya estructura la lectura.
    ax.grid(False)

    # Anclado a los ejes, no a la figura: con la relación de aspecto fijada
    # el eje no ocupa toda la altura y un pie en coordenadas de figura
    # quedaría descolgado muy por debajo.
    ax.annotate(
        "ERA5, rejilla 0,25° (~21 × 28 km); resolución nativa ~31 km, más gruesa "
        "que la rejilla: léase el gradiente, no la celda.\nSolo nubosidad — no "
        "representa oscuridad del cielo, seeing ni horizonte.\n"
        "▲ emplazamientos   ● capitales de provincia",
        xy=(0.5, -0.14), xycoords="axes fraction",
        ha="center", va="top", fontsize=8, color=INK_SECONDARY,
    )

    path = figures_dir / f"mapa_{metric.replace('pct_', '')}.png"
    fig.savefig(path, dpi=150, facecolor=SURFACE, bbox_inches="tight")
    plt.close(fig)
    return path


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--processed-dir", type=Path, default=None)
    parser.add_argument("--output-dir", type=Path, default=None)
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv)
    processed_dir = args.processed_dir or DATA_PROCESSED_DIR
    output_dir = args.output_dir or OUTPUTS_DIR

    try:
        comparison = load_comparison(processed_dir)
    except FileNotFoundError as exc:
        print(exc, file=sys.stderr)
        return 1

    table_path = write_comparison_table(comparison, output_dir / "tables")
    print(f"Tabla comparativa: {table_path}")

    figure_path = plot_comparison(comparison, output_dir / "figures")
    print(f"Gráfico comparativo: {figure_path}")

    grid_path = processed_dir / "rejilla_metricas.csv"
    if grid_path.exists():
        grid = pd.read_csv(grid_path)
        for metric, titulo in (
            ("pct_aprovechable", "Noches astronómicamente aprovechables"),
            ("pct_fotometrica", "Noches fotométricas"),
        ):
            map_path = plot_map(grid, output_dir / "figures", metric, titulo)
            print(f"Mapa regional: {map_path}")
    else:
        print(
            f"No existe {grid_path.name}; ejecuta 'python src/grid.py' para el "
            "mapa regional.",
            file=sys.stderr,
        )

    for site_id in comparison.index:
        monthly_path = processed_dir / f"{site_id}_mensual.csv"
        if not monthly_path.exists():
            print(f"[{site_id}] no existe {monthly_path.name}, se omite el gráfico estacional", file=sys.stderr)
            continue
        monthly = pd.read_csv(monthly_path)
        seasonal_path = plot_seasonal(site_id, monthly, output_dir / "figures")
        print(f"[{site_id}] gráfico estacional: {seasonal_path}")

    return 0


if __name__ == "__main__":
    sys.exit(main())
