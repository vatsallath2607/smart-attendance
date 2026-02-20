# app.py
import os
import io
import sqlite3
import pickle
import base64
import uuid
import traceback
import logging
from datetime import datetime, timedelta
from flask import Flask, request, jsonify, g, send_from_directory

# face libs
import face_recognition
import numpy as np
import qrcode
from PIL import Image

# ---------- Config ----------
DB_PATH = 'data/attendance.db'
KNOWN_PICKLE = 'data/known_encodings.pkl'
PHOTOS_DIR = 'data/photos'
STATIC_DIR = 'static'    # static/faculty.html and static/student.html

# ---------- Logging & ensure folders ----------
logging.basicConfig(level=logging.INFO, format='%(asctime)s %(levelname)s %(message)s')
os.makedirs(os.path.dirname(DB_PATH) or '.', exist_ok=True)
os.makedirs(PHOTOS_DIR, exist_ok=True)
os.makedirs(STATIC_DIR, exist_ok=True)

app = Flask(__name__, static_folder=STATIC_DIR, template_folder=STATIC_DIR)

# ---------- DB helpers ----------
def get_db():
    db = getattr(g, '_database', None)
    if db is None:
        db = g._database = sqlite3.connect(DB_PATH, detect_types=sqlite3.PARSE_DECLTYPES | sqlite3.PARSE_COLNAMES)
        db.row_factory = sqlite3.Row
    return db

@app.teardown_appcontext
def close_connection(exception):
    db = getattr(g, '_database', None)
    if db is not None:
        db.close()

def ensure_tables_exist():
    conn = sqlite3.connect(DB_PATH)
    c = conn.cursor()
    c.executescript("""
    CREATE TABLE IF NOT EXISTS sections (
      id INTEGER PRIMARY KEY AUTOINCREMENT,
      name TEXT NOT NULL
    );
    CREATE TABLE IF NOT EXISTS students (
      id INTEGER PRIMARY KEY AUTOINCREMENT,
      reg_no TEXT UNIQUE NOT NULL,
      name TEXT,
      section_id INTEGER,
      image_path TEXT,
      encoding BLOB,
      FOREIGN KEY(section_id) REFERENCES sections(id)
    );
    CREATE TABLE IF NOT EXISTS sessions (
      id INTEGER PRIMARY KEY AUTOINCREMENT,
      section_id INTEGER NOT NULL,
      code TEXT NOT NULL,
      started_at DATETIME NOT NULL,
      expires_at DATETIME NOT NULL,
      active INTEGER DEFAULT 1,
      FOREIGN KEY(section_id) REFERENCES sections(id)
    );
    CREATE TABLE IF NOT EXISTS attendance (
      id INTEGER PRIMARY KEY AUTOINCREMENT,
      reg_no TEXT NOT NULL,
      section_id INTEGER NOT NULL,
      session_id INTEGER NOT NULL,
      photo_path TEXT,
      timestamp DATETIME DEFAULT CURRENT_TIMESTAMP
    );
    """)
    conn.commit()
    conn.close()

ensure_tables_exist()

# ---------- Known encodings ----------
def load_known_encodings():
    if os.path.exists(KNOWN_PICKLE):
        try:
            with open(KNOWN_PICKLE, 'rb') as f:
                known = pickle.load(f)
                logging.info("Loaded %d encodings from %s", len(known), KNOWN_PICKLE)
                return known
        except Exception as e:
            logging.warning("Failed to load known encodings pickle: %s", e)
    # fallback to DB
    known = {}
    try:
        conn = sqlite3.connect(DB_PATH)
        c = conn.cursor()
        for reg_no, blob in c.execute('SELECT reg_no, encoding FROM students'):
            if blob:
                try:
                    enc = pickle.loads(blob)
                    known[reg_no] = enc
                except Exception:
                    pass
        conn.close()
        logging.info("Loaded %d encodings from DB", len(known))
    except Exception as e:
        logging.warning("Failed to load encodings from DB: %s", e)
    return known

KNOWN = load_known_encodings()

# ---------- Static pages ----------
@app.route('/')
def faculty_page():
    return send_from_directory(STATIC_DIR, 'faculty.html')

@app.route('/student')
def student_page():
    return send_from_directory(STATIC_DIR, 'student.html')

@app.route('/data/photos/<path:filename>')
def serve_photo(filename):
    return send_from_directory(PHOTOS_DIR, filename)

# ---------- API: list/create sections ----------
@app.route('/api/sections', methods=['GET'])
def api_sections():
    try:
        conn = get_db()
        c = conn.cursor()
        rows = c.execute('SELECT id, name FROM sections ORDER BY id').fetchall()
        sections = [{'id': r['id'], 'name': r['name']} for r in rows]
        return jsonify({'success': True, 'sections': sections})
    except Exception as e:
        logging.error("Error in api_sections: %s", e)
        traceback.print_exc()
        return jsonify({'success': False, 'msg': str(e)}), 500

