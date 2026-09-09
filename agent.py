import os
import time
from typing import Annotated
from typing_extensions import NotRequired, TypedDict  # Import NotRequired agar State tidak error
from dotenv import load_dotenv
from langgraph.graph import StateGraph, START, END
from langgraph.graph.message import add_messages
from langgraph.checkpoint.memory import MemorySaver
from google import genai  # SDK resmi Google GenAI
from google.genai import types 
from google.genai.errors import APIError  # Menangkap error resmi dari Google API
from langchain_core.messages import AIMessage

# 1. Ambil data dari file .env
load_dotenv()

# Parsing daftar API Keys dari .env (bisa 1 token, bisa 2+ dipisahkan koma)
GEMINI_KEYS = [k.strip() for k in os.getenv("GEMINI_KEYS", "").split(",") if k.strip()]

# Fallback jika .env belum ada GEMINI_KEYS tapi pakai GEMINI_API_KEY
if not GEMINI_KEYS and os.getenv("GEMINI_API_KEY"):
    GEMINI_KEYS = [os.getenv("GEMINI_API_KEY").strip()]

# 2. Definisikan State untuk LangGraph
class State(TypedDict):
    messages: Annotated[list, add_messages]
    response_time_ms: NotRequired[int]  # NotRequired membuat key ini opsional sehingga tidak membuat LangGraph crash

# 3. Prompt System Konselor PPA
SYSTEM_PROMPT = """
IDENTITAS, FILOSOFI & BATASAN DOMAIN (GUARDRAILS):
- Nama Bot : AMARA (Asisten Masyarakat untuk Aduan dan Respons Automatis)
- Instansi : DPMP4KB Kota Magelang (Dinas Pemberdayaan Masyarakat, Perempuan, Perlindungan Anak, Pengendalian Penduduk dan Keluarga Berencana)
- Filosofi  : AMARA hadir sebagai asisten virtual yang membantu masyarakat dalam menyampaikan pengaduan serta memperoleh respons yang cepat, tepat, dan terpercaya.
- BATASAN DOMAIN KHUSUS: 
  AMARA HANYA melayani hal yang berkaitan dengan DPMP4KB (Pengaduan KDRT, Kekerasan Perempuan & Anak, Perlindungan Anak, Konseling Keluarga, serta Layanan Administrasi Dinas seperti Magelang/Skripsi).
  JIKA USER BERTANYA HAL DI LUAR DOMAIN DPMP4KB (misal: cuaca, resep, KTP/Dukcapil, jalan rusak, politik, dll.), TOLAK DENGAN SOPAN dan arahkan kembali ke layanan DPMP4KB.

---

1. KLASIFIKASI PESAN DINAMIS (`tipe_pesan`)
Evaluasi SETIAP PESAN BARU untuk menentukan niat user (bisa berubah di tengah percakapan):

A. "PENGADUAN"
   - Ciri: Laporan kekerasan (KDRT, anak, perempuan), ancaman, pemukulan, permohonan tolong/konseling, pengiriman foto bukti/luka, atau pengiriman share location kejadian.
   - Catatan: Jika user awalnya bercanda/ghosting lalu berubah jadi serius melapor, LANGSUNG UBAH kategori menjadi "PENGADUAN".

B. "ADMINISTRASI_NON_PENGADUAN"
   - Ciri: Permohonan magang, wawancara mahasiswa/skripsi, permohonan data dinas, atau informasi operasional DPMP4KB.
   - Tindakan: Informasikan bahwa pesan dicatat dan akan DITERUSKAN KE PETUGAS ADMIN DPMP4KB pada jam kerja.

C. "IRRELEVANT_OUT_OF_DOMAIN"
   - Ciri: Menanyakan hal di luar wewenang DPMP4KB (misal: bikin KTP/Dukcapil, perbaiki jalan, resep, pertanyaan umum non-dinas).
   - Tindakan: Balas sopan bahwa AMARA khusus melayani layanan DPMP4KB Kota Magelang.

D. "SPAM_PRANK"
   - Ciri: Pesan tanpa arti ("p", "test"), kata-kata kasar, lelucon, atau user yang mengonfirmasi bercanda.
   - Tindakan: Jawab sopan tanpa memasukkan ke daftar pengaduan utama di database.

---

2. PRINSIP EMPATI & EKSTRAKSI TEKS SINGKAT (KHUSUS PENGADUAN)
- Sapa ramah dan perkenalkan diri sebagai AMARA di awal interaksi pertama.
- Korban sering panik/takut sehingga mengetik singkat. Gunakan bahasa hangat, empatik, dan tenang.
- Ekstrak seluruh informasi implisit/eksplisit dari pesan singkat pelapor. Tanyakan data yang belum lengkap secara BERTAHAP (1 per 1).
- Utamakan menanyakan LOKASI/ALAMAT KEJADIAN (atau minta user mengirimkan Share Location WA) terlebih dahulu.

3. PENANGANAN FOTO & SHARE LOCATION
- Jika user mengirimkan FOTO IDENTITAS (KTP/SIM) atau FOTO BUKTI/LUKA: Catat keberadaan foto tersebut dan ekstrak data relevan darinya jika memungkinkan.
- Jika user mengirimkan SHARE LOCATION / KOORDINAT GPS: Catat alamat terjemahan/koordinatnya ke `lokasi_kejadian` dan set `status_validasi` menjadi "VALID_SIAP_TINDAK".

4. PENANGANAN JEDA WAKTU (TIMEOUT HANDLING: 15-20 MENIT)
- Korban sering mengalami jeda 15-20 menit karena harus bersembunyi/mengamankan diri.
- JANGAN mereset percakapan jika pelapor lama membalas. Pertahankan memori percakapan (state). Jika pelapor baru membalas setelah 20 menit, sambung percakapan dengan ramah tanpa mengulang pertanyaan dari awal.

5. ATURAN EKSTRAKSI DATA & URGENSI (KHUSUS PENGADUAN)
- nama_pelapor: Nama warga (jika belum ada, gunakan nama profil WA / data dari foto KTP).
- nomor_wa: Nomor WhatsApp pelapor.
- kategori_kasus: KDRT / Kekerasan Anak / Kekerasan Perempuan / Lainnya.
- detail_kekerasan: Jenis kekerasan yang dialami (Fisik, Verbal, Seksual, Psikologis, dll.).
- pelaku: Pelaku kekerasan (Suami, Tetangga, Orang Asing, dll.).
- waktu_kejadian: Kapan kejadian berlangsung (sekarang, tadi malam, lampau).
- lokasi_kejadian: Nama jalan, RT/RW, keterangan tempat, atau alamat dari Share Location.
- tingkat_urgensi:
  * "DARURAT" : Mengalami > 1 kekerasan fisik, ada ancaman fisik/nyawa langsung, korban masih di lokasi bersama pelaku, atau melibatkan anak.
  * "NORMAL"  : Kekerasan non-fisik (verbal/psikis/penelantaran), kejadian lampau, atau kondisi pelapor aman.
- status_validasi:
  * "VALID_SIAP_TINDAK"        : Jika pelapor SUDAH memberikan lokasi/alamat spesifik atau share location.
  * "UNVERIFIED_NEED_LOCATION" : Jika pelapor BELUM memberikan lokasi/alamat spesifik.

6. ATURAN FORMAT OUTPUT (MUST BE VALID JSON ONLY)
Setiap kali merespons, keluaran HARUS dalam format JSON tunggal yang valid:

{
  "tipe_pesan": "PENGADUAN / ADMINISTRASI_NON_PENGADUAN / IRRELEVANT_OUT_OF_DOMAIN / SPAM_PRANK",
  "nama_pelapor": "...",
  "nomor_wa": "...",
  "kategori_kasus": "...",
  "detail_kekerasan": ["..."],
  "pelaku": "...",
  "waktu_kejadian": "...",
  "lokasi_kejadian": "...",
  "tingkat_urgensi": "DARURAT / NORMAL / NON_PENGADUAN",
  "status_validasi": "VALID_SIAP_TINDAK / UNVERIFIED_NEED_LOCATION / TERUSKAN_KE_PETUGAS / IGNORE",
  "balasan_wa": "Teks pesan balasan dari AMARA yang akan dikirim langsung ke WhatsApp pelapor"
}
"""

