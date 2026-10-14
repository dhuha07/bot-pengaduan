import datetime
import json
import os
import re
import time
import bcrypt
import jwt
import pymysql
import requests
import httpx
from dotenv import load_dotenv
from fastapi import FastAPI, Request, HTTPException, Response
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel
from typing import Optional

load_dotenv()

app = FastAPI(title="AMARA Bot Service", redirect_slashes=True)

# Alamat dasbor yang diizinkan
ALLOWED_ORIGINS = ["*"]

app.add_middleware(
    CORSMiddleware,
    allow_origins=ALLOWED_ORIGINS,
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

@app.middleware("http")
async def add_security_headers(request: Request, call_next):
    response = await call_next(request)
    response.headers["Content-Security-Policy"] = "default-src * 'unsafe-inline' 'unsafe-eval' data: blob:;"
    return response

# Konfigurasi
LOG_DI_MAIN = os.getenv("LOG_DI_MAIN", "true").lower() == "true"
PENGADUAN_JENDELA_JAM = int(os.getenv("PENGADUAN_JENDELA_JAM", "24"))
JWT_SECRET = os.getenv("JWT_SECRET", "amara-secret-key-ganti-produksi")
JWT_ALGORITHM = "HS256"
JWT_EXPIRE_HOURS = 24
FONNTE_TOKEN = "ZynSW6k4CsGSurioBopU"

class AuthLogin(BaseModel):
    username: str
    password: str

class AuthDaftar(BaseModel):
    nama: str
    username: str
    email: str
    password: str

class ChatRequest(BaseModel):
    nomor_wa: str
    message: str

class PesanAdmin(BaseModel):
    teks: str
    petugas: Optional[str] = None

class UbahMode(BaseModel):
    mode: str  # "bot" atau "petugas"

def get_db_connection():
    host = os.getenv("DB_HOST", "localhost")
    user = os.getenv("DB_USER", "root")
    password = os.getenv("DB_PASSWORD", "")
    database = os.getenv("DB_NAME", "db_pengaduan")
    port = int(os.getenv("DB_PORT", "3306"))
    return pymysql.connect(
        host=host, user=user, password=password, database=database, port=port,
        charset="utf8mb4", connect_timeout=5, cursorclass=pymysql.cursors.DictCursor
    )

def clean_text(raw: str) -> str:
    if not isinstance(raw, str): return raw
    raw = raw.strip()
    raw = re.sub(r"^```(?:json)?\s*", "", raw)
    raw = re.sub(r"\s*```$", "", raw)
    return raw.strip()

def log_chat(nomor_wa: str, pengirim: str, pesan: str, response_ms: Optional[int] = None):
    if not LOG_DI_MAIN: return
    try:
        conn = get_db_connection()
        with conn.cursor() as cursor:
            cursor.execute(
                "INSERT INTO chat_logs (nomor_wa, pengirim, pesan, response_time_ms) VALUES (%s, %s, %s, %s)",
                (str(nomor_wa), pengirim, pesan, response_ms),
            )
            conn.commit()
        conn.close()
    except Exception as e: print(f"[DB ERR]: {e}")

def get_mode(nomor_wa: str) -> str:
    try:
        conn = get_db_connection()
        with conn.cursor() as cursor:
            cursor.execute("SELECT mode FROM kontak WHERE nomor_wa = %s", (str(nomor_wa),))
            row = cursor.fetchone()
        conn.close()
        return row["mode"] if row else "bot"
    except: return "bot"

def set_mode(nomor_wa: str, mode: str):
    conn = get_db_connection()
    with conn.cursor() as cursor:
        cursor.execute("INSERT INTO kontak (nomor_wa, mode) VALUES (%s, %s) ON DUPLICATE KEY UPDATE mode = VALUES(mode)", (str(nomor_wa), mode))
        conn.commit()
    conn.close()

def kirim_fonnte(nomor_wa: str, teks: str) -> bool:
    try:
        r = requests.post("https://api.fonnte.com/send", data={"target": str(nomor_wa), "message": str(teks)}, headers={"Authorization": FONNTE_TOKEN}, timeout=8)
        return r.status_code == 200
    except: return False

@app.get("/")
async def root():
    wa_status = "offline"
    try:
        r = requests.post("https://api.fonnte.com/device", headers={"Authorization": FONNTE_TOKEN}, timeout=5)
        if r.status_code == 200 and r.json().get("device_status") == "connect": wa_status = "online"
    except: pass
    return {"status": "online", "wa_status": wa_status, "service": "AMARA Bot API", "version": "1.2.2"}

@app.get("/qr-status")
@app.get("/api/qr-status")
async def qr_status():
    try:
        r = requests.post("https://api.fonnte.com/device", headers={"Authorization": FONNTE_TOKEN}, timeout=5)
        if r.status_code == 200:
            res = r.json()
            if res.get("device_status") == "connect": return {"status": "connected"}
        
        r_qr = requests.post("https://api.fonnte.com/qr", headers={"Authorization": FONNTE_TOKEN}, timeout=10)
        if r_qr.status_code == 200:
            res_qr = r_qr.json()
            qr = res_qr.get("qr") or res_qr.get("url") or res_qr.get("data")
            if qr: return {"status": "scan", "qr_link": qr}
        return {"status": "loading"}
    except: return {"status": "error"}

@app.api_route("/whatsapp", methods=["GET", "POST"])
async def whatsapp_webhook(request: Request):
    if request.method == "GET": return {"status": "success"}
    try:
        data = await request.json() if "application/json" in request.headers.get("content-type", "") else dict(await request.form())
        sender = str(data.get("sender", "")).split("@")[0].strip()
        message = clean_text(str(data.get("message", "")).strip())
        if not sender or not message: return {"status": "ignored"}
        log_chat(sender, "user", message)
        if get_mode(sender) == "petugas": return {"status": "manual"}
        from agent import compiled_agent
        result = compiled_agent.invoke({"messages": [("user", message)]}, config={"configurable": {"thread_id": sender}})
        ai_reply = clean_text(result["messages"][-1].content)
        log_chat(sender, "bot", ai_reply)
        kirim_fonnte(sender, ai_reply)
        return {"status": "ok"}
    except Exception as e: return {"status": "error", "detail": str(e)}

@app.post("/auth/login")
async def login(req: AuthLogin):
    conn = get_db_connection()
    try:
        with conn.cursor() as cur:
            cur.execute("SELECT * FROM pengguna WHERE username=%s OR email=%s", (req.username, req.username))
            user = cur.fetchone()
            if not user or not bcrypt.checkpw(req.password.encode('utf-8'), user['password_hash'].encode('utf-8')):
                raise HTTPException(status_code=401, detail="Username atau password salah")
            cur.execute("UPDATE pengguna SET terakhir_login=NOW() WHERE id=%s", (user['id'],))
            conn.commit()
            token = jwt.encode({"sub": user['username'], "exp": datetime.datetime.now(datetime.timezone.utc) + datetime.timedelta(hours=JWT_EXPIRE_HOURS)}, JWT_SECRET, algorithm=JWT_ALGORITHM)
            return {"token": token, "pengguna": {"username": user['username'], "nama": user['nama'], "peran": user['peran']}}
    finally: conn.close()

# Endpoint lainnya (pengaduan, pengguna, dll) tetap di main.py asli namun dioptimasi
