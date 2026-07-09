from __future__ import annotations

import sqlite3
import time

import app

FIELDS = ["name", "color", "material", "description"]
LANGS = app.DESCRIPTION_LANGS


def ensure_columns(con: sqlite3.Connection) -> None:
    for lang in LANGS:
        for field in FIELDS:
            app.ensure_column(con, "products", f"{field}_{lang}", "TEXT")
        app.ensure_column(con, "categories", f"name_{lang}", "TEXT")
        app.ensure_column(con, "categories", f"description_{lang}", "TEXT")
    con.commit()


def translate_missing_products(con: sqlite3.Connection) -> None:
    rows = con.execute("SELECT * FROM products ORDER BY id").fetchall()
    for row in rows:
        updates = {}
        for lang in LANGS:
            for field in FIELDS:
                base = (row[field] or "").strip()
                col = f"{field}_{lang}"
                current = ""
                try:
                    current = (row[col] or "").strip()
                except Exception:
                    current = ""
                if base and not current:
                    print(f"Traduc produs #{row['id']} / {field} -> {lang} ...")
                    updates[col] = app.google_translate_free(base, lang)
                    time.sleep(0.2)
        if updates:
            set_sql = ", ".join([f"{col} = ?" for col in updates])
            con.execute(
                f"UPDATE products SET {set_sql} WHERE id = ?",
                list(updates.values()) + [row["id"]],
            )
            con.commit()


def main() -> None:
    app.init_db()
    con = app.db()
    ensure_columns(con)
    translate_missing_products(con)
    con.close()
    print("GATA: coloane create și traduceri completate pentru produse.")


if __name__ == "__main__":
    main()
