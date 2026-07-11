@echo off
REM ============================================================
REM  Recall — one-click launcher (Windows)
REM  No conda activation needed — uses the env's python directly.
REM ============================================================
setlocal
cd /d "%~dp0"

REM --- Step 1: find the conda installation folder ---
set "CONDA_ROOT="

REM 1a) if conda is on PATH, ask it directly
for /f "delims=" %%i in ('conda info --base 2^>nul') do set "CONDA_ROOT=%%i"
if defined CONDA_ROOT goto conda_found

REM 1b) otherwise check common install folders
for %%P in (
  "%USERPROFILE%\anaconda3"
  "%USERPROFILE%\miniconda3"
  "%LOCALAPPDATA%\anaconda3"
  "%LOCALAPPDATA%\miniconda3"
  "C:\ProgramData\anaconda3"
  "C:\ProgramData\miniconda3"
  "C:\anaconda3"
  "C:\miniconda3"
  "D:\anaconda3"
  "D:\miniconda3"
) do (
  if exist "%%~P\Scripts\conda.exe" (
    set "CONDA_ROOT=%%~P"
    goto conda_found
  )
)

echo [!] Conda nahi mila. "Anaconda Prompt" me ja kar is folder me  run.bat  chalayen,
echo     ya mujhe batayen aapka Anaconda kis folder me installed hai.
pause
exit /b 1

:conda_found
echo [*] Conda: %CONDA_ROOT%

REM env do jagah ho sakta hai: conda folder ke andar, ya user profile me
REM (ProgramData wali "All Users" install me user envs yahan bante hain)
set "ENV_PY=%CONDA_ROOT%\envs\recall\python.exe"
if not exist "%ENV_PY%" set "ENV_PY=%USERPROFILE%\.conda\envs\recall\python.exe"

REM --- Step 2: create the env on first run (takes a few minutes) ---
if not exist "%ENV_PY%" (
  echo [*] Creating conda environment "recall" — ye pehli dafa 2-5 minute lega...
  call "%CONDA_ROOT%\Scripts\conda.exe" create -y -n recall python=3.11
  set "ENV_PY=%CONDA_ROOT%\envs\recall\python.exe"
)
if not exist "%ENV_PY%" set "ENV_PY=%USERPROFILE%\.conda\envs\recall\python.exe"
if not exist "%ENV_PY%" (
  echo [!] Environment nahi ban saka. Anaconda Prompt me ye chala kar dekhein:
  echo         conda create -n recall python=3.11
  echo     phir run.bat dobara chalayen.
  pause
  exit /b 1
)
echo [*] Python: %ENV_PY%

REM --- Step 3: install requirements if missing ---
"%ENV_PY%" -c "import flask, chromadb, pandas, dotenv, google.genai" >nul 2>nul
if errorlevel 1 (
  echo [*] Installing requirements — pehli dafa 2-3 minute...
  "%ENV_PY%" -m pip install -r requirements.txt
)

REM --- Step 4: .env on first run ---
if not exist ".env" (
  copy .env.example .env >nul
  echo.
  echo [!] .env ban gayi hai — is me GEMINI_API_KEY aur DASHBOARD_PASSWORD
  echo     dal kar save karein, phir run.bat dobara chalayen.
  echo     Free key: https://aistudio.google.com/apikey
  notepad .env
  exit /b 0
)

REM --- Step 5: UI files on first run ---
if not exist "static\vendor\tailwind.js" (
  echo [*] Downloading UI libraries — one time...
  "%ENV_PY%" get_vendor.py
)

REM --- Step 6: launch ---
echo [*] Starting Recall at http://127.0.0.1:5000 ...
start "" http://127.0.0.1:5000
"%ENV_PY%" app.py

echo.
echo [i] App band ho gayi. Window band karne ke liye koi key dabayen.
pause
