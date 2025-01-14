#!/usr/bin/env python3

import base64
import datetime
import functools
import glob
import hashlib
import json
import logging
import os
import sqlite3
import struct
import sys
import threading
import time
from datetime import timezone  # Added to handle timezone-aware datetime
from http.server import HTTPServer, SimpleHTTPRequestHandler

import config
import folium
import pandas as pd
import requests
from cryptography.hazmat.backends import default_backend
from cryptography.hazmat.primitives.asymmetric import ec
from cryptography.hazmat.primitives.ciphers import Cipher, algorithms, modes
from folium.plugins import AntPath
from register import pypush_gsa_icloud

# Configure logging
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger()

# Check if the config file exists
if not os.path.exists(config.getConfigFile()):
    logger.info(
        "No auth-token found. Please run mh_endpoint.py to register the device first."
    )
    sys.exit(1)

# Update interval in seconds (default to 5 minutes)
INTERVAL = int(os.getenv("LOCATION_SERVER_FINDER_UPDATE_INTERVAL", "300"))

# Get the number of history days from environment variable, default to 7
__history_days_env = os.getenv("LOCATION_SERVER_HISTORY_DAYS", "7")
try:
    HISTORY_DAYS = int(__history_days_env)
except ValueError:
    logger.warning(
        f"Invalid LOCATION_SERVER_HISTORY_DAYS value: {__history_days_env}. Defaulting to 7."
    )
    HISTORY_DAYS = 7


state_dir = os.path.join(
    os.path.dirname(os.path.realpath(__file__)),
    "data",
    "location-server",
)


def sha256(data):
    digest = hashlib.new("sha256")
    digest.update(data)
    return digest.digest()


def decrypt(enc_data, algorithm_dkey, mode):
    decryptor = Cipher(algorithm_dkey, mode, default_backend()).decryptor()
    return decryptor.update(enc_data) + decryptor.finalize()


def decode_tag(data):
    latitude = struct.unpack(">i", data[0:4])[0] / 10000000.0
    longitude = struct.unpack(">i", data[4:8])[0] / 10000000.0
    confidence = int.from_bytes(data[8:9], "big")
    status = int.from_bytes(data[9:10], "big")
    return {"lat": latitude, "lon": longitude, "conf": confidence, "status": status}


def getAuth(regenerate=False, second_factor="sms"):
    if os.path.exists(config.getConfigFile()) and not regenerate:
        with open(config.getConfigFile(), "r") as f:
            j = json.load(f)
    else:
        mobileme = pypush_gsa_icloud.icloud_login_mobileme(
            username=config.USER, password=config.PASS, second_factor=second_factor
        )
        logger.info(f"Mobileme result: {mobileme}")
        j = {
            "dsid": mobileme["dsid"],
            "searchPartyToken": mobileme["delegates"]["com.apple.mobileme"][
                "service-data"
            ]["tokens"]["searchPartyToken"],
        }
        with open(config.getConfigFile(), "w") as f:
            json.dump(j, f)
    return (j["dsid"], j["searchPartyToken"])


