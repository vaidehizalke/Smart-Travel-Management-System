"""Tests for Module 2. Run:  python -m unittest discover tests"""
import os, sys, sqlite3, unittest, datetime
sys.path.insert(0, os.path.dirname(os.path.dirname(__file__)))
os.environ["STMS_DB"] = os.path.join(os.path.dirname(__file__), "test.db")
import app as appmod
from module2 import validate_profile, compatibility_score, validate_message

def trip(dest="Goa", s="2026-11-10", e="2026-11-14", budget=20000, interests=("beach", "food")):
    return dict(destination=dest, start_date=s, end_date=e, budget=budget, interests=list(interests))

def pform(**kw):
    base = dict(name="Asha", email="asha@example.com", age="25", travel_style="Comfort",
                interests=["beach"], preference_id="1")
    base.update(kw)
    return base

class TestProfileValidation(unittest.TestCase):
    def bad(self, **kw): return bool(validate_profile(pform(**kw))[0])
    def test_valid(self):            self.assertFalse(self.bad())
    def test_age_boundaries(self):
        for a, bad in [("17", True), ("18", False), ("19", False), ("69", False), ("70", False), ("71", True), ("abc", True)]:
            self.assertEqual(self.bad(age=a), bad, a)
    def test_email_formats(self):
        for e, bad in [("a@b.com", False), ("a@b", True), ("ab.com", True), ("", True), ("a b@c.com", True)]:
            self.assertEqual(self.bad(email=e), bad, e)
    def test_name_required(self):     self.assertTrue(self.bad(name=" "))
    def test_style_invalid(self):     self.assertTrue(self.bad(travel_style="Royal"))
    def test_no_interest(self):       self.assertTrue(self.bad(interests=[]))

class TestScore(unittest.TestCase):
    def test_identical_trips_high(self):   self.assertEqual(compatibility_score(trip(), trip()), 100)
    def test_no_overlap_excluded(self):
        self.assertIsNone(compatibility_score(trip(), trip(s="2026-11-20", e="2026-11-25")))
    def test_one_day_overlap_counts(self):
        self.assertIsNotNone(compatibility_score(trip(), trip(s="2026-11-14", e="2026-11-18")))
    def test_other_destination_scores_lower(self):
        self.assertLess(compatibility_score(trip(), trip(dest="Delhi")), compatibility_score(trip(), trip()))
    def test_range(self):
        s = compatibility_score(trip(), trip(dest="Manali", budget=1, interests=("nature",)))
        self.assertTrue(0 <= s <= 100)

class TestMessage(unittest.TestCase):
    def test_empty(self):     self.assertIsNotNone(validate_message("   "))
    def test_ok(self):        self.assertIsNone(validate_message("Hi"))
    def test_limit(self):
        self.assertIsNone(validate_message("a" * 500)); self.assertIsNotNone(validate_message("a" * 501))

class TestFlow(unittest.TestCase):
    """Integration: Module 1 trips -> Module 2 profiles, matching, requests, chat."""
    def setUp(self):
        c = sqlite3.connect(appmod.DB)
        for t in ("messages", "connection_requests", "profiles", "itinerary_days", "itineraries", "preferences"):
            c.execute(f"DELETE FROM {t}")
        c.commit(); c.close()
        self.today = datetime.date.today()

    def add_trip(self, client, dest, start, end):
        d = lambda n: (self.today + datetime.timedelta(days=n)).isoformat()
        client.post("/preferences", data=dict(destination=dest, start_date=d(start), end_date=d(end),
                    budget="20000", travelers="2", interests=["beach", "food"]))
        c = sqlite3.connect(appmod.DB); i = c.execute("SELECT MAX(id) FROM preferences").fetchone()[0]; c.close()
        return i

    def add_profile(self, client, name, pref):
        r = client.post("/companions/profile/new", data=dict(name=name, email=f"{name.lower()}@example.com",
                        age="25", travel_style="Comfort", interests=["beach", "food"], preference_id=str(pref)))
        c = sqlite3.connect(appmod.DB); i = c.execute("SELECT id FROM profiles WHERE name=?", (name,)).fetchone(); c.close()
        return r, (i[0] if i else None)

    def test_full_flow(self):
        a, b, far = appmod.app.test_client(), appmod.app.test_client(), appmod.app.test_client()
        pa, pb, pf = self.add_trip(a, "Goa", 10, 14), self.add_trip(b, "Goa", 12, 16), self.add_trip(far, "Goa", 60, 64)
        _, ida = self.add_profile(a, "Asha", pa); _, idb = self.add_profile(b, "Ravi", pb); _, idf = self.add_profile(far, "Meera", pf)
        page = a.get("/companions/matches").data
        self.assertIn(b"Ravi", page); self.assertNotIn(b"Meera", page)               # overlap rule
        self.assertIn(b"yourself", a.post(f"/companions/request/{ida}", follow_redirects=True).data)
        self.assertIn(b"Pending", a.post(f"/companions/request/{idb}", follow_redirects=True).data)
        self.assertIn(b"already exists", a.post(f"/companions/request/{idb}", follow_redirects=True).data)
        self.assertIn(b"already exists", b.post(f"/companions/request/{ida}", follow_redirects=True).data)  # reverse duplicate
        c = sqlite3.connect(appmod.DB); rid = c.execute("SELECT id FROM connection_requests").fetchone()[0]; c.close()
        self.assertEqual(a.get(f"/companions/chat/{rid}").status_code, 403)            # not accepted yet
        self.assertIn(b"Only the receiver", a.post(f"/companions/request/{rid}/respond", data={"action": "accept"}, follow_redirects=True).data)
        self.assertIn(b"accepted", b.post(f"/companions/request/{rid}/respond", data={"action": "accept"}, follow_redirects=True).data)
        self.assertEqual(far.get(f"/companions/chat/{rid}").status_code, 403)         # outsider blocked
        self.assertEqual(a.get(f"/companions/chat/{rid}").status_code, 200)
        self.assertIn(b"cannot be empty", a.post(f"/companions/chat/{rid}", data={"body": "  "}, follow_redirects=True).data)
        a.post(f"/companions/chat/{rid}", data={"body": "Hello Ravi"})
        self.assertIn(b"Hello Ravi", b.get(f"/companions/chat/{rid}").data)           # saved and visible to both

    def test_profile_rules(self):
        a = appmod.app.test_client(); p = self.add_trip(a, "Goa", 10, 14)
        r, _ = self.add_profile(a, "Asha", p); self.assertEqual(r.status_code, 302)
        r, _ = self.add_profile(a, "Asha", p); self.assertEqual(r.status_code, 400)    # duplicate email
        r = a.post("/companions/profile/new", data=dict(name="Bob", email="b@x.com", age="17", travel_style="Budget",
                   interests=["food"], preference_id=str(p)))
        self.assertEqual(r.status_code, 400)

if __name__ == "__main__":
    unittest.main()
