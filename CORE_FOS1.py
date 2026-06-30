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
from shapely.geometry import Polygon

# Identifichiamo la cartella 'outbox' rispetto alla posizione di questo script
cartella_outbox = os.path.join(os.path.dirname(__file__), "outbox")

print("🔄 Caricamento del file AOI...")
# 1. Glob cerca il vero nome del file dentro la cartella outbox
pattern_ricerca = os.path.join(cartella_outbox, "MP_ACQPLAN_*.json*")
file_trovati = glob.glob(pattern_ricerca)

# 2. Se il file esiste, lo passiamo a GeoPandas
if file_trovati:
    nome_file = file_trovati[0]  # Recupera il primo file corrispondente
    aoi = gpd.read_file(nome_file)
    print(f"Successo! File caricato: {os.path.basename(nome_file)}")
else:
    print("Errore: Nessun file che inizia con 'MP_ACQPLAN_' è stato trovato.")
    # Se l'AOI non viene trovata, creiamo una variabile vuota o blocchiamo per evitare crash successivi
    aoi = None

# 2. LISTA DEI FILE KML (Puntano correttamente dentro outbox/)
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
extracted_records = []

# Loop attraverso tutti i file KML forniti se l'aoi è valida
if aoi is not None:
    for kml_file in kml_files:
        print(
            f"📅 Estrazione dettagliata dei dati dal file: {os.path.basename(kml_file)}..."
        )
        try:
            tree = ET.parse(kml_file)
            root = tree.getroot()
        except Exception as e:
            print(
                f"⚠️ Errore durante la lettura di {os.path.basename(kml_file)}: {e}. Salto il file."
            )
            continue

        # Si esaminano tutti i Placemark all'interno del file KML corrente
        for placemark in root.findall(".//kml:Placemark", namespaces):
            name = placemark.find("kml:name", namespaces)
            name_text = name.text if name is not None else ""

            orbit_rel = "Sconosciuta"
            start_time = "Sconosciuto"

            for data in placemark.findall(".//kml:Data", namespaces):
                data_name = data.get("name")
                value_node = data.find("kml:value", namespaces)
                if value_node is not None:
                    if data_name == "OrbitRelative":
                        orbit_rel = f"R{int(value_node.text):03d}"
                    elif data_name == "ObservationTimeStart":
                        start_time = value_node.text

            # Recupera la geometria del Placemark
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

                        # Verifica intersezione con l'AOI
                        sat_poly = gpd.GeoDataFrame(
                            geometry=[Polygon(coords_list)], crs=aoi.crs
                        )

                        if sat_poly.intersects(aoi.geometry.iloc[0]).any():
                            # Identificazione del satellite (Incluso S2C)
                            if name_text.endswith("-1") or "S2A" in name_text:
                                sat_type = "Sentinel-2A"
                            elif (
                                name_text.endswith("-2") or "S2B" in name_text
                            ):
                                sat_type = "Sentinel-2B"
                            elif (
                                name_text.endswith("-3") or "S2C" in name_text
                            ):
                                sat_type = "Sentinel-2C"
                            else:
                                sat_type = "Sentinel-2 (Altro/Nuovo)"

                            extracted_records.append(
                                {
                                    "date_start": start_time,
                                    "relative_orbit": orbit_rel,
                                    "satellite": sat_type,
                                    "status": "Scheduled",
                                }
                            )
                    except Exception:
                        continue

# 🔥 OTTIMIZZAZIONE: Ordina i risultati per data (dalla più recente alla più lontana)
def estrai_data(record):
    try:
        return datetime.fromisoformat(record["date_start"])
    except:
        return datetime.min  # Se la data non è valida, la mette in fondo


extracted_records.sort(key=estrai_data)

# 3. Struttura finale pulita
output_json = {
    "aoi_reference": "La mia Zona di Interesse (AOI)",
    "planned_acquisitions": extracted_records,
}

# 4. Salvataggio del file con il nome corretto richiesto dal tuteur
with open("FOS_PLFILE.json", "w", encoding="utf-8") as f:
    json.dump(output_json, f, indent=2, ensure_ascii=False)

print(
    f"✅ Terminato! In totale, {len(extracted_records)} acquisizioni corrispondono alla tua AOI su tutti i satelliti."
)