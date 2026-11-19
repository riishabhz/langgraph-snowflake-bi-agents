"""Generate an RSA key pair for Snowflake key-pair auth.

Writes rsa_key.p8 (private, keep it secret, never commit it) and
rsa_key.pub (paste into ALTER USER ... SET RSA_PUBLIC_KEY).
"""
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric import rsa

key = rsa.generate_private_key(public_exponent=65537, key_size=2048)

with open("rsa_key.p8", "wb") as f:
    f.write(
        key.private_bytes(
            encoding=serialization.Encoding.PEM,
            format=serialization.PrivateFormat.PKCS8,
            encryption_algorithm=serialization.NoEncryption(),
        )
    )

pub = key.public_key().public_bytes(
    encoding=serialization.Encoding.PEM,
    format=serialization.PublicFormat.SubjectPublicKeyInfo,
).decode()

with open("rsa_key.pub", "w") as f:
    f.write(pub)

body = "".join(line for line in pub.splitlines() if "-----" not in line)
print("Run this in Snowsight:\n")
print(f"ALTER USER RISHABH SET RSA_PUBLIC_KEY = '{body}';")
