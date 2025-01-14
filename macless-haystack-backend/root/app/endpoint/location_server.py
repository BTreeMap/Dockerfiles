#!/usr/bin/env python3

import base64
import datetime
import glob
import hashlib
import json
import os
import sqlite3
import struct
import sys
import threading
import time
from datetime import timezone  # Added to handle timezone-aware datetime
from http.server import HTTPServer, SimpleHTTPRequestHandler

import config
import requests
from cryptography.hazmat.backends import default_backend
from cryptography.hazmat.primitives.asymmetric import ec
from cryptography.hazmat.primitives.ciphers import Cipher, algorithms, modes
from location_server_common import db_path, logger, state_dir
from location_server_helper import generate_html_map, initialize_database
from register import pypush_gsa_icloud

# Check if the config file exists
if not os.path.exists(config.getConfigFile()):
    logger.info(
        "No auth-token found. Please run mh_endpoint.py to register the device first."
    )
    sys.exit(1)

# Update interval in seconds (default to 5 minutes)
INTERVAL = int(os.getenv("LOCATION_SERVER_FINDER_UPDATE_INTERVAL", "300"))


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

        # Connect to the database
        conn = sqlite3.connect(db_path, check_same_thread=False)
        c = conn.cursor()

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

                # Insert data into the reports table
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
                c.execute(query, parameters)
            except Exception as ex:
                logger.error(f"Error processing report: {ex}", exc_info=True)

        # The triggers will automatically update 'rowcount'

        # Get the total number of records in reports.db using O(1) operation
        c.execute("SELECT count FROM rowcount")
        total_records = c.fetchone()[0]
        logger.info(f"Total number of data points in reports.db: {total_records}")

        conn.commit()
        conn.close()

        logger.info(f"{len(ordered)} reports processed.")

        # Generate the HTML maps
        generate_html_map()

    except Exception as e:
        if str(e) == "AuthenticationError":
            logger.error(
                "Authentication failed. Please check your Apple ID credentials."
            )
        else:
            logger.error(f"Exception occurred: {e}", exc_info=True)


def periodic_fetch():
    while True:
        logger.info("Fetching and processing data...")
        fetch_and_process_data()
        logger.info(f"Waiting for {INTERVAL} seconds before next update...")
        time.sleep(INTERVAL)


# Custom HTTP handler to serve files
class CustomHandler(SimpleHTTPRequestHandler):
    def __init__(self, *args, **kwargs):
        super().__init__(*args, directory=state_dir, **kwargs)

    def do_GET(self):
        # Serve the requested path
        return super().do_GET()


def run_web_server(port=27184):
    Handler = CustomHandler
    with HTTPServer(("", port), Handler) as httpd:
        logger.info(f"Serving at port {port}")
        try:
            httpd.serve_forever()
        except KeyboardInterrupt:
            logger.info("Server is shutting down.")
            httpd.shutdown()


if __name__ == "__main__":
    # Initialize the database
    initialize_database()

    # Start the periodic fetch in a background thread
    fetch_thread = threading.Thread(target=periodic_fetch)
    fetch_thread.daemon = True
    fetch_thread.start()

    # Start the web server
    logger.info("Starting web server...")
    PORT = int(os.getenv("LOCATION_SERVER_PORT", "27184"))
    run_web_server(port=PORT)
