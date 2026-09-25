"""Escuelas: DENUE (base, se actualiza sola) + catálogo SEP (niveles exactos) + correcciones a mano.

Uso aparte, una vez al año:  python scripts/sep.py preparar <archivo.csv|.zip>
Convierte el catálogo de la SEP (CNCT, ~170 MB) en fuentes/sep/escuelas.csv.gz (solo escuelas, columnas útiles).
"""
import csv
import datetime as dt
import gzip
import io
import json
import re
import statistics
import sys
import zipfile
from pathlib import Path

import denue
from comun import RAIZ, arreglar, completar, config, distancia, log, norm, parecido, punto

SEP_GZ = RAIZ / "fuentes" / "sep" / "escuelas.csv.gz"
SEP_META = RAIZ / "fuentes" / "sep" / "escuelas.json"
VIGENCIA_MESES = 18  # después de esto, la SEP solo completa niveles y ya no agrega escuelas
COLUMNAS = ["cct", "nombre", "entidad", "municipio", "lat", "lng", "control", "nivel1", "nivel2", "vialidad", "numero"]


# ---------- preparación anual ----------

def preparar(origen):
    origen = Path(origen)
    if origen.suffix.lower() == ".zip":
        z = zipfile.ZipFile(origen)
        interno = next(n for n in z.namelist() if n.lower().endswith(".csv"))
        crudo, nombre_original = z.open(interno), Path(interno).name
    else:
        crudo, nombre_original = open(origen, "rb"), origen.name
    lector = csv.DictReader(io.TextIOWrapper(crudo, encoding="latin-1", newline=""))
    n = 0
    SEP_GZ.parent.mkdir(parents=True, exist_ok=True)
    with gzip.open(SEP_GZ, "wt", encoding="utf-8", newline="") as sal:
        w = csv.writer(sal)
        w.writerow(COLUMNAS)
        for r in lector:
            if r["C_TIPO"].strip() != "ESCUELA":
                continue
            w.writerow([
                r["CV_CCT"], arreglar(r["C_NOMBRE"]), arreglar(r["INMUEBLE_C_NOM_ENT"]),
                arreglar(r["INMUEBLE_C_NOM_MUN"]), r["INMUEBLE_LATITUD"], r["INMUEBLE_LONGITUD"],
                arreglar(r["SOSTENIMIENTO_C_CONTROL"]), arreglar(r["TIPONIVELSUB_C_SERVICION1"]),
                arreglar(r["TIPONIVELSUB_C_SERVICION2"]), arreglar(r["INMUEBLE_C_VIALIDAD_PRINCIPAL"]),
                r["INMUEBLE_N_EXTNUM"],
            ])
            n += 1
    m = re.search(r"(\d{2})(\d{2})(\d{4})", nombre_original)
    fecha = f"{m.group(3)}-{m.group(2)}-{m.group(1)}" if m else dt.date.today().isoformat()
    SEP_META.write_text(json.dumps({"archivo_original": nombre_original, "fecha": fecha, "escuelas": n},
                                   ensure_ascii=False, indent=2), encoding="utf-8")
    log(f"SEP preparada: {n} escuelas, fecha {fecha}, {SEP_GZ.stat().st_size / 1e6:.1f} MB")


# ---------- lectura ----------

def _nivel(n1, n2):
    n1, n2 = norm(n1), norm(n2)
    if n2 in ("PREESCOLAR", "PRIMARIA", "SECUNDARIA"):
        return n2.lower()
    if n1 == "MEDIA SUPERIOR":
        return "preparatoria"
    if n1 == "SUPERIOR":
        return "universidad"
    if n1 in ("ESPECIAL", "INICIAL"):
        return "otras"
    return None  # capacitación para el trabajo y otros: fuera


def meta():
    if SEP_META.exists():
        return json.loads(SEP_META.read_text(encoding="utf-8"))
    return None


