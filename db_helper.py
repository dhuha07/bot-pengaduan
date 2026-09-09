import mysql.connector
import os
from dotenv import load_dotenv

load_dotenv()

def get_db_connection():
    return mysql.connector.connect(
        host=os.getenv("DB_HOST", "localhost"),
        user=os.getenv("DB_USER", "root"),
        password=os.getenv("DB_PASSWORD", ""),
        database=os.getenv("DB_NAME", "db_pengaduan")
    )

def simpan_atau_update_pengaduan(data_json: dict):
    if not data_json or not data_json.get("nomor_wa"):
        return

    conn = None
    cursor = None

    try:
        conn = get_db_connection()
        cursor = conn.cursor()

        # Format detail_kekerasan dengan aman
        detail_raw = data_json.get("detail_kekerasan")
        if isinstance(detail_raw, list):
            detail_kekerasan_str = ", ".join([str(x) for x in detail_raw if x])
        elif isinstance(detail_raw, str):
            detail_kekerasan_str = detail_raw
        else:
            detail_kekerasan_str = ""

        # Query UPSERT: Hanya memperbarui kolom jika data baru TIDAK NULL / TIDAK KOSONG
        query = """
        INSERT INTO pengaduan (
            nomor_wa, nama_pelapor, kategori_kasus, detail_kekerasan, 
            pelaku, waktu_kejadian, lokasi_kejadian, tingkat_urgensi, 
            status_validasi, tipe_pesan
        ) VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
        ON DUPLICATE KEY UPDATE
            nama_pelapor = COALESCE(NULLIF(VALUES(nama_pelapor), ''), nama_pelapor),
            kategori_kasus = COALESCE(NULLIF(VALUES(kategori_kasus), ''), kategori_kasus),
            detail_kekerasan = COALESCE(NULLIF(VALUES(detail_kekerasan), ''), detail_kekerasan),
            pelaku = COALESCE(NULLIF(VALUES(pelaku), ''), pelaku),
            waktu_kejadian = COALESCE(NULLIF(VALUES(waktu_kejadian), ''), waktu_kejadian),
            lokasi_kejadian = COALESCE(NULLIF(VALUES(lokasi_kejadian), ''), lokasi_kejadian),
            tingkat_urgensi = COALESCE(NULLIF(VALUES(tingkat_urgensi), ''), tingkat_urgensi),
            status_validasi = COALESCE(NULLIF(VALUES(status_validasi), ''), status_validasi),
            tipe_pesan = COALESCE(NULLIF(VALUES(tipe_pesan), ''), tipe_pesan);
        """

        values = (
            data_json.get("nomor_wa"),
            data_json.get("nama_pelapor") or "",
            data_json.get("kategori_kasus") or "",
            detail_kekerasan_str,
            data_json.get("pelaku") or "",
            data_json.get("waktu_kejadian") or "",
            data_json.get("lokasi_kejadian") or "",
            data_json.get("tingkat_urgensi") or "",
            data_json.get("status_validasi") or "",
            data_json.get("tipe_pesan") or ""
        )

        cursor.execute(query, values)
        conn.commit()
        print(f"[DB SUCCESS] Data untuk {data_json.get('nomor_wa')} berhasil disimpan/diperbarui.")

    except Exception as e:
        print(f"[DB ERROR] Gagal menyimpan ke database: {e}")
    finally:
        if cursor:
            cursor.close()
        if conn and conn.is_connected():
            conn.close()