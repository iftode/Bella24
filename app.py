from __future__ import annotations

import base64
import json
import mimetypes
import os
import sqlite3
import urllib.parse
import urllib.request
from functools import wraps
from pathlib import Path
from uuid import uuid4

from flask import Flask, flash, jsonify, redirect, render_template, request, session, url_for
from werkzeug.security import check_password_hash, generate_password_hash
from werkzeug.utils import secure_filename

try:
    from openai import OpenAI
except Exception:
    OpenAI = None


BASE_DIR = Path(__file__).resolve().parent
DB_PATH = BASE_DIR / "database" / "store.db"
UPLOAD_DIR = BASE_DIR / "static" / "images" / "products"
ALLOWED_EXT = {"png", "jpg", "jpeg", "webp", "gif"}

DB_PATH.parent.mkdir(exist_ok=True)
UPLOAD_DIR.mkdir(parents=True, exist_ok=True)

app = Flask(__name__)
app.secret_key = "bella24-secret-key-change-me"


TEXT = {
    "ro": {
        "home": "Acasă",
        "products": "Produse",
        "promos": "Promoții",
        "contact": "Contact",
        "cart": "Coș",
        "login": "Login",
        "register": "Înregistrare",
        "admin": "Admin",
        "hero": "Fashion pentru tine, în fiecare zi",
        "see_products": "Vezi produsele",
        "my_cart": "Coșul meu",
    },
    "en": {
        "home": "Home",
        "products": "Products",
        "promos": "Promos",
        "contact": "Contact",
        "cart": "Cart",
        "login": "Login",
        "register": "Register",
        "admin": "Admin",
        "hero": "Fashion for you, every day",
        "see_products": "See products",
        "my_cart": "My cart",
    },
    "de": {
        "home": "Start",
        "products": "Produkte",
        "promos": "Angebote",
        "contact": "Kontakt",
        "cart": "Warenkorb",
        "login": "Login",
        "register": "Registrieren",
        "admin": "Admin",
        "hero": "Mode für dich, jeden Tag",
        "see_products": "Produkte ansehen",
        "my_cart": "Warenkorb",
    },
}


# Limbile disponibile pentru site. Codurile sunt folosite în URL: /set-language/<cod>
LANG_OPTIONS = [
    ("ro", "🇷🇴 RO", "Română"),
    ("en", "🇬🇧 EN", "English"),
    ("de", "🇩🇪 DE", "Deutsch"),
    ("hu", "🇭🇺 HU", "Magyar"),
    ("bg", "🇧🇬 BG", "Български"),
    ("el", "🇬🇷 EL", "Ελληνικά"),
    ("ru", "🇷🇺 RU", "Русский"),
    ("uk", "🇺🇦 UK", "Українська"),
    ("sr", "🇷🇸 SR", "Српски"),
]

DESCRIPTION_LANGS = ["en", "de", "hu", "bg", "el", "ru", "uk", "sr"]
TRANSLATE_TARGETS = {
    "en": "en",
    "de": "de",
    "hu": "hu",
    "bg": "bg",
    "el": "el",
    "ru": "ru",
    "uk": "uk",
    "sr": "sr",
}
LANG_LABELS = {
    "en": "English",
    "de": "Deutsch",
    "hu": "Magyar",
    "bg": "Български",
    "el": "Ελληνικά",
    "ru": "Русский",
    "uk": "Українська",
    "sr": "Српски",
}

def google_translate_free(text, target_lang):
    """Traducere gratuită, fără cheie API, folosită doar la salvarea produsului.
    Dacă serviciul nu răspunde, întoarce text gol ca să poți salva manual ulterior.
    """
    text = (text or "").strip()
    if not text or target_lang not in TRANSLATE_TARGETS:
        return ""

    chunks = []
    current = ""
    for part in text.split(". "):
        candidate = (current + ". " + part).strip(". ") if current else part
        if len(candidate) > 2500 and current:
            chunks.append(current)
            current = part
        else:
            current = candidate
    if current:
        chunks.append(current)

    translated_parts = []
    for chunk in chunks:
        try:
            params = urllib.parse.urlencode({
                "client": "gtx",
                "sl": "ro",
                "tl": TRANSLATE_TARGETS[target_lang],
                "dt": "t",
                "q": chunk,
            })
            url = "https://translate.googleapis.com/translate_a/single?" + params
            req = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0"})
            with urllib.request.urlopen(req, timeout=12) as resp:
                data = json.loads(resp.read().decode("utf-8"))
            translated = "".join(seg[0] for seg in data[0] if seg and seg[0])
            translated_parts.append(translated)
        except Exception:
            return ""
    return " ".join(part.strip() for part in translated_parts if part.strip()).strip()

def _row_get(row, key, default=""):
    try:
        if isinstance(row, dict):
            return row.get(key, default)
        return row[key]
    except Exception:
        return default

def _display_translated_field(row, base_field, lang=None):
    lang = lang or session.get("lang", "ro")
    if row is None:
        return ""

    original = _row_get(row, base_field, "") or ""

    # Pentru română folosim valoarea originală din baza de date.
    if lang == "ro":
        return original

    if lang in DESCRIPTION_LANGS:
        value = _row_get(row, f"{base_field}_{lang}", "")
        if value and str(value).strip():
            return value

    # Fallback final: română, nu engleză.
    return original

def display_description(row, lang=None):
    return _display_translated_field(row, "description", lang)

def display_product_name(row, lang=None):
    return _display_translated_field(row, "name", lang)

def display_product_color(row, lang=None):
    return _display_translated_field(row, "color", lang)

def display_product_material(row, lang=None):
    return _display_translated_field(row, "material", lang)

def localize_product(row):
    if row is None:
        return None
    item = dict(row)
    item["name_original"] = item.get("name") or ""
    item["description_original"] = item.get("description") or ""
    item["color_original"] = item.get("color") or ""
    item["material_original"] = item.get("material") or ""
    item["name"] = display_product_name(row)
    item["description"] = display_description(row)
    item["color"] = display_product_color(row)
    item["material"] = display_product_material(row)
    if item.get("category_name"):
        item["category_name"] = translate_known_category_text(item.get("category_name"))
    return item


CATEGORY_LANGS = DESCRIPTION_LANGS

CATEGORY_TRANSLATIONS = {
    "Bluze": {
        "en": "Blouses", "de": "Blusen", "hu": "Blúzok", "bg": "Блузи",
        "el": "Μπλούζες", "ru": "Блузы", "uk": "Блузи", "sr": "Блузе",
    },
    "Rochii": {
        "en": "Dresses", "de": "Kleider", "hu": "Ruhák", "bg": "Рокли",
        "el": "Φορέματα", "ru": "Платья", "uk": "Сукні", "sr": "Хаљине",
    },
    "Fuste": {
        "en": "Skirts", "de": "Röcke", "hu": "Szoknyák", "bg": "Поли",
        "el": "Φούστες", "ru": "Юбки", "uk": "Спідниці", "sr": "Сукње",
    },
    "Costume": {
        "en": "Women’s suits", "de": "Damenkostüme", "hu": "Női kosztümök", "bg": "Дамски костюми",
        "el": "Γυναικεία κοστούμια", "ru": "Женские костюмы", "uk": "Жіночі костюми", "sr": "Женски костими",
    },
    "Costume de dama": {
        "en": "Women’s suits", "de": "Damenkostüme", "hu": "Női kosztümök", "bg": "Дамски костюми",
        "el": "Γυναικεία κοστούμια", "ru": "Женские костюмы", "uk": "Жіночі костюми", "sr": "Женски костими",
    },
    "Costume de damă": {
        "en": "Women’s suits", "de": "Damenkostüme", "hu": "Női kosztümök", "bg": "Дамски костюми",
        "el": "Γυναικεία κοστούμια", "ru": "Женские костюмы", "uk": "Жіночі костюми", "sr": "Женски костими",
    },
    "Geci și paltoane": {
        "en": "Jackets and coats", "de": "Jacken und Mäntel", "hu": "Dzsekik és kabátok", "bg": "Якета и палта",
        "el": "Μπουφάν και παλτά", "ru": "Куртки и пальто", "uk": "Куртки та пальта", "sr": "Јакне и капути",
    },
    "Geci si paltoane": {
        "en": "Jackets and coats", "de": "Jacken und Mäntel", "hu": "Dzsekik és kabátok", "bg": "Якета и палта",
        "el": "Μπουφάν και παλτά", "ru": "Куртки и пальто", "uk": "Куртки та пальта", "sr": "Јакне и капути",
    },

    "Bluze elegante, casual și moderne.": {
        "en": "Elegant, casual and modern blouses.", "de": "Elegante, lässige und moderne Blusen.", "hu": "Elegáns, hétköznapi és modern blúzok.", "bg": "Елегантни, ежедневни и модерни блузи.",
        "el": "Κομψές, casual και μοντέρνες μπλούζες.", "ru": "Элегантные, повседневные и современные блузы.", "uk": "Елегантні, повсякденні та сучасні блузи.", "sr": "Елегантне, лежерне и модерне блузе.",
    },
    "Rochii de zi, rochii elegante și rochii de seară.": {
        "en": "Day dresses, elegant dresses and evening dresses.", "de": "Tageskleider, elegante Kleider und Abendkleider.", "hu": "Nappali ruhák, elegáns ruhák és estélyi ruhák.", "bg": "Ежедневни, елегантни и вечерни рокли.",
        "el": "Καθημερινά, κομψά και βραδινά φορέματα.", "ru": "Повседневные, элегантные и вечерние платья.", "uk": "Повсякденні, елегантні та вечірні сукні.", "sr": "Дневне, елегантне и вечерње хаљине.",
    },
    "Rochii de zi și de seară.": {
        "en": "Day and evening dresses.", "de": "Tages- und Abendkleider.", "hu": "Nappali és estélyi ruhák.", "bg": "Дневни и вечерни рокли.",
        "el": "Καθημερινά και βραδινά φορέματα.", "ru": "Дневные и вечерние платья.", "uk": "Денні та вечірні сукні.", "sr": "Дневне и вечерње хаљине.",
    },
    "Fuste feminine și versatile.": {
        "en": "Feminine and versatile skirts.", "de": "Feminine und vielseitige Röcke.", "hu": "Nőies és sokoldalú szoknyák.", "bg": "Женствени и универсални поли.",
        "el": "Θηλυκές και ευέλικτες φούστες.", "ru": "Женственные и универсальные юбки.", "uk": "Жіночні та універсальні спідниці.", "sr": "Женствене и свестране сукње.",
    },
    "Costume feminine și versatile.": {
        "en": "Feminine and versatile women’s suits.", "de": "Feminine und vielseitige Damenkostüme.", "hu": "Nőies és sokoldalú női kosztümök.", "bg": "Женствени и универсални дамски костюми.",
        "el": "Θηλυκά και ευέλικτα γυναικεία κοστούμια.", "ru": "Женственные и универсальные женские костюмы.", "uk": "Жіночні та універсальні жіночі костюми.", "sr": "Женствени и свестрани женски костими.",
    },
    "Costume elegante de damă.": {
        "en": "Elegant women’s suits.", "de": "Elegante Damenkostüme.", "hu": "Elegáns női kosztümök.", "bg": "Елегантни дамски костюми.",
        "el": "Κομψά γυναικεία κοστούμια.", "ru": "Элегантные женские костюмы.", "uk": "Елегантні жіночі костюми.", "sr": "Елегантни женски костими.",
    },
    "Articole de exterior.": {
        "en": "Outerwear items.", "de": "Oberbekleidung.", "hu": "Kültéri viseletek.", "bg": "Връхни дрехи.",
        "el": "Είδη εξωτερικής ένδυσης.", "ru": "Верхняя одежда.", "uk": "Верхній одяг.", "sr": "Горња одећа.",
    },
}

def translate_known_category_text(text, lang=None):
    lang = lang or session.get("lang", "ro")
    text = (text or "").strip()
    if lang == "ro" or not text:
        return text

    direct = CATEGORY_TRANSLATIONS.get(text)
    if direct and direct.get(lang):
        return direct[lang]

    # Fallback pentru denumiri introduse ușor diferit în admin.
    normalized = text.lower().replace("ă", "a").replace("â", "a").replace("î", "i").replace("ș", "s").replace("ş", "s").replace("ț", "t").replace("ţ", "t")
    aliases = {
        "bluze": "Bluze",
        "rochii": "Rochii",
        "fuste": "Fuste",
        "costume": "Costume",
        "costume de dama": "Costume de dama",
        "geci si paltoane": "Geci și paltoane",
    }
    key = aliases.get(normalized)
    if key and CATEGORY_TRANSLATIONS.get(key, {}).get(lang):
        return CATEGORY_TRANSLATIONS[key][lang]

    return text

def display_category_name(row, lang=None):
    lang = lang or session.get("lang", "ro")
    if row is None:
        return ""

    if lang != "ro":
        try:
            value = row[f"name_{lang}"]
            if value and str(value).strip():
                return value
        except Exception:
            pass

    try:
        return translate_known_category_text(row["name"], lang)
    except Exception:
        return ""

def display_category_description(row, lang=None):
    lang = lang or session.get("lang", "ro")
    if row is None:
        return ""

    if lang != "ro":
        try:
            value = row[f"description_{lang}"]
            if value and str(value).strip():
                return value
        except Exception:
            pass

    try:
        return translate_known_category_text(row["description"], lang)
    except Exception:
        return ""