@app.route('/api/sections', methods=['POST'])
def api_create_section():
    try:
        data = request.get_json(force=True, silent=True) or {}
        name = data.get('name', '').strip()
        if not name:
            return jsonify({'success': False, 'msg': 'Missing section name'}), 400
        conn = get_db()
        c = conn.cursor()
        c.execute('INSERT INTO sections(name) VALUES(?)', (name,))
        conn.commit()
        return jsonify({'success': True, 'id': c.lastrowid, 'name': name})
    except Exception as e:
        logging.error("Error in api_create_section: %s", e)
        traceback.print_exc()
        return jsonify({'success': False, 'msg': str(e)}), 500

# ---------- API: generate session ----------
@app.route('/api/generate_session', methods=['POST'])
def generate_session():
    try:
        data = request.get_json(force=True, silent=True) or {}
        section_id = data.get('section_id')
        if section_id is None or str(section_id).strip() == "":
            return jsonify({'success': False, 'msg': 'Missing section_id'}), 400

        try:
            duration_min = int(data.get('duration_min', 5))
        except Exception:
            duration_min = 5

        code = str(uuid.uuid4()).split('-')[0]  # short code
        started = datetime.utcnow()
        expires = started + timedelta(minutes=duration_min)

        conn = get_db()
        c = conn.cursor()
        c.execute(
            'INSERT INTO sessions(section_id,code,started_at,expires_at,active) VALUES(?,?,?,?,1)',
            (section_id, code, started.isoformat(), expires.isoformat()),
        )
        conn.commit()
        session_id = c.lastrowid

        # Return only the code (no QR)
        return jsonify({
            'success': True,
            'session_id': session_id,
            'code': code,
            'expires_at': expires.isoformat()
        })
    except Exception as e:
        logging.error("Error in generate_session: %s", e)
        traceback.print_exc()
        return jsonify({'success': False, 'msg': 'Server error generating session', 'details': str(e)}), 500

# ---------- API: stop session ----------
@app.route('/api/stop_session', methods=['POST'])
def stop_session():
    """
    Stop an active session. Accepts JSON with:
      - session_id (preferred)
    OR - code + section_id
    OR - section_id (stops the latest active session for that section)
    Returns number of sessions stopped.
    """
    try:
        data = request.get_json(force=True, silent=True) or {}
        session_id = data.get('session_id')
        code = data.get('code')
        section_id = data.get('section_id')

        conn = get_db()
        c = conn.cursor()

        if session_id:
            c.execute('UPDATE sessions SET active=0 WHERE id=? AND active=1', (session_id,))
        elif code and section_id:
            c.execute('UPDATE sessions SET active=0 WHERE code=? AND section_id=? AND active=1', (code, section_id))
        elif section_id:
            # stop the latest active session for the section
            c.execute('UPDATE sessions SET active=0 WHERE id IN (SELECT id FROM sessions WHERE section_id=? AND active=1 ORDER BY id DESC LIMIT 1)', (section_id,))
        else:
            return jsonify({'success': False, 'msg': 'Provide session_id or section_id or code+section_id'}), 400

        stopped = c.rowcount if hasattr(c, 'rowcount') else 0
        conn.commit()

        if stopped == 0:
            return jsonify({'success': False, 'msg': 'No active session found to stop'}), 404

        return jsonify({'success': True, 'stopped': stopped})
    except Exception as e:
        logging.error("Error in stop_session: %s", e)
        traceback.print_exc()
        return jsonify({'success': False, 'msg': 'Server error stopping session', 'details': str(e)}), 500
        

