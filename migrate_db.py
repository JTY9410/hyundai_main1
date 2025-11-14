#!/usr/bin/env python3
"""
데이터베이스 스키마 마이그레이션 스크립트
기존 데이터베이스에 새로운 컬럼들을 추가합니다.
"""

import sqlite3
import os
from datetime import datetime

def migrate_database():
    """데이터베이스 스키마를 마이그레이션합니다."""
    
    # 데이터베이스 파일 경로
    db_path = os.path.join('data', 'busan.db')
    
    if not os.path.exists(db_path):
        print("❌ 데이터베이스 파일이 존재하지 않습니다.")
        return False
    
    # 백업 생성
    backup_path = f"data/busan_backup_{datetime.now().strftime('%Y%m%d_%H%M%S')}.db"
    os.system(f"cp {db_path} {backup_path}")
    print(f"✅ 데이터베이스 백업 생성: {backup_path}")
    
    try:
        conn = sqlite3.connect(db_path)
        cursor = conn.cursor()
        
        print("🔄 데이터베이스 스키마 마이그레이션 시작...")
        
        # 1. PartnerGroup 테이블 생성
        print("📋 PartnerGroup 테이블 생성 중...")
        cursor.execute("""
            CREATE TABLE IF NOT EXISTS partner_group (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                name VARCHAR(255) NOT NULL UNIQUE,
                admin_username VARCHAR(120) NOT NULL UNIQUE,
                admin_password_hash VARCHAR(255) NOT NULL,
                business_number VARCHAR(64) NOT NULL UNIQUE,
                representative VARCHAR(128) NOT NULL,
                phone VARCHAR(64) NOT NULL,
                mobile VARCHAR(64),
                address VARCHAR(255),
                bank_name VARCHAR(128),
                account_number VARCHAR(128),
                registration_cert_path VARCHAR(512),
                logo_path VARCHAR(512),
                memo VARCHAR(255),
                created_at DATETIME DEFAULT CURRENT_TIMESTAMP
            )
        """)
        
        # 2. Member 테이블에 새 컬럼 추가
        print("👥 Member 테이블 업데이트 중...")
        
        # 기존 컬럼 확인
        cursor.execute("PRAGMA table_info(member)")
        existing_columns = [column[1] for column in cursor.fetchall()]
        
        # partner_group_id 컬럼 추가
        if 'partner_group_id' not in existing_columns:
            cursor.execute("ALTER TABLE member ADD COLUMN partner_group_id INTEGER")
            print("  ✅ partner_group_id 컬럼 추가됨")
        
        # role 컬럼 추가
        if 'role' not in existing_columns:
            cursor.execute("ALTER TABLE member ADD COLUMN role VARCHAR(32) DEFAULT 'member'")
            print("  ✅ role 컬럼 추가됨")
        
        # member_type 컬럼 추가
        if 'member_type' not in existing_columns:
            cursor.execute("ALTER TABLE member ADD COLUMN member_type VARCHAR(32) DEFAULT '법인'")
            print("  ✅ member_type 컬럼 추가됨")
        
        # privacy_agreement 컬럼 추가
        if 'privacy_agreement' not in existing_columns:
            cursor.execute("ALTER TABLE member ADD COLUMN privacy_agreement BOOLEAN DEFAULT 0")
            print("  ✅ privacy_agreement 컬럼 추가됨")

        # settlement_method 컬럼 추가
        if 'settlement_method' not in existing_columns:
            cursor.execute("ALTER TABLE member ADD COLUMN settlement_method VARCHAR(16) DEFAULT '포인트'")
            print("  ✅ settlement_method 컬럼 추가됨")
        
        # point_balance 컬럼 추가
        if 'point_balance' not in existing_columns:
            cursor.execute("ALTER TABLE member ADD COLUMN point_balance INTEGER DEFAULT 0")
            print("  ✅ point_balance 컬럼 추가됨")
        
        # 3. InsuranceApplication 테이블에 새 컬럼 추가
        print("📄 InsuranceApplication 테이블 업데이트 중...")
        
        cursor.execute("PRAGMA table_info(insurance_application)")
        existing_columns = [column[1] for column in cursor.fetchall()]
        
        # partner_group_id 컬럼 추가
        if 'partner_group_id' not in existing_columns:
            cursor.execute("ALTER TABLE insurance_application ADD COLUMN partner_group_id INTEGER")
            print("  ✅ partner_group_id 컬럼 추가됨")
        
        # insurance_policy_path 컬럼 추가
        if 'insurance_policy_path' not in existing_columns:
            cursor.execute("ALTER TABLE insurance_application ADD COLUMN insurance_policy_path VARCHAR(512)")
            print("  ✅ insurance_policy_path 컬럼 추가됨")
        
        # insurance_policy_url 컬럼 추가
        if 'insurance_policy_url' not in existing_columns:
            cursor.execute("ALTER TABLE insurance_application ADD COLUMN insurance_policy_url VARCHAR(512)")
            print("  ✅ insurance_policy_url 컬럼 추가됨")
        
        if 'point_deducted' not in existing_columns:
            cursor.execute("ALTER TABLE insurance_application ADD COLUMN point_deducted BOOLEAN DEFAULT 0")
            print("  ✅ point_deducted 컬럼 추가됨")

        # 4. 포인트 관리 관련 테이블 생성
        print("💳 포인트 관리 테이블 생성 중...")
        
        cursor.execute("""
            CREATE TABLE IF NOT EXISTS deposit_history (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                member_id INTEGER NOT NULL,
                partner_group_id INTEGER NOT NULL,
                bank_name VARCHAR(128) NOT NULL,
                account_number VARCHAR(128) NOT NULL,
                deposit_amount INTEGER NOT NULL,
                deposit_date DATETIME DEFAULT CURRENT_TIMESTAMP,
                created_at DATETIME DEFAULT CURRENT_TIMESTAMP,
                FOREIGN KEY (member_id) REFERENCES member(id),
                FOREIGN KEY (partner_group_id) REFERENCES partner_group(id)
            )
        """)
        print("  ✅ deposit_history 테이블 확인/생성 완료")
        
        cursor.execute("""
            CREATE TABLE IF NOT EXISTS deposit_request (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                member_id INTEGER NOT NULL,
                partner_group_id INTEGER NOT NULL,
                amount INTEGER NOT NULL,
                account_holder VARCHAR(128) NOT NULL DEFAULT '',
                bank_name VARCHAR(128) NOT NULL DEFAULT '',
                status VARCHAR(32) DEFAULT 'requested',
                created_at DATETIME DEFAULT CURRENT_TIMESTAMP,
                confirmed_at DATETIME,
                FOREIGN KEY (member_id) REFERENCES member(id),
                FOREIGN KEY (partner_group_id) REFERENCES partner_group(id)
            )
        """)
        print("  ✅ deposit_request 테이블 확인/생성 완료")
        
        # deposit_request 테이블에 account_holder, bank_name 컬럼 추가 (기존 테이블이 있는 경우)
        try:
            cursor.execute("PRAGMA table_info(deposit_request)")
            existing_columns = [row[1] for row in cursor.fetchall()]
            
            if 'account_holder' not in existing_columns:
                cursor.execute("ALTER TABLE deposit_request ADD COLUMN account_holder VARCHAR(128) NOT NULL DEFAULT ''")
                print("  ✅ deposit_request.account_holder 컬럼 추가됨")
            
            if 'bank_name' not in existing_columns:
                cursor.execute("ALTER TABLE deposit_request ADD COLUMN bank_name VARCHAR(128) NOT NULL DEFAULT ''")
                print("  ✅ deposit_request.bank_name 컬럼 추가됨")
        except Exception as e:
            print(f"  ⚠️ deposit_request 컬럼 추가 중 오류 (무시 가능): {e}")
        
        cursor.execute("""
            CREATE TABLE IF NOT EXISTS virtual_account (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                member_id INTEGER NOT NULL,
                partner_group_id INTEGER NOT NULL,
                account_holder VARCHAR(128) NOT NULL,
                bank_name VARCHAR(128) NOT NULL,
                virtual_account_number VARCHAR(128) NOT NULL UNIQUE,
                deposit_amount INTEGER NOT NULL,
                expiry_date DATE NOT NULL,
                status VARCHAR(32) DEFAULT '대기',
                created_at DATETIME DEFAULT CURRENT_TIMESTAMP,
                FOREIGN KEY (member_id) REFERENCES member(id),
                FOREIGN KEY (partner_group_id) REFERENCES partner_group(id)
            )
        """)
        print("  ✅ virtual_account 테이블 확인/생성 완료")

        cursor.execute("""
            CREATE TABLE IF NOT EXISTS point_adjustment (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                member_id INTEGER NOT NULL,
                partner_group_id INTEGER NOT NULL,
                decrease_amount INTEGER DEFAULT 0,
                increase_amount INTEGER DEFAULT 0,
                change_amount INTEGER DEFAULT 0,
                note VARCHAR(255),
                created_at DATETIME DEFAULT CURRENT_TIMESTAMP,
                FOREIGN KEY (member_id) REFERENCES member(id),
                FOREIGN KEY (partner_group_id) REFERENCES partner_group(id)
            )
        """)
        print("  ✅ point_adjustment 테이블 확인/생성 완료")
        
        # 5. 기존 관리자 계정 업데이트
        print("🔐 기존 관리자 계정 업데이트 중...")
        
        # 기존 admin 계정을 hyundai로 변경하고 role 설정
        cursor.execute("""
            UPDATE member 
            SET username = 'hyundai', 
                role = 'admin',
                partner_group_id = NULL,
                company_name = '현대해상30일책임보험전산',
                representative = '전체관리자'
            WHERE username = 'admin' OR role IS NULL
        """)
        
        # 비밀번호 해시 업데이트 (bcrypt로 #admin1004 해시)
        import bcrypt
        password_hash = bcrypt.hashpw('#admin1004'.encode('utf-8'), bcrypt.gensalt()).decode('utf-8')
        cursor.execute("""
            UPDATE member 
            SET password_hash = ?
            WHERE username = 'hyundai' AND role = 'admin'
        """, (password_hash,))
        
        print("  ✅ 관리자 계정 업데이트 완료 (ID: hyundai, PW: #admin1004)")
        
        # 6. 기존 회원들에게 기본 파트너그룹 생성 및 할당
        print("🏢 기본 파트너그룹 생성 중...")
        
        # 기본 파트너그룹 생성
        default_group_password = bcrypt.hashpw('busan1004'.encode('utf-8'), bcrypt.gensalt()).decode('utf-8')
        cursor.execute("""
            INSERT OR IGNORE INTO partner_group 
            (name, admin_username, admin_password_hash, business_number, representative, phone, address)
            VALUES (?, ?, ?, ?, ?, ?, ?)
        """, (
            '부산자동차매매사업자조합',
            'busan_admin',
            default_group_password,
            '123-45-67890',
            '조합장',
            '051-123-4567',
            '부산광역시'
        ))
        
        # 기본 파트너그룹 ID 가져오기
        cursor.execute("SELECT id FROM partner_group WHERE name = '부산자동차매매사업자조합'")
        default_group_id = cursor.fetchone()[0]
        
        # 기존 회원들을 기본 파트너그룹에 할당
        cursor.execute("""
            UPDATE member 
            SET partner_group_id = ?
            WHERE role != 'admin' AND partner_group_id IS NULL
        """, (default_group_id,))
        
        # 기존 보험 신청들을 기본 파트너그룹에 할당
        cursor.execute("""
            UPDATE insurance_application 
            SET partner_group_id = ?
            WHERE partner_group_id IS NULL
        """, (default_group_id,))
        
        print(f"  ✅ 기본 파트너그룹 생성 및 할당 완료 (ID: {default_group_id})")
        
        # 변경사항 커밋
        conn.commit()
        print("✅ 데이터베이스 마이그레이션 완료!")
        
        # 마이그레이션 결과 확인
        cursor.execute("SELECT COUNT(*) FROM partner_group")
        partner_count = cursor.fetchone()[0]
        
        cursor.execute("SELECT COUNT(*) FROM member WHERE role = 'admin'")
        admin_count = cursor.fetchone()[0]
        
        cursor.execute("SELECT COUNT(*) FROM member WHERE role != 'admin'")
        member_count = cursor.fetchone()[0]
        
        print(f"""
📊 마이그레이션 결과:
   - 파트너그룹: {partner_count}개
   - 전체관리자: {admin_count}명
   - 일반회원: {member_count}명
   - 백업파일: {backup_path}
        """)
        
        return True
        
    except Exception as e:
        print(f"❌ 마이그레이션 중 오류 발생: {e}")
        conn.rollback()
        return False
        
    finally:
        conn.close()

if __name__ == "__main__":
    # data 디렉토리 생성
    os.makedirs('data', exist_ok=True)
    
    print("🚀 현대해상30일책임보험전산 데이터베이스 마이그레이션")
    print("=" * 50)
    
    success = migrate_database()
    
    if success:
        print("\n🎉 마이그레이션이 성공적으로 완료되었습니다!")
        print("이제 애플리케이션을 시작할 수 있습니다.")
    else:
        print("\n💥 마이그레이션이 실패했습니다.")
        print("백업 파일을 확인하고 다시 시도해주세요.")
