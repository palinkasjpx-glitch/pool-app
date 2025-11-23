import os
from datetime import datetime, date

import bcrypt
import pandas as pd
import psycopg2
import streamlit as st
from io import BytesIO


# ------------- NASTAVENIE STRÁNKY ------------- #

st.set_page_config(
    page_title="Bazén - merania",
    page_icon="💧",
    layout="centered",
)


# ------------- PRIPOJENIE K DATABÁZE ------------- #

def get_connection():
    db_url = os.getenv("DATABASE_URL")
    if not db_url:
        st.error(
            "Databáza nie je nastavená.\n\n"
            "Prosím, nastav premennú prostredia **DATABASE_URL** "
            "na PostgreSQL connection string (napr. z Neon/Railway/Render)."
        )
        return None

    try:
        conn = psycopg2.connect(db_url)
        return conn
    except Exception as e:
        st.error(f"Nepodarilo sa pripojiť k databáze: {e}")
        return None


def init_db(conn):
    """Vytvorí tabuľky, ak ešte neexistujú."""
    create_users = """
    CREATE TABLE IF NOT EXISTS users (
        id SERIAL PRIMARY KEY,
        username VARCHAR(50) UNIQUE NOT NULL,
        password_hash TEXT NOT NULL,
        role VARCHAR(20) NOT NULL CHECK (role IN ('admin', 'user')),
        created_at TIMESTAMP DEFAULT NOW()
    );
    """

    create_measurements = """
    CREATE TABLE IF NOT EXISTS measurements (
        id SERIAL PRIMARY KEY,
        date DATE NOT NULL,
        day VARCHAR(20) NOT NULL,
        time VARCHAR(5) NOT NULL,
        free_chlorine NUMERIC(4,2) NOT NULL,
        total_chlorine NUMERIC(4,2) NOT NULL,
        bound_chlorine NUMERIC(4,2) NOT NULL,
        ph NUMERIC(3,1) NOT NULL,
        temperature NUMERIC(4,1),
        note TEXT,
        user_id INTEGER REFERENCES users(id),
        created_at TIMESTAMP DEFAULT NOW()
    );
    """

    cur = conn.cursor()
    cur.execute(create_users)
    cur.execute(create_measurements)
    conn.commit()


def ensure_default_admin(conn):
    """
    Ak v tabuľke users nie je žiadny používateľ,
    vytvorí default admina: meno=admin, heslo=admin123
    """
    cur = conn.cursor()
    cur.execute("SELECT COUNT(*) FROM users;")
    count = cur.fetchone()[0]
    if count == 0:
        username = "admin"
        password = "admin123"
        hashed = bcrypt.hashpw(password.encode(), bcrypt.gensalt()).decode()

        cur.execute(
            "INSERT INTO users (username, password_hash, role) VALUES (%s, %s, %s);",
            (username, hashed, "admin"),
        )
        conn.commit()
        st.info("Vytvorený default admin účet: meno **admin**, heslo **admin123**.")


# ------------- POMOCNÉ FUNKCIE ------------- #

def day_of_week_sk(date_obj: date) -> str:
    dni = ["Pondelok", "Utorok", "Streda", "Štvrtok", "Piatok", "Sobota", "Nedeľa"]
    return dni[date_obj.weekday()]


def farba_volny_chlor(val):
    """Vracia CSS pre farbenie buniek podľa voľného chlóru."""
    try:
        v = float(val)
    except (TypeError, ValueError):
        return ""

    if v <= 0.3:
        return "background-color: #cce6ff"  # bledomodrá
    elif 0.4 <= v <= 0.7:
        return ""  # bez farby
    elif v >= 0.8:
        return "background-color: #fff3b0"  # žltá
    return ""


# ------------- LOGIN / LOGOUT ------------- #

def login_screen(conn):
    st.title("Prihlásenie")

    username = st.text_input("Používateľské meno")
    password = st.text_input("Heslo", type="password")
    login_btn = st.button("Prihlásiť sa")

    if login_btn:
        cur = conn.cursor()
        cur.execute(
            "SELECT id, password_hash, role FROM users WHERE username = %s",
            (username,),
        )
        row = cur.fetchone()

        if row is None:
            st.error("Nesprávne meno alebo heslo.")
            return

        user_id, password_hash, role = row

        try:
            if bcrypt.checkpw(password.encode(), password_hash.encode()):
                st.session_state["logged_in"] = True
                st.session_state["user_id"] = user_id
                st.session_state["username"] = username
                st.session_state["role"] = role
                st.experimental_rerun()
            else:
                st.error("Nesprávne meno alebo heslo.")
        except Exception:
            st.error("Chyba pri overovaní hesla.")


def logout_button():
    if st.sidebar.button("Odhlásiť sa"):
        for key in ["logged_in", "user_id", "username", "role"]:
            if key in st.session_state:
                del st.session_state[key]
        st.experimental_rerun()


# ------------- STRÁNKA: ZÁPIS MERANÍ ------------- #