def localize_category(row):
    if row is None:
        return None
    item = dict(row)
    item["name_original"] = item.get("name") or ""
    item["description_original"] = item.get("description") or ""
    item["name"] = display_category_name(row)
    item["description"] = display_category_description(row)
    return item


# Traduceri pentru meniu și elemente folosite direct prin {{ t.cheie }}.
TEXT.update({
    "hu": {"home":"Kezdőlap","products":"Termékek","promos":"Akciók","contact":"Kapcsolat","cart":"Kosár","login":"Belépés","register":"Regisztráció","admin":"Adminisztrátor","hero":"Divat neked, minden nap","see_products":"Termékek megtekintése","my_cart":"Kosaram"},
    "bg": {"home":"Начало","products":"Продукти","promos":"Промоции","contact":"Контакт","cart":"Количка","login":"Вход","register":"Регистрация","admin":"Администратор","hero":"Мода за теб всеки ден","see_products":"Виж продуктите","my_cart":"Моята количка"},
    "el": {"home":"Αρχική","products":"Προϊόντα","promos":"Προσφορές","contact":"Επικοινωνία","cart":"Καλάθι","login":"Σύνδεση","register":"Εγγραφή","admin":"Διαχειριστής","hero":"Μόδα για εσένα κάθε μέρα","see_products":"Δες τα προϊόντα","my_cart":"Το καλάθι μου"},
    "ru": {"home":"Главная","products":"Товары","promos":"Акции","contact":"Контакты","cart":"Корзина","login":"Войти","register":"Регистрация","admin":"Администратор","hero":"Мода для вас каждый день","see_products":"Смотреть товары","my_cart":"Моя корзина"},
    "uk": {"home":"Головна","products":"Товари","promos":"Акції","contact":"Контакти","cart":"Кошик","login":"Увійти","register":"Реєстрація","admin":"Адміністратор","hero":"Мода для вас щодня","see_products":"Переглянути товари","my_cart":"Мій кошик"},
    "sr": {"home":"Почетна","products":"Производи","promos":"Акције","contact":"Контакт","cart":"Корпа","login":"Пријава","register":"Регистрација","admin":"Администратор","hero":"Мода за тебе, сваког дана","see_products":"Погледај производе","my_cart":"Моја корпа"},
})


# Texte pentru prima pagină (hero, SEO, categorii, produse noi)
HOME_PAGE_TEXT = {
    "ro": {
        "hero_desc": "Descoperă rochii, bluze, pantaloni, fuste, costume, geci și paltoane într-un magazin online modern, ușor de folosit și gândit pentru cumpărături rapide.",
        "seo_title": "Haine de damă elegante – Bella24",
        "seo_desc": "Bella24 este un magazin online de haine de damă elegante, dedicat femeilor care își doresc ținute moderne și confortabile. La noi găsești rochii elegante, bluze, compleuri, pantaloni, fuste și alte articole vestimentare atent selecționate, disponibile în diverse mărimi și culori, la prețuri accesibile. Comandă rapid online și descoperă colecțiile Bella24!",
        "categories_title": "Categorii",
        "categories_desc": "Alege rapid categoria potrivită pentru ținuta ta.",
        "see_category": "Vezi categoria",
        "new_products": "Produse noi",
        "no_products": "Nu există produse încă",
        "add_products_admin": "Adaugă produse din panoul de admin.",
        "go_admin": "Mergi la admin",
    },
    "en": {
        "hero_desc": "Discover dresses, blouses, trousers, skirts, suits, jackets and coats in a modern online store, easy to use and designed for fast shopping.",
        "seo_title": "Elegant women's clothing – Bella24",
        "seo_desc": "Bella24 is an online store for elegant women's clothing, dedicated to women who want modern and comfortable outfits. Here you can find elegant dresses, blouses, sets, trousers, skirts and other carefully selected fashion items, available in various sizes and colors at affordable prices. Order quickly online and discover the Bella24 collections!",
        "categories_title": "Categories",
        "categories_desc": "Quickly choose the right category for your outfit.",
        "see_category": "See category",
        "new_products": "New products",
        "no_products": "No products yet",
        "add_products_admin": "Add products from the admin panel.",
        "go_admin": "Go to admin",
    },
    "de": {
        "hero_desc": "Entdecke Kleider, Blusen, Hosen, Röcke, Kostüme, Jacken und Mäntel in einem modernen Online-Shop, einfach zu bedienen und für schnelles Einkaufen gedacht.",
        "seo_title": "Elegante Damenmode – Bella24",
        "seo_desc": "Bella24 ist ein Online-Shop für elegante Damenmode, für Frauen, die moderne und bequeme Outfits suchen. Bei uns findest du elegante Kleider, Blusen, Sets, Hosen, Röcke und andere sorgfältig ausgewählte Kleidungsstücke in verschiedenen Größen und Farben zu erschwinglichen Preisen. Bestelle schnell online und entdecke die Bella24-Kollektionen!",
        "categories_title": "Kategorien",
        "categories_desc": "Wähle schnell die passende Kategorie für dein Outfit.",
        "see_category": "Kategorie ansehen",
        "new_products": "Neue Produkte",
        "no_products": "Noch keine Produkte",
        "add_products_admin": "Füge Produkte im Adminbereich hinzu.",
        "go_admin": "Zum Adminbereich",
    },
    "hu": {
        "hero_desc": "Fedezz fel ruhákat, blúzokat, nadrágokat, szoknyákat, kosztümöket, dzsekiket és kabátokat egy modern, könnyen használható online áruházban.",
        "seo_title": "Elegáns női ruházat – Bella24",
        "seo_desc": "A Bella24 egy elegáns női ruházati webáruház, modern és kényelmes összeállításokat kereső nők számára. Nálunk elegáns ruhákat, blúzokat, szetteket, nadrágokat, szoknyákat és más gondosan válogatott termékeket találsz több méretben és színben, elérhető áron.",
        "categories_title": "Kategóriák",
        "categories_desc": "Válaszd ki gyorsan a megfelelő kategóriát.",
        "see_category": "Kategória megtekintése",
        "new_products": "Új termékek",
        "no_products": "Még nincsenek termékek",
        "add_products_admin": "Adj hozzá termékeket az admin felületen.",
        "go_admin": "Admin felület",
    },
    "bg": {
        "hero_desc": "Открий рокли, блузи, панталони, поли, костюми, якета и палта в модерен онлайн магазин, лесен за използване и създаден за бързо пазаруване.",
        "seo_title": "Елегантни дамски дрехи – Bella24",
        "seo_desc": "Bella24 е онлайн магазин за елегантни дамски дрехи, посветен на жените, които търсят модерни и удобни тоалети. При нас ще намериш рокли, блузи, комплекти, панталони, поли и други внимателно подбрани артикули в различни размери и цветове.",
        "categories_title": "Категории",
        "categories_desc": "Избери бързо подходящата категория.",
        "see_category": "Виж категорията",
        "new_products": "Нови продукти",
        "no_products": "Все още няма продукти",
        "add_products_admin": "Добави продукти от админ панела.",
        "go_admin": "Към администрацията",
    },
    "el": {
        "hero_desc": "Ανακάλυψε φορέματα, μπλούζες, παντελόνια, φούστες, κοστούμια, μπουφάν και παλτό σε ένα σύγχρονο ηλεκτρονικό κατάστημα, εύκολο στη χρήση και ιδανικό για γρήγορες αγορές.",
        "seo_title": "Κομψά γυναικεία ρούχα – Bella24",
        "seo_desc": "Το Bella24 είναι ένα ηλεκτρονικό κατάστημα με κομψά γυναικεία ρούχα, αφιερωμένο σε γυναίκες που αναζητούν μοντέρνες και άνετες εμφανίσεις. Εδώ θα βρεις φορέματα, μπλούζες, σετ, παντελόνια, φούστες και άλλα προσεκτικά επιλεγμένα είδη σε διάφορα μεγέθη και χρώματα.",
        "categories_title": "Κατηγορίες",
        "categories_desc": "Επίλεξε γρήγορα την κατάλληλη κατηγορία.",
        "see_category": "Δες την κατηγορία",
        "new_products": "Νέα προϊόντα",
        "no_products": "Δεν υπάρχουν ακόμη προϊόντα",
        "add_products_admin": "Πρόσθεσε προϊόντα από το πάνελ διαχείρισης.",
        "go_admin": "Πίνακας διαχείρισης",
    },
    "ru": {
        "hero_desc": "Откройте для себя платья, блузы, брюки, юбки, костюмы, куртки и пальто в современном онлайн-магазине, удобном и созданном для быстрых покупок.",
        "seo_title": "Элегантная женская одежда – Bella24",
        "seo_desc": "Bella24 — это интернет-магазин элегантной женской одежды для женщин, которые хотят современные и удобные образы. У нас вы найдете платья, блузы, комплекты, брюки, юбки и другие тщательно подобранные товары разных размеров и цветов.",
        "categories_title": "Категории",
        "categories_desc": "Быстро выберите подходящую категорию.",
        "see_category": "Смотреть категорию",
        "new_products": "Новые товары",
        "no_products": "Товаров пока нет",
        "add_products_admin": "Добавьте товары из панели администратора.",
        "go_admin": "В админ-панель",
    },
    "uk": {
        "hero_desc": "Відкрийте для себе сукні, блузи, штани, спідниці, костюми, куртки та пальта в сучасному інтернет-магазині, зручному та створеному для швидких покупок.",
        "seo_title": "Елегантний жіночий одяг – Bella24",
        "seo_desc": "Bella24 — це інтернет-магазин елегантного жіночого одягу для жінок, які шукають сучасні та комфортні образи. У нас ви знайдете сукні, блузи, комплекти, штани, спідниці та інші ретельно відібрані товари різних розмірів і кольорів.",
        "categories_title": "Категорії",
        "categories_desc": "Швидко оберіть відповідну категорію.",
        "see_category": "Переглянути категорію",
        "new_products": "Нові товари",
        "no_products": "Товарів поки немає",
        "add_products_admin": "Додайте товари з адмін-панелі.",
        "go_admin": "До адмін-панелі",
    },
    "sr": {
        "hero_desc": "Откриј хаљине, блузе, панталоне, сукње, костиме, јакне и капуте у модерној онлајн продавници, једноставној за коришћење и направљеној за брзу куповину.",
        "seo_title": "Елегантна женска одећа – Bella24",
        "seo_desc": "Bella24 је онлајн продавница елегантне женске одеће, намењена женама које желе модерне и удобне комбинације. Код нас можеш пронаћи хаљине, блузе, комплете, панталоне, сукње и друге пажљиво одабране артикле.",
        "categories_title": "Категорије",
        "categories_desc": "Брзо изабери одговарајућу категорију.",
        "see_category": "Погледај категорију",
        "new_products": "Нови производи",
        "no_products": "Још нема производа",
        "add_products_admin": "Додај производе из админ панела.",
        "go_admin": "Иди у админ",
    },
}

for _lang, _values in HOME_PAGE_TEXT.items():
    TEXT.setdefault(_lang, {}).update(_values)


