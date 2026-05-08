import os
from pymongo import MongoClient, ASCENDING

class Database:
    def __init__(self):
        url = os.environ.get('MONGODB_URI')
        if not url:
            raise RuntimeError("MONGODB_URI environment variable belum di-set")
        self.client = MongoClient(url)
        self.db = self.client['db_sholat']
        self.collection = self.db['checklist_harian']
        self.collection.create_index(
            [("uid", ASCENDING), ("tanggal", ASCENDING)],
            unique=True,
        )

    def sholat(self, tanggal, subuh, dzuhur, ashar, maghrib, isya, uid, email):
        if not uid:
            return False, "UID tidak valid"
        result = self.collection.update_one(
            {"uid": uid, "tanggal": str(tanggal)},
            {"$set": {
                "email": email,
                "tanggal": str(tanggal),
                "sholat": {
                    "subuh": bool(subuh),
                    "dzuhur": bool(dzuhur),
                    "ashar": bool(ashar),
                    "maghrib": bool(maghrib),
                    "isya": bool(isya),
                },
            }},
            upsert=True,
        )
        if result.upserted_id:
            return True, "Laporan hari ini berhasil disimpan!"
        return True, "Laporan hari ini berhasil diperbarui!"
