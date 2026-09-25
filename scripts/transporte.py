"""Tren Ligero de Guadalajara / Metro de Madrid: líneas (con color) y estaciones, desde OpenStreetMap."""
import re
import statistics

from comun import distancia, norm, overpass, simplificar, titulo

FUENTE = "OpenStreetMap"
COLORES = {"orange": "#F28C00", "red": "#E30613", "green": "#009A44", "blue": "#0055A4", "yellow": "#FFD100",
           "purple": "#7B2D8E", "pink": "#E5007D", "brown": "#8B5A2B", "grey": "#808080", "gray": "#808080",
           "white": "#B0B7C3"}  # el Ramal de Madrid es blanco: se usa gris claro para que se vea
ROLES_PARADA =re.compile(r"^(stop|stop_entry_only|stop_exit_only|platform|platform_entry_only|platform_exit_only)$")


def tren(zona, clave_zona):
    cfg = zona["tren"]
    s, o, n, e = zona["bbox"]
    q = f"""[out:json][timeout:180];
rel["type"="route"]["route"~"^({cfg['route']})$"]["network"~"{cfg['network']}",i]({s},{o},{n},{e})->.r;
.r out geom;
node(r.r);
out;
way(r.r)["public_transport"="platform"];
out center tags;
rel(br.r)["type"="route_master"];
out tags;"""
    datos = overpass(q, f"tren_{clave_zona}")["elements"]
    nodos = {x["id"]: x for x in datos if x["type"] == "node"}
    vias = {x["id"]: x for x in datos if x["type"] == "way"}
    maestros = [x for x in datos if x["type"] == "relation" and x["tags"].get("type") == "route_master"]
    rutas = [x for x in datos if x["type"] == "relation" and x["tags"].get("type") == "route"]

    color_maestro = {}
    for m in maestros:
        for mem in m.get("members", []):
            color_maestro[mem["ref"]] = m["tags"].get("colour")

    # una relación por línea (la de más tramos)
    por_linea = {}
    for r in rutas:
        ref = r["tags"].get("ref") or r["tags"].get("name")
        tramos = [m for m in r.get("members", []) if m["type"] == "way" and "geometry" in m
                  and not ROLES_PARADA.match(m.get("role", ""))]
        if ref not in por_linea or len(tramos) > len(por_linea[ref][1]):
            por_linea[ref] = (r, tramos)

    features, estaciones = [], {}
    for ref, (r, tramos) in sorted(por_linea.items()):
        t = r["tags"]
        linea = re.sub(r"^(TL|L)-?\s*", "", ref or "")
        linea = linea if re.match(r"^[A-Z]", linea) else f"L{linea}"
        color = t.get("colour") or color_maestro.get(r["id"]) or "#666666"
        color = COLORES.get(color.lower(), color).upper()
        nombre_linea = re.split(r"[:.(]", t.get("name") or linea)[0].strip().replace("Linea", "Línea")
        nombre_linea = re.sub(r"^Tren Ligero\s+", "", nombre_linea)
        geometria = [simplificar([[round(p["lon"], 5), round(p["lat"], 5)] for p in m["geometry"]])
                     for m in tramos]
        features.append({
            "type": "Feature",
            "properties": {"tipo": "linea", "linea": linea, "nombre": nombre_linea, "color": color,
                           "fuente": FUENTE},
            "geometry": {"type": "MultiLineString", "coordinates": geometria},
        })
        # estaciones de todas las relaciones con esta ref (ambos sentidos)
        for rr in rutas:
            if (rr["tags"].get("ref") or rr["tags"].get("name")) != ref:
                continue
            for m in rr.get("members", []):
                if not ROLES_PARADA.match(m.get("role", "")):
                    continue
                el = nodos.get(m["ref"]) if m["type"] == "node" else vias.get(m["ref"])
                if not el:
                    continue
                nombre = el.get("tags", {}).get("name")
                if not nombre:
                    continue
                lat = el.get("lat") or el.get("center", {}).get("lat")
                lng = el.get("lon") or el.get("center", {}).get("lon")
                nombre = re.sub(r"^(Estaci[oó]n|Andén|Anden)\s+", "", nombre).strip()
                grupo = estaciones.setdefault(norm(nombre), {"nombre": nombre, "lats": [], "lngs": [], "lineas": set()})
                grupo["lats"].append(lat)
                grupo["lngs"].append(lng)
                grupo["lineas"].add(linea)

    # estaciones con el mismo nombre pero lejanas (p. ej. homónimas en líneas distintas) se separan
    for g in estaciones.values():
        lat, lng = statistics.median(g["lats"]), statistics.median(g["lngs"])
        lejos = max(distancia(lat, lng, a, b) for a, b in zip(g["lats"], g["lngs"]))
        features.append({
            "type": "Feature",
            "properties": {"tipo": "estacion", "nombre": titulo(g["nombre"]), "lineas": sorted(g["lineas"]),
                           "clave": "transporte:tren", "principal": False, "fuente": FUENTE,
                           **({"revisar": True} if lejos > 800 else {})},
            "geometry": {"type": "Point", "coordinates": [round(lng, 5), round(lat, 5)]},
        })
    return {"type": "FeatureCollection", "tipo": cfg["tipo"], "features": features}
