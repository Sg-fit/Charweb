from flask import Flask, request
from .config import Config
from flask_sqlalchemy import SQLAlchemy
from flask_migrate import Migrate
from flask_login import LoginManager, current_user
import logging
from logging.handlers import SMTPHandler, RotatingFileHandler
import os
from flask_mail import Mail
from flask_moment import Moment
from flask_babel import Babel
from elasticsearch import Elasticsearch
from flask_socketio import SocketIO, emit, join_room   # New


def get_locale():
    return request.accept_languages.best_match(Config.LANGUAGES)

# Create the Flask app first
app = Flask(__name__, template_folder='templates')
app.config.from_object(Config)

db = SQLAlchemy(app)
migrate = Migrate(app, db)
mail = Mail(app)
login = LoginManager(app)
login.login_view = 'login'
moment = Moment(app)
babel = Babel(app, locale_selector=get_locale)

# Initialize SocketIO AFTER app is created
socketio = SocketIO(app, cors_allowed_origins="*", async_mode='gevent', 
                    logger=False, engineio_logger=False,
                    allow_upgrades=True)


# Logging setup
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
from app.models import User, Message, Notification

def handle_message(data):
    msg = Message(
        sender_id=current_user.id,
        recipient_id=data['recipient_id'],
        body=data['message']
    )
    db.session.add(msg)
    # create notification for recipient
    notif = Notification(
        user_id=data['recipient_id'],
        message=f"New message from {current_user.username}: {data['message'][:50]}"
    )
    db.session.add(notif)
    # update last_message_at on recipient
    recipient = db.session.get(User, data['recipient_id'])
    if recipient:
        recipient.last_message_at = datetime.now(timezone.utc)
    db.session.commit()
    emit('message', {
        'username': current_user.username,
        'message': data['message'],
        'timestamp': datetime.now(timezone.utc).isoformat(),
        'sender_id': current_user.id
    }, room=data['room'])
    emit('new_notification', {
        'count': db.session.scalar(
            sa.select(sa.func.count()).select_from(Notification)
            .where(Notification.user_id == data['recipient_id'])
            .where(Notification.is_read == False)
        )
    }, room=f"user_{data['recipient_id']}")

# Blueprints and imports
from app.main.routes import bp as main_bp
app.register_blueprint(main_bp)

from app import routes, models, error

# Chat blueprint
from app.chat import bp as chat_bp
app.register_blueprint(chat_bp)

# Make socketio available to other modules
__all__ = ['app', 'db', 'socketio']