from flask import Flask, render_template, request, redirect, url_for, session, flash
import mysql.connector
from mysql.connector import Error
from werkzeug.security import generate_password_hash, check_password_hash
from functools import wraps
from datetime import datetime, timedelta
import re
import secrets
import os

app = Flask(__name__)
app.secret_key = os.getenv("FLASK_SECRET_KEY", "online-voting-secret-key-change-this")

DB_CONFIG = {
    "host": "localhost",
    "user": "root",
    "password": "Root@1234",
    "database": "online_voting"
}

# Optional SMS settings. Fill these in (or set environment variables) to send real SMS.
TWILIO_ACCOUNT_SID = os.getenv("TWILIO_ACCOUNT_SID", "")
TWILIO_AUTH_TOKEN = os.getenv("TWILIO_AUTH_TOKEN", "")
TWILIO_PHONE_NUMBER = os.getenv("TWILIO_PHONE_NUMBER", "")

DEFAULT_ADMIN_ID = "ADMIN001"
DEFAULT_ADMIN_PASSWORD = "admin123"
OTP_MINUTES = 10


def get_db():
    return mysql.connector.connect(**DB_CONFIG)


def normalize_phone(phone):
    digits = re.sub(r"\D", "", phone or "")
    if digits.startswith("91") and len(digits) == 12:
        digits = digits[2:]
    if len(digits) != 10 or digits[0] not in "6789":
        return None
    return digits


def send_sms(phone, message):
    """Send SMS through Twilio when credentials are configured.
    Returns (True, '') on success. Returns (False, reason) when unavailable/failed.
    """
    if not (TWILIO_ACCOUNT_SID and TWILIO_AUTH_TOKEN and TWILIO_PHONE_NUMBER):
        return False, "SMS service is not configured."

    try:
        from twilio.rest import Client
        client = Client(TWILIO_ACCOUNT_SID, TWILIO_AUTH_TOKEN)
        client.messages.create(
            body=message,
            from_=TWILIO_PHONE_NUMBER,
            to=f"+91{phone}",
        )
        return True, ""
    except Exception as exc:
        return False, str(exc)


def ensure_phone_column():
    """Add the phone column to an existing project database if needed."""
    db = get_db()
    cursor = db.cursor()
    try:
        cursor.execute(
            """SELECT COUNT(*) FROM INFORMATION_SCHEMA.COLUMNS
               WHERE TABLE_SCHEMA = %s AND TABLE_NAME = 'users' AND COLUMN_NAME = 'phone'""",
            (DB_CONFIG["database"],),
        )
        exists = cursor.fetchone()[0]
        if not exists:
            cursor.execute("ALTER TABLE users ADD COLUMN phone VARCHAR(15) NULL")
            cursor.execute("ALTER TABLE users ADD UNIQUE KEY uq_users_phone (phone)")
            db.commit()
    finally:
        cursor.close()
        db.close()


