"""
BC Morel Map — Generator
Queries BC WFS for recent wildfire burn perimeters, excludes protected areas,
and produces an interactive Folium map saved as index.html.
"""

import sys
import logging
from datetime import datetime, timezone

import requests
import geopandas as gpd
import folium
from folium.plugins import LocateControl, Search

# ---------------------------------------------------------------------------
# Configuration
# ---------------------------------------------------------------------------

WFS_BASE = "https://openmaps.gov.bc.ca/geo/pub/wfs"
OUTPUT_FILE = "index.html"
PAGE_SIZE = 5000  # WFS pagination size
GEOMETRY_SIMPLIFY_TOLERANCE = 0.001  # degrees, for file-size reduction

# WFS layer names
CURRENT_FIRES = "pub:WHSE_LAND_AND_NATURAL_RESOURCE.PROT_CURRENT_FIRE_POLYS_SP"
HISTORICAL_FIRES = "pub:WHSE_LAND_AND_NATURAL_RESOURCE.PROT_HISTORICAL_FIRE_POLYS_SP"
PARKS_ECORES = "pub:WHSE_TANTALIS.TA_PARK_ECORES_PA_SVW"

logging.basicConfig(level=logging.INFO, format="%(levelname)s  %(message)s")
log = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# WFS helpers
# ---------------------------------------------------------------------------

def fetch_wfs_geojson(type_name: str, cql_filter: str | None = None,
                      max_features: int | None = None,
                      page_size: int | None = None) -> dict | None:
    """Fetch GeoJSON from BC WFS with pagination and optional CQL filter.
    Some BC WFS layers reject startIndex=0, so we only include it after first page."""
    all_features: list[dict] = []
    start_index = 0
    chunk = page_size or PAGE_SIZE

    while True:
        params = {
            "service": "WFS",
            "version": "2.0.0",
            "request": "GetFeature",
            "typeName": type_name,
            "outputFormat": "json",
            "srsName": "EPSG:4326",
            "count": str(chunk),
        }
        # Some layers reject startIndex=0; only include for subsequent pages
        if start_index > 0:
            params["startIndex"] = str(start_index)
        if cql_filter:
            params["CQL_FILTER"] = cql_filter

        log.info("WFS request: %s  startIndex=%d", type_name.split(".")[-1], start_index)
        try:
            resp = requests.get(WFS_BASE, params=params, timeout=180)
            resp.raise_for_status()
            data = resp.json()
        except Exception as exc:
            log.warning("WFS fetch failed for %s: %s", type_name, exc)
            return None

        features = data.get("features", [])
        if not features:
            break

        all_features.extend(features)
        log.info("  → received %d features (total so far: %d)", len(features), len(all_features))

        if max_features and len(all_features) >= max_features:
            all_features = all_features[:max_features]
            break

        if len(features) < chunk:
            break  # Last page

        start_index += chunk

    if not all_features:
        return None

    return {
        "type": "FeatureCollection",
        "features": all_features,
    }


def geojson_to_gdf(geojson: dict | None) -> gpd.GeoDataFrame | None:
    """Convert a GeoJSON dict to a GeoDataFrame, returning None if empty."""
    if geojson is None or not geojson.get("features"):
        return None
    gdf = gpd.GeoDataFrame.from_features(geojson, crs="EPSG:4326")
    return gdf if not gdf.empty else None


# ---------------------------------------------------------------------------
# Data acquisition
# ---------------------------------------------------------------------------

def fetch_burn_data(target_year: int) -> gpd.GeoDataFrame | None:
    """Fetch wildfire burn perimeters (>20 ha) for a specific year from both current and
    historical fire layers, merge, and deduplicate."""

    # Current fires — field is FIRE_SIZE_HECTARES, FIRE_YEAR
    current_cql = f"FIRE_YEAR={target_year} AND FIRE_SIZE_HECTARES>20"
    current_gj = fetch_wfs_geojson(CURRENT_FIRES, cql_filter=current_cql)
    gdf_current = geojson_to_gdf(current_gj)
    if gdf_current is not None:
        log.info("Current fires (%d): %d features", target_year, len(gdf_current))
    else:
        log.info("Current fires (%d): 0 features (or unavailable)", target_year)

    # Historical fires — also uses FIRE_SIZE_HECTARES, FIRE_YEAR
    hist_cql = f"FIRE_YEAR={target_year} AND FIRE_SIZE_HECTARES>20"
    hist_gj = fetch_wfs_geojson(HISTORICAL_FIRES, cql_filter=hist_cql)
    gdf_hist = geojson_to_gdf(hist_gj)
    if gdf_hist is not None:
        log.info("Historical fires (%d): %d features", target_year, len(gdf_hist))
    else:
        log.info("Historical fires (%d): 0 features (or unavailable)", target_year)

    # Merge
    frames = [f for f in [gdf_current, gdf_hist] if f is not None]
    if not frames:
        return None

    burns = gpd.GeoDataFrame(
        __import__("pandas").concat(frames, ignore_index=True), crs="EPSG:4326"
    )

    # Deduplicate by FIRE_NUMBER if available
    if "FIRE_NUMBER" in burns.columns:
        before = len(burns)
        burns = burns.drop_duplicates(subset="FIRE_NUMBER", keep="first")
        log.info("Deduplicated fires (%d): %d → %d", target_year, before, len(burns))

    return burns if not burns.empty else None


