# Cafe24 AI Studio — GitHub + Render 배포용 (개인 API 키)

## 핵심
- 사용자마다 웹페이지에서 **자신의 Cafe24 API 키**를 입력합니다.
- 키는 브라우저 탭 메모리에서만 유지되고 localStorage나 GitHub에 저장되지 않습니다.
- API 요청 때 `X-Cafe24-Key` 헤더로 배포된 Python 서버에 전송하고, 서버는 Cafe24로 중계합니다. 서버 운영자와 호스팅 제공자는 키에 접근할 수 있으므로 신뢰 가능한 환경에서만 사용하세요.
- 서버에 공용 키를 등록할 필요가 없습니다.
- 여러 모델 선택, 입력/출력 토큰 단가 표기(확인된 경우에만), 이미지 붙여넣기/첨부, 동영상 3프레임 추출, 대화 기능 유지.

## GitHub에서 실행되는 것은 아님
GitHub 저장소는 코드 보관소입니다. **GitHub Pages만으로는 Python `server.py`를 실행할 수 없습니다.** Render Web Service에 GitHub 저장소를 연결하면 외부 사용자가 `https://...onrender.com`에 접속할 수 있습니다.

## 배포 순서
1. GitHub에서 새 Repository를 만듭니다. 예: `cafe24-ai-studio`.
2. 이 폴더의 파일을 Repository 최상단에 업로드합니다 (`.env`나 실제 API 키 제외).
3. Render (https://dashboard.render.com/) → New → Web Service → GitHub 저장소 연결.
4. `render.yaml`을 사용해 Blueprint로 배포하거나 Web Service에서 `Build Command: pip install -r requirements.txt`, `Start Command: python server.py` 입력.
5. Render가 제공하는 HTTPS 주소를 학생들에게 배포합니다.
6. 학생이 해당 주소 접속 → 개인 Cafe24 API 키 입력 → 모델 선택 → 채팅.

## 로컬 실행
```bash
python -m pip install -r requirements.txt
python server.py
```
`http://127.0.0.1:8000`으로 접속합니다. 로컬에서도 API 키는 브라우저에서 입력합니다.

## 주의
- 개인 API 키를 입력하는 웹사이트의 **운영자/서버를 신뢰**해야 합니다. 서버 코드는 요청을 기록하지 않도록 작성했지만 서버·호스팅 운영자는 기술적으로 접근할 수 있습니다.
- 배포 전에 HTTPS 주소인지 확인하세요. 타인이 작성한 포크나 가짜 사이트에 키를 입력하지 마세요.
- 공용 PC에서는 사용 후 탭을 닫고 브라우저의 사이트 데이터를 삭제하세요. 대화 텍스트는 브라우저 로컬 저장소에 남을 수 있습니다.
- API 키에 사용 한도/만료일을 설정하세요. 학생에게 관리자 공용 키를 주지 마세요.
- 외부 공개 시 과도한 요청 및 호스팅 비용에 주의하세요. 이 예제는 교육용이며 운영 환경의 인증/사용자 제한/모니터링이 추가로 필요할 수 있습니다.
- 이미지 입력은 비전 지원 모델에만 가능하고, 동영상은 음성과 전체 시간 맥락이 아니라 3프레임 이미지로 처리됩니다.
- 모델 가격은 Cafe24 API가 원화/100만 토큰 형식으로 제공하거나 `model_prices.json`에 수동 등록된 경우에만 표시합니다.