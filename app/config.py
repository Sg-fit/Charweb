import os
basedir = os.path.abspath(os.path.dirname(__file__))

class Config:
    SECRET_KEY = os.environ.get('SECRET_KEY') or 'KEY_XKI'
    SQLALCHEMY_DATABASE_URI = os.environ.get('DATABASE_URL') or \
        'sqlite:///' + os.path.join(basedir, 'app.db')
    MAIL_SERVER = os.environ.get('MAIL_SERVER')
    MAIL_PORT = int(os.environ.get('MAIL_PORT') or 587) if os.environ.get('MAIL_SERVER') else None
    MAIL_USE_TLS = os.environ.get('MAIL_USE_TLS', 'true').strip().lower() in (
        '1', 'true', 'yes', 'on')
    MAIL_USE_SSL = os.environ.get('MAIL_USE_SSL', 'false').strip().lower() in (
        '1', 'true', 'yes', 'on')
    MAIL_USERNAME =  'User'
    MAIL_PASSWORD =  'Password'
    MAIL_DEFAULT_SENDER = os.environ.get('MAIL_DEFAULT_SENDER') or MAIL_USERNAME
    ADMINS = [MAIL_USERNAME] 
    POSTS_PER_PAGE = 15
    LANGUAGES = ['en', 'es', 'fr', 'ch']
    ELASTICSEARCH_URL = os.environ.get('ELASTICSEARCH_URL')
