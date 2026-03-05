import os

folders = ['models', 'services', 'static']

for folder in folders:
    os.makedirs(folder, exist_ok=True)

files = [
    'main.py',
    'init_db.py',
    'models/correction_model.py',
    'services/ocr_service.py',
    'services/db_service.py',
    'services/rag_service.py'
]

for file in files:
    with open(file, 'w', encoding='utf-8') as f:
        pass

print("🎉 [성공] 세이프필 프로젝트의 모든 폴더와 파일 구조가 자동으로 세팅되었습니다!")