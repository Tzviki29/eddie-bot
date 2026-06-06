import os
import httpx
import time
import threading
import datetime
import subprocess
import tempfile
import base64
import json
import smtplib
import re
from email.mime.text import MIMEText
from email.mime.multipart import MIMEMultipart
from http.server import HTTPServer, BaseHTTPRequestHandler
import psycopg2
from psycopg2.extras import RealDictCursor

# ── Config ────────────────────────────────────────────────────
TG_TOKEN   = os.environ.get("TG_TOKEN", "")
GROQ_KEY   = os.environ.get("GROQ_KEY", "")
HF_TOKEN   = os.environ.get("HF_TOKEN", "")
DB_URL     = os.environ.get("DB_URL", "")
BIZ_EMAIL  = os.environ.get("BIZ_EMAIL", "aibusinessupgrade1@gmail.com")
BIZ_PASS   = os.environ.get("BIZ_PASS", "")
PER_EMAIL  = os.environ.get("PER_EMAIL", "tzvikilieberman@gmail.com")
PER_PASS   = os.environ.get("PER_PASS", "")
TG_API     = f"https://api.telegram.org/bot{TG_TOKEN}"
OWNER_ID   = None

SYSTEM_PROMPT = """You are Eddie — Tzviki's personal AI and chief of staff.
You're like that one friend who knows everything and gives real answers, not corporate fluff.
Sharp, direct, funny when it fits. Help with anything: business, life, ideas, research,
writing, coding, strategy, drafting messages. Talk like a real person. No "Great question!"
nonsense. Be real, be Eddie. Keep responses concise unless the question is deep.
You have access to Tzviki's memory, clients, goals, ideas, expenses, tasks, and reminders.
When referring to saved data, be specific and helpful.
IMPORTANT: If the user writes in Hebrew, respond in Hebrew. If in English, respond in English.
Always match the language the user is writing in.

JEWISH CONTEXT — Tzviki is an observant Jew. Keep this in mind:
- Shabbat is Friday sundown to Saturday night — don't schedule things then
- Jewish holidays matter: Rosh Hashana, Yom Kippur, Sukkot, Chanuka, Purim, Pesach, Shavuot
- Kosher food laws apply — no mixing meat and dairy, no pork or shellfish
- Weekly summary sends Sunday morning (start of Israeli work week)
- Greet with "Shavua Tov" on Sunday, "Shabbat Shalom" on Friday
- Understand Hebrew words and phrases naturally
- "Baruch Hashem", "Bezrat Hashem", "Bli Neder" are common expressions Tzviki may use
- Israeli culture: direct, no-nonsense, warm with people they trust"""

# ── Database ──────────────────────────────────────────────────
def get_db():
    return psycopg2.connect(DB_URL)

def init_db():
    try:
        conn = get_db()
        cur = conn.cursor()
        cur.execute("""
            CREATE TABLE IF NOT EXISTS messages (
                id SERIAL PRIMARY KEY,
                user_id BIGINT,
                role TEXT,
                content TEXT,
                created_at TIMESTAMP DEFAULT NOW()
            );
            CREATE TABLE IF NOT EXISTS clients (
                id SERIAL PRIMARY KEY,
                name TEXT,
                details TEXT,
                last_contact TIMESTAMP,
                created_at TIMESTAMP DEFAULT NOW()
            );
            CREATE TABLE IF NOT EXISTS goals (
                id SERIAL PRIMARY KEY,
                goal TEXT,
                status TEXT DEFAULT 'active',
                created_at TIMESTAMP DEFAULT NOW()
            );
            CREATE TABLE IF NOT EXISTS ideas (
                id SERIAL PRIMARY KEY,
                idea TEXT,
                created_at TIMESTAMP DEFAULT NOW()
            );
            CREATE TABLE IF NOT EXISTS expenses (
                id SERIAL PRIMARY KEY,
                amount REAL,
                description TEXT,
                category TEXT,
                created_at TIMESTAMP DEFAULT NOW()
            );
            CREATE TABLE IF NOT EXISTS tasks (
                id SERIAL PRIMARY KEY,
                task TEXT,
                priority TEXT DEFAULT 'normal',
                status TEXT DEFAULT 'pending',
                due_date TIMESTAMP,
                created_at TIMESTAMP DEFAULT NOW()
            );
            CREATE TABLE IF NOT EXISTS reminders (
                id SERIAL PRIMARY KEY,
                user_id BIGINT,
                message TEXT,
                remind_at TIMESTAMP,
                recurring TEXT,
                sent BOOLEAN DEFAULT FALSE,
                created_at TIMESTAMP DEFAULT NOW()
            );
            CREATE TABLE IF NOT EXISTS budget (
                id SERIAL PRIMARY KEY,
                monthly_limit REAL,
                created_at TIMESTAMP DEFAULT NOW()
            );
            CREATE TABLE IF NOT EXISTS owner (
                id SERIAL PRIMARY KEY,
                user_id BIGINT
            );
        """)
        conn.commit()
        cur.close()
        conn.close()
        print("Database initialized!")
    except Exception as e:
        print(f"DB init error: {e}")

def save_message(user_id, role, content):
    try:
        conn = get_db()
        cur = conn.cursor()
        cur.execute("INSERT INTO messages (user_id, role, content) VALUES (%s, %s, %s)", (user_id, role, content))
        conn.commit()
        cur.close()
        conn.close()
    except Exception as e:
        print(f"Save message error: {e}")

def get_history(user_id, limit=20):
    try:
        conn = get_db()
        cur = conn.cursor(cursor_factory=RealDictCursor)
        cur.execute("SELECT role, content FROM messages WHERE user_id = %s ORDER BY created_at DESC LIMIT %s", (user_id, limit))
        rows = cur.fetchall()
        cur.close()
        conn.close()
        return [{"role": r["role"], "content": r["content"]} for r in reversed(rows)]
    except:
        return []

def summarize_old_messages(user_id):
    try:
        conn = get_db()
        cur = conn.cursor(cursor_factory=RealDictCursor)
        cur.execute("SELECT role, content FROM messages WHERE user_id = %s ORDER BY created_at DESC LIMIT 100", (user_id,))
        rows = cur.fetchall()
        cur.close()
        conn.close()
        if not rows:
            return "No conversation history."
        text = "\n".join([f"{r['role']}: {r['content']}" for r in reversed(rows)])
        summary = query_groq([{"role": "user", "content": f"Summarize this conversation in bullet points:\n{text}"}], max_tokens=500)
        return summary
    except Exception as e:
        return f"Error: {e}"

def save_client(name, details):
    try:
        conn = get_db()
        cur = conn.cursor()
        cur.execute("INSERT INTO clients (name, details, last_contact) VALUES (%s, %s, NOW())", (name, details))
        conn.commit()
        cur.close()
        conn.close()
        return True
    except:
        return False

def update_client_contact(name):
    try:
        conn = get_db()
        cur = conn.cursor()
        cur.execute("UPDATE clients SET last_contact = NOW() WHERE LOWER(name) = LOWER(%s)", (name,))
        conn.commit()
        cur.close()
        conn.close()
    except:
        pass

def get_clients():
    try:
        conn = get_db()
        cur = conn.cursor(cursor_factory=RealDictCursor)
        cur.execute("SELECT name, details, last_contact FROM clients ORDER BY created_at DESC")
        rows = cur.fetchall()
        cur.close()
        conn.close()
        return rows
    except:
        return []

def get_stale_clients(days=14):
    try:
        conn = get_db()
        cur = conn.cursor(cursor_factory=RealDictCursor)
        cur.execute("SELECT name FROM clients WHERE last_contact < NOW() - INTERVAL '%s days' OR last_contact IS NULL", (days,))
        rows = cur.fetchall()
        cur.close()
        conn.close()
        return rows
    except:
        return []

def save_goal(goal):
    try:
        conn = get_db()
        cur = conn.cursor()
        cur.execute("INSERT INTO goals (goal) VALUES (%s)", (goal,))
        conn.commit()
        cur.close()
        conn.close()
        return True
    except:
        return False

def get_goals():
    try:
        conn = get_db()
        cur = conn.cursor(cursor_factory=RealDictCursor)
        cur.execute("SELECT id, goal, status FROM goals WHERE status = 'active' ORDER BY created_at DESC")
        rows = cur.fetchall()
        cur.close()
        conn.close()
        return rows
    except:
        return []

def complete_goal(goal_text):
    try:
        conn = get_db()
        cur = conn.cursor()
        cur.execute("UPDATE goals SET status = 'done' WHERE LOWER(goal) LIKE LOWER(%s)", (f"%{goal_text}%",))
        conn.commit()
        cur.close()
        conn.close()
        return True
    except:
        return False

