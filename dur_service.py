import requests

API_KEY = "c19810ce2ac3ca44903a0b27c6773c819ca6da948e6d12195b77101538bec3f4"

def check_dur_api(drug_name):
    """
    메인 서버(main.py)에서 약 이름을 던져주면, 
    국가 서버를 조회한 뒤 결과를 예쁜 데이터 포맷으로 돌려주는 함수
    """
    url = "http://apis.data.go.kr/1471000/DURPrdlstInfoService03/getUsjntTabooInfoList03"
    params = {
        "serviceKey": requests.utils.unquote(API_KEY),
        "pageNo": "1",
        "numOfRows": "5",
        "type": "json",
        "itemName": drug_name
    }

    try:
        response = requests.get(url, params=params, timeout=5)
        
        if response.status_code == 200:
            data = response.json()
            total_count = data.get('body', {}).get('totalCount', 0)
            
            if total_count == 0:
                return {
                    "status": "safe", 
                    "message": "국가 DUR 기준 병용금기 사항 없음", 
                    "warnings": []
                }
            
            items = data.get('body', {}).get('items', [])
            warning_list = []
            for item in items:
                warning_list.append({
                    "mix_drug": item.get('MIXTURE_INGR_KOR_NAME', '알수없음'),
                    "warning_text": item.get('PROHBT_CONTENT', '경고내용 없음')
                })
                
            return {
                "status": "danger", 
                "message": f"총 {total_count}건의 병용금기 발견", 
                "warnings": warning_list
            }
        else:
            return {"status": "error", "message": "국가 서버 통신 실패"}
            
    except Exception as e:
        return {"status": "error", "message": f"서버 오류: {str(e)}"}