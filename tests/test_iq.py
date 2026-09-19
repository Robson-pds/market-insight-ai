import os

from dotenv import load_dotenv
from iqair.client import IQOptionClient


def main():
    load_dotenv()
    email = os.getenv("IQ_EMAIL")
    password = os.getenv("IQ_PASSWORD")
    if not email or not password:
        raise SystemExit("IQ_EMAIL/IQ_PASSWORD ausentes no .env")

    print("Conectando...")
    api = IQOptionClient(email, password)
    ok, reason = api.connect()
    print("Conexão:", ok, "|", reason)

    if ok:
        api.change_balance("PRACTICE")
        print("Saldo PRACTICE:", api.get_balance())


if __name__ == "__main__":
    main()