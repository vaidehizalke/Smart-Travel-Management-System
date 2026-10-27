"""Smart Travel Management System - Module 1 (STMS-3, STMS-4, STMS-5)."""
import os
import sqlite3
import datetime
from flask import Flask, render_template, request, redirect, url_for, flash, g

app = Flask(__name__)
app.secret_key = "stms-dev-key"
DB = os.environ.get("STMS_DB", os.path.join(os.path.dirname(__file__), "database.db"))

INTERESTS = ["beach", "food", "adventure", "culture", "nature", "shopping"]
MAX_DAYS = 30

# Activities per destination and interest: (name, cost per person in Rs.)
ACTIVITIES = {
    "goa": {
        "beach": [("Calangute beach day", 500), ("Water sports at Baga", 1500)],
        "food": [("Seafood trail in Panjim", 900), ("Cafe hopping in Assagao", 700)],
        "culture": [("Old Goa churches tour", 400), ("Fontainhas heritage walk", 300)],
        "adventure": [("Dudhsagar jeep safari", 1800), ("Scuba diving at Grande Island", 2500)],
    },
    "jaipur": {
        "culture": [("Amber Fort and Jaigarh", 800), ("City Palace and Hawa Mahal", 700)],
        "food": [("Rajasthani thali at Chokhi Dhani", 1000), ("Street food at Johari Bazaar", 400)],
        "shopping": [("Bapu Bazaar textiles", 300), ("Gem and jewellery market", 500)],
        "adventure": [("Hot air balloon ride", 3500), ("Elephant village visit", 1200)],
    },
    "manali": {
        "adventure": [("Paragliding at Solang Valley", 2500), ("River rafting in Kullu", 1500)],
        "nature": [("Rohtang Pass visit", 1800), ("Hadimba forest walk", 200)],
        "culture": [("Hadimba Temple and Old Manali", 300), ("Naggar Castle visit", 500)],
        "food": [("Cafe trail in Old Manali", 800), ("Trout fish lunch", 900)],
    },
    "delhi": {
        "culture": [("Red Fort and Jama Masjid", 600), ("Humayun's Tomb and Qutub Minar", 700)],
        "food": [("Chandni Chowk food walk", 700), ("Dinner at Connaught Place", 1500)],
        "shopping": [("Sarojini Nagar market", 500), ("Dilli Haat crafts", 600)],
        "nature": [("Lodhi Garden morning walk", 100), ("Sunder Nursery visit", 300)],
    },
}
DESTINATIONS = ["Goa", "Jaipur", "Manali", "Delhi"]

SCHEMA = """
CREATE TABLE IF NOT EXISTS preferences(
  id INTEGER PRIMARY KEY AUTOINCREMENT, destination TEXT NOT NULL,
  start_date TEXT NOT NULL, end_date TEXT NOT NULL, budget REAL NOT NULL,
  travelers INTEGER NOT NULL, interests TEXT NOT NULL,
  created_at TEXT DEFAULT CURRENT_TIMESTAMP);
CREATE TABLE IF NOT EXISTS itineraries(
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  preference_id INTEGER NOT NULL REFERENCES preferences(id),
  total_days INTEGER NOT NULL, estimated_cost REAL NOT NULL);
CREATE TABLE IF NOT EXISTS itinerary_days(
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  itinerary_id INTEGER NOT NULL REFERENCES itineraries(id),
  day_number INTEGER NOT NULL, activity TEXT NOT NULL, cost REAL NOT NULL);
CREATE TABLE IF NOT EXISTS flights(
  id INTEGER PRIMARY KEY AUTOINCREMENT, origin TEXT, destination TEXT NOT NULL,
  date TEXT NOT NULL, airline TEXT, departure TEXT, price REAL NOT NULL);
CREATE TABLE IF NOT EXISTS hotels(
  id INTEGER PRIMARY KEY AUTOINCREMENT, name TEXT NOT NULL,
  destination TEXT NOT NULL, price_per_night REAL NOT NULL, rating REAL);
"""