# Traduceri automate pentru textele scrise direct în șabloane și pentru datele inițiale din baza de date.
# Astfel limba aleasă schimbă tot site-ul, nu doar meniul.
PAGE_TRANSLATIONS = {
    "de": {
        "Livrare rapidă în România":"Schnelle Lieferung in Rumänien", "Transport gratuit la comenzi peste 1001 lei":"Kostenloser Versand ab 1001 Lei", "Comenzile mele":"Meine Bestellungen", "Logout":"Abmelden", "Admin produse":"Produkte verwalten", "Admin categorii":"Kategorien verwalten", "Admin comenzi":"Bestellungen verwalten", "Logout admin":"Admin abmelden", "Asistent Bella24":"Bella24 Assistent", "Comenzi, retururi și sugestii":"Bestellungen, Rückgaben und Vorschläge", "Recomandări, mărimi, comenzi și ținute":"Empfehlungen, Größen, Bestellungen und Outfits", "Bună! Îți pot răspunde exact la ce întrebi:":"Hallo! Ich kann dir genau auf deine Fragen antworten:", "Trimite":"Senden",
        "Filtrează produsele":"Produkte filtern", "Caută produs":"Produkt suchen", "Categorie":"Kategorie", "Toate categoriile":"Alle Kategorien", "Mărime":"Größe", "Mărimi":"Größen", "Culoare":"Farbe", "Material":"Material", "Doar promoții":"Nur Angebote", "Aplică filtre":"Filter anwenden", "Nu există produse.":"Keine Produkte vorhanden.", "Promoție":"Angebot", "Promovare":"Angebot", "Adaugă în coș":"In den Warenkorb", "Produs":"Produkt", "Produse noi":"Neue Produkte", "Nu există produse încă":"Es gibt noch keine Produkte", "Adaugă produse din panoul de admin.":"Füge Produkte im Adminbereich hinzu.", "Mergi la admin":"Zum Adminbereich", "Categorii":"Kategorien", "Alege rapid categoria potrivită pentru ținuta ta.":"Wähle schnell die passende Kategorie für dein Outfit.", "Vezi categoria":"Kategorie ansehen",
        "CONTACT BELLA24":"KONTAKT BELLA24", "Ai o întrebare? Scrie-ne fără emoții 💌":"Eine Frage? Schreib uns ganz einfach 💌", "Pentru mărimi, comenzi, livrare sau retururi, suntem aici.":"Für Größen, Bestellungen, Lieferung oder Rückgaben sind wir da.", "Scrie-ne pe WhatsApp":"Schreib uns auf WhatsApp", "Consilier Bella24":"Bella24 Berater", "Te ajutăm rapid.":"Wir helfen dir schnell.", "Telefon / WhatsApp":"Telefon / WhatsApp", "Livrare":"Lieferung", "Transport gratuit peste 1001 lei.":"Kostenloser Versand ab 1001 Lei.", "Program":"Öffnungszeiten", "Luni - Vineri 09:00 - 18:00":"Montag - Freitag 09:00 - 18:00",
        "PRODUSE ADĂUGATE":"HINZUGEFÜGTE PRODUKTE", "Produsele din coș":"Produkte im Warenkorb", "Fără poză":"Kein Bild", "Mărime:":"Größe:", "Cantitate":"Menge", "Total produs":"Produkt gesamt", "Șterge":"Löschen", "Actualizează coșul":"Warenkorb aktualisieren", "Continuă cumpărăturile":"Weiter einkaufen", "Coșul este gol":"Der Warenkorb ist leer", "Adaugă produse în coș pentru a continua comanda.":"Füge Produkte hinzu, um die Bestellung fortzusetzen.", "Vezi produsele":"Produkte ansehen", "SUMAR COMANDĂ":"BESTELLÜBERSICHT", "Total de plată":"Zu zahlender Betrag", "Subtotal":"Zwischensumme", "Reducere":"Rabatt", "Transport":"Versand", "Gratuit":"Kostenlos", "Total":"Gesamt", "Finalizează comanda":"Bestellung abschließen",
        "CHECKOUT":"KASSE", "Completează datele de livrare și metoda de plată.":"Fülle Lieferdaten und Zahlungsart aus.", "Plata cu cardul este momentan în lucru și nu funcționează.":"Kartenzahlung ist in Arbeit und funktioniert derzeit nicht.", "Comenzile se pot plasa":"Bestellungen können aufgegeben werden", "ramburs la curier":"per Nachnahme beim Kurier", "sau direct pe WhatsApp.":"oder direkt per WhatsApp.", "Trimite comanda pe WhatsApp":"Bestellung per WhatsApp senden", "Nume complet":"Vollständiger Name", "Telefon":"Telefon", "Adresă completă":"Vollständige Adresse", "Metodă de plată":"Zahlungsart", "Ramburs la curier":"Nachnahme beim Kurier", "Card online — în lucru, momentan indisponibil":"Online-Karte — in Arbeit, derzeit nicht verfügbar", "Plata cu cardul":"Kartenzahlung", "Plasează comanda":"Bestellung aufgeben", "Comanda ta":"Deine Bestellung", "După plasarea comenzii, o poți vedea în „Comenzile mele” dacă folosești același email ca în cont.":"Nach der Bestellung findest du sie unter „Meine Bestellungen“, wenn du dieselbe E-Mail verwendest.",
        "Bine ai revenit!":"Willkommen zurück!", "Comenzi salvate":"Gespeicherte Bestellungen", "Vezi rapid comenzile plasate.":"Sieh deine Bestellungen schnell ein.", "Livrare ușoară":"Einfache Lieferung", "Datele tale rămân pregătite.":"Deine Daten bleiben gespeichert.", "Autentificare":"Anmeldung", "Parolă":"Passwort", "Intră în cont":"Einloggen", "Creează cont Bella24":"Bella24 Konto erstellen", "ÎNREGISTRARE BELLA24":"BELLA24 REGISTRIERUNG", "Creează contul tău":"Erstelle dein Konto", "Ținute favorite":"Lieblingsoutfits", "Comanzi mai rapid produsele preferate.":"Bestelle deine Lieblingsprodukte schneller.", "Istoric comenzi":"Bestellverlauf", "Vezi comenzile tale într-un singur loc.":"Sieh alle Bestellungen an einem Ort.", "Înregistrare":"Registrierung", "Creează cont":"Konto erstellen", "Ai deja cont?":"Hast du schon ein Konto?",
        "CONTUL MEU":"MEIN KONTO", "Aici vezi comenzile plasate și poți solicita retur dacă este nevoie.":"Hier siehst du deine Bestellungen und kannst bei Bedarf eine Rückgabe anfordern.", "Comandă":"Bestellung", "Data":"Datum", "Acțiune":"Aktion", "Vezi comanda":"Bestellung ansehen", "Nu ai comenzi încă":"Du hast noch keine Bestellungen", "După ce plasezi o comandă cu emailul contului tău, o vei vedea aici.":"Nachdem du mit deiner Konto-E-Mail bestellt hast, erscheint sie hier.",
        "Rochii":"Kleider", "Bluze":"Blusen", "Fuste":"Röcke", "Costume":"Kostüme", "Geci și paltoane":"Jacken und Mäntel", "Rochii de zi și de seară.":"Tages- und Abendkleider.", "Bluze elegante, casual și moderne.":"Elegante, lässige und moderne Blusen.", "Fuste feminine și versatile.":"Feminine und vielseitige Röcke.", "Costume elegante de damă.":"Elegante Damenkostüme.", "Articole de exterior.":"Outdoor-Artikel.", "Rochie midi verde petrol cu broderie florală":"Petrolgrünes Midikleid mit Blumenstickerei", "Rochie de seară turcoaz Amira – lungă, elegantă, cu decolteu în V":"Türkises Abendkleid Amira – lang, elegant, mit V-Ausschnitt", "Rochie elegantă de damă, în nuanță verde petrol, cu croi drept și mânecă 3/4. Modelul are broderie florală decorativă la bază și fermoar la spate. Potrivită pentru birou, cununii, botezuri, evenimente sau ocazii speciale.":"Elegantes Damenkleid in Petrolgrün, gerader Schnitt und 3/4-Ärmel. Das Modell hat dekorative Blumenstickerei am Saum und einen Reißverschluss hinten. Geeignet für Büro, Hochzeiten, Taufen, Events oder besondere Anlässe.", "Verde Petrol":"Petrolgrün", "Turcoaz, Albastru, Roz":"Türkis, Blau, Rosa", "stofă fină / poliester elastic":"feiner Stoff / elastischer Polyester", "Material fluid, elegant, cu aspect satinat":"Fließendes, elegantes Material mit Satinoptik"
    }
}

