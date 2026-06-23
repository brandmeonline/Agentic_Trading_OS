import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from core.rest_api import JWTAuth


def test_jwt_tampered_signature_is_rejected():
    auth = JWTAuth("test-secret")
    token = auth.create_token("user-1", ["read"])
    header, payload, signature = token.split(".")
    tampered = f"{header}.{payload}.{signature[:-1]}x"

    assert auth.verify_token(token)["sub"] == "user-1"
    assert auth.verify_token(tampered) is None
