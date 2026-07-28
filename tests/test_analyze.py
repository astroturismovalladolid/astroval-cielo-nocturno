import datetime as dt

import pandas as pd
import pytest

from analyze import aggregate_monthly, classify_night, longest_consecutive_run

THRESHOLDS = {
    "noche_aprovechable": {"horas_consecutivas_min": 3, "cobertura_total_max_pct": 40},
    "noche_despejada": {"cobertura_media_max_pct": 20},
    "noche_fotometrica": {"cobertura_media_max_pct": 10, "nube_alta_max_pct": 10},
    "noche_perdida": {"cobertura_total_max_pct": 40},
    "riesgo_rocio": {"depresion_punto_rocio_max_c": 2},
    "cierre_por_viento": {"racha_sostenida_min_kmh": 40},
}


def _hours(n, start_hour=19):
    base = dt.datetime(2025, 1, 1, tzinfo=dt.timezone.utc)
    return [base.replace(hour=0) + dt.timedelta(hours=start_hour + i) for i in range(n)]


@pytest.mark.parametrize(
    "mask, expected",
    [
        ([True, True, True, False, False], 3),
        ([False, True, False, True, True], 2),
        ([False, False, False], 0),
        ([True, True, True, True], 4),
    ],
)
def test_longest_consecutive_run(mask, expected):
    assert longest_consecutive_run(pd.Series(mask)) == expected


def test_classify_night_aprovechable_but_not_despejada():
    hours = _hours(5)
    df_night = pd.DataFrame(
        {"total_cloud_cover": [10, 10, 10, 50, 50], "high_cloud_cover": [5, 5, 5, 5, 5]},
        index=hours,
    )
    result = classify_night(df_night, hours, THRESHOLDS)

    assert result["noche_aprovechable"] is True
    assert result["noche_perdida"] is False
    assert result["noche_despejada"] is False  # media 26% > 20%
    assert result["n_horas_con_dato"] == 5


def test_classify_night_perdida():
    hours = _hours(4)
    df_night = pd.DataFrame({"total_cloud_cover": [80, 90, 70, 60]}, index=hours)
    result = classify_night(df_night, hours, THRESHOLDS)

    assert result["noche_aprovechable"] is False
    assert result["noche_perdida"] is True


def test_classify_night_fotometrica():
    hours = _hours(4)
    df_night = pd.DataFrame(
        {"total_cloud_cover": [5, 5, 5, 5], "high_cloud_cover": [2, 2, 2, 2]}, index=hours
    )
    result = classify_night(df_night, hours, THRESHOLDS)

    assert result["noche_fotometrica"] is True
    assert result["noche_despejada"] is True


def test_classify_night_missing_data_hours_do_not_bridge_runs():
    # Solo hay dato en 2 de las 5 horas esperadas; reindex debe rellenar
    # con NaN (tratado como no-despejado) y no inventar una racha continua.
    hours = _hours(5)
    partial = pd.DataFrame({"total_cloud_cover": [10, 10]}, index=[hours[0], hours[1]])
    result = classify_night(partial, hours, THRESHOLDS)

    assert result["n_horas_noche"] == 5
    assert result["n_horas_con_dato"] == 2
    assert result["noche_aprovechable"] is False  # solo 2h consecutivas de dato, no 3


def test_classify_night_riesgo_rocio_y_viento():
    hours = _hours(3)
    df_night = pd.DataFrame(
        {
            "total_cloud_cover": [10, 10, 10],
            "2m_temperature": [5.0, 5.0, 1.0],
            "2m_dewpoint_temperature": [4.0, 2.0, 0.5],
            "10m_u_component_of_wind": [10.0, 10.0, 10.0],
            "10m_v_component_of_wind": [0.0, 0.0, 0.0],
        },
        index=hours,
    )
    result = classify_night(df_night, hours, THRESHOLDS)

    assert result["riesgo_rocio"] is True  # hora 0: depresión 1.0 < 2
    assert result["cierre_por_viento"] is False  # 36 km/h < 40 km/h


def test_aggregate_monthly_groups_by_calendar_month():
    nightly = pd.DataFrame(
        {
            "noche_aprovechable": [True, False, True, True],
            "noche_despejada": [False, False, True, True],
            "noche_fotometrica": [False, False, False, True],
            "noche_perdida": [False, True, False, False],
        },
        index=["2024-01-01", "2024-01-02", "2024-06-01", "2024-06-02"],
    )
    monthly = aggregate_monthly(nightly)

    assert list(monthly.index) == [1, 6]
    assert monthly.loc[1, "pct_aprovechable"] == 50.0
    assert monthly.loc[6, "pct_aprovechable"] == 100.0