def ensure_election_system():
    """Create election rounds and migrate the old one-vote-per-voter schema.

    The old project stored a single users.has_voted flag and made votes.voter_id
    globally unique. That prevents a voter from participating in a later election.
    Votes are now unique per (voter, election), so the same registered voter can
    vote once in every new election round.
    """
    db = get_db()
    cursor = db.cursor(dictionary=True)
    try:
        cursor.execute(
            """CREATE TABLE IF NOT EXISTS elections (
                id INT AUTO_INCREMENT PRIMARY KEY,
                name VARCHAR(100) NOT NULL,
                start_at DATETIME NOT NULL,
                end_at DATETIME NOT NULL,
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            )"""
        )

        cursor.execute("SELECT id FROM elections ORDER BY id ASC LIMIT 1")
        first = cursor.fetchone()
        if not first:
            cursor.execute("SELECT start_at, end_at FROM election_settings WHERE id = 1")
            setting = cursor.fetchone()
            if setting:
                cursor.execute(
                    "INSERT INTO elections (id, name, start_at, end_at) VALUES (1, %s, %s, %s)",
                    ("Election 1", setting["start_at"], setting["end_at"]),
                )
            else:
                now = datetime.now()
                cursor.execute(
                    "INSERT INTO elections (id, name, start_at, end_at) VALUES (1, %s, %s, %s)",
                    ("Election 1", now, now),
                )

        # Add election_id to existing votes if the project is being upgraded.
        cursor.execute(
            """SELECT COUNT(*) AS total FROM INFORMATION_SCHEMA.COLUMNS
               WHERE TABLE_SCHEMA = %s AND TABLE_NAME = 'votes' AND COLUMN_NAME = 'election_id'""",
            (DB_CONFIG["database"],),
        )
        if cursor.fetchone()["total"] == 0:
            cursor.execute("ALTER TABLE votes ADD COLUMN election_id INT NULL AFTER voter_id")
            cursor.execute("UPDATE votes SET election_id = 1 WHERE election_id IS NULL")
            cursor.execute("ALTER TABLE votes MODIFY election_id INT NOT NULL")

        # Remove the old global UNIQUE(voter_id) constraint if it exists.
        # IMPORTANT: MySQL may be using that unique index to support the
        # votes.voter_id -> users.id foreign key. A foreign-key-supporting index
        # cannot be dropped directly (MySQL error 1553), so temporarily remove
        # the dependent FK(s), replace the UNIQUE index with a normal index, and
        # then restore the FK(s).
        cursor.execute(
            """SELECT DISTINCT INDEX_NAME FROM INFORMATION_SCHEMA.STATISTICS
               WHERE TABLE_SCHEMA = %s AND TABLE_NAME = 'votes'
                 AND NON_UNIQUE = 0 AND COLUMN_NAME = 'voter_id'
                 AND INDEX_NAME <> 'PRIMARY'""",
            (DB_CONFIG["database"],),
        )
        unique_indexes = [row["INDEX_NAME"] for row in cursor.fetchall()]

        if unique_indexes:
            cursor.execute(
                """SELECT DISTINCT kcu.CONSTRAINT_NAME,
                                  rc.DELETE_RULE, rc.UPDATE_RULE
                   FROM INFORMATION_SCHEMA.KEY_COLUMN_USAGE kcu
                   JOIN INFORMATION_SCHEMA.REFERENTIAL_CONSTRAINTS rc
                     ON rc.CONSTRAINT_SCHEMA = kcu.CONSTRAINT_SCHEMA
                    AND rc.TABLE_NAME = kcu.TABLE_NAME
                    AND rc.CONSTRAINT_NAME = kcu.CONSTRAINT_NAME
                   WHERE kcu.CONSTRAINT_SCHEMA = %s
                     AND kcu.TABLE_NAME = 'votes'
                     AND kcu.COLUMN_NAME = 'voter_id'
                     AND kcu.REFERENCED_TABLE_NAME = 'users'
                     AND kcu.REFERENCED_COLUMN_NAME = 'id'""",
                (DB_CONFIG["database"],),
            )
            voter_fks = cursor.fetchall()

            for fk in voter_fks:
                cursor.execute(
                    f"ALTER TABLE votes DROP FOREIGN KEY `{fk['CONSTRAINT_NAME']}`"
                )

            for index_name in unique_indexes:
                cursor.execute(f"ALTER TABLE votes DROP INDEX `{index_name}`")

            # Recreate a normal (non-unique) voter index. This allows the same
            # voter to appear in votes for multiple election rounds.
            cursor.execute(
                """SELECT COUNT(*) AS total
                   FROM INFORMATION_SCHEMA.STATISTICS
                   WHERE TABLE_SCHEMA = %s AND TABLE_NAME = 'votes'
                     AND INDEX_NAME = 'idx_votes_voter_id'""",
                (DB_CONFIG["database"],),
            )
            if cursor.fetchone()["total"] == 0:
                cursor.execute("ALTER TABLE votes ADD INDEX idx_votes_voter_id (voter_id)")

            for fk in voter_fks:
                cursor.execute(
                    f"""ALTER TABLE votes
                        ADD CONSTRAINT `{fk['CONSTRAINT_NAME']}`
                        FOREIGN KEY (voter_id) REFERENCES users(id)
                        ON DELETE {fk['DELETE_RULE']}
                        ON UPDATE {fk['UPDATE_RULE']}"""
                )

        cursor.execute(
            """SELECT COUNT(*) AS total FROM INFORMATION_SCHEMA.STATISTICS
               WHERE TABLE_SCHEMA = %s AND TABLE_NAME = 'votes'
                 AND INDEX_NAME = 'uq_votes_voter_election'""",
            (DB_CONFIG["database"],),
        )
        if cursor.fetchone()["total"] == 0:
            cursor.execute(
                "ALTER TABLE votes ADD CONSTRAINT uq_votes_voter_election UNIQUE (voter_id, election_id)"
            )

        cursor.execute(
            """SELECT COUNT(*) AS total FROM INFORMATION_SCHEMA.KEY_COLUMN_USAGE
               WHERE TABLE_SCHEMA = %s AND TABLE_NAME = 'votes'
                 AND CONSTRAINT_NAME = 'fk_votes_election'""",
            (DB_CONFIG["database"],),
        )
        if cursor.fetchone()["total"] == 0:
            cursor.execute(
                """ALTER TABLE votes
                   ADD CONSTRAINT fk_votes_election
                   FOREIGN KEY (election_id) REFERENCES elections(id) ON DELETE RESTRICT"""
            )

        # Keep the legacy column for compatibility with older databases, but do not
        # use it as the voting rule anymore. Voting eligibility is election-specific.
        db.commit()
    finally:
        cursor.close()
        db.close()


