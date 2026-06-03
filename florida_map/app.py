from flask import Flask, render_template, jsonify, Response
import os
import json
import logging
import threading
import requests as http_requests
import urllib3

urllib3.disable_warnings(urllib3.exceptions.InsecureRequestWarning)

app = Flask(__name__)

STATIC_DIR = os.path.join(os.path.dirname(__file__), 'static')
CACHE_FILE = os.path.join(STATIC_DIR, 'florida_counties.geojson')

# GeoJSON sources – tried in order; all-US datasets are filtered to FL FIPS 12 server-side
GEOJSON_SOURCES = [
    # Plotly datasets – all US counties GeoJSON (well-known, reliably hosted)
    "https://raw.githubusercontent.com/plotly/datasets/master/geojson-counties-fips.json",
    # Census Bureau Cartographic Boundary Files (Florida only, ~2MB)
    "https://www2.census.gov/geo/tiger/GENZ2020/json/cb_2020_12_county_500k.json",
    "https://www2.census.gov/geo/tiger/GENZ2019/json/cb_2019_12_county_500k.json",
    # TIGERweb ArcGIS REST (Census Bureau)
    "https://tigerweb.geo.census.gov/arcgis/rest/services/TIGERweb/State_County/MapServer/1/query"
    "?where=STATE_FIPS%3D'12'&outFields=NAME%2CGEOID&outSR=4326&f=geojson&resultRecordCount=100",
]


def _fetch_geojson(url, timeout=60):
    """Fetch GeoJSON with SSL verification disabled (dev-only, known-good sources)."""
    r = http_requests.get(url, verify=False, timeout=timeout,
                          headers={"User-Agent": "Mozilla/5.0"}, stream=True)
    r.raise_for_status()
    # Stream-read to handle large files
    chunks = []
    for chunk in r.iter_content(chunk_size=1024 * 256):
        chunks.append(chunk)
    return json.loads(b"".join(chunks).decode("utf-8"))


def _download_geojson():
    """Download and cache Florida county GeoJSON at startup."""
    if os.path.exists(CACHE_FILE):
        logging.info("GeoJSON already cached at %s", CACHE_FILE)
        return
    os.makedirs(STATIC_DIR, exist_ok=True)

    for url in GEOJSON_SOURCES:
        try:
            logging.info("Downloading GeoJSON from %s …", url)
            data = _fetch_geojson(url)
            features = data.get("features", [])

            # If this is the full US dataset, filter to Florida (FIPS prefix 12)
            if len(features) > 200:
                features = [
                    f for f in features
                    if str(f.get("id", "")).startswith("12")
                    or str(f.get("properties", {}).get("GEOID", "")).startswith("12")
                ]
                data = {"type": "FeatureCollection", "features": features}

            if len(features) >= 60:
                raw = json.dumps(data)
                with open(CACHE_FILE, "w", encoding="utf-8") as f:
                    f.write(raw)
                logging.info("Cached Florida GeoJSON (%d features) from %s", len(features), url)
                return
        except Exception as exc:
            logging.warning("GeoJSON download failed %s – %s", url, exc)

    logging.error("All GeoJSON sources failed. Counties will not render.")


# Pre-download on startup so the first page load is instant
_download_geojson()

