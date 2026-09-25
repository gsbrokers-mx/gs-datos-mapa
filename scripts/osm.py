"""OpenStreetMap (Overpass): gobierno, cadenas por marca, parques y sitios emblemáticos, zonas militares."""
import math
import re

from comun import Poligonos, armar_anillos, config, deduplicar, norm, overpass, punto

FUENTE = "OpenStreetMap"

# Elementos que nunca son un punto de interés aunque lleven el nombre de una marca
IGNORAR_AMENITY = {"atm", "parking", "parking_entrance", "parking_space", "fuel", "car_wash", "bicycle_parking",
                   "vending_machine", "charging_station", "bus_station", "taxi", "toilets", "loading_dock"}
IGNORAR_CLAVES = ("highway", "railway", "public_transport", "route", "entrance", "barrier", "power", "waterway")
NO_ES_GOBIERNO = re.compile(r"\bCLUB\b|CENTRO COMUNITARIO|DESARROLLO COMUNITARIO|SALON\b|\bCOTO\b|FRACCIONAMIENTO"
                            r"|ASOCIACION DE COLONOS|BIBLIOTECA|MUEBLERIA|CASA EJIDAL|^EJIDO\b")
# Parques: solo los grandes (20 ha o más, o 5 ha con ficha en Wikidata/Wikipedia); todos principales.
# Plazas con ficha pero chicas van a otros:monumentos. Superficie aproximada por el recuadro del polígono.
PARQUE_MIN_HA, PARQUE_MIN_HA_CON_FICHA, PARQUE_MAX_HA_SIN_FICHA = 20, 5, 1000
GOBIERNO_PRINCIPAL = re.compile(
    r"\bCISZ\b|CENTRO INTEGRAL DE SERVICIOS|UNIDAD ADMINISTRATIVA|PRESIDENCIA MUNICIPAL|PALACIO (MUNICIPAL|DE GOBIERNO)"
    r"|AYUNTAMIENTO|CENTRO ADMINISTRATIVO|CIUDAD JUDICIAL|CASA JALISCO|\bSAT\b|PASAPORTES|DELEGACION (DE LA )?SRE|RECAUDADORA")


def filtro_zona(zona):
    """Devuelve (prefijo de consulta, filtro) usando los municipios de la zona o su recuadro."""
    if "osm_area" in zona:
        a = zona["osm_area"]
        nombres = "|".join(re.escape(n) for n in zona["municipios"].values())
        pre = (f'area["ISO3166-2"="{a["iso_estado"]}"]->.estado;'
               f'rel(area.estado)["boundary"="administrative"]["admin_level"="{a["admin_level"]}"]'
               f'["name"~"^({nombres})$"];map_to_area->.zona;')
        return pre, "(area.zona)"
    s, o, n, e = zona["bbox"]
    return "", f"({s},{o},{n},{e})"


def _regex_marcas(marcas):
    # Patrón para Overpass: sin \b (no lo admite) y con clases de acentos ("Aurrerá", "Bajío").
    # El filtro fino se hace después en Python con los patrones originales.
    acentos = {"A": "[AÁaá]", "E": "[EÉeé]", "I": "[IÍií]", "O": "[OÓoó]", "U": "[UÚÜuúü]"}
    trozos = {re.sub(r"[AEIOU]", lambda v: acentos[v.group(0)], p.replace("\\b", ""))
              for m in marcas for p in m["patrones"]}
    return "|".join(sorted(trozos))


def elementos(zona, clave_zona):
    pre, f = filtro_zona(zona)
    marcas = config("marcas.json")["marcas"]
    rx = _regex_marcas(marcas)
    q_general = f"""[out:json][timeout:180];{pre}
(
  nwr["office"="government"]{f};
  nwr["government"]{f};
  nwr["amenity"~"^(townhall|hospital|dentist|bank)$"]{f};
  nwr["healthcare"="dentist"]{f};
  nwr["shop"="mall"]["name"]{f};
  nwr["leisure"="stadium"]["name"]{f};
  nwr["tourism"="museum"]["name"]{f};
  nwr["historic"~"^(monument|memorial)$"]["wikidata"]{f};
  nwr["tourism"="attraction"]["wikidata"]{f};
  nwr["sport"="karting"]{f};
);
out center tags;"""
    q_marcas = f"""[out:json][timeout:180];{pre}
(
  nwr["shop"]["name"~"{rx}",i]{f};
  nwr["amenity"]["name"~"{rx}",i]{f};
  nwr["brand"~"{rx}",i]{f};
);
out center tags;"""
    q_parques = f"""[out:json][timeout:180];{pre}
(
  nwr["leisure"~"^(park|nature_reserve)$"]["name"]{f};
);
out tags bb;"""
    vistos, salida = set(), []
    # parques primero: su consulta trae el recuadro (bounds) para calcular la superficie
    for el in overpass(q_parques, f"parques_{clave_zona}")["elements"] + \
            overpass(q_general, f"general_{clave_zona}")["elements"] + \
            overpass(q_marcas, f"marcas_{clave_zona}")["elements"]:
        if (el["type"], el["id"]) not in vistos:
            vistos.add((el["type"], el["id"]))
            salida.append(el)
    return salida