# Pentru limbile noi, folosim aceeași listă de expresii traduse cu atenție pentru interfață.
# Produsul și categoriile inițiale sunt traduse astfel încât site-ul să nu rămână în română.
PAGE_TRANSLATIONS["hu"] = {k: v for k, v in {
"Livrare rapidă în România":"Gyors szállítás Romániában","Transport gratuit la comenzi peste 1001 lei":"Ingyenes szállítás 1001 lej felett","Comenzile mele":"Rendeléseim","Logout":"Kijelentkezés","Admin produse":"Termék admin","Admin categorii":"Kategória admin","Admin comenzi":"Rendelés admin","Asistent Bella24":"Bella24 asszisztens","Comenzi, retururi și sugestii":"Rendelések, visszaküldések és javaslatok","Filtrează produsele":"Termékek szűrése","Caută produs":"Termék keresése","Categorie":"Kategória","Toate categoriile":"Összes kategória","Mărime":"Méret","Mărimi":"Méretek","Culoare":"Szín","Material":"Anyag","Doar promoții":"Csak akciók","Aplică filtre":"Szűrők alkalmazása","Nu există produse.":"Nincsenek termékek.","Promoție":"Akció","Promovare":"Akció","Adaugă în coș":"Kosárba","Produse noi":"Új termékek","CONTACT BELLA24":"BELLA24 KAPCSOLAT","Ai o întrebare? Scrie-ne fără emoții 💌":"Van kérdésed? Írj bátran 💌","Pentru mărimi, comenzi, livrare sau retururi, suntem aici.":"Méret, rendelés, szállítás vagy visszaküldés ügyben itt vagyunk.","Scrie-ne pe WhatsApp":"Írj WhatsAppon","Consilier Bella24":"Bella24 tanácsadó","Te ajutăm rapid.":"Gyorsan segítünk.","Livrare":"Szállítás","Program":"Nyitvatartás","Luni - Vineri 09:00 - 18:00":"Hétfő - Péntek 09:00 - 18:00","Produsele din coș":"Termékek a kosárban","Coșul este gol":"A kosár üres","Vezi produsele":"Termékek megtekintése","Finalizează comanda":"Rendelés befejezése","Total de plată":"Fizetendő összeg","Subtotal":"Részösszeg","Reducere":"Kedvezmény","Transport":"Szállítás","Gratuit":"Ingyenes","Total":"Összesen","Bine ai revenit!":"Üdv újra!","Autentificare":"Bejelentkezés","Parolă":"Jelszó","Intră în cont":"Belépés","Creează cont":"Fiók létrehozása","Rochii":"Ruhák","Bluze":"Blúzok","Fuste":"Szoknyák","Costume":"Kosztümök","Geci și paltoane":"Dzsekik és kabátok","Rochie midi verde petrol cu broderie florală":"Petrolzöld midi ruha virágos hímzéssel","Rochie de seară turcoaz Amira – lungă, elegantă, cu decolteu în V":"Türkiz Amira estélyi ruha – hosszú, elegáns, V-kivágással","Verde Petrol":"Petrolzöld"}.items()}
PAGE_TRANSLATIONS["bg"] = {k: v for k, v in {
"Livrare rapidă în România":"Бърза доставка в Румъния","Transport gratuit la comenzi peste 1001 lei":"Безплатна доставка над 1001 леи","Comenzile mele":"Моите поръчки","Logout":"Изход","Asistent Bella24":"Асистент Bella24","Comenzi, retururi și sugestii":"Поръчки, връщания и предложения","Filtrează produsele":"Филтрирай продуктите","Caută produs":"Търси продукт","Categorie":"Категория","Toate categoriile":"Всички категории","Mărime":"Размер","Culoare":"Цвят","Material":"Материал","Doar promoții":"Само промоции","Aplică filtre":"Приложи филтри","Promoție":"Промоция","Promovare":"Промоция","Adaugă în coș":"Добави в количката","CONTACT BELLA24":"КОНТАКТ BELLA24","Ai o întrebare? Scrie-ne fără emoții 💌":"Имаш въпрос? Пиши ни спокойно 💌","Scrie-ne pe WhatsApp":"Пиши ни в WhatsApp","Consilier Bella24":"Консултант Bella24","Te ajutăm rapid.":"Помагаме бързо.","Livrare":"Доставка","Program":"Работно време","Coșul este gol":"Количката е празна","Vezi produsele":"Виж продуктите","Finalizează comanda":"Завърши поръчката","Total de plată":"Общо за плащане","Subtotal":"Междинна сума","Reducere":"Отстъпка","Transport":"Доставка","Gratuit":"Безплатно","Total":"Общо","Bine ai revenit!":"Добре дошъл отново!","Autentificare":"Вход","Parolă":"Парола","Intră în cont":"Влез","Creează cont":"Създай акаунт","Rochii":"Рокли","Bluze":"Блузи","Fuste":"Поли","Costume":"Костюми","Geci și paltoane":"Якета и палта","Rochie midi verde petrol cu broderie florală":"Петроленозелена миди рокля с флорална бродерия","Verde Petrol":"Петроленозелено"}.items()}
PAGE_TRANSLATIONS["el"] = {k: v for k, v in {
"Livrare rapidă în România":"Γρήγορη παράδοση στη Ρουμανία","Transport gratuit la comenzi peste 1001 lei":"Δωρεάν μεταφορά για παραγγελίες άνω των 1001 λέι","Comenzile mele":"Οι παραγγελίες μου","Logout":"Αποσύνδεση","Asistent Bella24":"Βοηθός Bella24","Comenzi, retururi și sugestii":"Παραγγελίες, επιστροφές και προτάσεις","Filtrează produsele":"Φίλτραρε τα προϊόντα","Caută produs":"Αναζήτηση προϊόντος","Categorie":"Κατηγορία","Toate categoriile":"Όλες οι κατηγορίες","Mărime":"Μέγεθος","Culoare":"Χρώμα","Material":"Υλικό","Doar promoții":"Μόνο προσφορές","Aplică filtre":"Εφαρμογή φίλτρων","Promoție":"Προσφορά","Promovare":"Προσφορά","Adaugă în coș":"Προσθήκη στο καλάθι","CONTACT BELLA24":"ΕΠΙΚΟΙΝΩΝΙΑ BELLA24","Ai o întrebare? Scrie-ne fără emoții 💌":"Έχεις ερώτηση; Γράψε μας άνετα 💌","Scrie-ne pe WhatsApp":"Γράψε μας στο WhatsApp","Consilier Bella24":"Σύμβουλος Bella24","Te ajutăm rapid.":"Βοηθάμε γρήγορα.","Livrare":"Παράδοση","Program":"Ωράριο","Coșul este gol":"Το καλάθι είναι άδειο","Vezi produsele":"Δες τα προϊόντα","Finalizează comanda":"Ολοκλήρωση παραγγελίας","Total de plată":"Σύνολο πληρωμής","Subtotal":"Μερικό σύνολο","Reducere":"Έκπτωση","Transport":"Μεταφορά","Gratuit":"Δωρεάν","Total":"Σύνολο","Bine ai revenit!":"Καλώς ήρθες ξανά!","Autentificare":"Σύνδεση","Parolă":"Κωδικός","Intră în cont":"Είσοδος","Creează cont":"Δημιουργία λογαριασμού","Rochii":"Φορέματα","Bluze":"Μπλούζες","Fuste":"Φούστες","Costume":"Κοστούμια","Geci și paltoane":"Μπουφάν και παλτά","Rochie midi verde petrol cu broderie florală":"Πετρόλ μίντι φόρεμα με floral κέντημα","Verde Petrol":"Πετρόλ πράσινο"}.items()}
PAGE_TRANSLATIONS["ru"] = {k: v for k, v in {
"Livrare rapidă în România":"Быстрая доставка по Румынии","Transport gratuit la comenzi peste 1001 lei":"Бесплатная доставка от 1001 лея","Comenzile mele":"Мои заказы","Logout":"Выйти","Asistent Bella24":"Ассистент Bella24","Comenzi, retururi și sugestii":"Заказы, возвраты и рекомендации","Filtrează produsele":"Фильтр товаров","Caută produs":"Искать товар","Categorie":"Категория","Toate categoriile":"Все категории","Mărime":"Размер","Culoare":"Цвет","Material":"Материал","Doar promoții":"Только акции","Aplică filtre":"Применить фильтры","Promoție":"Акция","Promovare":"Акция","Adaugă în coș":"В корзину","CONTACT BELLA24":"КОНТАКТЫ BELLA24","Ai o întrebare? Scrie-ne fără emoții 💌":"Есть вопрос? Напишите нам 💌","Scrie-ne pe WhatsApp":"Напишите в WhatsApp","Consilier Bella24":"Консультант Bella24","Te ajutăm rapid.":"Мы быстро поможем.","Livrare":"Доставка","Program":"График","Coșul este gol":"Корзина пуста","Vezi produsele":"Смотреть товары","Finalizează comanda":"Оформить заказ","Total de plată":"К оплате","Subtotal":"Промежуточный итог","Reducere":"Скидка","Transport":"Доставка","Gratuit":"Бесплатно","Total":"Итого","Bine ai revenit!":"С возвращением!","Autentificare":"Авторизация","Parolă":"Пароль","Intră în cont":"Войти","Creează cont":"Создать аккаунт","Rochii":"Платья","Bluze":"Блузы","Fuste":"Юбки","Costume":"Костюмы","Geci și paltoane":"Куртки и пальто","Rochie midi verde petrol cu broderie florală":"Миди-платье цвета петрол с цветочной вышивкой","Rochie de seară turcoaz Amira – lungă, elegantă, cu decolteu în V":"Бирюзовое вечернее платье Amira — длинное, элегантное, с V-образным вырезом","Verde Petrol":"Петроловый зелёный"}.items()}
PAGE_TRANSLATIONS["uk"] = {k: v for k, v in {
"Livrare rapidă în România":"Швидка доставка по Румунії","Transport gratuit la comenzi peste 1001 lei":"Безкоштовна доставка від 1001 лея","Comenzile mele":"Мої замовлення","Logout":"Вийти","Asistent Bella24":"Асистент Bella24","Comenzi, retururi și sugestii":"Замовлення, повернення та поради","Filtrează produsele":"Фільтрувати товари","Caută produs":"Пошук товару","Categorie":"Категорія","Toate categoriile":"Усі категорії","Mărime":"Розмір","Culoare":"Колір","Material":"Матеріал","Doar promoții":"Лише акції","Aplică filtre":"Застосувати фільтри","Promoție":"Акція","Promovare":"Акція","Adaugă în coș":"Додати в кошик","CONTACT BELLA24":"КОНТАКТИ BELLA24","Ai o întrebare? Scrie-ne fără emoții 💌":"Є питання? Напишіть нам 💌","Scrie-ne pe WhatsApp":"Напишіть у WhatsApp","Consilier Bella24":"Консультант Bella24","Te ajutăm rapid.":"Ми швидко допоможемо.","Livrare":"Доставка","Program":"Графік","Coșul este gol":"Кошик порожній","Vezi produsele":"Переглянути товари","Finalizează comanda":"Оформити замовлення","Total de plată":"До сплати","Subtotal":"Проміжний підсумок","Reducere":"Знижка","Transport":"Доставка","Gratuit":"Безкоштовно","Total":"Разом","Bine ai revenit!":"З поверненням!","Autentificare":"Авторизація","Parolă":"Пароль","Intră în cont":"Увійти","Creează cont":"Створити акаунт","Rochii":"Сукні","Bluze":"Блузи","Fuste":"Спідниці","Costume":"Костюми","Geci și paltoane":"Куртки та пальта","Rochie midi verde petrol cu broderie florală":"Міді-сукня кольору петрол із квітковою вишивкою","Verde Petrol":"Петроловий зелений"}.items()}
PAGE_TRANSLATIONS["sr"] = {k: v for k, v in {
"Livrare rapidă în România":"Брза достава у Румунији","Transport gratuit la comenzi peste 1001 lei":"Бесплатна достава преко 1001 леја","Comenzile mele":"Моје поруџбине","Logout":"Одјава","Asistent Bella24":"Bella24 асистент","Comenzi, retururi și sugestii":"Поруџбине, поврати и предлози","Filtrează produsele":"Филтрирај производе","Caută produs":"Претражи производ","Categorie":"Категорија","Toate categoriile":"Све категорије","Mărime":"Величина","Culoare":"Боја","Material":"Материјал","Doar promoții":"Само акције","Aplică filtre":"Примени филтере","Promoție":"Акција","Promovare":"Акција","Adaugă în coș":"Додај у корпу","CONTACT BELLA24":"КОНТАКТ BELLA24","Ai o întrebare? Scrie-ne fără emoții 💌":"Имаш питање? Пиши нам слободно 💌","Scrie-ne pe WhatsApp":"Пиши нам на WhatsApp","Consilier Bella24":"Bella24 саветник","Te ajutăm rapid.":"Брзо помажемо.","Livrare":"Достава","Program":"Радно време","Coșul este gol":"Корпа је празна","Vezi produsele":"Погледај производе","Finalizează comanda":"Заврши поруџбину","Total de plată":"Укупно за плаћање","Subtotal":"Међузбир","Reducere":"Попуст","Transport":"Достава","Gratuit":"Бесплатно","Total":"Укупно","Bine ai revenit!":"Добро дошли назад!","Autentificare":"Пријава","Parolă":"Лозинка","Intră în cont":"Уђи","Creează cont":"Креирај налог","Rochii":"Хаљине","Bluze":"Блузе","Fuste":"Сукње","Costume":"Костими","Geci și paltoane":"Јакне и капути","Rochie midi verde petrol cu broderie florală":"Петрол зелена миди хаљина са цветним везом","Verde Petrol":"Петрол зелена"}.items()}

# Nu completăm limbile noi cu germană. Dacă lipsește o cheie, rămâne română sau fallback-ul controlat.

@app.after_request
def translate_rendered_html(response):
    lang = session.get("lang", "ro")
    if lang == "ro" or lang not in PAGE_TRANSLATIONS:
        return response
    ctype = response.headers.get("Content-Type", "")
    if "text/html" not in ctype.lower():
        return response
    html = response.get_data(as_text=True)
    # Mai întâi expresiile mai lungi, ca să nu stricăm bucăți din propoziții.
    for src, dst in sorted(PAGE_TRANSLATIONS[lang].items(), key=lambda item: len(item[0]), reverse=True):
        html = html.replace(src, dst)
    response.set_data(html)
    response.headers["Content-Length"] = str(len(response.get_data()))
    return response


def tr():
    return TEXT.get(session.get("lang", "ro"), TEXT["ro"])


@app.context_processor
def inject():
    return {
        "t": tr(),
        "cart_count": sum(session.get("cart", {}).values()),
        "current_lang": session.get("lang", "ro"),
        "lang_options": LANG_OPTIONS,
    }


def db():
    con = sqlite3.connect(DB_PATH, timeout=60, check_same_thread=False)
    con.row_factory = sqlite3.Row
    con.execute("PRAGMA journal_mode=WAL")
    con.execute("PRAGMA synchronous=NORMAL")
    con.execute("PRAGMA busy_timeout=60000")
    return con


def allowed(name):
    return "." in name and name.rsplit(".", 1)[1].lower() in ALLOWED_EXT


def save_upload(file):
    if not file or not file.filename or not allowed(file.filename):
        return None

    filename = f"{uuid4().hex}_{secure_filename(file.filename)}"
    file.save(UPLOAD_DIR / filename)
    return f"images/products/{filename}"


def parse_cart_key(cart_key):
    cart_key = str(cart_key)
    if "|" in cart_key:
        product_id, selected_size = cart_key.split("|", 1)
        return product_id, selected_size
    return cart_key, "Universală"


def admin_required(fn):
    @wraps(fn)
    def wrapper(*args, **kwargs):
        if not session.get("admin_logged"):
            flash("Intră în admin mai întâi.")
            return redirect(url_for("admin_login"))
        return fn(*args, **kwargs)

    return wrapper


def ensure_column(con, table, column, definition):
    existing = con.execute(f"PRAGMA table_info({table})").fetchall()
    columns = [row["name"] for row in existing]

    if column not in columns:
        con.execute(f"ALTER TABLE {table} ADD COLUMN {column} {definition}")


def init_db():
    con = db()
    cur = con.cursor()

    cur.executescript(
        """
        CREATE TABLE IF NOT EXISTS categories (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            name TEXT NOT NULL,
            slug TEXT UNIQUE NOT NULL,
            description TEXT,
            is_active INTEGER DEFAULT 1
        );

        CREATE TABLE IF NOT EXISTS products (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            category_id INTEGER,
            name TEXT NOT NULL,
            description TEXT,
            price REAL NOT NULL,
            old_price REAL,
            stock INTEGER DEFAULT 0,
            size TEXT,
            color TEXT,
            material TEXT,
            is_promo INTEGER DEFAULT 0,
            is_active INTEGER DEFAULT 1,
            created_at TEXT DEFAULT CURRENT_TIMESTAMP
        );

        CREATE TABLE IF NOT EXISTS product_images (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            product_id INTEGER NOT NULL,
            image TEXT NOT NULL,
            is_main INTEGER DEFAULT 0
        );

        CREATE TABLE IF NOT EXISTS users (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            name TEXT,
            email TEXT UNIQUE,
            password TEXT,
            created_at TEXT DEFAULT CURRENT_TIMESTAMP
        );

        CREATE TABLE IF NOT EXISTS orders (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            name TEXT,
            email TEXT,
            phone TEXT,
            address TEXT,
            payment TEXT,
            total REAL,
            status TEXT DEFAULT 'Nouă',
            admin_note TEXT,
            created_at TEXT DEFAULT CURRENT_TIMESTAMP
        );

        CREATE TABLE IF NOT EXISTS order_items (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            order_id INTEGER,
            product_id INTEGER,
            product_name TEXT,
            qty INTEGER,
            price REAL,
            image TEXT,
            selected_size TEXT
        );

        CREATE TABLE IF NOT EXISTS returns (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            order_id INTEGER NOT NULL,
            user_email TEXT,
            reason TEXT,
            status TEXT DEFAULT 'Cerere trimisă',
            admin_note TEXT,
            created_at TEXT DEFAULT CURRENT_TIMESTAMP
        );

        CREATE TABLE IF NOT EXISTS shipments (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            order_id INTEGER NOT NULL,
            courier TEXT,
            awb TEXT,
            tracking_url TEXT,
            delivery_type TEXT DEFAULT 'address',
            easybox_name TEXT,
            easybox_address TEXT,
            status TEXT DEFAULT 'Creat',
            created_at TEXT DEFAULT CURRENT_TIMESTAMP
        );

        CREATE TABLE IF NOT EXISTS invoices (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            order_id INTEGER NOT NULL,
            invoice_number TEXT,
            customer_name TEXT,
            customer_email TEXT,
            customer_address TEXT,
            subtotal REAL,
            shipping REAL,
            discount REAL,
            total REAL,
            created_by TEXT DEFAULT 'admin',
            created_at TEXT DEFAULT CURRENT_TIMESTAMP
        );

        CREATE TABLE IF NOT EXISTS admins (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            username TEXT UNIQUE,
            password TEXT
        );
        """
    )

    ensure_column(con, "order_items", "selected_size", "TEXT")
    ensure_column(con, "orders", "admin_note", "TEXT")
    ensure_column(con, "returns", "admin_note", "TEXT")
    ensure_column(con, "shipments", "delivery_type", "TEXT DEFAULT 'address'")
    ensure_column(con, "shipments", "easybox_name", "TEXT")
    ensure_column(con, "shipments", "easybox_address", "TEXT")
    for _lang in DESCRIPTION_LANGS:
        ensure_column(con, "products", f"description_{_lang}", "TEXT")
        ensure_column(con, "products", f"name_{_lang}", "TEXT")
        ensure_column(con, "products", f"color_{_lang}", "TEXT")
        ensure_column(con, "products", f"material_{_lang}", "TEXT")
    for _lang in CATEGORY_LANGS:
        ensure_column(con, "categories", f"name_{_lang}", "TEXT")
        ensure_column(con, "categories", f"description_{_lang}", "TEXT")

    if cur.execute("SELECT COUNT(*) FROM admins").fetchone()[0] == 0:
        cur.execute(
            "INSERT INTO admins(username, password) VALUES(?, ?)",
            ("Miscoci", generate_password_hash("Bella24!Admin#2026")),
        )

    if cur.execute("SELECT COUNT(*) FROM categories").fetchone()[0] == 0:
        cats = [
            ("Bluze", "bluze", "Bluze elegante, casual și moderne."),
            ("Rochii", "rochii", "Rochii de zi, rochii elegante și rochii de seară."),
            ("Fuste", "fuste", "Fuste feminine și versatile."),
            ("Costume", "costume", "Costume elegante de damă."),
            ("Geci și paltoane", "geci-paltoane", "Articole de exterior."),
        ]
        cur.executemany(
            "INSERT INTO categories(name, slug, description) VALUES(?, ?, ?)",
            cats,
        )
        cur.execute(
    "UPDATE admins SET username = ?, password = ? WHERE id = 1",
    ("Miscoci", generate_password_hash("Bella24!Admin#2026")),
)

        con.commit()
        con.close()

