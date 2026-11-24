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

There are no demo accounts to remember. Click **Create an account**, enter a name and any
email address, then choose a password on the next screen, and you are in. The email does
not have to be a school address, and nothing is verified or sent to it.

## Put it online (public link, free)

1. Create a GitHub account and a new **public** repository called `focusly`.
2. Click **uploading an existing file** and drag in `app.py` and `requirements.txt`
   (the README too, if you want the repo to look complete).
3. Commit.
4. Go to https://share.streamlit.io, sign in with GitHub, click **New app**.
5. Pick your repository, branch `main`, main file path `app.py`, then **Deploy**.
6. Wait two minutes. You get a public URL like `https://focusly-xxxx.streamlit.app`.

Anyone — judges, teachers, your teammates — can open that link from a phone.

## Accounts and passwords

Signing up happens in two steps:

1. **Create an account** — your name, your email (any address works), and whether you are a
   student or a teacher.
2. **Create a password** — choose one of at least 8 characters and type it twice. When you
   click **Create password**, the account exists and you are logged in.

After that, logging in needs the email and the password together. Passwords are stored
hashed (PBKDF2 with a salt), never as plain text, so nobody can read them out of the
database.

Two limits worth knowing: the email is not verified, so an address that does not exist will
still work; and there is no "forgot password" yet. For a demo that is fine. Before a pilot
with real students you would add email verification, password reset, and a privacy note for
parents.

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
