"""Module 2 - Companion Matching and Collaboration (STMS-8, 9, 10, 11).

Plugs into the Module 1 app as a Flask Blueprint. Integration point: every
profile is linked to a trip saved in Module 1 (the `preferences` table), and
the matching reads that trip's destination, dates and budget.
"""
import re
import sqlite3
import datetime
from flask import (Blueprint, render_template, request, redirect, url_for,
                   flash, g, session, abort, current_app)

m2 = Blueprint("m2", __name__, url_prefix="/companions")

INTERESTS = ["beach", "food", "adventure", "culture", "nature", "shopping"]
STYLES = ["Budget", "Comfort", "Luxury"]
EMAIL_RE = re.compile(r"^[^@\s]+@[^@\s]+\.[^@\s]+$")
MAX_MESSAGE = 500

SCHEMA = """
CREATE TABLE IF NOT EXISTS profiles(
  id INTEGER PRIMARY KEY AUTOINCREMENT, name TEXT NOT NULL,
  email TEXT NOT NULL UNIQUE, age INTEGER NOT NULL, travel_style TEXT NOT NULL,
  interests TEXT NOT NULL, preference_id INTEGER NOT NULL REFERENCES preferences(id),
  created_at TEXT DEFAULT CURRENT_TIMESTAMP);
CREATE TABLE IF NOT EXISTS connection_requests(
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  sender_id INTEGER NOT NULL REFERENCES profiles(id),
  receiver_id INTEGER NOT NULL REFERENCES profiles(id),
  status TEXT NOT NULL DEFAULT 'Pending',
  created_at TEXT DEFAULT CURRENT_TIMESTAMP,
  UNIQUE(sender_id, receiver_id));
CREATE TABLE IF NOT EXISTS messages(
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  request_id INTEGER NOT NULL REFERENCES connection_requests(id),
  sender_id INTEGER NOT NULL REFERENCES profiles(id),
  body TEXT NOT NULL, created_at TEXT DEFAULT CURRENT_TIMESTAMP);
"""


def init_m2_db(path):
    conn = sqlite3.connect(path)
    conn.executescript(SCHEMA)
    conn.commit()
    conn.close()


def get_db():
    if "db" not in g:
        g.db = sqlite3.connect(current_app.config["DB_PATH"])
        g.db.row_factory = sqlite3.Row
    return g.db


def current_profile():
    pid = session.get("profile_id")
    if not pid:
        return None
    return get_db().execute("SELECT * FROM profiles WHERE id=?", (pid,)).fetchone()


@m2.app_context_processor
def inject_profile():
    return {"current_profile": current_profile()}


# ---------------- STMS-8: profile validation ----------------
def validate_profile(form):
    """Return (errors, data). `form` is a Flask form or a plain dict."""
    errors = []
    name = (form.get("name") or "").strip()
    email = (form.get("email") or "").strip().lower()
    style = (form.get("travel_style") or "").strip()
    interests = form.getlist("interests") if hasattr(form, "getlist") else list(form.get("interests", []))
    interests = [i for i in interests if i in INTERESTS]

    if not 2 <= len(name) <= 50:
        errors.append("Name is required (2 to 50 characters).")
    if not EMAIL_RE.match(email):
        errors.append("Enter a valid email address.")
    age = None
    try:
        age = int(str(form.get("age", "")).strip())
        if not 18 <= age <= 70:
            errors.append("Age must be between 18 and 70.")
    except ValueError:
        errors.append("Age must be a whole number.")
    if style not in STYLES:
        errors.append("Choose a travel style.")
    if not interests:
        errors.append("Select at least one interest.")
    pref_id = None
    try:
        pref_id = int(str(form.get("preference_id", "")).strip())
    except ValueError:
        errors.append("Choose the trip you want companions for.")
    return errors, dict(name=name, email=email, age=age, travel_style=style,
                        interests=interests, preference_id=pref_id)


# ---------------- STMS-9: compatibility score ----------------
def _days(a, b):
    return (b - a).days + 1


def compatibility_score(a, b):
    """a, b: dicts with destination, start_date, end_date (ISO), budget, interests (list).
    Returns an int 0-100, or None when the trips do not overlap in dates.
    Weights: destination 40, date overlap 25, budget 15, shared interests 20."""
    s1, e1 = datetime.date.fromisoformat(a["start_date"]), datetime.date.fromisoformat(a["end_date"])
    s2, e2 = datetime.date.fromisoformat(b["start_date"]), datetime.date.fromisoformat(b["end_date"])
    start, end = max(s1, s2), min(e1, e2)
    if end < start:
        return None
    overlap = _days(start, end)
    shorter = min(_days(s1, e1), _days(s2, e2))
    score = 25 * overlap / shorter
    if a["destination"].strip().lower() == b["destination"].strip().lower():
        score += 40
    hi = max(a["budget"], b["budget"])
    if hi > 0:
        score += 15 * min(a["budget"], b["budget"]) / hi
    ia, ib = set(a["interests"]), set(b["interests"])
    if ia | ib:
        score += 20 * len(ia & ib) / len(ia | ib)
    return max(0, min(100, round(score)))


