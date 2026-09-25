"""Utilidades compartidas: descargas, texto, geometría, deduplicado y escritura."""
import json
import math
import os
import re
import time
import unicodedata
from pathlib import Path

import requests

RAIZ = Path(__file__).resolve().parent.parent
CONFIG = RAIZ / "config"
CACHE = Path(os.environ.get("GS_CACHE", RAIZ / ".cache"))
UA = {"User-Agent": "gs-datos-mapa/1.0 (+https://github.com/gsbrokers-mx/gs-datos-mapa)"}

OVERPASS = [
    "https://overpass-api.de/api/interpreter",
    "https://overpass.private.coffee/api/interpreter",
    "https://maps.mail.ru/osm/tools/overpass/api/interpreter",
]


def log(*a):
    print(*a, flush=True)


def config(nombre):
    return json.loads((CONFIG / nombre).read_text(encoding="utf-8"))


# ---------- descargas ----------

def descargar(url, nombre, max_horas=20, intentos=3):
    """Descarga a .cache/<nombre>; reutiliza el archivo si tiene menos de max_horas."""
    CACHE.mkdir(parents=True, exist_ok=True)
    destino = CACHE / nombre
    if destino.exists() and time.time() - destino.stat().st_mtime < max_horas * 3600:
        return destino
    ultimo = None
    for i in range(intentos):
        try:
            with requests.get(url, headers=UA, stream=True, timeout=180) as r:
                r.raise_for_status()
                tmp = destino.with_suffix(destino.suffix + ".part")
                with open(tmp, "wb") as f:
                    for trozo in r.iter_content(1 << 20):
                        f.write(trozo)
                tmp.replace(destino)
            return destino
        except Exception as e:  # noqa: BLE001
            ultimo = e
            log(f"   intento {i + 1} falló: {e}")
            time.sleep(10 * (i + 1))
    raise RuntimeError(f"No se pudo descargar {url}: {ultimo}")


def overpass(consulta, nombre_cache, max_horas=20):
    """Ejecuta una consulta Overpass con espejos de respaldo y caché local."""
    CACHE.mkdir(parents=True, exist_ok=True)
    destino = CACHE / f"osm_{nombre_cache}.json"
    if destino.exists() and time.time() - destino.stat().st_mtime < max_horas * 3600:
        return json.loads(destino.read_text(encoding="utf-8"))
    ultimo = None
    time.sleep(3)  # cortesía entre consultas (Overpass limita por IP)
    for intento in range(3):
        if intento:
            time.sleep(60 * intento)
        for url in OVERPASS:
            try:
                r = requests.post(url, data={"data": consulta}, headers=UA, timeout=200)
                r.raise_for_status()
                datos = r.json()
                if "remark" in datos and "error" in datos["remark"].lower():
                    raise RuntimeError(datos["remark"])
                destino.write_text(json.dumps(datos), encoding="utf-8")
                return datos
            except Exception as e:  # noqa: BLE001
                ultimo = e
                log(f"   Overpass {url} falló: {str(e)[:150]}")
    raise RuntimeError(f"Overpass no respondió: {ultimo}")


# ---------- texto ----------

def arreglar(s):
    """Corrige campos con codificación mezclada (UTF-8 leído como Latin-1)."""
    if not s:
        return ""
    try:
        return s.encode("latin-1").decode("utf-8")
    except (UnicodeEncodeError, UnicodeDecodeError):
        return s


def norm(s):
    """MAYÚSCULAS sin acentos ni signos, para comparar."""
    s = unicodedata.normalize("NFKD", s or "").encode("ascii", "ignore").decode()
    return re.sub(r"\s+", " ", re.sub(r"[^A-Z0-9' ]", " ", s.upper())).strip()


SIGLAS = {
    "IMSS", "ISSSTE", "UMF", "DIF", "CRIT", "UDG", "UAG", "ITESO", "ITEA", "CUCEI", "CUCEA", "CUCSH",
    "CUAAD", "CUCS", "CUCBA", "CUTONALA", "UNIVA", "UP", "UVM", "ICEL", "CETI", "CONALEP", "CECYTEJ",
    "CBTIS", "CETIS", "CBTA", "SEP", "SSA", "SAT", "CFE", "SIAPA", "HSBC", "BBVA", "CISZ", "UNEMES",
    "CAM", "CAPS", "CENDI", "SEDENA", "SEMAR", "IPN", "TEC", "USA", "II", "III", "IV", "VI", "VII",
    "VIII", "IX", "XI", "XII", "XX", "XXI", "SA", "CV", "AC", "SC", "S", "C", "V", "TCS", "UTJ", "UTZMG",
    "IMSS-BIENESTAR", "DHL", "HGZ", "HGR", "HGO", "HES", "HP", "HR", "HAE", "CMN", "UMAA", "UMAE", "CAAPS",
    "CESSA", "CRUM", "SEDENA", "ITESM", "UTEG", "UNE", "UNID", "UTEL", "CUT", "CUCIENEGA", "UPS", "GDL", "ZMG", "OPD", "SSJ", "SAPI", "LG", "HP", "IBM",
}
MINUSCULAS = {"de", "del", "la", "las", "el", "los", "y", "e", "en", "a", "para", "por", "con", "al", "o"}


