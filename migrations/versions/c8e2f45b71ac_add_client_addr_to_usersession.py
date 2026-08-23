"""add client addr/port to usersession

Records the client IP and source port of each session's most recent
/api/track request, so passively-captured TLS ClientHellos
(research/extract_ja3.py) can be joined to sessions on the exact TCP
connection rather than by fuzzy IP + timestamp matching.

Revision ID: c8e2f45b71ac
Revises: b3f7c21a90de
Create Date: 2026-07-26

"""
from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision = 'c8e2f45b71ac'
down_revision = 'b3f7c21a90de'
branch_labels = None
depends_on = None


def upgrade():
    with op.batch_alter_table('user_session', schema=None) as batch_op:
        batch_op.add_column(sa.Column('remote_addr', sa.String(length=45), nullable=True))
        batch_op.add_column(sa.Column('remote_port', sa.Integer(), nullable=True))
        batch_op.create_index(batch_op.f('ix_user_session_remote_addr'),
                              ['remote_addr'], unique=False)


def downgrade():
    with op.batch_alter_table('user_session', schema=None) as batch_op:
        batch_op.drop_index(batch_op.f('ix_user_session_remote_addr'))
        batch_op.drop_column('remote_port')
        batch_op.drop_column('remote_addr')
