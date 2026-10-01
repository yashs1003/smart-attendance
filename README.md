# Smart Attendance Management System

A working Flask web app for the "Smart Attendance Management System" mini-project,
with a QR code that **auto-refreshes every 15 seconds** during a live lecture so
students can't share a screenshot to mark a friend present.

## Features implemented
- Student login / Faculty login (separate roles, session-based auth)
- Faculty: **Start Lecture** → generates a live, auto-refreshing QR code
- Students: scan via camera (in-browser, no app needed) or type the code manually
- Automatic attendance-percentage calculation
- Subject-wise attendance breakdown
- Monthly reports (student view + faculty view)
- Low-attendance warning banner (< 75%, configurable)
- Full attendance history log
- Demo data seeded automatically on first run

## Setup

```bash
cd smart_attendance
pip install -r requirements.txt
python app.py
```

Open **http://127.0.0.1:5000** in your browser — this is enough to try it out
alone on one computer. To use it from phones or across multiple devices/networks
at once, see the two sections below.

## Demo accounts (auto-created)

| Role    | Username  | Password | Name           |
|---------|-----------|----------|----------------|
| Faculty | faculty1  | pass123  | Prof. Mehta    |
| Student | student1  | pass123  | Yash Swain     |
| Student | student2  | pass123  | Rajdatt Sawant |

Seeded subjects: AI & AD (AIAD101), .Net Core (DOTNET101), Full Stack Practical
(FSP101) — both students are enrolled in all three subjects.

## Making it run independently of your PC (permanent hosting)

Everything above (ngrok, LAN mode) still needs your PC on and both terminals
running. To make the app work as its own standalone service — reachable any
time, from any device, on any network, even with your PC completely off —
deploy it to a free cloud host. The project is already set up for this
(`Procfile`, `gunicorn`, `runtime.txt`).

**Using Render (free tier, recommended — no credit card needed):**

1. Create a free GitHub account if you don't have one, and push this
   `smart_attendance` folder to a new GitHub repository:
   ```bash
   cd smart_attendance
   git init
   git add .
   git commit -m "Smart Attendance Management System"
   git branch -M main
   git remote add origin https://github.com/<your-username>/<repo-name>.git
   git push -u origin main
   ```
2. Go to https://render.com → sign up free → **New → Web Service**.
3. Connect your GitHub account and pick the repository you just pushed.
4. Render should auto-detect Python. Set:
   - **Build Command:** `pip install -r requirements.txt`
   - **Start Command:** `gunicorn app:app`
5. Click **Create Web Service**. Render builds and deploys it — after a couple
   of minutes you'll get a permanent URL like
   `https://smart-attendance-xxxx.onrender.com`.
6. That URL now works forever (while the free service is active) from **any**
   device, any network, any time — you don't need to run anything on your own
   PC at all anymore.

**Two things to know about the free tier:**
- **Cold start:** a free Render service "sleeps" after ~15 minutes with no
  traffic. The first request after that takes ~30–50 seconds to wake up — normal
  free-tier behavior, not a bug. Later requests are instant.
- **Data resets on redeploy:** the free tier's disk is ephemeral, so the SQLite
  database (and any new attendance records) resets to the seeded demo data
  whenever you push a new deploy — but persists fine between requests otherwise.
  For a college demo/submission this is normally not an issue; if you need
  attendance data to survive redeploys long-term, Render's free PostgreSQL
  add-on is the next step (a bigger change, not needed for a working demo).

**Alternative:** PythonAnywhere's free tier also works well for Flask +
SQLite and keeps the database file persistent across restarts, at the cost of a
slightly more manual setup (upload files + configure one WSGI file through
their dashboard rather than a git push). Worth considering if the data-reset
behavior above is a dealbreaker for your use case.

## Using it across DIFFERENT networks at the same time (via your own PC)

If faculty and students are on different Wi-Fi networks or mobile data — not all
on the same router — your PC's local IP (`192.168.x.x`) won't reach them; that
address only works within one network. Use a tunnel to get a public URL instead:

