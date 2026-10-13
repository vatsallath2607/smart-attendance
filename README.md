# Smart Attendance System (AI Face Recognition)

An automated attendance system using **Flask**, **OpenCV**, and **Face Recognition**.

## Features
- **Face Verification**: Uses 128-d encodings via Dlib.
- **Liveness Detection**: Prevents photo-spoofing using eye-blink heuristics.
- **Dual-Code Verification**: Generates distinct **Session Code** (`SES-XXXXXX`) and **Range Code** (`RNG-XXXXXX`). Both codes are mandatory for student attendance submission.
- **10-Meter Range Geofencing**: Students farther than 10 meters from the faculty location are strictly blocked from submitting attendance.
- **Faculty Dashboard Control**: Faculty enters their name, selects section, starts attendance, and can also directly submit/mark attendance manually.
- **24-Hour Rolling Window**: Dynamic visibility for faculty records.
- **Section Management**: Supports multiple sections with CSV export and real-time distance tracking.

## Installation
1. Clone the repo: `git clone https://github.com/yourusername/smart-attendance.git`
2. Install dependencies: `pip install -r requirements.txt`
3. Register students: `python register_students.py students.csv`
4. Run app: `python app.py`

## Deployment

### Why Can't Netlify Host Python/OpenCV Alone?
Netlify is designed for static websites and serverless functions (Node.js/Go). This project requires a continuous Python runtime, heavy C++ libraries (`dlib`, `face_recognition`, OpenCV), and a persistent database (`attendance.db`).

### Recommended Deployment Options:

#### Option 1: Full App on Render / Railway / Koyeb (Simplest & Recommended)
1. Push your repository to GitHub.
2. Sign up at [Render.com](https://render.com) or [Railway.app](https://railway.app).
3. Create a **New Web Service** pointing to your repository.
4. Select **Docker** (it automatically uses the included `Dockerfile` with all C++ and OpenCV libraries pre-configured).
5. Deploy! Both frontend and backend will be live on your custom `.onrender.com` or `.up.railway.app` URL.

#### Option 2: Split Deployment (Netlify Frontend + Cloud Backend)
1. Deploy the backend (Python Flask) on Render or Railway using the included `Dockerfile`.
2. Copy your backend URL (e.g. `https://my-attendance-api.onrender.com`).
3. In [netlify.toml](file:///c:/Users/Public/smart-attenadnce/netlify.toml) and [static/_redirects](file:///c:/Users/Public/smart-attenadnce/static/_redirects), replace `https://your-smart-attendance-backend.onrender.com` with your real backend URL.
4. Deploy the repository to **Netlify**:
   - Build directory / Publish directory: `static`
   - Netlify will serve the frontend and automatically proxy all `/api/*` and image requests to your Python backend!