# Florida county data – population, area (sq mi), Medicaid eligibles by age group
# Population/area: Census 2020 / ACS estimates
# Medicaid: FL AHCA "Number of Medicaid Eligibles by Program-Group by County" as of 8/31/2025
#   Age mapping: 0-18  → SOBRA Children + SOBRA Children OP + Med Exp 6-18 + Med Exp <1
#                19-44 → TANF + UP + MN-TANF + Refugee + SOBRA Preg Women + SOBRA PW OP + FP Waiver
#                45-64 → SSI + PMA
#                65+   → Elderly & Disabled + QMB Only + QMB SLMB + MN-SSI + Silver Saver Rx + QMB QI
COUNTY_DATA = {
    "Alachua":      {"pop": 275487,  "area": 874.3,  "medicaid": {"0-18": 18366, "19-44": 10869, "45-64": 8240,   "65+": 4396}},
    "Baker":        {"pop": 29210,   "area": 585.3,  "medicaid": {"0-18": 2903,  "19-44": 1635,  "45-64": 858,    "65+": 568}},
    "Bay":          {"pop": 180075,  "area": 763.8,  "medicaid": {"0-18": 17310, "19-44": 9954,  "45-64": 4286,   "65+": 3895}},
    "Bradford":     {"pop": 28219,   "area": 293.8,  "medicaid": {"0-18": 2835,  "19-44": 1705,  "45-64": 1080,   "65+": 796}},
    "Brevard":      {"pop": 606612,  "area": 1015.9, "medicaid": {"0-18": 40512, "19-44": 24196, "45-64": 14221,  "65+": 12631}},
    "Broward":      {"pop": 1944375, "area": 1209.4, "medicaid": {"0-18": 150528,"19-44": 76110, "45-64": 55452,  "65+": 52740}},
    "Calhoun":      {"pop": 14235,   "area": 567.0,  "medicaid": {"0-18": 1473,  "19-44": 843,   "45-64": 666,    "65+": 413}},
    "Charlotte":    {"pop": 185978,  "area": 680.6,  "medicaid": {"0-18": 10560, "19-44": 6725,  "45-64": 4023,   "65+": 3820}},
    "Citrus":       {"pop": 149657,  "area": 582.9,  "medicaid": {"0-18": 12329, "19-44": 7897,  "45-64": 4359,   "65+": 5378}},
    "Clay":         {"pop": 219252,  "area": 601.3,  "medicaid": {"0-18": 16909, "19-44": 10236, "45-64": 4476,   "65+": 3408}},
    "Collier":      {"pop": 375752,  "area": 2025.2, "medicaid": {"0-18": 22571, "19-44": 10862, "45-64": 5139,   "65+": 4898}},
    "Columbia":     {"pop": 71686,   "area": 797.0,  "medicaid": {"0-18": 8247,  "19-44": 4875,  "45-64": 3155,   "65+": 2614}},
    "DeSoto":       {"pop": 37106,   "area": 639.4,  "medicaid": {"0-18": 3933,  "19-44": 1880,  "45-64": 1064,   "65+": 957}},
    "Dixie":        {"pop": 16422,   "area": 703.8,  "medicaid": {"0-18": 1934,  "19-44": 1191,  "45-64": 729,    "65+": 804}},
    "Duval":        {"pop": 983885,  "area": 763.5,  "medicaid": {"0-18": 105186,"19-44": 57454, "45-64": 31097,  "65+": 23388}},
    "Escambia":     {"pop": 321905,  "area": 661.2,  "medicaid": {"0-18": 29572, "19-44": 16518, "45-64": 11221,  "65+": 8057}},
    "Flagler":      {"pop": 115081,  "area": 485.3,  "medicaid": {"0-18": 8585,  "19-44": 5215,  "45-64": 2431,   "65+": 2553}},
    "Franklin":     {"pop": 12404,   "area": 534.9,  "medicaid": {"0-18": 1020,  "19-44": 572,   "45-64": 339,    "65+": 283}},
    "Gadsden":      {"pop": 45087,   "area": 516.2,  "medicaid": {"0-18": 5414,  "19-44": 2440,  "45-64": 2598,   "65+": 1545}},
    "Gilchrist":    {"pop": 18582,   "area": 349.5,  "medicaid": {"0-18": 2144,  "19-44": 1179,  "45-64": 633,    "65+": 621}},
    "Glades":       {"pop": 13363,   "area": 773.6,  "medicaid": {"0-18": 352,   "19-44": 209,   "45-64": 220,    "65+": 149}},
    "Gulf":         {"pop": 13639,   "area": 554.9,  "medicaid": {"0-18": 1082,  "19-44": 623,   "45-64": 417,    "65+": 400}},
    "Hamilton":     {"pop": 14428,   "area": 515.1,  "medicaid": {"0-18": 1754,  "19-44": 837,   "45-64": 686,    "65+": 406}},
    "Hardee":       {"pop": 26919,   "area": 637.4,  "medicaid": {"0-18": 3976,  "19-44": 1719,  "45-64": 783,    "65+": 867}},
    "Hendry":       {"pop": 42671,   "area": 1152.7, "medicaid": {"0-18": 7847,  "19-44": 3652,  "45-64": 1656,   "65+": 1526}},
    "Hernando":     {"pop": 193920,  "area": 477.2,  "medicaid": {"0-18": 17385, "19-44": 10834, "45-64": 6737,   "65+": 5318}},
    "Highlands":    {"pop": 104847,  "area": 1028.3, "medicaid": {"0-18": 10917, "19-44": 6118,  "45-64": 3987,   "65+": 4074}},
    "Hillsborough": {"pop": 1471968, "area": 1020.3, "medicaid": {"0-18": 140535,"19-44": 79216, "45-64": 48228,  "65+": 32920}},
    "Holmes":       {"pop": 19430,   "area": 482.2,  "medicaid": {"0-18": 2073,  "19-44": 1255,  "45-64": 909,    "65+": 765}},
    "Indian River": {"pop": 159923,  "area": 503.3,  "medicaid": {"0-18": 9797,  "19-44": 5497,  "45-64": 3540,   "65+": 3358}},
    "Jackson":      {"pop": 48472,   "area": 916.4,  "medicaid": {"0-18": 5098,  "19-44": 2904,  "45-64": 2315,   "65+": 1501}},
    "Jefferson":    {"pop": 14252,   "area": 598.4,  "medicaid": {"0-18": 1342,  "19-44": 709,   "45-64": 644,    "65+": 437}},
    "Lafayette":    {"pop": 8624,    "area": 543.6,  "medicaid": {"0-18": 713,   "19-44": 358,   "45-64": 206,    "65+": 181}},
    "Lake":         {"pop": 385720,  "area": 953.4,  "medicaid": {"0-18": 29792, "19-44": 16452, "45-64": 9961,   "65+": 9128}},
    "Lee":          {"pop": 760822,  "area": 804.6,  "medicaid": {"0-18": 71240, "19-44": 38421, "45-64": 16765,  "65+": 15428}},
    "Leon":         {"pop": 292198,  "area": 667.2,  "medicaid": {"0-18": 20457, "19-44": 12338, "45-64": 7853,   "65+": 3887}},
    "Levy":         {"pop": 41503,   "area": 1118.0, "medicaid": {"0-18": 4763,  "19-44": 2662,  "45-64": 1587,   "65+": 1798}},
    "Liberty":      {"pop": 8365,    "area": 835.9,  "medicaid": {"0-18": 700,   "19-44": 411,   "45-64": 277,    "65+": 234}},
    "Madison":      {"pop": 18501,   "area": 692.1,  "medicaid": {"0-18": 1969,  "19-44": 1051,  "45-64": 1005,   "65+": 577}},
    "Manatee":      {"pop": 403253,  "area": 741.0,  "medicaid": {"0-18": 29581, "19-44": 14638, "45-64": 7121,   "65+": 6338}},
    "Marion":       {"pop": 375908,  "area": 1584.0, "medicaid": {"0-18": 39762, "19-44": 22849, "45-64": 12940,  "65+": 11934}},
    "Martin":       {"pop": 161000,  "area": 556.4,  "medicaid": {"0-18": 8621,  "19-44": 3553,  "45-64": 2284,   "65+": 2044}},
    "Miami-Dade":   {"pop": 2701767, "area": 1899.0, "medicaid": {"0-18": 260457,"19-44": 138770,"45-64": 176138, "65+": 107704}},
    "Monroe":       {"pop": 74228,   "area": 983.0,  "medicaid": {"0-18": 3982,  "19-44": 2258,  "45-64": 1331,   "65+": 1266}},
    "Nassau":       {"pop": 85070,   "area": 652.9,  "medicaid": {"0-18": 5012,  "19-44": 3016,  "45-64": 1565,   "65+": 1440}},
    "Okaloosa":     {"pop": 210738,  "area": 936.2,  "medicaid": {"0-18": 13425, "19-44": 7481,  "45-64": 3956,   "65+": 2880}},
    "Okeechobee":   {"pop": 40572,   "area": 773.8,  "medicaid": {"0-18": 4931,  "19-44": 2486,  "45-64": 1279,   "65+": 1161}},
    "Orange":       {"pop": 1393452, "area": 903.4,  "medicaid": {"0-18": 123312,"19-44": 65452, "45-64": 37192,  "65+": 34918}},
    "Osceola":      {"pop": 389476,  "area": 1323.5, "medicaid": {"0-18": 49016, "19-44": 25447, "45-64": 14507,  "65+": 16102}},
    "Palm Beach":   {"pop": 1496770, "area": 1970.0, "medicaid": {"0-18": 113834,"19-44": 54769, "45-64": 33368,  "65+": 29045}},
    "Pasco":        {"pop": 561891,  "area": 744.0,  "medicaid": {"0-18": 44831, "19-44": 26910, "45-64": 15398,  "65+": 14814}},
    "Pinellas":     {"pop": 959107,  "area": 273.8,  "medicaid": {"0-18": 51200, "19-44": 30584, "45-64": 27619,  "65+": 21334}},
    "Polk":         {"pop": 724777,  "area": 1797.7, "medicaid": {"0-18": 94432, "19-44": 51523, "45-64": 25768,  "65+": 24737}},
    "Putnam":       {"pop": 73624,   "area": 722.4,  "medicaid": {"0-18": 10163, "19-44": 5702,  "45-64": 3234,   "65+": 3196}},
    "Santa Rosa":   {"pop": 182084,  "area": 1012.1, "medicaid": {"0-18": 11724, "19-44": 7267,  "45-64": 3129,   "65+": 2838}},
    "Sarasota":     {"pop": 434006,  "area": 571.8,  "medicaid": {"0-18": 20377, "19-44": 11852, "45-64": 8184,   "65+": 7000}},
    "Seminole":     {"pop": 471826,  "area": 308.5,  "medicaid": {"0-18": 27239, "19-44": 15374, "45-64": 9045,   "65+": 7836}},
    "St. Johns":    {"pop": 273425,  "area": 601.2,  "medicaid": {"0-18": 10300, "19-44": 6140,  "45-64": 3450,   "65+": 2740}},
    "St. Lucie":    {"pop": 328297,  "area": 572.0,  "medicaid": {"0-18": 34923, "19-44": 18662, "45-64": 9361,   "65+": 8948}},
    "Sumter":       {"pop": 134952,  "area": 546.2,  "medicaid": {"0-18": 5173,  "19-44": 2887,  "45-64": 1980,   "65+": 2523}},
    "Suwannee":     {"pop": 44417,   "area": 688.2,  "medicaid": {"0-18": 5541,  "19-44": 3029,  "45-64": 1922,   "65+": 1631}},
    "Taylor":       {"pop": 21569,   "area": 1043.1, "medicaid": {"0-18": 2640,  "19-44": 1488,  "45-64": 844,    "65+": 693}},
    "Union":        {"pop": 14468,   "area": 240.2,  "medicaid": {"0-18": 1214,  "19-44": 761,   "45-64": 398,    "65+": 313}},
    "Volusia":      {"pop": 553284,  "area": 1101.2, "medicaid": {"0-18": 44920, "19-44": 26047, "45-64": 15500,  "65+": 15598}},
    "Wakulla":      {"pop": 33739,   "area": 607.0,  "medicaid": {"0-18": 2034,  "19-44": 1299,  "45-64": 774,    "65+": 569}},
    "Walton":       {"pop": 74071,   "area": 1058.2, "medicaid": {"0-18": 6125,  "19-44": 3079,  "45-64": 1117,   "65+": 1299}},
    "Washington":   {"pop": 24509,   "area": 580.5,  "medicaid": {"0-18": 2604,  "19-44": 1493,  "45-64": 941,    "65+": 755}},
}

