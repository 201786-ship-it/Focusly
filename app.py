"""
FOCUSLY - Earn Your Screen Time. Build Your Future.

A single-file Streamlit app for the SEVA FIRST Innovation Challenge 2026
(School & Vocational). AI study planning with Sarvam AI, AI homework
verification, earned screen time, and focus insights.

Run locally:      streamlit run app.py
Deploy publicly:  push this file to GitHub, then streamlit.io/cloud -> New app

Everything works without an API key (a rule-based planner takes over), so a
public demo never breaks in front of judges.
"""

import hashlib
import hmac
import json
import os
import re
import sqlite3
import time
from datetime import date, datetime, timedelta

import pandas as pd
import streamlit as st

# ============================================================
# PAGE + STYLE
# ============================================================

st.set_page_config(page_title="FOCUSLY", page_icon="⏳", layout="wide")

st.markdown(
    """
    <style>
    .block-container {
        padding-top: 1.2rem;
        padding-bottom: 2rem;
        padding-left: clamp(0.75rem, 3vw, 2.5rem);
        padding-right: clamp(0.75rem, 3vw, 2.5rem);
        max-width: 1200px;
    }
    div[data-testid="stMetric"] {
        background: #ffffff;
        border: 1px solid #e3e8e8;
        border-radius: 14px;
        padding: 14px 16px;
    }
    div[data-testid="stMetricLabel"] p {
        font-size: 0.82rem;
        color: #4b5563;
    }
    .focusly-hero {
        background: linear-gradient(135deg, #0f766e 0%, #115e59 100%);
        color: #ffffff;
        padding: 22px 26px;
        border-radius: 18px;
        margin-bottom: 18px;
    }
    .focusly-hero h1 {
        font-size: 1.6rem;
        margin: 0 0 4px 0;
    }
    .focusly-hero p {
        margin: 0;
        opacity: 0.9;
        font-size: 0.92rem;
    }
    .focusly-card {
        background: #ffffff;
        border: 1px solid #e3e8e8;
        border-radius: 14px;
        padding: 16px 18px;
        margin-bottom: 12px;
    }
    .focusly-note {
        font-size: 0.82rem;
        color: #4b5563;
    }
    </style>
    """,
    unsafe_allow_html=True,
)

DB_NAME = "focusly.db"
MODEL = "sarvam-105b"
BLOCK_MINUTES = 25
BREAK_MINUTES = 5
DAY_START = 16 * 60
PASS_MARK = 0.5
BASE_MINUTES = 10
SCORE_MINUTES = 20
STREAK_MINUTES = 2
LEVEL_STEP = 100

# ============================================================
# SARVAM AI CLIENT (optional - the app works without it)
# ============================================================


def get_api_key():
    key = None
    try:
        key = st.secrets.get("SARVAM_API_KEY")
    except Exception:
        key = None
    return key or os.getenv("SARVAM_API_KEY")


API_KEY = get_api_key()
CLIENT = None
AI_ERROR = None

if API_KEY:
    try:
        from sarvamai import SarvamAI

        CLIENT = SarvamAI(api_subscription_key=API_KEY)
    except Exception as exc:  # noqa: BLE001 - we degrade, never crash
        AI_ERROR = f"{type(exc).__name__}: {exc}"


def ai_ready():
    return CLIENT is not None


def ask_text(prompt, max_tokens=3000):
    """Ask Sarvam AI for plain text. Raises on failure so callers can fall back."""
    if not ai_ready():
        raise RuntimeError("Sarvam AI is not configured")
    response = CLIENT.chat.completions(
        model=MODEL,
        messages=[{"role": "user", "content": prompt}],
        max_tokens=max_tokens,
    )
    text = response.choices[0].message.content or ""
    return re.sub(r"<think>.*?</think>", "", text, flags=re.DOTALL).strip()


def extract_json(text):
    text = text.replace("```json", "").replace("```", "")
    start, end = text.find("{"), text.rfind("}")
    if start == -1 or end == -1:
        raise ValueError("No JSON in the AI reply: " + repr(text[:200]))
    return json.loads(text[start : end + 1])


def ask_json(prompt, tries=2):
    last = None
    for attempt in range(tries):
        try:
            return extract_json(ask_text(prompt))
        except Exception as exc:  # noqa: BLE001
            last = exc
            if attempt < tries - 1:
                time.sleep(1.2)
    raise last


# ============================================================
# DATABASE
# ============================================================


def get_connection():
    conn = sqlite3.connect(DB_NAME, check_same_thread=False, timeout=30)
    conn.row_factory = sqlite3.Row
    return conn


def init_database():
    conn = get_connection()
    conn.executescript(
        """
        CREATE TABLE IF NOT EXISTS users (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            name TEXT NOT NULL,
            email TEXT UNIQUE NOT NULL,
            password TEXT NOT NULL,
            role TEXT NOT NULL DEFAULT 'student',
            created_at TEXT
        );
        CREATE TABLE IF NOT EXISTS submissions (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            user_id INTEGER NOT NULL,
            subject TEXT,
            question TEXT,
            answer TEXT,
            score REAL,
            max_score REAL,
            feedback TEXT,
            needs_review INTEGER DEFAULT 0,
            photo_note TEXT,
            created_at TEXT
        );
        CREATE TABLE IF NOT EXISTS rewards (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            user_id INTEGER NOT NULL,
            submission_id INTEGER,
            minutes INTEGER NOT NULL,
            reason TEXT,
            created_at TEXT
        );
        CREATE TABLE IF NOT EXISTS usage_records (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            user_id INTEGER NOT NULL,
            day TEXT NOT NULL,
            app_name TEXT NOT NULL,
            minutes INTEGER NOT NULL
        );
        CREATE TABLE IF NOT EXISTS plans (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            user_id INTEGER NOT NULL,
            plan_json TEXT,
            source TEXT,
            created_at TEXT
        );
        """
    )
    conn.commit()
    conn.close()


