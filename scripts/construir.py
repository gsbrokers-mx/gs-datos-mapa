"""Construye todos los archivos publicables.

    python scripts/construir.py                      # todas las zonas y capas
    python scripts/construir.py --zonas zmg --capas salud,educacion
    python scripts/construir.py --base-url https://gsbrokers-mx.github.io/gs-datos-mapa   # respaldo si falla una fuente

Si una capa falla, se conserva la versión publicada anterior (descargada de --base-url) y se marca en indice.json.
"""
import argparse
import collections
import datetime as dt
import json
import re
import sys
import traceback
from pathlib import Path

import requests

import clues
import denue
import osm
import sep
import transporte
from comun import RAIZ, UA, config, deduplicar, distancia, es_generico, escribir_json, log, norm, parecido, punto

VERSION = "v1"
DESCRIPCION = {
    "salud": "Hospitales, clínicas, dentales y farmacias de cadena",
    "educacion": "Escuelas con niveles y sostenimiento",
    "bancos": "Bancos y oficinas de gobierno",
    "comercio": "Centros comerciales, supermercados, hogar y oficina (por marca)",
    "otros": "Estadios, museos y monumentos",
    "parques": "Parques y sitios con ficha en Wikidata",
    "referencias": "Empresas grandes, parques industriales, deporte, gimnasios y recintos de eventos",
    "salones": "Salones de fiestas (sin casilla en filtros, puntos pequeños)",
    "tren": "Líneas y estaciones de tren ligero / metro",
}
FUENTES = {
    "CLUES": "CLUES, Secretaría de Salud (DGIS)",
    "SEP": "Catálogo de Centros de Trabajo, SEP",
    "INEGI DENUE": "Directorio Estadístico Nacional de Unidades Económicas, INEGI",
    "OpenStreetMap": "© Colaboradores de OpenStreetMap (ODbL)",
    "Manual": "Corrección manual de GS Brokers",
}


class Zona:
    """Carga perezosa de las fuentes que comparten varias capas."""

    def __init__(self, clave, cfg):
        self.clave, self.cfg = clave, cfg
        self._osm = self._mil = self._refs = None

    def osm(self):
        if self._osm is None:
            self._osm = osm.clasificar(self.cfg, self.clave)
        return self._osm

    def militares(self):
        if self._mil is None:
            try:
                self._mil = osm.militares(self.cfg, self.clave)
            except Exception as e:  # noqa: BLE001
                log(f"   AVISO: sin polígonos militares ({e}); no se filtra")
                self._mil = osm.Poligonos([])
        return self._mil

    def refs(self):
        if self._refs is None:
            self._refs = denue.referencias(self.cfg)
        return self._refs

    def reconocidas(self):
        try:
            return osm.escuelas_reconocidas(self.cfg, self.clave)
        except Exception as e:  # noqa: BLE001
            log(f"   AVISO: sin escuelas con ficha en Wikidata ({e})")
            return []

    def de_osm(self, prefijos):
        return [p for p in self.osm() if p["clave"].split(":")[0] in prefijos]


def capa_salud(z):
    oficiales = clues.salud(z.cfg)
    extra = []
    for p in z.de_osm({"salud"}):
        if p["clave"] == "salud:hospitales" and any(
                distancia(p["lat"], p["lng"], q["lat"], q["lng"]) < 150 for q in oficiales):
            continue  # el hospital ya viene en CLUES
        extra.append(p)
    return oficiales + extra


CAPAS = {
    "salud": capa_salud,
    "educacion": lambda z: sep.escuelas(z.cfg, reconocidas=z.reconocidas()),
    "bancos": lambda z: z.de_osm({"bancos"}),
    "comercio": lambda z: z.de_osm({"comercio"}),
    "otros": lambda z: z.de_osm({"otros"}),
    "parques": lambda z: z.de_osm({"parques"}),
    # DENUE primero; lo de OSM (canchas, clubes, kartódromos) entra si no repite un lugar cercano con nombre parecido
    "referencias": lambda z: deduplicar(
        z.refs()[0] + [p for p in z.osm() if p["clave"].startswith("referencias:")], metros=150,
        similares=lambda p, q: parecido(p["nombre"], q["nombre"]) >= 0.5),
    "salones": lambda z: z.refs()[1],
}


