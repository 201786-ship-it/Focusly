# FOCUSLY — Earn Your Screen Time

SEVA FIRST Innovation Challenge 2026 · School & Vocational · Team **The Problem Solvers**
Bal Bharati Public School

One Streamlit file that plans study work with AI, verifies homework, and turns verified
homework into earned entertainment minutes.

## Files

```
app.py            the whole app
requirements.txt  the packages it needs
README.md         this page
```

## Run it on your laptop

```
pip install -r requirements.txt
streamlit run app.py
```

It opens at http://localhost:8501.

There are no demo accounts to remember. Type any email, take the code, then choose your
FOCUSLY password — and you are in.

## Put it online (public link, free)

1. Create a GitHub account and a new **public** repository called `focusly`.
2. Click **uploading an existing file** and drag in `app.py` and `requirements.txt`
   (the README too, if you want the repo to look complete).
3. Commit.
4. Go to https://share.streamlit.io, sign in with GitHub, click **New app**.
5. Pick your repository, branch `main`, main file path `app.py`, then **Deploy**.
6. Wait two minutes. You get a public URL like `https://focusly-xxxx.streamlit.app`.

Anyone — judges, teachers, your teammates — can open that link from a phone.

## How signing in works

**First time — three steps**

1. Type your email. A 6-digit code is sent to that inbox.
2. Type the code. It is good for ten minutes.
3. Choose your FOCUSLY password (at least 8 characters, typed twice), add your name, and the
   account is created and you are logged in.

**Every time after that — three steps**

1. Type your email.
2. Type the new code sent to that inbox.
3. Type your FOCUSLY password.

The code proves the email is really yours; the password is the one you made for FOCUSLY, not
your Gmail password. Both are needed, which is why a stolen password alone is not enough.

Passwords are stored hashed (PBKDF2 with a salt), never as plain text, so nobody can read
them out of the database — not even you.

### Sending the code

The code has to reach a real inbox, which means the app needs a mail account to send from.
Add these in Streamlit → **Settings → Secrets**:

```toml
SMTP_HOST = "smtp.gmail.com"
SMTP_PORT = 587
SMTP_USER = "your.address@gmail.com"
SMTP_PASSWORD = "your-16-character-app-password"
SMTP_FROM = "your.address@gmail.com"
```

For Gmail, create an **App Password** (Google Account → Security → 2-Step Verification → App
passwords). Your normal Gmail password will not work. Any provider's SMTP works — use its
host and port.

**Without those settings** the app still works for a demo: it shows the code on screen
instead of emailing it, and says so plainly. That is honest for a school demo, but it is not
real verification — with the code visible on screen, anyone can sign in with any address.

Two things still missing: there is no "forgot password", and nothing limits how many codes
one person can request. Both matter before real students use it.

## Turn on the real AI (Sarvam AI)

The app works without a key: the study coach uses a built-in planner and the chat page
shows prepared tips. To switch on Sarvam AI:

1. Get a key from https://dashboard.sarvam.ai.
2. On Streamlit Cloud: your app → **Settings → Secrets**, paste:

```toml
SARVAM_API_KEY = "your-key-here"
```

3. Save. The app restarts and the sidebar shows "Sarvam AI connected".

Locally, either put the same line in `.streamlit/secrets.toml` or set the environment
variable `SARVAM_API_KEY`.

**Never put the key in `app.py` or in the GitHub repository.** A public repo means anyone
can read it.

## What is real and what is not — say this honestly to judges

| Feature | State |
|---|---|
| Sign up, log in, study plan, homework checking, rewards | Working, stored in a real database |
| Study planning with Sarvam AI | Working when a key is added; built-in planner otherwise |
| Homework photo | Uploaded and quality-checked (blur, brightness, resolution); handwriting is **not** read without a vision model |
| Screen time | Sample week loaded by a button on the Insights page, or numbers you type in. Real per-app data needs the Android build (`UsageStatsManager`) |
| App blocking | **Not implemented.** Blocking needs Android Accessibility/Device Admin permissions and Play Store approval |
| Database | SQLite. On Streamlit Cloud the file resets when the app reboots, so it is fine for a demo, not for a pilot |

## How homework is scored

Three signals: **coverage** (does the answer address the question), **effort** (is there
enough written work), and **arithmetic** (every sum is re-calculated, including stepped
ones like `1 + 2 + 1 = 4`). Scores in the uncertain 40–60% band are flagged for a teacher
who can override them. No `eval()` anywhere — sums are parsed with a regex and an
operator table, so a photo of code cannot run anything.

The AI assists; the teacher decides. That sentence is the safety story, and it is the
part judges remember.