def fetch_and_process_data():
    try:
        # Set default parameters
        hours = 5 * 24
        prefix = ""
        regen = False
        trusteddevice = False

        # Connect to the database
        db_path = os.path.join(
            state_dir,
            "reports.db",
        )
        sq3db = sqlite3.connect(db_path, check_same_thread=False)
        sq3 = sq3db.cursor()

        # Load private keys
        privkeys = {}
        names = {}
        keys_path = os.path.join(
            state_dir,
            "keys",
        )
        key_files = glob.glob(os.path.join(keys_path, prefix + "*.keys"))
        if not key_files:
            logger.warning("No keys found in the keys directory.")
        for keyfile in key_files:
            with open(keyfile) as f:
                hashed_adv = priv = ""
                name = os.path.basename(keyfile)[len(prefix) : -5]
                for line in f:
                    key = line.rstrip("\n").split(": ")
                    if key[0] == "Private key":
                        priv = key[1]
                    elif key[0] == "Hashed adv key":
                        hashed_adv = key[1]
                if priv and hashed_adv:
                    privkeys[hashed_adv] = priv
                    names[hashed_adv] = name
                else:
                    logger.warning(f"Couldn't find key pair in {keyfile}")

        # Prepare data for request
        unixEpoch = int(datetime.datetime.now().timestamp())
        startdate = unixEpoch - (60 * 60 * hours)
        data = {
            "search": [
                {
                    "startDate": startdate * 1000,
                    "endDate": unixEpoch * 1000,
                    "ids": list(names.keys()),
                }
            ]
        }

        # Get authentication
        dsid, searchPartyToken = getAuth(
            regenerate=regen, second_factor="trusted_device" if trusteddevice else "sms"
        )

        # Make the request
        r = requests.post(
            "https://gateway.icloud.com/acsnservice/fetch",
            auth=(dsid, searchPartyToken),
            headers=pypush_gsa_icloud.generate_anisette_headers(),
            json=data,
        )
        logger.info(f"{r.status_code}: Response received from fetch service.")

        res = r.json().get("results", [])

        ordered = []
        found = set()
        sq3.execute(
            """CREATE TABLE IF NOT EXISTS reports (
            id_short TEXT, timestamp INTEGER, datePublished INTEGER, payload TEXT, 
            id TEXT, statusCode INTEGER, lat REAL, lon REAL, conf INTEGER,
            PRIMARY KEY(id_short,timestamp));"""
        )

        for report in res:
            try:
                priv = int.from_bytes(
                    base64.b64decode(privkeys[report["id"]]), byteorder="big"
                )
                data = base64.b64decode(report["payload"])
                timestamp = int.from_bytes(data[0:4], "big") + 978307200  # Apple Epoch

                # Check for NULL bytes
                adj = len(data) - 88

                # Adjust data slicing
                eph_pubkey_bytes = data[5 + adj : 62 + adj]
                eph_key = ec.EllipticCurvePublicKey.from_encoded_point(
                    ec.SECP224R1(), eph_pubkey_bytes
                )
                private_key = ec.derive_private_key(
                    priv, ec.SECP224R1(), default_backend()
                )
                shared_key = private_key.exchange(ec.ECDH(), eph_key)
                symmetric_key = sha256(
                    shared_key + b"\x00\x00\x00\x01" + eph_pubkey_bytes
                )
                decryption_key = symmetric_key[:16]
                iv = symmetric_key[16:]
                enc_data = data[62 + adj : 72 + adj]
                auth_tag = data[72 + adj :]

                decrypted = decrypt(
                    enc_data, algorithms.AES(decryption_key), modes.GCM(iv, auth_tag)
                )
                tag = decode_tag(decrypted)
                tag["timestamp"] = timestamp
                tag["isodatetime"] = datetime.datetime.fromtimestamp(
                    timestamp, tz=timezone.utc
                ).isoformat()
                tag["key"] = names[report["id"]]
                tag["goog"] = (
                    "https://maps.google.com/maps?q="
                    + str(tag["lat"])
                    + ","
                    + str(tag["lon"])
                )
                found.add(tag["key"])
                ordered.append(tag)

                # SQL Injection Mitigation
                query = (
                    "INSERT OR REPLACE INTO reports VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)"
                )
                parameters = (
                    names[report["id"]],
                    timestamp,
                    report["datePublished"],
                    report["payload"],
                    report["id"],
                    report["statusCode"],
                    float(tag["lat"]),
                    float(tag["lon"]),
                    tag["conf"],
                )
                sq3.execute(query, parameters)
            except Exception as ex:
                logger.error(f"Error processing report: {ex}", exc_info=True)

        # Get the total number of records in reports.db using rowid
        sq3.execute("SELECT COUNT(rowid) FROM reports")
        total_records = sq3.fetchone()[0]
        logger.info(f"Total number of data points in reports.db: {total_records}")

        sq3db.commit()
        sq3db.close()

        logger.info(f"{len(ordered)} reports processed.")

        # Generate the HTML map
        generate_html_map()

    except Exception as e:
        if str(e) == "AuthenticationError":
            logger.error(
                "Authentication failed. Please check your Apple ID credentials."
            )
        else:
            logger.error(f"Exception occurred: {e}", exc_info=True)


