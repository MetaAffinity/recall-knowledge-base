"""
Recall — personal knowledge base with semantic search.

Stack:  Flask + Gemini (embeddings & fallback LLM) + Chroma (local vector store)
Data:   problems.csv is the human-readable source of truth.
        chroma_db/ holds the embeddings (auto-synced from the CSV on startup).

Run:    pip install -r requirements.txt
        copy .env.example -> .env and fill in the values
        python app.py
"""

import os
import re
import subprocess
import threading
import time
import uuid
from datetime import date
from functools import wraps
from pathlib import Path

import chromadb
import pandas as pd
from dotenv import load_dotenv
from flask import (Flask, jsonify, redirect, render_template, request,
                   session, url_for)
from google import genai
from google.genai import types

# ---------------------------------------------------------------- config ----

BASE_DIR = Path(__file__).resolve().parent
load_dotenv(BASE_DIR / ".env")

CSV_FILE = BASE_DIR / "problems.csv"
CHROMA_DIR = BASE_DIR / "chroma_db"
CSV_COLUMNS = ["ID", "Problems", "Solutions", "Tags", "Date"]

EMBED_MODEL = os.getenv("EMBED_MODEL", "gemini-embedding-001")
EMBED_DIM = int(os.getenv("EMBED_DIM", "768"))
CHAT_MODEL = os.getenv("CHAT_MODEL", "gemini-2.5-flash")

# Similarity thresholds (cosine similarity, 0..1). Defaults tuned empirically
# against the real KB: true re-search matches score ~0.70-0.84, out-of-KB
# queries top out ~0.59, so 0.65 cleanly separates them. Lower PRIMARY if good
# matches are missed, raise it if wrong solutions show up as "best match".
PRIMARY_THRESHOLD = float(os.getenv("PRIMARY_THRESHOLD", "0.65"))
SECONDARY_THRESHOLD = float(os.getenv("SECONDARY_THRESHOLD", "0.55"))

# How many entries per page on the dashboard list. Set PAGE_SIZE in .env to
# whatever you like (e.g. 10, 50). Must be at least 1.
PAGE_SIZE = max(1, int(os.getenv("PAGE_SIZE", "25")))

# How many "related" result cards to show under the best match on search.
# Set RELATED_RESULTS in .env (0 = only the best match, no related cards).
RELATED_RESULTS = max(0, int(os.getenv("RELATED_RESULTS", "2")))

# How search results animate in: "classic" (stagger fade) or "branches"
# (an amber branch grows and cards appear one by one). Set in .env.
REVEAL_STYLE = os.getenv("REVEAL_STYLE", "classic").strip().lower()
if REVEAL_STYLE not in ("classic", "branches"):
    REVEAL_STYLE = "classic"

GEMINI_API_KEY = os.getenv("GEMINI_API_KEY")
DASHBOARD_PASSWORD = os.getenv("DASHBOARD_PASSWORD", "admin")
AUTO_GIT_COMMIT = os.getenv("AUTO_GIT_COMMIT", "false").lower() == "true"

app = Flask(__name__)
app.secret_key = os.getenv("SECRET_KEY", uuid.uuid4().hex)

if not GEMINI_API_KEY:
    raise SystemExit(
        "GEMINI_API_KEY is missing. Copy .env.example to .env and add your key "
        "(https://aistudio.google.com/apikey)."
    )

gemini = genai.Client(api_key=GEMINI_API_KEY)
_write_lock = threading.Lock()

# ------------------------------------------------------------- embeddings ----

# Signals of a *transient* network hiccup (dropped keep-alive connection,
# brief Wi-Fi blip, AV/firewall reset) — safe to retry. WinError 10054
# ("connection forcibly closed by remote host") is the classic one.
_TRANSIENT_SIGNS = ("10054", "forcibly closed", "connection reset",
                    "connection aborted", "remoteprotocol", "connection error",
                    "timed out", "timeout", "temporarily", "unavailable",
                    "eof occurred")