def save_idea(idea):
    try:
        conn = get_db()
        cur = conn.cursor()
        cur.execute("INSERT INTO ideas (idea) VALUES (%s)", (idea,))
        conn.commit()
        cur.close()
        conn.close()
        return True
    except:
        return False

def get_ideas():
    try:
        conn = get_db()
        cur = conn.cursor(cursor_factory=RealDictCursor)
        cur.execute("SELECT idea FROM ideas ORDER BY created_at DESC LIMIT 10")
        rows = cur.fetchall()
        cur.close()
        conn.close()
        return rows
    except:
        return []

def save_expense(amount, description, category="general"):
    try:
        conn = get_db()
        cur = conn.cursor()
        cur.execute("INSERT INTO expenses (amount, description, category) VALUES (%s, %s, %s)", (amount, description, category))
        conn.commit()
        cur.close()
        conn.close()
        return True
    except:
        return False

def get_expenses(days=30):
    try:
        conn = get_db()
        cur = conn.cursor(cursor_factory=RealDictCursor)
        cur.execute("SELECT amount, description, category, created_at FROM expenses WHERE created_at > NOW() - INTERVAL '%s days' ORDER BY created_at DESC", (days,))
        rows = cur.fetchall()
        cur.close()
        conn.close()
        return rows
    except:
        return []

def get_budget():
    try:
        conn = get_db()
        cur = conn.cursor()
        cur.execute("SELECT monthly_limit FROM budget ORDER BY created_at DESC LIMIT 1")
        row = cur.fetchone()
        cur.close()
        conn.close()
        return row[0] if row else None
    except:
        return None

def set_budget(amount):
    try:
        conn = get_db()
        cur = conn.cursor()
        cur.execute("INSERT INTO budget (monthly_limit) VALUES (%s)", (amount,))
        conn.commit()
        cur.close()
        conn.close()
        return True
    except:
        return False

def save_task(task, priority="normal", due_date=None):
    try:
        conn = get_db()
        cur = conn.cursor()
        cur.execute("INSERT INTO tasks (task, priority, due_date) VALUES (%s, %s, %s)", (task, priority, due_date))
        conn.commit()
        cur.close()
        conn.close()
        return True
    except:
        return False

def get_tasks():
    try:
        conn = get_db()
        cur = conn.cursor(cursor_factory=RealDictCursor)
        cur.execute("SELECT id, task, priority, due_date FROM tasks WHERE status = 'pending' ORDER BY CASE priority WHEN 'high' THEN 1 WHEN 'normal' THEN 2 WHEN 'low' THEN 3 END, created_at DESC")
        rows = cur.fetchall()
        cur.close()
        conn.close()
        return rows
    except:
        return []

def complete_task(task_text):
    try:
        conn = get_db()
        cur = conn.cursor()
        cur.execute("UPDATE tasks SET status = 'done' WHERE LOWER(task) LIKE LOWER(%s) AND status = 'pending'", (f"%{task_text}%",))
        conn.commit()
        cur.close()
        conn.close()
        return True
    except:
        return False

def save_reminder(user_id, message, remind_at, recurring=None):
    try:
        conn = get_db()
        cur = conn.cursor()
        cur.execute("INSERT INTO reminders (user_id, message, remind_at, recurring) VALUES (%s, %s, %s, %s)",
                    (user_id, message, remind_at, recurring))
        conn.commit()
        cur.close()
        conn.close()
        return True
    except:
        return False

def get_due_reminders():
    try:
        conn = get_db()
        cur = conn.cursor(cursor_factory=RealDictCursor)
        cur.execute("SELECT * FROM reminders WHERE sent = FALSE AND remind_at <= NOW()")
        rows = cur.fetchall()
        cur.close()
        conn.close()
        return rows
    except:
        return []

def mark_reminder_sent(reminder_id, recurring=None):
    try:
        conn = get_db()
        cur = conn.cursor()
        if recurring == "daily":
            cur.execute("UPDATE reminders SET remind_at = remind_at + INTERVAL '1 day' WHERE id = %s", (reminder_id,))
        elif recurring == "weekly":
            cur.execute("UPDATE reminders SET remind_at = remind_at + INTERVAL '7 days' WHERE id = %s", (reminder_id,))
        elif recurring == "hourly":
            cur.execute("UPDATE reminders SET remind_at = remind_at + INTERVAL '1 hour' WHERE id = %s", (reminder_id,))
        else:
            cur.execute("UPDATE reminders SET sent = TRUE WHERE id = %s", (reminder_id,))
        conn.commit()
        cur.close()
        conn.close()
    except:
        pass

def set_owner(user_id):
    global OWNER_ID
    OWNER_ID = user_id
    try:
        conn = get_db()
        cur = conn.cursor()
        cur.execute("DELETE FROM owner")
        cur.execute("INSERT INTO owner (user_id) VALUES (%s)", (user_id,))
        conn.commit()
        cur.close()
        conn.close()
    except:
        pass

def get_owner():
    global OWNER_ID
    if OWNER_ID:
        return OWNER_ID
    try:
        conn = get_db()
        cur = conn.cursor()
        cur.execute("SELECT user_id FROM owner LIMIT 1")
        row = cur.fetchone()
        cur.close()
        conn.close()
        if row:
            OWNER_ID = row[0]
            return OWNER_ID
    except:
        pass
    return None

# ── Telegram ──────────────────────────────────────────────────
def send_tg(chat_id, text):
    try:
        with httpx.Client(timeout=30) as client:
            client.post(f"{TG_API}/sendMessage", json={"chat_id": chat_id, "text": text, "parse_mode": "Markdown"})
    except Exception as e:
        print(f"Send TG error: {e}")

def download_file(file_id):
    try:
        with httpx.Client(timeout=60) as client:
            resp = client.get(f"{TG_API}/getFile?file_id={file_id}")
            file_path = resp.json()["result"]["file_path"]
            file_resp = client.get(f"https://api.telegram.org/file/bot{TG_TOKEN}/{file_path}")
            return file_resp.content, file_path
    except Exception as e:
        print(f"Download file error: {e}")
        return None, None

# ── Email ─────────────────────────────────────────────────────
def send_email(to_email, subject, body, is_business=True):
    try:
        from_email = BIZ_EMAIL if is_business else PER_EMAIL
        password = BIZ_PASS if is_business else PER_PASS
        msg = MIMEMultipart()
        msg["From"] = from_email
        msg["To"] = to_email
        msg["Subject"] = subject
        msg.attach(MIMEText(body, "plain"))
        server = smtplib.SMTP_SSL("smtp.gmail.com", 465)
        server.login(from_email, password)
        server.sendmail(from_email, to_email, msg.as_string())
        server.quit()
        return True
    except Exception as e:
        print(f"Email error: {e}")
        return False

# ── Web Search ────────────────────────────────────────────────
def web_search(query):
    try:
        with httpx.Client(timeout=15) as client:
            resp = client.get("https://api.duckduckgo.com/",
                params={"q": query, "format": "json", "no_html": 1, "skip_disambig": 1})
            data = resp.json()
            results = []
            if data.get("AbstractText"):
                results.append(data["AbstractText"])
            for r in data.get("RelatedTopics", [])[:5]:
                if isinstance(r, dict) and r.get("Text"):
                    results.append(r["Text"])
            if results:
                return "\n".join(results[:4])
            # fallback
            resp2 = client.get(f"https://api.duckduckgo.com/?q={query}&format=json&no_html=1")
            data2 = resp2.json()
            if data2.get("Answer"):
                return data2["Answer"]
            return "No results found. Try rephrasing your search."
    except Exception as e:
        return f"Search error: {e}"

# ── Groq ──────────────────────────────────────────────────────
DEEP_KEYWORDS = ["explain","why","how","analyze","strategy","plan","write","draft",
                 "research","compare","pros","cons","difference","detail","summarize",
                 "code","build","create","help me","should i","what would","opinion",
                 "email","message","letter","למה","איך","תכתוב","תסביר","תנתח"]

def is_deep(text):
    t = text.lower() if isinstance(text, str) else ""
    return len(t) > 80 or any(k in t for k in DEEP_KEYWORDS)

