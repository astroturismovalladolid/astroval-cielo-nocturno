import pytest

import cds
from download import run_async, run_sync, target_path, variables_for, year_blocks
from jobs import JobState

DATASET = "reanalysis-era5-single-levels"

CFG = {
    "variables": {
        "prioritarias": ["total_cloud_cover"],
        "complementarias": ["2m_temperature"],
    }
}


class FakeJob:
    """Trabajo que está listo tras `ready_after` comprobaciones.

    Registra sus acciones en el log compartido del backend para poder
    afirmar sobre el *entrelazado* de envíos y descargas, no solo sobre el
    resultado final.
    """

    def __init__(self, request_id, ready_after=0, fail_on_ready=False, events=None):
        self.request_id = request_id
        self._ready_after = ready_after
        self._checks = 0
        self._fail_on_ready = fail_on_ready
        self.events = events if events is not None else []

    def ready(self):
        if self._fail_on_ready:
            self.events.append(("fail", self.request_id))
            raise RuntimeError("required licences not accepted")
        self._checks += 1
        return self._checks > self._ready_after

    def download(self, target):
        self.events.append(("download", self.request_id))
        with open(target, "w", encoding="utf-8") as f:
            f.write("netcdf-falso")
        return target


class FakeBackend:
    name = "fake"
    supports_async = True

    def __init__(self, jobs=None):
        self.events = []
        self._jobs = jobs or {}
        for job in self._jobs.values():
            job.events = self.events
        self.retrieved = []
        self.failed_resumes = []
        self._counter = 0

    # Vistas derivadas del log, para que los tests lean claro.
    @property
    def submitted(self):
        return [rid for kind, rid in self.events if kind == "submit"]

    @property
    def resumed(self):
        return [rid for kind, rid in self.events if kind == "resume"]

    def peak_in_flight(self):
        """Máximo de trabajos encolados a la vez a lo largo de la ejecución."""
        peak = current = 0
        for kind, _ in self.events:
            if kind in ("submit", "resume"):
                current += 1
                peak = max(peak, current)
            elif kind in ("download", "fail"):
                current -= 1
        return peak

    def retrieve(self, dataset, request, target):
        self.retrieved.append(target)
        with open(target, "w", encoding="utf-8") as f:
            f.write("netcdf-falso")
        return target

    def submit(self, dataset, request):
        self._counter += 1
        request_id = f"req-{self._counter}"
        self.events.append(("submit", request_id))
        return self._jobs.setdefault(
            request_id, FakeJob(request_id, events=self.events)
        )

    def get_job(self, request_id):
        if request_id not in self._jobs:
            # No se registra: un retomado fallido no ocupa sitio en la cola.
            self.failed_resumes.append(request_id)
            raise RuntimeError(f"request desconocida: {request_id}")
        self.events.append(("resume", request_id))
        return self._jobs[request_id]


def make_plan(tmp_path, n):
    return [
        (tmp_path / f"era5_{1996 + i}_all.nc", {"year": [str(1996 + i)]}) for i in range(n)
    ]


# --- planificación de bloques -------------------------------------------------


def test_year_blocks_splits_inclusive_range():
    assert year_blocks(1996, 2001, 3) == [[1996, 1997, 1998], [1999, 2000, 2001]]


def test_year_blocks_last_block_may_be_shorter():
    assert year_blocks(1996, 2000, 3) == [[1996, 1997, 1998], [1999, 2000]]


def test_year_blocks_rejects_inverted_range():
    with pytest.raises(ValueError):
        year_blocks(2025, 1996, 3)


def test_year_blocks_rejects_zero_block():
    with pytest.raises(ValueError):
        year_blocks(1996, 2000, 0)


def test_target_path_includes_variables_group(tmp_path):
    prioritarias = target_path(tmp_path, [1996, 1998], "prioritarias")
    complementarias = target_path(tmp_path, [1996, 1998], "complementarias")

    # Grupos distintos del mismo rango no pueden colisionar en disco.
    assert prioritarias != complementarias
    assert prioritarias.name == "era5_1996_1998_prioritarias.nc"


def test_target_path_single_year_has_no_range_suffix(tmp_path):
    assert target_path(tmp_path, [1996], "all").name == "era5_1996_all.nc"


def test_variables_for_all_merges_both_groups():
    assert variables_for(CFG, "all") == ["total_cloud_cover", "2m_temperature"]


def test_variables_for_rejects_unknown_group():
    with pytest.raises(ValueError):
        variables_for(CFG, "inventadas")


# --- modo síncrono ------------------------------------------------------------


def test_run_sync_downloads_every_block(tmp_path):
    backend = FakeBackend()
    plan = make_plan(tmp_path, 3)

    failures = run_sync(backend, DATASET, plan, force=False)

    assert failures == 0
    assert len(backend.retrieved) == 3


def test_run_sync_skips_existing_files(tmp_path):
    backend = FakeBackend()
    plan = make_plan(tmp_path, 2)
    plan[0][0].write_text("ya-descargado", encoding="utf-8")

    run_sync(backend, DATASET, plan, force=False)

    assert len(backend.retrieved) == 1


def test_run_sync_force_redownloads(tmp_path):
    backend = FakeBackend()
    plan = make_plan(tmp_path, 2)
    plan[0][0].write_text("ya-descargado", encoding="utf-8")

    run_sync(backend, DATASET, plan, force=True)

    assert len(backend.retrieved) == 2


# --- modo asíncrono -----------------------------------------------------------


