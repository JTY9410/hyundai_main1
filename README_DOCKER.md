# 🐳 현대해상30일책임보험전산 - Docker 배포

## 🚀 빠른 시작

### 1. 로컬 실행 (Python 직접 실행)
```bash
# 간단한 실행
./start.sh

# 또는 수동 실행
python3 -m venv venv
source venv/bin/activate
pip install -r requirements.txt
python app.py
```

### 2. Docker 실행

#### Docker Desktop이 설치된 경우:
```bash
# 1. Docker 이미지 빌드
./docker-build.sh

# 2. Docker Compose로 실행
docker-compose up -d

# 3. 브라우저에서 접속
# http://localhost:8000
```

#### Docker Hub에서 이미지 다운로드:
```bash
# 이미지 다운로드 (Docker Hub에 업로드된 경우)
docker pull YOUR_USERNAME/hyundai-insurance:latest

# 컨테이너 실행
docker run -d -p 8000:5000 \
  -v $(pwd)/data:/app/data \
  -v $(pwd)/static:/app/static \
  -v $(pwd)/uploads:/app/uploads \
  YOUR_USERNAME/hyundai-insurance:latest
```

## 📦 Docker Hub 업로드 방법

### 1. Docker Hub 계정 준비
1. https://hub.docker.com 에서 계정 생성
2. 로컬에서 Docker 로그인:
   ```bash
   docker login
   ```

### 2. 이미지 빌드 및 업로드
```bash
# Docker Hub 사용자명을 입력하여 업로드
./docker-upload.sh YOUR_DOCKERHUB_USERNAME

# 예시
./docker-upload.sh hyundai
./docker-upload.sh mycompany
```

### 3. 업로드 확인
- Docker Hub 웹사이트에서 이미지 확인
- 다른 서버에서 이미지 다운로드 테스트:
  ```bash
  docker pull YOUR_USERNAME/hyundai-insurance:latest
  ```

## 🏢 시스템 정보

### 기본 로그인 정보
- **전체관리자**
  - 파트너그룹: "전체관리자" 선택
  - 아이디: `hyundai`
  - 비밀번호: `#admin1004`

### 포트 정보
- **로컬 실행**: http://localhost:5000
- **Docker 실행**: http://localhost:8000

### 주요 기능
1. **전체관리자섹션**
   - 파트너그룹 생성/관리
   - 전체 보험 현황 조회
   - 전체 정산 관리
   - 관리자 계정 관리

2. **파트너그룹섹션**
   - 파트너그룹별 대시보드
   - 보험 가입 신청/관리
   - 회원가입 승인
   - 보험 승인 처리

## 🛠️ 개발 환경

### 기술 스택
- **Backend**: Python 3.11, Flask
- **Database**: SQLite (기본), PostgreSQL 지원
- **Frontend**: HTML5, CSS3, JavaScript, Bootstrap 5
- **Container**: Docker, Docker Compose

### 프로젝트 구조
```
hyundai/
├── app.py                 # 메인 애플리케이션
├── requirements.txt       # Python 의존성
├── Dockerfile            # Docker 이미지 정의
├── docker-compose.yml    # Docker Compose 설정
├── docker-build.sh       # 로컬 빌드 스크립트
├── docker-upload.sh      # Docker Hub 업로드 스크립트
├── start.sh             # 간단 실행 스크립트
├── templates/           # HTML 템플릿
│   ├── admin/          # 전체관리자 페이지
│   ├── partner/        # 파트너그룹 페이지
│   └── auth/           # 로그인/회원가입
├── static/             # 정적 파일 (CSS, JS, 이미지)
├── data/               # 데이터베이스 파일
└── uploads/            # 업로드된 파일
```

## 📋 배포 체크리스트

### 프로덕션 배포 전 확인사항
- [ ] SECRET_KEY 환경변수 설정
- [ ] 데이터베이스 백업 설정
- [ ] 방화벽 포트 설정 (8000번)
- [ ] SSL 인증서 설정 (HTTPS)
- [ ] 로그 로테이션 설정
- [ ] 모니터링 도구 설정

### Docker 배포 확인사항
- [ ] Docker Desktop 설치 및 실행
- [ ] 충분한 디스크 공간 (최소 5GB)
- [ ] 메모리 할당 (최소 2GB)
- [ ] 포트 충돌 확인
- [ ] 볼륨 마운트 권한 설정

## 🔧 문제 해결

### 일반적인 문제
1. **포트 충돌**: 8000번 포트가 사용 중인 경우
   ```bash
   # 다른 포트 사용
   docker run -p 8080:5000 hyundai-insurance:latest
   ```

2. **권한 문제**: 볼륨 마운트 권한 오류
   ```bash
   sudo chown -R $USER:$USER ./data ./static ./uploads
   ```

3. **메모리 부족**: 컨테이너 메모리 제한
   ```bash
   docker run --memory="2g" hyundai-insurance:latest
   ```

### 로그 확인
```bash
# 컨테이너 로그 확인
docker logs hyundai-insurance-app

# 실시간 로그 확인
docker logs -f hyundai-insurance-app
```

## 📞 지원

자세한 배포 가이드는 `DOCKER_DEPLOYMENT.md` 파일을 참조하세요.

---

**🏢 현대해상30일책임보험전산**  
**📅 2024년 11월**
