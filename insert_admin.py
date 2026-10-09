import os
import pymysql
import bcrypt

def insert_super_admin_local():
    username = "admin"
    nama = "Super Admin DPMP4KB"
    email = "admin@dpmp4kb.magelangkota.go.id"
    password = "admin123"

    password_hash = bcrypt.hashpw(password.encode('utf-8'), bcrypt.gensalt()).decode('utf-8')

    conn = pymysql.connect(
        host="localhost",
        user="root",
        password="",
        database="db_pengaduan",
        port=3306,
        charset="utf8mb4",
        cursorclass=pymysql.cursors.DictCursor,
    )

    try:
        with conn.cursor() as cur:
            cur.execute("SELECT id FROM pengguna WHERE username = %s", (username,))
            existing = cur.fetchone()

            if existing:
                sql = """
                UPDATE pengguna 
                SET nama=%s, email=%s, password_hash=%s, peran='Super Admin', status='Aktif'
                WHERE username=%s
                """
                cur.execute(sql, (nama, email, password_hash, username))
                print(f"Akun super admin '{username}' berhasil diperbarui!")
            else:
                sql = """
                INSERT INTO pengguna (username, nama, email, password_hash, peran, status)
                VALUES (%s, %s, %s, %s, 'Super Admin', 'Aktif')
                """
                cur.execute(sql, (username, nama, email, password_hash))
                print(f"Akun super admin '{username}' berhasil dibuat!")

            conn.commit()
            print(f"Username: {username}")
            print(f"Password: {password}")
    except Exception as e:
        print(f"Gagal: {e}")
    finally:
        conn.close()

if __name__ == "__main__":
    insert_super_admin_local()
