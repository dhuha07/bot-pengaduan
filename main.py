import json
import os
import time
import pymysql
import requests
from agent import compiled_agent
from dotenv import load_dotenv
from fastapi import FastAPI, HTTPException, Request
from pydantic import BaseModel

# 1. Load Environment Variables
load_dotenv()

app = FastAPI(title="AMARA Bot Service")


# 2. Middleware Response Time
@app.middleware("http")
async def add_process_time_header(request: Request, call_next):
    start_time = time.perf_counter()
    response = await call_next(request)
    process_time = time.perf_counter() - start_time
    print(
        f"⏱️ [{request.method}] {request.url.path} - Selesai dalam {process_time:.4f} detik"
    )
    response.headers["X-Process-Time"] = str(process_time)
    return response


# 3. Pydantic Model untuk Testing Swagger UI
class ChatRequest(BaseModel):
    nomor_wa: str
    message: str


# 4. Database Connection Helper
def get_db_connection():
    return pymysql.connect(
        host=os.getenv("DB_HOST", "localhost"),
        user=os.getenv("DB_USER", "root"),
        password=os.getenv("DB_PASSWORD", ""),
        database=os.getenv("DB_NAME", "db_pengaduan"),
        charset="utf8mb4",
        cursorclass=pymysql.cursors.DictCursor,
    )


# 5. Helper Reverse Geocoding GPS (Koordinat -> Alamat Teks)
def get_alamat_dari_koordinat(lat: str, lng: str) -> str:
    try:
        url = f"https://nominatim.openstreetmap.org/reverse?format=json&lat={lat}&lon={lng}"
        headers = {"User-Agent": "AMARA-Bot-App"}
        res = requests.get(url, headers=headers, timeout=5)
        if res.status_code == 200:
            data = res.json()
            return data.get("display_name", f"Koordinat: {lat}, {lng}")
    except Exception as e:
        print(f"[⚠️ REVERSE GEOCODING ERROR]: {e}")
    return f"Koordinat: {lat}, {lng}"


