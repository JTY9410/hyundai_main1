import os
import shutil
from datetime import datetime, timedelta
try:
    from dateutil.tz import tzlocal, gettz
except ImportError:
    # Fallback for different dateutil versions
    try:
        from dateutil import tz
        tzlocal = tz.tzlocal
        gettz = tz.gettz
    except ImportError:
        # Ultimate fallback: use UTC
        from datetime import timezone
        tzlocal = lambda: timezone.utc
        gettz = lambda name: timezone.utc if name else timezone.utc

from flask import Flask, render_template, request, redirect, url_for, flash, send_file, session, jsonify, abort
from werkzeug.exceptions import HTTPException
from werkzeug.utils import secure_filename
from flask_sqlalchemy import SQLAlchemy
from sqlalchemy import Index, CheckConstraint, event
from sqlalchemy.pool import NullPool
from sqlalchemy.engine import Engine
from sqlalchemy.exc import SQLAlchemyError, OperationalError, IntegrityError
from flask_login import LoginManager, UserMixin, login_user, logout_user, login_required, current_user
from werkzeug.security import generate_password_hash, check_password_hash
from io import BytesIO
import functools
# Defer pandas import to avoid heavy loading at module import time


BASE_DIR = os.path.abspath(os.path.dirname(__file__))

# 한국 시간대 설정
try:
    KST = gettz('Asia/Seoul')
    if KST is None:
        # Fallback to UTC if timezone not available
        from datetime import timezone
        KST = timezone.utc
except Exception:
    # Fallback to UTC if timezone setup fails
    from datetime import timezone
    KST = timezone.utc

# Robust detection for serverless / read-only FS
def _is_read_only_fs() -> bool:
    """Safely detect if filesystem is read-only (serverless environment)"""
    try:
        # Use /tmp which is writable in most serverless environments
        test_dir = "/tmp/__wtest__"
        os.makedirs(test_dir, exist_ok=True)
        test_file = os.path.join(test_dir, "t")
        try:
            with open(test_file, "wb") as f:
                f.write(b"x")   # 대부분의 서버리스에서 여기서 OSError: [Errno 30]
            os.remove(test_file)
            return False
        except (OSError, IOError, PermissionError):
            return True
        finally:
            # Cleanup
            try:
                if os.path.exists(test_file):
                    os.remove(test_file)
            except Exception:
                pass
    except Exception:
        # If we can't determine, assume serverless if VERCEL env var is set
        return bool(os.environ.get('VERCEL') or os.environ.get('VERCEL_ENV'))

# Check environment variables first (fastest and most reliable)
is_serverless = bool(
    os.environ.get('VERCEL') or
    os.environ.get('VERCEL_ENV') or
    os.environ.get('AWS_LAMBDA_FUNCTION_NAME') or
    os.environ.get('LAMBDA_TASK_ROOT') or
    os.environ.get('K_SERVICE')
)

# Only check filesystem if env vars didn't indicate serverless
# This avoids potential import-time errors
if not is_serverless:
    try:
        is_serverless = _is_read_only_fs()
    except Exception:
        # If filesystem check fails, default to non-serverless (safer for local dev)
        is_serverless = False

if is_serverless:
    INSTANCE_DIR = os.environ.get('INSTANCE_PATH', '/tmp/instance')
    DATA_DIR = os.environ.get('DATA_DIR', '/tmp/data')
    UPLOAD_DIR = os.environ.get('UPLOAD_DIR', '/tmp/uploads')
else:
    INSTANCE_DIR = os.path.join(BASE_DIR, 'instance')
    DATA_DIR = os.path.join(BASE_DIR, 'data')
    UPLOAD_DIR = os.path.join(BASE_DIR, 'uploads')


DB_PATH = os.path.join(DATA_DIR, 'busan.db')
STATIC_DIR = os.path.join(BASE_DIR, 'static')
TEMPLATE_DIR = os.path.join(BASE_DIR, 'templates')

# Ensure static directory exists (skip in serverless/read-only environments)
# In Vercel, /var/task is read-only, so we cannot create directories there
# Static files should be served from the project directory directly
if not is_serverless:
    # Only try to create static directory in non-serverless environments
    try:
        if not os.path.exists(STATIC_DIR):
            os.makedirs(STATIC_DIR, exist_ok=True)
    except (OSError, PermissionError):
        # If we can't create it, that's okay - static files may already exist
        pass

LOGO_SRC_FILENAME = 'logo.png'
# 컨테이너 내부에서 접근 가능한 경로로 변경
LOGO_SOURCE_PATH_IN_CONTAINER = os.path.join(BASE_DIR, LOGO_SRC_FILENAME)

# Ensure directories exist
if is_serverless:
    os.makedirs(INSTANCE_DIR, exist_ok=True)  # /tmp/instance
    os.makedirs(DATA_DIR, exist_ok=True)      # /tmp/data
    os.makedirs(UPLOAD_DIR, exist_ok=True)    # /tmp/uploads
else:
    os.makedirs(INSTANCE_DIR, exist_ok=True)  # 로컬에서도 만들어두는 편이 안전
    os.makedirs(DATA_DIR, exist_ok=True)
    os.makedirs(UPLOAD_DIR, exist_ok=True)
    # STATIC_DIR is already created above (if not serverless)
    # Only create if not already created
    try:
        if not os.path.exists(STATIC_DIR):
            os.makedirs(STATIC_DIR, exist_ok=True)
    except (OSError, PermissionError):
        pass



def create_app():
    # Use instance_path for Vercel compatibility
    instance_path = INSTANCE_DIR if is_serverless else None
    
    # In serverless (Vercel), static files are served directly by Vercel
    # We should not set static_folder to a read-only path
    # Flask will use the default 'static' folder relative to the app root
    if is_serverless:
        # Check if static directory exists (read-only check)
        static_folder = STATIC_DIR if os.path.exists(STATIC_DIR) else None
    else:
        static_folder = STATIC_DIR
    
    app = Flask(__name__, 
                template_folder=TEMPLATE_DIR, 
                static_folder=static_folder,
                instance_path=instance_path)
    app.secret_key = os.environ.get('SECRET_KEY', 'dev-secret-key')
    
    # 세션 쿠키 설정 (Docker 환경에서 세션 유지)
    app.config['SESSION_COOKIE_NAME'] = 'hyundai_session'
    app.config['SESSION_COOKIE_HTTPONLY'] = True
    app.config['SESSION_COOKIE_SAMESITE'] = None  # None으로 설정하여 크로스 사이트 요청에서도 쿠키 전달
    app.config['SESSION_COOKIE_SECURE'] = False  # HTTP 환경이므로 False (HTTPS면 True)
    app.config['PERMANENT_SESSION_LIFETIME'] = timedelta(hours=24)  # 24시간 세션 유지
    app.config['SESSION_COOKIE_PATH'] = '/'  # 모든 경로에서 쿠키 사용
    
    # Disable template caching in development
    if not is_serverless:
        app.config['TEMPLATES_AUTO_RELOAD'] = True
        app.jinja_env.auto_reload = True
        app.jinja_env.cache = {}
    
    # Database configuration with Vercel support
    if is_serverless:
        # Check for external database first
        database_url = os.environ.get('DATABASE_URL')
        if database_url:
            # External database (PostgreSQL, MySQL, etc.)
            app.config['SQLALCHEMY_DATABASE_URI'] = database_url
            app.config['SQLALCHEMY_ENGINE_OPTIONS'] = {
                'poolclass': NullPool,  # Serverless-friendly
                'pool_pre_ping': True,
                'connect_args': {
                    'connect_timeout': 10,
                }
            }
            # PostgreSQL-specific: enable autocommit mode to avoid transaction issues
            if 'postgresql' in database_url or 'postgres' in database_url:
                try:
                    # Register event listener to handle transaction errors
                    @event.listens_for(Engine, "connect")
                    def set_postgres_pragmas(dbapi_connection, connection_record):
                        """PostgreSQL connection setup"""
                        try:
                            if hasattr(dbapi_connection, 'cursor'):
                                cursor = dbapi_connection.cursor()
                                # Don't set autocommit here - let SQLAlchemy manage transactions
                                # But ensure connection is clean
                                cursor.close()
                        except Exception:
                            pass
                except Exception as e:
                    try:
                        import sys
                        sys.stderr.write(f"Warning: Failed to register PostgreSQL event: {e}\n")
                    except Exception:
                        pass
        else:
            # Fallback to /tmp SQLite for Vercel
            tmp_db_path = os.path.join(DATA_DIR, 'busan.db')
            app.config['SQLALCHEMY_DATABASE_URI'] = f'sqlite:///{tmp_db_path}'
            
            # SQLite pragma를 위한 커스텀 커넥션 팩토리
            def get_sqlite_connect_args():
                return {
                    'check_same_thread': False,
                    'timeout': 20,
                }
            
            app.config['SQLALCHEMY_ENGINE_OPTIONS'] = {
                'poolclass': NullPool,  # Serverless-friendly
                'connect_args': get_sqlite_connect_args(),
            }
            # Log a clear warning about ephemeral storage on serverless
            try:
                import sys
                sys.stderr.write(
                    "WARNING: Using ephemeral SQLite on serverless (/tmp). "
                    "Data will be lost on cold start. Set DATABASE_URL to a persistent DB.\n"
                )
                sys.stderr.write(f"DB_FILE: {tmp_db_path}\n")
            except Exception:
                pass
    else:
        # Local development
        app.config['SQLALCHEMY_DATABASE_URI'] = f'sqlite:///{DB_PATH}'
    
    app.config['SQLALCHEMY_TRACK_MODIFICATIONS'] = False
    return app


# SQLite foreign keys 설정 함수 (app 생성 전에 정의)
_sqlite_pragma_registered = False

def register_sqlite_pragma():
    """Register SQLite pragma event listener (called once, in app context)"""
    global _sqlite_pragma_registered
    if not _sqlite_pragma_registered:
        try:
            # Register on Engine class level (doesn't require app context)
            @event.listens_for(Engine, "connect")
            def set_sqlite_pragma(dbapi_connection, connection_record):
                """SQLite 연결 시 foreign keys 활성화"""
                try:
                    if hasattr(dbapi_connection, 'cursor'):
                        cursor = dbapi_connection.cursor()
                        cursor.execute("PRAGMA foreign_keys=ON")
                        cursor.close()
                except Exception:
                    pass
            _sqlite_pragma_registered = True
        except Exception as e:
            print(f"Warning: Failed to register SQLite pragma: {e}")
            pass

# Create app and extensions using standard Flask pattern
try:
    app = create_app()
    db = SQLAlchemy()
    login_manager = LoginManager()
    
    # Initialize extensions with app - CRITICAL for serverless
    db.init_app(app)
    login_manager.init_app(app)
    login_manager.login_view = 'login'
    
    # Verify extensions are attached
    if not hasattr(app, 'extensions') or 'sqlalchemy' not in app.extensions:
        try:
            import sys
            sys.stderr.write("WARNING: SQLAlchemy not properly attached to app\n")
            # Force re-init
            db.init_app(app)
        except Exception as e:
            try:
                import sys
                sys.stderr.write(f"WARNING: Failed to re-init db: {e}\n")
            except Exception:
                pass
    
    if not hasattr(app, 'login_manager'):
        try:
            import sys
            sys.stderr.write("WARNING: LoginManager not properly attached to app\n")
            # Force re-init
            login_manager.init_app(app)
            login_manager.login_view = 'login'
        except Exception as e:
            try:
                import sys
                sys.stderr.write(f"WARNING: Failed to re-init login_manager: {e}\n")
            except Exception:
                pass
    
    # Ensure tzlocal is available in Jinja templates
    try:
        app.jinja_env.globals['tzlocal'] = tzlocal
    except Exception:
        pass
    # Register SQLite pragma after app is created
    register_sqlite_pragma()
    try:
        import sys
        sys.stderr.write("✓ Flask app created successfully\n")
    except Exception:
        print("✓ Flask app created successfully")
except Exception as e:
    import traceback
    error_msg = f"CRITICAL: App creation failed: {e}\n{traceback.format_exc()}"
    try:
        import sys
        sys.stderr.write(f"VERCEL_ERROR: {error_msg}\n")
    except Exception:
        print(error_msg)
    # Create minimal error app instead of crashing
    # But still try to create db and login_manager for Vercel compatibility
    try:
        app = Flask(__name__)
        db = SQLAlchemy()
        login_manager = LoginManager()
        # Try to initialize if possible
        try:
            db.init_app(app)
            login_manager.init_app(app)
            login_manager.login_view = 'login'
        except Exception:
            pass
    except Exception:
        app = Flask(__name__)
        db = None
        login_manager = None

# Add custom Jinja filter for datetime formatting (only if app exists)
if app is not None:
    @app.template_filter('to_local_datetime')
    def to_local_datetime(dt):
        """Convert datetime to local timezone and format for datetime-local input"""
        if not dt:
            return ''
        try:
            local_dt = dt.astimezone(KST)
            return local_dt.strftime('%Y-%m-%dT%H:%M')
        except Exception:
            return ''

    # Add another filter for safe datetime display
    @app.template_filter('safe_datetime')
    def safe_datetime(dt):
        """Safely format datetime for display"""
        if not dt:
            return ''
        try:
            if hasattr(dt, 'strftime'):
                return dt.strftime('%Y-%m-%d %H:%M')
            return str(dt)
        except Exception:
            return ''


def _ensure_aware(dt):
    if not dt:
        return None
    try:
        # tz-naive if no tzinfo or utcoffset is None
        if dt.tzinfo is None or dt.tzinfo.utcoffset(dt) is None:
            return dt.replace(tzinfo=KST)
        return dt
    except Exception:
        return dt


def ensure_logo():
    """로고 파일이 없으면 원본에서 복사 - 안전하게 처리"""
    try:
        os.makedirs(STATIC_DIR, exist_ok=True)
    except (OSError, PermissionError) as e:
        # Cannot create static directory - skip logo setup
        try:
            import sys
            sys.stderr.write(f"Warning: Cannot create static directory: {e}\n")
        except Exception:
            pass
        return
    
    dst = os.path.join(STATIC_DIR, 'logo.png')
    
    # 이미 존재하고 크기가 0보다 크면 완료
    try:
        if os.path.exists(dst):
            file_size = os.path.getsize(dst)
            if file_size > 0:
                return
    except (OSError, IOError, PermissionError):
        # Cannot check existing file - will try to copy
        pass
    except Exception:
        # Other errors - skip
        return
    
    # 원본 로고 파일 경로들 시도
    original_logo_paths = [
        LOGO_SOURCE_PATH_IN_CONTAINER,  # 컨테이너 내부: /app/logo.png (repo 동봉)
        os.path.join(BASE_DIR, 'logo.png'),  # 프로젝트 루트의 logo.png
        '/Users/USER/dev/busan/logo.png',  # 호스트 절대 경로 (개발 환경)
    ]
    
    for src in original_logo_paths:
        try:
            # Check if source exists and is readable
            if not os.path.exists(src):
                continue
                
            try:
                file_size = os.path.getsize(src)
                if file_size <= 0:
                    continue
            except (OSError, IOError, PermissionError):
                # Cannot read source file - skip
                continue
            
            # Try to copy
            if is_serverless:
                # Vercel 환경에서는 복사 시도 (실패해도 계속)
                try:
                    shutil.copy(src, dst)
                    try:
                        import sys
                        sys.stderr.write(f"✓ Logo copied from {src} to {dst}\n")
                    except Exception:
                        pass
                    return
                except (OSError, IOError, PermissionError) as e:
                    # Cannot write to static in serverless - this is expected
                    try:
                        import sys
                        sys.stderr.write(f"Info: Cannot copy logo in serverless (expected): {e}\n")
                    except Exception:
                        pass
                    # Continue - will serve from source path directly
                    continue
                except Exception as e:
                    # Other errors
                    try:
                        import sys
                        sys.stderr.write(f"Warning: Error copying logo: {e}\n")
                    except Exception:
                        pass
                    continue
            else:
                # 로컬 환경: 정상 복사
                try:
                    shutil.copy(src, dst)
                    return
                except (OSError, IOError, PermissionError) as e:
                    try:
                        import sys
                        sys.stderr.write(f"Warning: Could not copy logo: {e}\n")
                    except Exception:
                        pass
                    continue
        except Exception as e:
            # Any other error - log and continue to next path
            try:
                import sys
                sys.stderr.write(f"Warning: Error processing logo path {src}: {e}\n")
            except Exception:
                pass
            continue
    
    # 로고 파일이 없으면 빈 파일 생성 시도 (나중에 업로드 가능)
    if not is_serverless:
        try:
            try:
                with open(dst, 'wb') as f:
                    f.write(b'')
            except (OSError, IOError, PermissionError):
                # Cannot create empty file - skip
                pass
        except Exception:
            # Any other error - skip
            pass


# Models need db to be available - but handle gracefully for Vercel
# Always define models - they will be properly initialized when db is available
# For Vercel: models are defined conditionally but Member/InsuranceApplication classes always exist
_model_classes_defined = False

def define_models():
    """Define SQLAlchemy models - called once when db is available"""
    global PartnerGroup, Member, InsuranceApplication, _model_classes_defined
    
    if _model_classes_defined or db is None:
        return
    
    try:
        ModelBase = db.Model
        
        # 파트너그룹 모델 (전체관리자가 생성/관리)
        class PartnerGroup(ModelBase):
            __tablename__ = 'partner_group'
            
            id = db.Column(db.Integer, primary_key=True)
            name = db.Column(db.String(255), nullable=False, unique=True)  # 파트너그룹명
            admin_username = db.Column(db.String(120), nullable=False, unique=True)  # 파트너그룹 관리자 아이디
            admin_password_hash = db.Column(db.String(255), nullable=False)  # 파트너그룹 관리자 패스워드
            business_number = db.Column(db.String(64), nullable=False, unique=True)  # 사업자등록번호
            representative = db.Column(db.String(128), nullable=False)  # 대표자
            phone = db.Column(db.String(64), nullable=False)  # 유선번호
            mobile = db.Column(db.String(64))  # 휴대폰번호
            address = db.Column(db.String(255))  # 주소
            bank_name = db.Column(db.String(128))  # 은행
            account_number = db.Column(db.String(128))  # 계좌번호
            registration_cert_path = db.Column(db.String(512))  # 사업자등록증 첨부
            logo_path = db.Column(db.String(512))  # 로고 첨부
            memo = db.Column(db.String(255))  # 비고
            created_at = db.Column(db.DateTime, default=lambda: datetime.now(KST))
            
            __table_args__ = (
                Index('idx_partner_group_created_at', 'created_at'),
                Index('idx_partner_group_name', 'name'),
            )

            def set_admin_password(self, password: str) -> None:
                self.admin_password_hash = generate_password_hash(password)

            def check_admin_password(self, password: str) -> bool:
                return check_password_hash(self.admin_password_hash, password)
        
        # 회원사 모델 (파트너그룹에 소속)
        class Member(UserMixin, ModelBase):
            __tablename__ = 'member'
            
            id = db.Column(db.Integer, primary_key=True)
            partner_group_id = db.Column(db.Integer, db.ForeignKey('partner_group.id'), nullable=True)  # 파트너그룹 소속 (전체관리자는 None)
            username = db.Column(db.String(120), nullable=False)  # 아이디 (파트너그룹 내에서 유니크)
            password_hash = db.Column(db.String(255), nullable=False)
            company_name = db.Column(db.String(255))  # 상사명
            address = db.Column(db.String(255))  # 주소
            business_number = db.Column(db.String(64))  # 사업자번호
            corporation_number = db.Column(db.String(64))  # 법인번호
            representative = db.Column(db.String(128))  # 대표자
            phone = db.Column(db.String(64))  # 연락처
            mobile = db.Column(db.String(64))  # 휴대폰
            email = db.Column(db.String(255))  # 이메일
            registration_cert_path = db.Column(db.String(512))  # 사업자등록증 첨부
            member_type = db.Column(db.String(32), default='법인')  # 법인, 개인
            privacy_agreement = db.Column(db.Boolean, default=False)  # 개인정보이용동의 (개인인 경우)
            approval_status = db.Column(db.String(32), default='신청')  # 신청, 승인
            role = db.Column(db.String(32), default='member')  # member, admin, partner_admin
            memo = db.Column(db.String(255))  # 비고
            created_at = db.Column(db.DateTime, default=lambda: datetime.now(KST))
            
            __table_args__ = (
                Index('idx_member_created_at', 'created_at'),
                Index('idx_member_partner_group', 'partner_group_id'),
                Index('idx_member_username_partner', 'username', 'partner_group_id'),
                Index('idx_member_business_number', 'business_number'),
                CheckConstraint("approval_status IN ('신청','승인')", name='ck_member_approval_status'),
                CheckConstraint("role IN ('member','admin','partner_admin')", name='ck_member_role'),
                CheckConstraint("member_type IN ('법인','개인')", name='ck_member_type'),
                # 파트너그룹 내에서 username 유니크
                db.UniqueConstraint('username', 'partner_group_id', name='uq_member_username_partner'),
            )

            # 관계 설정
            partner_group = db.relationship('PartnerGroup', backref='members')

            def set_password(self, password: str) -> None:
                self.password_hash = generate_password_hash(password)

            def check_password(self, password: str) -> bool:
                return check_password_hash(self.password_hash, password)

        # 보험신청 모델 (회원사가 신청)
        class InsuranceApplication(ModelBase):
            __tablename__ = 'insurance_application'
            
            id = db.Column(db.Integer, primary_key=True)
            partner_group_id = db.Column(db.Integer, db.ForeignKey('partner_group.id'), nullable=False)  # 파트너그룹
            created_at = db.Column(db.DateTime, default=lambda: datetime.now(KST))  # 신청시간
            desired_start_date = db.Column(db.Date, nullable=False)  # 가입희망일자
            start_at = db.Column(db.DateTime(timezone=True))  # 가입시간
            end_at = db.Column(db.DateTime(timezone=True))  # 종료시간
            approved_at = db.Column(db.DateTime(timezone=True))  # 조합승인시간
            insured_code = db.Column(db.String(64))  # 피보험자코드 = 사업자번호
            contractor_code = db.Column(db.String(64), default='부산자동차매매사업자조합')  # 계약자코드
            car_plate = db.Column(db.String(64))  # 한글차량번호
            vin = db.Column(db.String(64))  # 차대번호
            car_name = db.Column(db.String(128))  # 차량명
            car_registered_at = db.Column(db.Date)  # 차량등록일자
            premium = db.Column(db.Integer, default=9500)  # 보험료 9500 고정
            status = db.Column(db.String(32), default='신청')  # 신청, 조합승인, 가입, 종료
            memo = db.Column(db.String(255))  # 비고
            insurance_policy_path = db.Column(db.String(512))  # 보험증권 파일 경로
            insurance_policy_url = db.Column(db.String(512))  # 보험증권 URL
            created_by_member_id = db.Column(db.Integer, db.ForeignKey('member.id'))

            __table_args__ = (
                Index('idx_ins_app_partner_group', 'partner_group_id'),
                Index('idx_ins_app_created_by', 'created_by_member_id'),
                Index('idx_ins_app_desired', 'desired_start_date'),
                Index('idx_ins_app_created', 'created_at'),
                Index('idx_ins_app_approved', 'approved_at'),
                Index('idx_ins_app_status', 'status'),
                Index('idx_ins_app_start', 'start_at'),
                Index('idx_ins_app_car_plate', 'car_plate'),
                Index('idx_ins_app_vin', 'vin'),
                CheckConstraint("status IN ('신청','조합승인','가입','종료')", name='ck_ins_app_status'),
            )

            # 관계 설정
            partner_group = db.relationship('PartnerGroup', backref='insurance_applications')
            created_by_member = db.relationship('Member', backref='applications')

            def recompute_status(self) -> None:
                now = datetime.now(KST)
                approved_at_local = _ensure_aware(self.approved_at)
                end_at_local = _ensure_aware(self.end_at)
                # After approval + 2 hours -> 가입
                if self.status in ('신청', '조합승인'):
                    if approved_at_local and now >= approved_at_local + timedelta(hours=2):
                        self.status = '가입'
                        if not self.start_at:
                            # 가입일/종료일 설정: 가입희망일자 기준으로 세팅, 종료는 30일 후
                            start_date = datetime.combine(self.desired_start_date, datetime.min.time(), tzinfo=KST)
                            self.start_at = start_date
                            self.end_at = start_date + timedelta(days=30)
                # 종료일 경과 -> 종료
                if end_at_local and now >= end_at_local:
                    self.status = '종료'
        
        _model_classes_defined = True
        print("✓ Models defined successfully")
    except Exception as e:
        print(f"✗ Model definition failed: {e}")
        import traceback
        traceback.print_exc()

# Initialize models if db is available at module import time
if db is not None:
    define_models()
else:
    # Create minimal stub classes for import compatibility
    class PartnerGroup:
        id = None
        name = None
        def set_admin_password(self, password: str) -> None:
            pass
        def check_admin_password(self, password: str) -> bool:
            return False

    class Member(UserMixin):
        id = None
        username = None
        approval_status = None
        role = None
        business_number = None
        partner_group_id = None
        def set_password(self, password: str) -> None:
            pass
        def check_password(self, password: str) -> bool:
            return False

    class InsuranceApplication:
        id = None
        status = None
        partner_group_id = None
        def recompute_status(self) -> None:
            pass


# User loader - register conditionally
def load_user(user_id):
    try:
        if db is None or login_manager is None:
            return None
        # Ensure models are defined
        if not _model_classes_defined:
            try:
                define_models()
            except Exception:
                pass
        if _model_classes_defined:
            # Use get() which is more efficient and handles detached sessions better
            try:
                user = db.session.get(Member, int(user_id))
                # Ensure the user object is properly bound to the session
                if user is not None:
                    # Refresh to ensure attributes are accessible
                    try:
                        db.session.refresh(user)
                    except Exception:
                        # If refresh fails, merge to attach to current session
                        try:
                            db.session.merge(user)
                        except Exception:
                            pass
                return user
            except Exception as e:
                # Log error but don't crash
                try:
                    import sys
                    sys.stderr.write(f"Error loading user {user_id}: {e}\n")
                except Exception:
                    pass
                return None
        return None
    except Exception:
        return None

# Register user loader only if login_manager exists
if login_manager is not None:
    login_manager.user_loader(load_user)


