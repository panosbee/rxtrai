"""
RxTrail — Graph Memory: Entity Relationship Extraction.

Phase 2 of the Multi-Tier Memory System.

Extracts and queries relationships:
  1. Doctor ↔ Structure (where does this doctor work?)
  2. Doctor ↔ Doctor (colleagues in same structure)
  3. Doctor ↔ Product (prescribing preferences, categories)
  4. Structure ↔ Structure (geo-proximity)

Storage: relationships computed on-the-fly from existing data.
Future: could be persisted as a graph DB (Neo4j) or edge collection.
"""

import logging
from datetime import datetime, timedelta, timezone
from typing import Optional

from bson import ObjectId
from motor.motor_asyncio import AsyncIOMotorDatabase

logger = logging.getLogger(__name__)


# ── Doctor ↔ Structure ──────────────────────────────────────────────────────

async def get_doctor_structures(
    db: AsyncIOMotorDatabase, org_id: str, doctor_name: str,
) -> list[dict]:
    """Get structures where a doctor works."""
    doctor = await db.doctors.find_one({
        "org_id": org_id,
        "name": {"$regex": doctor_name, "$options": "i"},
    })
    if not doctor:
        return []
    
    structures = []
    for sid in doctor.get("structure_ids", []):
        try:
            s = await db.structures.find_one({"_id": ObjectId(sid), "org_id": org_id})
            if s:
                structures.append({
                    "id": str(s["_id"]),
                    "name": s.get("name", ""),
                    "type": s.get("type", ""),
                    "city": s.get("city", ""),
                    "prefecture": s.get("prefecture", ""),
                })
        except Exception:
            pass
    return structures


async def get_colleagues(
    db: AsyncIOMotorDatabase, org_id: str, doctor_name: str,
) -> list[dict]:
    """Find other doctors working in the same structures."""
    doctor = await db.doctors.find_one({
        "org_id": org_id,
        "name": {"$regex": doctor_name, "$options": "i"},
    })
    if not doctor or not doctor.get("structure_ids"):
        return []
    
    colleagues = []
    cursor = db.doctors.find({
        "org_id": org_id,
        "_id": {"$ne": doctor["_id"]},
        "structure_ids": {"$in": doctor["structure_ids"]},
    }).limit(10)
    
    async for d in cursor:
        colleagues.append({
            "id": str(d["_id"]),
            "name": d.get("name", ""),
            "specialty": d.get("specialty", ""),
            "rx_count_90d": d.get("rx_count_90d", 0),
        })
    
    return colleagues


# ── Doctor ↔ Product Preferences ─────────────────────────────────────────────

async def get_doctor_product_preferences(
    db: AsyncIOMotorDatabase, org_id: str, doctor_name: str,
    top_k: int = 10,
) -> dict:
    """Analyze a doctor's prescribing patterns from case/prescription history."""
    doctor = await db.doctors.find_one({
        "org_id": org_id,
        "name": {"$regex": doctor_name, "$options": "i"},
    })
    if not doctor:
        return {"error": f"Doctor '{doctor_name}' not found"}
    
    # Find cases linked to this doctor
    case_cursor = db.cases.find({
        "org_id": org_id,
        "doctor_id": str(doctor["_id"]),
    })
    
    sku_counts = {}  # sku_id → count
    category_counts = {}  # category → count
    total_prescriptions = 0
    
    async for case in case_cursor:
        # Find prescriptions for these cases
        presc_cursor = db.prescriptions.find({
            "org_id": org_id,
            "case_id": str(case["_id"]),
        })
        async for presc in presc_cursor:
            total_prescriptions += 1
            sku_id = presc.get("sku_id", "")
            if sku_id:
                sku_counts[sku_id] = sku_counts.get(sku_id, 0) + 1
    
    # Resolve SKU names
    preferred_products = []
    for sku_id, count in sorted(sku_counts.items(), key=lambda x: -x[1])[:top_k]:
        try:
            sku = await db.skus.find_one({"_id": ObjectId(sku_id), "org_id": org_id})
            if sku:
                cat = sku.get("category", "")
                category_counts[cat] = category_counts.get(cat, 0) + count
                preferred_products.append({
                    "id": str(sku["_id"]),
                    "name": sku.get("commercial_name", ""),
                    "product_id": sku.get("product_id", ""),
                    "category": cat,
                    "price": sku.get("reimb_price", 0),
                    "prescription_count": count,
                })
        except Exception:
            pass
    
    # Top categories
    top_categories = sorted(category_counts.items(), key=lambda x: -x[1])[:5]
    
    return {
        "doctor": {
            "name": doctor.get("name"),
            "specialty": doctor.get("specialty"),
        },
        "total_prescriptions_analyzed": total_prescriptions,
        "preferred_products": preferred_products,
        "top_categories": [{"category": cat, "count": cnt} for cat, cnt in top_categories],
    }


async def get_similar_doctors(
    db: AsyncIOMotorDatabase, org_id: str, doctor_name: str,
    top_k: int = 5,
) -> list[dict]:
    """Find doctors similar to the given one based on specialty + product preferences."""
    doctor = await db.doctors.find_one({
        "org_id": org_id,
        "name": {"$regex": doctor_name, "$options": "i"},
    })
    if not doctor:
        return []
    
    # Same specialty
    specialty = doctor.get("specialty", "OTHER")
    cursor = db.doctors.find({
        "org_id": org_id,
        "specialty": specialty,
        "name": {"$ne": doctor.get("name")},
    }).limit(top_k)
    
    similar = []
    async for d in cursor:
        similar.append({
            "id": str(d["_id"]),
            "name": d.get("name", ""),
            "specialty": d.get("specialty", ""),
            "rx_count_90d": d.get("rx_count_90d", 0),
            "notes": (d.get("notes", "") or "")[:150],
            "reason": f"Ίδια ειδικότητα: {specialty}",
        })
    
    return similar


# ── Structure Network ─────────────────────────────────────────────────────────

async def get_structure_doctors(
    db: AsyncIOMotorDatabase, org_id: str, structure_name: str,
) -> list[dict]:
    """Get all doctors working in a given structure."""
    structure = await db.structures.find_one({
        "org_id": org_id,
        "name": {"$regex": structure_name, "$options": "i"},
    })
    if not structure:
        return []
    
    sid = str(structure["_id"])
    cursor = db.doctors.find({
        "org_id": org_id,
        "structure_ids": sid,
    }).limit(20)
    
    doctors = []
    async for d in cursor:
        doctors.append({
            "id": str(d["_id"]),
            "name": d.get("name", ""),
            "specialty": d.get("specialty", ""),
            "rx_count_90d": d.get("rx_count_90d", 0),
        })
    
    return doctors


async def get_nearby_structures(
    db: AsyncIOMotorDatabase, org_id: str, prefecture: str,
) -> list[dict]:
    """Get structures in the same prefecture."""
    cursor = db.structures.find({
        "org_id": org_id,
        "prefecture": {"$regex": prefecture, "$options": "i"},
    }).limit(10)
    
    structures = []
    async for s in cursor:
        structures.append({
            "id": str(s["_id"]),
            "name": s.get("name", ""),
            "type": s.get("type", ""),
            "city": s.get("city", ""),
        })
    
    return structures
