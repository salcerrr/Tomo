from pathlib import Path

BASE_DIR = Path(__file__).resolve().parent

DATA_DIR = BASE_DIR / "data"
DEFAULT_LIST_PATH = DATA_DIR / "mangalist.xml.gz"

JIKAN_BASE_URL = "https://api.tenrai.org/v1"

MAL_USERNAME = "salcer"

# Romance en MyAnimeList
ROMANCE_GENRE_ID = 22

# Revistas de tu lista personal.
MAGAZINES = [
    "Afternoon",
    "Bessatsu Shounen Champion",
    "Big Gangan",
    "Comic Alive",
    'Comic Dengeki Daioh "g"',
    "Comic Days",
    "Comic Fuz",
    "Cycomics",
    "Dengeki Comic Regulus",
    "Dengeki Daioh",
    "Ganma!",
    "Gangan Joker",
    "Gessan",
    "Grand Jump",
    "Harta",
    "Jump SQ.",
    "Kurage Bunch",
    "Magazine pocket",
    "Manga Action",
    "Manga Park",
    "Manga Time Original",
    "Manga UP!",
    "Morning Two",
    "Shounen Champion (Weekly)",
    "Shounen Jump (Weekly)",
    "Shounen Jump+",
    "Shounen Magazine (Weekly)",
    "Shounen Sirius",
    "Shounen Sunday",
    "Sunday Webry",
    "Tonari no Young Jump",
    "Web Comic Apanta",
    "Wild Hero's",
    "Yanchan Web",
    "Young Animal",
    "Young Jump",
    "Young King",
    "Young Magazine (Weekly)",
]