#!/bin/bash

# 현대해상30일책임보험전산 시스템 시작 스크립트
# 사용법: ./start.sh

set -e

echo "🏢 현대해상30일책임보험전산 시스템 시작"
echo "================================================"
echo ""

# Python 버전 확인
echo "🐍 Python 버전 확인..."
python3 --version || {
    echo "❌ Python 3이 설치되지 않았습니다."
    echo "Python 3.8 이상을 설치해주세요."
    exit 1
}

# 가상환경 확인 및 생성
if [ ! -d "venv" ]; then
    echo "📦 가상환경 생성 중..."
    python3 -m venv venv
fi

# 가상환경 활성화
echo "🔧 가상환경 활성화..."
source venv/bin/activate

# 의존성 설치
echo "📚 의존성 패키지 설치..."
pip install --upgrade pip
pip install -r requirements.txt

# 필요한 디렉토리 생성
echo "📁 디렉토리 생성..."
mkdir -p data static uploads instance

# 환경 변수 설정
export FLASK_APP=app.py
export FLASK_ENV=development
export SECRET_KEY="hyundai-dev-secret-key-$(date +%s)"

echo ""
echo "✅ 설정 완료!"
echo ""
echo "🚀 서버 시작 중..."
echo "📍 접속 주소: http://localhost:5000"
echo ""
echo "🔑 전체관리자 로그인 정보:"
echo "   파트너그룹: 전체관리자"
echo "   아이디: hyundai"
echo "   비밀번호: #admin1004"
echo ""
echo "⏹️  서버 중지: Ctrl+C"
echo "================================================"
echo ""

# Flask 애플리케이션 실행
python app.py
