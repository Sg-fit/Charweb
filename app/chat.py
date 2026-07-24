from flask import Blueprint, render_template, request, jsonify
from flask_login import login_required, current_user
from flask_socketio import emit, join_room, leave_room
from app import socketio, db
from app.models import User, Message
from datetime import datetime, timezone
import sqlalchemy as sa

bp = Blueprint('chat', __name__, url_prefix='/chat')
online_users = set()

@bp.route('/')
@login_required
def chat_page():
    users = User.query.filter(User.id != current_user.id).all()
    return render_template('chat.html', users=users, online_users=online_users)

@bp.route('/history/<int:user_id>')
@login_required
def chat_history(user_id):
    messages = db.session.scalars(
        sa.select(Message).where(
            sa.or_(
                sa.and_(Message.sender_id == current_user.id, Message.recipient_id == user_id),
                sa.and_(Message.sender_id == user_id, Message.recipient_id == current_user.id)
            )
        ).order_by(Message.timestamp.asc())
    ).all()
    return jsonify([{
        'username': m.sender.username,
        'message': m.body,
        'timestamp': m.timestamp.isoformat(),
        'mine': m.sender_id == current_user.id
    } for m in messages])

@socketio.on('connect')
def on_connect():
    online_users.add(current_user.id)
    join_room(f"user_{current_user.id}")
    emit('user_status', {'user_id': current_user.id, 'status': 'online'}, broadcast=True)
@socketio.on('disconnect')
def on_disconnect():
    online_users.discard(current_user.id)
    emit('user_status', {'user_id': current_user.id, 'status': 'offline'}, broadcast=True)

@socketio.on('join')
def on_join(data):
    join_room(data['room'])

@socketio.on('leave')
def on_leave(data):
    leave_room(data['room'])

@socketio.on('message')
def handle_message(data):
    msg = Message(
        sender_id=current_user.id,
        recipient_id=data['recipient_id'],
        body=data['message']
    )
    db.session.add(msg)
    db.session.commit()
    emit('message', {
        'username': current_user.username,
        'message': data['message'],
        'timestamp': datetime.now(timezone.utc).isoformat(),
        'sender_id': current_user.id
    }, room=data['room'])