def query_groq(messages, model="llama-3.3-70b-versatile", max_tokens=None, image_url=None):
    if max_tokens is None:
        last = messages[-1]["content"] if messages else ""
        max_tokens = 1024 if is_deep(last) else 200

    if image_url:
        model = "llama-3.2-11b-vision-preview"
        last_msg = messages[-1]
        text_content = last_msg["content"] if isinstance(last_msg["content"], str) else "What's in this image?"
        messages[-1]["content"] = [
            {"type": "image_url", "image_url": {"url": image_url}},
            {"type": "text", "text": text_content}
        ]

    headers = {"Authorization": f"Bearer {GROQ_KEY}", "Content-Type": "application/json"}
    payload = {"model": model, "messages": messages, "max_tokens": max_tokens, "temperature": 0.8}
    try:
        with httpx.Client(timeout=60) as client:
            resp = client.post("https://api.groq.com/openai/v1/chat/completions", json=payload, headers=headers)
            resp.raise_for_status()
            return resp.json()["choices"][0]["message"]["content"].strip()
    except Exception as e:
        return f"Error: {str(e)}"

def transcribe_audio(audio_bytes, filename="audio.ogg"):
    try:
        with tempfile.NamedTemporaryFile(suffix=".ogg", delete=False) as f:
            f.write(audio_bytes)
            tmp_path = f.name
        with open(tmp_path, "rb") as f:
            files = {"file": (filename, f, "audio/ogg")}
            data = {"model": "whisper-large-v3", "response_format": "text"}
            headers = {"Authorization": f"Bearer {GROQ_KEY}"}
            with httpx.Client(timeout=60) as client:
                resp = client.post("https://api.groq.com/openai/v1/audio/transcriptions",
                                   headers=headers, files=files, data=data)
                resp.raise_for_status()
                return resp.text.strip()
    except Exception as e:
        return f"Transcription error: {e}"

# ── Image Generation ──────────────────────────────────────────
def generate_image(prompt):
    try:
        headers = {"Authorization": f"Bearer {HF_TOKEN}"}
        with httpx.Client(timeout=120) as client:
            resp = client.post(
                "https://api-inference.huggingface.co/models/stabilityai/stable-diffusion-xl-base-1.0",
                headers=headers, json={"inputs": prompt})
            if resp.status_code == 200:
                with tempfile.NamedTemporaryFile(suffix=".png", delete=False) as f:
                    f.write(resp.content)
                    return f.name
    except Exception as e:
        print(f"Image gen error: {e}")
    return None

# ── Code Execution ────────────────────────────────────────────
def execute_code(code, language="python"):
    try:
        suffix = ".py" if language == "python" else ".js"
        with tempfile.NamedTemporaryFile(mode='w', suffix=suffix, delete=False) as f:
            f.write(code)
            tmp_path = f.name
        if language == "python":
            result = subprocess.run(["python3", tmp_path], capture_output=True, text=True, timeout=15)
        elif language in ["javascript", "js", "node"]:
            result = subprocess.run(["node", tmp_path], capture_output=True, text=True, timeout=15)
        else:
            return f"Language '{language}' not supported for execution."
        return (result.stdout or result.stderr or "No output")[:2000]
    except subprocess.TimeoutExpired:
        return "Code timed out after 15 seconds."
    except Exception as e:
        return f"Execution error: {e}"

# ── Video ─────────────────────────────────────────────────────
def process_video(video_bytes):
    try:
        with tempfile.NamedTemporaryFile(suffix=".mp4", delete=False) as f:
            f.write(video_bytes)
            video_path = f.name
        audio_path = video_path.replace(".mp4", ".ogg")
        subprocess.run(["ffmpeg", "-i", video_path, "-vn", "-acodec", "libvorbis", audio_path, "-y"],
                       capture_output=True, timeout=60)
        transcript = ""
        if os.path.exists(audio_path):
            with open(audio_path, "rb") as f:
                transcript = transcribe_audio(f.read())
        frame_path = video_path.replace(".mp4", "_frame.jpg")
        subprocess.run(["ffmpeg", "-i", video_path, "-ss", "00:00:05", "-vframes", "1", frame_path, "-y"],
                       capture_output=True, timeout=30)
        frame_desc = ""
        if os.path.exists(frame_path):
            with open(frame_path, "rb") as f:
                img_b64 = base64.b64encode(f.read()).decode()
            img_url = f"data:image/jpeg;base64,{img_b64}"
            msgs = [{"role": "user", "content": "Describe what you see in this video frame."}]
            frame_desc = query_groq(msgs, image_url=img_url)
        result = ""
        if transcript:
            result += f"🎙️ *Audio:* {transcript}\n\n"
        if frame_desc:
            result += f"🎬 *Visual:* {frame_desc}"
        return result or "Could not process video."
    except Exception as e:
        return f"Video error: {e}"

# ── Intent Detection ──────────────────────────────────────────
def detect_intent(text):
    t = text.lower()
    if re.search(r'add client|save client|new client|הוסף לקוח', t): return "add_client"
    if re.search(r'show clients|list clients|my clients|הלקוחות שלי', t): return "list_clients"
    if re.search(r'add goal|save goal|new goal|הוסף מטרה', t): return "add_goal"
    if re.search(r'show goals|list goals|my goals|המטרות שלי', t): return "list_goals"
    if re.search(r'goal done|completed goal|finished goal', t): return "complete_goal"
    if re.search(r'save idea|new idea|add idea|שמור רעיון', t): return "add_idea"
    if re.search(r'show ideas|list ideas|my ideas|הרעיונות שלי', t): return "list_ideas"
    if re.search(r'log expense|add expense|spent|paid \d|הוצאה', t): return "add_expense"
    if re.search(r'show expenses|list expenses|my expenses|ההוצאות שלי', t): return "list_expenses"
    if re.search(r'set budget|my budget|תקציב', t): return "budget"
    if re.search(r'add task|new task|to.?do|הוסף משימה', t): return "add_task"
    if re.search(r'show tasks|list tasks|my tasks|המשימות שלי', t): return "list_tasks"
    if re.search(r'task done|completed task|finished task|סיימתי', t): return "complete_task"
    if re.search(r'remind me|set reminder|תזכיר לי', t): return "set_reminder"
    if re.search(r'summarize (?:our|the|my) (?:conversation|chat|history)', t): return "summarize"
    if re.search(r'search|look up|latest|news|current|today|who is|what is|חפש|מה זה', t): return "web_search"
    if re.search(r'generate image|create image|draw|make an image|צור תמונה', t): return "generate_image"
    if re.search(r'run code|execute|```python|```js', t): return "execute_code"
    if re.search(r'stock|share price|nasdaq|nyse|ticker', t): return "stock"
    if re.search(r'convert|exchange rate|shekel|dollar|euro|currency', t): return "currency"
    if re.search(r'news|headlines|what happened|latest in', t): return "news"
    if re.search(r'jewish calendar|parasha|shabbat times|zmanim|havdalah|candle lighting', t): return "jewish_calendar"
    if re.search(r'zmanim|prayer times|shacharit|mincha|maariv', t): return "zmanim"
    if re.search(r'draft contract|create contract|make contract|service agreement|nda|contract for', t): return "contract"
    if re.search(r'add to portfolio|bought|i own|my portfolio|portfolio summary', t): return "portfolio"
    if re.search(r'alert me|notify me when|price alert', t): return "price_alert"
    if re.search(r'prediction market|polymarket|odds on|what are the odds', t): return "prediction"
    if re.search(r'create invoice|make invoice|invoice for|generate invoice', t): return "invoice"
    if re.search(r'weekly pnl|profit and loss|weekly report|how much did i spend', t): return "weekly_pnl"
    if re.search(r'log workout|workout|exercise|gym|ran|lifted|training', t): return "workout"
    if re.search(r'prayer time|mincha|shacharit|maariv|next prayer', t): return "prayer"
    return "chat"

def build_context():
    ctx = ""
    goals = get_goals()
    if goals:
        ctx += "Active goals: " + ", ".join([g["goal"] for g in goals]) + "\n"
    clients = get_clients()
    if clients:
        ctx += "Clients: " + ", ".join([c["name"] for c in clients]) + "\n"
    tasks = get_tasks()
    if tasks:
        ctx += "Pending tasks: " + ", ".join([t["task"] for t in tasks[:5]]) + "\n"
    expenses = get_expenses(30)
    if expenses:
        total = sum(e["amount"] for e in expenses)
        budget = get_budget()
        ctx += f"Expenses this month: ${total:.2f}"
        if budget:
            ctx += f" (budget: ${budget:.2f})"
        ctx += "\n"
    return ctx

# ── Proactive Follow-up Check ─────────────────────────────────
def check_followups(chat_id):
    stale = get_stale_clients(14)
    if stale:
        names = ", ".join([c["name"] for c in stale])
        send_tg(chat_id, f"👋 Hey — you haven't been in touch with *{names}* in over 2 weeks. Want to follow up?")

