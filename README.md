# Smart Attendance System (AI Face Recognition)

An automated attendance system using **Flask**, **OpenCV**, and **Face Recognition**.

## Features
- **Face Verification**: Uses 128-d encodings via Dlib.
- **Liveness Detection**: Prevents photo-spoofing using eye-blink heuristics.
- **24-Hour Rolling Window**: Dynamic visibility for faculty records.
- **Section Management**: Supports multiple sections with unique session codes.

## Installation
1. Clone the repo: `git clone https://github.com/yourusername/smart-attendance.git`
2. Install dependencies: `pip install -r requirements.txt`
3. Register students: `python register_students.py students.csv`
4. Run app: `python app.py`