def fetch_parks() -> gpd.GeoDataFrame | None:
    """Fetch BC Parks, Ecological Reserves, and Protected Areas."""
    gj = fetch_wfs_geojson(PARKS_ECORES)
    gdf = geojson_to_gdf(gj)
    if gdf is not None:
        log.info("Parks / Ecological Reserves: %d features", len(gdf))
    return gdf


# ---------------------------------------------------------------------------
# Geometry processing
# ---------------------------------------------------------------------------

def subtract_parks(burns: gpd.GeoDataFrame,
                   parks: gpd.GeoDataFrame | None) -> gpd.GeoDataFrame:
    """Remove park areas from burn polygons using overlay difference."""
    if parks is None or parks.empty:
        log.info("No park data — skipping overlay subtraction")
        return burns

    log.info("Subtracting parks from burn zones …")
    try:
        safe = gpd.overlay(burns, parks, how="difference", keep_geom_type=True)
        log.info("Overlay result: %d polygons", len(safe))
        return safe if not safe.empty else burns
    except Exception as exc:
        log.warning("Overlay failed (%s) — returning raw burns", exc)
        return burns


def simplify_geometries(gdf: gpd.GeoDataFrame,
                        tolerance: float = GEOMETRY_SIMPLIFY_TOLERANCE) -> gpd.GeoDataFrame:
    """Simplify polygon geometries to reduce HTML file size."""
    gdf = gdf.copy()
    gdf["geometry"] = gdf["geometry"].simplify(tolerance, preserve_topology=True)
    return gdf


# ---------------------------------------------------------------------------
# Map building
# ---------------------------------------------------------------------------

BC_CITIES = [
    {"name": "Vancouver", "lat": 49.2827, "lon": -123.1207},
    {"name": "Victoria", "lat": 48.4284, "lon": -123.3656},
    {"name": "Kelowna", "lat": 49.8880, "lon": -119.4960},
    {"name": "Kamloops", "lat": 50.6745, "lon": -120.3273},
    {"name": "Prince George", "lat": 53.9171, "lon": -122.7497},
    {"name": "Nanaimo", "lat": 49.1659, "lon": -123.9401},
    {"name": "Penticton", "lat": 49.4991, "lon": -119.5937},
    {"name": "Vernon", "lat": 50.2671, "lon": -119.2720},
    {"name": "Cranbrook", "lat": 49.5097, "lon": -115.7689},
    {"name": "Nelson", "lat": 49.4928, "lon": -117.2948},
    {"name": "Williams Lake", "lat": 52.1417, "lon": -122.1417},
    {"name": "Quesnel", "lat": 52.9784, "lon": -122.4927},
    {"name": "Terrace", "lat": 54.5164, "lon": -128.5997},
    {"name": "Smithers", "lat": 54.7804, "lon": -127.1743},
    {"name": "Fort St. John", "lat": 56.2465, "lon": -120.8476},
    {"name": "Dawson Creek", "lat": 55.7596, "lon": -120.2353},
    {"name": "Prince Rupert", "lat": 54.3150, "lon": -130.3208},
    {"name": "Revelstoke", "lat": 51.0000, "lon": -118.1957},
    {"name": "Golden", "lat": 51.2990, "lon": -116.9676},
    {"name": "Merritt", "lat": 50.1113, "lon": -120.7862},
    {"name": "Lytton", "lat": 50.2316, "lon": -121.5822},
    {"name": "100 Mile House", "lat": 51.6418, "lon": -121.2930},
    {"name": "Burns Lake", "lat": 54.2310, "lon": -125.7601},
    {"name": "Vanderhoof", "lat": 54.0166, "lon": -124.0076},
]