def login_required(f):
    @wraps(f)
    def decorated(*args, **kwargs):
        if "user_id" not in session:
            flash("Please login first.", "error")
            return redirect(url_for("index"))
        return f(*args, **kwargs)
    return decorated


def admin_required(f):
    @wraps(f)
    def decorated(*args, **kwargs):
        if session.get("role") != "admin":
            flash("Admin login is required for this page.", "error")
            return redirect(url_for("index"))
        return f(*args, **kwargs)
    return decorated


def ensure_default_admin():
    db = get_db()
    cursor = db.cursor(dictionary=True)
    try:
        cursor.execute("SELECT id FROM users WHERE voter_id = %s", (DEFAULT_ADMIN_ID,))
        if cursor.fetchone() is None:
            cursor.execute(
                """INSERT INTO users (name, voter_id, email, phone, password, role)
                   VALUES (%s, %s, %s, %s, %s, 'admin')""",
                (
                    "System Administrator",
                    DEFAULT_ADMIN_ID,
                    "admin@onlinevoting.local",
                    None,
                    generate_password_hash(DEFAULT_ADMIN_PASSWORD),
                ),
            )
            db.commit()
    finally:
        cursor.close()
        db.close()


def get_current_election():
    db = get_db()
    cursor = db.cursor(dictionary=True)
    try:
        cursor.execute("SELECT * FROM elections ORDER BY id DESC LIMIT 1")
        return cursor.fetchone()
    finally:
        cursor.close()
        db.close()


def get_voting_status():
    setting = get_current_election()
    if not setting:
        return "not_set", None

    now = datetime.now()
    if now < setting["start_at"]:
        return "upcoming", setting
    if now > setting["end_at"]:
        return "closed", setting
    return "open", setting


def generate_unique_voter_id(cursor):
    while True:
        voter_id = f"VT{secrets.randbelow(1_000_000):06d}"
        cursor.execute("SELECT id FROM users WHERE voter_id = %s", (voter_id,))
        if cursor.fetchone() is None:
            return voter_id


@app.route("/")
def index():
    if session.get("role") == "admin":
        return redirect(url_for("admin_dashboard"))

    status, setting = get_voting_status()
    db = get_db()
    cursor = db.cursor(dictionary=True)
    try:
        cursor.execute("SELECT id, name, party FROM candidates ORDER BY id ASC")
        candidates = cursor.fetchall()

        already_voted = False
        if session.get("user_id") and session.get("role") == "voter":
            current_election = get_current_election()
            if current_election:
                cursor.execute(
                    "SELECT id FROM votes WHERE voter_id = %s AND election_id = %s",
                    (session["user_id"], current_election["id"]),
                )
                already_voted = cursor.fetchone() is not None
    finally:
        cursor.close()
        db.close()

    return render_template(
        "index.html",
        status=status,
        setting=setting,
        candidates=candidates,
        already_voted=already_voted,
    )