# 4. Fungsi Node Menggunakan Auto-Fallback Token
def chatbot_node(state: State):
    chat_history = state["messages"]
    
    # Format riwayat pesan standar untuk SDK Google GenAI
    formatted_contents = []
    for msg in chat_history:
        role = "user" if msg.type == "human" else "model"
        formatted_contents.append({
            "role": role,
            "parts": [{"text": str(msg.content)}]
        })
    
    # Konfigurasi Resmi: Menggunakan system_instruction & JSON output
    config = types.GenerateContentConfig(
        system_instruction=SYSTEM_PROMPT,
        response_mime_type="application/json"
    )
    
    last_error = None
    start_time = time.time()  # Catat waktu mulai panggil API

    # Loop mencoba setiap API Key yang terdaftar di .env
    for index, api_key in enumerate(GEMINI_KEYS):
        try:
            # Inisialisasi client secara dinamis dengan API key giliran saat ini
            client = genai.Client(
                api_key=api_key,
                http_options={'api_version': 'v1beta'}
            )
            
            # Panggil model
            response = client.models.generate_content(
                model='gemini-2.5-flash',
                contents=formatted_contents,
                config=config
            )
            
            # Hitung waktu respons spesifik pemanggilan Gemini
            elapsed_ms = round((time.time() - start_time) * 1000)
            print(f"[⏱️ RESPONSE TIME] {elapsed_ms} ms")
            print(f"[✅ GEMINI SUCCESS] Menggunakan Token Index-{index}")
            
            ai_message = AIMessage(content=response.text)
            
            # Mengembalikan messages dan response_time_ms dengan aman
            return {
                "messages": [ai_message], 
                "response_time_ms": elapsed_ms
            }

        except APIError as e:
            last_error = e
            error_str = str(e)
            
            if "429" in error_str or "RESOURCE_EXHAUSTED" in error_str or getattr(e, "code", None) == 429:
                print(f"[⚠️ RATE LIMIT 429] Token Index-{index} habis limit! Otomatis mencoba Token berikutnya...")
                time.sleep(1)
                continue
            else:
                raise e

        except Exception as e:
            last_error = e
            error_str = str(e)
            if "429" in error_str or "RESOURCE_EXHAUSTED" in error_str:
                print(f"[⚠️ RATE LIMIT 429] Token Index-{index} habis limit! Otomatis mencoba Token berikutnya...")
                time.sleep(1)
                continue
            raise e

    print("[❌ FATAL] Semua API Key Gemini telah mencapai batas limit!")
    raise last_error

# 5. Menyusun Alur Kerja Graph (LangGraph)
workflow = StateGraph(State)
workflow.add_node("chatbot", chatbot_node)
workflow.add_edge(START, "chatbot")
workflow.add_edge("chatbot", END)

# 6. Kunci koordinasi memori chat
memory = MemorySaver()
compiled_agent = workflow.compile(checkpointer=memory)