# ── Handle Update ─────────────────────────────────────────────
def handle_update(update):
    message = update.get("message", {})
    chat_id = message.get("chat", {}).get("id")
    user_id = message.get("from", {}).get("id")
    if not chat_id:
        return

    text = message.get("text", "").strip()

    if text == "/start":
        set_owner(user_id)
        send_tg(chat_id, "Eddie here. What do you need?\n\nType /help to see everything I can do.")
        return

    if text == "/reset":
        try:
            conn = get_db()
            cur = conn.cursor()
            cur.execute("DELETE FROM messages WHERE user_id = %s", (user_id,))
            conn.commit()
            cur.close()
            conn.close()
        except:
            pass
        send_tg(chat_id, "Memory cleared! Fresh start.")
        return

    if text == "/briefing":
        send_briefing(chat_id)
        return

    if text == "/followup":
        check_followups(chat_id)
        return

    if text == "/help":
        help_text = """*Eddie v2 — Full Command List:*

*💬 Just talk naturally — I understand context!*

*Clients:*
• "add client: Motty, 054..."
• "show my clients"
• (I'll remind you to follow up after 2 weeks)

*Goals:*
• "add goal: close 3 deals"
• "show my goals"
• "goal done: close 3 deals"

*Tasks:*
• "add task: call Motty"
• "show my tasks"
• "task done: call Motty"

*Ideas:*
• "save idea: ..."
• "show my ideas"

*Expenses:*
• "log expense: 500 on ads"
• "show my expenses"
• "set budget: 5000"

*Reminders:*
• "remind me in 30 min to call Motty"
• "remind me every day at 9am to check emails"
• "remind me every Monday to send invoices"

*Search:* "search for latest news on..."

*Generate image:* "generate image of..."

*Run code:* wrap in \`\`\`python ... \`\`\`

*Media:* send voice, photos, videos, documents

*Other:*
• "summarize our conversation"
• /briefing — morning briefing now
• /followup — check stale clients
• /reset — clear conversation memory"""
        send_tg(chat_id, help_text)
        return

    # Voice
    if "voice" in message:
        file_id = message["voice"]["file_id"]
        audio_bytes, _ = download_file(file_id)
        if audio_bytes:
            send_tg(chat_id, "🎙️ Transcribing...")
            transcript = transcribe_audio(audio_bytes)
            send_tg(chat_id, f"You said: _{transcript}_")
            text = transcript
        else:
            send_tg(chat_id, "Couldn't download voice message.")
            return

    # Photo
    if "photo" in message:
        file_id = message["photo"][-1]["file_id"]
        img_bytes, _ = download_file(file_id)
        if img_bytes:
            img_b64 = base64.b64encode(img_bytes).decode()
            img_url = f"data:image/jpeg;base64,{img_b64}"
            caption = message.get("caption", "What's in this image?")
            msgs = [{"role": "user", "content": caption}]
            reply = query_groq(msgs, image_url=img_url, max_tokens=512)
            save_message(user_id, "user", f"[Photo] {caption}")
            save_message(user_id, "assistant", reply)
            send_tg(chat_id, reply)
        else:
            send_tg(chat_id, "Couldn't read that photo.")
        return

    # Document
    if "document" in message:
        file_id = message["document"]["file_id"]
        doc_bytes, _ = download_file(file_id)
        if doc_bytes:
            content = doc_bytes.decode("utf-8", errors="ignore")[:3000]
            text = f"Here's a document I uploaded, please summarize it:\n\n{content}"
        else:
            send_tg(chat_id, "Couldn't download that document.")
            return

    # Video
    if "video" in message:
        file_id = message["video"]["file_id"]
        send_tg(chat_id, "🎬 Processing video...")
        video_bytes, _ = download_file(file_id)
        if video_bytes:
            result = process_video(video_bytes)
            send_tg(chat_id, result)
        else:
            send_tg(chat_id, "Couldn't download that video.")
        return

    if not text:
        return

    intent = detect_intent(text)

    if intent == "add_client":
        match = re.search(r'(?:add|save|new) client[:\s]+([^\n]+)', text, re.I)
        name = match.group(1).strip().split(",")[0] if match else text.replace("add client", "").strip()
        save_client(name, text)
        send_tg(chat_id, f"✅ Client *{name}* saved!")
        return

    if intent == "list_clients":
        clients = get_clients()
        if clients:
            lines = []
            for c in clients:
                last = ""
                if c.get("last_contact"):
                    days_ago = (datetime.datetime.utcnow() - c["last_contact"]).days
                    last = f" _(last contact: {days_ago}d ago)_"
                lines.append(f"• *{c['name']}*: {c['details']}{last}")
            msg = "📋 *Your clients:*\n" + "\n".join(lines)
        else:
            msg = "No clients saved yet."
        send_tg(chat_id, msg)
        return

    if intent == "complete_goal":
        match = re.search(r'(?:goal done|completed goal|finished goal)[:\s]+(.+)', text, re.I)
        goal_text = match.group(1).strip() if match else text
        complete_goal(goal_text)
        send_tg(chat_id, f"🎯 Goal completed: _{goal_text}_ ✅")
        return

    if intent == "add_goal":
        match = re.search(r'(?:add|save|new) goal[:\s]+(.+)', text, re.I)
        goal = match.group(1).strip() if match else text
        save_goal(goal)
        send_tg(chat_id, f"🎯 Goal saved: _{goal}_")
        return

    if intent == "list_goals":
        goals = get_goals()
        msg = "🎯 *Your goals:*\n" + "\n".join([f"• {g['goal']}" for g in goals]) if goals else "No goals yet."
        send_tg(chat_id, msg)
        return

    if intent == "add_idea":
        match = re.search(r'(?:save|new|add) idea[:\s]+(.+)', text, re.I)
        idea = match.group(1).strip() if match else text
        save_idea(idea)
        send_tg(chat_id, f"💡 Idea saved: _{idea}_")
        return

    if intent == "list_ideas":
        ideas = get_ideas()
        msg = "💡 *Your ideas:*\n" + "\n".join([f"• {i['idea']}" for i in ideas]) if ideas else "No ideas saved yet."
        send_tg(chat_id, msg)
        return

    if intent == "add_expense":
        amount_match = re.search(r'(\d+(?:\.\d+)?)', text)
        amount = float(amount_match.group(1)) if amount_match else 0
        save_expense(amount, text)
        reply = f"💸 Expense logged: ${amount}"
        budget = get_budget()
        if budget:
            expenses = get_expenses(30)
            total = sum(e["amount"] for e in expenses)
            pct = (total / budget) * 100
            reply += f"\n_{pct:.0f}% of monthly budget used (${total:.0f}/${budget:.0f})_"
            if pct >= 90:
                reply += "\n⚠️ *Warning: Almost at budget limit!*"
        send_tg(chat_id, reply)
        return

    if intent == "list_expenses":
        expenses = get_expenses(30)
        if expenses:
            total = sum(e["amount"] for e in expenses)
            lines = "\n".join([f"• ${e['amount']} — {e['description']}" for e in expenses[:10]])
            msg = f"💸 *Expenses (last 30 days):*\nTotal: *${total:.2f}*\n\n{lines}"
            budget = get_budget()
            if budget:
                msg += f"\n\nBudget: ${budget:.2f} | Remaining: ${budget-total:.2f}"
        else:
            msg = "No expenses logged yet."
        send_tg(chat_id, msg)
        return

    if intent == "budget":
        match = re.search(r'(\d+(?:\.\d+)?)', text)
        if match:
            amount = float(match.group(1))
            set_budget(amount)
            send_tg(chat_id, f"💰 Monthly budget set to ${amount:.2f}")
        else:
            budget = get_budget()
            if budget:
                expenses = get_expenses(30)
                total = sum(e["amount"] for e in expenses)
                send_tg(chat_id, f"💰 Monthly budget: ${budget:.2f}\nSpent: ${total:.2f}\nRemaining: ${budget-total:.2f}")
            else:
                send_tg(chat_id, "No budget set. Say 'set budget: 5000'")
        return

    if intent == "add_task":
        match = re.search(r'(?:add|new) task[:\s]+(.+)', text, re.I)
        task = match.group(1).strip() if match else text
        priority = "high" if re.search(r'urgent|important|asap|high priority', text, re.I) else "normal"
        save_task(task, priority)
        send_tg(chat_id, f"✅ Task added: _{task}_ {'🔴' if priority == 'high' else ''}")
        return

    if intent == "list_tasks":
        tasks = get_tasks()
        if tasks:
            lines = []
            for t in tasks:
                priority_icon = "🔴" if t["priority"] == "high" else "⚪"
                lines.append(f"{priority_icon} {t['task']}")
            msg = "📝 *Your tasks:*\n" + "\n".join(lines)
        else:
            msg = "No pending tasks!"
        send_tg(chat_id, msg)
        return

    if intent == "complete_task":
        match = re.search(r'(?:task done|completed task|finished task|סיימתי)[:\s]+(.+)', text, re.I)
        task_text = match.group(1).strip() if match else text
        complete_task(task_text)
        send_tg(chat_id, f"✅ Task done: _{task_text}_")
        return

    if intent == "set_reminder":
        parse_prompt = f"""Extract reminder info from: "{text}"
Return ONLY JSON: {{"message": "what to remind", "minutes_from_now": 30, "recurring": null}}
For recurring: use "daily", "weekly", "hourly" or null.
Examples: "every day" -> "daily", "every Monday" -> "weekly", null for one-time."""
        ai_response = query_groq([{"role": "user", "content": parse_prompt}], max_tokens=150)
        try:
            clean = re.search(r'\{.*\}', ai_response, re.DOTALL)
            if clean:
                parsed = json.loads(clean.group())
                minutes = int(parsed.get("minutes_from_now", 60))
                reminder_msg = parsed.get("message", text)
                recurring = parsed.get("recurring")
                remind_at = datetime.datetime.utcnow() + datetime.timedelta(minutes=minutes)
                save_reminder(user_id, reminder_msg, remind_at, recurring)
                recurring_text = f" (repeats {recurring})" if recurring else ""
                send_tg(chat_id, f"⏰ Reminder set for {minutes} minutes{recurring_text}: _{reminder_msg}_")
                return
        except:
            pass
        send_tg(chat_id, "Tell me exactly when: 'remind me in 30 minutes to call Motty' or 'remind me every day at 9am'")
        return

    if intent == "summarize":
        send_tg(chat_id, "📝 Summarizing our conversation...")
        summary = summarize_old_messages(user_id)
        send_tg(chat_id, f"*Conversation Summary:*\n{summary}")
        return

    if intent == "web_search":
        send_tg(chat_id, "🔍 Searching...")
        results = web_search(text)
        history = get_history(user_id)
        history.append({"role": "user", "content": f"Web search for '{text}':\n{results}\n\nAnswer based on these results in the same language as the question."})
        reply = query_groq([{"role": "system", "content": SYSTEM_PROMPT}] + history, max_tokens=512)
        save_message(user_id, "user", text)
        save_message(user_id, "assistant", reply)
        send_tg(chat_id, reply)
        return

    if intent == "generate_image":
        send_tg(chat_id, "🎨 Generating image...")
        match = re.search(r'(?:generate|create|draw|make) (?:an? )?image (?:of )?(.+)', text, re.I)
        prompt = match.group(1) if match else text
        img_path = generate_image(prompt)
        if img_path:
            with open(img_path, "rb") as f:
                with httpx.Client(timeout=30) as client:
                    client.post(f"{TG_API}/sendPhoto", files={"photo": f}, data={"chat_id": chat_id, "caption": prompt})
        else:
            send_tg(chat_id, "Couldn't generate image right now. Try again!")
        return

    if intent == "execute_code":
        code_match = re.search(r'```(\w+)?\n(.*?)```', text, re.DOTALL)
        if code_match:
            lang = code_match.group(1) or "python"
            code = code_match.group(2)
            send_tg(chat_id, f"⚙️ Running {lang}...")
            output = execute_code(code, lang)
            send_tg(chat_id, f"```\n{output}\n```")
        else:
            send_tg(chat_id, "Wrap your code in triple backticks to run it!")
        return

    if intent == "stock":
        symbols = re.findall(r'\b[A-Z]{1,5}\b', text)
        common = ["I", "A", "AN", "THE", "MY", "IS", "AT", "IN", "ON", "OR", "AND", "FOR", "GET", "HOW"]
        symbols = [s for s in symbols if s not in common]
        if not symbols:
            # Try to extract from lowercase
            match = re.search(r'(?:stock|price of|shares? of)\s+([a-zA-Z]+)', text, re.I)
            if match:
                symbols = [match.group(1).upper()]
        if symbols:
            results = []
            for sym in symbols[:3]:
                results.append(get_stock_price(sym))
            send_tg(chat_id, "\n".join(results))
        else:
            send_tg(chat_id, "Which stock? Give me a ticker like AAPL, TSLA, MSFT...")
        return

    if intent == "currency":
        match = re.search(r'(\d+(?:\.\d+)?)\s*([a-zA-Z]{3})\s*(?:to|in)\s*([a-zA-Z]{3})', text, re.I)
        if match:
            amount = float(match.group(1))
            from_curr = match.group(2)
            to_curr = match.group(3)
            send_tg(chat_id, convert_currency(amount, from_curr, to_curr))
        else:
            # Let AI figure it out
            history = get_history(user_id)
            history.append({"role": "user", "content": text})
            reply = query_groq([{"role": "system", "content": SYSTEM_PROMPT + "\nFor currency questions, extract the amount and currencies and format as: CONVERT:amount:FROM:TO"}] + history)
            if "CONVERT:" in reply:
                parts = reply.split(":")
                try:
                    send_tg(chat_id, convert_currency(float(parts[1]), parts[2], parts[3]))
                except:
                    send_tg(chat_id, reply)
            else:
                send_tg(chat_id, reply)
        return

    if intent == "news":
        match = re.search(r'(?:news|headlines|latest) (?:about|on|in|of)?\s*(.+)', text, re.I)
        topic = match.group(1).strip() if match else "world"
        send_tg(chat_id, "📰 Getting news...")
        send_tg(chat_id, get_news(topic))
        return

    if intent == "jewish_calendar":
        send_tg(chat_id, get_jewish_info())
        return

    if intent == "zmanim":
        send_tg(chat_id, get_zmanim())
        return

    if intent == "contract":
        match = re.search(r'(?:draft|create|make) (?:a )?(?:contract|agreement|nda) (?:for )?(.+)', text, re.I)
        details = match.group(1) if match else text
        send_tg(chat_id, "📄 Drafting contract...")
        result = generate_contract("Service Agreement", details, chat_id)
        if isinstance(result, str):
            send_tg(chat_id, result)
        return

    if intent == "portfolio":
        if re.search(r'add to portfolio|bought|i own', text, re.I):
            match = re.search(r'([A-Z]{1,5})\s+(\d+(?:\.\d+)?)\s+(?:shares?|@|at)\s+\$?(\d+(?:\.\d+)?)', text, re.I)
            if match:
                sym, shares, price = match.group(1), float(match.group(2)), float(match.group(3))
                save_portfolio_stock(sym, shares, price)
                send_tg(chat_id, f"✅ Added {shares} shares of *{sym.upper()}* at ${price:.2f} to portfolio!")
            else:
                send_tg(chat_id, "Format: 'add to portfolio: AAPL 10 shares at $150'")
        else:
            send_tg(chat_id, "📊 Getting portfolio...")
            send_tg(chat_id, get_portfolio_summary())
        return

    if intent == "price_alert":
        match = re.search(r'([A-Z]{1,5})\s+(?:drops?|falls?|goes?|rises?|above|below)\s+\$?(\d+(?:\.\d+)?)', text, re.I)
        if match:
            sym = match.group(1).upper()
            price = float(match.group(2))
            direction = "below" if re.search(r'drops?|falls?|below', text, re.I) else "above"
            save_price_alert(user_id, sym, price, direction)
            send_tg(chat_id, f"🔔 Alert set! I'll notify you when *{sym}* goes {direction} ${price:.2f}")
        else:
            send_tg(chat_id, "Format: 'alert me when AAPL drops below $200' or 'alert me when TSLA rises above $300'")
        return

    if intent == "prediction":
        match = re.search(r'(?:prediction market|odds on|what are the odds on?)\s+(.+)', text, re.I)
        topic = match.group(1) if match else ""
        send_tg(chat_id, "🔮 Checking prediction markets...")
        send_tg(chat_id, get_prediction_markets(topic))
        return

    if intent == "invoice":
        match = re.search(r'(?:invoice|bill) (?:for )?(.+)', text, re.I)
        details = match.group(1) if match else text
        send_tg(chat_id, "🧾 Generating invoice...")
        result = generate_invoice(details, chat_id)
        if isinstance(result, str):
            send_tg(chat_id, result)
        return

    if intent == "weekly_pnl":
        send_tg(chat_id, get_weekly_pnl())
        return

    if intent == "workout":
        if re.search(r'log workout|i (?:ran|lifted|worked out|did|went to)', text, re.I):
            duration_match = re.search(r'(\d+)\s*(?:min|minutes|hours?|hrs?)', text, re.I)
            duration = int(duration_match.group(1)) if duration_match else 30
            workout_match = re.search(r'(?:log workout[:\s]+)?(.+?)(?:\s+for|\s+\d+\s*min|$)', text, re.I)
            workout_type = workout_match.group(1).replace("log workout", "").strip() if workout_match else "General workout"
            save_workout(workout_type, duration, text)
            send_tg(chat_id, f"💪 Workout logged: {workout_type} — {duration} min!")
        else:
            send_tg(chat_id, get_workout_stats())
        return

    if intent == "prayer":
        send_tg(chat_id, get_next_prayer_time())
        return

    # Handle PDF filling
    if "document" in message:
        file_id = message["document"]["file_id"]
        filename = message["document"].get("file_name", "")
        if filename.lower().endswith(".pdf"):
            doc_bytes, _ = download_file(file_id)
            if doc_bytes:
                send_tg(chat_id, "📋 Analyzing PDF form...")
                analysis = analyze_pdf_for_filling(doc_bytes, chat_id)
                send_tg(chat_id, f"Here's what I need to fill this form:\n\n{analysis}\n\nJust reply with the info and I'll fill it in!")
                save_message(user_id, "user", f"[PDF Upload] {filename}")
                save_message(user_id, "assistant", analysis)
            return

    # Default chat — update client contact if mentioned
    for c in get_clients():
        if c["name"].lower() in text.lower():
            update_client_contact(c["name"])

    history = get_history(user_id)
    context = build_context()
    system = SYSTEM_PROMPT + (f"\n\nContext:\n{context}" if context else "")
    history.append({"role": "user", "content": text})
    reply = query_groq([{"role": "system", "content": system}] + history)
    save_message(user_id, "user", text)
    save_message(user_id, "assistant", reply)
    send_tg(chat_id, reply)