# ---------- API: submit attendance (prevent duplicates) ----------
@app.route('/api/submit_attendance', methods=['POST'])
def submit_attendance():
    try:
        reg_no = request.form.get('reg_no')
        code = request.form.get('code')
        section = request.form.get('section')
        if not reg_no or not code or not section:
            return jsonify({'success': False, 'msg': 'Missing reg_no, code, or section'}), 400

        # ensure numeric section id
        try:
            section_id = int(section)
        except Exception:
            return jsonify({'success': False, 'msg': 'Invalid section id'}), 400

        conn = get_db()
        c = conn.cursor()

        # validate session
        c.execute('SELECT id,expires_at,active FROM sessions WHERE code=? AND section_id=? ORDER BY id DESC', (code, section_id))
        row = c.fetchone()
        if not row:
            return jsonify({'success': False, 'msg': 'Invalid code or section'}), 400
        session_id, expires_at, active = row
        if not active:
            return jsonify({'success': False, 'msg': 'Session is inactive'}), 400
        try:
            if datetime.fromisoformat(expires_at) < datetime.utcnow():
                return jsonify({'success': False, 'msg': 'Code expired'}), 400
        except Exception:
            pass

        # --- DUPLICATE CHECK: has this reg_no already submitted for this session? ---
        c.execute('SELECT 1 FROM attendance WHERE reg_no=? AND session_id=? LIMIT 1', (reg_no, session_id))
        if c.fetchone():
            return jsonify({'success': False, 'msg': 'Attendance already submitted'}), 409

        # Read image bytes either as file upload or base64 string
        if 'image' in request.files:
            image_file = request.files['image']
            img_bytes = image_file.read()
        else:
            img_b64 = request.form.get('image_base64')
            if not img_b64:
                return jsonify({'success': False, 'msg': 'No image provided'}), 400
            header, data = img_b64.split(',', 1) if ',' in img_b64 else (None, img_b64)
            try:
                img_bytes = base64.b64decode(data)
            except Exception:
                return jsonify({'success': False, 'msg': 'Invalid base64 image'}), 400

        # Save photo
        safe_reg = "".join(ch for ch in reg_no if ch.isalnum() or ch in ('_', '-'))
        filename = f'{safe_reg}_{int(datetime.utcnow().timestamp())}.jpg'
        saved_path = os.path.join(PHOTOS_DIR, filename)
        with open(saved_path, 'wb') as f:
            f.write(img_bytes)

        # verify student registered
        if reg_no not in KNOWN:
            return jsonify({'success': False, 'msg': 'Student not registered'}), 400
        known_enc = KNOWN[reg_no]

        # face verification
        captured_img = face_recognition.load_image_file(io.BytesIO(img_bytes))
        encs = face_recognition.face_encodings(captured_img)
        if not encs:
            return jsonify({'success': False, 'msg': 'No face detected in image'}), 400
        captured_enc = encs[0]
        dist = np.linalg.norm(captured_enc - known_enc)
        if dist > 0.6:
            return jsonify({'success': False, 'msg': 'Face mismatch'}), 400

        # eyes-open heuristic (optional)
        landmarks = face_recognition.face_landmarks(captured_img)
        if landmarks:
            def ear(points):
                pts = np.array(points)
                if pts.shape[0] < 6:
                    return 0.0
                left = np.linalg.norm(pts[1] - pts[5]) + np.linalg.norm(pts[2] - pts[4])
                left /= 2.0
                horiz = np.linalg.norm(pts[0] - pts[3])
                return left / horiz if horiz > 0 else 0.0
            ear_val = 0.0
            if 'left_eye' in landmarks[0]:
                ear_val = max(ear_val, ear(landmarks[0]['left_eye']))
            if 'right_eye' in landmarks[0]:
                ear_val = max(ear_val, ear(landmarks[0]['right_eye']))
            if ear_val < 0.13:
                return jsonify({'success': False, 'msg': 'Please open your eyes clearly'}), 400

        # insert attendance (first submission)
        c.execute('INSERT INTO attendance(reg_no,section_id,session_id,photo_path) VALUES(?,?,?,?)',
                  (reg_no, section_id, session_id, saved_path))
        conn.commit()
        return jsonify({'success': True, 'msg': 'Attendance recorded'})
    except Exception as e:
        logging.error("Error in submit_attendance: %s", e)
        traceback.print_exc()
        return jsonify({'success': False, 'msg': 'Server error submitting attendance', 'details': str(e)}), 500


# ---------- API: attendance list (FINAL 24-HOUR ROLLING WINDOW LOGIC) ----------
@app.route('/api/attendance/<int:section_id>')
def attendance_list(section_id):
    try:
        conn = get_db()
        c = conn.cursor()

        # Retrieve the FIRST submission per student that was recorded within the last 24 hours.
        rows = c.execute('''
            SELECT reg_no, photo_path, timestamp
            FROM attendance
            WHERE id IN (
                SELECT MIN(id)
                FROM attendance
                WHERE section_id=?
                AND datetime(timestamp) >= datetime('now','-1 day') -- 24-hour rolling window
                GROUP BY reg_no
            )
            ORDER BY timestamp DESC
        ''', (section_id,)).fetchall()

        data = [{'reg_no': r[0], 'photo_path': r[1], 'timestamp': r[2]} for r in rows]
        
        return jsonify({'success': True, 'items': data})
    except Exception as e:
        logging.error("Error in attendance_list: %s", e)
        traceback.print_exc()
        return jsonify({'success': False, 'msg': 'Server error listing attendance', 'details': str(e)}), 500

# ---------- API: reload known encodings ----------
@app.route('/api/reload_encodings', methods=['POST'])
def reload_encodings():
    global KNOWN
    try:
        KNOWN = load_known_encodings()
        return jsonify({'success': True, 'count': len(KNOWN)})
    except Exception as e:
        logging.error("Error in reload_encodings: %s", e)
        traceback.print_exc()
        return jsonify({'success': False, 'msg': str(e)}), 500

# ---------- Run ----------
if __name__ == '__main__':
    logging.info("Starting app; DB=%s PHOTOS=%s", DB_PATH, PHOTOS_DIR)
    app.run(host='0.0.0.0', port=5000, debug=True)