@app.route("/signup", methods=["POST"])
def signup():
    name = request.form.get("name", "").strip()
    email = request.form.get("email", "").strip().lower()
    phone = normalize_phone(request.form.get("phone", ""))
    password = request.form.get("password", "")
    confirm_password = request.form.get("confirm_password", "")

    if not name or not email or not phone or not password or not confirm_password:
        flash("Please fill all signup fields.", "error")
        return redirect(url_for("index", _anchor="auth"))

    if password != confirm_password:
        flash("Passwords do not match.", "error")
        return redirect(url_for("index", _anchor="auth"))

    if len(password) < 6:
        flash("Passwords must contain at least 6 characters.", "error")
        return redirect(url_for("index", _anchor="auth"))

    db = get_db()
    cursor = db.cursor()
    try:
        cursor.execute("SELECT id FROM users WHERE email = %s", (email,))
        if cursor.fetchone():
            flash("Email is already registered.", "error")
            return redirect(url_for("index", _anchor="auth"))

        cursor.execute("SELECT id FROM users WHERE phone = %s", (phone,))
        if cursor.fetchone():
            flash("Mobile number is already registered.", "error")
            return redirect(url_for("index", _anchor="auth"))

        voter_id = generate_unique_voter_id(cursor)
        password_hash = generate_password_hash(password)
        cursor.execute(
            """INSERT INTO users (name, voter_id, email, phone, password, role)
               VALUES (%s, %s, %s, %s, %s, 'voter')""",
            (name, voter_id, email, phone, password_hash),
        )
        db.commit()

        sms_ok, sms_error = send_sms(
            phone,
            f"Online Voting System: Your Voter ID is {voter_id}. Keep it safe for login.",
        )

        # Show the ID in the web notification too, even when SMS is unavailable.
        flash(voter_id, "voter_id")
        if sms_ok:
            flash("Signup successful! Your Voter ID was sent to your mobile number by SMS.", "success")
        else:
            flash("Signup successful! Your Voter ID is shown above. Configure Twilio to send SMS to the mobile number.", "success")
            if sms_error:
                print("SMS notice:", sms_error)
    except Error as exc:
        db.rollback()
        flash(f"Signup failed: {exc}", "error")
    finally:
        cursor.close()
        db.close()

    return redirect(url_for("index", _anchor="auth"))


@app.route("/login", methods=["POST"])
def login():
    voter_id = request.form.get("voter_id", "").strip().upper()
    password = request.form.get("password", "")

    if not re.fullmatch(r"[A-Z0-9]+", voter_id):
        flash("Voter ID can contain only letters and numbers.", "error")
        return redirect(url_for("index", _anchor="auth"))

    db = get_db()
    cursor = db.cursor(dictionary=True)
    try:
        cursor.execute("SELECT * FROM users WHERE voter_id = %s", (voter_id,))
        user = cursor.fetchone()
    finally:
        cursor.close()
        db.close()

    if user and user["role"] == "voter" and check_password_hash(user["password"], password):
        session["user_id"] = user["id"]
        session["name"] = user["name"]
        session["voter_id"] = user["voter_id"]
        session["role"] = "voter"
        flash("Voter login successful.", "success")
    elif user and user["role"] == "admin":
        flash("Use the Admin Login tab to sign in as administrator.", "error")
    else:
        flash("Invalid Voter ID or password.", "error")

    return redirect(url_for("index"))


