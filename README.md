# astroval-cielo-nocturno

Caracterización climatológica de emplazamientos para observación astronómica en Castilla y León, a partir del reanálisis ERA5 de Copernicus.

Proyecto de la **Asociación Vallisoletana de Astroturismo (Astroval)**.

---

## Objetivo

Estimar, para un conjunto de emplazamientos candidatos, el **porcentaje de noches astronómicamente aprovechables** y su distribución estacional, con una metodología homogénea que permita compararlos entre sí.

El objetivo operativo es doble:

1. Aportar un criterio objetivo para la selección de emplazamientos de observación y, eventualmente, de un observatorio o servicio de *hosting* de telescopios.
2. Generar un indicador reutilizable que acompañe a los informes de calidad de cielo dirigidos a los ayuntamientos de la provincia de Valladolid.

---

## Fuente de datos

**ERA5 — ECMWF Reanalysis v5**, distribuido por el Climate Data Store (CDS) de Copernicus.

| Característica | Valor |
| --- | --- |
| Dataset | `reanalysis-era5-single-levels` |
| Resolución espacial | 0,25° (≈ 31 km) |
| Resolución temporal | Horaria |
| Cobertura temporal | Desde 1940 |
| Formato de descarga | NetCDF4 |

### Variables

**Prioritarias (serie larga, ≥ 20 años):**

- `total_cloud_cover`
- `high_cloud_cover`, `medium_cloud_cover`, `low_cloud_cover`

**Complementarias (serie corta, ~10 años suficiente):**

- `2m_temperature`, `2m_dewpoint_temperature` — depresión del punto de rocío, condensación en óptica y niebla de radiación
- `10m_u_component_of_wind`, `10m_v_component_of_wind` — umbral de cierre por seguridad
- `total_column_water_vapour` — proxy de transparencia
- `snow_depth` — accesibilidad rodada en invierno

**Pendientes de incorporar:**

- Espesor óptico de aerosoles (AOD), desde el reanálisis CAMS en el Atmosphere Data Store
- Viento a 200 hPa (`reanalysis-era5-pressure-levels`) como proxy de *seeing*

### Ámbito geográfico

Recorte que cubre Castilla y León con margen:

```
North:  43.5
West:   -7.5
East:   -1.5
South:  39.75
```

### Ventana horaria

`18:00`–`05:00` UTC. Cubre la noche astronómica completa en esta latitud en cualquier época del año, y reduce a la mitad el volumen de descarga.

---

## Estructura del repositorio

```
astroval-cielo-nocturno/
├── config/
│   ├── sites.yaml             # emplazamientos y coordenadas
│   ├── thresholds.yaml        # umbrales de las métricas nocturnas
│   └── download.yaml          # dataset, variables, área y ventana horaria
├── src/
│   ├── config.py              # carga de configuración (sites/thresholds/download)
│   ├── cds.py                 # acceso al CDS, backend intercambiable
│   ├── jobs.py                # registro de trabajos encolados (modo async)
│   ├── download.py            # descarga por bloques de años (reanudable)
│   ├── twilight.py            # crepúsculos astronómicos por fecha y lugar
│   ├── analyze.py             # umbrales, agregación mensual y anual
│   └── report.py              # tablas y gráficos
├── data/
│   ├── raw/                   # .nc descargados — NO versionado
│   └── processed/             # CSV agregados — sí versionado
├── outputs/                   # tablas, figuras, informes
├── tests/                     # pruebas unitarias
└── notebooks/                 # exploración
```

---

## Requisitos