init_database()


def now_str():
    return datetime.now().strftime("%Y-%m-%d %H:%M:%S")


# ============================================================
# AUTH
# ============================================================


def hash_password(password, salt=None):
    salt = salt or os.urandom(16).hex()
    digest = hashlib.pbkdf2_hmac("sha256", password.encode(), salt.encode(), 120_000).hex()
    return f"{salt}${digest}"


def verify_password(password, stored):
    try:
        salt, _ = stored.split("$")
    except ValueError:
        return False
    return hmac.compare_digest(hash_password(password, salt), stored)


def create_user(name, email, password="", role="student"):
    """Password is optional: with none, the account logs in on email alone."""
    conn = get_connection()
    try:
        conn.execute(
            "INSERT INTO users (name, email, password, role, created_at) VALUES (?, ?, ?, ?, ?)",
            (name.strip(), email.strip().lower(),
             hash_password(password) if password else "", role, now_str()),
        )
        conn.commit()
        return True, "Account created."
    except sqlite3.IntegrityError:
        return False, "That email is already registered. Try logging in."
    finally:
        conn.close()


def email_exists(email):
    conn = get_connection()
    row = conn.execute(
        "SELECT 1 FROM users WHERE email = ?", (email.strip().lower(),)
    ).fetchone()
    conn.close()
    return row is not None


def find_user(email, password=""):
    """Accounts created without a password log in with the email only."""
    conn = get_connection()
    row = conn.execute(
        "SELECT * FROM users WHERE email = ?", (email.strip().lower(),)
    ).fetchone()
    conn.close()
    if row is None:
        return None
    stored = row["password"] or ""
    if stored == "":
        return dict(row)
    if verify_password(password, row["password"]):
        return dict(row)
    return None


SAMPLE_USAGE = {
    "Instagram": [95, 120, 75, 140, 110, 88, 130],
    "YouTube": [45, 60, 30, 55, 70, 40, 65],
    "Google Classroom": [25, 30, 20, 35, 28, 22, 30],
    "WhatsApp": [35, 40, 50, 30, 45, 55, 38],
    "Khan Academy": [20, 15, 25, 10, 18, 12, 22],
}


def load_sample_usage(user_id):
    """Fill a week of example screen time for this user, so the charts have data."""
    conn = get_connection()
    for app_name, minutes_by_day in SAMPLE_USAGE.items():
        for offset, minutes in enumerate(minutes_by_day):
            day = (date.today() - timedelta(days=6 - offset)).isoformat()
            conn.execute(
                "DELETE FROM usage_records WHERE user_id = ? AND day = ? AND app_name = ?",
                (user_id, day, app_name),
            )
            conn.execute(
                "INSERT INTO usage_records (user_id, day, app_name, minutes) VALUES (?, ?, ?, ?)",
                (user_id, day, app_name, minutes),
            )
    conn.commit()
    conn.close()


# ============================================================
# STUDY COACH
# ============================================================

PLAN_SYSTEM = (
    "You are a study planner for Indian school students. Reply with STRICT JSON only: "
    '{"days":[{"day":1,"date":"YYYY-MM-DD","focus":"short theme","blocks":[{"subject":"",'
    '"minutes":25,"start":"16:00","task":""}]}],"tips":[""]}. '
    "Use 25-minute blocks with 5-minute breaks. Never exceed the student's daily hours. "
    "Put the subject with the nearest deadline first."
)


def allocate_blocks(weights, slots):
    """Largest-remainder allocation so no subject is starved out."""
    count = len(weights)
    if count == 0 or slots <= 0:
        return [0] * count
    if slots <= count:
        alloc = [0] * count
        for index in sorted(range(count), key=lambda i: -weights[i])[:slots]:
            alloc[index] = 1
        return alloc
    total = sum(weights) or 1.0
    exact = [slots * w / total for w in weights]
    alloc = [max(1, int(value)) for value in exact]
    while sum(alloc) > slots:
        candidates = [i for i in range(count) if alloc[i] > 1]
        if not candidates:
            break
        alloc[max(candidates, key=lambda i: (alloc[i], weights[i]))] -= 1
    while sum(alloc) < slots:
        alloc[max(range(count), key=lambda i: exact[i] - alloc[i])] += 1
    return alloc


