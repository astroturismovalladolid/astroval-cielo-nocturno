"""Crepúsculos astronómicos por fecha y lugar.

Define la "noche" como el intervalo entre el crepúsculo astronómico
vespertino y el matutino (Sol a más de 18° bajo el horizonte), tal y como
se especifica en la metodología del proyecto (ver README).
"""

from __future__ import annotations

import datetime as dt

from astral import Observer
from astral.sun import dawn, dusk

ASTRONOMICAL_DEPRESSION = 18.0


class PolarNightError(RuntimeError):
    """El Sol no alcanza los 18° bajo el horizonte en esa fecha y lugar."""


def night_window(
    lat: float, lon: float, date: dt.date, elevation: float = 0.0
) -> tuple[dt.datetime, dt.datetime]:
    """Devuelve (inicio, fin) del crepúsculo astronómico en UTC.

    `date` es la fecha del atardecer; la noche termina de madrugada al
    día siguiente.
    """
    observer = Observer(latitude=lat, longitude=lon, elevation=elevation)
    try:
        start = dusk(
            observer, date=date, depression=ASTRONOMICAL_DEPRESSION, tzinfo=dt.timezone.utc
        )
        end = dawn(
            observer,
            date=date + dt.timedelta(days=1),
            depression=ASTRONOMICAL_DEPRESSION,
            tzinfo=dt.timezone.utc,
        )
    except ValueError as exc:
        # astral lanza ValueError cuando el Sol no cruza esa depresión
        # (relevante a latitudes altas; no se espera en Castilla y León).
        raise PolarNightError(
            f"El Sol no alcanza {ASTRONOMICAL_DEPRESSION}° bajo el horizonte "
            f"en {lat},{lon} el {date}."
        ) from exc
    return start, end


def night_hours(
    lat: float, lon: float, date: dt.date, elevation: float = 0.0
) -> list[dt.datetime]:
    """Horas en punto (UTC) contenidas en la noche astronómica de `date`.

    Devuelve las horas exactas entre el inicio y el fin del crepúsculo,
    ambas inclusive, para poder indexar directamente un dataset horario
    de ERA5.
    """
    start, end = night_window(lat, lon, date, elevation)
    first_hour = start.replace(minute=0, second=0, microsecond=0)
    if first_hour < start:
        first_hour += dt.timedelta(hours=1)

    hours = []
    hour = first_hour
    while hour <= end:
        hours.append(hour)
        hour += dt.timedelta(hours=1)
    return hours


def clip_to_available_hours(
    hours: list[dt.datetime], available_hours_utc: set[int]
) -> list[dt.datetime]:
    """Filtra las horas de una noche a las que realmente se descargan.

    ERA5 se descarga solo en la ventana 18:00-05:00 UTC (config/download.yaml).
    En fechas cercanas al solsticio de invierno el crepúsculo matutino puede
    caer unos minutos después de las 05:00 UTC; esas horas no tienen dato
    descargado y deben excluirse explícitamente en vez de fallar en
    silencio al indexar el dataset.
    """
    return [h for h in hours if h.hour in available_hours_utc]


def date_range(start: dt.date, end: dt.date):
    """Generador de fechas [start, end] (ambas inclusive)."""
    current = start
    one_day = dt.timedelta(days=1)
    while current <= end:
        yield current
        current += one_day
