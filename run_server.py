import uvicorn

if __name__ == "__main__":
    print("🌐 세이프필(SafePill) 메인 서버를 구동합니다...")
    # uvicorn 서버를 파이썬 코드 안에서 직접 실행시킵니다.
    uvicorn.run("main:app", host="127.0.0.1", port=8000, reload=True)