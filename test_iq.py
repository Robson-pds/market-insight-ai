import os
from dotenv import load_dotenv
load_dotenv()

from iqair.client import IQOptionClient

email = os.getenv("IQ_EMAIL")
password = os.getenv("IQ_PASSWORD")

print("Email:", email)
print("Senha:", "OK" if password else "VAZIA")

print("\nConectando...")
api = IQOptionClient(email, password)
ok, reason = api.connect()
print("Conexão:", ok, "|", reason)

if ok:
    api.change_balance("PRACTICE")
    print("Saldo PRACTICE:", api.get_balance())

    # Lista métodos disponíveis para sabermos quais usar
    methods = [m for m in dir(api) if not m.startswith("_")]
    print("\nMétodos disponíveis:")
    for m in methods:
        print(" -", m)