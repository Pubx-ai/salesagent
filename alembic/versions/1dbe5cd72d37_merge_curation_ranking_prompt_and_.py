"""merge curation ranking prompt and account approval mode heads

Revision ID: 1dbe5cd72d37
Revises: c612d0326eb0, df7866e6dc89
Create Date: 2026-04-22 15:50:03.442760

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = '1dbe5cd72d37'
down_revision: Union[str, Sequence[str], None] = ('c612d0326eb0', 'df7866e6dc89')
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Upgrade schema."""
    pass


def downgrade() -> None:
    """Downgrade schema."""
    pass