def init_db_and_assets():
    """데이터베이스 및 리소스 초기화 (app context 내에서 호출해야 함)"""
    from flask import current_app
    
    if db is None:
        print("Warning: db is None, skipping initialization")
        return
    
    # Ensure models are defined before creating tables
    try:
        define_models()
    except Exception as e:
        print(f"Warning: Model definition failed: {e}")
        import traceback
        traceback.print_exc()
    
    try:
        # Vercel에서도 in-memory SQLite를 사용하므로 테이블 생성은 항상 수행
        db.create_all()
    except Exception as e:
        print(f"Warning: Database creation failed: {e}")
        import traceback
        traceback.print_exc()
        # Continue anyway - tables might already exist
    
    try:
        ensure_logo()
    except Exception as e:
        # Logo setup failure should not crash the app
        try:
            import sys
            sys.stderr.write(f"Warning: Logo setup failed: {e}\n")
            import traceback
            sys.stderr.write(traceback.format_exc())
        except Exception:
            print(f"Warning: Logo setup failed: {e}")
        # Continue - logo is not critical for app functionality
        pass

    # 스키마 보정: 컬럼이 없으면 추가
    try:
        from sqlalchemy import text, inspect
        db_uri = current_app.config.get('SQLALCHEMY_DATABASE_URI', '')
        
        if 'sqlite' in db_uri:
            # Member 테이블: role 컬럼 추가 (SQLite)
            res = db.session.execute(text("PRAGMA table_info(member)"))
            cols = [r[1] for r in res.fetchall()]
            if 'role' not in cols:
                if not is_serverless:  # Vercel 환경이 아니면 스키마 변경
                    db.session.execute(text("ALTER TABLE member ADD COLUMN role TEXT NOT NULL DEFAULT 'member'"))
                    safe_commit()  # Schema migration - don't show error if it fails
            
            # Member 테이블: member_type 컬럼 추가 (SQLite)
            if 'member_type' not in cols:
                if not is_serverless:
                    try:
                        db.session.execute(text("ALTER TABLE member ADD COLUMN member_type VARCHAR(32) DEFAULT '법인'"))
                        safe_commit()
                        print("Added member_type column to member table")
                    except Exception as e:
                        print(f"Warning: Failed to add member_type: {e}")
            
            # Member 테이블: privacy_agreement 컬럼 추가 (SQLite)
            if 'privacy_agreement' not in cols:
                if not is_serverless:
                    try:
                        db.session.execute(text("ALTER TABLE member ADD COLUMN privacy_agreement BOOLEAN DEFAULT 0"))
                        safe_commit()
                        print("Added privacy_agreement column to member table")
                    except Exception as e:
                        print(f"Warning: Failed to add privacy_agreement: {e}")
            
            # Member 테이블: memo 컬럼 추가 (SQLite)
            if 'memo' not in cols:
                if not is_serverless:
                    try:
                        db.session.execute(text("ALTER TABLE member ADD COLUMN memo VARCHAR(255)"))
                        safe_commit()
                        print("Added memo column to member table")
                    except Exception as e:
                        print(f"Warning: Failed to add memo: {e}")
            
            # InsuranceApplication 테이블: 보험증권 필드 추가 (SQLite)
            res = db.session.execute(text("PRAGMA table_info(insurance_application)"))
            cols = [r[1] for r in res.fetchall()]
            
            if 'insurance_policy_path' not in cols:
                if not is_serverless:
                    try:
                        db.session.execute(text("ALTER TABLE insurance_application ADD COLUMN insurance_policy_path TEXT"))
                        safe_commit()
                        print("Added insurance_policy_path column to insurance_application table")
                    except Exception as e:
                        print(f"Warning: Failed to add insurance_policy_path: {e}")
            
            if 'insurance_policy_url' not in cols:
                if not is_serverless:
                    try:
                        db.session.execute(text("ALTER TABLE insurance_application ADD COLUMN insurance_policy_url TEXT"))
                        safe_commit()
                        print("Added insurance_policy_url column to insurance_application table")
                    except Exception as e:
                        print(f"Warning: Failed to add insurance_policy_url: {e}")
        elif 'postgresql' in db_uri or 'postgres' in db_uri:
            # PostgreSQL: 컬럼 존재 여부 확인 후 추가
            inspector = inspect(db.engine)
            
            # Member 테이블: role 컬럼 추가 (PostgreSQL)
            member_cols = [col['name'] for col in inspector.get_columns('member')]
            if 'role' not in member_cols:
                if not is_serverless:
                    try:
                        db.session.execute(text("ALTER TABLE member ADD COLUMN role VARCHAR(32) NOT NULL DEFAULT 'member'"))
                        safe_commit()
                        print("Added role column to member table (PostgreSQL)")
                    except Exception as e:
                        print(f"Warning: Failed to add role to member: {e}")
            
            # Member 테이블: member_type 컬럼 추가 (PostgreSQL)
            if 'member_type' not in member_cols:
                if not is_serverless:
                    try:
                        db.session.execute(text("ALTER TABLE member ADD COLUMN member_type VARCHAR(32) DEFAULT '법인'"))
                        safe_commit()
                        print("Added member_type column to member table (PostgreSQL)")
                    except Exception as e:
                        print(f"Warning: Failed to add member_type: {e}")
            
            # Member 테이블: privacy_agreement 컬럼 추가 (PostgreSQL)
            if 'privacy_agreement' not in member_cols:
                if not is_serverless:
                    try:
                        db.session.execute(text("ALTER TABLE member ADD COLUMN privacy_agreement BOOLEAN DEFAULT FALSE"))
                        safe_commit()
                        print("Added privacy_agreement column to member table (PostgreSQL)")
                    except Exception as e:
                        print(f"Warning: Failed to add privacy_agreement: {e}")
            
            # Member 테이블: memo 컬럼 추가 (PostgreSQL)
            if 'memo' not in member_cols:
                if not is_serverless:
                    try:
                        db.session.execute(text("ALTER TABLE member ADD COLUMN memo VARCHAR(255)"))
                        safe_commit()
                        print("Added memo column to member table (PostgreSQL)")
                    except Exception as e:
                        print(f"Warning: Failed to add memo: {e}")
            
            # InsuranceApplication 테이블: 보험증권 필드 추가 (PostgreSQL)
            ins_app_cols = [col['name'] for col in inspector.get_columns('insurance_application')]
            
            if 'insurance_policy_path' not in ins_app_cols:
                if not is_serverless:
                    try:
                        db.session.execute(text("ALTER TABLE insurance_application ADD COLUMN insurance_policy_path VARCHAR(512)"))
                        safe_commit()
                        print("Added insurance_policy_path column to insurance_application table (PostgreSQL)")
                    except Exception as e:
                        print(f"Warning: Failed to add insurance_policy_path: {e}")
            
            if 'insurance_policy_url' not in ins_app_cols:
                if not is_serverless:
                    try:
                        db.session.execute(text("ALTER TABLE insurance_application ADD COLUMN insurance_policy_url VARCHAR(512)"))
                        safe_commit()
                        print("Added insurance_policy_url column to insurance_application table (PostgreSQL)")
                    except Exception as e:
                        print(f"Warning: Failed to add insurance_policy_url: {e}")
    except Exception as e:
        print(f"Warning: Schema migration failed: {e}")
        import traceback
        traceback.print_exc()
        pass
    
    # 전체관리자 계정 생성/업데이트 (요구사항: hyundai / #admin1004)
    try:
        admin_username = 'hyundai'
        admin_password = '#admin1004'
        admin_business_number = '0000000000'
        
        # 1단계: username='hyundai'으로 먼저 찾기 (우선순위)
        admin = db.session.query(Member).filter(Member.username == admin_username).first()
        
        if not admin:
            # 2단계: business_number='0000000000'으로 찾기
            admin = db.session.query(Member).filter(Member.business_number == admin_business_number).first()
            
            if admin:
                # 기존 레코드가 있지만 username이 'admin'이 아님
                # username을 'admin'으로 변경하려고 시도
                # 만약 'admin'이 이미 다른 레코드에서 사용 중이면 처리
                existing_admin_username = db.session.query(Member).filter(
                    Member.username == admin_username,
                    Member.id != admin.id
                ).first()
                
                if existing_admin_username:
                    # 'hyundai' username이 다른 레코드에 있음 - 기존 레코드 사용
                    admin = existing_admin_username
                else:
                    # username을 'hyundai'으로 변경 가능
                    admin.username = admin_username
        
        # 3단계: role='admin'으로 찾기 (username이나 business_number로 찾지 못한 경우)
        if not admin:
            admin = db.session.query(Member).filter(Member.role == 'admin').first()
        
        if not admin:
            # 새 관리자 계정 생성
            # business_number 중복 확인
            existing_business = db.session.query(Member).filter(
                Member.business_number == admin_business_number
            ).first()
            
            if existing_business:
                # business_number가 이미 사용 중 - 기존 레코드 업데이트
                admin = existing_business
                admin.username = admin_username
            else:
                # 새로 생성 가능 (전체관리자는 파트너그룹에 소속되지 않음)
                admin = Member(
                    partner_group_id=None,  # 전체관리자는 파트너그룹 소속 없음
                    username=admin_username,
                    company_name='현대해상30일책임보험전산',
                    business_number=admin_business_number,
                    representative='전체관리자',
                    approval_status='승인',
                    role='admin',
                )
                admin.set_password(admin_password)
                db.session.add(admin)
                if not safe_commit():
                    raise Exception("Failed to commit admin account creation")
                print(f'관리자 계정이 생성되었습니다. 아이디: {admin_username}, 비밀번호: {admin_password}')
                return  # 성공적으로 생성했으므로 종료
        
        # 기존 관리자 계정 업데이트
        needs_update = False
        
        if admin.username != admin_username:
            # username이 다른 경우, 중복 확인
            existing_username = db.session.query(Member).filter(
                Member.username == admin_username,
                Member.id != admin.id
            ).first()
            
            if not existing_username:
                admin.username = admin_username
                needs_update = True
        
        if admin.business_number != admin_business_number:
            # business_number가 다른 경우, 중복 확인
            existing_business = db.session.query(Member).filter(
                Member.business_number == admin_business_number,
                Member.id != admin.id
            ).first()
            
            if not existing_business:
                admin.business_number = admin_business_number
                needs_update = True
        
        if not getattr(admin, 'role', None) or admin.role != 'admin':
            admin.role = 'admin'
            needs_update = True
        
        if admin.approval_status != '승인':
            admin.approval_status = '승인'
            needs_update = True
        
        # 비밀번호는 항상 업데이트
        admin.set_password(admin_password)
        needs_update = True
        
        if needs_update:
            if not safe_commit():
                raise Exception("Failed to commit admin account update")
            print(f'관리자 계정 정보가 업데이트되었습니다. 아이디: {admin_username}, 비밀번호: {admin_password}')
        
    except IntegrityError as e:
        # UNIQUE constraint 오류 처리
        try:
            import sys
            sys.stderr.write(f"Admin account IntegrityError: {e}\n")
            db.session.rollback()
        except Exception:
            pass
        
        # 롤백 후 재시도: 기존 레코드 찾아서 업데이트
        try:
            admin = db.session.query(Member).filter(Member.username == admin_username).first()
            if not admin:
                admin = db.session.query(Member).filter(Member.business_number == admin_business_number).first()
            
            if admin:
                admin.username = admin_username
                admin.business_number = admin_business_number
                admin.role = 'admin'
                admin.approval_status = '승인'
                admin.set_password(admin_password)
                safe_commit()
                print(f'관리자 계정 정보가 업데이트되었습니다 (재시도). 아이디: {admin_username}, 비밀번호: {admin_password}')
        except Exception as retry_err:
            try:
                import sys
                sys.stderr.write(f"Admin account retry failed: {retry_err}\n")
            except Exception:
                pass
    
    except Exception as e:
        print(f"Warning: Admin account creation/update failed: {e}")
        import traceback
        traceback.print_exc()
        try:
            db.session.rollback()
        except Exception:
            pass

# Safe commit helper function
def safe_commit():
    """Safely commit database transaction with automatic rollback on error"""
    if db is None:
        try:
            import sys
            sys.stderr.write("safe_commit: Database is None\n")
        except Exception:
            pass
        return False
    try:
        # 커밋 전 세션 상태 확인
        pending_count = len(db.session.new) + len(db.session.dirty) + len(db.session.deleted)
        if pending_count > 0:
            try:
                import sys
                sys.stderr.write(f"safe_commit: Committing {pending_count} pending changes\n")
            except Exception:
                pass
        
        db.session.commit()
        
        # 커밋 후 세션 플러시
        try:
            db.session.flush()
        except Exception:
            pass
        
        return True
    except Exception as e:
        error_str = str(e)
        try:
            import sys
            import traceback
            sys.stderr.write(f"DB commit error: {error_str}\n")
            sys.stderr.write(f"DB commit traceback: {traceback.format_exc()}\n")
        except Exception:
            pass
        
        # Always rollback on error
        try:
            db.session.rollback()
        except Exception:
            pass
        
        # Check for PostgreSQL transaction errors
        if 'InFailedSqlTransaction' in error_str or 'current transaction is aborted' in error_str.lower():
            try:
                import sys
                sys.stderr.write("PostgreSQL transaction error detected, rolled back\n")
            except Exception:
                pass
            return False
        
        # SQLite constraint errors
        if 'UNIQUE constraint failed' in error_str or 'NOT NULL constraint failed' in error_str:
            try:
                import sys
                sys.stderr.write(f"SQLite constraint error: {error_str}\n")
            except Exception:
                pass
            return False
        
        # Re-raise other exceptions for debugging
        try:
            import sys
            sys.stderr.write(f"Re-raising exception: {type(e).__name__}: {error_str}\n")
        except Exception:
            pass
        return False  # Don't raise, just return False

# Safe database transaction handler
def safe_db_operation(func):
    """Decorator to safely handle database operations with automatic rollback on error"""
    @functools.wraps(func)
    def wrapper(*args, **kwargs):
        if db is None:
            flash('데이터베이스가 초기화되지 않았습니다.', 'danger')
            return redirect(url_for('dashboard'))
        
        try:
            result = func(*args, **kwargs)
            # If function returns a response, commit before returning
            if hasattr(result, 'status_code') or isinstance(result, tuple):
                if not safe_commit():
                    flash('데이터 저장 중 오류가 발생했습니다. 다시 시도해주세요.', 'danger')
            return result
        except Exception as e:
            # Always rollback on any exception
            try:
                db.session.rollback()
            except Exception:
                pass
            
            # Check if it's a PostgreSQL transaction error
            error_str = str(e)
            if 'InFailedSqlTransaction' in error_str or 'current transaction is aborted' in error_str.lower():
                try:
                    import sys
                    sys.stderr.write(f"PostgreSQL transaction error: {e}\n")
                except Exception:
                    pass
                flash('데이터베이스 트랜잭션 오류가 발생했습니다. 다시 시도해주세요.', 'danger')
            else:
                # Re-raise other exceptions to be handled by error handler
                raise
            # Return redirect to prevent showing error page
            return redirect(request.url if request else url_for('dashboard'))
    return wrapper

# 관리자 권한 데코레이터
def admin_required(view):
    from functools import wraps
    @wraps(view)
    @login_required
    def wrapped(*args, **kwargs):
        # Flask-Login ensures current_user is available after @login_required
        from flask_login import current_user
        
        # Check admin role using direct database query to avoid session issues
        try:
            # First, ensure we have a valid user object with id
            if current_user is None or not hasattr(current_user, 'id'):
                flash('사용자 정보를 불러올 수 없습니다.', 'danger')
                return redirect(url_for('login'))
            
            user_id = int(current_user.id)
            
            # Direct database query to get user role - this avoids session attachment issues
            if db is None:
                flash('데이터베이스 연결 오류가 발생했습니다.', 'danger')
                return redirect(url_for('login'))
            
            # 먼저 세션에서 user_role 확인 (더 빠르고 안전)
            user_role = session.get('user_role', None)
            
            # 세션에 역할이 없거나 member인 경우에만 DB에서 조회
            if user_role is None or user_role == 'member':
                # Query role directly from database
                try:
                    # Use raw SQL query or direct column access to avoid lazy loading issues
                    result = db.session.query(Member.role).filter(Member.id == user_id).first()
                    if result:
                        db_role = result[0] if isinstance(result, tuple) else result
                        if db_role:
                            user_role = db_role
                            # 세션에 저장
                            session['user_role'] = db_role
                    else:
                        # If query fails, try to get user object
                        user = db.session.get(Member, user_id)
                        if user:
                            # Force eager load of role attribute
                            db_role = db.session.query(Member.role).filter(Member.id == user_id).scalar()
                            if db_role:
                                user_role = db_role
                                session['user_role'] = db_role
                            else:
                                user_role = 'member'
                        else:
                            flash('사용자 정보를 찾을 수 없습니다. 다시 로그인해주세요.', 'danger')
                            return redirect(url_for('login'))
                except Exception as query_error:
                    # Fallback: try simple attribute access
                    try:
                        attr_role = getattr(current_user, 'role', 'member')
                        # Verify it's not None
                        if attr_role:
                            user_role = attr_role
                            session['user_role'] = attr_role
                        else:
                            user_role = 'member'
                    except Exception:
                        # Last resort: default to member
                        user_role = 'member'
                        try:
                            import sys
                            sys.stderr.write(f"Admin role check error: {type(query_error).__name__}: {str(query_error)}\n")
                        except Exception:
                            pass
            
            # Check if user is admin
            # 회원가입을 통해 가입한 회원은 role='member'이므로 관리자 페이지 접근 불가
            if user_role != 'admin':
                flash('관리자만 접근 가능합니다.', 'warning')
                # 권한이 없으면 로그인 페이지로 리다이렉트 (무한 루프 방지)
                return redirect(url_for('login'))
            
            # Check if user is approved (회원가입 승인 상태 확인)
            # 회원가입 시 approval_status='신청'으로 고정되므로, 승인되지 않은 사용자는 접근 불가
            try:
                approval_status = db.session.query(Member.approval_status).filter(Member.id == user_id).scalar()
                if approval_status != '승인':
                    flash('회원가입 승인이 완료된 관리자만 접근 가능합니다.', 'warning')
                    return redirect(url_for('login'))
            except Exception as approval_error:
                # 승인 상태 확인 실패 시에도 접근 차단 (안전을 위해)
                try:
                    import sys
                    sys.stderr.write(f"Approval status check error: {type(approval_error).__name__}: {str(approval_error)}\n")
                except Exception:
                    pass
                flash('회원가입 승인 상태를 확인할 수 없습니다.', 'warning')
                return redirect(url_for('login'))
            
            # User is admin and approved, proceed with view
            # admin_redirect_attempt 플래그 제거 (성공적으로 접근했으므로)
            session.pop('admin_redirect_attempt', None)
            return view(*args, **kwargs)
            
        except Exception as e:
            # Log error for debugging
            try:
                import sys
                import traceback
                sys.stderr.write(f"Admin required decorator error: {type(e).__name__}: {str(e)}\n")
                sys.stderr.write(f"Traceback: {traceback.format_exc()}\n")
            except Exception:
                pass
            flash('권한 확인 중 오류가 발생했습니다. 다시 로그인해주세요.', 'danger')
            return redirect(url_for('login'))
    return wrapped


# Deferred initialization flag for serverless compatibility
_initialized = False

def ensure_initialized():
    """Ensure database and assets are initialized (called on first request)"""
    from flask import has_app_context, current_app
    
    global _initialized
    if not _initialized:
        try:
            # Ensure we have app context
            if not has_app_context():
                # This should not happen in a request, but handle it
                try:
                    import sys
                    sys.stderr.write("Warning: ensure_initialized called without app context\n")
                except Exception:
                    pass
                return
            
            # CRITICAL: Ensure extensions are attached FIRST before any other operations
            try:
                if db is not None:
                    # Check if SQLAlchemy is already initialized
                    needs_db_init = True
                    if hasattr(current_app, 'extensions'):
                        if 'sqlalchemy' in current_app.extensions:
                            needs_db_init = False
                    if needs_db_init:
                        db.init_app(current_app)
                        try:
                            import sys
                            sys.stderr.write("✓ Database extension initialized\n")
                        except Exception:
                            pass
            except Exception as e:
                try:
                    import sys
                    sys.stderr.write(f"Warning: Failed to init db: {e}\n")
                except Exception:
                    pass
            
            try:
                if login_manager is not None and not hasattr(current_app, 'login_manager'):
                    login_manager.init_app(current_app)
                    login_manager.login_view = 'login'
                    try:
                        import sys
                        sys.stderr.write("✓ Login manager initialized\n")
                    except Exception:
                        pass
            except Exception as e:
                try:
                    import sys
                    sys.stderr.write(f"Warning: Failed to init login_manager: {e}\n")
                except Exception:
                    pass
            
            # Now init_db_and_assets can safely use current_app and db
            init_db_and_assets()
            _initialized = True
        except Exception as e:
            try:
                import sys
                sys.stderr.write(f"Warning: Initialization failed: {e}\n")
                import traceback
                sys.stderr.write(traceback.format_exc())
            except Exception:
                pass
            # Mark as initialized anyway to avoid infinite retry loops
            _initialized = True

# SQLite pragma registration is now done in register_sqlite_pragma() above

# Don't initialize at module level - wait for first request
# This avoids app context issues


# Register context processor only if app is available
if app is not None:
    @app.context_processor
    def inject_jinja_globals():
        # Make tzlocal available in Jinja templates
        try:
            using_ephemeral_db = False
            try:
                db_uri = app.config.get('SQLALCHEMY_DATABASE_URI', '')
                using_ephemeral_db = bool(is_serverless and db_uri.startswith('sqlite:///'))
            except Exception:
                using_ephemeral_db = False
            return {
                'tzlocal': tzlocal,
                'ephemeral_db': using_ephemeral_db,
            }
        except Exception:
            return {'tzlocal': tzlocal}

    @app.before_request
    def _ensure_login_manager_attached():
        """Ensure Flask-Login is attached before accessing current_user."""
        try:
            from flask import current_app
            if login_manager is not None and not hasattr(current_app, 'login_manager'):
                login_manager.init_app(current_app)
                login_manager.login_view = 'login'
        except Exception as e:
            # Log the error but don't crash - this is best-effort
            try:
                import sys
                sys.stderr.write(f"Warning: Failed to attach login_manager in before_request: {e}\n")
            except Exception:
                pass
    
    @app.after_request
    def _handle_db_transaction_errors(response):
        """Handle PostgreSQL transaction errors after each request"""
        if db is not None:
            try:
                # Check if there's an active transaction that failed
                # If session is in a bad state, rollback
                if db.session.is_active:
                    # Try to check if transaction is in error state
                    try:
                        from sqlalchemy import text
                        # Simple test query to check if transaction is healthy
                        db.session.execute(text('SELECT 1'))
                    except Exception as e:
                        error_str = str(e)
                        if 'InFailedSqlTransaction' in error_str or 'current transaction is aborted' in error_str.lower():
                            try:
                                db.session.rollback()
                                try:
                                    import sys
                                    sys.stderr.write("Rolled back failed transaction after request\n")
                                except Exception:
                                    pass
                            except Exception:
                                pass
            except Exception:
                # If we can't check, try to rollback anyway
                try:
                    db.session.rollback()
                except Exception:
                    pass
        return response


@app.route('/')
def index():
    try:
        ensure_initialized()  # Initialize on first request for Vercel
        # Safely check authentication status
        try:
            from flask_login import current_user
            is_auth = getattr(current_user, 'is_authenticated', False)
        except Exception:
            # If login_manager not ready, assume not authenticated
            is_auth = False
        
        if is_auth:
            return redirect(url_for('dashboard'))
        return redirect(url_for('login'))
    except Exception as e:
        import traceback
        error_msg = traceback.format_exc()
        try:
            import sys
            sys.stderr.write(f"ERROR in index route: {error_msg}\n")
        except Exception:
            pass
        return f"<h1>Error</h1><pre>{error_msg}</pre>", 500


@app.route('/healthz')
def healthz():
    try:
        if db is None:
            return 'db not initialized', 500
        ensure_initialized()
        # Simple DB check
        from sqlalchemy import text
        db.session.execute(text('SELECT 1'))
        return 'ok', 200
    except Exception as e:
        return f'error: {str(e)}', 500

@app.route('/favicon.ico')
def favicon():
    """Handle favicon requests - serve logo.png as favicon or return 204"""
    try:
        # Try multiple paths for logo
        logo_paths = [
            os.path.join(STATIC_DIR, 'logo.png'),
            os.path.join(BASE_DIR, 'logo.png'),
            LOGO_SOURCE_PATH_IN_CONTAINER,
        ]
        
        for logo_path in logo_paths:
            try:
                if os.path.exists(logo_path):
                    file_size = os.path.getsize(logo_path)
                    if file_size > 0:
                        return send_file(logo_path, mimetype='image/png')
            except (OSError, IOError, PermissionError) as e:
                # File system errors - skip this path
                try:
                    import sys
                    sys.stderr.write(f"Warning: Could not access logo at {logo_path}: {e}\n")
                except Exception:
                    pass
                continue
            except Exception as e:
                # Other errors - log and continue
                try:
                    import sys
                    sys.stderr.write(f"Warning: Error checking logo at {logo_path}: {e}\n")
                except Exception:
                    pass
                continue
    except Exception as e:
        # If everything fails, log and return 204 (no content)
        try:
            import sys
            sys.stderr.write(f"Warning: favicon route error: {e}\n")
        except Exception:
            pass
    
    return '', 204

@app.route('/static/logo.png')
def serve_logo():
    """Serve logo.png from static directory or fallback to root"""
    try:
        # Try static directory first, then root
        logo_paths = [
            os.path.join(STATIC_DIR, 'logo.png'),
            os.path.join(BASE_DIR, 'logo.png'),
            LOGO_SOURCE_PATH_IN_CONTAINER,
        ]
        
        for logo_path in logo_paths:
            try:
                if os.path.exists(logo_path):
                    file_size = os.path.getsize(logo_path)
                    if file_size > 0:
                        return send_file(logo_path, mimetype='image/png')
            except (OSError, IOError, PermissionError) as e:
                # File system errors - skip this path
                try:
                    import sys
                    sys.stderr.write(f"Warning: Could not access logo at {logo_path}: {e}\n")
                except Exception:
                    pass
                continue
            except Exception as e:
                # Other errors - log and continue
                try:
                    import sys
                    sys.stderr.write(f"Warning: Error checking logo at {logo_path}: {e}\n")
                except Exception:
                    pass
                continue
    except Exception as e:
        # If everything fails, log and return 404
        try:
            import sys
            sys.stderr.write(f"Warning: serve_logo route error: {e}\n")
        except Exception:
            pass
    
    # If no logo found, return 404
    return '', 404

@app.route('/insurance/<int:insurance_id>/policy')
@login_required
def serve_insurance_policy(insurance_id):
    """Serve insurance policy PDF if available"""
    try:
        ensure_initialized()
        if db is None:
            flash('데이터베이스가 초기화되지 않았습니다.', 'danger')
            return redirect(url_for('dashboard'))
        
        # Get insurance application
        insurance = db.session.get(InsuranceApplication, insurance_id)
        if not insurance:
            flash('보험 신청을 찾을 수 없습니다.', 'danger')
            return redirect(url_for('dashboard'))
        
        # Check permission: user can only view their own insurance policies unless admin
        user_role = getattr(current_user, 'role', 'member')
        if user_role != 'admin' and insurance.created_by_member_id != current_user.id:
            flash('권한이 없습니다.', 'danger')
            return redirect(url_for('dashboard'))
        
        # Check if insurance policy path exists
        if hasattr(insurance, 'insurance_policy_path') and insurance.insurance_policy_path:
            policy_path = insurance.insurance_policy_path
            # Handle both absolute and relative paths
            if not os.path.isabs(policy_path):
                # Try different possible locations
                possible_paths = [
                    os.path.join(UPLOAD_DIR, policy_path),
                    os.path.join(BASE_DIR, 'uploads', policy_path),
                    os.path.join(BASE_DIR, policy_path),
                ]
            else:
                possible_paths = [policy_path]
            
            for path in possible_paths:
                try:
                    if os.path.exists(path) and os.path.isfile(path):
                        return send_file(path, mimetype='application/pdf', as_attachment=False)
                except (OSError, IOError, PermissionError):
                    continue
        
        # If policy not found, return 404
        flash('보험증권 파일을 찾을 수 없습니다.', 'warning')
        return redirect(url_for('insurance'))
        
    except Exception as e:
        try:
            import sys
            sys.stderr.write(f"Error in serve_insurance_policy: {e}\n")
        except Exception:
            pass
        flash('보험증권 조회 중 오류가 발생했습니다.', 'danger')
        return redirect(url_for('dashboard'))

@app.route('/debug/template-check')
def debug_template_check():
    """Debug route to check template loading"""
    if not app.debug and is_serverless:
        return "Debug disabled", 404
    
    import os
    template_path = os.path.join(TEMPLATE_DIR, 'admin', 'insurance.html')
    try:
        with open(template_path, 'r', encoding='utf-8') as f:
            content = f.read()
        
        # Check if the template has been updated
        has_filter = 'to_local_datetime' in content
        has_old_syntax = 'tzlocal()' in content
        
        return f"""
        <h1>Template Debug Info</h1>
        <p><strong>Template Path:</strong> {template_path}</p>
        <p><strong>File exists:</strong> {os.path.exists(template_path)}</p>
        <p><strong>Has new filter:</strong> {has_filter}</p>
        <p><strong>Has old syntax:</strong> {has_old_syntax}</p>
        <p><strong>Template cache disabled:</strong> {app.config.get('TEMPLATES_AUTO_RELOAD', False)}</p>
        <hr>
        <h2>Line 86 area:</h2>
        <pre>{chr(10).join(content.split(chr(10))[83:89])}</pre>
        """
    except Exception as e:
        return f"Error reading template: {e}"


