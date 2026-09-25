# gs-datos-mapa

Datos públicos para el mapa de propiedades del sitio de **GS Brokers** (`Mapa_Propiedades.tsx`, en Framer).
Todas las noches (03:00, hora del centro de México) un trabajo de GitHub Actions descarga las fuentes
oficiales y de OpenStreetMap, las limpia y publica archivos JSON ligeros en GitHub Pages. El mapa los lee
solo cuando los necesita: por zona y por capa.

**Base pública:** `https://gsbrokers-mx.github.io/gs-datos-mapa/v1/`
(CORS abierto, CDN y compresión gzip los pone GitHub Pages).

## Archivos

| Archivo | Contenido |
|---|---|
| `v1/indice.json` | Zonas, recuadro (`bbox` = [sur, oeste, norte, este]), capas, conteos por clave, fecha, estado y avisos. **Léelo primero.** |
| `v1/<zona>/salud.json` | `salud:hospitales`, `salud:clinicas`, `salud:dentales`, `salud:farmacias` |
| `v1/<zona>/educacion.json` | Escuelas (`escuelas:todo`) con `niveles` y `sostenimiento` |
| `v1/<zona>/bancos.json` | `bancos:bancos`, `bancos:gobierno` |
| `v1/<zona>/comercio.json` | `comercio:centros`, `comercio:super`, `comercio:hogar`, `comercio:oficina` |
| `v1/<zona>/otros.json` | `otros:estadios`, `otros:monumentos`, `otros:museos` |
| `v1/<zona>/parques.json` | `parques:todo` (solo parques grandes: 20 ha o más, o 5 ha con ficha en Wikidata; `hectareas` aproximadas; todos principales) |
| `v1/<zona>/referencias.json` | `referencias:empresa`, `:parque_industrial`, `:deporte`, `:eventos`, `:gimnasio` |
| `v1/<zona>/salones.json` | `referencias:salon` — salones de fiestas; `filtro: false` (sin casilla, puntos pequeños) |
| `v1/<zona>/tren.geojson` | Líneas (con color) y estaciones de tren ligero / metro |

Zonas actuales: `zmg` (Zona Metropolitana de Guadalajara, todas las capas) y `madrid` (solo `tren`).

### Formato de un archivo de puntos

```json
{
  "zona": "zmg", "capa": "salud", "generado": "2026-09-25T09:10:00Z",
  "fuentes": { "CLUES": "CLUES, Secretaría de Salud (DGIS)", "OpenStreetMap": "© Colaboradores de OpenStreetMap (ODbL)" },
  "puntos": [
    { "nombre": "HGZ 14 Guadalajara (IMSS)", "lat": 20.65712, "lng": -103.32381,
      "clave": "salud:hospitales", "principal": true, "fuente": "CLUES", "institucion": "IMSS" }
  ]
}
```

Campos de cada punto: `nombre`, `lat`, `lng`, `clave` (`categoria:subcategoria`), `principal` (anillo dorado)
y `fuente`. Campos opcionales: `institucion` (salud), `marca` (cadenas), `niveles` y `sostenimiento`
(escuelas), `tipo` (referencias), `filtro: false` (no aparece en los filtros), `wikidata`.

**Escuelas.** Un punto por plantel. `niveles` ∈ `preescolar`, `primaria`, `secundaria`, `preparatoria`,
`universidad`, `otras` (especial, inicial) y `varios` (combina niveles y no se pudo saber cuáles).
El mapa lo muestra en `educacion:<nivel>` si el nivel está en la lista. `sostenimiento` es `publico` o `privado`.

**tren.geojson.** `FeatureCollection` con dos tipos de `properties.tipo`:
`linea` (`linea`, `nombre`, `color`, geometría `MultiLineString`) y
`estacion` (`nombre`, `lineas`, `clave: "transporte:tren"`, geometría `Point`).

## Reglas de clasificación

- **Principales** (anillo dorado):
  - *Salud*: hospitales institucionales; hospitales privados que se llaman hospital, centro médico o
    sanatorio; unidades institucionales grandes (UMF del IMSS, clínicas del ISSSTE, Cruz Roja, Cruz Verde,
    clínicas de especialidades). Centros de salud, consultorios y clínicas chicas no.
  - *Escuelas*: planteles de nivel superior con nombre de universidad, tecnológico o normal; colegios
    particulares con 3 niveles o más; escuelas reconocidas (`config/escuelas_principales.json` o ficha en
    Wikidata en OSM); correcciones a mano. Una escuela pública de un solo nivel (básica o media) nunca.
  - *Otros*: grandes cadenas por marca (formatos "Express" no), parques grandes, monumentos y plazas con
    ficha en Wikidata, estadios, presidencias municipales y centros de trámites clave (CISZ, SAT,
    recaudadoras, unidades administrativas…).
