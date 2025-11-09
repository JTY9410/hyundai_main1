# 🐳 현대해상30일책임보험전산 Docker 배포 가이드

## 📋 개요
현대해상30일책임보험전산 시스템을 Docker 컨테이너로 배포하는 방법을 안내합니다.

## 🛠️ 사전 준비사항

### 1. Docker 설치
- **Docker Desktop** (Windows/Mac): https://www.docker.com/products/docker-desktop
- **Docker Engine** (Linux): https://docs.docker.com/engine/install/

### 2. 시스템 요구사항
- **메모리**: 최소 2GB, 권장 4GB
- **디스크**: 최소 5GB 여유 공간
- **포트**: 8000번 포트 사용 가능

## 🚀 빠른 시작

### 방법 1: Docker Compose 사용 (권장)

```bash
# 1. 프로젝트 디렉토리로 이동
cd /Users/USER/dev/hyundai

# 2. Docker 이미지 빌드
./docker-build.sh

# 3. 서비스 시작
docker-compose up -d

# 4. 브라우저에서 접속
# http://localhost:8000
```

### 방법 2: Docker 직접 실행

```bash
# 1. 이미지 빌드
docker build -t hyundai-insurance:latest .

# 2. 컨테이너 실행
docker run -d \
  --name hyundai-insurance-app \
  -p 8000:5000 \
  -v $(pwd)/data:/app/data \
  -v $(pwd)/static:/app/static \
  -v $(pwd)/uploads:/app/uploads \
  -e SECRET_KEY="your-secret-key-here" \
  hyundai-insurance:latest
```

## 📦 Docker Hub 업로드

### 1. Docker Hub 계정 준비
- Docker Hub 계정 생성: https://hub.docker.com
- 로컬에서 Docker Hub 로그인

### 2. 이미지 업로드
```bash
# Docker Hub 사용자명을 입력하여 업로드
./docker-upload.sh YOUR_DOCKERHUB_USERNAME

# 예시
./docker-upload.sh hyundai
```

### 3. 업로드된 이미지 사용
```bash
# 이미지 다운로드
docker pull YOUR_DOCKERHUB_USERNAME/hyundai-insurance:latest

# 컨테이너 실행
docker run -d -p 8000:5000 YOUR_DOCKERHUB_USERNAME/hyundai-insurance:latest
```

## 🔧 환경 설정

### 환경 변수
| 변수명 | 설명 | 기본값 |
|--------|------|--------|
| `SECRET_KEY` | Flask 보안 키 | `hyundai-secret-key-change-in-production` |
| `FLASK_ENV` | Flask 환경 | `production` |
| `DATABASE_URL` | 외부 데이터베이스 URL | SQLite 사용 |

### 볼륨 마운트
| 호스트 경로 | 컨테이너 경로 | 설명 |
|-------------|---------------|------|
| `./data` | `/app/data` | 데이터베이스 파일 |
| `./static` | `/app/static` | 정적 파일 (로고 등) |
| `./uploads` | `/app/uploads` | 업로드된 파일 |

## 🏥 헬스체크

### 컨테이너 상태 확인
```bash
# 컨테이너 상태 확인
docker ps

# 로그 확인
docker logs hyundai-insurance-app

# 헬스체크 엔드포인트
curl http://localhost:8000/healthz
```

## 🔐 보안 설정

### 1. 프로덕션 환경 설정
```bash
# 강력한 SECRET_KEY 생성
python -c "import secrets; print(secrets.token_hex(32))"

# docker-compose.yml에서 환경변수 설정
export SECRET_KEY="generated-secret-key"
docker-compose up -d
```

### 2. 방화벽 설정
```bash
# 8000번 포트만 허용
sudo ufw allow 8000/tcp
```

## 📊 모니터링

### 리소스 사용량 확인
```bash
# 컨테이너 리소스 사용량
docker stats hyundai-insurance-app

# 디스크 사용량
docker system df
```

### 로그 관리
```bash
# 실시간 로그 확인
docker logs -f hyundai-insurance-app

# 로그 파일 크기 제한 (docker-compose.yml)
logging:
  driver: "json-file"
  options:
    max-size: "10m"
    max-file: "3"
```

## 🔄 업데이트 및 백업

### 시스템 업데이트
```bash
# 1. 새 이미지 빌드
./docker-build.sh

# 2. 서비스 재시작
docker-compose down
docker-compose up -d
```

### 데이터 백업
```bash
# 데이터베이스 백업
cp ./data/busan.db ./backup/busan_$(date +%Y%m%d_%H%M%S).db

# 업로드 파일 백업
tar -czf ./backup/uploads_$(date +%Y%m%d_%H%M%S).tar.gz ./uploads/
```

## 🐛 문제 해결

### 일반적인 문제들

#### 1. 포트 충돌
```bash
# 사용 중인 포트 확인
lsof -i :8000

# 다른 포트 사용
docker run -p 8080:5000 hyundai-insurance:latest
```

#### 2. 권한 문제
```bash
# 볼륨 디렉토리 권한 설정
sudo chown -R $USER:$USER ./data ./static ./uploads
chmod 755 ./data ./static ./uploads
```

#### 3. 메모리 부족
```bash
# 메모리 제한 설정
docker run --memory="2g" hyundai-insurance:latest
```

## 📞 지원

### 로그 수집
문제 발생 시 다음 정보를 수집해주세요:

```bash
# 시스템 정보
docker version
docker-compose version

# 컨테이너 정보
docker ps -a
docker logs hyundai-insurance-app

# 시스템 리소스
free -h
df -h
```

## 🎯 성능 최적화

### 1. 프로덕션 최적화
```dockerfile
# Dockerfile에 멀티스테이지 빌드 적용
FROM python:3.11-slim as builder
# ... 빌드 단계

FROM python:3.11-slim as runtime
# ... 런타임 단계
```

### 2. 캐시 최적화
```bash
# 빌드 캐시 활용
docker build --cache-from hyundai-insurance:latest -t hyundai-insurance:latest .
```

---

## 📝 기본 로그인 정보

**전체관리자 계정**
- 파트너그룹: "전체관리자" 선택
- 아이디: `hyundai`
- 비밀번호: `#admin1004`

시스템 접속 후 파트너그룹을 생성하고 테스트를 진행하세요.

---

**🏢 현대해상30일책임보험전산 시스템**  
**📧 기술지원**: 시스템 관리자에게 문의  
**📅 업데이트**: 2024년 11월