# Signals of no internet / DNS failure — can't even resolve the host.
# WinError 11001 / getaddrinfo = DNS lookup failed (offline).
_OFFLINE_SIGNS = ("getaddrinfo", "11001", "name resolution", "name or service",
                  "nodename", "failed to establish", "max retries",
                  "newconnectionerror", "temporary failure in name")

_TRANSIENT_SIGNS += _OFFLINE_SIGNS   # offline is retried too (in case it's brief)


def _is_offline(exc):
    return any(s in f"{type(exc).__name__} {exc}".lower() for s in _OFFLINE_SIGNS)


RETRY_ATTEMPTS = 5   # transient 10054 resets usually clear on a fresh connection


def _is_transient(exc):
    if isinstance(exc, (ConnectionError, TimeoutError)):
        return True
    return any(s in f"{type(exc).__name__} {exc}".lower() for s in _TRANSIENT_SIGNS)


def _with_retry(fn, attempts=RETRY_ATTEMPTS, base_delay=0.5):
    """Run a Gemini call, retrying transient network resets with capped backoff.
    Non-transient errors (bad key, quota, bad request) raise immediately."""
    for i in range(attempts):
        try:
            return fn()
        except Exception as e:
            if i == attempts - 1 or not _is_transient(e):
                raise
            time.sleep(min(2.0, base_delay * (2 ** i)))  # 0.5,1,2,2 (capped)


def gemini_error_detail(exc):
    """Turn a raw Gemini exception into a clear, actionable message that says
    WHAT went wrong (bad key, quota, model name, network, Google outage)
    instead of dumping a cryptic socket error."""
    # SDK APIError carries a real HTTP status code + Google status string.
    code = getattr(exc, "code", None)
    status = getattr(exc, "status", "") or ""
    msg = (getattr(exc, "message", None) or str(exc)).strip()

    if code in (401, 403) or "unauthenticated" in status.lower() \
            or "permission_denied" in status.lower() or "api key" in msg.lower():
        return (f"Gemini rejected your API key ({code or 'auth'} {status}). "
                "Check GEMINI_API_KEY in .env — it may be wrong, expired, or "
                "revoked. Get a fresh one at https://aistudio.google.com/apikey")
    if code == 429 or "resource_exhausted" in status.lower():
        return ("Gemini quota / rate limit reached (429). You've hit the "
                "free-tier limit for now — wait a minute and try again, or "
                "check your usage at https://aistudio.google.com")
    if code == 404:
        return (f"Gemini model not found (404): the model name is wrong. "
                "Check EMBED_MODEL / CHAT_MODEL in .env. "
                f"Details: {msg[:160]}")
    if code == 400:
        return (f"Gemini rejected the request (400 {status}) — usually a bad "
                "model name or parameter. "
                f"Details: {msg[:160]}")
    if code in (500, 503):
        return (f"Gemini's servers had a temporary problem ({code} {status}). "
                "This is on Google's side — wait a few seconds and retry.")
    if code:  # some other HTTP error we didn't special-case
        return f"Gemini API error ({code} {status}): {msg[:160]}"

    # No HTTP code => the call never got a real response = socket/network layer.
    # Offline / DNS failure gets its own plain message (most common real cause).
    if _is_offline(exc):
        return ("No internet connection — couldn't reach Google's servers "
                "(DNS lookup failed). Check your Wi-Fi / network and try again. "
                f"Raw: {type(exc).__name__}: {msg[:100]}")
    # NOTE: an INVALID API key can also surface here as a connection reset
    # (10054) rather than a clean 401, so we name that possibility too.
    if _is_transient(exc):
        return (f"Couldn't reach Gemini — the connection kept dropping after "
                f"{RETRY_ATTEMPTS} tries. Likely one of: (1) no or unstable "
                "internet; (2) a firewall / VPN / antivirus blocking "
                "generativelanguage.googleapis.com; or (3) an invalid or blocked "
                "API key (Google sometimes resets the connection for bad keys "
                f"instead of saying so). Raw: {type(exc).__name__}: {msg[:120]}")
    return f"Unexpected Gemini error — {type(exc).__name__}: {msg[:160]}"


