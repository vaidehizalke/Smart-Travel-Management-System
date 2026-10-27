"""Module 1, part 2: STMS-6 Booking cart and STMS-7 Save and edit itinerary.

Plugs into the app as a Flask Blueprint. It reuses Module 1's validation through
app.config["M1"] and works on the same `preferences` (trip) and `itineraries` tables.
"""
import math
import sqlite3
import datetime
from flask import (Blueprint, render_template, request, redirect, url_for,
                   flash, g, abort, current_app)

m1b = Blueprint("m1b", __name__)
MAX_CART_ITEMS = 10

SCHEMA = """
CREATE TABLE IF NOT EXISTS cart(
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  preference_id INTEGER NOT NULL REFERENCES preferences(id),
  item_type TEXT NOT NULL CHECK(item_type IN ('flight','hotel')),
  item_id INTEGER NOT NULL, name TEXT NOT NULL, details TEXT,
  unit_price REAL NOT NULL, quantity INTEGER NOT NULL, line_total REAL NOT NULL,
  UNIQUE(preference_id, item_type, item_id));
CREATE TABLE IF NOT EXISTS bookings(
  id INTEGER PRIMARY KEY AUTOINCREMENT, booking_ref TEXT UNIQUE,
  preference_id INTEGER NOT NULL REFERENCES preferences(id),
  total REAL NOT NULL, status TEXT NOT NULL DEFAULT 'Confirmed',
  created_at TEXT DEFAULT CURRENT_TIMESTAMP);
CREATE TABLE IF NOT EXISTS booking_items(
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  booking_id INTEGER NOT NULL REFERENCES bookings(id),
  item_type TEXT NOT NULL, item_id INTEGER NOT NULL, name TEXT NOT NULL, details TEXT,
  unit_price REAL NOT NULL, quantity INTEGER NOT NULL, line_total REAL NOT NULL);
"""


def init_m1b_db(path):
    conn = sqlite3.connect(path)
    conn.executescript(SCHEMA)
    conn.commit()
    conn.close()


def get_db():
    if "db" not in g:
        g.db = sqlite3.connect(current_app.config["DB_PATH"])
        g.db.row_factory = sqlite3.Row
    return g.db


@m1b.app_context_processor
def inject_trips():
    rows = get_db().execute("SELECT id,destination,start_date,end_date FROM preferences ORDER BY id DESC").fetchall()
    return {"all_trips": rows}


# ---------------- pure helpers (unit tested) ----------------
def hotel_nights(start_iso, end_iso):
    """Nights = end - start, at least 1 (a same-day trip still needs one night)."""
    days = (datetime.date.fromisoformat(end_iso) - datetime.date.fromisoformat(start_iso)).days
    return max(1, days)


def rooms_needed(travelers):
    return math.ceil(travelers / 2)


def flight_line(price, travelers):
    return price * travelers


def hotel_line(price_per_night, nights, rooms):
    return price_per_night * nights * rooms


def make_booking_ref(booking_id, year):
    return f"BK{year}{booking_id:04d}"


# ---------------- STMS-6: cart and booking ----------------
def _trip_or_404(db, pref_id):
    trip = db.execute("SELECT * FROM preferences WHERE id=?", (pref_id,)).fetchone()
    if trip is None:
        abort(404)
    return trip


