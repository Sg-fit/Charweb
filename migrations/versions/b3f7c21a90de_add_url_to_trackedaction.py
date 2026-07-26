"""add url to trackedaction

Records the page each tracked event happened on. Populated client-side by
track.js at event-capture time; NULL for every row collected before this
migration, which research/task_labeling.py treats as "no URL evidence"
rather than as a missing value to impute.

Revision ID: b3f7c21a90de
Revises: 5448acb9734f
Create Date: 2026-07-26

"""
from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision = 'b3f7c21a90de'
down_revision = '5448acb9734f'
branch_labels = None
depends_on = None


def upgrade():
    # batch_alter_table so this works on SQLite, which cannot ALTER a table
    # to add an indexed column in place -- Alembic rebuilds and copies instead.
    with op.batch_alter_table('tracked_action', schema=None) as batch_op:
        batch_op.add_column(sa.Column('url', sa.String(length=512), nullable=True))
        batch_op.create_index(batch_op.f('ix_tracked_action_url'), ['url'], unique=False)


def downgrade():
    with op.batch_alter_table('tracked_action', schema=None) as batch_op:
        batch_op.drop_index(batch_op.f('ix_tracked_action_url'))
        batch_op.drop_column('url')
