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

    def check_prayer(self, uid, email, nama, npm, program_studi, tanggal, prayer, checked, jamaah_status=None):
        if prayer not in VALID_PRAYERS:
            return False, "Sholat tidak valid"
            
        from datetime import datetime, timedelta, timezone
        wita = timezone(timedelta(hours=8))
        waktu_sekarang = datetime.now(wita).strftime('%H:%M')

        update_fields = {
            f"sholat.{prayer}": bool(checked),
            "email": email,
            "nama": nama,
            "npm": npm,
            "program_studi": program_studi,
            "haid": False,
        }
        
        if checked:
            update_fields[f"waktu_ceklis.{prayer}"] = waktu_sekarang
            if jamaah_status:
                update_fields[f"jamaah.{prayer}"] = jamaah_status
        else:
            # Bersihkan status jamaah jika sholat di-uncheck
            update_fields[f"jamaah.{prayer}"] = None

        self.collection.update_one(
            {"uid": uid, "tanggal": tanggal},
            {"$set": update_fields},
            upsert=True,
        )
        return True, "OK"
    
    def set_haid(self, uid, email, nama, npm, program_studi, tanggal, is_haid):
        update_fields = {
            "haid": bool(is_haid),
            "email": email,
            "nama": nama,
            "npm": npm,
            "program_studi": program_studi,
            "tanggal": tanggal
        }
        if is_haid:
            # Kosongkan sholat jika haid
            update_fields["sholat"] = {p: False for p in VALID_PRAYERS}
            update_fields["jamaah"] = {}
            update_fields["waktu_ceklis"] = {}
            
        self.collection.update_one(
            {"uid": uid, "tanggal": tanggal},
            {"$set": update_fields},
            upsert=True
        )
        return True, "OK"

    def get_statistik_lomba(self, start_date='', end_date=''):
        match_filter = {}
        if start_date or end_date:
            date_q = {}
            if start_date:
                date_q["$gte"] = start_date
            if end_date:
                date_q["$lte"] = end_date
            match_filter["tanggal"] = date_q

        pipeline = [
            *([{"$match": match_filter}] if match_filter else []),
            {"$group": {
                "_id": "$uid",
                "nama": {"$first": "$nama"},
                "email": {"$first": "$email"},
                "total_sholat": {
                    "$sum": {
                        "$add": [
                            {"$cond": [{"$eq": ["$sholat.subuh", True]}, 1, 0]},
                            {"$cond": [{"$eq": ["$sholat.dzuhur", True]}, 1, 0]},
                            {"$cond": [{"$eq": ["$sholat.ashar", True]}, 1, 0]},
                            {"$cond": [{"$eq": ["$sholat.maghrib", True]}, 1, 0]},
                            {"$cond": [{"$eq": ["$sholat.isya", True]}, 1, 0]}
                        ]
                    }
                },
                "detail_harian": {
                    "$push": {
                        "tanggal": "$tanggal",
                        "sholat": "$sholat",
                        "jamaah": "$jamaah",
                        "haid": "$haid",
                        "waktu_ceklis": "$waktu_ceklis",
                        "alasan": "$alasan"
                    }
                }
            }},
            {"$sort": {"total_sholat": -1, "nama": 1}}
        ]
        return list(self.collection.aggregate(pipeline))

    def get_today_status(self, uid, tanggal):
        doc = self.collection.find_one(
            {"uid": uid, "tanggal": tanggal},
            {"_id": 0}
        )
        if not doc:
            return {"sholat": {p: False for p in VALID_PRAYERS}, "jamaah": {}, "haid": False, "finalized": False, "nama": ""}
        sholat = doc.get("sholat", {})
        for p in VALID_PRAYERS:
            if p not in sholat:
                sholat[p] = False
        return {
            "sholat": sholat,
            "jamaah": doc.get("jamaah", {}),
            "haid": doc.get("haid", False),
            "finalized": doc.get("finalized", False),
            "nama": doc.get("nama", ""),
            "alasan": doc.get("alasan", ""),
        }

    def finalize_day(self, uid, tanggal):
        result = self.collection.update_one(
            {"uid": uid, "tanggal": tanggal},
            {"$set": {"finalized": True}}
        )
        return result.modified_count > 0 or result.matched_count > 0

    def get_settings(self):
        doc = self.settings.find_one({"_id": "app_settings"})
        if not doc:
            return {"tolerance_minutes": 60}
        return {"tolerance_minutes": doc.get("tolerance_minutes", 60)}

    def update_tolerance(self, minutes):
        if minutes not in (60, 120):
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

    def save_reason(self, uid, tanggal, reason):
        reason = reason.strip()[:500] 
        result = self.collection.update_one(
            {"uid": uid, "tanggal": tanggal},
            {"$set": {"alasan": reason}},
            upsert=False,
        )
        return result.matched_count > 0

    def get_weekly_summary(self, date_from='', date_to=''):
        from datetime import date as date_cls, timedelta
        import math

        query = {}
        if date_from or date_to:
            date_q = {}
            if date_from:
                date_q["$gte"] = date_from
            if date_to:
                date_q["$lte"] = date_to
            query["tanggal"] = date_q

        docs = list(self.collection.find(query, {"_id": 0}))
        from collections import defaultdict
        groups = defaultdict(lambda: {"nama": "", "email": "", "hari": {}})
        for doc in docs:
            uid = doc.get("uid", doc.get("email", ""))
            tanggal_str = doc.get("tanggal", "")
            try:
                d = date_cls.fromisoformat(tanggal_str)
                iso = d.isocalendar()
                week_key = f"{iso.year}-W{str(iso.week).zfill(2)}"
                week_start = d - timedelta(days=d.weekday())
                week_label = f"{week_start.strftime('%d %b')} – {(week_start + timedelta(6)).strftime('%d %b %Y')}"
            except Exception:
                continue
            key = (uid, week_key)
            groups[key]["nama"] = doc.get("nama", "")
            groups[key]["email"] = doc.get("email", "")
            groups[key]["minggu"] = week_key
            groups[key]["minggu_label"] = week_label
            sholat = doc.get("sholat", {})
            haid = doc.get("haid", False)
            total = sum(1 for v in sholat.values() if v) if not haid else 0
            groups[key]["hari"][tanggal_str] = {
                "tanggal": tanggal_str,
                "total": total,
                "sholat": sholat,
                "jamaah": doc.get("jamaah", {}),
                "haid": haid,
                "alasan": doc.get("alasan", ""),
            }

        result = []
        for (uid, week_key), g in groups.items():
            hari_list = sorted(g["hari"].values(), key=lambda x: x["tanggal"])
            total_sholat = sum(h["total"] for h in hari_list)
            max_sholat = len(hari_list) * 5
            result.append({
                "uid": uid,
                "nama": g["nama"],
                "email": g["email"],
                "minggu": week_key,
                "minggu_label": g.get("minggu_label", week_key),
                "total_hari": len(hari_list),
                "total_sholat": total_sholat,
                "max_sholat": max_sholat,
                "persen": round(total_sholat / max_sholat * 100) if max_sholat else 0,
                "detail": hari_list,
            })

        result.sort(key=lambda x: (x["minggu"], x["nama"]))
        return result

    def get_today_scoreboard(self, tanggal):
        docs = list(self.collection.find(
            {"tanggal": tanggal},
            {"_id": 0, "nama": 1, "sholat": 1, "uid": 1, "haid": 1, "jamaah": 1}
        ))
        result = []
        for doc in docs:
            nama = doc.get("nama", "")
            uid = doc.get("uid", "")
            haid = doc.get("haid", False)
            sholat = doc.get("sholat", {})
            jamaah = doc.get("jamaah", {})
            total = sum(1 for v in sholat.values() if v) if not haid else 0
            result.append({
                "nama": nama,
                "uid": uid,
                "total": total,
                "haid": haid,
                "sholat": {p: sholat.get(p, False) for p in VALID_PRAYERS},
                "jamaah": jamaah
            })
        result.sort(key=lambda x: (-x["total"], x["nama"]))
        return result

    def get_monthly_summary(self, date_from='', date_to=''):
        from datetime import date as date_cls
        from collections import defaultdict

        query = {}
        if date_from or date_to:
            date_q = {}
            if date_from:
                date_q["$gte"] = date_from
            if date_to:
                date_q["$lte"] = date_to
            query["tanggal"] = date_q

        docs = list(self.collection.find(query, {"_id": 0}))

        groups = defaultdict(lambda: {"nama": "", "email": "", "hari": {}})
        for doc in docs:
            uid = doc.get("uid", doc.get("email", ""))
            tanggal_str = doc.get("tanggal", "")
            try:
                d = date_cls.fromisoformat(tanggal_str)
                month_key = d.strftime("%Y-%m")
                month_label = d.strftime("%B %Y")
            except Exception:
                continue
            key = (uid, month_key)
            groups[key]["nama"] = doc.get("nama", "")
            groups[key]["email"] = doc.get("email", "")
            groups[key]["bulan"] = month_key
            groups[key]["bulan_label"] = month_label
            sholat = doc.get("sholat", {})
            haid = doc.get("haid", False)
            total = sum(1 for v in sholat.values() if v) if not haid else 0
            groups[key]["hari"][tanggal_str] = {
                "tanggal": tanggal_str,
                "total": total,
                "sholat": sholat,
                "jamaah": doc.get("jamaah", {}),
                "haid": haid,
                "alasan": doc.get("alasan", ""),
            }

        result = []
        for (uid, month_key), g in groups.items():
            hari_list = sorted(g["hari"].values(), key=lambda x: x["tanggal"])
            total_sholat = sum(h["total"] for h in hari_list)
            max_sholat = len(hari_list) * 5
            result.append({
                "uid": uid,
                "nama": g["nama"],
                "email": g["email"],
                "bulan": month_key,
                "bulan_label": g.get("bulan_label", month_key),
                "total_hari": len(hari_list),
                "total_sholat": total_sholat,
                "max_sholat": max_sholat,
                "persen": round(total_sholat / max_sholat * 100) if max_sholat else 0,
                "detail": hari_list,
            })

        result.sort(key=lambda x: (x["bulan"], x["nama"]))
        return result