def embed_texts(texts, task="RETRIEVAL_DOCUMENT"):
    """Embed a list of texts with Gemini. Returns list of vectors."""
    vectors = []
    for i in range(0, len(texts), 100):  # API accepts up to 100 per call
        batch = texts[i:i + 100]
        result = _with_retry(lambda: gemini.models.embed_content(
            model=EMBED_MODEL,
            contents=batch,
            config=types.EmbedContentConfig(
                task_type=task, output_dimensionality=EMBED_DIM
            ),
        ))
        vectors.extend([e.values for e in result.embeddings])
    return vectors


def embed_query(text):
    return embed_texts([text], task="RETRIEVAL_QUERY")[0]

# ------------------------------------------------------------------ data ----

def load_df():
    if not CSV_FILE.exists():
        pd.DataFrame(columns=CSV_COLUMNS).to_csv(CSV_FILE, index=False, encoding="utf-8")
    df = pd.read_csv(CSV_FILE, encoding="utf-8", dtype=str).fillna("")
    changed = False
    for col in CSV_COLUMNS:
        if col not in df.columns:
            df[col] = ""
            changed = True
    missing_id = df["ID"].eq("")
    if missing_id.any():
        df.loc[missing_id, "ID"] = [uuid.uuid4().hex[:12] for _ in range(missing_id.sum())]
        changed = True
    df = df[CSV_COLUMNS]
    if changed:
        save_df(df)
    return df


def save_df(df):
    df[CSV_COLUMNS].to_csv(CSV_FILE, index=False, encoding="utf-8")


def git_autocommit(message):
    """Optionally commit the CSV after each change. Never blocks the app —
    if git or the repo is missing, it just skips quietly."""
    if not AUTO_GIT_COMMIT:
        return
    try:
        subprocess.run(["git", "add", CSV_FILE.name], cwd=BASE_DIR,
                       capture_output=True, timeout=10)
        subprocess.run(["git", "commit", "-m", message, "--", CSV_FILE.name],
                       cwd=BASE_DIR, capture_output=True, timeout=10)
    except Exception:
        pass


chroma = chromadb.PersistentClient(path=str(CHROMA_DIR))
collection = chroma.get_or_create_collection(
    name="problems", metadata={"hnsw:space": "cosine"}
)


def meta_of(row):
    return {"problem": row["Problems"], "solution": row["Solutions"],
            "tags": row["Tags"], "date": row["Date"]}


def embed_text_of(problem, tags=""):
    """Text that gets embedded — tags help matching across wording."""
    return f"{problem}\nTags: {tags}" if tags else problem


def sync_collection():
    """Make Chroma match the CSV: embed new/changed rows, drop deleted ones.
    Only rows whose searched text (Problem+Tags) is new or changed cost an API
    call — everything else is reused from disk. This is also the safety net:
    any entry that was saved to the CSV but couldn't be embedded at the time
    (e.g. Gemini was down) gets embedded automatically here on the next start."""
    df = load_df()
    csv_ids = set(df["ID"])
    stored = collection.get()  # ids + documents + metadatas
    stored_ids = set(stored["ids"])
    # what's currently embedded, so we can spot rows whose text has changed
    embedded = {i: (doc, (meta or {}).get("tags", ""))
                for i, doc, meta in zip(stored["ids"], stored["documents"],
                                        stored["metadatas"])}

    stale = list(stored_ids - csv_ids)
    if stale:
        collection.delete(ids=stale)

    def needs_embed(r):
        if r["ID"] not in stored_ids:           # brand new / never embedded
            return True
        doc, tags = embedded.get(r["ID"], ("", ""))
        return r["Problems"] != doc or r["Tags"] != tags   # searched text changed

    todo = df[df.apply(needs_embed, axis=1)] if not df.empty else df
    if not todo.empty:
        print(f"Embedding {len(todo)} new/changed entr"
              f"{'y' if len(todo)==1 else 'ies'}...")
        vectors = embed_texts([embed_text_of(r["Problems"], r["Tags"])
                               for _, r in todo.iterrows()])
        collection.upsert(                      # upsert = add new + update existing
            ids=todo["ID"].tolist(),
            embeddings=vectors,
            documents=todo["Problems"].tolist(),
            metadatas=[meta_of(r) for _, r in todo.iterrows()],
        )
    print(f"Knowledge base ready — {len(csv_ids)} entries, "
          f"{len(todo)} newly embedded.")

