"""persist device status transition cursors

Revision ID: 0002_status_cursors
Revises: 0001_initial
Create Date: 2026-10-22
"""

from alembic import op
import sqlalchemy as sa


revision = "0002_status_cursors"
down_revision = "0001_initial"
branch_labels = None
depends_on = None


def upgrade():
    op.create_table(
        "device_status_cursors",
        sa.Column("org_code", sa.String(length=120), nullable=False),
        sa.Column("device_id", sa.String(length=180), nullable=False),
        sa.Column("state", sa.String(length=16), nullable=False),
        sa.Column("state_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.PrimaryKeyConstraint("org_code", "device_id"),
    )


def downgrade():
    op.drop_table("device_status_cursors")
