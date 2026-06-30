import glob
import json
import os
import time
import xml.etree.ElementTree as ET
from datetime import datetime
import geopandas as gpd
import paramiko
import simplekml
from shapely.geometry import MultiPolygon, Polygon

# ==========================================
# 1. CONFIGURAZIONE CONNESSIONE E PARAMETRI
# ==========================================
SFTP_HOST = "10.0.1.219"
SFTP_PORT = 22
SFTP_USER = "sftpuser"
SFTP_PASSWORD = "password"

# File KML sorgente (devono essere presenti nella cartella outbox locale)
KML_FILES = [
    "outbox/S2A_MP_ACQ__KML_20260625T150000_20260713T180000.kml",
    "outbox/S2B_MP_ACQ__KML_20260618T120000_20260706T150000.kml",
    "outbox/S2C_MP_ACQ__KML_20260625T120000_20260713T150000.kml",
]

# Memoria di stato per evitare di rielaborare le stesse richieste
FILE_GIA_ELABORATI = set()


# ==========================================
# 2. IL CUORE DEL FOS: PIPELINE DI ELABORAZIONE
# ==========================================
def esegui_pipeline_fos(percorso_file_aoi, timestamp_id):
    """Esegue in sequenza il filtrage temporale e la découpe geometrica per una specifica richiesta."""
    print(f"\n🚀 [PIPELINE] Avvio elaborazione per ID: {timestamp_id}")

    # ----------------------------------------------------
    # FASE A: Caricamento AOI e Filtraggio Temporale (Futuro)
    # ----------------------------------------------------
    try:
        aoi = gpd.read_file(percorso_file_aoi)
        if aoi.crs != "EPSG:4326":
            aoi = aoi.to_crs("EPSG:4326")
        aoi_geom = aoi.geometry.iloc[0]
    except Exception as e:
        print(f"❌ Errore critico nel caricamento dell'AOI: {e}")
        return False

    namespaces = {"kml": "http://www.opengis.net/kml/2.2"}
    extracted_records = []
    orbite_target = set()
    adesso = datetime.now()

    # Inizializziamo il KML per il ritaglio geometrico
    kml_output = simplekml.Kml()

    # Disegniamo l'AOI in rosso nel KML di output
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

    # Scansione dei file KML dei satelliti
    for kml_file in KML_FILES:
        if not os.path.exists(kml_file):
            print(f"⚠️ File KML mancante: {kml_file}. Salto.")
            continue

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

            # ⏱️ FILTRO TEMPORALE: Solo date presenti o future
            try:
                data_orbita = datetime.fromisoformat(start_time)
            except Exception:
                continue

            if data_orbita < adesso:
                continue  # Salta le orbite passate

            # ----------------------------------------------------
            # FASE B: Controllo Intersezione e Taglio Geometrico
            # ----------------------------------------------------
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

                        # Verifica sovrapposizione reale con la nostra area (AOI)
                        if sat_poly.intersects(aoi_geom):
                            orbite_target.add(orbit_num_kml)

                            # Identificazione del satellite specifico
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
                                sat_type = "Sentinel-2"

                            # Aggiungiamo il record per il JSON
                            extracted_records.append(
                                {
                                    "date_start": start_time,
                                    "relative_orbit": orbit_rel,
                                    "satellite": sat_type,
                                    "status": "Scheduled",
                                }
                            )

                            # Eseguiamo il taglio geometrico immediato (Intersection)
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

    # Ordinamento dei record estratti per data cronologica
    def estrai_data(record):
        try:
            return datetime.fromisoformat(record["date_start"])
        except:
            return datetime.min

    extracted_records.sort(key=estrai_data)

    # ----------------------------------------------------
    # FASE C: Scrittura dei File Locali per questa Richiesta
    # ----------------------------------------------------
    print("📝 Generazione locale dei file di risposta...")

    # 1. JSON Pianificazione
    output_plfile = {
        "aoi_reference": f"Pianificazione associata a {os.path.basename(percorso_file_aoi)}",
        "planned_acquisitions": extracted_records,
    }
    with open("FOS_PLFILE.json", "w", encoding="utf-8") as f:
        json.dump(output_plfile, f, indent=2, ensure_ascii=False)

    # 2. KML Ritagliato
    kml_output.save("FOS_ACFILE.kml")

    # 3, 4, 5. File strutturali nominali richiesti dal FOS
    with open("FOS_ORFILE.json", "w") as f:
        json.dump(
            {
                "info": "File orbite nominali",
                "total_orbits_monitored": len(orbite_target),
            },
            f,
        )
    with open("FOS_UNFILE.json", "w") as f:
        json.dump({"info": "Nessuna indisponibilità rilevata"}, f)
    with open("FOS_TMFILE.json", "w") as f:
        json.dump({"status": "Telemetria nominale"}, f)

    print(
        f"✅ Elaborazione completata con successo ({len(extracted_records)} acquisizioni future trovate)."
    )
    return True