def planteles(zona):
    """Planteles SEP de la zona, agrupados por dirección (una escuela puede tener varias claves)."""
    if not SEP_GZ.exists():
        return []
    municipios = {norm(m) for m in zona["municipios"].values()}
    grupos = {}
    with gzip.open(SEP_GZ, "rt", encoding="utf-8", newline="") as f:
        for r in csv.DictReader(f):
            if norm(r["municipio"]) not in municipios:
                continue
            nivel = _nivel(r["nivel1"], r["nivel2"])
            try:
                lat, lng = float(r["lat"]), float(r["lng"])
            except ValueError:
                continue
            if not nivel or not lat:
                continue
            llave = (norm(r["municipio"]), norm(r["vialidad"]), norm(r["numero"]))
            if not llave[1] or llave[2] in ("", "0", "SN", "S N"):
                llave = (round(lat, 4), round(lng, 4))
            g = grupos.setdefault(llave, {"nombres": [], "lats": [], "lngs": [], "niveles": set(), "control": set()})
            g["nombres"].append(r["nombre"])
            g["lats"].append(lat)
            g["lngs"].append(lng)
            g["niveles"].add(nivel)
            g["control"].add("privado" if norm(r["control"]) == "PRIVADO" else "publico")
    salida = []
    for g in grupos.values():
        salida.append({
            "nombre": max(set(g["nombres"]), key=g["nombres"].count),
            "nombres": set(g["nombres"]),
            "lat": statistics.median(g["lats"]), "lng": statistics.median(g["lngs"]),
            "niveles": g["niveles"],
            "sostenimiento": "privado" if "privado" in g["control"] else "publico",
            "fuente": "SEP",
        })
    return salida


# ---------- combinación ----------

INFERIR = [
    (r"\b(KINDER|KINDERGARTEN|PREESCOLAR|JARDIN DE NINOS|MATERNAL)\b", "preescolar"),
    (r"\bPRIMARIA\b", "primaria"),
    (r"\bSECUNDARIA\b", "secundaria"),
    (r"\b(PREPARATORIA|PREPA|BACHILLERATO|HIGH SCHOOL)\b", "preparatoria"),
    (r"\b(UNIVERSIDAD|UNIVERSITY|UNIVERSITARIO|LICENCIATURA)\b", "universidad"),
]


def _cercanos(indice, lat, lng, metros):
    celda = 0.005
    cx, cy = int(lat / celda), int(lng / celda)
    alcance = int(metros / 555) + 1
    for dx in range(-alcance, alcance + 1):
        for dy in range(-alcance, alcance + 1):
            for e in indice.get((cx + dx, cy + dy), []):
                d = distancia(lat, lng, e["lat"], e["lng"])
                if d <= metros:
                    yield d, e


def _indexar(lista):
    indice = {}
    for e in lista:
        indice.setdefault((int(e["lat"] / 0.005), int(e["lng"] / 0.005)), []).append(e)
    return indice


# Principales (anillo dorado): planteles de nivel superior con nombre de universidad, tecnológico o normal,
# colegios particulares con 3 niveles o más, escuelas reconocidas (lista curada o ficha en Wikidata)
# y correcciones a mano. Una escuela pública de educación básica o media de un solo nivel nunca es principal.
UNIVERSIDAD = re.compile(r"UNIVERSIDAD|UNIVERSITARI|TECNOLOGICO|POLITECNIC|NORMAL|ITESO|ESCUELA SUPERIOR"
                         r"|CENTRO DE ENSENANZA TECNICA|\bCETI\b|CENTRO UNIVERSITARIO")


def _reconocida(e, patrones, reconocidas):
    nombres = [e["nombre"], *e.get("nombres", [])]
    if any(p.search(norm(n)) for p in patrones for n in nombres):
        return True
    for lat, lng, nombre in reconocidas:
        d = distancia(e["lat"], e["lng"], lat, lng)
        if d < 60 or (d < 250 and max(parecido(nombre, n) for n in nombres) >= 0.34):
            return True
    return False


def es_principal(e, niveles, patrones, reconocidas):
    reales = [n for n in niveles if n not in ("otras", "varios")]
    if e["sostenimiento"] == "publico" and len(reales) <= 1 and "universidad" not in reales:
        return False
    if "universidad" in niveles and any(UNIVERSIDAD.search(norm(n)) for n in [e["nombre"], *e.get("nombres", [])]):
        return True
    if e["sostenimiento"] == "privado" and len(reales) >= 3:
        return True
    return _reconocida(e, patrones, reconocidas)