HOTELS = [
    ("Sea Breeze Resort", "Goa", 4200, 4.4), ("Palm Grove Inn", "Goa", 2300, 4.0),
    ("Budget Stay Calangute", "Goa", 1200, 3.6),
    ("Pink City Haveli", "Jaipur", 3800, 4.5), ("Rajputana Lodge", "Jaipur", 2100, 4.1),
    ("Traveller's Nest", "Jaipur", 1100, 3.7),
    ("Snow Valley Retreat", "Manali", 3500, 4.3), ("Pine View Cottage", "Manali", 2000, 4.0),
    ("Mall Road Hostel", "Manali", 900, 3.5),
    ("Capital Grand", "Delhi", 5200, 4.6), ("Metro Comfort", "Delhi", 2600, 4.0),
    ("Backpackers Delhi", "Delhi", 1000, 3.6),
]


def get_db():
    if "db" not in g:
        g.db = sqlite3.connect(DB)
        g.db.row_factory = sqlite3.Row
    return g.db


@app.teardown_appcontext
def close_db(_exc=None):
    conn = g.pop("db", None)
    if conn:
        conn.close()


def init_db():
    conn = sqlite3.connect(DB)
    conn.executescript(SCHEMA)
    if conn.execute("SELECT COUNT(*) FROM hotels").fetchone()[0] == 0:
        conn.executemany(
            "INSERT INTO hotels(name,destination,price_per_night,rating) VALUES(?,?,?,?)", HOTELS)
        start = datetime.date.today()
        rows = []
        for dest_i, dest in enumerate(DESTINATIONS):
            for d in range(90):
                day = (start + datetime.timedelta(days=d)).isoformat()
                rows.append(("Mumbai", dest, day, "IndiGo", "07:30", 3200 + dest_i * 450 + (d % 7) * 150))
                rows.append(("Mumbai", dest, day, "Air India", "17:45", 3900 + dest_i * 500 + (d % 5) * 120))
        conn.executemany(
            "INSERT INTO flights(origin,destination,date,airline,departure,price) VALUES(?,?,?,?,?,?)", rows)
    conn.commit()
    conn.close()


# ---------- STMS-3: validation (kept separate so it can be unit tested) ----------
def validate_preferences(form, today=None):
    """Return (errors, data). `form` is a Flask form or a plain dict."""
    today = today or datetime.date.today()
    errors = []
    dest = (form.get("destination") or "").strip()
    interests = form.getlist("interests") if hasattr(form, "getlist") else list(form.get("interests", []))
    interests = [i for i in interests if i in INTERESTS]

    if not dest:
        errors.append("Destination is required.")
    if not interests:
        errors.append("Select at least one interest.")

    start = end = None
    try:
        start = datetime.datetime.strptime((form.get("start_date") or "").strip(), "%Y-%m-%d").date()
        end = datetime.datetime.strptime((form.get("end_date") or "").strip(), "%Y-%m-%d").date()
    except ValueError:
        errors.append("Enter valid start and end dates.")
    if start and end:
        if start < today:
            errors.append("Start date cannot be in the past.")
        if end < start:
            errors.append("End date cannot be before the start date.")
        elif (end - start).days + 1 > MAX_DAYS:
            errors.append(f"Trip cannot be longer than {MAX_DAYS} days.")

    budget = travelers = None
    try:
        budget = float(str(form.get("budget", "")).strip())
        if budget <= 0:
            errors.append("Budget must be greater than 0.")
    except ValueError:
        errors.append("Budget must be a number.")
    try:
        travelers = int(str(form.get("travelers", "")).strip())
        if not 1 <= travelers <= 20:
            errors.append("Number of travelers must be between 1 and 20.")
    except ValueError:
        errors.append("Number of travelers must be a whole number.")

    data = dict(destination=dest, start=start, end=end, budget=budget,
                travelers=travelers, interests=interests)
    return errors, data


# ---------- STMS-4: itinerary generation ----------
def build_itinerary(destination, days, interests, travelers):
    """Return list of (day_number, activity, cost) or [] if no activities exist."""
    pool = []
    for interest in interests:
        pool += ACTIVITIES.get(destination.lower(), {}).get(interest, [])
    if not pool:
        return []
    return [(d + 1, pool[d % len(pool)][0], pool[d % len(pool)][1] * travelers)
            for d in range(days)]


# ---------- routes ----------
@app.route("/")
def index():
    return render_template("preferences.html", form={}, interests=INTERESTS,
                           destinations=DESTINATIONS, errors=[])


