#!/usr/bin/env python3
"""
데이터베이스 연결 및 데이터 저장 테스트 스크립트
"""
import sys
import os
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from app import app, db, ensure_initialized, PartnerGroup, Member, InsuranceApplication
from datetime import datetime, date
from pytz import timezone

KST = timezone('Asia/Seoul')

def test_database_connection():
    """데이터베이스 연결 테스트"""
    print("=" * 60)
    print("데이터베이스 연결 및 저장 테스트")
    print("=" * 60)
    
    with app.app_context():
        ensure_initialized()
        
        # 1. 데이터베이스 연결 확인
        print("\n1. 데이터베이스 연결 확인:")
        try:
            db_uri = app.config.get('SQLALCHEMY_DATABASE_URI', 'N/A')
            print(f"   DB URI: {db_uri}")
            
            # 테이블 존재 확인
            inspector = db.inspect(db.engine)
            tables = inspector.get_table_names()
            print(f"   테이블 목록: {tables}")
            
            # 기존 데이터 확인
            pg_count = db.session.query(PartnerGroup).count()
            member_count = db.session.query(Member).count()
            ins_count = db.session.query(InsuranceApplication).count()
            print(f"   기존 데이터:")
            print(f"     - 파트너그룹: {pg_count}개")
            print(f"     - 회원사: {member_count}개")
            print(f"     - 보험신청: {ins_count}개")
        except Exception as e:
            print(f"   ❌ 오류: {e}")
            return False
        
        # 2. 데이터 저장 테스트
        print("\n2. 데이터 저장 테스트:")
        try:
            # 테스트용 파트너그룹 생성 (중복 방지)
            test_name = f"테스트그룹_{datetime.now(KST).strftime('%Y%m%d%H%M%S')}"
            existing = db.session.query(PartnerGroup).filter_by(name=test_name).first()
            if existing:
                print(f"   테스트 데이터가 이미 존재합니다. 삭제 중...")
                db.session.delete(existing)
                db.session.commit()
            
            # 테스트 파트너그룹 생성
            test_group = PartnerGroup(
                name=test_name,
                admin_username=f"test_admin_{datetime.now(KST).strftime('%Y%m%d%H%M%S')}",
                business_number="123-45-67890",
                representative="테스트대표",
                phone="02-1234-5678",
                address="테스트주소",
                memo="테스트용 데이터"
            )
            test_group.set_admin_password("test123")
            db.session.add(test_group)
            db.session.commit()
            
            # 저장 확인
            saved_group = db.session.query(PartnerGroup).filter_by(name=test_name).first()
            if saved_group:
                print(f"   ✅ 파트너그룹 저장 성공 (ID: {saved_group.id})")
                
                # 테스트 데이터 삭제
                db.session.delete(saved_group)
                db.session.commit()
                print(f"   ✅ 테스트 데이터 삭제 완료")
            else:
                print(f"   ❌ 파트너그룹 저장 실패")
                return False
        except Exception as e:
            print(f"   ❌ 오류: {e}")
            import traceback
            traceback.print_exc()
            return False
        
        # 3. 파일 업로드 디렉토리 확인
        print("\n3. 파일 업로드 디렉토리 확인:")
        try:
            from app import UPLOAD_DIR, DATA_DIR
            print(f"   업로드 디렉토리: {UPLOAD_DIR}")
            print(f"   데이터 디렉토리: {DATA_DIR}")
            
            # 디렉토리 존재 확인
            if os.path.exists(UPLOAD_DIR):
                print(f"   ✅ 업로드 디렉토리 존재")
                files = os.listdir(UPLOAD_DIR)
                print(f"   업로드된 파일 수: {len(files)}개")
            else:
                print(f"   ⚠️  업로드 디렉토리가 없습니다. 생성 중...")
                os.makedirs(UPLOAD_DIR, exist_ok=True)
                print(f"   ✅ 업로드 디렉토리 생성 완료")
            
            if os.path.exists(DATA_DIR):
                print(f"   ✅ 데이터 디렉토리 존재")
                db_file = os.path.join(DATA_DIR, 'busan.db')
                if os.path.exists(db_file):
                    size = os.path.getsize(db_file)
                    print(f"   데이터베이스 파일 크기: {size:,} bytes")
                else:
                    print(f"   ⚠️  데이터베이스 파일이 없습니다")
            else:
                print(f"   ⚠️  데이터 디렉토리가 없습니다")
        except Exception as e:
            print(f"   ❌ 오류: {e}")
            return False
        
        # 4. 트랜잭션 테스트
        print("\n4. 트랜잭션 테스트:")
        try:
            from app import safe_commit
            
            # 트랜잭션 시작
            test_member = Member(
                username=f"test_user_{datetime.now(KST).strftime('%Y%m%d%H%M%S')}",
                company_name="테스트회사",
                business_number="999-99-99999",
                role='member',
                partner_group_id=1
            )
            test_member.set_password("test123")
            db.session.add(test_member)
            
            # 커밋 테스트
            if safe_commit():
                print(f"   ✅ safe_commit 성공")
                
                # 롤백 테스트 (잘못된 데이터로)
                try:
                    invalid_member = Member(
                        username=test_member.username,  # 중복 (에러 발생)
                        company_name="중복테스트",
                        role='member'
                    )
                    db.session.add(invalid_member)
                    safe_commit()
                    print(f"   ⚠️  중복 검증이 작동하지 않습니다")
                except Exception as e:
                    print(f"   ✅ 롤백 작동 확인 (예상된 오류: {type(e).__name__})")
                
                # 테스트 데이터 삭제
                db.session.delete(test_member)
                safe_commit()
                print(f"   ✅ 테스트 데이터 삭제 완료")
            else:
                print(f"   ❌ safe_commit 실패")
                return False
        except Exception as e:
            print(f"   ❌ 오류: {e}")
            import traceback
            traceback.print_exc()
            return False
        
        print("\n" + "=" * 60)
        print("✅ 모든 테스트 통과!")
        print("=" * 60)
        return True

if __name__ == '__main__':
    success = test_database_connection()
    sys.exit(0 if success else 1)

