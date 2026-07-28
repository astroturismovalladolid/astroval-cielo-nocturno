"""Carga de la configuración del proyecto (config/*.yaml)."""

from __future__ import annotations

from pathlib import Path

import yaml

PROJECT_ROOT = Path(__file__).resolve().parent.parent
CONFIG_DIR = PROJECT_ROOT / "config"
DATA_RAW_DIR = PROJECT_ROOT / "data" / "raw"
DATA_PROCESSED_DIR = PROJECT_ROOT / "data" / "processed"
OUTPUTS_DIR = PROJECT_ROOT / "outputs"


def _load_yaml(path: Path) -> dict:
    with open(path, encoding="utf-8") as f:
        return yaml.safe_load(f)


def load_sites(path: Path | None = None) -> list[dict]:
    """Devuelve la lista de emplazamientos definidos en config/sites.yaml."""
    path = path or CONFIG_DIR / "sites.yaml"
    return _load_yaml(path)["sites"]


def get_site(site_id: str, sites: list[dict] | None = None) -> dict:
    """Busca un emplazamiento por id. Lanza KeyError si no existe."""
    sites = sites if sites is not None else load_sites()
    for site in sites:
        if site["id"] == site_id:
            return site
    known = ", ".join(s["id"] for s in sites)
    raise KeyError(f"Emplazamiento desconocido: '{site_id}'. Disponibles: {known}")


def require_coordinates(site: dict) -> None:
    """Lanza ValueError si al emplazamiento le faltan coordenadas verificadas."""
    if site.get("lat") is None or site.get("lon") is None:
        raise ValueError(
            f"El emplazamiento '{site['id']}' no tiene coordenadas verificadas "
            "todavía (lat/lon a null en config/sites.yaml)."
        )


def load_thresholds(path: Path | None = None) -> dict:
    """Devuelve los umbrales de config/thresholds.yaml."""
    path = path or CONFIG_DIR / "thresholds.yaml"
    return _load_yaml(path)


def load_download_config(path: Path | None = None) -> dict:
    """Devuelve la configuración de descarga de config/download.yaml."""
    path = path or CONFIG_DIR / "download.yaml"
    return _load_yaml(path)


def load_reference_cities(path: Path | None = None) -> list[dict]:
    """Ciudades de referencia para orientar el mapa (config/referencias.yaml)."""
    path = path or CONFIG_DIR / "referencias.yaml"
    if not path.exists():
        return []
    return _load_yaml(path).get("ciudades", [])
