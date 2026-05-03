from __future__ import annotations

from decimal import Decimal
from math import cos, radians, sqrt

from sqlalchemy import select
from sqlalchemy.orm import Session

from models import PriceSuggestion, Room

AREA_RATE = Decimal("70000")
AMENITY_PRICES = {
    "has_aircon": Decimal("250000"),
    "has_fridge": Decimal("150000"),
    "has_water_heater": Decimal("180000"),
    "has_balcony": Decimal("120000"),
    "has_elevator": Decimal("180000"),
}
CITY_CENTER_LAT = Decimal("10.7769")
CITY_CENTER_LON = Decimal("106.7009")
HCMC_DISTRICT_MARKET = {
    "Quận 1": {"market_min": 4_800_000, "market_avg": 5_800_000, "market_max": 8_200_000, "district_factor": Decimal("1.18")},
    "Quận 3": {"market_min": 4_300_000, "market_avg": 5_200_000, "market_max": 7_300_000, "district_factor": Decimal("1.14")},
    "Quận 4": {"market_min": 3_900_000, "market_avg": 4_700_000, "market_max": 6_600_000, "district_factor": Decimal("1.10")},
    "Quận 5": {"market_min": 3_800_000, "market_avg": 4_600_000, "market_max": 6_400_000, "district_factor": Decimal("1.09")},
    "Quận 6": {"market_min": 3_300_000, "market_avg": 4_000_000, "market_max": 5_700_000, "district_factor": Decimal("1.04")},
    "Quận 7": {"market_min": 4_100_000, "market_avg": 5_000_000, "market_max": 7_000_000, "district_factor": Decimal("1.12")},
    "Quận 8": {"market_min": 3_100_000, "market_avg": 3_800_000, "market_max": 5_300_000, "district_factor": Decimal("1.00")},
    "Quận 10": {"market_min": 3_900_000, "market_avg": 4_700_000, "market_max": 6_500_000, "district_factor": Decimal("1.09")},
    "Quận 11": {"market_min": 3_500_000, "market_avg": 4_200_000, "market_max": 5_900_000, "district_factor": Decimal("1.05")},
    "Quận 12": {"market_min": 2_800_000, "market_avg": 3_400_000, "market_max": 4_700_000, "district_factor": Decimal("0.96")},
    "Bình Thạnh": {"market_min": 3_900_000, "market_avg": 4_800_000, "market_max": 6_900_000, "district_factor": Decimal("1.10")},
    "Tân Bình": {"market_min": 3_700_000, "market_avg": 4_500_000, "market_max": 6_400_000, "district_factor": Decimal("1.08")},
    "Tân Phú": {"market_min": 3_300_000, "market_avg": 4_000_000, "market_max": 5_600_000, "district_factor": Decimal("1.02")},
    "Phú Nhuận": {"market_min": 4_000_000, "market_avg": 4_900_000, "market_max": 6_900_000, "district_factor": Decimal("1.11")},
    "Gò Vấp": {"market_min": 3_200_000, "market_avg": 3_900_000, "market_max": 5_500_000, "district_factor": Decimal("1.01")},
    "Bình Tân": {"market_min": 2_900_000, "market_avg": 3_600_000, "market_max": 5_100_000, "district_factor": Decimal("0.98")},
    "Thủ Đức": {"market_min": 3_500_000, "market_avg": 4_300_000, "market_max": 6_100_000, "district_factor": Decimal("1.06")},
    "Bình Chánh": {"market_min": 2_500_000, "market_avg": 3_100_000, "market_max": 4_400_000, "district_factor": Decimal("0.94")},
    "Hóc Môn": {"market_min": 2_500_000, "market_avg": 3_000_000, "market_max": 4_200_000, "district_factor": Decimal("0.93")},
    "Nhà Bè": {"market_min": 2_900_000, "market_avg": 3_600_000, "market_max": 5_100_000, "district_factor": Decimal("0.98")},
    "Củ Chi": {"market_min": 2_300_000, "market_avg": 2_800_000, "market_max": 3_900_000, "district_factor": Decimal("0.90")},
    "Cần Giờ": {"market_min": 2_200_000, "market_avg": 2_700_000, "market_max": 3_800_000, "district_factor": Decimal("0.88")},
}