def rule_plan(subjects, days, hours_per_day):
    daily_minutes = max(int(hours_per_day * 60), BLOCK_MINUTES)
    slots = max(daily_minutes // BLOCK_MINUTES, 1)
    weights = []
    for subject in subjects:
        days_left = max(int(subject.get("days_left", days)), 1)
        weights.append(float(subject.get("difficulty", 2)) / days_left)
    allocation = allocate_blocks(weights, slots)
    order = sorted(range(len(subjects)), key=lambda i: -weights[i])

    out_days = []
    for day_index in range(days):
        rotated = order[day_index % len(order):] + order[: day_index % len(order)]
        clock = DAY_START
        blocks = []
        for index in rotated:
            for _ in range(allocation[index]):
                blocks.append(
                    {
                        "subject": subjects[index]["name"],
                        "minutes": BLOCK_MINUTES,
                        "start": f"{clock // 60:02d}:{clock % 60:02d}",
                        "task": task_for(subjects[index], day_index, len(blocks)),
                    }
                )
                clock += BLOCK_MINUTES + BREAK_MINUTES
        out_days.append(
            {
                "day": day_index + 1,
                "date": (date.today() + timedelta(days=day_index)).isoformat(),
                "focus": focus_for(subjects, day_index),
                "blocks": blocks,
            }
        )
    return {
        "days": out_days,
        "tips": [
            "Keep the phone in another room during each 25-minute block.",
            "Finish the nearest-deadline subject first - it is scheduled first on purpose.",
            "Take the 5-minute break; do not stretch a block past 25 minutes.",
        ],
        "source": "rules",
    }


def task_for(subject, day_index, block_index):
    name = subject["name"]
    days_left = int(subject.get("days_left", 7))
    if day_index == 0 and block_index == 0:
        return f"List every pending {name} task and tick off what is already done"
    if days_left <= day_index + 1:
        return f"Final revision of {name} - the deadline is close"
    return f"{name}: one focused block of practice or reading"


def focus_for(subjects, day_index):
    ordered = sorted(subjects, key=lambda s: (int(s.get("days_left", 99)), -float(s.get("difficulty", 2))))
    if day_index == 0:
        return f"Clear the nearest deadline: {ordered[0]['name']}"
    if day_index == len(ordered):
        return "Revision and catch-up day"
    return f"{ordered[min(day_index, len(ordered) - 1)]['name']} in focus"


def build_plan(subjects, days, hours_per_day):
    """AI plan when Sarvam AI is configured; rule-based plan otherwise."""
    if ai_ready() and subjects:
        prompt = (
            f"{PLAN_SYSTEM}\n\nPlan the next {days} days. The student has "
            f"{hours_per_day} hours free each day. Today is {date.today().isoformat()}. "
            f"Subjects (name, days_left, difficulty 1-3): {subjects}"
        )
        try:
            data = ask_json(prompt)
            if isinstance(data.get("days"), list) and data["days"]:
                data["source"] = "sarvam"
                return data
        except Exception:  # noqa: BLE001 - fall through to the rule-based plan
            pass
    return rule_plan(subjects, days, hours_per_day)


# ============================================================
# HOMEWORK VERIFICATION
# ============================================================

OPERATORS = {
    "+": lambda a, b: a + b,
    "-": lambda a, b: a - b,
    "*": lambda a, b: a * b,
    "x": lambda a, b: a * b,
    "×": lambda a, b: a * b,
    "/": lambda a, b: a / b,
    "÷": lambda a, b: a / b,
}
PRECEDENCE = {"+": 1, "-": 1, "*": 2, "/": 2}

STOPWORDS = {
    "the", "and", "for", "with", "that", "this", "from", "what", "which", "who",
    "why", "how", "are", "was", "were", "you", "your", "have", "has", "does",
    "did", "not", "but", "all", "any", "can", "will", "would", "should", "each",
    "into", "than", "then", "them", "they", "there", "here", "when", "where",
    "explain", "write", "solve", "find", "give", "answer", "question", "state",
}

ARITH = re.compile(r"(\d+(?:\.\d+)?(?:\s*[+\-*/x×÷]\s*\d+(?:\.\d+)?)+)\s*=\s*(-?\d+(?:\.\d+)?)")
TOKEN = re.compile(r"\d+(?:\.\d+)?|[+\-*/x×÷]")


def keywords(text):
    words = re.findall(r"[a-zA-Z0-9]+", (text or "").lower())
    return {w for w in words if (w.isdigit() or (len(w) > 2 and w not in STOPWORDS))}


def evaluate_expression(expression):
    """Evaluate a written sum without eval(): tokenise, then apply precedence."""
    text = expression.replace("×", "*").replace("x", "*").replace("÷", "/")
    values, operators = [], []

    def apply_top():
        operator = operators.pop()
        right, left = values.pop(), values.pop()
        values.append(OPERATORS[operator](left, right))

    for token in TOKEN.findall(text):
        if token in PRECEDENCE:
            while operators and PRECEDENCE[operators[-1]] >= PRECEDENCE[token]:
                apply_top()
            operators.append(token)
        else:
            values.append(float(token))
    while operators:
        apply_top()
    return values[0] if len(values) == 1 else None


def check_arithmetic(answer):
    results = []
    for match in ARITH.finditer(answer or ""):
        try:
            expected = evaluate_expression(match.group(1))
            given = float(match.group(2))
        except (KeyError, ZeroDivisionError, ValueError):
            continue
        if expected is None:
            continue
        results.append(abs(expected - given) < 1e-6)
    return results


def verify_answer(question, answer, max_score=10.0):
    answer = (answer or "").strip()
    if not answer:
        return {
            "score": 0.0, "max_score": max_score, "ratio": 0.0, "confidence": 0.9,
            "needs_review": True,
            "feedback": ["No readable answer was submitted, so nothing could be verified."],
        }

    q_words, a_words = keywords(question), keywords(answer)
    coverage = len(q_words & a_words) / len(q_words) if q_words else 1.0
    coverage = min(coverage * 1.6, 1.0)

    word_count = len(answer.split())
    effort = min(word_count / 40.0, 1.0)

    sums = check_arithmetic(answer)
    sums_ratio = sum(sums) / len(sums) if sums else None

    if sums_ratio is None:
        ratio = 0.65 * coverage + 0.35 * effort
        confidence = 0.55
    else:
        ratio = 0.45 * coverage + 0.15 * effort + 0.40 * sums_ratio
        confidence = 0.8

    feedback = []
    if coverage < 0.35:
        feedback.append("The answer does not seem to address the question directly.")
    if word_count < 15:
        feedback.append("Very short answer - add the working or the reasoning.")
    if sums is not None:
        wrong = sums.count(False)
        if wrong == 0:
            feedback.append(f"All {len(sums)} written calculations are correct.")
        else:
            feedback.append(f"{wrong} of {len(sums)} written calculations look wrong - recheck them.")
    if not feedback:
        feedback.append("Answer looks complete and on-topic.")

    ratio = max(0.0, min(ratio, 1.0))
    needs_review = (0.4 <= ratio <= 0.6) or (confidence < 0.6 and ratio < 0.85)
    if needs_review:
        feedback.append("Flagged for teacher review.")

    return {
        "score": round(ratio * max_score, 1),
        "max_score": max_score,
        "ratio": round(ratio, 3),
        "confidence": confidence,
        "needs_review": needs_review,
        "feedback": feedback,
    }


def check_photo(uploaded_file):
    """Quality check on a homework photo (brightness, blur, size)."""
    from PIL import Image, ImageFilter, ImageStat

    try:
        image = Image.open(uploaded_file)
    except Exception:  # noqa: BLE001
        return "That file could not be opened as an image.", "poor"

    grey = image.convert("L")
    mean = ImageStat.Stat(grey).mean[0]
    sharpness = ImageStat.Stat(grey.filter(ImageFilter.FIND_EDGES)).stddev[0]
    width, height = grey.size

    problems = []
    if mean < 45:
        problems.append("too dark")
    if mean > 225:
        problems.append("overexposed")
    if sharpness < 12:
        problems.append("blurry")
    if width < 480 or height < 480:
        problems.append("low resolution")

    if problems:
        return "Photo is " + ", ".join(problems) + " - retake it for a better check.", "poor"
    return "Photo looks clear enough to read.", "good"


# ============================================================
# REWARDS
# ============================================================


def balance_minutes(user_id):
    conn = get_connection()
    total = conn.execute(
        "SELECT COALESCE(SUM(minutes), 0) AS t FROM rewards WHERE user_id = ?", (user_id,)
    ).fetchone()["t"]
    conn.close()
    return int(total)


def lifetime_minutes(user_id):
    conn = get_connection()
    total = conn.execute(
        "SELECT COALESCE(SUM(minutes), 0) AS t FROM rewards WHERE user_id = ? AND minutes > 0",
        (user_id,),
    ).fetchone()["t"]
    conn.close()
    return int(total)


def streak_days(user_id):
    conn = get_connection()
    rows = conn.execute(
        "SELECT DISTINCT substr(created_at, 1, 10) AS d FROM submissions WHERE user_id = ? "
        "ORDER BY d DESC",
        (user_id,),
    ).fetchall()
    conn.close()
    days = [datetime.strptime(r["d"], "%Y-%m-%d").date() for r in rows if r["d"]]
    if not days:
        return 0
    today = date.today()
    if days[0] not in (today, today - timedelta(days=1)):
        return 0
    streak, expected = 1, days[0] - timedelta(days=1)
    for day in days[1:]:
        if day == expected:
            streak += 1
            expected -= timedelta(days=1)
        else:
            break
    return streak


def award_minutes(user_id, submission_id, subject, ratio):
    streak = streak_days(user_id)
    minutes = BASE_MINUTES + round(ratio * SCORE_MINUTES) + min(streak, 7) * STREAK_MINUTES
    conn = get_connection()
    conn.execute(
        "INSERT INTO rewards (user_id, submission_id, minutes, reason, created_at) "
        "VALUES (?, ?, ?, ?, ?)",
        (user_id, submission_id, minutes,
         f"{subject} homework verified ({ratio:.0%}) + {streak}-day streak", now_str()),
    )
    conn.commit()
    conn.close()
    return minutes


def spend_minutes(user_id, minutes, reason):
    if minutes > balance_minutes(user_id):
        return False
    conn = get_connection()
    conn.execute(
        "INSERT INTO rewards (user_id, minutes, reason, created_at) VALUES (?, ?, ?, ?)",
        (user_id, -int(minutes), reason, now_str()),
    )
    conn.commit()
    conn.close()
    return True


def reward_history(user_id, limit=30):
    conn = get_connection()
    rows = conn.execute(
        "SELECT minutes, reason, created_at FROM rewards WHERE user_id = ? "
        "ORDER BY id DESC LIMIT ?",
        (user_id, limit),
    ).fetchall()
    conn.close()
    return [dict(r) for r in rows]


# ============================================================
# USAGE + INSIGHTS
# ============================================================

ENTERTAINMENT = {"instagram", "youtube", "snapchat", "netflix", "tiktok", "facebook",
                 "whatsapp", "spotify", "game", "pubg", "freefire", "reels", "twitter"}


def is_entertainment(name):
    lowered = name.lower()
    return any(word in lowered for word in ENTERTAINMENT)


def save_usage(user_id, day, app_name, minutes):
    conn = get_connection()
    conn.execute(
        "DELETE FROM usage_records WHERE user_id = ? AND day = ? AND app_name = ?",
        (user_id, day, app_name),
    )
    conn.execute(
        "INSERT INTO usage_records (user_id, day, app_name, minutes) VALUES (?, ?, ?, ?)",
        (user_id, day, app_name, int(minutes)),
    )
    conn.commit()
    conn.close()


def usage_frame(user_id, days=7):
    start = (date.today() - timedelta(days=days - 1)).isoformat()
    conn = get_connection()
    frame = pd.read_sql_query(
        "SELECT day, app_name, minutes FROM usage_records WHERE user_id = ? AND day >= ?",
        conn,
        params=(user_id, start),
    )
    conn.close()
    return frame


def usage_insights(user_id, days=7):
    frame = usage_frame(user_id, days)
    if frame.empty:
        return None

    totals = frame.groupby("app_name")["minutes"].sum().sort_values(ascending=False)
    total = int(totals.sum())
    covered = max(frame["day"].nunique(), 1)
    entertainment = int(sum(m for app, m in totals.items() if is_entertainment(app)))
    ent_share = entertainment / total if total else 0

    suggestions = []
    top_app, top_minutes = totals.index[0], int(totals.iloc[0])
    if total and top_minutes / total >= 0.4:
        suggestions.append(
            f"{top_app} alone is {top_minutes / total:.0%} of your screen time. "
            "Put it behind an earned-time unlock."
        )
    if ent_share >= 0.5:
        suggestions.append(
            f"Entertainment apps take {ent_share:.0%} of your time. "
            "Try one 25-minute study block before opening them."
        )
    if total / covered > 300:
        suggestions.append("You average over five hours a day. Set a daily cap you can keep.")
    if not suggestions:
        suggestions.append("Your usage looks balanced this week. Keep the routine going.")

    return {
        "totals": totals,
        "total": total,
        "daily_average": int(total / covered),
        "days_covered": covered,
        "suggestions": suggestions,
        "frame": frame,
    }


# ============================================================
# SMALL UI HELPERS
# ============================================================


def hero(title, subtitle):
    st.markdown(
        f'<div class="focusly-hero"><h1>{title}</h1><p>{subtitle}</p></div>',
        unsafe_allow_html=True,
    )


def note(text):
    st.markdown(f'<div class="focusly-note">{text}</div>', unsafe_allow_html=True)


def current_user():
    return st.session_state.get("user")


def require_login():
    if current_user() is None:
        st.warning("Please log in on the **Log in** page first.")
        st.stop()


# ============================================================
# PAGES
# ============================================================


def page_login():
    hero("FOCUSLY", "Earn your screen time. Build your future.")

    left, mid, right = st.columns([1, 1.7, 1])
    with mid:
        step = st.session_state.get("signup_step")

        # ---------- step 2: choose a password ----------
        if step == "password":
            pending = st.session_state.get("pending_signup", {})
            st.subheader("Create a password")
            st.caption(f"For {pending.get('email', '')}")

            with st.form("password_form"):
                password = st.text_input("Password", type="password",
                                         help="At least 8 characters.")
                confirm = st.text_input("Repeat the password", type="password")
                saved = st.form_submit_button("Create password", use_container_width=True)

            if saved:
                if len(password) < 8:
                    st.error("Use at least 8 characters.")
                elif password != confirm:
                    st.error("The two passwords do not match.")
                else:
                    ok, message = create_user(
                        pending["name"], pending["email"], password, pending["role"]
                    )
                    if ok:
                        st.session_state["user"] = find_user(pending["email"], password)
                        st.session_state.pop("signup_step", None)
                        st.session_state.pop("pending_signup", None)
                        st.success(message)
                        st.rerun()
                    else:
                        st.error(message)

            if st.button("Start again", use_container_width=True):
                st.session_state.pop("signup_step", None)
                st.session_state.pop("pending_signup", None)
                st.rerun()
            return

        # ---------- step 1: name and email ----------
        if step == "details":
            st.subheader("Create an account")
            with st.form("signup_form"):
                name = st.text_input("Your name")
                email = st.text_input("Email", placeholder="you@example.com")
                role = st.selectbox("I am a", ["student", "teacher"])
                next_step = st.form_submit_button("Create account", use_container_width=True)

            if next_step:
                if len(name.strip()) < 2:
                    st.error("Please enter your name.")
                elif "@" not in email or "." not in email.split("@")[-1]:
                    st.error("Please enter an email address, like name@example.com.")
                else:
                    if email_exists(email):
                        st.error("That email already has an account. Go back and log in.")
                    else:
                        st.session_state["pending_signup"] = {
                            "name": name.strip(),
                            "email": email.strip().lower(),
                            "role": role,
                        }
                        st.session_state["signup_step"] = "password"
                        st.rerun()

            if st.button("Back to log in", use_container_width=True):
                st.session_state.pop("signup_step", None)
                st.rerun()
            return

        # ---------- log in ----------
        st.subheader("Log in")
        with st.form("login_form"):
            email = st.text_input("Email", placeholder="you@example.com")
            password = st.text_input("Password", type="password")
            submitted = st.form_submit_button("Log in", use_container_width=True)

        if submitted:
            user = find_user(email, password)
            if user:
                st.session_state["user"] = user
                st.success(f"Welcome back, {user['name']}.")
                st.rerun()
            else:
                st.error("No account matches that email and password.")

        st.write("")
        st.caption("New here?")
        if st.button("Create an account", type="primary", use_container_width=True):
            st.session_state["signup_step"] = "details"
            st.rerun()


def page_dashboard(user):
    hero(f"Hi, {user['name'].split()[0]}", "Here is where you stand today.")

    balance = balance_minutes(user["id"])
    earned = lifetime_minutes(user["id"])
    streak = streak_days(user["id"])
    level = earned // LEVEL_STEP + 1
    to_next = LEVEL_STEP - (earned % LEVEL_STEP)

    c1, c2, c3, c4 = st.columns(4)
    c1.metric("Minutes to spend", balance)
    c2.metric("Earned all time", earned)
    c3.metric("Level", level, help=f"{to_next} minutes to level {level + 1}")
    c4.metric("Day streak", streak)

    st.progress(min(1.0, (earned % LEVEL_STEP) / LEVEL_STEP), text=f"{to_next} minutes to level {level + 1}")

    st.divider()
    left, right = st.columns([1.2, 1], gap="large")

    with left:
        st.subheader("Recent homework")
        conn = get_connection()
        rows = conn.execute(
            "SELECT * FROM submissions WHERE user_id = ? ORDER BY id DESC LIMIT 6", (user["id"],)
        ).fetchall()
        conn.close()
        if not rows:
            st.info("Nothing submitted yet. Open **Homework** and send your first answer.")
        for row in rows:
            feedback = json.loads(row["feedback"] or "[]")
            with st.container(border=True):
                a, b = st.columns([3, 1])
                a.markdown(f"**{row['subject']}** · {row['created_at'][:16]}")
                b.markdown(f"### {row['score']:.0f}/{row['max_score']:.0f}")
                if feedback:
                    st.caption(feedback[0])
                if row["needs_review"]:
                    st.caption("Flagged for teacher review.")

    with right:
        st.subheader("Minutes ledger")
        history = reward_history(user["id"], 8)
        if not history:
            st.info("No minutes yet.")
        for event in history:
            sign = "+" if event["minutes"] >= 0 else ""
            st.markdown(f"`{sign}{event['minutes']} min` &nbsp; {event['reason']}", unsafe_allow_html=True)


def page_study_coach(user):
    hero("AI Smart Study Coach", "Tell it what you have to study. It builds the plan.")

    if "subjects" not in st.session_state:
        st.session_state["subjects"] = [
            {"name": "Maths", "days_left": 2, "difficulty": 3},
            {"name": "Science", "days_left": 6, "difficulty": 2},
        ]

    st.subheader("Your subjects")
    with st.form("add_subject"):
        c1, c2, c3, c4 = st.columns([2.2, 1, 1.2, 0.9])
        name = c1.text_input("Subject", placeholder="English")
        days_left = c2.number_input("Days to deadline", min_value=1, max_value=60, value=5)
        difficulty = c3.select_slider("Difficulty", options=[1, 2, 3], value=2,
                                      format_func=lambda v: {1: "Easy", 2: "Medium", 3: "Hard"}[v])
        added = c4.form_submit_button("Add")
    if added and name.strip():
        st.session_state["subjects"].append(
            {"name": name.strip(), "days_left": int(days_left), "difficulty": int(difficulty)}
        )
        st.rerun()

    for index, subject in enumerate(list(st.session_state["subjects"])):
        with st.container(border=True):
            c1, c2, c3, c4 = st.columns([2.5, 1.4, 1.6, 0.6])
            c1.markdown(f"**{subject['name']}**")
            c2.caption(f"Deadline in {subject['days_left']} day(s)")
            c3.caption({1: "Easy", 2: "Medium", 3: "Hard"}[subject["difficulty"]])
            if c4.button("Remove", key=f"rm{index}"):
                st.session_state["subjects"].pop(index)
                st.rerun()

    st.divider()
    c1, c2 = st.columns(2)
    hours = c1.slider("Free hours per day", 0.5, 6.0, 2.0, 0.5)
    days = c2.slider("Plan for how many days", 1, 14, 7)

    if st.button("Build my study plan", type="primary", use_container_width=True):
        if not st.session_state["subjects"]:
            st.error("Add at least one subject first.")
        else:
            with st.spinner("Building your plan..."):
                plan = build_plan(st.session_state["subjects"], int(days), float(hours))
            st.session_state["plan"] = plan
            conn = get_connection()
            conn.execute(
                "INSERT INTO plans (user_id, plan_json, source, created_at) VALUES (?, ?, ?, ?)",
                (user["id"], json.dumps(plan), plan.get("source", "rules"), now_str()),
            )
            conn.commit()
            conn.close()

    plan = st.session_state.get("plan")
    if plan:
        source = plan.get("source", "rules")
        st.caption("Generated by Sarvam AI." if source == "sarvam"
                   else "Generated by the built-in planner (add a Sarvam AI key for model-generated plans).")
        for day in plan["days"]:
            with st.expander(f"Day {day['day']} · {day.get('focus', '')} · {day.get('date', '')}", expanded=day["day"] == 1):
                frame = pd.DataFrame(day["blocks"])
                if not frame.empty:
                    st.dataframe(
                        frame[["start", "subject", "minutes", "task"]],
                        use_container_width=True, hide_index=True,
                    )
        if plan.get("tips"):
            st.subheader("Tips")
            for tip in plan["tips"]:
                st.markdown(f"- {tip}")


def page_homework(user):
    hero("Homework verification", "Send your work. Earn your screen time.")

    c1, c2 = st.columns([1.3, 1], gap="large")

    with c1:
        with st.form("homework_form", clear_on_submit=False):
            subject = st.text_input("Subject", value="Maths")
            question = st.text_area("The question", placeholder="Solve 15 + 27 and show your steps", height=80)
            answer = st.text_area(
                "Your answer",
                placeholder="Type your working here. You can also attach a photo.",
                height=180,
            )
            photo = st.file_uploader("Photo of your work (optional)", type=["jpg", "jpeg", "png", "webp"])
            submitted = st.form_submit_button("Submit for verification", use_container_width=True)

        if submitted:
            photo_note = ""
            if photo is not None:
                photo_note, quality = check_photo(photo)
                st.info(f"Photo check: {photo_note}")

            if not answer.strip() and not photo_note:
                st.error("Write your answer, or attach a photo of it.")
            else:
                result = verify_answer(question, answer)
                conn = get_connection()
                conn.execute(
                    "INSERT INTO submissions (user_id, subject, question, answer, score, max_score, "
                    "feedback, needs_review, photo_note, created_at) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                    (user["id"], subject, question, answer, result["score"], result["max_score"],
                     json.dumps(result["feedback"]), int(result["needs_review"]), photo_note, now_str()),
                )
                submission_id = conn.execute("SELECT last_insert_rowid() AS id").fetchone()["id"]
                conn.commit()
                conn.close()
                minutes = award_minutes(user["id"], submission_id, subject, result["ratio"])

                st.success(f"Verified: {result['score']}/{result['max_score']} · +{minutes} minutes earned")
                for line in result["feedback"]:
                    st.markdown(f"- {line}")
                if result["needs_review"]:
                    st.warning("A teacher will double-check this one before it counts.")
                st.metric("Minutes you can spend now", balance_minutes(user["id"]))

    with c2:
        st.subheader("How the check works")
        st.markdown(
            "Three signals decide your score:\n\n"
            "1. **Coverage** — does the answer address the question?\n"
            "2. **Effort** — is there enough written work?\n"
            "3. **Arithmetic** — every sum you write is re-calculated, including "
            "stepped ones like `1 + 2 + 1 = 4`.\n\n"
            "If the score lands in the uncertain 40–60% band, it is **flagged for a teacher** "
            "who can override it. The AI assists; the teacher decides."
        )
        st.divider()
        st.subheader("Try it")
        st.markdown(
            "**Good answer** — gets full marks:\n\n"
            "> 15 + 27 = 42. I added 5 + 7 = 12, carried 1, then added 1 + 2 + 1 = 4.\n\n"
            "**Wrong sum** — loses marks:\n\n"
            "> 15 + 27 = 40 because I added the digits.\n\n"
            "Submitting both, one after the other, is a strong way to show judges that the "
            "checker reads the maths rather than counting words."
        )