def titulo(s):
    """Convierte NOMBRES EN MAYÚSCULAS a Tipo Título conservando siglas."""
    s = re.sub(r"\s+", " ", (s or "").strip())
    if not s:
        return s
    if s != s.upper():  # ya viene con mayúsculas y minúsculas
        return s
    palabras = []
    for i, p in enumerate(s.split(" ")):
        base = norm(p)
        if "/" in p and all(norm(x) in SIGLAS for x in p.split("/")):
            palabras.append(p)
        elif base in SIGLAS or (len(base) > 1 and not re.search(r"[AEIOU]", base) and base.isalpha()):
            palabras.append(p)
        elif i > 0 and p.lower() in MINUSCULAS:
            palabras.append(p.lower())
        else:
            palabras.append(p[:1].upper() + p[1:].lower())
    return " ".join(palabras)


GENERICOS = {
    "HOSPITAL", "HOSPITALES", "CLINICA", "CLINICAS", "FARMACIA", "FARMACIAS", "ESCUELA", "ESCUELAS", "BANCO",
    "BANCOS", "CAJERO", "CONSULTORIO", "CONSULTORIOS", "CONSULTORIO MEDICO", "CONSULTORIO DENTAL", "DENTISTA",
    "DENTAL", "CLINICA DENTAL", "GIMNASIO", "GYM", "PARQUE", "MUSEO", "ESTADIO", "PRIMARIA", "SECUNDARIA",
    "PREESCOLAR", "KINDER", "JARDIN DE NINOS", "ESCUELA PRIMARIA", "ESCUELA SECUNDARIA", "CENTRO DE SALUD",
    "OFICINA", "OFICINAS", "GOBIERNO", "PLAZA", "SALON", "SALON DE EVENTOS", "SALON DE FIESTAS", "BIBLIOTECA",
    "UNIVERSIDAD", "COLEGIO", "INSTITUTO", "PREPARATORIA", "BACHILLERATO", "GUARDERIA", "CANCHA", "CANCHAS",
    "CANCHA DE FUTBOL", "CAMPO DE FUTBOL", "UNIDAD DEPORTIVA", "SUPERMERCADO", "TIENDA", "CENTRO COMERCIAL",
    "EMPRESA", "FABRICA", "BODEGA", "MONUMENTO", "ESTATUA", "AREA VERDE", "JARDIN", "IGLESIA", "TEMPLO",
    "SIN NOMBRE", "NINGUNO", "ESTACION", "PARQUE INDUSTRIAL", "CENTRO DEPORTIVO", "CLUB DEPORTIVO", "ALBERCA",
    "SOCCER", "SOCCER FIELD", "FOOTBALL", "FOOTBALL FIELD", "BASKETBALL", "BASKETBALL COURT", "TENNIS COURT",
    "TENNIS", "PITCH", "SPORTS CENTRE", "SPORTS CENTER", "PLAYGROUND", "SKATEPARK", "SKATE PARK", "PISTA",
    "CANCHA DE BASQUETBOL", "CANCHA DE BASQUET", "CANCHA DE FUTBOL RAPIDO", "CANCHA DE TENIS", "CANCHA DE VOLEIBOL",
    "CANCHA MULTIUSOS", "CAMPO", "CAMPO DE BEISBOL", "CAMPO DE SOFTBALL", "CAMPO DEPORTIVO", "FRONTON",
}


def es_generico(nombre):
    """True si el nombre está vacío o es solo la categoría ("hospital", "Unidad Deportiva 51", "Jardín de Niños")."""
    base = re.sub(r"\b(NO|NUM|NUMERO)\b", " ", re.sub(r"[^A-Z ]", " ", norm(nombre)))
    base = re.sub(r"^(EL|LA|LOS|LAS)\s+", "", re.sub(r"\s+", " ", base).strip())
    return not base or base in GENERICOS


def completar(nombre, *alternativas):
    """Devuelve el nombre si no es genérico; si lo es, la primera alternativa útil; si no hay, None."""
    if not es_generico(nombre):
        return nombre
    for alt in alternativas:
        if alt and not es_generico(alt):
            return alt
    return None


PALABRAS_VACIAS = {
    "DE", "DEL", "LA", "LAS", "LOS", "EL", "Y", "E", "A", "EN", "S", "C", "V", "SA", "CV", "AC", "SC",
    "ESCUELA", "COLEGIO", "INSTITUTO", "JARDIN", "NINOS", "PRIMARIA", "SECUNDARIA", "PREESCOLAR",
    "KINDER", "PREPARATORIA", "BACHILLERATO", "CENTRO", "EDUCATIVO", "EDUCATIVA", "URBANA", "FEDERAL",
    "ESTATAL", "TECNICA", "GENERAL", "NUM", "NO", "TURNO", "MATUTINO", "VESPERTINO", "CAMPUS", "PLANTEL",
}


def fichas(s):
    return {t for t in norm(s).split() if t not in PALABRAS_VACIAS and len(t) > 1}