# ── Briefing ──────────────────────────────────────────────────
def send_briefing(chat_id=None):
    if not chat_id:
        chat_id = get_owner()
    if not chat_id:
        return
    goals = get_goals()
    ideas = get_ideas()
    expenses = get_expenses(30)
    clients = get_clients()
    tasks = get_tasks()
    stale = get_stale_clients(14)

    # Check if Friday for Shabbat greeting
    day_result = subprocess.run(["bash", "-c", "TZ=Asia/Jerusalem date +%u"], capture_output=True, text=True)
    day = day_result.stdout.strip()
    if day == "5":
        greeting = "🕯️ *Shabbat Shalom, Tzviki! Here's your briefing before Shabbat:*\n\n"
    elif day == "7":
        greeting = "✨ *Shavua Tov, Tzviki! Here's your weekly briefing:*\n\n"
    else:
        greeting = "☀️ *Good morning, Tzviki! Here's your daily briefing:*\n\n"
    briefing = greeting

    if tasks:
        high = [t for t in tasks if t["priority"] == "high"]
        if high:
            briefing += "🔴 *Urgent tasks:*\n" + "\n".join([f"• {t['task']}" for t in high]) + "\n\n"
        briefing += f"📝 *Total pending tasks:* {len(tasks)}\n\n"

    if goals:
        briefing += "🎯 *Active Goals:*\n" + "\n".join([f"• {g['goal']}" for g in goals]) + "\n\n"

    if expenses:
        total = sum(e["amount"] for e in expenses)
        budget = get_budget()
        briefing += f"💸 *Expenses this month:* ${total:.2f}"
        if budget:
            briefing += f" / ${budget:.2f} budget"
        briefing += "\n\n"

    if stale:
        names = ", ".join([c["name"] for c in stale])
        briefing += f"👋 *Follow up needed:* {names}\n\n"

    if clients:
        briefing += f"👥 *Total clients:* {len(clients)}\n\n"

    if ideas:
        briefing += "💡 *Latest idea:* " + ideas[0]["idea"] + "\n\n"

    briefing += "Have a great day! 🚀"

    send_tg(chat_id, briefing)
    try:
        send_email(BIZ_EMAIL, "Mikey Morning Briefing", briefing.replace("*", "").replace("_", ""), is_business=True)
    except:
        pass