def zapis_merania(conn):
    st.title("Zápis hodnôt bazénovej vody")

    today = datetime.now().date()
    datum = st.date_input("Dátum merania", today)

    den = day_of_week_sk(datum)
    st.text_input("Deň", value=den, disabled=True)

    aktualny_cas = datetime.now().strftime("%H:%M")
    st.text_input("Čas merania", value=aktualny_cas, disabled=True)

    volny = st.number_input("Voľný chlór (mg/L)", min_value=0.0, step=0.1)
    celkovy = st.number_input("Celkový chlór (mg/L)", min_value=0.0, step=0.1)

    viazany = max(celkovy - volny, 0.0)
    st.text_input("Viazaný chlór (mg/L)", value=f"{viazany:.2f}", disabled=True)

    ph = st.number_input("pH", min_value=0.0, max_value=14.0, step=0.1)

    teplota = st.number_input("Teplota vody (°C)", min_value=-10.0, max_value=60.0, step=0.1)

    poznamka = st.text_input("Poznámka", "")

    if st.button("Uložiť hodnoty"):
        try:
            cur = conn.cursor()
            cur.execute(
                """
                INSERT INTO measurements
                (date, day, time, free_chlorine, total_chlorine, bound_chlorine,
                 ph, temperature, note, user_id)
                VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
                """,
                (
                    datum,
                    den,
                    aktualny_cas,
                    volny,
                    celkovy,
                    viazany,
                    ph,
                    teplota,
                    poznamka,
                    st.session_state["user_id"],
                ),
            )
            conn.commit()
            st.success("Hodnoty boli úspešne uložené.")
        except Exception as e:
            st.error(f"Chyba pri ukladaní: {e}")


# ------------- STRÁNKA: HISTÓRIA ------------- #

def historia_merani(conn):
    st.title("História meraní")

    cur = conn.cursor()
    cur.execute(
        """
        SELECT
            m.id,
            m.date,
            m.day,
            m.time,
            m.free_chlorine,
            m.total_chlorine,
            m.bound_chlorine,
            m.ph,
            m.temperature,
            m.note,
            u.username
        FROM measurements m
        LEFT JOIN users u ON m.user_id = u.id
        ORDER BY m.date DESC, m.time DESC
        """
    )
    rows = cur.fetchall()

    if not rows:
        st.info("Zatiaľ nie sú žiadne merania.")
        return

    df = pd.DataFrame(
        rows,
        columns=[
            "ID",
            "Dátum",
            "Deň",
            "Čas",
            "Voľný Cl",
            "Celkový Cl",
            "Viazaný Cl",
            "pH",
            "Teplota",
            "Poznámka",
            "Zadal",
        ],
    )

    # pre filter podľa roka/mesiaca
    df["Dátum"] = pd.to_datetime(df["Dátum"])

    st.subheader("Filtrovanie podľa mesiaca")
    col1, col2 = st.columns(2)
    current_year = datetime.now().year
    current_month = datetime.now().month
    rok = col1.number_input("Rok", min_value=2020, max_value=2100, value=current_year)
    mesiac = col2.number_input("Mesiac", min_value=1, max_value=12, value=current_month)

    if st.button("Filtrovať"):
        df = df[(df["Dátum"].dt.year == rok) & (df["Dátum"].dt.month == mesiac)]

        if df.empty:
            st.warning("Pre tento mesiac nie sú žiadne záznamy.")

    # farbenie stĺpca Voľný Cl
    styled = df.style.applymap(farba_volny_chlor, subset=["Voľný Cl"])

    st.dataframe(styled, use_container_width=True)


# ------------- STRÁNKA: GRAFY ------------- #

def grafy_merani(conn):
    st.title("Grafy vývoja")

    cur = conn.cursor()
    cur.execute(
        """
        SELECT
            date,
            time,
            free_chlorine,
            total_chlorine,
            bound_chlorine,
            ph,
            temperature
        FROM measurements
        ORDER BY date ASC, time ASC
        """
    )
    rows = cur.fetchall()

    if not rows:
        st.info("Zatiaľ nie sú žiadne merania na zobrazenie grafov.")
        return

    df = pd.DataFrame(
        rows,
        columns=[
            "Dátum",
            "Čas",
            "Voľný Cl",
            "Celkový Cl",
            "Viazaný Cl",
            "pH",
            "Teplota",
        ],
    )

    df["Dátum"] = pd.to_datetime(df["Dátum"])
    df["Datetime"] = pd.to_datetime(
        df["Dátum"].dt.strftime("%Y-%m-%d") + " " + df["Čas"]
    )
    df = df.set_index("Datetime")

    st.subheader("Chlór (voľný, celkový, viazaný)")
    st.line_chart(df[["Voľný Cl", "Celkový Cl", "Viazaný Cl"]])

    st.subheader("pH")
    st.line_chart(df[["pH"]])

    st.subheader("Teplota vody")
    st.line_chart(df[["Teplota"]])