def _profile_dict(row):
    d = dict(row)
    d["interests"] = [i for i in row["interests"].split(",") if i]
    return d


def find_matches(db, me_id):
    """Matches for profile `me_id`, highest score first. Never includes self."""
    sql = ("SELECT p.*, t.destination, t.start_date, t.end_date, t.budget "
           "FROM profiles p JOIN preferences t ON t.id = p.preference_id")
    people = [_profile_dict(r) for r in db.execute(sql).fetchall()]
    me = next((p for p in people if p["id"] == me_id), None)
    if me is None:
        return None, []
    matches = []
    for other in people:
        if other["id"] == me_id:
            continue
        score = compatibility_score(me, other)
        if score is None:
            continue
        other["score"] = score
        other["shared"] = [i for i in me["interests"] if i in other["interests"]]
        matches.append(other)
    matches.sort(key=lambda m: (-m["score"], m["name"]))
    return me, matches


# ---------------- STMS-11: message validation ----------------
def validate_message(body):
    body = (body or "").strip()
    if not body:
        return "Message cannot be empty."
    if len(body) > MAX_MESSAGE:
        return f"Message cannot be longer than {MAX_MESSAGE} characters."
    return None


# ---------------- routes ----------------
def _need_profile():
    flash("Create or choose your traveler profile first.", "error")
    return redirect(url_for("m2.profile_form"))


def _existing_request(db, a, b):
    return db.execute("SELECT * FROM connection_requests WHERE (sender_id=? AND receiver_id=?) "
                      "OR (sender_id=? AND receiver_id=?)", (a, b, b, a)).fetchone()


@m2.route("/")
def home():
    return redirect(url_for("m2.matches" if current_profile() else "m2.profile_form"))


@m2.route("/profile/new", methods=["GET", "POST"])
@m2.route("/profile/<int:pid>/edit", methods=["GET", "POST"])
def profile_form(pid=None):
    db = get_db()
    profile = None
    if pid is not None:
        me = current_profile()
        if me is None or me["id"] != pid:
            abort(403)
        profile = me
    trips = db.execute("SELECT id,destination,start_date,end_date,budget FROM preferences "
                       "ORDER BY id DESC").fetchall()
    errors, form = [], {}
    if request.method == "POST":
        errors, data = validate_profile(request.form)
        form = request.form.to_dict()
        form["interests"] = request.form.getlist("interests")
        if not errors and not any(t["id"] == data["preference_id"] for t in trips):
            errors.append("Selected trip does not exist.")
        if not errors:
            dup = db.execute("SELECT id FROM profiles WHERE email=? AND id IS NOT ?",
                             (data["email"], pid)).fetchone()
            if dup:
                errors.append("A profile with this email already exists.")
        if not errors:
            values = (data["name"], data["email"], data["age"], data["travel_style"],
                      ",".join(data["interests"]), data["preference_id"])
            if pid is None:
                cur = db.execute("INSERT INTO profiles(name,email,age,travel_style,interests,preference_id)"
                                 " VALUES(?,?,?,?,?,?)", values)
                session["profile_id"] = cur.lastrowid
                flash("Profile created.")
            else:
                db.execute("UPDATE profiles SET name=?,email=?,age=?,travel_style=?,interests=?,"
                           "preference_id=? WHERE id=?", values + (pid,))
                flash("Profile updated.")
            db.commit()
            return redirect(url_for("m2.matches"))
    elif profile is not None:
        form = dict(profile)
        form["interests"] = profile["interests"].split(",")
    return render_template("companions/profile_form.html", form=form, errors=errors, trips=trips,
                           interests=INTERESTS, styles=STYLES, profile=profile), (400 if errors else 200)


@m2.route("/switch", methods=["GET", "POST"])
def switch():
    db = get_db()
    if request.method == "POST":
        row = db.execute("SELECT id FROM profiles WHERE id=?", (request.form.get("profile_id", type=int),)).fetchone()
        if row is None:
            flash("Choose a valid traveler.", "error")
        else:
            session["profile_id"] = row["id"]
            return redirect(url_for("m2.matches"))
    people = db.execute("SELECT p.id,p.name,p.email,t.destination FROM profiles p "
                        "JOIN preferences t ON t.id=p.preference_id ORDER BY p.name").fetchall()
    return render_template("companions/switch.html", people=people)