# ------------------------------------------------------------------ auth ----

def require_auth(fn):
    @wraps(fn)
    def wrapper(*args, **kwargs):
        if not session.get("auth"):
            if request.path.startswith("/api/"):
                return jsonify({"error": "Not signed in."}), 401
            return redirect(url_for("login"))
        return fn(*args, **kwargs)
    return wrapper

# ----------------------------------------------------------------- pages ----

@app.route("/")
def index():
    return render_template("index.html", total=len(load_df()),
                           reveal_style=REVEAL_STYLE)


@app.route("/login", methods=["GET", "POST"])
def login():
    error = None
    if request.method == "POST":
        if request.form.get("password") == DASHBOARD_PASSWORD:
            session["auth"] = True
            return redirect(url_for("dashboard"))
        error = "That password didn't match. Try again."
    return render_template("login.html", error=error)


@app.route("/logout")
def logout():
    session.clear()
    return redirect(url_for("index"))


@app.route("/dashboard")
@require_auth
def dashboard():
    return render_template("dashboard.html")

# ------------------------------------------------------------- search api ----

def _words(text):
    return {w for w in re.split(r"\W+", text.lower()) if len(w) > 1}


def keyword_search(query, limit):
    """Offline fallback: whole-word matching over the CSV when the embedding
    service can't be reached. Not semantic, but it still finds entries by the
    words they contain so search is never fully dead without internet.
    Whole-word (not substring) so "term" doesn't match "terminal"."""
    words = _words(query)
    if not words:
        return []
    df = load_df()
    hits = []
    for _, r in df.iterrows():
        strong_words = _words(f"{r['Problems']} {r['Tags']}")  # weighted higher
        all_words = strong_words | _words(r["Solutions"])
        matched = words & all_words
        if not matched:
            continue
        strong = words & strong_words
        score = round((len(matched) + len(strong)) / (2 * len(words)), 4)  # 0..1
        hits.append({"id": r["ID"], "score": score, "problem": r["Problems"],
                     "solution": r["Solutions"], "tags": r["Tags"], "date": r["Date"],
                     "_rank": (len(matched), len(strong))})
    hits.sort(key=lambda h: h["_rank"], reverse=True)
    for h in hits:
        h.pop("_rank")
    return hits[:limit]


@app.route("/api/search", methods=["POST"])
def api_search():
    query = (request.get_json(silent=True) or {}).get("query", "").strip()
    if not query:
        return jsonify({"error": "Type a problem to search for."}), 400
    if collection.count() == 0:
        return jsonify({"found": False, "related": [], "empty_kb": True})

    want = RELATED_RESULTS + 1
    try:
        qv = embed_query(query)
    except Exception as e:
        # No internet / Gemini down -> fall back to offline keyword search so
        # search still works. Flag the mode so the UI can show an indicator.
        hits = keyword_search(query, want)
        return jsonify({
            "found": len(hits) > 0, "best": hits[0] if hits else None,
            "related": hits[1:], "offline": True,
            "notice": gemini_error_detail(e),
        })

    # fetch enough candidates: 1 for the best match + however many related cards
    n = min(want, collection.count())
    res = collection.query(query_embeddings=[qv], n_results=n,
                           include=["metadatas", "distances"])
    hits = []
    for _id, meta, dist in zip(res["ids"][0], res["metadatas"][0], res["distances"][0]):
        hits.append({"id": _id, "score": round(1 - dist, 4), **meta})

    best = hits[0] if hits and hits[0]["score"] >= PRIMARY_THRESHOLD else None
    # only surface "related" alongside a real best match — otherwise generic
    # nearest-neighbours (noise) would show for unrelated queries like "hello".
    related = ([h for h in hits if h is not best
                and h["score"] >= SECONDARY_THRESHOLD][:RELATED_RESULTS]
               if best else [])

    return jsonify({"found": best is not None, "best": best,
                    "related": related, "offline": False})


