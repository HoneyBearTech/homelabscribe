"""Create the Ed25519 keypair used to sign Pro license keys. Run ONCE.

    python tools/make_keypair.py

- private_key.pem stays with you (password manager / offline backup). Never commit it;
  .gitignore already excludes it. It also goes into the payment webhook as a secret.
- Paste the printed public key into PUBLIC_KEY_B64 in src/homelabscribe/licensing.py.
"""

import base64
import sys
from pathlib import Path

from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey
from cryptography.hazmat.primitives.serialization import (
    Encoding,
    NoEncryption,
    PrivateFormat,
    PublicFormat,
)

out = Path("private_key.pem")
if out.exists():
    sys.exit("private_key.pem already exists. Refusing to overwrite your signing key.")

key = Ed25519PrivateKey.generate()
out.write_bytes(key.private_bytes(Encoding.PEM, PrivateFormat.PKCS8, NoEncryption()))
out.chmod(0o600)
pub = base64.urlsafe_b64encode(key.public_key().public_bytes(Encoding.Raw, PublicFormat.Raw)).decode()
print("Wrote private_key.pem (keep it secret, keep it backed up).")
print(f'PUBLIC_KEY_B64 = "{pub}"')
