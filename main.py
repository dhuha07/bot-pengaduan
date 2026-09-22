import json
import os
import time
import pymysql
import requests
from dotenv import load_dotenv
from fastapi import FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel

load_dotenv()

app = FastAPI(title="AMARA Bot Service")

# CORS agar Vercel bisa akses tanpa terhalang browser
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)


class ChatRequest(BaseModel):
    nomor_wa: str
    message: str


def get_db_connection():
    # Timeout dipatok ketat 3 detik agar server tidak gantung
    return pymysql.connect(
        host=os.getenv("DB_HOST", "localhost"),
        user=os.getenv("DB_USER", "root"),
        password=os.getenv("DB_PASSWORD", ""),
        database=os.getenv("DB_NAME", "db_pengaduan"),
        port=int(os.getenv("DB_PORT", "13306")),
        charset="utf8mb4",
        connect_timeout=3,
        cursorclass=pymysql.cursors.DictCursor,
    )


def simpan_atau_update_pengaduan(data_json: dict, sender: str):
    try:
        conn = get_db_connection()
        with conn.cursor() as cursor:
            detail = data_json.get("detail_kekerasan", [])
            detail_str = (
                ", ".join(detail) if isinstance(detail, list) else str(detail)
            )
            sql = """
            INSERT INTO pengaduan (
                nomor_wa, nama_pelapor, tipe_pesan, kategori_kasus, 
                detail_kekerasan, pelaku, waktu_kejadian, lokasi_kejadian, 
                tingkat_urgensi, status_validasi
            ) VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
            ON DUPLICATE KEY UPDATE updated_at = CURRENT_TIMESTAMP
            """
            cursor.execute(
                sql,
                (
                    str(sender),
                    data_json.get("nama_pelapor", ""),
                    data_json.get("tipe_pesan", "PENGADUAN"),
                    data_json.get("kategori_kasus", ""),
                    detail_str,
                    data_json.get("pelaku", ""),
                    data_json.get("waktu_kejadian", ""),
                    data_json.get("lokasi_kejadian", ""),
                    data_json.get("tingkat_urgensi", ""),
                    data_json.get("status_validasi", ""),
                ),
            )
            conn.commit()
        conn.close()
    except Exception as e:
        print(f"[⚠️ DB WARNING]: {e}")


def process_agent_response(sender: str, raw_message: str) -> str:
    try:
        # Lazy Import AI Agent agar WSGI tidak gantung saat startup
        from agent import compiled_agent

        config = {"configurable": {"thread_id": str(sender)}}
        input_state = {"messages": [("user", str(raw_message))]}
        result = compiled_agent.invoke(input_state, config=config)
        ai_reply = result["messages"][-1].content

        try:
            json_data = json.loads(ai_reply)
            text_to_send = json_data.get("balasan_wa", ai_reply)
            simpan_atau_update_pengaduan(json_data, sender)
            return text_to_send
        except Exception:
            return ai_reply
    except Exception as ai_err:
        print(f"[❌ AI ERROR]: {ai_err}")
        return "Maaf, sistem AI sedang mengalami kendala jaringan. Silakan coba lagi."


@app.get("/")
async def root():
    return {
        "status": "online",
        "service": "AMARA Bot API",
        "version": "1.0.0",
    }


@app.post("/chat")
async def chat_manual(req: ChatRequest):
    text_to_send = process_agent_response(
        sender=req.nomor_wa, raw_message=req.message
    )
    return {"status": "success", "reply": text_to_send}


@app.api_route("/whatsapp", methods=["GET", "POST"])
async def whatsapp_webhook(request: Request):
    if request.method == "GET":
        return {"status": "success", "message": "Webhook Active"}

    try:
        content_type = request.headers.get("content-type", "")
        if "application/json" in content_type:
            data = await request.json()
        else:
            form_data = await request.form()
            data = dict(form_data)

        sender = str(data.get("sender", "")).split("@")[0].strip()
        message = str(data.get("message", "")).strip()

        if not sender or not message:
            return {"status": "ignored"}

        text_to_send = process_agent_response(sender, message)

        # Kirim Balasan ke Fonnte
        fonnte_token = os.getenv("FONNTE_TOKEN", "")
        if fonnte_token:
            requests.post(
                "https://api.fonnte.com/send",
                data={"target": str(sender), "message": str(text_to_send)},
                headers={"Authorization": fonnte_token},
                timeout=5,
            )
        return {"status": "processed"}
    except Exception as e:
        return {"status": "error", "detail": str(e)}