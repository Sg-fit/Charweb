from app import app, db
from app.models import User, Post
import sqlalchemy as sa
import sqlalchemy.orm as so
import click
# Use plain strings for CLI commands to avoid needing a request context

@app.shell_context_processor
def make_shell_context():
    return {'db': db, 'User': User, 'Post': Post}

@app.cli.command()
def test_mail():
    """Send a test email to verify Gmail SMTP is configured correctly."""
    from flask_mail import Message
    from app import mail
    
    if not app.config.get('MAIL_PASSWORD'):
        click.echo('Error: MAIL_PASSWORD not set in environment')
        return
    
    try:
        sender = app.config.get('MAIL_DEFAULT_SENDER') or app.config.get('MAIL_USERNAME')
        msg = Message(
            'Test from Charweb',
            sender=sender,
            recipients=[sender]
        )
        msg.body = 'If you see this, Gmail SMTP is working!'
        msg.html = '<p>If you see this, Gmail SMTP is working!</p>'
        # Ensure application context for Flask-Mail
        with app.app_context():
            mail.send(msg)
        click.echo('✓ Test email sent successfully!')
    except Exception as e:
        click.echo(f'✗ Error sending email: {e}')
