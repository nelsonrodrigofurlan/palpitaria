@echo off
cd /d E:\GitHub\palpitaria
.\.venv\Scripts\python.exe -m uvicorn palpitaria.main:app --reload
echo.
echo ============================================
echo O servidor parou ou falhou ao iniciar.
echo Leia a mensagem acima para ver o motivo.
echo ============================================
pause
