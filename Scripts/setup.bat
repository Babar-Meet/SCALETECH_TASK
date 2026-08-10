@echo off
echo ==============================================
echo  WhatsApp to YouTube Music Extractor - Setup
echo ==============================================
echo.

cd ..

echo [1/2] Setting up Backend...
cd backend
if not exist venv (
    echo Creating Python virtual environment...
    python -m venv venv
)
echo Installing Python dependencies...
call venv\Scripts\activate.bat
pip install -r requirements.txt

echo.
echo Configuring cuBLAS DLLs for GPU transcription (whisper)...
if exist venv\Lib\site-packages\nvidia\cublas\bin\cublas64_12.dll (
    copy /Y venv\Lib\site-packages\nvidia\cublas\bin\cublas64_12.dll venv\Lib\site-packages\ctranslate2\ >nul
    copy /Y venv\Lib\site-packages\nvidia\cublas\bin\cublasLt64_12.dll venv\Lib\site-packages\ctranslate2\ >nul
    echo cuBLAS DLLs copied into ctranslate2 - GPU whisper enabled.
) else (
    echo WARNING: nvidia-cublas-cu12 not found, GPU whisper will fall back to CPU.
)

cd ..
echo Backend setup complete!
echo.

echo [2/2] Setting up Frontend...
cd frontend
echo Installing Node modules...
call npm install
cd ..
echo Frontend setup complete!
echo.

echo ==============================================
echo Setup Finished successfully!
echo You can now use "Scripts\run.bat" to start the app.
echo ==============================================
pause
