@echo off
echo ==============================================
echo Starting WhatsApp to YouTube Music Extractor
echo ==============================================
echo.

cd ..

echo Starting Backend Server (FastAPI)...
start cmd /k "cd backend && call venv\Scripts\activate.bat && uvicorn main:app --reload --port 8000"

echo Starting Frontend Server (Vite)...
start cmd /k "cd frontend && npm run dev"

echo.
echo Both servers are starting in new windows.
echo Frontend will be available at: http://localhost:5173
echo Backend will be available at: http://localhost:8000
echo.
echo Close this window at any time. To stop the servers, close the two popup windows.
pause