# ── Schedulers ────────────────────────────────────────────────
def check_reminders():
    while True:
        try:
            for reminder in get_due_reminders():
                send_tg(reminder["user_id"], f"⏰ *Reminder:* {reminder['message']}")
                mark_reminder_sent(reminder["id"], reminder.get("recurring"))
        except Exception as e:
            print(f"Reminder error: {e}")
        time.sleep(60)

def briefing_scheduler():
    while True:
        try:
            result = subprocess.run(["bash", "-c", "TZ=Asia/Jerusalem date +%H:%M"],
                                    capture_output=True, text=True)
            if result.stdout.strip() == "08:45":
                owner = get_owner()
                if owner:
                    send_briefing(owner)
                time.sleep(61)
            else:
                time.sleep(30)
        except Exception as e:
            print(f"Briefing error: {e}")
            time.sleep(30)

def weekly_summary_scheduler():
    while True:
        try:
            result = subprocess.run(["bash", "-c", "TZ=Asia/Jerusalem date '+%u %H:%M'"],
                                    capture_output=True, text=True)
            val = result.stdout.strip()
            # Sunday = 7, at 09:00
            if val == "7 09:00":
                owner = get_owner()
                if owner:
                    send_weekly_summary(owner)
                time.sleep(61)
            else:
                time.sleep(30)
        except Exception as e:
            print(f"Weekly summary error: {e}")
            time.sleep(30)

def send_weekly_summary(chat_id):
    goals = get_goals()
    expenses = get_expenses(7)
    tasks = get_tasks()
    total_exp = sum(e["amount"] for e in expenses)

    summary = "📊 *Weekly Summary — Shavua Tov!*\n\n"
    summary += f"💸 *Spent this week:* ${total_exp:.2f}\n"
    summary += f"📝 *Pending tasks:* {len(tasks)}\n"
    summary += f"🎯 *Active goals:* {len(goals)}\n\n"
    summary += "Have a great week! 🚀"

    send_tg(chat_id, summary)
    try:
        send_email(BIZ_EMAIL, "Mikey Weekly Summary", summary.replace("*", "").replace("_", ""), is_business=True)
    except:
        pass

# ── Polling ───────────────────────────────────────────────────
last_update_id = 0

def poll():
    global last_update_id
    print("Eddie v2 is running!")
    while True:
        try:
            with httpx.Client(timeout=35) as client:
                resp = client.get(f"{TG_API}/getUpdates", params={"offset": last_update_id + 1, "timeout": 30})
                updates = resp.json().get("result", [])
            for update in updates:
                last_update_id = update["update_id"]
                try:
                    handle_update(update)
                except Exception as e:
                    print(f"Handle error: {e}")
        except Exception as e:
            print(f"Polling error: {e}")
            time.sleep(5)

# ── Health Server ─────────────────────────────────────────────
class HealthHandler(BaseHTTPRequestHandler):
    def do_GET(self):
        self.send_response(200)
        self.end_headers()
        self.wfile.write(b"Eddie v2 is running!")
    def log_message(self, format, *args):
        pass

if __name__ == "__main__":
    init_db()
    port = int(os.environ.get("PORT", 8080))
    server = HTTPServer(("0.0.0.0", port), HealthHandler)
    print(f"Eddie v2 starting on port {port}")
    threading.Thread(target=poll, daemon=True).start()
    threading.Thread(target=check_reminders, daemon=True).start()
    threading.Thread(target=briefing_scheduler, daemon=True).start()
    threading.Thread(target=weekly_summary_scheduler, daemon=True).start()
    threading.Thread(target=check_price_alerts, daemon=True).start()
    server.serve_forever()

# ── ADDITIONS: Stocks, Currency, News, Jewish Calendar, Contracts, PDF ────────

import urllib.parse

# ── Stock Prices ──────────────────────────────────────────────
def get_stock_price(symbol):
    try:
        symbol = symbol.upper().strip()
        with httpx.Client(timeout=15) as client:
            resp = client.get(f"https://query1.finance.yahoo.com/v8/finance/chart/{symbol}?interval=1d&range=1d")
            data = resp.json()
            price = data["chart"]["result"][0]["meta"]["regularMarketPrice"]
            prev = data["chart"]["result"][0]["meta"]["previousClose"]
            change = price - prev
            pct = (change / prev) * 100
            arrow = "📈" if change >= 0 else "📉"
            return f"{arrow} *{symbol}*: ${price:.2f} ({change:+.2f} / {pct:+.2f}%)"
    except Exception as e:
        return f"Couldn't get price for {symbol}: {e}"

