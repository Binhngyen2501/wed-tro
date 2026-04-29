from __future__ import annotations

from decimal import Decimal

from sqlalchemy import select
from sqlalchemy.orm import Session

from models import PriceSuggestion, Room

AREA_RATE = Decimal("180000")
AMENITY_PRICES = {
    "has_aircon": Decimal("250000"),
    "has_fridge": Decimal("180000"),
    "has_water_heater": Decimal("120000"),
    "has_balcony": Decimal("150000"),
    "has_elevator": Decimal("200000"),
}
REGION_FACTOR = {
    "trung tâm": Decimal("1.20"),
    "cận trung tâm": Decimal("1.10"),
    "ngoại thành": Decimal("1.00"),
}


def normalize_region(region: str) -> str:
    region = (region or "").strip().lower()
    if region in REGION_FACTOR:
        return region
    return "ngoại thành"


def calculate_price_for_room(room: Room) -> tuple[Decimal, dict]:
    area_component = Decimal(str(room.area_m2)) * AREA_RATE
    region_key = normalize_region(room.khu_vuc)
    position_factor = REGION_FACTOR[region_key]
    amenity_total = Decimal("0")
    amenities_enabled = []

    for field_name, extra_price in AMENITY_PRICES.items():
        if getattr(room, field_name):
            amenity_total += extra_price
            amenities_enabled.append(field_name)

    floor_bonus = Decimal("50000") * max(room.tang - 1, 0)
    base_price = area_component * position_factor
    suggested_price = base_price + amenity_total + floor_bonus

    breakdown = {
        "area_component": float(area_component),
        "position_factor": float(position_factor),
        "amenity_total": float(amenity_total),
        "floor_bonus": float(floor_bonus),
        "amenities_enabled": amenities_enabled,
        "formula": "(area_m2 * 180000 * region_factor) + amenity_total + floor_bonus",
    }
    return suggested_price.quantize(Decimal("1.")), breakdown


def persist_price_suggestion(db: Session, room: Room) -> PriceSuggestion:
    stmt = select(Room).where(Room.khu_vuc == room.khu_vuc, Room.room_id != room.room_id)
    similar_rooms = db.execute(stmt).scalars().all()
    suggested_price, breakdown = calculate_price_for_room(room)
    item = PriceSuggestion(
        room_id=room.room_id,
        suggested_price=suggested_price,
        based_on_count=len(similar_rooms),
        algo_version="weighted-scoring-v1",
        score_breakdown=breakdown,
    )
    db.add(item)
    db.flush()
    return item
