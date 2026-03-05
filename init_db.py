import sqlite3

def create_and_seed_db():
    conn = sqlite3.connect('pharmguard.db')
    cursor = conn.cursor()

    cursor.execute('''
        CREATE TABLE IF NOT EXISTS dur_contraindications (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            drug_a TEXT NOT NULL,
            drug_b TEXT NOT NULL,
            risk_level TEXT NOT NULL,  -- '위험' 또는 '주의'
            reason TEXT NOT NULL
        )
    ''')

    cursor.execute('DELETE FROM dur_contraindications')

    sample_data = [
        ('타이레놀', '이부프로펜', '주의', '신장 부담 및 위장관 출혈 위험 증가'),
        ('제클라정', '피모자이드', '위험', '심각한 심실성 부정맥 발생 위험 (절대 병용 금기)'),
        ('아스피린', '와파린', '위험', '혈액 응고 지연으로 인한 치명적 출혈 위험 급증'),
        ('로바스타틴', '에리스로마이신', '위험', '근병증 및 횡문근융해증 발생 위험 증가')
    ]

    cursor.executemany('''
        INSERT INTO dur_contraindications (drug_a, drug_b, risk_level, reason)
        VALUES (?, ?, ?, ?)
    ''', sample_data)

    conn.commit()
    conn.close()
    print("✅ [성공] 식약처 병용금기 데이터베이스(pharmguard.db)가 성공적으로 생성되었습니다!")

if __name__ == "__main__":
    create_and_seed_db()