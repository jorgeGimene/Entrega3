import hashlib
import hmac
import json
import os
import re
import secrets
import sqlite3
import time
from contextlib import closing
from pathlib import Path

from flask import Flask, abort, jsonify, request

BODY_LIMIT = 10 * 1024
TOKEN_TTL = 15 * 60
ATTEMPT_WINDOW = 10 * 60
USERNAME = re.compile(r"[a-z0-9_]{3,20}")
TYPES = set(
    "normal fire water grass electric ice fighting poison ground flying "
    "psychic bug rock ghost dragon dark steel fairy".split()
)


def unique_keys(pairs):
    result = dict(pairs)
    if len(result) != len(pairs):
        raise ValueError("Duplicate key")
    return result


def reject_constant(_):
    raise ValueError("Invalid JSON constant")


def read_body():
    if (request.content_length or 0) > BODY_LIMIT:
        abort(400, "Body too large")
    raw = request.stream.read(BODY_LIMIT + 1)
    if len(raw) > BODY_LIMIT:
        abort(400, "Body too large")
    return raw


def read_json():
    raw = read_body()
    try:
        data = json.loads(raw.decode("utf-8"), object_pairs_hook=unique_keys,
                          parse_constant=reject_constant)
        json.dumps(data, ensure_ascii=False).encode("utf-8")
        return data
    except (ValueError, RecursionError):
        abort(400, "Invalid JSON")


def check_fields(data, expected):
    require(isinstance(data, dict))
    if expected - data.keys():
        abort(400, "Missing fields")
    require(data.keys() == expected)


def require(condition):
    if not condition:
        abort(422, "Invalid field value")


def credentials():
    data = read_json()
    check_fields(data, {"username", "password"})
    require(all(isinstance(value, str) for value in data.values()))
    return data["username"], data["password"]


def is_integer(value, minimum, maximum):
    return type(value) is int and minimum <= value <= maximum


def is_type(value):
    return isinstance(value, str) and value in TYPES


def validate_pokemon(data, dex):
    check_fields(data, {"dex", "name", "types", "attacks"})
    require(is_integer(data["dex"], 1, 1025) and data["dex"] == dex)

    name = data["name"]
    require(isinstance(name, str) and 2 <= len(name) <= 20
            and all(letter.isalpha() or letter in " -'." for letter in name))

    types = data["types"]
    require(isinstance(types, list) and 1 <= len(types) <= 2
            and all(is_type(kind) for kind in types) and len(set(types)) == len(types))

    attacks = data["attacks"]
    require(isinstance(attacks, list) and 1 <= len(attacks) <= 20)
    names = set()
    for attack in attacks:
        check_fields(attack, {"name", "type", "level"})
        name = attack["name"]
        require(isinstance(name, str) and 2 <= len(name) <= 30 and name not in names)
        require(is_type(attack["type"]) and is_integer(attack["level"], 1, 100))
        names.add(name)


def hash_password(password, salt):
    return hashlib.scrypt(password.encode("utf-8"), salt=salt, n=2**15, r=8, p=3,
                          maxmem=64 * 1024**2, dklen=32)


def remove_expired(records, now):
    for key, (_, expires) in list(records.items()):
        if expires <= now:
            del records[key]


