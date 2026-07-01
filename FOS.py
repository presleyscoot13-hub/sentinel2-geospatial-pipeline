import json
import os
import xml.etree.ElementTree as ET
from datetime import datetime
import io  # Indispensabile per creare file virtuali nella memoria RAM
import glob
import geopandas as gpd
import paramiko
import simplekml
from shapely.geometry import MultiPolygon, Polygon

# ==========================================
# 1. CONFIGURAZIONE DELLE CONNESSIONI E CARTELLE
# ==========================================
SFTP_HOST = "10.0.1.219"
SFTP_PORT = 22
SFTP_USER = "sftpuser"
SFTP_PASSWORD = "password"

# Cartelle sorgenti e di destinazione sul server SFTP
SFTP_REMOTE_DIR = "/mp/plans/outbox/"      # Dove cercare i file MP_ACQPLAN_*.json
DIR_PLFILE = "/mp/plans/inbox"             # Dove inviare i file FOS_PLFILE_*.json
DIR_ORFILE = "/mp/orbits"                  # Dove inviare i file FOS_ORFILE_*.json
DIR_UNFILE = "/mp/unavailabilities"        # Dove inviare i file FOS_UNFILE_*.json
DIR_ACFILE = "/delivery/acquisitions"      # Dove inviare il KML finale visivo (FOS_ACFILE_*.kml)

# La cartella outbox locale contiene solo i KML sorgenti dei satelliti
cartella_outbox = os.path.join(os.path.dirname(__file__), "outbox")


# ==========================================
# 2. FUNZIONI DI UTILITÀ
# ==========================================
def estrai_data(record):
    """Permette di ordinare le acquisizioni in ordine cronologico."""
    try:
        return datetime.fromisoformat(record["date_start"])
    except:
        return datetime.min


# ==========================================
# 3. FASE 1: CONNESSIONE E RICERCA DEI PIANI
# ==========================================
print("Connessione al server SFTP...")
try:
    transport = paramiko.Transport((SFTP_HOST, SFTP_PORT))
    transport.connect(username=SFTP_USER, password=SFTP_PASSWORD)
    sftp = paramiko.SFTPClient.from_transport(transport)
except Exception as e:
    print(f"Errore critico di connessione SFTP: {e}")
    exit()

try:
    sftp.chdir(SFTP_REMOTE_DIR)
    fichiers_distants = sftp.listdir()
    fichiers_aoi_sftp = [f for f in fichiers_distants if f.startswith("MP_ACQPLAN_") and f.endswith(".json")]
except Exception as e:
    print(f"Impossibile leggere la cartella remota {SFTP_REMOTE_DIR}: {e}")
    sftp.close()
    transport.close()
    exit()

if not fichiers_aoi_sftp:
    print("Nessun file MP_ACQPLAN_ trovato sul server SFTP. Fine del programma.")
    sftp.close()
    transport.close()
    exit()

# Ricerca dei file KML sorgenti locali dei satelliti (indispensabili per il ritaglio)
kml_files = glob.glob(os.path.join(cartella_outbox, "*.kml"))
namespaces = {"kml": "http://www.opengis.net/kml/2.2"}
oggi_inizio = datetime.now().replace(hour=0, minute=0, second=0, microsecond=0)

if not kml_files:
    print("Errore: Nessun file KML sorgente (Sentinel) trovato nella cartella 'outbox' locale.")
    sftp.close()
    transport.close()
    exit()