def militares(zona, clave_zona):
    pre, f = filtro_zona(zona)
    s, o, n, e = zona["bbox"]
    q = f"""[out:json][timeout:180];
(
  way["landuse"="military"]({s},{o},{n},{e});
  way["military"]["military"!~"^(checkpoint|office|recruitment_office)$"]({s},{o},{n},{e});
  rel["landuse"="military"]({s},{o},{n},{e});
  rel["military"]({s},{o},{n},{e});
);
out geom;"""
    datos = overpass(q, f"militar_{clave_zona}")["elements"]
    anillos = []
    for el in datos:
        if el["type"] == "way" and "geometry" in el:
            anillos += armar_anillos([[(p["lat"], p["lon"]) for p in el["geometry"]]])
        elif el["type"] == "relation":
            tramos = [[(p["lat"], p["lon"]) for p in m["geometry"]]
                      for m in el.get("members", []) if m.get("role", "outer") in ("outer", "") and "geometry" in m]
            anillos += armar_anillos(tramos)
    return Poligonos(anillos)


def _coords(el):
    if "center" in el:
        return el["center"]["lat"], el["center"]["lon"]
    if "bounds" in el:
        b = el["bounds"]
        return (b["minlat"] + b["maxlat"]) / 2, (b["minlon"] + b["maxlon"]) / 2
    return el.get("lat"), el.get("lon")


def _hectareas(el):
    """Superficie aproximada por el recuadro del polígono (sobreestima formas diagonales)."""
    b = el.get("bounds")
    if not b:
        return 0
    alto = (b["maxlat"] - b["minlat"]) * 111_000
    ancho = (b["maxlon"] - b["minlon"]) * 111_000 * math.cos(math.radians(b["minlat"]))
    return alto * ancho / 10_000


def clasificar(zona, clave_zona):
    """Devuelve lista de puntos (todas las categorías OSM) ya deduplicados."""
    marcas = config("marcas.json")["marcas"]
    compiladas = [(m, [re.compile(p) for p in m["patrones"]]) for m in marcas]
    puntos = []
    for el in elementos(zona, clave_zona):
        t = el.get("tags", {})
        lat, lng = _coords(el)
        if lat is None:
            continue
        if t.get("amenity") in IGNORAR_AMENITY or any(k in t for k in IGNORAR_CLAVES):
            continue
        nombre = t.get("name") or t.get("brand") or ""
        n = norm(nombre)
        extra = {"wikidata": t.get("wikidata")}

        # 1) cadenas, por nombre o marca (no por shop)
        marca = None
        for m, pats in compiladas:
            if any(p.search(n) or p.search(norm(t.get("brand", ""))) for p in pats):
                marca = m
                break
        if marca and not t.get("amenity") in ("school", "university", "hospital", "clinic"):
            principal = marca["principal"] and not any(x in n for x in marca.get("no_principal_si", []))
            if marca["clave"] == "bancos:bancos" and t.get("amenity") not in ("bank", None):
                continue
            puntos.append(punto(nombre, lat, lng, marca["clave"], principal, FUENTE, marca=marca["marca"], **extra))
            continue
        if not nombre:
            continue

        # 2) gobierno y trámites (sin centros comunitarios ni clubes)
        if t.get("office") == "government" or "government" in t or t.get("amenity") == "townhall":
            if (t.get("amenity") == "community_centre" or "club" in t or "leisure" in t or "shop" in t
                    or NO_ES_GOBIERNO.search(n)):
                continue
            principal = bool(GOBIERNO_PRINCIPAL.search(n))
            puntos.append(punto(nombre, lat, lng, "bancos:gobierno", principal, FUENTE, **extra))
        elif t.get("amenity") == "bank":
            puntos.append(punto(nombre, lat, lng, "bancos:bancos", False, FUENTE, **extra))
        elif t.get("amenity") == "hospital":
            puntos.append(punto(nombre, lat, lng, "salud:hospitales", True, FUENTE, **extra))
        elif t.get("amenity") == "dentist" or t.get("healthcare") == "dentist":
            puntos.append(punto(nombre, lat, lng, "salud:dentales", False, FUENTE, **extra))
        elif t.get("shop") == "mall":
            puntos.append(punto(nombre, lat, lng, "comercio:centros", bool(t.get("wikidata")), FUENTE, **extra))
        elif t.get("sport") == "karting":
            puntos.append(punto(nombre, lat, lng, "referencias:deporte", False, FUENTE, tipo="deporte"))
        elif t.get("leisure") == "stadium":
            puntos.append(punto(nombre, lat, lng, "otros:estadios", True, FUENTE, **extra))
        elif t.get("tourism") == "museum":
            puntos.append(punto(nombre, lat, lng, "otros:museos", bool(t.get("wikidata")), FUENTE, **extra))
        elif t.get("leisure") in ("park", "nature_reserve"):
            ha = _hectareas(el)
            ficha = bool(t.get("wikidata") or t.get("wikipedia"))
            if ha > PARQUE_MAX_HA_SIN_FICHA and not ficha:
                continue  # áreas de protección enormes: su punto central no ayuda a ubicarse
            if ha >= PARQUE_MIN_HA or (ficha and ha >= PARQUE_MIN_HA_CON_FICHA):
                puntos.append(punto(nombre, lat, lng, "parques:todo", True, FUENTE, hectareas=round(ha), **extra))
            elif ficha:  # plazas y jardines emblemáticos pero chicos
                puntos.append(punto(nombre, lat, lng, "otros:monumentos", True, FUENTE, **extra))
        elif t.get("wikidata") and (t.get("historic") in ("monument", "memorial") or t.get("tourism") == "attraction"):
            puntos.append(punto(nombre, lat, lng, "otros:monumentos", True, FUENTE, **extra))

    # nodos y edificios del mismo lugar: mismo nombre y categoría a menos de 250 m
    # las cadenas se comparan por marca ("The Home Depot" = "The Home Depot Santa Anita")
    puntos.sort(key=lambda p: (not p["principal"], len(p["nombre"]), p["nombre"]))
    return deduplicar(puntos, metros=250, llave=lambda p: (p["clave"], p.get("marca") or norm(p["nombre"])))
