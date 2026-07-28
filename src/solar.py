"""Elevación solar vectorizada, para evaluar la rejilla completa.

`twilight.py` calcula los crepúsculos punto a punto con `astral`, que es
cómodo y legible para un puñado de emplazamientos. Para el mapa hay que
resolver la noche en cada celda de la rejilla y cada hora de la serie
—cientos de celdas por decenas de miles de horas—, y esa vía es demasiado
lenta.

Aquí se calcula la elevación solar con NumPy sobre todos los (tiempo,
latitud, longitud) a la vez. La precisión es de sobra: los datos son
horarios, así que un error de unos minutos en el instante del crepúsculo
no cambia ninguna hora de la clasificación.
"""

from __future__ import annotations

import numpy as np

ASTRONOMICAL_DEPRESSION = 18.0


def _julian_centuries(times: np.ndarray) -> np.ndarray:
    """Siglos julianos desde J2000.0 para un array de datetime64."""
    epoch = np.datetime64("2000-01-01T12:00:00")
    days = (times - epoch) / np.timedelta64(1, "D")
    return days.astype(float) / 36525.0


def solar_position(times: np.ndarray) -> tuple[np.ndarray, np.ndarray]:
    """(declinación, ecuación del tiempo) en grados y minutos.

    Algoritmo estándar NOAA, suficiente para determinar el crepúsculo con
    un error muy por debajo de la resolución horaria del dato.
    """
    t = _julian_centuries(times)

    # Longitud media y anomalía media del Sol.
    mean_long = np.radians((280.46646 + t * (36000.76983 + t * 0.0003032)) % 360.0)
    mean_anom = np.radians(357.52911 + t * (35999.05029 - 0.0001537 * t))

    # Ecuación del centro -> longitud verdadera.
    centre = (
        np.sin(mean_anom) * (1.914602 - t * (0.004817 + 0.000014 * t))
        + np.sin(2 * mean_anom) * (0.019993 - 0.000101 * t)
        + np.sin(3 * mean_anom) * 0.000289
    )
    true_long = np.degrees(mean_long) + centre
    apparent_long = np.radians(
        true_long - 0.00569 - 0.00478 * np.sin(np.radians(125.04 - 1934.136 * t))
    )

    # Oblicuidad de la eclíptica, con la corrección de nutación.
    seconds = 21.448 - t * (46.815 + t * (0.00059 - t * 0.001813))
    obliquity = 23.0 + (26.0 + seconds / 60.0) / 60.0
    obliquity_corr = np.radians(
        obliquity + 0.00256 * np.cos(np.radians(125.04 - 1934.136 * t))
    )

    declination = np.arcsin(np.sin(obliquity_corr) * np.sin(apparent_long))

    # Ecuación del tiempo, en minutos.
    y = np.tan(obliquity_corr / 2.0) ** 2
    eccentricity = 0.016708634 - t * (0.000042037 + 0.0000001267 * t)
    equation_of_time = 4.0 * np.degrees(
        y * np.sin(2 * mean_long)
        - 2.0 * eccentricity * np.sin(mean_anom)
        + 4.0 * eccentricity * y * np.sin(mean_anom) * np.cos(2 * mean_long)
        - 0.5 * y * y * np.sin(4 * mean_long)
        - 1.25 * eccentricity * eccentricity * np.sin(2 * mean_anom)
    )

    return np.degrees(declination), equation_of_time


def solar_elevation(
    times: np.ndarray, lats: np.ndarray, lons: np.ndarray
) -> np.ndarray:
    """Elevación solar en grados, con forma (tiempo, latitud, longitud).

    `times` debe estar en UTC.
    """
    declination, equation_of_time = solar_position(times)

    minutes_utc = (
        (times - times.astype("datetime64[D]")) / np.timedelta64(1, "m")
    ).astype(float)

    # (t, 1, 1) contra (1, lat, 1) y (1, 1, lon) para difundir a la rejilla.
    dec = np.radians(declination)[:, None, None]
    eot = equation_of_time[:, None, None]
    minutes = minutes_utc[:, None, None]
    lat = np.radians(lats)[None, :, None]
    lon = lons[None, None, :]

    # Ángulo horario: hora solar verdadera respecto al mediodía local.
    true_solar_time = (minutes + eot + 4.0 * lon) % 1440.0
    hour_angle = np.radians(true_solar_time / 4.0 - 180.0)

    cos_zenith = np.sin(lat) * np.sin(dec) + np.cos(lat) * np.cos(dec) * np.cos(
        hour_angle
    )
    return np.degrees(np.arcsin(np.clip(cos_zenith, -1.0, 1.0)))


def is_astronomical_night(
    times: np.ndarray,
    lats: np.ndarray,
    lons: np.ndarray,
    depression: float = ASTRONOMICAL_DEPRESSION,
) -> np.ndarray:
    """Máscara booleana: ¿el Sol está más de `depression` grados bajo el horizonte?"""
    return solar_elevation(times, lats, lons) < -depression
