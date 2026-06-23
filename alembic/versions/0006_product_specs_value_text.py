"""Change product_specs.value from VARCHAR(512) to TEXT.

Revision ID: 0006
Revises: 0005
Create Date: 2026-06-23

Spec values (e.g. long "Примітка" fields) can exceed 512 characters.
TEXT has no length limit.
"""
from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "0006"
down_revision = "0005"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.alter_column(
        "product_specs",
        "value",
        existing_type=sa.String(512),
        type_=sa.Text(),
        existing_nullable=False,
    )


def downgrade() -> None:
    op.alter_column(
        "product_specs",
        "value",
        existing_type=sa.Text(),
        type_=sa.String(512),
        existing_nullable=False,
    )
