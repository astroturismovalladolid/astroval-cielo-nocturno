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
import sys
from pathlib import Path

import matplotlib

matplotlib.use("Agg")
import matplotlib.pyplot as plt
import pandas as pd

from config import DATA_PROCESSED_DIR, OUTPUTS_DIR

MESES = [
    "ene", "feb", "mar", "abr", "may", "jun",
    "jul", "ago", "sep", "oct", "nov", "dic",
]


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