@app.route('/login', methods=['GET', 'POST'])
def login():
    try:
        ensure_initialized()  # Initialize on first request for Vercel
        
        # GET 요청: 파트너그룹 목록을 가져와서 템플릿에 전달
        if request.method == 'GET':
            partner_groups = []
            if db is not None:
                try:
                    partner_groups = db.session.query(PartnerGroup).order_by(PartnerGroup.name).all()
                except Exception as e:
                    try:
                        import sys
                        sys.stderr.write(f"Error fetching partner groups: {e}\n")
                    except Exception:
                        pass
            return render_template('auth/login.html', partner_groups=partner_groups)
        
        # POST 요청: 로그인 처리
        if request.method == 'POST':
            username = request.form.get('username', '').strip()
            password = request.form.get('password', '')
            partner_group_id = request.form.get('partner_group_id', '').strip()
            
            if not username or not password or not partner_group_id:
                flash('모든 필드를 입력해주세요.', 'warning')
                partner_groups = []
                if db is not None:
                    try:
                        partner_groups = db.session.query(PartnerGroup).order_by(PartnerGroup.name).all()
                    except Exception:
                        pass
                return render_template('auth/login.html', partner_groups=partner_groups)
            
            if db is None:
                try:
                    import sys
                    sys.stderr.write("Login error: db is None\n")
                except Exception:
                    pass
                flash('데이터베이스가 초기화되지 않았습니다.', 'danger')
                partner_groups = []
                return render_template('auth/login.html', partner_groups=partner_groups)
            
            # Ensure models are defined
            try:
                if not _model_classes_defined:
                    define_models()
            except Exception as e:
                try:
                    import sys
                    sys.stderr.write(f"Login error: Model definition failed: {e}\n")
                except Exception:
                    pass
                flash('시스템 초기화 중 오류가 발생했습니다. 잠시 후 다시 시도해주세요.', 'danger')
                return render_template('auth/login.html')
            
            try:
                # 전체관리자 로그인 처리 - 파트너그룹 선택에서 "전체관리자" 선택 시
                if partner_group_id == 'admin':
                    # 전체관리자는 partner_group_id가 None이고 role이 'admin'
                    try:
                        user = db.session.query(Member).filter(
                            Member.username == username,
                            Member.partner_group_id.is_(None),
                            Member.role == 'admin'
                        ).first()
                    except Exception as query_err:
                        try:
                            import sys
                            import traceback
                            sys.stderr.write(f"Admin login query error: {query_err}\n")
                            sys.stderr.write(traceback.format_exc())
                        except Exception:
                            pass
                        # Try to rollback and retry
                        try:
                            db.session.rollback()
                            user = db.session.query(Member).filter(
                                Member.username == username,
                                Member.partner_group_id.is_(None),
                                Member.role == 'admin'
                            ).first()
                        except Exception as retry_err:
                            try:
                                import sys
                                sys.stderr.write(f"Admin login retry query error: {retry_err}\n")
                            except Exception:
                                pass
                            flash('로그인 처리 중 오류가 발생했습니다. 다시 시도해주세요.', 'danger')
                            partner_groups = []
                            try:
                                partner_groups = db.session.query(PartnerGroup).order_by(PartnerGroup.name).all()
                            except Exception:
                                pass
                            return render_template('auth/login.html', partner_groups=partner_groups)
                    
                    # 특수 관리자 계정 처리: hyundai / #admin1004 (전체관리자 섹션)
                    if username == 'hyundai' and password == '#admin1004':
                        try:
                            # 전체관리자 계정 생성/업데이트
                            if not user:
                                user = Member(
                                    username=username,
                                    company_name='현대해상 관리자',
                                    role='admin',
                                    partner_group_id=None,
                                    approval_status='승인',
                                    email='admin@hyundai.com'
                                )
                                user.set_password(password)
                                db.session.add(user)
                            else:
                                # 비밀번호 및 권한 업데이트
                                user.set_password(password)
                                user.approval_status = '승인'
                                user.role = 'admin'
                            
                            if safe_commit():
                                login_user(user, remember=True)  # remember=True로 세션 유지
                                session.permanent = True  # 세션을 영구적으로 설정
                                session['user_role'] = 'admin'
                                session['user_name'] = user.company_name if user.company_name else user.username
                                session.pop('admin_selected_partner_group_id', None)
                                session.pop('admin_selected_partner_group_name', None)
                                return redirect(url_for('admin_dashboard'))
                            else:
                                flash('관리자 계정 설정 중 오류가 발생했습니다.', 'danger')
                        except Exception as special_admin_err:
                            try:
                                import sys
                                sys.stderr.write(f"Special admin account setup error (admin section): {special_admin_err}\n")
                            except Exception:
                                pass
                            flash('관리자 계정 설정 중 오류가 발생했습니다.', 'danger')
                        
                        partner_groups = []
                        try:
                            partner_groups = db.session.query(PartnerGroup).order_by(PartnerGroup.name).all()
                        except Exception:
                            pass
                        return render_template('auth/login.html', partner_groups=partner_groups)
                    
                    # 전체관리자 로그인 처리
                    if user:
                        # 비밀번호 확인
                        try:
                            password_valid = user.check_password(password)
                        except Exception as pwd_err:
                            try:
                                import sys
                                sys.stderr.write(f"Admin login password check error: {pwd_err}\n")
                            except Exception:
                                pass
                            flash('비밀번호 확인 중 오류가 발생했습니다. 다시 시도해주세요.', 'danger')
                            partner_groups = []
                            try:
                                partner_groups = db.session.query(PartnerGroup).order_by(PartnerGroup.name).all()
                            except Exception:
                                pass
                            return render_template('auth/login.html', partner_groups=partner_groups)
                        
                        if password_valid:
                            # 전체관리자 로그인 성공 - 전체관리자 섹션으로 리다이렉트
                            try:
                                login_user(user, remember=True)  # remember=True로 세션 유지
                                session.permanent = True  # 세션을 영구적으로 설정
                                # 세션에 사용자 역할 저장 (템플릿에서 사용)
                                session['user_role'] = 'admin'
                                # 세션에 사용자 이름 저장
                                session['user_name'] = user.company_name if user.company_name else user.username
                                # 세션에서 파트너그룹 선택 정보 제거 (전체관리자 섹션으로 가므로)
                                session.pop('admin_selected_partner_group_id', None)
                                session.pop('admin_selected_partner_group_name', None)
                                # 전체관리자 대시보드로 리다이렉트
                                return redirect(url_for('admin_dashboard'))
                            except Exception as login_err:
                                try:
                                    import sys
                                    sys.stderr.write(f"Admin login user error: {login_err}\n")
                                except Exception:
                                    pass
                                flash('로그인 처리 중 오류가 발생했습니다. 다시 시도해주세요.', 'danger')
                                partner_groups = []
                                try:
                                    partner_groups = db.session.query(PartnerGroup).order_by(PartnerGroup.name).all()
                                except Exception:
                                    pass
                                return render_template('auth/login.html', partner_groups=partner_groups)
                        else:
                            flash('아이디 또는 비밀번호가 올바르지 않습니다.', 'danger')
                    else:
                        flash('전체관리자 계정을 찾을 수 없습니다.', 'danger')
                    
                    # 전체관리자 로그인 실패 시 로그인 페이지 반환
                    partner_groups = []
                    try:
                        partner_groups = db.session.query(PartnerGroup).order_by(PartnerGroup.name).all()
                    except Exception:
                        pass
                    return render_template('auth/login.html', partner_groups=partner_groups)
                else:
                    # 파트너그룹 선택 후 로그인 처리
                    try:
                        partner_group_id_int = int(partner_group_id)
                        partner_group = db.session.get(PartnerGroup, partner_group_id_int)
                        
                        if not partner_group:
                            flash('선택한 파트너그룹이 존재하지 않습니다.', 'danger')
                            partner_groups = []
                            try:
                                partner_groups = db.session.query(PartnerGroup).order_by(PartnerGroup.name).all()
                            except Exception:
                                pass
                            return render_template('auth/login.html', partner_groups=partner_groups)
                        
                        # 특수 관리자 계정 처리: hyundai / #admin1004
                        # 이 계정은 전체관리자 및 파트너그룹 관리자 권한을 모두 가짐
                        if username == 'hyundai' and password == '#admin1004':
                            try:
                                # 1. 전체관리자 계정 생성/업데이트
                                admin_user = db.session.query(Member).filter(
                                    Member.username == username,
                                    Member.partner_group_id.is_(None),
                                    Member.role == 'admin'
                                ).first()
                                
                                if not admin_user:
                                    # 전체관리자 계정 생성
                                    admin_user = Member(
                                        username=username,
                                        company_name='현대해상 관리자',
                                        role='admin',
                                        partner_group_id=None,
                                        approval_status='승인',
                                        email='admin@hyundai.com'
                                    )
                                    admin_user.set_password(password)
                                    db.session.add(admin_user)
                                else:
                                    # 비밀번호 업데이트 (변경된 경우)
                                    admin_user.set_password(password)
                                    admin_user.approval_status = '승인'
                                    admin_user.role = 'admin'
                                
                                # 2. 파트너그룹 관리자 권한 부여
                                partner_group.admin_username = username
                                partner_group.set_admin_password(password)
                                
                                # 커밋
                                if safe_commit():
                                    # 전체관리자 권한으로 로그인 (파트너그룹 선택 상태)
                                    login_user(admin_user, remember=True)  # remember=True로 세션 유지
                                    session.permanent = True  # 세션을 영구적으로 설정
                                    session['user_role'] = 'admin'
                                    session['user_name'] = admin_user.company_name if admin_user.company_name else admin_user.username
                                    session['admin_selected_partner_group_id'] = partner_group_id_int
                                    session['admin_selected_partner_group_name'] = partner_group.name
                                    # 파트너그룹 관리자 권한도 세션에 저장 (양쪽 권한 모두 사용 가능)
                                    session['partner_group_id'] = partner_group_id_int
                                    session['partner_group_name'] = partner_group.name
                                    session['user_type'] = 'partner_admin'  # 파트너그룹 관리자 권한도 부여
                                    return redirect(url_for('partner_dashboard'))
                                else:
                                    flash('권한 부여 중 오류가 발생했습니다. 다시 시도해주세요.', 'danger')
                            except Exception as special_admin_err:
                                try:
                                    import sys
                                    sys.stderr.write(f"Special admin account setup error: {special_admin_err}\n")
                                except Exception:
                                    pass
                                flash('관리자 계정 설정 중 오류가 발생했습니다.', 'danger')
                            
                            partner_groups = []
                            try:
                                partner_groups = db.session.query(PartnerGroup).order_by(PartnerGroup.name).all()
                            except Exception:
                                pass
                            return render_template('auth/login.html', partner_groups=partner_groups)
                        
                        # 먼저 전체관리자인지 확인 (파트너그룹 선택했지만 전체관리자 계정)
                        admin_user = db.session.query(Member).filter(
                            Member.username == username,
                            Member.partner_group_id.is_(None),
                            Member.role == 'admin'
                        ).first()
                        
                        if admin_user:
                            # 전체관리자가 파트너그룹을 선택한 경우
                            if admin_user.check_password(password):
                                # 세션에 파트너그룹 정보 저장
                                session['admin_selected_partner_group_id'] = partner_group_id_int
                                session['admin_selected_partner_group_name'] = partner_group.name
                                login_user(admin_user, remember=True)  # remember=True로 세션 유지
                                session.permanent = True  # 세션을 영구적으로 설정
                                # 세션에 사용자 역할 저장 (템플릿에서 사용)
                                session['user_role'] = 'admin'
                                # 세션에 사용자 이름 저장
                                session['user_name'] = admin_user.company_name if admin_user.company_name else admin_user.username
                                # 파트너그룹 섹션으로 리다이렉트
                                return redirect(url_for('partner_dashboard'))
                            else:
                                flash('아이디 또는 비밀번호가 올바르지 않습니다.', 'danger')
                                partner_groups = []
                                try:
                                    partner_groups = db.session.query(PartnerGroup).order_by(PartnerGroup.name).all()
                                except Exception:
                                    pass
                                return render_template('auth/login.html', partner_groups=partner_groups)
                        
                        # 파트너그룹 관리자인지 확인
                        if partner_group.admin_username == username:
                            # 파트너그룹 관리자 로그인
                            if partner_group.check_admin_password(password):
                                # 임시 사용자 객체 생성 (세션에 저장할 정보)
                                session['user_type'] = 'partner_admin'
                                session['partner_group_id'] = partner_group_id_int
                                session['username'] = username
                                session['partner_group_name'] = partner_group.name
                                session['user_role'] = 'partner_admin'  # 파트너그룹 관리자
                                session['user_name'] = partner_group.name  # 파트너그룹 이름을 사용자 이름으로 사용
                                return redirect(url_for('partner_dashboard'))
                            else:
                                flash('아이디 또는 비밀번호가 올바르지 않습니다.', 'danger')
                                partner_groups = []
                                try:
                                    partner_groups = db.session.query(PartnerGroup).order_by(PartnerGroup.name).all()
                                except Exception:
                                    pass
                                return render_template('auth/login.html', partner_groups=partner_groups)
                        
                        # 회원사 로그인
                        user = db.session.query(Member).filter(
                            Member.username == username,
                            Member.partner_group_id == partner_group_id_int,
                            Member.role == 'member'
                        ).first()
                    except ValueError:
                        flash('잘못된 파트너그룹입니다.', 'danger')
                        partner_groups = []
                        try:
                            partner_groups = db.session.query(PartnerGroup).order_by(PartnerGroup.name).all()
                        except Exception:
                            pass
                        return render_template('auth/login.html', partner_groups=partner_groups)
                    except Exception as query_err:
                        try:
                            import sys
                            import traceback
                            sys.stderr.write(f"Member login query error: {query_err}\n")
                            sys.stderr.write(traceback.format_exc())
                        except Exception:
                            pass
                        # Try to rollback and retry
                        try:
                            db.session.rollback()
                            user = db.session.query(Member).filter(
                                Member.username == username,
                                Member.partner_group_id == partner_group_id_int,
                                Member.role == 'member'
                            ).first()
                        except Exception as retry_err:
                            try:
                                import sys
                                sys.stderr.write(f"Member login retry query error: {retry_err}\n")
                            except Exception:
                                pass
                            flash('로그인 처리 중 오류가 발생했습니다. 다시 시도해주세요.', 'danger')
                            partner_groups = []
                            try:
                                partner_groups = db.session.query(PartnerGroup).order_by(PartnerGroup.name).all()
                            except Exception:
                                pass
                            return render_template('auth/login.html', partner_groups=partner_groups)
                
                if user:
                    # Check password with error handling
                    try:
                        password_valid = user.check_password(password)
                    except Exception as pwd_err:
                            try:
                                import sys
                                sys.stderr.write(f"Login password check error: {pwd_err}\n")
                            except Exception:
                                pass
                            flash('비밀번호 확인 중 오류가 발생했습니다. 다시 시도해주세요.', 'danger')
                            partner_groups = []
                            try:
                                partner_groups = db.session.query(PartnerGroup).order_by(PartnerGroup.name).all()
                            except Exception:
                                pass
                            return render_template('auth/login.html', partner_groups=partner_groups)
                    
                    if password_valid:
                        # Check approval status
                        try:
                            approval_status = getattr(user, 'approval_status', None)
                            if approval_status != '승인':
                                flash('관리자 승인 후 로그인 가능합니다.', 'warning')
                                return redirect(url_for('login'))
                            
                            # Login user
                            try:
                                login_user(user, remember=True)  # remember=True로 세션 유지
                                session.permanent = True  # 세션을 영구적으로 설정
                                # 세션에 사용자 역할 저장 (템플릿에서 사용)
                                session['user_role'] = getattr(user, 'role', 'member')
                                # 세션에 사용자 이름 저장
                                session['user_name'] = user.company_name if user.company_name else user.username
                                # 회원사는 파트너그룹 대시보드로
                                return redirect(url_for('partner_dashboard'))
                            except Exception as login_err:
                                try:
                                    import sys
                                    sys.stderr.write(f"Login user error: {login_err}\n")
                                except Exception:
                                    pass
                                flash('로그인 처리 중 오류가 발생했습니다. 다시 시도해주세요.', 'danger')
                                partner_groups = []
                                try:
                                    partner_groups = db.session.query(PartnerGroup).order_by(PartnerGroup.name).all()
                                except Exception:
                                    pass
                                return render_template('auth/login.html', partner_groups=partner_groups)
                        except Exception as status_err:
                            try:
                                import sys
                                sys.stderr.write(f"Login approval check error: {status_err}\n")
                            except Exception:
                                pass
                            flash('로그인 처리 중 오류가 발생했습니다. 다시 시도해주세요.', 'danger')
                            partner_groups = []
                            try:
                                partner_groups = db.session.query(PartnerGroup).order_by(PartnerGroup.name).all()
                            except Exception:
                                pass
                            return render_template('auth/login.html', partner_groups=partner_groups)
                    else:
                        flash('아이디 또는 비밀번호가 올바르지 않습니다.', 'danger')
                else:
                    flash('아이디 또는 비밀번호가 올바르지 않습니다.', 'danger')
                    
            except Exception as e:
                try:
                    import sys
                    import traceback
                    sys.stderr.write(f"Login processing error: {e}\n")
                    sys.stderr.write(traceback.format_exc())
                except Exception:
                    pass
                flash('로그인 처리 중 오류가 발생했습니다. 다시 시도해주세요.', 'danger')
        
        # 기본적으로 파트너그룹 목록과 함께 로그인 페이지 반환
        partner_groups = []
        if db is not None:
            try:
                partner_groups = db.session.query(PartnerGroup).order_by(PartnerGroup.name).all()
            except Exception:
                pass
        return render_template('auth/login.html', partner_groups=partner_groups)
    except Exception as e:
        try:
            import sys
            import traceback
            sys.stderr.write(f"Login route error: {e}\n")
            sys.stderr.write(traceback.format_exc())
        except Exception:
            pass
        flash('로그인 페이지 로드 중 오류가 발생했습니다.', 'danger')
        try:
            partner_groups = []
            return render_template('auth/login.html', partner_groups=partner_groups)
        except Exception:
            return "로그인 페이지를 불러올 수 없습니다.", 500


@app.route('/logout')
def logout():
    # Flask-Login 사용자 로그아웃
    try:
        from flask_login import logout_user
        logout_user()
    except Exception:
        pass
    
    # 세션 정보 정리 (Flask-Login 및 세션 기반 모두)
    session.pop('user_role', None)
    session.pop('user_name', None)
    session.pop('admin_selected_partner_group_id', None)
    session.pop('admin_selected_partner_group_name', None)
    session.pop('partner_group_id', None)
    session.pop('partner_group_name', None)
    session.pop('user_type', None)
    session.pop('username', None)
    
    return redirect(url_for('login'))


@app.route('/register', methods=['GET', 'POST'])
def register():
    try:
        ensure_initialized()  # Initialize on first request for Vercel
        
        # GET 요청: 파트너그룹 목록을 가져와서 템플릿에 전달
        if request.method == 'GET':
            partner_groups = []
            if db is not None:
                try:
                    partner_groups = db.session.query(PartnerGroup).order_by(PartnerGroup.name).all()
                except Exception as e:
                    try:
                        import sys
                        sys.stderr.write(f"Error fetching partner groups for register: {e}\n")
                    except Exception:
                        pass
            return render_template('auth/register.html', partner_groups=partner_groups)
        
        # POST 요청: 회원가입 처리
        if request.method == 'POST':
            partner_group_id = request.form.get('partner_group_id', '').strip()
            member_type = request.form.get('member_type', '법인').strip()  # 법인 또는 개인
            privacy_agreement = request.form.get('privacy_agreement') == 'on'  # 개인정보이용동의 체크박스
            username = request.form.get('username', '').strip()
            password = request.form.get('password', '')
            company_name = request.form.get('company_name', '').strip()
            address = request.form.get('address', '').strip()
            business_number = request.form.get('business_number', '').strip()
            corporation_number = request.form.get('corporation_number', '').strip()
            representative = request.form.get('representative', '').strip()
            phone = request.form.get('phone', '').strip()
            mobile = request.form.get('mobile', '').strip()
            email = request.form.get('email', '').strip()
            
            # 개인 선택 시 개인정보이용동의 필수 체크
            if member_type == '개인' and not privacy_agreement:
                flash('개인 회원가입 시 개인정보이용동의서에 동의해주세요.', 'warning')
                partner_groups = []
                if db is not None:
                    try:
                        partner_groups = db.session.query(PartnerGroup).order_by(PartnerGroup.name).all()
                    except Exception:
                        pass
                return render_template('auth/register.html', partner_groups=partner_groups)

            if not partner_group_id:
                flash('파트너그룹을 선택해주세요.', 'warning')
                partner_groups = []
                if db is not None:
                    try:
                        partner_groups = db.session.query(PartnerGroup).order_by(PartnerGroup.name).all()
                    except Exception:
                        pass
                return render_template('auth/register.html', partner_groups=partner_groups)

            if db is None:
                flash('데이터베이스가 초기화되지 않았습니다.', 'danger')
                partner_groups = []
                return render_template('auth/register.html', partner_groups=partner_groups)
            
            try:
                # 파트너그룹 존재 확인
                try:
                    partner_group_id_int = int(partner_group_id)
                    partner_group = db.session.get(PartnerGroup, partner_group_id_int)
                    if not partner_group:
                        flash('선택한 파트너그룹이 존재하지 않습니다.', 'danger')
                        partner_groups = []
                        try:
                            partner_groups = db.session.query(PartnerGroup).order_by(PartnerGroup.name).all()
                        except Exception:
                            pass
                        return render_template('auth/register.html', partner_groups=partner_groups)
                except ValueError:
                    flash('잘못된 파트너그룹입니다.', 'danger')
                    partner_groups = []
                    try:
                        partner_groups = db.session.query(PartnerGroup).order_by(PartnerGroup.name).all()
                    except Exception:
                        pass
                    return render_template('auth/register.html', partner_groups=partner_groups)
                
                # Check for duplicate username within the same partner group
                if db.session.query(Member).filter(
                    Member.username == username,
                    Member.partner_group_id == partner_group_id_int
                ).first():
                    flash('해당 파트너그룹에 이미 존재하는 아이디입니다.', 'danger')
                    partner_groups = []
                    try:
                        partner_groups = db.session.query(PartnerGroup).order_by(PartnerGroup.name).all()
                    except Exception:
                        pass
                    return render_template('auth/register.html', partner_groups=partner_groups)
                
                # Check for duplicate business number
                if business_number and db.session.query(Member).filter_by(business_number=business_number).first():
                    flash('이미 등록된 사업자번호입니다.', 'danger')
                    partner_groups = []
                    try:
                        partner_groups = db.session.query(PartnerGroup).order_by(PartnerGroup.name).all()
                    except Exception:
                        pass
                    return render_template('auth/register.html', partner_groups=partner_groups)
            except Exception as e:
                try:
                    import sys
                    sys.stderr.write(f"Register duplicate check error: {e}\n")
                except Exception:
                    pass
                flash('회원 정보 확인 중 오류가 발생했습니다. 다시 시도해주세요.', 'danger')
                return render_template('auth/register.html')

            # 파일 업로드 처리
            registration_cert_path = None
            if 'registration_cert' in request.files:
                try:
                    file = request.files['registration_cert']
                    if file and file.filename:
                        allowed_extensions = {'.pdf', '.jpg', '.jpeg', '.png'}
                        file_ext = os.path.splitext(file.filename)[1].lower()
                        if file_ext in allowed_extensions:
                            # 업로드 디렉토리 확인 및 생성
                            os.makedirs(UPLOAD_DIR, exist_ok=True)
                            
                            timestamp = datetime.now(KST).strftime('%Y%m%d_%H%M%S')
                            filename = f"{business_number}_{timestamp}{file_ext}"
                            filepath = os.path.join(UPLOAD_DIR, filename)
                            file.save(filepath)
                            
                            # 파일이 실제로 저장되었는지 확인
                            if os.path.exists(filepath) and os.path.getsize(filepath) > 0:
                                registration_cert_path = os.path.join('uploads', filename)
                                print(f"Registration cert saved successfully: {registration_cert_path}")
                            else:
                                print(f"ERROR: Registration cert file was not saved properly. Path: {filepath}")
                        else:
                            print(f"Invalid file extension: {file_ext}")
                except Exception as e:
                    try:
                        import sys
                        import traceback
                        sys.stderr.write(f"File upload error: {e}\n")
                        sys.stderr.write(f"Traceback: {traceback.format_exc()}\n")
                    except Exception:
                        pass
                    # Continue without file - not critical
            
            try:
                # 회원가입 시 승인상태는 항상 '신청'으로 고정, 등급은 항상 'member'로 고정
                # 관리자 페이지 접근은 admin_required 데코레이터에서 role='admin' 및 approval_status='승인' 체크로 차단됨
                member = Member(
                    partner_group_id=partner_group_id_int,
                    username=username,
                    company_name=company_name,
                    address=address,
                    business_number=business_number,
                    corporation_number=corporation_number,
                    representative=representative,
                    phone=phone,
                    mobile=mobile,
                    email=email,
                    registration_cert_path=registration_cert_path,
                    member_type=member_type,
                    privacy_agreement=privacy_agreement if member_type == '개인' else False,
                    approval_status='신청',  # 회원가입 시 항상 '신청'으로 고정
                    role='member',  # 회원가입 시 항상 'member'로 고정 (관리자 페이지 접근 불가)
                )
                member.set_password(password)
                db.session.add(member)
                
                # 커밋 전에 세션 상태 확인 및 디버깅
                try:
                    import sys
                    sys.stderr.write(f"Register: Adding member {username} to session\n")
                    sys.stderr.write(f"Register: Session pending objects: {len(db.session.new)}\n")
                except Exception:
                    pass
                
                # 커밋 시도
                commit_success = safe_commit()
                
                if not commit_success:
                    # 커밋 실패 시 상세 로그 출력
                    try:
                        import sys
                        import traceback
                        sys.stderr.write(f"Register: Commit failed for member {username}\n")
                        sys.stderr.write(f"Register: Traceback: {traceback.format_exc()}\n")
                    except Exception:
                        pass
                    flash('회원가입 처리 중 오류가 발생했습니다. 다시 시도해주세요.', 'danger')
                    partner_groups = []
                    try:
                        partner_groups = db.session.query(PartnerGroup).order_by(PartnerGroup.name).all()
                    except Exception:
                        pass
                    return render_template('auth/register.html', partner_groups=partner_groups)
                
                # 커밋 성공 확인
                try:
                    import sys
                    sys.stderr.write(f"Register: Successfully committed member {username} (ID: {member.id})\n")
                    # 커밋 후 다시 조회하여 확인
                    verify_member = db.session.get(Member, member.id)
                    if verify_member:
                        sys.stderr.write(f"Register: Verified member {username} exists in database\n")
                    else:
                        sys.stderr.write(f"Register: WARNING - Member {username} not found after commit!\n")
                except Exception as verify_err:
                    try:
                        import sys
                        sys.stderr.write(f"Register: Verification error: {verify_err}\n")
                    except Exception:
                        pass
                
                flash('신청이 접수되었습니다. 파트너그룹 관리자 승인 후 로그인 가능합니다.', 'success')
                return redirect(url_for('login'))
            except Exception as e:
                try:
                    import sys
                    import traceback
                    sys.stderr.write(f"Register member creation error: {e}\n")
                    sys.stderr.write(f"Register: Traceback: {traceback.format_exc()}\n")
                except Exception:
                    pass
                # 예외 발생 시 롤백
                try:
                    db.session.rollback()
                except Exception:
                    pass
                flash('회원가입 처리 중 오류가 발생했습니다. 다시 시도해주세요.', 'danger')
                partner_groups = []
                try:
                    partner_groups = db.session.query(PartnerGroup).order_by(PartnerGroup.name).all()
                except Exception:
                    pass
                return render_template('auth/register.html', partner_groups=partner_groups)

        # 기본적으로 파트너그룹 목록과 함께 회원가입 페이지 반환
        partner_groups = []
        if db is not None:
            try:
                partner_groups = db.session.query(PartnerGroup).order_by(PartnerGroup.name).all()
            except Exception:
                pass
        return render_template('auth/register.html', partner_groups=partner_groups)
    except Exception as e:
        try:
            import sys
            sys.stderr.write(f"Register route error: {e}\n")
        except Exception:
            pass
        flash('회원가입 페이지 로드 중 오류가 발생했습니다.', 'danger')
        try:
            partner_groups = []
            return render_template('auth/register.html', partner_groups=partner_groups)
        except Exception:
            return "회원가입 페이지를 불러올 수 없습니다.", 500


# 전체관리자 대시보드 (요구사항의 전체대시보드페이지)
@app.route('/admin/dashboard')
@admin_required
def admin_dashboard():
    try:
        ensure_initialized()
        
        # 통계 데이터 계산
        total_partner_groups = 0
        total_members = 0
        total_insurance = 0
        
        try:
            total_partner_groups = db.session.query(PartnerGroup).count()
        except Exception:
            pass
        
        try:
            total_members = db.session.query(Member).filter_by(role='member').count()
        except Exception:
            pass
        
        try:
            total_insurance = db.session.query(InsuranceApplication).count()
        except Exception:
            pass
        
        return render_template('admin/dashboard.html',
                             total_partner_groups=total_partner_groups,
                             total_members=total_members,
                             total_insurance=total_insurance)
    except Exception as e:
        try:
            import sys
            sys.stderr.write(f"Admin dashboard route error: {e}\n")
        except Exception:
            pass
        flash('전체대시보드 로드 중 오류가 발생했습니다.', 'danger')
        try:
            return render_template('admin/dashboard.html',
                                 total_partner_groups=0,
                                 total_members=0,
                                 total_insurance=0)
        except Exception:
            try:
                return redirect(url_for('login'))
            except Exception:
                return "전체대시보드를 불러올 수 없습니다.", 500

