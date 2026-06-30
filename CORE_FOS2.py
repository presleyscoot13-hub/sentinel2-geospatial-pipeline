# =================================================================
# Project: Satellite Data Pre-processing Pipeline (FOS Simulator)
# Author: [Ton Prénom / Nom] - University Internship
# Date: June 2026
# Description: Automated pipeline to ingest, filter, and intersect
#              Sentinel-2 acquisition plans with an AOI.
# =================================================================
import glob
import json
import os
import xml.etree.ElementTree as ET
from datetime import datetime
import geopandas as gpd
import simplekml
from shapely.geometry import MultiPolygon, Polygon

print("🔄 Caricamento dei dati...")

# 1. TROVA AUTOMATICAMENTE IL FILE JSON NELLA CARTELLA OUTBOX
cartella_outbox = os.path.join(os.path.dirname(__file__), "outbox")
pattern_ricerca = os.path.join(cartella_outbox, "MP_ACQPLAN_*.json")
file_trovati = glob.glob(pattern_ricerca)

if not file_trovati:
    raise FileNotFoundError(
        "Nessun file MP_ACQPLAN trovato nella cartella outbox!"
    )

percorso_aoi = file_trovati[0]
print(f"📂 File AOI rilevato automaticamente: {os.path.basename(percorso_aoi)}")

aoi = gpd.read_file(percorso_aoi)
if aoi.crs != "EPSG:4326":
    aoi = aoi.to_crs("EPSG:4326")
aoi_geom = aoi.geometry.iloc[0]

# 2. Leggiamo le orbite dal file generato dal primo script (FOS_PLFILE.json)
# Usiamo os.path.join per sicurezza sul percorso
percorso_plfile = os.path.join(os.path.dirname(__file__), "FOS_PLFILE.json")
with open(percorso_plfile, "r", encoding="utf-8") as f:
    dati_json = json.load(f)

orbite_target = set()
for acq in dati_json["planned_acquisitions"]:
    try:
        num_orbit = int(acq["relative_orbit"].replace("R", ""))
        orbite_target.add(num_orbit)
    except:
        continue

# 3. Inizializziamo il KML per Google Earth
kml_output = simplekml.Kml()

# Disegniamo l'AOI (Rosso)
coords_aoi = (
    list(aoi_geom.exterior.coords)
    if aoi_geom.geom_type == "Polygon"
    else list(aoi_geom.geoms[0].exterior.coords)
)
pol_aoi = kml_output.newpolygon(name="La mia AOI")
pol_aoi.style.linestyle.color = simplekml.Color.red
pol_aoi.style.linestyle.width = 4
pol_aoi.style.polystyle.color = simplekml.Color.changealphaint(
    0, simplekml.Color.red
)
pol_aoi.outerboundaryis = coords_aoi

# 4. Scansione dei KML originali (CORRETTO: puntano dentro la cartella outbox/)
kml_files = [
    os.path.join(
        cartella_outbox, "S2A_MP_ACQ__KML_20260625T150000_20260713T180000.kml"
    ),
    os.path.join(
        cartella_outbox, "S2B_MP_ACQ__KML_20260618T120000_20260706T150000.kml"
    ),
    os.path.join(
        cartella_outbox, "S2C_MP_ACQ__KML_20260625T120000_20260713T150000.kml"
    ),
]
namespaces = {"kml": "http://www.opengis.net/kml/2.2"}
conteggio = 0

print("✂️ Estrazione e RITAGLIO geometrico delle orbite in corso...")
for kml_file in kml_files:
    try:
        tree = ET.parse(kml_file)
        root = tree.getroot()
    except Exception:
        continue

    for placemark in root.findall(".//kml:Placemark", namespaces):
        orbit_num_kml = None
        start_time = "Sconosciuto"

        for data in placemark.findall(".//kml:Data", namespaces):
            data_name = data.get("name")
            value_node = data.find("kml:value", namespaces)
            if value_node is not None:
                if data_name == "OrbitRelative":
                    orbit_num_kml = int(value_node.text)
                elif data_name == "ObservationTimeStart":
                    start_time = value_node.text

        if orbit_num_kml in orbite_target:
            poly_node = placemark.find(".//kml:Polygon", namespaces)
            if poly_node is not None:
                coord_node = placemark.find(".//kml:coordinates", namespaces)
                if coord_node is not None:
                    try:
                        coords_text = coord_node.text.strip()
                        coords_list = []
                        for c in coords_text.split():
                            parts = c.split(",")
                            coords_list.append(
                                (float(parts[0]), float(parts[1]))
                            )

                        sat_poly = Polygon(coords_list)

                        # ⚡ IL TRUCCO DEL TAGLIO: Calcoliamo l'intersezione reale
                        if sat_poly.intersects(aoi_geom):
                            poly_ritagliato = sat_poly.intersection(aoi_geom)

                            # Ignoriamo geometrie vuote o linee spurie post-taglio
                            if (
                                poly_ritagliato.is_empty
                                or poly_ritagliato.geom_type
                                not in ["Polygon", "MultiPolygon"]
                            ):
                                continue

                            # Gestiamo sia singoli poligoni che multipoligoni generati dal taglio
                            poligoni_da_disegnare = (
                                [poly_ritagliato]
                                if poly_ritagliato.geom_type == "Polygon"
                                else list(poly_ritagliato.geoms)
                            )

                            for p in poligoni_da_disegnare:
                                # Crea la nuova forma ritagliata nel KML
                                pol_sat = kml_output.newpolygon(
                                    name=f"R{orbit_num_kml:03d} | {start_time[:16]}"
                                )
                                pol_sat.outerboundaryis = list(
                                    p.exterior.coords
                                )

                                # Stile Verde originale
                                pol_sat.style.linestyle.color = (
                                    simplekml.Color.lime
                                )
                                pol_sat.style.linestyle.width = 2
                                pol_sat.style.polystyle.color = (
                                    simplekml.Color.changealphaint(
                                        50, simplekml.Color.lime
                                    )
                                )

                            conteggio += 1
                    except Exception:
                        continue

# 5. Salva il KML finale ritagliato
kml_output.save("FOS_ACFILE.kml")
print(
    f"\n✅ Operazione riuscita! Creato 'FOS_ACFILE.kml' con {conteggio} orbite tagliate al millimetro."
)