@app.route("/preferences", methods=["POST"])
def save_preferences():
    errors, d = validate_preferences(request.form)
    if errors:
        form = request.form.to_dict()
        form["interests"] = request.form.getlist("interests")
        return render_template("preferences.html", form=form, interests=INTERESTS,
                               destinations=DESTINATIONS, errors=errors), 400
    cur = get_db().execute(
        "INSERT INTO preferences(destination,start_date,end_date,budget,travelers,interests)"
        " VALUES(?,?,?,?,?,?)",
        (d["destination"], d["start"].isoformat(), d["end"].isoformat(),
         d["budget"], d["travelers"], ",".join(d["interests"])))
    get_db().commit()
    flash("Preferences saved successfully.")
    return redirect(url_for("itinerary", pref_id=cur.lastrowid))


@app.route("/itinerary/<int:pref_id>")
def itinerary(pref_id):
    conn = get_db()
    pref = conn.execute("SELECT * FROM preferences WHERE id=?", (pref_id,)).fetchone()
    if pref is None:
        return render_template("itinerary.html", pref=None, days=[], message="Preference not found."), 404
    it = conn.execute("SELECT * FROM itineraries WHERE preference_id=?", (pref_id,)).fetchone()
    if it is None:
        start = datetime.date.fromisoformat(pref["start_date"])
        end = datetime.date.fromisoformat(pref["end_date"])
        total_days = (end - start).days + 1
        plan = build_itinerary(pref["destination"], total_days,
                               pref["interests"].split(","), pref["travelers"])
        if not plan:
            return render_template(
                "itinerary.html", pref=pref, days=[], total=0, it=None,
                message="No activities are available for this destination and interests. "
                        "Try different interests."), 200
        total = sum(c for _, _, c in plan)
        cur = conn.execute("INSERT INTO itineraries(preference_id,total_days,estimated_cost) VALUES(?,?,?)",
                           (pref_id, total_days, total))
        conn.executemany("INSERT INTO itinerary_days(itinerary_id,day_number,activity,cost) VALUES(?,?,?,?)",
                         [(cur.lastrowid, n, a, c) for n, a, c in plan])
        conn.commit()
        it = conn.execute("SELECT * FROM itineraries WHERE id=?", (cur.lastrowid,)).fetchone()
    days = conn.execute("SELECT * FROM itinerary_days WHERE itinerary_id=? ORDER BY day_number",
                        (it["id"],)).fetchall()
    over = it["estimated_cost"] > pref["budget"]
    return render_template("itinerary.html", pref=pref, it=it, days=days, over=over, message=None)


# ---------- STMS-5: flight and hotel search ----------
@app.route("/search")
def search():
    conn = get_db()
    latest = conn.execute("SELECT * FROM preferences ORDER BY id DESC LIMIT 1").fetchone()
    dest = request.args.get("destination")
    date = request.args.get("date")
    sort = request.args.get("sort", "price_asc")
    if dest is None and latest:                     # first visit: default from saved preference
        dest, date = latest["destination"], latest["start_date"]
    dest = (dest or "").strip()
    date = (date or "").strip()
    errors, flights, hotels, searched = [], [], [], bool(request.args) or bool(latest)
    if searched and not dest:
        errors.append("Enter a destination to search.")
    if date:
        try:
            datetime.date.fromisoformat(date)
        except ValueError:
            errors.append("Enter a valid date.")
    if dest and not errors:
        order = "DESC" if sort == "price_desc" else "ASC"
        q, args = "SELECT * FROM flights WHERE LOWER(destination)=LOWER(?)", [dest]
        if date:
            q += " AND date=?"
            args.append(date)
        flights = conn.execute(q + f" ORDER BY price {order}", args).fetchall()
        hotels = conn.execute(
            f"SELECT * FROM hotels WHERE LOWER(destination)=LOWER(?) ORDER BY price_per_night {order}",
            (dest,)).fetchall()
    return render_template("search.html", dest=dest, date=date, sort=sort, errors=errors,
                           flights=flights, hotels=hotels, searched=searched,
                           destinations=DESTINATIONS)


from module1b import m1b, init_m1b_db   # Module 1 part 2: cart, booking, edit trips
from module2 import m2, init_m2_db      # Module 2: companions

app.config["DB_PATH"] = DB
app.config["M1"] = dict(validate=validate_preferences, interests=INTERESTS, destinations=DESTINATIONS)
app.register_blueprint(m1b)
app.register_blueprint(m2)

init_db()
init_m1b_db(DB)
init_m2_db(DB)

if __name__ == "__main__":
    app.run(debug=True)
