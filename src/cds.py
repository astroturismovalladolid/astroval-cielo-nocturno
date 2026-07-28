"""Acceso al Climate Data Store, con dos backends intercambiables.

ECMWF distribuye hoy dos clientes para la misma API:

- `ecmwf-datastores-client`, el nuevo, con envío asíncrono de trabajos y
  consulta de metadatos. Está en estado *Incubating*: la interfaz es
  "mostly stable" pero ECMWF advierte de cambios incompatibles.
- `cdsapi`, el clásico, que sigue soportado. La propia documentación del
  CDS dice que no se pide migrar todavía.

Este módulo aísla esa elección para que `download.py` no dependa de un
paquete en incubación: si el nuevo cliente rompe algo, se pasa a
`--backend cdsapi` sin tocar la lógica de descarga.
"""

from __future__ import annotations

from typing import Any, Protocol

DATASTORES = "datastores"
CDSAPI = "cdsapi"
BACKENDS = (DATASTORES, CDSAPI)

# Mensaje del CDS cuando no se han aceptado los Términos de Uso del dataset.
# Es el error más frecuente en el primer intento, así que se detecta para
# poder dar una indicación accionable en vez del volcado crudo de la API.
TERMS_HINT_MARKERS = ("licence", "license", "terms", "required licences")


class UnsupportedOperation(RuntimeError):
    """La operación pedida no existe en el backend seleccionado."""


class Job(Protocol):
    """Trabajo ya enviado a la cola del CDS."""

    @property
    def request_id(self) -> str: ...

    def ready(self) -> bool: ...

    def download(self, target: str) -> str: ...


class DatastoresJob:
    """Envuelve un `ecmwf.datastores.Remote`."""

    def __init__(self, remote: Any) -> None:
        self._remote = remote

    @property
    def request_id(self) -> str:
        return str(self._remote.request_id)

    def ready(self) -> bool:
        """True si el resultado está listo. No bloquea.

        Propaga ProcessingFailedError si el trabajo ha fallado o ha sido
        rechazado, que es como el cliente señala ambos casos.
        """
        return bool(self._remote.results_ready)

    def download(self, target: str) -> str:
        return str(self._remote.download(target))


class DatastoresBackend:
    """Backend sobre `ecmwf-datastores-client` (soporta sync y async)."""

    name = DATASTORES
    supports_async = True

    def __init__(self) -> None:
        try:
            from ecmwf.datastores import Client
        except ImportError as exc:
            raise ImportError(
                "ecmwf-datastores-client no está instalado. Ejecuta "
                "'pip install -r requirements.txt', o usa --backend cdsapi."
            ) from exc
        self._client = Client()

    def check_authentication(self) -> dict:
        return dict(self._client.check_authentication())

    def retrieve(self, dataset: str, request: dict, target: str) -> str:
        return str(self._client.retrieve(dataset, request, target))

    def submit(self, dataset: str, request: dict) -> DatastoresJob:
        return DatastoresJob(self._client.submit(dataset, request))

    def get_job(self, request_id: str) -> DatastoresJob:
        return DatastoresJob(self._client.get_remote(request_id))

    def collection_window(self, dataset: str) -> tuple[Any, Any]:
        """(inicio, fin) de la cobertura temporal del dataset. Pueden ser None."""
        collection = self._client.get_collection(dataset)
        return collection.begin_datetime, collection.end_datetime

    def estimate_costs(self, dataset: str, request: dict) -> dict:
        """Coste estimado de la petición, tal y como lo devuelve la API."""
        return dict(self._client.get_collection(dataset).estimate_costs(request))

    def apply_constraints(self, dataset: str, request: dict) -> dict:
        """Recorta la petición a lo que el dataset ofrece realmente."""
        return dict(self._client.apply_constraints(dataset, request))

    def list_jobs(self, status: str | None = None) -> list[str]:
        """request_id de los trabajos del usuario, recorriendo las páginas."""
        jobs = self._client.get_jobs(sortby="-created", status=status)
        request_ids: list[str] = []
        while jobs is not None:
            request_ids.extend(jobs.request_ids)
            jobs = jobs.next
        return request_ids


