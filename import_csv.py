import pandas as pd
import sqlite3

def import_dur_data():
    print("⏳ 식약처 공공데이터를 불러오는 중입니다. 잠시만 기다려주세요...")
    
    try:
        df = pd.read_csv('dur_data.csv', encoding='cp949')
    except Exception as e:
        print(f"❌ 파일 읽기 오류: {e}")
        return

    df.columns = df.columns.str.strip()
    
    def clean_drug_name(name):
        if pd.isna(name): return ""
    
        return str(name).split('(')[0].strip()

    df['clean_drug_a'] = df['제품명1'].apply(clean_drug_name)
    df['clean_drug_b'] = df['제품명2'].apply(clean_drug_name)
    
    df['금기사유'] = df['금기사유'].fillna('병용금기 약물 (상세사유 없음)')

    records = []
    for index, row in df.iterrows():
        if row['clean_drug_a'] and row['clean_drug_b']:
            records.append((
                row['clean_drug_a'], 
                row['clean_drug_b'], 
                '위험', 
                str(row['금기사유'])
            ))

    print(f"🚀 총 {len(records):,}건의 데이터를 정제했습니다. 세이프필 DB에 저장을 시작합니다...")
    conn = sqlite3.connect('pharmguard.db')
    cursor = conn.cursor()

    cursor.execute('DELETE FROM dur_contraindications')

    cursor.executemany('''
        INSERT INTO dur_contraindications (drug_a, drug_b, risk_level, reason)
        VALUES (?, ?, ?, ?)
    ''', records)

    conn.commit()
    conn.close()
    
    print("✅ [성공] 식약처 병용금기 공공데이터가 세이프필(SafePill) 데이터베이스에 완벽하게 세팅되었습니다!")

if __name__ == '__main__':
    import_dur_data()