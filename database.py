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
            
        from datetime import datetime, timedelta, timezone
        wita = timezone(timedelta(hours=8))
        waktu_sekarang = datetime.now(wita).strftime('%H:%M')

        update_fields = {
            f"sholat.{prayer}": bool(checked),
            "email": email,
            "nama": nama,
        }
        
        # Rekam jam ceklis hanya jika statusnya dicentang (True)
        if checked:
            update_fields[f"waktu_ceklis.{prayer}"] = waktu_sekarang

        self.collection.update_one(
            {"uid": uid, "tanggal": tanggal},
            {"$set": update_fields},
            upsert=True,
        )
        return True, "OK"
    
    def get_statistik_lomba(self, start_date='', end_date=''):
        """Menghitung total sholat dan merekap waktu ceklis untuk penentuan juara.
        Jika start_date dan end_date kosong, ambil semua data (keseluruhan)."""
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
            return {"sholat": {p: False for p in VALID_PRAYERS}, "finalized": False, "nama": ""}
        sholat = doc.get("sholat", {})
        for p in VALID_PRAYERS:
            if p not in sholat:
                sholat[p] = False
        return {
            "sholat": sholat,
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
        """Simpan alasan sholat yang terlewat (dikirim saat layar EOD jam 22:00)."""
        reason = reason.strip()[:500]  # batasi 500 karakter
        result = self.collection.update_one(
            {"uid": uid, "tanggal": tanggal},
            {"$set": {"alasan": reason}},
            upsert=False,
        )
        return result.matched_count > 0

    def get_weekly_summary(self, date_from='', date_to=''):
        """
        Kembalikan ringkasan mingguan per siswa.
        Setiap entry: { nama, email, minggu (YYYY-Www), total_hari, total_sholat, max_sholat, detail: [...] }
        """
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

        # group by (uid, iso_week)
        from collections import defaultdict
        groups = defaultdict(lambda: {"nama": "", "email": "", "hari": {}})
        for doc in docs:
            uid = doc.get("uid", doc.get("email", ""))
            tanggal_str = doc.get("tanggal", "")
            try:
                d = date_cls.fromisoformat(tanggal_str)
                iso = d.isocalendar()
                week_key = f"{iso.year}-W{str(iso.week).zfill(2)}"
                # Senin minggu ini
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
            total = sum(1 for v in sholat.values() if v)
            groups[key]["hari"][tanggal_str] = {
                "tanggal": tanggal_str,
                "total": total,
                "sholat": sholat,
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
        """Ambil semua data sholat hari ini, urutkan dari yang paling banyak sholat."""
        docs = list(self.collection.find(
            {"tanggal": tanggal},
            {"_id": 0, "nama": 1, "sholat": 1, "uid": 1}
        ))
        result = []
        for doc in docs:
            nama = doc.get("nama", "")
            uid = doc.get("uid", "")
            sholat = doc.get("sholat", {})
            total = sum(1 for v in sholat.values() if v)
            result.append({
                "nama": nama,
                "uid": uid,
                "total": total,
                "sholat": {p: sholat.get(p, False) for p in VALID_PRAYERS},
            })
        result.sort(key=lambda x: (-x["total"], x["nama"]))
        return result

    def get_monthly_summary(self, date_from='', date_to=''):
        """Ringkasan bulanan per siswa."""
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
            total = sum(1 for v in sholat.values() if v)
            groups[key]["hari"][tanggal_str] = {
                "tanggal": tanggal_str,
                "total": total,
                "sholat": sholat,
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
