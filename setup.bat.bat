@echo off
echo Criando ambiente virtual...
python -m venv .venv
call .venv\Scripts\activate

echo Instalando dependencias...
python -m pip install --upgrade pip
pip install -r requirements.txt

if not exist .env copy .env.example .env

echo.
echo Pronto. Para rodar:
echo    .venv\Scripts\activate ^&^& python run.py
pause