@m1b.route("/cart/add", methods=["POST"])
def add_to_cart():
    db = get_db()
    back = request.referrer or url_for("search")
    pref_id = request.form.get("pref_id", type=int)
    trip = db.execute("SELECT * FROM preferences WHERE id=?", (pref_id,)).fetchone() if pref_id else None
    if trip is None:
        flash("Choose a saved trip first. Plan a trip, then search.", "error")
        return redirect(back)
    try:
        kind, raw = (request.form.get("item") or "").split(":", 1)
        item_id = int(raw)
    except ValueError:
        kind = None
    if kind not in ("flight", "hotel"):
        flash("Invalid item.", "error")
        return redirect(back)

    table = "flights" if kind == "flight" else "hotels"
    row = db.execute(f"SELECT * FROM {table} WHERE id=?", (item_id,)).fetchone()
    if row is None:
        flash("This item no longer exists.", "error")
        return redirect(back)
    if row["destination"].lower() != trip["destination"].lower():
        flash(f"This {kind} is not for your trip destination ({trip['destination']}).", "error")
        return redirect(back)
    if kind == "flight" and not (trip["start_date"] <= row["date"] <= trip["end_date"]):
        flash("Flight date must fall within your trip dates.", "error")
        return redirect(back)
    if db.execute("SELECT 1 FROM cart WHERE preference_id=? AND item_type=? AND item_id=?",
                  (pref_id, kind, item_id)).fetchone():
        flash("This item is already in your cart.", "error")
        return redirect(back)
    if db.execute("SELECT 1 FROM booking_items bi JOIN bookings b ON b.id=bi.booking_id "
                  "WHERE b.preference_id=? AND bi.item_type=? AND bi.item_id=? AND b.status='Confirmed'",
                  (pref_id, kind, item_id)).fetchone():
        flash("This item is already booked for this trip.", "error")
        return redirect(back)
    if db.execute("SELECT COUNT(*) FROM cart WHERE preference_id=?", (pref_id,)).fetchone()[0] >= MAX_CART_ITEMS:
        flash(f"Cart can hold at most {MAX_CART_ITEMS} items.", "error")
        return redirect(back)

    if kind == "flight":
        name = f"{row['airline']} flight"
        details = f"{row['origin']} to {row['destination']}, {row['date']} at {row['departure']}"
        unit, qty = row["price"], trip["travelers"]
        line = flight_line(unit, qty)
    else:
        nights, rooms = hotel_nights(trip["start_date"], trip["end_date"]), rooms_needed(trip["travelers"])
        name = row["name"]
        details = f"{nights} night(s) x {rooms} room(s), rating {row['rating']}"
        unit, qty = row["price_per_night"], nights * rooms
        line = hotel_line(unit, nights, rooms)
    db.execute("INSERT INTO cart(preference_id,item_type,item_id,name,details,unit_price,quantity,line_total)"
               " VALUES(?,?,?,?,?,?,?,?)", (pref_id, kind, item_id, name, details, unit, qty, line))
    db.commit()
    flash(f"Added {name} to your cart.")
    return redirect(url_for("m1b.cart", pref_id=pref_id))


@m1b.route("/cart/<int:pref_id>")
def cart(pref_id):
    db = get_db()
    trip = _trip_or_404(db, pref_id)
    items = db.execute("SELECT * FROM cart WHERE preference_id=? ORDER BY id", (pref_id,)).fetchall()
    total = sum(i["line_total"] for i in items)
    return render_template("booking/cart.html", trip=trip, items=items, total=total,
                           nights=hotel_nights(trip["start_date"], trip["end_date"]),
                           rooms=rooms_needed(trip["travelers"]))


@m1b.route("/cart/<int:pref_id>/remove/<int:cart_id>", methods=["POST"])
def remove_from_cart(pref_id, cart_id):
    db = get_db()
    cur = db.execute("DELETE FROM cart WHERE id=? AND preference_id=?", (cart_id, pref_id))
    db.commit()
    flash("Item removed from cart." if cur.rowcount else "Item was not in your cart.",
          "ok" if cur.rowcount else "error")
    return redirect(url_for("m1b.cart", pref_id=pref_id))


@m1b.route("/cart/<int:pref_id>/confirm", methods=["POST"])
def confirm_booking(pref_id):
    db = get_db()
    _trip_or_404(db, pref_id)
    items = db.execute("SELECT * FROM cart WHERE preference_id=?", (pref_id,)).fetchall()
    if not items:
        flash("Your cart is empty. Add a flight or hotel first.", "error")
        return redirect(url_for("m1b.cart", pref_id=pref_id))
    total = sum(i["line_total"] for i in items)
    cur = db.execute("INSERT INTO bookings(preference_id,total) VALUES(?,?)", (pref_id, total))
    bid = cur.lastrowid
    db.execute("UPDATE bookings SET booking_ref=? WHERE id=?",
               (make_booking_ref(bid, datetime.date.today().year), bid))
    db.executemany(
        "INSERT INTO booking_items(booking_id,item_type,item_id,name,details,unit_price,quantity,line_total)"
        " VALUES(?,?,?,?,?,?,?,?)",
        [(bid, i["item_type"], i["item_id"], i["name"], i["details"], i["unit_price"],
          i["quantity"], i["line_total"]) for i in items])
    db.execute("DELETE FROM cart WHERE preference_id=?", (pref_id,))
    db.commit()
    return redirect(url_for("m1b.booking", booking_id=bid))


