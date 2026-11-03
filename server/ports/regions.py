"""REGIONS-v1: a published country-name -> region table (mechanical
filter aid only; not a political classification and never a modelling
input). Chokepoints are assigned by place id. A country the table does
not know reads "Unclassified" — named, not guessed.
"""
from __future__ import annotations

VERSION = "1"

_R = {
    "North America": [
        "United States", "Canada", "Mexico", "United States / Canada",
        "United States / Mexico", "Bermuda"],
    "Latin America & Caribbean": [
        "Argentina", "Aruba", "Antigua and Barbuda", "Bahamas",
        "The Bahamas", "Barbados", "Bolivia", "Brazil",
        "Caribbean Netherlands", "Chile", "Colombia", "Costa Rica",
        "Curaçao", "Dominican Republic", "Ecuador", "El Salvador",
        "Guadeloupe", "Guatemala", "Guyana", "Haiti", "Honduras",
        "Jamaica", "Martinique", "Panama", "Peru", "Puerto Rico",
        "Sint Maarten", "St.  Lucia", "St.  Vincent and the Grenadines",
        "Suriname", "Trinidad and Tobago", "United States Virgin Islands",
        "Uruguay"],
    "Europe & Central Asia": [
        "Albania", "Armenia", "Austria", "Azerbaijan", "Belarus",
        "Belgium", "Bosnia and Herzegovina", "Bulgaria", "Croatia",
        "Cyprus", "Denmark", "Estonia", "Finland", "France", "Georgia",
        "Germany", "Greece", "Hungary", "Iceland", "Ireland", "Italy",
        "Kazakhstan", "Kazakhstan / China", "Latvia", "Lithuania",
        "Luxembourg", "Malta", "Netherlands", "The Netherlands", "Norway",
        "Poland", "Poland / Belarus", "Portugal", "Romania", "Russia",
        "Russian Federation", "Slovenia", "Spain", "Sweden", "Turkey",
        "Türkiye", "Turkmenistan", "Ukraine", "United Kingdom",
        "United Kingdom / France"],
    "Middle East & North Africa": [
        "Algeria", "Bahrain", "Djibouti", "Egypt", "Iran", "Iraq",
        "Israel", "Jordan", "Kuwait", "Lebanon", "Libya", "Morocco",
        "Oman", "Qatar", "Saudi Arabia", "Tunisia",
        "United Arab Emirates"],
    "Sub-Saharan Africa": [
        "Angola", "Benin", "Botswana", "Burkina Faso", "Burundi",
        "Cabo Verde", "Cameroon", "Côte d'Ivoire", "Ethiopia", "Gabon",
        "Ghana", "Guinea", "Kenya", "Mauritania", "Mauritius",
        "Mozambique", "Namibia", "Nigeria", "Republic of Congo",
        "Réunion", "Senegal", "Sierra Leone", "South Africa", "Sudan",
        "Tanzania", "Togo"],
    "South Asia": [
        "Afghanistan", "Bangladesh", "Bhutan", "India", "Maldives",
        "Pakistan", "Sri Lanka"],
    "East Asia": [
        "China", "China / Kazakhstan", "China / Mongolia",
        "China / Russia", "Japan", "Korea", "South Korea", "Taiwan",
        "Taiwan Province of China"],
    "Southeast Asia": [
        "Brunei", "Brunei Darussalam", "Cambodia", "Indonesia",
        "Malaysia", "Myanmar", "Philippines", "Singapore", "Thailand",
        "Vietnam"],
    "Oceania": [
        "American Samoa", "Australia", "New Caledonia", "New Zealand",
        "Papua New Guinea"],
}

COUNTRY_REGION = {c: r for r, cs in _R.items() for c in cs}

CHOKEPOINT_REGION = {
    "chk_babelmandeb": "Middle East & North Africa",
    "chk_capeofgoodhope": "Sub-Saharan Africa",
    "chk_danish": "Europe & Central Asia",
    "chk_dover": "Europe & Central Asia",
    "chk_gibraltar": "Europe & Central Asia",
    "chk_hormuz": "Middle East & North Africa",
    "chk_malacca": "Southeast Asia",
    "chk_mozambique": "Sub-Saharan Africa",
    "chk_panama": "Latin America & Caribbean",
    "chk_suez": "Middle East & North Africa",
    "chk_taiwan": "East Asia",
    "chk_turkish": "Europe & Central Asia",
}

REGIONS = tuple(_R) + ("Unclassified",)


def region_of(place_id: str, country: str | None) -> str:
    if place_id in CHOKEPOINT_REGION:
        return CHOKEPOINT_REGION[place_id]
    return COUNTRY_REGION.get(country or "", "Unclassified")