@app.route("/forgot-password", methods=["GET", "POST"])
def forgot_password():
    if request.method == "GET":
        return render_template("forgot_password.html", otp_step=False, demo_otp=None)

    action = request.form.get("action", "send_otp")

    if action == "send_otp":
        phone = normalize_phone(request.form.get("phone", ""))
        if not phone:
            flash("Enter a valid 10-digit Indian mobile number.", "error")
            return render_template("forgot_password.html", otp_step=False, demo_otp=None)

        db = get_db()
        cursor = db.cursor(dictionary=True)
        try:
            cursor.execute(
                "SELECT id, name, voter_id FROM users WHERE phone = %s AND role = 'voter'",
                (phone,),
            )
            user = cursor.fetchone()
        finally:
            cursor.close()
            db.close()

        if not user:
            flash("No voter account is registered with this mobile number.", "error")
            return render_template("forgot_password.html", otp_step=False, demo_otp=None)

        otp = f"{secrets.randbelow(1_000_000):06d}"
        session["reset_user_id"] = user["id"]
        session["reset_phone"] = phone
        session["reset_otp_hash"] = generate_password_hash(otp)
        session["reset_otp_expires"] = (datetime.now() + timedelta(minutes=OTP_MINUTES)).isoformat()

        sms_ok, sms_error = send_sms(
            phone,
            f"Online Voting System: Your password reset OTP is {otp}. It expires in {OTP_MINUTES} minutes.",
        )

        if sms_ok:
            flash("OTP sent to your registered mobile number.", "success")
            demo_otp = None
        else:
            # Local academic demo fallback so the feature can be tested without an SMS provider.
            demo_otp = otp
            flash("SMS service is not configured. Demo OTP is shown on this page for local testing.", "error")
            if sms_error:
                print("SMS notice:", sms_error)

        return render_template(
            "forgot_password.html",
            otp_step=True,
            phone=phone,
            demo_otp=demo_otp,
        )

    if action == "reset":
        phone = normalize_phone(request.form.get("phone", ""))
        otp = request.form.get("otp", "").strip()
        new_password = request.form.get("new_password", "")
        confirm_password = request.form.get("confirm_password", "")

        expires = session.get("reset_otp_expires")
        otp_hash = session.get("reset_otp_hash")
        reset_phone = session.get("reset_phone")
        reset_user_id = session.get("reset_user_id")

        if not phone or phone != reset_phone or not otp_hash or not expires or not reset_user_id:
            flash("Please request a new OTP.", "error")
            return redirect(url_for("forgot_password"))

        try:
            expires_at = datetime.fromisoformat(expires)
        except ValueError:
            expires_at = datetime.min

        if datetime.now() > expires_at:
            session.pop("reset_otp_hash", None)
            session.pop("reset_otp_expires", None)
            flash("OTP has expired. Please request a new OTP.", "error")
            return redirect(url_for("forgot_password"))

        if not check_password_hash(otp_hash, otp):
            flash("Invalid OTP.", "error")
            return render_template("forgot_password.html", otp_step=True, phone=phone, demo_otp=None)

        if len(new_password) < 6:
            flash("New password must contain at least 6 characters.", "error")
            return render_template("forgot_password.html", otp_step=True, phone=phone, demo_otp=None)

        if new_password != confirm_password:
            flash("Passwords do not match.", "error")
            return render_template("forgot_password.html", otp_step=True, phone=phone, demo_otp=None)

        db = get_db()
        cursor = db.cursor()
        try:
            cursor.execute(
                "UPDATE users SET password = %s WHERE id = %s AND role = 'voter' AND phone = %s",
                (generate_password_hash(new_password), reset_user_id, phone),
            )
            db.commit()
            flash("Password reset successful. You can now login with your new password.", "success")
        except Error as exc:
            db.rollback()
            flash(f"Could not reset password: {exc}", "error")
            return render_template("forgot_password.html", otp_step=True, phone=phone, demo_otp=None)
        finally:
            cursor.close()
            db.close()

        for key in ("reset_user_id", "reset_phone", "reset_otp_hash", "reset_otp_expires"):
            session.pop(key, None)
        return redirect(url_for("index", _anchor="auth"))

    flash("Invalid password reset request.", "error")
    return redirect(url_for("forgot_password"))


@app.route("/admin/login", methods=["POST"])
def admin_login():
    admin_id = request.form.get("admin_id", "").strip().upper()
    password = request.form.get("password", "")

    if not re.fullmatch(r"[A-Z0-9]+", admin_id):
        flash("Admin ID can contain only letters and numbers.", "error")
        return redirect(url_for("index", _anchor="auth"))

    db = get_db()
    cursor = db.cursor(dictionary=True)
    try:
        cursor.execute(
            "SELECT * FROM users WHERE voter_id = %s AND role = 'admin'",
            (admin_id,),
        )
        admin = cursor.fetchone()
    finally:
        cursor.close()
        db.close()

    if admin and check_password_hash(admin["password"], password):
        session.clear()
        session["user_id"] = admin["id"]
        session["name"] = admin["name"]
        session["voter_id"] = admin["voter_id"]
        session["role"] = "admin"
        return redirect(url_for("admin_dashboard"))

    flash("Invalid Admin ID or password.", "error")
    return redirect(url_for("index", _anchor="auth"))


@app.route("/logout")
def logout():
    session.clear()
    flash("Logged out successfully.", "success")
    return redirect(url_for("index"))


