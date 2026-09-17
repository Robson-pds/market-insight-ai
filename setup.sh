#!/usr/bin/env bash
set -e

echo "→ Criando ambiente virtual..."
python3 -m venv .venv
source .venv/bin/activate

echo "→ Instalando dependências..."
pip install --upgrade pip
pip install -r requirements.txt

echo "→ Copiando .env.example para .env (se ainda não existir)..."
[ -f .env ] || cp .env.example .env

echo ""
echo "✔ Pronto. Para rodar:"
echo "   source .venv/bin/activate && python run.py"