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


def get_backend(name: str = DATASTORES):
    """Instancia el backend pedido. Lanza ValueError si el nombre no existe."""
    if name == DATASTORES:
        return DatastoresBackend()
    if name == CDSAPI:
        return CdsapiBackend()
    raise ValueError(f"Backend desconocido: '{name}'. Disponibles: {', '.join(BACKENDS)}")


def looks_like_terms_error(message: str) -> bool:
    """Heurística: ¿el fallo se debe a Términos de Uso sin aceptar?"""
    lowered = message.lower()
    return any(marker in lowered for marker in TERMS_HINT_MARKERS)


TERMS_HINT = (
    "Puede que no hayas aceptado los Términos de Uso del dataset. Se aceptan a "
    "mano, al final del formulario de descarga en la web del CDS; sin ese paso "
    "la API falla aunque las credenciales sean correctas."
)
