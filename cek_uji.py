import os, pymysql
from dotenv import load_dotenv
load_dotenv()
conn=pymysql.connect(host=os.getenv('DB_HOST'),port=int(os.getenv('DB_PORT')),user=os.getenv('DB_USER'),password=os.getenv('DB_PASSWORD'),database=os.getenv('DB_NAME'),ssl={'ca': os.getenv('DB_SSL_CA')},cursorclass=pymysql.cursors.DictCursor)
cur=conn.cursor()
cur.execute("SELECT id, nomor_wa, kategori_kasus, lokasi_kejadian, status_validasi, status_penanganan FROM pengaduan WHERE nomor_wa='628000000001' ORDER BY id DESC")
print('pengaduan:', cur.fetchall())
cur.execute("SELECT r.* FROM riwayat_status r JOIN pengaduan p ON p.id=r.id_pengaduan WHERE p.nomor_wa='628000000001'")
print('riwayat:', cur.fetchall())
cur.execute("SELECT COUNT(*) AS c FROM chat_logs WHERE nomor_wa='628000000001'")
print('chat_logs:', cur.fetchone())
conn.close()
