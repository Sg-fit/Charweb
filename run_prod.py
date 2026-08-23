# Production entry point for Charweb.
#
# We run through Flask-SocketIO's own eventlet server rather than gunicorn's
# eventlet worker: gunicorn's `--worker-class eventlet` is incompatible with
# eventlet >= 0.38 (the worker entry point fails to load), whereas the
# Flask-SocketIO server drives eventlet directly. Single process, which also
# keeps server response time consistent -- a measured variable during
# data collection.
import eventlet
eventlet.monkey_patch()

from app import app, socketio

if __name__ == "__main__":
    socketio.run(app, host="127.0.0.1", port=8000)
