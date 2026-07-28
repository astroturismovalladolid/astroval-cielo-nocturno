import numpy as np
import pandas as pd
import pytest

from analyze import longest_consecutive_run
from grid import NIGHT_SLOTS, longest_run, night_dates, slot_timestamps


def test_night_dates_agrupa_la_madrugada_con_la_tarde_anterior():
    times = pd.DatetimeIndex(
        ["2024-03-10 18:00", "2024-03-10 23:00", "2024-03-11 02:00", "2024-03-11 05:00"]
    )
    assert list(night_dates(times)) == [pd.Timestamp("2024-03-10")]


def test_night_dates_separa_dos_noches():
    times = pd.DatetimeIndex(
        ["2024-03-10 20:00", "2024-03-11 03:00", "2024-03-11 21:00", "2024-03-12 01:00"]
    )
    assert list(night_dates(times)) == [
        pd.Timestamp("2024-03-10"),
        pd.Timestamp("2024-03-11"),
    ]


def test_slot_timestamps_cubre_de_las_18_a_las_5():
    fecha = pd.DatetimeIndex(["2024-03-10"])
    horas = [slot_timestamps(fecha, s)[0] for s in range(NIGHT_SLOTS)]

    assert horas[0] == pd.Timestamp("2024-03-10 18:00")
    assert horas[6] == pd.Timestamp("2024-03-11 00:00")
    assert horas[-1] == pd.Timestamp("2024-03-11 05:00")


@pytest.mark.parametrize(
    "fila, esperado",
    [
        ([True, True, True, False, False], 3),
        ([False, True, False, True, True], 2),
        ([False, False, False], 0),
        ([True, True, True, True], 4),
        ([True, False, True, False, True], 1),
    ],
)
def test_longest_run_coincide_con_la_version_escalar(fila, esperado):
    # Una sola celda: (1 noche, n horas, 1 lat, 1 lon)
    mask = np.array(fila).reshape(1, len(fila), 1, 1)
    assert longest_run(mask, axis=1)[0, 0, 0] == esperado
    assert longest_consecutive_run(pd.Series(fila)) == esperado


def test_longest_run_sobre_rejilla_aleatoria_coincide_celda_a_celda():
    """Comprobación cruzada: la versión vectorizada contra la escalar."""
    rng = np.random.default_rng(11)
    mask = rng.random((7, NIGHT_SLOTS, 4, 5)) < 0.5

    resultado = longest_run(mask, axis=1)

    assert resultado.shape == (7, 4, 5)
    for n in range(mask.shape[0]):
        for i in range(mask.shape[2]):
            for j in range(mask.shape[3]):
                esperado = longest_consecutive_run(pd.Series(mask[n, :, i, j]))
                assert resultado[n, i, j] == esperado


def test_longest_run_todo_falso_da_cero():
    mask = np.zeros((2, NIGHT_SLOTS, 3, 3), dtype=bool)
    assert longest_run(mask, axis=1).max() == 0


def test_longest_run_todo_verdadero_da_la_longitud_completa():
    mask = np.ones((2, NIGHT_SLOTS, 3, 3), dtype=bool)
    assert longest_run(mask, axis=1).min() == NIGHT_SLOTS
