import datetime
import json
import os
import re
import time
import bcrypt
import jwt
import pymysql
import requests
from dotenv import load_dotenv
from fastapi import FastAPI, Request, HTTPException, Response
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel
from typing import Optional

load_dotenv()

app = FastAPI(title="AMARA Bot Service", redirect_slashes=True)

# Alamat dasbor yang diizinkan, pisahkan dengan koma. Contoh: http://localhost:5173
ALLOWED_ORIGINS = ["*"]  # Perlonggar sementara untuk tes koneksi

app.add_middleware(
    CORSMiddleware,
    allow_origins=ALLOWED_ORIGINS,
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# Isi false jika ada kode lain (mis. api/index.py) yang SUDAH menulis ke chat_logs
LOG_DI_MAIN = os.getenv("LOG_DI_MAIN", "true").lower() == "true"
# Pesan dalam rentang ini dianggap kelanjutan pengaduan yang sama
PENGADUAN_JENDELA_JAM = int(os.getenv("PENGADUAN_JENDELA_JAM", "24"))

# JWT Secret untuk autentikasi
JWT_SECRET = os.getenv("JWT_SECRET", "amara-secret-key-ganti-produksi")
JWT_ALGORITHM = "HS256"
JWT_EXPIRE_HOURS = 24


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
    """Koneksi MySQL. Fallback ke localhost jika host utama gagal."""
    host = os.getenv("DB_HOST", "localhost")
    user = os.getenv("DB_USER", "root")
    password = os.getenv("DB_PASSWORD", "")
    database = os.getenv("DB_NAME", "db_pengaduan")
    port = int(os.getenv("DB_PORT", "3306"))

    try:
        return pymysql.connect(
            host=host,
            user=user,
            password=password,
            database=database,
            port=port,
            charset="utf8mb4",
            connect_timeout=5,
            cursorclass=pymysql.cursors.DictCursor
        )
    except Exception as e:
        print(f"[DB ERROR] Gagal ke {host}, mencoba localhost: {e}")
        return pymysql.connect(
            host="localhost",
            user="root",
            password="",
            database="db_pengaduan",
            port=3306,
            charset="utf8mb4",
            connect_timeout=5,
            cursorclass=pymysql.cursors.DictCursor
        )


def clean_text(raw: str) -> str:
    """Buang code fence markdown dan karakter invisible/spasi siluman."""
    if not isinstance(raw, str):
        return raw
    raw = raw.strip()
    raw = re.sub(r"^```(?:json)?\s*", "", raw)
    raw = re.sub(r"\s*```$", "", raw)
    raw = raw.replace("\u200b", "")
    raw = raw.replace("\u200c", "")
    raw = raw.replace("\u200d", "")
    raw = raw.replace("\ufeff", "")
    raw = raw.replace("\xa0", " ")
    raw = raw.replace("\u2028", " ")
    raw = raw.replace("\u2029", " ")
    return raw.strip()


# ---------------------------------------------------------------
# Pencatatan pesan dan mode percakapan
# ---------------------------------------------------------------
def log_chat(nomor_wa: str, pengirim: str, pesan: str, response_ms: Optional[int] = None):
    """Catat pesan ke chat_logs. pengirim: user | bot | admin."""
    if not LOG_DI_MAIN:
        return
    try:
        conn = get_db_connection()
        with conn.cursor() as cursor:
            cursor.execute(
                "INSERT INTO chat_logs (nomor_wa, pengirim, pesan, response_time_ms) "
                "VALUES (%s, %s, %s, %s)",
                (str(nomor_wa), pengirim, pesan, response_ms),
            )
            conn.commit()
        conn.close()
    except Exception as e:
        print(f"[DB WARNING log_chat]: {e}")


def get_mode(nomor_wa: str) -> str:
    """Mode percakapan: 'bot' (bawaan) atau 'petugas'."""
    try:
        conn = get_db_connection()
        with conn.cursor() as cursor:
            cursor.execute("SELECT mode FROM kontak WHERE nomor_wa = %s", (str(nomor_wa),))
            row = cursor.fetchone()
        conn.close()
        return row["mode"] if row else "bot"
    except Exception as e:
        print(f"[DB WARNING get_mode]: {e}")
        return "bot"


def set_mode(nomor_wa: str, mode: str):
    conn = get_db_connection()
    with conn.cursor() as cursor:
        cursor.execute(
            "INSERT INTO kontak (nomor_wa, mode) VALUES (%s, %s) "
            "ON DUPLICATE KEY UPDATE mode = VALUES(mode)",
            (str(nomor_wa), mode),
        )
        conn.commit()
    conn.close()


def kirim_fonnte(nomor_wa: str, teks: str) -> bool:
    """Kirim pesan WhatsApp lewat Fonnte. Token hanya dibaca dari .env server."""
    token = os.getenv("FONNTE_TOKEN", "")
    if not token:
        print("[FONNTE] Token belum diisi di .env")
        return False
    try:
        r = requests.post(
            "https://api.fonnte.com/send",
            data={"target": str(nomor_wa), "message": str(teks)},
            headers={"Authorization": token},
            timeout=8,
        )
        return r.status_code == 200
    except Exception as e:
        print(f"[FONNTE ERROR]: {e}")
        return False


# ---------------------------------------------------------------
# Penyimpanan pengaduan (satu nomor boleh punya banyak pengaduan)
# ---------------------------------------------------------------
# Nilai yang diizinkan oleh kolom ENUM di database
_ENUM_TIPE = {"PENGADUAN", "ADMINISTRASI_NON_PENGADUAN", "IRRELEVANT_OUT_OF_DOMAIN", "SPAM_PRANK"}
_ENUM_URGENSI = {"DARURAT", "NORMAL", "NON_PENGADUAN"}
_ENUM_VALIDASI = {"VALID_SIAP_TINDAK", "UNVERIFIED_NEED_LOCATION", "TERUSKAN_KE_PETUGAS", "IGNORE"}


def _enum_aman(nilai, diizinkan, bawaan=""):
    """Nilai di luar daftar ENUM dikosongkan agar tidak error 'Data truncated'."""
    nilai = (nilai or "").strip()
    return nilai if nilai in diizinkan else bawaan


def _nilai_pengaduan(data_json: dict):
    detail = data_json.get("detail_kekerasan", [])
    detail_str = ", ".join(detail) if isinstance(detail, list) else str(detail or "")
    return (
        data_json.get("nama_pelapor", "") or "",
        _enum_aman(data_json.get("tipe_pesan"), _ENUM_TIPE, "PENGADUAN"),
        data_json.get("kategori_kasus", "") or "",
        detail_str,
        data_json.get("pelaku", "") or "",
        data_json.get("waktu_kejadian", "") or "",
        data_json.get("lokasi_kejadian", "") or "",
        _enum_aman(data_json.get("tingkat_urgensi"), _ENUM_URGENSI, ""),
        _enum_aman(data_json.get("status_validasi"), _ENUM_VALIDASI, ""),
    )


def simpan_atau_update_pengaduan(data_json: dict, sender: str):
    try:
        conn = get_db_connection()
        with conn.cursor() as cursor:
            nilai = _nilai_pengaduan(data_json)

            # Cari pengaduan terbuka milik nomor ini dalam jendela waktu
            cursor.execute(
                "SELECT id FROM pengaduan "
                "WHERE nomor_wa = %s AND status_penanganan = 'Diproses' "
                "AND created_at > NOW() - INTERVAL %s HOUR "
                "ORDER BY id DESC LIMIT 1",
                (str(sender), PENGADUAN_JENDELA_JAM),
            )
            terbuka = cursor.fetchone()

            if terbuka:
                # Perbarui; isian kosong tidak menimpa data yang sudah ada
                cursor.execute(
                    """
                    UPDATE pengaduan SET
                        nama_pelapor = COALESCE(NULLIF(%s, ''), nama_pelapor),
                        tipe_pesan = COALESCE(NULLIF(%s, ''), tipe_pesan),
                        kategori_kasus = COALESCE(NULLIF(%s, ''), kategori_kasus),
                        detail_kekerasan = COALESCE(NULLIF(%s, ''), detail_kekerasan),
                        pelaku = COALESCE(NULLIF(%s, ''), pelaku),
                        waktu_kejadian = COALESCE(NULLIF(%s, ''), waktu_kejadian),
                        lokasi_kejadian = COALESCE(NULLIF(%s, ''), lokasi_kejadian),
                        tingkat_urgensi = COALESCE(NULLIF(%s, ''), tingkat_urgensi),
                        status_validasi = COALESCE(NULLIF(%s, ''), status_validasi),
                        updated_at = CURRENT_TIMESTAMP
                    WHERE id = %s
                    """,
                    nilai + (terbuka["id"],),
                )
            else:
                cursor.execute(
                    """
                    INSERT INTO pengaduan (
                        nomor_wa, nama_pelapor, tipe_pesan, kategori_kasus,
                        detail_kekerasan, pelaku, waktu_kejadian, lokasi_kejadian,
                        tingkat_urgensi, status_validasi
                    ) VALUES (%s, %s, %s, %s, %s, %s, %s, %s,
                              COALESCE(NULLIF(%s, ''), 'NORMAL'),
                              COALESCE(NULLIF(%s, ''), 'UNVERIFIED_NEED_LOCATION'))
                    """,
                    (str(sender),) + nilai,
                )
                id_baru = cursor.lastrowid
                cursor.execute(
                    "INSERT INTO riwayat_status (id_pengaduan, status, catatan, petugas) "
                    "VALUES (%s, 'Diproses', %s, 'AMARA Bot')",
                    (id_baru, "Laporan diterima melalui AMARA Bot"),
                )
            conn.commit()
        conn.close()
    except Exception as e:
        print(f"[DB WARNING pengaduan]: {e}")


def simpan_sampah(data_json: dict, sender: str):
    """Pesan spam/prank disimpan terpisah, tidak masuk daftar pengaduan."""
    try:
        nilai = _nilai_pengaduan(data_json)
        conn = get_db_connection()
        with conn.cursor() as cursor:
            cursor.execute(
                """
                INSERT INTO pengaduan_sampah (
                    nomor_wa, nama_pelapor, tipe_pesan, kategori_kasus,
                    detail_kekerasan, pelaku, waktu_kejadian, lokasi_kejadian,
                    tingkat_urgensi, status_validasi
                ) VALUES (%s, %s, %s, %s, %s, %s, %s, %s,
                          COALESCE(NULLIF(%s, ''), 'NON_PENGADUAN'),
                          COALESCE(NULLIF(%s, ''), 'IGNORE'))
                ON DUPLICATE KEY UPDATE updated_at = CURRENT_TIMESTAMP
                """,
                (str(sender),) + nilai,
            )
            conn.commit()
        conn.close()
    except Exception as e:
        print(f"[DB WARNING sampah]: {e}")


def process_agent_response(sender: str, raw_message: str) -> str:
    try:
        from agent import compiled_agent

        config = {"configurable": {"thread_id": str(sender)}}
        input_state = {"messages": [("user", str(raw_message))]}
        result = compiled_agent.invoke(input_state, config=config)
        ai_reply = clean_text(result["messages"][-1].content)

        try:
            json_data = json.loads(ai_reply)
            text_to_send = clean_text(json_data.get("balasan_wa", ai_reply))
            tipe = json_data.get("tipe_pesan", "PENGADUAN")

            if tipe == "SPAM_PRANK" or json_data.get("status_validasi") == "IGNORE":
                simpan_sampah(json_data, sender)
            elif tipe != "IRRELEVANT_OUT_OF_DOMAIN":
                # PENGADUAN dan ADMINISTRASI_NON_PENGADUAN
                simpan_atau_update_pengaduan(json_data, sender)
            return text_to_send
        except json.JSONDecodeError as e:
            print(f"[JSON PARSE FAIL]: {e} | raw: {ai_reply!r}")
            return "Maaf, pesan Anda belum dapat kami proses. Mohon kirim ulang dengan kalimat singkat."
    except Exception as ai_err:
        print(f"[AI ERROR]: {ai_err}")
        return "Maaf, sistem AI sedang mengalami kendala jaringan. Silakan coba lagi."


# ---------------------------------------------------------------
# Endpoint
# ---------------------------------------------------------------
@app.get("/")
async def root():
    # Cek status Fonnte
    token = os.getenv("FONNTE_TOKEN", "")
    wa_status = "offline"
    if token:
        try:
            r = requests.post("https://api.fonnte.com/get-devices", headers={"Authorization": token}, timeout=5)
            if r.status_code == 200:
                data = r.json()
                # Jika ada perangkat yang aktif/connected
                if data.get("status") and any(d.get("status") == "connected" for d in data.get("data", [])):
                    wa_status = "online"
        except:
            pass
            
    return {
        "status": "online", 
        "wa_status": wa_status,
        "service": "AMARA Bot API", 
        "version": "1.2.1"
    }


@app.post("/chat")
async def chat_manual(req: ChatRequest):
    text_to_send = process_agent_response(req.nomor_wa, req.message)
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
        message = clean_text(str(data.get("message", "")).strip())

        if not sender or not message:
            return {"status": "ignored"}

        # Catat pesan pelapor
        log_chat(sender, "user", message)

        # Jika sedang ditangani petugas, bot tidak membalas
        if get_mode(sender) == "petugas":
            return {"status": "diteruskan_ke_petugas"}

        mulai = time.time()
        text_to_send = process_agent_response(sender, message)
        durasi_ms = int((time.time() - mulai) * 1000)

        log_chat(sender, "bot", text_to_send, durasi_ms)
        kirim_fonnte(sender, text_to_send)

        return {"status": "processed"}

    except Exception as e:
        return {"status": "error", "detail": str(e)}


# --- Endpoint untuk dasbor admin ---
# PERINGATAN: endpoint di bawah BELUM dilindungi login. Jangan di-deploy
# ke internet sebelum autentikasi dibuat. Pakai hanya di komputer sendiri.
@app.post("/api/chat/kontak/{nomor_wa}/pesan")
async def kirim_pesan_admin(nomor_wa: str, body: PesanAdmin):
    teks = clean_text(body.teks)
    if not teks:
        raise HTTPException(status_code=400, detail="Pesan tidak boleh kosong")

    # Petugas membalas: ambil alih percakapan agar bot berhenti menjawab
    set_mode(nomor_wa, "petugas")
    terkirim = kirim_fonnte(nomor_wa, teks)
    log_chat(nomor_wa, "admin", teks)

    return {
        "status": "terkirim" if terkirim else "gagal",
        "pengirim": "admin",
        "teks": teks,
        "mode": "petugas",
    }


@app.post("/api/chat/kontak/{nomor_wa}/mode")
async def ubah_mode(nomor_wa: str, body: UbahMode):
    if body.mode not in ("bot", "petugas"):
        raise HTTPException(status_code=400, detail="Mode harus 'bot' atau 'petugas'")
    set_mode(nomor_wa, body.mode)
    return {"status": "ok", "mode": body.mode}


# --- Endpoint Chat untuk Dasbor ---
@app.get("/chat/kontak")
async def list_kontak(request: Request):
    validate_token(request)
    conn = get_db_connection()
    try:
        with conn.cursor() as cur:
            cur.execute("""
                SELECT DISTINCT k.nomor_wa, k.mode, 
                       (SELECT COUNT(*) FROM chat_logs WHERE nomor_wa COLLATE utf8mb4_general_ci=k.nomor_wa COLLATE utf8mb4_general_ci AND pengirim='user') as belumDibaca,
                       (SELECT pesan FROM chat_logs WHERE nomor_wa COLLATE utf8mb4_general_ci=k.nomor_wa COLLATE utf8mb4_general_ci ORDER BY created_at DESC LIMIT 1) as pesanTerakhir,
                       (SELECT created_at FROM chat_logs WHERE nomor_wa COLLATE utf8mb4_general_ci=k.nomor_wa COLLATE utf8mb4_general_ci ORDER BY created_at DESC LIMIT 1) as waktu
                FROM kontak k
                ORDER BY waktu DESC
            """)
            rows = cur.fetchall()
            result = []
            for r in rows:
                result.append({
                    "id": r["nomor_wa"],
                    "nama": r["nomor_wa"],
                    "noWa": r["nomor_wa"],
                    "online": r["mode"] == "petugas",
                    "belumDibaca": int(r["belumDibaca"] or 0),
                    "pesanTerakhir": str(r["pesanTerakhir"] or ""),
                    "waktu": r["waktu"].isoformat() if r["waktu"] else None,
                })
            return result
    except Exception as e:
        print(f"[CHAT ERROR] {e}")
        raise HTTPException(status_code=500, detail=str(e))
    finally:
        conn.close()


@app.get("/chat/kontak/{nomor_wa}/pesan")
async def get_pesan_kontak(request: Request, nomor_wa: str):
    validate_token(request)
    conn = get_db_connection()
    try:
        with conn.cursor() as cur:
            cur.execute("SELECT * FROM chat_logs WHERE nomor_wa=%s ORDER BY created_at ASC", (nomor_wa,))
            rows = cur.fetchall()
            return [
                {
                    "id": str(r["id"]),
                    "pengirim": r["pengirim"],
                    "teks": r["pesan"],
                    "waktu": r["created_at"].isoformat() if r["created_at"] else None,
                    "statusKirim": "diterima" if r["pengirim"] == "user" else "terkirim",
                }
                for r in rows
            ]
    finally:
        conn.close()


@app.post("/chat/kontak/{nomor_wa}/dibaca")
async def mark_read(request: Request, nomor_wa: str):
    validate_token(request)
    return {"status": "ok"}


# --- Autentikasi ---
@app.post("/auth/login")
async def login(req: AuthLogin):
    conn = get_db_connection()
    try:
        with conn.cursor() as cur:
            cur.execute("SELECT * FROM pengguna WHERE username=%s OR email=%s", (req.username, req.username))
            user = cur.fetchone()
            if not user or not bcrypt.checkpw(req.password.encode('utf-8'), user['password_hash'].encode('utf-8')):
                raise HTTPException(status_code=401, detail="Username atau password salah")
            if user['status'] != 'Aktif':
                raise HTTPException(status_code=403, detail=f"Akun berstatus {user['status']}, hubungi Super Admin")
            
            cur.execute("UPDATE pengguna SET terakhir_login=NOW() WHERE id=%s", (user['id'],))
            conn.commit()
            
            payload = {"sub": user['username'], "exp": datetime.datetime.now(datetime.timezone.utc) + datetime.timedelta(hours=JWT_EXPIRE_HOURS)}
            token = jwt.encode(payload, JWT_SECRET, algorithm=JWT_ALGORITHM)
            return {"token": token, "pengguna": {"username": user['username'], "nama": user['nama'], "email": user['email'], "peran": user['peran']}}
    finally:
        conn.close()


@app.post("/auth/daftar")
async def daftar(req: AuthDaftar):
    conn = get_db_connection()
    try:
        with conn.cursor() as cur:
            cur.execute("INSERT INTO pengguna (username, nama, email, password_hash, peran, status) VALUES (%s, %s, %s, %s, 'Petugas', 'Menunggu')", 
                        (req.username, req.nama, req.email, bcrypt.hashpw(req.password.encode('utf-8'), bcrypt.gensalt()).decode('utf-8')))
            conn.commit()
            return {"pesan": "Pendaftaran berhasil, menunggu persetujuan Super Admin."}
    except Exception:
        raise HTTPException(status_code=400, detail="Username atau email sudah terdaftar")
    finally:
        conn.close()


# --- JWT Guard (Bypass untuk kemudahan dev lokal) ---
def validate_token(request: Request):
    return "admin"


# --- Endpoint Dasbor (perlu login) ---
class UpdateStatus(BaseModel):
    status: str
    catatan: str
    petugas: str


@app.get("/api/pengaduan")
async def list_pengaduan(request: Request, status: Optional[str] = None, kategori: Optional[str] = None):
    validate_token(request)
    conn = get_db_connection()
    try:
        with conn.cursor() as cur:
            sql = "SELECT * FROM pengaduan WHERE 1=1"
            params = []
            if status:
                sql += " AND status_penanganan=%s"
                params.append(status)
            if kategori:
                sql += " AND kategori_kasus=%s"
                params.append(kategori)
            sql += " ORDER BY created_at DESC"
            cur.execute(sql, params)
            rows = cur.fetchall()
            result = []
            for r in rows:
                result.append({
                    "id": r["id"],
                    "noWa": r["nomor_wa"],
                    "namaPelapor": r["nama_pelapor"],
                    "tipePesan": r["tipe_pesan"],
                    "kategoriKasus": r["kategori_kasus"],
                    "detailKekerasan": r["detail_kekerasan"],
                    "pelaku": r["pelaku"],
                    "waktuKejadian": r["waktu_kejadian"],
                    "lokasiKejadian": r["lokasi_kejadian"],
                    "tingkatUrgensi": r["tingkat_urgensi"],
                    "statusValidasi": r["status_validasi"],
                    "statusPenanganan": r["status_penanganan"],
                    "createdAt": r["created_at"].isoformat() if r["created_at"] else None,
                    "updatedAt": r["updated_at"].isoformat() if r["updated_at"] else None,
                })
            return {"data": result}
    finally:
        conn.close()


@app.get("/api/pengaduan/{id}")
async def detail_pengaduan(request: Request, id: int):
    validate_token(request)
    conn = get_db_connection()
    try:
        with conn.cursor() as cur:
            cur.execute("SELECT * FROM pengaduan WHERE id=%s", (id,))
            pengaduan = cur.fetchone()
            if not pengaduan:
                raise HTTPException(status_code=404, detail="Pengaduan tidak ditemukan")
            cur.execute("SELECT * FROM riwayat_status WHERE id_pengaduan=%s ORDER BY waktu DESC", (id,))
            riwayat = cur.fetchall()
            return {
                "pengaduan": {
                    "id": pengaduan["id"],
                    "noWa": pengaduan["nomor_wa"],
                    "namaPelapor": pengaduan["nama_pelapor"],
                    "tipePesan": pengaduan["tipe_pesan"],
                    "kategoriKasus": pengaduan["kategori_kasus"],
                    "detailKekerasan": pengaduan["detail_kekerasan"],
                    "pelaku": pengaduan["pelaku"],
                    "waktuKejadian": pengaduan["waktu_kejadian"],
                    "lokasiKejadian": pengaduan["lokasi_kejadian"],
                    "tingkatUrgensi": pengaduan["tingkat_urgensi"],
                    "statusValidasi": pengaduan["status_validasi"],
                    "statusPenanganan": pengaduan["status_penanganan"],
                    "createdAt": pengaduan["created_at"].isoformat() if pengaduan["created_at"] else None,
                },
                "riwayatStatus": [
                    {
                        "id": r["id"],
                        "idPengaduan": r["id_pengaduan"],
                        "status": r["status"],
                        "catatan": r["catatan"],
                        "petugas": r["petugas"],
                        "waktu": r["waktu"].isoformat() if r["waktu"] else None,
                    }
                    for r in riwayat
                ],
            }
    finally:
        conn.close()


@app.post("/api/pengaduan/{id}/status")
async def update_status_pengaduan(request: Request, id: int, body: UpdateStatus):
    validate_token(request)
    conn = get_db_connection()
    try:
        with conn.cursor() as cur:
            cur.execute("UPDATE pengaduan SET status_penanganan=%s, updated_at=NOW() WHERE id=%s", (body.status, id))
            cur.execute("INSERT INTO riwayat_status (id_pengaduan, status, catatan, petugas) VALUES (%s, %s, %s, %s)",
                        (id, body.status, body.catatan, body.petugas))
            conn.commit()
            return {"status": "ok", "pesan": "Status berhasil diupdate"}
    finally:
        conn.close()


@app.get("/api/chat_logs/{nomor_wa}")
async def get_chat_logs(request: Request, nomor_wa: str):
    validate_token(request)
    conn = get_db_connection()
    try:
        with conn.cursor() as cur:
            cur.execute("SELECT * FROM chat_logs WHERE nomor_wa=%s ORDER BY created_at ASC", (nomor_wa,))
            rows = cur.fetchall()
            return {
                "data": [
                    {
                        "id": r["id"],
                        "nomorWa": r["nomor_wa"],
                        "pengirim": r["pengirim"],
                        "pesan": r["pesan"],
                        "responseTimeMs": r["response_time_ms"],
                        "createdAt": r["created_at"].isoformat() if r["created_at"] else None,
                    }
                    for r in rows
                ]
            }
    finally:
        conn.close()


@app.get("/api/laporan")
async def laporan(request: Request, tgl_mulai: Optional[str] = None, tgl_selesai: Optional[str] = None):
    validate_token(request)
    conn = get_db_connection()
    try:
        with conn.cursor() as cur:
            where_clause = "WHERE 1=1"
            params = []
            if tgl_mulai:
                where_clause += " AND DATE(created_at) >= %s"
                params.append(tgl_mulai)
            if tgl_selesai:
                where_clause += " AND DATE(created_at) <= %s"
                params.append(tgl_selesai)

            cur.execute(f"SELECT kategori_kasus, COUNT(*) as jumlah FROM pengaduan {where_clause} GROUP BY kategori_kasus", params)
            rows_k = cur.fetchall()
            kategori = [{"nama": r["kategori_kasus"] or "Lainnya", "jumlah": r["jumlah"]} for r in rows_k]
            
            cur.execute(f"SELECT DATE_FORMAT(created_at, '%%d %%b') as label, COUNT(*) as jumlah FROM pengaduan {where_clause} GROUP BY label ORDER BY MIN(created_at) ASC", params)
            rows_p = cur.fetchall()
            per_periode = [{"label": r["label"], "jumlah": r["jumlah"]} for r in rows_p]
            
            cur.execute(f"SELECT status_penanganan, COUNT(*) as jumlah FROM pengaduan {where_clause} GROUP BY status_penanganan", params)
            rows_s = cur.fetchall()
            status_map = {r["status_penanganan"]: r["jumlah"] for r in rows_s}

            cur.execute(f"SELECT COUNT(*) as jumlah FROM pengaduan {where_clause} AND tingkat_urgensi='DARURAT'", params)
            darurat_count = cur.fetchone()["jumlah"]

            return {
                "total": sum(k["jumlah"] for k in kategori),
                "kategori": kategori,
                "perPeriode": per_periode,
                "status": {
                    "selesai": status_map.get("Selesai", 0),
                    "diproses": status_map.get("Diproses", 0),
                    "darurat": darurat_count,
                    "ditolak": status_map.get("Ditolak", 0)
                },
                "judulGrafik": f"Periode {tgl_mulai or 'Awal'} s.d {tgl_selesai or 'Sekarang'}" if (tgl_mulai or tgl_selesai) else "Pengaduan Keseluruhan"
            }
    finally:
        conn.close()


@app.get("/api/pengguna")
async def list_pengguna(request: Request):
    validate_token(request)
    conn = get_db_connection()
    try:
        with conn.cursor() as cur:
            cur.execute("SELECT id, username, nama, email, peran, status, terakhir_login, created_at FROM pengguna ORDER BY created_at DESC")
            rows = cur.fetchall()
            return {
                "data": [
                    {
                        "id": r["id"],
                        "username": r["username"],
                        "nama": r["nama"],
                        "email": r["email"],
                        "peran": r["peran"],
                        "status": r["status"],
                        "terakhirLogin": r["terakhir_login"].isoformat() if r["terakhir_login"] else None,
                        "createdAt": r["created_at"].isoformat() if r["created_at"] else None,
                    }
                    for r in rows
                ]
            }
    finally:
        conn.close()


@app.post("/api/pengguna")
async def tambah_pengguna_api(request: Request, body: dict):
    validate_token(request)
    conn = get_db_connection()
    try:
        with conn.cursor() as cur:
            nama = body.get("nama", "").strip()
            email = body.get("email", "").strip()
            peran = body.get("peran", "Petugas").strip()
            password = body.get("password", "password123").strip()
            username = body.get("username", "").strip() or email.split("@")[0].replace(".", "_")
            password_hash = bcrypt.hashpw(password.encode('utf-8'), bcrypt.gensalt()).decode('utf-8')
            
            cur.execute("SELECT id FROM pengguna WHERE email=%s OR username=%s", (email, username))
            if cur.fetchone():
                raise HTTPException(status_code=400, detail="Email atau username sudah digunakan")
                
            cur.execute("""
                INSERT INTO pengguna (username, nama, email, password_hash, peran, status)
                VALUES (%s, %s, %s, %s, %s, 'Aktif')
            """, (username, nama, email, password_hash, peran))
            conn.commit()
            id_baru = cur.lastrowid
            
            return {
                "id": id_baru,
                "username": username,
                "nama": nama,
                "email": email,
                "peran": peran,
                "status": "Aktif",
                "terakhirLogin": None,
                "createdAt": datetime.datetime.now().isoformat()
            }
    finally:
        conn.close()


@app.put("/api/pengguna/{id}")
async def ubah_pengguna_api(request: Request, id: int, body: dict):
    validate_token(request)
    conn = get_db_connection()
    try:
        with conn.cursor() as cur:
            nama = body.get("nama", "").strip()
            email = body.get("email", "").strip()
            peran = body.get("peran", "Petugas").strip()
            
            cur.execute("UPDATE pengguna SET nama=%s, email=%s, peran=%s WHERE id=%s", (nama, email, peran, id))
            conn.commit()
            
            cur.execute("SELECT id, username, nama, email, peran, status, terakhir_login, created_at FROM pengguna WHERE id=%s", (id,))
            user = cur.fetchone()
            return {
                "id": user["id"],
                "username": user["username"],
                "nama": user["nama"],
                "email": user["email"],
                "peran": user["peran"],
                "status": user["status"],
                "terakhirLogin": user["terakhir_login"].isoformat() if user["terakhir_login"] else None,
                "createdAt": user["created_at"].isoformat() if user["created_at"] else None,
            }
    finally:
        conn.close()


@app.delete("/api/pengguna/{id}")
async def hapus_pengguna_api(request: Request, id: int):
    validate_token(request)
    conn = get_db_connection()
    try:
        with conn.cursor() as cur:
            cur.execute("DELETE FROM pengguna WHERE id=%s", (id,))
            conn.commit()
            return {"status": "ok", "pesan": "Pengguna berhasil dihapus"}
    finally:
        conn.close()


@app.patch("/api/pengguna/{id}/status")
async def ubah_status_pengguna(request: Request, id: int, body: dict):
    validate_token(request)
    conn = get_db_connection()
    try:
        with conn.cursor() as cur:
            status = body.get("status", "")
            if status not in ("Aktif", "Nonaktif"):
                raise HTTPException(status_code=400, detail="Status harus 'Aktif' atau 'Nonaktif'")
            cur.execute("UPDATE pengguna SET status=%s WHERE id=%s", (status, id))
            conn.commit()
            cur.execute("SELECT id, username, nama, email, peran, status, terakhir_login, created_at FROM pengguna WHERE id=%s", (id,))
            user = cur.fetchone()
            if not user:
                raise HTTPException(status_code=404, detail="Pengguna tidak ditemukan")
            return {
                "id": user["id"],
                "username": user["username"],
                "nama": user["nama"],
                "email": user["email"],
                "peran": user["peran"],
                "status": user["status"],
                "terakhirLogin": user["terakhir_login"].isoformat() if user["terakhir_login"] else None,
                "createdAt": user["created_at"].isoformat() if user["created_at"] else None,
            }
    finally:
        conn.close()


@app.post("/api/pengguna/{id}/reset-password")
async def reset_password(request: Request, id: int, body: dict):
    validate_token(request)
    conn = get_db_connection()
    try:
        with conn.cursor() as cur:
            password = body.get("password", "").strip()
            if len(password) < 6:
                raise HTTPException(status_code=400, detail="Password minimal 6 karakter")
            password_hash = bcrypt.hashpw(password.encode('utf-8'), bcrypt.gensalt()).decode('utf-8')
            cur.execute("UPDATE pengguna SET password_hash=%s WHERE id=%s", (password_hash, id))
            conn.commit()
            return {"status": "ok", "pesan": "Password berhasil direset"}
    finally:
        conn.close()