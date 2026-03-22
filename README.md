# 🍄 BC Morel Map 2026

An interactive map of potential **Morel mushroom foraging zones** in British Columbia, based on 2025 wildfire burn perimeters.

> **Live Map →** [https://mengjun74.github.io/bc-morel-map/](https://mengjun74.github.io/bc-morel-map/)

![Folium](https://img.shields.io/badge/Folium-Interactive_Map-green)
![Python](https://img.shields.io/badge/Python-3.11-blue)
![GitHub Actions](https://img.shields.io/badge/CI-GitHub_Actions-orange)

---

## 📖 About

Morel mushrooms are known to fruit prolifically in areas burned by wildfire the previous year. This project automatically fetches **BC government wildfire data** via WFS (Web Feature Service), removes protected areas (parks & ecological reserves), and generates a beautiful interactive map for the upcoming foraging season.

### Key Features

- 🔥 **2025 Burn Zones** — Wildfire perimeters (>20 ha) highlighted as potential morel habitat
- 🚫 **No-Go Zones** — BC Parks & Ecological Reserves overlaid so you know where foraging is prohibited
- 🛰️ **Satellite & Street Views** — Toggle between Esri satellite imagery and OpenStreetMap
- 📍 **GPS Location** — Locate yourself on the map with one tap
- 🔍 **City Search** — Quickly jump to 24 BC cities and towns
- 📱 **Mobile Friendly** — Fully responsive, works great on phones in the field

---

## 🚀 How It Works

1. **Data Fetching** — Queries BC's [Open Maps WFS](https://openmaps.gov.bc.ca/) for current and historical 2025 fire polygons, plus parks/reserves
2. **Geometry Processing** — Subtracts protected areas from burn zones; simplifies geometries to keep file sizes manageable
3. **Map Generation** — Builds an interactive [Folium](https://python-visualization.github.io/folium/) map with styled layers, popups, and controls
4. **Auto Deployment** — A GitHub Actions workflow regenerates the map **every Monday** and pushes the updated `index.html` to the repo

---

## 🛠️ Local Setup

```bash
# Clone the repo
git clone https://github.com/Mengjun74/bc-morel-map.git
cd bc-morel-map

# Install dependencies
pip install -r requirements.txt

# Generate the map
python main.py

# Open index.html in your browser
```

### Requirements

- Python 3.11+
- Dependencies: `geopandas`, `folium`, `requests`, `pyogrio`, `rtree`

---

## ⚙️ GitHub Actions

The workflow at `.github/workflows/update_map.yml` runs automatically:

| Trigger | Schedule |
|---|---|
| Scheduled | Every Monday at 00:00 UTC |
| Manual | `workflow_dispatch` (run anytime) |

It regenerates `index.html` and commits it back to the repo. Enable **GitHub Pages** (from the `main` branch) to serve the map publicly.

---

## ⚠️ Disclaimers

- 🚫 **Foraging in National/Provincial Parks is illegal** — always check boundaries
- 🌲 **Danger Trees** — burned forests have hazardous standing dead trees; use extreme caution
- 📋 **Local Closures** — always check for area closures and active fire zones before heading out
- 📊 **Data is for reference only** — verify conditions on the ground

---

## 📄 License

This project is licensed under the MIT License - see the [LICENSE](LICENSE) file for details. Data sourced from [BC Open Maps](https://openmaps.gov.bc.ca/).
