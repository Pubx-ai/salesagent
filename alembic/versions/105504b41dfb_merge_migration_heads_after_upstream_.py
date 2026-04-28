"""merge migration heads after upstream sync 20260428

Revision ID: 105504b41dfb
Revises: 1dbe5cd72d37, 9cc36dfc54f6
Create Date: 2026-04-28 12:25:45.082432

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = '105504b41dfb'
down_revision: Union[str, Sequence[str], None] = ('1dbe5cd72d37', '9cc36dfc54f6')
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Upgrade schema."""
    pass


def downgrade() -> None:
    """Downgrade schema."""
    pass
