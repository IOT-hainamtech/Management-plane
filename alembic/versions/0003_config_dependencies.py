"""Track resource dependencies for independently deliverable config jobs.

Revision ID: 0003_config_deps
Revises: 0002_status_cursors
"""

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql


revision = "0003_config_deps"
down_revision = "0002_status_cursors"
branch_labels = None
depends_on = None


def upgrade():
    op.add_column(
        "config_jobs",
        sa.Column("dependencies", postgresql.JSONB(), nullable=False, server_default=sa.text("'[]'::jsonb")),
    )
    # Existing queued jobs must obey the same dependency rule immediately after
    # migration, even if their payload/version remains unchanged on the next scan.
    op.execute("""
        UPDATE config_jobs SET dependencies = CASE kind
          WHEN 'site' THEN jsonb_build_array(jsonb_build_object('kind', 'organization', 'key', payload->>'org_code'))
          WHEN 'zone' THEN jsonb_build_array(jsonb_build_object('kind', 'site', 'key', payload->>'site_code'))
          WHEN 'profile' THEN jsonb_build_array(jsonb_build_object('kind', 'organization', 'key', payload->>'org_code'))
          WHEN 'device' THEN jsonb_build_array(
            jsonb_build_object('kind', 'site', 'key', payload->>'site_code'),
            jsonb_build_object('kind', 'profile', 'key', payload->>'profile_code')
          ) || CASE WHEN payload->>'zone_code' IS NULL THEN '[]'::jsonb
                   ELSE jsonb_build_array(jsonb_build_object('kind', 'zone', 'key', payload->>'zone_code')) END
          WHEN 'rule' THEN jsonb_build_array(jsonb_build_object(
            'kind', lower(payload->'scope'->>'type'), 'key', payload->'scope'->>'ref'))
          ELSE '[]'::jsonb
        END
    """)
    op.create_index("ix_config_jobs_ready", "config_jobs", ["state", "retry_at", "priority"])


def downgrade():
    op.drop_index("ix_config_jobs_ready", table_name="config_jobs")
    op.drop_column("config_jobs", "dependencies")
