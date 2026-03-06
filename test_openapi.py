import requests

API_KEY = "c19810ce2ac3ca44903a0b27c6773c819ca6da948e6d12195b77101538bec3f4"

def test_public_api(drug_name):
    print(f"\n🔍 '{drug_name}' 국가 데이터 조회 중...")
    url = "http://apis.data.go.kr/1471000/DURPrdlstInfoService03/getUsjntTabooInfoList03"
    params = {
        "serviceKey": requests.utils.unquote(API_KEY),
        "pageNo": "1",
        "numOfRows": "3",
        "type": "json",
        "itemName": drug_name
    }

    try:
        response = requests.get(url, params=params, timeout=5)
        if response.status_code == 200:
            data = response.json()
            total_count = data.get('body', {}).get('totalCount', 0)
            
            print(f"✅ 결과: 병용금기(위험) 데이터가 총 [{total_count}건] 발견되었습니다!")
            
            if total_count > 0:
                print("⚠️ [같이 먹으면 위험한 성분 예시]")
                items = data.get('body', {}).get('items', [])
                for item in items:
                    mix_drug = item.get('MIXTURE_INGR_KOR_NAME', '알수없음')
                    warning = item.get('PROHBT_CONTENT', '경고내용 없음')
                    print(f" 🚫 절대 같이 먹지 마세요: {mix_drug}")
                    print(f"    ↳ 부작용: {warning}\n")
        else:
            print(f"❌ 에러: {response.status_code}")
    except Exception as e:
        print(f"❌ 통신 에러 발생: {e}")

if __name__ == "__main__":
    print("💊 [세이프필] 실시간 병용금기 검색기를 시작합니다!")
    while True:
        user_input = input("\n👉 검색할 약 이름을 입력하세요 (종료하려면 '끝' 입력): ")
        
        if user_input == '끝':
            print("검색기를 종료합니다. 수고하셨습니다!")
            break
            
        test_public_api(user_input)