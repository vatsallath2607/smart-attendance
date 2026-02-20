# register_students.py
"""
Batch-register students from a CSV into SQLite DB and precompute face encodings.

CSV format (header):
reg_no,name,image_path,section_name

Example:
RA2311028010009,Vatsal Lath,images/vatsal.jpg,SectionA
"""

import sqlite3
import os
import csv
import pickle
import sys
from typing import Dict

try:
    import numpy as np
    import face_recognition
except Exception as e:
    print("ERROR: Missing Python package. Make sure you installed requirements in your venv.")
    print("pip install -r requirements.txt")
    raise

DB_PATH = os.path.join("data", "attendance.db")
KNOWN_PICKLE = os.path.join("data", "known_encodings.pkl")
DB_SCHEMA_FILE = "db_schema.sql"


def ensure_db():
    """Create DB and tables by executing db_schema.sql"""
    if not os.path.exists(DB_SCHEMA_FILE):
        raise FileNotFoundError(f"{DB_SCHEMA_FILE} not found in project root. Create it with the required schema.")
    os.makedirs(os.path.dirname(DB_PATH) or ".", exist_ok=True)
    conn = sqlite3.connect(DB_PATH)
    with open(DB_SCHEMA_FILE, "r", encoding="utf-8") as f:
        sql = f.read()
    c = conn.cursor()
    c.executescript(sql)
    conn.commit()
    conn.close()
    print("Database schema ensured.")


def get_or_create_section(conn: sqlite3.Connection, name: str) -> int:
    c = conn.cursor()
    c.execute("SELECT id FROM sections WHERE name=?", (name,))
    row = c.fetchone()
    if row:
        return row[0]
    c.execute("INSERT INTO sections(name) VALUES(?)", (name,))
    conn.commit()
    return c.lastrowid


def register_from_csv(csv_path: str):
    if not os.path.exists(csv_path):
        raise FileNotFoundError(f"CSV file not found: {csv_path}")

    ensure_db()
    conn = sqlite3.connect(DB_PATH)
    known: Dict[str, np.ndarray] = {}

    with open(csv_path, newline="", encoding="utf-8") as f:
        reader = csv.DictReader(f)
        row_count = 0
        skipped = 0
        for r in reader:
            row_count += 1
            reg = r.get("reg_no", "").strip()
            name = r.get("name", "").strip()
            img_path = r.get("image_path", "").strip()
            section_name = r.get("section_name", "default").strip()

            if not reg:
                print(f"[{row_count}] Skipping row with empty reg_no.")
                skipped += 1
                continue

            # Resolve relative image path
            img_path_resolved = img_path
            if not os.path.isabs(img_path_resolved):
                img_path_resolved = os.path.join(os.getcwd(), img_path_resolved)

            if not os.path.exists(img_path_resolved):
                print(f"[{row_count}] Image not found for {reg}: {img_path} -> skipping")
                skipped += 1
                continue

            try:
                image = face_recognition.load_image_file(img_path_resolved)
                encs = face_recognition.face_encodings(image)
            except Exception as e:
                print(f"[{row_count}] Error processing image for {reg}: {e} -> skipping")
                skipped += 1
                continue

            if not encs:
                print(f"[{row_count}] No face found for {reg} in {img_path} -> skipping")
                skipped += 1
                continue

            encoding = encs[0]

            # store in DB
            section_id = get_or_create_section(conn, section_name)
            blob = pickle.dumps(encoding)
            c = conn.cursor()
            c.execute(
                "INSERT OR REPLACE INTO students(reg_no,name,section_id,image_path,encoding) VALUES(?,?,?,?,?)",
                (reg, name, section_id, img_path, blob),
            )
            conn.commit()
            known[reg] = encoding
            print(f"[{row_count}] Registered {reg} ({name}) in section '{section_name}'")

    # save pickle of known encodings
    try:
        os.makedirs(os.path.dirname(KNOWN_PICKLE), exist_ok=True)
        with open(KNOWN_PICKLE, "wb") as f:
            pickle.dump(known, f)
        print(f"Saved {len(known)} encodings to {KNOWN_PICKLE}")
    except Exception as e:
        print(f"Warning: failed to save known encodings pickle: {e}")

    conn.close()
    print(f"Done. Processed rows: {row_count}, skipped: {skipped}, registered: {len(known)}")


if __name__ == "__main__":
    if len(sys.argv) < 2:
        print("Usage: python register_students.py students.csv")
        sys.exit(1)
    csv_file = sys.argv[1]
    register_from_csv(csv_file)