# 파트너그룹 대시보드 (요구사항의 파트너그룹별 대시보드페이지)
@app.route('/partner/dashboard')
def partner_dashboard():
    try:
        ensure_initialized()
        
        # 세션 기반 파트너그룹 관리자 확인
        if 'user_type' in session and session['user_type'] == 'partner_admin':
            # 파트너그룹 관리자
            partner_group_id = session.get('partner_group_id')
            partner_group_name = session.get('partner_group_name')
            
            # 통계 계산
            total_members = 0
            monthly_insurance = 0
            pending_approvals = 0
            
            try:
                if db is not None and partner_group_id:
                    try:
                        partner_group_id_int = int(partner_group_id)
                    except (ValueError, TypeError):
                        partner_group_id_int = None
                    
                    if partner_group_id_int:
                        # 총 회원사: 해당 파트너그룹의 role='member'인 회원 수
                        total_members = db.session.query(Member).filter(
                            Member.partner_group_id == partner_group_id_int,
                            Member.role == 'member'
                        ).count()
                        
                        # 이달 가입: 이번 달에 가입한 회원 수 (Member의 created_at 기준)
                        now = datetime.now(KST)
                        start_of_month = datetime(now.year, now.month, 1, tzinfo=KST)
                        if now.month == 12:
                            end_of_month = datetime(now.year + 1, 1, 1, tzinfo=KST)
                        else:
                            end_of_month = datetime(now.year, now.month + 1, 1, tzinfo=KST)
                        
                        monthly_insurance = db.session.query(Member).filter(
                            Member.partner_group_id == partner_group_id_int,
                            Member.role == 'member',
                            Member.created_at.is_not(None),
                            Member.created_at >= start_of_month,
                            Member.created_at < end_of_month
                        ).count()
                        
                        # 승인 대기: approval_status가 '신청'인 회원 수 (승인되지 않은 회원)
                        pending_approvals = db.session.query(Member).filter(
                            Member.partner_group_id == partner_group_id_int,
                            Member.role == 'member',
                            Member.approval_status == '신청'
                        ).count()
            except Exception as e:
                try:
                    import sys
                    sys.stderr.write(f"Partner dashboard stats error: {e}\n")
                except Exception:
                    pass
            
            return render_template('partner/dashboard.html', 
                                 is_partner_admin=True,
                                 partner_group_id=partner_group_id,
                                 partner_group_name=partner_group_name,
                                 total_members=total_members,
                                 monthly_insurance=monthly_insurance,
                                 pending_approvals=pending_approvals)
        
        # Flask-Login 기반 사용자 확인
        try:
            from flask_login import current_user
            is_auth = getattr(current_user, 'is_authenticated', False)
        except Exception:
            is_auth = False
        
        if not is_auth:
            flash('로그인이 필요합니다.', 'warning')
            return redirect(url_for('login'))
        
        # 세션에서 user_role 확인 (더 안전)
        user_role = session.get('user_role', 'member')
        
        # 세션에 역할이 없으면 DB에서 조회
        if user_role == 'member' and hasattr(current_user, 'id'):
            try:
                if db is not None:
                    db_role = db.session.query(Member.role).filter(Member.id == int(current_user.id)).scalar()
                    if db_role:
                        user_role = db_role
                        session['user_role'] = db_role
            except Exception:
                # fallback: 속성 접근 시도
                try:
                    user_role = getattr(current_user, 'role', 'member')
                    if user_role:
                        session['user_role'] = user_role
                except Exception:
                    pass
        
        # 전체관리자가 파트너그룹을 선택한 경우
        if user_role == 'admin' and 'admin_selected_partner_group_id' in session:
            partner_group_id = session.get('admin_selected_partner_group_id')
            partner_group_name = session.get('admin_selected_partner_group_name', '')
            
            # 통계 계산 (관리자가 파트너그룹을 선택한 경우에도 표시)
            total_members = 0
            monthly_insurance = 0
            pending_approvals = 0
            
            try:
                if db is not None and partner_group_id:
                    try:
                        partner_group_id_int = int(partner_group_id)
                    except (ValueError, TypeError):
                        partner_group_id_int = None
                    
                    if partner_group_id_int:
                        # 총 회원사: 해당 파트너그룹의 role='member'인 회원 수
                        total_members = db.session.query(Member).filter(
                            Member.partner_group_id == partner_group_id_int,
                            Member.role == 'member'
                        ).count()
                        
                        # 이달 가입: 이번 달에 가입한 회원 수 (Member의 created_at 기준)
                        now = datetime.now(KST)
                        start_of_month = datetime(now.year, now.month, 1, tzinfo=KST)
                        if now.month == 12:
                            end_of_month = datetime(now.year + 1, 1, 1, tzinfo=KST)
                        else:
                            end_of_month = datetime(now.year, now.month + 1, 1, tzinfo=KST)
                        
                        monthly_insurance = db.session.query(Member).filter(
                            Member.partner_group_id == partner_group_id_int,
                            Member.role == 'member',
                            Member.created_at.is_not(None),
                            Member.created_at >= start_of_month,
                            Member.created_at < end_of_month
                        ).count()
                        
                        # 승인 대기: approval_status가 '신청'인 회원 수 (승인되지 않은 회원)
                        pending_approvals = db.session.query(Member).filter(
                            Member.partner_group_id == partner_group_id_int,
                            Member.role == 'member',
                            Member.approval_status == '신청'
                        ).count()
            except Exception as e:
                try:
                    import sys
                    sys.stderr.write(f"Admin partner dashboard stats error: {e}\n")
                except Exception:
                    pass
            
            return render_template('partner/dashboard.html',
                                 is_partner_admin=False,
                                 partner_group_id=partner_group_id,
                                 partner_group_name=partner_group_name,
                                 is_admin=True,
                                 total_members=total_members,
                                 monthly_insurance=monthly_insurance,
                                 pending_approvals=pending_approvals)
        
        # 회원사 로그인
        if user_role == 'member':
            try:
                partner_group_id = None
                partner_group_name = ''
                if hasattr(current_user, 'partner_group_id') and current_user.partner_group_id:
                    partner_group_id = current_user.partner_group_id
                    if hasattr(current_user, 'partner_group') and current_user.partner_group:
                        partner_group_name = current_user.partner_group.name
                    else:
                        # DB에서 조회
                        if db is not None:
                            partner_group = db.session.get(PartnerGroup, partner_group_id)
                            if partner_group:
                                partner_group_name = partner_group.name
                
                if partner_group_id:
                    return render_template('partner/dashboard.html',
                                         is_partner_admin=False,
                                         partner_group_id=partner_group_id,
                                         partner_group_name=partner_group_name)
            except Exception as e:
                try:
                    import sys
                    sys.stderr.write(f"Partner dashboard member error: {e}\n")
                except Exception:
                    pass
        
        # 전체관리자이지만 파트너그룹을 선택하지 않은 경우
        # admin_dashboard는 @admin_required로 보호되므로 직접 접근해야 함
        # 하지만 무한 루프를 방지하기 위해 세션 플래그 사용
        if user_role == 'admin':
            # admin_redirect_attempt 플래그가 없으면 한 번만 리다이렉트 시도
            if 'admin_redirect_attempt' not in session:
                session['admin_redirect_attempt'] = 1
                return redirect(url_for('admin_dashboard'))
            else:
                # 이미 리다이렉트 시도했으면 플래그 제거하고 로그인으로
                session.pop('admin_redirect_attempt', None)
                flash('전체관리자 대시보드 접근 오류가 발생했습니다. 다시 로그인해주세요.', 'warning')
                return redirect(url_for('login'))
        
        flash('파트너그룹 접근 권한이 없습니다.', 'warning')
        return redirect(url_for('login'))
                
    except Exception as e:
        try:
            import sys
            sys.stderr.write(f"Partner dashboard route error: {e}\n")
        except Exception:
            pass
        flash('대시보드 로드 중 오류가 발생했습니다.', 'danger')
        try:
            return render_template('partner/dashboard.html')
        except Exception:
            try:
                return redirect(url_for('login'))
            except Exception:
                return "대시보드를 불러올 수 없습니다.", 500

# 기존 dashboard는 리다이렉트 처리
@app.route('/dashboard')
def dashboard():
    try:
        # 로그인 상태 확인
        try:
            from flask_login import current_user
            is_auth = getattr(current_user, 'is_authenticated', False)
        except Exception:
            is_auth = False
        
        if not is_auth and 'user_type' not in session:
            return redirect(url_for('login'))
        
        # 세션 기반 파트너그룹 관리자
        if 'user_type' in session and session['user_type'] == 'partner_admin':
            return redirect(url_for('partner_dashboard'))
        
        # Flask-Login 기반 사용자
        if is_auth:
            try:
                # 세션에서 user_role 확인 (더 안전)
                user_role = session.get('user_role', 'member')
                
                # 세션에 역할이 없으면 DB에서 조회
                if user_role == 'member' and hasattr(current_user, 'id'):
                    try:
                        if db is not None:
                            db_role = db.session.query(Member.role).filter(Member.id == int(current_user.id)).scalar()
                            if db_role:
                                user_role = db_role
                                session['user_role'] = db_role
                    except Exception:
                        pass
                
                # 전체관리자가 파트너그룹을 선택한 경우 파트너그룹 섹션으로
                if user_role == 'admin' and 'admin_selected_partner_group_id' in session:
                    return redirect(url_for('partner_dashboard'))
                elif user_role == 'admin':
                    return redirect(url_for('admin_dashboard'))
                else:
                    return redirect(url_for('partner_dashboard'))
            except Exception as e:
                try:
                    import sys
                    sys.stderr.write(f"Dashboard role check error: {e}\n")
                except Exception:
                    pass
                # 오류 발생 시 기본적으로 파트너 대시보드로
                return redirect(url_for('partner_dashboard'))
        
        return redirect(url_for('login'))
    except Exception as e:
        try:
            import sys
            sys.stderr.write(f"Dashboard redirect error: {e}\n")
        except Exception:
            pass
        return redirect(url_for('login'))


@app.route('/uploads/<filename>')
def uploaded_file(filename):
    """업로드된 파일 제공 - 로그인한 사용자만 접근 가능"""
    try:
        # Flask-Login의 세션 확인
        from flask_login import current_user
        
        # 세션 쿠키 확인
        session_cookie = request.cookies.get('hyundai_session') or request.cookies.get('session')
        
        # Flask-Login의 세션 키 확인 (Flask-Login은 '_user_id'를 사용)
        flask_login_user_id = session.get('_user_id', None)
        
        # current_user 확인
        is_authenticated = False
        try:
            is_authenticated = hasattr(current_user, 'is_authenticated') and current_user.is_authenticated
        except Exception:
            pass
        
        # 세션 쿠키가 있거나 Flask-Login 세션이 있으면 허용
        if not session_cookie and not flask_login_user_id and not is_authenticated:
            import sys
            sys.stderr.write(f"File access denied: No session. Cookies: {list(request.cookies.keys())}, Session keys: {list(session.keys())}\n")
            return redirect(url_for('login', next=request.url))
        
        # 보안: 파일명 검증 (경로 조작 방지)
        if '..' in filename or '/' in filename or '\\' in filename:
            abort(403, description="잘못된 파일 요청입니다.")
        
        filepath = os.path.join(UPLOAD_DIR, filename)
        
        # 파일 존재 확인
        if not os.path.exists(filepath):
            try:
                import sys
                sys.stderr.write(f"File not found: {filepath}\n")
            except Exception:
                pass
            abort(404, description="파일을 찾을 수 없습니다.")
        
        # MIME 타입 자동 감지
        import mimetypes
        mimetype = mimetypes.guess_type(filepath)[0] or 'application/octet-stream'
        
        # 파일 제공 (브라우저에서 직접 열기)
        # 세션 쿠키를 명시적으로 설정하여 응답에 포함
        response = send_file(
            filepath, 
            mimetype=mimetype,
            as_attachment=False,
            download_name=filename
        )
        
        return response
    except Exception as e:
        try:
            import sys
            import traceback
            sys.stderr.write(f"File download error: {e}\n")
            sys.stderr.write(f"Traceback: {traceback.format_exc()}\n")
        except Exception:
            pass
        abort(500, description="파일을 불러오는 중 오류가 발생했습니다.")


@app.route('/terms')
@login_required
def terms():
    ensure_initialized()  # Ensure initialization
    return render_template('terms.html')


@app.route('/terms/guide.pdf')
@login_required
def terms_guide_pdf():
    """상품안내 PDF를 브라우저에 표시 (inline)"""
    try:
        pdf_path = os.path.join(BASE_DIR, '@중고차매매업자자동차보험_상품안내_부산.pdf')
        return send_file(pdf_path, mimetype='application/pdf')
    except Exception:
        flash('안내 문서를 불러올 수 없습니다.', 'danger')
        return redirect(url_for('terms'))


@app.route('/terms/policy/download')
@login_required
def terms_policy_download():
    """약관 PDF 다운로드"""
    try:
        pdf_path = os.path.join(BASE_DIR, '중고차 매매업자 자동차보험 약관.pdf')
        return send_file(pdf_path, as_attachment=True, download_name='중고차_매매업자_자동차보험_약관.pdf', mimetype='application/pdf')
    except Exception:
        flash('약관 파일을 다운로드할 수 없습니다.', 'danger')
        return redirect(url_for('terms'))


def parse_date(value: str):
    if not value:
        return None
    try:
        return datetime.strptime(value, '%Y-%m-%d').date()
    except Exception:
        return None


def parse_datetime(value: str):
    if not value:
        return None
    try:
        dt = datetime.strptime(value, '%Y-%m-%d %H:%M')
        return _ensure_aware(dt)
    except ValueError:
        try:
            dt = datetime.strptime(value, '%Y-%m-%d')
            return _ensure_aware(dt)
        except Exception:
            return None
    except Exception:
        return None


@app.route('/insurance', methods=['GET', 'POST'])
@login_required
def insurance():
    ensure_initialized()  # Ensure initialization
    if request.method == 'POST':
        action = request.form.get('action')
        
        if action == 'save' or action == 'delete':
            # 저장/삭제 작업
            row_id = request.form.get('row_id')
            if row_id:
                row = db.session.get(InsuranceApplication, int(row_id))
                if row and row.created_by_member_id == current_user.id:
                    if action == 'delete':
                        if not row.approved_at:  # 조합승인 전까지만 삭제 가능
                            try:
                                row_id = row.id
                                db.session.delete(row)
                                
                                try:
                                    import sys
                                    sys.stderr.write(f"Insurance delete (/insurance): Deleting application ID {row_id}\n")
                                except Exception:
                                    pass
                                
                                commit_success = safe_commit()
                                
                                if commit_success:
                                    try:
                                        import sys
                                        sys.stderr.write(f"Insurance delete (/insurance): Successfully deleted ID {row_id}\n")
                                    except Exception:
                                        pass
                                    flash('삭제되었습니다.', 'success')
                                else:
                                    try:
                                        import sys
                                        import traceback
                                        sys.stderr.write(f"Insurance delete (/insurance): Commit failed for ID {row_id}\n")
                                        sys.stderr.write(f"Insurance delete traceback: {traceback.format_exc()}\n")
                                    except Exception:
                                        pass
                                    flash('삭제 처리 중 오류가 발생했습니다.', 'danger')
                            except Exception as e:
                                try:
                                    import sys
                                    import traceback
                                    sys.stderr.write(f"Insurance deletion error: {e}\n")
                                    sys.stderr.write(f"Insurance deletion traceback: {traceback.format_exc()}\n")
                                except Exception:
                                    pass
                                try:
                                    db.session.rollback()
                                except Exception:
                                    pass
                                flash('삭제 처리 중 오류가 발생했습니다.', 'danger')
                        else:
                            flash('조합승인 후에는 삭제할 수 없습니다.', 'warning')
                    elif action == 'save':
                        if not row.approved_at:  # 조합승인 전까지만 수정 가능
                            try:
                                row_id = row.id
                                # 편집 모드에서 온 경우에만 모든 필드 업데이트
                                if request.form.get('desired_start_date'):
                                    row.desired_start_date = parse_date(request.form.get('desired_start_date'))
                                    row.car_plate = request.form.get('car_plate', '').strip()
                                    row.vin = request.form.get('vin', '').strip()
                                    row.car_name = request.form.get('car_name', '').strip()
                                    row.car_registered_at = parse_date(request.form.get('car_registered_at'))
                                # 비고는 항상 업데이트 가능
                                row.memo = request.form.get('memo', '').strip()
                                
                                try:
                                    import sys
                                    sys.stderr.write(f"Insurance save (/insurance): Updating application ID {row_id}\n")
                                except Exception:
                                    pass
                                
                                commit_success = safe_commit()
                                
                                if commit_success:
                                    try:
                                        import sys
                                        verify_app = db.session.get(InsuranceApplication, row_id)
                                        if verify_app:
                                            sys.stderr.write(f"Insurance save (/insurance): Verified application ID {row_id} exists\n")
                                        else:
                                            sys.stderr.write(f"Insurance save (/insurance): WARNING - Application ID {row_id} not found after commit!\n")
                                    except Exception:
                                        pass
                                    flash('저장되었습니다.', 'success')
                                else:
                                    try:
                                        import sys
                                        import traceback
                                        sys.stderr.write(f"Insurance save (/insurance): Commit failed for ID {row_id}\n")
                                        sys.stderr.write(f"Insurance save traceback: {traceback.format_exc()}\n")
                                    except Exception:
                                        pass
                                    flash('저장 처리 중 오류가 발생했습니다.', 'danger')
                            except Exception as e:
                                try:
                                    import sys
                                    import traceback
                                    sys.stderr.write(f"Insurance update error: {e}\n")
                                    sys.stderr.write(f"Insurance update traceback: {traceback.format_exc()}\n")
                                except Exception:
                                    pass
                                try:
                                    db.session.rollback()
                                except Exception:
                                    pass
                                flash('저장 처리 중 오류가 발생했습니다.', 'danger')
                        else:
                            flash('조합승인 후에는 수정할 수 없습니다.', 'warning')
            return redirect(url_for('insurance'))
        
        # 신규 가입
        desired_start_date = parse_date(request.form.get('desired_start_date'))
        car_plate = request.form.get('car_plate', '').strip()
        vin = request.form.get('vin', '').strip()
        car_name = request.form.get('car_name', '').strip()
        car_registered_at = parse_date(request.form.get('car_registered_at'))
        memo = request.form.get('memo', '').strip()

        try:
            app_row = InsuranceApplication(
                desired_start_date=desired_start_date,
                insured_code=current_user.business_number or '',
                contractor_code='부산자동차매매사업자조합',
                car_plate=car_plate,
                vin=vin,
                car_name=car_name,
                car_registered_at=car_registered_at,
                premium=9500,
                status='신청',
                memo=memo,
                created_by_member_id=current_user.id,
            )
            db.session.add(app_row)
            
            try:
                import sys
                sys.stderr.write(f"Insurance application (/insurance): Adding {car_plate} to session\n")
            except Exception:
                pass
            
            commit_success = safe_commit()
            
            if not commit_success:
                try:
                    import sys
                    import traceback
                    sys.stderr.write(f"Insurance application (/insurance): Commit failed for {car_plate}\n")
                    sys.stderr.write(f"Insurance application traceback: {traceback.format_exc()}\n")
                except Exception:
                    pass
                flash('신청 처리 중 오류가 발생했습니다. 다시 시도해주세요.', 'danger')
                return redirect(url_for('insurance'))
            
            # 커밋 성공 후 검증
            try:
                import sys
                verify_app = db.session.get(InsuranceApplication, app_row.id)
                if verify_app:
                    sys.stderr.write(f"Insurance application (/insurance): Verified {car_plate} (ID: {app_row.id}) exists\n")
                else:
                    sys.stderr.write(f"Insurance application (/insurance): WARNING - {car_plate} not found after commit!\n")
            except Exception:
                pass
            
            flash('신청이 등록되었습니다.', 'success')
            return redirect(url_for('insurance'))
        except Exception as e:
            try:
                import sys
                import traceback
                sys.stderr.write(f"Insurance application (/insurance) error: {e}\n")
                sys.stderr.write(f"Insurance application traceback: {traceback.format_exc()}\n")
            except Exception:
                pass
            try:
                db.session.rollback()
            except Exception:
                pass
            flash('신청 처리 중 오류가 발생했습니다. 다시 시도해주세요.', 'danger')
            return redirect(url_for('insurance'))

    # 검색
    start_date = parse_date(request.args.get('start_date', ''))
    end_date = parse_date(request.args.get('end_date', ''))
    edit_id = request.args.get('edit_id')  # 편집 모드

    if db is None:
        flash('데이터베이스가 초기화되지 않았습니다.', 'danger')
        return redirect(url_for('dashboard'))
    q = db.session.query(InsuranceApplication).filter_by(created_by_member_id=current_user.id)
    if start_date:
        q = q.filter(InsuranceApplication.desired_start_date >= start_date)
    if end_date:
        q = q.filter(InsuranceApplication.desired_start_date <= end_date)

    rows = q.order_by(InsuranceApplication.created_at.desc()).all()
    # 상태 재계산
    changed = False
    for r in rows:
        old_status = r.status
        r.recompute_status()
        if r.status != old_status:
            changed = True
    if changed:
        safe_commit()  # Don't show error if status update fails, just log it

    # Build view models with proper timezone formatting
    def fmt_display_safe(dt):
        if not dt:
            return ''
        try:
            # Ensure timezone-aware datetime and convert to KST
            if dt.tzinfo is None:
                # If naive, assume it's already in KST
                local_dt = dt.replace(tzinfo=KST)
            else:
                # Convert to KST
                local_dt = dt.astimezone(KST)
            return local_dt.strftime('%Y-%m-%d %H:%M')
        except Exception:
            return ''

    items = []
    for r in rows:
        items.append({
            'id': r.id,
            'created_at_str': fmt_display_safe(r.created_at),
            'start_at_str': fmt_display_safe(r.start_at),
            'end_at_str': fmt_display_safe(r.end_at),
            'approved_at_str': fmt_display_safe(r.approved_at),
        })

    return render_template('insurance.html', rows=rows, items=items, edit_id=edit_id)


@app.route('/insurance/template')
@login_required
def insurance_template_download():
    # Import pandas only when needed
    import pandas as pd
    
    # Create Excel template in-memory
    df = pd.DataFrame([
        {
            '가입희망일자(YYYY-MM-DD)': '',
            '한글차량번호': '',
            '차대번호': '',
            '차량명': '',
            '차량등록일자(YYYY-MM-DD)': '',
            '비고': '',
        }
    ])
    buffer = BytesIO()
    with pd.ExcelWriter(buffer, engine='openpyxl') as writer:
        df.to_excel(writer, index=False, sheet_name='template')
    buffer.seek(0)
    return send_file(
        buffer,
        as_attachment=True,
        download_name='insurance_upload_template.xlsx',
        mimetype='application/vnd.openxmlformats-officedocument.spreadsheetml.sheet',
    )


@app.route('/insurance/upload', methods=['POST'])
@login_required
def insurance_upload():
    if is_serverless:
        flash('Vercel 환경에서는 엑셀 업로드가 제한됩니다.', 'warning')
        return redirect(url_for('insurance'))
    file = request.files.get('file')
    if not file:
        flash('파일을 선택하세요.', 'warning')
        return redirect(url_for('insurance'))
    try:
        # Import pandas only when needed
        import pandas as pd
        df = pd.read_excel(file)
        required_cols = {
            '가입희망일자(YYYY-MM-DD)',
            '한글차량번호',
            '차대번호',
            '차량명',
            '차량등록일자(YYYY-MM-DD)'
        }
        if not required_cols.issubset(set(df.columns)):
            flash('엑셀 양식이 올바르지 않습니다.', 'danger')
            return redirect(url_for('insurance'))
        count = 0
        for _, row in df.iterrows():
            desired_start_date = parse_date(str(row.get('가입희망일자(YYYY-MM-DD)', '')).strip())
            car_plate = str(row.get('한글차량번호', '')).strip()
            vin = str(row.get('차대번호', '')).strip()
            car_name = str(row.get('차량명', '')).strip()
            car_registered_at = parse_date(str(row.get('차량등록일자(YYYY-MM-DD)', '')).strip())
            memo = str(row.get('비고', '')).strip() if '비고' in df.columns else None
            if not desired_start_date or not car_plate:
                continue
            app_row = InsuranceApplication(
                desired_start_date=desired_start_date,
                insured_code=current_user.business_number or '',
                contractor_code='부산자동차매매사업자조합',
                car_plate=car_plate,
                vin=vin,
                car_name=car_name,
                car_registered_at=car_registered_at,
                premium=9500,
                status='신청',
                memo=memo,
                created_by_member_id=current_user.id,
            )
            db.session.add(app_row)
            count += 1
        if not safe_commit():
            flash('업로드 처리 중 오류가 발생했습니다. 다시 시도해주세요.', 'danger')
        else:
            flash(f'{count}건 업로드되었습니다.', 'success')
    except Exception as e:
        flash('업로드 중 오류가 발생했습니다.', 'danger')
    return redirect(url_for('insurance'))


# 파트너그룹 관리 (요구사항의 파트너그룹만들기 페이지)
@app.route('/admin/partner-groups', methods=['GET', 'POST'])
@login_required
@admin_required
def admin_partner_groups():
    ensure_initialized()
    
    if request.method == 'POST':
        action = request.form.get('action')
        
        if action == 'create':
            # 신규 파트너그룹 생성
            name = request.form.get('name', '').strip()
            admin_username = request.form.get('admin_username', '').strip()
            admin_password = request.form.get('admin_password', '').strip()
            business_number = request.form.get('business_number', '').strip()
            representative = request.form.get('representative', '').strip()
            phone = request.form.get('phone', '').strip()
            mobile = request.form.get('mobile', '').strip()
            address = request.form.get('address', '').strip()
            bank_name = request.form.get('bank_name', '').strip()
            account_number = request.form.get('account_number', '').strip()
            memo = request.form.get('memo', '').strip()
            
            # 필수 항목 검증
            if not name or not business_number or not representative or not phone:
                flash('파트너그룹명, 사업자등록번호, 대표자, 유선번호는 필수항목입니다.', 'warning')
                return redirect(url_for('admin_partner_groups'))
            
            # 중복 검증
            if db.session.query(PartnerGroup).filter(
                (PartnerGroup.name == name) | 
                (PartnerGroup.business_number == business_number) |
                (PartnerGroup.admin_username == admin_username)
            ).first():
                flash('이미 존재하는 파트너그룹명, 사업자번호 또는 관리자 아이디입니다.', 'danger')
                return redirect(url_for('admin_partner_groups'))
            
            # 파일 업로드 처리
            registration_cert_path = None
            logo_path = None
            
            # 사업자등록증 파일 처리
            if 'registration_cert' in request.files:
                file = request.files['registration_cert']
                file_filename = file.filename if file else None
                print(f"Registration cert file: {file}, filename: {file_filename}")
                
                # 파일이 실제로 선택되었는지 확인 (빈 파일이 아닌지)
                if file and file_filename and file_filename.strip():
                    try:
                        filename = secure_filename(file.filename)
                        file_ext = os.path.splitext(filename)[1].lower()
                        allowed_extensions = ['.pdf', '.jpg', '.jpeg', '.png']
                        
                        print(f"Processing registration cert: {filename}, ext: {file_ext}")
                        if file_ext in allowed_extensions:
                            timestamp = datetime.now(KST).strftime('%Y%m%d_%H%M%S')
                            new_filename = f"reg_{business_number}_{timestamp}{file_ext}"
                            filepath = os.path.join(UPLOAD_DIR, new_filename)
                            print(f"Saving registration cert to: {filepath}")
                            
                            # 디렉토리가 존재하는지 확인
                            os.makedirs(UPLOAD_DIR, exist_ok=True)
                            
                            # 파일 저장
                            file.save(filepath)
                            
                            # 파일이 실제로 저장되었는지 확인
                            if os.path.exists(filepath) and os.path.getsize(filepath) > 0:
                                registration_cert_path = os.path.join('uploads', new_filename)
                                print(f"Registration cert saved successfully: {registration_cert_path}")
                            else:
                                print(f"ERROR: Registration cert file was not saved properly. Path: {filepath}")
                                registration_cert_path = None
                        else:
                            print(f"Invalid file extension for registration cert: {file_ext}")
                    except Exception as e:
                        try:
                            import sys
                            import traceback
                            sys.stderr.write(f"Registration cert upload error: {e}\n")
                            sys.stderr.write(f"Traceback: {traceback.format_exc()}\n")
                        except Exception:
                            pass
            else:
                print("No registration_cert file in request")
            
            # 로고 파일 처리
            if 'logo' in request.files:
                file = request.files['logo']
                file_filename = file.filename if file else None
                print(f"Logo file: {file}, filename: {file_filename}")
                
                # 파일이 실제로 선택되었는지 확인 (빈 파일이 아닌지)
                if file and file_filename and file_filename.strip():
                    try:
                        filename = secure_filename(file.filename)
                        file_ext = os.path.splitext(filename)[1].lower()
                        allowed_extensions = ['.jpg', '.jpeg', '.png', '.gif']
                        
                        print(f"Processing logo: {filename}, ext: {file_ext}")
                        if file_ext in allowed_extensions:
                            timestamp = datetime.now(KST).strftime('%Y%m%d_%H%M%S')
                            new_filename = f"logo_{business_number}_{timestamp}{file_ext}"
                            
                            # 파트너그룹 로고 디렉토리에 저장
                            partner_logo_dir = os.path.join('static', 'partner_logos')
                            os.makedirs(partner_logo_dir, exist_ok=True)
                            filepath = os.path.join(partner_logo_dir, new_filename)
                            print(f"Saving logo to: {filepath}")
                            
                            # 파일 저장
                            file.save(filepath)
                            
                            # 파일이 실제로 저장되었는지 확인
                            if os.path.exists(filepath) and os.path.getsize(filepath) > 0:
                                logo_path = os.path.join('partner_logos', new_filename)
                                print(f"Logo saved successfully: {logo_path}")
                            else:
                                print(f"ERROR: Logo file was not saved properly. Path: {filepath}")
                                logo_path = None
                        else:
                            print(f"Invalid file extension for logo: {file_ext}")
                    except Exception as e:
                        try:
                            import sys
                            import traceback
                            sys.stderr.write(f"Logo upload error: {e}\n")
                            sys.stderr.write(f"Traceback: {traceback.format_exc()}\n")
                        except Exception:
                            pass
            else:
                print("No logo file in request")

            try:
                print(f"Creating partner group with files:")
                print(f"  Registration cert path: {registration_cert_path}")
                print(f"  Logo path: {logo_path}")
                
                partner_group = PartnerGroup(
                    name=name,
                    admin_username=admin_username,
                    business_number=business_number,
                    representative=representative,
                    phone=phone,
                    mobile=mobile,
                    address=address,
                    bank_name=bank_name,
                    account_number=account_number,
                    registration_cert_path=registration_cert_path,
                    logo_path=logo_path,
                    memo=memo
                )
                partner_group.set_admin_password(admin_password)
                db.session.add(partner_group)
                print(f"Partner group added to session: {name}")
                
                try:
                    import sys
                    sys.stderr.write(f"Partner group create: Adding {name} to session\n")
                except Exception:
                    pass
                
                commit_success = safe_commit()
                
                if not commit_success:
                    try:
                        import sys
                        import traceback
                        sys.stderr.write(f"Partner group create: Commit failed for {name}\n")
                        sys.stderr.write(f"Partner group create traceback: {traceback.format_exc()}\n")
                    except Exception:
                        pass
                    flash('파트너그룹 생성 중 오류가 발생했습니다.', 'danger')
                    print("ERROR: Failed to commit partner group to database")
                else:
                    # 커밋 성공 후 검증
                    try:
                        import sys
                        verify_group = db.session.get(PartnerGroup, partner_group.id)
                        if verify_group:
                            sys.stderr.write(f"Partner group create: Verified {name} (ID: {partner_group.id}) exists\n")
                        else:
                            sys.stderr.write(f"Partner group create: WARNING - {name} not found after commit!\n")
                    except Exception:
                        pass
                    # 데이터베이스에 저장된 정보 확인
                    db.session.refresh(partner_group)
                    print(f"Partner group saved successfully:")
                    print(f"  ID: {partner_group.id}")
                    print(f"  Registration cert path: {partner_group.registration_cert_path}")
                    print(f"  Logo path: {partner_group.logo_path}")
                    
                    # 로고가 업로드된 경우 파트너그룹별 로고 파일도 생성
                    if logo_path and 'logo' in request.files:
                        try:
                            file = request.files['logo']
                            file_ext = os.path.splitext(secure_filename(file.filename))[1].lower()
                            group_logo_filename = f"group_{partner_group.id}_logo{file_ext}"
                            partner_logo_dir = os.path.join('static', 'partner_logos')
                            group_logo_path = os.path.join(partner_logo_dir, group_logo_filename)
                            file.seek(0)  # 파일 포인터 리셋
                            file.save(group_logo_path)
                        except Exception as e:
                            try:
                                import sys
                                sys.stderr.write(f"Group logo creation error: {e}\n")
                            except Exception:
                                pass
                    
                    flash(f'파트너그룹 "{name}"이 생성되었습니다.', 'success')
            except Exception as e:
                try:
                    import sys
                    sys.stderr.write(f"Partner group creation error: {e}\n")
                except Exception:
                    pass
                flash('파트너그룹 생성 중 오류가 발생했습니다.', 'danger')
                
        elif action in ['save', 'delete']:
            # 수정/삭제
            group_id = request.form.get('group_id')
            if group_id:
                group = db.session.get(PartnerGroup, int(group_id))
                if group:
                    if action == 'delete':
                        try:
                            group_id = group.id
                            group_name = group.name
                            db.session.delete(group)
                            
                            try:
                                import sys
                                sys.stderr.write(f"Partner group delete: Deleting {group_name} (ID: {group_id})\n")
                            except Exception:
                                pass
                            
                            commit_success = safe_commit()
                            
                            if commit_success:
                                try:
                                    import sys
                                    sys.stderr.write(f"Partner group delete: Successfully deleted {group_name}\n")
                                except Exception:
                                    pass
                                flash('파트너그룹이 삭제되었습니다.', 'success')
                            else:
                                try:
                                    import sys
                                    import traceback
                                    sys.stderr.write(f"Partner group delete: Commit failed for {group_name}\n")
                                    sys.stderr.write(f"Partner group delete traceback: {traceback.format_exc()}\n")
                                except Exception:
                                    pass
                                flash('삭제 처리 중 오류가 발생했습니다.', 'danger')
                        except Exception as e:
                            try:
                                import sys
                                import traceback
                                sys.stderr.write(f"Partner group deletion error: {e}\n")
                                sys.stderr.write(f"Partner group deletion traceback: {traceback.format_exc()}\n")
                            except Exception:
                                pass
                            try:
                                db.session.rollback()
                            except Exception:
                                pass
                            flash('삭제 처리 중 오류가 발생했습니다.', 'danger')
                    elif action == 'save':
                        try:
                            group.name = request.form.get('name', '').strip()
                            group.admin_username = request.form.get('admin_username', '').strip()
                            if request.form.get('admin_password', '').strip():
                                group.set_admin_password(request.form.get('admin_password', '').strip())
                            group.business_number = request.form.get('business_number', '').strip()
                            group.representative = request.form.get('representative', '').strip()
                            group.phone = request.form.get('phone', '').strip()
                            group.mobile = request.form.get('mobile', '').strip()
                            group.address = request.form.get('address', '').strip()
                            group.bank_name = request.form.get('bank_name', '').strip()
                            group.account_number = request.form.get('account_number', '').strip()
                            group.memo = request.form.get('memo', '').strip()
                            
                            # 파일 업데이트 처리
                            # 사업자등록증 파일 처리
                            if 'registration_cert' in request.files:
                                file = request.files['registration_cert']
                                if file and file.filename:
                                    try:
                                        filename = secure_filename(file.filename)
                                        file_ext = os.path.splitext(filename)[1].lower()
                                        allowed_extensions = ['.pdf', '.jpg', '.jpeg', '.png']
                                        
                                        if file_ext in allowed_extensions:
                                            timestamp = datetime.now(KST).strftime('%Y%m%d_%H%M%S')
                                            new_filename = f"reg_{group.business_number}_{timestamp}{file_ext}"
                                            filepath = os.path.join(UPLOAD_DIR, new_filename)
                                            file.save(filepath)
                                            group.registration_cert_path = os.path.join('uploads', new_filename)
                                    except Exception as e:
                                        try:
                                            import sys
                                            sys.stderr.write(f"Registration cert update error: {e}\n")
                                        except Exception:
                                            pass
                            
                            # 로고 파일 처리
                            if 'logo' in request.files:
                                file = request.files['logo']
                                if file and file.filename:
                                    try:
                                        filename = secure_filename(file.filename)
                                        file_ext = os.path.splitext(filename)[1].lower()
                                        allowed_extensions = ['.jpg', '.jpeg', '.png', '.gif']
                                        
                                        if file_ext in allowed_extensions:
                                            timestamp = datetime.now(KST).strftime('%Y%m%d_%H%M%S')
                                            new_filename = f"logo_{group.business_number}_{timestamp}{file_ext}"
                                            
                                            # 파트너그룹 로고 디렉토리에 저장
                                            partner_logo_dir = os.path.join('static', 'partner_logos')
                                            os.makedirs(partner_logo_dir, exist_ok=True)
                                            filepath = os.path.join(partner_logo_dir, new_filename)
                                            file.save(filepath)
                                            group.logo_path = os.path.join('partner_logos', new_filename)
                                            
                                            # 파트너그룹별 로고 파일도 생성 (group_{id}_logo.png)
                                            try:
                                                group_logo_filename = f"group_{group.id}_logo{file_ext}"
                                                group_logo_path = os.path.join(partner_logo_dir, group_logo_filename)
                                                file.seek(0)  # 파일 포인터 리셋
                                                file.save(group_logo_path)
                                            except Exception as e:
                                                try:
                                                    import sys
                                                    sys.stderr.write(f"Group logo copy error: {e}\n")
                                                except Exception:
                                                    pass
                                    except Exception as e:
                                        try:
                                            import sys
                                            sys.stderr.write(f"Logo update error: {e}\n")
                                        except Exception:
                                            pass
                            
                            try:
                                import sys
                                sys.stderr.write(f"Partner group save: Updating {group_name} (ID: {group_id})\n")
                            except Exception:
                                pass
                            
                            commit_success = safe_commit()
                            
                            if commit_success:
                                # 커밋 성공 후 검증
                                try:
                                    import sys
                                    verify_group = db.session.get(PartnerGroup, group_id)
                                    if verify_group:
                                        sys.stderr.write(f"Partner group save: Verified {group_name} (ID: {group_id}) exists\n")
                                    else:
                                        sys.stderr.write(f"Partner group save: WARNING - {group_name} not found after commit!\n")
                                except Exception:
                                    pass
                                flash('파트너그룹 정보가 저장되었습니다.', 'success')
                            else:
                                try:
                                    import sys
                                    import traceback
                                    sys.stderr.write(f"Partner group save: Commit failed for {group_name}\n")
                                    sys.stderr.write(f"Partner group save traceback: {traceback.format_exc()}\n")
                                except Exception:
                                    pass
                                flash('저장 처리 중 오류가 발생했습니다.', 'danger')
                        except Exception as e:
                            try:
                                import sys
                                import traceback
                                sys.stderr.write(f"Partner group update error: {e}\n")
                                sys.stderr.write(f"Partner group update traceback: {traceback.format_exc()}\n")
                            except Exception:
                                pass
                            try:
                                db.session.rollback()
                            except Exception:
                                pass
                            flash('저장 처리 중 오류가 발생했습니다.', 'danger')
        
        return redirect(url_for('admin_partner_groups'))
    
    # GET 요청: 파트너그룹 목록 조회
    edit_id = request.args.get('edit_id')
    partner_groups = []
    if db is not None:
        try:
            partner_groups = db.session.query(PartnerGroup).order_by(PartnerGroup.created_at.desc()).all()
        except Exception as e:
            try:
                import sys
                sys.stderr.write(f"Error fetching partner groups: {e}\n")
            except Exception:
                pass
    
    return render_template('admin/partner_groups.html', 
                         partner_groups=partner_groups, 
                         edit_id=edit_id)

