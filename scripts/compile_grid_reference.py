# -*- coding: utf-8 -*-
"""Final Sentinel grid camera table: location, coordinates, stream URL, facing.

One row per camera, 30 rows. Built from three evidence sources and every row
states which one it rests on, so an estimate is never mistaken for a survey.
"""
import json, urllib.request, csv, datetime, os

BASE = "https://live.corp8.cloud"
UA = ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
      "(KHTML, like Gecko) Chrome/124.0 Safari/537.36")
cat = {c["id"]: c for c in json.load(urllib.request.urlopen(
    urllib.request.Request(BASE + "/api/cameras", headers={"User-Agent": UA}),
    timeout=30))["cameras"]}

# id: (site_name, district, lat, lon, facing, facing_basis, geo_conf, geo_source)
R = {
"1":  ("Chimanbhai Patel Bridge, RTO Circle, Ranip", "Ahmedabad", 23.068493, 72.580767, "West",    "operator survey",            "verified",         "your survey; OSM 'RTO Circle' bus stop 12.3 m away confirms it"),
"2":  ("Janpath T-Junction",                          "Ahmedabad", 23.030000, 72.570000, "North",   "unconfirmed",                "estimated",        "no OSM match for 'Janpath'; overlay CSITMS-10_PTZ2"),
"3":  ("O.N.G.C. Office",                             "Ahmedabad", 23.103638, 72.586766, "East",    "unconfirmed",                "geocoded",         "OSM bus stop 'ONGC'"),
"4":  ("Paldi Junction (V.S. Hospital approach)",     "Ahmedabad", 23.016500, 72.565500, "North",   "sign 'V.S. Hospital <-'",    "landmark_derived", "OSM Paldi cluster; Gujarat Blood Bank / VS Hospital 23.016535,72.566382"),
"5":  ("Visat Three-Way Junction (Motera)",           "Ahmedabad", 23.103000, 72.587500, "North",   "oncoming traffic on image right (left-hand drive)", "landmark_derived", "gantry GANDHINAGAR/RTO CIRCLE/SABARMATI + MOTERA; anchored to ONGC hit"),
"6":  ("Madhuram Bypass Road (from Aksharvadi)",      "Bhavnagar", 21.769769, 72.145940, "South",   "overlay 'From Aksharvadi'",  "geocoded",         "OSM 'Madhuram Hospital' Bhavnagar - overlay beats catalogue label"),
"7":  ("Hero Showroom (Bhavani Char Rasta approach)", "Gir Somnath", 20.910000, 70.367000, "East",  "overlay 'Bhavani Char Rasta to Hero'", "estimated", "no OSM match for 'Bhavani'"),
"8":  ("Majevadi Gate",                               "Junagadh",  21.517000, 70.467000, "North",   "unconfirmed",                "estimated",        "OSM 'Majevadi' is a village 10 km away - REJECTED, frame shows city gate"),
"9":  ("New Bypass at 66KV Substation (from Vadla Fatak)", "Junagadh", 21.496747, 70.402476, "South", "overlay 'From Vadla Fatak'", "geocoded",       "OSM node named 'Vadla fatak' - exact name match to overlay"),
"10": ("Char Chowk (from Diparti Furniture)",         "Junagadh",  21.518500, 70.460000, "East",    "overlay 'From Diparti Furniture'", "estimated",  "no OSM match for 'Char Chowk'"),
"11": ("Dolatpara Gate",                              "Junagadh",  21.558757, 70.465922, "West",    "unconfirmed",                "geocoded",         "OSM suburb + marketyard, two independent Dolatpara nodes"),
"12": ("Tri Mandir Adalaj Toll Plaza, Lane 9",        "Gandhinagar", 23.164692, 72.582105, "North", "toll lane direction",        "landmark_derived", "OSM Adalaj town; Adalaj Circle 23.168342,72.586684"),
"13": ("C.N. Vidyalaya Junction",                     "Ahmedabad", 23.020838, 72.552041, "South",   "unconfirmed",                "geocoded",         "OSM way named 'Vidyalaya'"),
"14": ("Delight Circle",                              "Ahmedabad", 23.047000, 72.562000, "East",    "unconfirmed",                "estimated",        "'WEST BANK' facade legible in frame but absent from OSM"),
"15": ("Suvidha Park Junction",                       "Ahmedabad", 23.014680, 72.559172, "West",    "unconfirmed",                "geocoded",         "OSM node 'Suvidha Crossroads' - exact junction match"),
"16": ("Visat T-Junction",                            "Ahmedabad", 23.103500, 72.588000, "South",   "unconfirmed",                "landmark_derived", "ONGC hoarding in frame anchors to camera 3"),
"17": ("Rajkot Bus Port",                             "Rajkot",    22.295499, 70.798950, "Unknown", "stream down, never seen",    "geocoded",         "OSM 'Rajkot New Bus Station'"),
"18": ("Rajkot City Camera",                          "Rajkot",    22.303900, 70.802200, "Unknown", "stream down, never seen",    "district_centroid","no location detail available"),
"19": ("Khaparia Gram Panchayat",                     "Navsari",   20.830000, 72.990000, "North",   "unconfirmed",                "estimated",        "no OSM match; Gandevi taluka per catalogue label"),
"20": ("Mohanpura (AI IPC)",                          "Navsari",   20.850000, 73.000000, "East",    "unconfirmed",                "estimated",        "no OSM match; S-Gujarat vegetation in frame"),
"21": ("Dethali Char Rasta",                          "Patan",     23.916593, 72.361148, "North",   "unconfirmed",                "geocoded",         "OSM 'Dethali Circle' - exact junction match"),
"22": ("Mervada Three-Way Junction",                  "Banaskantha", 24.170000, 72.430000, "Unknown","stream unreachable",        "district_centroid","no OSM match; never captured a frame"),
"23": ("Kheram village road",                         "Navsari",   20.860000, 73.050000, "North",   "unconfirmed",                "estimated",        "no OSM match; overlay is generic 'Camera 01'"),
"24": ("Dehgam town road",                            "Gandhinagar", 23.164033, 72.881832, "North", "unconfirmed",                "geocoded",         "OSM Dehgam"),
"25": ("Dhanori Gram Panchayat",                      "Navsari",   20.838862, 73.023955, "North",   "unconfirmed",                "geocoded",         "OSM 'Civil Hospital, Dhanori'"),
"26": ("Tankal village junction",                     "Navsari",   20.859893, 73.115038, "East",    "unconfirmed",                "geocoded",         "OSM way 'Tankal' village centre"),
"27": ("Bilimora Bus Station - bay apron",            "Navsari",   20.767169, 72.969345, "North",   "unconfirmed",                "landmark_derived", "OSM Bilimora town; bus station not mapped in OSM"),
"28": ("Bilimora Bus Station - concourse",            "Navsari",   20.766635, 72.970008, "North",   "indoor camera",              "landmark_derived", "OSM node 'Bilimora Junction'"),
"29": ("Bilimora Bus Station - waiting hall",         "Navsari",   20.766635, 72.970008, "South",   "indoor camera",              "landmark_derived", "OSM node 'Bilimora Junction'"),
"30": ("Hiralal Parakh Circle, Rambaug",              "Kutch",     23.075403, 70.090851, "North",   "unconfirmed",                "geocoded",         "OSM 'Rambaug' Gandhidham; circle nameplate legible in frame"),
}

