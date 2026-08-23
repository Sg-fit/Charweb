from app import app, db, mail
from app.models import User, Notification
from flask_mail import Message
import sqlalchemy as sa
from datetime import datetime, timezone

def send_daily_digest():
    with app.app_context():
        users = db.session.scalars(sa.select(User)).all()
        for user in users:
            if not user.email:
                continue
            notifs = db.session.scalars(
                sa.select(Notification)
                .where(Notification.user_id == user.id)
                .where(Notification.is_read == False)
            ).all()
            if not notifs:
                continue
            body = f"Hi {user.username},\n\nYou have {len(notifs)} unread notification(s):\n\n"
            for n in notifs[:10]:
                body += f"• {n.message}\n"
            body += "\nVisit https://charweb.net to check them out.\n"
            try:
                msg = Message(
                    subject="Your Charweb Daily Update",
                    sender=app.config['MAIL_DEFAULT_SENDER'],
                    recipients=[user.email],
                    body=body
                )
                mail.send(msg)
                print(f"Sent digest to {user.email}")
            except Exception as e:
                print(f"Failed for {user.email}: {e}")

if __name__ == '__main__':
    send_daily_digest()