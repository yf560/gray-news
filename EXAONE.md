# EXAONE + Tavily 전환

실제 분석은 프로젝트 루트의 `.env`에 키를 넣고 실행합니다.

    EXAONE_API_KEY=발급받은_엑사원_키
    TAVILY_API_KEY=발급받은_Tavily_키
    py server.py

선택 환경변수: EXAONE_MODEL (기본값 k-exaone_v2.5). 셸에 같은 변수가 있으면 셸 값이 우선합니다.
`.env`는 Git에서 제외합니다. 키를 커밋하거나 프론트 코드에 넣지 마세요.

처리 흐름: URL 본문 추출 → 엑사원 검색어 생성 → Tavily 검색 최대 3회 → 엑사원 관점 분석 → 출처 ID 검증.
본문을 직접 입력하면 URL 추출을 생략합니다. 추출 실패 시 본문 붙여넣기를 안내합니다.
검색은 공식 발표도 찾을 수 있도록 general 모드로 수행합니다.
응답은 기존 B 화면의 version: 1 구조를 사용하며 개인 기록은 브라우저에 저장됩니다.
모델이 생성한 URL을 출처로 채택하지 않고 검색 결과에 있는 URL에 서버가 ID를 부여합니다.
출처 ID 검증은 출처 존재 여부를 확인하며 주장 자체의 진위를 보증하지 않습니다.

API 응답을 대체한 단위 테스트: py -m unittest test_server.py
실제 API 키를 이용한 연동 검증은 별도 필요합니다.

공식 문서:
- https://docs.exaone.ai/ko/docs/k-exaone-2.5
- https://docs.tavily.com/documentation/api-reference/endpoint/search
- https://docs.tavily.com/documentation/api-reference/endpoint/extract

외부 요청 제한: 검색어 생성 60초, 최종 분석 180초, Tavily 20초. 429는 최대 2회 재시도하며 Retry-After가 20초보다 크면 즉시 RATE_LIMITED로 안내합니다. 단계와 경과 시간만 로그에 기록하며 키·본문·API 응답은 기록하지 않습니다.