# ------------- STRÁNKA: EXPORT ------------- #

def export_merani(conn):
    st.title("Export mesačných meraní")

    col1, col2 = st.columns(2)
    current_year = datetime.now().year
    current_month = datetime.now().month
    rok = col1.number_input("Rok", min_value=2020, max_value=2100, value=current_year)
    mesiac = col2.number_input("Mesiac", min_value=1, max_value=12, value=current_month)

    if st.button("Vygenerovať report"):
        cur = conn.cursor()
        cur.execute(
            """
            SELECT
                m.date,
                m.day,
                m.time,
                m.free_chlorine,
                m.total_chlorine,
                m.bound_chlorine,
                m.ph,
                m.temperature,
                m.note,
                u.username
            FROM measurements m
            LEFT JOIN users u ON m.user_id = u.id
            WHERE EXTRACT(YEAR FROM m.date) = %s
              AND EXTRACT(MONTH FROM m.date) = %s
            ORDER BY m.date ASC, m.time ASC
            """,
            (rok, mesiac),
        )
        rows = cur.fetchall()

        if not rows:
            st.warning("V tomto mesiaci nie sú žiadne dáta.")
            return

        df = pd.DataFrame(
            rows,
            columns=[
                "Dátum",
                "Deň",
                "Čas",
                "Voľný Cl",
                "Celkový Cl",
                "Viazaný Cl",
                "pH",
                "Teplota vody",
                "Poznámka",
                "Zadal",
            ],
        )

        # CSV
        csv = df.to_csv(index=False).encode("utf-8")
        st.download_button(
            label="Stiahnuť CSV",
            data=csv,
            file_name=f"bazen_merania_{rok}_{mesiac}.csv",
            mime="text/csv",
        )

        # Excel
        output = BytesIO()
        with pd.ExcelWriter(output, engine="xlsxwriter") as writer:
            df.to_excel(writer, index=False, sheet_name="Merania")

        st.download_button(
            label="Stiahnuť Excel (.xlsx)",
            data=output.getvalue(),
            file_name=f"bazen_merania_{rok}_{mesiac}.xlsx",
            mime=(
                "application/vnd.openxmlformats-officedocument."
                "spreadsheetml.sheet"
            ),
        )


# ------------- STRÁNKA: SPRÁVA POUŽÍVATEĽOV (ADMIN) ------------- #

def sprava_pouzivatelov(conn):
    st.title("Správa používateľov (Admin)")

    if st.session_state.get("role") != "admin":
        st.error("Nemáte oprávnenie na prístup.")
        return

    cur = conn.cursor()
    cur.execute("SELECT id, username, role, created_at FROM users ORDER BY id;")
    rows = cur.fetchall()

    if rows:
        df = pd.DataFrame(
            rows,
            columns=["ID", "Meno", "Rola", "Vytvorený"],
        )
        st.subheader("Existujúci používatelia")
        st.dataframe(df, use_container_width=True)
    else:
        st.info("Zatiaľ nie sú žiadni používatelia.")

    st.subheader("Pridať nového používateľa")

    new_username = st.text_input("Používateľské meno")
    new_password = st.text_input("Heslo", type="password")
    new_role = st.selectbox("Rola", ["user", "admin"])

    if st.button("Pridať používateľa"):
        if not new_username or not new_password:
            st.error("Meno aj heslo musia byť vyplnené.")
            return

        hashed = bcrypt.hashpw(new_password.encode(), bcrypt.gensalt()).decode()

        try:
            cur.execute(
                """
                INSERT INTO users (username, password_hash, role)
                VALUES (%s, %s, %s)
                """,
                (new_username, hashed, new_role),
            )
            conn.commit()
            st.success(f"Používateľ '{new_username}' bol pridaný.")
            st.experimental_rerun()
        except Exception as e:
            st.error(f"Chyba pri pridávaní používateľa: {e}")


# ------------- HLAVNÁ FUNKCIA ------------- #

def main():
    conn = get_connection()
    if conn is None:
        # Bez DB sa nikam nepohneme
        return

    init_db(conn)
    ensure_default_admin(conn)

    if "logged_in" not in st.session_state or not st.session_state["logged_in"]:
        login_screen(conn)
        return

    # Sidebar
    st.sidebar.markdown(f"👤 **{st.session_state['username']}**")
    st.sidebar.markdown(f"Rola: **{st.session_state['role']}**")
    logout_button()

    menu = ["Zápis meraní", "História", "Grafy", "Export"]
    if st.session_state["role"] == "admin":
        menu.append("Správa používateľov")

    vyber = st.sidebar.selectbox("Menu", menu)

    if vyber == "Zápis meraní":
        zapis_merania(conn)
    elif vyber == "História":
        historia_merani(conn)
    elif vyber == "Grafy":
        grafy_merani(conn)
    elif vyber == "Export":
        export_merani(conn)
    elif vyber == "Správa používateľov":
        sprava_pouzivatelov(conn)


if __name__ == "__main__":
    main()
