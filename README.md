# Sentinel-2 Geospatial Data Pre-processing Pipeline

## Project Overview
This repository contains a Python-based **Data Engineering & Geospatial pipeline** developed during my 3rd-year engineering internship. It simulates a Flight Operations Segment (FOS) by automating the ingestion, spatial filtering, and preprocessing of **Sentinel-2 satellite acquisition plans** before feeding them into downstream analysis or AI models.

---

##  Data Pipeline Architecture
The pipeline operates automated end-to-end data processing:

1. **Data Ingestion (SFTP):** Securely connects to a remote server using `Paramiko` to automate the download of user-defined Areas of Interest (AOI) in JSON format.
2. **Geospatial Parsing:** Reads and extracts complex orbital trajectory data from raw mission files (XML/KML formats).
3. **Spatial Analysis & Intersection (Core Data Science):** * Converts coordinates into geometric structures.
   * Utilizes `GeoPandas` and `Shapely` to perform real-time geometric intersections.
   * Filters and crops satellite orbit tracks *exactly* over the targeted AOI footprint.
4. **Data Delivery:** Generates 5 nominal mission control files (including spatial sub-sets in KML format) and uploads them back to the server.

---

##Tech Stack & Libraries
* **Language:** Python 3
* **Geospatial Processing:** `geopandas`, `shapely`, `simplekml`
* **Network & Parsing:** `paramiko`, `xml.etree.ElementTree`

---

## Key Takeaways for Data Science
* **Data Wrangling:** Cleaned, parsed, and structured inconsistent raw satellite telemetry data.
* **Geospatial Expertise:** Mastered vector data manipulation (Polygons, LineStrings) and coordinate alignment, a critical skill for Earth Observation and Satellite AI modeling.
* **Production-Ready Code:** Developed automated workflows handling edge cases (such as zero-intersection detection).