- **Cadenas**: por nombre o marca (`config/marcas.json`), nunca por la etiqueta `shop` de OSM.
- **Gobierno**: `government=*`, `amenity=townhall` u `office=government` **con nombre de dependencia
  pública** (en OSM hay edificios de empresas mal etiquetados como gobierno). Sin centros comunitarios,
  clubes, tiendas, marcas ni nombres de la lista `gobierno_prohibidas` (`config/validacion.json`).
- **Referencias**: del DENUE, empresas con 251 o más empleados (sin comercio al por menor, escuelas, salud
  ni gobierno, que ya tienen su capa), parques industriales, instalaciones deportivas, gimnasios y
  recintos de eventos de cualquier tamaño; de OSM, canchas y clubes con nombre y kartódromos.
- **Nombres genéricos**: si el nombre es solo la categoría ("Hospital", "Unidad Deportiva 51"), se completa
  con otro campo de la fuente (nombre de la unidad en CLUES, colonia en el DENUE, `official_name` u
  `operator` en OSM). Si no hay con qué, el punto se descarta.
- **Zonas militares**: los puntos dentro de `landuse=military` se quitan antes de publicar.
- **Coordenadas**: solo de fuentes abiertas (CLUES, DENUE, SEP, OSM) o de `config/*_manual.json`
  (fuente `Manual`). Nunca de Google Maps.
- Si una fuente falla, se vuelve a publicar la versión anterior de esa capa y se marca en `indice.json`
  (`estado: "anterior"`). Además se abre un *issue* con el aviso.

## Validación

Antes de publicar, cada capa se revisa con `config/validacion.json`. Si algo no cumple, **la corrida
falla, no se publica nada y sigue en línea la versión anterior**; se abre un *issue* con el detalle.

- Porcentaje de principales por capa por encima del tope (salud 30 %, escuelas 10 %, bancos y gobierno
  15 %, referencias 5 %, salones 0 %; comercio 60 %, otros 75 % y parques 100 % porque ya son solo
  lugares clave).
- Cualquier nombre genérico o vacío.
- Un punto de gobierno con marca comercial o con un nombre de la lista `gobierno_prohibidas`.

## Fuentes y créditos

- **INEGI, DENUE** — descarga masiva por entidad.
- **Secretaría de Salud, CLUES** (DGIS) — catálogo de establecimientos de salud.
- **SEP, Catálogo de Centros de Trabajo** — niveles exactos por plantel (se actualiza a mano una vez al año).
- **OpenStreetMap** vía Overpass — © colaboradores de OpenStreetMap, datos bajo licencia **ODbL**.
  Los archivos derivados de OSM se publican bajo la misma licencia.

## Cómo ampliar

- **Otra cadena**: agrega un bloque en `config/marcas.json` (patrón sobre el nombre en MAYÚSCULAS sin acentos).
- **Corregir una escuela**: agrega una entrada en `config/escuelas_manual.json`.
- **Marcar una escuela como reconocida** (anillo dorado): agrega un patrón en `config/escuelas_principales.json`.
- **Agregar un lugar que OSM no trae** (p. ej. Bosque Los Colomos): `config/lugares_manual.json`.
- **Otra ciudad**: agrega una zona en `config/zonas.json` (entidad, municipios con su clave INEGI, `bbox`
  y capas). El DENUE y CLUES se filtran por entidad y municipio; OSM, por los polígonos municipales.

Cualquier cambio en `config/` o `scripts/` que se suba a `main` dispara una corrida.

## Actualizar la SEP (una vez al año)

No hace falta programar. Si no se actualiza, el mapa sigue funcionando: las escuelas salen del DENUE y la
SEP solo afina los niveles. Después de 13 meses el trabajo abre un *issue* recordándolo.

1. En el navegador, abre <https://www.datos.gob.mx/dataset/catalogo_centros_trabajo_sep> y descarga el CSV
   más reciente del catálogo (`CNCT_DA_…csv`, ~170 MB).
2. Comprímelo en ZIP (clic derecho → *Comprimir en archivo ZIP*). Queda en unos 17 MB.
3. En GitHub, entra a este repositorio → carpeta `fuentes/sep/entrada` → *Add file* → *Upload files*,
   suelta el ZIP y pulsa *Commit changes*.
4. Listo: el trabajo lo procesa, guarda `fuentes/sep/escuelas.csv.gz` y borra el ZIP.

## Correr en local

```bash
pip install -r requirements.txt
python scripts/construir.py                        # todas las zonas
python scripts/construir.py --zonas zmg --capas salud,educacion
```

Los archivos quedan en `publicar/` y las descargas en `.cache/` (se reutilizan durante 20 h).

## Mantenimiento

GitHub apaga los trabajos programados de repositorios sin actividad en 60 días; el trabajo hace un
commit mensual (`estado/ultima_corrida.json`) para evitarlo. Para correrlo a mano:
*Actions* → *Datos del mapa (nocturno)* → *Run workflow*.