CATEGORIAS_CAPA = {"educacion": {"escuelas"}, "referencias": {"referencias"}, "salones": {"referencias"}}


def aplicar_manual(zona, capa, puntos):
    """Agrega los lugares de config/lugares_manual.json que tocan a esta zona y capa."""
    for m in config("lugares_manual.json").get(zona, []):
        if m["clave"].split(":")[0] not in CATEGORIAS_CAPA.get(capa, {capa}):
            continue
        if (capa == "salones") != (m["clave"] == "referencias:salon"):
            continue
        if m.get("reemplaza"):
            rx = re.compile(m["reemplaza"])
            puntos = [p for p in puntos if not (p["clave"] == m["clave"] and rx.search(norm(p["nombre"])))]
        extra = {k: v for k, v in m.items() if k not in ("nombre", "lat", "lng", "clave", "principal", "reemplaza")}
        puntos.append(punto(m["nombre"], m["lat"], m["lng"], m["clave"], m.get("principal", False), "Manual", **extra))
    return puntos


def validar(capa, puntos):
    """Errores que impiden publicar: exceso de principales, nombres genéricos, gobierno con marca comercial."""
    reglas = config("validacion.json")
    errores = []
    tope = reglas["max_principales_pct"].get(capa)
    if tope is not None and puntos:
        pct = 100 * sum(p["principal"] for p in puntos) / len(puntos)
        if pct > tope:
            errores.append(f"{capa}: {pct:.1f}% de principales (máximo {tope}%)")
    genericos = sorted({p["nombre"] or "(vacío)" for p in puntos if es_generico(p["nombre"])})
    if genericos:
        errores.append(f"{capa}: {len(genericos)} nombres genéricos o vacíos: {', '.join(genericos[:10])}")
    marcas = [re.compile(x) for m in config("marcas.json")["marcas"] for x in m["patrones"]]
    prohibidas = [re.compile(x) for x in reglas["gobierno_prohibidas"]]
    malos = sorted({p["nombre"] for p in puntos if p["clave"] == "bancos:gobierno"
                    and (p.get("marca") or any(r.search(norm(p["nombre"])) for r in marcas + prohibidas))})
    if malos:
        errores.append(f"{capa}: gobierno con marca comercial: {', '.join(malos[:10])}")
    return errores


