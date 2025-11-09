#!/bin/bash

# 강제 재빌드 스크립트 - 모든 캐시 제거
# Docker 컨테이너 에러 발생 시 실행할 것
# 실행 방법: bash force-rebuild.sh 또는 ./force-rebuild.sh

echo "🧹 Force rebuilding with cache clearing..."

# 모든 컨테이너 중지 및 제거
echo "⏹️  Stopping and removing containers..."
docker compose down --volumes --remove-orphans

# hyundai-insurance 이미지 제거
echo "🗑️  Removing old images..."
docker rmi $(docker images "hyundai-insurance*" -q) 2>/dev/null || true
docker rmi hyundai-insurance:local 2>/dev/null || true

# Docker 빌드 캐시 정리
echo "🧽 Cleaning build cache..."
docker builder prune -f

# 네트워크 정리
echo "🌐 Cleaning networks..."
docker network prune -f

# 완전 재빌드 (캐시 없이)
echo "🔨 Complete rebuild (no cache)..."
docker compose build --no-cache --pull

# 재시작
echo "🚀 Starting fresh containers..."
docker compose up -d

echo ""
echo "✅ Force rebuild complete!"
echo "📱 App available at: http://localhost:8901"
echo "🌍 Production: https://hyundai.wecarmobility.co.kr"
echo ""

# 로그 확인
echo "📋 Checking logs..."
sleep 3
docker compose logs --tail=50

echo ""
echo "💡 실시간 로그 보기: docker compose logs -f hyundai-insurance"
