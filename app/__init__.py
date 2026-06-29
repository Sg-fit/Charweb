from flask import Flask, request
from .config import Config
from flask_sqlalchemy import SQLAlchemy
from flask_migrate import Migrate
from flask_login import LoginManager
import logging
from logging.handlers import SMTPHandler, RotatingFileHandler
import os
from flask_mail import Mail
from flask_moment import Moment
from flask_babel import Babel
from elasticsearch import Elasticsearch

def get_locale():
    return request.accept_languages.best_match(Config.LANGUAGES)

app = Flask(__name__, template_folder='templates')
app.config.from_object(Config)
db = SQLAlchemy(app)
migrate = Migrate(app, db)
mail = Mail(app)
login = LoginManager(app)
login.login_view = 'login'
moment = Moment(app)
babel = Babel(app, locale_selector=get_locale)
if not app.debug:
    if app.config['MAIL_SERVER'] and app.config['ADMINS'] and (
            app.config['MAIL_USERNAME'] and app.config['MAIL_PASSWORD']):
        auth = (app.config['MAIL_USERNAME'], app.config['MAIL_PASSWORD'])
        secure = None
        if app.config['MAIL_USE_TLS']:
            secure = ()
        mail_handler = SMTPHandler(
            mailhost=(app.config['MAIL_SERVER'], app.config['MAIL_PORT']),
            fromaddr=app.config.get('MAIL_DEFAULT_SENDER') or app.config.get('MAIL_USERNAME'),
            toaddrs=app.config['ADMINS'], subject='Charweb Failure',
            credentials=auth, secure=secure)
        mail_handler.setLevel(logging.ERROR)
        try:
            app.logger.addHandler(mail_handler)
        except Exception:
            pass
    
    if not os.path.exists('logs'):
        os.mkdir('logs')
    file_handler = RotatingFileHandler('logs/Charweb.log', maxBytes=10240,
        backupCount=10)
    file_handler.setFormatter(logging.Formatter(
        '%(asctime)s %(levelname)s: %(message)s [in %(pathname)s:%(lineno)d]'))
    file_handler.setLevel(logging.INFO)
    app.logger.addHandler(file_handler)

    app.logger.setLevel(logging.INFO)
    app.logger.info('Charweb startup')

if app.config.get('ELASTICSEARCH_URL'):
    try:
        app.elasticsearch = Elasticsearch([app.config['ELASTICSEARCH_URL']])
    except Exception:
        app.elasticsearch = None
else:
    app.elasticsearch = None

from app.main.routes import bp as main_bp
app.register_blueprint(main_bp)
from app import routes, models, error

