"""Issue a Pro license key by hand (beta testers, refunds, giveaways).

python tools/issue_license.py someone@example.com              # lifetime
python tools/issue_license.py someone@example.com --days 365   # yearly
"""

import argparse
from datetime import date, timedelta
from pathlib import Path

from cryptography.hazmat.primitives.serialization import load_pem_private_key

from homelabscribe.licensing import sign

p = argparse.ArgumentParser()
p.add_argument("email")
p.add_argument("--days", type=int, help="Days until expiry; omit for lifetime")
p.add_argument("--key", default="private_key.pem")
args = p.parse_args()

private_key = load_pem_private_key(Path(args.key).read_bytes(), password=None)
expires = date.today() + timedelta(days=args.days) if args.days else None
print(sign(args.email, "pro", expires, private_key))
