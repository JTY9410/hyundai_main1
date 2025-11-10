# 데이터베이스 설계 (2025-11-10 업데이트)

본 문서는 현대해상 30일 책임보험 전산 시스템의 핵심 데이터베이스 구조를 정리한 설계도입니다. 최신 포인트 관리 요구사항을 반영하여 테이블과 컬럼 구성을 업데이트했습니다.

## 1. 개요

- **DB 엔진**: SQLite (개발/테스트), PostgreSQL (운영 고려)
- **주요 도메인**: 파트너그룹, 회원사, 책임보험 신청, 포인트 및 입금 관리
- **명명 규칙**: 스네이크 케이스(table_name, column_name)

## 2. 테이블 구조

### 2.1 `partner_group`

| 컬럼 | 타입 | 설명 |
| --- | --- | --- |
| `id` | INTEGER (PK) | 파트너그룹 식별자 |
| `name` | VARCHAR(255) | 파트너그룹명 (UNIQUE) |
| `admin_username` | VARCHAR(120) | 파트너그룹 관리자 아이디 (UNIQUE) |
| `admin_password_hash` | VARCHAR(255) | 관리자 비밀번호 해시 |
| `business_number` | VARCHAR(64) | 사업자등록번호 (UNIQUE) |
| `representative` | VARCHAR(128) | 대표자 |
| `phone` | VARCHAR(64) | 대표 전화번호 |
| `mobile` | VARCHAR(64) | 대표 휴대폰 (선택) |
| `address` | VARCHAR(255) | 주소 |
| `bank_name` | VARCHAR(128) | 정산 은행명 |
| `account_number` | VARCHAR(128) | 정산 계좌번호 |
| `registration_cert_path` | VARCHAR(512) | 사업자등록증 파일 경로 |
| `logo_path` | VARCHAR(512) | 로고 파일 경로 |
| `memo` | VARCHAR(255) | 비고 |
| `created_at` | DATETIME | 생성일시 |

**관계**
- `partner_group` 1 : N `member`
- `partner_group` 1 : N `insurance_application`
- `partner_group` 1 : N `deposit_history`
- `partner_group` 1 : N `virtual_account`
- `partner_group` 1 : N `point_adjustment`

### 2.2 `member`

| 컬럼 | 타입 | 설명 |
| --- | --- | --- |
| `id` | INTEGER (PK) |
| `partner_group_id` | INTEGER (FK) | 소속 파트너그룹, 전체관리자는 `NULL` |
| `username` | VARCHAR(120) | 회원 아이디 (파트너그룹 내 UNIQUE) |
| `password_hash` | VARCHAR(255) | 비밀번호 해시 |
| `company_name` | VARCHAR(255) | 상사명 |
| `address` | VARCHAR(255) | 주소 |
| `business_number` | VARCHAR(64) | 사업자번호 |
| `corporation_number` | VARCHAR(64) | 법인번호 |
| `representative` | VARCHAR(128) | 대표자 |
| `phone` | VARCHAR(64) | 연락처 |
| `mobile` | VARCHAR(64) | 휴대폰 |
| `email` | VARCHAR(255) | 이메일 |
| `registration_cert_path` | VARCHAR(512) | 사업자등록증 파일 경로 |
| `member_type` | VARCHAR(32) | 회원 유형 (법인/개인) |
| `privacy_agreement` | BOOLEAN | 개인정보 동의 여부 |
| `approval_status` | VARCHAR(32) | 신청 / 승인 |
| `role` | VARCHAR(32) | member / admin / partner_admin |
| `memo` | VARCHAR(255) | 비고 |
| `point_balance` | INTEGER | 잔류 포인트 |
| `settlement_method` | VARCHAR(16) | 대금 정산 방식 (포인트 / 후불정산) |
| `created_at` | DATETIME | 가입일시 |

**제약**
- `UNIQUE(username, partner_group_id)`
- `CHECK(role IN ('member', 'admin', 'partner_admin'))`
- `CHECK(approval_status IN ('신청','승인'))`

**관계**
- `member` 1 : N `insurance_application` (작성자)
- `member` 1 : N `deposit_history`
- `member` 1 : N `virtual_account`
- `member` 1 : N `point_adjustment`

### 2.3 `insurance_application`

| 컬럼 | 타입 | 설명 |
| --- | --- | --- |
| `id` | INTEGER (PK) |
| `partner_group_id` | INTEGER (FK) | 소속 파트너그룹 |
| `created_at` | DATETIME | 신청시간 |
| `desired_start_date` | DATE | 가입희망일자 |
| `start_at` | DATETIME | 가입시간 |
| `end_at` | DATETIME | 종료시간 |
| `approved_at` | DATETIME | 조합승인시간 |
| `insured_code` | VARCHAR(64) | 피보험자코드 |
| `contractor_code` | VARCHAR(64) | 계약자코드 |
| `car_plate` | VARCHAR(64) | 차량번호 |
| `vin` | VARCHAR(64) | 차대번호 |
| `car_name` | VARCHAR(128) | 차량명 |
| `car_registered_at` | DATE | 차량등록일자 |
| `premium` | INTEGER | 보험료 (기본 9,500원) |
| `status` | VARCHAR(32) | 신청 / 조합승인 / 가입 / 종료 |
| `memo` | VARCHAR(255) | 비고 |
| `insurance_policy_path` | VARCHAR(512) | 증권 파일 경로 |
| `insurance_policy_url` | VARCHAR(512) | 증권 URL |
| `created_by_member_id` | INTEGER (FK) | 신청한 회원 |
| `point_deducted` | BOOLEAN | 포인트 차감 여부 (신규) |

