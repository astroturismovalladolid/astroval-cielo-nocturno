"""El cálculo vectorizado debe coincidir con astral, que es la referencia.

`grid.py` no usa `astral` por velocidad, así que sin esta comprobación el
mapa podría alejarse en silencio de las tablas por emplazamiento.
"""

import datetime as dt

import numpy as np
import pytest

from solar import ASTRONOMICAL_DEPRESSION, is_astronomical_night, solar_elevation
from twilight import night_window

# Extremos del recorte descargado, más los emplazamientos con coordenadas.
LUGARES = [
    (41.367, -2.761),   # Rello, Soria
    (42.578, -4.400),   # Páramo de Boedo, Palencia
    (41.954, -5.280),   # Barcial de la Loma, Valladolid
    (43.5, -7.5),       # esquina noroeste del recorte
    (39.75, -1.5),      # esquina sureste del recorte
]
FECHAS = [dt.date(2024, m, 21) for m in (1, 3, 6, 9, 12)]


def _cruces_vectorizados(lat, lon, date):
    """(inicio, fin) del crepúsculo según solar.py, al minuto."""
    base = np.datetime64(f"{date}T12:00:00")
    minutos = np.arange(0, 24 * 60)
    times = base + minutos.astype("timedelta64[m]")
    noche = is_astronomical_night(times, np.array([lat]), np.array([lon]))[:, 0, 0]
    cambios = np.where(np.diff(noche.astype(int)) != 0)[0]
    if len(cambios) < 2:
        return None
    return times[cambios[0] + 1], times[cambios[1] + 1]


@pytest.mark.parametrize("lat, lon", LUGARES)
@pytest.mark.parametrize("date", FECHAS)
def test_crepusculos_coinciden_con_astral(lat, lon, date):
    cruces = _cruces_vectorizados(lat, lon, date)
    if cruces is None:
        pytest.skip("no hay noche astronómica completa en esa fecha y lugar")
    v_ini, v_fin = cruces

    a_ini, a_fin = night_window(lat, lon, date)
    a_ini = np.datetime64(a_ini.replace(tzinfo=None))
    a_fin = np.datetime64(a_fin.replace(tzinfo=None))

    # 2 minutos de margen: el barrido es al minuto y el dato es horario,
    # así que este acuerdo es holgadamente suficiente.
    assert abs((v_ini - a_ini) / np.timedelta64(1, "m")) <= 2
    assert abs((v_fin - a_fin) / np.timedelta64(1, "m")) <= 2


def test_elevacion_es_negativa_a_medianoche_y_positiva_a_mediodia():
    times = np.array(["2024-06-21T00:00:00", "2024-06-21T12:00:00"], dtype="datetime64[s]")
    elev = solar_elevation(times, np.array([41.4]), np.array([-4.7]))
    assert elev[0, 0, 0] < 0
    assert elev[1, 0, 0] > 0


def test_elevacion_maxima_mayor_en_solsticio_de_verano():
    lat, lon = np.array([41.4]), np.array([-4.7])
    horas = np.arange(0, 24 * 60, 10).astype("timedelta64[m]")
    verano = solar_elevation(np.datetime64("2024-06-21T00:00") + horas, lat, lon).max()
    invierno = solar_elevation(np.datetime64("2024-12-21T00:00") + horas, lat, lon).max()
    assert verano > invierno + 30  # ~47 grados de diferencia entre solsticios


def test_forma_de_salida_es_tiempo_por_lat_por_lon():
    times = np.array(["2024-01-01T22:00:00"], dtype="datetime64[s]")
    lats = np.array([41.0, 42.0, 43.0])
    lons = np.array([-5.0, -4.0])
    assert solar_elevation(times, lats, lons).shape == (1, 3, 2)


def test_la_noche_dura_menos_en_verano_que_en_invierno():
    lat, lon = 41.4, -4.7
    horas = np.arange(0, 24 * 60, 1).astype("timedelta64[m]")
    verano = is_astronomical_night(
        np.datetime64("2024-06-21T12:00") + horas, np.array([lat]), np.array([lon])
    ).sum()
    invierno = is_astronomical_night(
        np.datetime64("2024-12-21T12:00") + horas, np.array([lat]), np.array([lon])
    ).sum()
    assert invierno > verano


def test_depresion_mas_exigente_acorta_la_noche():
    times = np.datetime64("2024-03-21T12:00") + np.arange(0, 24 * 60, 1).astype(
        "timedelta64[m]"
    )
    lat, lon = np.array([41.4]), np.array([-4.7])
    astronomica = is_astronomical_night(times, lat, lon, ASTRONOMICAL_DEPRESSION).sum()
    civil = is_astronomical_night(times, lat, lon, 6.0).sum()
    assert civil > astronomica