def page_rewards(user):
    hero("Earned screen time", "Homework earns minutes. Entertainment spends them.")

    balance = balance_minutes(user["id"])
    earned = lifetime_minutes(user["id"])
    streak = streak_days(user["id"])

    c1, c2, c3 = st.columns(3)
    c1.metric("Minutes to spend", balance)
    c2.metric("Earned all time", earned)
    c3.metric("Day streak", streak)

    st.divider()
    c1, c2 = st.columns([1, 1], gap="large")

    with c1:
        st.subheader("Unlock entertainment")
        minutes = st.slider("How many minutes?", 5, 120, 15, 5)
        reason = st.text_input("What for?", value="Instagram unlock")
        if st.button("Spend minutes", type="primary", use_container_width=True):
            if spend_minutes(user["id"], minutes, reason):
                st.success(f"Unlocked {minutes} minutes for {reason}.")
                st.rerun()
            else:
                st.error(
                    f"Not enough earned time. You have {balance_minutes(user['id'])} minutes. "
                    "Complete homework to earn more."
                )

    with c2:
        st.subheader("Ledger")
        history = reward_history(user["id"], 15)
        if not history:
            st.info("No minutes yet.")
        else:
            frame = pd.DataFrame(history)
            frame["minutes"] = frame["minutes"].apply(lambda m: f"{m:+d}")
            st.dataframe(frame.rename(columns={"minutes": "min", "reason": "reason", "created_at": "when"}),
                         use_container_width=True, hide_index=True)