@m1b.route("/bookings/<int:booking_id>")
def booking(booking_id):
    db = get_db()
    b = db.execute("SELECT * FROM bookings WHERE id=?", (booking_id,)).fetchone()
    if b is None:
        abort(404)
    trip = db.execute("SELECT * FROM preferences WHERE id=?", (b["preference_id"],)).fetchone()
    items = db.execute("SELECT * FROM booking_items WHERE booking_id=? ORDER BY id", (booking_id,)).fetchall()
    return render_template("booking/confirmation.html", b=b, trip=trip, items=items)


# ---------------- STMS-7: save, edit, delete itinerary ----------------
def _trip_flags(db, pid):
    return dict(
        bookings=db.execute("SELECT COUNT(*) FROM bookings WHERE preference_id=?", (pid,)).fetchone()[0],
        profiles=db.execute("SELECT COUNT(*) FROM profiles WHERE preference_id=?", (pid,)).fetchone()[0])


@m1b.route("/trips")
def trips():
    db = get_db()
    rows = db.execute(
        "SELECT t.*, i.id AS itinerary_id, i.total_days, i.estimated_cost,"
        " (SELECT COUNT(*) FROM bookings b WHERE b.preference_id=t.id) AS bookings,"
        " (SELECT COUNT(*) FROM cart c WHERE c.preference_id=t.id) AS cart_items"
        " FROM preferences t LEFT JOIN itineraries i ON i.preference_id=t.id ORDER BY t.id DESC").fetchall()
    bookings = db.execute("SELECT b.*, t.destination FROM bookings b JOIN preferences t ON t.id=b.preference_id "
                          "ORDER BY b.id DESC").fetchall()
    return render_template("trips/list.html", trips=rows, bookings=bookings)


@m1b.route("/trips/<int:pid>/edit", methods=["GET", "POST"])
def edit_trip(pid):
    db = get_db()
    trip = _trip_or_404(db, pid)
    if _trip_flags(db, pid)["bookings"]:
        flash("This trip has a confirmed booking and can no longer be edited.", "error")
        return redirect(url_for("m1b.trips"))
    cfg = current_app.config["M1"]
    errors = []
    form = dict(trip)
    form["interests"] = trip["interests"].split(",")
    if request.method == "POST":
        errors, d = cfg["validate"](request.form)
        form = request.form.to_dict()
        form["interests"] = request.form.getlist("interests")
        if not errors:
            db.execute("UPDATE preferences SET destination=?,start_date=?,end_date=?,budget=?,travelers=?,"
                       "interests=? WHERE id=?",
                       (d["destination"], d["start"].isoformat(), d["end"].isoformat(), d["budget"],
                        d["travelers"], ",".join(d["interests"]), pid))
            db.execute("DELETE FROM itinerary_days WHERE itinerary_id IN "
                       "(SELECT id FROM itineraries WHERE preference_id=?)", (pid,))
            db.execute("DELETE FROM itineraries WHERE preference_id=?", (pid,))
            cleared = db.execute("DELETE FROM cart WHERE preference_id=?", (pid,)).rowcount
            db.commit()
            flash("Trip updated. Your itinerary has been regenerated."
                  + (" Cart items were cleared because trip details changed." if cleared else ""))
            return redirect(url_for("itinerary", pref_id=pid))
    return render_template("trips/edit.html", trip=trip, form=form, errors=errors,
                           interests=cfg["interests"], destinations=cfg["destinations"]), (400 if errors else 200)


@m1b.route("/trips/<int:pid>/delete", methods=["GET", "POST"])
def delete_trip(pid):
    db = get_db()
    trip = _trip_or_404(db, pid)
    flags = _trip_flags(db, pid)
    blockers = []
    if flags["bookings"]:
        blockers.append("it has a confirmed booking")
    if flags["profiles"]:
        blockers.append("a companion profile uses it (edit that profile to pick another trip first)")
    if request.method == "POST":
        if blockers:
            flash("This trip cannot be deleted because " + " and ".join(blockers) + ".", "error")
            return redirect(url_for("m1b.trips"))
        db.execute("DELETE FROM cart WHERE preference_id=?", (pid,))
        db.execute("DELETE FROM itinerary_days WHERE itinerary_id IN "
                   "(SELECT id FROM itineraries WHERE preference_id=?)", (pid,))
        db.execute("DELETE FROM itineraries WHERE preference_id=?", (pid,))
        db.execute("DELETE FROM preferences WHERE id=?", (pid,))
        db.commit()
        flash("Trip deleted.")
        return redirect(url_for("m1b.trips"))
    return render_template("trips/delete.html", trip=trip, blockers=blockers)