def anterior(base_url, ruta_rel):
    if not base_url:
        return None
    try:
        r = requests.get(f"{base_url.rstrip('/')}/{ruta_rel}", headers=UA, timeout=60)
        if r.ok:
            return r.json()
    except Exception:  # noqa: BLE001
        pass
    return None


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--zonas", default="")
    ap.add_argument("--capas", default="")
    ap.add_argument("--salida", default=str(RAIZ / "publicar"))
    ap.add_argument("--base-url", default="")
    args = ap.parse_args()

    salida = Path(args.salida)
    zonas_cfg = config("zonas.json")
    elegidas = [z for z in (args.zonas.split(",") if args.zonas else zonas_cfg)]
    ahora = dt.datetime.now(dt.timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")
    indice = {"version": VERSION, "generado": ahora, "fuentes": FUENTES, "zonas": {}}
    avisos = []
    errores_validacion = []

    for clave in elegidas:
        cfg = zonas_cfg[clave]
        z = Zona(clave, cfg)
        info_zona = {"nombre": cfg["nombre"], "bbox": cfg["bbox"], "capas": {}}
        capas = [c for c in cfg["capas"] if not args.capas or c in args.capas.split(",")]
        for capa in capas:
            ext = "geojson" if capa == "tren" else "json"
            ruta_rel = f"{VERSION}/{clave}/{capa}.{ext}"
            log(f"== {clave}/{capa}")
            estado, error = "ok", None
            try:
                if capa == "tren":
                    datos = transporte.tren(cfg, clave)
                    datos.update(zona=clave, generado=ahora)
                    conteo = collections.Counter(f["properties"]["tipo"] for f in datos["features"])
                    fuentes_capa = ["OpenStreetMap"]
                else:
                    puntos = aplicar_manual(clave, capa, CAPAS[capa](z))
                    mil = z.militares()
                    antes = len(puntos)
                    puntos = [p for p in puntos if not mil.contiene(p["lat"], p["lng"])]
                    if antes != len(puntos):
                        log(f"   {antes - len(puntos)} puntos dentro de zonas militares, ocultos")
                    puntos.sort(key=lambda p: (p["clave"], not p["principal"], p["nombre"]))
                    conteo = collections.Counter(p["clave"] for p in puntos)
                    errores = validar(capa, puntos)
                    for err in errores:
                        log(f"   VALIDACIÓN: {err}")
                    errores_validacion += [f"{clave}/{err}" for err in errores]
                    fuentes_capa = sorted({f for p in puntos for f in p["fuente"].split(" + ")})
                    datos = {"zona": clave, "capa": capa, "generado": ahora,
                             "fuentes": {f: FUENTES[f] for f in fuentes_capa}, "puntos": puntos}
                if not sum(conteo.values()):
                    raise RuntimeError("la capa salió vacía")
            except Exception as e:  # noqa: BLE001
                traceback.print_exc()
                error = f"{type(e).__name__}: {e}"
                datos = anterior(args.base_url, ruta_rel)
                estado = "anterior" if datos else "sin_datos"
                avisos.append(f"{clave}/{capa}: {error} -> se publicó {'la versión anterior' if datos else 'nada'}")
                if not datos:
                    continue
                conteo = collections.Counter(
                    (f["properties"]["tipo"] for f in datos.get("features", [])) if capa == "tren"
                    else (p["clave"] for p in datos.get("puntos", [])))
                fuentes_capa = list(datos.get("fuentes", {"OpenStreetMap": ""}))
            peso = escribir_json(salida / ruta_rel, datos)
            info_zona["capas"][capa] = {
                "archivo": ruta_rel, "descripcion": DESCRIPCION[capa], "estado": estado,
                "generado": datos.get("generado", ahora), "bytes": peso,
                "conteo": dict(sorted(conteo.items())), "fuentes": fuentes_capa,
                **({"error": error} if error else {}),
            }
            for k, v in sorted(conteo.items()):
                log(f"   {v:6}  {k}")
            log(f"   -> {ruta_rel} ({peso / 1024:.0f} KB) [{estado}]")
        indice["zonas"][clave] = info_zona

    info_sep = sep.meta()
    if info_sep:
        indice["sep"] = info_sep
        meses = (dt.date.today() - dt.date.fromisoformat(info_sep["fecha"])).days / 30.4
        if meses > 13:
            avisos.append(f"El catálogo de la SEP es del {info_sep['fecha']} ({meses:.0f} meses). "
                          "Toca actualizarlo: ver README, sección 'Actualizar la SEP'.")
    avisos += [f"VALIDACIÓN {e}" for e in errores_validacion]
    indice["avisos"] = avisos
    escribir_json(salida / VERSION / "indice.json", indice)
    (salida / ".nojekyll").write_text("", encoding="utf-8")
    (salida / "index.html").write_text(
        '<!doctype html><meta charset="utf-8"><title>gs-datos-mapa</title>'
        f'<p>Datos del mapa de GS Brokers. Índice: <a href="{VERSION}/indice.json">{VERSION}/indice.json</a></p>',
        encoding="utf-8")
    Path(RAIZ / "avisos.txt").write_text("\n".join(avisos), encoding="utf-8")
    if avisos:
        log("\nAVISOS:\n- " + "\n- ".join(avisos))
    if errores_validacion:
        log("\nLa validación falló: no se publica (sigue en línea la versión anterior).")
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