@app.route("/api/llm", methods=["POST"])
def api_llm():
    """Fallback answer from Gemini — only called when the user asks for it."""
    query = (request.get_json(silent=True) or {}).get("query", "").strip()
    if not query:
        return jsonify({"error": "No question given."}), 400
    prompt = (
        "You are a concise technical assistant. Solve the user's problem in "
        "under 150 words. Short sentences, no repetition, plain text only.\n\n"
        f"Problem: {query}"
    )
    try:
        out = _with_retry(lambda: gemini.models.generate_content(
            model=CHAT_MODEL, contents=prompt))
        return jsonify({"answer": out.text.strip()})
    except Exception as e:
        return jsonify({"error": f"AI answer failed. {gemini_error_detail(e)}"}), 502


@app.route("/api/explain", methods=["POST"])
def api_explain():
    """Expand a stored solution for the current query — grounded, no invention."""
    data = request.get_json(silent=True) or {}
    query, entry_id = data.get("query", "").strip(), data.get("id", "")
    row = load_df().loc[lambda d: d["ID"] == entry_id]
    if row.empty:
        return jsonify({"error": "Entry not found."}), 404
    row = row.iloc[0]
    prompt = (
        "The user solved this problem before and saved a note. Explain the saved "
        "solution for their current question in under 120 words. Stay strictly "
        "within the saved note — do not add outside steps.\n\n"
        f"Current question: {query}\n"
        f"Saved problem: {row['Problems']}\n"
        f"Saved solution: {row['Solutions']}"
    )
    try:
        out = _with_retry(lambda: gemini.models.generate_content(
            model=CHAT_MODEL, contents=prompt))
        return jsonify({"answer": out.text.strip()})
    except Exception as e:
        return jsonify({"error": f"Explain failed. {gemini_error_detail(e)}"}), 502


@app.route("/api/chat", methods=["POST"])
def api_chat():
    """Multi-turn chat. If an entry id is given, the chat is grounded in that
    saved note; otherwise it's a plain assistant continuing the conversation.
    History is stateless — the client sends the full message list each turn."""
    data = request.get_json(silent=True) or {}
    messages = data.get("messages", [])
    if not messages:
        return jsonify({"error": "No message given."}), 400

    entry_id = data.get("id", "")
    if entry_id:  # grounded in the user's saved note (fetched fresh, not trusted from client)
        row = load_df().loc[lambda d: d["ID"] == entry_id]
        if row.empty:
            return jsonify({"error": "Entry not found."}), 404
        row = row.iloc[0]
        system = (
            "You are a helpful technical assistant. The user saved this note "
            "earlier and is asking follow-up questions about it:\n\n"
            f"Saved problem: {row['Problems']}\n"
            f"Saved solution: {row['Solutions']}\n\n"
            "Use the note as context, but ALWAYS give a genuinely useful, direct "
            "answer. If the note covers it, answer from the note. If it doesn't, "
            "use your own knowledge to actually help (you may briefly note when "
            "you go beyond the saved note). Never just reply that the note "
            "'doesn't cover it' without also answering. Be concise, practical, "
            "plain text."
        )
    else:  # continuing an AI answer — no saved note to ground in
        system = ("You are a concise technical assistant continuing a chat. "
                  "Answer clearly in plain text, short sentences, no repetition.")

    # last 20 turns is plenty of context and keeps the payload small
    contents = [{"role": "model" if m.get("role") == "assistant" else "user",
                 "parts": [{"text": (m.get("text") or "").strip()}]}
                for m in messages[-20:] if (m.get("text") or "").strip()]
    if not contents:
        return jsonify({"error": "No message given."}), 400
    try:
        out = _with_retry(lambda: gemini.models.generate_content(
            model=CHAT_MODEL, contents=contents,
            config=types.GenerateContentConfig(system_instruction=system)))
        return jsonify({"answer": out.text.strip()})
    except Exception as e:
        return jsonify({"error": f"Chat failed. {gemini_error_detail(e)}"}), 502