def generate_html_map():
    try:
        # Connect to the database
        db_path = os.path.join(
            state_dir,
            "reports.db",
        )
        conn = sqlite3.connect(db_path, check_same_thread=False)

        if HISTORY_DAYS == 0:
            # Query all records
            query = "SELECT * FROM reports"
            df = pd.read_sql_query(query, conn)
        else:
            # Compute the timestamp for 'history_days' days ago
            history_timestamp = int(
                (
                    datetime.datetime.now() - datetime.timedelta(days=HISTORY_DAYS)
                ).timestamp()
            )
            # Read data from 'reports' table where timestamp is greater than or equal to history_timestamp
            query = "SELECT * FROM reports WHERE timestamp >= ?"
            df = pd.read_sql_query(query, conn, params=(history_timestamp,))
        conn.close()

        if not df.empty:
            # Process the data
            df["timestamp"] = pd.to_datetime(df["timestamp"], unit="s", utc=True)
            df = df.sort_values("timestamp")

            df["lat"] = df["lat"].astype(float)
            df["lon"] = df["lon"].astype(float)
            df["datetime"] = df["timestamp"]
            df["isodatetime"] = df["timestamp"].dt.strftime("%Y-%m-%dT%H:%M:%S%z")
            df["time_diff"] = df["timestamp"].diff().dt.total_seconds()
            average_time_diff = df["time_diff"][1:].mean()
            time_diff_total = (
                df.iloc[-1]["timestamp"] - df.iloc[0]["timestamp"]
            ).total_seconds()

            # Formatting time
            def format_time(seconds):
                if not seconds or seconds != seconds:  # Check for NaN
                    return "0s"
                hours = seconds // 3600
                minutes = (seconds % 3600) // 60
                seconds = seconds % 60
                return f"{int(hours)}h {int(minutes)}m {int(seconds)}s"

            formatted_total_time = format_time(time_diff_total)
            formatted_avg_time = format_time(average_time_diff)

            start_timestamp = df.iloc[0]["timestamp"].strftime("%Y-%m-%d %H:%M:%S")
            end_timestamp = df.iloc[-1]["timestamp"].strftime("%Y-%m-%d %H:%M:%S")

            ping_count = df.shape[0]

            # Log the number of data points collected in the dataframe
            logger.info(f"Data points collected in dataframe: {ping_count}")

            # Sanity check before plotting
            if not df.empty:
                map_center = [df.iloc[0]["lat"], df.iloc[0]["lon"]]
                m = folium.Map(location=map_center, zoom_start=13)

                latlon_pairs = list(zip(df["lat"], df["lon"]))
                ant_path = AntPath(
                    locations=latlon_pairs,
                    dash_array=[10, 20],
                    delay=1000,
                    color="red",
                    weight=5,
                    pulse_color="black",
                )
                m.add_child(ant_path)

                # Location markers
                for index, row in df.iterrows():
                    if index == 0:  # First marker
                        folium.Marker(
                            [row["lat"], row["lon"]],
                            popup=f"Timestamp: {row['isodatetime']} Start Point",
                            tooltip=f"Start Point",
                            icon=folium.Icon(color="green"),
                        ).add_to(m)
                    elif index == len(df) - 1:  # Last marker
                        folium.Marker(
                            [row["lat"], row["lon"]],
                            popup=f"Timestamp: {row['isodatetime']} End Point",
                            tooltip=f"End Point",
                            icon=folium.Icon(color="red"),
                        ).add_to(m)
                    else:  # Other markers
                        folium.Marker(
                            [row["lat"], row["lon"]],
                            popup=f"Timestamp: {row['isodatetime']}",
                            tooltip=f"Point {index+1}",
                        ).add_to(m)

                title_and_info_html = f"""
                 <h3 align="center" style="font-size:20px; margin-top:10px;"><b>FindMy Location Mapper</b></h3>
                 <div style="position: fixed; bottom: 50px; left: 50px; width: 300px; height: 160px; z-index:9999; font-size:14px; background-color: white; padding: 10px; border-radius: 10px; box-shadow: 0 0 5px rgba(0,0,0,0.5);">
                 <b>Location Summary</b><br>
                 Start: {start_timestamp}<br>
                 End: {end_timestamp}<br>
                 Number of Location Pings: {ping_count}<br>
                 Total Time: {formatted_total_time}<br>
                 Average Time Between Pings: {formatted_avg_time}<br>
                 </div>
                 """
                m.get_root().html.add_child(folium.Element(title_and_info_html))

                html_output_path = os.path.join(
                    state_dir,
                    "map.html",
                )
                m.save(html_output_path)
                logger.info(
                    f"Map generated! Access 'map.html' through the web server to view."
                )
            else:
                logger.info("No data available to plot.")
        else:
            logger.info(
                f"No data available in the database for the last {HISTORY_DAYS} days."
            )
    except Exception as e:
        logger.error(f"Error generating HTML map: {e}", exc_info=True)


def periodic_fetch():
    while True:
        logger.info("Fetching and processing data...")
        fetch_and_process_data()
        logger.info(f"Waiting for {INTERVAL} seconds before next update...")
        time.sleep(INTERVAL)


# Custom HTTP handler to serve only map.html
class CustomHandler(SimpleHTTPRequestHandler):
    def do_GET(self):
        # Always serve 'map.html', regardless of the requested path
        self.path = "/map.html"
        return SimpleHTTPRequestHandler.do_GET(self)


def run_web_server(port=27184):
    Handler = functools.partial(CustomHandler, directory=state_dir)
    with HTTPServer(("", port), Handler) as httpd:
        logger.info(f"Serving at port {port}")
        try:
            httpd.serve_forever()
        except KeyboardInterrupt:
            logger.info("Server is shutting down.")
            httpd.shutdown()


if __name__ == "__main__":
    # Start the periodic fetch in a background thread
    fetch_thread = threading.Thread(target=periodic_fetch)
    fetch_thread.daemon = True
    fetch_thread.start()

    # Start the web server
    logger.info("Starting web server...")
    PORT = int(os.getenv("LOCATION_SERVER_PORT", "27184"))
    run_web_server(port=PORT)