# 전체책임보험현황 (요구사항의 전체책임보험현황페이지)
@app.route('/admin/insurance-overview', methods=['GET', 'POST'])
@admin_required
def admin_insurance_overview():
    ensure_initialized()
    
    edit_id = request.args.get('edit_id', '')
    
    if request.method == 'POST':
        action = request.form.get('action')
        application_id = request.form.get('application_id')
        
        if not application_id:
            flash('신청 ID가 필요합니다.', 'danger')
            return redirect(url_for('admin_insurance_overview'))
        
        try:
            app = db.session.query(InsuranceApplication).filter_by(id=int(application_id)).first()
            if not app:
                flash('보험 신청을 찾을 수 없습니다.', 'danger')
                return redirect(url_for('admin_insurance_overview'))
            
            if action == 'save':
                # 수정 저장
                app.desired_start_date = parse_date(request.form.get('desired_start_date', '')) or app.desired_start_date
                app.car_plate = request.form.get('car_plate', '').strip() or app.car_plate
                app.vin = request.form.get('vin', '').strip() or app.vin
                app.car_name = request.form.get('car_name', '').strip() or app.car_name
                app.car_registered_at = parse_date(request.form.get('car_registered_at', '')) or app.car_registered_at
                app.insured_code = request.form.get('insured_code', '').strip() or app.insured_code
                app.contractor_code = request.form.get('contractor_code', '').strip() or app.contractor_code
                app.premium = float(request.form.get('premium', 0) or 0) or app.premium
                app.memo = request.form.get('memo', '').strip() or app.memo
                
                # 가입시간 처리
                start_at_str = request.form.get('start_at', '').strip()
                if start_at_str:
                    try:
                        app.start_at = datetime.strptime(start_at_str, '%Y-%m-%dT%H:%M').replace(tzinfo=KST)
                    except ValueError:
                        pass
                
                # 종료시간 처리
                end_at_str = request.form.get('end_at', '').strip()
                if end_at_str:
                    try:
                        app.end_at = datetime.strptime(end_at_str, '%Y-%m-%dT%H:%M').replace(tzinfo=KST)
                    except ValueError:
                        pass
                
                # 조합승인시간 처리
                approved_at_str = request.form.get('approved_at', '').strip()
                if approved_at_str:
                    try:
                        app.approved_at = datetime.strptime(approved_at_str, '%Y-%m-%dT%H:%M').replace(tzinfo=KST)
                        app.status = '조합승인'
                    except ValueError:
                        pass
                
                db.session.add(app)
                if safe_commit():
                    flash('보험 신청 정보가 저장되었습니다.', 'success')
                else:
                    flash('저장 중 오류가 발생했습니다.', 'danger')
            
            elif action == 'delete':
                # 삭제
                db.session.delete(app)
                if safe_commit():
                    flash('보험 신청이 삭제되었습니다.', 'success')
                else:
                    flash('삭제 중 오류가 발생했습니다.', 'danger')
        
        except Exception as e:
            flash(f'처리 중 오류가 발생했습니다: {str(e)}', 'danger')
        
        # 검색 조건 유지하며 리다이렉트
        params = {}
        for key in ['start_date', 'end_date', 'partner_group_id', 'company_name', 'status_filter']:
            if request.args.get(key):
                params[key] = request.args.get(key)
        return redirect(url_for('admin_insurance_overview', **params))
    
    # 검색 조건
    start_date = parse_date(request.args.get('start_date', ''))
    end_date = parse_date(request.args.get('end_date', ''))
    partner_group_id = request.args.get('partner_group_id', '')
    company_name = request.args.get('company_name', '').strip()
    status_filter = request.args.get('status_filter', '')  # 가입완료/미가입
    
    if db is None:
        flash('데이터베이스가 초기화되지 않았습니다.', 'danger')
        return redirect(url_for('admin_dashboard'))
    
    # 파트너그룹 목록
    partner_groups = []
    try:
        partner_groups = db.session.query(PartnerGroup).order_by(PartnerGroup.name).all()
    except Exception:
        pass
    
    # 선택된 파트너그룹의 회원사 목록 (상사명 필터용)
    members = []
    if partner_group_id:
        try:
            members = db.session.query(Member).filter_by(
                partner_group_id=int(partner_group_id),
                role='member'
            ).order_by(Member.company_name).all()
        except (ValueError, Exception):
            pass
    
    # 보험신청 데이터 조회
    q = db.session.query(InsuranceApplication)
    
    if start_date:
        q = q.filter(InsuranceApplication.created_at >= datetime.combine(start_date, datetime.min.time(), tzinfo=KST))
    if end_date:
        q = q.filter(InsuranceApplication.created_at <= datetime.combine(end_date, datetime.max.time(), tzinfo=KST))
    if partner_group_id:
        try:
            q = q.filter(InsuranceApplication.partner_group_id == int(partner_group_id))
        except ValueError:
            pass
    if company_name:
        q = q.join(Member).filter(Member.company_name.like(f'%{company_name}%'))
    if status_filter == '가입완료':
        q = q.filter(InsuranceApplication.start_at.is_not(None))
    elif status_filter == '미가입':
        q = q.filter(InsuranceApplication.start_at.is_(None))
    
    applications = q.order_by(InsuranceApplication.created_at.desc()).all()
    
    # 상태 재계산
    for app in applications:
        app.recompute_status()
    safe_commit()
    
    return render_template('admin/insurance_overview.html',
                         applications=applications,
                         partner_groups=partner_groups,
                         members=members,
                         start_date=start_date,
                         end_date=end_date,
                         partner_group_id=partner_group_id,
                         company_name=company_name,
                         status_filter=status_filter,
                         edit_id=edit_id)

# 전체책임보험현황 회원사 목록 API
@app.route('/admin/insurance-overview/members')
@admin_required
def admin_insurance_overview_members():
    ensure_initialized()
    partner_group_id = request.args.get('partner_group_id', '')
    
    if not partner_group_id:
        return jsonify({'members': []})
    
    try:
        members = db.session.query(Member).filter_by(
            partner_group_id=int(partner_group_id),
            role='member'
        ).order_by(Member.company_name).all()
        
        return jsonify({
            'members': [{'company_name': m.company_name} for m in members]
        })
    except Exception as e:
        return jsonify({'members': [], 'error': str(e)})

# 전체책임보험현황 엑셀 다운로드
@app.route('/admin/insurance-overview/export')
@admin_required
def admin_insurance_overview_export():
    ensure_initialized()
    import pandas as pd
    from io import BytesIO
    
    # 검색 조건 (동일한 필터 적용)
    start_date = parse_date(request.args.get('start_date', ''))
    end_date = parse_date(request.args.get('end_date', ''))
    partner_group_id = request.args.get('partner_group_id', '')
    company_name = request.args.get('company_name', '').strip()
    status_filter = request.args.get('status_filter', '')
    
    if db is None:
        flash('데이터베이스가 초기화되지 않았습니다.', 'danger')
        return redirect(url_for('admin_insurance_overview'))
    
    # 보험신청 데이터 조회 (동일한 필터)
    q = db.session.query(InsuranceApplication)
    
    if start_date:
        q = q.filter(InsuranceApplication.created_at >= datetime.combine(start_date, datetime.min.time(), tzinfo=KST))
    if end_date:
        q = q.filter(InsuranceApplication.created_at <= datetime.combine(end_date, datetime.max.time(), tzinfo=KST))
    if partner_group_id:
        try:
            q = q.filter(InsuranceApplication.partner_group_id == int(partner_group_id))
        except ValueError:
            pass
    if company_name:
        q = q.join(Member).filter(Member.company_name.like(f'%{company_name}%'))
    if status_filter == '가입완료':
        q = q.filter(InsuranceApplication.start_at.is_not(None))
    elif status_filter == '미가입':
        q = q.filter(InsuranceApplication.start_at.is_(None))
    
    applications = q.order_by(InsuranceApplication.created_at.desc()).all()
    
    # 엑셀 데이터 생성
    data = []
    for app in applications:
        partner_group_name = ''
        if app.partner_group_id:
            try:
                pg = db.session.query(PartnerGroup).filter_by(id=app.partner_group_id).first()
                if pg:
                    partner_group_name = pg.name
            except Exception:
                pass
        
        data.append({
            '순': len(data) + 1,
            '파트너그룹(상호)': partner_group_name,
            '상사명': app.created_by_member.company_name if app.created_by_member else '',
            '신청시간': app.created_at.strftime('%Y-%m-%d %H:%M:%S') if app.created_at else '',
            '가입희망일자': app.desired_start_date.strftime('%Y-%m-%d') if app.desired_start_date else '',
            '가입시간': app.start_at.strftime('%Y-%m-%d %H:%M:%S') if app.start_at else '',
            '종료시간': app.end_at.strftime('%Y-%m-%d %H:%M:%S') if app.end_at else '',
            '조합승인시간': app.approved_at.strftime('%Y-%m-%d %H:%M:%S') if app.approved_at else '',
            '피보험자코드': app.insured_code or '',
            '계약자코드': app.contractor_code or '',
            '한글차량번호': app.car_plate or '',
            '차대번호': app.vin or '',
            '차량명': app.car_name or '',
            '차량등록일자': app.car_registered_at.strftime('%Y-%m-%d') if app.car_registered_at else '',
            '보험료': app.premium or 0,
            '보험증권': '있음' if (app.insurance_policy_path or app.insurance_policy_url) else '없음',
            '비고': app.memo or '',
        })
    
    df = pd.DataFrame(data)
    buffer = BytesIO()
    with pd.ExcelWriter(buffer, engine='openpyxl') as writer:
        df.to_excel(writer, index=False, sheet_name='전체책임보험현황')
    buffer.seek(0)
    
    filename = f'전체책임보험현황_{datetime.now(KST).strftime("%Y%m%d_%H%M%S")}.xlsx'
    return send_file(
        buffer,
        as_attachment=True,
        download_name=filename,
        mimetype='application/vnd.openxmlformats-officedocument.spreadsheetml.sheet',
    )

# 전체정산페이지 (요구사항의 전체정산페이지)
@app.route('/admin/settlement-overview')
@admin_required
def admin_settlement_overview():
    ensure_initialized()
    
    year = int(request.args.get('year', datetime.now().year))
    month = int(request.args.get('month', datetime.now().month))
    partner_group_id = request.args.get('partner_group_id', '')
    
    if db is None:
        flash('데이터베이스가 초기화되지 않았습니다.', 'danger')
        return redirect(url_for('admin_dashboard'))
    
    # 파트너그룹 목록
    partner_groups = []
    try:
        partner_groups = db.session.query(PartnerGroup).order_by(PartnerGroup.name).all()
    except Exception:
        pass
    
    # 정산 데이터 계산
    start_period = datetime(year, month, 1, tzinfo=KST)
    if month == 12:
        next_month = datetime(year + 1, 1, 1, tzinfo=KST)
    else:
        next_month = datetime(year, month + 1, 1, tzinfo=KST)
    
    q = db.session.query(InsuranceApplication).filter(
        InsuranceApplication.start_at.is_not(None),
        InsuranceApplication.start_at >= start_period,
        InsuranceApplication.start_at < next_month,
    )
    
    if partner_group_id:
        try:
            q = q.filter(InsuranceApplication.partner_group_id == int(partner_group_id))
        except ValueError:
            pass
    
    applications = q.all()
    
    # 파트너그룹별 정산 데이터 집계
    settlements_by_group = {}
    for app in applications:
        if app.partner_group and app.created_by_member:
            group_key = app.partner_group_id
            company_key = (app.created_by_member.company_name, app.created_by_member.representative)
            
            if group_key not in settlements_by_group:
                settlements_by_group[group_key] = {
                    'group_name': app.partner_group.name,
                    'companies': {},
                    'total_count': 0,
                    'total_amount': 0
                }
            
            if company_key not in settlements_by_group[group_key]['companies']:
                settlements_by_group[group_key]['companies'][company_key] = {
                    'company_name': app.created_by_member.company_name,
                    'representative': app.created_by_member.representative,
                    'count': 0,
                    'amount': 0
                }
            
            settlements_by_group[group_key]['companies'][company_key]['count'] += 1
            settlements_by_group[group_key]['companies'][company_key]['amount'] += 95000  # 건수 × 95,000원
            settlements_by_group[group_key]['total_count'] += 1
            settlements_by_group[group_key]['total_amount'] += 95000
    
    # 전체 합계
    grand_total_count = sum(group['total_count'] for group in settlements_by_group.values())
    grand_total_amount = sum(group['total_amount'] for group in settlements_by_group.values())
    
    return render_template('admin/settlement_overview.html',
                         settlements_by_group=settlements_by_group,
                         partner_groups=partner_groups,
                         year=year,
                         month=month,
                         partner_group_id=partner_group_id,
                         grand_total_count=grand_total_count,
                         grand_total_amount=grand_total_amount)

# 전체정산페이지 엑셀 다운로드
@app.route('/admin/settlement-overview/export')
@admin_required
def admin_settlement_overview_export():
    ensure_initialized()
    import pandas as pd
    from io import BytesIO
    
    year = int(request.args.get('year', datetime.now().year))
    month = int(request.args.get('month', datetime.now().month))
    partner_group_id = request.args.get('partner_group_id', '')
    
    if db is None:
        flash('데이터베이스가 초기화되지 않았습니다.', 'danger')
        return redirect(url_for('admin_settlement_overview'))
    
    # 정산 데이터 계산 (동일한 로직)
    start_period = datetime(year, month, 1, tzinfo=KST)
    if month == 12:
        next_month = datetime(year + 1, 1, 1, tzinfo=KST)
    else:
        next_month = datetime(year, month + 1, 1, tzinfo=KST)
    
    q = db.session.query(InsuranceApplication).filter(
        InsuranceApplication.start_at.is_not(None),
        InsuranceApplication.start_at >= start_period,
        InsuranceApplication.start_at < next_month,
    )
    
    if partner_group_id:
        try:
            q = q.filter(InsuranceApplication.partner_group_id == int(partner_group_id))
        except ValueError:
            pass
    
    applications = q.all()
    
    # 정산 데이터 집계
    data = []
    row_num = 1
    settlements_by_group = {}
    
    for app in applications:
        if app.partner_group and app.created_by_member:
            group_key = app.partner_group_id
            company_key = (app.created_by_member.company_name, app.created_by_member.representative)
            
            if group_key not in settlements_by_group:
                settlements_by_group[group_key] = {
                    'group_name': app.partner_group.name,
                    'companies': {},
                    'total_count': 0,
                    'total_amount': 0
                }
            
            if company_key not in settlements_by_group[group_key]['companies']:
                settlements_by_group[group_key]['companies'][company_key] = {
                    'company_name': app.created_by_member.company_name,
                    'representative': app.created_by_member.representative,
                    'count': 0,
                    'amount': 0
                }
            
            settlements_by_group[group_key]['companies'][company_key]['count'] += 1
            settlements_by_group[group_key]['companies'][company_key]['amount'] += 95000
            settlements_by_group[group_key]['total_count'] += 1
            settlements_by_group[group_key]['total_amount'] += 95000
    
    # 엑셀 데이터 생성
    grand_total_count = 0
    grand_total_amount = 0
    
    for group_id, group_data in sorted(settlements_by_group.items()):
        # 회사별 데이터
        for company_key, company_data in sorted(group_data['companies'].items()):
            data.append({
                '순': row_num,
                '파트너그룹': group_data['group_name'],
                '상사명': company_data['company_name'],
                '대표자': company_data['representative'],
                '건수': company_data['count'],
                '금액': company_data['amount'],
                '비고': '',
            })
            row_num += 1
        
        # 파트너그룹별 합계
        data.append({
            '순': '',
            '파트너그룹': f"{group_data['group_name']} 합계",
            '상사명': '',
            '대표자': '',
            '건수': group_data['total_count'],
            '금액': group_data['total_amount'],
            '비고': '',
        })
        grand_total_count += group_data['total_count']
        grand_total_amount += group_data['total_amount']
    
    # 총합계
    if data:
        data.append({
            '순': '',
            '파트너그룹': '총합계',
            '상사명': '',
            '대표자': '',
            '건수': grand_total_count,
            '금액': grand_total_amount,
            '비고': '',
        })
    
    df = pd.DataFrame(data)
    buffer = BytesIO()
    with pd.ExcelWriter(buffer, engine='openpyxl') as writer:
        df.to_excel(writer, index=False, sheet_name='정산내역')
    buffer.seek(0)
    
    filename = f'정산내역_{year}년{month}월_{datetime.now(KST).strftime("%Y%m%d_%H%M%S")}.xlsx'
    return send_file(
        buffer,
        as_attachment=True,
        download_name=filename,
        mimetype='application/vnd.openxmlformats-officedocument.spreadsheetml.sheet',
    )

# 전체관리자페이지 (요구사항의 전체관리자페이지)
@app.route('/admin/administrators', methods=['GET', 'POST'])
@admin_required
def admin_administrators():
    ensure_initialized()
    
    if request.method == 'POST':
        action = request.form.get('action')
        
        if action == 'create':
            # 새 관리자 추가
            name = request.form.get('name', '').strip()
            username = request.form.get('username', '').strip()
            password = request.form.get('password', '').strip()
            is_active = request.form.get('is_active') == 'on'
            permission = request.form.get('permission', 'viewer')
            memo = request.form.get('memo', '').strip()
            
            if not name or not username or not password:
                flash('이름, 아이디, 비밀번호는 필수입니다.', 'warning')
                return redirect(url_for('admin_administrators'))
            
            # 중복 확인
            if db.session.query(Member).filter(
                Member.username == username,
                Member.partner_group_id.is_(None)
            ).first():
                flash('이미 존재하는 아이디입니다.', 'danger')
                return redirect(url_for('admin_administrators'))
            
            try:
                admin = Member(
                    partner_group_id=None,
                    username=username,
                    company_name=name,
                    representative=name,
                    approval_status='승인' if is_active else '신청',
                    role='admin' if permission == 'admin' else 'viewer'  # 권한: 관리자 또는 열람
                )
                admin.set_password(password)
                db.session.add(admin)
                
                if not safe_commit():
                    flash('관리자 추가 중 오류가 발생했습니다.', 'danger')
                else:
                    flash('관리자가 추가되었습니다.', 'success')
            except Exception as e:
                try:
                    import sys
                    sys.stderr.write(f"Admin creation error: {e}\n")
                except Exception:
                    pass
                flash('관리자 추가 중 오류가 발생했습니다.', 'danger')
        
        elif action in ['save', 'delete']:
            admin_id = request.form.get('admin_id')
            if admin_id:
                admin = db.session.get(Member, int(admin_id))
                if admin and admin.partner_group_id is None:
                    if action == 'delete':
                        # 자기 자신은 삭제할 수 없음
                        if admin.id == current_user.id:
                            flash('자기 자신은 삭제할 수 없습니다.', 'warning')
                        else:
                            try:
                                db.session.delete(admin)
                                if not safe_commit():
                                    flash('삭제 처리 중 오류가 발생했습니다.', 'danger')
                                else:
                                    flash('관리자가 삭제되었습니다.', 'success')
                            except Exception as e:
                                try:
                                    import sys
                                    sys.stderr.write(f"Admin deletion error: {e}\n")
                                except Exception:
                                    pass
                                flash('삭제 처리 중 오류가 발생했습니다.', 'danger')
                    elif action == 'save':
                        try:
                            admin.company_name = request.form.get('name', '').strip()
                            admin.representative = request.form.get('name', '').strip()
                            admin.username = request.form.get('username', '').strip()
                            if request.form.get('password', '').strip():
                                admin.set_password(request.form.get('password', '').strip())
                            admin.approval_status = '승인' if request.form.get('is_active') == 'on' else '신청'
                            permission = request.form.get('permission', 'viewer')
                            admin.role = 'admin' if permission == 'admin' else 'viewer'  # 권한 업데이트
                            
                            if not safe_commit():
                                flash('저장 처리 중 오류가 발생했습니다.', 'danger')
                            else:
                                flash('관리자 정보가 저장되었습니다.', 'success')
                        except Exception as e:
                            try:
                                import sys
                                sys.stderr.write(f"Admin update error: {e}\n")
                            except Exception:
                                pass
                            flash('저장 처리 중 오류가 발생했습니다.', 'danger')
        
        return redirect(url_for('admin_administrators'))
    
    # GET 요청: 관리자 목록 조회
    edit_id = request.args.get('edit_id')
    administrators = []
    if db is not None:
        try:
            # 전체관리자 페이지: role='admin' 또는 role='viewer'인 관리자 모두 조회
            administrators = db.session.query(Member).filter(
                Member.partner_group_id.is_(None),
                Member.role.in_(['admin', 'viewer'])
            ).order_by(Member.created_at.desc()).all()
        except Exception as e:
            try:
                import sys
                sys.stderr.write(f"Error fetching administrators: {e}\n")
            except Exception:
                pass
    
    return render_template('admin/administrators.html',
                         administrators=administrators,
                         edit_id=edit_id)

# 파트너그룹 섹션 라우트들

