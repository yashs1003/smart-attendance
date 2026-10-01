"""
Smart Attendance Management System
-----------------------------------
Features:
- Student login / Faculty login
- Faculty starts a lecture -> a QR code is generated that changes automatically
  every few seconds (a fresh random token, stored server-side with an expiry).
- Students scan the live QR (camera, via the html5-qrcode JS library) or type
  the code manually -> attendance is recorded instantly.
- Automatic percentage calculation, subject-wise breakdown
- Monthly reports
- Low-attendance warning (< 75%)
- Full attendance history

Run:
    pip install -r requirements.txt
    python app.py
Then open http://127.0.0.1:5000

Demo accounts (also auto-created on first run, see seed_data()):
    Faculty : faculty1 / pass123
    Students: student1 / pass123, student2 / pass123, student3 / pass123
"""

import os
import secrets
from datetime import datetime, timedelta, date
from calendar import month_name

from flask import Flask, render_template, redirect, url_for, request, flash, jsonify
from flask_sqlalchemy import SQLAlchemy
from flask_login import (
    LoginManager, UserMixin, login_user, logout_user,
    login_required, current_user
)
from werkzeug.security import generate_password_hash, check_password_hash
import qrcode
import io
import base64

BASE_DIR = os.path.dirname(os.path.abspath(__file__))

app = Flask(__name__)
app.config["SECRET_KEY"] = os.environ.get("SECRET_KEY", "change-this-secret-key-in-production")
app.config["SQLALCHEMY_DATABASE_URI"] = f"sqlite:///{os.path.join(BASE_DIR, 'attendance.db')}"
app.config["SQLALCHEMY_TRACK_MODIFICATIONS"] = False

db = SQLAlchemy(app)
login_manager = LoginManager(app)
login_manager.login_view = "login"

# How long a single QR token stays valid before it auto-refreshes (seconds)
QR_REFRESH_SECONDS = 15
LOW_ATTENDANCE_THRESHOLD = 75.0


# ---------------------------------------------------------------------------
# MODELS
# ---------------------------------------------------------------------------

class User(UserMixin, db.Model):
    id = db.Column(db.Integer, primary_key=True)
    username = db.Column(db.String(50), unique=True, nullable=False)
    password_hash = db.Column(db.String(255), nullable=False)
    name = db.Column(db.String(100), nullable=False)
    role = db.Column(db.String(10), nullable=False)  # 'student' or 'faculty'
    roll_no = db.Column(db.String(20))  # only for students

    def set_password(self, pw):
        self.password_hash = generate_password_hash(pw)

    def check_password(self, pw):
        return check_password_hash(self.password_hash, pw)