# ── Currency Converter ────────────────────────────────────────
def convert_currency(amount, from_curr, to_curr):
    try:
        from_curr = from_curr.upper()
        to_curr = to_curr.upper()
        with httpx.Client(timeout=15) as client:
            resp = client.get(f"https://open.er-api.com/v6/latest/{from_curr}")
            data = resp.json()
            if data.get("result") == "success":
                rate = data["rates"][to_curr]
                converted = amount * rate
                return f"💱 {amount} {from_curr} = *{converted:.2f} {to_curr}*\n_Rate: 1 {from_curr} = {rate:.4f} {to_curr}_"
            return "Couldn't get exchange rate."
    except Exception as e:
        return f"Currency error: {e}"

# ── News ──────────────────────────────────────────────────────
def get_news(topic="world"):
    try:
        query = urllib.parse.quote(topic)
        with httpx.Client(timeout=15) as client:
            resp = client.get(f"https://api.duckduckgo.com/?q={query}+news&format=json&no_html=1")
            data = resp.json()
            results = []
            for r in data.get("RelatedTopics", [])[:5]:
                if isinstance(r, dict) and r.get("Text"):
                    results.append(f"• {r['Text'][:150]}")
            if results:
                return f"📰 *{topic.title()} News:*\n" + "\n\n".join(results)
            # fallback to web search
            return f"📰 *{topic.title()} News:*\n" + web_search(f"{topic} news today")
    except Exception as e:
        return f"News error: {e}"

# ── Jewish Calendar & Zmanim ──────────────────────────────────
def get_jewish_info():
    try:
        with httpx.Client(timeout=15) as client:
            # Get Hebrew date and parasha
            resp = client.get("https://www.hebcal.com/shabbat?cfg=json&geonameid=281184&M=on")
            data = resp.json()
            items = data.get("items", [])
            
            info = []
            parasha = None
            candles = None
            havdalah = None
            holidays = []
            
            for item in items:
                if item.get("category") == "parashat":
                    parasha = item.get("title", "")
                elif item.get("category") == "candles":
                    candles = item.get("title", "") + " " + item.get("date", "")
                elif item.get("category") == "havdalah":
                    havdalah = item.get("title", "") + " " + item.get("date", "")
                elif item.get("category") == "holiday":
                    holidays.append(item.get("title", ""))
            
            result = "🕍 *Jewish Calendar (Jerusalem):*\n\n"
            
            if parasha:
                result += f"📖 *Parasha:* {parasha}\n"
            if candles:
                result += f"🕯️ *{candles}*\n"
            if havdalah:
                result += f"✨ *{havdalah}*\n"
            if holidays:
                result += f"🎉 *Upcoming:* {', '.join(holidays)}\n"
            
            return result
    except Exception as e:
        return f"Jewish calendar error: {e}"

def get_zmanim():
    try:
        with httpx.Client(timeout=15) as client:
            # Jerusalem zmanim
            resp = client.get("https://www.hebcal.com/zmanim?cfg=json&geonameid=281184&date=now")
            data = resp.json()
            zmanim = data.get("times", {})
            
            def fmt(t):
                if t:
                    return t[11:16]  # Extract HH:MM
                return "N/A"
            
            result = "⏰ *Zmanim for Jerusalem:*\n\n"
            result += f"🌅 Alot Hashachar: {fmt(zmanim.get('alotHaShachar'))}\n"
            result += f"🌄 Sunrise: {fmt(zmanim.get('sunrise'))}\n"
            result += f"📿 Sof Zman Kriat Shema (GRA): {fmt(zmanim.get('sofZmanShmaMGA'))}\n"
            result += f"🙏 Sof Zman Tefila: {fmt(zmanim.get('sofZmanTfilla'))}\n"
            result += f"☀️ Chatzot: {fmt(zmanim.get('chatzot'))}\n"
            result += f"🌆 Mincha Gedola: {fmt(zmanim.get('minchaGedola'))}\n"
            result += f"🌇 Sunset: {fmt(zmanim.get('sunset'))}\n"
            result += f"🌃 Tzait Hakochavim: {fmt(zmanim.get('tzeit7083deg'))}\n"
            
            return result
    except Exception as e:
        return f"Zmanim error: {e}"

# ── Contract Generator ────────────────────────────────────────
def generate_contract(contract_type, details, chat_id):
    prompt = f"""Draft a professional {contract_type} contract based on these details:
{details}

Format it properly with:
- Parties involved
- Terms and conditions  
- Payment terms if applicable
- Signatures section
- Date

Make it legally sound but clear. Use Israeli business context where relevant."""
    
    contract = query_groq([{"role": "user", "content": prompt}], max_tokens=2000)
    
    # Save as text file and send
    try:
        with tempfile.NamedTemporaryFile(mode='w', suffix='.txt', delete=False, encoding='utf-8') as f:
            f.write(contract)
            tmp_path = f.name
        
        with open(tmp_path, 'rb') as f:
            with httpx.Client(timeout=30) as client:
                client.post(f"{TG_API}/sendDocument", 
                           files={"document": (f"{contract_type}_contract.txt", f, "text/plain")},
                           data={"chat_id": chat_id, "caption": f"📄 {contract_type} Contract"})
        return True
    except Exception as e:
        return contract  # Return as text if file sending fails

# ── PDF Filling ───────────────────────────────────────────────
def analyze_pdf_for_filling(pdf_bytes, chat_id):
    """Extract text from PDF and ask Eddie what to fill"""
    try:
        # Save PDF temporarily
        with tempfile.NamedTemporaryFile(suffix='.pdf', delete=False) as f:
            f.write(pdf_bytes)
            tmp_path = f.name
        
        # Extract text using pdftotext if available, otherwise read raw
        result = subprocess.run(['pdftotext', tmp_path, '-'], capture_output=True, text=True, timeout=30)
        if result.returncode == 0:
            pdf_text = result.stdout[:3000]
        else:
            pdf_text = pdf_bytes.decode('utf-8', errors='ignore')[:3000]
        
        prompt = f"""This is a PDF form that needs to be filled out. Analyze it and list:
1. What fields need to be filled
2. What information you need from the user

PDF content:
{pdf_text}

Respond with a clear list of what information is needed to fill this form."""
        
        analysis = query_groq([{"role": "user", "content": prompt}], max_tokens=500)
        return analysis
    except Exception as e:
        return f"PDF analysis error: {e}"


# ── Portfolio Tracker ─────────────────────────────────────────
def save_portfolio_stock(symbol, shares, buy_price):
    try:
        conn = get_db()
        cur = conn.cursor()
        cur.execute("""CREATE TABLE IF NOT EXISTS portfolio (
            id SERIAL PRIMARY KEY,
            symbol TEXT,
            shares REAL,
            buy_price REAL,
            created_at TIMESTAMP DEFAULT NOW()
        )""")
        cur.execute("INSERT INTO portfolio (symbol, shares, buy_price) VALUES (%s, %s, %s)",
                    (symbol.upper(), shares, buy_price))
        conn.commit()
        cur.close()
        conn.close()
        return True
    except Exception as e:
        print(f"Portfolio save error: {e}")
        return False

def get_portfolio():
    try:
        conn = get_db()
        cur = conn.cursor(cursor_factory=RealDictCursor)
        cur.execute("SELECT symbol, shares, buy_price FROM portfolio ORDER BY created_at DESC")
        rows = cur.fetchall()
        cur.close()
        conn.close()
        return rows
    except:
        return []

def get_portfolio_summary():
    holdings = get_portfolio()
    if not holdings:
        return "No stocks in portfolio yet. Say 'add to portfolio: AAPL 10 shares at $150'"
    
    result = "📊 *Your Portfolio:*\n\n"
    total_invested = 0
    total_current = 0
    
    for h in holdings:
        try:
            with httpx.Client(timeout=10) as client:
                resp = client.get(f"https://query1.finance.yahoo.com/v8/finance/chart/{h['symbol']}?interval=1d&range=1d")
                data = resp.json()
                current = data["chart"]["result"][0]["meta"]["regularMarketPrice"]
            
            invested = h["shares"] * h["buy_price"]
            current_val = h["shares"] * current
            pnl = current_val - invested
            pnl_pct = (pnl / invested) * 100
            arrow = "📈" if pnl >= 0 else "📉"
            
            result += f"{arrow} *{h['symbol']}*: {h['shares']} shares\n"
            result += f"   Buy: ${h['buy_price']:.2f} → Now: ${current:.2f}\n"
            result += f"   P&L: ${pnl:+.2f} ({pnl_pct:+.1f}%)\n\n"
            
            total_invested += invested
            total_current += current_val
        except:
            result += f"• *{h['symbol']}*: {h['shares']} shares @ ${h['buy_price']:.2f}\n\n"
    
    total_pnl = total_current - total_invested
    total_pct = (total_pnl / total_invested * 100) if total_invested > 0 else 0
    result += f"*Total invested:* ${total_invested:.2f}\n"
    result += f"*Current value:* ${total_current:.2f}\n"
    result += f"*Total P&L:* ${total_pnl:+.2f} ({total_pct:+.1f}%)"
    
    return result

