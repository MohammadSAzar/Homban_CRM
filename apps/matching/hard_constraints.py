"""Temporary exact pair requirements; never weights or persisted settings."""
from .engine import _preferred


def has_age_requirement(customer):
    return customer.min_building_age is not None or customer.max_building_age is not None


def hard_constraint_failure(file, customer, constraints):
    if constraints.get("area") and not customer.min_area <= file.area <= customer.max_area:
        return "hard_area"
    if constraints.get("bedrooms") and file.bedrooms != customer.bedrooms:
        return "hard_bedrooms"
    # For candidate Customers with no age requirement, this toggle is inapplicable.
    # A fixed Customer source is validated before candidate discovery.
    if constraints.get("building_age") and has_age_requirement(customer):
        age = file.building_age
        if age is None or (customer.min_building_age is not None and age < customer.min_building_age) or (customer.max_building_age is not None and age > customer.max_building_age):
            return "hard_building_age"
    if constraints.get("region") and not _preferred(file, customer)[0]:
        return "hard_region"
    for name in ("parking", "elevator", "storage", "balcony"):
        if constraints.get(name) and getattr(file, name) is not True:
            return f"hard_{name}"
    return None
