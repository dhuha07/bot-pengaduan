import requests, json, sys
BASE="http://127.0.0.1:8000"
def cek(nama, fn):
    try:
        r=fn()
        print(f"[OK] {nama}: {r}")
        return r
    except Exception as e:
        print(f"[FAIL] {nama}: {e}")
        return None

print("=== TES SISTEM AMARA BOT ===\n")
r=requests.get(f"{BASE}/")
print(f"1. Health: {r.json()} | status {r.status_code}")

r=requests.post(f"{BASE}/auth/login", json={"username":"admin","password":"admin123"})
print(f"2. Login valid: {r.status_code} -> {r.json().get('pengguna')}")
token=r.json().get("token")
h={"Authorization": f"Bearer {token}"} if token else {}

r=requests.post(f"{BASE}/auth/login", json={"username":"admin","password":"salah"})
print(f"3. Login invalid: {r.status_code} (harusnya 401)")

r=requests.get(f"{BASE}/api/pengaduan", headers=h)
print(f"4. List pengaduan: {r.status_code} jumlah={len(r.json().get('data',[]))}")
if r.json().get('data'): print(f"   contoh: {r.json()['data'][0]}")

r=requests.get(f"{BASE}/api/pengaduan/84", headers=h)
print(f"5. Detail pengaduan 84: {r.status_code} -> {r.json().get('pengaduan',{}).get('kategoriKasus')} riwayat={len(r.json().get('riwayatStatus',[]))}")

r=requests.post(f"{BASE}/api/pengaduan/84/status", headers=h, json={"status":"Selesai","catatan":"Tes petugas","petugas":"admin"})
print(f"6. Update status 84->Selesai: {r.status_code} {r.json()}")

r=requests.get(f"{BASE}/api/pengaduan/84", headers=h)
print(f"7. Cek status setelah update: {r.json().get('pengaduan',{}).get('statusPenanganan')}")

r=requests.post(f"{BASE}/api/pengaduan/84/status", headers=h, json={"status":"Diproses","catatan":"Kembalikan","petugas":"admin"})
print(f"8. Kembalikan 84->Diproses: {r.status_code}")

r=requests.get(f"{BASE}/chat/kontak", headers=h)
print(f"9. List kontak chat: {r.status_code} jumlah={len(r.json()) if isinstance(r.json(), list) else 0}")

r=requests.get(f"{BASE}/api/chat_logs/628000000001", headers=h)
print(f"10. Chat logs 628000000001: {r.status_code} jumlah={len(r.json().get('data',[]))}")

r=requests.get(f"{BASE}/chat/kontak/628000000001/pesan", headers=h)
print(f"11. Pesan kontak 628000000001: {r.status_code} jumlah={len(r.json()) if isinstance(r.json(), list) else 0}")

r=requests.get(f"{BASE}/api/laporan", headers=h)
print(f"12. Laporan: {r.status_code} {r.json()}")

r=requests.get(f"{BASE}/api/pengguna", headers=h)
print(f"13. List pengguna: {r.status_code} jumlah={len(r.json().get('data',[]))}")

r=requests.post(f"{BASE}/whatsapp", json={"sender":"628999888777","message":"tolong saya mengalami KDRT tadi malam di Jl. Merdeka No 10"})
print(f"14. Simulasi WA baru: {r.status_code} {r.json()}")

r=requests.get(f"{BASE}/api/pengaduan", headers=h)
baru=[x for x in r.json().get('data',[]) if x['noWa']=='628999888777']
print(f"15. Verifikasi pengaduan baru 628999888777: ditemukan={len(baru)>0} {baru[0] if baru else ''}")

r=requests.post(f"{BASE}/whatsapp", json={"sender":"628999888777","message":"pelaku suami saya"})
print(f"16. Lanjutan WA (update jendela 24jam): {r.status_code} {r.json()}")

r=requests.get(f"{BASE}/api/pengaduan", headers=h)
baru=[x for x in r.json().get('data',[]) if x['noWa']=='628999888777']
print(f"17. Cek update pelaku: {baru[0].get('pelaku') if baru else 'tidak ada'}")

print("\n=== TES SELESAI ===")
