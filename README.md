# Telegram Quiz Extractor v3.0

An upgraded, high-reliability Telegram Quiz Extractor tailored for Termux and Linux environments.

This tool extracts quizzes from **@QuizBot** and **@advance_quiz_bot**, exports questions in a strict standard TXT format, and delivers them directly through your personal Telegram bot or via interactive CLI.

---

## 🏗️ Architecture & Credentials

The system operates on a dual-interface architecture:

```
┌────────────────────────────────────────────────────────┐
│               Telegram User (You)                      │
└───────────────────────┬────────────────────────────────┘
                        │ Sends quiz link / card
                        ▼
┌────────────────────────────────────────────────────────┐
│     User-Facing Bot Interface (bot_interface.py)       │
│     • Uses Telegram Bot API (TG_BOT_TOKEN)             │
│     • Validates link/card & queues extraction jobs     │
│     • Sends progress & delivers final TXT export       │
└───────────────────────┬────────────────────────────────┘
                        │ Serial Job Queue (FIFO)
                        ▼
┌────────────────────────────────────────────────────────┐
│       Extraction Engine (core/engine.py & bot3.py)     │
│     • Uses Telethon User Session (TG_API_ID/TG_API_HASH│
│     • Interacts with @QuizBot & @advance_quiz_bot      │
│     • Answers polls to reveal correct answers          │
│     • Handles FloodWait rate-limits & poll delays      │
└────────────────────────────────────────────────────────┘
```

> **Why both credentials?**
> Telegram bots cannot interact with other bots or vote in another bot's quizzes. Therefore:
> 1. **Telethon User Session** (`TG_API_ID`, `TG_API_HASH`): Acts as your user account to start quizzes, click inline buttons, and read poll answers from `@QuizBot` and `@advance_quiz_bot`.
> 2. **Telegram Bot Token** (`TG_BOT_TOKEN`): Acts as your private helper bot where you send links and receive files.

---

## 📋 Strict Output Format

All valid four-option questions are exported strictly as:

```text
TITLE: [Quiz Title]

Q1. [Question text]
(A) [Option A]
(B) [Option B]
(C) [Option C]
(D) [Option D]
ANSWER: B
EXP: [Explanation]

Q2. [Question text]
(A) [Option A]
(B) [Option B]
(C) [Option C]
(D) [Option D]
ANSWER: UNKNOWN
EXP: Not available
```

### Formatting Rules:
1. First line is always `TITLE: [Quiz Title]`.
2. Sequential numbering `Q1.`, `Q2.`, etc.
3. Exactly four options labelled `(A)`, `(B)`, `(C)`, `(D)`.
4. Answer is letter format `ANSWER: A/B/C/D`, or `ANSWER: UNKNOWN` (never numeric, never guessed).
5. Explanation is `EXP: [Explanation]` or `EXP: Not available` if the quiz has no explanation.
6. **Manual Review Separation**: Questions with fewer or more than 4 options are separated into `[Quiz_Title]_manual_review.txt` with detailed diagnostic reasons so they never corrupt the primary export.
7. **Filenames**: Saved as `[Sanitized_Quiz_Title].txt` (e.g. `Himachal_Pradesh_GK.txt`), with collision detection (`_1`, `_2`) so files are never overwritten.

---

## 🚀 Installation & Termux Setup

### 1. Install prerequisites in Termux
```bash
pkg update && pkg install python git -y
```

### 2. Set up virtual environment
```bash
cd ~
git clone <repo_url> quiz-extractor
cd quiz-extractor
python -m venv ~/quiz-venv
source ~/quiz-venv/bin/activate
pip install -r requirements.txt
```

### 3. Configure credentials
Copy the template and edit your secrets:
```bash
cp .env.example ~/.quizbot.env
nano ~/.quizbot.env
```

Fill in:
```env
TG_API_ID=YOUR_TELEGRAM_API_ID
TG_API_HASH=YOUR_TELEGRAM_API_HASH_HERE
TG_BOT_TOKEN=YOUR_BOT_TOKEN_FROM_BOTFATHER
AUTHORIZED_USERS=123456789
# Optional: explicitly set session path if located elsewhere
# TG_SESSION_PATH=/data/data/com.termux/files/home/quiz-extractor/telegram_session
```

### Session Path Resolution (`TG_SESSION_PATH`)
The application automatically auto-discovers existing `.session` files in:
1. The project directory (`./telegram_session.session` or any `*.session`)
2. Termux home (`/data/data/com.termux/files/home/quiz-extractor/telegram_session.session`)
3. Standard home (`~/quiz-extractor/telegram_session.session`)

If you want to pin a specific path, set `TG_SESSION_PATH` without the `.session` suffix.

> **Note on Initial Telethon Login:**
> The first time you use your Telethon user session, run `python bot3.py` in the terminal. Telethon will prompt for your phone number and OTP code to authenticate and create the session file. Once authenticated, `bot_interface.py` can run continuously in the background without prompting for login codes.

---

## 🏃 Running the Application

### Option A: Run as a Telegram Bot (Recommended)
You can forward quiz links directly to your bot in Telegram:
```bash
source ~/quiz-venv/bin/activate
python bot_interface.py
```

### Option B: Run Interactive CLI
If you prefer running in the terminal and pulling from Saved Messages:
```bash
source ~/quiz-venv/bin/activate
python bot3.py
```

---

## 🛡️ Multiple Quiz Jobs & FloodWait Protection

- **Serial Queue**: Consecutive quizzes are assigned unique IDs and processed one at a time. Live quizzes never conflict.
- **Isolated State**: Each job maintains its own poll listener, message queue, watermark, and output file.
- **FloodWait Handling**: Catches Telethon `FloodWaitError` and sleeps for the server-requested wait duration plus safety buffer before resuming.
- **Delayed Poll Recovery**: Scans chat history if poll delivery is delayed to ensure questions are not missed.
- **Deduplication**: Repeated requests for the same quiz payload are detected to prevent duplicate active sessions.
- **Restart Persistence**: Job state is stored in `jobs_state.json`. Completed jobs are recorded and never silently re-run.

---

## 🧪 Running Tests
Run automated unit and integration tests using pytest:
```bash
pytest tests/ -v
```