1. **Install ngrok** (free): https://ngrok.com/download — sign up for a free
   account, then follow their one-line `ngrok config add-authtoken <your-token>`
   setup step shown on their dashboard.
2. **Run the Flask app normally** (plain HTTP is fine — the tunnel handles
   HTTPS):
   ```bash
   python app.py
   ```
3. **In a second terminal**, start the tunnel:
   ```bash
   ngrok http 5000
   ```
   ngrok will print a public URL like `https://a1b2-203-0-113-5.ngrok-free.app`.
4. **Share that exact URL** with everyone — faculty and every student, regardless
   of what network or country they're on. Everyone opens the same link, logs in
   with their own account, and it all talks to the one Flask server + database
   on your PC in real time. No security warnings, no "not private" pop-ups —
   ngrok provides a real trusted HTTPS certificate, so phone cameras work
   immediately.
5. Keep both terminals (Flask + ngrok) running for the whole session — closing
   either one ends access for everyone. The free ngrok URL also changes each
   time you restart the tunnel, so re-share the new link if you stop and restart
   it.

This is the best option for a live demo/viva where the examiner, faculty, and
students might all be on different networks (e.g. campus Wi-Fi vs. mobile data).

## Using it on the same Wi-Fi only (no tunnel needed)

If every device really is on the same Wi-Fi network as your PC, you can skip
ngrok and expose the app directly over your LAN with a self-signed HTTPS
certificate (still needed for phone camera access — plain HTTP only gets camera
permission on `localhost`):

```bash
# Windows PowerShell / Mac / Linux
set USE_ADHOC_SSL=1        # Windows cmd: use `set` ; Mac/Linux: use `export`
python app.py
```

1. Find your PC's local IP: `ipconfig` (Windows) or `ifconfig` / `ip a`
   (Mac/Linux) → look for the Wi-Fi adapter's IPv4/`inet` address, e.g.
   `192.168.0.103`.
2. On each phone (same Wi-Fi), open `https://<your-PC-IP>:5000`.
3. Accept the "connection not private" warning (tap **Advanced → Proceed** —
   expected for a self-signed certificate on your own network).
4. Log in and use the app as normal.

If a phone's camera still won't open in either mode, use the **manual code
entry** box on the scan page instead — it needs no camera permission at all and
works identically.

## How the "changing QR code" works

1. Faculty clicks **Start Lecture** on a subject → a `Lecture` row is created with a
   random token and a 15-second expiry.
2. The lecture screen polls `/faculty/lecture/<id>/qr_data` every 2 seconds. When the
   token has expired, the server silently generates a **new random token** and a new
   QR image is pushed to the screen — this is the "QR that changes every lecture"
   behaviour, applied continuously through the lecture so it keeps refreshing.
3. When a student scans, the browser POSTs the decoded string to
   `/student/mark_attendance`. The server checks:
   - does an active lecture currently have this exact token, and has it not expired?
   - is the student enrolled in that subject?
   - have they already marked attendance for this specific lecture?
4. If all checks pass, an `AttendanceRecord` is saved and the live "present count"
   on the faculty screen updates on its next poll.

## Project structure

```
smart_attendance/
├── app.py                  # Flask app: models, routes, QR + attendance logic
├── requirements.txt
├── attendance.db           # SQLite DB (auto-created, demo data pre-seeded)
├── templates/               # Jinja2 HTML templates
│   ├── base.html
│   ├── login.html
│   ├── faculty_dashboard.html
│   ├── run_lecture.html    # live auto-refreshing QR screen
│   ├── faculty_reports.html
│   ├── student_dashboard.html
│   ├── scan.html            # camera scanner + manual entry
│   ├── history.html
│   └── monthly_report.html
└── static/
    └── style.css
```

## Extending it further (ideas for your report's "Future Scope" section)
- Add geofencing (only accept scans from within classroom Wi-Fi/GPS range)
- Add face-verification alongside the QR to stop proxy attendance entirely
- Email/SMS alert to students automatically when they drop below the threshold
- Admin role to manage subjects/enrollments through the UI instead of seed data
- Export monthly reports to PDF/Excel
