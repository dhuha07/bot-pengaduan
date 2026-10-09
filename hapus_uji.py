import os, pymysql
from dotenv import load_dotenv
load_dotenv()
conn=pymysql.connect(host=os.getenv('DB_HOST'),port=int(os.getenv('DB_PORT')),user=os.getenv('DB_USER'),password=os.getenv('DB_PASSWORD'),database=os.getenv('DB_NAME'),ssl={'ca': os.getenv('DB_SSL_CA')},cursorclass=pymysql.cursors.DictCursor)
cur=conn.cursor()
cur.execute("DELETE FROM riwayat_status WHERE id_pengaduan IN (SELECT id FROM pengaduan WHERE nomor_wa='628000000001')")
cur.execute("DELETE FROM chat_logs WHERE nomor_wa='628000000001'")
cur.execute("DELETE FROM pengaduan WHERE nomor_wa='628000000001'")
cur.execute("DELETE FROM pengaduan_sampah WHERE nomor_wa='628000000001'")
conn.commit()
print('data uji dihapus')
conn.close()