def create_app(data_dir=None, clock=time.monotonic):
    app = Flask(__name__)
    app.config["MAX_CONTENT_LENGTH"] = BODY_LIMIT + 1
    app.config["PROVIDE_AUTOMATIC_OPTIONS"] = False
    folder = Path(data_dir or os.getenv("POKEAPI_DATA", "data"))
    folder.mkdir(mode=0o700, parents=True, exist_ok=True)
    database = folder / "accounts.sqlite3"
    with closing(sqlite3.connect(database)) as db:
        db.execute("""CREATE TABLE IF NOT EXISTS users (
            id INTEGER PRIMARY KEY, username TEXT UNIQUE NOT NULL,
            salt BLOB NOT NULL, digest BLOB NOT NULL
        )""")

    # Each in-memory record contains a value and its expiration time.
    sessions = {}
    login_attempts = {}
    signup_attempts = {}
    dummy_salt = secrets.token_bytes(16)
    dummy_hash = secrets.token_bytes(32)

    @app.errorhandler(Exception)
    def error_response(error):
        code = getattr(error, "code", 500)
        message = getattr(error, "description", "Internal server error")
        if code == 413:
            return jsonify(error="Body too large"), 400
        if code == 500:
            message = "Internal server error"
        return jsonify(error=message), code

    @app.after_request
    def disable_cache(response):
        response.headers["Cache-Control"] = "no-store"
        return response

    @app.before_request
    def check_method():
        if request.method == "HEAD":
            abort(405)

    @app.get("/version")
    def version():
        read_body()
        return jsonify(service="secure-pokeapi", version="1.0.0")

    @app.post("/signup")
    def signup():
        now = clock()
        remove_expired(signup_attempts, now)
        address = request.remote_addr
        count, expires = signup_attempts.get(address, (0, now + ATTEMPT_WINDOW))
        if count >= 20:
            abort(429, "Too many requests")
        signup_attempts[address] = (count + 1, expires)

        username, password = credentials()
        require(USERNAME.fullmatch(username) is not None)
        salt = secrets.token_bytes(16)
        try:
            with closing(sqlite3.connect(database)) as db, db:
                if db.execute("SELECT 1 FROM users WHERE username=?", (username,)).fetchone():
                    abort(409, "User already exists")
                db.execute("INSERT INTO users (username, salt, digest) VALUES (?, ?, ?)",
                           (username, salt, hash_password(password, salt)))
        except sqlite3.IntegrityError:
            abort(409, "User already exists")
        return jsonify(username=username), 201

    @app.post("/login")
    def login():
        username, password = credentials()
        now = clock()
        remove_expired(login_attempts, now)
        count, _ = login_attempts.get(username, (0, 0))
        if count >= 5:
            abort(429, "Too many requests")

        with closing(sqlite3.connect(database)) as db:
            account = db.execute("SELECT id, salt, digest FROM users WHERE username=?",
                                 (username,)).fetchone()
        salt, expected = account[1:] if account else (dummy_salt, dummy_hash)
        hash_matches = hmac.compare_digest(hash_password(password, salt), expected)
        if not hash_matches or account is None:
            login_attempts[username] = (count + 1, clock() + ATTEMPT_WINDOW)
            abort(401, "Invalid credentials")

        login_attempts.pop(username, None)
        token = secrets.token_hex(32)
        now = clock()
        remove_expired(sessions, now)
        sessions[token] = ((account[0], username), now + TOKEN_TTL)
        return jsonify(token=token)

    def owner_directory(user):
        match = re.fullmatch(r"(?i:Bearer) ([a-f0-9]{64})",
                             request.headers.get("Authorization", ""))
        now = clock()
        remove_expired(sessions, now)
        session = sessions.get(match[1]) if match else None
        if session is None:
            abort(401, "Invalid or missing token")
        (account_id, username), _ = session
        if user != username:
            abort(403, "Forbidden")
        return folder / str(account_id)

    @app.get("/user/<path:user>/pokedex")
    def list_pokedex(user):
        directory = owner_directory(user)
        read_body()
        entries = []
        for path in sorted(directory.glob("*.json"), key=lambda item: int(item.stem)):
            pokemon = json.loads(path.read_bytes())
            entries.append({"dex": pokemon["dex"], "name": pokemon["name"]})
        return jsonify(entries)

    @app.route("/user/<path:user>/pokedex/<dex>", methods=["GET", "PUT", "DELETE"])
    def pokedex_entry(user, dex):
        directory = owner_directory(user)
        if request.method != "PUT":
            read_body()
        if not re.fullmatch(r"[0-9]+", dex):
            abort(400, "Invalid dex")
        digits = dex.lstrip("0") or "0"
        require(len(digits) <= 4)
        number = int(digits)
        require(1 <= number <= 1025)
        path = directory / f"{number}.json"

        if request.method == "PUT":
            data = read_json()
            validate_pokemon(data, number)
            directory.mkdir(mode=0o700, exist_ok=True)
            status = 200 if path.is_file() else 201
            temporary = path.with_suffix(".tmp")
            temporary.write_text(json.dumps(data), encoding="utf-8")
            temporary.replace(path)
            return jsonify(data), status

        if not path.is_file():
            abort(404, "Entry not found")
        if request.method == "DELETE":
            path.unlink()
            return "", 204
        return jsonify(json.loads(path.read_bytes()))

    return app


if __name__ == "__main__":
    os.umask(0o077)
    create_app().run(host="127.0.0.1", port=8080, threaded=False)