def build_map(burns_t1: gpd.GeoDataFrame | None,
              burns_t2: gpd.GeoDataFrame | None,
              parks: gpd.GeoDataFrame | None,
              current_year: int) -> folium.Map:
    """Build the interactive Folium map with all layers and controls."""

    # --- Base map -----------------------------------------------------------
    m = folium.Map(
        location=[54.5, -125.5],
        zoom_start=5,
        tiles=None,
        control_scale=True,
    )

    # Satellite (default)
    folium.TileLayer(
        tiles="https://server.arcgisonline.com/ArcGIS/rest/services/World_Imagery/MapServer/tile/{z}/{y}/{x}",
        attr="Esri",
        name="🛰️ Satellite",
        overlay=False,
    ).add_to(m)

    # OpenStreetMap
    folium.TileLayer(
        tiles="openstreetmap",
        name="🗺️ OpenStreetMap",
        overlay=False,
    ).add_to(m)

    # --- Parks / No-Go Zones -----------------------------------------------
    if parks is not None and not parks.empty:
        parks_simple = simplify_geometries(parks, tolerance=0.005)
        parks_group = folium.FeatureGroup(name="🚫 No-Go Zones (Parks & Reserves)")

        def park_style(feature):
            return {
                "fillColor": "#8B0000",
                "color": "#2d0000",
                "weight": 1,
                "fillOpacity": 0.35,
                "dashArray": "5 5",
            }

        park_name_col = "PROTECTED_LANDS_NAME" if "PROTECTED_LANDS_NAME" in parks_simple.columns else None

        folium.GeoJson(
            parks_simple,
            style_function=park_style,
            tooltip=folium.GeoJsonTooltip(
                fields=[park_name_col] if park_name_col else [],
                aliases=["Area:"] if park_name_col else [],
                sticky=True,
            ),
            name="Parks",
        ).add_to(parks_group)
        parks_group.add_to(m)

    # --- Burn Sites T-1 --------------------------------------------------------
    if burns_t1 is not None and not burns_t1.empty:
        burns_simple_t1 = simplify_geometries(burns_t1)
        burns_group_t1 = folium.FeatureGroup(name=f"🔥 {current_year - 1} Burn Sites (Best Morel Zones)")

        def burn_style_t1(feature):
            return {
                "fillColor": "#FF8C00",
                "color": "#CC5500",
                "weight": 2,
                "fillOpacity": 0.6,
            }

        # Build popup fields dynamically based on actual WFS schema
        popup_fields_t1 = []
        popup_aliases_t1 = []
        field_map = {
            "FIRE_NUMBER": "Fire #:",
            "FIRE_LABEL": "Fire Label:",
            "FIRE_SIZE_HECTARES": "Size (Ha):",
            "FIRE_DATE": "Fire Date:",
            "TRACK_DATE": "Track Date:",
            "FIRE_STATUS": "Status:",
            "FIRE_CAUSE": "Cause:",
            "SOURCE": "Source:",
        }
        for col, alias in field_map.items():
            if col in burns_simple_t1.columns:
                popup_fields_t1.append(col)
                popup_aliases_t1.append(alias)

        folium.GeoJson(
            burns_simple_t1,
            style_function=burn_style_t1,
            tooltip=folium.GeoJsonTooltip(
                fields=popup_fields_t1[:3] if popup_fields_t1 else [],
                aliases=popup_aliases_t1[:3] if popup_aliases_t1 else [],
                sticky=True,
            ),
            popup=folium.GeoJsonPopup(
                fields=popup_fields_t1 if popup_fields_t1 else [],
                aliases=popup_aliases_t1 if popup_aliases_t1 else [],
                labels=True,
                localize=True,
            ),
            name=f"{current_year - 1} Burn Sites",
        ).add_to(burns_group_t1)
        burns_group_t1.add_to(m)

    # --- Burn Sites T-2 --------------------------------------------------------
    if burns_t2 is not None and not burns_t2.empty:
        burns_simple_t2 = simplify_geometries(burns_t2)
        burns_group_t2 = folium.FeatureGroup(name=f"🔥 {current_year - 2} Burn Sites (Secondary Zones)")

        def burn_style_t2(feature):
            return {
                "fillColor": "#FFD700",
                "color": "#B8860B",
                "weight": 2,
                "fillOpacity": 0.5,
            }

        # Build popup fields dynamically based on actual WFS schema
        popup_fields_t2 = []
        popup_aliases_t2 = []
        for col, alias in field_map.items():
            if col in burns_simple_t2.columns:
                popup_fields_t2.append(col)
                popup_aliases_t2.append(alias)

        folium.GeoJson(
            burns_simple_t2,
            style_function=burn_style_t2,
            tooltip=folium.GeoJsonTooltip(
                fields=popup_fields_t2[:3] if popup_fields_t2 else [],
                aliases=popup_aliases_t2[:3] if popup_aliases_t2 else [],
                sticky=True,
            ),
            popup=folium.GeoJsonPopup(
                fields=popup_fields_t2 if popup_fields_t2 else [],
                aliases=popup_aliases_t2 if popup_aliases_t2 else [],
                labels=True,
                localize=True,
            ),
            name=f"{current_year - 2} Burn Sites",
        ).add_to(burns_group_t2)
        burns_group_t2.add_to(m)

    # --- City markers for search -------------------------------------------
    cities_group = folium.FeatureGroup(name="🏙️ BC Cities", show=False)
    for city in BC_CITIES:
        folium.Marker(
            location=[city["lat"], city["lon"]],
            popup=city["name"],
            tooltip=city["name"],
            icon=folium.Icon(color="gray", icon="info-sign"),
        ).add_to(cities_group)
    cities_group.add_to(m)

    # Search widget bound to cities layer
    Search(
        layer=cities_group,
        geom_type="Point",
        placeholder="Search BC cities…",
        collapsed=True,
        search_label="popup",
    ).add_to(m)

    # --- Controls ----------------------------------------------------------
    LocateControl(
        auto_start=False,
        strings={"title": "📍 My Location"},
    ).add_to(m)
    folium.LayerControl(collapsed=False).add_to(m)

    # --- Disclaimer & title ------------------------------------------------
    generation_time = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M UTC")

    disclaimer_html = f"""
    <div id="map-disclaimer" style="
        position: fixed;
        bottom: 30px; left: 10px;
        z-index: 9999;
        background: rgba(0,0,0,0.82);
        color: #f0f0f0;
        padding: 12px 16px;
        border-radius: 8px;
        font-family: 'Segoe UI', Arial, sans-serif;
        font-size: 12px;
        max-width: 340px;
        line-height: 1.5;
        box-shadow: 0 2px 12px rgba(0,0,0,0.4);
        border-left: 4px solid #FF8C00;
    ">
        <div style="font-size:15px; font-weight:700; margin-bottom:6px; color:#FF8C00;">
            🍄 BC Morel Map — {current_year} Season
        </div>
        <div style="margin-bottom:4px;">
            ⚠️ <strong>Data for reference only.</strong>
        </div>
        <div style="margin-bottom:4px;">
            🚫 Foraging in National Parks is <strong>illegal</strong>.
        </div>
        <div style="margin-bottom:4px;">
            🌲 Watch for <strong>Danger Trees</strong> in burn zones.
        </div>
        <div style="margin-bottom:4px;">
            📋 Check local signage for hazards &amp; closures.
        </div>
        <div style="margin-top:8px; font-size:10px; color:#aaa;">
            Generated: {generation_time}
        </div>
    </div>
    """
    m.get_root().html.add_child(folium.Element(disclaimer_html))

    # Page title
    m.get_root().html.add_child(folium.Element(
        f"<title>BC Morel Map {current_year} — Burn Zone Foraging Guide</title>"
    ))

    return m


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

def main():
    current_year = datetime.now(timezone.utc).year
    log.info("=" * 60)
    log.info("BC Morel Map %d — Generator", current_year)
    log.info("=" * 60)

    # 1. Fetch data
    burns_t1_raw = fetch_burn_data(current_year - 1)
    burns_t2_raw = fetch_burn_data(current_year - 2)
    parks = fetch_parks()

    if burns_t1_raw is None and burns_t2_raw is None:
        log.warning(
            "No burn data available for %d and %d. "
            "The map will be generated with parks only.",
            current_year - 1, current_year - 2
        )

    # 2. Subtract parks from burns
    burns_t1 = subtract_parks(burns_t1_raw, parks) if burns_t1_raw is not None else None
    burns_t2 = subtract_parks(burns_t2_raw, parks) if burns_t2_raw is not None else None

    # 3. Build map
    m = build_map(burns_t1, burns_t2, parks, current_year)

    # 4. Save
    m.save(OUTPUT_FILE)
    log.info("✅  Map saved to %s", OUTPUT_FILE)


if __name__ == "__main__":
    main()
