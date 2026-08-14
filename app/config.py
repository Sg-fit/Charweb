import os
basedir = os.path.abspath(os.path.dirname(__file__))
from dotenv import load_dotenv
load_dotenv(os.path.join(basedir, '.env'))
UPLOAD_FOLDER = os.path.join(basedir, 'app/static/uploads')
ALLOWED_EXTENSIONS = {'png', 'jpg', 'jpeg', 'gif', 'webp'}

class Config:
    SECRET_KEY = os.environ.get('SECRET_KEY') or 'KEY_XKI'
    # Postgres in production (durable, no row loss), SQLite fallback for local
    # dev. Some managed providers hand out a legacy 'postgres://' URL that
    # SQLAlchemy 2.x no longer accepts -- normalise it to 'postgresql://'.
    _db_url = os.environ.get('DATABASE_URL')
    if _db_url and _db_url.startswith('postgres://'):
        _db_url = _db_url.replace('postgres://', 'postgresql://', 1)
    SQLALCHEMY_DATABASE_URI = _db_url or \
        'sqlite:///' + os.path.join(basedir, 'app.db')
    # pool_pre_ping drops dead connections before use, so a Postgres idle
    # timeout doesn't surface as a mid-collection 500.
    SQLALCHEMY_ENGINE_OPTIONS = {'pool_pre_ping': True}
    MAIL_SERVER = os.environ.get('MAIL_SERVER')
    MAIL_PORT = int(os.environ.get('MAIL_PORT') or 587) if os.environ.get('MAIL_SERVER') else None
    MAIL_USE_TLS = os.environ.get('MAIL_USE_TLS', 'true').strip().lower() in (
        '1', 'true', 'yes', 'on')
    MAIL_USE_SSL = os.environ.get('MAIL_USE_SSL', 'false').strip().lower() in (
        '1', 'true', 'yes', 'on')
    MAIL_USERNAME =  os.environ.get('MAIL_USERNAME')
    MAIL_PASSWORD =  os.environ.get('MAIL_PASSWORD')
    MAIL_DEFAULT_SENDER = os.environ.get('MAIL_DEFAULT_SENDER') or MAIL_USERNAME
    ADMINS = [MAIL_USERNAME] 
    POSTS_PER_PAGE = 15
    LANGUAGES = ['en', 'es', 'fr', 'ch']
    ELASTICSEARCH_URL = os.environ.get('ELASTICSEARCH_URL')
    MAX_CONTENT_LENGTH = 16 * 1024 * 1024  # 16MB max
    UPLOAD_FOLDER = UPLOAD_FOLDER
    ALLOWED_EXTENSIONS = ALLOWED_EXTENSIONS
    MAIL_USE_TLS = True
    MAIL_USE_SSL = False
    MAIL_TIMEOUT = 30