def page_insights(user):
    hero("Focus intelligence", "Where your time actually goes, and what to do about it.")

    note(
        "The numbers here come from what you enter, or from the sample week. In the Android "
        "build this page reads real per-app minutes from Android's UsageStatsManager."
    )
    if st.button("Load a sample week of usage"):
        load_sample_usage(user["id"])
        st.success("Sample week loaded.")
        st.rerun()
    st.write("")

    with st.expander("Add or update a day's usage"):
        c1, c2, c3, c4 = st.columns([1.4, 1.6, 1, 0.9])
        day = c1.date_input("Day", value=date.today())
        app_name = c2.text_input("App", value="Instagram")
        minutes = c3.number_input("Minutes", min_value=0, max_value=1440, value=60)
        if c4.button("Save"):
            save_usage(user["id"], day.isoformat(), app_name, minutes)
            st.success("Saved.")
            st.rerun()

    days = st.radio("Window", [7, 14], horizontal=True, format_func=lambda d: f"Last {d} days")
    data = usage_insights(user["id"], days)

    if not data:
        st.info("No usage recorded yet. Add a day above, or log in as the demo student.")
        return

    c1, c2, c3 = st.columns(3)
    c1.metric("Total minutes", data["total"])
    c2.metric("Daily average", data["daily_average"])
    c3.metric("Days covered", data["days_covered"])

    st.subheader("Most used apps")
    st.bar_chart(data["totals"], horizontal=True, height=260)

    st.subheader("Daily pattern")
    pivot = data["frame"].pivot_table(index="day", columns="app_name", values="minutes",
                                      aggfunc="sum").fillna(0)
    st.line_chart(pivot, height=280)

    st.subheader("What to do about it")
    for tip in data["suggestions"]:
        st.markdown(f"- {tip}")


