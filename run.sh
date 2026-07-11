#!/usr/bin/env bash
# Recall — one-click launcher (Mac/Linux). See run.bat for the Windows version.
set -e
cd "$(dirname "$0")"

if command -v conda >/dev/null 2>&1; then
  if ! conda env list | grep -q "^recall "; then
    echo "[*] Creating conda environment 'recall' (one time)..."
    conda create -y -n recall python=3.11
    eval "$(conda shell.bash hook)"
    conda activate recall
    pip install -r requirements.txt
  else
    eval "$(conda shell.bash hook)"
    conda activate recall
  fi
else
  echo "[!] Conda not found — using system python."
  pip install -r requirements.txt
fi

if [ ! -f .env ]; then
  cp .env.example .env
  echo "[!] .env created — add your GEMINI_API_KEY and DASHBOARD_PASSWORD, then run again."
  exit 0
fi

[ -f static/vendor/tailwind.js ] || python get_vendor.py

( sleep 2 && (xdg-open http://127.0.0.1:5000 || open http://127.0.0.1:5000) >/dev/null 2>&1 ) &
python app.py