def normalize_district(name: str) -> str:
    key = (name or "").strip().lower()
    for district in HCMC_DISTRICT_MARKET:
        if district.lower() == key:
            return district
    return "Thủ Đức"


def _geo_factor(room: Room) -> tuple[Decimal, float | None]:
    if room.latitude is None or room.longitude is None:
        return Decimal("1.00"), None

    lat = Decimal(str(room.latitude))
    lon = Decimal(str(room.longitude))
    dlat = float(lat - CITY_CENTER_LAT)
    dlon = float(lon - CITY_CENTER_LON)
    lat_km = dlat * 111.32
    lon_km = dlon * 111.32 * cos(radians(float(CITY_CENTER_LAT)))
    distance_km = sqrt(lat_km * lat_km + lon_km * lon_km)

    if distance_km <= 1:
        return Decimal("1.12"), distance_km
    if distance_km <= 3:
        return Decimal("1.08"), distance_km
    if distance_km <= 6:
        return Decimal("1.04"), distance_km
    if distance_km <= 10:
        return Decimal("1.00"), distance_km
    return Decimal("0.96"), distance_km


def calculate_price_for_room(room: Room) -> tuple[Decimal, dict]:
    district = normalize_district(room.khu_vuc)
    benchmark = HCMC_DISTRICT_MARKET[district]
    market_avg = Decimal(str(benchmark["market_avg"]))
    market_min = Decimal(str(benchmark["market_min"]))
    market_max = Decimal(str(benchmark["market_max"]))
    district_factor = benchmark["district_factor"]
    geo_factor, distance_km = _geo_factor(room)

    amenity_total = Decimal("0")
    amenities_enabled = []
    for field_name, extra_price in AMENITY_PRICES.items():
        if getattr(room, field_name):
            amenity_total += extra_price
            amenities_enabled.append(field_name)

    area_adjustment = (Decimal(str(room.area_m2)) - Decimal("20")) * AREA_RATE
    floor_bonus = Decimal("80000") * max(room.tang - 1, 0)
    base_price = market_avg * district_factor * geo_factor
    suggested_price = base_price + area_adjustment + amenity_total + floor_bonus

    min_price = market_min * geo_factor
    max_price = market_max * geo_factor
    if suggested_price < min_price:
        suggested_price = min_price
    if suggested_price > max_price:
        suggested_price = max_price

    breakdown = {
        "district": district,
        "district_factor": float(district_factor),
        "geo_factor": float(geo_factor),
        "distance_to_center_km": None if distance_km is None else round(distance_km, 2),
        "market_avg": float(market_avg),
        "market_min": float(min_price),
        "market_max": float(max_price),
        "area_adjustment": float(area_adjustment),
        "amenity_total": float(amenity_total),
        "floor_bonus": float(floor_bonus),
        "amenities_enabled": amenities_enabled,
        "formula": "clamp((market_avg * district_factor * geo_factor) + area_adjustment + amenity_total + floor_bonus)",
    }
    return suggested_price.quantize(Decimal("1.")), breakdown


def persist_price_suggestion(db: Session, room: Room) -> PriceSuggestion:
    district = normalize_district(room.khu_vuc)
    stmt = select(Room).where(Room.khu_vuc == district, Room.room_id != room.room_id)
    similar_rooms = db.execute(stmt).scalars().all()
    suggested_price, breakdown = calculate_price_for_room(room)
    item = PriceSuggestion(
        room_id=room.room_id,
        suggested_price=suggested_price,
        based_on_count=len(similar_rooms),
        algo_version="hcm-district-geoscore-v1",
        score_breakdown=breakdown,
    )
    db.add(item)
    db.flush()
    return item