def page_coach_chat(user):
    hero("Ask your study coach", "Stuck on a topic? Ask in plain words.")

    if not ai_ready():
        st.info(
            "Sarvam AI is not connected, so this page shows prepared tips instead of live answers. "
            "Add SARVAM_API_KEY in the app secrets to switch it on."
        )
        st.markdown(
            "- **How long should I study?** 25 minutes at a time, then a 5-minute break. Four blocks "
            "is two hours.\n"
            "- **I keep picking up my phone.** Put it in another room for one block. One block is easier "
            "than 'no phone all evening'.\n"
            "- **I have too much to do.** Write every task down, then start with the nearest deadline. "
            "The study coach page does this ordering for you.\n"
            "- **I forget what I studied.** End each block by writing three lines from memory."
        )
        return

    if "chat" not in st.session_state:
        st.session_state["chat"] = []

    for message in st.session_state["chat"]:
        with st.chat_message(message["role"]):
            st.markdown(message["content"])

    prompt = st.chat_input("Ask about a subject, a deadline, or how to focus")
    if prompt:
        st.session_state["chat"].append({"role": "user", "content": prompt})
        with st.chat_message("user"):
            st.markdown(prompt)
        with st.chat_message("assistant"):
            with st.spinner("Thinking..."):
                try:
                    reply = ask_text(
                        "You are a friendly study coach for an Indian school student. "
                        "Answer in under 150 words, practical and encouraging. "
                        f"Question: {prompt}"
                    )
                except Exception:  # noqa: BLE001
                    reply = "I could not reach the AI just now. Try again in a moment."
            st.markdown(reply)
        st.session_state["chat"].append({"role": "assistant", "content": reply})


