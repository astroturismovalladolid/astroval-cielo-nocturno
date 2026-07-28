"""Registro persistente de los trabajos enviados a la cola del CDS.

En modo asíncrono los bloques se envían todos de golpe y luego se espera.
Como las colas del CDS pueden ser de horas, es probable que el proceso se
interrumpa antes de que terminen. Guardar el `request_id` de cada bloque
permite retomar la espera de un trabajo que ya está encolado en vez de
volver a enviarlo y perder el turno.

El fichero vive en `data/raw/.jobs.json`, que ya está fuera del control de
versiones junto al resto de datos crudos.
"""

from __future__ import annotations

import datetime as dt
import json
import os
from pathlib import Path

STATE_FILENAME = ".jobs.json"


class JobState:
    """Mapa persistente: fichero de destino -> trabajo encolado."""

    def __init__(self, path: Path) -> None:
        self.path = path
        self._jobs: dict[str, dict] = {}

    @classmethod
    def load(cls, directory: Path) -> "JobState":
        state = cls(directory / STATE_FILENAME)
        if state.path.exists():
            try:
                with open(state.path, encoding="utf-8") as f:
                    state._jobs = json.load(f)
            except (json.JSONDecodeError, OSError):
                # Un estado corrupto no debe impedir descargar: se descarta
                # y los bloques se vuelven a enviar.
                state._jobs = {}
        return state

    def save(self) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        # Escritura atómica: un Ctrl-C a mitad no puede dejar el fichero
        # a medias, que es justo el escenario que este registro existe para
        # sobrevivir.
        tmp = self.path.with_suffix(".tmp")
        with open(tmp, "w", encoding="utf-8") as f:
            json.dump(self._jobs, f, indent=2, sort_keys=True)
        os.replace(tmp, self.path)

    def get(self, target_name: str) -> str | None:
        """request_id del trabajo encolado para ese destino, si lo hay."""
        entry = self._jobs.get(target_name)
        return entry["request_id"] if entry else None

    def record(self, target_name: str, request_id: str, dataset: str) -> None:
        self._jobs[target_name] = {
            "request_id": request_id,
            "dataset": dataset,
            "submitted_at": dt.datetime.now(dt.timezone.utc).isoformat(),
        }

    def forget(self, target_name: str) -> None:
        self._jobs.pop(target_name, None)

    def pending(self) -> list[str]:
        return sorted(self._jobs)

    def __len__(self) -> int:
        return len(self._jobs)