def test_run_async_downloads_every_block(tmp_path):
    backend = FakeBackend()
    plan = make_plan(tmp_path, 3)

    failures = run_async(
        backend, DATASET, plan, force=False, output_dir=tmp_path,
        max_parallel=4, poll_seconds=0,
    )

    assert failures == 0
    for target, _ in plan:
        assert target.exists()


def test_run_async_submits_up_front_not_one_by_one(tmp_path):
    """El objetivo del modo async: varios bloques en cola a la vez.

    Se afirma sobre el entrelazado, no sobre el orden final: una
    implementación secuencial (enviar, esperar, descargar, enviar el
    siguiente) produce la misma lista de envíos, así que solo la posición
    relativa de la primera descarga distingue ambos comportamientos.
    """
    jobs = {f"req-{i}": FakeJob(f"req-{i}", ready_after=1) for i in range(1, 4)}
    backend = FakeBackend(jobs)
    plan = make_plan(tmp_path, 3)

    run_async(
        backend, DATASET, plan, force=False, output_dir=tmp_path,
        max_parallel=3, poll_seconds=0,
    )

    kinds = [kind for kind, _ in backend.events]
    first_download = kinds.index("download")
    # Los tres bloques están encolados antes de descargar ninguno.
    assert kinds[:first_download] == ["submit", "submit", "submit"]


def test_run_async_respects_max_parallel(tmp_path):
    jobs = {f"req-{i}": FakeJob(f"req-{i}", ready_after=2) for i in range(1, 5)}
    backend = FakeBackend(jobs)
    plan = make_plan(tmp_path, 4)

    run_async(
        backend, DATASET, plan, force=False, output_dir=tmp_path,
        max_parallel=2, poll_seconds=0,
    )

    assert backend.peak_in_flight() == 2
    assert len(backend.submitted) == 4


def test_run_async_peak_in_flight_grows_with_max_parallel(tmp_path):
    """Control del test anterior: el tope es efectivo, no incidental."""
    jobs = {f"req-{i}": FakeJob(f"req-{i}", ready_after=2) for i in range(1, 5)}
    backend = FakeBackend(jobs)

    run_async(
        backend, DATASET, make_plan(tmp_path, 4), force=False, output_dir=tmp_path,
        max_parallel=4, poll_seconds=0,
    )

    assert backend.peak_in_flight() == 4


def test_run_async_clears_state_after_success(tmp_path):
    backend = FakeBackend()
    plan = make_plan(tmp_path, 2)

    run_async(
        backend, DATASET, plan, force=False, output_dir=tmp_path,
        max_parallel=2, poll_seconds=0,
    )

    assert len(JobState.load(tmp_path)) == 0


def test_run_async_resumes_persisted_request(tmp_path):
    """Un bloque ya encolado no se reenvía: se retoma por su request_id."""
    plan = make_plan(tmp_path, 1)
    target = plan[0][0]

    state = JobState.load(tmp_path)
    state.record(target.name, "req-previa", DATASET)
    state.save()

    jobs = {"req-previa": FakeJob("req-previa")}
    backend = FakeBackend(jobs)

    run_async(
        backend, DATASET, plan, force=False, output_dir=tmp_path,
        max_parallel=2, poll_seconds=0,
    )

    assert backend.resumed == ["req-previa"]
    assert backend.submitted == []
    assert target.exists()


def test_run_async_resubmits_when_stored_request_is_gone(tmp_path):
    plan = make_plan(tmp_path, 1)
    target = plan[0][0]

    state = JobState.load(tmp_path)
    state.record(target.name, "req-caducada", DATASET)
    state.save()

    backend = FakeBackend()  # no conoce 'req-caducada'

    failures = run_async(
        backend, DATASET, plan, force=False, output_dir=tmp_path,
        max_parallel=2, poll_seconds=0,
    )

    assert backend.failed_resumes == ["req-caducada"]
    assert backend.submitted == ["req-1"]
    assert failures == 0
    assert target.exists()


def test_run_async_failed_job_is_forgotten_and_counted(tmp_path):
    jobs = {"req-1": FakeJob("req-1", fail_on_ready=True)}
    backend = FakeBackend(jobs)
    plan = make_plan(tmp_path, 1)

    failures = run_async(
        backend, DATASET, plan, force=False, output_dir=tmp_path,
        max_parallel=2, poll_seconds=0,
    )

    assert failures == 1
    # Se olvida para que la próxima ejecución lo reenvíe desde cero.
    assert len(JobState.load(tmp_path)) == 0


def test_run_async_skips_existing_files(tmp_path):
    backend = FakeBackend()
    plan = make_plan(tmp_path, 2)
    plan[0][0].write_text("ya-descargado", encoding="utf-8")

    run_async(
        backend, DATASET, plan, force=False, output_dir=tmp_path,
        max_parallel=2, poll_seconds=0,
    )

    assert backend.submitted == ["req-1"]


def test_run_async_rejects_backend_without_support(tmp_path):
    backend = FakeBackend()
    backend.supports_async = False

    with pytest.raises(cds.UnsupportedOperation):
        run_async(
            backend, DATASET, make_plan(tmp_path, 1), force=False, output_dir=tmp_path,
            max_parallel=2, poll_seconds=0,
        )


# --- backends -----------------------------------------------------------------


def test_get_backend_rejects_unknown_name():
    with pytest.raises(ValueError):
        cds.get_backend("inventado")


def test_terms_error_is_recognised():
    assert cds.looks_like_terms_error("required licences not accepted")
    assert cds.looks_like_terms_error("Please accept the Terms of Use")
    assert not cds.looks_like_terms_error("connection reset by peer")
