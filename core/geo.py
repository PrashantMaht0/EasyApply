"""Curated place to country and region table. Small on purpose, extend it as gaps appear."""

# Regions a country belongs to. A posting naming any of these is reachable from that country.
COUNTRY_REGIONS = {
    "ireland": {"eu", "emea", "europe", "eea"},
    "united kingdom": {"emea", "europe", "uk"},
    "germany": {"eu", "emea", "europe", "eea", "dach"},
    "france": {"eu", "emea", "europe", "eea"},
    "spain": {"eu", "emea", "europe", "eea"},
    "portugal": {"eu", "emea", "europe", "eea"},
    "netherlands": {"eu", "emea", "europe", "eea"},
    "belgium": {"eu", "emea", "europe", "eea"},
    "poland": {"eu", "emea", "europe", "eea"},
    "sweden": {"eu", "emea", "europe", "eea"},
    "denmark": {"eu", "emea", "europe", "eea"},
    "norway": {"emea", "europe", "eea"},
    "finland": {"eu", "emea", "europe", "eea"},
    "italy": {"eu", "emea", "europe", "eea"},
    "austria": {"eu", "emea", "europe", "eea", "dach"},
    "switzerland": {"emea", "europe", "dach"},
    "czechia": {"eu", "emea", "europe", "eea"},
    "romania": {"eu", "emea", "europe", "eea"},
    "united states": {"us", "usa", "north america", "americas", "amer", "namer"},
    "canada": {"north america", "americas", "amer", "namer"},
    "mexico": {"north america", "americas", "latam"},
    "brazil": {"americas", "latam", "south america"},
    "costa rica": {"americas", "latam"},
    "india": {"apac", "asia"},
    "singapore": {"apac", "asia", "sea"},
    "japan": {"apac", "asia"},
    "australia": {"apac", "anz", "oceania"},
    "new zealand": {"apac", "anz", "oceania"},
    "israel": {"emea"},
    "south africa": {"emea", "africa"},
}

# Country names and the abbreviations boards actually use.
COUNTRY_ALIASES = {
    "ireland": "ireland", "irl": "ireland", "eire": "ireland",
    "united kingdom": "united kingdom", "uk": "united kingdom", "england": "united kingdom",
    "scotland": "united kingdom", "wales": "united kingdom", "britain": "united kingdom",
    "united states": "united states", "usa": "united states", "us": "united states",
    "u.s.": "united states", "u.s.a.": "united states", "america": "united states",
    "germany": "germany", "deutschland": "germany", "france": "france", "spain": "spain",
    "portugal": "portugal", "netherlands": "netherlands", "holland": "netherlands",
    "belgium": "belgium", "poland": "poland", "sweden": "sweden", "denmark": "denmark",
    "norway": "norway", "finland": "finland", "italy": "italy", "austria": "austria",
    "switzerland": "switzerland", "czechia": "czechia", "czech republic": "czechia",
    "romania": "romania", "canada": "canada", "mexico": "mexico", "brazil": "brazil",
    "costa rica": "costa rica", "india": "india", "singapore": "singapore", "japan": "japan",
    "australia": "australia", "new zealand": "new zealand", "israel": "israel",
    "south africa": "south africa",
}

# Cities and subdivisions, enough to place the postings these boards actually return.
CITY_COUNTRY = {
    "dublin": "ireland", "cork": "ireland", "galway": "ireland", "limerick": "ireland",
    "athlone": "ireland", "waterford": "ireland",
    "london": "united kingdom", "manchester": "united kingdom", "edinburgh": "united kingdom",
    "cambridge": "united kingdom", "bristol": "united kingdom", "belfast": "united kingdom",
    "berlin": "germany", "munich": "germany", "hamburg": "germany", "cologne": "germany",
    "frankfurt": "germany", "paris": "france", "lyon": "france", "madrid": "spain",
    "barcelona": "spain", "lisbon": "portugal", "porto": "portugal",
    "amsterdam": "netherlands", "rotterdam": "netherlands", "utrecht": "netherlands",
    "brussels": "belgium", "warsaw": "poland", "krakow": "poland", "wroclaw": "poland",
    "stockholm": "sweden", "copenhagen": "denmark", "oslo": "norway", "helsinki": "finland",
    "milan": "italy", "rome": "italy", "vienna": "austria", "zurich": "switzerland",
    "geneva": "switzerland", "prague": "czechia", "bucharest": "romania",
    "toronto": "canada", "vancouver": "canada", "montreal": "canada", "ottawa": "canada",
    "waterloo": "canada", "calgary": "canada",
    "bengaluru": "india", "bangalore": "india", "hyderabad": "india", "pune": "india",
    "mumbai": "india", "delhi": "india", "gurgaon": "india", "chennai": "india",
    "noida": "india", "sydney": "australia", "melbourne": "australia",
    "auckland": "new zealand", "tokyo": "japan", "tel aviv": "israel",
    "sao paulo": "brazil", "mexico city": "mexico", "cape town": "south africa",
}

# Full state names are safe to match case insensitively.
_US_STATES = """alabama alaska arizona arkansas california colorado connecticut delaware
florida georgia hawaii idaho illinois indiana iowa kansas kentucky louisiana maine maryland
massachusetts michigan minnesota mississippi missouri montana nebraska nevada ohio oklahoma
oregon pennsylvania tennessee texas utah vermont virginia wisconsin wyoming""".split()

_US_STATE_PHRASES = ("new hampshire", "new jersey", "new mexico", "new york", "north carolina",
                     "north dakota", "rhode island", "south carolina", "south dakota",
                     "west virginia", "district of columbia")

