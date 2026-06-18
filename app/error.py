from flask import render_template, request
from app import app, db, mail
from flask_mail import Message
import traceback


@app.errorhandler(404)
def not_found_error(error):
    return render_template('404.html'), 404


@app.errorhandler(500)
def internal_error(error):
    # Rollback the database session and attempt to email the traceback
    db.session.rollback()
    try:
        if app.config.get('MAIL_USERNAME') and app.config.get('MAIL_PASSWORD'):
            tb = traceback.format_exc()
            subject = f"Charweb error: {request.path}"
            body = f"Error: {error}\n\nPath: {request.path}\n\nTraceback:\n{tb}"
            sender = app.config.get('MAIL_DEFAULT_SENDER') or app.config.get('MAIL_USERNAME')
            recipients = app.config.get('ADMINS') or [app.config.get('MAIL_USERNAME')]
            msg = Message(subject,
                          sender=sender,
                          recipients=recipients)
            msg.body = body
            mail.send(msg)
    except Exception:
        app.logger.exception('Failed to send error email')

    return render_template('500.html'), 500