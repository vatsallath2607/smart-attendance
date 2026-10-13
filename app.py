# app.py
import os
import io
import math
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

# ---------- Geo helpers ----------
def haversine_distance(lat1, lon1, lat2, lon2):
    """
    Calculate the great-circle distance between two GPS coordinates in meters
    using the Haversine formula.
    """
    R = 6371000.0  # Earth's radius in meters
    phi1 = math.radians(lat1)
    phi2 = math.radians(lat2)
    delta_phi = math.radians(lat2 - lat1)
    delta_lambda = math.radians(lon2 - lon1)

    a = (math.sin(delta_phi / 2.0) ** 2 +
         math.cos(phi1) * math.cos(phi2) * math.sin(delta_lambda / 2.0) ** 2)
    c = 2.0 * math.atan2(math.sqrt(a), math.sqrt(1.0 - a))
    return R * c

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
      latitude REAL,
      longitude REAL,
      range_meters REAL,
      is_range_active INTEGER DEFAULT 0,
      FOREIGN KEY(section_id) REFERENCES sections(id)
    );
    CREATE TABLE IF NOT EXISTS attendance (
      id INTEGER PRIMARY KEY AUTOINCREMENT,
      reg_no TEXT NOT NULL,
      section_id INTEGER NOT NULL,
      session_id INTEGER NOT NULL,
      photo_path TEXT,
      timestamp DATETIME DEFAULT CURRENT_TIMESTAMP,
      distance REAL
    );
    """)
    # Migration checks for existing databases
    c.execute("PRAGMA table_info(sessions)")
    sess_cols = [col[1] for col in c.fetchall()]
    if 'latitude' not in sess_cols:
        c.execute("ALTER TABLE sessions ADD COLUMN latitude REAL")
    if 'longitude' not in sess_cols:
        c.execute("ALTER TABLE sessions ADD COLUMN longitude REAL")
    if 'range_meters' not in sess_cols:
        c.execute("ALTER TABLE sessions ADD COLUMN range_meters REAL DEFAULT 10.0")
    if 'is_range_active' not in sess_cols:
        c.execute("ALTER TABLE sessions ADD COLUMN is_range_active INTEGER DEFAULT 1")
    if 'range_code' not in sess_cols:
        c.execute("ALTER TABLE sessions ADD COLUMN range_code TEXT")
    if 'faculty_name' not in sess_cols:
        c.execute("ALTER TABLE sessions ADD COLUMN faculty_name TEXT")
    if 'session_num' not in sess_cols:
        c.execute("ALTER TABLE sessions ADD COLUMN session_num INTEGER DEFAULT 1")

    c.execute("PRAGMA table_info(attendance)")
    att_cols = [col[1] for col in c.fetchall()]
    if 'distance' not in att_cols:
        c.execute("ALTER TABLE attendance ADD COLUMN distance REAL")
    if 'faculty_name' not in att_cols:
        c.execute("ALTER TABLE attendance ADD COLUMN faculty_name TEXT")
    if 'status' not in att_cols:
        c.execute("ALTER TABLE attendance ADD COLUMN status TEXT DEFAULT 'PENDING'")
        c.execute("UPDATE attendance SET status = 'MARKED' WHERE status IS NULL")
    if 'session_num' not in att_cols:
        c.execute("ALTER TABLE attendance ADD COLUMN session_num INTEGER DEFAULT 1")

    # Seed default sections if table is empty
    c.execute("SELECT COUNT(*) FROM sections")
    if c.fetchone()[0] == 0:
        default_sections = ['Session 1', 'Session 2', 'Section 1', 'Section 2', 'SectionU1', 'SectionP2']
        for s in default_sections:
            c.execute("INSERT INTO sections(name) VALUES(?)", (s,))

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

    # If empty, auto-register from students.csv
    if not known and os.path.exists('students.csv'):
        try:
            from register_students import register_from_csv
            logging.info("Auto-registering students from students.csv on initial startup...")
            register_from_csv('students.csv')
            if os.path.exists(KNOWN_PICKLE):
                with open(KNOWN_PICKLE, 'rb') as f:
                    known = pickle.load(f)
        except Exception as e:
            logging.warning("Auto-registration from csv failed: %s", e)
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
        if not rows:
            default_sections = ['Session 1', 'Session 2', 'Section 1', 'Section 2', 'SectionU1', 'SectionP2']
            for s in default_sections:
                c.execute("INSERT INTO sections(name) VALUES(?)", (s,))
            conn.commit()
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

        faculty_name = data.get('faculty_name', '').strip() or 'Faculty'
        try:
            session_num = int(data.get('session_num', 1))
        except Exception:
            session_num = 1
        is_range = bool(data.get('is_range', True)) # default to True for geofenced sessions
        latitude = data.get('latitude')
        longitude = data.get('longitude')
        range_meters = data.get('range_meters', 10.0)

        if is_range and latitude is not None and longitude is not None:
            try:
                latitude = float(latitude)
                longitude = float(longitude)
                range_meters = float(range_meters) if range_meters else 10.0
            except (TypeError, ValueError):
                return jsonify({
                    'success': False,
                    'msg': 'Valid faculty GPS coordinates (latitude, longitude) are required for a range session.'
                }), 400
        else:
            latitude = None
            longitude = None
            range_meters = None
            is_range = False

        # Generate distinct Session Code and Range Code
        session_code = 'SES-' + uuid.uuid4().hex[:6].upper()
        range_code = 'RNG-' + uuid.uuid4().hex[6:12].upper()
        started = datetime.utcnow()
        expires = started + timedelta(minutes=duration_min)

        conn = get_db()
        c = conn.cursor()
        c.execute(
            '''INSERT INTO sessions(section_id,session_num,code,range_code,faculty_name,started_at,expires_at,active,latitude,longitude,range_meters,is_range_active)
               VALUES(?,?,?,?,?,?,?,1,?,?,?,?)''',
            (section_id, session_num, session_code, range_code, faculty_name, started.isoformat(), expires.isoformat(), latitude, longitude, range_meters, 1 if is_range else 0),
        )
        conn.commit()
        session_id = c.lastrowid

        return jsonify({
            'success': True,
            'session_id': session_id,
            'session_num': session_num,
            'code': session_code,
            'session_code': session_code,
            'range_code': range_code,
            'faculty_name': faculty_name,
            'expires_at': expires.isoformat(),
            'is_range': is_range,
            'range_meters': range_meters,
            'latitude': latitude,
            'longitude': longitude
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
            c.execute('UPDATE sessions SET active=0 WHERE (code=? OR range_code=?) AND section_id=? AND active=1', (code, code, section_id))
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


# ---------- API: get current active session for section ----------
@app.route('/api/current_session/<int:section_id>')
def get_current_session(section_id):
    try:
        conn = get_db()
        c = conn.cursor()
        c.execute('''
            SELECT id, session_num, code, range_code, faculty_name, expires_at, range_meters, latitude, longitude
            FROM sessions
            WHERE section_id=? AND active=1
            ORDER BY id DESC LIMIT 1
        ''', (section_id,))
        row = c.fetchone()
        if not row:
            return jsonify({'success': False, 'msg': 'No active session'})
        
        # Check if expired
        try:
            if datetime.fromisoformat(row['expires_at']) < datetime.utcnow():
                c.execute('UPDATE sessions SET active=0 WHERE id=?', (row['id'],))
                conn.commit()
                return jsonify({'success': False, 'msg': 'Session expired'})
        except Exception:
            pass

        return jsonify({
            'success': True,
            'session_id': row['id'],
            'session_num': row['session_num'] or 1,
            'session_code': row['code'],
            'code': row['code'],
            'range_code': row['range_code'],
            'faculty_name': row['faculty_name'],
            'expires_at': row['expires_at'],
            'range_meters': row['range_meters'],
            'latitude': row['latitude'],
            'longitude': row['longitude']
        })
    except Exception as e:
        return jsonify({'success': False, 'msg': str(e)}), 500


# ---------- API: faculty submit attendance ----------
@app.route('/api/faculty_submit_attendance', methods=['POST'])
def faculty_submit_attendance():
    """
    Faculty submits and officially marks the attendance for the section/session.
    No registration number required — faculty marks all verified attendees!
    """
    try:
        data = request.get_json(force=True, silent=True) or {}
        section_id = data.get('section_id')
        session_id = data.get('session_id')
        faculty_name = (data.get('faculty_name') or '').strip() or 'Faculty'

        if not section_id:
            return jsonify({'success': False, 'msg': 'Missing section ID'}), 400

        try:
            section_id = int(section_id)
        except Exception:
            return jsonify({'success': False, 'msg': 'Invalid section ID'}), 400

        conn = get_db()
        c = conn.cursor()

        # Find active or referenced session to retrieve the faculty name who started attendance
        sess = None
        if session_id:
            sess = c.execute(
                'SELECT id, faculty_name FROM sessions WHERE id=?',
                (session_id,)
            ).fetchone()
        if not sess:
            sess = c.execute(
                'SELECT id, faculty_name FROM sessions WHERE section_id=? AND active=1 ORDER BY id DESC LIMIT 1',
                (section_id,)
            ).fetchone()

        # If faculty name was saved when starting session, keep that name
        if sess and sess[1] and str(sess[1]).strip():
            faculty_name = sess[1].strip()
        if sess:
            session_id = sess[0]

        # Update all PENDING check-ins to 'ATTENDANCE RECORDED'
        if session_id:
            c.execute('''
                UPDATE attendance
                SET status = 'ATTENDANCE RECORDED', faculty_name = ?
                WHERE section_id = ? AND session_id = ? AND status = 'PENDING'
            ''', (faculty_name, section_id, session_id))
            marked_count = c.rowcount
            # Stop the session so no more entries can be made
            c.execute('UPDATE sessions SET active = 0 WHERE id = ?', (session_id,))
        else:
            c.execute('''
                UPDATE attendance
                SET status = 'ATTENDANCE RECORDED', faculty_name = ?
                WHERE section_id = ? AND status = 'PENDING'
            ''', (faculty_name, section_id))
            marked_count = c.rowcount

        conn.commit()

        if marked_count == 0:
            already = c.execute(
                'SELECT COUNT(*) FROM attendance WHERE section_id=? AND status IN ("ATTENDANCE RECORDED", "ATTENDANCE SUBMITTED", "MARKED") AND datetime(timestamp) >= datetime("now","-1 day")',
                (section_id,)
            ).fetchone()[0]
            if already > 0:
                return jsonify({
                    'success': True,
                    'marked_count': 0,
                    'msg': f'Attendance for this section is already recorded ({already} records recorded by {faculty_name}).'
                })
            return jsonify({
                'success': False,
                'msg': 'No student check-ins found to submit for this section.'
            }), 400

        return jsonify({
            'success': True,
            'marked_count': marked_count,
            'msg': f'Attendance successfully submitted and marked for {marked_count} student(s) by {faculty_name}!'
        })
    except Exception as e:
        logging.error("Error in faculty_submit_attendance: %s", e)
        traceback.print_exc()
        return jsonify({'success': False, 'msg': 'Server error submitting attendance', 'details': str(e)}), 500
        

# ---------- API: submit attendance (student submission with dual codes) ----------
@app.route('/api/submit_attendance', methods=['POST'])
def submit_attendance():
    try:
        reg_no = (request.form.get('reg_no') or '').strip()
        session_code = (request.form.get('session_code') or request.form.get('code') or '').strip()
        range_code = (request.form.get('range_code') or '').strip()
        section = (request.form.get('section') or '').strip()

        # Require BOTH Session Code and Range Code
        if not reg_no or not session_code or not range_code or not section:
            return jsonify({
                'success': False,
                'msg': 'Both Session Code and Range Code are required! Attendance cannot be marked without both codes.'
            }), 400

        # ensure numeric section id
        try:
            section_id = int(section)
        except Exception:
            return jsonify({'success': False, 'msg': 'Invalid section id'}), 400

        conn = get_db()
        c = conn.cursor()

        # validate session by matching session_code and section_id
        c.execute(
            '''SELECT id, expires_at, active, latitude, longitude, range_meters, is_range_active, code, range_code, faculty_name, session_num 
               FROM sessions WHERE (UPPER(code)=UPPER(?) OR UPPER(range_code)=UPPER(?)) AND section_id=? ORDER BY id DESC''',
            (session_code, session_code, section_id)
        )
        row = c.fetchone()
        if not row:
            return jsonify({'success': False, 'msg': 'Invalid Session Code or Section'}), 400
        session_id, expires_at, active, sess_lat, sess_lon, sess_range_meters, is_range_active, db_code, db_range_code, faculty_name, sess_num = row
        sess_num = sess_num or 1

        # Verify range_code matches the session's range_code
        if db_range_code and db_range_code.strip():
            if range_code.strip().upper() != db_range_code.strip().upper():
                return jsonify({
                    'success': False,
                    'msg': 'Invalid Range Code! Both Session Code and Range Code must match the active session.'
                }), 400

        # Verify session_code matches db_code
        if db_code and db_code.strip():
            if session_code.strip().upper() != db_code.strip().upper():
                return jsonify({
                    'success': False,
                    'msg': 'Invalid Session Code! Both Session Code and Range Code must match the active session.'
                }), 400

        if not active:
            return jsonify({'success': False, 'msg': 'Session is closed or already submitted by faculty.'}), 400
        try:
            if datetime.fromisoformat(expires_at) < datetime.utcnow():
                return jsonify({'success': False, 'msg': 'Session code has expired'}), 400
        except Exception:
            pass

        # --- 10M RANGE GEOFENCING CHECK ---
        student_dist = None
        if is_range_active and sess_lat is not None and sess_lon is not None:
            student_lat = request.form.get('latitude')
            student_lon = request.form.get('longitude')
            if not student_lat or not student_lon:
                return jsonify({
                    'success': False,
                    'msg': 'Location required: This session requires 10m range verification. Please enable GPS location on your device.'
                }), 400
            try:
                student_lat = float(student_lat)
                student_lon = float(student_lon)
            except (TypeError, ValueError):
                return jsonify({'success': False, 'msg': 'Invalid GPS coordinates received for range verification.'}), 400

            student_dist = haversine_distance(sess_lat, sess_lon, student_lat, student_lon)
            max_range = float(sess_range_meters) if sess_range_meters else 10.0

            # GPS accuracy margin: Indoor/browser GPS readings naturally have a 15-20m circle of uncertainty.
            # Allow a tolerance margin based on device GPS accuracy reading or minimum 20m tolerance.
            student_acc = request.form.get('accuracy')
            try:
                acc_val = float(student_acc) if student_acc else 0.0
            except Exception:
                acc_val = 0.0

            effective_max_range = max(max_range, max_range + min(acc_val, 25.0), 30.0 if max_range <= 10.0 else max_range)

            if student_dist > effective_max_range:
                return jsonify({
                    'success': False,
                    'msg': f'Out of Range: You are {student_dist:.1f}m away from the faculty location! Attendance is only allowed within {max_range:.0f}m.'
                }), 403

        # --- DUPLICATE CHECK: has this reg_no already submitted for this session? ---
        c.execute('SELECT 1 FROM attendance WHERE reg_no=? AND session_id=? LIMIT 1', (reg_no, session_id))
        if c.fetchone():
            return jsonify({'success': False, 'msg': 'Attendance check-in already submitted for this session'}), 409

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

        # insert attendance with PENDING status (officially marked once faculty submits)
        c.execute('INSERT INTO attendance(reg_no,section_id,session_id,session_num,photo_path,distance,faculty_name,status) VALUES(?,?,?,?,?,?,?,?)',
                  (reg_no, section_id, session_id, sess_num, saved_path, round(student_dist, 2) if student_dist is not None else None, faculty_name, 'PENDING'))
        conn.commit()

        dist_suffix = f' (Verified within range: {student_dist:.1f}m)' if student_dist is not None else ''
        return jsonify({'success': True, 'msg': f'Check-in verified{dist_suffix}! Attendance will be marked once faculty submits attendance.'})
    except Exception as e:
        logging.error("Error in submit_attendance: %s", e)
        traceback.print_exc()
        return jsonify({'success': False, 'msg': 'Server error submitting attendance', 'details': str(e)}), 500


# ---------- API: attendance list (FINAL 24-HOUR ROLLING WINDOW LOGIC) ----------
@app.route('/api/attendance/<int:section_id>')
def attendance_list(section_id):
    try:
        session_filter = request.args.get('session_num')
        conn = get_db()
        c = conn.cursor()

        if session_filter and session_filter.strip():
            try:
                s_num = int(session_filter)
                rows = c.execute('''
                    SELECT a.reg_no, a.photo_path, a.timestamp, a.distance, 
                           COALESCE(NULLIF(s.faculty_name, ''), NULLIF(a.faculty_name, ''), 'Faculty') AS faculty_name,
                           COALESCE(a.status, 'ATTENDANCE RECORDED') AS status,
                           COALESCE(a.session_num, s.session_num, 1) AS session_num
                    FROM attendance a
                    LEFT JOIN sessions s ON a.session_id = s.id
                    WHERE a.id IN (
                        SELECT MAX(id)
                        FROM attendance
                        WHERE section_id=? AND (session_num=? OR session_num IS NULL)
                        AND datetime(timestamp) >= datetime('now','-1 day')
                        GROUP BY reg_no
                    )
                    ORDER BY a.timestamp DESC
                ''', (section_id, s_num)).fetchall()
            except Exception:
                rows = []
        else:
            # Retrieve latest submission per student and session_num recorded within the last 24 hours.
            # Grouping by (reg_no, session_num) ensures both Session 1 and Session 2 records exist side-by-side.
            rows = c.execute('''
                SELECT a.reg_no, a.photo_path, a.timestamp, a.distance, 
                       COALESCE(NULLIF(s.faculty_name, ''), NULLIF(a.faculty_name, ''), 'Faculty') AS faculty_name,
                       COALESCE(a.status, 'ATTENDANCE RECORDED') AS status,
                       COALESCE(a.session_num, s.session_num, 1) AS session_num
                FROM attendance a
                LEFT JOIN sessions s ON a.session_id = s.id
                WHERE a.id IN (
                    SELECT MAX(id)
                    FROM attendance
                    WHERE section_id=?
                    AND datetime(timestamp) >= datetime('now','-1 day') -- 24-hour rolling window
                    GROUP BY reg_no, COALESCE(session_num, 1)
                )
                ORDER BY a.timestamp DESC
            ''', (section_id,)).fetchall()

        data = [{
            'reg_no': r[0],
            'photo_path': r[1],
            'timestamp': r[2],
            'distance': r[3],
            'faculty_name': r[4],
            'status': r[5],
            'session_num': r[6]
        } for r in rows]
        
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