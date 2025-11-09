# 현대해상30일책임보험전산 시스템

현대해상 30일 책임보험 가입 및 관리를 위한 웹 기반 전산 시스템입니다.

## 🏗️ 시스템 구조

```
전체관리자 (hyundai)
    ├── 파트너그룹 관리
    ├── 전체 보험 현황 조회
    ├── 전체 정산 관리
    └── 시스템 관리자 관리
        │
        └── 파트너그룹 (예: 부산자동차매매사업자조합)
            ├── 파트너그룹 관리자
            └── 회원사들
                ├── 책임보험 신청
                ├── 신청 현황 조회
                └── 정산 내역 확인
```

## ✨ 주요 기능

### 🔐 다중 사용자 권한 관리
- **전체관리자**: 시스템 전체 관리 및 파트너그룹 생성
- **파트너관리자**: 소속 회원사 관리 및 보험 승인
- **회원사**: 책임보험 신청 및 현황 조회

### 🏢 파트너그룹별 브랜딩
- 파트너그룹별 로고 설정 가능
- 동적 로고 변경 및 그룹명 표시
- 현대해상 기본 브랜딩 적용

### 📋 보험 관리 기능
- 책임보험 온라인 신청
- 엑셀 일괄 업로드/다운로드
- 신청 승인/반려 처리
- 실시간 현황 조회

### 💰 정산 관리
- 월별/연도별 정산 내역
- 자동 정산 계산 (건당 9,500원)
- PDF 인보이스 생성
- 파트너그룹별 정산 분리

## 🚀 빠른 시작

### Docker를 이용한 실행 (권장)

```bash
# 1. 저장소 클론
git clone https://github.com/JTY9410/hyundai.git
cd hyundai

# 2. Docker Compose로 실행
docker-compose up -d

# 3. 웹 브라우저에서 접속
open http://localhost:8001
```

### Docker Hub 이미지 사용

```bash
# 이미지 다운로드 및 실행
docker pull wecarmobility/hyundai-insurance:latest
docker run -d -p 8001:5000 \
  -v $(pwd)/data:/app/data \
  -v $(pwd)/static:/app/static \
  -v $(pwd)/uploads:/app/uploads \
  --name hyundai-insurance-app \
  wecarmobility/hyundai-insurance:latest
```

### 로컬 개발 환경

```bash
# 1. Python 가상환경 생성
python -m venv venv
source venv/bin/activate  # Windows: venv\Scripts\activate

# 2. 의존성 설치
pip install -r requirements.txt

# 3. 애플리케이션 실행
python app.py

# 4. 웹 브라우저에서 접속
open http://localhost:5000
```

## 🔑 기본 계정 정보

### 전체관리자
- **파트너그룹**: `전체관리자` 선택
- **사용자명**: `hyundai`
- **비밀번호**: `#admin1004`

### 회원가입
1. 웹사이트 접속 후 "회원가입" 클릭
2. 파트너그룹 선택 (사전에 전체관리자가 생성한 그룹)
3. 회원 정보 입력 및 사업자등록증 첨부
4. 파트너그룹 관리자 승인 대기

## 📁 프로젝트 구조

```
hyundai/
├── app.py                 # Flask 메인 애플리케이션
├── requirements.txt       # Python 의존성
├── docker-compose.yml     # Docker Compose 설정
├── Dockerfile            # Docker 이미지 빌드 설정
├── 요구사항.md            # 상세 요구사항 문서
├── static/               # 정적 파일 (CSS, JS, 이미지)
│   ├── hyundai_logo.png  # 현대해상 로고
│   └── partner_logos/    # 파트너그룹별 로고
├── templates/            # HTML 템플릿
│   ├── auth/            # 로그인/회원가입
│   ├── admin/           # 전체관리자 페이지
│   └── partner/         # 파트너그룹 페이지
├── data/                # SQLite 데이터베이스
└── uploads/             # 업로드된 파일
```

## 🛠️ 기술 스택

- **Backend**: Python 3.11, Flask, SQLAlchemy
- **Database**: SQLite (개발), PostgreSQL (운영 권장)
- **Frontend**: HTML5, Tailwind CSS, Bootstrap Icons
- **Container**: Docker, Docker Compose
- **File Processing**: Pandas, OpenPyXL

## 📊 데이터베이스 스키마

### PartnerGroup (파트너그룹)
- 파트너그룹 기본 정보
- 관리자 계정 정보
- 로고 및 브랜딩 설정

### Member (회원사)
- 회원사 기본 정보
- 파트너그룹 소속 정보
- 사업자등록증 첨부

### InsuranceApplication (보험신청)
- 보험 신청 정보
- 차량 정보
- 승인/반려 상태 관리

## 🔧 환경 설정

### 환경 변수
```bash
# 데이터베이스 설정 (선택사항)
DATABASE_URL=postgresql://user:password@localhost/hyundai_insurance

# Flask 설정
FLASK_ENV=production
SECRET_KEY=your-secret-key-here
```

### 파트너그룹 로고 설정
```bash
# 파트너그룹 ID가 1인 경우
cp your_logo.png static/partner_logos/group_1_logo.png
```

## 📈 운영 가이드

### 백업
```bash
# SQLite 데이터베이스 백업
cp data/busan.db data/backup_$(date +%Y%m%d).db

# 업로드 파일 백업
tar -czf uploads_backup_$(date +%Y%m%d).tar.gz uploads/
```

### 로그 확인
```bash
# Docker 로그 확인
docker logs hyundai-insurance-app

# 실시간 로그 모니터링
docker logs -f hyundai-insurance-app
```

## 🤝 기여하기

1. Fork the Project
2. Create your Feature Branch (`git checkout -b feature/AmazingFeature`)
3. Commit your Changes (`git commit -m 'Add some AmazingFeature'`)
4. Push to the Branch (`git push origin feature/AmazingFeature`)
5. Open a Pull Request

## 📝 라이선스

이 프로젝트는 MIT 라이선스 하에 배포됩니다. 자세한 내용은 `LICENSE` 파일을 참조하세요.

## 📞 지원

문의사항이나 기술 지원이 필요한 경우:
- GitHub Issues: [https://github.com/JTY9410/hyundai/issues](https://github.com/JTY9410/hyundai/issues)
- Docker Hub: [wecarmobility/hyundai-insurance](https://hub.docker.com/r/wecarmobility/hyundai-insurance)

---

**현대해상30일책임보험전산** - 효율적이고 안전한 보험 관리 시스템 🚗💼