# 파트너그룹 보험가입 페이지 (요구사항의 책임보험가입페이지)
@app.route('/partner/insurance', methods=['GET', 'POST'])
def partner_insurance():
    try:
        ensure_initialized()
        
        # 권한 확인
        partner_group_id = None
        is_partner_admin = False
        
        if 'user_type' in session and session['user_type'] == 'partner_admin':
            # 파트너그룹 관리자
            partner_group_id = session.get('partner_group_id')
            is_partner_admin = True
        else:
            # Flask-Login 기반 회원사 확인
            try:
                from flask_login import current_user
                is_auth = getattr(current_user, 'is_authenticated', False)
            except Exception:
                is_auth = False
            
            if not is_auth:
                flash('로그인이 필요합니다.', 'warning')
                return redirect(url_for('login'))
            
            if current_user.role == 'member' and current_user.partner_group_id:
                partner_group_id = current_user.partner_group_id
            else:
                flash('파트너그룹 접근 권한이 없습니다.', 'warning')
                return redirect(url_for('login'))
        
        if not partner_group_id:
            flash('파트너그룹 정보를 찾을 수 없습니다.', 'danger')
            return redirect(url_for('login'))
        
        if request.method == 'POST':
            action = request.form.get('action')
            
            if action == 'apply':
                # 신규 가입 신청 (회원사 및 파트너그룹 관리자 모두 가능)
                desired_start_date = parse_date(request.form.get('desired_start_date'))
                car_plate = request.form.get('car_plate', '').strip()
                vin = request.form.get('vin', '').strip()
                car_name = request.form.get('car_name', '').strip()
                car_registered_at = parse_date(request.form.get('car_registered_at'))
                memo = request.form.get('memo', '').strip()
                
                # 피보험자코드: 파트너그룹 관리자는 폼에서 가져오거나, 회원사는 자동으로
                insured_code = request.form.get('insured_code', '').strip()
                
                if not desired_start_date or not car_plate:
                    flash('가입희망일자와 차량번호는 필수입니다.', 'warning')
                    return redirect(url_for('partner_insurance'))
                
                try:
                    # 파트너그룹 정보 가져오기
                    partner_group = db.session.query(PartnerGroup).filter_by(id=partner_group_id).first()
                    partner_group_name = partner_group.name if partner_group else ''
                    
                    # 피보험자코드 설정
                    if not is_partner_admin:
                        # 회원사: 로그인한 사업자의 사업자번호
                        if hasattr(current_user, 'business_number'):
                            insured_code = current_user.business_number or ''
                        else:
                            insured_code = ''
                    # 파트너그룹 관리자는 폼에서 입력받은 값 사용 (이미 위에서 가져옴)
                    
                    # 계약자코드: 파트너그룹 이름
                    contractor_code = partner_group_name
                    
                    application = InsuranceApplication(
                        partner_group_id=partner_group_id,
                        desired_start_date=desired_start_date,
                        insured_code=insured_code,  # 피보험자코드
                        contractor_code=contractor_code,  # 계약자코드: 파트너그룹 이름
                        car_plate=car_plate,
                        vin=vin,
                        car_name=car_name,
                        car_registered_at=car_registered_at,
                        premium=9500,  # 보험료 9500원 고정
                        status='신청',
                        memo=memo,
                        created_by_member_id=current_user.id if not is_partner_admin and hasattr(current_user, 'id') else None,
                    )
                    db.session.add(application)
                    
                    # 커밋 전 디버깅
                    try:
                        import sys
                        sys.stderr.write(f"Insurance application: Adding {car_plate} to session\n")
                    except Exception:
                        pass
                    
                    commit_success = safe_commit()
                    
                    if not commit_success:
                        try:
                            import sys
                            import traceback
                            sys.stderr.write(f"Insurance application: Commit failed for {car_plate}\n")
                            sys.stderr.write(f"Insurance application traceback: {traceback.format_exc()}\n")
                        except Exception:
                            pass
                        flash('신청 처리 중 오류가 발생했습니다.', 'danger')
                    else:
                        # 커밋 성공 후 검증
                        try:
                            import sys
                            verify_app = db.session.get(InsuranceApplication, application.id)
                            if verify_app:
                                sys.stderr.write(f"Insurance application: Verified {car_plate} (ID: {application.id}) exists\n")
                            else:
                                sys.stderr.write(f"Insurance application: WARNING - {car_plate} not found after commit!\n")
                        except Exception:
                            pass
                        flash('보험 신청이 등록되었습니다.', 'success')
                except Exception as e:
                    try:
                        import sys
                        import traceback
                        sys.stderr.write(f"Insurance application error: {e}\n")
                        sys.stderr.write(f"Traceback: {traceback.format_exc()}\n")
                    except Exception:
                        pass
                    try:
                        db.session.rollback()
                    except Exception:
                        pass
                    flash(f'신청 처리 중 오류가 발생했습니다: {str(e)}', 'danger')
            
            elif action == 'excel_upload':
                # 엑셀 일괄 업로드
                if 'excel_file' not in request.files:
                    flash('파일을 선택해주세요.', 'warning')
                    return redirect(url_for('partner_insurance'))
                
                file = request.files['excel_file']
                if file and file.filename:
                    try:
                        import pandas as pd
                        from io import BytesIO
                        
                        # 엑셀 파일 읽기
                        df = pd.read_excel(file)
                        
                        # 파트너그룹 정보
                        partner_group = db.session.query(PartnerGroup).filter_by(id=partner_group_id).first()
                        partner_group_name = partner_group.name if partner_group else ''
                        
                        success_count = 0
                        error_count = 0
                        
                        for _, row in df.iterrows():
                            try:
                                desired_start_date = parse_date(str(row.get('가입희망일자', '')))
                                car_plate = str(row.get('한글차량번호', '')).strip()
                                
                                if not desired_start_date or not car_plate:
                                    error_count += 1
                                    continue
                                
                                # 피보험자코드: 회원사는 본인 사업자번호, 관리자는 엑셀에서 가져오거나 기본값
                                if is_partner_admin:
                                    insured_code = str(row.get('피보험자코드', '')).strip() or ''
                                else:
                                    insured_code = current_user.business_number or ''
                                
                                # 계약자코드: 파트너그룹 이름
                                contractor_code = partner_group_name
                                
                                application = InsuranceApplication(
                                    partner_group_id=partner_group_id,
                                    desired_start_date=desired_start_date,
                                    insured_code=insured_code,
                                    contractor_code=contractor_code,
                                    car_plate=car_plate,
                                    vin=str(row.get('차대번호', '')).strip() or None,
                                    car_name=str(row.get('차량명', '')).strip() or None,
                                    car_registered_at=parse_date(str(row.get('차량등록일자', ''))),
                                    premium=9500,
                                    status='신청',
                                    memo=str(row.get('비고', '')).strip() or None,
                                    created_by_member_id=current_user.id if not is_partner_admin else None,
                                )
                                db.session.add(application)
                                success_count += 1
                            except Exception as e:
                                error_count += 1
                                try:
                                    import sys
                                    sys.stderr.write(f"Insurance excel upload error: {e}\n")
                                except Exception:
                                    pass
                                continue
                        
                        # 커밋 전 디버깅
                        try:
                            import sys
                            sys.stderr.write(f"Insurance excel upload: Committing {success_count} applications\n")
                        except Exception:
                            pass
                        
                        commit_success = safe_commit()
                        
                        if commit_success:
                            try:
                                import sys
                                sys.stderr.write(f"Insurance excel upload: Successfully committed {success_count} applications\n")
                            except Exception:
                                pass
                            flash(f'엑셀 업로드 완료: 성공 {success_count}건, 실패 {error_count}건', 'success' if success_count > 0 else 'warning')
                        else:
                            try:
                                import sys
                                import traceback
                                sys.stderr.write(f"Insurance excel upload: Commit failed\n")
                                sys.stderr.write(f"Insurance excel upload traceback: {traceback.format_exc()}\n")
                            except Exception:
                                pass
                            flash('엑셀 업로드 중 오류가 발생했습니다.', 'danger')
                    except Exception as e:
                        try:
                            import sys
                            sys.stderr.write(f"Excel upload error: {e}\n")
                        except Exception:
                            pass
                        flash(f'엑셀 파일 처리 중 오류가 발생했습니다: {str(e)}', 'danger')
                
                return redirect(url_for('partner_insurance'))
            
            elif action in ['save', 'delete']:
                # 수정/삭제 (조합승인 전까지만 가능)
                app_id = request.form.get('app_id')
                if app_id:
                    application = db.session.get(InsuranceApplication, int(app_id))
                    if application and application.partner_group_id == partner_group_id:
                        # 권한 확인: 본인이 신청한 것이거나 파트너그룹 관리자
                        if not is_partner_admin:
                            if not hasattr(current_user, 'id') or application.created_by_member_id != current_user.id:
                                flash('권한이 없습니다.', 'warning')
                                return redirect(url_for('partner_insurance'))
                        if application.approved_at:
                            flash('조합승인 후에는 수정/삭제할 수 없습니다.', 'warning')
                        else:
                            if action == 'delete':
                                try:
                                    app_id = application.id
                                    db.session.delete(application)
                                    
                                    try:
                                        import sys
                                        sys.stderr.write(f"Insurance delete: Deleting application ID {app_id}\n")
                                    except Exception:
                                        pass
                                    
                                    commit_success = safe_commit()
                                    
                                    if not commit_success:
                                        try:
                                            import sys
                                            import traceback
                                            sys.stderr.write(f"Insurance delete: Commit failed for ID {app_id}\n")
                                            sys.stderr.write(f"Insurance delete traceback: {traceback.format_exc()}\n")
                                        except Exception:
                                            pass
                                        flash('삭제 처리 중 오류가 발생했습니다.', 'danger')
                                    else:
                                        try:
                                            import sys
                                            sys.stderr.write(f"Insurance delete: Successfully deleted application ID {app_id}\n")
                                        except Exception:
                                            pass
                                        flash('삭제되었습니다.', 'success')
                                except Exception as e:
                                    try:
                                        import sys
                                        import traceback
                                        sys.stderr.write(f"Insurance deletion error: {e}\n")
                                        sys.stderr.write(f"Insurance deletion traceback: {traceback.format_exc()}\n")
                                    except Exception:
                                        pass
                                    try:
                                        db.session.rollback()
                                    except Exception:
                                        pass
                                    flash('삭제 처리 중 오류가 발생했습니다.', 'danger')
                            elif action == 'save':
                                try:
                                    app_id = application.id
                                    application.desired_start_date = parse_date(request.form.get('desired_start_date'))
                                    application.car_plate = request.form.get('car_plate', '').strip()
                                    application.vin = request.form.get('vin', '').strip()
                                    application.car_name = request.form.get('car_name', '').strip()
                                    application.car_registered_at = parse_date(request.form.get('car_registered_at'))
                                    application.memo = request.form.get('memo', '').strip()
                                    
                                    try:
                                        import sys
                                        sys.stderr.write(f"Insurance save: Updating application ID {app_id}\n")
                                    except Exception:
                                        pass
                                    
                                    commit_success = safe_commit()
                                    
                                    if not commit_success:
                                        try:
                                            import sys
                                            import traceback
                                            sys.stderr.write(f"Insurance save: Commit failed for ID {app_id}\n")
                                            sys.stderr.write(f"Insurance save traceback: {traceback.format_exc()}\n")
                                        except Exception:
                                            pass
                                        flash('저장 처리 중 오류가 발생했습니다.', 'danger')
                                    else:
                                        # 커밋 성공 후 검증
                                        try:
                                            import sys
                                            verify_app = db.session.get(InsuranceApplication, app_id)
                                            if verify_app:
                                                sys.stderr.write(f"Insurance save: Verified application ID {app_id} exists\n")
                                            else:
                                                sys.stderr.write(f"Insurance save: WARNING - Application ID {app_id} not found after commit!\n")
                                        except Exception:
                                            pass
                                        flash('저장되었습니다.', 'success')
                                except Exception as e:
                                    try:
                                        import sys
                                        import traceback
                                        sys.stderr.write(f"Insurance update error: {e}\n")
                                        sys.stderr.write(f"Insurance update traceback: {traceback.format_exc()}\n")
                                    except Exception:
                                        pass
                                    try:
                                        db.session.rollback()
                                    except Exception:
                                        pass
                                    flash('저장 처리 중 오류가 발생했습니다.', 'danger')
            
            return redirect(url_for('partner_insurance'))
        
        # GET 요청: 보험신청 목록 조회
        start_date = parse_date(request.args.get('start_date', ''))
        end_date = parse_date(request.args.get('end_date', ''))
        edit_id = request.args.get('edit_id')
        
        # 파트너그룹 정보 가져오기
        partner_group = db.session.query(PartnerGroup).filter_by(id=partner_group_id).first()
        partner_group_name = partner_group.name if partner_group else ''
        
        q = db.session.query(InsuranceApplication).filter_by(partner_group_id=partner_group_id)
        
        # 회원사는 본인 신청만 조회
        if not is_partner_admin and hasattr(current_user, 'id'):
            q = q.filter_by(created_by_member_id=current_user.id)
        
        # 검색 조건: 가입일자 기준 (start_at이 있으면 start_at, 없으면 desired_start_date)
        if start_date:
            q = q.filter(
                db.or_(
                    db.and_(InsuranceApplication.start_at.is_not(None), 
                           InsuranceApplication.start_at >= datetime.combine(start_date, datetime.min.time(), tzinfo=KST)),
                    db.and_(InsuranceApplication.start_at.is_(None),
                           InsuranceApplication.desired_start_date >= start_date)
                )
            )
        if end_date:
            q = q.filter(
                db.or_(
                    db.and_(InsuranceApplication.start_at.is_not(None),
                           InsuranceApplication.start_at <= datetime.combine(end_date, datetime.max.time(), tzinfo=KST)),
                    db.and_(InsuranceApplication.start_at.is_(None),
                           InsuranceApplication.desired_start_date <= end_date)
                )
            )
        
        applications = q.order_by(InsuranceApplication.created_at.desc()).all()
        
        # 상태 재계산
        for app in applications:
            app.recompute_status()
        safe_commit()
        
        return render_template('partner/insurance.html',
                             applications=applications,
                             is_partner_admin=is_partner_admin,
                             partner_group_id=partner_group_id,
                             partner_group_name=partner_group_name,
                             start_date=start_date,
                             end_date=end_date,
                             edit_id=edit_id,
                             current_user=current_user if not is_partner_admin else None)
        
    except Exception as e:
        try:
            import sys
            import traceback
            sys.stderr.write(f"Partner insurance route error: {e}\n")
            sys.stderr.write(f"Traceback: {traceback.format_exc()}\n")
        except Exception:
            pass
        flash('페이지 로드 중 오류가 발생했습니다.', 'danger')
        return redirect(url_for('partner_dashboard'))

# API: 차량 정보 조회 (보안을 위해 백엔드에서 처리)
@app.route('/api/get-car-info', methods=['POST'])
def api_get_car_info():
    """외부 API에서 차량 정보를 가져오는 보안 엔드포인트"""
    print("=== API 호출 시작 ===", flush=True)
    try:
        ensure_initialized()
        print("1. ensure_initialized 완료", flush=True)

        # 권한 확인
        partner_group_id = None
        is_partner_admin = False

        print(f"2. Session: {dict(session)}", flush=True)

        if 'user_type' in session and session['user_type'] == 'partner_admin':
            partner_group_id = session.get('partner_group_id')
            is_partner_admin = True
            print(f"3. Partner admin - group_id: {partner_group_id}", flush=True)
        else:
            try:
                from flask_login import current_user
                is_auth = getattr(current_user, 'is_authenticated', False)
                print(f"3. Current user auth: {is_auth}", flush=True)
            except Exception as auth_err:
                print(f"3. Auth error: {auth_err}", flush=True)
                is_auth = False

            if not is_auth:
                print("4. 로그인 필요", flush=True)
                return jsonify({'status': 'error', 'message': '로그인이 필요합니다.'}), 401

            print(f"4. User role: {current_user.role}, partner_group_id: {current_user.partner_group_id}", flush=True)

            if current_user.role == 'member' and current_user.partner_group_id:
                partner_group_id = current_user.partner_group_id
            else:
                print("5. 접근 권한 없음", flush=True)
                return jsonify({'status': 'error', 'message': '접근 권한이 없습니다.'}), 403

        if not partner_group_id:
            print("6. 파트너그룹 정보 없음", flush=True)
            return jsonify({'status': 'error', 'message': '파트너그룹 정보를 찾을 수 없습니다.'}), 403

        print(f"6. Partner group ID: {partner_group_id}", flush=True)

        # 요청 데이터 가져오기
        data = request.get_json()
        print(f"7. Request data: {data}", flush=True)

        if not data:
            print("8. 요청 데이터 없음", flush=True)
            return jsonify({'status': 'error', 'message': '요청 데이터가 없습니다.'}), 400

        car_no = data.get('car_no', '').strip()
        if not car_no:
            print("9. 차량번호 없음", flush=True)
            return jsonify({'status': 'error', 'message': '차량번호를 입력해주세요.'}), 400

        print(f"9. 차량번호: {car_no}", flush=True)

        # 외부 API 호출 (authkey는 서버측에서 관리)
        import requests
        print("10. requests 모듈 임포트 완료", flush=True)

        api_url = 'https://diag.wecarmobility.co.kr/api/get_car_info'
        authkey = 'ab9d332978438705e1fe52e6af0f0b025687128227dca046f9b6f6e5da5a5f7eee430edfdc6079092244a07fd737abc238e7e2ca71a36283619bfb4b978edb49'

        print(f"11. 외부 API 호출 시작: {api_url}", flush=True)

        try:
            response = requests.post(
                api_url,
                data={
                    'car_no': car_no,
                    'authkey': authkey
                },
                timeout=10
            )

            print(f"12. 외부 API 응답 상태: {response.status_code}", flush=True)

            if response.status_code == 200:
                result = response.json()
                print(f"13. 외부 API 응답 데이터: {result.get('status')}", flush=True)

                # API 응답 확인
                if result.get('status') == 'ok' and result.get('dto'):
                    dto = result['dto']
                    print(f"14. DTO 데이터 추출 완료", flush=True)

                    # 필요한 데이터만 추출
                    car_info = {
                        'status': 'ok',
                        'data': {
                            'diag_registered_date': dto.get('diag_registred_date'),  # 최초등록일
                            'diag_car_id': dto.get('diag_car_id'),  # 차대번호
                            'diag_model': dto.get('diag_model')  # 차량명
                        }
                    }

                    print(f"15. 성공 응답 반환: {car_info}", flush=True)
                    return jsonify(car_info), 200
                else:
                    error_msg = result.get('msg', '차량 정보를 찾을 수 없습니다.')
                    print(f"16. API 에러 응답: {error_msg}", flush=True)
                    return jsonify({
                        'status': 'error',
                        'message': error_msg
                    }), 404
            else:
                print(f"17. HTTP 에러: {response.status_code}", flush=True)
                return jsonify({
                    'status': 'error',
                    'message': f'외부 API 오류 (HTTP {response.status_code})'
                }), 500

        except requests.exceptions.Timeout:
            print("18. Timeout 에러", flush=True)
            return jsonify({
                'status': 'error',
                'message': '요청 시간이 초과되었습니다. 다시 시도해주세요.'
            }), 504
        except requests.exceptions.RequestException as e:
            print(f"19. Request 에러: {e}", flush=True)
            try:
                import sys
                sys.stderr.write(f"Car info API error: {e}\n")
            except Exception:
                pass
            return jsonify({
                'status': 'error',
                'message': '차량 정보 조회 중 오류가 발생했습니다.'
            }), 500

    except Exception as e:
        try:
            import sys
            import traceback
            error_msg = f"Get car info API error: {e}\n{traceback.format_exc()}"
            sys.stderr.write(error_msg + "\n")
            print(error_msg, flush=True)  # 콘솔 출력
        except Exception as log_err:
            print(f"Logging error: {log_err}", flush=True)
        return jsonify({
            'status': 'error',
            'message': f'서버 오류가 발생했습니다: {str(e)}'
        }), 500

# 파트너그룹 책임보험가입 엑셀 양식 다운로드
@app.route('/partner/insurance/excel-template')
def partner_insurance_excel_template():
    try:
        ensure_initialized()
        import pandas as pd
        from io import BytesIO
        
        # 엑셀 양식 생성
        data = {
            '가입희망일자': ['2024-01-01'],
            '피보험자코드': ['123-45-67890'],
            '계약자코드': [''],
            '한글차량번호': ['12가3456'],
            '차대번호': [''],
            '차량명': ['소나타'],
            '차량등록일자': ['2020-01-01'],
            '비고': [''],
        }
        
        df = pd.DataFrame(data)
        buffer = BytesIO()
        with pd.ExcelWriter(buffer, engine='openpyxl') as writer:
            df.to_excel(writer, index=False, sheet_name='보험가입양식')
        buffer.seek(0)
        
        return send_file(
            buffer,
            as_attachment=True,
            download_name='보험가입업로드양식.xlsx',
            mimetype='application/vnd.openxmlformats-officedocument.spreadsheetml.sheet',
        )
    except Exception as e:
        try:
            import sys
            import traceback
            sys.stderr.write(f"Partner insurance excel template error: {e}\n")
            sys.stderr.write(f"Traceback: {traceback.format_exc()}\n")
        except Exception:
            pass
        flash('엑셀 양식 다운로드 중 오류가 발생했습니다.', 'danger')
        return redirect(url_for('partner_insurance'))

# 파트너그룹 약관 페이지 (요구사항의 책임보험약관페이지)
@app.route('/partner/terms')
def partner_terms():
    try:
        ensure_initialized()
        
        # 권한 확인 (간단히)
        if 'user_type' not in session and not getattr(current_user, 'is_authenticated', False):
            flash('로그인이 필요합니다.', 'warning')
            return redirect(url_for('login'))
        
        return render_template('partner/terms.html')
        
    except Exception as e:
        try:
            import sys
            sys.stderr.write(f"Partner terms route error: {e}\n")
        except Exception:
            pass
        flash('페이지 로드 중 오류가 발생했습니다.', 'danger')
        return redirect(url_for('partner_dashboard'))

# 파트너그룹 관리자페이지 (요구사항의 관리자페이지)
@app.route('/partner/admin')
def partner_admin():
    try:
        ensure_initialized()
        
        # 파트너그룹 관리자만 접근 가능
        if 'user_type' not in session or session['user_type'] != 'partner_admin':
            flash('파트너그룹 관리자만 접근 가능합니다.', 'warning')
            return redirect(url_for('partner_dashboard'))
        
        partner_group_id = session.get('partner_group_id')
        partner_group_name = session.get('partner_group_name')
        
        return render_template('partner/admin.html',
                             partner_group_id=partner_group_id,
                             partner_group_name=partner_group_name)
        
    except Exception as e:
        try:
            import sys
            sys.stderr.write(f"Partner admin route error: {e}\n")
        except Exception:
            pass
        flash('페이지 로드 중 오류가 발생했습니다.', 'danger')
        return redirect(url_for('partner_dashboard'))

# 파트너그룹 회원가입승인 페이지
@app.route('/partner/admin/member-approval', methods=['GET', 'POST'])
def partner_admin_member_approval():
    try:
        ensure_initialized()
        
        # 파트너그룹 관리자만 접근 가능
        if 'user_type' not in session or session['user_type'] != 'partner_admin':
            flash('파트너그룹 관리자만 접근 가능합니다.', 'warning')
            return redirect(url_for('partner_dashboard'))
        
        partner_group_id = session.get('partner_group_id')
        partner_group_name = session.get('partner_group_name')
        
        if request.method == 'POST':
            action = request.form.get('action')
            member_id = request.form.get('member_id')
            
            # 회원추가
            if action == 'add':
                company_name = request.form.get('company_name', '').strip()
                username = request.form.get('username', '').strip()
                password = request.form.get('password', '').strip()
                address = request.form.get('address', '').strip()
                business_number = request.form.get('business_number', '').strip()
                corporation_number = request.form.get('corporation_number', '').strip()
                representative = request.form.get('representative', '').strip()
                phone = request.form.get('phone', '').strip()
                mobile = request.form.get('mobile', '').strip()
                email = request.form.get('email', '').strip()
                approval_status = request.form.get('approval_status', '신청').strip()
                memo = request.form.get('memo', '').strip()
                
                if not company_name or not username or not password or not business_number:
                    flash('상사명, 아이디, 패스워드, 사업자번호는 필수입니다.', 'warning')
                else:
                    # 중복 확인 (파트너그룹 내에서)
                    existing = db.session.query(Member).filter(
                        Member.partner_group_id == partner_group_id,
                        (Member.username == username) | (Member.business_number == business_number)
                    ).first()
                    
                    if existing:
                        flash('이미 존재하는 아이디 또는 사업자번호입니다.', 'danger')
                    else:
                        try:
                            new_member = Member(
                                partner_group_id=partner_group_id,
                                username=username,
                                company_name=company_name,
                                address=address,
                                business_number=business_number,
                                corporation_number=corporation_number,
                                representative=representative,
                                phone=phone,
                                mobile=mobile,
                                email=email,
                                approval_status=approval_status,
                                role='member',
                                memo=memo,
                                member_type='법인',  # 기본값: 법인
                                privacy_agreement=False  # 기본값: False
                            )
                            new_member.set_password(password)
                            db.session.add(new_member)
                            
                            # 커밋 전 디버깅
                            try:
                                import sys
                                sys.stderr.write(f"Add member: Adding {username} to session\n")
                            except Exception:
                                pass
                            
                            commit_success = safe_commit()
                            
                            if commit_success:
                                # 커밋 성공 후 검증
                                try:
                                    import sys
                                    verify_member = db.session.get(Member, new_member.id)
                                    if verify_member:
                                        sys.stderr.write(f"Add member: Verified {username} (ID: {new_member.id}) exists\n")
                                    else:
                                        sys.stderr.write(f"Add member: WARNING - {username} not found after commit!\n")
                                except Exception:
                                    pass
                                flash('회원이 추가되었습니다.', 'success')
                            else:
                                try:
                                    import sys
                                    import traceback
                                    sys.stderr.write(f"Add member: Commit failed for {username}\n")
                                    sys.stderr.write(f"Add member: Traceback: {traceback.format_exc()}\n")
                                except Exception:
                                    pass
                                flash('회원 추가 중 오류가 발생했습니다.', 'danger')
                        except Exception as e:
                            try:
                                import sys
                                import traceback
                                sys.stderr.write(f"Add member error: {e}\n")
                                sys.stderr.write(f"Add member traceback: {traceback.format_exc()}\n")
                            except Exception:
                                pass
                            try:
                                db.session.rollback()
                            except Exception:
                                pass
                            flash('회원 추가 중 오류가 발생했습니다.', 'danger')
            
            # 엑셀 업로드
            elif action == 'excel_upload':
                file = request.files.get('excel_file')
                if not file:
                    flash('엑셀 파일을 선택하세요.', 'warning')
                else:
                    try:
                        import pandas as pd
                        df = pd.read_excel(file)
                        created = 0
                        skipped = 0
                        errors = []
                        
                        # 필수 컬럼 확인
                        required_cols = {'username', 'company_name', 'business_number', 'password'}
                        if not required_cols.issubset(set(df.columns)):
                            flash('엑셀 컬럼이 올바르지 않습니다. (필수: username, company_name, business_number, password)', 'danger')
                        else:
                            for idx, row in df.iterrows():
                                try:
                                    username = str(row.get('username', '')).strip()
                                    company_name = str(row.get('company_name', '')).strip()
                                    business_number = str(row.get('business_number', '')).strip()
                                    password = str(row.get('password', '')).strip()
                                    
                                    if not username or not company_name or not business_number or not password:
                                        skipped += 1
                                        continue
                                    
                                    # 중복 확인 (파트너그룹 내에서)
                                    existing = db.session.query(Member).filter(
                                        Member.partner_group_id == partner_group_id,
                                        (Member.username == username) | (Member.business_number == business_number)
                                    ).first()
                                    
                                    if existing:
                                        skipped += 1
                                        continue
                                    
                                    new_member = Member(
                                        partner_group_id=partner_group_id,
                                        username=username,
                                        company_name=company_name,
                                        address=str(row.get('address', '') or '').strip(),
                                        business_number=business_number,
                                        corporation_number=str(row.get('corporation_number', '') or '').strip(),
                                        representative=str(row.get('representative', '') or '').strip(),
                                        phone=str(row.get('phone', '') or '').strip(),
                                        mobile=str(row.get('mobile', '') or '').strip(),
                                        email=str(row.get('email', '') or '').strip(),
                                        approval_status=str(row.get('approval_status', '신청') or '신청').strip() or '신청',
                                        role='member',
                                        memo=str(row.get('memo', '') or '').strip(),
                                        member_type=str(row.get('member_type', '법인') or '법인').strip() or '법인',
                                        privacy_agreement=bool(row.get('privacy_agreement', False)) if 'privacy_agreement' in row else False
                                    )
                                    new_member.set_password(password)
                                    db.session.add(new_member)
                                    created += 1
                                except Exception as e:
                                    skipped += 1
                                    errors.append(f"행 {idx+2}: {str(e)}")
                                    try:
                                        import sys
                                        sys.stderr.write(f"Excel upload member error at row {idx+2}: {e}\n")
                                    except Exception:
                                        pass
                            
                            # 커밋 전 디버깅
                            try:
                                import sys
                                sys.stderr.write(f"Excel upload: Committing {created} members\n")
                            except Exception:
                                pass
                            
                            commit_success = safe_commit()
                            
                            if not commit_success:
                                try:
                                    import sys
                                    import traceback
                                    sys.stderr.write(f"Excel upload: Commit failed\n")
                                    sys.stderr.write(f"Excel upload traceback: {traceback.format_exc()}\n")
                                except Exception:
                                    pass
                                flash('일괄 업로드 처리 중 오류가 발생했습니다.', 'danger')
                            else:
                                try:
                                    import sys
                                    sys.stderr.write(f"Excel upload: Successfully committed {created} members\n")
                                except Exception:
                                    pass
                                msg = f'일괄 업로드 완료: 추가 {created}건, 건너뜀 {skipped}건'
                                if errors:
                                    msg += f' (오류: {len(errors)}건)'
                                flash(msg, 'success' if created > 0 else 'warning')
                    except Exception as e:
                        try:
                            import sys
                            sys.stderr.write(f"Excel upload error: {e}\n")
                        except Exception:
                            pass
                        flash('업로드 처리 중 오류가 발생했습니다.', 'danger')
            
            # 기존 수정/삭제 처리
            elif action in ['save', 'delete'] and member_id:
                member = db.session.get(Member, int(member_id))
                if member and member.partner_group_id == partner_group_id:
                    if action == 'delete':
                        try:
                            member_id = member.id
                            member_username = member.username
                            db.session.delete(member)
                            
                            try:
                                import sys
                                sys.stderr.write(f"Member delete: Deleting member {member_username} (ID: {member_id})\n")
                            except Exception:
                                pass
                            
                            commit_success = safe_commit()
                            
                            if commit_success:
                                try:
                                    import sys
                                    sys.stderr.write(f"Member delete: Successfully deleted member {member_username}\n")
                                except Exception:
                                    pass
                                flash('회원이 삭제되었습니다.', 'success')
                            else:
                                try:
                                    import sys
                                    import traceback
                                    sys.stderr.write(f"Member delete: Commit failed for {member_username}\n")
                                    sys.stderr.write(f"Member delete traceback: {traceback.format_exc()}\n")
                                except Exception:
                                    pass
                                flash('회원 삭제 중 오류가 발생했습니다.', 'danger')
                        except Exception as e:
                            try:
                                import sys
                                import traceback
                                sys.stderr.write(f"Member deletion error: {e}\n")
                                sys.stderr.write(f"Member deletion traceback: {traceback.format_exc()}\n")
                            except Exception:
                                pass
                            try:
                                db.session.rollback()
                            except Exception:
                                pass
                            flash('회원 삭제 중 오류가 발생했습니다.', 'danger')
                    elif action == 'save':
                        try:
                            member_id = member.id
                            member_username = member.username
                            member.company_name = request.form.get('company_name', '').strip()
                            member.username = request.form.get('username', '').strip()
                            if request.form.get('password', '').strip():
                                member.set_password(request.form.get('password', '').strip())
                            member.address = request.form.get('address', '').strip()
                            member.business_number = request.form.get('business_number', '').strip()
                            member.corporation_number = request.form.get('corporation_number', '').strip()
                            member.representative = request.form.get('representative', '').strip()
                            member.phone = request.form.get('phone', '').strip()
                            member.mobile = request.form.get('mobile', '').strip()
                            member.email = request.form.get('email', '').strip()
                            member.approval_status = request.form.get('approval_status', '신청')
                            member.memo = request.form.get('memo', '').strip()
                            
                            # 사업자등록증 파일 업로드 처리
                            if 'registration_cert' in request.files:
                                try:
                                    file = request.files['registration_cert']
                                    if file and file.filename:
                                        allowed_extensions = {'.pdf', '.jpg', '.jpeg', '.png'}
                                        file_ext = os.path.splitext(file.filename)[1].lower()
                                        if file_ext in allowed_extensions:
                                            # 업로드 디렉토리 확인 및 생성
                                            os.makedirs(UPLOAD_DIR, exist_ok=True)
                                            
                                            # 기존 파일이 있으면 삭제 (선택사항)
                                            if member.registration_cert_path:
                                                old_filepath = os.path.join(UPLOAD_DIR, member.registration_cert_path.split('/')[-1])
                                                try:
                                                    if os.path.exists(old_filepath):
                                                        os.remove(old_filepath)
                                                except Exception:
                                                    pass  # 기존 파일 삭제 실패해도 계속 진행
                                            
                                            # 새 파일 저장
                                            timestamp = datetime.now(KST).strftime('%Y%m%d_%H%M%S')
                                            business_number = member.business_number or 'unknown'
                                            filename = f"{business_number}_{timestamp}{file_ext}"
                                            filepath = os.path.join(UPLOAD_DIR, filename)
                                            file.save(filepath)
                                            
                                            # 파일이 실제로 저장되었는지 확인
                                            if os.path.exists(filepath) and os.path.getsize(filepath) > 0:
                                                member.registration_cert_path = os.path.join('uploads', filename)
                                                print(f"Registration cert updated successfully: {member.registration_cert_path}")
                                            else:
                                                print(f"ERROR: Registration cert file was not saved properly. Path: {filepath}")
                                        else:
                                            flash(f'허용되지 않은 파일 형식입니다. (PDF, JPG, PNG만 가능)', 'warning')
                                except Exception as e:
                                    try:
                                        import sys
                                        import traceback
                                        sys.stderr.write(f"Registration cert upload error: {e}\n")
                                        sys.stderr.write(f"Traceback: {traceback.format_exc()}\n")
                                    except Exception:
                                        pass
                                    flash('사업자등록증 파일 업로드 중 오류가 발생했습니다.', 'warning')
                                    # 파일 업로드 실패해도 다른 정보는 저장
                            
                            try:
                                import sys
                                sys.stderr.write(f"Member save: Updating member {member_username} (ID: {member_id})\n")
                            except Exception:
                                pass
                            
                            commit_success = safe_commit()
                            
                            if commit_success:
                                # 커밋 성공 후 검증
                                try:
                                    import sys
                                    verify_member = db.session.get(Member, member_id)
                                    if verify_member:
                                        sys.stderr.write(f"Member save: Verified member {member_username} (ID: {member_id}) exists\n")
                                    else:
                                        sys.stderr.write(f"Member save: WARNING - Member {member_username} not found after commit!\n")
                                except Exception:
                                    pass
                                flash('회원 정보가 저장되었습니다.', 'success')
                            else:
                                try:
                                    import sys
                                    import traceback
                                    sys.stderr.write(f"Member save: Commit failed for {member_username}\n")
                                    sys.stderr.write(f"Member save traceback: {traceback.format_exc()}\n")
                                except Exception:
                                    pass
                                flash('회원 정보 저장 중 오류가 발생했습니다.', 'danger')
                        except Exception as e:
                            try:
                                import sys
                                import traceback
                                sys.stderr.write(f"Member update error: {e}\n")
                                sys.stderr.write(f"Member update traceback: {traceback.format_exc()}\n")
                            except Exception:
                                pass
                            try:
                                db.session.rollback()
                            except Exception:
                                pass
                            flash('회원 정보 저장 중 오류가 발생했습니다.', 'danger')
            
            return redirect(url_for('partner_admin_member_approval', edit_id=request.args.get('edit_id')))
        
        edit_id = request.args.get('edit_id')
        members = db.session.query(Member).filter_by(
            partner_group_id=partner_group_id,
            role='member'
        ).order_by(Member.created_at.desc()).all()
        
        return render_template('partner/admin_member_approval.html',
                             members=members,
                             partner_group_name=partner_group_name,
                             edit_id=edit_id)
        
    except Exception as e:
        try:
            import sys
            sys.stderr.write(f"Partner admin member approval error: {e}\n")
        except Exception:
            pass
        flash('페이지 로드 중 오류가 발생했습니다.', 'danger')
        return redirect(url_for('partner_admin'))