_US_CITIES = """seattle bellevue redmond portland denver boulder austin dallas houston atlanta
chicago boston cambridge nyc brooklyn philadelphia pittsburgh raleigh durham charlotte miami
orlando phoenix tucson minneapolis detroit nashville princeton reston arlington bethesda
sunnyvale cupertino oakland berkeley""".split()

_US_CITY_PHRASES = ("new york", "san francisco", "los angeles", "san diego", "san jose",
                    "washington dc", "salt lake city", "kansas city", "san antonio",
                    "santa clara", "menlo park", "mountain view", "palo alto")

for _token in _US_STATES + _US_CITIES:
    CITY_COUNTRY.setdefault(_token, "united states")
for _phrase in _US_STATE_PHRASES + _US_CITY_PHRASES:
    CITY_COUNTRY.setdefault(_phrase, "united states")

# Two letter state codes are matched case sensitively and only as standalone uppercase tokens,
# because lowercase "or", "in" and "me" are ordinary English words.
US_STATE_CODES = set("""AL AK AZ AR CA CO CT DE FL GA HI ID IL IN IA KS KY LA ME MD MA MI MN
MS MO MT NE NV NH NJ NM NY NC ND OH OK OR PA RI SC SD TN TX UT VT VA WA WV WI WY DC""".split())

# Region words that appear directly in a posting location, without naming a country.
REGION_WORDS = {"eu", "emea", "europe", "eea", "apac", "asia", "latam", "anz", "dach",
                "north america", "americas", "amer", "namer", "oceania", "africa", "uk", "us",
                "usa", "sea", "south america"}

# A remote posting carrying one of these is open to anyone, so geography cannot rule it out.
GLOBAL_WORDS = {"worldwide", "global", "anywhere", "any location", "fully remote", "distributed"}

# Every remaining country, so an unfamiliar place name is recognised as foreign rather than
# slipping through the "cannot judge, so keep it" fallback in core/filters.py.
_REGION_COUNTRIES = {
    ("eu", "emea", "europe", "eea"): """bulgaria croatia cyprus estonia greece hungary latvia
        lithuania luxembourg malta slovakia slovenia""",
    ("emea", "europe"): """albania belarus bosnia georgia iceland moldova monaco montenegro
        macedonia serbia ukraine russia turkey""",
    ("emea", "middle east"): """bahrain egypt iran iraq jordan kuwait lebanon oman palestine
        qatar saudi arabia syria yemen""",
    ("emea", "africa"): """algeria angola botswana cameroon ethiopia ghana ivory coast kenya
        libya malawi mali morocco mozambique namibia nigeria rwanda senegal somalia sudan
        tanzania tunisia uganda zambia zimbabwe mauritius""",
    ("apac", "asia"): """bangladesh bhutan brunei cambodia china hong kong indonesia kazakhstan
        laos macau malaysia maldives mongolia myanmar nepal pakistan philippines south korea
        korea sri lanka taiwan thailand uzbekistan vietnam""",
    ("apac", "oceania"): "fiji papua new guinea",
    ("americas", "latam", "south america"): """argentina bolivia chile colombia ecuador guyana
        paraguay peru suriname uruguay venezuela""",
    ("americas", "latam"): """belize cuba dominican republic el salvador guatemala haiti
        honduras jamaica nicaragua panama puerto rico trinidad""",
}
# multi word countries must not be split on whitespace, so they are listed explicitly
_MULTIWORD = {
    "hong kong": ("apac", "asia"), "south korea": ("apac", "asia"),
    "sri lanka": ("apac", "asia"), "saudi arabia": ("emea", "middle east"),
    "ivory coast": ("emea", "africa"), "papua new guinea": ("apac", "oceania"),
    "dominican republic": ("americas", "latam"), "el salvador": ("americas", "latam"),
    "puerto rico": ("americas", "latam"),
}
for _regions, _names in _REGION_COUNTRIES.items():
    _tokens = _names.split()
    for _country in _tokens:
        if any(_country in _phrase.split() for _phrase in _MULTIWORD):
            continue
        COUNTRY_REGIONS.setdefault(_country, set(_regions))
        COUNTRY_ALIASES.setdefault(_country, _country)
for _phrase, _regions in _MULTIWORD.items():
    COUNTRY_REGIONS.setdefault(_phrase, set(_regions))
    COUNTRY_ALIASES.setdefault(_phrase, _phrase)

# Subdivisions and hubs these boards actually return.
CITY_COUNTRY.update({
    "ontario": "canada", "quebec": "canada", "british columbia": "canada", "alberta": "canada",
    "gurugram": "india", "bhubaneswar": "india", "kolkata": "india", "ahmedabad": "india",
    "seoul": "south korea", "shanghai": "china", "beijing": "china", "shenzhen": "china",
    "sao paulo": "brazil", "rio de janeiro": "brazil", "bogota": "colombia",
    "buenos aires": "argentina", "santiago": "chile", "lima": "peru",
    "taipei": "taiwan", "bangkok": "thailand", "jakarta": "indonesia", "manila": "philippines",
    "hanoi": "vietnam", "ho chi minh": "vietnam", "kuala lumpur": "malaysia",
    "dubai": "united arab emirates", "abu dhabi": "united arab emirates",
    "istanbul": "turkey", "athens": "greece", "budapest": "hungary", "sofia": "bulgaria",
    "zagreb": "croatia", "vilnius": "lithuania", "riga": "latvia", "tallinn": "estonia",
    "nairobi": "kenya", "lagos": "nigeria", "cairo": "egypt", "casablanca": "morocco",
})
COUNTRY_REGIONS.setdefault("united arab emirates", {"emea", "middle east"})
COUNTRY_ALIASES.setdefault("united arab emirates", "united arab emirates")
COUNTRY_ALIASES.setdefault("uae", "united arab emirates")
