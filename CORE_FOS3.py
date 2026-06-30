# =================================================================
# Project: Satellite Data Pre-processing Pipeline (FOS Simulator)
# Author: [Ton Prénom / Nom] - University Internship
# Date: June 2026
# Description: Automated pipeline to ingest, filter, and intersect
#              Sentinel-2 acquisition plans with an AOI.
# =================================================================
import json
import os
import xml.etree.ElementTree as ET
from datetime import datetime
import geopandas as gpd
import paramiko
import simplekml
from shapely.geometry import Polygon

# ==========================================
# 1. CONFIGURAZIONE CONNESSIONE
# ==========================================
SFTP_HOST = "10.0.1.219"
SFTP_PORT = 22
SFTP_USER = "sftpuser"
SFTP_PASSWORD = "password"

# Nomi esatti dei file KML forniti dal tutor (senza il "(2)")
KML_FILES = [
    "outbox/S2A_MP_ACQ__KML_20260625T150000_20260713T180000.kml",
    "outbox/S2B_MP_ACQ__KML_20260618T120000_20260706T150000.kml",
    "outbox/S2C_MP_ACQ__KML_20260625T120000_20260713T150000.kml"
]


# ==========================================
# 2. ELABORAZIONE SPAZIALE (IL TUO ALGORITMO)
# ==========================================
def executer_calculs_spatial():
    print("🔄 Caricamento del file AOI...")
    # Crea un percorso automatico verso la cartella outbox basato sulla posizione dello script
   # Cerca automaticamente qualsiasi file che inizia con 'MP_ACQPLAN' nella cartella outbox
    import glob
    cartella_outbox = os.path.join(os.path.dirname(__file__), "outbox")
    pattern_ricerca = os.path.join(cartella_outbox, "MP_ACQPLAN_*.json")
    file_trovati = glob.glob(pattern_ricerca)

    if not file_trovati:
        raise FileNotFoundError("Nessun file MP_ACQPLAN trovato nella cartella outbox!")
    
    # Prende il primo file corrispondente trovato
    percorso_completo = file_trovati[0]
    print(f"📂 File AOI rilevato automaticamente: {os.path.basename(percorso_completo)}")
    
    aoi = gpd.read_file(percorso_completo)
    if aoi.crs != "EPSG:4326":
        aoi = aoi.to_crs("EPSG:4326")
    aoi_geom = aoi.geometry.iloc[0]

    namespaces = {"kml": "http://www.opengis.net/kml/2.2"}
    extracted_records = []
    orbite_target = set()

    # Inizializzazione del KML di output per la visualizzazione
    kml_output = simplekml.Kml()

    # Disegno dell'AOI in rosso nel file KML
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

    # Fase 1: Estrazione dei dati temporali e calcolo delle intersezioni
    for kml_file in KML_FILES:
        print(f"📅 Analisi del file KML: {os.path.basename(kml_file)}...")
        try:
            tree = ET.parse(kml_file)
            root = tree.getroot()
        except Exception:
            continue

        for placemark in root.findall(".//kml:Placemark", namespaces):
            name_text = (
                placemark.find("kml:name", namespaces).text
                if placemark.find("kml:name", namespaces) is not None
                else ""
            )
            orbit_rel = "Sconosciuta"
            orbit_num_kml = None
            start_time = "Sconosciuto"

            for data in placemark.findall(".//kml:Data", namespaces):
                data_name = data.get("name")
                value_node = data.find("kml:value", namespaces)
                if value_node is not None:
                    if data_name == "OrbitRelative":
                        orbit_num_kml = int(value_node.text)
                        orbit_rel = f"R{orbit_num_kml:03d}"
                    elif data_name == "ObservationTimeStart":
                        start_time = value_node.text

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

                        # Verifica dell'intersezione reale con l'AOI
                        if sat_poly.intersects(aoi_geom):
                            orbite_target.add(orbit_num_kml)

                            # Identificazione del satellite specifico
                            if name_text.endswith("-1") or "S2A" in name_text:
                                sat_type = "Sentinel-2A"
                            elif name_text.endswith("-2") or "S2B" in name_text:
                                sat_type = "Sentinel-2B"
                            elif name_text.endswith("-3") or "S2C" in name_text:
                                sat_type = "Sentinel-2C"
                            else:
                                sat_type = "Sentinel-2"

                            extracted_records.append(
                                {
                                    "date_start": start_time,
                                    "relative_orbit": orbit_rel,
                                    "satellite": sat_type,
                                    "status": "Scheduled",
                                }
                            )

                            # Ritaglio geometrico rigoroso delle orbite sopra l'AOI
                            poly_ritagliato = sat_poly.intersection(aoi_geom)
                            if (
                                not poly_ritagliato.is_empty
                                and poly_ritagliato.geom_type
                                in ["Polygon", "MultiPolygon"]
                            ):
                                poligoni_da_disegnare = (
                                    [poly_ritagliato]
                                    if poly_ritagliato.geom_type == "Polygon"
                                    else list(poly_ritagliato.geoms)
                                )
                                for p in poligoni_da_disegnare:
                                    pol_sat = kml_output.newpolygon(
                                        name=f"{orbit_rel} | {start_time[:16]}"
                                    )
                                    pol_sat.outerboundaryis = list(
                                        p.exterior.coords
                                    )
                                    pol_sat.style.linestyle.color = (
                                        simplekml.Color.lime
                                    )
                                    pol_sat.style.linestyle.width = 2
                                    pol_sat.style.polystyle.color = (
                                        simplekml.Color.changealphaint(
                                            50, simplekml.Color.lime
                                        )
                                    )
                    except Exception:
                        continue

    # Ordinamento cronologico dei record (dal più recente)
    def estrai_data(record):
        try:
            return datetime.fromisoformat(record["date_start"])
        except:
            return datetime.min

    extracted_records.sort(key=estrai_data)

    # ==========================================
    # 3. GENERAZIONE DEI 5 COMPONENTI PER IL FOS
    # ==========================================
    print("📝 Generazione dei file richiesti dal sistema FOS...")

    # File 1: FOS_PLFILE.json (Pianificazione)
    output_plfile = {
        "aoi_reference": "La mia Zona di Interesse (AOI)",
        "planned_acquisitions": extracted_records,
    }
    with open("FOS_PLFILE.json", "w", encoding="utf-8") as f:
        json.dump(output_plfile, f, indent=2, ensure_ascii=False)

    # File 2: FOS_ACFILE.kml (Ritaglio geometrico finale delle acquisizioni)
    kml_output.save("FOS_ACFILE.kml")

    # File 3, 4, 5: Generazione dei file dummy nominali per test di architettura
    with open("FOS_ORFILE.json", "w") as f:
        json.dump(
            {
                "info": "File orbite (Modello temporaneo)",
                "total_orbits_monitored": len(orbite_target),
            },
            f,
        )
    with open("FOS_UNFILE.json", "w") as f:
        json.dump({"info": "Nessuna indisponibilità rilevata"}, f)
    with open("FOS_TMFILE.json", "w") as f:
        json.dump({"status": "Telemetria nominale"}, f)

    print("✅ File locali generati correttamente!")