**포인트 차감 로직**
- `status`가 `가입`으로 전환되면서 `point_deducted`가 `False`일 경우, 신청한 회원(`created_by_member_id`)의 `point_balance`에서 9,500원을 차감하고 `point_deducted=True`로 마킹합니다.

### 2.4 `deposit_history` (신규)

포인트 충전 기록과 최근 입금 현황을 관리하는 테이블입니다.

| 컬럼 | 타입 | 설명 |
| --- | --- | --- |
| `id` | INTEGER (PK) |
| `member_id` | INTEGER (FK) | 입금 회원 |
| `partner_group_id` | INTEGER (FK) | 소속 파트너그룹 |
| `bank_name` | VARCHAR(128) | 입금 은행 |
| `account_number` | VARCHAR(128) | 입금 계좌번호 |
| `deposit_amount` | INTEGER | 입금액 |
| `deposit_date` | DATETIME | 입금일시 |
| `created_at` | DATETIME | 등록일시 |

**비즈니스 로직**
- 입금 저장 시 `member.point_balance`에 입금액을 누적합니다.
- 최근 3건의 입금 정보를 `은행/계좌번호/입금액` 형식으로 UI에 제공합니다.

### 2.5 `virtual_account` (신규)

입금 신청 팝업에서 발급되는 가상계좌 정보를 보관합니다.

| 컬럼 | 타입 | 설명 |
| --- | --- | --- |
| `id` | INTEGER (PK) |
| `member_id` | INTEGER (FK) | 발급 대상 회원 |
| `partner_group_id` | INTEGER (FK) | 소속 파트너그룹 |
| `account_holder` | VARCHAR(128) | 입금주 |
| `bank_name` | VARCHAR(128) | 은행명 |
| `virtual_account_number` | VARCHAR(128) | 가상계좌번호 (UNIQUE) |
| `deposit_amount` | INTEGER | 입금 요청 금액 |
| `expiry_date` | DATE | 사용 가능 종료일 |
| `status` | VARCHAR(32) | 대기 / 입금완료 / 만료 |
| `created_at` | DATETIME | 발급일시 |

**발급 규칙**
- 발급 시 기존 `status='대기'` 계좌는 `만료`로 일괄 업데이트합니다.
- 신규 계좌는 3일 후 자동 만료되도록 `expiry_date`를 설정합니다.
- API 응답으로 가상계좌번호, 종료일, 은행명, 입금액을 반환하여 UI 팝업에 표시합니다.

### 2.6 `point_adjustment` (신규)

포인트 수동 조정 내역을 관리합니다.

| 컬럼 | 타입 | 설명 |
| --- | --- | --- |
| `id` | INTEGER (PK) |
| `member_id` | INTEGER (FK) | 대상 회원 |
| `partner_group_id` | INTEGER (FK) | 소속 파트너그룹 |
| `decrease_amount` | INTEGER | 차감된 포인트 |
| `increase_amount` | INTEGER | 증가된 포인트 |
| `change_amount` | INTEGER | 순 증감 (증가 - 차감) |
| `note` | VARCHAR(255) | 변경 내역 (UI에 표시) |
| `created_at` | DATETIME | 조정일시 |

**비즈니스 로직**
- 관리자 페이지의 포인트 수정 팝업에서 차감/증가를 입력하면 `member.point_balance`를 갱신하고 본 테이블에 기록합니다.
- `note` 필드에는 `포인트차감 10,000원 / 포인트증가 5,000원 / 메모`와 같이 사용자에게 보여줄 변경 요약을 저장합니다.

## 3. 인덱스 및 성능 고려

| 테이블 | 인덱스 |
| --- | --- |
| `member` | `idx_member_created_at`, `idx_member_partner_group`, `idx_member_username_partner`, `idx_member_business_number` |
| `insurance_application` | `idx_ins_app_partner_group`, `idx_ins_app_created_by`, `idx_ins_app_desired`, `idx_ins_app_created`, `idx_ins_app_approved`, `idx_ins_app_status`, `idx_ins_app_start`, `idx_ins_app_car_plate`, `idx_ins_app_vin` |
| `deposit_history` | 향후 조회 패턴에 따라 `member_id`, `deposit_date` 인덱스 추가 고려 |
| `virtual_account` | `virtual_account_number` UNIQUE 인덱스 |
| `point_adjustment` | `member_id`, `created_at` 인덱스 추가 검토 |

## 4. 트랜잭션 및 데이터 정합성

- 모든 쓰기 작업은 `safe_commit()`을 통해 예외 발생 시 자동 롤백됩니다.
- 포인트 차감과 입금 기록은 동일 세션에서 처리되어 정합성을 유지합니다.
- 책임보험 신청 → 가입 상태 전환 시 중복 차감을 방지하기 위해 `point_deducted` 플래그를 사용합니다.

## 5. 향후 확장 고려사항

- 가상계좌 상태 자동 만료 배치 작업 (일일 스케줄러)
- 입금 확정 시 `deposit_history` → `virtual_account` 연동 및 상태 변경
- 포인트 사용 및 환불 기록을 위한 별도 트랜잭션 로그 테이블 도입
- PostgreSQL 환경에서의 마이그레이션 스크립트 고도화 (`ALTER TABLE` → `alembic` 전환 고려)

---

본 설계도는 `migrate_db.py` 및 `app.py`에 반영된 실제 스키마를 기반으로 작성되었습니다. 요구사항 변경 시 본 문서를 최신 상태로 유지해 주세요.