@m2.route("/matches")
def matches():
    me = current_profile()
    if me is None:
        return _need_profile()
    db = get_db()
    me_d, found = find_matches(db, me["id"])
    reqs = {}
    for r in db.execute("SELECT * FROM connection_requests WHERE sender_id=? OR receiver_id=?",
                        (me["id"], me["id"])).fetchall():
        reqs[r["receiver_id"] if r["sender_id"] == me["id"] else r["sender_id"]] = r
    return render_template("companions/matches.html", me=me_d, matches=found, reqs=reqs)


@m2.route("/request/<int:receiver_id>", methods=["POST"])
def send_request(receiver_id):
    me = current_profile()
    if me is None:
        return _need_profile()
    db = get_db()
    if receiver_id == me["id"]:
        flash("You cannot send a request to yourself.", "error")
    elif db.execute("SELECT id FROM profiles WHERE id=?", (receiver_id,)).fetchone() is None:
        abort(404)
    elif _existing_request(db, me["id"], receiver_id):
        flash("A request between you and this traveler already exists.", "error")
    else:
        db.execute("INSERT INTO connection_requests(sender_id,receiver_id,status) VALUES(?,?,'Pending')",
                   (me["id"], receiver_id))
        db.commit()
        flash("Request sent. Status: Pending.")
    return redirect(url_for("m2.matches"))


@m2.route("/requests")
def requests_page():
    me = current_profile()
    if me is None:
        return _need_profile()
    rows = get_db().execute(
        "SELECT r.*, s.name AS sender_name, c.name AS receiver_name FROM connection_requests r "
        "JOIN profiles s ON s.id=r.sender_id JOIN profiles c ON c.id=r.receiver_id "
        "WHERE r.sender_id=? OR r.receiver_id=? ORDER BY r.id DESC", (me["id"], me["id"])).fetchall()
    return render_template("companions/requests.html", me=me,
                           incoming=[r for r in rows if r["receiver_id"] == me["id"] and r["status"] == "Pending"],
                           sent=[r for r in rows if r["sender_id"] == me["id"]],
                           connections=[r for r in rows if r["status"] == "Accepted"])


@m2.route("/request/<int:req_id>/respond", methods=["POST"])
def respond(req_id):
    me = current_profile()
    if me is None:
        return _need_profile()
    db = get_db()
    req = db.execute("SELECT * FROM connection_requests WHERE id=?", (req_id,)).fetchone()
    action = request.form.get("action")
    if req is None:
        abort(404)
    if req["receiver_id"] != me["id"]:
        flash("Only the receiver can respond to a request.", "error")
    elif req["status"] != "Pending":
        flash("This request has already been answered.", "error")
    elif action not in ("accept", "reject"):
        flash("Invalid action.", "error")
    else:
        status = "Accepted" if action == "accept" else "Rejected"
        db.execute("UPDATE connection_requests SET status=? WHERE id=?", (status, req_id))
        db.commit()
        flash(f"Request {status.lower()}.")
    return redirect(url_for("m2.requests_page"))


@m2.route("/chat/<int:req_id>", methods=["GET", "POST"])
def chat(req_id):
    me = current_profile()
    if me is None:
        return _need_profile()
    db = get_db()
    req = db.execute("SELECT * FROM connection_requests WHERE id=?", (req_id,)).fetchone()
    if req is None:
        abort(404)
    if me["id"] not in (req["sender_id"], req["receiver_id"]) or req["status"] != "Accepted":
        abort(403)                       # chat only for accepted connections (integration with STMS-10)
    if request.method == "POST":
        error = validate_message(request.form.get("body"))
        if error:
            flash(error, "error")
        else:
            db.execute("INSERT INTO messages(request_id,sender_id,body) VALUES(?,?,?)",
                       (req_id, me["id"], request.form["body"].strip()))
            db.commit()
        return redirect(url_for("m2.chat", req_id=req_id))
    other_id = req["receiver_id"] if req["sender_id"] == me["id"] else req["sender_id"]
    other = db.execute("SELECT * FROM profiles WHERE id=?", (other_id,)).fetchone()
    msgs = db.execute("SELECT m.*, p.name FROM messages m JOIN profiles p ON p.id=m.sender_id "
                      "WHERE m.request_id=? ORDER BY m.id", (req_id,)).fetchall()
    return render_template("companions/chat.html", me=me, other=other, msgs=msgs, req=req,
                           max_len=MAX_MESSAGE)
