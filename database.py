import os
import logging
from pymongo import MongoClient, ASCENDING

logger = logging.getLogger(__name__)

class Database:
    def __init__(self):
        url = os.environ.get('MONGODB_URI')
        if not url:
            raise RuntimeError("MONGODB_URI environment variable belum di-set")
        self.client = MongoClient(url, serverSelectionTimeoutMS=5000)
        self.db = self.client['db_sholat']
        self.collection = self.db['checklist_harian']
        self.settings = self.db['settings']
        try:
            self.collection.create_index(
                [("uid", ASCENDING), ("tanggal", ASCENDING)],
                unique=True,
            )
        except Exception as e:
            logger.warning("Gagal membuat index: %s", e)

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

    def get_settings(self):
        doc = self.settings.find_one({"_id": "app_settings"})
        if not doc:
            return {"tolerance_minutes": 30}
        return {"tolerance_minutes": doc.get("tolerance_minutes", 30)}

    def update_tolerance(self, minutes):
        if minutes not in (30, 60):
            return False
        self.settings.update_one(
            {"_id": "app_settings"},
            {"$set": {"tolerance_minutes": minutes}},
            upsert=True,
        )
        return True

    def get_all_records(self):
        docs = list(self.collection.find({}, {"_id": 0}).sort("tanggal", -1))
        return docs