class CdsapiBackend:
    """Backend sobre `cdsapi`, el cliente clásico. Solo modo síncrono."""

    name = CDSAPI
    supports_async = False

    def __init__(self) -> None:
        try:
            import cdsapi
        except ImportError as exc:
            raise ImportError(
                "cdsapi no está instalado. Ejecuta 'pip install \"cdsapi>=0.7.7\"', "
                "o usa el backend por defecto."
            ) from exc
        self._client = cdsapi.Client()

    def check_authentication(self) -> dict:
        raise UnsupportedOperation(
            "cdsapi no expone comprobación de credenciales; usa --backend datastores."
        )

    def retrieve(self, dataset: str, request: dict, target: str) -> str:
        self._client.retrieve(dataset, request, target)
        return target

    def submit(self, dataset: str, request: dict) -> Job:
        raise UnsupportedOperation(
            "El modo asíncrono requiere --backend datastores; cdsapi solo descarga "
            "de forma bloqueante."
        )

    def get_job(self, request_id: str) -> Job:
        raise UnsupportedOperation(
            "El modo asíncrono requiere --backend datastores; cdsapi solo descarga "
            "de forma bloqueante."
        )

    def collection_window(self, dataset: str) -> tuple[Any, Any]:
        raise UnsupportedOperation(
            "cdsapi no expone metadatos del catálogo; usa --backend datastores."
        )

    def estimate_costs(self, dataset: str, request: dict) -> dict:
        raise UnsupportedOperation(
            "cdsapi no expone estimación de coste; usa --backend datastores."
        )

    def apply_constraints(self, dataset: str, request: dict) -> dict:
        raise UnsupportedOperation(
            "cdsapi no expone las restricciones del dataset; usa --backend datastores."
        )

    def list_jobs(self, status: str | None = None) -> list[str]:
        raise UnsupportedOperation(
            "cdsapi no expone el listado de trabajos; usa --backend datastores."
        )


def get_backend(name: str = DATASTORES):
    """Instancia el backend pedido. Lanza ValueError si el nombre no existe."""
    if name == DATASTORES:
        return DatastoresBackend()
    if name == CDSAPI:
        return CdsapiBackend()
    raise ValueError(f"Backend desconocido: '{name}'. Disponibles: {', '.join(BACKENDS)}")


def interpret_costs(costs: dict) -> tuple[str, str]:
    """Traduce la estimación de coste a (veredicto, descripción).

    El veredicto es "exceeded" solo cuando se puede determinar con certeza
    que la petición supera el límite del CDS; si la forma de la respuesta
    no se reconoce se devuelve "unknown" y la descripción cruda, para no
    bloquear una descarga válida por no saber leer un formato nuevo.
    """
    if not isinstance(costs, dict) or not costs:
        return "unknown", str(costs)

    def pair(d: dict) -> tuple[float, float] | None:
        cost, limit = d.get("cost"), d.get("limit")
        if isinstance(cost, (int, float)) and isinstance(limit, (int, float)):
            return float(cost), float(limit)
        return None

    found = pair(costs)
    label = str(costs.get("id", "coste"))
    if found is None:
        for key, value in costs.items():
            if isinstance(value, dict) and (found := pair(value)) is not None:
                label = str(key)
                break

    if found is None:
        return "unknown", str(costs)

    cost, limit = found
    description = f"{label}: {cost:g} de un límite de {limit:g}"
    if limit > 0 and cost > limit:
        return "exceeded", description
    return "ok", description


def looks_like_terms_error(message: str) -> bool:
    """Heurística: ¿el fallo se debe a Términos de Uso sin aceptar?"""
    lowered = message.lower()
    return any(marker in lowered for marker in TERMS_HINT_MARKERS)


TERMS_HINT = (
    "Puede que no hayas aceptado los Términos de Uso del dataset. Se aceptan a "
    "mano, al final del formulario de descarga en la web del CDS; sin ese paso "
    "la API falla aunque las credenciales sean correctas."
)