def get_order_financials(order_id):
    con = db()
    items = con.execute(
        "SELECT * FROM order_items WHERE order_id = ?",
        (order_id,),
    ).fetchall()

    order = con.execute(
        "SELECT * FROM orders WHERE id = ?",
        (order_id,),
    ).fetchone()
    con.close()

    subtotal = sum((item["qty"] or 0) * (item["price"] or 0) for item in items)
    discount = subtotal * 0.05 if sum(item["qty"] or 0 for item in items) >= 2 else 0
    shipping = 0 if subtotal - discount >= 1001 or subtotal == 0 else 19.99
    total = order["total"] if order and order["total"] is not None else subtotal - discount + shipping

    return subtotal, discount, shipping, total


def generate_invoice_number(invoice_id):
    return f"BLL-{invoice_id:06d}"


def generate_awb_number(order_id, courier, delivery_type="address"):
    random_part = uuid4().hex[:10].upper()

    if courier == "Fan Courier":
        return f"FAN-{order_id}-{random_part}"

    if courier == "Sameday" and delivery_type == "easybox":
        return f"SDY-BOX-{order_id}-{random_part}"

    if courier == "Sameday":
        return f"SDY-{order_id}-{random_part}"

    return f"AWB-{order_id}-{random_part}"


def generate_tracking_url(courier, awb):
    if courier == "Fan Courier":
        return f"https://www.fancourier.ro/awb-tracking/?xawb={awb}"

    if courier == "Sameday":
        return f"https://sameday.ro/awb-tracking/?awb={awb}"

    return ""


@app.route("/set-language/<lang>")
def set_language(lang):
    session["lang"] = lang if lang in TEXT else "ro"
    return redirect(request.referrer or url_for("index"))


@app.route("/")
def index():
    con = db()

    cats = con.execute(
        "SELECT * FROM categories WHERE is_active = 1"
    ).fetchall()

    featured = con.execute(
        """
        SELECT p.*,
               (
                   SELECT image
                   FROM product_images
                   WHERE product_id = p.id
                   ORDER BY is_main DESC, id
                   LIMIT 1
               ) AS image
        FROM products p
        WHERE is_active = 1
        ORDER BY id DESC
        LIMIT 4
        """
    ).fetchall()

    con.close()
    cats = [localize_category(row) for row in cats]
    featured = [localize_product(row) for row in featured]
    return render_template("index.html", categories=cats, featured=featured)


@app.route("/products")
def products():
    q = request.args.get("q", "").strip()
    category = request.args.get("category", "")
    size = request.args.get("size", "")
    color = request.args.get("color", "")
    material = request.args.get("material", "")
    promo = request.args.get("promo", "")

    sql = """
        SELECT p.*,
               c.name AS category_name,
               (
                   SELECT image
                   FROM product_images
                   WHERE product_id = p.id
                   ORDER BY is_main DESC, id
                   LIMIT 1
               ) AS image
        FROM products p
        LEFT JOIN categories c ON c.id = p.category_id
        WHERE p.is_active = 1
    """

    params = []

    if q:
        sql += " AND (p.name LIKE ? OR p.description LIKE ?)"
        params += [f"%{q}%", f"%{q}%"]

    if category:
        sql += " AND c.slug = ?"
        params.append(category)

    if size:
        sql += " AND p.size LIKE ?"
        params.append(f"%{size}%")

    if color:
        sql += " AND p.color LIKE ?"
        params.append(f"%{color}%")

    if material:
        sql += " AND p.material LIKE ?"
        params.append(f"%{material}%")

    if promo:
        sql += " AND p.is_promo = 1"

    sql += " ORDER BY p.id DESC"

    con = db()
    items = con.execute(sql, params).fetchall()
    cats = con.execute("SELECT * FROM categories WHERE is_active = 1").fetchall()
    con.close()
    items = [localize_product(row) for row in items]
    cats = [localize_category(row) for row in cats]

    return render_template("products.html", products=items, categories=cats)


@app.route("/product/<int:pid>")
def product_detail(pid):
    con = db()

    product = con.execute(
        """
        SELECT p.*, c.name AS category_name
        FROM products p
        LEFT JOIN categories c ON c.id = p.category_id
        WHERE p.id = ?
        """,
        (pid,),
    ).fetchone()

    images = con.execute(
        """
        SELECT *
        FROM product_images
        WHERE product_id = ?
        ORDER BY is_main DESC, id
        """,
        (pid,),
    ).fetchall()

    con.close()

    if not product:
        return redirect(url_for("products"))

    product = localize_product(product)
    return render_template("product_detail.html", product=product, images=images)


@app.post("/cart/add/<int:pid>")
def add_to_cart(pid):
    qty = int(request.form.get("qty", 1) or 1)
    selected_size = request.form.get("selected_size", "Universală").strip()

    cart_data = session.get("cart", {})
    cart_key = f"{pid}|{selected_size}"

    cart_data[cart_key] = cart_data.get(cart_key, 0) + qty
    session["cart"] = cart_data

    flash(f"Produs adăugat în coș. Mărime: {selected_size}")
    return redirect(request.referrer or url_for("cart"))


@app.route("/cart", methods=["GET", "POST"])
def cart():
    if request.method == "POST":
        updated_cart = {}

        for key, value in request.form.items():
            if key.startswith("qty_"):
                cart_key = key.replace("qty_", "")

                try:
                    qty = int(value)
                    if qty > 0:
                        updated_cart[cart_key] = qty
                except ValueError:
                    pass

        session["cart"] = updated_cart
        return redirect(url_for("cart"))

    cart_data = session.get("cart", {})
    product_ids = []

    for cart_key in cart_data.keys():
        product_id, selected_size = parse_cart_key(cart_key)
        if product_id not in product_ids:
            product_ids.append(product_id)

    items = []
    subtotal = 0

    if product_ids:
        placeholders = ",".join("?" * len(product_ids))
        con = db()

        rows = con.execute(
            f"""
            SELECT p.*,
                   (
                       SELECT image
                       FROM product_images
                       WHERE product_id = p.id
                       ORDER BY is_main DESC, id
                       LIMIT 1
                   ) AS image
            FROM products p
            WHERE id IN ({placeholders})
            """,
            product_ids,
        ).fetchall()

        con.close()

        product_map = {str(row["id"]): row for row in rows}

        for cart_key, qty in cart_data.items():
            product_id, selected_size = parse_cart_key(cart_key)
            row = product_map.get(str(product_id))

            if row:
                total = qty * row["price"]
                subtotal += total
                items.append(
                    {
                        "cart_key": cart_key,
                        "p": row,
                        "qty": qty,
                        "selected_size": selected_size,
                        "total": total,
                    }
                )

    total_qty = sum(cart_data.values())
    discount = subtotal * 0.05 if total_qty >= 2 else 0
    shipping = 0 if subtotal - discount >= 1001 or subtotal == 0 else 19.99
    total = subtotal - discount + shipping

    return render_template(
        "cart.html",
        items=items,
        subtotal=subtotal,
        discount=discount,
        shipping=shipping,
        total=total,
    )


@app.route("/cart/remove/<path:cart_key>")
def remove_from_cart(cart_key):
    cart_data = session.get("cart", {})
    cart_data.pop(cart_key, None)
    session["cart"] = cart_data
    return redirect(url_for("cart"))


