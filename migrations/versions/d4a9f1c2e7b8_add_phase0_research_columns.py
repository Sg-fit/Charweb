"""add phase-0 research columns (attribution labels + server-side event ordering)

Adds the per-session attribution labels the AI-only study groups on
(harness / model / instruction_condition / run_id) plus the Phase-2
adversarial-condition headroom (adversarial_condition defaults to 'clean',
mimicry_target stays null), and the server-authoritative event fields
(server_ts, seq) that let client-trust vs server-trust be compared on the
same sessions. All nullable / defaulted so existing rows upgrade cleanly.

Revision ID: d4a9f1c2e7b8
Revises: c8e2f45b71ac
Create Date: 2026-08-14

"""
from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision = 'd4a9f1c2e7b8'
down_revision = 'c8e2f45b71ac'
branch_labels = None
depends_on = None


def upgrade():
    with op.batch_alter_table('user_session', schema=None) as batch_op:
        batch_op.add_column(sa.Column('run_id', sa.String(length=64), nullable=True))
        batch_op.add_column(sa.Column('harness', sa.String(length=32), nullable=True))
        batch_op.add_column(sa.Column('model', sa.String(length=64), nullable=True))
        batch_op.add_column(sa.Column('instruction_condition', sa.String(length=32), nullable=True))
        batch_op.add_column(sa.Column('adversarial_condition', sa.String(length=32),
                                      nullable=False, server_default='clean'))
        batch_op.add_column(sa.Column('mimicry_target', sa.String(length=64), nullable=True))
        batch_op.create_index(batch_op.f('ix_user_session_run_id'), ['run_id'], unique=False)
        batch_op.create_index(batch_op.f('ix_user_session_harness'), ['harness'], unique=False)
        batch_op.create_index(batch_op.f('ix_user_session_model'), ['model'], unique=False)
        batch_op.create_index(batch_op.f('ix_user_session_adversarial_condition'),
                              ['adversarial_condition'], unique=False)

    with op.batch_alter_table('tracked_action', schema=None) as batch_op:
        # Server-assigned receipt time of the batch this event arrived in, and a
        # monotonic per-session sequence number. Both are set server-side and
        # cannot be forged by a tampered track.js -- the trust anchor for E3.
        batch_op.add_column(sa.Column('server_ts', sa.DateTime(), nullable=True))
        batch_op.add_column(sa.Column('seq', sa.Integer(), nullable=True))
        batch_op.create_index(batch_op.f('ix_tracked_action_server_ts'),
                              ['server_ts'], unique=False)


def downgrade():
    with op.batch_alter_table('tracked_action', schema=None) as batch_op:
        batch_op.drop_index(batch_op.f('ix_tracked_action_server_ts'))
        batch_op.drop_column('seq')
        batch_op.drop_column('server_ts')

    with op.batch_alter_table('user_session', schema=None) as batch_op:
        batch_op.drop_index(batch_op.f('ix_user_session_adversarial_condition'))
        batch_op.drop_index(batch_op.f('ix_user_session_model'))
        batch_op.drop_index(batch_op.f('ix_user_session_harness'))
        batch_op.drop_index(batch_op.f('ix_user_session_run_id'))
        batch_op.drop_column('mimicry_target')
        batch_op.drop_column('adversarial_condition')
        batch_op.drop_column('instruction_condition')
        batch_op.drop_column('model')
        batch_op.drop_column('harness')
        batch_op.drop_column('run_id')
