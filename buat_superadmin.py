import os
import pymysql
import bcrypt
from dotenv import load_dotenv

load_dotenv()

def buat_admin():
    username = input("Username: ").strip()
    nama = input("Nama Lengkap: ").strip()
    email = input("Email: ").strip()
    password = input("Password: ").strip()

    if not username or not password:
        print("Username dan password tidak boleh kosong!")
        return

    password_hash = bcrypt.hashpw(password.encode('utf-8'), bcrypt.gensalt()).decode('utf-8')

    ssl_ca = os.getenv("DB_SSL_CA", "")
    if ssl_ca and not os.path.isabs(ssl_ca):
        ssl_ca = os.path.join(os.path.dirname(os.path.abspath(__file__)), ssl_ca)

    ssl_opt = {"ssl": {"ca": ssl_ca}} if ssl_ca else {}

    conn = pymysql.connect(
        host=os.getenv("DB_HOST", "localhost"),
        user=os.getenv("DB_USER", "root"),
        password=os.getenv("DB_PASSWORD", ""),
        database=os.getenv("DB_NAME", "db_pengaduan"),
        port=int(os.getenv("DB_PORT", "3306")),
        charset="utf8mb4",
        cursorclass=pymysql.cursors.DictCursor,
        **ssl_opt,
    )

    try:
        with conn.cursor() as cur:
            sql = """
            INSERT INTO pengguna (username, nama, email, password_hash, peran, status)
            VALUES (%s, %s, %s, %s, 'Super Admin', 'Aktif')
            """
            cur.execute(sql, (username, nama, email, password_hash))
            conn.commit()
            print(f"Berhasil membuat Super Admin: {username}")
    except Exception as e:
        print(f"Gagal membuat Super Admin: {e}")
    finally:
        conn.close()

if __name__ == "__main__":
    buat_admin()