def page_teacher(user):
    hero("Teacher review", "The AI flags; you decide.")

    conn = get_connection()
    rows = conn.execute(
        "SELECT s.*, u.name AS student_name FROM submissions s JOIN users u ON u.id = s.user_id "
        "ORDER BY s.needs_review DESC, s.id DESC LIMIT 30"
    ).fetchall()
    conn.close()

    if not rows:
        st.info("No submissions yet.")
        return

    for row in rows:
        with st.container(border=True):
            c1, c2, c3 = st.columns([2.4, 1.2, 1.2])
            c1.markdown(f"**{row['student_name']}** · {row['subject']}")
            c1.caption(f"{row['created_at'][:16]} · {row['question'][:90]}")
            c2.metric("Score", f"{row['score']:.0f}/{row['max_score']:.0f}")
            c3.markdown("🚩 Needs review" if row["needs_review"] else "✅ Checked")

            with st.expander("Answer and feedback"):
                st.write(row["answer"] or "(no typed answer)")
                for line in json.loads(row["feedback"] or "[]"):
                    st.markdown(f"- {line}")

                new_score = st.number_input(
                    "Override score", min_value=0.0, max_value=float(row["max_score"]),
                    value=float(row["score"]), key=f"score{row['id']}",
                )
                comment = st.text_input("Comment", key=f"comment{row['id']}")
                if st.button("Save review", key=f"save{row['id']}"):
                    feedback = json.loads(row["feedback"] or "[]")
                    if comment:
                        feedback.append(f"Teacher: {comment}")
                    conn = get_connection()
                    conn.execute(
                        "UPDATE submissions SET score = ?, needs_review = 0, feedback = ? WHERE id = ?",
                        (new_score, json.dumps(feedback), row["id"]),
                    )
                    conn.commit()
                    conn.close()
                    st.success("Saved.")
                    st.rerun()