# 파트너그룹 회원가입승인 엑셀 양식 다운로드
@app.route('/partner/admin/member-approval/excel-template')
def partner_admin_member_excel_template():
    try:
        ensure_initialized()
        import pandas as pd
        from io import BytesIO
        
        # 엑셀 양식 생성
        data = {
            'username': ['아이디'],
            'password': ['패스워드'],
            'company_name': ['상사명'],
            'address': ['주소'],
            'business_number': ['사업자번호'],
            'corporation_number': ['법인번호'],
            'representative': ['대표자'],
            'phone': ['연락처'],
            'mobile': ['휴대폰'],
            'email': ['이메일'],
            'approval_status': ['승인'],
            'memo': ['비고'],
        }
        
        df = pd.DataFrame(data)
        buffer = BytesIO()
        with pd.ExcelWriter(buffer, engine='openpyxl') as writer:
            df.to_excel(writer, index=False, sheet_name='회원업로드양식')
        buffer.seek(0)
        
        return send_file(
            buffer,
            as_attachment=True,
            download_name='회원업로드양식.xlsx',
            mimetype='application/vnd.openxmlformats-officedocument.spreadsheetml.sheet',
        )
    except Exception as e:
        try:
            import sys
            sys.stderr.write(f"Excel template download error: {e}\n")
        except Exception:
            pass
        flash('양식 다운로드 중 오류가 발생했습니다.', 'danger')
        return redirect(url_for('partner_admin_member_approval'))

# 파트너그룹 책임보험승인 페이지
@app.route('/partner/admin/insurance-approval', methods=['GET', 'POST'])
def partner_admin_insurance_approval():
    try:
        ensure_initialized()
        
        # 파트너그룹 관리자만 접근 가능
        if 'user_type' not in session or session['user_type'] != 'partner_admin':
            flash('파트너그룹 관리자만 접근 가능합니다.', 'warning')
            return redirect(url_for('partner_dashboard'))
        
        partner_group_id = session.get('partner_group_id')
        if not partner_group_id:
            flash('파트너그룹 정보가 없습니다.', 'warning')
            return redirect(url_for('partner_dashboard'))
        
        partner_group = db.session.query(PartnerGroup).filter_by(id=partner_group_id).first()
        if partner_group:
            partner_group_name = partner_group.name
        else:
            partner_group_name = session.get('partner_group_name', '')
        
        if request.method == 'POST':
            action = request.form.get('action')
            
            if action == 'bulk_approve':
                # 일괄 승인
                applications = db.session.query(InsuranceApplication).filter_by(
                    partner_group_id=partner_group_id,
                    approved_at=None
                ).all()
                
                now = datetime.now(KST)
                for app in applications:
                    app.approved_at = now
                    app.status = '조합승인'
                    # 가입일/종료일 설정: 가입희망일자 기준
                    if app.desired_start_date and not app.start_at:
                        app.start_at = datetime.combine(app.desired_start_date, datetime.min.time(), tzinfo=KST)
                    if app.start_at and not app.end_at:
                        from datetime import timedelta
                        app.end_at = app.start_at + timedelta(days=30)
                
                try:
                    import sys
                    sys.stderr.write(f"Insurance bulk approve: Approving {len(applications)} applications\n")
                except Exception:
                    pass
                
                commit_success = safe_commit()
                
                if commit_success:
                    try:
                        import sys
                        sys.stderr.write(f"Insurance bulk approve: Successfully approved {len(applications)} applications\n")
                    except Exception:
                        pass
                    flash(f'{len(applications)}건이 일괄 승인되었습니다.', 'success')
                else:
                    try:
                        import sys
                        import traceback
                        sys.stderr.write(f"Insurance bulk approve: Commit failed\n")
                        sys.stderr.write(f"Insurance bulk approve traceback: {traceback.format_exc()}\n")
                    except Exception:
                        pass
                    flash('일괄 승인 처리 중 오류가 발생했습니다.', 'danger')
            
            elif action in ['save', 'delete', 'approve']:
                app_id = request.form.get('app_id')
                if app_id:
                    application = db.session.get(InsuranceApplication, int(app_id))
                    if application and application.partner_group_id == partner_group_id:
                        if action == 'approve':
                            try:
                                app_id = application.id
                                now = datetime.now(KST)
                                application.approved_at = now
                                application.status = '조합승인'
                                if application.desired_start_date and not application.start_at:
                                    application.start_at = datetime.combine(application.desired_start_date, datetime.min.time(), tzinfo=KST)
                                if application.start_at and not application.end_at:
                                    from datetime import timedelta
                                    application.end_at = application.start_at + timedelta(days=30)
                                
                                try:
                                    import sys
                                    sys.stderr.write(f"Insurance approve: Approving application ID {app_id}\n")
                                except Exception:
                                    pass
                                
                                commit_success = safe_commit()
                                
                                if commit_success:
                                    try:
                                        import sys
                                        sys.stderr.write(f"Insurance approve: Successfully approved application ID {app_id}\n")
                                    except Exception:
                                        pass
                                    flash('승인되었습니다.', 'success')
                                else:
                                    try:
                                        import sys
                                        import traceback
                                        sys.stderr.write(f"Insurance approve: Commit failed for ID {app_id}\n")
                                        sys.stderr.write(f"Insurance approve traceback: {traceback.format_exc()}\n")
                                    except Exception:
                                        pass
                                    flash('승인 처리 중 오류가 발생했습니다.', 'danger')
                            except Exception as e:
                                try:
                                    import sys
                                    import traceback
                                    sys.stderr.write(f"Insurance approve error: {e}\n")
                                    sys.stderr.write(f"Insurance approve traceback: {traceback.format_exc()}\n")
                                except Exception:
                                    pass
                                try:
                                    db.session.rollback()
                                except Exception:
                                    pass
                                flash('승인 처리 중 오류가 발생했습니다.', 'danger')
                        
                        elif action == 'delete':
                            try:
                                app_id = application.id
                                db.session.delete(application)
                                
                                try:
                                    import sys
                                    sys.stderr.write(f"Insurance delete (approval): Deleting application ID {app_id}\n")
                                except Exception:
                                    pass
                                
                                commit_success = safe_commit()
                                
                                if commit_success:
                                    try:
                                        import sys
                                        sys.stderr.write(f"Insurance delete (approval): Successfully deleted ID {app_id}\n")
                                    except Exception:
                                        pass
                                    flash('삭제되었습니다.', 'success')
                                else:
                                    try:
                                        import sys
                                        import traceback
                                        sys.stderr.write(f"Insurance delete (approval): Commit failed for ID {app_id}\n")
                                        sys.stderr.write(f"Insurance delete traceback: {traceback.format_exc()}\n")
                                    except Exception:
                                        pass
                                    flash('삭제 처리 중 오류가 발생했습니다.', 'danger')
                            except Exception as e:
                                try:
                                    import sys
                                    import traceback
                                    sys.stderr.write(f"Insurance deletion error: {e}\n")
                                    sys.stderr.write(f"Insurance deletion traceback: {traceback.format_exc()}\n")
                                except Exception:
                                    pass
                                try:
                                    db.session.rollback()
                                except Exception:
                                    pass
                                flash('삭제 처리 중 오류가 발생했습니다.', 'danger')
                        
                        elif action == 'save':
                            try:
                                app_id = application.id
                                application.desired_start_date = parse_date(request.form.get('desired_start_date', ''))
                                application.car_plate = request.form.get('car_plate', '').strip()
                                application.vin = request.form.get('vin', '').strip()
                                application.car_name = request.form.get('car_name', '').strip()
                                application.car_registered_at = parse_date(request.form.get('car_registered_at', ''))
                                application.insured_code = request.form.get('insured_code', '').strip()
                                application.contractor_code = request.form.get('contractor_code', '').strip()
                                application.memo = request.form.get('memo', '').strip()
                                
                                try:
                                    import sys
                                    sys.stderr.write(f"Insurance save (approval): Updating application ID {app_id}\n")
                                except Exception:
                                    pass
                                
                                commit_success = safe_commit()
                                
                                if commit_success:
                                    try:
                                        import sys
                                        verify_app = db.session.get(InsuranceApplication, app_id)
                                        if verify_app:
                                            sys.stderr.write(f"Insurance save (approval): Verified application ID {app_id} exists\n")
                                        else:
                                            sys.stderr.write(f"Insurance save (approval): WARNING - Application ID {app_id} not found after commit!\n")
                                    except Exception:
                                        pass
                                    flash('저장되었습니다.', 'success')
                                else:
                                    try:
                                        import sys
                                        import traceback
                                        sys.stderr.write(f"Insurance save (approval): Commit failed for ID {app_id}\n")
                                        sys.stderr.write(f"Insurance save traceback: {traceback.format_exc()}\n")
                                    except Exception:
                                        pass
                                    flash('저장 처리 중 오류가 발생했습니다.', 'danger')
                            except Exception as e:
                                try:
                                    import sys
                                    import traceback
                                    sys.stderr.write(f"Insurance save error: {e}\n")
                                    sys.stderr.write(f"Insurance save traceback: {traceback.format_exc()}\n")
                                except Exception:
                                    pass
                                try:
                                    db.session.rollback()
                                except Exception:
                                    pass
                                flash('저장 처리 중 오류가 발생했습니다.', 'danger')
            
            return redirect(url_for('partner_admin_insurance_approval', 
                                  req_start=request.args.get('req_start'),
                                  req_end=request.args.get('req_end'),
                                  approved=request.args.get('approved'),
                                  appr_start=request.args.get('appr_start'),
                                  appr_end=request.args.get('appr_end'),
                                  edit_id=request.args.get('edit_id')))
        
        # 검색 조건
        req_start = parse_date(request.args.get('req_start', ''))
        req_end = parse_date(request.args.get('req_end', ''))
        approved = request.args.get('approved', '전체')
        appr_start = parse_date(request.args.get('appr_start', ''))
        appr_end = parse_date(request.args.get('appr_end', ''))
        edit_id = request.args.get('edit_id')
        
        q = db.session.query(InsuranceApplication).filter_by(partner_group_id=partner_group_id)
        
        # 신청시간 기준 검색
        if req_start:
            q = q.filter(InsuranceApplication.created_at >= datetime.combine(req_start, datetime.min.time(), tzinfo=KST))
        if req_end:
            q = q.filter(InsuranceApplication.created_at <= datetime.combine(req_end, datetime.max.time(), tzinfo=KST))
        
        # 승인여부 검색
        if approved == '승인':
            q = q.filter(InsuranceApplication.approved_at.is_not(None))
        elif approved == '미승인':
            q = q.filter(InsuranceApplication.approved_at.is_(None))
        
        # 조합승인시간 기준 검색
        if appr_start:
            q = q.filter(InsuranceApplication.approved_at >= datetime.combine(appr_start, datetime.min.time(), tzinfo=KST))
        if appr_end:
            q = q.filter(InsuranceApplication.approved_at <= datetime.combine(appr_end, datetime.max.time(), tzinfo=KST))
        
        applications = q.order_by(InsuranceApplication.created_at.desc()).all()
        
        # 상태 재계산
        for app in applications:
            app.recompute_status()
        safe_commit()
        
        return render_template('partner/admin_insurance_approval.html',
                             applications=applications,
                             partner_group_name=partner_group_name,
                             req_start=req_start,
                             req_end=req_end,
                             approved=approved,
                             appr_start=appr_start,
                             appr_end=appr_end,
                             edit_id=edit_id)
        
    except Exception as e:
        try:
            import sys
            import traceback
            sys.stderr.write(f"Partner admin insurance approval error: {e}\n")
            sys.stderr.write(f"Partner admin insurance approval traceback: {traceback.format_exc()}\n")
        except Exception:
            pass
        flash('페이지 로드 중 오류가 발생했습니다.', 'danger')
        return redirect(url_for('partner_admin'))

# 파트너그룹 책임보험승인 엑셀 다운로드
@app.route('/partner/admin/insurance-approval/export')
def partner_admin_insurance_approval_export():
    try:
        ensure_initialized()
        
        # 파트너그룹 관리자만 접근 가능
        if 'user_type' not in session or session['user_type'] != 'partner_admin':
            flash('파트너그룹 관리자만 접근 가능합니다.', 'warning')
            return redirect(url_for('partner_dashboard'))
        
        partner_group_id = session.get('partner_group_id')
        if not partner_group_id:
            flash('파트너그룹 정보가 없습니다.', 'warning')
            return redirect(url_for('partner_dashboard'))
        
        import pandas as pd
        from io import BytesIO
        
        # 검색 조건 (동일한 필터 적용)
        req_start = parse_date(request.args.get('req_start', ''))
        req_end = parse_date(request.args.get('req_end', ''))
        approved = request.args.get('approved', '전체')
        appr_start = parse_date(request.args.get('appr_start', ''))
        appr_end = parse_date(request.args.get('appr_end', ''))
        
        if db is None:
            flash('데이터베이스가 초기화되지 않았습니다.', 'danger')
            return redirect(url_for('partner_admin_insurance_approval'))
        
        # 보험신청 데이터 조회 (동일한 필터)
        q = db.session.query(InsuranceApplication).filter_by(partner_group_id=partner_group_id)
        
        # 신청시간 기준 검색
        if req_start:
            q = q.filter(InsuranceApplication.created_at >= datetime.combine(req_start, datetime.min.time(), tzinfo=KST))
        if req_end:
            q = q.filter(InsuranceApplication.created_at <= datetime.combine(req_end, datetime.max.time(), tzinfo=KST))
        
        # 승인여부 검색
        if approved == '승인':
            q = q.filter(InsuranceApplication.approved_at.is_not(None))
        elif approved == '미승인':
            q = q.filter(InsuranceApplication.approved_at.is_(None))
        
        # 조합승인시간 기준 검색
        if appr_start:
            q = q.filter(InsuranceApplication.approved_at >= datetime.combine(appr_start, datetime.min.time(), tzinfo=KST))
        if appr_end:
            q = q.filter(InsuranceApplication.approved_at <= datetime.combine(appr_end, datetime.max.time(), tzinfo=KST))
        
        applications = q.order_by(InsuranceApplication.created_at.desc()).all()
        
        # 엑셀 데이터 생성
        data = []
        for app in applications:
            data.append({
                '순번': len(data) + 1,
                '상사명': app.created_by_member.company_name if app.created_by_member else '',
                '대표자': app.created_by_member.representative if app.created_by_member else '',
                '사업자번호': app.created_by_member.business_number if app.created_by_member else '',
                '신청시간': app.created_at.strftime('%Y-%m-%d %H:%M:%S') if app.created_at else '',
                '가입희망일자': app.desired_start_date.strftime('%Y-%m-%d') if app.desired_start_date else '',
                '가입시간': app.start_at.strftime('%Y-%m-%d %H:%M:%S') if app.start_at else '',
                '종료시간': app.end_at.strftime('%Y-%m-%d %H:%M:%S') if app.end_at else '',
                '조합승인시간': app.approved_at.strftime('%Y-%m-%d %H:%M:%S') if app.approved_at else '',
                '피보험자코드': app.insured_code or '',
                '계약자코드': app.contractor_code or '',
                '한글차량번호': app.car_plate or '',
                '차대번호': app.vin or '',
                '차량명': app.car_name or '',
                '차량등록일자': app.car_registered_at.strftime('%Y-%m-%d') if app.car_registered_at else '',
                '보험료': app.premium or 0,
                '상태': app.status or '',
                '비고': app.memo or '',
            })
        
        df = pd.DataFrame(data)
        buffer = BytesIO()
        with pd.ExcelWriter(buffer, engine='openpyxl') as writer:
            df.to_excel(writer, index=False, sheet_name='책임보험승인')
        buffer.seek(0)
        
        partner_group = db.session.query(PartnerGroup).filter_by(id=partner_group_id).first()
        partner_group_name = partner_group.name if partner_group else '파트너그룹'
        filename = f'{partner_group_name}_책임보험승인_{datetime.now(KST).strftime("%Y%m%d_%H%M%S")}.xlsx'
        return send_file(
            buffer,
            as_attachment=True,
            download_name=filename,
            mimetype='application/vnd.openxmlformats-officedocument.spreadsheetml.sheet',
        )
    except Exception as e:
        try:
            import sys
            import traceback
            sys.stderr.write(f"Partner admin insurance approval export error: {e}\n")
            sys.stderr.write(f"Partner admin insurance approval export traceback: {traceback.format_exc()}\n")
        except Exception:
            pass
        flash('엑셀 다운로드 중 오류가 발생했습니다.', 'danger')
        return redirect(url_for('partner_admin_insurance_approval'))

# 파트너그룹 정산 페이지
@app.route('/partner/admin/settlement', methods=['GET', 'POST'])
def partner_admin_settlement():
    try:
        ensure_initialized()
        
        # 파트너그룹 관리자만 접근 가능
        if 'user_type' not in session or session['user_type'] != 'partner_admin':
            flash('파트너그룹 관리자만 접근 가능합니다.', 'warning')
            return redirect(url_for('partner_dashboard'))
        
        partner_group_id = session.get('partner_group_id')
        if not partner_group_id:
            flash('파트너그룹 정보가 없습니다.', 'warning')
            return redirect(url_for('partner_dashboard'))
        
        partner_group = db.session.query(PartnerGroup).filter_by(id=partner_group_id).first()
        if not partner_group:
            flash('파트너그룹을 찾을 수 없습니다.', 'warning')
            return redirect(url_for('partner_dashboard'))
        
        partner_group_name = partner_group.name if partner_group else ''
        
        # 현재 날짜를 timezone-aware로 가져오기
        now = datetime.now(KST)
        try:
            year = int(request.args.get('year', now.year))
        except (ValueError, TypeError):
            year = now.year
        try:
            month = int(request.args.get('month', now.month))
        except (ValueError, TypeError):
            month = now.month
        
        # 월 유효성 검사
        if month < 1 or month > 12:
            month = now.month
        if year < 2000 or year > 2100:
            year = now.year
        
        # 정산 데이터 계산
        start_period = datetime(year, month, 1, tzinfo=KST)
        if month == 12:
            next_month = datetime(year + 1, 1, 1, tzinfo=KST)
        else:
            next_month = datetime(year, month + 1, 1, tzinfo=KST)
        
        applications = db.session.query(InsuranceApplication).filter(
            InsuranceApplication.partner_group_id == partner_group_id,
            InsuranceApplication.start_at.is_not(None),
            InsuranceApplication.start_at >= start_period,
            InsuranceApplication.start_at < next_month,
        ).all()
        
        # 회사별 정산 데이터 집계
        settlements = {}
        for app in applications:
            try:
                # created_by_member가 None일 수 있으므로 안전하게 처리
                if app.created_by_member:
                    company_name = app.created_by_member.company_name or ''
                    representative = app.created_by_member.representative or ''
                    business_number = app.created_by_member.business_number or ''
                    
                    company_key = (company_name, representative, business_number)
                    if company_key not in settlements:
                        settlements[company_key] = {
                            'company_name': company_name,
                            'representative': representative,
                            'business_number': business_number,
                            'count': 0,
                            'amount': 0
                        }
                    settlements[company_key]['count'] += 1
                    settlements[company_key]['amount'] += 9500  # 건수 × 9,500원
            except Exception as app_err:
                try:
                    import sys
                    sys.stderr.write(f"Settlement aggregation error for app {app.id}: {app_err}\n")
                except Exception:
                    pass
                continue
        
        total_count = sum(s['count'] for s in settlements.values())
        total_amount = sum(s['amount'] for s in settlements.values())
        
        # PartnerGroup 객체를 딕셔너리로 변환 (JSON 직렬화 가능하도록)
        partner_group_dict = None
        if partner_group:
            partner_group_dict = {
                'id': partner_group.id,
                'name': partner_group.name,
                'business_number': partner_group.business_number or '',
                'representative': partner_group.representative or '',
                'phone': partner_group.phone or '',
                'mobile': partner_group.mobile or '',
                'address': partner_group.address or '',
                'bank_name': partner_group.bank_name or '',
                'account_number': partner_group.account_number or '',
            }
        
        return render_template('partner/admin_settlement.html',
                             settlements=list(settlements.values()),
                             partner_group=partner_group_dict,
                             partner_group_name=partner_group_name,
                             year=year,
                             month=month,
                             total_count=total_count,
                             total_amount=total_amount)
        
    except Exception as e:
        try:
            import sys
            import traceback
            sys.stderr.write(f"Partner admin settlement error: {e}\n")
            sys.stderr.write(f"Partner admin settlement traceback: {traceback.format_exc()}\n")
        except Exception:
            pass
        flash('페이지 로드 중 오류가 발생했습니다.', 'danger')
        return redirect(url_for('partner_admin'))


@app.route('/partner/admin/settlement/export')
def partner_admin_settlement_export():
    """파트너 정산 결과를 엑셀로 다운로드"""
    try:
        ensure_initialized()
        import pandas as pd
        from io import BytesIO
        
        # 파트너그룹 관리자만 접근 가능
        if 'user_type' not in session or session['user_type'] != 'partner_admin':
            flash('파트너그룹 관리자만 접근 가능합니다.', 'warning')
            return redirect(url_for('partner_dashboard'))
        
        partner_group_id = session.get('partner_group_id')
        if not partner_group_id:
            flash('파트너그룹 정보가 없습니다.', 'warning')
            return redirect(url_for('partner_dashboard'))
        
        partner_group = db.session.query(PartnerGroup).filter_by(id=partner_group_id).first()
        if not partner_group:
            flash('파트너그룹을 찾을 수 없습니다.', 'warning')
            return redirect(url_for('partner_dashboard'))
        
        partner_group_name = partner_group.name if partner_group else ''
        
        # 년도/월 파라미터 처리
        now = datetime.now(KST)
        try:
            year = int(request.args.get('year', now.year))
        except (ValueError, TypeError):
            year = now.year
        try:
            month = int(request.args.get('month', now.month))
        except (ValueError, TypeError):
            month = now.month
        
        # 월 유효성 검사
        if month < 1 or month > 12:
            month = now.month
        if year < 2000 or year > 2100:
            year = now.year
        
        # 정산 데이터 계산 (partner_admin_settlement와 동일한 로직)
        start_period = datetime(year, month, 1, tzinfo=KST)
        if month == 12:
            next_month = datetime(year + 1, 1, 1, tzinfo=KST)
        else:
            next_month = datetime(year, month + 1, 1, tzinfo=KST)
        
        applications = db.session.query(InsuranceApplication).filter(
            InsuranceApplication.partner_group_id == partner_group_id,
            InsuranceApplication.start_at.is_not(None),
            InsuranceApplication.start_at >= start_period,
            InsuranceApplication.start_at < next_month,
        ).all()
        
        # 회사별 정산 데이터 집계
        settlements = {}
        for app in applications:
            try:
                if app.created_by_member:
                    company_name = app.created_by_member.company_name or ''
                    representative = app.created_by_member.representative or ''
                    business_number = app.created_by_member.business_number or ''
                    
                    company_key = (company_name, representative, business_number)
                    if company_key not in settlements:
                        settlements[company_key] = {
                            'company_name': company_name,
                            'representative': representative,
                            'business_number': business_number,
                            'count': 0,
                            'amount': 0
                        }
                    settlements[company_key]['count'] += 1
                    settlements[company_key]['amount'] += 9500  # 건수 × 9,500원
            except Exception:
                continue
        
        # 엑셀 데이터 생성
        data = []
        row_num = 1
        total_count = 0
        total_amount = 0
        
        for company_key in sorted(settlements.keys()):
            settlement = settlements[company_key]
            total_count += settlement['count']
            total_amount += settlement['amount']
            
            data.append({
                '순번': row_num,
                '상사명': settlement['company_name'],
                '대표자': settlement['representative'],
                '사업자번호': settlement['business_number'],
                '건수': settlement['count'],
                '금액': settlement['amount'],
                '비고': '',
            })
            row_num += 1
        
        # 합계 행 추가
        if data:
            data.append({
                '순번': '',
                '상사명': '합계',
                '대표자': '',
                '사업자번호': '',
                '건수': total_count,
                '금액': total_amount,
                '비고': '',
            })
        
        # DataFrame 생성 및 엑셀 파일 생성
        df = pd.DataFrame(data)
        buffer = BytesIO()
        with pd.ExcelWriter(buffer, engine='openpyxl') as writer:
            df.to_excel(writer, index=False, sheet_name='정산내역')
        buffer.seek(0)
        
        filename = f'{partner_group_name}_정산내역_{year}년{month}월_{datetime.now(KST).strftime("%Y%m%d_%H%M%S")}.xlsx'
        return send_file(
            buffer,
            as_attachment=True,
            download_name=filename,
            mimetype='application/vnd.openxmlformats-officedocument.spreadsheetml.sheet',
        )
        
    except Exception as e:
        try:
            import sys
            import traceback
            sys.stderr.write(f"Partner admin settlement export error: {e}\n")
            sys.stderr.write(f"Traceback: {traceback.format_exc()}\n")
        except Exception:
            pass
        flash('엑셀 다운로드 중 오류가 발생했습니다.', 'danger')
        return redirect(url_for('partner_admin_settlement'))