def parecido(a, b):
    fa, fb = fichas(a), fichas(b)
    if not fa or not fb:
        return 0.0
    return len(fa & fb) / len(fa | fb)


# ---------- geometría ----------

def distancia(lat1, lng1, lat2, lng2):
    """Metros (aproximación equirectangular, suficiente para < 50 km)."""
    x = math.radians(lng2 - lng1) * math.cos(math.radians((lat1 + lat2) / 2))
    y = math.radians(lat2 - lat1)
    return 6371000 * math.hypot(x, y)


def en_anillo(lat, lng, anillo):
    dentro = False
    j = len(anillo) - 1
    for i in range(len(anillo)):
        yi, xi = anillo[i]
        yj, xj = anillo[j]
        if (yi > lat) != (yj > lat) and lng < (xj - xi) * (lat - yi) / (yj - yi) + xi:
            dentro = not dentro
        j = i
    return dentro


class Poligonos:
    """Conjunto de anillos (lat, lng) con prefiltro por recuadro."""

    def __init__(self, anillos):
        self.items = []
        for a in anillos:
            if len(a) >= 4:
                lats = [p[0] for p in a]
                lngs = [p[1] for p in a]
                self.items.append((min(lats), max(lats), min(lngs), max(lngs), a))

    def contiene(self, lat, lng):
        for s, n, o, e, a in self.items:
            if s <= lat <= n and o <= lng <= e and en_anillo(lat, lng, a):
                return True
        return False


def armar_anillos(tramos):
    """Une tramos de vía (listas de (lat,lng)) en anillos cerrados."""
    tramos = [list(t) for t in tramos if len(t) > 1]
    anillos = []
    while tramos:
        actual = tramos.pop(0)
        cambio = True
        while actual[0] != actual[-1] and cambio:
            cambio = False
            for i, t in enumerate(tramos):
                if t[0] == actual[-1]:
                    actual += t[1:]
                elif t[-1] == actual[-1]:
                    actual += t[::-1][1:]
                elif t[-1] == actual[0]:
                    actual = t + actual[1:]
                elif t[0] == actual[0]:
                    actual = t[::-1] + actual[1:]
                else:
                    continue
                tramos.pop(i)
                cambio = True
                break
        if actual[0] == actual[-1]:
            anillos.append(actual)
    return anillos


def simplificar(puntos, tol=0.00005):
    """Douglas-Peucker sobre [(lng, lat)]; tol en grados (~5 m)."""
    if len(puntos) < 3:
        return puntos

    def dist_seg(p, a, b):
        if a == b:
            return math.hypot(p[0] - a[0], p[1] - a[1])
        t = max(0, min(1, ((p[0] - a[0]) * (b[0] - a[0]) + (p[1] - a[1]) * (b[1] - a[1]))
                       / ((b[0] - a[0]) ** 2 + (b[1] - a[1]) ** 2)))
        return math.hypot(p[0] - (a[0] + t * (b[0] - a[0])), p[1] - (a[1] + t * (b[1] - a[1])))

    conservar = [False] * len(puntos)
    conservar[0] = conservar[-1] = True
    pila = [(0, len(puntos) - 1)]
    while pila:
        i, j = pila.pop()
        mx, idx = 0, None
        for k in range(i + 1, j):
            d = dist_seg(puntos[k], puntos[i], puntos[j])
            if d > mx:
                mx, idx = d, k
        if idx is not None and mx > tol:
            conservar[idx] = True
            pila += [(i, idx), (idx, j)]
    return [p for p, c in zip(puntos, conservar) if c]


# ---------- puntos ----------

def punto(nombre, lat, lng, clave, principal, fuente, **extra):
    p = {
        "nombre": titulo(nombre),
        "lat": round(float(lat), 5),
        "lng": round(float(lng), 5),
        "clave": clave,
        "principal": bool(principal),
        "fuente": fuente,
    }
    p.update({k: v for k, v in extra.items() if v not in (None, "", [], {})})
    return p


def deduplicar(puntos, metros=150, llave=lambda p: (p["clave"], norm(p["nombre"])), similares=None):
    """Quita repetidos cercanos. Conserva el primero (ordena antes por prioridad)."""
    celda = metros / 111000
    rejilla = {}
    salida = []
    for p in puntos:
        cx, cy = int(p["lat"] / celda), int(p["lng"] / celda)
        k = llave(p)
        repetido = False
        for dx in (-1, 0, 1):
            for dy in (-1, 0, 1):
                for q in rejilla.get((cx + dx, cy + dy), []):
                    mismo = (similares(p, q) if similares else llave(q) == k)
                    if mismo and distancia(p["lat"], p["lng"], q["lat"], q["lng"]) <= metros:
                        repetido = True
                        break
                if repetido:
                    break
            if repetido:
                break
        if not repetido:
            rejilla.setdefault((cx, cy), []).append(p)
            salida.append(p)
    return salida


def escribir_json(ruta, obj):
    ruta = Path(ruta)
    ruta.parent.mkdir(parents=True, exist_ok=True)
    texto = json.dumps(obj, ensure_ascii=False, separators=(",", ":"))
    ruta.write_text(texto, encoding="utf-8")
    return len(texto.encode("utf-8"))
