import io
import numpy as np
from PIL import Image
import easyocr

# ✅ Reader는 1번만 생성 (매 요청마다 만들면 느림)
_reader = easyocr.Reader(['ko', 'en'], gpu=False)

async def extract_text_from_image(image):
    """
    image: FastAPI UploadFile
    return: OCR 결과 텍스트(str)
    """
    contents = await image.read()
    np_image = np.array(Image.open(io.BytesIO(contents)))

    results = _reader.readtext(np_image, detail=0)
    text = " ".join(results)
    return text.strip()