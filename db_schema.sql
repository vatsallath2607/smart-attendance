-- sections
CREATE TABLE IF NOT EXISTS sections (
id INTEGER PRIMARY KEY AUTOINCREMENT,
name TEXT NOT NULL
);


-- students
CREATE TABLE IF NOT EXISTS students (
id INTEGER PRIMARY KEY AUTOINCREMENT,
reg_no TEXT UNIQUE NOT NULL,
name TEXT,
section_id INTEGER,
image_path TEXT,
encoding BLOB, -- store face encoding as blob (optional)
FOREIGN KEY(section_id) REFERENCES sections(id)
);


-- sessions (attendance windows)
CREATE TABLE IF NOT EXISTS sessions (
id INTEGER PRIMARY KEY AUTOINCREMENT,
section_id INTEGER NOT NULL,
session_num INTEGER DEFAULT 1,
code TEXT NOT NULL,
range_code TEXT,
faculty_name TEXT,
started_at DATETIME NOT NULL,
expires_at DATETIME NOT NULL,
active INTEGER DEFAULT 1,
latitude REAL,
longitude REAL,
range_meters REAL,
is_range_active INTEGER DEFAULT 1,
FOREIGN KEY(section_id) REFERENCES sections(id)
);


-- attendance
CREATE TABLE IF NOT EXISTS attendance (
id INTEGER PRIMARY KEY AUTOINCREMENT,
reg_no TEXT NOT NULL,
section_id INTEGER NOT NULL,
session_id INTEGER NOT NULL,
session_num INTEGER DEFAULT 1,
photo_path TEXT,
timestamp DATETIME DEFAULT CURRENT_TIMESTAMP,
distance REAL,
faculty_name TEXT,
status TEXT DEFAULT 'PENDING'
);