# ==========================================
# 4. FLUSSO DI RETE (CONNESSIONE SFTP & PUSH)
# ==========================================
def avvia_modulo_fos():
    print("🔌 Connessione al server SFTP in corso...")
    transport = paramiko.Transport((SFTP_HOST, SFTP_PORT))
    try:
        transport.connect(username=SFTP_USER, password=SFTP_PASSWORD)
        sftp = paramiko.SFTPClient.from_transport(transport)
    except Exception as e:
        print(f"❌ Impossibile connettersi al server SFTP: {e}")
        return

    try:
        print("📥 Controllo della cartella Outbox sul server SFTP...")
        
        # 1. Elenca i file sul server nella cartella outbox
        file_remoti = sftp.listdir("mp/plans/outbox/")
        
        # 2. Cerca un file che inizia con MP_ACQPLAN
        file_aoi_remoto = None
        for file in file_remoti:
            if file.startswith("MP_ACQPLAN_") and file.endswith(".json"):
                file_aoi_remoto = file
                break
        
        if file_aoi_remoto:
            print(f"🎯 Trovato nuovo piano sul server: {file_aoi_remoto}")
            
            # Definiamo dove salvarlo localmente (nella nostra cartella outbox locale)
            percorso_locale = os.path.join(os.path.dirname(__file__), "outbox", file_aoi_remoto)
            
            # Scarichiamo il file vero dal server!
            sftp.get(f"mp/plans/outbox/{file_aoi_remoto}", percorso_locale)
            print(f"➡️ File {file_aoi_remoto} scaricato con successo in locale.")
        else:
            print("📭 Nessun nuovo piano di acquisizione trovato sul server. Attesa...")
            return # Si ferma qui se non c'è nulla da elaborare

        # Esecuzione del motore di calcolo spaziale
        executer_calculs_spatial()

        # Invio dei file rinominati ESATTAMENTE secondo le specifiche del tutor (senza estensione)
        print("📤 Invio dei 5 file di risposta sul server...")
        sftp.put("FOS_PLFILE.json", "mp/plans/inbox/FOS_PLFILE")
        sftp.put("FOS_ORFILE.json", "mp/orbits/FOS_ORFILE")
        sftp.put("FOS_UNFILE.json", "mp/unavailabilities/FOS_UNFILE")
        sftp.put("FOS_TMFILE.json", "delivery/telemetries/FOS_TMFILE")
        sftp.put("FOS_ACFILE.kml", "delivery/acquisitions/FOS_ACFILE")

        print(
            "🚀 [FOS SOFTWARE] SUCCESS: Tutti i file sono stati depositati nelle rispettive cartelle!"
        )

    except Exception as e:
        print(f"❌ Errore durante il processo FOS: {e}")
    finally:
        sftp.close()
        transport.close()


# ==========================================
# 5. BLOCCO DI AVVIO REALE DELLO SCRIPT
# ==========================================
if __name__ == "__main__":
    print("🎬 Script FOS.py avviato correttamente dal terminale...")
    avvia_modulo_fos()