@app.route("/checkout", methods=["GET", "POST"])
def checkout():
    cart_data = session.get("cart", {})

    product_ids = []

    for cart_key in cart_data.keys():
        product_id, selected_size = parse_cart_key(cart_key)
        if product_id not in product_ids:
            product_ids.append(product_id)

    items = []
    subtotal = 0

    if product_ids:
        placeholders = ",".join("?" * len(product_ids))
        con = db()
        rows = con.execute(
            f"""
            SELECT p.*,
                   (
                       SELECT image
                       FROM product_images
                       WHERE product_id = p.id
                       ORDER BY is_main DESC, id
                       LIMIT 1
                   ) AS image
            FROM products p
            WHERE id IN ({placeholders})
            """,
            product_ids,
        ).fetchall()
        con.close()

        product_map = {str(row["id"]): row for row in rows}

        for cart_key, qty in cart_data.items():
            product_id, selected_size = parse_cart_key(cart_key)
            row = product_map.get(str(product_id))

            if row:
                line_total = qty * row["price"]
                subtotal += line_total
                items.append(
                    {
                        "cart_key": cart_key,
                        "p": row,
                        "qty": qty,
                        "selected_size": selected_size,
                        "total": line_total,
                    }
                )

    discount = subtotal * 0.05 if sum(cart_data.values()) >= 2 else 0
    shipping = 0 if subtotal - discount >= 1001 or subtotal == 0 else 19.99
    total = subtotal - discount + shipping

    if request.method == "POST":
        if not cart_data or not items:
            return redirect(url_for("products"))

        payment_method = request.form.get("payment", "Ramburs")
        if payment_method == "Card online":
            flash("Plata cu cardul este în lucru și nu funcționează momentan. Alege ramburs sau comandă pe WhatsApp.")
            return redirect(url_for("checkout"))

        con = db()
        cur = con.cursor()

        cur.execute(
            """
            INSERT INTO orders(name, email, phone, address, payment, total)
            VALUES(?, ?, ?, ?, ?, ?)
            """,
            (
                request.form["name"],
                request.form["email"],
                request.form["phone"],
                request.form["address"],
                payment_method,
                total,
            ),
        )

        order_id = cur.lastrowid

        for item in items:
            product = item["p"]

            cur.execute(
                """
                INSERT INTO order_items(order_id, product_id, product_name, qty, price, image, selected_size)
                VALUES(?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    order_id,
                    product["id"],
                    product["name"],
                    item["qty"],
                    product["price"],
                    product["image"],
                    item["selected_size"],
                ),
            )

        con.commit()
        con.close()

        session["cart"] = {}
        return render_template("order_success.html", order_id=order_id)

    return render_template(
        "checkout.html",
        items=items,
        subtotal=subtotal,
        discount=discount,
        shipping=shipping,
        total=total,
    )


@app.route("/login", methods=["GET", "POST"])
def login():
    if request.method == "POST":
        con = db()

        user = con.execute(
            "SELECT * FROM users WHERE email = ?",
            (request.form["email"],),
        ).fetchone()

        con.close()

        if user and check_password_hash(user["password"], request.form["password"]):
            session["user_id"] = user["id"]
            session["user_name"] = user["name"]
            session["user_email"] = user["email"]
            return redirect(url_for("my_orders"))

        flash("Email sau parolă greșită.")

    return render_template("login.html")


@app.route("/register", methods=["GET", "POST"])
def register():
    if request.method == "POST":
        try:
            con = db()

            con.execute(
                """
                INSERT INTO users(name, email, password)
                VALUES(?, ?, ?)
                """,
                (
                    request.form["name"],
                    request.form["email"],
                    generate_password_hash(request.form["password"]),
                ),
            )

            con.commit()
            con.close()

            flash("Cont creat. Te poți autentifica.")
            return redirect(url_for("login"))

        except sqlite3.IntegrityError:
            flash("Emailul există deja.")

    return render_template("register.html")


@app.route("/logout")
def logout():
    session.pop("user_id", None)
    session.pop("user_name", None)
    session.pop("user_email", None)
    return redirect(url_for("index"))


@app.route("/contul-meu")
def my_orders():
    if not session.get("user_email"):
        flash("Intră în cont pentru a vedea comenzile tale.")
        return redirect(url_for("login"))

    con = db()

    orders = con.execute(
        """
        SELECT *
        FROM orders
        WHERE email = ?
        ORDER BY id DESC
        """,
        (session["user_email"],),
    ).fetchall()

    con.close()

    return render_template("my_orders.html", orders=orders)


@app.route("/comanda/<int:order_id>")
def order_detail_client(order_id):
    if not session.get("user_email"):
        flash("Intră în cont pentru a vedea comanda.")
        return redirect(url_for("login"))

    con = db()

    order = con.execute(
        """
        SELECT *
        FROM orders
        WHERE id = ? AND email = ?
        """,
        (order_id, session["user_email"]),
    ).fetchone()

    if not order:
        con.close()
        flash("Comanda nu a fost găsită.")
        return redirect(url_for("my_orders"))

    items = con.execute(
        """
        SELECT *
        FROM order_items
        WHERE order_id = ?
        """,
        (order_id,),
    ).fetchall()

    existing_return = con.execute(
        """
        SELECT *
        FROM returns
        WHERE order_id = ?
        ORDER BY id DESC
        LIMIT 1
        """,
        (order_id,),
    ).fetchone()

    shipment = con.execute(
        """
        SELECT *
        FROM shipments
        WHERE order_id = ?
        ORDER BY id DESC
        LIMIT 1
        """,
        (order_id,),
    ).fetchone()

    invoice = con.execute(
        """
        SELECT *
        FROM invoices
        WHERE order_id = ?
        ORDER BY id DESC
        LIMIT 1
        """,
        (order_id,),
    ).fetchone()

    con.close()

    return render_template(
        "order_detail_client.html",
        order=order,
        items=items,
        existing_return=existing_return,
        shipment=shipment,
        invoice=invoice,
    )


@app.route("/comanda/<int:order_id>/retur", methods=["POST"])
def request_return(order_id):
    if not session.get("user_email"):
        flash("Intră în cont pentru a cere retur.")
        return redirect(url_for("login"))

    reason = request.form.get("reason", "").strip()

    if not reason:
        flash("Scrie motivul returului.")
        return redirect(url_for("order_detail_client", order_id=order_id))

    con = db()

    order = con.execute(
        """
        SELECT *
        FROM orders
        WHERE id = ? AND email = ?
        """,
        (order_id, session["user_email"]),
    ).fetchone()

    if not order:
        con.close()
        flash("Comanda nu a fost găsită.")
        return redirect(url_for("my_orders"))

    con.execute(
        """
        INSERT INTO returns(order_id, user_email, reason, status)
        VALUES(?, ?, ?, ?)
        """,
        (order_id, session["user_email"], reason, "Cerere trimisă"),
    )

    con.commit()
    con.close()

    flash("Cererea de retur a fost trimisă.")
    return redirect(url_for("order_detail_client", order_id=order_id))


@app.route("/comanda/<int:order_id>/genereaza-factura")
def client_generate_invoice(order_id):
    if not session.get("user_email"):
        flash("Intră în cont pentru a genera factura.")
        return redirect(url_for("login"))

    con = db()

    order = con.execute(
        """
        SELECT *
        FROM orders
        WHERE id = ? AND email = ?
        """,
        (order_id, session["user_email"]),
    ).fetchone()

    if not order:
        con.close()
        flash("Comanda nu a fost găsită.")
        return redirect(url_for("my_orders"))

    existing = con.execute(
        """
        SELECT *
        FROM invoices
        WHERE order_id = ?
        ORDER BY id DESC
        LIMIT 1
        """,
        (order_id,),
    ).fetchone()

    if existing:
        con.close()
        return redirect(url_for("client_invoice", order_id=order_id))

    subtotal, discount, shipping, total = get_order_financials(order_id)

    cur = con.cursor()
    cur.execute(
        """
        INSERT INTO invoices(
            order_id, invoice_number, customer_name, customer_email,
            customer_address, subtotal, shipping, discount, total, created_by
        )
        VALUES(?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (
            order_id,
            "",
            order["name"],
            order["email"],
            order["address"],
            subtotal,
            shipping,
            discount,
            total,
            "client",
        ),
    )

    invoice_id = cur.lastrowid
    invoice_number = generate_invoice_number(invoice_id)

    cur.execute(
        "UPDATE invoices SET invoice_number = ? WHERE id = ?",
        (invoice_number, invoice_id),
    )

    con.commit()
    con.close()

    flash("Factura a fost generată.")
    return redirect(url_for("client_invoice", order_id=order_id))


@app.route("/comanda/<int:order_id>/factura")
def client_invoice(order_id):
    if not session.get("user_email"):
        flash("Intră în cont pentru a vedea factura.")
        return redirect(url_for("login"))

    con = db()

    order = con.execute(
        """
        SELECT *
        FROM orders
        WHERE id = ? AND email = ?
        """,
        (order_id, session["user_email"]),
    ).fetchone()

    if not order:
        con.close()
        flash("Comanda nu a fost găsită.")
        return redirect(url_for("my_orders"))

    invoice = con.execute(
        """
        SELECT *
        FROM invoices
        WHERE order_id = ?
        ORDER BY id DESC
        LIMIT 1
        """,
        (order_id,),
    ).fetchone()

    items = con.execute(
        """
        SELECT *
        FROM order_items
        WHERE order_id = ?
        """,
        (order_id,),
    ).fetchall()

    con.close()

    if not invoice:
        return redirect(url_for("client_generate_invoice", order_id=order_id))

    return render_template("invoice.html", order=order, invoice=invoice, items=items)


@app.route("/promotii")
def promos():
    return redirect(url_for("products", promo="1"))


@app.route("/contacteaza-ne", methods=["GET", "POST"])
def contact():
    if request.method == "POST":
        flash("Mesaj trimis. Te contactăm rapid.")
    return render_template("contact.html")


@app.route("/admin")
def admin():
    return redirect(url_for("admin_dashboard"))


@app.route("/admin/login", methods=["GET", "POST"])
def admin_login():
    if request.method == "POST":
        con = db()

        admin_user = con.execute(
            "SELECT * FROM admins WHERE username = ?",
            (request.form["username"],),
        ).fetchone()

        con.close()

        if admin_user and check_password_hash(admin_user["password"], request.form["password"]):
            session["admin_logged"] = True
            return redirect(url_for("admin_dashboard"))

        flash("Date admin greșite. Verifică utilizatorul și parola.")

    return render_template("admin/login.html")


@app.route("/admin/logout")
def admin_logout():
    session.pop("admin_logged", None)
    return redirect(url_for("admin_login"))


@app.route("/admin/dashboard")
@admin_required
def admin_dashboard():
    con = db()

    stats = {
        "products": con.execute("SELECT COUNT(*) FROM products").fetchone()[0],
        "orders": con.execute("SELECT COUNT(*) FROM orders").fetchone()[0],
        "users": con.execute("SELECT COUNT(*) FROM users").fetchone()[0],
        "sales": con.execute("SELECT COALESCE(SUM(total), 0) FROM orders").fetchone()[0],
    }

    con.close()

    return render_template("admin/dashboard.html", stats=stats)


@app.route("/admin/products")
@admin_required
def admin_products():
    con = db()

    rows = con.execute(
        """
        SELECT p.*,
               c.name AS category_name,
               (
                   SELECT image
                   FROM product_images
                   WHERE product_id = p.id
                   ORDER BY is_main DESC, id
                   LIMIT 1
               ) AS image
        FROM products p
        LEFT JOIN categories c ON c.id = p.category_id
        ORDER BY p.id DESC
        """
    ).fetchall()

    con.close()

    return render_template("admin/products.html", products=rows)

def make_slug(text):
    text = (text or "").lower().strip()
    replacements = {
        "ă": "a", "â": "a", "î": "i", "ș": "s", "ş": "s", "ț": "t", "ţ": "t"
    }

    for old, new in replacements.items():
        text = text.replace(old, new)

    slug = ""
    for ch in text:
        if ch.isalnum():
            slug += ch
        elif ch in [" ", "-", "_"]:
            slug += "-"

    while "--" in slug:
        slug = slug.replace("--", "-")

    return slug.strip("-")


@app.route("/admin/categories")
@admin_required
def admin_categories():
    con = db()

    categories = con.execute(
        """
        SELECT c.*,
               COUNT(p.id) AS products_count
        FROM categories c
        LEFT JOIN products p ON p.category_id = c.id
        GROUP BY c.id
        ORDER BY c.id DESC
        """
    ).fetchall()

    con.close()

    return render_template("admin/categories.html", categories=categories)


@app.route("/admin/category/new", methods=["GET", "POST"])
@app.route("/admin/category/<int:cid>/edit", methods=["GET", "POST"])
@admin_required
def admin_category_form(cid=None):
    con = db()
    category = None

    if cid:
        category = con.execute(
            "SELECT * FROM categories WHERE id = ?",
            (cid,),
        ).fetchone()

        if not category:
            con.close()
            flash("Categoria nu există.")
            return redirect(url_for("admin_categories"))

    if request.method == "POST":
        name = request.form.get("name", "").strip()
        description = request.form.get("description", "").strip()
        slug = request.form.get("slug", "").strip() or make_slug(name)
        is_active = 1 if request.form.get("is_active") else 0

        if not name:
            flash("Numele categoriei este obligatoriu.")
            return redirect(request.url)

        try:
            if cid:
                con.execute(
                    """
                    UPDATE categories
                    SET name = ?, slug = ?, description = ?, is_active = ?
                    WHERE id = ?
                    """,
                    (name, slug, description, is_active, cid),
                )
            else:
                con.execute(
                    """
                    INSERT INTO categories(name, slug, description, is_active)
                    VALUES(?, ?, ?, ?)
                    """,
                    (name, slug, description, is_active),
                )

            con.commit()
            con.close()

            flash("Categoria a fost salvată.")
            return redirect(url_for("admin_categories"))

        except sqlite3.IntegrityError:
            con.close()
            flash("Există deja o categorie cu acest slug.")
            return redirect(request.url)

    con.close()

    return render_template(
        "admin/category_form.html",
        category=category,
    )


@app.route("/admin/category/<int:cid>/toggle")
@admin_required
def admin_category_toggle(cid):
    con = db()

    try:
        con.execute(
            """
            UPDATE categories
            SET is_active = CASE WHEN is_active = 1 THEN 0 ELSE 1 END
            WHERE id = ?
            """,
            (cid,),
        )

        con.commit()
        flash("Statusul categoriei a fost modificat.")

    except sqlite3.OperationalError:
        flash("Baza de date este ocupată. Închide DB Browser și încearcă din nou.")

    finally:
        con.close()

    return redirect(url_for("admin_categories"))


@app.route("/admin/category/<int:cid>/delete")
@admin_required
def admin_category_delete(cid):
    con = db()

    try:
        used = con.execute(
            "SELECT COUNT(*) FROM products WHERE category_id = ?",
            (cid,),
        ).fetchone()[0]

        if used > 0:
            flash("Nu poți șterge categoria deoarece are produse. Mută produsele înainte.")
            return redirect(url_for("admin_categories"))

        con.execute("DELETE FROM categories WHERE id = ?", (cid,))
        con.commit()

        flash("Categoria a fost ștearsă.")

    except sqlite3.OperationalError:
        flash("Baza de date este ocupată. Închide DB Browser și încearcă din nou.")

    finally:
        con.close()

    return redirect(url_for("admin_categories"))

@app.route("/admin/product/new", methods=["GET", "POST"])
@app.route("/admin/product/<int:pid>/edit", methods=["GET", "POST"])
@admin_required
def admin_product_form(pid=None):
    con = db()
    product = None
    images = []

    if pid:
        product = con.execute(
            "SELECT * FROM products WHERE id = ?",
            (pid,),
        ).fetchone()

        images = con.execute(
            """
            SELECT *
            FROM product_images
            WHERE product_id = ?
            ORDER BY is_main DESC, id
            """,
            (pid,),
        ).fetchall()

    categories = con.execute("SELECT * FROM categories").fetchall()

    if request.method == "POST":
        name_ro = request.form.get("name", "").strip()
        description_ro = request.form.get("description", "").strip()
        color_ro = request.form.get("color", "").strip()
        material_ro = request.form.get("material", "").strip()

        translated_descriptions = {}
        translated_names = {}
        translated_colors = {}
        translated_materials = {}

        # Dacă bifa este activă, completăm automat limbile lipsă din valorile RO.
        auto_translate = bool(request.form.get("auto_translate_descriptions"))
        for lang in DESCRIPTION_LANGS:
            desc_value = request.form.get(f"description_{lang}", "").strip()
            if auto_translate and description_ro and not desc_value:
                desc_value = google_translate_free(description_ro, lang)
            translated_descriptions[lang] = desc_value

            name_value = request.form.get(f"name_{lang}", "").strip()
            if auto_translate and name_ro and not name_value:
                name_value = google_translate_free(name_ro, lang)
            translated_names[lang] = name_value

            color_value = request.form.get(f"color_{lang}", "").strip()
            if auto_translate and color_ro and not color_value:
                color_value = google_translate_free(color_ro, lang)
            translated_colors[lang] = color_value

            material_value = request.form.get(f"material_{lang}", "").strip()
            if auto_translate and material_ro and not material_value:
                material_value = google_translate_free(material_ro, lang)
            translated_materials[lang] = material_value

        data = (
            request.form.get("category_id") or None,
            name_ro,
            translated_names["en"],
            translated_names["de"],
            translated_names["hu"],
            translated_names["bg"],
            translated_names["el"],
            translated_names["ru"],
            translated_names["uk"],
            translated_names["sr"],
            description_ro,
            translated_descriptions["en"],
            translated_descriptions["de"],
            translated_descriptions["hu"],
            translated_descriptions["bg"],
            translated_descriptions["el"],
            translated_descriptions["ru"],
            translated_descriptions["uk"],
            translated_descriptions["sr"],
            float(request.form.get("price") or 0),
            request.form.get("old_price") or None,
            int(request.form.get("stock") or 0),
            request.form.get("size", ""),
            color_ro,
            translated_colors["en"],
            translated_colors["de"],
            translated_colors["hu"],
            translated_colors["bg"],
            translated_colors["el"],
            translated_colors["ru"],
            translated_colors["uk"],
            translated_colors["sr"],
            material_ro,
            translated_materials["en"],
            translated_materials["de"],
            translated_materials["hu"],
            translated_materials["bg"],
            translated_materials["el"],
            translated_materials["ru"],
            translated_materials["uk"],
            translated_materials["sr"],
            1 if request.form.get("is_promo") else 0,
            1 if request.form.get("is_active") else 0,
        )

        cur = con.cursor()

        if pid:
            cur.execute(
                """
                UPDATE products
                SET category_id = ?,
                    name = ?,
                    name_en = ?,
                    name_de = ?,
                    name_hu = ?,
                    name_bg = ?,
                    name_el = ?,
                    name_ru = ?,
                    name_uk = ?,
                    name_sr = ?,
                    description = ?,
                    description_en = ?,
                    description_de = ?,
                    description_hu = ?,
                    description_bg = ?,
                    description_el = ?,
                    description_ru = ?,
                    description_uk = ?,
                    description_sr = ?,
                    price = ?,
                    old_price = ?,
                    stock = ?,
                    size = ?,
                    color = ?,
                    color_en = ?,
                    color_de = ?,
                    color_hu = ?,
                    color_bg = ?,
                    color_el = ?,
                    color_ru = ?,
                    color_uk = ?,
                    color_sr = ?,
                    material = ?,
                    material_en = ?,
                    material_de = ?,
                    material_hu = ?,
                    material_bg = ?,
                    material_el = ?,
                    material_ru = ?,
                    material_uk = ?,
                    material_sr = ?,
                    is_promo = ?,
                    is_active = ?
                WHERE id = ?
                """,
                data + (pid,),
            )
            product_id = pid
        else:
            cur.execute(
                """
                INSERT INTO products(
                    category_id, name,
                    name_en, name_de, name_hu, name_bg, name_el, name_ru, name_uk, name_sr,
                    description,
                    description_en, description_de, description_hu, description_bg,
                    description_el, description_ru, description_uk, description_sr,
                    price, old_price, stock, size,
                    color, color_en, color_de, color_hu, color_bg, color_el, color_ru, color_uk, color_sr,
                    material, material_en, material_de, material_hu, material_bg, material_el, material_ru, material_uk, material_sr,
                    is_promo, is_active
                )
                VALUES(?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                data,
            )
            product_id = cur.lastrowid

        uploaded = [save_upload(file) for file in request.files.getlist("images")]
        uploaded = [image for image in uploaded if image]

        already = con.execute(
            "SELECT COUNT(*) FROM product_images WHERE product_id = ?",
            (product_id,),
        ).fetchone()[0]

        for i, image in enumerate(uploaded):
            cur.execute(
                """
                INSERT INTO product_images(product_id, image, is_main)
                VALUES(?, ?, ?)
                """,
                (product_id, image, 1 if already == 0 and i == 0 else 0),
            )

        con.commit()
        con.close()

        return redirect(url_for("admin_products"))

    con.close()

    return render_template(
        "admin/product_form.html",
        product=product,
        categories=categories,
        images=images,
    )


@app.route("/admin/product/<int:pid>/delete")
@admin_required
def admin_product_delete(pid):
    con = db()
    con.execute("DELETE FROM products WHERE id = ?", (pid,))
    con.execute("DELETE FROM product_images WHERE product_id = ?", (pid,))
    con.commit()
    con.close()
    return redirect(url_for("admin_products"))


@app.route("/admin/image/<int:image_id>/main/<int:pid>")
@admin_required
def admin_image_main(image_id, pid):
    con = db()
    con.execute("UPDATE product_images SET is_main = 0 WHERE product_id = ?", (pid,))
    con.execute("UPDATE product_images SET is_main = 1 WHERE id = ?", (image_id,))
    con.commit()
    con.close()
    return redirect(url_for("admin_product_form", pid=pid))


@app.route("/admin/image/<int:image_id>/delete/<int:pid>")
@admin_required
def admin_image_delete(image_id, pid):
    con = db()
    con.execute("DELETE FROM product_images WHERE id = ?", (image_id,))
    con.commit()
    con.close()
    return redirect(url_for("admin_product_form", pid=pid))


@app.route("/admin/orders")
@admin_required
def admin_orders():
    con = db()
    orders = con.execute("SELECT * FROM orders ORDER BY id DESC").fetchall()
    con.close()
    return render_template("admin/orders.html", orders=orders)


@app.route("/admin/order/<int:order_id>")
@admin_required
def admin_order_detail(order_id):
    con = db()

    order = con.execute(
        "SELECT * FROM orders WHERE id = ?",
        (order_id,),
    ).fetchone()

    if not order:
        con.close()
        flash("Comanda nu există.")
        return redirect(url_for("admin_orders"))

    items = con.execute(
        "SELECT * FROM order_items WHERE order_id = ?",
        (order_id,),
    ).fetchall()

    return_request = con.execute(
        """
        SELECT *
        FROM returns
        WHERE order_id = ?
        ORDER BY id DESC
        LIMIT 1
        """,
        (order_id,),
    ).fetchone()

    shipment = con.execute(
        """
        SELECT *
        FROM shipments
        WHERE order_id = ?
        ORDER BY id DESC
        LIMIT 1
        """,
        (order_id,),
    ).fetchone()

    invoice = con.execute(
        """
        SELECT *
        FROM invoices
        WHERE order_id = ?
        ORDER BY id DESC
        LIMIT 1
        """,
        (order_id,),
    ).fetchone()

    con.close()

    return render_template(
        "admin/order_detail.html",
        order=order,
        items=items,
        return_request=return_request,
        shipment=shipment,
        invoice=invoice,
    )


@app.post("/admin/order/<int:order_id>/status")
@admin_required
def admin_update_order_status(order_id):
    status = request.form.get("status", "Nouă")
    admin_note = request.form.get("admin_note", "")

    con = db()
    con.execute(
        """
        UPDATE orders
        SET status = ?, admin_note = ?
        WHERE id = ?
        """,
        (status, admin_note, order_id),
    )
    con.commit()
    con.close()

    flash("Statusul comenzii a fost actualizat.")
    return redirect(url_for("admin_order_detail", order_id=order_id))


@app.post("/admin/order/<int:order_id>/awb")
@admin_required
def admin_create_awb(order_id):
    courier = request.form.get("courier", "Fan Courier")
    delivery_type = request.form.get("delivery_type", "address")
    easybox_name = request.form.get("easybox_name", "").strip()
    easybox_address = request.form.get("easybox_address", "").strip()

    if courier not in ["Fan Courier", "Sameday"]:
        courier = "Fan Courier"

    if courier == "Fan Courier":
        delivery_type = "address"
        easybox_name = ""
        easybox_address = ""

    if courier == "Sameday" and delivery_type not in ["address", "easybox"]:
        delivery_type = "address"

    if courier == "Sameday" and delivery_type == "easybox":
        if not easybox_name:
            easybox_name = "Easybox selectat manual"
        if not easybox_address:
            easybox_address = "Adresă Easybox nespecificată"

    awb = generate_awb_number(order_id, courier, delivery_type)
    tracking_url = generate_tracking_url(courier, awb)

    con = db()

    existing = con.execute(
        "SELECT * FROM shipments WHERE order_id = ? ORDER BY id DESC LIMIT 1",
        (order_id,),
    ).fetchone()

    if existing:
        con.execute(
            """
            UPDATE shipments
            SET courier = ?, awb = ?, tracking_url = ?, delivery_type = ?,
                easybox_name = ?, easybox_address = ?, status = ?
            WHERE id = ?
            """,
            (
                courier,
                awb,
                tracking_url,
                delivery_type,
                easybox_name,
                easybox_address,
                "Creat",
                existing["id"],
            ),
        )
    else:
        con.execute(
            """
            INSERT INTO shipments(
                order_id, courier, awb, tracking_url, delivery_type,
                easybox_name, easybox_address, status
            )
            VALUES(?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                order_id,
                courier,
                awb,
                tracking_url,
                delivery_type,
                easybox_name,
                easybox_address,
                "Creat",
            ),
        )

    con.execute(
        "UPDATE orders SET status = ? WHERE id = ?",
        ("Expediată", order_id),
    )

    con.commit()
    con.close()

    if courier == "Sameday" and delivery_type == "easybox":
        flash(f"AWB Sameday Easybox generat automat: {awb}")
    else:
        flash(f"AWB {courier} generat automat: {awb}")

    return redirect(url_for("admin_order_detail", order_id=order_id))


@app.post("/admin/order/<int:order_id>/retur")
@admin_required
def admin_update_return(order_id):
    return_status = request.form.get("return_status", "Cerere trimisă")
    admin_note = request.form.get("admin_note", "")

    con = db()

    return_request = con.execute(
        """
        SELECT *
        FROM returns
        WHERE order_id = ?
        ORDER BY id DESC
        LIMIT 1
        """,
        (order_id,),
    ).fetchone()

    if return_request:
        con.execute(
            """
            UPDATE returns
            SET status = ?, admin_note = ?
            WHERE id = ?
            """,
            (return_status, admin_note, return_request["id"]),
        )

        if return_status == "Acceptat":
            con.execute(
                "UPDATE orders SET status = ? WHERE id = ?",
                ("Retur acceptat", order_id),
            )

        if return_status == "Refuzat":
            con.execute(
                "UPDATE orders SET status = ? WHERE id = ?",
                ("Retur refuzat", order_id),
            )

    con.commit()
    con.close()

    flash("Statusul returului a fost actualizat.")
    return redirect(url_for("admin_order_detail", order_id=order_id))


@app.route("/admin/order/<int:order_id>/factura")
@admin_required
def admin_create_invoice(order_id):
    con = db()

    order = con.execute(
        "SELECT * FROM orders WHERE id = ?",
        (order_id,),
    ).fetchone()

    if not order:
        con.close()
        flash("Comanda nu există.")
        return redirect(url_for("admin_orders"))

    existing = con.execute(
        """
        SELECT *
        FROM invoices
        WHERE order_id = ?
        ORDER BY id DESC
        LIMIT 1
        """,
        (order_id,),
    ).fetchone()

    if existing:
        con.close()
        return redirect(url_for("admin_invoice_view", order_id=order_id))

    subtotal, discount, shipping, total = get_order_financials(order_id)

    cur = con.cursor()
    cur.execute(
        """
        INSERT INTO invoices(
            order_id, invoice_number, customer_name, customer_email,
            customer_address, subtotal, shipping, discount, total, created_by
        )
        VALUES(?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (
            order_id,
            "",
            order["name"],
            order["email"],
            order["address"],
            subtotal,
            shipping,
            discount,
            total,
            "admin",
        ),
    )

    invoice_id = cur.lastrowid
    invoice_number = generate_invoice_number(invoice_id)

    cur.execute(
        "UPDATE invoices SET invoice_number = ? WHERE id = ?",
        (invoice_number, invoice_id),
    )

    con.commit()
    con.close()

    flash("Factura a fost creată.")
    return redirect(url_for("admin_invoice_view", order_id=order_id))


@app.route("/admin/order/<int:order_id>/factura/vezi")
@admin_required
def admin_invoice_view(order_id):
    con = db()

    order = con.execute(
        "SELECT * FROM orders WHERE id = ?",
        (order_id,),
    ).fetchone()

    invoice = con.execute(
        """
        SELECT *
        FROM invoices
        WHERE order_id = ?
        ORDER BY id DESC
        LIMIT 1
        """,
        (order_id,),
    ).fetchone()

    items = con.execute(
        "SELECT * FROM order_items WHERE order_id = ?",
        (order_id,),
    ).fetchall()

    con.close()

    if not order or not invoice:
        flash("Factura nu există.")
        return redirect(url_for("admin_order_detail", order_id=order_id))

    return render_template("invoice.html", order=order, invoice=invoice, items=items)


@app.post("/admin/order/<int:order_id>/delete")
@admin_required
def admin_delete_order(order_id):
    con = db()

    con.execute("DELETE FROM order_items WHERE order_id = ?", (order_id,))
    con.execute("DELETE FROM returns WHERE order_id = ?", (order_id,))
    con.execute("DELETE FROM shipments WHERE order_id = ?", (order_id,))
    con.execute("DELETE FROM invoices WHERE order_id = ?", (order_id,))
    con.execute("DELETE FROM orders WHERE id = ?", (order_id,))

    con.commit()
    con.close()

    flash("Comanda a fost ștearsă.")
    return redirect(url_for("admin_orders"))


@app.route("/admin/users")
@admin_required
def admin_users():
    con = db()
    users = con.execute("SELECT * FROM users ORDER BY id DESC").fetchall()
    con.close()
    return render_template("admin/users.html", users=users)


@app.post("/admin/user/<int:user_id>/delete")
@admin_required
def admin_delete_user(user_id):
    con = db()
    con.execute("DELETE FROM users WHERE id = ?", (user_id,))
    con.commit()
    con.close()

    flash("Utilizatorul a fost șters.")
    return redirect(url_for("admin_users"))

# ==========================================================
# ASISTENT BELLA24
# ==========================================================

def normalize_ai_text(text):
    return (text or "").strip().lower()


def get_catalog_for_assistant(limit=30):
    con = db()

    rows = con.execute(
        """
        SELECT 
            p.id,
            p.name,
            p.description,
            p.price,
            p.old_price,
            p.stock,
            p.size,
            p.color,
            p.material,
            p.is_promo,
            c.name AS category_name,
            (
                SELECT image 
                FROM product_images 
                WHERE product_id = p.id 
                ORDER BY is_main DESC, id 
                LIMIT 1
            ) AS image
        FROM products p
        LEFT JOIN categories c ON c.id = p.category_id
        WHERE p.is_active = 1
        ORDER BY p.id DESC
        LIMIT ?
        """,
        (limit,),
    ).fetchall()

    con.close()

    products_list = []

    for row in rows:
        products_list.append(
            {
                "id": row["id"],
                "name": row["name"],
                "description": row["description"] or "",
                "price": float(row["price"] or 0),
                "old_price": float(row["old_price"] or 0) if row["old_price"] else None,
                "stock": int(row["stock"] or 0),
                "size": row["size"] or "",
                "color": row["color"] or "",
                "material": row["material"] or "",
                "is_promo": int(row["is_promo"] or 0),
                "category": row["category_name"] or "",
                "image": row["image"] or "",
                "url": url_for("product_detail", pid=row["id"]),
            }
        )

    return products_list


def get_user_orders_for_assistant():
    if not session.get("user_email"):
        return None

    con = db()

    orders = con.execute(
        """
        SELECT id, created_at, status, total, payment, admin_note
        FROM orders
        WHERE email = ?
        ORDER BY id DESC
        LIMIT 10
        """,
        (session["user_email"],),
    ).fetchall()

    con.close()
    return orders


def get_order_items_for_assistant(order_id):
    if not session.get("user_email"):
        return None, []

    con = db()

    order = con.execute(
        """
        SELECT *
        FROM orders
        WHERE id = ? AND email = ?
        """,
        (order_id, session["user_email"]),
    ).fetchone()

    items = []

    if order:
        items = con.execute(
            """
            SELECT *
            FROM order_items
            WHERE order_id = ?
            """,
            (order_id,),
        ).fetchall()

    con.close()
    return order, items


def assistant_wants_orders(message):
    msg = normalize_ai_text(message)

    words = [
        "comenzi",
        "comanda",
        "comandă",
        "status",
        "stare",
        "starea",
        "unde este comanda",
        "unde e comanda",
        "ce am comandat",
        "ce comenzi am",
        "awb",
        "tracking",
        "livrare comanda",
        "livrare comandă",
    ]

    return any(word in msg for word in words)


def assistant_wants_products(message):
    msg = normalize_ai_text(message)

    words = [
        "recomanda",
        "recomandă",
        "recomanzi",
        "potrivi",
        "potrivește",
        "asorta",
        "asortez",
        "tinuta",
        "ținută",
        "rochie",
        "rochii",
        "bluza",
        "bluză",
        "bluze",
        "fusta",
        "fustă",
        "fuste",
        "costum",
        "costume",
        "palton",
        "geaca",
        "geacă",
        "culoare",
        "marime",
        "mărime",
        "produs",
        "produse",
        "ai ceva",
        "nunta",
        "nuntă",
        "botez",
        "gala",
        "gală",
        "eveniment",
    ]

    return any(word in msg for word in words)


def score_product_for_message(product, message):
    msg = normalize_ai_text(message)

    product_text = normalize_ai_text(
        f"{product['name']} {product['description']} {product['color']} "
        f"{product['material']} {product['category']} {product['size']}"
    )

    score = 0

    colors = {
        "rosu": ["rosu", "roșu", "red", "bordo", "grena"],
        "verde": ["verde", "smarald", "petrol", "marin", "green"],
        "turcoaz": ["turcoaz", "turquoise"],
        "champagne": ["champagne", "auriu", "aurie", "bej", "nude"],
        "negru": ["negru", "black"],
        "alb": ["alb", "white", "ivory"],
        "albastru": ["albastru", "bleu", "blue"],
        "roz": ["roz", "pink", "pudra", "pudră"],
    }

    for words in colors.values():
        if any(word in msg for word in words):
            if any(word in product_text for word in words):
                score += 7

    categories = {
        "rochie": ["rochie", "rochii", "dress"],
        "bluza": ["bluza", "bluză", "bluze"],
        "fusta": ["fusta", "fustă", "fuste"],
        "costum": ["costum", "costume"],
        "palton": ["palton", "geaca", "geacă", "geci"],
    }

    for words in categories.values():
        if any(word in msg for word in words):
            if any(word in product_text for word in words):
                score += 8

    elegant_words = [
        "gala",
        "gală",
        "nunta",
        "nuntă",
        "botez",
        "seara",
        "seară",
        "elegant",
        "eveniment",
        "petrecere",
        "restaurant",
    ]

    elegant_product_words = [
        "seara",
        "seară",
        "elegant",
        "eleganta",
        "elegantă",
        "lung",
        "lungă",
        "stralucitor",
        "strălucitor",
        "gala",
        "gală",
    ]

    if any(word in msg for word in elegant_words):
        if any(word in product_text for word in elegant_product_words):
            score += 5

    if product["stock"] > 0:
        score += 2

    if product["is_promo"]:
        score += 1

    return score


def build_product_recommendation(message, has_image=False):
    products_list = get_catalog_for_assistant(limit=30)

    scored = []

    for product in products_list:
        score = score_product_for_message(product, message)
        if score > 0:
            scored.append((score, product))

    scored.sort(key=lambda item: item[0], reverse=True)
    recommended = [product for _, product in scored[:3]]

    if not recommended:
        if has_image:
            return (
                "Am primit poza, dar nu am găsit în catalog un produs clar potrivit după întrebarea ta. "
                "Spune-mi pentru ce eveniment vrei ținuta: nuntă, botez, gală, birou sau ieșire casual."
            )

        return (
            "Nu am găsit un produs potrivit după întrebarea ta. "
            "Spune-mi culoarea, mărimea sau evenimentul pentru care cauți ținuta."
        )

    html = ""

    if has_image:
        html += (
            "Am primit poza. Pentru bluza sau ținuta încărcată, m-aș orienta către produse care completează culoarea și stilul ei. "
            "Din catalogul Bella24 îți recomand:<br><br>"
        )
    else:
        html += "Din catalogul Bella24 îți recomand:<br><br>"

    for product in recommended:
        html += f"""
        <div class="bella-ai-product">
            <b>{product['name']}</b><br>
            <span>{product['price']:.2f} lei</span><br>
            <small>Culoare: {product['color'] or 'nespecificată'} | Mărimi: {product['size'] or 'verifică produsul'}</small><br>
            <a href="{product['url']}">Vezi produsul</a>
        </div>
        """

    return html


def build_orders_reply(message):
    msg = normalize_ai_text(message)

    if not session.get("user_email"):
        return "Pentru a vedea comenzile tale, trebuie să fii autentificat în cont."

    import re

    match = re.search(r"#?\s*(\d+)", msg)

    if match:
        order_id = int(match.group(1))
        order, items = get_order_items_for_assistant(order_id)

        if not order:
            return f"Nu am găsit comanda #{order_id} în contul tău."

        html = f"""
        Comanda ta <b>#{order['id']}</b> are statusul: <b>{order['status']}</b>.<br>
        Data: {order['created_at']}<br>
        Total: {order['total']:.2f} lei<br>
        Plata: {order['payment'] or 'nespecificată'}<br>
        """

        if order["admin_note"]:
            html += f"Notă admin: {order['admin_note']}<br>"

        if items:
            html += "<br>Produse comandate:<br>"
            for item in items:
                html += f"- {item['product_name']} x {item['qty']} | Mărime: {item['selected_size'] or 'nespecificată'}<br>"

        html += f'<br><a href="{url_for("order_detail_client", order_id=order["id"])}">Vezi comanda</a>'
        return html

    orders = get_user_orders_for_assistant()

    if not orders:
        return "Momentan nu ai comenzi în cont."

    html = "Comenzile tale sunt:<br><br>"

    for order in orders:
        html += f"""
        <div class="bella-ai-product">
            <b>Comanda #{order['id']}</b><br>
            Status: <b>{order['status']}</b><br>
            Total: {order['total']:.2f} lei<br>
            Data: {order['created_at']}<br>
            <a href="{url_for('order_detail_client', order_id=order['id'])}">Vezi comanda</a>
        </div>
        """

    return html


def local_assistant_reply(message, has_image=False):
    msg = normalize_ai_text(message)

    if not msg and not has_image:
        return "Scrie întrebarea ta și îți răspund exact pe subiect: comenzi, retur, livrare, mărimi sau recomandări."

    if assistant_wants_orders(message):
        return build_orders_reply(message)

    if any(word in msg for word in ["retur", "returnez", "schimb"]):
        return (
            "Poți solicita retur din pagina «Comenzile mele». "
            "Intră în cont, deschide comanda și apasă pe «Solicită retur»."
        )

    if any(word in msg for word in ["livrare", "transport", "curier"]):
        return "Livrarea este rapidă în România. Transportul este gratuit pentru comenzi peste 1001 lei."

    if any(word in msg for word in ["plata", "plată", "ramburs", "card", "transfer"]):
        return "Poți alege plata ramburs, card online sau transfer bancar, în funcție de opțiunile disponibile la checkout."

    if has_image or assistant_wants_products(message):
        return build_product_recommendation(message, has_image=has_image)

    return (
        "Îți răspund doar pe ce mă întrebi. Poți întreba despre comenzile tale, status comandă, retur, livrare, mărimi "
        "sau poți încărca o poză cu o bluză/ținută ca să îți recomand ceva potrivit."
    )


def image_to_data_url(file_storage):
    raw = file_storage.read()
    mime = file_storage.mimetype or mimetypes.guess_type(file_storage.filename)[0] or "image/jpeg"
    encoded = base64.b64encode(raw).decode("utf-8")
    return f"data:{mime};base64,{encoded}"


def openai_assistant_reply(message, image_file=None):
    api_key = os.environ.get("OPENAI_API_KEY")

    if not api_key or OpenAI is None:
        return None

    products_list = get_catalog_for_assistant(limit=20)
    orders = []

    if session.get("user_email"):
        user_orders = get_user_orders_for_assistant()
        if user_orders:
            for order in user_orders:
                orders.append(
                    {
                        "id": order["id"],
                        "created_at": order["created_at"],
                        "status": order["status"],
                        "total": order["total"],
                        "payment": order["payment"],
                        "admin_note": order["admin_note"],
                        "url": url_for("order_detail_client", order_id=order["id"]),
                    }
                )

    client = OpenAI(api_key=api_key)

    product_text = json.dumps(products_list, ensure_ascii=False, indent=2)
    orders_text = json.dumps(orders, ensure_ascii=False, indent=2)

    system_prompt = f"""
Ești Asistentul Bella24 pentru magazinul online Bella24.

Răspunzi în română, clar și scurt.

Reguli importante:
- Răspunzi STRICT la întrebarea utilizatorului.
- NU afișa produse automat.
- Răspunde conversațional, ca un consultant real de modă.
- Dacă întrebarea este despre ținute, culori, pantofi, accesorii sau potriviri, oferă mai întâi sfat general, apoi recomandă produse din catalog doar dacă se potrivesc.
- Nu forța recomandări de produse dacă întrebarea cere doar opinie sau explicație.- Pentru comenzi, folosește DOAR comenzile utilizatorului logat.
- Dacă utilizatorul întreabă "ce comenzi am", "starea comenzii", "status comandă", răspunde din lista de comenzi.
- Nu inventa produse.
- Nu inventa comenzi.
- Dacă utilizatorul trimite poză, oferă recomandări de stil, dar nu identifica persoana.
- Nu face afirmații sensibile despre corp.

Comenzile utilizatorului logat:
{orders_text}

Catalog Bella24:
{product_text}
"""

    content = [
        {
            "type": "input_text",
            "text": f"{system_prompt}\n\nÎntrebarea clientului: {message}",
        }
    ]

    if image_file:
        content.append(
            {
                "type": "input_image",
                "image_url": image_to_data_url(image_file),
            }
        )

    try:
        response = client.responses.create(
            model=os.environ.get("OPENAI_MODEL", "gpt-4o"),
            input=[
                {
                    "role": "user",
                    "content": content,
                }
            ],
        )
        return response.output_text

    except Exception:
        return None


@app.route("/assistant-chat", methods=["POST"])
def assistant_chat():
    message = request.form.get("message", "").strip()
    image_file = request.files.get("image")

    if not message and not image_file:
        return jsonify({"reply": "Scrie întrebarea ta sau încarcă o poză."})

    ai_reply = openai_assistant_reply(message, image_file)

    if ai_reply:
        return jsonify({"reply": ai_reply})

    fallback_reply = local_assistant_reply(message, has_image=bool(image_file))
    return jsonify({"reply": fallback_reply})

if __name__ == "__main__":
    init_db()
    app.run(debug=True)