HEADER = ["camera_no", "site_name", "district", "latitude", "longitude",
          "google_maps_link", "facing", "facing_basis", "geo_confidence",
          "geo_source", "stream_url_to_open", "rtsp_url", "codec", "resolution",
          "fps", "stream_status"]

rows = []
for cid in map(str, range(1, 31)):
    c = cat[cid]
    site, dist, lat, lon, face, fbasis, conf, gsrc = R[cid]
    codec = {"h264": "H.264", "hevc": "H.265"}.get(c.get("codec") or "", "not probed")
    res = f'{c["width"]}x{c["height"]}' if c.get("width") else "not probed"
    fps = str(round(c["fps"], 1)) if c.get("fps") else "not probed"
    status = "LIVE" if cid not in ("17", "18", "22") else "DOWN (catalogue says live)"
    rows.append([cid, site, dist, f"{lat:.6f}", f"{lon:.6f}",
                 f"https://www.google.com/maps?q={lat:.6f},{lon:.6f}",
                 face, fbasis, conf, gsrc,
                 f'{BASE}{c["hls_live_url"]}?cookieCheck=1', c["rtsp_url"],
                 codec, res, fps, status])

os.makedirs("data/reference", exist_ok=True)
with open("data/reference/grid_cameras_final.csv", "w", newline="", encoding="utf-8-sig") as f:
    w = csv.writer(f, dialect="excel")
    w.writerow(HEADER)
    w.writerows(rows)

# human-readable companion
with open("data/reference/grid_cameras_final.txt", "w", encoding="utf-8") as f:
    f.write("SENTINEL CAMERA GRID - 30 CAMERAS\n")
    f.write("snapshot %s UTC   source %s/api/ingest\n" %
            (datetime.datetime.now(datetime.timezone.utc).strftime("%Y-%m-%d %H:%M"), BASE))
    f.write("=" * 78 + "\n\n")
    for r in rows:
        f.write(f"Camera {r[0]}  [{r[15]}]\n")
        f.write(f"  {r[10]}\n")
        f.write(f"  Lat Long: {r[3]}, {r[4]}   ({r[8]})\n")
        f.write(f"  Name: {r[1]}   [{r[2]}]\n")
        f.write(f"  Direction: {r[6]} Facing   ({r[7]})\n")
        f.write(f"  Map: {r[5]}\n")
        f.write(f"  Evidence: {r[9]}\n\n")

json.dump([dict(zip(HEADER, r)) for r in rows],
          open("data/reference/grid_cameras_final.json", "w", encoding="utf-8"),
          indent=2, ensure_ascii=False)

from collections import Counter
print("wrote data/reference/grid_cameras_final.csv  (%d rows x %d cols)" % (len(rows), len(HEADER)))
print("wrote data/reference/grid_cameras_final.txt")
print("wrote data/reference/grid_cameras_final.json")
print()
print("geo confidence:", dict(Counter(r[8] for r in rows)))
print("stream status :", dict(Counter(r[15] for r in rows)))
