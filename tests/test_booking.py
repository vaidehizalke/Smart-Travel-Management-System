"""Tests for STMS-6 (cart, booking) and STMS-7 (edit, delete). Run: python -m unittest discover tests"""
import os, sys, sqlite3, unittest, datetime
sys.path.insert(0, os.path.dirname(os.path.dirname(__file__)))
os.environ["STMS_DB"] = os.path.join(os.path.dirname(__file__), "test.db")
import app as appmod
from module1b import hotel_nights, rooms_needed, flight_line, hotel_line, make_booking_ref


class TestHelpers(unittest.TestCase):
    def test_nights(self):
        self.assertEqual(hotel_nights("2026-11-10", "2026-11-13"), 3)
        self.assertEqual(hotel_nights("2026-11-10", "2026-11-10"), 1)      # same-day trip, boundary
    def test_rooms(self):
        for travelers, rooms in [(1, 1), (2, 1), (3, 2), (4, 2), (5, 3)]:
            self.assertEqual(rooms_needed(travelers), rooms)
    def test_line_totals(self):
        self.assertEqual(flight_line(4000, 2), 8000)
        self.assertEqual(hotel_line(2000, 3, 1), 6000)
    def test_booking_ref(self):
        self.assertEqual(make_booking_ref(7, 2026), "BK20260007")


class TestCartAndTrips(unittest.TestCase):
    def setUp(self):
        c = sqlite3.connect(appmod.DB)
        for t in ("messages", "connection_requests", "profiles", "booking_items", "bookings", "cart",
                  "itinerary_days", "itineraries", "preferences"):
            c.execute(f"DELETE FROM {t}")
        c.commit(); c.close()
        self.client = appmod.app.test_client()
        d = lambda n: (datetime.date.today() + datetime.timedelta(days=n)).isoformat()
        self.start, self.end = d(10), d(13)                    # 4 days, 3 nights
        self.client.post("/preferences", data=dict(destination="Goa", start_date=self.start, end_date=self.end,
                         budget="50000", travelers="2", interests=["beach"]))
        c = sqlite3.connect(appmod.DB)
        self.pid = c.execute("SELECT MAX(id) FROM preferences").fetchone()[0]
        self.flight = c.execute("INSERT INTO flights(origin,destination,date,airline,departure,price) "
                                "VALUES('Mumbai','Goa',?,'TestAir','09:00',4000)", (self.start,)).lastrowid
        self.late_flight = c.execute("INSERT INTO flights(origin,destination,date,airline,departure,price) "
                                     "VALUES('Mumbai','Goa',?,'TestAir','09:00',4000)", (d(40),)).lastrowid
        self.delhi_flight = c.execute("INSERT INTO flights(origin,destination,date,airline,departure,price) "
                                      "VALUES('Mumbai','Delhi',?,'TestAir','09:00',4000)", (self.start,)).lastrowid
        self.hotel = c.execute("INSERT INTO hotels(name,destination,price_per_night,rating) "
                               "VALUES('Test Inn','Goa',2000,4.0)").lastrowid
        c.commit(); c.close()

    def add(self, item):
        return self.client.post("/cart/add", data={"pref_id": self.pid, "item": item}, follow_redirects=True)

    def test_cart_total_duplicates_remove(self):
        self.assertIn(b"Added TestAir flight", self.add(f"flight:{self.flight}").data)
        page = self.add(f"hotel:{self.hotel}").data
        self.assertIn(b"14000", page)                                   # 4000*2 + 2000*3*1
        self.assertIn(b"already in your cart", self.add(f"flight:{self.flight}").data)
        c = sqlite3.connect(appmod.DB); cid = c.execute("SELECT id FROM cart WHERE item_type='flight'").fetchone()[0]; c.close()
        page = self.client.post(f"/cart/{self.pid}/remove/{cid}", follow_redirects=True).data
        self.assertIn(b"removed", page); self.assertIn(b"6000", page)

    def test_cart_validation(self):
        self.assertIn(b"not for your trip", self.add(f"flight:{self.delhi_flight}").data)
        self.assertIn(b"within your trip dates", self.add(f"flight:{self.late_flight}").data)
        self.assertIn(b"Invalid item", self.add("train:1").data)
        self.assertIn(b"Invalid item", self.add("flight:abc").data)
        self.assertIn(b"no longer exists", self.add("flight:99999999").data)
        r = self.client.post("/cart/add", data={"pref_id": 9999, "item": f"flight:{self.flight}"}, follow_redirects=True)
        self.assertIn(b"Choose a saved trip", r.data)

    def test_empty_cart_cannot_book(self):
        self.assertIn(b"cart is empty", self.client.post(f"/cart/{self.pid}/confirm", follow_redirects=True).data)

    def test_booking_flow_and_locks(self):
        self.add(f"flight:{self.flight}"); self.add(f"hotel:{self.hotel}")
        r = self.client.post(f"/cart/{self.pid}/confirm", follow_redirects=True)
        self.assertIn(b"BK", r.data); self.assertIn(b"14000", r.data)           # booking ID and total shown
        self.assertIn(b"empty", self.client.get(f"/cart/{self.pid}").data)       # cart cleared
        self.assertIn(b"already booked", self.add(f"flight:{self.flight}").data)
        self.assertIn(b"confirmed booking", self.client.get(f"/trips/{self.pid}/edit", follow_redirects=True).data)
        self.assertIn(b"cannot be deleted", self.client.post(f"/trips/{self.pid}/delete", follow_redirects=True).data)

    def test_edit_trip(self):
        self.add(f"hotel:{self.hotel}")
        self.assertEqual(self.client.get(f"/trips/{self.pid}/edit").status_code, 200)
        bad = self.client.post(f"/trips/{self.pid}/edit", data=dict(destination="Goa", start_date=self.end,
                               end_date=self.start, budget="-5", travelers="2", interests=["beach"]))
        self.assertEqual(bad.status_code, 400); self.assertIn(b"cannot be before", bad.data)
        d = lambda n: (datetime.date.today() + datetime.timedelta(days=n)).isoformat()
        ok = self.client.post(f"/trips/{self.pid}/edit", data=dict(destination="Jaipur", start_date=d(20),
                              end_date=d(21), budget="30000", travelers="3", interests=["culture"]), follow_redirects=True)
        self.assertEqual(ok.status_code, 200); self.assertIn(b"Jaipur", ok.data); self.assertIn(b"Cart items were cleared", ok.data)
        self.assertIn(b"2 days", self.client.get("/trips").data)                 # itinerary regenerated for new dates

    def test_delete_with_confirmation(self):
        page = self.client.get(f"/trips/{self.pid}/delete")
        self.assertIn(b"Yes, delete", page.data)                                  # confirmation shown first
        self.assertEqual(len(sqlite3.connect(appmod.DB).execute("SELECT id FROM preferences").fetchall()), 1)
        r = self.client.post(f"/trips/{self.pid}/delete", follow_redirects=True)
        self.assertIn(b"Trip deleted", r.data)
        self.assertEqual(sqlite3.connect(appmod.DB).execute("SELECT COUNT(*) FROM preferences").fetchone()[0], 0)
        self.assertEqual(self.client.get(f"/trips/{self.pid}/delete").status_code, 404)

    def test_delete_blocked_by_companion_profile(self):
        self.client.post("/companions/profile/new", data=dict(name="Asha", email="a@x.com", age="25",
                         travel_style="Comfort", interests=["beach"], preference_id=str(self.pid)))
        self.assertIn(b"companion profile", self.client.post(f"/trips/{self.pid}/delete", follow_redirects=True).data)


if __name__ == "__main__":
    unittest.main()