@app.route("/api/florida-geojson")
def florida_geojson():
    """Proxy + cache Florida counties GeoJSON from Census/public sources."""
    if os.path.exists(CACHE_FILE):
        with open(CACHE_FILE, 'r', encoding='utf-8') as f:
            return Response(f.read(), mimetype='application/json')

    os.makedirs(STATIC_DIR, exist_ok=True)

    for url in GEOJSON_SOURCES:
        try:
            data = _fetch_geojson(url)
            features = data.get("features", [])
            if len(features) > 200:
                features = [
                    f for f in features
                    if str(f.get("id", "")).startswith("12")
                    or str(f.get("properties", {}).get("GEOID", "")).startswith("12")
                ]
                data = {"type": "FeatureCollection", "features": features}
            if len(features) >= 60:
                raw = json.dumps(data)
                with open(CACHE_FILE, "w", encoding="utf-8") as f:
                    f.write(raw)
                return Response(raw, mimetype="application/json")
        except Exception as exc:
            app.logger.warning("GeoJSON source failed %s – %s", url, exc)

    return jsonify({"error": "Could not load Florida county boundaries. Check server connectivity."}), 503


@app.route("/")
def index():
    return render_template("index.html")

@app.route("/api/counties")
def counties():
    result = {}
    for name, d in COUNTY_DATA.items():
        density = round(d["pop"] / d["area"], 1)
        total_medicaid = sum(d["medicaid"].values())
        result[name] = {
            "population": d["pop"],
            "area_sq_mi": d["area"],
            "density": density,
            "medicaid": d["medicaid"],
            "medicaid_total": total_medicaid,
            "medicaid_pct": round(total_medicaid / d["pop"] * 100, 1)
        }
    return jsonify(result)

if __name__ == "__main__":
    app.run(debug=True, port=5050)