# 6. Helper Simpan / Update Data Pengaduan
def simpan_atau_update_pengaduan(data_json: dict, sender: str):
    try:
        tipe = data_json.get("tipe_pesan", "PENGADUAN")
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
            ON DUPLICATE KEY UPDATE
                nama_pelapor = IF(VALUES(nama_pelapor) != '', VALUES(nama_pelapor), nama_pelapor),
                tipe_pesan = VALUES(tipe_pesan),
                kategori_kasus = IF(VALUES(kategori_kasus) != '', VALUES(kategori_kasus), kategori_kasus),
                detail_kekerasan = IF(VALUES(detail_kekerasan) != '', VALUES(detail_kekerasan), detail_kekerasan),
                pelaku = IF(VALUES(pelaku) != '', VALUES(pelaku), pelaku),
                waktu_kejadian = IF(VALUES(waktu_kejadian) != '', VALUES(waktu_kejadian), waktu_kejadian),
                lokasi_kejadian = IF(VALUES(lokasi_kejadian) != '', VALUES(lokasi_kejadian), lokasi_kejadian),
                tingkat_urgensi = VALUES(tingkat_urgensi),
                status_validasi = VALUES(status_validasi),
                updated_at = CURRENT_TIMESTAMP
            """

            cursor.execute(
                sql,
                (
                    str(sender),
                    data_json.get("nama_pelapor", ""),
                    tipe,
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
        print("[✅ DB PENGADUAN SUCCESS]: Data pengaduan tersimpan/diupdate!")
    except Exception as e:
        print(f"[❌ DB PENGADUAN ERROR]: {e}")


# 7. Helper Pindah ke Tabel Sampah Khusus
def pindahkan_ke_tabel_sampah(nomor_wa: str):
    try:
        conn = get_db_connection()
        with conn.cursor() as cursor:
            sql_copy = """
            INSERT INTO pengaduan_sampah 
            SELECT * FROM pengaduan WHERE nomor_wa = %s
            ON DUPLICATE KEY UPDATE updated_at = CURRENT_TIMESTAMP
            """
            cursor.execute(sql_copy, (str(nomor_wa),))

            sql_delete = "DELETE FROM pengaduan WHERE nomor_wa = %s"
            cursor.execute(sql_delete, (str(nomor_wa),))

            conn.commit()
            print(
                f"[📦 ARCHIVE SUCCESS]: Data {nomor_wa} dipindahkan ke tabel pengaduan_sampah."
            )
        conn.close()
    except Exception as e:
        print(f"[❌ ARCHIVE ERROR]: {e}")


# 8. Helper Logging Chat
def simpan_chat_log(
    nomor_wa: str, pengirim: str, pesan: str, response_time_ms: int = 0
):
    try:
        conn = get_db_connection()
        with conn.cursor() as cursor:
            sql = "INSERT INTO chat_logs (nomor_wa, pengirim, pesan, response_time_ms) VALUES (%s, %s, %s, %s)"
            cursor.execute(
                sql, (str(nomor_wa), pengirim, str(pesan), response_time_ms)
            )
            conn.commit()
        conn.close()
    except Exception as db_err:
        print(f"[❌ DB CHAT LOG ERROR]: {db_err}")


# 9. Core Business Logic Handler
def process_agent_response(
    sender: str, raw_message: str, gps_location_text: str = ""
) -> str:
    start_time = time.time()

    full_user_message = raw_message
    if gps_location_text:
        full_user_message = f"{raw_message} [SISTEM: User mengirim Share Location GPS: {gps_location_text}]"

    config = {"configurable": {"thread_id": str(sender)}}
    input_state = {"messages": [("user", str(full_user_message))]}
    result = compiled_agent.invoke(input_state, config=config)

    ai_reply = result["messages"][-1].content
    text_to_send = ai_reply

    response_time_ms = result.get("response_time_ms")
    if response_time_ms is None:
        response_time_ms = round((time.time() - start_time) * 1000)

    try:
        json_data = json.loads(ai_reply)
        text_to_send = json_data.get("balasan_wa", ai_reply)
        tipe = json_data.get("tipe_pesan", "")

        if gps_location_text:
            json_data["lokasi_kejadian"] = gps_location_text
            json_data["status_validasi"] = "VALID_SIAP_TINDAK"

        simpan_atau_update_pengaduan(json_data, sender)

        if tipe == "SPAM_PRANK":
            pindahkan_ke_tabel_sampah(sender)

    except Exception as parse_err:
        print(f"[⚠️ JSON PARSE WARNING]: {parse_err}")

    simpan_chat_log(sender, "User", raw_message, 0)
    simpan_chat_log(sender, "Bot AI", text_to_send, response_time_ms)

    return text_to_send


# ===================================================================
# ENDPOINT HEALTH CHECK / ROOT
# ===================================================================
@app.get("/")
async def root():
    return {"status": "ok", "message": "AMARA Bot Service Running"}


# ===================================================================
# ENDPOINT 1: TESTING SWAGGER UI (/chat)
# ===================================================================
@app.post("/chat")
async def chat_manual(req: ChatRequest):
    try:
        text_to_send = process_agent_response(
            sender=req.nomor_wa, raw_message=req.message
        )
        return {"status": "success", "reply": text_to_send}
    except Exception as e:
        print(f"[❌ ERROR SWAGGER CHAT]: {str(e)}")
        raise HTTPException(status_code=500, detail=str(e))


# ===================================================================
# ENDPOINT 2: WEBHOOK FONNTE WHATSAPP (/whatsapp)
# ===================================================================
@app.api_route("/whatsapp", methods=["GET", "POST"])
async def whatsapp_webhook(request: Request):
    if request.method == "GET":
        return {"status": "success", "message": "Webhook Whatsapp Fonnte Aktif"}

    try:
        content_type = request.headers.get("content-type", "")
        if "application/json" in content_type:
            data = await request.json()
        else:
            form_data = await request.form()
            data = dict(form_data)

        raw_sender = (
            data.get("sender")
            or data.get("from_sender")
            or data.get("from")
            or ""
        )
        sender = str(raw_sender).split("@")[0].strip()

        raw_msg = (
            data.get("message") or data.get("msg") or data.get("text") or ""
        )
        message = str(raw_msg).strip()

        latitude = data.get("latitude") or data.get("lat")
        longitude = (
            data.get("longitude") or data.get("long") or data.get("lng")
        )

        gps_location_text = ""
        if latitude and longitude:
            gps_location_text = get_alamat_dari_koordinat(
                str(latitude), str(longitude)
            )

        if not sender or (not message and not gps_location_text):
            return {"status": "ignored", "reason": "Data tidak lengkap"}

        print(f"\n[📥 PESAN MASUK FONNTE] Dari: {sender} | Isi: {message}")

        text_to_send = process_agent_response(
            sender=sender,
            raw_message=message,
            gps_location_text=gps_location_text,
        )

        try:
            fonnte_token = os.getenv("FONNTE_TOKEN", "")
            url = "https://api.fonnte.com/send"
            payload = {
                "target": str(sender),
                "message": str(text_to_send),
            }
            headers = {"Authorization": fonnte_token}

            fonnte_response = requests.post(
                url, data=payload, headers=headers, timeout=10
            )
            print(f"[🚀 FONNTE SEND SUCCESS]: {fonnte_response.text}")
        except Exception as fonnte_err:
            print(f"[❌ FONNTE ERROR]: {fonnte_err}")

        return {"status": "processed"}

    except Exception as e:
        print(f"\n[❌ ERROR GLOBAL WEBHOOK]: {str(e)}\n")
        return {"status": "error", "detail": str(e)}