def escuelas(zona, reconocidas=()):
    base = denue.escuelas(zona)
    # 1) juntar registros repetidos del DENUE (mismo colegio en varios edificios o niveles)
    indice = {}
    unidas = []
    for e in base:
        mejor = None
        for d, o in _cercanos(indice, e["lat"], e["lng"], 200):
            if parecido(e["nombre"], o["nombre"]) >= 0.5 and o["sostenimiento"] == e["sostenimiento"]:
                mejor = o
                break
        if mejor:
            mejor["niveles"] |= e["niveles"]
        else:
            unidas.append(e)
            indice.setdefault((int(e["lat"] / 0.005), int(e["lng"] / 0.005)), []).append(e)
    log(f"   DENUE: {len(base)} registros -> {len(unidas)} escuelas")

    # 2) completar niveles con la SEP
    info = meta()
    sep = planteles(zona)
    vigente = False
    if info:
        edad = (dt.date.today() - dt.date.fromisoformat(info["fecha"])).days / 30.4
        vigente = edad <= VIGENCIA_MESES
        log(f"   SEP: {len(sep)} planteles (archivo del {info['fecha']}, {edad:.0f} meses, "
            f"{'vigente' if vigente else 'solo completa niveles'})")
    indice = _indexar(unidas)
    solo_sep = 0
    for s in sep:
        candidato, mejor_puntaje = None, 0
        for d, o in _cercanos(indice, s["lat"], s["lng"], 300):
            sim = max(parecido(n, o["nombre"]) for n in s["nombres"])
            puntaje = sim + (0.4 if d < 60 else 0) - d / 1000
            if (sim >= 0.34 or d < 60) and puntaje > mejor_puntaje:
                candidato, mejor_puntaje = o, puntaje
        if candidato:
            if "varios" in candidato["niveles"]:
                candidato["niveles"].discard("varios")
            candidato["niveles"] |= s["niveles"]
            candidato["sep"] = True
        elif vigente:
            nombre = completar(s["nombre"], *sorted(s["nombres"]))
            if not nombre:
                continue  # solo "Jardín de Niños", "Primaria"…: sin nombre útil
            unidas.append(dict(s, nombre=nombre, sep=True))
            indice.setdefault((int(s["lat"] / 0.005), int(s["lng"] / 0.005)), []).append(unidas[-1])
            solo_sep += 1
    log(f"   SEP: {solo_sep} escuelas que solo están en la SEP se agregaron")

    # 3) inferir niveles por nombre cuando siguen como "varios"
    for e in unidas:
        if "varios" in e["niveles"]:
            inferidos = {nivel for patron, nivel in INFERIR if re.search(patron, norm(e["nombre"]))}
            if len(inferidos) >= 2:
                e["niveles"] = inferidos

    # 4) correcciones a mano
    for m in config("escuelas_manual.json")["escuelas"]:
        alias = [norm(a) for a in m.get("alias", [])] + [norm(m["nombre"])]
        cerca = sorted(_cercanos(_indexar(unidas), m["lat"], m["lng"], m.get("radio", 300)), key=lambda x: x[0])
        # registros del mismo campus: los que coinciden con algún alias
        mismos = [o for d, o in cerca if any(re.search(rf"\b{re.escape(a)}\b", norm(n)) for a in alias
                                              for n in [o["nombre"], *o.get("nombres", [])])]
        if mismos:
            objetivo = mismos[0]
            for o in mismos[1:]:
                objetivo["niveles"] |= o["niveles"]
                o["borrar"] = True
        else:
            objetivo = {"niveles": set()}
            unidas.append(objetivo)
        objetivo.update(nombre=m["nombre"], niveles=set(m["niveles"]), sostenimiento=m["sostenimiento"],
                        manual=True, principal=m.get("principal"), lat=m["lat"], lng=m["lng"],
                        fuente="Manual")

    # 5) salida
    orden = ["preescolar", "primaria", "secundaria", "preparatoria", "universidad", "otras", "varios"]
    patrones = [re.compile(p) for p in config("escuelas_principales.json")["patrones"]]
    puntos = []
    for e in unidas:
        if e.get("borrar"):
            continue
        niveles = [n for n in orden if n in e["niveles"]]
        reales = [n for n in niveles if n not in ("otras", "varios")]
        principal = e.get("principal")
        if principal is None:
            principal = es_principal(e, niveles, patrones, reconocidas)
        fuentes = [e["fuente"]] + (["SEP"] if e.get("sep") and e["fuente"] != "SEP" else [])
        puntos.append(punto(e["nombre"], e["lat"], e["lng"], "escuelas:todo", principal, " + ".join(fuentes),
                            niveles=niveles, sostenimiento=e["sostenimiento"]))
    return puntos


if __name__ == "__main__":
    if len(sys.argv) == 3 and sys.argv[1] == "preparar":
        preparar(sys.argv[2])
    else:
        print(__doc__)
