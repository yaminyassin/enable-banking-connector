"""Offline checks: JWT signs with real key and verifies with public.crt. Prints only claims/header."""
import os, jwt
from cryptography import x509
from ebmcp import client
tok = client.make_jwt(app_id=os.environ.get("ENABLEBANKING_APP_ID", "00000000-0000-4000-8000-000000000000"))
pub = x509.load_pem_x509_certificate(open(os.environ.get("ENABLEBANKING_CERT_PATH", "/workspace/enablebanking/public.crt"), "rb").read()).public_key()
claims = jwt.decode(tok, pub, algorithms=["RS256"], audience="api.enablebanking.com", issuer="enablebanking.com")
print("header:", jwt.get_unverified_header(tok)); print("claims ok:", sorted(claims), "ttl", claims["exp"] - claims["iat"])
