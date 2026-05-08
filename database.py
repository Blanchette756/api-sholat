import os
import logging
from pymongo import MongoClient, ASCENDING

logger = logging.getLogger(__name__)

VALID_PRAYERS = ['subuh', 'dzuhur', 'ashar', 'maghrib', 'isya']


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

    def check_prayer(self, uid, email, nama, tanggal, prayer, checked):
        if prayer not in VALID_PRAYERS:
            return False, "Sholat tidak valid"
        self.collection.update_one(
            {"uid": uid, "tanggal": tanggal},
            {"$set": {
                f"sholat.{prayer}": bool(checked),
                "email": email,
                "nama": nama,
            }},
            upsert=True,
        )
        return True, "OK"

    def get_today_status(self, uid, tanggal):
        doc = self.collection.find_one(
            {"uid": uid, "tanggal": tanggal},
            {"_id": 0}
        )
        if not doc:
            return {"sholat": {p: False for p in VALID_PRAYERS}, "finalized": False, "nama": ""}
        sholat = doc.get("sholat", {})
        for p in VALID_PRAYERS:
            if p not in sholat:
                sholat[p] = False
        return {
            "sholat": sholat,
            "finalized": doc.get("finalized", False),
            "nama": doc.get("nama", ""),
        }

    def finalize_day(self, uid, tanggal):
        result = self.collection.update_one(
            {"uid": uid, "tanggal": tanggal},
            {"$set": {"finalized": True}}
        )
        return result.modified_count > 0 or result.matched_count > 0

    def sholat(self, tanggal, subuh, dzuhur, ashar, maghrib, isya, uid, email, nama=''):
        if not uid:
            return False, "UID tidak valid"
        result = self.collection.update_one(
            {"uid": uid, "tanggal": str(tanggal)},
            {"$set": {
                "email": email,
                "nama": nama,
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

    def get_all_records(self, page=1, limit=50, date_from='', date_to=''):
        query = {}
        if date_from or date_to:
            date_q = {}
            if date_from:
                date_q["$gte"] = date_from
            if date_to:
                date_q["$lte"] = date_to
            query["tanggal"] = date_q
        total = self.collection.count_documents(query)
        skip = (page - 1) * limit
        docs = list(
            self.collection.find(query, {"_id": 0})
            .sort("tanggal", -1)
            .skip(skip)
            .limit(limit)
        )
        return docs, total
