# Recall — personal knowledge base

Search everything you have already figured out — any kind of knowledge, in your own words.
Flask + Gemini embeddings + Chroma (local vector store). Your data lives
in `problems.csv`; embeddings are cached in `chroma_db/` so nothing is
re-embedded on restart.

## Easiest start (Windows)

Double-click **`run.bat`** — it creates the conda environment, installs
everything, downloads UI files, makes your `.env`, and opens the app.
First run stops so you can put your `GEMINI_API_KEY` in `.env`; run it again
and you're in. (Mac/Linux: `./run.sh`)

If it says conda is not available even though you have Anaconda installed,
that's because conda only lives inside "Anaconda Prompt" by default. The
script now searches common install folders automatically; if it still can't
find it, open Anaconda Prompt once and run `conda init cmd.exe`, then
double-click `run.bat` again.

## Manual setup (one time)

With conda (recommended):
```
conda create -n recall python=3.11 -y
conda activate recall
pip install -r requirements.txt
python get_vendor.py          (downloads UI libraries + fonts into static/vendor/)
copy .env.example .env        (Windows)   |   cp .env.example .env  (Mac/Linux)
```
Without conda: skip the first two lines. Next time, just
`conda activate recall` then `python app.py` — or keep using `run.bat`.

Your knowledge base lives in `problems.csv`. It isn't in the repo (it's your
own data); the app creates an empty one on first run. To start with a few demo
entries instead, copy the sample:
`copy problems.sample.csv problems.csv` (Windows) | `cp problems.sample.csv problems.csv` (Mac/Linux)

Open `.env` and fill in:
- `GEMINI_API_KEY` — free from https://aistudio.google.com/apikey
- `SECRET_KEY` — any long random string
- `DASHBOARD_PASSWORD` — password for the Logbook page

Optional (all have sensible defaults — see `.env.example`):
- `PAGE_SIZE` — entries per page on the Logbook list (default 25)
- `RELATED_RESULTS` — how many "related" cards under the best match (default 2, `0` = none)
- `PRIMARY_THRESHOLD` / `SECONDARY_THRESHOLD` — match cutoffs (default 0.65 / 0.55)

On startup the console prints `Gemini OK` if the key works, or a clear banner
telling you exactly what's wrong (bad key, quota, network) if it doesn't.

## Run

```
conda activate recall
python app.py
```
Or just double-click `run.bat`.

Open http://127.0.0.1:5000 — search is on the home page, adding/editing
entries is under **Logbook** (password protected).

First start embeds your existing entries once; after that, only new or
edited entries call the embedding API.

## Local UI files vs CDN

The interface loads Tailwind, GSAP, Lucide and fonts from `static/vendor/`
(downloaded by `get_vendor.py`), so it opens instantly and works offline.
Each template also has the CDN links right there in an HTML comment — to
switch, comment the local line and uncomment the CDN block. `static/vendor/`
is git-ignored; after cloning on a new machine, run `get_vendor.py` again.

## Backup (recommended)

```
git init
git add .
git commit -m "initial"
```

`.env`, `chroma_db/`, and **`problems.csv`** are git-ignored — never commit them.

Two different goals, don't mix them:
- **Sharing the code** (public repo): safe as-is — your key (`.env`) and your
  personal entries (`problems.csv`) stay out of the repo. Others run it with
  their own key and start with their own empty knowledge base.
- **Backing up *your* data**: use a **separate private** repo and un-ignore
  `problems.csv` there (or just copy the file somewhere safe). Never put your
  personal `problems.csv` in the public repo.

Before your first public push, run `git status` and confirm `.env` and
`problems.csv` are **not** listed.

## Tuning

Defaults are tuned against a real knowledge base (PRIMARY 0.65 / SECONDARY 0.55).
If good matches get missed, lower `PRIMARY_THRESHOLD` in `.env` (e.g. 0.60). If
wrong entries show as best match, raise it. `SECONDARY_THRESHOLD` controls the
"related" cutoff, `RELATED_RESULTS` how many related cards show, and `PAGE_SIZE`
the Logbook page length. Change any of them in `.env`, then restart the app.

Transient network hiccups (e.g. a dropped connection to Google) are retried
automatically; only a persistent failure surfaces an error.

## Auto-backup on every change (optional)

After `git init`, set `AUTO_GIT_COMMIT=true` in `.env` — every add, edit
and delete commits `problems.csv` automatically. You only need to
`git push` now and then.

## Keyboard shortcuts

- `/` — jump to search (home page)
- `Ctrl+Enter` — save the new entry (dashboard)