@app.route('/admin')
@login_required
@admin_required
def admin_home():
    # 전체대시보드로 리다이렉트
    return redirect(url_for('admin_dashboard'))


@app.route('/admin/members', methods=['GET', 'POST'])
@login_required
@admin_required
def admin_members():
    ensure_initialized()  # Ensure initialization
    if request.method == 'POST':
        action = request.form.get('action')
        member_id = request.form.get('member_id')
        if member_id:
            m = db.session.get(Member, int(member_id))
            if m:
                if action == 'update_status':
                    m.approval_status = request.form.get('approval_status', '신청')
                    if not safe_commit():
                        flash('승인 상태 변경 중 오류가 발생했습니다.', 'danger')
                    else:
                        flash('승인 상태가 변경되었습니다.', 'success')
                elif action == 'save':
                    m.company_name = request.form.get('company_name', '').strip()
                    m.address = request.form.get('address', '').strip()
                    m.corporation_number = request.form.get('corporation_number', '').strip()
                    m.representative = request.form.get('representative', '').strip()
                    m.phone = request.form.get('phone', '').strip()
                    m.mobile = request.form.get('mobile', '').strip()
                    m.email = request.form.get('email', '').strip()
                    m.approval_status = request.form.get('approval_status', '신청')
                    m.role = request.form.get('role', m.role or 'member')
                    m.memo = request.form.get('memo', '').strip()
                    
                    # 사업자등록증 파일 업로드 처리
                    if 'registration_cert' in request.files:
                        try:
                            file = request.files['registration_cert']
                            if file and file.filename:
                                allowed_extensions = {'.pdf', '.jpg', '.jpeg', '.png'}
                                file_ext = os.path.splitext(file.filename)[1].lower()
                                if file_ext in allowed_extensions:
                                    # 업로드 디렉토리 확인 및 생성
                                    os.makedirs(UPLOAD_DIR, exist_ok=True)
                                    
                                    # 기존 파일이 있으면 삭제 (선택사항)
                                    if m.registration_cert_path:
                                        old_filepath = os.path.join(UPLOAD_DIR, m.registration_cert_path.split('/')[-1])
                                        try:
                                            if os.path.exists(old_filepath):
                                                os.remove(old_filepath)
                                        except Exception:
                                            pass  # 기존 파일 삭제 실패해도 계속 진행
                                    
                                    # 새 파일 저장
                                    timestamp = datetime.now(KST).strftime('%Y%m%d_%H%M%S')
                                    business_number = m.business_number or 'unknown'
                                    filename = f"{business_number}_{timestamp}{file_ext}"
                                    filepath = os.path.join(UPLOAD_DIR, filename)
                                    file.save(filepath)
                                    
                                    # 파일이 실제로 저장되었는지 확인
                                    if os.path.exists(filepath) and os.path.getsize(filepath) > 0:
                                        m.registration_cert_path = os.path.join('uploads', filename)
                                        print(f"Registration cert updated successfully: {m.registration_cert_path}")
                                    else:
                                        print(f"ERROR: Registration cert file was not saved properly. Path: {filepath}")
                                else:
                                    flash(f'허용되지 않은 파일 형식입니다. (PDF, JPG, PNG만 가능)', 'warning')
                        except Exception as e:
                            try:
                                import sys
                                import traceback
                                sys.stderr.write(f"Registration cert upload error: {e}\n")
                                sys.stderr.write(f"Traceback: {traceback.format_exc()}\n")
                            except Exception:
                                pass
                            flash('사업자등록증 파일 업로드 중 오류가 발생했습니다.', 'warning')
                            # 파일 업로드 실패해도 다른 정보는 저장
                    
                    if not safe_commit():
                        flash('저장 처리 중 오류가 발생했습니다.', 'danger')
                    else:
                        flash('저장되었습니다.', 'success')
                    return redirect(url_for('admin_members'))
                elif action == 'delete':
                    db.session.delete(m)
                    if not safe_commit():
                        flash('삭제 처리 중 오류가 발생했습니다.', 'danger')
                    else:
                        flash('삭제되었습니다.', 'success')
        else:
            if action == 'create':
                # 파트너그룹 ID 처리
                partner_group_id = request.form.get('partner_group_id', '').strip()
                partner_group_id_int = None
                if partner_group_id:
                    try:
                        partner_group_id_int = int(partner_group_id)
                    except (ValueError, TypeError):
                        flash('올바른 파트너그룹을 선택해주세요.', 'warning')
                        return redirect(url_for('admin_members'))
                
                username = request.form.get('username', '').strip()
                password = request.form.get('password', 'temp1234')
                company_name = request.form.get('company_name', '').strip()
                address = request.form.get('address', '').strip()
                business_number = request.form.get('business_number', '').strip()
                corporation_number = request.form.get('corporation_number', '').strip()
                representative = request.form.get('representative', '').strip()
                phone = request.form.get('phone', '').strip()
                mobile = request.form.get('mobile', '').strip()
                email = request.form.get('email', '').strip()
                member_type = request.form.get('member_type', '법인')
                privacy_agreement = request.form.get('privacy_agreement') == 'on'
                approval_status = request.form.get('approval_status', '승인')
                role = request.form.get('role', 'member')
                
                if not username or not company_name or not business_number:
                    flash('아이디/상사명/사업자번호는 필수입니다.', 'warning')
                elif not partner_group_id_int:
                    flash('파트너그룹을 선택해주세요.', 'warning')
                elif db.session.query(Member).filter((Member.username == username) | (Member.business_number == business_number)).first():
                    flash('이미 존재하는 아이디 또는 사업자번호입니다.', 'danger')
                else:
                    # 사업자등록증 파일 업로드 처리
                    registration_cert_path = None
                    if 'registration_cert' in request.files:
                        try:
                            file = request.files['registration_cert']
                            if file and file.filename:
                                allowed_extensions = {'.pdf', '.jpg', '.jpeg', '.png'}
                                file_ext = os.path.splitext(file.filename)[1].lower()
                                if file_ext in allowed_extensions:
                                    # 업로드 디렉토리 확인 및 생성
                                    os.makedirs(UPLOAD_DIR, exist_ok=True)
                                    
                                    timestamp = datetime.now(KST).strftime('%Y%m%d_%H%M%S')
                                    filename = f"{business_number}_{timestamp}{file_ext}"
                                    filepath = os.path.join(UPLOAD_DIR, filename)
                                    file.save(filepath)
                                    
                                    # 파일이 실제로 저장되었는지 확인
                                    if os.path.exists(filepath) and os.path.getsize(filepath) > 0:
                                        registration_cert_path = os.path.join('uploads', filename)
                                        print(f"Registration cert saved successfully: {registration_cert_path}")
                                    else:
                                        print(f"ERROR: Registration cert file was not saved properly. Path: {filepath}")
                                else:
                                    flash(f'허용되지 않은 파일 형식입니다. (PDF, JPG, PNG만 가능)', 'warning')
                        except Exception as e:
                            try:
                                import sys
                                import traceback
                                sys.stderr.write(f"Registration cert upload error: {e}\n")
                                sys.stderr.write(f"Traceback: {traceback.format_exc()}\n")
                            except Exception:
                                pass
                            flash('사업자등록증 파일 업로드 중 오류가 발생했습니다.', 'warning')
                    
                    nm = Member(
                        partner_group_id=partner_group_id_int,
                        username=username,
                        company_name=company_name,
                        address=address,
                        business_number=business_number,
                        corporation_number=corporation_number,
                        representative=representative,
                        phone=phone,
                        mobile=mobile,
                        email=email,
                        member_type=member_type,
                        privacy_agreement=privacy_agreement if member_type == '개인' else False,
                        registration_cert_path=registration_cert_path,
                        approval_status=approval_status,
                        role=role,
                    )
                    nm.set_password(password)
                    db.session.add(nm)
                    if not safe_commit():
                        flash('회원 추가 중 오류가 발생했습니다.', 'danger')
                    else:
                        flash('회원이 추가되었습니다.', 'success')
                return redirect(url_for('admin_members'))

    edit_id = request.args.get('edit_id')
    if db is None:
        flash('데이터베이스가 초기화되지 않았습니다.', 'danger')
        return redirect(url_for('dashboard'))
    members = db.session.query(Member).order_by(Member.created_at.desc()).all()
    
    # 파트너그룹 목록 가져오기
    partner_groups = []
    try:
        partner_groups = db.session.query(PartnerGroup).order_by(PartnerGroup.name).all()
    except Exception:
        pass
    
    return render_template('admin/members.html', members=members, edit_id=edit_id, partner_groups=partner_groups)


@app.route('/admin/members/upload', methods=['POST'])
@login_required
@admin_required
def admin_members_upload():
    ensure_initialized()  # Ensure initialization
    if db is None:
        flash('데이터베이스가 초기화되지 않았습니다.', 'danger')
        return redirect(url_for('admin_members'))
    if is_serverless:
        flash('Vercel 환경에서는 엑셀 업로드가 제한됩니다.', 'warning')
        return redirect(url_for('admin_members'))
    file = request.files.get('file')
    if not file:
        flash('엑셀 파일을 선택하세요.', 'warning')
        return redirect(url_for('admin_members'))
    try:
        # Import pandas only when needed
        import pandas as pd
        df = pd.read_excel(file)
        created = 0
        skipped = 0
        required_cols = {'username', 'company_name', 'business_number'}
        if not required_cols.issubset(set(df.columns)):
            flash('엑셀 컬럼이 올바르지 않습니다. (필수: username, company_name, business_number)', 'danger')
            return redirect(url_for('admin_members'))
        for _, row in df.iterrows():
            username = str(row.get('username', '')).strip()
            company_name = str(row.get('company_name', '')).strip()
            business_number = str(row.get('business_number', '')).strip()
            if not username or not company_name or not business_number:
                skipped += 1
                continue
            # Dup checks
            if db.session.query(Member).filter((Member.username == username) | (Member.business_number == business_number)).first():
                skipped += 1
                continue
            m = Member(
                username=username,
                company_name=company_name,
                address=str(row.get('address', '') or '').strip(),
                business_number=business_number,
                corporation_number=str(row.get('corporation_number', '') or '').strip(),
                representative=str(row.get('representative', '') or '').strip(),
                phone=str(row.get('phone', '') or '').strip(),
                mobile=str(row.get('mobile', '') or '').strip(),
                email=str(row.get('email', '') or '').strip(),
                approval_status=str(row.get('approval_status', '승인') or '승인').strip() or '승인',
                role=str(row.get('role', 'member') or 'member').strip() or 'member',
            )
            password = str(row.get('password', 'temp1234'))
            m.set_password(password)
            db.session.add(m)
            created += 1
        if not safe_commit():
            flash('일괄 업로드 처리 중 오류가 발생했습니다.', 'danger')
        else:
            flash(f'일괄 업로드 완료: 추가 {created}건, 건너뜀 {skipped}건', 'success')
    except Exception:
        flash('업로드 처리 중 오류가 발생했습니다.', 'danger')
    return redirect(url_for('admin_members'))


@app.route('/admin/insurance', methods=['GET', 'POST'])
@login_required
@admin_required
def admin_insurance():
    ensure_initialized()  # Ensure initialization
    if request.method == 'POST':
        if request.form.get('bulk_approve') == '1':
            # 일괄 승인: 미승인(= status == 신청)인 데이터 모두 승인 시간 부여
            if db is None:
                flash('데이터베이스가 초기화되지 않았습니다.', 'danger')
                return redirect(url_for('admin_insurance'))
            rows = db.session.query(InsuranceApplication).filter(
                (InsuranceApplication.approved_at.is_(None))
            ).all()
            now = datetime.now(KST)
            for r in rows:
                r.approved_at = now
                r.status = '조합승인'
                # 가입일/종료일 설정: 가입희망일자 기준으로 세팅, 종료는 30일 후
                if not r.start_at:
                    start_date_aware = datetime.combine(r.desired_start_date, datetime.min.time(), tzinfo=KST)
                    r.start_at = start_date_aware
                    r.end_at = start_date_aware + timedelta(days=30)
            if not safe_commit():
                flash('일괄 승인 처리 중 오류가 발생했습니다.', 'danger')
            else:
                flash('일괄 승인되었습니다.', 'success')
        else:
            # 단건 수정/삭제
            action = request.form.get('action')
            row_id = request.form.get('row_id')
            print(f"DEBUG: action={action}, row_id={row_id}")  # 디버그 로그
            
            if row_id:
                try:
                    row = db.session.get(InsuranceApplication, int(row_id))
                    print(f"DEBUG: Found row={row}")  # 디버그 로그
                except Exception as e:
                    print(f"DEBUG: Error finding row: {e}")
                    row = None
            else:
                row = None
                
            if row and action:
                if action == 'approve':
                    print(f"DEBUG: Approving row {row.id}")  # 디버그 로그
                    row.approved_at = datetime.now(KST)
                    row.status = '조합승인'
                    # 가입일/종료일 설정: 가입희망일자 기준으로 세팅, 종료는 30일 후
                    if not row.start_at:
                        start_date_aware = datetime.combine(row.desired_start_date, datetime.min.time(), tzinfo=KST)
                        row.start_at = start_date_aware
                        row.end_at = start_date_aware + timedelta(days=30)
                    if not safe_commit():
                        flash('승인 처리 중 오류가 발생했습니다.', 'danger')
                    else:
                        flash('승인되었습니다.', 'success')
                        print(f"DEBUG: Approval completed for row {row.id}")  # 디버그 로그
                elif action == 'delete':
                    db.session.delete(row)
                    if not safe_commit():
                        flash('삭제 처리 중 오류가 발생했습니다.', 'danger')
                    else:
                        flash('삭제되었습니다.', 'success')
                elif action == 'save_memo':
                    row.memo = request.form.get('memo', row.memo)
                    if not safe_commit():
                        flash('비고 저장 중 오류가 발생했습니다.', 'danger')
                    else:
                        flash('비고가 저장되었습니다.', 'success')
                elif action == 'save':
                    # 편집 모드에서 저장
                    row.desired_start_date = parse_date(request.form.get('desired_start_date'))
                    row.car_plate = request.form.get('car_plate', '').strip()
                    row.vin = request.form.get('vin', '').strip()
                    row.car_name = request.form.get('car_name', '').strip()
                    row.car_registered_at = parse_date(request.form.get('car_registered_at'))
                    row.start_at = parse_datetime(request.form.get('start_at')) # 가입시간 수정 추가
                    row.end_at = parse_datetime(request.form.get('end_at'))   # 종료시간 수정 추가
                    row.memo = request.form.get('memo', '').strip()
                    # 보험증권 정보 저장
                    if hasattr(row, 'insurance_policy_path'):
                        row.insurance_policy_path = request.form.get('insurance_policy_path', '').strip() or None
                    if hasattr(row, 'insurance_policy_url'):
                        row.insurance_policy_url = request.form.get('insurance_policy_url', '').strip() or None
                    if not safe_commit():
                        flash('저장 처리 중 오류가 발생했습니다.', 'danger')
                    else:
                        flash('저장되었습니다.', 'success')
                    return redirect(url_for('admin_insurance', 
                                           req_start=request.args.get('req_start'),
                                           req_end=request.args.get('req_end'),
                                           approved=request.args.get('approved'),
                                           appr_start=request.args.get('appr_start'),
                                           appr_end=request.args.get('appr_end')))

    # Filters
    req_start = parse_date(request.args.get('req_start', ''))
    req_end = parse_date(request.args.get('req_end', ''))
    approved_filter = request.args.get('approved')  # 승인/미승인/전체
    appr_start = parse_date(request.args.get('appr_start', ''))
    appr_end = parse_date(request.args.get('appr_end', ''))
    edit_id = request.args.get('edit_id')

    if db is None:
        flash('데이터베이스가 초기화되지 않았습니다.', 'danger')
        return redirect(url_for('dashboard'))
    q = db.session.query(InsuranceApplication)
    if req_start:
        q = q.filter(InsuranceApplication.created_at >= datetime.combine(req_start, datetime.min.time(), tzinfo=KST))
    if req_end:
        q = q.filter(InsuranceApplication.created_at <= datetime.combine(req_end, datetime.max.time(), tzinfo=KST))
    if approved_filter == '승인':
        q = q.filter(InsuranceApplication.approved_at.is_not(None))
    elif approved_filter == '미승인':
        q = q.filter(InsuranceApplication.approved_at.is_(None))
    if appr_start:
        q = q.filter(InsuranceApplication.approved_at >= datetime.combine(appr_start, datetime.min.time(), tzinfo=KST))
    if appr_end:
        q = q.filter(InsuranceApplication.approved_at <= datetime.combine(appr_end, datetime.max.time(), tzinfo=KST))

    rows = q.order_by(InsuranceApplication.created_at.desc()).all()
    for r in rows:
        r.recompute_status()
    safe_commit()  # Status updates - don't show error if it fails

    # Build view models with pre-formatted strings to avoid tzlocal usage in templates
    def fmt_display(dt):
        if not dt:
            return ''
        try:
            # Ensure timezone-aware datetime and convert to KST
            if dt.tzinfo is None:
                # If naive, assume it's already in KST
                local_dt = dt.replace(tzinfo=KST)
            else:
                # Convert to KST
                local_dt = dt.astimezone(KST)
            return local_dt.strftime('%Y-%m-%d %H:%M')
        except Exception:
            return ''

    def fmt_input(dt):
        if not dt:
            return ''
        try:
            # Ensure timezone-aware datetime and convert to KST
            if dt.tzinfo is None:
                # If naive, assume it's already in KST
                local_dt = dt.replace(tzinfo=KST)
            else:
                # Convert to KST
                local_dt = dt.astimezone(KST)
            return local_dt.strftime('%Y-%m-%dT%H:%M')
        except Exception:
            return ''

    items = []
    for r in rows:
        items.append({
            'id': r.id,
            'created_by_company': (r.created_by_member.company_name if r.created_by_member else ''),
            'created_at_str': fmt_display(r.created_at),
            'desired_start_date': r.desired_start_date,
            'start_at_str': fmt_display(r.start_at),
            'end_at_str': fmt_display(r.end_at),
            'approved_at_str': fmt_display(r.approved_at),
            'start_at_input': fmt_input(r.start_at),
            'end_at_input': fmt_input(r.end_at),
            'insured_code': r.insured_code,
            'contractor_code': r.contractor_code,
            'car_plate': r.car_plate,
            'vin': r.vin,
            'car_name': r.car_name,
            'car_registered_at': r.car_registered_at,
            'approved_at': r.approved_at,
            'memo': r.memo or '',
        })

    return render_template('admin/insurance.html', rows=rows, items=items, edit_id=edit_id)


@app.route('/admin/insurance/download')
@login_required
@admin_required
def admin_insurance_download():
    ensure_initialized()  # Ensure initialization
    # Import pandas only when needed
    import pandas as pd
    
    if db is None:
        flash('데이터베이스가 초기화되지 않았습니다.', 'danger')
        return redirect(url_for('admin_insurance'))
    # Export to Excel
    rows = db.session.query(InsuranceApplication).order_by(InsuranceApplication.created_at.desc()).all()
    data = []
    for r in rows:
        data.append({
            '상사명': r.created_by_member.company_name if r.created_by_member else '',
            '신청시간': r.created_at,
            '가입희망일자': r.desired_start_date,
            '가입시간': r.start_at,
            '종료시간': r.end_at,
            '조합승인시간': r.approved_at,
            '피보험자코드': r.insured_code,
            '계약자코드': r.contractor_code,
            '한글차량번호': r.car_plate,
            '차대번호': r.vin,
            '차량명': r.car_name,
            '차량등록일자': r.car_registered_at,
            '보험료': r.premium,
            '조합승인': '승인' if r.approved_at else '미승인',
            '비고': r.memo or '',
        })
    df = pd.DataFrame(data)
    buffer = BytesIO()
    with pd.ExcelWriter(buffer, engine='openpyxl') as writer:
        df.to_excel(writer, index=False, sheet_name='data')
    buffer.seek(0)
    return send_file(
        buffer,
        as_attachment=True,
        download_name='insurance_data.xlsx',
        mimetype='application/vnd.openxmlformats-officedocument.spreadsheetml.sheet',
    )


@app.route('/admin/settlement')
@login_required
@admin_required
def admin_settlement():
    ensure_initialized()  # Ensure initialization
    year = int(request.args.get('year', datetime.now().year))
    month = int(request.args.get('month', datetime.now().month))

    # 기준: 책임보험 승인페이지에서 해당 년/월 데이터 (시작일 기준)
    start_period = datetime(year, month, 1, tzinfo=KST)
    if month == 12:
        next_month = datetime(year + 1, 1, 1, tzinfo=KST)
    else:
        next_month = datetime(year, month + 1, 1, tzinfo=KST)

    if db is None:
        flash('데이터베이스가 초기화되지 않았습니다.', 'danger')
        return redirect(url_for('admin_settlement'))
    rows = db.session.query(InsuranceApplication).filter(
        InsuranceApplication.start_at.is_not(None),
        InsuranceApplication.start_at >= start_period,
        InsuranceApplication.start_at < next_month,
    ).all()

    # 그룹핑: 상사별 건수/금액
    by_company = {}
    for r in rows:
        company = r.created_by_member.company_name if r.created_by_member else '미상'
        rep = r.created_by_member.representative if r.created_by_member else ''
        biz = r.created_by_member.business_number if r.created_by_member else ''
        key = (company, rep, biz)
        by_company.setdefault(key, 0)
        by_company[key] += 1

    settlements = []
    total_count = 0
    total_amount = 0
    for (company, rep, biz), count in by_company.items():
        amount = count * 9500
        total_count += count
        total_amount += amount
        settlements.append({
            'company': company,
            'representative': rep,
            'business_number': biz,
            'count': count,
            'amount': amount,
        })

    # Ensure total_count and total_amount are always integers (not None)
    total_count = int(total_count) if total_count else 0
    total_amount = int(total_amount) if total_amount else 0

    return render_template('admin/settlement.html', 
                          year=year, 
                          month=month, 
                          settlements=settlements, 
                          total_count=total_count, 
                          total_amount=total_amount)


@app.route('/admin/settlement/export')
@login_required
@admin_required
def admin_settlement_export():
    """정산 결과를 엑셀로 다운로드"""
    ensure_initialized()
    import pandas as pd
    from io import BytesIO
    
    try:
        year = int(request.args.get('year', datetime.now().year))
        month = int(request.args.get('month', datetime.now().month))
    except (ValueError, TypeError):
        flash('올바른 년도와 월을 입력해주세요.', 'warning')
        return redirect(url_for('admin_settlement'))
    
    # 월 유효성 검사
    if month < 1 or month > 12:
        flash('올바른 월을 입력해주세요. (1-12)', 'warning')
        return redirect(url_for('admin_settlement'))
    if year < 2000 or year > 2100:
        flash('올바른 년도를 입력해주세요.', 'warning')
        return redirect(url_for('admin_settlement'))
    
    if db is None:
        flash('데이터베이스가 초기화되지 않았습니다.', 'danger')
        return redirect(url_for('admin_settlement'))
    
    # 정산 데이터 계산 (admin_settlement와 동일한 로직)
    start_period = datetime(year, month, 1, tzinfo=KST)
    if month == 12:
        next_month = datetime(year + 1, 1, 1, tzinfo=KST)
    else:
        next_month = datetime(year, month + 1, 1, tzinfo=KST)
    
    rows = db.session.query(InsuranceApplication).filter(
        InsuranceApplication.start_at.is_not(None),
        InsuranceApplication.start_at >= start_period,
        InsuranceApplication.start_at < next_month,
    ).all()
    
    # 그룹핑: 상사별 건수/금액
    by_company = {}
    for r in rows:
        company = r.created_by_member.company_name if r.created_by_member else '미상'
        rep = r.created_by_member.representative if r.created_by_member else ''
        biz = r.created_by_member.business_number if r.created_by_member else ''
        key = (company, rep, biz)
        by_company.setdefault(key, 0)
        by_company[key] += 1
    
    # 엑셀 데이터 생성
    data = []
    row_num = 1
    total_count = 0
    total_amount = 0
    
    for (company, rep, biz), count in sorted(by_company.items()):
        amount = count * 9500
        total_count += count
        total_amount += amount
        
        data.append({
            '순번': row_num,
            '상사명': company,
            '대표자': rep,
            '사업자번호': biz,
            '건수': count,
            '금액': amount,
            '비고': '',
        })
        row_num += 1
    
    # 합계 행 추가
    if data:
        data.append({
            '순번': '',
            '상사명': '합계',
            '대표자': '',
            '사업자번호': '',
            '건수': total_count,
            '금액': total_amount,
            '비고': '',
        })
    
    # DataFrame 생성 및 엑셀 파일 생성
    df = pd.DataFrame(data)
    buffer = BytesIO()
    with pd.ExcelWriter(buffer, engine='openpyxl') as writer:
        df.to_excel(writer, index=False, sheet_name='정산내역')
    buffer.seek(0)
    
    filename = f'정산내역_{year}년{month}월_{datetime.now(KST).strftime("%Y%m%d_%H%M%S")}.xlsx'
    return send_file(
        buffer,
        as_attachment=True,
        download_name=filename,
        mimetype='application/vnd.openxmlformats-officedocument.spreadsheetml.sheet',
    )


@app.route('/admin/invoice')
@login_required
@admin_required
def admin_invoice():
    ensure_initialized()  # Ensure initialization
    company = request.args.get('company', '')
    representative = request.args.get('representative', '')
    business_number = request.args.get('business_number', '')
    try:
        year = int(request.args.get('year'))
        month = int(request.args.get('month'))
        count = int(request.args.get('count'))
        amount = int(request.args.get('amount'))
    except Exception:
        flash('요청 파라미터가 올바르지 않습니다.', 'danger')
        return redirect(url_for('admin_settlement'))
    return render_template('invoice.html',
                           company=company,
                           representative=representative,
                           business_number=business_number,
                           year=year,
                           month=month,
                           count=count,
                           amount=amount)


@app.route('/admin/invoice/batch')
@login_required
@admin_required
def admin_invoice_batch():
    ensure_initialized()  # Ensure initialization
    # Render a combined printable page for all companies for the selected month
    year = int(request.args.get('year', datetime.now().year))
    month = int(request.args.get('month', datetime.now().month))

    start_period = datetime(year, month, 1, tzinfo=KST)
    if month == 12:
        next_month = datetime(year + 1, 1, 1, tzinfo=KST)
    else:
        next_month = datetime(year, month + 1, 1, tzinfo=KST)

    if db is None:
        flash('데이터베이스가 초기화되지 않았습니다.', 'danger')
        return redirect(url_for('admin_settlement'))
    rows = db.session.query(InsuranceApplication).filter(
        InsuranceApplication.start_at.is_not(None),
        InsuranceApplication.start_at >= start_period,
        InsuranceApplication.start_at < next_month,
    ).all()

    by_company = {}
    for r in rows:
        company = r.created_by_member.company_name if r.created_by_member else '미상'
        rep = r.created_by_member.representative if r.created_by_member else ''
        biz = r.created_by_member.business_number if r.created_by_member else ''
        key = (company, rep, biz)
        by_company.setdefault(key, 0)
        by_company[key] += 1

    invoices = []
    for (company, rep, biz), count in by_company.items():
        amount = count * 9500
        invoices.append({
            'company': company,
            'representative': rep,
            'business_number': biz,
            'year': year,
            'month': month,
            'count': count,
            'amount': amount,
        })
    return render_template('invoice_batch.html', invoices=invoices)


@app.errorhandler(Exception)
def handle_unexpected_error(e):
    """Handle all unhandled exceptions with detailed logging"""
    # Let Flask/Flask-Login handle HTTPExceptions (e.g., 401 login required)
    if isinstance(e, HTTPException):
        return e
    
    # Log full stack trace for debugging
    import traceback
    error_trace = traceback.format_exc()
    error_msg = str(e)
    error_type = type(e).__name__
    
    # Log to stderr for Vercel (more reliable than app.logger)
    try:
        import sys
        sys.stderr.write(f"\n{'='*60}\n")
        sys.stderr.write(f"UNHANDLED EXCEPTION: {error_type}\n")
        sys.stderr.write(f"Error Message: {error_msg}\n")
        sys.stderr.write(f"Traceback:\n{error_trace}\n")
        sys.stderr.write(f"{'='*60}\n")
    except Exception:
        pass
    
    # Also log via Flask logger if available
    try:
        app.logger.exception("Unhandled exception")
    except Exception:
        pass
    
    # Try to provide user-friendly error message
    try:
        from flask import request, has_request_context
        
        if has_request_context():
            # Only flash if we're in a request context
            try:
                flash('서버 처리 중 오류가 발생했습니다.', 'danger')
            except Exception:
                pass
            
            # Try to redirect appropriately
            try:
                # Check authentication status safely
                is_auth = False
                try:
                    from flask_login import current_user
                    is_auth = getattr(current_user, 'is_authenticated', False)
                except Exception:
                    pass
                
                if not is_auth:
                    try:
                        return redirect(url_for('login'))
                    except Exception:
                        pass
                else:
                    try:
                        return redirect(url_for('dashboard'))
                    except Exception:
                        pass
            except Exception:
                pass
        
        # If redirect failed, show error page
        try:
            from flask import render_template
            return render_template('error.html', error_message=error_msg), 500
        except Exception:
            pass
        
    except Exception:
        pass
    
    # Ultimate fallback: return minimal error response
    try:
        return f"<h1>서버 오류</h1><p>오류가 발생했습니다: {error_type}</p>", 500
    except Exception:
        return ("서버 오류가 발생했습니다.", 500)


if __name__ == '__main__':
    app.run(host='0.0.0.0', port=int(os.environ.get('PORT', 8901)), debug=True)