# ── Price Alerts ──────────────────────────────────────────────
def save_price_alert(user_id, symbol, target_price, direction):
    try:
        conn = get_db()
        cur = conn.cursor()
        cur.execute("""CREATE TABLE IF NOT EXISTS price_alerts (
            id SERIAL PRIMARY KEY,
            user_id BIGINT,
            symbol TEXT,
            target_price REAL,
            direction TEXT,
            triggered BOOLEAN DEFAULT FALSE,
            created_at TIMESTAMP DEFAULT NOW()
        )""")
        cur.execute("INSERT INTO price_alerts (user_id, symbol, target_price, direction) VALUES (%s, %s, %s, %s)",
                    (user_id, symbol.upper(), target_price, direction))
        conn.commit()
        cur.close()
        conn.close()
        return True
    except Exception as e:
        print(f"Alert save error: {e}")
        return False

def check_price_alerts():
    while True:
        try:
            conn = get_db()
            cur = conn.cursor(cursor_factory=RealDictCursor)
            cur.execute("SELECT * FROM price_alerts WHERE triggered = FALSE")
            alerts = cur.fetchall()
            cur.close()
            conn.close()
            
            for alert in alerts:
                try:
                    with httpx.Client(timeout=10) as client:
                        resp = client.get(f"https://query1.finance.yahoo.com/v8/finance/chart/{alert['symbol']}?interval=1d&range=1d")
                        data = resp.json()
                        price = data["chart"]["result"][0]["meta"]["regularMarketPrice"]
                    
                    triggered = False
                    if alert["direction"] == "below" and price <= alert["target_price"]:
                        triggered = True
                        msg = f"🚨 *Price Alert!* {alert['symbol']} dropped to ${price:.2f} (your target: below ${alert['target_price']:.2f})"
                    elif alert["direction"] == "above" and price >= alert["target_price"]:
                        triggered = True
                        msg = f"🚨 *Price Alert!* {alert['symbol']} rose to ${price:.2f} (your target: above ${alert['target_price']:.2f})"
                    
                    if triggered:
                        send_tg(alert["user_id"], msg)
                        conn = get_db()
                        cur = conn.cursor()
                        cur.execute("UPDATE price_alerts SET triggered = TRUE WHERE id = %s", (alert["id"],))
                        conn.commit()
                        cur.close()
                        conn.close()
                except:
                    pass
        except Exception as e:
            print(f"Price alert check error: {e}")
        time.sleep(300)  # Check every 5 minutes

# ── Prediction Markets ────────────────────────────────────────
def get_prediction_markets(topic=""):
    try:
        with httpx.Client(timeout=15) as client:
            if topic:
                resp = client.get(f"https://api.manifold.markets/v0/search-markets?term={urllib.parse.quote(topic)}&limit=5")
            else:
                resp = client.get("https://api.manifold.markets/v0/markets?limit=5&sort=24-hour-vol")
            markets = resp.json()
            
            if not markets:
                return "No prediction markets found."
            
            result = f"🔮 *Prediction Markets{' on ' + topic if topic else ''}:*\n\n"
            for m in markets[:5]:
                prob = m.get("probability", 0) * 100
                result += f"• *{m.get('question', 'Unknown')}*\n"
                result += f"  Probability: *{prob:.0f}%* | Volume: ${m.get('volume', 0):.0f}\n\n"
            return result
    except Exception as e:
        return f"Prediction market error: {e}"

# ── Invoice Generator ─────────────────────────────────────────
def generate_invoice(details, chat_id):
    prompt = f"""Create a professional invoice based on these details:
{details}

Format as a clean text invoice with:
- Invoice number (generate one)
- Date: {datetime.datetime.now().strftime('%B %d, %Y')}
- From: Tzviki Lieberman
- Bill To: [client from details]
- Services/Items with amounts
- Subtotal, VAT (17% Israeli VAT if applicable), Total
- Payment terms
- Bank details placeholder

Make it clean and professional."""
    
    invoice = query_groq([{"role": "user", "content": prompt}], max_tokens=1000)
    
    try:
        with tempfile.NamedTemporaryFile(mode='w', suffix='.txt', delete=False, encoding='utf-8') as f:
            f.write(invoice)
            tmp_path = f.name
        
        with open(tmp_path, 'rb') as f:
            with httpx.Client(timeout=30) as client:
                client.post(f"{TG_API}/sendDocument",
                           files={"document": ("invoice.txt", f, "text/plain")},
                           data={"chat_id": chat_id, "caption": "🧾 Invoice"})
        return True
    except:
        return invoice

# ── Expense Categorizer ───────────────────────────────────────
def categorize_expense(description, amount):
    prompt = f"""Categorize this expense into ONE category:
Expense: {description}, Amount: {amount}
Categories: Food, Marketing, Travel, Software, Office, Client Entertainment, Education, Health, Other
Return ONLY the category name, nothing else."""
    return query_groq([{"role": "user", "content": prompt}], max_tokens=10).strip()

# ── Weekly P&L ────────────────────────────────────────────────
def get_weekly_pnl():
    expenses = get_expenses(7)
    total_out = sum(e["amount"] for e in expenses)
    
    # Group by category
    by_cat = {}
    for e in expenses:
        cat = e.get("category", "Other") or "Other"
        by_cat[cat] = by_cat.get(cat, 0) + e["amount"]
    
    result = f"📊 *Weekly P&L Report:*\n\n"
    result += f"💸 *Total Spent:* ${total_out:.2f}\n\n"
    
    if by_cat:
        result += "*By Category:*\n"
        for cat, amt in sorted(by_cat.items(), key=lambda x: x[1], reverse=True):
            result += f"• {cat}: ${amt:.2f}\n"
    
    portfolio = get_portfolio_summary()
    if "No stocks" not in portfolio:
        result += f"\n{portfolio}"
    
    return result

# ── Workout Tracker ───────────────────────────────────────────
def save_workout(workout_type, duration, notes=""):
    try:
        conn = get_db()
        cur = conn.cursor()
        cur.execute("""CREATE TABLE IF NOT EXISTS workouts (
            id SERIAL PRIMARY KEY,
            workout_type TEXT,
            duration INTEGER,
            notes TEXT,
            created_at TIMESTAMP DEFAULT NOW()
        )""")
        cur.execute("INSERT INTO workouts (workout_type, duration, notes) VALUES (%s, %s, %s)",
                    (workout_type, duration, notes))
        conn.commit()
        cur.close()
        conn.close()
        return True
    except Exception as e:
        print(f"Workout save error: {e}")
        return False

def get_workout_stats():
    try:
        conn = get_db()
        cur = conn.cursor(cursor_factory=RealDictCursor)
        cur.execute("SELECT workout_type, duration, notes, created_at FROM workouts ORDER BY created_at DESC LIMIT 10")
        rows = cur.fetchall()
        cur.close()
        conn.close()
        
        if not rows:
            return "No workouts logged yet. Say 'log workout: 45 min run'"
        
        total_this_week = sum(r["duration"] for r in rows if 
                             (datetime.datetime.utcnow() - r["created_at"]).days < 7)
        
        result = f"💪 *Workout Stats:*\n\n"
        result += f"*This week:* {total_this_week} minutes\n\n"
        result += "*Recent workouts:*\n"
        for w in rows[:5]:
            result += f"• {w['workout_type']} — {w['duration']} min\n"
        return result
    except:
        return "Couldn't get workout stats."

# ── Prayer Time Reminders ─────────────────────────────────────
def get_next_prayer_time():
    try:
        with httpx.Client(timeout=15) as client:
            resp = client.get("https://www.hebcal.com/zmanim?cfg=json&geonameid=281184&date=now")
            data = resp.json()
            zmanim = data.get("times", {})
            
            now_result = subprocess.run(["bash", "-c", "TZ=Asia/Jerusalem date +%H:%M"], capture_output=True, text=True)
            now_str = now_result.stdout.strip()
            now_h, now_m = map(int, now_str.split(":"))
            now_mins = now_h * 60 + now_m
            
            prayers = {
                "Shacharit": zmanim.get("sunrise", ""),
                "Mincha": zmanim.get("minchaGedola", ""),
                "Maariv": zmanim.get("tzeit7083deg", "")
            }
            
            result = "🙏 *Next Prayer Times (Jerusalem):*\n"
            for name, t in prayers.items():
                if t:
                    prayer_time = t[11:16]
                    result += f"• {name}: {prayer_time}\n"
            return result
    except Exception as e:
        return f"Prayer time error: {e}"