@app.route("/vote/<int:candidate_id>", methods=["POST"])
@login_required
def vote(candidate_id):
    if session.get("role") != "voter":
        flash("Only voters can cast a vote.", "error")
        return redirect(url_for("admin_dashboard"))

    status, _ = get_voting_status()
    if status != "open":
        flash("Voting is not open now.", "error")
        return redirect(url_for("index", _anchor="vote"))

    db = get_db()
    cursor = db.cursor(dictionary=True)
    try:
        current_election = get_current_election()
        if not current_election:
            flash("No election is configured.", "error")
            return redirect(url_for("index", _anchor="vote"))

        cursor.execute("SELECT id FROM users WHERE id = %s AND role = 'voter'", (session["user_id"],))
        voter_row = cursor.fetchone()
        if not voter_row:
            flash("Voter account not found.", "error")
            return redirect(url_for("index", _anchor="vote"))

        cursor.execute(
            "SELECT id FROM votes WHERE voter_id = %s AND election_id = %s",
            (session["user_id"], current_election["id"]),
        )
        if cursor.fetchone():
            flash("You have already voted in this election.", "error")
            return redirect(url_for("index", _anchor="vote"))

        cursor.execute("SELECT id, name FROM candidates WHERE id = %s", (candidate_id,))
        candidate = cursor.fetchone()
        if not candidate:
            flash("Candidate not found.", "error")
            return redirect(url_for("index", _anchor="vote"))

        cursor.execute(
            "INSERT INTO votes (voter_id, election_id, candidate_id) VALUES (%s, %s, %s)",
            (session["user_id"], current_election["id"], candidate_id),
        )
        db.commit()
        flash("Your vote has been recorded successfully.", "success")
    except Error as exc:
        db.rollback()
        flash(f"Unable to record your vote: {exc}", "error")
    finally:
        cursor.close()
        db.close()

    return redirect(url_for("index", _anchor="vote"))


@app.route("/admin/dashboard")
@admin_required
def admin_dashboard():
    status, setting = get_voting_status()
    db = get_db()
    cursor = db.cursor(dictionary=True)
    try:
        cursor.execute("SELECT id, name, party FROM candidates ORDER BY id ASC")
        candidates = cursor.fetchall()

        current_election = get_current_election()
        election_id = current_election["id"] if current_election else None

        cursor.execute(
            """SELECT c.name, c.party, COUNT(v.id) AS vote_count
               FROM candidates c
               LEFT JOIN votes v ON c.id = v.candidate_id AND v.election_id = %s
               GROUP BY c.id, c.name, c.party
               ORDER BY vote_count DESC, c.name ASC""",
            (election_id,),
        )
        results = cursor.fetchall()

        cursor.execute("SELECT COUNT(*) AS total FROM users WHERE role = 'voter'")
        voter_count = cursor.fetchone()["total"]

        cursor.execute(
            """SELECT u.id, u.name, u.voter_id, u.email, u.phone,
                      CASE WHEN v.id IS NULL THEN 0 ELSE 1 END AS has_voted
               FROM users u
               LEFT JOIN votes v ON v.voter_id = u.id AND v.election_id = %s
               WHERE u.role = 'voter'
               ORDER BY u.id DESC""",
            (election_id,),
        )
        voters = cursor.fetchall()

        cursor.execute("SELECT COUNT(*) AS total FROM votes WHERE election_id = %s", (election_id,))
        vote_count = cursor.fetchone()["total"]
    finally:
        cursor.close()
        db.close()

    return render_template(
        "admin_dashboard.html",
        status=status,
        setting=setting,
        candidates=candidates,
        results=results,
        voter_count=voter_count,
        vote_count=vote_count,
        voters=voters,
    )


@app.route("/admin/voter/delete/<int:voter_id>", methods=["POST"])
@admin_required
def delete_voter(voter_id):
    db = get_db()
    cursor = db.cursor(dictionary=True)
    try:
        cursor.execute(
            "SELECT id, name FROM users WHERE id = %s AND role = 'voter'",
            (voter_id,),
        )
        voter = cursor.fetchone()
        if not voter:
            flash("Voter not found.", "error")
            return redirect(url_for("admin_dashboard"))

        # Do not remove voters who already cast a vote, because deleting the
        # user would also break the audit/result relationship in this project.
        cursor.execute("SELECT id FROM votes WHERE voter_id = %s", (voter_id,))
        if cursor.fetchone():
            flash(
                "This voter has already voted and cannot be removed. This keeps the recorded election result unchanged.",
                "error",
            )
            return redirect(url_for("admin_dashboard"))

        cursor.execute(
            "DELETE FROM users WHERE id = %s AND role = 'voter'",
            (voter_id,),
        )
        db.commit()
        if cursor.rowcount:
            flash(f"Voter {voter['name']} was removed successfully.", "success")
        else:
            flash("Voter could not be removed.", "error")
    except Error as exc:
        db.rollback()
        flash(f"Could not remove voter: {exc}", "error")
    finally:
        cursor.close()
        db.close()

    return redirect(url_for("admin_dashboard"))


