"""INEGI DENUE: capa de Referencias, salones de fiestas y base de escuelas."""
import csv
import io
import re
import statistics
import zipfile
from functools import lru_cache

from comun import arreglar, deduplicar, descargar, log, norm, parecido, punto

FUENTE = "INEGI DENUE"
URL = "https://www.inegi.org.mx/contenidos/masiva/denue/denue_{ent}_csv.zip"

# Códigos SCIAN
DEPORTE = {"713941", "713942", "713910", "713950", "713992", "713998", "713999"}
GIMNASIO = {"713943", "713944"}
EVENTOS = {"531115", "561920", "711311", "711312", "711320"}
SALONES = {"531113"}
# Sectores con 251+ empleados que ya tienen su propia capa (comercio al por menor, escuelas, salud, gobierno)
EXCLUIR_EMPRESA = {"46", "61", "62", "93"}
TIPOS_INDUSTRIALES = {"PARQUE INDUSTRIAL", "ZONA INDUSTRIAL", "CORREDOR INDUSTRIAL", "CIUDAD INDUSTRIAL"}
SIN_NOMBRE = re.compile(
    r"SIN NOMBRE|^ALQUILER\b|^SALON( DE (FIESTAS|EVENTOS))?$|^GIMNASIO$|^GYM$|"
    r"^CENTRO DE ACONDICIONAMIENTO|^NINGUNO$|^NA$|^$|^PARQUE INDUSTRIAL$|^ZONA INDUSTRIAL$"
)

# Escuelas: SCIAN -> (nivel, sostenimiento)
NIVEL_SCIAN = {
    "61111": "preescolar", "61112": "primaria", "61113": "secundaria", "61114": "secundaria",
    "61115": "preparatoria", "61116": "preparatoria", "61117": "varios", "61118": "otras",
    "61121": "universidad", "61131": "universidad",
}


@lru_cache(maxsize=None)
def filas(entidad, municipios):
    """Establecimientos del DENUE de la entidad, filtrados a los municipios de la zona."""
    ruta = descargar(URL.format(ent=entidad), f"denue_{entidad}_csv.zip", max_horas=20)
    z = zipfile.ZipFile(ruta)
    nombre = next(n for n in z.namelist() if n.startswith("conjunto_de_datos/") and n.endswith(".csv"))
    salida = []
    with z.open(nombre) as f:
        for r in csv.DictReader(io.TextIOWrapper(f, encoding="latin-1", newline="")):
            if r["cve_mun"] not in municipios:
                continue
            try:
                lat, lng = float(r["latitud"]), float(r["longitud"])
            except ValueError:
                continue
            salida.append({
                "nombre": arreglar(r["nom_estab"]).strip(),
                "razon": arreglar(r["raz_social"]).strip(),
                "scian": r["codigo_act"],
                "personal": r["per_ocu"],
                "tipo_centro": norm(arreglar(r["tipoCenCom"])),
                "nombre_centro": arreglar(r["nom_CenCom"]).strip(),
                "lat": lat, "lng": lng,
            })
    log(f"   DENUE {entidad}: {len(salida)} establecimientos en la zona")
    return salida


def _con_nombre(n):
    return not SIN_NOMBRE.search(norm(n))


def _nombre_parque(n):
    base = norm(n)
    base = re.sub(r"^(PARQUE|ZONA|CORREDOR|CIUDAD) INDUSTRIAL( Y (LOGISTICO|TECNOLOGICO))?\b", "", base)
    base = re.sub(r"\b(CONDOMINIO|CONDOMINO) INDUSTRIAL\b", "", base).strip()
    return base


GENERICAS_PARQUE = {"PARQUE", "INDUSTRIAL", "INDUSTRIALES", "ZONA", "CORREDOR", "CIUDAD", "PARK", "CONDOMINIO",
                    "CONDOMINO", "NORTE", "SUR", "ORIENTE", "PONIENTE", "AC", "II", "III", "2", "3"}


def _mismo_parque(a, b):
    fa = {t for t in norm(a).split() if t not in GENERICAS_PARQUE and len(t) > 1}
    fb = {t for t in norm(b).split() if t not in GENERICAS_PARQUE and len(t) > 1}
    return bool(fa and fb) and len(fa & fb) / len(fa | fb) >= 0.5


def referencias(zona):
    """Devuelve (referencias, salones)."""
    rows = filas(zona["entidad"], tuple(sorted(zona["municipios"])))
    municipios = {norm(m) for m in zona["municipios"].values()} | {"GUADALAJARA", "JALISCO"}
    refs, salones = [], []
    parques = {}
    for r in rows:
        c, n = r["scian"], r["nombre"]
        if r["tipo_centro"] in TIPOS_INDUSTRIALES:
            llave = _nombre_parque(r["nombre_centro"])
            if llave and llave not in municipios and _con_nombre(r["nombre_centro"]):
                parques.setdefault(llave, []).append(r)
        if c in DEPORTE:
            tipo = "deporte"
        elif c in GIMNASIO:
            tipo = "gimnasio"
        elif c in EVENTOS:
            tipo = "eventos"
        elif c in SALONES:
            if _con_nombre(n):
                salones.append(punto(n, r["lat"], r["lng"], "referencias:salon", False, FUENTE,
                                     tipo="salon", filtro=False))
            continue
        elif r["personal"].startswith("251") and c[:2] not in EXCLUIR_EMPRESA:
            tipo = "empresa"
        else:
            continue
        if not _con_nombre(n):
            continue
        refs.append(punto(n, r["lat"], r["lng"], f"referencias:{tipo}", False, FUENTE, tipo=tipo))

    for llave, miembros in parques.items():
        if len(miembros) < 2:
            continue
        nombres = [m["nombre_centro"] for m in miembros]
        nombre = max(set(nombres), key=nombres.count)
        if not re.search(r"INDUSTRIAL|PARK|PARQUE", norm(nombre)):
            nombre = f"Parque Industrial {nombre.title()}"
        lat = statistics.median(m["lat"] for m in miembros)
        lng = statistics.median(m["lng"] for m in miembros)
        refs.append(punto(nombre, lat, lng, "referencias:parque_industrial", False, FUENTE,
                          tipo="parque_industrial"))

    # Plantas de la misma empresa a menos de 300 m y parques con nombres parecidos a menos de 1.5 km
    refs = deduplicar(refs, metros=300)
    refs = deduplicar(refs, metros=1500, similares=lambda p, q: p["tipo"] == q["tipo"] == "parque_industrial"
                      and _mismo_parque(p["nombre"], q["nombre"]))
    salones = deduplicar(salones, metros=100)
    return refs, salones


def escuelas(zona):
    """Escuelas del DENUE con nivel y sostenimiento según SCIAN."""
    rows = filas(zona["entidad"], tuple(sorted(zona["municipios"])))
    salida = []
    for r in rows:
        nivel = NIVEL_SCIAN.get(r["scian"][:5])
        if not nivel or not _con_nombre(r["nombre"]):
            continue
        salida.append({
            "nombre": r["nombre"], "lat": r["lat"], "lng": r["lng"],
            "niveles": {nivel},
            "sostenimiento": "privado" if r["scian"].endswith("1") else "publico",
            "fuente": FUENTE,
        })
    return salida