# ==========================================
# 3. CONTROLLO RETE SFTP E FLUSSO CONTINUO
# ==========================================
def gestisci_connessione_sftp():
    transport = paramiko.Transport((SFTP_HOST, SFTP_PORT))
    try:
        transport.connect(username=SFTP_USER, password=SFTP_PASSWORD)
        sftp = paramiko.SFTPClient.from_transport(transport)
    except Exception as e:
        print(f"❌ Errore di connessione SFTP: {e}")
        return

    try:
        # Elenca i file pronti nella cartella remota del collega
        file_remoti = sftp.listdir("mp/plans/outbox/")
        file_aoi_remoto = None

        for file in file_remoti:
            if file.startswith("MP_ACQPLAN_") and file.endswith(".json"):
                if file not in FILE_GIA_ELABORATI:
                    file_aoi_remoto = file
                    break

        if file_aoi_remoto:
            print(f"\n🎯 [NUOVA RICHIESTA] Rilevato file: {file_aoi_remoto}")

            # Estrazione dell'ID unico (Timestamp) dal nome file
            timestamp_id = file_aoi_remoto[11:-5]
            if not timestamp_id:
                timestamp_id = datetime.now().strftime("%Y%m%d_%H%M%S")

            # Download locale temporaneo del file AOI specifico
            percorso_locale = os.path.join(
                os.path.dirname(__file__), "outbox", file_aoi_remoto
            )
            sftp.get(f"mp/plans/outbox/{file_aoi_remoto}", percorso_locale)

            # Esecuzione immediata della Pipeline (Analisi + Taglio) su questo file
            successo = esegui_pipeline_fos(percorso_locale, timestamp_id)

            if successo:
                # Caricamento dei 5 file sul server con nomi unici accoppiati alla richiesta
                print(
                    f"📤 Invio dei 5 file di output associati all'ID {timestamp_id}..."
                )
                sftp.put(
                    "FOS_PLFILE.json", f"mp/plans/inbox/FOS_PLFILE_{timestamp_id}"
                )
                sftp.put("FOS_ORFILE.json", f"mp/orbits/FOS_ORFILE_{timestamp_id}")
                sftp.put(
                    "FOS_UNFILE.json",
                    f"mp/unavailabilities/FOS_UNFILE_{timestamp_id}",
                )
                sftp.put(
                    "FOS_TMFILE.json",
                    f"delivery/telemetries/FOS_TMFILE_{timestamp_id}",
                )
                sftp.put(
                    "FOS_ACFILE.kml",
                    f"delivery/acquisitions/FOS_ACFILE_{timestamp_id}",
                )

                # Registriamo il successo per non farlo mai più due volte
                FILE_GIA_ELABORATI.add(file_aoi_remoto)
                print(
                    f"🏁 [COMPLETATO] Tutti i file per l'ID {timestamp_id} sono online. Richiesta chiusa."
                )
        else:
            print("📭 Nessun nuovo piano da elaborare sul server. Riposo...")

    except Exception as e:
        print(f"❌ Errore durante il ciclo SFTP: {e}")
    finally:
        sftp.close()
        transport.close()


# ==========================================
# 4. BLOCCO DI AVVIO DEL DEMONE CONTINUO
# ==========================================
if __name__ == "__main__":
    print("🛸 =================================================== 🛸")
    print("    FOS INTEGRATED MASTER SYSTEM - PRONTO PER IL WORKFLOW")
    print("🛸 =================================================== 🛸")
    print("Il sistema controllerà il server ogni 10 secondi in ciclo continuo.\n")

    while True:
        try:
            print(
                f"\n🕒 [{datetime.now().strftime('%H:%M:%S')}] Inizio controllo remoto..."
            )
            gestisci_connessione_sftp()
        except Exception as e:
            print(f"⚠️ Errore critico nel ciclo principale: {e}")

        time.sleep(10)