# ==========================================
# 4. CATENA DI ELABORAZIONE 100% IN MEMORIA
# ==========================================
for nom_fichier_aoi in fichiers_aoi_sftp:
    base_aoi_name = os.path.splitext(nom_fichier_aoi)[0]

    print(f"\n==================================================================")
    print(f"ELABORAZIONE IN RAM AVVIATA PER: {nom_fichier_aoi}")
    print(f"==================================================================")

    # Lettura dell'AOI direttamente dal server SFTP nella memoria volatile (RAM)
    try:
        sftp.chdir(SFTP_REMOTE_DIR)
        with sftp.file(nom_fichier_aoi, "r") as f_distant:
            contenu_json = f_distant.read().decode("utf-8")
        
        # Geopandas puo leggere direttamente una stringa JSON grezza
        aoi = gpd.read_file(io.StringIO(contenu_json), driver="GeoJSON")
        if aoi.crs != "EPSG:4326":
            aoi = aoi.to_crs("EPSG:4326")
        aoi_geom = aoi.geometry.iloc[0]
    except Exception as e:
        print(f"Errore durante la lettura in RAM dell'AOI remota: {e}")
        continue

    extracted_records = []
    orbite_target = set()

   # ------------------------------------------------------------------
    # PARTE A: ANALISI DEI KML SATELLITI
    # ------------------------------------------------------------------
    print("Analisi dei KML satelliti...")
    for kml_file in kml_files:
        try:
            tree = ET.parse(kml_file)
            root = tree.getroot()
        except Exception:
            continue

        for placemark in root.findall(".//kml:Placemark", namespaces):
            name_node = placemark.find("kml:name", namespaces)
            name_text = name_node.text if name_node is not None else ""

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

            try:
                # Gestione della stringa ISO rimuovendo eventuale 'Z' finale per consistenza nativa
                clean_start_time = start_time.replace("Z", "")
                data_orbita = datetime.fromisoformat(clean_start_time)
            except:
                continue

            # CRITICO: Salta l'acquisizione se antecedente a oggi a mezzanotte
            if data_orbita < oggi_inizio:
                continue

            poly_node = placemark.find(".//kml:Polygon", namespaces)
            if poly_node is not None:
                coord_node = placemark.find(".//kml:coordinates", namespaces)
                if coord_node is not None:
                    try:
                        coords_text = coord_node.text.strip()
                        coords_list = []
                        for c in coords_text.split():
                            parts = c.split(",")
                            coords_list.append((float(parts[0]), float(parts[1])))

                        sat_poly = Polygon(coords_list)

                        if sat_poly.intersects(aoi_geom):
                            # Inseriamo l'orbita nei target SOLO se il passaggio specifico è futuro/odierno
                            if orbit_num_kml is not None:
                                orbite_target.add(orbit_num_kml)

                            if name_text.endswith("-1") or "S2A" in name_text:
                                sat_type = "Sentinel-2A"
                            elif name_text.endswith("-2") or "S2B" in name_text:
                                sat_type = "Sentinel-2B"
                            elif name_text.endswith("-3") or "S2C" in name_text:
                                sat_type = "Sentinel-2C"
                            else:
                                sat_type = "Sentinel-2"

                            extracted_records.append({
                                "date_start": start_time,
                                "relative_orbit": orbit_rel,
                                "satellite": sat_type,
                                "status": "Scheduled"
                            })
                    except:
                        continue

    extracted_records.sort(key=estrai_data)

    # ------------------------------------------------------------------
    # PARTE B: STREAMING DEI 3 FILE JSON VERSO IL SERVER SFTP
    # ------------------------------------------------------------------
    print("Streaming dei report JSON direttamente sul server...")

    # 1. Caricamento diretto di FOS_PLFILE
    pl_records = [{"date_start": r["date_start"], "satellite": r["satellite"]} for r in extracted_records]
    output_pl = {"aoi_reference": nom_fichier_aoi, "planned_acquisitions": pl_records}
    nom_plfile = f"FOS_PLFILE_{base_aoi_name}.json"
    
    try:
        chemin_distant_pl = os.path.join(DIR_PLFILE, nom_plfile)
        with sftp.file(chemin_distant_pl, "w") as f_distant:
            json.dump(output_pl, f_distant, indent=2, ensure_ascii=False)
        print(f"   Trasferito con successo -> {chemin_distant_pl}")
    except Exception as e:
        print(f"   Errore di trasferimento per PLFILE: {e}")

    # 2. Caricamento diretto di FOS_ORFILE
    or_records = [{"date_start": r["date_start"], "relative_orbit": r["relative_orbit"]} for r in extracted_records]
    output_or = {"aoi_reference": nom_fichier_aoi, "orbits": or_records}
    nom_orfile = f"FOS_ORFILE_{base_aoi_name}.json"
    
    try:
        chemin_distant_or = os.path.join(DIR_ORFILE, nom_orfile)
        with sftp.file(chemin_distant_or, "w") as f_distant:
            json.dump(output_or, f_distant, indent=2, ensure_ascii=False)
        print(f"   Trasferito con successo -> {chemin_distant_or}")
    except Exception as e:
        print(f"   Errore di trasferimento per ORFILE: {e}")

    # 3. Caricamento directo di FOS_UNFILE
    un_records = [{"date_start": r["date_start"], "status": r["status"]} for r in extracted_records]
    output_un = {"aoi_reference": nom_fichier_aoi, "statuses": un_records}
    nom_unfile = f"FOS_UNFILE_{base_aoi_name}.json"
    
    try:
        chemin_distant_un = os.path.join(DIR_UNFILE, nom_unfile)
        with sftp.file(chemin_distant_un, "w") as f_distant:
            json.dump(output_un, f_distant, indent=2, ensure_ascii=False)
        print(f"   Trasferito con successo -> {chemin_distant_un}")
    except Exception as e:
        print(f"   Errore di trasferimento per UNFILE: {e}")

    # ------------------------------------------------------------------
   # ------------------------------------------------------------------
    # PARTE C: RITAGLIO GEOMETRICO E STREAMING DEL FILE KML
    # ------------------------------------------------------------------
    print("Ritaglio geometrico KML in memoria...")
    kml_output = simplekml.Kml()

    coords_aoi = list(aoi_geom.exterior.coords) if aoi_geom.geom_type == "Polygon" else list(aoi_geom.geoms[0].exterior.coords)
    pol_aoi = kml_output.newpolygon(name="La mia AOI")
    pol_aoi.style.linestyle.color = simplekml.Color.red
    pol_aoi.style.linestyle.width = 4
    pol_aoi.style.polystyle.color = simplekml.Color.changealphaint(0, simplekml.Color.red)
    pol_aoi.outerboundaryis = coords_aoi

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

            try:
                clean_start_time = start_time.replace("Z", "")
                data_orbita = datetime.fromisoformat(clean_start_time)
            except:
                continue

            # BLOCCO DI SICUREZZA: Rifiuta categoricamente i poligoni del passato
            if data_orbita < oggi_inizio:
                continue

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
                                coords_list.append((float(parts[0]), float(parts[1])))

                            sat_poly = Polygon(coords_list)

                            if sat_poly.intersects(aoi_geom):
                                poly_ritagliato = sat_poly.intersection(aoi_geom)

                                if poly_ritagliato.is_empty or poly_ritagliato.geom_type not in ["Polygon", "MultiPolygon"]:
                                    continue

                                poligoni_da_disegnare = [poly_ritagliato] if poly_ritagliato.geom_type == "Polygon" else list(poly_ritagliato.geoms)

                                for p in poligoni_da_disegnare:
                                    # Genera l'elemento visivo solo se passa i controlli cronologici
                                    pol_sat = kml_output.newpolygon(name=f"R{orbit_num_kml:03d} | {start_time[:16]}")
                                    pol_sat.outerboundaryis = list(p.exterior.coords)

                                    pol_sat.style.linestyle.color = simplekml.Color.lime
                                    pol_sat.style.linestyle.width = 2
                                    pol_sat.style.polystyle.color = simplekml.Color.changealphaint(50, simplekml.Color.lime)
                        except:
                            continue

    # Trasferimento del KML testuale direttamente tramite la rete SFTP
    nom_kml_final = f"FOS_ACFILE_{base_aoi_name}.kml"
    try:
        kml_string = kml_output.kml()
        chemin_distant_kml = os.path.join(DIR_ACFILE, nom_kml_final)
        with sftp.file(chemin_distant_kml, "w") as f_distant:
            f_distant.write(kml_string)
        print(f"   Trasferito con successo -> {chemin_distant_kml}")
    except Exception as e:
        print(f"   Errore di trasferimento SFTP per il file KML: {e}")

    print(f"[SUCCESSO] Elaborazione 100% Cloud convalidata per {nom_fichier_aoi}!")

# Chiusura delle connessioni
sftp.close()
transport.close()
print("\n[FINE COMPLETA] Il programma e stato eseguito integralmente nella memoria RAM. Nessun file e stato creato sulla macchina locale!")