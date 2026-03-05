import io
import numpy as np
from PIL import Image
import easyocr
from typing import List

# ✅ Reader는 1번만 생성
_reader = easyocr.Reader(['ko', 'en'], gpu=False)

async def extract_text_from_images(images: List):
    """
    images: List[FastAPI UploadFile]
    return: 모든 이미지에서 추출된 결합 텍스트(str)
    """
    combined_text = []

    for image in images:
        # 1. 파일 내용 읽기
        contents = await image.read()
        
        # 2. PIL 이미지로 변환 후 Numpy 배열로 변환
        img = Image.open(io.BytesIO(contents))
        np_image = np.array(img)

        # 3. OCR 실행
        results = _reader.readtext(np_image, detail=0)
        
        # 4. 결과 저장
        combined_text.append(" ".join(results))
        
        # (선택 사항) 다음 파일을 위해 포인터 초기화는 하지 않아도 되지만, 
        # 필요하다면 image.seek(0)를 사용할 수 있습니다.

    # 모든 리스트의 내용을 하나의 문자열로 합쳐서 반환
    return " ".join(combined_text).strip()