# ------------------------------------------------------------ entries api ----

@app.route("/api/entries", methods=["GET"])
@require_auth
def list_entries():
    """Return one page of entries (newest first). ?page=1&limit=25&q=text
    The optional q filters across ALL entries (Problem/Solution/Tags) before
    paginating, so the dashboard filter searches the whole logbook, not one page."""
    full = load_df().iloc[::-1]  # newest first

    # pending count is always over the WHOLE logbook, regardless of the filter
    stored_ids = set(collection.get(include=[])["ids"])
    pending = int((~full["ID"].isin(stored_ids)).sum())

    q = request.args.get("q", "").strip().lower()
    df = full
    if q:
        cols = full["Problems"].str.lower() + "\n" + full["Solutions"].str.lower() \
               + "\n" + full["Tags"].str.lower()
        df = full[cols.str.contains(q, na=False, regex=False)]

    total = len(df)
    try:
        page = max(1, int(request.args.get("page", 1)))
        # default page size comes from PAGE_SIZE (.env); ?limit= can override,
        # capped at 1000 so a stray URL can't try to render everything at once.
        limit = min(1000, max(1, int(request.args.get("limit", PAGE_SIZE))))
    except (TypeError, ValueError):
        page, limit = 1, PAGE_SIZE
    pages = max(1, (total + limit - 1) // limit)
    page = min(page, pages)                       # clamp to last real page
    start = (page - 1) * limit
    page_df = df.iloc[start:start + limit]

    rows = page_df.to_dict(orient="records")
    for r in rows:
        r["indexed"] = r["ID"] in stored_ids
    return jsonify({
        "entries": rows, "total": total, "page": page, "pages": pages,
        "limit": limit, "pending": pending,
    })


@app.route("/api/reindex", methods=["POST"])
@require_auth
def reindex():
    """Retry embedding any saved-but-not-indexed (or changed) entries now,
    without restarting the app. Used by the dashboard's 'Re-index' button."""
    with _write_lock:
        try:
            sync_collection()
        except Exception as e:
            return jsonify({"error": f"Re-index failed. {gemini_error_detail(e)}"}), 502
        stored_ids = set(collection.get(include=[])["ids"])
        pending = int((~load_df()["ID"].isin(stored_ids)).sum())
    if pending:
        return jsonify({"message": f"Re-indexed, but {pending} still pending "
                        "(Gemini still unreachable).", "pending": pending})
    return jsonify({"message": "All entries indexed and searchable.", "pending": 0})


@app.route("/api/entries", methods=["POST"])
@require_auth
def add_entry():
    data = request.get_json(silent=True) or {}
    problem = data.get("problem", "").strip()
    solution = data.get("solution", "").strip()
    tags = data.get("tags", "").strip()
    entry_date = data.get("date", "").strip() or date.today().isoformat()
    if not problem or not solution:
        return jsonify({"error": "Both problem and solution are required."}), 400

    with _write_lock:
        row = {"ID": uuid.uuid4().hex[:12], "Problems": problem,
               "Solutions": solution, "Tags": tags, "Date": entry_date}
        # 1) Save to the CSV (source of truth) FIRST so the knowledge is never
        #    lost — even if the embedding service is down right now.
        df = pd.concat([load_df(), pd.DataFrame([row])], ignore_index=True)
        save_df(df)
        git_autocommit(f"add entry: {problem[:60]}")
        # 2) Then index it for search. If embedding fails, the entry still lives
        #    in the CSV and sync_collection() will embed it automatically on the
        #    next startup (it's a new ID Chroma hasn't seen yet).
        try:
            vec = embed_texts([embed_text_of(problem, tags)])[0]
            collection.add(ids=[row["ID"]], embeddings=[vec],
                           documents=[problem], metadatas=[meta_of(row)])
        except Exception:
            return jsonify({
                "message": "Saved to your logbook.",
                "warning": "Saved — but couldn't index it for search right now "
                           "(Gemini unreachable). It'll become searchable "
                           "automatically after the next app restart.",
                "entry": row,
            })
    return jsonify({"message": "Saved to your logbook.", "entry": row})


@app.route("/api/entries/<entry_id>", methods=["PUT"])
@require_auth
def edit_entry(entry_id):
    data = request.get_json(silent=True) or {}
    problem = data.get("problem", "").strip()
    solution = data.get("solution", "").strip()
    tags = data.get("tags", "").strip()
    entry_date = data.get("date", "").strip()
    if not problem or not solution:
        return jsonify({"error": "Both problem and solution are required."}), 400

    with _write_lock:
        df = load_df()
        mask = df["ID"] == entry_id
        if not mask.any():
            return jsonify({"error": "Entry not found."}), 404
        old_problem = df.loc[mask, "Problems"].iloc[0]
        old_tags = df.loc[mask, "Tags"].iloc[0]
        df.loc[mask, ["Problems", "Solutions", "Tags", "Date"]] = [
            problem, solution, tags, entry_date or df.loc[mask, "Date"].iloc[0]]
        save_df(df)

        row = df.loc[mask].iloc[0]
        update = {"ids": [entry_id], "documents": [problem],
                  "metadatas": [meta_of(row)]}
        warning = None
        if problem != old_problem or tags != old_tags:  # searched text changed
            try:
                update["embeddings"] = [embed_texts([embed_text_of(problem, tags)])[0]]
            except Exception:
                # keep the old embedding for now; still refresh the visible
                # text/metadata below. sync_collection() re-embeds on next start.
                warning = ("Entry updated — but its search index couldn't be "
                           "refreshed right now (Gemini unreachable). It'll "
                           "re-index automatically after the next app restart.")
        collection.update(**update)  # always fix the shown text/metadata
        git_autocommit(f"edit entry: {problem[:60]}")
    resp = {"message": "Entry updated."}
    if warning:
        resp["warning"] = warning
    return jsonify(resp)


@app.route("/api/entries/<entry_id>", methods=["DELETE"])
@require_auth
def delete_entry(entry_id):
    with _write_lock:
        df = load_df()
        mask = df["ID"] == entry_id
        if not mask.any():
            return jsonify({"error": "Entry not found."}), 404
        save_df(df[~mask])
        collection.delete(ids=[entry_id])
        git_autocommit("delete entry")
    return jsonify({"message": "Entry deleted."})

# ------------------------------------------------------------------- main ----

_tailwind = BASE_DIR / "static" / "vendor" / "tailwind.js"
if not _tailwind.exists():
    print("NOTE: local UI files not found — run `python get_vendor.py` once "
          "(needs internet), or switch the templates to the CDN option. "
          "The app will still run, but the interface will look unstyled.")

def check_gemini():
    """Ping Gemini once at startup so key/network problems show up clearly in
    the console immediately, instead of only on the first search. Never blocks
    startup — the app still runs, but you know what's wrong up front."""
    try:
        embed_query("startup connectivity check")
        print("Gemini OK — API key valid and reachable.")
    except Exception as e:
        print("\n" + "!" * 68)
        print("GEMINI NOT WORKING — searches and AI answers will fail until fixed:")
        print("  " + gemini_error_detail(e))
        print("!" * 68 + "\n")


with app.app_context():
    sync_collection()
    check_gemini()

if __name__ == "__main__":
    app.run(host="127.0.0.1", port=5000, debug=False)