class Subject(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    code = db.Column(db.String(20), unique=True, nullable=False)
    name = db.Column(db.String(100), nullable=False)
    faculty_id = db.Column(db.Integer, db.ForeignKey("user.id"), nullable=False)

    faculty = db.relationship("User", backref="subjects_taught")


class Enrollment(db.Model):
    """Which students are enrolled in which subjects."""
    id = db.Column(db.Integer, primary_key=True)
    student_id = db.Column(db.Integer, db.ForeignKey("user.id"), nullable=False)
    subject_id = db.Column(db.Integer, db.ForeignKey("subject.id"), nullable=False)

    student = db.relationship("User", backref="enrollments")
    subject = db.relationship("Subject", backref="enrollments")


class Lecture(db.Model):
    """One class session. Holds the currently-valid QR token."""
    id = db.Column(db.Integer, primary_key=True)
    subject_id = db.Column(db.Integer, db.ForeignKey("subject.id"), nullable=False)
    date = db.Column(db.Date, default=date.today, nullable=False)
    started_at = db.Column(db.DateTime, default=datetime.utcnow)
    ended_at = db.Column(db.DateTime)
    is_active = db.Column(db.Boolean, default=True)

    current_token = db.Column(db.String(64))
    token_expires_at = db.Column(db.DateTime)

    subject = db.relationship("Subject", backref="lectures")

    def refresh_token(self):
        self.current_token = secrets.token_hex(8)
        self.token_expires_at = datetime.utcnow() + timedelta(seconds=QR_REFRESH_SECONDS)

    def token_is_valid(self, token):
        return (
            self.is_active
            and self.current_token == token
            and self.token_expires_at
            and datetime.utcnow() <= self.token_expires_at
        )


class AttendanceRecord(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    lecture_id = db.Column(db.Integer, db.ForeignKey("lecture.id"), nullable=False)
    student_id = db.Column(db.Integer, db.ForeignKey("user.id"), nullable=False)
    marked_at = db.Column(db.DateTime, default=datetime.utcnow)

    lecture = db.relationship("Lecture", backref="attendance_records")
    student = db.relationship("User", backref="attendance_records")

    __table_args__ = (db.UniqueConstraint("lecture_id", "student_id", name="uix_lecture_student"),)


@login_manager.user_loader
def load_user(user_id):
    return User.query.get(int(user_id))


# ---------------------------------------------------------------------------
# HELPERS
# ---------------------------------------------------------------------------

def qr_image_base64(data: str) -> str:
    """Generate a QR PNG for `data` and return it as a base64 data-URI string."""
    img = qrcode.make(data)
    buf = io.BytesIO()
    img.save(buf, format="PNG")
    return "data:image/png;base64," + base64.b64encode(buf.getvalue()).decode()


def student_subject_stats(student):
    """Return list of dicts: subject-wise attendance stats for a student."""
    stats = []
    for enr in student.enrollments:
        subject = enr.subject
        total_lectures = Lecture.query.filter_by(subject_id=subject.id).count()
        attended = (
            AttendanceRecord.query
            .join(Lecture)
            .filter(Lecture.subject_id == subject.id, AttendanceRecord.student_id == student.id)
            .count()
        )
        pct = round((attended / total_lectures * 100), 1) if total_lectures else 0.0
        stats.append({
            "subject": subject,
            "total": total_lectures,
            "attended": attended,
            "percentage": pct,
            "low": pct < LOW_ATTENDANCE_THRESHOLD and total_lectures > 0,
        })
    return stats


def overall_percentage(stats):
    total = sum(s["total"] for s in stats)
    attended = sum(s["attended"] for s in stats)
    return round((attended / total * 100), 1) if total else 0.0


# ---------------------------------------------------------------------------
# AUTH ROUTES
# ---------------------------------------------------------------------------

@app.route("/")
def index():
    if current_user.is_authenticated:
        return redirect(url_for("faculty_dashboard" if current_user.role == "faculty" else "student_dashboard"))
    return redirect(url_for("login"))


@app.route("/login", methods=["GET", "POST"])
def login():
    if request.method == "POST":
        username = request.form["username"].strip()
        password = request.form["password"]
        role = request.form["role"]
        user = User.query.filter_by(username=username, role=role).first()
        if user and user.check_password(password):
            login_user(user)
            return redirect(url_for("faculty_dashboard" if role == "faculty" else "student_dashboard"))
        flash("Invalid username, password, or role.", "danger")
    return render_template("login.html")


@app.route("/logout")
@login_required
def logout():
    logout_user()
    return redirect(url_for("login"))


# ---------------------------------------------------------------------------
# FACULTY ROUTES
# ---------------------------------------------------------------------------

def faculty_required():
    return current_user.is_authenticated and current_user.role == "faculty"


@app.route("/faculty")
@login_required
def faculty_dashboard():
    if not faculty_required():
        return redirect(url_for("student_dashboard"))
    subjects = Subject.query.filter_by(faculty_id=current_user.id).all()
    active_lecture = Lecture.query.filter_by(is_active=True).join(Subject).filter(
        Subject.faculty_id == current_user.id
    ).first()
    return render_template("faculty_dashboard.html", subjects=subjects, active_lecture=active_lecture)


@app.route("/faculty/start_lecture/<int:subject_id>", methods=["POST"])
@login_required
def start_lecture(subject_id):
    if not faculty_required():
        return redirect(url_for("student_dashboard"))
    subject = Subject.query.get_or_404(subject_id)
    if subject.faculty_id != current_user.id:
        flash("Not your subject.", "danger")
        return redirect(url_for("faculty_dashboard"))

    # End any other active lecture for this faculty first
    for lec in Lecture.query.filter_by(is_active=True).join(Subject).filter(Subject.faculty_id == current_user.id):
        lec.is_active = False
        lec.ended_at = datetime.utcnow()

    lecture = Lecture(subject_id=subject.id)
    lecture.refresh_token()
    db.session.add(lecture)
    db.session.commit()
    return redirect(url_for("run_lecture", lecture_id=lecture.id))


@app.route("/faculty/lecture/<int:lecture_id>")
@login_required
def run_lecture(lecture_id):
    if not faculty_required():
        return redirect(url_for("student_dashboard"))
    lecture = Lecture.query.get_or_404(lecture_id)
    present_count = AttendanceRecord.query.filter_by(lecture_id=lecture.id).count()
    total_enrolled = Enrollment.query.filter_by(subject_id=lecture.subject_id).count()
    return render_template(
        "run_lecture.html",
        lecture=lecture,
        refresh_seconds=QR_REFRESH_SECONDS,
        present_count=present_count,
        total_enrolled=total_enrolled,
    )


@app.route("/faculty/lecture/<int:lecture_id>/qr_data")
@login_required
def lecture_qr_data(lecture_id):
    """Polled by the faculty page every few seconds to get a fresh QR + live count."""
    lecture = Lecture.query.get_or_404(lecture_id)
    if lecture.is_active and datetime.utcnow() >= lecture.token_expires_at:
        lecture.refresh_token()
        db.session.commit()
    present_count = AttendanceRecord.query.filter_by(lecture_id=lecture.id).count()
    total_enrolled = Enrollment.query.filter_by(subject_id=lecture.subject_id).count()
    seconds_left = max(0, int((lecture.token_expires_at - datetime.utcnow()).total_seconds())) if lecture.is_active else 0
    return jsonify({
        "qr_image": qr_image_base64(lecture.current_token) if lecture.is_active else None,
        "token": lecture.current_token if lecture.is_active else None,
        "is_active": lecture.is_active,
        "present_count": present_count,
        "total_enrolled": total_enrolled,
        "seconds_left": seconds_left,
    })


@app.route("/faculty/lecture/<int:lecture_id>/end", methods=["POST"])
@login_required
def end_lecture(lecture_id):
    if not faculty_required():
        return redirect(url_for("student_dashboard"))
    lecture = Lecture.query.get_or_404(lecture_id)
    lecture.is_active = False
    lecture.ended_at = datetime.utcnow()
    db.session.commit()
    flash("Lecture ended.", "success")
    return redirect(url_for("faculty_dashboard"))


@app.route("/faculty/reports/<int:subject_id>")
@login_required
def faculty_reports(subject_id):
    if not faculty_required():
        return redirect(url_for("student_dashboard"))
    subject = Subject.query.get_or_404(subject_id)
    lectures = Lecture.query.filter_by(subject_id=subject.id).order_by(Lecture.date).all()
    students = [e.student for e in Enrollment.query.filter_by(subject_id=subject.id)]

    rows = []
    for student in students:
        attended = AttendanceRecord.query.join(Lecture).filter(
            Lecture.subject_id == subject.id, AttendanceRecord.student_id == student.id
        ).count()
        total = len(lectures)
        pct = round((attended / total * 100), 1) if total else 0.0
        rows.append({"student": student, "attended": attended, "total": total, "pct": pct,
                      "low": pct < LOW_ATTENDANCE_THRESHOLD and total > 0})

    # monthly breakdown: {month_label: {student_id: count}}
    monthly = {}
    for lec in lectures:
        label = f"{month_name[lec.date.month]} {lec.date.year}"
        monthly.setdefault(label, {"total_lectures": 0, "by_student": {s.id: 0 for s in students}})
        monthly[label]["total_lectures"] += 1
    for rec in AttendanceRecord.query.join(Lecture).filter(Lecture.subject_id == subject.id):
        label = f"{month_name[rec.lecture.date.month]} {rec.lecture.date.year}"
        if label in monthly and rec.student_id in monthly[label]["by_student"]:
            monthly[label]["by_student"][rec.student_id] += 1

    return render_template(
        "faculty_reports.html", subject=subject, rows=rows, students=students,
        monthly=monthly, threshold=LOW_ATTENDANCE_THRESHOLD
    )


# ---------------------------------------------------------------------------
# STUDENT ROUTES
# ---------------------------------------------------------------------------

@app.route("/student")
@login_required
def student_dashboard():
    if current_user.role != "student":
        return redirect(url_for("faculty_dashboard"))
    stats = student_subject_stats(current_user)
    overall = overall_percentage(stats)
    low_subjects = [s for s in stats if s["low"]]
    return render_template("student_dashboard.html", stats=stats, overall=overall,
                            low_subjects=low_subjects, threshold=LOW_ATTENDANCE_THRESHOLD)


@app.route("/student/scan")
@login_required
def scan_page():
    if current_user.role != "student":
        return redirect(url_for("faculty_dashboard"))
    return render_template("scan.html")


@app.route("/student/mark_attendance", methods=["POST"])
@login_required
def mark_attendance():
    if current_user.role != "student":
        return jsonify({"ok": False, "message": "Only students can mark attendance."}), 403

    token = (request.json or {}).get("token", "").strip()
    lecture = Lecture.query.filter_by(current_token=token, is_active=True).first()

    if not lecture:
        return jsonify({"ok": False, "message": "Invalid or expired QR code. Ask faculty to show the current one."})

    if not lecture.token_is_valid(token):
        return jsonify({"ok": False, "message": "This QR code just expired — scan the new one on screen."})

    enrolled = Enrollment.query.filter_by(student_id=current_user.id, subject_id=lecture.subject_id).first()
    if not enrolled:
        return jsonify({"ok": False, "message": "You are not enrolled in this subject."})

    existing = AttendanceRecord.query.filter_by(lecture_id=lecture.id, student_id=current_user.id).first()
    if existing:
        return jsonify({"ok": False, "message": "Attendance already marked for this lecture."})

    record = AttendanceRecord(lecture_id=lecture.id, student_id=current_user.id)
    db.session.add(record)
    db.session.commit()
    return jsonify({"ok": True, "message": f"Attendance marked for {lecture.subject.name}!"})


@app.route("/student/history")
@login_required
def student_history():
    if current_user.role != "student":
        return redirect(url_for("faculty_dashboard"))
    records = (
        AttendanceRecord.query
        .filter_by(student_id=current_user.id)
        .join(Lecture)
        .order_by(Lecture.date.desc())
        .all()
    )
    return render_template("history.html", records=records)


@app.route("/student/monthly_report")
@login_required
def student_monthly_report():
    if current_user.role != "student":
        return redirect(url_for("faculty_dashboard"))
    stats = student_subject_stats(current_user)
    report = {}  # {month_label: {subject_name: {"attended": x, "total": y}}}
    for enr in current_user.enrollments:
        subject = enr.subject
        lectures = Lecture.query.filter_by(subject_id=subject.id).all()
        for lec in lectures:
            label = f"{month_name[lec.date.month]} {lec.date.year}"
            report.setdefault(label, {})
            report[label].setdefault(subject.name, {"attended": 0, "total": 0})
            report[label][subject.name]["total"] += 1
        attended_lectures = AttendanceRecord.query.filter_by(student_id=current_user.id).join(Lecture).filter(
            Lecture.subject_id == subject.id
        ).all()
        for rec in attended_lectures:
            label = f"{month_name[rec.lecture.date.month]} {rec.lecture.date.year}"
            report[label][subject.name]["attended"] += 1
    return render_template("monthly_report.html", report=report)


# ---------------------------------------------------------------------------
# SEED DATA (demo accounts, subjects, enrollments) — runs once on first launch
# ---------------------------------------------------------------------------

def seed_data():
    if User.query.first():
        return  # already seeded

    faculty1 = User(username="Bhagyashree", name="Asst. Prof. Bhagyashree Kulkarni", role="faculty")
    faculty1.set_password("pass123")
    db.session.add(faculty1)
    db.session.commit()

    subjects = [
        Subject(code="AIAD101", name="AI & AD", faculty_id=faculty1.id),
        Subject(code="DOTNET101", name=".Net Core", faculty_id=faculty1.id),
        Subject(code="FSP101", name="Full Stack Practical", faculty_id=faculty1.id),
    ]
    db.session.add_all(subjects)
    db.session.commit()

    students_data = [
        ("Yash", "Yash Swain", "TYIT01"),
        ("Rajdatt", "Rajdatt Sawant", "TYIT02"),
    ]
    students = []
    for uname, name, roll in students_data:
        s = User(username=uname, name=name, role="student", roll_no=roll)
        s.set_password("pass123")
        students.append(s)
    db.session.add_all(students)
    db.session.commit()

    for s in students:
        for subj in subjects:
            db.session.add(Enrollment(student_id=s.id, subject_id=subj.id))
    db.session.commit()


with app.app_context():
    db.create_all()
    seed_data()


if __name__ == "__main__":
    # For reaching the app from devices on a DIFFERENT network than your PC
    # (different Wi-Fi, mobile data, etc.), run this app normally (plain HTTP is
    # fine here) and separately start a tunnel, e.g.:
    #     ngrok http 5000
    # ngrok gives you a public https://xxxx.ngrok-free.app URL — share that same
    # URL with faculty and every student, on any network, and they can all use
    # it at the same time. ngrok provides its own trusted HTTPS certificate, so
    # phone cameras work with no security warnings at all. See README.md.
    #
    # If instead everyone is on the SAME Wi-Fi and you don't want to use a
    # tunnel, set USE_ADHOC_SSL=1 to serve self-signed HTTPS directly (needed
    # for phone camera access when connecting via your PC's LAN IP). This
    # requires the pyOpenSSL package.
    port = int(os.environ.get("PORT", 5000))
    use_adhoc_ssl = os.environ.get("USE_ADHOC_SSL") == "1"
    ssl_context = "adhoc" if use_adhoc_ssl else None
    app.run(debug=True, host="0.0.0.0", port=port, ssl_context=ssl_context)