@app.route("/admin/settings", methods=["POST"])
@admin_required
def admin_settings():
    start_at = request.form.get("start_at", "")
    end_at = request.form.get("end_at", "")

    try:
        start = datetime.fromisoformat(start_at)
        end = datetime.fromisoformat(end_at)
    except ValueError:
        flash("Invalid date/time format.", "error")
        return redirect(url_for("admin_dashboard"))

    if end <= start:
        flash("End time must be after start time.", "error")
        return redirect(url_for("admin_dashboard"))

    db = get_db()
    cursor = db.cursor(dictionary=True)
    try:
        cursor.execute("SELECT * FROM elections ORDER BY id DESC LIMIT 1")
        current = cursor.fetchone()
        now = datetime.now()

        if current and current["end_at"] < now:
            # A closed election stays in history. Saving a new voting window starts
            # a fresh election round, allowing all registered voters to vote once again.
            cursor.execute("SELECT COALESCE(MAX(id), 0) + 1 AS next_id FROM elections")
            next_id = cursor.fetchone()["next_id"]
            cursor.execute(
                "INSERT INTO elections (id, name, start_at, end_at) VALUES (%s, %s, %s, %s)",
                (next_id, f"Election {next_id}", start, end),
            )
            flash(f"New election round Election {next_id} created. All registered voters can vote once in this round.", "success")
        elif current:
            cursor.execute(
                "UPDATE elections SET start_at = %s, end_at = %s WHERE id = %s",
                (start, end, current["id"]),
            )
            flash("Voting time updated successfully.", "success")
        else:
            cursor.execute(
                "INSERT INTO elections (name, start_at, end_at) VALUES (%s, %s, %s)",
                ("Election 1", start, end),
            )
            flash("Election created successfully.", "success")
        db.commit()
    except Error as exc:
        db.rollback()
        flash(f"Could not update voting time: {exc}", "error")
    finally:
        cursor.close()
        db.close()

    return redirect(url_for("admin_dashboard"))


@app.route("/admin/candidate/add", methods=["POST"])
@admin_required
def add_candidate():
    name = request.form.get("name", "").strip()
    party = request.form.get("party", "").strip()

    if not name or not party:
        flash("Candidate name and party are required.", "error")
        return redirect(url_for("admin_dashboard"))

    db = get_db()
    cursor = db.cursor()
    try:
        cursor.execute(
            "INSERT INTO candidates (name, party) VALUES (%s, %s)",
            (name, party),
        )
        db.commit()
        flash("Candidate added successfully.", "success")
    except Error as exc:
        db.rollback()
        flash(f"Could not add candidate: {exc}", "error")
    finally:
        cursor.close()
        db.close()

    return redirect(url_for("admin_dashboard"))


@app.route("/admin/candidate/delete/<int:candidate_id>", methods=["POST"])
@admin_required
def delete_candidate(candidate_id):
    db = get_db()
    cursor = db.cursor(dictionary=True)
    try:
        cursor.execute("SELECT id, name FROM candidates WHERE id = %s", (candidate_id,))
        candidate = cursor.fetchone()
        if not candidate:
            flash("Candidate not found.", "error")
            return redirect(url_for("admin_dashboard"))

        # Admin-only hard delete: works whether or not the candidate has votes.
        # Current implementation removes linked vote records; voter eligibility is
        # controlled per election, so those voters can vote in a future election.
        cursor.execute("DELETE FROM votes WHERE candidate_id = %s", (candidate_id,))
        deleted_votes = cursor.rowcount

        cursor.execute("DELETE FROM candidates WHERE id = %s", (candidate_id,))
        db.commit()

        if deleted_votes:
            flash(
                f"Candidate {candidate['name']} deleted. {deleted_votes} linked vote(s) were also removed from the results.",
                "success",
            )
        else:
            flash(f"Candidate {candidate['name']} deleted successfully.", "success")
    except Error as exc:
        db.rollback()
        flash(f"Could not delete candidate: {exc}", "error")
    finally:
        cursor.close()
        db.close()

    return redirect(url_for("admin_dashboard"))


if __name__ == "__main__":
    try:
        ensure_phone_column()
        ensure_election_system()
        ensure_default_admin()
    except Error as exc:
        print("Database setup error:", exc)
        print("Check the MySQL password in DB_CONFIG and make sure database 'online_voting' exists.")
        raise

    app.run(debug=True)