def page_about(user=None):
    hero("About FOCUSLY", "SEVA FIRST Innovation Challenge 2026 · School & Vocational")
    st.markdown(
        """
**Team:** The Problem Solvers · Bal Bharati Public School

**The problem.** Smartphones distract students from study. Existing screen-time tools
track and block, but they do not connect schoolwork to what students actually want —
entertainment time.

**What FOCUSLY does.** It plans study work, verifies that homework is done, and turns
verified homework into earned entertainment minutes.

**How each part is built**

| Part | Technology |
|---|---|
| App screens | Streamlit (this file) |
| Backend logic | Python |
| Database + login | SQLite, PBKDF2 password hashing |
| Study planning | Sarvam AI (`sarvam-105b`), with a built-in planner as fallback |
| Homework verification | Keyword coverage, effort, and re-calculation of every written sum |
| Screen time | Sample data here; real data comes from Android `UsageStatsManager` |
| Earned screen time | A minutes ledger — credits from homework, debits from spending |

**What is honest about it**

- App blocking is **not** implemented. FOCUSLY measures usage and rewards earned time;
  blocking needs Android Accessibility/Device Admin permissions and Play Store approval.
- Photo upload checks image quality; reading handwriting needs a vision model.
- Push notifications are designed but not wired.

**Why the AI is allowed to be wrong.** Every uncertain score is flagged for a teacher who
can override it. The AI assists; the teacher decides. That is a design choice, not a gap.
        """
    )


# ============================================================
# NAVIGATION
# ============================================================

PAGES = {
    "Log in": page_login,
    "Today": page_dashboard,
    "Study coach": page_study_coach,
    "Homework": page_homework,
    "Rewards": page_rewards,
    "Insights": page_insights,
    "Ask the coach": page_coach_chat,
    "Teacher review": page_teacher,
    "About": page_about,
}

user = current_user()

with st.sidebar:
    st.markdown("### ⏳ FOCUSLY")
    st.caption("Earn your screen time. Build your future.")
    st.divider()

    options = [name for name in PAGES if name != "Log in"] if user else ["Log in", "About"]
    choice = st.radio("Go to", options, label_visibility="collapsed")

    if user:
        st.divider()
        st.caption(f"Logged in as **{user['name']}** ({user['role']})")
        if st.button("Log out", use_container_width=True):
            st.session_state.pop("user", None)
            st.session_state.pop("plan", None)
            st.session_state.pop("chat", None)
            st.session_state.pop("signup_step", None)
            st.session_state.pop("pending_signup", None)
            st.rerun()

    st.divider()
    st.caption("AI: " + ("Sarvam AI connected" if ai_ready() else "offline planner"))

if choice in ("Today", "Study coach", "Homework", "Rewards", "Insights", "Ask the coach"):
    require_login()

if choice == "Log in":
    PAGES[choice]()
else:
    PAGES[choice](user)
