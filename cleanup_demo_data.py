import sqlite3
from pathlib import Path

DB_PATH = Path(__file__).resolve().parent / 'safepill.db'

TARGETS = [
    ('홍길동', '와파린'),
]


def main():
    if not DB_PATH.exists():
        print(f'DB not found: {DB_PATH}')
        return

    conn = sqlite3.connect(str(DB_PATH))
    cur = conn.cursor()

    print('[before] matching rows')
    total = 0
    for nickname, medicine_name in TARGETS:
        cur.execute(
            'SELECT id, nickname, medicine_name, COALESCE(active_ingredients, "") FROM user_medicines WHERE nickname = ? AND medicine_name = ?',
            (nickname, medicine_name),
        )
        rows = cur.fetchall()
        total += len(rows)
        for row in rows:
            print(row)

    if total == 0:
        print('No matching demo rows found.')
        conn.close()
        return

    for nickname, medicine_name in TARGETS:
        cur.execute(
            'DELETE FROM user_medicines WHERE nickname = ? AND medicine_name = ?',
            (nickname, medicine_name),
        )

    conn.commit()
    print(f'Removed {cur.rowcount if cur.rowcount is not None else total} row(s).')

    print('[after] remaining rows')
    for nickname, medicine_name in TARGETS:
        cur.execute(
            'SELECT id, nickname, medicine_name, COALESCE(active_ingredients, "") FROM user_medicines WHERE nickname = ? AND medicine_name = ?',
            (nickname, medicine_name),
        )
        print((nickname, medicine_name), cur.fetchall())

    conn.close()


if __name__ == '__main__':
    main()
