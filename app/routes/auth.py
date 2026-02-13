"""
app/routes/auth.py
==================
Dummy multi-user authentication routes.

Uses simple username/password with werkzeug password hashing.
Users are stored in SQLite. Flask session tracks logged-in user.

Endpoints:
    POST /auth/register  — create new user (auto-creates account)
    POST /auth/login     — log in, set session
    POST /auth/logout    — clear session
    GET  /auth/me        — return current user info
"""

import logging
import uuid
from flask import Blueprint, jsonify, request, session
from werkzeug.security import generate_password_hash, check_password_hash

from app.db import storage
from app.config import INITIAL_CAPITAL

logger = logging.getLogger(__name__)

auth_bp = Blueprint("auth", __name__, url_prefix="/auth")


@auth_bp.route("/register", methods=["POST"])
def register():
    """Register a new user.

    Body::
        {"username": "alice", "password": "secret123"}

    Creates a user row AND a matching account with initial capital.
    """
    body = request.get_json(silent=True)
    if not body or not body.get("username") or not body.get("password"):
        return jsonify({"error": "username and password required"}), 400

    username = body["username"].strip().lower()
    if len(username) < 2 or len(username) > 50:
        return jsonify({"error": "username must be 2-50 characters"}), 400

    password = body["password"]
    if len(password) < 4:
        return jsonify({"error": "password must be at least 4 characters"}), 400

    # Check for duplicate
    existing = storage.get_user_by_username(username)
    if existing:
        return jsonify({"error": "username already taken"}), 409

    user_id = str(uuid.uuid4())
    pw_hash = generate_password_hash(password)

    try:
        user = storage.create_user(user_id, username, pw_hash)
    except Exception as exc:
        logger.error("User creation failed: %s", exc)
        return jsonify({"error": "Registration failed"}), 500

    # Create a matching account for this user
    account_id = f"user_{user_id[:8]}"
    storage.ensure_default_account(INITIAL_CAPITAL, account_id)

    # Do NOT set session here — user must log in after registration
    logger.info("User registered: %s (account=%s)", username, account_id)
    return (
        jsonify(
            {
                "user_id": user_id,
                "username": username,
                "account_id": account_id,
                "message": "Account created. Please log in.",
            }
        ),
        201,
    )


@auth_bp.route("/login", methods=["POST"])
def login():
    """Log in with username/password.

    Body::
        {"username": "alice", "password": "secret123"}
    """
    body = request.get_json(silent=True)
    if not body or not body.get("username") or not body.get("password"):
        return jsonify({"error": "username and password required"}), 400

    username = body["username"].strip().lower()
    user = storage.get_user_by_username(username)

    if not user or not check_password_hash(user["password"], body["password"]):
        return jsonify({"error": "Invalid credentials"}), 401

    account_id = f"user_{user['user_id'][:8]}"
    # Ensure account exists (auto-create if first login after migration)
    storage.ensure_default_account(INITIAL_CAPITAL, account_id)

    session["user_id"] = user["user_id"]
    session["username"] = username
    session["account_id"] = account_id

    logger.info("User logged in: %s", username)
    return (
        jsonify(
            {
                "user_id": user["user_id"],
                "username": username,
                "account_id": account_id,
            }
        ),
        200,
    )


@auth_bp.route("/logout", methods=["POST"])
def logout():
    """Clear the session."""
    username = session.get("username", "?")
    session.clear()
    logger.info("User logged out: %s", username)
    return jsonify({"status": "logged_out"}), 200


@auth_bp.route("/me", methods=["GET"])
def me():
    """Return the current user info (or guest/default)."""
    user_id = session.get("user_id")
    if user_id:
        return (
            jsonify(
                {
                    "logged_in": True,
                    "user_id": user_id,
                    "username": session.get("username", ""),
                    "account_id": session.get("account_id", "default"),
                }
            ),
            200,
        )
    return (
        jsonify(
            {
                "logged_in": False,
                "user_id": None,
                "username": "guest",
                "account_id": "default",
            }
        ),
        200,
    )


def get_current_account_id() -> str:
    """Helper: return the account_id for the current session (or 'default')."""
    return session.get("account_id", "default")
