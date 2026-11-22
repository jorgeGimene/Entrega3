import json
import tempfile
import unittest

from api import create_app

BASE = "/user/ash/pokedex"
ENTRY = {"dex": 25, "name": "Pikachu", "types": ["electric"],
         "attacks": [{"name": "Thunder Shock", "type": "electric", "level": 1}]}


class Acceptance(unittest.TestCase):
    def setUp(self):
        data_dir = tempfile.TemporaryDirectory()
        self.addCleanup(data_dir.cleanup)
        self.data_dir = data_dir.name
        self.now = 1000
        self.start()

    def start(self):
        self.client = create_app(self.data_dir, lambda: self.now).test_client()

    def account(self, route, username="ash", password="class-demo"):
        return self.client.post(route, json={"username": username, "password": password})

    def login(self):
        response = self.account("/login")
        self.assertEqual(response.status_code, 200)
        return {"Authorization": "Bearer " + response.json["token"]}

    def test_version_and_accounts(self):
        version = self.client.get("/version")
        self.assertEqual(version.status_code, 200)
        self.assertEqual(version.json, {"service": "secure-pokeapi", "version": "1.0.0"})
        signup = self.account("/signup")
        self.assertEqual(signup.status_code, 201)
        self.assertEqual(signup.json, {"username": "ash"})
        self.assertEqual(self.account("/signup").status_code, 409)
        for username in ("Ash", "a", "a b", "x" * 21):
            self.assertEqual(self.account("/signup", username).status_code, 422)
        self.assertEqual(self.client.post("/signup", data="{").status_code, 400)
        self.assertEqual(self.client.post("/signup", json={"username": "ash"}).status_code, 400)
        wrong_password = self.account("/login", password="wrong")
        unknown_user = self.account("/login", username="missing")
        self.assertEqual((wrong_password.status_code, wrong_password.json),
                         (401, unknown_user.json))
        self.assertEqual(unknown_user.status_code, 401)

    def test_pokedex_and_restart(self):
        self.account("/signup")
        headers = self.login()
        path = BASE + "/25"
        for expected in (201, 200):
            self.assertEqual(self.client.put(path, headers=headers, json=ENTRY).status_code, expected)
        self.assertEqual(self.client.get(BASE, headers=headers).json, [{"dex": 25, "name": "Pikachu"}])
        self.start()
        self.assertEqual(self.client.get(path, headers=headers).status_code, 401)
        headers = self.login()
        self.assertEqual(self.client.get(path, headers=headers).json, ENTRY)
        deleted = self.client.delete(path, headers=headers)
        self.assertEqual((deleted.status_code, deleted.data), (204, b""))
        self.assertEqual(self.client.get(path, headers=headers).status_code, 404)
        self.assertEqual(self.client.delete(path, headers=headers).status_code, 404)

    def test_access_order_and_expiration(self):
        self.account("/signup")
        self.account("/signup", "misty")
        headers = self.login()
        routes = [("GET", ""), ("GET", "/abc"), ("PUT", "/abc"), ("DELETE", "/abc")]
        for method, suffix in routes:
            path = "/user/misty/pokedex" + suffix
            for auth in ({}, {"Authorization": "Bearer invalid"}, {"Authorization": "Bearer " + "0"*64}):
                self.assertEqual(self.client.open(path, method=method, headers=auth).status_code, 401)
            self.assertEqual(self.client.open(path, method=method, headers=headers).status_code, 403)
        self.assertEqual(self.client.get(BASE + "/abc", headers=headers).status_code, 400)
        self.now += 900
        for method, suffix in routes:
            self.assertEqual(self.client.open(BASE + suffix, method=method, headers=headers).status_code, 401)

    def test_validation(self):
        self.account("/signup")
        headers = self.login()
        path = BASE + "/25"
        cases = [{"types": ["electric", "electric"]}, {"types": ["unknown"]},
                 {"dex": 6}, {"dex": True}, {"extra": True}, {"name": "Pikachu25"},
                 {"attacks": ENTRY["attacks"] * 2}, {"attacks": []},
                 {"attacks": [dict(ENTRY["attacks"][0], level=False)]},
                 {"attacks": [dict(ENTRY["attacks"][0], extra=True)]}]
        for invalid in cases:
            self.assertEqual(self.client.put(path, headers=headers, json=ENTRY | invalid).status_code, 422)
        self.assertEqual(self.client.put(path, headers=headers, json={}).status_code, 400)
        body = json.dumps(ENTRY).ljust(10240)
        self.assertEqual(self.client.put(path, headers=headers, data=body).status_code, 201)
        self.assertEqual(self.client.put(path, headers=headers, data=body + " ").status_code, 400)

    def test_limits_reset(self):
        self.account("/signup")
        for _ in range(5):
            self.assertEqual(self.account("/login", password="wrong").status_code, 401)
        self.assertEqual(self.account("/login").status_code, 429)
        for _ in range(19):
            self.assertEqual(self.account("/signup").status_code, 409)
        self.assertEqual(self.account("/signup", "misty").status_code, 429)
        self.now += 600
        self.assertEqual(self.account("/login").status_code, 200)
        self.assertEqual(self.account("/signup", "misty").status_code, 201)
