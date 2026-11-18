"""tavily connection (Sign in with Tavily)

Revision ID: 5c1e7a9b2d40
Revises: 82ecd431e975
"""
from alembic import op
import sqlalchemy as sa

revision = '5c1e7a9b2d40'
down_revision = '82ecd431e975'
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table('tavily_connections',
    sa.Column('id', sa.String(length=32), nullable=False),
    sa.Column('client_id', sa.String(length=200), nullable=False),
    sa.Column('redirect_uri', sa.String(length=500), nullable=False),
    sa.Column('access_token', sa.Text(), nullable=True),
    sa.Column('refresh_token', sa.Text(), nullable=True),
    sa.Column('expires_at', sa.DateTime(timezone=True), nullable=True),
    sa.Column('pending_client_id', sa.String(length=200), nullable=True),
    sa.Column('pending_redirect_uri', sa.String(length=500), nullable=True),
    sa.Column('pending_user_id', sa.String(length=32), nullable=True),
    sa.Column('pending_state', sa.String(length=100), nullable=True),
    sa.Column('pending_verifier', sa.String(length=200), nullable=True),
    sa.Column('connected_by_user_id', sa.String(length=32), nullable=True),
    sa.Column('connected_at', sa.DateTime(timezone=True), nullable=True),
    sa.Column('updated_at', sa.DateTime(timezone=True), nullable=False),
    sa.PrimaryKeyConstraint('id')
    )


def downgrade() -> None:
    op.drop_table('tavily_connections')