- Python 3.10 o superior
- Cuenta en el [Climate Data Store](https://cds.climate.copernicus.eu)

```bash
pip install -r requirements.txt
```

### Cliente del CDS

La descarga usa por defecto [`ecmwf-datastores-client`](https://github.com/ecmwf/ecmwf-datastores-client), el cliente Python de ECMWF para la API de Data Stores, que aporta envío asíncrono de trabajos y comprobación de credenciales.

ECMWF lo clasifica como **Incubating**: la interfaz es mayormente estable, pero recomiendan fijar una versión publicada y contar con cambios incompatibles. Por eso `requirements.txt` la fija a `>=0.5.1,<0.6`, y el CDS sigue soportando el cliente clásico `cdsapi` sin pedir migración.

Para no depender de un paquete en incubación, `src/cds.py` aísla esa elección: si el cliente nuevo diese problemas, basta con

```bash
python src/download.py --start 1996 --end 2025 --block 3 --backend cdsapi
```

El backend `cdsapi` solo admite el modo síncrono; el resto del pipeline es idéntico.

---

## Configuración

### 1. Credenciales

Crear `$HOME/.ecmwfdatastoresrc` (en Windows, `C:\Users\<usuario>\.ecmwfdatastoresrc`) con el token personal que muestra el CDS al estar logueado:

```
url: https://cds.climate.copernicus.eu/api
key: <TOKEN-PERSONAL>
```

Si ya tienes un `$HOME/.cdsapirc` de antes (mismo formato), puedes reutilizarlo apuntando la variable de entorno `ECMWF_DATASTORES_RC_FILE` a esa ruta en lugar de duplicar el fichero.

> **Este fichero nunca debe subirse al repositorio.** Vive fuera del proyecto, en el directorio de usuario.

### 2. Términos de uso

Antes de la primera descarga hay que **aceptar manualmente los Términos de Uso del dataset**, al final del formulario de descarga en la web del CDS. Sin ese paso la API falla aunque las credenciales sean correctas. Es la causa más frecuente de error en el primer intento.

Documentación oficial: https://cds.climate.copernicus.eu/how-to-api · https://ecmwf.github.io/ecmwf-datastores-client/

---

## Uso

```bash
# Comprobar credenciales antes de una descarga larga
python src/download.py --check-auth

# Descarga por bloques de años (comprueba si el fichero ya existe)
python src/download.py --start 1996 --end 2025 --block 3

# Serie completa: encola varios bloques a la vez (recomendado)
python src/download.py --start 1996 --end 2025 --block 3 --mode async

# Análisis para un emplazamiento
python src/analyze.py --site rello

# Comparativa de todos los emplazamientos
python src/analyze.py --all

# Generación de tablas y figuras
python src/report.py
```

### Modos de descarga

Las colas del CDS pueden ser de horas, y la serie 1996–2025 en bloques de 3 años son diez peticiones.

- **`--mode sync`** (por defecto) descarga un bloque cada vez: espera a que termine el primero antes de pedir el segundo. Diez colas, una detrás de otra.
- **`--mode async`** envía varios bloques de golpe —`--max-parallel`, 4 por defecto— y los descarga según van estando listos. El CDS los procesa en paralelo, así que las esperas se solapan en vez de sumarse.

En ambos modos, un bloque ya descargado se omite. En modo asíncrono se guardan además los `request_id` en `data/raw/.jobs.json`: si se interrumpe el proceso, al relanzarlo se retoman los trabajos que siguen en cola en lugar de reenviarlos al final de la fila.

`--check-auth` valida las credenciales contra la API sin descargar nada. Conviene usarlo antes de una serie larga: si los Términos de Uso del dataset no están aceptados, el script lo señala explícitamente en vez de dejar el error crudo de la API.

### Tests

```bash
pip install -r requirements-dev.txt
pytest
```

Cubren el cálculo de crepúsculos (`twilight.py`), la clasificación de noches (`analyze.py`), el registro de trabajos encolados (`jobs.py`) y ambos modos de descarga (`download.py`) con un backend simulado. No requieren credenciales del CDS ni datos descargados.

---

## Metodología

### Definición de noche

Intervalo entre el crepúsculo astronómico vespertino y el matutino (Sol a más de 18° bajo el horizonte), calculado para las coordenadas y la fecha de cada emplazamiento.

### Métricas

| Métrica | Definición |
| --- | --- |
| **Noche aprovechable** | ≥ 3 horas consecutivas con cobertura total < 40% |
| **Noche despejada** | Cobertura media nocturna < 20% |
| **Noche fotométrica** | Cobertura media < 10% y nube alta < 10% |
| **Noche perdida** | Ninguna hora con cobertura < 40% |
| **Riesgo de rocío** | Depresión del punto de rocío < 2 °C |
| **Cierre por viento** | Racha sostenida > 40 km/h |

Los umbrales son parámetros configurables, no constantes del código (ver `config/thresholds.yaml`).

### Nota sobre nubosidad por niveles

La cobertura total penaliza por igual una noche con cirros altos —a menudo utilizable— y una noche con estratos bajos —inservible—. El desglose por niveles corrige ese sesgo y, además, identifica directamente los episodios de inversión térmica en fondos de valle.

---

## Emplazamientos

Definidos en `config/sites.yaml`. Estado inicial:

| Emplazamiento | Provincia | Altitud | Coordenadas | Estado |
| --- | --- | --- | --- | --- |
| Rello / Atalaya del Tiñón | Soria | ~1.140 m | 41,367 N — 2,761 W | Candidato principal |
| Páramo de Boedo | Palencia | ~940 m | 42,578 N — 4,400 W | Alternativa |
| Cembrero (Villameriel) | Palencia | 918 m | — | Pendiente |
| Barcial de la Loma | Valladolid | 744 m | 41,954 N — 5,280 W | Referencia provincial |
| Urueña | Valladolid | ~840 m | — | Referencia provincial |
| Cerro de Cuchillejo | Valladolid | 932 m | — | Techo provincial |
| Sierra de Béjar–La Covatilla | Salamanca | ~2.000 m | — | Control (vertiente húmeda) |

Las coordenadas pendientes deben verificarse antes de ejecutar el análisis. La celda de rejilla asignada a cada emplazamiento se registra en la salida para trazabilidad.

---

## Limitaciones

Conviene tenerlas presentes al interpretar los resultados:

- **Resolución.** Una celda de 0,25° cubre unos 31 km. ERA5 no resuelve nieblas locales, efectos de valle ni contrastes topográficos de pequeña escala. Dos emplazamientos separados por 10 km comparten celda.
- **Es un reanálisis, no una observación.** Reproduce bien la climatología sinóptica; peor los fenómenos de capa límite, que son justamente los que arruinan noches en fondo de valle.
- **No estima el *seeing*.** La turbulencia óptica no se modela; los proxies disponibles son indirectos.
- **No sustituye a la caracterización in situ.** Cualquier decisión de inversión requiere al menos un año de monitorización real: fotómetro continuo, sensor de nubes, medida de *seeing* y registro de humedad.

Este repositorio sirve para **descartar y priorizar candidatos**, no para validar uno.

---

## Atribución

Contiene información modificada del Servicio de Cambio Climático de Copernicus (C3S), 2026. Ni la Comisión Europea ni el ECMWF se responsabilizan del uso que se haga de la información aquí contenida.

Cita del dataset:

> Hersbach, H. et al. (2023): ERA5 hourly data on single levels from 1940 to present. Copernicus Climate Change Service (C3S) Climate Data Store (CDS). DOI: 10.24381/cds.adbb2d47

---

## Hoja de ruta

- [x] Estructura del proyecto y scripts base (descarga, crepúsculos, análisis, informes)
- [ ] Descarga completa de la serie 1996–2025 para nubosidad
- [ ] Cálculo de crepúsculos y agregación mensual
- [ ] Comparativa entre emplazamientos candidatos
- [ ] Incorporación de variables complementarias
- [ ] AOD desde CAMS
- [ ] Mapa provincial de noches aprovechables para los informes municipales
- [ ] Validación cruzada con datos de estación de AEMET (Soria, Valladolid/Villanubla)

---

## Contacto

Asociación Vallisoletana de Astroturismo (Astroval)
C/ Almudena Grandes, 6 — 47320 Tudela de Duero (Valladolid)
astroturismovalladolid@gmail.com
