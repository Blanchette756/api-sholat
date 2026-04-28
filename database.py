import os
from pymongo import MongoClient

class Database:
    def __init__(self):
        url = os.environ.get('MONGODB_URI')
        if not url:
            raise RuntimeError("MONGODB_URI environment variable belum di-set")
        self.client = MongoClient(url)
        self.db = self.client['db_sholat']
        self.collection = self.db['checklist_harian']

    def sholat(self, nama, tanggal, subuh, dzuhur, ashar, maghrib, isya, uid='', email=''):
        if not isinstance(nama, str) or len(nama.strip()) < 3:
            return False, "Nama harus string minimal 3 karakter"
        nama = nama.strip()[:100]
        data = {
            "uid": uid,
            "email": email,
            "nama_siswa": nama,
            "tanggal": str(tanggal),
            "sholat": {
                "subuh": bool(subuh),
                "dzuhur": bool(dzuhur),
                "ashar": bool(ashar),
                "maghrib": bool(maghrib),
                "isya": bool(isya)
            }
        }
        self.collection.insert_one(data)
        return True, "Sukses mendarat di MongoDB Atlas!"
